"""cmdb_export: ServiceNow CMDB table exports (CSV), references as sys_ids.

Raw (data/raw/cmdb_export/): cmdb_ci.csv, cmdb_rel_ci.csv, cmdb_rel_type.csv,
cmn_location.csv, cmn_department.csv, sys_user_group.csv, change_request.csv.
Field names follow the ServiceNow tables (RESEARCH_NOTES R9): `sys_class_name`,
`install_status` (1 Installed, 7 Retired), `used_for`, `location` /
`department` / `support_group` as sys_id references, custom fields `u_*`.
cmdb_rel_ci rows reference parent, child and type by sys_id, as a real
"export with sys_ids" does.

Connector output (normalised, internal names): cmdb_ci, cmdb_rel_ci (names),
cmn_location, cmn_department, sys_user_group, change_request.
"""

import hashlib
import os

from ingest.sources.common import raw_dir, read_csv, write_csv

INSTALL_STATUS = {"operational": "1", "retired": "7"}       # 1 Installed, 7 Retired (raw choice values)
INSTALL_STATUS_BACK = {"1": "operational", "7": "retired"}
OPERATIONAL_STATUS = {"operational": "1", "retired": "6"}   # 1 Operational, 6 Retired
CHG_STATE = {"scheduled": "-2", "closed": "3", "implement": "-1", "new": "-5"}
CHG_RISK = {"high": "2", "moderate": "3", "low": "4"}
WELL_KNOWN_REL = {"Depends on::Used by": "1a9cb166f1571100a92eb60da2bce5c5"}  # OOB sys_id in every instance
USED_FOR = {"production": "Production", "test": "Test", "development": "Development"}
REL_TYPES = [("Contains::Contained by", "Contains", "Contained by"), ("Depends on::Used by", "Depends on", "Used by"),
             ("Runs on::Runs", "Runs on", "Runs"), ("Provides storage for::Stored on", "Provides storage for", "Stored on"),
             ("Replicates to::Replica of", "Replicates to", "Replica of"),
             ("Backed up by::Backs up", "Backed up by", "Backs up"), ("Connects to::Connected by", "Connects to", "Connected by")]
# internal column -> ServiceNow export column
CI_FIELDS = [("sys_id", "sys_id"), ("name", "name"), ("class", "sys_class_name"), ("status", "install_status"),
             ("environment", "used_for"), ("site", "location"), ("department", "department"),
             ("support_group", "support_group"), ("criticality", "busines_criticality"),
             ("serial_number", "serial_number"), ("ip_address", "ip_address"), ("os", "os"),
             ("os_version", "os_version"), ("model", "model_number"), ("manufacturer", "manufacturer"),
             ("description", "short_description"), ("handles_phi", "u_handles_phi"),
             ("data_residency", "u_data_residency"), ("rpo_minutes", "u_rpo_minutes"), ("rto_hours", "u_rto_hours"),
             ("backup_policy", "u_backup_policy"), ("backup_offsite", "u_backup_offsite"),
             ("patchable", "u_patchable"), ("patient_safety_impact", "u_patient_safety_impact"),
             ("count", "u_count"), ("role", "u_role"), ("message_type", "u_message_type"),
             ("protocol", "u_protocol"), ("source", "u_source_ci"), ("target", "u_target_ci"), ("via", "u_via_ci"),
             ("room", "u_room"), ("raid_level", "u_raid_level"), ("capacity_tb", "u_capacity_tb"),
             ("encrypted", "u_encrypted"), ("immutable", "u_immutable")]
REF_CI = {"source", "target", "via"}  # custom reference fields to other CIs


def sid(kind, key):
    """Deterministic 32-hex sys_id for reference tables (CIs keep theirs)."""
    return hashlib.md5(f"northwind:{kind}:{key}".encode()).hexdigest()


