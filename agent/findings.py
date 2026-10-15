"""Findings: built from detector rows, scored, given a proposed CMDB patch,
and stored as `(:Finding)-[:ABOUT]->(:Component)` nodes.

Findings are the only thing the agent writes to the graph. CMDB files and
declared edges change only through `agent.approval.approve`.

Patch format (JSON-Patch style, applied by agent.approval):
    {"op": "add",     "path": "/cmdb_rel_ci/-", "value": {parent, child, type, failover, mode}}
    {"op": "remove",  "path": "/cmdb_rel_ci", "match": {parent, child, type}}
    {"op": "replace", "path": "/cmdb_ci/<name>/<field>", "value": "..."}
    {"op": "add",     "path": "/cmdb_ci/-", "value": {name, class, site, status, ...}}
Write-back rule (REALISM_SPEC 2): a patch may only touch CI existence, the
attributes in ingest.config.CMDB_PATCH_FIELDS, or CMDB relationships. Every
other finding (D9 protection gap, D10 failing backups, D11 residency, D12
access) proposes a ServiceNow-shaped ticket instead (`action = "ticket"`).
"""

import hashlib
import json
import math
from datetime import datetime, timezone

from graph import queries as Q

DEP, REP = "Depends on::Used by", "Replicates to::Replica of"
SAFETY = {"high": 3, "medium": 2, "low": 1}
KIND = {"D1": "undocumented dependency", "D2": "stale relationship", "D4": "retired CI still linked",
        "D5": "owner drift", "D6": "shadow IT", "D7": "ghost CI", "D8": "attribute drift",
        "D9": "protection-path drift", "D10": "backups failing", "D11": "data residency",
        "D12": "access drift", "D14": "unregistered SMART on FHIR app"}


def finding_id(dtype, key):
    return f"F-{dtype}-{hashlib.sha1(json.dumps(key).encode()).hexdigest()[:10]}"


def group_rows(dtype, rows):
    """D1 rows sharing a callee with >= 3 callers become one finding (e.g. 51
    critical-result routes staging into lrc-db-01). Others stay single."""
    if dtype != "D1":
        return [[r] for r in rows]
    by_dst = {}
    for r in rows:
        by_dst.setdefault(r["key"][2], []).append(r)
    out = []
    for dst, rs in sorted(by_dst.items()):
        out += [rs] if len(rs) >= 3 else [[r] for r in rs]
    return out


def subjects(dtype, rows):
    """Components the finding is ABOUT (first = primary)."""
    k = rows[0]["key"]
    if dtype == "D1":
        return [k[2]] + [r["key"][1] for r in rows] if len(rows) > 1 else [k[1], k[2]]
    if dtype in ("D2", "D11"):
        return [k[1], k[2]]
    if dtype == "D12":
        return [k[2]]
    if dtype in ("D6", "D14"):
        return []
    return [k[1]]


def propose_patch(dtype, rows, g=None):
    """Proposed CMDB fix for one finding (empty list = operational fix)."""
    ops = []
    for r in rows:
        k = r["key"]
        if dtype == "D1":
            ops.append({"op": "add", "path": "/cmdb_rel_ci/-",
                        "value": {"parent": k[1], "child": k[2], "type": DEP, "failover": "false", "mode": ""}})
        elif dtype == "D2":
            ops.append({"op": "remove", "path": "/cmdb_rel_ci", "match": {"parent": k[1], "child": k[2], "type": k[3]}})
        elif dtype == "D4":
            ops.append({"op": "remove", "path": "/cmdb_rel_ci", "match": {"any_end": k[1]}})
        elif dtype == "D5":
            ops.append({"op": "replace", "path": f"/cmdb_ci/{k[1]}/support_group", "value": r["observed_owner"]})
        elif dtype == "D7":
            ops.append({"op": "replace", "path": f"/cmdb_ci/{k[1]}/status", "value": "retired"})
        elif dtype == "D8":
            ops.append({"op": "replace", "path": f"/cmdb_ci/{k[1]}/{k[2]}", "value": r["discovered"]})
        elif dtype == "D6":
            ops.append({"op": "add", "path": "/cmdb_ci/-", "value": {
                "name": k[1], "class": "cmdb_ci_computer", "site": r.get("site", ""), "status": "operational",
                "ip_address": k[2], "description": f"Registered from discovery ({r.get('model', '')}); owner to confirm"}})
        elif dtype == "D14":
            ops.append({"op": "add", "path": "/cmdb_ci/-", "value": {
                "name": k[1], "class": "cmdb_ci_cloud_service_account", "site": "EXTERNAL", "status": "operational",
                "handles_phi": "true", "description": "Third-party SMART on FHIR app; vendor review pending"}})
    return ops  # D9-D12: no CMDB edit (see propose_ticket)


