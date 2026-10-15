"""Write every discovery source's raw files from the truth model + drift plan.

This is the generator side of REALISM_SPEC 3: the truth model decides what
happens; each block below renders it in the native format of the tool that
would see it (see ingest/sources/*). Connectors (ingest.connect) read these
files back without access to the truth.
"""

import hashlib
import os
import random
from datetime import timedelta

from ingest import views
from ingest.sources import (apm_otel, backup_software, cmdb_export, db_audit, discovery_scanner, hr_feed,
                            identity_sso, interface_engine, itsm_history, netflow, storage_telemetry)
from ingest.views import END, HOURS, P95_MS, START, _count, factor, is_hourly
from ingest.world import rng_for

APP_PORT = {"tds": 1433, "postgres": 5432, "oracle-net": 1521, "tcp-superserver": 1972, "https": 443, "saml": 443,
            "oidc": 443, "ldaps": 636, "kerberos": 88, "smtp": 587, "sftp": 22, "ipp": 631, "syslog-tls": 6514,
            "mllp": 2575, "dicom": 104, "ddboost": 2049}
SAMPLE_SITES = ("h03-", "h17-", "h44-")  # OTLP span sample: services at the demo hospitals
SAMPLE_HOURS = (16, 17)
MIRTH_SERVER = "6f1c0b2e-9d4a-4b8e-a1c3-5e7f2a9d0b14"                  # 2026-10-14 16:00-18:00Z (09:00-11:00 Pacific)


