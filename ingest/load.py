"""Load the generated views into FalkorDB (DATA_SPEC section 8).

    python -m ingest.load --data data/ --graph northwind

Idempotent: every node and edge is MERGEd on a stable key, so loading twice
gives the same graph. Batched with UNWIND. Never reads `truth/world.json`.

Graph model
- `:Component` + class label for every CMDB CI; reference nodes `:Site`,
  `:Department`, `:SupportGroup`, `:User`, `:Group`, `:Role`, `:Change`.
- Declared edges from the CMDB and identity files; observed edges
  aggregated from flows, netflow, replication, backups, access and
  ownership evidence.
- `:Discovered` nodes hold what scanners saw; `IDENTIFIES` links them to a
  `:Component` by serial number, else hostname + IP. Discovered attributes
  never overwrite CMDB attributes.
- Flow endpoints resolve by exact name (or sys_id), then by IP. Names
  resolved by IP or serial are stored in `Component.aliases`.
"""

import argparse
import csv
import json
import os
import time
from collections import Counter

from falkordb import FalkorDB

from ingest.views import open_in
from ingest.world import CLASS_LABEL

BATCH = 2000
INT_FIELDS = {"count", "rpo_minutes", "rto_hours", "capacity_tb", "member_count", "beds"}
BOOL_FIELDS = {"handles_phi", "backup_offsite", "patchable", "encrypted", "immutable", "failover", "scanned"}
REL_TYPES = {
    "Contains::Contained by": "CONTAINS", "Depends on::Used by": "DEPENDS_ON", "Runs on::Runs": "RUNS_ON",
    "Stores on::Stores": "STORES_ON", "Replicates to::Replica of": "REPLICATES_TO",
    "Backed up by::Backs up": "BACKED_UP_BY", "Connects through::Connects": "CONNECTS_THROUGH",
    "Located at": "LOCATED_AT", "Managed by": "MANAGED_BY",
}
INDEXES = [("Component", "name"), ("Component", "sys_id"), ("Component", "ip_address"),
           ("Discovered", "serial_number"), ("Discovered", "key"), ("Site", "id"), ("Department", "id"),
           ("SupportGroup", "name"), ("User", "id"), ("Group", "id"), ("Role", "id"), ("Change", "number")]


def typed(row, skip=()):
    """CSV strings -> typed properties; empty cells are omitted."""
    out = {}
    for k, v in row.items():
        if k in skip or v in ("", None):
            continue
        if k in BOOL_FIELDS:
            out[k] = v == "true"
        elif k in INT_FIELDS:
            out[k] = int(float(v))
        else:
            out[k] = v
    return out


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_jsonl(path):
    if os.path.exists(path) or os.path.exists(path + ".gz"):
        with open_in(path) as f:  # plain or .gz
            for line in f:
                yield json.loads(line)


