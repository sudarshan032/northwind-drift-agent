"""Derive every view (CMDB, discovery, observed, identity, evidence) from
the truth model.

Views are derived from one world plus a drift plan (`ingest.drift`). With
the empty plan (`drift.NO_DRIFT`) all views agree; every disagreement comes
from a plan entry. Observed files are large, so they are streamed to disk
by `write_observed` instead of being built in memory.
"""

import gzip
import io
import ipaddress
import json
import re
import math
import os
from datetime import datetime, timedelta, timezone

from ingest.world import CENTRAL_SITES, DEMO_SITES, STORAGE, WINDOW_DAYS, rng_for

END = datetime(2026, 10, 14, 23, 59, 59, tzinfo=timezone.utc)
START = END - timedelta(days=WINDOW_DAYS) + timedelta(seconds=1)  # 2026-10-01T00:00:00Z
HOURS = [START + timedelta(hours=h) for h in range(WINDOW_DAYS * 24)]
HOURLY_SITES = set(DEMO_SITES) | set(CENTRAL_SITES)
FMT = "%Y-%m-%dT%H:%M:%SZ"

CMDB_COLUMNS = [
    "sys_id", "name", "class", "site", "department", "environment", "status", "support_group",
    "criticality", "handles_phi", "data_residency", "serial_number", "os", "os_version", "model",
    "manufacturer", "ip_address", "rpo_minutes", "rto_hours", "backup_policy", "backup_offsite",
    "patchable", "patient_safety_impact", "count", "role", "message_type", "protocol", "source",
    "target", "via", "room", "raid_level", "capacity_tb", "encrypted", "immutable", "description",
]
REL_COLUMNS = ["parent", "child", "type", "failover", "mode"]
EVIDENCE_LABELS = {"Application", "Interface", "Database", "VM", "Host", "StorageArray",
                   "BackupTarget", "NetworkDevice", "MedicalDevice"}
SERVICE_DESK = "NW-Service-Desk"


def ts(dt):
    return dt.strftime(FMT)


def fmt_value(v):
    """CSV cell formatting: booleans as true/false, None as empty."""
    if isinstance(v, bool):
        return "true" if v else "false"
    return "" if v is None else str(v)


# --------------------------------------------------------------------------
# CMDB (declared view)
# --------------------------------------------------------------------------


def csdm_class(c):
    """CSDM at export: per-site services are Service Offerings of a Business Service."""
    if c["class"] == "cmdb_ci_service_business" and re.fullmatch(r"svc-h\d\d-ed-critical-results", c["name"]):
        return "service_offering"
    return c["class"]


def cmdb_view(world, plan):
    """ServiceNow-shaped tables. Location, department and support group are
    reference columns on cmdb_ci (as in ServiceNow); the loader turns them
    into LOCATED_AT / IN_DEPARTMENT / MANAGED_BY edges."""
    cis = []
    for c in world["cis"] + plan["cmdb_add_cis"]:
        row = {k: fmt_value(c.get(k, "")) for k in CMDB_COLUMNS}
        row["class"] = csdm_class(c)
        for k, v in plan["cmdb_attr"].get(c["name"], {}).items():
            row[k] = fmt_value(v)
        cis.append(row)
    cis.sort(key=lambda r: r["name"])
    removed = {(r["parent"], r["child"], r["type"]) for r in plan["cmdb_remove_rels"]}
    rels = [{"parent": r["parent"], "child": r["child"], "type": r["type"],
             "failover": fmt_value(r["failover"]), "mode": r.get("mode", "")}
            for r in world["rels"] + plan["cmdb_add_rels"]
            if (r["parent"], r["child"], r["type"]) not in removed]
    rels.sort(key=lambda r: (r["parent"], r["child"], r["type"]))
    locations = [{"sys_id": s["id"], "name": s["id"], "full_name": s["name"], "type": s["type"],
                  "subtype": s["subtype"], "city": s["city"], "state": s["state"],
                  "country": "IN" if s["id"] in ("GCC-HYD", "cloud-central-india") else "US",
                  "beds": s["beds"] or "", "utc_offset_hours": s["tz"]}
                 for s in world["sites"]]
    locations.append({"sys_id": "EXTERNAL", "name": "EXTERNAL", "full_name": "External SaaS / partners",
                      "type": "external", "subtype": "", "city": "", "state": "", "country": "",
                      "beds": "", "utc_offset_hours": ""})
    depts = [{"sys_id": d["id"], "name": d["name"], "code": d["code"], "location": d["site"]}
             for d in world["departments"]]
    groups = [{"name": g["name"], "description": g["description"], "type": "support"}
              for g in world["support_groups"]]
    groups.append({"name": SERVICE_DESK, "description": "Enterprise service desk (tier 1)", "type": "support"})
    groups.sort(key=lambda g: g["name"])
    return {"cmdb_ci": cis, "cmdb_rel_ci": rels, "cmn_location": locations, "cmn_department": depts,
            "sys_user_group": groups, "change_request": world["changes"]}


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------