TICKET_TYPES = {"D9", "D10", "D11", "D12"}


def propose_ticket(dtype, rows, evidence_sources):
    """ServiceNow-shaped ticket for findings that must not touch the CMDB
    (and the security ticket raised if a D14 registration is rejected)."""
    k, r0 = rows[0]["key"], rows[0]
    src = ", ".join(evidence_sources)
    if dtype == "D9":
        return {"table": "incident", "short_description": f"Offsite protection gap: {k[1]} has only same-site copies",
                "cmdb_ci": k[1], "assignment_group": "NW-Backup-Storage", "priority": "2 - High",
                "category": "Backup and recovery",
                "description": f"Copies stay at {r0.get('site')} ({', '.join(r0.get('backup_targets', []))}); no copy "
                               f"at another site or in the immutable vault. Evidence: {src}."}
    if dtype == "D10":
        return {"table": "incident", "short_description": f"Backups failing for {k[1]} (no success in 7 days)",
                "cmdb_ci": k[1], "assignment_group": "NW-Backup-Storage", "priority": "2 - High",
                "category": "Backup and recovery",
                "description": f"Last successful backup: {r0.get('last_success')}. Evidence: {src}."}
    if dtype == "D11":
        return {"table": "sn_si_incident", "short_description": f"PHI copied into cloud-central-india ({k[1]} -> {k[2]})",
                "cmdb_ci": k[2], "assignment_group": "NW-SecOps", "priority": "1 - Critical",
                "category": "Data residency / privacy", "notify": ["NW-Privacy-Compliance"],
                "description": f"Replication of {', '.join(r0.get('detail') or [])} from {k[1]} into {k[2]}. "
                               f"Evidence: {src}."}
    if dtype == "D12":
        reasons = ", ".join(r0.get("reasons", []))
        return {"table": "sn_si_incident", "short_description": f"Access drift: {k[1]} on {k[2]} ({reasons})",
                "cmdb_ci": k[2], "assignment_group": "NW-SecOps",
                "priority": "1 - Critical" if "admin_outside_change_window" in reasons else "2 - High",
                "category": "Unauthorized access", "description": f"User {k[1]}: {reasons}. Evidence: {src}."}
    if dtype == "D14":
        return {"table": "sn_si_incident", "short_description": f"Unregistered third-party app {k[1]} reading EMR data",
                "cmdb_ci": "emr-fhir-api", "assignment_group": "NW-SecOps", "priority": "2 - High",
                "category": "Third-party risk",
                "description": f"{k[1]} has no CMDB record and no vendor review; traffic on {r0.get('days_seen')} days "
                               f"by {r0.get('users')} users. Evidence: {src}."}
    return None


SOURCES = {"D1": ["apm_otel", "interface_engine", "netflow", "cmdb_export"], "D2": ["cmdb_export", "apm_otel",
           "interface_engine", "netflow", "storage_telemetry"], "D4": ["cmdb_export"],
           "D5": ["cmdb_export", "itsm_history"], "D6": ["discovery_scanner", "cmdb_export"],
           "D7": ["cmdb_export", "discovery_scanner"], "D8": ["discovery_scanner", "cmdb_export"],
           "D9": ["backup_software", "storage_telemetry", "cmdb_export"], "D10": ["backup_software", "cmdb_export"],
           "D11": ["storage_telemetry", "cmdb_export"], "D12": ["identity_sso", "db_audit", "hr_feed", "cmdb_export"],
           "D14": ["netflow", "identity_sso", "cmdb_export"]}


def evidence_sources(dtype, rows):
    """Sources behind a finding: from the evidence rows when they carry them, else the detector's inputs."""
    seen = sorted({s for r in rows for s in (r.get("sources") or [])})
    return seen + [s for s in ("cmdb_export",) if s not in seen] if seen else SOURCES[dtype]