def stamp(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def leg_hours(world, leg):
    """Hourly (t, n, errors, p95_ms, bytes) for one observed leg (same model as before)."""
    r = rng_for(world["meta"]["seed"], "leg", leg["src"], leg["dst"], leg.get("route", ""))
    tz, proto = leg.get("tz", -7), leg.get("protocol", "tcp")
    p95_base, err_rate = P95_MS.get(proto, 50), r.uniform(0.0005, 0.004)
    out = []
    for t in HOURS:
        f = factor(leg["profile"], t + timedelta(hours=tz))
        n = _count(r, leg["rate"] * f * r.uniform(0.85, 1.15)) if f else 0
        err = _count(r, n * err_rate)
        p95 = round(p95_base * r.uniform(0.8, 1.3) * (1 + 0.3 * f), 1)
        b = int(n * leg.get("bytes_per", 1500) * r.uniform(0.7, 1.3))
        if n:
            out.append((t, n, err, p95, b))
    return out


def by_day(hours):
    days = {}
    for t, n, err, p95, b in hours:
        d = t.replace(hour=0)
        x = days.setdefault(d, [0, 0, 0.0, 0])
        x[0] += n
        x[1] += err
        x[2] = max(x[2], p95)
        x[3] += b
    return [(d, *v) for d, v in sorted(days.items())]


def host_of(world, name):
    """First VM/host a CI runs on (its network identity)."""
    for rel in world["rels"]:
        if rel["parent"] == name and rel["type"] == "Runs on::Runs":
            return rel["child"]
    return name


def write_all(world, plan, out):
    by = world["_by_name"]
    seed = world["meta"]["seed"]
    counts = {}
    legs = [leg for rel in world["rels"] for leg in rel["obs"]]
    hours_of = {id(leg): leg_hours(world, leg) for leg in legs}

    # ---- cmdb_export
    cm = views.cmdb_view(world, plan)
    cmdb_export.write(cm, out)
    ci_sid = {r["name"]: r["sys_id"] for r in cm["cmdb_ci"]}
    grp_sid = {g["name"]: cmdb_export.sid("sys_user_group", g["name"]) for g in cm["sys_user_group"]}
    counts["cmdb_export"] = len(cm["cmdb_ci"]) + len(cm["cmdb_rel_ci"])

    # ---- listening services (what discovery sees on each host) and interface destinations
    listening = set()
    for leg in legs:
        dst = by[leg["dst"]]
        if dst["label"] in ("Application", "Database") and not leg.get("route"):
            port = leg.get("port") or APP_PORT.get(leg.get("protocol", ""), 443)
            listening.add((dst["name"], dst["class"], port))
    for leg in legs:  # interface engine destinations: HL7 (2575), DICOM (104), JDBC (1433)
        if leg.get("route") and leg["src"] == leg["route"]:
            dst = by[leg["dst"]]
            port = 1433 if dst["label"] == "Database" else 104 if leg["protocol"] == "dicom" else 2575
            listening.add((dst["name"], dst["class"], port))
    listen_rows = []
    for app, cls, port in sorted(listening):
        host = host_of(world, app)
        listen_rows.append((app, cls, host, by[host]["ip_address"], port))
    seen = {}
    for app, cls, host, ip, port in listen_rows:
        assert (ip, port) not in seen or seen[(ip, port)] == app, (ip, port, app, seen.get((ip, port)))
        seen[(ip, port)] = app

    # ---- discovery_scanner
    disc = views.discovery_view(world, plan)
    cls_by_serial = {c["serial_number"]: c["class"] for c in world["cis"] if c["serial_number"]}
    for r in disc["scan_results"]:
        r["class_name"] = cls_by_serial.get(r["serial_number"], "cmdb_ci_hardware")
    discovery_scanner.write(disc["scan_results"], listen_rows, disc["scan_coverage"], out)
    counts["discovery_scanner"] = len(disc["scan_results"])

    # ---- apm_otel (app-level calls) and interface_engine (route legs) and netflow (device legs)
    series, spans, channels, stats, nf = {}, {}, {}, {}, []
    rs = random.Random(f"{seed}:spans")
    alias = plan["flow_alias"]
    for leg in legs:
        hrs = hours_of[id(leg)]
        if not hrs:
            continue
        src, dst = by[leg["src"]], by[leg["dst"]]
        if leg.get("route"):
            ch = channels.setdefault(leg["route"], {"in": [], "out": []})
            (ch["out"] if leg["src"] == leg["route"] else ch["in"]).append((leg, by_day(hrs)))
        elif leg.get("channel") == "netflow":
            hourly = is_hourly(world, leg)
            periods = hrs if hourly else by_day(hrs)
            server_host = host_of(world, dst["name"])
            for t, n, err, p95, b in periods:
                nf.append(_flow(leg, t, n, b, hourly, src_ip=src["ip_address"],
                                src_dns=alias.get(src["name"], src["name"]), dst_ip=by[server_host]["ip_address"],
                                dst_dns=server_host, port=leg["port"]))
        else:
            conn = "database" if dst["label"] == "Database" else ""
            hourly = is_hourly(world, leg)
            key = (src["name"], dst["name"], conn, "hourly" if hourly else "daily")
            series.setdefault(key, []).extend((t, n, e, p) for t, n, e, p, _ in (hrs if hourly else by_day(hrs)))
            if hourly and src["name"].startswith(SAMPLE_SITES):
                for t, n, e, p, _ in hrs:
                    if t.date() == END.date() and t.hour in SAMPLE_HOURS:
                        calls = spans.setdefault((src["name"], dst["name"], conn), [])
                        for i in range(n):
                            calls.append((t + timedelta(seconds=rs.randint(0, 3599)), round(p * rs.uniform(0.3, 1.0), 1),
                                          i < e))
    for rec in views.noise_flows(world):  # one-off ad-hoc calls (< 3 days) and tagged health-check probes
        t = views.datetime.strptime(rec["hour"], views.FMT).replace(tzinfo=views.timezone.utc)
        if rec.get("health_check"):
            dst_host = host_of(world, rec["dst_name"])
            nf.append({"granularity": "hourly", "start": t + timedelta(minutes=5), "end": t + timedelta(minutes=5, seconds=1),
                       "sa": world["_ip"]("mon-app"), "da": by[dst_host]["ip_address"], "sp": 50000 + len(nf) % 9000,
                       "dp": 443, "pkts": 6, "bytes": 1800, "sa_dns": host_of(world, "mon-app"), "da_dns": dst_host,
                       "tag": "monitoring-probe"})
        else:
            key = (rec["src_name"], rec["dst_name"], "database" if by[rec["dst_name"]]["label"] == "Database" else "",
                   "hourly")
            series.setdefault(key, []).append((t, rec["count"], rec["errors"], rec["p95_ms"]))
    for rec in plan["flows_add"]:  # D14: an external app seen only on the network and in sign-ins
        t = views.datetime.strptime(rec["hour"], views.FMT).replace(tzinfo=views.timezone.utc)
        dst_host = host_of(world, rec["dst_name"])
        nf.append(_flow({"src": rec["src_name"], "dst": rec["dst_name"], "port": 443}, t, rec["count"],
                        rec["count"] * 9000, True, src_ip=rec["src_ip"], src_dns=rec["src_name"],
                        dst_ip=by[dst_host]["ip_address"], dst_dns=dst_host, port=443))
    for k in series:
        series[k].sort()
    counts["apm_otel"] = apm_otel.write(series, out)
    apm_otel.write_spans(spans, out, rs)
    nf.sort(key=lambda r: (r["start"], r["sa"], r["da"], r["sp"], r["dp"]))
    counts["netflow"] = sum(netflow.write(nf, out).values())

    chan_list, stat_rows = [], []
    for i, (route, legs_) in enumerate(sorted(channels.items())):
        c = by[route]
        dicom = c["protocol"] == "DICOM"
        dests = []
        for leg, days in legs_["out"]:
            dst = by[leg["dst"]]
            host = host_of(world, dst["name"])
            if dst["label"] == "Database":
                dests.append({"name": f"Stage in {dst['name']}", "type": "Database Writer",
                              "url": f"jdbc:sqlserver://{host}:1433;databaseName={dst['name'].split('-db')[0]}",
                              "leg": leg, "days": days})
            else:
                dests.append({"name": f"To {dst['name']}", "type": "DICOM Sender" if dicom else "TCP Sender",
                              "host": host, "port": 104 if dicom else 2575, "leg": leg, "days": days})
        chan_list.append({"id": _uuid(c["sys_id"]), "name": route, "description": c.get("description", "") or
                          f"{c['message_type']} {c['source'] or 'modalities'} -> {c['target']}",
                          "source": {"type": "DICOM Listener" if dicom else "TCP Listener", "port": 104 if dicom else 6600 + i},
                          "destinations": dests})
        inbound = {d: (n, e) for leg, days in legs_["in"] for d, n, e, _, _ in days}
        src_name = legs_["in"][0][0]["src"] if legs_["in"] else ""
        all_days = sorted({d for _, days in legs_["out"] for d, *_ in days} | set(inbound))
        for d in all_days:
            received = inbound.get(d, (None,))[0]
            if received is None:  # no single named sender: received = what went out
                received = max([n for _, days in legs_["out"] for dd, n, *_ in days if dd == d] + [0])
            stat_rows.append({"serverId": MIRTH_SERVER, "channelId": chan_list[-1]["id"], "channel": route, "metaDataId": 0,
                              "connector": "sourceConnector", "connector_type": chan_list[-1]["source"]["type"],
                              "date": d.strftime("%Y-%m-%d"), "SOURCE": src_name, "received": received,
                              "filtered": 0, "sent": 0, "error": 0, "queued": 0})
            for dst in dests:
                hit = next(((n, e) for dd, n, e, _, _ in dst["days"] if dd == d), None)
                if hit:
                    stat_rows.append({"serverId": MIRTH_SERVER, "channelId": chan_list[-1]["id"], "channel": route,
                                      "metaDataId": dests.index(dst) + 1, "connector": dst["name"],
                                      "connector_type": dst["type"], "date": d.strftime("%Y-%m-%d"), "SOURCE": "",
                                      "received": 0, "filtered": 0, "sent": hit[0] - hit[1], "error": hit[1], "queued": 0})
    for c in chan_list:
        for dst in c["destinations"]:
            dst.pop("leg"), dst.pop("days")
    counts["interface_engine"] = interface_engine.write(chan_list, stat_rows, out) + len(stat_rows)

    # ---- backup_software
    sessions = []
    rb = random.Random(f"{seed}:backup-sessions")

    def session(sid_, name, stype, obj, start, end, result, repo, src_repo, size, msg, snap=False):
        s_ = {"id": _uuid(sid_), "name": name, "jobId": _uuid(name), "sessionType": stype,
              "creationTime": start, "endTime": end, "state": "Stopped", "progressPercent": 100,
              "result": {"result": result, "message": msg, "isCanceled": False},
              "progress": {"duration": "00:05:00", "processedSize": size, "readSize": size, "transferredSize": size},
              "objectName": obj, "repositoryName": repo, "sourceRepositoryName": src_repo}
        if snap:
            s_["isStorageSnapshot"] = True
        return s_

    for j in views.backup_jobs(world, plan):
        snap = j["policy"].startswith("snap-")
        ok = j["status"] == "success"
        result = "Failed" if not ok else ("Warning" if rb.random() < 0.02 else "Success")
        msg = j["error"] or ("Changed block tracking was reset" if result == "Warning" else "")
        sessions.append(session(j["job_id"], f"{'SNAP' if snap else 'BKP'} {j['policy']} {j['object']}", "BackupJob",
                                j["object"], j["start"], j["end"], result, j["target"], "", j["bytes"], msg, snap))
        if ok and not snap:  # backup copy jobs: DR appliance and immutable vault
            end = views.datetime.strptime(j["end"], views.FMT).replace(tzinfo=views.timezone.utc)
            for k, tgt in enumerate(("bkp-appl-dr-01", "bkp-vault-usw-01")):
                st = end + timedelta(minutes=30 + 15 * k)
                if st > END:
                    continue
                sessions.append(session(f"{j['job_id']}-copy{k + 1}", f"COPY {tgt} {j['object']}", "BackupCopyJob",
                                        j["object"], views.ts(st), views.ts(st + timedelta(minutes=10)), "Success",
                                        tgt, j["target"], j["bytes"], ""))
    sessions.sort(key=lambda r: (r["creationTime"], r["id"]))
    counts["backup_software"] = backup_software.write(sessions, out)

    # ---- storage_telemetry (array pairs only; backup repositories are covered by backup copy jobs)
    samples = []
    for r in views.replication_status(world, plan):
        if by[r["source_array"]]["label"] == "BackupTarget":
            continue
        t = views.datetime.strptime(r["hour"], views.FMT).replace(tzinfo=views.timezone.utc)
        samples.append(storage_telemetry.sample(t, r["source_volume"], r["source_array"], r["target_array"], r["mode"],
                                                r["lag_seconds"], r["state"]))
    counts["storage_telemetry"] = storage_telemetry.write(samples, out)

    # ---- identity: declared IAM export (no HR status), hr_feed, sign-ins, DB audit
    idn = views.identity_view(world, plan)
    os.makedirs(os.path.join(out, "identity"), exist_ok=True)
    users = [{k: u[k] for k in ("id", "site", "department", "job_role")} | {"account_enabled": "true"}
             for u in idn["users"]]
    from ingest.generate import write_csv
    write_csv(os.path.join(out, "identity", "users.csv"), users)
    for t in ("groups", "memberships", "roles", "grants"):
        write_csv(os.path.join(out, "identity", f"{t}.csv"), idn[t])
    rh = random.Random(f"{seed}:hr")
    workers = []
    for u in idn["users"]:
        term = u["status"] != "active"
        hire = END - timedelta(days=rh.randint(200, 4000))
        termd = (END - timedelta(days=rh.randint(20, 180))).strftime("%Y-%m-%d") if term else ""
        workers.append({"Employee_ID": u["id"], "Worker_Type": "Employee", "Worker_Status": "Terminated" if term else "Active",
                        "Active": 0 if term else 1, "Terminated": 1 if term else 0, "On_Leave": 0,
                        "Hire_Date": hire.strftime("%Y-%m-%d"), "Termination_Date": termd,
                        "Effective_Date": termd or hire.strftime("%Y-%m-%d"), "Supervisory_Organization": u["department"]})
    counts["hr_feed"] = hr_feed.write(workers, out)
    ws_ip = lambda w: by[w]["ip_address"] if w in by else ""  # noqa: E731
    signins, audit = [], []
    ra = random.Random(f"{seed}:signins")
    for i, e in enumerate(views.access_log(world, plan)):
        tgt = by.get(e["target"])
        if tgt is not None and tgt["label"] == "Database":
            act = {"read": "SL", "write": "UP", "admin": "AL", "login": "SL"}[e["action"]]
            stmt = {"SL": "SELECT TOP (100) * FROM dbo.encounter WHERE ...", "UP": "UPDATE dbo.claim SET status = @p1 WHERE id = @p2",
                    "AL": "ALTER INDEX ALL ON dbo.result REBUILD"}[act]
            audit.append({"event_time": e["ts"].replace("T", " ").replace("Z", ".0000000"), "sequence_number": 1,
                          "action_id": act, "succeeded": 1, "session_id": 50 + i % 200,
                          "database_principal_name": "dbo", "session_server_principal_name": e["user"],
                          "server_principal_name": e["user"], "server_instance_name": e["target"],
                          "database_name": e["target"].split("-db")[0], "schema_name": "dbo",
                          "object_name": stmt.split(" ")[-3] if act != "AL" else "result", "statement": stmt,
                          "class_type": "U", "client_ip": ws_ip(e["workstation"]),
                          "application_name": f"Microsoft SQL Server Management Studio@{e['workstation']}"})
            continue
        sid = hashlib.md5(f"signin:{i}:{e['user']}:{e['ts']}".encode()).hexdigest()
        fail = e["action"] == "login" and ra.random() < 0.01 and e["target"] == "emr-app"
        rec = {"id": _uuid(sid), "createdDateTime": e["ts"], "userPrincipalName": e["user"], "userId": _uuid(e["user"]),
               "appDisplayName": e["target"], "appId": _uuid(ci_sid.get(e["target"], e["target"])),
               "ipAddress": ws_ip(e["workstation"]), "clientAppUsed": "Browser",
               "deviceDetail": {"deviceId": _uuid(e["workstation"]), "displayName": e["workstation"],
                                "operatingSystem": "Windows 11", "browser": "Edge 129.0"},
               "status": {"errorCode": 0, "failureReason": None, "additionalDetails": None},
               "conditionalAccessStatus": "success", "isInteractive": True,
               "resourceDisplayName": e["target"], "location": _location(world, e["user"])}
        if fail:  # a mistyped password, then the real sign-in (noise)
            signins.append(dict(rec, id=_uuid(sid + "f"), status={"errorCode": 50126, "additionalDetails": None,
                                "failureReason": "Error validating credentials due to invalid username or password."},
                                conditionalAccessStatus="notApplied"))
        signins.append(rec)
    counts["identity_sso"] = identity_sso.write(signins, out)
    counts["db_audit"] = db_audit.write(audit, out)

    # ---- itsm_history (90 days)
    ri = random.Random(f"{seed}:itsm")
    inc, chg = [], []
    for row in views.evidence_view(world, plan)["ownership_evidence"]:
        for k in range(int(row["incidents_resolved"])):
            o = END - timedelta(days=ri.randint(1, 89), hours=ri.randint(0, 23))
            r_ = o + timedelta(hours=ri.randint(1, 72))
            inc.append({"number": f"INC{len(inc) + 1010000:07d}", "short_description": f"Issue on {row['ci']}",
                        "cmdb_ci": ci_sid.get(row["ci"], ""), "assignment_group": grp_sid[row["team"]],
                        "sys_id": cmdb_export.sid("incident", len(inc)), "priority": ri.choice(["2", "3", "3", "4"]),
                        "state": "7",
                        "opened_at": stamp(o), "resolved_at": stamp(min(r_, END)),
                        "closed_at": stamp(min(r_ + timedelta(days=3), END)), "close_code": "Solved (Permanently)"})
        for k in range(int(row["changes_implemented"])):
            s = END - timedelta(days=ri.randint(1, 89), hours=ri.randint(0, 23))
            chg.append({"number": f"CHG{len(chg) + 2010000:07d}", "short_description": f"Change on {row['ci']}",
                        "cmdb_ci": ci_sid.get(row["ci"], ""), "assignment_group": grp_sid[row["team"]],
                        "sys_id": cmdb_export.sid("change_request_hist", len(chg)), "type": "standard", "state": "3",
                        "start_date": stamp(s),
                        "end_date": stamp(s + timedelta(hours=2)), "close_code": "Successful"})
    counts["itsm_history"] = itsm_history.write(inc, chg, out)
    return counts


def _flow(leg, t, n, b, hourly, src_ip, src_dns, dst_ip, dst_dns, port):
    """Forward (client -> server) aggregated record; netflow.write adds the reverse direction."""
    h = int(hashlib.md5(f"{leg['src']}|{leg['dst']}|{t.isoformat()}".encode()).hexdigest()[:6], 16)
    sp = 49152 + h % 16000
    span = timedelta(hours=1) if hourly else timedelta(days=1)
    start, end = t + timedelta(seconds=h % 600), t + span - timedelta(seconds=1 + h % 300)
    return {"granularity": "hourly" if hourly else "daily", "start": start, "end": end, "sa": src_ip, "da": dst_ip,
            "sp": sp, "dp": port, "pkts": max(1, b // 1200), "bytes": max(b, 64), "sa_dns": src_dns, "da_dns": dst_dns,
            "reverse": True}


def _location(world, uid):
    site = uid.split("-")[1].upper()
    s = world["_site_tz"] and next((x for x in world["sites"] if x["id"] == ("GCC-HYD" if site == "HYD" else site)), None)
    if not s:
        return {}
    return {"city": s["city"], "state": s["state"] or "Telangana", "countryOrRegion": "IN" if site == "HYD" else "US"}


def _uuid(s):
    h = hashlib.md5(str(s).encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


__all__ = ["write_all", "START"]