def _subnet_index(world):
    by_site = {}
    for sn in world["subnets"]:
        by_site.setdefault(sn["site"], []).append((ipaddress.ip_network(sn["cidr"]), sn))
    return by_site


def subnet_of(index, site, ip):
    if not ip:
        return None
    addr = ipaddress.ip_address(ip)
    for net, sn in index.get(site, []):
        if addr in net:
            return sn
    return None


def discovery_view(world, plan):
    """One scan record per discoverable CI (scanners see real hostnames,
    serials and addresses), plus coverage per subnet."""
    r = rng_for(world["meta"]["seed"], "discovery")
    index = _subnet_index(world)
    records = []
    for c in world["cis"]:
        if not c["discoverable"]:
            continue
        sn = subnet_of(index, c["site"], c["ip_address"])
        method = sn["method"] if sn else "active"
        age_h = r.randint(1, 24 if method == "passive" else 72)
        records.append({
            "serial_number": c["serial_number"],
            "hostname": plan["discovery_hostname"].get(c["name"], c["name"]),
            "ip_address": c["ip_address"], "os": c["os"], "os_version": c["os_version"],
            "model": c["model"], "manufacturer": c["manufacturer"], "site": c["site"],
            "last_scanned": ts(END - timedelta(hours=age_h)), "method": method,
        })
    records += plan["discovery_add"]
    records.sort(key=lambda x: (x["site"], x["hostname"], x["ip_address"]))
    found = {}
    for rec in records:
        sn = subnet_of(index, rec["site"], rec["ip_address"])
        if sn:
            found[sn["cidr"]] = found.get(sn["cidr"], 0) + 1
    endpoint_counts, fleet_counts = {}, {}
    for c in world["cis"]:
        if c["label"] == "EndpointGroup":
            endpoint_counts[c["site"]] = endpoint_counts.get(c["site"], 0) + int(c["count"])
        elif c["label"] == "MedicalDevice" and c["count"] != "":
            fleet_counts[c["site"]] = fleet_counts.get(c["site"], 0) + int(c["count"])
    coverage = []
    for sn in world["subnets"]:
        n = endpoint_counts.get(sn["site"], 0) if sn["method"] == "aggregate" else found.get(sn["cidr"], 0)
        if sn["kind"] == "medical":  # passive sensors also count bedside fleets
            n += fleet_counts.get(sn["site"], 0)
        scanned = sn.get("scanned", True)
        coverage.append({"subnet": sn["cidr"], "site": sn["site"], "kind": sn["kind"],
                         "scanned": "true" if scanned else "false", "method": sn["method"],
                         "last_scan": ts(END - timedelta(hours=6)) if scanned else "",
                         "devices_found": n if scanned else 0})
    return {"scan_results": records, "scan_coverage": coverage}


# --------------------------------------------------------------------------
# Identity and evidence
# --------------------------------------------------------------------------


