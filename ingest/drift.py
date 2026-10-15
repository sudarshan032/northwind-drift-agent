"""Drift catalogue (DATA_SPEC section 7).

`plan_drift(world)` returns a plan: a set of edits applied while deriving
views, plus `ground_truth`, one entry per injected drift. `NO_DRIFT` is the
empty plan (all views agree).

Each ground-truth entry carries `finding_keys`: the exact findings the
reference checker (`tests/oracle.py`) must report for it. Key shapes:

    ["undocumented", src, dst]     ["stale", parent, child, type]
    ["retired_linked", ci]         ["owner", ci]
    ["shadow", hostname, ip]       ["ghost", ci]
    ["attribute", ci, field]       ["protection", ci]
    ["backup_failing", ci]         ["residency", source, target]
    ["access", user, target]       ["unregistered_app", name]
D3 entries list the failover relationships that must NOT be flagged; D13
entries list `[alias, ci]` pairs that must resolve to the same CI.
"""

import copy
import ipaddress

from ingest.world import STORAGE, make_sys_id, rng_for

NO_DRIFT = {
    "cmdb_remove_rels": [], "cmdb_add_rels": [], "cmdb_add_cis": [], "cmdb_attr": {},
    "discovery_add": [], "discovery_hostname": {}, "flow_alias": {}, "backup_override": {},
    "replication_add": [], "access_add": [], "flows_add": [], "ground_truth": [],
}
DEP = "Depends on::Used by"


def empty_plan():
    return copy.deepcopy(NO_DRIFT)


def _rel_exists(world, a, b):
    return any({r["parent"], r["child"]} == {a, b} for r in world["rels"])


def _free_ip(world, prefix, start, used):
    for i in range(start, 255):
        ip = f"{prefix}.{i}"
        if ip not in used:
            used.add(ip)
            return ip
    raise ValueError(prefix)