def write(cm, out):
    """`cm` is views.cmdb_view output (internal names). Writes ServiceNow-shaped exports."""
    d = raw_dir(out, "cmdb_export")
    loc = {r["sys_id"]: sid("cmn_location", r["sys_id"]) for r in cm["cmn_location"]}
    dept = {r["sys_id"]: sid("cmn_department", r["sys_id"]) for r in cm["cmn_department"]}
    grp = {r["name"]: sid("sys_user_group", r["name"]) for r in cm["sys_user_group"]}
    ci_sid = {r["name"]: r["sys_id"] for r in cm["cmdb_ci"]}
    rt = {n: WELL_KNOWN_REL.get(n, sid("cmdb_rel_type", n)) for n, _, _ in REL_TYPES}
    write_csv(os.path.join(d, "cmn_location.csv"),
              [{"sys_id": loc[r["sys_id"]], "name": r["name"], "full_name": r["full_name"], "city": r["city"],
                "state": r["state"], "country": r["country"], "u_site_type": r["type"], "u_site_subtype": r["subtype"],
                "u_beds": r["beds"], "u_utc_offset": r["utc_offset_hours"]} for r in cm["cmn_location"]],
              ["sys_id", "name", "full_name", "city", "state", "country", "u_site_type", "u_site_subtype", "u_beds",
               "u_utc_offset"])
    write_csv(os.path.join(d, "cmn_department.csv"),
              [{"sys_id": dept[r["sys_id"]], "id": r["sys_id"], "name": r["name"], "u_code": r["code"],
                "u_location": loc[r["location"]]} for r in cm["cmn_department"]],
              ["sys_id", "id", "name", "u_code", "u_location"])
    write_csv(os.path.join(d, "sys_user_group.csv"),
              [{"sys_id": grp[r["name"]], "name": r["name"], "description": r["description"], "type": r["type"],
                "active": "true"} for r in cm["sys_user_group"]], ["sys_id", "name", "description", "type", "active"])
    write_csv(os.path.join(d, "cmdb_rel_type.csv"),
              [{"sys_id": rt[n], "name": n, "parent_descriptor": p, "child_descriptor": c} for n, p, c in REL_TYPES],
              ["sys_id", "name", "parent_descriptor", "child_descriptor"])
    rows = []
    for r in cm["cmdb_ci"]:
        x = {"mac_address": "", "operational_status": OPERATIONAL_STATUS[r["status"]]}
        for internal, col in CI_FIELDS:
            v = r.get(internal, "")
            if internal == "status":
                v = INSTALL_STATUS[v]
            elif internal == "environment":
                v = USED_FOR.get(v, v)
            elif internal == "site":
                v = loc.get(v, "")
            elif internal == "department":
                v = dept.get(v, "")
            elif internal == "support_group":
                v = grp.get(v, "")
            elif internal in REF_CI:
                v = ci_sid.get(v, "")
            x[col] = v
        x["assignment_group"] = x["support_group"]
        rows.append(x)
    cols = [c for _, c in CI_FIELDS]
    cols = cols[:4] + ["operational_status"] + cols[4:8] + ["assignment_group"] + cols[8:11] + ["mac_address"] + cols[11:]
    write_csv(os.path.join(d, "cmdb_ci.csv"), rows, cols)
    write_csv(os.path.join(d, "cmdb_rel_ci.csv"),
              [{"sys_id": sid("cmdb_rel_ci", f"{r['parent']}|{r['child']}|{r['type']}"), "parent": ci_sid[r["parent"]],
                "child": ci_sid[r["child"]], "type": rt[r["type"]], "port": "", "percent_outage": "",
                "connection_strength": "always", "u_failover": r["failover"], "u_replication_mode": r["mode"]}
               for r in cm["cmdb_rel_ci"]],
              ["sys_id", "parent", "child", "type", "port", "percent_outage", "connection_strength", "u_failover",
               "u_replication_mode"])
    write_csv(os.path.join(d, "change_request.csv"),
              [{"sys_id": sid("change_request", c["number"]), "number": c["number"],
                "short_description": c["short_description"], "type": c["type"], "state": CHG_STATE[c["state"]],
                "approval": c["approval"], "cmdb_ci": ci_sid.get(c["cmdb_ci"], ""),
                "assignment_group": grp.get(c["assignment_group"], ""), "start_date": c["start_date"].replace("T", " ").rstrip("Z"),
                "end_date": c["end_date"].replace("T", " ").rstrip("Z"), "risk": CHG_RISK[c["risk"]]}
               for c in cm["change_request"]],
              ["sys_id", "number", "short_description", "type", "state", "approval", "cmdb_ci", "assignment_group",
               "start_date", "end_date", "risk"])


