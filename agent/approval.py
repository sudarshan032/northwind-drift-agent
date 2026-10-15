"""Human approval of a finding's proposed CMDB patch.

CMDB-patch findings: `approve` checks the patch against the write-back rule
(REALISM_SPEC 2), applies it to the CMDB CSVs and the declared graph layer,
rebuilds the impact layers, re-runs the finding's detector, marks the
finding `approved` (`resolved` = its keys no longer detected) and records the
CMDB update as a `change_request` in `tickets.jsonl`.
Ticket findings (D9-D12): `approve` writes the proposed `incident` /
`sn_si_incident` to `tickets.jsonl` and never touches the CMDB.
`reject` changes only the finding status, except that rejecting a D14
registration raises its security ticket instead. Everything is audited.
The LLM never calls these; only the API's approve/reject endpoints do.

Public mode (DEPLOY_MODE=public, no writable disk): the caller passes
`data=None` and a `GraphRecords` as `runtime`. The CMDB patch is applied to
the declared graph layer only (the graph is the demo's CMDB), and tickets,
notifications and the audit trail become `:Record` nodes in the graph, so a
demo reset (copying the pristine graph back) also clears them.
"""

import json
import os
import re
import time
from datetime import datetime, timezone

from agent import findings as F
from graph import impact
from graph import queries as Q
from ingest import connect
from ingest.config import CMDB_PATCH_FIELDS
from ingest.sources import cmdb_export
from ingest.views import CMDB_COLUMNS
from ingest.load import BOOL_FIELDS, INT_FIELDS, REL_TYPES
from ingest.world import CLASS_LABEL

CMDB_EDGES = "CONTAINS|DEPENDS_ON|RUNS_ON|STORES_ON|REPLICATES_TO|BACKED_UP_BY|CONNECTS_THROUGH"
FIELD = re.compile(r"^[a-z_]+$")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _typed(field, value):
    if field in BOOL_FIELDS:
        return str(value).lower() == "true"
    if field in INT_FIELDS and value != "":
        return int(float(value))
    return value


NEW_CI_FIELDS = {"name", "class", "site", "status", "ip_address", "serial_number", "description", "handles_phi",
                 "support_group", "sys_id"}


def validate(op):
    """Enforce the CMDB write-back rule; raise ValueError on anything else."""
    path = op.get("path", "")
    if path in ("/cmdb_rel_ci/-", "/cmdb_rel_ci"):
        return
    if path == "/cmdb_ci/-" and op["op"] == "add":
        extra = set(op["value"]) - NEW_CI_FIELDS
        if extra:
            raise ValueError(f"not a CMDB attribute for a new CI: {sorted(extra)}")
        return
    if path.startswith("/cmdb_ci/") and op["op"] == "replace":
        field = path.split("/", 3)[3]
        if field not in CMDB_PATCH_FIELDS:
            raise ValueError(f"write-back rule: {field} may not be patched (allowed: {sorted(CMDB_PATCH_FIELDS)})")
        return
    raise ValueError(f"write-back rule: unsupported op {op}")


class GraphRecords:
    """Tickets, notifications, audit events and undo logs stored in the graph (public mode)."""

    def __init__(self, g):
        self.g = g

    def add(self, kind, rec):
        self.g.query("CREATE (:Record {kind: $k, ts: $ts, seq: $seq, finding: $f, body: $b})",
                     {"k": kind, "ts": _now(), "seq": time.time_ns(),
                      "f": rec.get("u_source_finding") or rec.get("finding") or "", "b": json.dumps(rec, default=str)})

    def list(self, kind, limit=100):
        rows = self.g.query("MATCH (r:Record {kind: $k}) RETURN r.body ORDER BY r.seq DESC LIMIT $n",
                            {"k": kind, "n": limit}).result_set
        return [json.loads(r[0]) for r in rows]