IMPACT_TYPES = {"D1", "D5", "D8", "D9", "D10"}  # failure-type findings: weigh the real blast radius
EXPOSURE = {"D11": 5.0, "D14": 5.0, "D6": 2.5, "D2": 1.5, "D4": 1.5, "D7": 1.5}


def score(dtype, rows, impact):
    """Severity: for failure-type findings, patient-safety impact + log(real
    radius) + type weight; for exposure findings (residency, access,
    unregistered app, inventory gaps) a fixed PHI/security weight."""
    if dtype in IMPACT_TYPES:
        safety = max([SAFETY.get(x.get("safety") or "", 0) for x in impact.get("impacted", [])] + [0])
        size = impact.get("counts", {}).get("components", 0)
        s = safety + math.log10(size + 1) + (0.5 if len(rows) > 1 else 0)
        s += {"D1": 1.0, "D9": 1.5, "D10": 1.5}.get(dtype, 0)
    elif dtype == "D12":
        reasons = set(rows[0].get("reasons", []))
        s = 4.5 if reasons & {"disabled_user", "admin_outside_change_window"} else 3.5
    else:
        s = EXPOSURE[dtype]
    label = "critical" if s >= 5 else "high" if s >= 3.5 else "medium" if s >= 2 else "low"
    return round(s, 2), label


def template_explanation(dtype, rows, impact):
    k = rows[0]["key"]
    n = impact.get("counts", {})
    if dtype == "D1" and len(rows) > 1:
        return (f"{len(rows)} components call {k[2]} on 3+ days but the CMDB records none of these dependencies. "
                f"If {k[2]} fails, the real radius reaches {n.get('components', 0)} components, "
                f"{n.get('hospitals', 0)} hospitals and {n.get('business_services', 0)} business services.")
    r0 = rows[0]
    text = {"D1": lambda: f"{k[1]} calls {k[2]} ({r0.get('days_seen')} days observed); the CMDB has no relationship.",
            "D2": lambda: f"The CMDB says {k[1]} depends on {k[2]}, but nothing was observed between them in 14 days.",
            "D4": lambda: f"{k[1]} is retired in the CMDB yet still linked to {', '.join(r0.get('linked', []))}.",
            "D5": lambda: f"CMDB owner of {k[1]} is {r0.get('cmdb_owner')}, but {r0.get('observed_owner')} resolves "
                  f"its incidents and changes.",
            "D6": lambda: f"A scanner found {k[1]} at {k[2]} ({r0.get('model')}) with no CMDB record.",
            "D7": lambda: f"{k[1]} is operational in the CMDB but was never discovered in its scanned subnet and has no traffic.",
            "D8": lambda: f"CMDB {k[2]} of {k[1]} is {r0.get('cmdb')}; discovery reports {r0.get('discovered')}.",
            "D9": lambda: f"{k[1]} holds PHI and the CMDB claims offsite backup, but its only copies stay at "
                  f"{r0.get('site')} ({', '.join(r0.get('backup_targets', []))}).",
            "D10": lambda: f"{k[1]} has had no successful backup since {r0.get('last_success')}.",
            "D11": lambda: f"PHI data moves from {k[1]} into {k[2]} in cloud-central-india ({r0.get('via')}).",
            "D12": lambda: f"User {k[1]} accessed {k[2]}: {', '.join(r0.get('reasons', []))}.",
            "D14": lambda: f"{k[1]} has no CMDB record yet reads EMR data via the FHIR API ({r0.get('days_seen')} days, "
                   f"{r0.get('users')} users)."}
    return text[dtype]()


