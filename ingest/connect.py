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
from ingest.sources.common import write_csv, write_jsonl
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
    summary = [dict(s, evidence_rows=by_source.get(s["name"], 0)) for s in SOURCES]
    with open(os.path.join(norm, "sources.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, indent=1)
    return {s["name"]: s["evidence_rows"] for s in summary}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data")
    print(json.dumps(run(ap.parse_args().data), indent=1))


if __name__ == "__main__":
    main()