def read(data):
    """Raw exports -> normalised tables keyed by names (what the loader uses)."""
    d = os.path.join(data, "raw", "cmdb_export")
    locs = read_csv(os.path.join(d, "cmn_location.csv"))
    loc = {r["sys_id"]: r["name"] for r in locs}
    depts = read_csv(os.path.join(d, "cmn_department.csv"))
    dept = {r["sys_id"]: r["id"] for r in depts}
    groups = read_csv(os.path.join(d, "sys_user_group.csv"))
    grp = {r["sys_id"]: r["name"] for r in groups}
    rtypes = {r["sys_id"]: r["name"] for r in read_csv(os.path.join(d, "cmdb_rel_type.csv"))}
    raw_cis = read_csv(os.path.join(d, "cmdb_ci.csv"))
    ci_name = {r["sys_id"]: r["name"] for r in raw_cis}
    cis = []
    for r in raw_cis:
        x = {}
        for internal, col in CI_FIELDS:
            v = r[col]
            if internal == "status":
                v = INSTALL_STATUS_BACK[v]
            elif internal == "environment":
                v = v.lower()
            elif internal == "site":
                v = loc.get(v, "")
            elif internal == "department":
                v = dept.get(v, "")
            elif internal == "support_group":
                v = grp.get(v, "")
            elif internal in REF_CI:
                v = ci_name.get(v, "")
            x[internal] = v
        cis.append(x)
    rels = [{"parent": ci_name[r["parent"]], "child": ci_name[r["child"]], "type": rtypes[r["type"]],
             "failover": r["u_failover"], "mode": r["u_replication_mode"]}
            for r in read_csv(os.path.join(d, "cmdb_rel_ci.csv"))]
    st = {v: k for k, v in CHG_STATE.items()}
    rk = {v: k for k, v in CHG_RISK.items()}
    changes = [{"number": c["number"], "short_description": c["short_description"], "type": c["type"],
                "state": st[c["state"]], "approval": c["approval"], "cmdb_ci": ci_name.get(c["cmdb_ci"], ""),
                "assignment_group": grp.get(c["assignment_group"], ""),
                "start_date": c["start_date"].replace(" ", "T") + "Z", "end_date": c["end_date"].replace(" ", "T") + "Z",
                "risk": rk[c["risk"]]} for c in read_csv(os.path.join(d, "change_request.csv"))]
    return {
        "cmdb_ci": cis, "cmdb_rel_ci": rels,
        "cmn_location": [{"sys_id": r["name"], "name": r["name"], "full_name": r["full_name"], "type": r["u_site_type"],
                          "subtype": r["u_site_subtype"], "city": r["city"], "state": r["state"],
                          "country": r["country"], "beds": r["u_beds"], "utc_offset_hours": r["u_utc_offset"]}
                         for r in locs],
        "cmn_department": [{"sys_id": r["id"], "name": r["name"], "code": r["u_code"], "location": loc[r["u_location"]]}
                           for r in depts],
        "sys_user_group": [{"name": r["name"], "description": r["description"], "type": r["type"]} for r in groups],
        "change_request": changes,
    }


def read_raw_index(data):
    """sys_id -> name maps for CIs and groups (used by sources that reference CMDB sys_ids)."""
    d = os.path.join(data, "raw", "cmdb_export")
    return {"ci": {r["sys_id"]: r["name"] for r in read_csv(os.path.join(d, "cmdb_ci.csv"))},
            "group": {r["sys_id"]: r["name"] for r in read_csv(os.path.join(d, "sys_user_group.csv"))}}