def identity_view(world, plan):
    idn = world["identity"]
    users = [{k: u[k] for k in ("id", "site", "department", "job_role", "status", "status_source")}
             for u in idn["users"]]
    groups = [{"id": g["id"], "site": g["site"], "department": g["department"], "job_role": g["job_role"],
               "roles": ";".join(g["roles"]), "count": g["member_count"], "scope": g["scope"]}
              for g in idn["groups"]]
    return {"users": users, "groups": groups, "memberships": idn["memberships"],
            "roles": idn["roles"], "grants": idn["grants"]}


def evidence_view(world, plan):
    """Synthetic ITSM history: who actually resolves incidents / implements
    changes per CI. The true owner always dominates; the service desk adds
    small noise."""
    r = rng_for(world["meta"]["seed"], "evidence")
    rows = []
    for c in world["cis"]:
        if c["label"] not in EVIDENCE_LABELS or c["count"] != "":
            continue
        inc, chg = r.randint(3, 25), r.randint(1, 8)
        last = ts(END - timedelta(days=r.randint(0, 60)))
        rows.append({"ci": c["name"], "team": c["support_group"], "incidents_resolved": inc,
                     "changes_implemented": chg, "last_activity": last, "period_days": 180})
        if r.random() < 0.3:
            rows.append({"ci": c["name"], "team": SERVICE_DESK, "incidents_resolved": r.randint(1, 2),
                         "changes_implemented": 0, "last_activity": last, "period_days": 180})
    return {"ownership_evidence": rows}


# --------------------------------------------------------------------------
# Observed: flows and netflow
# --------------------------------------------------------------------------

DIURNAL = [0.35, 0.3, 0.28, 0.27, 0.3, 0.4, 0.6, 0.8, 0.95, 1.0, 1.0, 0.98,
           0.95, 0.97, 1.0, 0.98, 0.9, 0.8, 0.7, 0.62, 0.55, 0.5, 0.45, 0.4]
P95_MS = {"tds": 18, "oracle-net": 22, "postgres": 15, "tcp-superserver": 6, "mllp": 40, "dicom": 900,
          "https": 120, "saml": 80, "oidc": 70, "ldaps": 12, "kerberos": 8, "smtp": 300, "sftp": 2500,
          "syslog-tls": 5, "ipp": 400, "ddboost": 50, "scim": 200}


def factor(profile, local):
    """Traffic multiplier for a local datetime: diurnal shape plus weekend dips."""
    h, wd = local.hour, local.weekday()
    if profile == "24x7":
        return DIURNAL[h] * (0.8 if wd >= 5 else 1.0)
    if profile == "business":
        return (1.0 if 8 <= h < 18 else 0.05) * (0.15 if wd >= 5 else 1.0)
    if profile == "clinic":
        if wd < 5:
            return 1.0 if 8 <= h < 18 else 0.0
        return 0.5 if wd == 5 and 9 <= h < 13 else 0.0
    if profile == "nightly":
        return 1.0 if 1 <= h < 5 else 0.0
    raise ValueError(profile)


def _count(r, expected):
    """Integer count with the right mean (keeps rare routes realistic)."""
    base = math.floor(expected)
    return base + (1 if r.random() < expected - base else 0)


