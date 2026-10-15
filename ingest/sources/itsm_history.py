"""itsm_history: ServiceNow incident and change_request history (90 days).

Raw (data/raw/itsm_history/incident.csv, change_request.csv): ServiceNow list
exports; references (cmdb_ci, assignment_group) as sys_ids. incident: sys_id, number,
short_description, cmdb_ci, assignment_group (the group that resolved it),
priority (1-5), state (7 = Closed), opened_at, resolved_at, closed_at, close_code. change_request:
number, short_description, cmdb_ci, assignment_group (implementing group),
type, state, start_date, end_date, close_code.

Connector: observed owner evidence per CI and group = incidents resolved +
changes implemented within OWNERSHIP_WINDOW_DAYS; ties are not reported.
"""

import os
from datetime import timedelta

from ingest.config import OWNERSHIP_WINDOW_DAYS, SNAPSHOT_END
from ingest.sources.common import iso, parse_ts, raw_dir, read_csv, write_csv

INC = ["sys_id", "number", "short_description", "cmdb_ci", "assignment_group", "priority", "state", "opened_at",
       "resolved_at", "closed_at", "close_code"]
CHG = ["sys_id", "number", "short_description", "cmdb_ci", "assignment_group", "type", "state", "start_date",
       "end_date", "close_code"]
CLOSED = {"incident": "7", "change_request": "3"}  # raw choice values: incident 7 Closed, change 3 Closed


def write(incidents, changes, out):
    d = raw_dir(out, "itsm_history")
    write_csv(os.path.join(d, "incident.csv"), incidents, INC)
    write_csv(os.path.join(d, "change_request.csv"), changes, CHG)
    return len(incidents) + len(changes)


def read(data, ci_by_sys_id, group_by_sys_id):
    d = os.path.join(data, "raw", "itsm_history")
    cutoff = iso(parse_ts(SNAPSHOT_END) - timedelta(days=OWNERSHIP_WINDOW_DAYS)).replace("T", " ").rstrip("Z")
    agg = {}
    for table, col, field, path in (("incident", "resolved_at", "incidents_resolved", "incident.csv"),
                                    ("change_request", "end_date", "changes_implemented", "change_request.csv")):
        for i, r in enumerate(read_csv(os.path.join(d, path))):
            if r["state"] != CLOSED[table] or r[col] < cutoff:
                continue
            key = (ci_by_sys_id.get(r["cmdb_ci"]), group_by_sys_id.get(r["assignment_group"]))
            e = agg.setdefault(key, {"incidents_resolved": 0, "changes_implemented": 0, "last": "",
                                     "record": f"{path}#{i + 2} {r['number']}"})
            e[field] += 1
            e["last"] = max(e["last"], r[col])
    return [{"source": "itsm_history", "source_record": e["record"], "ci": ci, "team": team,
             "incidents_resolved": e["incidents_resolved"], "changes_implemented": e["changes_implemented"],
             "last_activity": e["last"].replace(" ", "T") + "Z", "period_days": OWNERSHIP_WINDOW_DAYS}
            for (ci, team), e in sorted(agg.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1])))
            if ci and team]
