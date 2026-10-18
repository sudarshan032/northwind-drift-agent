"""Load normalised evidence into FalkorDB (DATA_SPEC section 8, REALISM_SPEC 2-4).

    python -m ingest.load --data data/ --graph northwind

Idempotent: every node and edge is MERGEd on a stable key, so loading twice
gives the same graph. Batched with UNWIND. Never reads `truth/world.json`.

Graph model
- `:Component` + class label for every CMDB CI; reference nodes `:Site`,
  `:Department`, `:SupportGroup`, `:User`, `:Group`, `:Role`, `:Change`.
- Declared layer: the cmdb_export connector's tables and the IAM export.
- Evidence layer: connector output only (data/normalised): every edge
  carries `source` and `source_record`. The loader never parses raw tool
  formats; identification (serial, name/alias, IP, IP:port) was already done
  by the connectors (ingest/sources/identify.py).
- `:Discovered` nodes hold what scanners saw; `IDENTIFIES {matched_by}` links
  them to their CI. Discovered attributes never overwrite CMDB attributes.
- Endpoints that match no CI become `:UnregisteredEndpoint`.
"""

import argparse
import ipaddress
import json
import os
import time
from collections import Counter

from graph.connection import connect

from graph import impact
from ingest.sources import common
from ingest.world import CLASS_LABEL

BATCH = 2000
DECLARED_EDGES = ("CONTAINS|DEPENDS_ON|RUNS_ON|STORES_ON|REPLICATES_TO|BACKED_UP_BY|CONNECTS_THROUGH|LOCATED_AT|"
                  "IN_DEPARTMENT|MANAGED_BY|MEMBER_OF|HAS_ROLE|GRANTS|TARGETS")
EVIDENCE_EDGES = ("OBSERVED_CALLS|OBSERVED_NETFLOW|OBSERVED_REPLICATES_TO|OBSERVED_BACKUP_TO|OBSERVED_ACCESS|"
                  "OBSERVED_OWNER")
RECONCILIATION_EDGES = "IDENTIFIES|IMPACTS_DECLARED|IMPACTS_REAL|AFFECTS_DECLARED|AFFECTS_REAL|ABOUT"
LAYER_TAGS = [  # declared = CMDB export + IAM; evidence = discovery/observed sources; reconciliation = matches, analysis
    "MATCH (n) WHERE n:Component OR n:Site OR n:Department OR n:SupportGroup OR n:Change OR n:User OR n:Group "
    "OR n:Role SET n.layer = 'declared'",
    "MATCH (n) WHERE n:Discovered OR n:UnregisteredEndpoint SET n.layer = 'evidence'",
    "MATCH (n:Finding) SET n.layer = 'reconciliation'",
    f"MATCH ()-[e:{DECLARED_EDGES}]->() SET e.layer = 'declared'",
    f"MATCH ()-[e:{EVIDENCE_EDGES}]->() SET e.layer = 'evidence'",
    f"MATCH ()-[e:{RECONCILIATION_EDGES}]->() SET e.layer = 'reconciliation'",
]
INT_FIELDS = {"count", "rpo_minutes", "rto_hours", "capacity_tb", "member_count", "beds"}
BOOL_FIELDS = {"handles_phi", "backup_offsite", "patchable", "encrypted", "immutable", "failover", "scanned"}
# ServiceNow type -> graph edge. Graph edge names are the DATA_SPEC section 8 names.
REL_TYPES = {
    "Contains::Contained by": "CONTAINS", "Depends on::Used by": "DEPENDS_ON", "Runs on::Runs": "RUNS_ON",
    "Provides storage for::Stored on": "STORES_ON", "Replicates to::Replica of": "REPLICATES_TO",
    "Backed up by::Backs up": "BACKED_UP_BY", "Connects to::Connected by": "CONNECTS_THROUGH",
    "Located at": "LOCATED_AT", "Managed by": "MANAGED_BY",
}
INDEXES = [("Component", "name"), ("Component", "sys_id"), ("Component", "ip_address"),
           ("Discovered", "serial_number"), ("Discovered", "key"), ("Site", "id"), ("Department", "id"),
           ("SupportGroup", "name"), ("User", "id"), ("Group", "id"), ("Role", "id"), ("Change", "number"),
           ("UnregisteredEndpoint", "name")]


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
    return common.read_csv(path)  # plain or .gz