def leg_records(world, leg, hourly, alias):
    """Yield observed records for one leg (one hour or one day each)."""
    by = world["_by_name"]
    src, dst = by[leg["src"]], by[leg["dst"]]
    r = rng_for(world["meta"]["seed"], "leg", leg["src"], leg["dst"], leg.get("route", ""))
    tz = leg.get("tz", -7)
    netflow = leg.get("channel") == "netflow"
    proto = leg.get("protocol", "tcp")
    p95_base = P95_MS.get(proto, 50)
    err_rate = r.uniform(0.0005, 0.004)
    base = {"src_name": alias.get(src["name"], src["name"]), "src_ip": world["_ip"](src["name"]),
            "dst_name": alias.get(dst["name"], dst["name"]), "dst_ip": world["_ip"](dst["name"])}
    day_acc = None
    for t in HOURS:
        f = factor(leg["profile"], t + timedelta(hours=tz))
        n = _count(r, leg["rate"] * f * r.uniform(0.85, 1.15)) if f else 0
        if netflow:
            rec = {"port": leg.get("port", 0), "protocol": "tcp",
                   "bytes": int(n * leg.get("bytes_per", 1500) * r.uniform(0.7, 1.3)),
                   "flows": n}
        else:
            rec = {"protocol": proto, "msg_type": leg.get("msg_type", ""), "count": n,
                   "errors": _count(r, n * err_rate),
                   "p95_ms": round(p95_base * r.uniform(0.8, 1.3) * (1 + 0.3 * f), 1)}
            if leg.get("route"):
                rec["route"] = leg["route"]
        if hourly:
            if n and "msg_mix" in leg:
                for mt, share in leg["msg_mix"]:
                    k = _count(r, n * share)
                    if k:
                        yield dict(base, hour=ts(t), **dict(rec, msg_type=mt, count=k,
                                                            errors=_count(r, k * err_rate)))
            elif n:
                yield dict(base, hour=ts(t), **rec)
            continue
        assert "msg_mix" not in leg, "event mixes are only modelled on hourly (central) legs"
        if day_acc is None:
            day_acc = dict(base, day=t.strftime("%Y-%m-%d"), **rec)
        else:
            for k in ("count", "errors", "bytes", "flows"):
                if k in rec:
                    day_acc[k] += rec[k]
            if "p95_ms" in rec and n:
                day_acc["p95_ms"] = max(day_acc["p95_ms"], rec["p95_ms"])
        if t.hour == 23:
            if day_acc.get("count", day_acc.get("flows", 0)):
                yield day_acc
            day_acc = None


def is_hourly(world, leg):
    by = world["_by_name"]
    return by[leg["src"]]["site"] in HOURLY_SITES or by[leg["dst"]]["site"] in HOURLY_SITES


def noise_flows(world):
    """One-off health checks (tagged) and short ad-hoc flows (< 3 days):
    realistic noise that must not produce findings."""
    r = rng_for(world["meta"]["seed"], "noise")
    by = world["_by_name"]
    out = []
    targets = ["emr-app", "lis-app", "pacs-app", "h03-pacs-cache", "h17-labmw", "ris-app"]
    for i, t in enumerate(targets):
        hr = HOURS[24 * r.randint(0, 13) + r.randint(0, 23)]
        out.append({"src_name": "mon-app", "src_ip": world["_ip"]("mon-app"), "dst_name": t,
                    "dst_ip": world["_ip"](t), "hour": ts(hr), "protocol": "https", "msg_type": "GET /health",
                    "count": 1, "errors": 0, "p95_ms": 35.0, "health_check": True})
    for src, dst, days in (("lis-reporting", "pharm-db-01", [4]), ("sched-app", "ris-app", [6, 7])):
        if src not in by or dst not in by:
            continue
        for d in days:
            for h in (2, 3):
                out.append({"src_name": src, "src_ip": world["_ip"](src), "dst_name": dst,
                            "dst_ip": world["_ip"](dst), "hour": ts(HOURS[24 * d + h]), "protocol": "tds",
                            "msg_type": "", "count": r.randint(50, 400), "errors": 0, "p95_ms": 40.0})
    return out


# --------------------------------------------------------------------------
# Observed: backups, replication, access
# --------------------------------------------------------------------------


