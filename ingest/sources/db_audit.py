"""db_audit: database audit records (SQL Server Audit, as read by sys.fn_get_audit_file).

Raw (data/raw/db_audit/audit_records.csv): event_time, sequence_number,
action_id (SL select, IN insert, UP update, DL delete, AL alter, CR create,
DR drop, EX execute), succeeded, session_server_principal_name,
server_principal_name (pseudonymous worker ID), server_instance_name,
database_name, schema_name, object_name, statement, class_type, client_ip,
application_name. Demo sites and GCC-HYD users only. Non-SQL-Server engines
(IRIS, Oracle, PostgreSQL) are exported by their audit tools into the same
columns.

Connector: user -> database actions: SELECT -> read, DML -> write,
DDL/admin -> admin (with every admin timestamp for the change-window check).
"""

import os

from ingest.sources.common import raw_dir, read_csv, write_csv

COLUMNS = ["event_time", "sequence_number", "action_id", "succeeded", "session_id", "database_principal_name",
           "session_server_principal_name",
           "server_principal_name", "server_instance_name", "database_name", "schema_name", "object_name",
           "statement", "class_type", "client_ip", "application_name"]
ACTION = {"SL": "read", "IN": "write", "UP": "write", "DL": "write", "EX": "write", "AL": "admin", "CR": "admin",
          "DR": "admin"}


def write(records, out):
    write_csv(os.path.join(raw_dir(out, "db_audit"), "audit_records.csv"), records, COLUMNS)
    return len(records)


def read(data, ident):
    agg = {}
    for i, r in enumerate(read_csv(os.path.join(data, "raw", "db_audit", "audit_records.csv"))):
        if r["succeeded"] != "1":
            continue
        db, rule = ident.match(name=r["server_instance_name"])
        act = ACTION[r["action_id"]]
        t = r["event_time"].replace(" ", "T")[:19] + "Z"
        key = (r["server_principal_name"], db or r["server_instance_name"], act)
        e = agg.setdefault(key, {"count": 0, "first": t, "last": t, "times": [], "hosts": set(), "rule": rule,
                                 "record": f"audit_records.csv#{i + 2}"})
        e["count"] += 1
        e["first"], e["last"] = min(e["first"], t), max(e["last"], t)
        e["hosts"].add(r["application_name"].split("@")[-1])
        if act == "admin":
            e["times"].append(t)
    return [{"source": "db_audit", "source_record": e["record"], "user": u, "target": t, "action": a,
             "registered": True, "target_rule": e["rule"], "count": e["count"], "failures": 0, "first_seen": e["first"],
             "last_seen": e["last"], "workstations": sorted(e["hosts"]), "times": sorted(e["times"])}
            for (u, t, a), e in sorted(agg.items())]