def build(g, detections):
    """Detector output {type: rows} -> list of finding dicts (not stored)."""
    out = []
    for dtype, rows in detections.items():
        if dtype == "D13" or not isinstance(rows, list):
            continue
        for grp in group_rows(dtype, rows):
            subj = subjects(dtype, grp)
            impact = Q.radius(g, subj[0], "real") if subj else {}
            impact = impact if impact.get("found") else {}
            s, label = score(dtype, grp, impact)
            key = grp[0]["key"] if len(grp) == 1 else ["undocumented_group", grp[0]["key"][2]]
            path = None
            if dtype == "D1":
                path = {"nodes": [grp[0]["key"][1], grp[0]["key"][2]], "edges": ["OBSERVED_CALLS"]}
            srcs = evidence_sources(dtype, grp)
            ticket = propose_ticket(dtype, grp, srcs)
            out.append({
                "id": finding_id(dtype, key), "type": dtype, "kind": KIND[dtype], "key": key,
                "keys": [r["key"] for r in grp], "subjects": subj, "severity": label, "score": s,
                "sources": srcs, "action": "ticket" if dtype in TICKET_TYPES else "cmdb_patch",
                "ticket": ticket,
                "evidence": {"rows": grp[:60], "real_radius": impact.get("counts", {}),
                             "high_safety_services": impact.get("high_safety_services", [])[:10]},
                "path": path, "patch": propose_patch(dtype, grp, g), "status": "open",
                "explanation": template_explanation(dtype, grp, impact), "explained_by": "template",
            })
    out.sort(key=lambda f: (-f["score"], f["id"]))
    return out


UPSERT = """
UNWIND $rows AS r
MERGE (f:Finding {id: r.id})
SET f.type = r.type, f.kind = r.kind, f.key_json = r.key_json, f.severity = r.severity, f.score = r.score,
    f.explanation = r.explanation, f.explained_by = r.explained_by, f.evidence_json = r.evidence_json,
    f.path_json = r.path_json, f.patch_json = r.patch_json, f.keys_json = r.keys_json,
    f.sources_json = r.sources_json, f.action = r.action, f.ticket_json = r.ticket_json, f.layer = 'reconciliation',
    f.status = coalesce(f.status, 'open'), f.created_at = coalesce(f.created_at, r.now), f.updated_at = r.now
WITH f, r
UNWIND (CASE WHEN size(r.subjects) = 0 THEN [null] ELSE r.subjects END) AS s
OPTIONAL MATCH (c:Component {name: s})
FOREACH (_ IN CASE WHEN c IS NULL THEN [] ELSE [1] END | MERGE (f)-[:ABOUT]->(c))
"""


def store(g, findings):
    """Upsert findings; status and created_at survive re-scans."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [{"id": f["id"], "type": f["type"], "kind": f["kind"], "key_json": json.dumps(f["key"]),
             "severity": f["severity"], "score": f["score"], "explanation": f["explanation"],
             "explained_by": f["explained_by"], "evidence_json": json.dumps(f["evidence"], default=str),
             "path_json": json.dumps(f["path"]), "patch_json": json.dumps(f["patch"]),
             "keys_json": json.dumps(f["keys"]), "subjects": f["subjects"], "now": now,
             "sources_json": json.dumps(f.get("sources", [])), "action": f.get("action", "cmdb_patch"),
             "ticket_json": json.dumps(f.get("ticket"))} for f in findings]
    for i in range(0, len(rows), 200):
        g.query(UPSERT, {"rows": rows[i:i + 200]})


def _decode(props):
    f = dict(props)
    for k in ("key", "keys", "evidence", "path", "patch", "judgement", "sources", "ticket"):
        if f.get(f"{k}_json") is not None:
            f[k] = json.loads(f.pop(f"{k}_json"))
    return f


def list_findings(g, status=None, limit=100):
    rows = g.query("MATCH (f:Finding) WHERE $status IS NULL OR f.status = $status "
                   "OPTIONAL MATCH (f)-[:ABOUT]->(c:Component) "
                   "WITH f, collect(c.name) AS subj "
                   "RETURN properties(f) AS p, subj, f.score AS score, f.id AS id ORDER BY score DESC, id LIMIT $limit",
                   {"status": status, "limit": limit}).result_set
    return [dict(_decode(p), subjects=s) for p, s, _, _ in rows]


def get_finding(g, fid):
    rows = g.query("MATCH (f:Finding {id: $id}) OPTIONAL MATCH (f)-[:ABOUT]->(c:Component) "
                   "RETURN properties(f), collect(c.name)", {"id": fid}).result_set
    return dict(_decode(rows[0][0]), subjects=rows[0][1]) if rows else None


def set_fields(g, fid, **fields):
    for k, v in list(fields.items()):
        if isinstance(v, (dict, list)):
            fields[f"{k}_json"] = json.dumps(v, default=str)
            del fields[k]
    g.query("MATCH (f:Finding {id: $id}) SET f += $p", {"id": fid, "p": fields})
