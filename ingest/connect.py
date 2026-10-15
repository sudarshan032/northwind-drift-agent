"""Run every connector on the raw source files and write normalised evidence.

    python -m ingest.connect --data data/

Order matters: the CMDB export first (what can be identified), then the
discovery scanner (devices, listening services, serial-number aliases), then
every other source through the same Identifier. Output in data/normalised/:

    cmdb_ci.csv, cmdb_rel_ci.csv, cmn_location.csv, cmn_department.csv,
    sys_user_group.csv, change_request.csv        (cmdb_export, names resolved)
    discovered.jsonl, listening.jsonl, scan_coverage.csv   (discovery_scanner)
    calls.jsonl        (apm_otel + interface_engine)
    netflow.jsonl, backups.jsonl, replication.jsonl (storage_telemetry + backup copies),
    access.jsonl (identity_sso + db_audit), workers.csv (hr_feed), owners.jsonl (itsm_history),
    aliases.jsonl (names resolved by serial number or IP), sources.json (per-source summary + samples)

Every evidence row carries `source` and `source_record`.
"""

import argparse
import json
import os

from ingest import views
from ingest.sources import SOURCES
from ingest.sources import (apm_otel, backup_software, cmdb_export, db_audit, discovery_scanner, hr_feed,
                            identity_sso, interface_engine, itsm_history, netflow, storage_telemetry)
from ingest.sources.common import read_csv, write_csv, write_jsonl
from ingest.sources.identify import Identifier

STORAGE = "Provides storage for::Stored on"


def ip_aliases(rows, pairs=(("client", "client_name", "client_rule"), ("server", "server_name", "server_rule"))):
    """Names that only resolved by IP are aliases of the CI (e.g. a DICOM AE title in reverse DNS)."""
    out = set()
    for r in rows:
        for ci_k, name_k, rule_k in pairs:
            ci, name, rule = r.get(ci_k), r.get(name_k), r.get(rule_k) or ""
            if ci and name and rule.endswith("ip_address") and name.lower() != ci.lower():
                out.add((name, ci, r["source"], "ip_address"))
    return out


def write_cmdb(data, cm):
    """Normalised CMDB tables (names instead of sys_ids) from the cmdb_export connector."""
    norm = os.path.join(data, "normalised")
    os.makedirs(norm, exist_ok=True)
    write_csv(os.path.join(norm, "cmdb_ci.csv"), cm["cmdb_ci"], views.CMDB_COLUMNS)
    write_csv(os.path.join(norm, "cmdb_rel_ci.csv"), cm["cmdb_rel_ci"], views.REL_COLUMNS)
    for t, cols in (("cmn_location", ["sys_id", "name", "full_name", "type", "subtype", "city", "state", "country",
                                      "beds", "utc_offset_hours"]),
                    ("cmn_department", ["sys_id", "name", "code", "location"]),
                    ("sys_user_group", ["name", "description", "type"]),
                    ("change_request", ["number", "short_description", "type", "state", "approval", "cmdb_ci",
                                        "assignment_group", "start_date", "end_date", "risk"])):
        write_csv(os.path.join(norm, f"{t}.csv"), cm[t], cols)


