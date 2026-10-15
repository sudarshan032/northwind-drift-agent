"""Human approval of a finding's proposed CMDB patch.

`approve` applies the patch to the CMDB CSVs and the declared graph layer,
rebuilds the impact layers, re-runs the finding's detector, marks the
finding `approved` (with `resolved` = its keys no longer detected) and
appends to `audit.jsonl`. `reject` only changes the finding status.
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
    for op in f["patch"]:
        apply_op(g, data, op)
    impact.build(g)
    rows = Q.detect(g, f["type"])
    still = {tuple(r["key"]) for r in rows} if isinstance(rows, list) else set()
    resolved = not any(tuple(k) in still for k in f["keys"])
    F.set_fields(g, fid, status="approved", resolved=resolved, decided_at=_now(), decided_by=approver)
    audit(runtime, {"action": "approve", "finding": fid, "type": f["type"], "patch": f["patch"],
                    "resolved": resolved, "by": approver})
    return {"id": fid, "status": "approved", "resolved": resolved, "applied_ops": len(f["patch"])}


def reject(g, runtime, fid, approver="human", reason=""):
    f = F.get_finding(g, fid)
    if f is None:
        return {"error": "no such finding", "id": fid}
    F.set_fields(g, fid, status="rejected", decided_at=_now(), decided_by=approver, reason=reason)
    audit(runtime, {"action": "reject", "finding": fid, "type": f["type"], "by": approver, "reason": reason})
    return {"id": fid, "status": "rejected"}