def write_ticket(runtime, ticket, finding):
    rec = {"sys_created_on": _now(), "state": "New", "u_source_finding": finding["id"], **ticket}
    if isinstance(runtime, GraphRecords):
        runtime.add("ticket", rec)
        for team in ticket.get("notify", []):
            runtime.add("notification", {"ts": _now(), "team": team, "message": ticket["short_description"],
                                         "components": [ticket.get("cmdb_ci")], "channel": "compliance-notification",
                                         "finding": finding["id"]})
        return rec
    os.makedirs(runtime, exist_ok=True)
    with open(os.path.join(runtime, "tickets.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    for team in ticket.get("notify", []):  # e.g. compliance notification for data residency
        with open(os.path.join(runtime, "notifications.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), "team": team, "message": ticket["short_description"],
                                "components": [ticket.get("cmdb_ci")], "channel": "compliance-notification"}) + "\n")
    return rec


def _edges(g, where, params):
    """Declared CMDB edges matching `where` (on a, e, b), with properties, for the undo log."""
    rows = g.query(f"MATCH (a:Component)-[e:{CMDB_EDGES}]->(b:Component) WHERE {where} "
                   "RETURN a.name, type(e), b.name, properties(e)", params).result_set
    return [{"do": "create_edge", "a": r[0], "type": r[1], "b": r[2], "props": r[3]} for r in rows]


def apply_op(g, cm, op, undo=None):
    """Apply one patch op to the CMDB tables `cm` (cmdb_export connector shape) and the declared graph layer.
    With `undo` (a list), first append the operations that reverse it (public-mode demo reset)."""
    path = op["path"]
    undo = undo if undo is not None else []
    if path == "/cmdb_rel_ci/-" and op["op"] == "add":
        v = op["value"]
        cm["cmdb_rel_ci"].append({**v})
        et = REL_TYPES[v["type"]]
        a, b = (v["child"], v["parent"]) if et == "STORES_ON" else (v["parent"], v["child"])
        if not _edges(g, f"a.name = $a AND b.name = $b AND type(e) = '{et}'", {"a": a, "b": b}):
            undo.append({"do": "delete_edge", "a": a, "type": et, "b": b})
        g.query(f"MATCH (a:Component {{name: $a}}), (b:Component {{name: $b}}) "
                f"MERGE (a)-[e:{et}]->(b) SET e.failover = $fo", {"a": a, "b": b, "fo": v["failover"] == "true"})
    elif path == "/cmdb_rel_ci" and op["op"] == "remove":
        m = op["match"]
        if "any_end" in m:
            n = m["any_end"]
            undo.extend(_edges(g, "a.name = $n OR b.name = $n", {"n": n}))
            cm["cmdb_rel_ci"] = [r for r in cm["cmdb_rel_ci"] if n not in (r["parent"], r["child"])]
            g.query(f"MATCH (c:Component {{name: $n}})-[e:{CMDB_EDGES}]-(:Component) DELETE e", {"n": n})
        else:
            cm["cmdb_rel_ci"] = [r for r in cm["cmdb_rel_ci"]
                                 if (r["parent"], r["child"], r["type"]) != (m["parent"], m["child"], m["type"])]
            et = REL_TYPES[m["type"]]
            a, b = (m["child"], m["parent"]) if et == "STORES_ON" else (m["parent"], m["child"])
            undo.extend(_edges(g, f"a.name = $a AND b.name = $b AND type(e) = '{et}'", {"a": a, "b": b}))
            g.query(f"MATCH (:Component {{name: $a}})-[e:{et}]->(:Component {{name: $b}}) DELETE e", {"a": a, "b": b})
    elif path.startswith("/cmdb_ci/") and op["op"] == "replace":
        _, _, name, field = path.split("/", 3)
        if not FIELD.match(field):
            raise ValueError(f"bad field {field}")
        value = op["value"]

        def upd(rows):
            for r in rows:
                if r["name"] == name:
                    r[field] = value
            return rows
        cm["cmdb_ci"] = upd(cm["cmdb_ci"])
        old = g.query(f"MATCH (c:Component {{name: $n}}) OPTIONAL MATCH (c)-[:MANAGED_BY]->(s:SupportGroup) "
                      f"RETURN c.{field}, s.name", {"n": name}).result_set
        if old:
            undo.append({"do": "set_prop", "name": name, "field": field, "value": old[0][0]})
            if field == "support_group":
                undo.append({"do": "set_managed_by", "name": name, "group": old[0][1]})
        g.query(f"MATCH (c:Component {{name: $n}}) SET c.{field} = $v", {"n": name, "v": _typed(field, value)})
        if field == "support_group":
            g.query("MATCH (c:Component {name: $n})-[e:MANAGED_BY]->() DELETE e", {"n": name})
            g.query("MATCH (c:Component {name: $n}), (s:SupportGroup {name: $v}) MERGE (c)-[:MANAGED_BY]->(s)",
                    {"n": name, "v": value})
    elif path == "/cmdb_ci/-" and op["op"] == "add":
        v = dict(op["value"])
        v.setdefault("sys_id", F.hashlib.md5(f"approved:{v['name']}".encode()).hexdigest())
        cm["cmdb_ci"].append({**{k: "" for k in CMDB_COLUMNS}, "status": "operational", **v})
        label = CLASS_LABEL[v["class"]]
        props = {k: _typed(k, x) for k, x in v.items() if x != ""}
        ep = g.query("MATCH (x:UnregisteredEndpoint {name: $n}) OPTIONAL MATCH (x)-[l:LOCATED_AT]->() "
                     "OPTIONAL MATCH ()-[i:IDENTIFIES]->(x) RETURN properties(x), count(DISTINCT l), count(DISTINCT i)",
                     {"n": v["name"]}).result_set
        if ep and ep[0][0] is not None:
            undo.append({"do": "restore_endpoint", "name": v["name"], "props": ep[0][0], "label": label,
                         "had_located": ep[0][1] > 0, "had_identifies": ep[0][2] > 0})
        elif not g.query("MATCH (c:Component {name: $n}) RETURN 1", {"n": v["name"]}).result_set:
            undo.append({"do": "delete_ci", "name": v["name"]})
        # an endpoint seen in traffic becomes a registered CI (keeps its observed edges)
        g.query("MATCH (x:UnregisteredEndpoint {name: $n}) REMOVE x:UnregisteredEndpoint "
                f"SET x:Component:{label}", {"n": v["name"]})
        g.query(f"MERGE (c:Component:{label} {{name: $n}}) SET c += $p", {"n": v["name"], "p": props})
        g.query("MATCH (c:Component {name: $n}), (s:Site {id: $s}) MERGE (c)-[:LOCATED_AT]->(s)",
                {"n": v["name"], "s": v.get("site", "")})
        if v.get("ip_address"):
            g.query("MATCH (d:Discovered {hostname: $n, ip_address: $ip}), (c:Component {name: $n}) "
                    "MERGE (d)-[e:IDENTIFIES]->(c) SET e.matched_by = 'hostname+ip'",
                    {"n": v["name"], "ip": v["ip_address"]})
    else:
        raise ValueError(f"unsupported patch op {op}")


def revert(g, ops):
    """Apply undo operations (newest first is the caller's job), then the caller rebuilds the impact layer."""
    for u in ops:
        d = u["do"]
        if d == "delete_edge":
            g.query(f"MATCH (:Component {{name: $a}})-[e:{u['type']}]->(:Component {{name: $b}}) DELETE e", u)
        elif d == "create_edge":
            g.query(f"MATCH (a:Component {{name: $a}}), (b:Component {{name: $b}}) CREATE (a)-[e:{u['type']}]->(b) "
                    "SET e += $props", u)
        elif d == "set_prop":
            if not FIELD.match(u["field"]):
                raise ValueError(f"bad field {u['field']}")
            g.query(f"MATCH (c:Component {{name: $name}}) SET c.{u['field']} = $value", u)
        elif d == "set_managed_by":
            g.query("MATCH (c:Component {name: $name})-[e:MANAGED_BY]->() DELETE e", u)
            if u["group"]:
                g.query("MATCH (c:Component {name: $name}), (s:SupportGroup {name: $group}) MERGE (c)-[:MANAGED_BY]->(s)", u)
        elif d == "delete_ci":
            g.query("MATCH (c:Component {name: $name}) DETACH DELETE c", u)
        elif d == "restore_endpoint":
            g.query(f"MATCH (x:Component {{name: $name}}) REMOVE x:Component:{u['label']} SET x:UnregisteredEndpoint "
                    "SET x = $props", u)
            if not u["had_located"]:
                g.query("MATCH (x:UnregisteredEndpoint {name: $name})-[e:LOCATED_AT]->() DELETE e", u)
            if not u["had_identifies"]:
                g.query("MATCH ()-[e:IDENTIFIES]->(x:UnregisteredEndpoint {name: $name}) DELETE e", u)


def audit(runtime, event):
    if isinstance(runtime, GraphRecords):
        runtime.add("audit", {"ts": _now(), **event})
        return
    os.makedirs(runtime, exist_ok=True)
    with open(os.path.join(runtime, "audit.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _now(), **event}, default=str) + "\n")


def approve(g, data, runtime, fid, approver="human"):
    f = F.get_finding(g, fid)
    if f is None:
        return {"error": "no such finding", "id": fid}
    if f["status"] != "open":
        return {"error": f"finding is {f['status']}", "id": fid}
    if f.get("action") == "ticket":
        t = write_ticket(runtime, f["ticket"], f)
        F.set_fields(g, fid, status="approved", resolved=False, decided_at=_now(), decided_by=approver)
        audit(runtime, {"action": "approve", "finding": fid, "type": f["type"], "ticket": t["table"], "by": approver})
        return {"id": fid, "status": "approved", "resolved": False, "ticket": t, "applied_ops": 0}
    for op in f["patch"]:
        validate(op)
    # the CMDB is its export: edit it and re-run its connector (public mode: the graph only)
    cm = cmdb_export.read(data) if data is not None else {"cmdb_ci": [], "cmdb_rel_ci": []}
    undo = []
    for op in f["patch"]:
        apply_op(g, cm, op, undo)
    if isinstance(runtime, GraphRecords):
        runtime.add("undo", {"finding": fid, "ops": undo[::-1]})  # reversed: newest change undone first
    if data is not None:
        cmdb_export.write(cm, data)
        connect.write_cmdb(data, cmdb_export.read(data))
    impact.build(g)
    rows = Q.detect(g, f["type"])
    still = {tuple(r["key"]) for r in rows} if isinstance(rows, list) else set()
    resolved = not any(tuple(k) in still for k in f["keys"])
    F.set_fields(g, fid, status="approved", resolved=resolved, decided_at=_now(), decided_by=approver)
    chg = write_ticket(runtime, {"table": "change_request", "type": "standard", "state": "Closed",
                                 "short_description": f"CMDB update from finding {fid} ({f['kind']})",
                                 "cmdb_ci": (f.get("subjects") or [""])[0], "assignment_group": "NW-CMDB-Governance",
                                 "description": json.dumps(f["patch"])[:4000]}, f)
    audit(runtime, {"action": "approve", "finding": fid, "type": f["type"], "patch": f["patch"],
                    "resolved": resolved, "by": approver, "change_request": chg["short_description"]})
    return {"id": fid, "status": "approved", "resolved": resolved, "applied_ops": len(f["patch"])}


def reject(g, runtime, fid, approver="human", reason=""):
    f = F.get_finding(g, fid)
    if f is None:
        return {"error": "no such finding", "id": fid}
    F.set_fields(g, fid, status="rejected", decided_at=_now(), decided_by=approver, reason=reason)
    ticket = None
    if f["type"] == "D14" and f.get("ticket"):  # not registered -> it stays a security matter
        ticket = write_ticket(runtime, f["ticket"], f)
    audit(runtime, {"action": "reject", "finding": fid, "type": f["type"], "by": approver, "reason": reason,
                    "ticket": ticket and ticket["table"]})
    return {"id": fid, "status": "rejected", "ticket": ticket}