def backup_jobs(world, plan):
    """Nightly job per data-holding CI. Transient failures are followed by a
    success the next night (noise); plan overrides inject D9/D10."""
    r = rng_for(world["meta"]["seed"], "backup")
    holders = [c for c in world["cis"] if c["data_holder"]]
    transient = set(r.sample([c["name"] for c in holders if "weekly" not in c["backup_policy"]], 5))
    jobs = []
    for c in holders:
        tz = world["_site_tz"].get(c["site"], -7)
        hour_local = 1 + (int(c["sys_id"][:2], 16) % 4)
        size = float(c.get("size_gb", 200))
        fail_day = r.randint(2, 9) if c["name"] in transient else None
        override = plan["backup_override"].get(c["name"], {})
        for d in range(WINDOW_DAYS):
            day = START + timedelta(days=d)
            start = day + timedelta(hours=hour_local - tz, minutes=r.randint(0, 50))
            if start > END or start < START:
                continue
            if "weekly" in c["backup_policy"] and day.weekday() != 6:
                continue  # tier 3: weekly full only
            full = day.weekday() == 6
            gb = size * (1.0 if full else r.uniform(0.02, 0.06))
            status = "success"
            if d == fail_day:
                status = "failed"
            if override.get("kind") == "failing" and d >= override["last_success_day"] + 1:
                status = "failed"
            dur = timedelta(minutes=max(5, int(gb / 4)))
            jobs.append({"job_id": f"job-{c['sys_id'][:8]}-{d:02d}", "object": c["name"],
                         "policy": override.get("policy", c["backup_policy"]),
                         "target": override.get("target", "bkp-appl-pri-01"), "status": status,
                         "type": "full" if full else "incremental", "start": ts(start),
                         "end": ts(start + (dur if status == "success" else timedelta(minutes=3))),
                         "bytes": int(gb * 1e9) if status == "success" else 0,
                         "error": "" if status == "success" else "media server connection timeout"})
    jobs.sort(key=lambda j: (j["start"], j["object"]))
    return jobs


def replication_pairs(world):
    """(volume, source, target, mode) per declared non-failover replication.
    Volumes are the data holders stored on the source array."""
    stores = {}
    for rel in world["rels"]:
        if rel["type"] == STORAGE and world["_by_name"][rel["child"]]["data_holder"]:
            stores.setdefault(rel["parent"], []).append(rel["child"])
    pairs = []
    for rel in world["rels"]:
        if rel["type"] != "Replicates to::Replica of" or rel["failover"]:
            continue
        src = rel["parent"]
        vols = [f"vol-{n}" for n in sorted(stores.get(src, []))] or [f"vol-{src}-ds01"]
        if world["_by_name"][src]["label"] == "BackupTarget":
            vols = ["bkpset-daily"]
        for v in vols:
            pairs.append((v, src, rel["child"], rel["mode"]))
    return pairs


def replication_status(world, plan):
    r = rng_for(world["meta"]["seed"], "replication")
    out = []
    for vol, src, dst, mode in replication_pairs(world) + plan["replication_add"]:
        hours = HOURS if not isinstance(mode, tuple) else HOURS[-24 * mode[1]:]
        m = mode if not isinstance(mode, tuple) else mode[0]
        for t in hours:
            if m == "sync":
                lag = r.randint(0, 1)
            elif src.startswith("bkp-"):
                lag = r.randint(300, 3600)
            else:
                lag = r.randint(20, 240) if r.random() > 0.01 else r.randint(600, 1800)  # rare spike
            out.append({"hour": ts(t), "source_volume": vol, "source_array": src, "target_array": dst,
                        "mode": m, "lag_seconds": lag, "state": "in_sync" if m == "sync" else "replicating"})
    return out