class Loader:
    def __init__(self, data, graph, host="localhost", port=6379, batch=BATCH):
        self.data = data
        self.g = FalkorDB(host=host, port=port).select_graph(graph)
        self.batch = batch
        self.unresolved = Counter()

    def path(self, *p):
        return os.path.join(self.data, *p)

    def run(self, query, rows):
        for i in range(0, len(rows), self.batch):
            self.g.query(query, {"rows": rows[i:i + self.batch]})

    # ---- schema --------------------------------------------------------

    def indexes(self):
        existing = set()
        try:
            for row in self.g.query("CALL db.indexes()").result_set:
                for prop in row[1]:
                    existing.add((row[0], prop))
        except Exception:  # empty graph has no index procedure output yet
            pass
        for label, prop in INDEXES:
            if (label, prop) not in existing:
                self.g.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")

    # ---- declared view ---------------------------------------------------

    def reference(self):
        sites = [typed(r) for r in read_csv(self.path("cmdb", "cmn_location.csv"))]
        self.run("UNWIND $rows AS r MERGE (s:Site {id: r.sys_id}) SET s += r", sites)
        depts = [typed(r) for r in read_csv(self.path("cmdb", "cmn_department.csv"))]
        self.run("UNWIND $rows AS r MERGE (d:Department {id: r.sys_id}) SET d.name = r.name, d.code = r.code "
                 "WITH d, r MATCH (s:Site {id: r.location}) MERGE (s)-[:CONTAINS]->(d)", depts)
        groups = [typed(r) for r in read_csv(self.path("cmdb", "sys_user_group.csv"))]
        self.run("UNWIND $rows AS r MERGE (g:SupportGroup {name: r.name}) SET g += r", groups)

    def components(self):
        rows = read_csv(self.path("cmdb", "cmdb_ci.csv"))
        self.cis = {r["name"]: r for r in rows}
        self.by_sys = {r["sys_id"]: r["name"] for r in rows}
        ips = Counter(r["ip_address"] for r in rows if r["ip_address"])
        self.by_ip = {r["ip_address"]: r["name"] for r in rows if r["ip_address"] and ips[r["ip_address"]] == 1}
        by_label = {}
        for r in rows:
            by_label.setdefault(CLASS_LABEL[r["class"]], []).append(typed(r))
        for label, items in sorted(by_label.items()):
            self.run(f"UNWIND $rows AS r MERGE (c:Component:{label} {{name: r.name}}) SET c += r", items)
        for col, label, key, etype in (("site", "Site", "id", "LOCATED_AT"),
                                       ("department", "Department", "id", "IN_DEPARTMENT"),
                                       ("support_group", "SupportGroup", "name", "MANAGED_BY")):
            pairs = [{"a": r["name"], "b": r[col]} for r in rows if r[col]]
            self.run(f"UNWIND $rows AS r MATCH (c:Component {{name: r.a}}) MATCH (x:{label} {{{key}: r.b}}) "
                     f"MERGE (c)-[:{etype}]->(x)", pairs)

    def relationships(self):
        by_type = {}
        for r in read_csv(self.path("cmdb", "cmdb_rel_ci.csv")):
            et = REL_TYPES[r["type"]]
            props = {"failover": r["failover"] == "true"}
            if et == "REPLICATES_TO":
                props["mode"] = r["mode"]
            by_type.setdefault(et, []).append({"a": r["parent"], "b": r["child"], "p": props})
        for et, rows in sorted(by_type.items()):
            self.run(f"UNWIND $rows AS r MATCH (a:Component {{name: r.a}}) MATCH (b:Component {{name: r.b}}) "
                     f"MERGE (a)-[e:{et}]->(b) SET e += r.p", rows)

    def changes(self):
        rows = [typed(r) for r in read_csv(self.path("cmdb", "change_request.csv"))]
        self.run("UNWIND $rows AS r MERGE (c:Change {number: r.number}) SET c += r "
                 "WITH c, r MATCH (x:Component {name: r.cmdb_ci}) MERGE (c)-[:TARGETS]->(x)", rows)

    def identity(self):
        users = [typed(r) for r in read_csv(self.path("identity", "users.csv"))]
        self.run("UNWIND $rows AS r MERGE (u:User {id: r.id}) SET u += r", users)
        roles = [typed(r) for r in read_csv(self.path("identity", "roles.csv"))]
        self.run("UNWIND $rows AS r MERGE (x:Role {id: r.id}) SET x += r", roles)
        groups = read_csv(self.path("identity", "groups.csv"))
        self.run("UNWIND $rows AS r MERGE (g:Group {id: r.id}) SET g += r",
                 [typed(g, skip={"roles"}) for g in groups])
        has_role = [{"g": g["id"], "r": role} for g in groups for role in g["roles"].split(";") if role]
        self.run("UNWIND $rows AS r MATCH (g:Group {id: r.g}) MATCH (x:Role {id: r.r}) MERGE (g)-[:HAS_ROLE]->(x)",
                 has_role)
        self.run("UNWIND $rows AS r MATCH (u:User {id: r.user}) MATCH (g:Group {id: r.group}) "
                 "MERGE (u)-[:MEMBER_OF]->(g)", read_csv(self.path("identity", "memberships.csv")))
        self.run("UNWIND $rows AS r MATCH (x:Role {id: r.role}) MATCH (c:Component {name: r.target}) "
                 "MERGE (x)-[e:GRANTS {level: r.level}]->(c)", read_csv(self.path("identity", "grants.csv")))

    # ---- discovery -----------------------------------------------------

    def discovery(self):
        by_serial = {r["serial_number"]: n for n, r in self.cis.items() if r["serial_number"]}
        by_host_ip = {(n, r["ip_address"]): n for n, r in self.cis.items() if r["ip_address"]}
        nodes, links = [], []
        for rec in read_jsonl(self.path("discovery", "scan_results.jsonl")):
            key = f"{rec['site']}|{rec['hostname']}|{rec['ip_address']}"
            nodes.append(dict({k: v for k, v in rec.items() if v != ""}, key=key))
            ci = by_serial.get(rec["serial_number"]) if rec["serial_number"] else None
            how = "serial_number"
            if ci is None:
                ci, how = by_host_ip.get((rec["hostname"], rec["ip_address"])), "hostname+ip"
            if ci:
                links.append({"k": key, "c": ci, "how": how})
                if rec["hostname"] != ci:
                    self.alias(ci, rec["hostname"])
        self.run("UNWIND $rows AS r MERGE (d:Discovered {key: r.key}) SET d += r", nodes)
        self.run("UNWIND $rows AS r MATCH (d:Discovered {key: r.k}) MATCH (c:Component {name: r.c}) "
                 "MERGE (d)-[e:IDENTIFIES]->(c) SET e.matched_by = r.how", links)
        subnets = {}
        for r in read_csv(self.path("discovery", "scan_coverage.csv")):
            if r["scanned"] == "true":
                subnets.setdefault(r["site"], []).append(r["subnet"])
        self.run("UNWIND $rows AS r MATCH (s:Site {id: r.s}) SET s.scanned_subnets = r.n",
                 [{"s": k, "n": v} for k, v in sorted(subnets.items())])

    # ---- observed --------------------------------------------------------

    def alias(self, ci, name):
        self.aliases.setdefault(ci, set()).add(name)

    def resolve(self, name, ip):
        if name in self.cis:
            return name
        if name in self.by_sys:
            self.alias(self.by_sys[name], name)
            return self.by_sys[name]
        if ip in self.by_ip:
            self.alias(self.by_ip[ip], name)
            return self.by_ip[ip]
        self.unresolved[name] += 1
        return None

    def flows(self):
        calls, net = {}, {}
        for fname in ("flows_hourly.jsonl", "flows_daily.jsonl"):
            for rec in read_jsonl(self.path("observed", fname)):
                a, b = self.resolve(rec["src_name"], rec["src_ip"]), self.resolve(rec["dst_name"], rec["dst_ip"])
                if not (a and b):
                    continue
                t = rec.get("hour") or rec["day"] + "T00:00:00Z"
                k = (a, b, rec["msg_type"], rec["protocol"])
                e = calls.setdefault(k, {"count": 0, "errors": 0, "p95_ms": 0.0, "first_seen": t, "last_seen": t,
                                         "days": set(), "health_check": True})
                e["count"] += rec["count"]
                e["errors"] += rec["errors"]
                e["p95_ms"] = max(e["p95_ms"], rec["p95_ms"])
                e["first_seen"], e["last_seen"] = min(e["first_seen"], t), max(e["last_seen"], t)
                e["days"].add(t[:10])
                e["health_check"] = e["health_check"] and bool(rec.get("health_check"))
        for fname in ("netflow_hourly.jsonl", "netflow_daily.jsonl"):
            for rec in read_jsonl(self.path("observed", fname)):
                a, b = self.resolve(rec["src_name"], rec["src_ip"]), self.resolve(rec["dst_name"], rec["dst_ip"])
                if not (a and b):
                    continue
                t = rec.get("hour") or rec["day"] + "T00:00:00Z"
                e = net.setdefault((a, b, rec["port"]), {"bytes": 0, "flows": 0, "first_seen": t, "last_seen": t,
                                                         "days": set()})
                e["bytes"] += rec["bytes"]
                e["flows"] += rec["flows"]
                e["first_seen"], e["last_seen"] = min(e["first_seen"], t), max(e["last_seen"], t)
                e["days"].add(t[:10])
        def props(e):  # distinct days with traffic, kept for the ">= 3 days" rule
            return {**{k: v for k, v in e.items() if k != "days"}, "days_seen": len(e["days"])}

        rows = [{"a": a, "b": b, "m": m, "pr": pr, "p": props(e)} for (a, b, m, pr), e in sorted(calls.items())]
        self.run("UNWIND $rows AS r MATCH (a:Component {name: r.a}) MATCH (b:Component {name: r.b}) "
                 "MERGE (a)-[e:OBSERVED_CALLS {msg_type: r.m, protocol: r.pr}]->(b) SET e += r.p", rows)
        rows = [{"a": a, "b": b, "port": port, "p": props(e)} for (a, b, port), e in sorted(net.items())]
        self.run("UNWIND $rows AS r MATCH (a:Component {name: r.a}) MATCH (b:Component {name: r.b}) "
                 "MERGE (a)-[e:OBSERVED_NETFLOW {port: r.port}]->(b) SET e += r.p", rows)

    def replication(self):
        agg = {}
        for rec in read_jsonl(self.path("observed", "replication_status.jsonl")):
            k = (rec["source_array"], rec["target_array"])
            e = agg.setdefault(k, {"mode": rec["mode"], "max_lag_s": 0, "last_seen": rec["hour"],
                                   "state": rec["state"], "volumes": set()})
            e["max_lag_s"] = max(e["max_lag_s"], rec["lag_seconds"])
            if rec["hour"] >= e["last_seen"]:
                e["last_seen"], e["state"] = rec["hour"], rec["state"]
            e["volumes"].add(rec["source_volume"])
        rows = [{"a": a, "b": b, "p": dict(e, volumes=sorted(e["volumes"]))} for (a, b), e in sorted(agg.items())]
        self.run("UNWIND $rows AS r MATCH (a:Component {name: r.a}) MATCH (b:Component {name: r.b}) "
                 "MERGE (a)-[e:OBSERVED_REPLICATES_TO]->(b) SET e += r.p", rows)

    def backups(self):
        agg = {}
        for job in read_jsonl(self.path("observed", "backup_jobs.jsonl")):
            k = (job["object"], job["target"])
            e = agg.setdefault(k, {"last_run": "", "last_status": "", "jobs": 0, "failures": 0,
                                   "policy": job["policy"]})
            e["jobs"] += 1
            e["failures"] += job["status"] != "success"
            if job["start"] >= e["last_run"]:
                e["last_run"], e["last_status"] = job["start"], job["status"]
            if job["status"] == "success" and job["end"] > e.get("last_success", ""):
                e["last_success"] = job["end"]
        rows = [{"a": a, "b": b, "p": e} for (a, b), e in sorted(agg.items())]
        self.run("UNWIND $rows AS r MATCH (a:Component {name: r.a}) MATCH (b:Component {name: r.b}) "
                 "MERGE (a)-[e:OBSERVED_BACKUP_TO]->(b) SET e += r.p", rows)

    def access(self):
        agg = {}
        for e in read_jsonl(self.path("observed", "access_log.jsonl")):
            k = (e["user"], e["target"], e["action"])
            x = agg.setdefault(k, {"count": 0, "failures": 0, "first_seen": e["ts"], "last_seen": e["ts"],
                                   "workstations": set()})
            x["count"] += 1
            x["failures"] += e["result"] != "success"
            x["first_seen"], x["last_seen"] = min(x["first_seen"], e["ts"]), max(x["last_seen"], e["ts"])
            x["workstations"].add(e["workstation"])
        rows = [{"u": u, "c": c, "a": a, "p": dict(x, workstations=sorted(x["workstations"]))}
                for (u, c, a), x in sorted(agg.items())]
        self.run("UNWIND $rows AS r MATCH (u:User {id: r.u}) MATCH (c:Component {name: r.c}) "
                 "MERGE (u)-[e:OBSERVED_ACCESS {action: r.a}]->(c) SET e += r.p", rows)

    def evidence(self):
        rows = [{"g": r["team"], "c": r["ci"],
                 "p": {"incidents_resolved": int(r["incidents_resolved"]),
                       "changes_implemented": int(r["changes_implemented"]), "last_activity": r["last_activity"]}}
                for r in read_csv(self.path("evidence", "ownership_evidence.csv"))]
        self.run("UNWIND $rows AS r MATCH (g:SupportGroup {name: r.g}) MATCH (c:Component {name: r.c}) "
                 "MERGE (g)-[e:OBSERVED_OWNER]->(c) SET e += r.p", rows)

    def write_aliases(self):
        rows = [{"c": c, "a": sorted(a)} for c, a in sorted(self.aliases.items())]
        self.run("UNWIND $rows AS r MATCH (c:Component {name: r.c}) SET c.aliases = r.a", rows)

    # ---- driver ----------------------------------------------------------

    def load(self):
        t0 = time.time()
        self.aliases = {}
        self.indexes()
        for step in (self.reference, self.components, self.relationships, self.changes, self.identity,
                     self.discovery, self.flows, self.replication, self.backups, self.access, self.evidence,
                     self.write_aliases):
            step()
        return round(time.time() - t0, 1)

    def counts(self):
        nodes = {r[0]: r[1] for r in self.g.query(
            "MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) ORDER BY l").result_set}
        edges = {r[0]: r[1] for r in self.g.query(
            "MATCH ()-[e]->() RETURN type(e), count(*) ORDER BY type(e)").result_set}
        total_n = self.g.query("MATCH (n) RETURN count(n)").result_set[0][0]
        total_e = self.g.query("MATCH ()-[e]->() RETURN count(e)").result_set[0][0]
        return {"nodes_by_label": nodes, "edges_by_type": edges, "total_nodes": total_n, "total_edges": total_e}


def main():
    ap = argparse.ArgumentParser(description="Load Northwind views into FalkorDB")
    ap.add_argument("--data", default="data")
    ap.add_argument("--graph", default="northwind")
    ap.add_argument("--host", default=os.environ.get("FALKORDB_HOST", "localhost"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("FALKORDB_PORT", 6379)))
    ap.add_argument("--batch", type=int, default=BATCH)
    a = ap.parse_args()
    ld = Loader(a.data, a.graph, a.host, a.port, a.batch)
    secs = ld.load()
    res = ld.counts()
    res["load_seconds"] = secs
    res["unresolved_flow_endpoints"] = dict(ld.unresolved)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