def plan_drift(world):
    """Build the full deterministic drift plan for `world`."""
    seed = world["meta"]["seed"]
    r = rng_for(seed, "drift")
    by = {c["name"]: c for c in world["cis"]}
    used_ips = {c["ip_address"] for c in world["cis"] if c["ip_address"]}
    p = empty_plan()
    gt = p["ground_truth"]
    touched = {"lrc-db-01", "dc1-vm-lrc01", "dc1-host-07"}  # keep the headline chain clean

    def entry(id_, type_, desc, ci_ids, sources, expected, keys):
        gt.append({"id": id_, "type": type_, "description": desc, "ci_ids": ci_ids, "sources": sources,
                   "expected_finding": expected, "finding_keys": keys})
        touched.update(ci_ids)

    # ---- D1-HEADLINE: CMDB omits every ORU-CRIT-* -> lrc-db-01 dependency
    crit = sorted(rel["parent"] for rel in world["rels"]
                  if rel["child"] == "lrc-db-01" and rel["parent"].startswith("ORU-CRIT-"))
    for name in crit:
        p["cmdb_remove_rels"].append({"parent": name, "child": "lrc-db-01", "type": DEP})
    entry("D1-HEADLINE", "D1",
          f"All {len(crit)} ORU-CRIT-* critical-result routes stage messages in lrc-db-01 (VM dc1-vm-lrc01 "
          "on host dc1-host-07). CMDB links lrc-db-01 only to lis-reporting. CHG-HEADLINE patches dc1-host-07.",
          ["lrc-db-01", "dc1-vm-lrc01", "dc1-host-07", "lis-reporting"] + crit,
          ["flows_hourly", "cmdb_rel_ci", "change_request"], "undocumented_dependency",
          [["undocumented", n, "lrc-db-01"] for n in crit])

    # ---- D1: 7 more real dependencies missing from the CMDB
    d1 = [("pharm-app", "lis-db-01"), ("billing-app", "emr-db-01"), ("portal-app", "sched-app"),
          ("dwh-etl-app", "pacs-db-01"), ("h03-rtls", "emr-fhir-api"), ("h03-mri-01", "h03-pacs-cache"),
          ("h17-lab-01", "h17-labmw")]
    deps = {(rel["parent"], rel["child"]): rel for rel in world["rels"] if rel["type"] == DEP and rel["obs"]}
    d1 = [x for x in d1 if x in deps]
    pool = sorted(k for k in deps if not k[0].startswith(("ORU-CRIT", "LAB-RES", "DICOM", "VITALS")))
    while len(d1) < 7:
        x = r.choice(pool)
        if x not in d1:
            d1.append(x)
    for i, (a, b) in enumerate(d1, 1):
        leg = deps[(a, b)]["obs"][0]
        p["cmdb_remove_rels"].append({"parent": a, "child": b, "type": DEP})
        src = "netflow" if leg.get("channel") == "netflow" else "flows"
        entry(f"D1-{i:02d}", "D1", f"{a} depends on {b} ({src}); no CMDB relationship.", [a, b],
              [src, "cmdb_rel_ci"], "undocumented_dependency", [["undocumented", leg["src"], leg["dst"]]])

    # ---- D2: stale CMDB relationships (no traffic, not failover)
    d2 = [("ris-app", "sched-db-01"), ("pharm-app", "billing-db-01"), ("portal-app", "lis-app"),
          ("dwh-etl-app", "sched-db-01"), ("h03-emr-edge", "lis-app"), ("mon-app", "ie-db-01")]
    d2 = [x for x in d2 if x[0] in by and x[1] in by]
    apps = sorted(c["name"] for c in world["cis"] if c["label"] in ("Application", "Database")
                  and c["site"] in ("DC-PRI", "cloud-us-west"))
    while len(d2) < 6:
        x = (r.choice(apps), r.choice(apps))
        if x[0] != x[1] and x not in d2:
            d2.append(x)
    for i, (a, b) in enumerate(d2, 1):
        assert not _rel_exists(world, a, b), (a, b)
        p["cmdb_add_rels"].append({"parent": a, "child": b, "type": DEP, "failover": False, "mode": ""})
        entry(f"D2-{i:02d}", "D2", f"CMDB says {a} depends on {b}; no traffic in 14 days.", [a, b],
              ["cmdb_rel_ci", "flows"], "stale_relationship", [["stale", a, b, DEP]])

    # ---- D3: failover relationships with no traffic (must NOT be flagged)
    fo = [rel for rel in world["rels"] if rel["failover"]]
    for i, rel in enumerate(fo, 1):
        entry(f"D3-{i:02d}", "D3", f"{rel['parent']} -> {rel['child']} ({rel['type']}) is a failover path "
              "(failover=true): quiet until failover, not stale.", [rel["parent"], rel["child"]],
              ["cmdb_rel_ci"], "none", [["stale", rel["parent"], rel["child"], rel["type"]]])

    # ---- D4: retired CIs still holding relationships (CMDB only)
    retired = [
        ("ris-app-legacy", "cmdb_ci_appl", "DC-PRI", "NW-Imaging-Systems", "ris-db-01", DEP),
        ("lab-interface-legacy", "u_cmdb_ci_hl7_interface", "DC-PRI", "NW-Integration", "lis-app", DEP),
        ("dc1-host-25", "cmdb_ci_esx_server", "DC-PRI", "NW-Datacenter-Infra", "arr-pri-b-03", STORAGE),
    ]
    for i, (name, cls, site, owner, target, rtype) in enumerate(retired, 1):
        ci = _cmdb_only_ci(world, seed, name, cls, site, owner, status="retired")
        if cls == "cmdb_ci_esx_server":
            ci.update(ip_address=_free_ip(world, "10.10.1", 200, used_ips), os="VMware ESXi", os_version="6.7 U3",
                      model="PowerEdge R740", manufacturer="Dell", serial_number="9XK2LM3")
        p["cmdb_add_cis"].append(ci)
        a, b = (target, name) if rtype == STORAGE else (name, target)  # storage: array is the parent
        p["cmdb_add_rels"].append({"parent": a, "child": b, "type": rtype, "failover": False, "mode": ""})
        entry(f"D4-{i:02d}", "D4", f"{name} is retired but still linked to {target}.", [name, target],
              ["cmdb_ci", "cmdb_rel_ci"], "retired_still_linked", [["retired_linked", name]])

    # ---- D5: owner drift (CMDB support_group is the former team)
    d5 = {"lrc-db-01": "NW-Lab-Systems", "ie-db-01": "NW-DBA", "portal-app": "NW-Digital-Portal",
          "siem-app": "NW-SecOps", "dwh-etl-app": "NW-Data-Analytics", "ris-db-01": "NW-Imaging-Systems"}
    for i, (name, old) in enumerate(sorted(d5.items()), 1):
        assert by[name]["support_group"] != old
        p["cmdb_attr"].setdefault(name, {})["support_group"] = old
        entry(f"D5-{i:02d}", "D5", f"CMDB support group of {name} is {old}; incidents and changes are handled "
              f"by {by[name]['support_group']}.", [name], ["cmdb_ci", "ownership_evidence"], "owner_drift",
              [["owner", name]])

    # ---- D6: discovered devices with no CMDB record (shadow IT)
    # Small sites for shadow devices: clinics; the demo subset has none, so community hospitals.
    clinics = sorted(s["id"] for s in world["sites"] if s["type"] == "clinic") or \
        sorted(s["id"] for s in world["sites"] if s["type"] == "community_hospital" and s["id"] != "H17")
    med_sites = sorted({sn["site"] for sn in world["subnets"] if sn["kind"] == "medical"} & set(clinics))
    c1, c2, c3 = r.sample(clinics, 3)
    c4 = r.choice([c for c in med_sites if c not in (c1, c2, c3)])

    def mgmt(cid, clinic_start, hosp_start):
        """(prefix, first host) in the site's management subnet."""
        if cid.startswith("C"):
            j = int(cid[1:]) - 1
            return f"172.{16 + j // 256}.{j % 256}", clinic_start
        return f"10.{100 + int(cid[1:])}.0", hosp_start

    def med(cid):
        if cid.startswith("C"):
            return mgmt(cid, 70, 0)
        return f"10.{100 + int(cid[1:])}.8", 220

    shadow = [
        (c1, *mgmt(c1, 40, 200), "WIN-FILESRV01", "Windows Server", "2012 R2", "OptiPlex 9020 (repurposed)", "Dell"),
        (c2, *mgmt(c2, 41, 201), "NAS-BACKUP", "DSM", "7.1", "DiskStation DS920+", "Synology"),
        ("H03", "10.103.8", 240, "RADWS-3D-07", "Windows 10 Enterprise", "21H2", "Precision 7920 (3D imaging workstation)", "Dell"),
        ("H03", "10.103.0", 240, "AP-ROGUE-4F", "", "", "Archer AX55", "TP-Link"),
        (c3, *mgmt(c3, 42, 202), "SW-UNMANAGED", "", "", "TL-SG108", "TP-Link"),
        (c4, *med(c4), "US-PORTABLE-02", "Windows 10 IoT Enterprise", "LTSC 2019", "Vscan Air", "GE HealthCare"),
        ("H17", "10.117.1", 240, "h17-nas-radiology", "DSM", "7.2", "DiskStation DS1522+", "Synology"),
        ("DC-PRI", "10.10.4", 250, "raspberrypi", "Raspbian", "11", "Raspberry Pi 4 Model B", "Raspberry Pi"),
        ("DC-PRI", "10.10.7", 240, "dc1-vm-poc-ai01", "Ubuntu", "22.04", "VMware Virtual Platform", "VMware"),
        ("H03", "10.103.8", 241, "VENDOR-RAS-GW", "Linux", "5.10", "Remote service gateway", "Siemens Healthineers"),
    ]
    end = world["meta"]["snapshot_end"]
    for i, (site, prefix, start, host, os_, ver, model, vendor) in enumerate(shadow, 1):
        ip = _free_ip(world, prefix, start, used_ips)
        sn = next(x for x in world["subnets"] if x["site"] == site
                  and ipaddress.ip_address(ip) in ipaddress.ip_network(x["cidr"]))
        method = sn["method"]
        serial = "" if vendor in ("TP-Link",) else f"SH{make_sys_id(seed, host)[:10].upper()}"
        p["discovery_add"].append({"serial_number": serial, "hostname": host, "ip_address": ip, "os": os_,
                                   "os_version": ver, "model": model, "manufacturer": vendor, "site": site,
                                   "last_scanned": end.replace("23:59:59", "18:00:00"), "method": method})
        entry(f"D6-{i:02d}", "D6", f"Scanner found {host} ({model}) at {site} {ip}; no CMDB record.", [host],
              ["scan_results", "cmdb_ci"], "shadow_it", [["shadow", host, ip]])

    # ---- D7: CMDB CIs never discovered (ghosts) in scanned subnets
    ghosts = [
        ("h05-srv-old-07", "cmdb_ci_win_server", "H05", "10.105.1", 200, "NW-Regional-Infra-WA", ""),
        ("dc1-host-26", "cmdb_ci_esx_server", "DC-PRI", "10.10.1", 210, "NW-Datacenter-Infra", ""),
        (f"{c3.lower()}-acc-{'03' if c3.startswith('C') else '099'}", "cmdb_ci_ip_switch", c3, *mgmt(c3, 50, 210),
         f"NW-Network-{_state(world, c3)}", ""),
        ("h22-xray-03", "cmdb_ci_med_diagnostic_imaging", "H22", "10.122.8", 200,
         "NW-Clinical-Engineering-OR", "H22-RAD"),
        ("h03-prn-9001", "cmdb_ci_printer", "H03", "10.103.31", 200, "NW-Field-Services-WA", "H03-RAD"),
    ]
    for i, (name, cls, site, prefix, start, owner, dept) in enumerate(ghosts, 1):
        ci = _cmdb_only_ci(world, seed, name, cls, site, owner, status="operational")
        ci.update(ip_address=_free_ip(world, prefix, start, used_ips), department=dept,
                  serial_number=f"GH{make_sys_id(seed, name)[:8].upper()}")
        p["cmdb_add_cis"].append(ci)
        entry(f"D7-{i:02d}", "D7", f"{name} is operational in the CMDB at {site} {ci['ip_address']} "
              "(scanned subnet) but never discovered and has no traffic.", [name],
              ["cmdb_ci", "scan_results", "scan_coverage"], "ghost_ci", [["ghost", name]])

    # ---- D13 (chosen before D8 so their CIs are excluded there)
    d13 = [("dc1-vm-lis02", "discovery", "LIS-PROD-02.nw.local"),
           ("h03-ct-02", "netflow", "H03CT2"),
           ("h17-core-01", "discovery", "H17-CORE-SW01"),
           ("dc1-host-12", "discovery", "DC1ESX12.nw.local"),
           ("h44-xray-01", "both", ("H44-DR-ROOM1", "H44XR1"))]
    d13_entries = []
    for name, where, alias in d13:
        if name not in by:
            continue
        pairs = []
        if where in ("discovery", "both"):
            a = alias[0] if where == "both" else alias
            p["discovery_hostname"][name] = a
            pairs.append([a, name])
        if where in ("netflow", "both"):
            a = alias[1] if where == "both" else alias
            p["flow_alias"][name] = a
            pairs.append([a, name])
        d13_entries.append((name, {"discovery": ["scan_results", "cmdb_ci"], "netflow": ["netflow_hourly", "cmdb_ci"],
                                   "both": ["scan_results", "netflow_hourly", "cmdb_ci"]}[where], pairs))

    # ---- D8: attribute drift CMDB vs discovery
    cands = sorted(c["name"] for c in world["cis"] if c["discoverable"] and c["label"] in
                   ("Host", "VM", "NetworkDevice", "MedicalDevice") and c["name"] not in touched
                   and c["name"] not in p["discovery_hostname"] and c["name"] not in p["flow_alias"])
    picks = r.sample(cands, 8)
    for i, name in enumerate(picks, 1):
        c = by[name]
        if i <= 3:
            field, new = "os_version", {"VMware ESXi": "7.0 U3", "Windows Server": "2016",
                                        "Red Hat Enterprise Linux": "7.9", "IOS-XE": "16.12.4",
                                        "FortiOS": "6.4.9"}.get(c["os"], "1.0")
        elif i <= 6:
            field = "ip_address"
            prefix = c["ip_address"].rsplit(".", 1)[0]
            new = _free_ip(world, prefix, 230, used_ips)
        else:
            field, new = "serial_number", f"{c['serial_number'][:-3]}{r.randint(100, 999)}"
        if new == c[field]:
            new = new + "-old"
        p["cmdb_attr"].setdefault(name, {})[field] = new
        entry(f"D8-{i:02d}", "D8", f"CMDB {field} of {name} is {new}; discovery reports {c[field]}.", [name],
              ["cmdb_ci", "scan_results"], "attribute_drift", [["attribute", name, field]])

    # ---- D9: PHI data protected only by same-site array snapshots
    site_arrays = {c["site"]: c["name"] for c in world["cis"]
                   if c["label"] == "StorageArray" and c["site"].startswith("H")}
    local_dbs = sorted(c["name"] for c in world["cis"] if c["label"] == "Database" and c["site"] in site_arrays
                       and c["handles_phi"] and c["name"] not in touched)
    d9 = ["h17-labmw-db"] if "h17-labmw-db" in local_dbs else []
    d9 += r.sample([x for x in local_dbs if x not in d9], 4 - len(d9))
    for i, name in enumerate(d9, 1):
        arr = site_arrays[by[name]["site"]]
        p["backup_override"][name] = {"kind": "snapshot", "policy": "snap-local-daily", "target": arr}
        entry(f"D9-{i:02d}", "D9", f"{name} (PHI) is only protected by snapshots on {arr} at the same site; "
              "CMDB says backup_offsite=true.", [name, arr], ["backup_jobs", "replication_status", "cmdb_ci"],
              "protection_path_drift", [["protection", name]])

    # ---- D10: backups failing for more than 7 days
    holders = sorted(c["name"] for c in world["cis"] if c["data_holder"] and c["name"] not in touched)
    d10 = ["sched-db-01"] if "sched-db-01" in holders else []
    d10 += r.sample([x for x in holders if x not in d10 and by[x]["site"].startswith("H")], 3 - len(d10))
    for i, name in enumerate(d10, 1):
        p["backup_override"][name] = {"kind": "failing", "last_success_day": r.randint(1, 5)}
        entry(f"D10-{i:02d}", "D10", f"Backups of {name} have failed every night for more than 7 days.", [name],
              ["backup_jobs", "cmdb_ci"], "backup_failing", [["backup_failing", name]])

    # ---- D11: PHI dataset copied into cloud-central-india
    p["replication_add"].append(("vol-dwh-db-01-export", "stor-usw-01", "stor-cin-01", ("async", 3)))
    entry("D11-01", "D11", "An ad-hoc replication copies a PHI analytics volume (dwh-db-01) from stor-usw-01 "
          "into stor-cin-01 in cloud-central-india.", ["dwh-db-01", "stor-usw-01", "stor-cin-01"],
          ["replication_status"], "data_residency_violation", [["residency", "stor-usw-01", "stor-cin-01"]])

    # ---- D12: access with no declared grant path
    users = world["identity"]["users"]
    grp = {}
    for m in world["identity"]["memberships"]:
        grp.setdefault(m["group"], []).append(m["user"])
    ubyid = {u["id"]: u for u in users}
    act = lambda g: sorted(x for x in grp.get(g, []) if ubyid[x]["status"] == "active")  # noqa: E731
    clin = act("grp-hyd-clinical-apps-support")[-1]
    disabled = sorted(u["id"] for u in users if u["site"] == "H03" and u["status"] == "disabled")[0]
    radtech = act("grp-H17-RAD-tech")[0]
    cloudops = act("grp-hyd-cloud-ops")[-1]
    d12 = [(clin, "emr-db-01", "admin", ["2026-10-11T21:10:00Z", "2026-10-11T21:40:00Z",
                                         "2026-10-12T20:55:00Z", "2026-10-12T21:30:00Z"],
            "GCC-HYD clinical-apps support account ran admin queries on EMR production DB outside any approved change window."),
           (disabled, "emr-app", "login", ["2026-10-10T15:05:00Z", "2026-10-11T14:58:00Z", "2026-10-13T15:12:00Z"],
            "Disabled user (HR status) still logging in to the EMR from an H03 workstation."),
           (radtech, "pharm-app", "read", ["2026-10-09T17:20:00Z", "2026-10-12T18:02:00Z", "2026-10-13T16:45:00Z"],
            "H17 radiology technologist reading the pharmacy system with no grant."),
           (cloudops, "billing-db-01", "write", ["2026-10-08T06:30:00Z", "2026-10-08T07:10:00Z"],
            "GCC-HYD cloud-ops account writing to the billing database with no grant.")]
    for i, (uid, target, action, times, desc) in enumerate(d12, 1):
        for t in times:
            p["access_add"].append({"ts": t, "user": uid, "workstation": ubyid[uid]["workstation"],
                                    "target": target, "action": action})
        entry(f"D12-{i:02d}", "D12", desc, [target], ["access_log", "identity", "change_request"],
              "access_drift", [["access", uid, target]])

    for i, (name, sources, pairs) in enumerate(d13_entries, 1):
        entry(f"D13-{i:02d}", "D13", f"{name} appears as {', '.join(a for a, _ in pairs)} in "
              f"{' and '.join(sources[:-1])}; same serial / IP, same CI.", [name], sources, "resolve_as_same_ci", pairs)
    # ---- D14: unregistered third-party SMART on FHIR app used by H17 cardiology
    app, app_ip = "cardiorisk.example.com", "203.0.113.250"
    card = act("grp-H17-CARD-physician")
    if card and "emr-fhir-api" in by:
        fhir_ip = by["emr-fhir-api"].get("service_ip", "")
        sso_ip = by["sso-app"].get("service_ip", "")
        from datetime import datetime, timedelta, timezone
        start = datetime(2026, 10, 5, tzinfo=timezone.utc)
        for d in range(10):
            day = start + timedelta(days=d)
            if day.weekday() >= 5:
                continue
            for h in range(15, 24):  # 08:00-17:00 Pacific clinic hours
                t = (day + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%SZ")
                n = r.randint(5, 40)
                p["flows_add"].append({"src_name": app, "src_ip": app_ip, "dst_name": "emr-fhir-api",
                                       "dst_ip": fhir_ip, "hour": t, "protocol": "https",
                                       "msg_type": "FHIR R4 Observation/Condition read", "count": n,
                                       "errors": 0, "p95_ms": round(r.uniform(150, 400), 1)})
                p["flows_add"].append({"src_name": app, "src_ip": app_ip, "dst_name": "sso-app", "dst_ip": sso_ip,
                                       "hour": t, "protocol": "oidc", "msg_type": "SMART launch token",
                                       "count": max(1, n // 8), "errors": 0, "p95_ms": 90.0})
            for uid in sorted(r.sample(card, min(4, len(card)))):
                t = (day + timedelta(hours=16, minutes=r.randint(0, 300))).strftime("%Y-%m-%dT%H:%M:%SZ")
                p["access_add"].append({"ts": t, "user": uid, "workstation": ubyid[uid]["workstation"],
                                        "target": app, "action": "launch"})
        entry("D14-01", "D14", "Unregistered third-party SMART on FHIR app (cardiorisk.example.com) launched by "
              "H17 cardiology physicians (grp-H17-CARD-physician) reads EMR data through emr-fhir-api; it has "
              "no CMDB record and no vendor review.", ["emr-fhir-api", "sso-app"],
              ["flows_hourly", "access_log", "identity", "cmdb_ci"], "unregistered_app",
              [["unregistered_app", app]])
    return p


def _state(world, site_id):
    return next(s["state"] for s in world["sites"] if s["id"] == site_id)


def _cmdb_only_ci(world, seed, name, cls, site, owner, status):
    """A CMDB record with no counterpart in the truth model."""
    from ingest.world import CLASS_LABEL
    return {"sys_id": make_sys_id(seed, name), "name": name, "class": cls, "label": CLASS_LABEL[cls],
            "site": site, "department": "", "environment": "production", "status": status,
            "support_group": owner, "criticality": "3 - less critical", "handles_phi": False,
            "data_residency": "US", "serial_number": "", "os": "", "os_version": "", "model": "",
            "manufacturer": "", "ip_address": "", "rpo_minutes": "", "rto_hours": "", "backup_policy": "",
            "backup_offsite": False, "patchable": True, "patient_safety_impact": "none", "count": "",
            "description": "Decommissioned" if status == "retired" else ""}