def access_log(world, plan):
    """Demo-site and GCC users: shift logins and actions on granted targets.
    Admin on production databases only inside approved change windows."""
    r = rng_for(world["meta"]["seed"], "access")
    idn = world["identity"]
    roles_of_group = {g["id"]: g["roles"] for g in idn["groups"]}
    grants = {}
    for g in idn["grants"]:
        grants.setdefault(g["role"], []).append((g["target"], g["level"]))
    by = world["_by_name"]
    events = []
    for u in idn["users"]:
        if u["status"] != "active":
            continue
        targets = []
        for role in roles_of_group[u["primary_group"]]:
            targets += grants.get(role, [])
        targets = [(t, "read" if lvl == "admin" and by[t]["label"] == "Database" else lvl)
                   for t, lvl in targets if t in by]
        if not targets:
            continue
        tz = world["_site_tz"][u["site"]]
        gcc = u["site"] == "GCC-HYD"
        start_local = 9.5 if gcc else (19 if u["job_role"] == "nurse" and r.random() < 0.35 else 7)
        for d in range(WINDOW_DAYS):
            day = START + timedelta(days=d)
            if gcc and day.weekday() >= 5:
                continue
            if r.random() > (0.95 if gcc else 0.55):
                continue
            t0 = day + timedelta(hours=start_local - tz, minutes=r.randint(0, 40))
            if t0 > END:
                continue
            t, lvl0 = targets[0]
            events.append((t0, u["id"], u["workstation"], t, "login"))
            for _ in range(r.randint(1, 3)):
                t, lvl = r.choice(targets)
                act = "write" if lvl in ("write", "admin") and r.random() < 0.5 else "read"
                events.append((t0 + timedelta(minutes=r.randint(5, 480)), u["id"], u["workstation"], t, act))
    # Approved change windows: DBAs (and EMR support for emr-db-01) run admin work inside them.
    users_by_group = {}
    for m in idn["memberships"]:
        users_by_group.setdefault(m["group"], []).append(m["user"])
    active = {u["id"]: u for u in idn["users"] if u["status"] == "active"}
    for ch in world["changes"]:
        if ch["approval"] != "approved" or ch["state"] != "closed" or by[ch["cmdb_ci"]]["label"] != "Database":
            continue
        s = datetime.strptime(ch["start_date"], FMT).replace(tzinfo=timezone.utc)
        pool = [x for x in users_by_group.get("grp-dba", []) if x in active]
        if ch["cmdb_ci"] == "emr-db-01":
            pool += [x for x in users_by_group.get("grp-hyd-clinical-apps-support", []) if x in active][:5]
        for uid in r.sample(pool, 2):
            for k in range(r.randint(2, 5)):
                events.append((s + timedelta(minutes=15 + 30 * k), uid, active[uid]["workstation"],
                               ch["cmdb_ci"], "admin"))
    events += [(datetime.strptime(e["ts"], FMT).replace(tzinfo=timezone.utc), e["user"], e["workstation"],
                e["target"], e["action"]) for e in plan["access_add"]]
    events = [e for e in events if e[0] <= END]
    events.sort(key=lambda e: (e[0], e[1], e[3], e[4]))
    return [{"ts": ts(e[0]), "user": e[1], "workstation": e[2], "target": e[3], "action": e[4],
             "result": "success"} for e in events]


# --------------------------------------------------------------------------
# OTLP trace sample (demo sites, 2 hours)
# --------------------------------------------------------------------------


