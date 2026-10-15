"""discovery_scanner: ServiceNow Discovery / BMC Helix Discovery.

Raw (data/raw/discovery_scanner/):
- ire_batch_NNNN.json: Identification and Reconciliation Engine payloads,
  {"items": [{"className", "values", "sys_object_source_info"}], "relations":
  [{"parent", "child", "type"}]}, as sent to the CMDB Instance API
  (/api/now/identifyreconcile). Device items carry name, serial_number,
  ip_address, os, os_version, model, manufacturer, last_discovered.
  Application / database items carry the process name and tcp_port and a
  "Runs on::Runs" relation to their host item (listening services).
- scan_schedule.csv: one row per subnet: schedule, discovery type, status,
  last run, devices found.

Connector output: discovered (identified devices and shadow candidates),
listening (ip, port, ci), aliases (hostname differing from the CI name when
identified by serial), scan_coverage.
"""

import json
import os

from ingest.sources.common import raw_dir, read_csv, write_csv
from ingest.sources.identify import Identifier

BATCH = 600
METHOD_TYPE = {"active": "IP Scan", "passive": "Passive (network sensor)", "cloud_api": "Cloud API",
               "aggregate": "Endpoint management summary"}


def write(records, listening, coverage, out):
    """records: views.discovery_view scan records; listening: [(app, kind, host, ip, port)];
    coverage: views scan coverage rows."""
    d = raw_dir(out, "discovery_scanner")
    host_rec = {r["hostname"]: r for r in records}
    items, n = [], 0
    by_host = {}
    for app, cls, host, ip, port in listening:
        by_host.setdefault(host, []).append((app, cls, port))

    def flush(batch_items, batch_rel, k):
        with open(os.path.join(d, f"ire_batch_{k:04d}.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump({"items": batch_items, "relations": batch_rel}, f, indent=1)

    batch, rel, k = [], [], 0
    for r in records:
        i = len(batch)
        batch.append({"className": r["class_name"], "internal_id": f"dev-{len(batch)}", "values": {
            "name": r["hostname"], "serial_number": r["serial_number"], "ip_address": r["ip_address"],
            "os": r["os"], "os_version": r["os_version"], "model_number": r["model"], "manufacturer": r["manufacturer"],
            "last_discovered": r["last_scanned"].replace("T", " ").rstrip("Z"), "u_site": r["site"]},
            "lookup": [{"className": "cmdb_ci_network_adapter", "values": {"name": "eth0", "ip_address": r["ip_address"]}}],
            "sys_object_source_info": {"source_name": "ServiceNow Discovery", "source_native_key": r["ip_address"],
                                       "source_feed": METHOD_TYPE[r["method"]],
                                       "source_recency_timestamp": r["last_scanned"].replace("T", " ").rstrip("Z")}})
        for app, cls, port in sorted(by_host.get(r["hostname"], [])):
            batch.append({"className": cls, "internal_id": f"svc-{len(batch)}",
                          "values": {"name": app, "tcp_port": str(port), "running_process_command": app},
                          "sys_object_source_info": {"source_name": "ServiceNow Discovery",
                                                     "source_native_key": f"{r['ip_address']}:{port}"}})
            rel.append({"parent": len(batch) - 1, "child": i, "type": "Runs on::Runs"})
        if len(batch) >= BATCH:
            k += 1
            flush(batch, rel, k)
            batch, rel = [], []
    if batch:
        k += 1
        flush(batch, rel, k)
    write_csv(os.path.join(d, "scan_schedule.csv"),
              [{"subnet": c["subnet"], "u_site": c["site"], "u_kind": c["kind"],
                "schedule": f"{c['site']} {c['kind']} scan", "discovery_type": METHOD_TYPE[c["method"]],
                "active": c["scanned"], "status": "Completed" if c["scanned"] == "true" else "Excluded",
                "last_run": c["last_scan"].replace("T", " ").rstrip("Z"), "devices_found": c["devices_found"]}
               for c in coverage],
              ["subnet", "u_site", "u_kind", "schedule", "discovery_type", "active", "status", "last_run",
               "devices_found"])
    return k


def read(data, cmdb):
    """IRE payloads -> discovered devices (identified), listening services, aliases."""
    d = os.path.join(data, "raw", "discovery_scanner")
    ident = Identifier(cmdb["cmdb_ci"])
    back = {v: k for k, v in METHOD_TYPE.items()}
    discovered, listening, aliases = [], [], set()
    for fn in sorted(f for f in os.listdir(d) if f.startswith("ire_batch_")):
        with open(os.path.join(d, fn), encoding="utf-8") as f:
            payload = json.load(f)
        items = payload["items"]
        host_of = {r["parent"]: r["child"] for r in payload["relations"] if r["type"] == "Runs on::Runs"}
        for i, it in enumerate(items):
            v = it["values"]
            if "tcp_port" in v:  # a listening service on its host item
                host = items[host_of[i]]["values"]
                ci, _ = ident.match(name=v["name"])
                if ci:
                    listening.append({"ip": host["ip_address"], "port": int(v["tcp_port"]), "ci": ci,
                                      "source": "discovery_scanner", "source_record": f"{fn}#items[{i}]"})
                continue
            ci, rule = ident.match(serial=v["serial_number"] or None, name=v["name"], ip=v["ip_address"])
            if ci and rule == "serial_number" and v["name"].lower() != ci.lower():
                aliases.add((v["name"], ci, "discovery_scanner", "serial_number"))
            discovered.append({
                "key": f"{v['u_site']}|{v['name']}|{v['ip_address']}", "hostname": v["name"],
                "serial_number": v["serial_number"], "ip_address": v["ip_address"], "os": v["os"],
                "os_version": v["os_version"], "model": v["model_number"], "manufacturer": v["manufacturer"],
                "site": v["u_site"], "last_scanned": v["last_discovered"].replace(" ", "T") + "Z",
                "method": back[it["sys_object_source_info"]["source_feed"]], "class_name": it["className"],
                "ci": ci, "match_rule": rule, "source": "discovery_scanner", "source_record": f"{fn}#items[{i}]"})
    coverage = [{"subnet": r["subnet"], "site": r["u_site"], "kind": r["u_kind"], "scanned": r["active"],
                 "method": back[r["discovery_type"]], "last_scan": (r["last_run"].replace(" ", "T") + "Z")
                 if r["last_run"] else "", "devices_found": r["devices_found"]}
                for r in read_csv(os.path.join(d, "scan_schedule.csv"))]
    return {"discovered": discovered, "listening": listening, "coverage": coverage,
            "aliases": [{"alias": a, "ci": c, "source": s, "match_rule": m} for a, c, s, m in sorted(aliases)]}