def read_jsonl(path):
    return common.read_jsonl(path)


def props(row, drop=()):
    """Evidence row -> edge properties (lists of primitives only; nested values as JSON)."""
    out = {}
    for k, v in row.items():
        if k in drop or v is None:
            continue
        if isinstance(v, list) and v and isinstance(v[0], dict):
            out[f"{k}_json"] = json.dumps(v)
            out[f"{k}_count"] = len(v)
        else:
            out[k] = v
    if "days" in out:
        out["days_seen"] = len(out["days"])
    return out


class Loader:
    """Loads data/normalised (connector output) and data/identity (declared IAM)."""

    def __init__(self, data, graph, host="localhost", port=6379, batch=BATCH, db=None):
        self.data = data
        self.db = db or connect(host, port)  # FALKORDB_USERNAME / PASSWORD / TLS from the environment
        self.g = self.db.select_graph(graph)
        self.batch = batch
        self.unresolved = Counter()

    def path(self, *p):
        return os.path.join(self.data, *p)

    def norm(self, name):
        return self.path("normalised", name)

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

    # ---- declared layer (cmdb_export connector output + IAM) --------------

    def reference(self):
        sites = [typed(r) for r in read_csv(self.norm("cmn_location.csv"))]
        self.run("UNWIND $rows AS r MERGE (s:Site {id: r.sys_id}) SET s += r", sites)
        depts = [typed(r) for r in read_csv(self.norm("cmn_department.csv"))]
        self.run("UNWIND $rows AS r MERGE (d:Department {id: r.sys_id}) SET d.name = r.name, d.code = r.code "
                 "WITH d, r MATCH (s:Site {id: r.location}) MERGE (s)-[:CONTAINS]->(d)", depts)
        groups = [typed(r) for r in read_csv(self.norm("sys_user_group.csv"))]
        self.run("UNWIND $rows AS r MERGE (g:SupportGroup {name: r.name}) SET g += r", groups)

    def components(self):
        rows = read_csv(self.norm("cmdb_ci.csv"))
        self.cis = {r["name"]: r for r in rows}
        by_label = {}
        for r in rows:
            by_label.setdefault(CLASS_LABEL[r["class"]], []).append(dict(typed(r), source="cmdb_export"))
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
        for r in read_csv(self.norm("cmdb_rel_ci.csv")):
            et = REL_TYPES[r["type"]]
            props_ = {"failover": r["failover"] == "true", "source": "cmdb_export", "rel_type": r["type"]}
            if et == "REPLICATES_TO":
                props_["mode"] = r["mode"]
            a, b = r["parent"], r["child"]
            if et == "STORES_ON":  # CMDB parent = storage provider; graph edge points consumer -> storage
                a, b = b, a
            by_type.setdefault(et, []).append({"a": a, "b": b, "p": props_})
        for et, rows in sorted(by_type.items()):
            self.run(f"UNWIND $rows AS r MATCH (a:Component {{name: r.a}}) MATCH (b:Component {{name: r.b}}) "
                     f"MERGE (a)-[e:{et}]->(b) SET e += r.p", rows)

    def changes(self):
        rows = [typed(r) for r in read_csv(self.norm("change_request.csv"))]
        self.run("UNWIND $rows AS r MERGE (c:Change {number: r.number}) SET c += r "
                 "WITH c, r MATCH (x:Component {name: r.cmdb_ci}) MERGE (c)-[:TARGETS]->(x)", rows)

    def identity(self):
        users = [typed(r) for r in read_csv(self.path("identity", "users.csv"))]
        self.run("UNWIND $rows AS r MERGE (u:User {id: r.id}) SET u += r", users)
        workers = [{"id": w["id"], "status": w["status"], "effective": w["effective_date"]}
                   for w in read_csv(self.norm("workers.csv"))]  # hr_feed: employment status behind the account
        self.run("UNWIND $rows AS r MATCH (u:User {id: r.id}) SET u.status = r.status, "
                 "u.hr_status_effective = r.effective, u.status_source = 'hr_feed'", workers)
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

    # ---- evidence layer (normalised connector output) ---------------------

    def discovery(self):
        nodes, links = [], []
        for rec in read_jsonl(self.norm("discovered.jsonl")):
            nodes.append({k: v for k, v in rec.items() if v not in ("", None) and k not in ("ci", "match_rule")})
            if rec["ci"]:
                links.append({"k": rec["key"], "c": rec["ci"], "how": rec["match_rule"]})
        self.run("UNWIND $rows AS r MERGE (d:Discovered {key: r.key}) SET d += r", nodes)
        self.run("UNWIND $rows AS r MATCH (d:Discovered {key: r.k}) MATCH (c:Component {name: r.c}) "
                 "MERGE (d)-[e:IDENTIFIES]->(c) SET e.matched_by = r.how", links)
        subnets = {}
        for r in read_csv(self.norm("scan_coverage.csv")):
            if r["scanned"] == "true":
                subnets.setdefault(r["site"], []).append(r["subnet"])
        self.run("UNWIND $rows AS r MATCH (s:Site {id: r.s}) SET s.scanned_subnets = r.n",
                 [{"s": k, "n": v} for k, v in sorted(subnets.items())])
        # IP-in-CIDR is not expressible in Cypher: record each CI's scanned subnet at ingest.
        nets = {s: [ipaddress.ip_network(n) for n in v] for s, v in subnets.items()}
        rows = []
        for n, r in self.cis.items():
            if r["ip_address"]:
                ip = ipaddress.ip_address(r["ip_address"])
                hit = next((str(x) for x in nets.get(r["site"], []) if ip in x), None)
                if hit:
                    rows.append({"c": n, "s": hit})
        self.run("UNWIND $rows AS r MATCH (c:Component {name: r.c}) SET c.scanned_subnet = r.s", rows)
        listen = [{"c": r["ci"], "p": f"{r['ip']}:{r['port']}"} for r in read_jsonl(self.norm("listening.jsonl"))]
        by_ci = {}
        for r in listen:
            by_ci.setdefault(r["c"], []).append(r["p"])
        self.run("UNWIND $rows AS r MATCH (c:Component {name: r.c}) SET c.listening = r.p",
                 [{"c": c, "p": sorted(v)} for c, v in sorted(by_ci.items())])

    def endpoints(self, rows):
        """Ends of evidence rows: (client, server) node names; unmatched ends become
        :UnregisteredEndpoint (shadow-IT / unregistered-app candidates)."""
        out = []
        for r in rows:
            ends = []
            for side in ("client", "server"):
                ci = r.get(side)
                if ci is None:
                    name = r.get(f"{side}_name") or r.get(f"{side}_ip")
                    if not name:
                        break
                    self.unresolved[name] += 1
                    self.unregistered[name] = r.get(f"{side}_ip", "")
                    ci = name
                ends.append(ci)
            if len(ends) == 2:
                out.append((ends[0], ends[1], r))
        return out

    def flows(self):
        calls = self.endpoints(list(read_jsonl(self.norm("calls.jsonl"))))
        net = self.endpoints(list(read_jsonl(self.norm("netflow.jsonl"))))
        self.run("UNWIND $rows AS r MERGE (x:UnregisteredEndpoint {name: r.n}) SET x.ip_address = r.ip, "
                 "x.source = 'netflow'", [{"n": n, "ip": ip} for n, ip in sorted(self.unregistered.items())])
        drop = ("client", "server", "client_name", "server_name", "client_ip", "server_ip")
        for edge, key, rows in (("OBSERVED_CALLS", "source", calls), ("OBSERVED_NETFLOW", "port", net)):
            items = [{"a": a, "b": b, "k": r[key], "p": props(r, drop), "ua": a in self.unregistered,
                      "ub": b in self.unregistered} for a, b, r in rows]
            for ua, ub in ((False, False), (True, False), (False, True), (True, True)):  # indexed label per end
                la, lb = ("UnregisteredEndpoint" if ua else "Component"), ("UnregisteredEndpoint" if ub else "Component")
                self.run(f"UNWIND $rows AS r MATCH (a:{la} {{name: r.a}}) MATCH (b:{lb} {{name: r.b}}) "
                         f"MERGE (a)-[e:{edge} {{{key}: r.k}}]->(b) SET e += r.p",
                         [x for x in items if (x["ua"], x["ub"]) == (ua, ub)])

    def replication(self):
        rows = [{"a": r["source_array"], "b": r["target_array"], "p": props(r, ("source_array", "target_array"))}
                for r in read_jsonl(self.norm("replication.jsonl")) if r["source_array"] and r["target_array"]]
        self.run("UNWIND $rows AS r MATCH (a:Component {name: r.a}) MATCH (b:Component {name: r.b}) "
                 "MERGE (a)-[e:OBSERVED_REPLICATES_TO]->(b) SET e += r.p, e.last_seen = r.p.last_transfer", rows)

    def backups(self):
        rows = [{"a": r["object"], "b": r["target"], "t": r["job_type"], "p": props(r, ("object", "target"))}
                for r in read_jsonl(self.norm("backups.jsonl"))]
        self.run("UNWIND $rows AS r MATCH (a:Component {name: r.a}) MATCH (b:Component {name: r.b}) "
                 "MERGE (a)-[e:OBSERVED_BACKUP_TO {job_type: r.t}]->(b) SET e += r.p", rows)

    def access(self):
        rows = [{"u": r["user"], "c": r["target"], "a": r["action"], "p": props(r, ("user", "target", "registered"))}
                for r in read_jsonl(self.norm("access.jsonl"))]
        self.run("UNWIND $rows AS r MATCH (u:User {id: r.u}) MATCH (c:Component {name: r.c}) "
                 "MERGE (u)-[e:OBSERVED_ACCESS {action: r.a}]->(c) SET e += r.p",
                 [x for x in rows if x["c"] in self.cis])
        self.run("UNWIND $rows AS r MATCH (u:User {id: r.u}) MERGE (c:UnregisteredEndpoint {name: r.c}) "
                 "MERGE (u)-[e:OBSERVED_ACCESS {action: r.a}]->(c) SET e += r.p",
                 [x for x in rows if x["c"] not in self.cis])

    def evidence(self):
        rows = [{"g": r["team"], "c": r["ci"], "p": props(r, ("team", "ci"))}
                for r in read_jsonl(self.norm("owners.jsonl"))]
        self.run("UNWIND $rows AS r MATCH (g:SupportGroup {name: r.g}) MATCH (c:Component {name: r.c}) "
                 "MERGE (g)-[e:OBSERVED_OWNER]->(c) SET e += r.p", rows)

    def write_aliases(self):
        al = {}
        for r in read_jsonl(self.norm("aliases.jsonl")):
            al.setdefault(r["ci"], set()).add(r["alias"])
        self.aliases = al
        rows = [{"c": c, "a": sorted(a)} for c, a in sorted(al.items())]
        self.run("UNWIND $rows AS r MATCH (c:Component {name: r.c}) SET c.aliases = r.a", rows)

    def tag_layers(self):
        """REALISM_SPEC 2: every node and edge says which layer it belongs to."""
        for q in LAYER_TAGS:
            self.g.query(q)

    def impact_layers(self):
        """Derived IMPACTS_*/AFFECTS_* edges (graph/impact.py), rebuilt on every load."""
        impact.build(self.g)

    # ---- driver ----------------------------------------------------------

    def prepare(self):
        """State the steps share, so a load can also run one step at a time (deploy.recover)."""
        self.aliases, self.unregistered = {}, {}
        self.cis = {r["name"]: r for r in read_csv(self.norm("cmdb_ci.csv"))}

    def load(self):
        t0 = time.time()
        self.aliases, self.unregistered = {}, {}
        self.indexes()
        for step in (self.reference, self.components, self.relationships, self.changes, self.identity,
                     self.discovery, self.flows, self.replication, self.backups, self.access, self.evidence,
                     self.write_aliases, self.impact_layers, self.tag_layers):
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
    ap.add_argument("--fresh", action="store_true",
                    help="delete the graph first (MERGE never removes nodes from an older data set)")
    a = ap.parse_args()
    ld = Loader(a.data, a.graph, a.host, a.port, a.batch)
    if a.fresh and a.graph in ld.db.list_graphs():
        ld.g.delete()
        ld.g = ld.db.select_graph(a.graph)
    secs = ld.load()
    res = ld.counts()
    res["load_seconds"] = secs
    res["unresolved_flow_endpoints"] = dict(ld.unresolved)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