def traces_sample(world):
    """OTLP-JSON (ExportTraceServiceRequest per line) for the critical-results
    path at demo hospitals: lis-app -> ie-app route -> lrc-db-01 -> emr-app."""
    r = rng_for(world["meta"]["seed"], "traces")
    by = world["_by_name"]
    t0 = END.replace(hour=16, minute=0, second=0)  # 09:00-11:00 Pacific on the last day
    lines = []

    def hexid(n):
        return "".join(r.choice("0123456789abcdef") for _ in range(n))

    def span(trace, parent, name, kind, start, dur_ms, attrs):
        s = {"traceId": trace, "spanId": hexid(16), "name": name, "kind": kind,
             "startTimeUnixNano": str(int(start.timestamp() * 1e9)),
             "endTimeUnixNano": str(int((start + timedelta(milliseconds=dur_ms)).timestamp() * 1e9)),
             "attributes": [{"key": k, "value": {"stringValue": str(v)} if isinstance(v, str)
                             else {"intValue": str(v)}} for k, v in attrs.items()],
             "status": {"code": 1}}
        if parent:
            s["parentSpanId"] = parent["spanId"]
        return s

    for sid in [s for s in ("H03", "H17", "H44") if f"ORU-CRIT-{s}" in by]:
        route = f"ORU-CRIT-{sid}"
        n = {"H03": 30, "H17": 12, "H44": 3}[sid]
        for i in range(n):
            start = t0 + timedelta(seconds=r.randint(0, 7199))
            trace = hexid(32)
            a = span(trace, None, "MLLP send ORU^R01", 3, start, 60,
                     {"messaging.system": "mllp", "messaging.destination.name": route,
                      "server.address": "ie-app", "hl7.message_type": "ORU^R01", "site": sid})
            b = span(trace, a, f"route {route}", 2, start + timedelta(milliseconds=5), 45,
                     {"hl7.route": route, "hl7.message_type": "ORU^R01"})
            c = span(trace, b, "INSERT critical_result_stage", 3, start + timedelta(milliseconds=8), 12,
                     {"db.system": "mssql", "db.name": "lrc", "server.address": "lrc-db-01",
                      "server.port": 1433})
            d = span(trace, b, "MLLP deliver ED worklist", 3, start + timedelta(milliseconds=25), 20,
                     {"messaging.system": "mllp", "server.address": "emr-app", "peer.service": "emr-app"})
            e = span(trace, d, "HL7 inbound ORU^R01 ED worklist", 2, start + timedelta(milliseconds=30), 12,
                     {"hl7.message_type": "ORU^R01", "emr.worklist": f"{sid}-ED"})

            def res(service, spans):
                return {"resource": {"attributes": [
                    {"key": "service.name", "value": {"stringValue": service}},
                    {"key": "deployment.environment", "value": {"stringValue": "production"}}]},
                    "scopeSpans": [{"scope": {"name": "northwind.synthetic"}, "spans": spans}]}

            lines.append({"resourceSpans": [res("lis-app", [a]), res("ie-app", [b, c, d]), res("emr-app", [e])]})
    lines.sort(key=lambda x: x["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["startTimeUnixNano"])
    return lines


def prepare(world):
    """Attach lookup helpers used by the observed generators (not serialised)."""
    by = {c["name"]: c for c in world["cis"]}
    world["_by_name"] = by
    world["_site_tz"] = {s["id"]: s["tz"] for s in world["sites"]}
    world["_ip"] = lambda n: by[n]["ip_address"] or by[n].get("service_ip", "")
    return world


def open_out(path, gz=False):
    """Text writer; with gz the file gets a .gz suffix and a fixed gzip
    header (mtime=0) so output stays byte-identical across runs."""
    if not gz:
        return open(path, "w", encoding="utf-8", newline="\n")
    raw = gzip.GzipFile(filename="", mode="wb", fileobj=open(path + ".gz", "wb"), mtime=0)
    return io.TextIOWrapper(raw, encoding="utf-8", newline="\n")


def open_in(path):
    """Read a plain or .gz text file transparently."""
    if not os.path.exists(path) and os.path.exists(path + ".gz"):
        return io.TextIOWrapper(gzip.open(path + ".gz", "rb"), encoding="utf-8")
    return open(path, encoding="utf-8")


def write_jsonl(path, rows, gz=False):
    n = 0
    with open_out(path, gz) as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":")) + "\n")
            n += 1
    return n


def write_observed(world, plan, out, gz=False):
    """Stream all observed files; returns record counts."""
    alias = plan["flow_alias"]
    legs = [leg for rel in world["rels"] for leg in rel["obs"]]
    counts = {}
    files = {("flows", True): "flows_hourly.jsonl", ("flows", False): "flows_daily.jsonl",
             ("netflow", True): "netflow_hourly.jsonl", ("netflow", False): "netflow_daily.jsonl"}
    handles = {k: open_out(f"{out}/observed/{v}", gz) for k, v in files.items()}
    try:
        for leg in legs:
            hourly = is_hourly(world, leg)
            key = (leg.get("channel", "flows"), hourly)
            for rec in leg_records(world, leg, hourly, alias):
                handles[key].write(json.dumps(rec, separators=(",", ":")) + "\n")
                counts[files[key]] = counts.get(files[key], 0) + 1
        for rec in noise_flows(world) + plan["flows_add"]:
            handles[("flows", True)].write(json.dumps(rec, separators=(",", ":")) + "\n")
            counts["flows_hourly.jsonl"] = counts.get("flows_hourly.jsonl", 0) + 1
    finally:
        for h in handles.values():
            h.close()
    for name, rows in (("backup_jobs.jsonl", backup_jobs(world, plan)),
                       ("replication_status.jsonl", replication_status(world, plan)),
                       ("access_log.jsonl", access_log(world, plan)),
                       ("traces_sample.otlp.jsonl", traces_sample(world))):
        counts[name] = write_jsonl(f"{out}/observed/{name}", rows, gz)
    return counts