def run(data):
    norm = os.path.join(data, "normalised")
    os.makedirs(norm, exist_ok=True)
    cm = cmdb_export.read(data)
    write_cmdb(data, cm)
    disc = discovery_scanner.read(data, cm)
    write_jsonl(os.path.join(norm, "discovered.jsonl"), disc["discovered"])
    write_jsonl(os.path.join(norm, "listening.jsonl"), disc["listening"])
    write_csv(os.path.join(norm, "scan_coverage.csv"), disc["coverage"],
              ["subnet", "site", "kind", "scanned", "method", "last_scan", "devices_found"])
    ident = Identifier(cm["cmdb_ci"], [(a["alias"], a["ci"]) for a in disc["aliases"]],
                       [(x["ip"], x["port"], x["ci"]) for x in disc["listening"]])
    calls = apm_otel.read(data, ident) + interface_engine.read(data, ident)
    flows = netflow.read(data, ident)
    backups, copies = backup_software.read(data, ident)
    cis = {r["name"]: r for r in cm["cmdb_ci"]}
    rpo = {}
    for r in cm["cmdb_rel_ci"]:  # RPO of what each array stores (min over its data holders)
        c = cis[r["child"]]
        if r["type"] == STORAGE and c["rpo_minutes"] not in ("", None) and c["backup_policy"]:
            rpo[r["parent"]] = min(rpo.get(r["parent"], 10 ** 9), int(c["rpo_minutes"]))
    repl = storage_telemetry.read(data, ident, rpo) + copies
    access = identity_sso.read(data, ident) + db_audit.read(data, ident)
    workers = hr_feed.read(data)
    raw_cis = cmdb_export.read_raw_index(data)
    owners = itsm_history.read(data, raw_cis["ci"], raw_cis["group"])
    aliases = {(a["alias"], a["ci"], a["source"], a["match_rule"]) for a in disc["aliases"]} | ip_aliases(calls) | \
        ip_aliases(flows)
    alias_rows = [{"alias": a, "ci": c, "source": s, "match_rule": m} for a, c, s, m in sorted(aliases)]
    out = {"calls": calls, "netflow": flows, "backups": backups, "replication": repl, "access": access,
           "owners": owners, "aliases": alias_rows}
    for name, rows in out.items():
        write_jsonl(os.path.join(norm, f"{name}.jsonl"), rows)
    write_csv(os.path.join(norm, "workers.csv"), workers, ["id", "status", "effective_date", "source", "source_record"])
    by_source = {}
    for rows in list(out.values()) + [disc["discovered"], disc["listening"], workers]:
        for r in rows:
            by_source[r.get("source", "")] = by_source.get(r.get("source", ""), 0) + 1
    by_source["cmdb_export"] = len(cm["cmdb_ci"]) + len(cm["cmdb_rel_ci"])
    smp = samples(data, cm, disc, out, workers)
    rc = raw_counts(data)
    summary = [dict(s, evidence_rows=by_source.get(s["name"], 0), raw_records=rc.get(s["name"]),
                    sample=smp.get(s["name"])) for s in SOURCES]
    with open(os.path.join(norm, "sources.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, indent=1)
    return {s["name"]: s["evidence_rows"] for s in summary}


def raw_counts(data):
    """Records in each source's raw export (rows, items, samples, sessions)."""
    raw = lambda *p: os.path.join(data, "raw", *p)  # noqa: E731
    lines = lambda path: sum(1 for x in open(path, encoding="utf-8") if x.strip())  # noqa: E731
    out = {"cmdb_export": sum(lines(raw("cmdb_export", f)) - 1 for f in os.listdir(raw("cmdb_export")))}
    items = 0
    for f in os.listdir(raw("discovery_scanner")):
        if f.startswith("ire_batch_"):
            with open(raw("discovery_scanner", f), encoding="utf-8") as fh:
                items += len(json.load(fh)["items"])
    out["discovery_scanner"] = items
    out["apm_otel"] = sum(sum(1 for x in open(os.path.join(raw("apm_otel", "scrapes", g), f), encoding="utf-8")
                              if x and not x.startswith("#"))
                          for g in ("hourly", "daily") for f in os.listdir(raw("apm_otel", "scrapes", g)))
    out["netflow"] = sum(lines(raw("netflow", f)) - 1 for f in os.listdir(raw("netflow")))
    with open(raw("interface_engine", "channels.xml"), encoding="utf-8") as f:
        out["interface_engine"] = f.read().count("<channel ") + lines(raw("interface_engine", "channel_statistics.csv")) - 1
    with open(raw("backup_software", "sessions.json"), encoding="utf-8") as f:
        out["backup_software"] = len(json.load(f)["data"])
    out["storage_telemetry"] = lines(raw("storage_telemetry", "replication_samples.jsonl"))
    out["identity_sso"] = lines(raw("identity_sso", "signins.jsonl"))
    out["db_audit"] = lines(raw("db_audit", "audit_records.csv")) - 1
    out["hr_feed"] = lines(raw("hr_feed", "workers.csv")) - 1
    out["itsm_history"] = sum(lines(raw("itsm_history", f)) - 1 for f in os.listdir(raw("itsm_history")))
    return out


def _first(it, pred):
    return next((x for x in it if pred(x)), None)


def samples(data, cm, disc, out, workers):
    """One raw record per source next to the evidence row it produced (UI Sources tab, README)."""
    raw = lambda *p: os.path.join(data, "raw", *p)  # noqa: E731
    r = {}
    ci_raw = {x["name"]: x for x in read_csv(raw("cmdb_export", "cmdb_ci.csv"))}
    r["cmdb_export"] = {"raw": ci_raw["lrc-db-01"], "evidence": _first(cm["cmdb_ci"], lambda x: x["name"] == "lrc-db-01"),
                        "raw_file": "raw/cmdb_export/cmdb_ci.csv"}
    ev = _first(disc["discovered"], lambda x: x["hostname"] == "LIS-PROD-02.nw.local") or disc["discovered"][0]
    fn, idx = ev["source_record"].split("#items[")
    with open(raw("discovery_scanner", fn), encoding="utf-8") as f:
        item = json.load(f)["items"][int(idx.rstrip("]"))]
    r["discovery_scanner"] = {"raw": item, "evidence": ev, "raw_file": f"raw/discovery_scanner/{fn}"}
    calls = out["calls"]
    ev = _first(calls, lambda x: x["source"] == "apm_otel" and x["client"] == "emr-app" and x["server"] == "emr-db-01")
    d = raw("apm_otel", "scrapes", "hourly")
    last = sorted(os.listdir(d))[-1]
    with open(os.path.join(d, last), encoding="utf-8") as f:
        lines = [x for x in f.read().splitlines() if 'client="emr-app"' in x and 'server="emr-db-01"' in x]
    r["apm_otel"] = {"raw": lines, "evidence": ev, "raw_file": f"raw/apm_otel/scrapes/hourly/{last}"}
    ev = _first(out["netflow"], lambda x: x["client"] == "h03-mri-01")
    nf = _first(read_csv(raw("netflow", "nfdump_hourly.csv")), lambda x: x["srcDns"] == "h03-mri-01")
    r["netflow"] = {"raw": nf, "evidence": ev, "raw_file": "raw/netflow/nfdump_hourly.csv"}
    with open(raw("interface_engine", "channels.xml"), encoding="utf-8") as f:
        xml = f.read()
    i = xml.index("<name>ORU-CRIT-H03</name>")
    j = xml.index("<transportName>Database Writer</transportName>", i)
    k = xml.rfind("<connector ", i, j)
    r["interface_engine"] = {"raw": xml[k:xml.index("</connector>", j) + len("</connector>")],
                             "evidence": _first(calls, lambda x: x["client"] == "ORU-CRIT-H03" and x["server"] == "lrc-db-01"),
                             "raw_file": "raw/interface_engine/channels.xml"}
    with open(raw("backup_software", "sessions.json"), encoding="utf-8") as f:
        sess = _first(json.load(f)["data"], lambda x: x["objectName"] == "emr-db-01")
    r["backup_software"] = {"raw": sess, "evidence": _first(out["backups"], lambda x: x["object"] == "emr-db-01"
                                                            and x["job_type"] == "backup"),
                            "raw_file": "raw/backup_software/sessions.json"}
    from ingest.sources.common import read_jsonl
    smp = _first(read_jsonl(raw("storage_telemetry", "replication_samples.jsonl")), lambda x: x["endpoint"] == "pod-replica-links")
    r["storage_telemetry"] = {"raw": smp, "evidence": _first(out["replication"], lambda x: x["source"] == "storage_telemetry"
                                                             and x["source_array"] == smp["_polled_array"]),
                              "raw_file": "raw/storage_telemetry/replication_samples.jsonl"}
    si = _first(read_jsonl(raw("identity_sso", "signins.jsonl")), lambda x: x["appDisplayName"] == "emr-app")
    r["identity_sso"] = {"raw": si, "evidence": _first(out["access"], lambda x: x["user"] == si["userPrincipalName"]
                                                       and x["target"] == "emr-app"), "raw_file": "raw/identity_sso/signins.jsonl"}
    au = _first(read_csv(raw("db_audit", "audit_records.csv")), lambda x: x["action_id"] == "AL")
    r["db_audit"] = {"raw": au, "evidence": _first(out["access"], lambda x: x["user"] == au["server_principal_name"]
                                                   and x["action"] == "admin"), "raw_file": "raw/db_audit/audit_records.csv"}
    wk = _first(read_csv(raw("hr_feed", "workers.csv")), lambda x: x["Terminated"] == "1")
    r["hr_feed"] = {"raw": wk, "evidence": _first(workers, lambda x: x["id"] == wk["Employee_ID"]),
                    "raw_file": "raw/hr_feed/workers.csv"}
    lrc_sid = ci_raw["lrc-db-01"]["sys_id"]
    inc = _first(read_csv(raw("itsm_history", "incident.csv")), lambda x: x["cmdb_ci"] == lrc_sid)
    r["itsm_history"] = {"raw": inc, "evidence": _first(out["owners"], lambda x: x["ci"] == "lrc-db-01"
                                                        and x["team"] == "HYD-Integration-Support"),
                         "raw_file": "raw/itsm_history/incident.csv"}
    return r


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data")
    print(json.dumps(run(ap.parse_args().data), indent=1))


if __name__ == "__main__":
    main()
