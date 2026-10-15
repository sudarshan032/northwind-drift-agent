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
"""

import csv
import json
import os
import re
from datetime import datetime, timezone

from agent import findings as F
from graph import impact
from graph import queries as Q
from ingest.config import CMDB_PATCH_FIELDS
from ingest.load import BOOL_FIELDS, INT_FIELDS, REL_TYPES
from ingest.world import CLASS_LABEL

CMDB_EDGES = "CONTAINS|DEPENDS_ON|RUNS_ON|STORES_ON|REPLICATES_TO|BACKED_UP_BY|CONNECTS_THROUGH"
FIELD = re.compile(r"^[a-z_]+$")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rw_csv(path, fn):
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        cols, rows = reader.fieldnames, list(reader)
    rows = fn(rows)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


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


def write_ticket(runtime, ticket, finding):
    os.makedirs(runtime, exist_ok=True)
    rec = {"sys_created_on": _now(), "state": "New", "u_source_finding": finding["id"], **ticket}
    with open(os.path.join(runtime, "tickets.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    for team in ticket.get("notify", []):  # e.g. compliance notification for data residency
        with open(os.path.join(runtime, "notifications.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": _now(), "team": team, "message": ticket["short_description"],
                                "components": [ticket.get("cmdb_ci")], "channel": "compliance-notification"}) + "\n")
    return rec


def apply_op(g, data, op):
    """Apply one patch op to the CSVs and the declared graph layer."""
    cmdb = os.path.join(data, "cmdb")
    path = op["path"]
    if path == "/cmdb_rel_ci/-" and op["op"] == "add":
        v = op["value"]
        _rw_csv(f"{cmdb}/cmdb_rel_ci.csv", lambda rows: rows + [{**v}])
        et = REL_TYPES[v["type"]]
        a, b = (v["child"], v["parent"]) if et == "STORES_ON" else (v["parent"], v["child"])
        g.query(f"MATCH (a:Component {{name: $a}}), (b:Component {{name: $b}}) "
                f"MERGE (a)-[e:{et}]->(b) SET e.failover = $fo", {"a": a, "b": b, "fo": v["failover"] == "true"})
    elif path == "/cmdb_rel_ci" and op["op"] == "remove":
        m = op["match"]
        if "any_end" in m:
            n = m["any_end"]
            _rw_csv(f"{cmdb}/cmdb_rel_ci.csv", lambda rows: [r for r in rows if n not in (r["parent"], r["child"])])
            g.query(f"MATCH (c:Component {{name: $n}})-[e:{CMDB_EDGES}]-(:Component) DELETE e", {"n": n})
        else:
            _rw_csv(f"{cmdb}/cmdb_rel_ci.csv", lambda rows: [
                r for r in rows if (r["parent"], r["child"], r["type"]) != (m["parent"], m["child"], m["type"])])
            et = REL_TYPES[m["type"]]
            a, b = (m["child"], m["parent"]) if et == "STORES_ON" else (m["parent"], m["child"])
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
        _rw_csv(f"{cmdb}/cmdb_ci.csv", upd)
        g.query(f"MATCH (c:Component {{name: $n}}) SET c.{field} = $v", {"n": name, "v": _typed(field, value)})
        if field == "support_group":
            g.query("MATCH (c:Component {name: $n})-[e:MANAGED_BY]->() DELETE e", {"n": name})
            g.query("MATCH (c:Component {name: $n}), (s:SupportGroup {name: $v}) MERGE (c)-[:MANAGED_BY]->(s)",
                    {"n": name, "v": value})
    elif path == "/cmdb_ci/-" and op["op"] == "add":
        v = dict(op["value"])
        v.setdefault("sys_id", F.hashlib.md5(f"approved:{v['name']}".encode()).hexdigest())
        _rw_csv(f"{cmdb}/cmdb_ci.csv", lambda rows: rows + [v])
        label = CLASS_LABEL[v["class"]]
        props = {k: _typed(k, x) for k, x in v.items() if x != ""}
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


def audit(runtime, event):
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
    for op in f["patch"]:
        apply_op(g, data, op)
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
