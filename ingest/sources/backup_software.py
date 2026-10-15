"""backup_software: backup & recovery job sessions (Veeam Backup & Replication REST API v1).

Raw (data/raw/backup_software/sessions.json): the `GET /api/v1/sessions`
page shape: {"data": [session, ...], "pagination": {...}}. Each session: id,
name (job name), jobId, sessionType (BackupJob | BackupCopyJob), creationTime,
endTime, state (Stopped), progressPercent, result {result: Success | Warning |
Failed, message, isCanceled}, progress {duration, processedSize, readSize,
transferredSize}. Per-object task details, which the API serves from the
session's task records, are flattened into objectName, repositoryName and
sourceRepositoryName. A storage-snapshot-only job is a BackupJob whose job
state has isStorageSnapshot: true (carried on the session here).

Connector, per protected object: last_success (latest Success or Warning;
snapshot-only sessions never count as a backup), the backup target, and the
Backup Copy targets (another site, immutable vault). Copy sessions also prove
repository -> repository replication (appliance -> DR appliance / vault).
A transient Failed followed by a Success within 48 h is not a finding; the
7-day rule (ingest.config) decides "backups failing".
"""

import json
import os

from ingest.sources.common import raw_dir


def write(sessions, out):
    d = raw_dir(out, "backup_software")
    with open(os.path.join(d, "sessions.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump({"data": sessions, "pagination": {"total": len(sessions), "count": len(sessions), "skip": 0,
                                                     "limit": len(sessions)}}, f, indent=1)
    return len(sessions)


def read(data, ident):
    with open(os.path.join(data, "raw", "backup_software", "sessions.json"), encoding="utf-8") as f:
        rows = json.load(f)["data"]
    agg, repl = {}, {}
    for i, r in enumerate(rows):
        jt = "snapshot" if r.get("isStorageSnapshot") else "backup_copy" if r["sessionType"] == "BackupCopyJob" \
            else "backup"
        obj, orule = ident.match(name=r["objectName"])
        tgt, trule = ident.match(name=r["repositoryName"])
        result = r["result"]["result"]
        ok = result in ("Success", "Warning") and jt != "snapshot"
        key = (obj or r["objectName"], tgt or r["repositoryName"], jt)
        e = agg.setdefault(key, {"jobs": 0, "failures": 0, "last_run": "", "last_status": "", "last_success": None,
                                 "object_rule": orule, "target_rule": trule, "record": f"sessions.json data[{i}] id={r['id']}"})
        e["jobs"] += 1
        e["failures"] += result == "Failed"
        if r["creationTime"] >= e["last_run"]:
            e["last_run"], e["last_status"] = r["creationTime"], result
        if ok and (e["last_success"] is None or r["endTime"] > e["last_success"]):
            e["last_success"] = r["endTime"]
        if jt == "backup_copy" and result != "Failed":
            src, _ = ident.match(name=r["sourceRepositoryName"])
            p = repl.setdefault((src, tgt), {"last": r["endTime"], "sessions": 0, "record": f"sessions.json data[{i}]"})
            p["last"] = max(p["last"], r["endTime"])
            p["sessions"] += 1
    backups = [{"source": "backup_software", "source_record": e["record"], "object": o, "target": t, "job_type": jt,
                "jobs": e["jobs"], "failures": e["failures"], "last_run": e["last_run"],
                "last_status": e["last_status"], "last_success": e["last_success"],
                "object_rule": e["object_rule"], "target_rule": e["target_rule"]}
               for (o, t, jt), e in sorted(agg.items())]
    copies = [{"source": "backup_software", "source_record": p["record"], "source_array": s, "target_array": t,
               "mode": "async", "max_lag_s": None, "last_transfer": p["last"], "state": "copying",
               "volumes": ["backup copy job"], "sessions": p["sessions"]} for (s, t), p in sorted(repl.items())]
    return backups, copies
