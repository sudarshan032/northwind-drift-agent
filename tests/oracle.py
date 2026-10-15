"""Pure-Python reference checker.

Computes drift findings from the generated *views* only (CMDB, discovery,
observed, identity, evidence), never from `truth/world.json`, using the same
rules the future agent will run as Cypher on the graph:

- undocumented: observed flow/netflow between two CIs on >= 3 distinct days,
  no declared `Depends on` between them (either direction)
- stale: declared `Depends on` / `Replicates to` with no observed traffic or
  replication in the 14-day window, failover=false, neither end retired
- retired_linked: retired CI still in any CMDB relationship
- owner: dominant team in ownership evidence != CMDB support_group
- shadow: scan record that identifies no CI (serial, else hostname + IP)
- ghost: operational, discoverable CI with an IP in a scanned subnet, never
  discovered and never seen in flows
- attribute: identified CI whose os_version / serial_number / ip_address
  differs from discovery
- protection: PHI CI with backup_offsite=true whose storage, backup and
  replication path never reaches another site
- backup_failing: CI with a backup policy and no successful job in the last
  7 days (transient failures followed by success are therefore ignored)
- residency: PHI data replicated or sent into cloud-central-india
- access: access with no grant path, by a disabled user, or admin on a
  production database outside an approved change window

Flows tagged `health_check=true` are ignored. Endpoints resolve by exact
name (or sys_id), then by IP; IP and serial resolutions are returned as
aliases (D13).
"""

import csv
import ipaddress
import json
import os
from datetime import datetime, timedelta, timezone

from ingest.views import open_in
from ingest.world import DISCOVERABLE_CLASSES

END = datetime(2026, 10, 14, 23, 59, 59, tzinfo=timezone.utc)
LEVEL = {"read": 1, "login": 1, "write": 2, "admin": 3}
DEP, REP, STORES = "Depends on::Used by", "Replicates to::Replica of", "Stores on::Stores"


def _csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _jsonl(path):
    if not (os.path.exists(path) or os.path.exists(path + ".gz")):
        return
    with open_in(path) as f:  # plain or .gz
        for line in f:
            yield json.loads(line)


def _t(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def check(data):
    """Return {"findings": set of tuples, "aliases": set of (alias, ci)}."""
    j = lambda *p: os.path.join(data, *p)  # noqa: E731
    cis = {r["name"]: r for r in _csv(j("cmdb", "cmdb_ci.csv"))}
    by_sys = {r["sys_id"]: n for n, r in cis.items()}
    ip_owner = {}
    for n, r in cis.items():
        if r["ip_address"]:
            ip_owner.setdefault(r["ip_address"], []).append(n)
    by_ip = {ip: ns[0] for ip, ns in ip_owner.items() if len(ns) == 1}
    rels = _csv(j("cmdb", "cmdb_rel_ci.csv"))
    findings, aliases = set(), set()

    def resolve(name, ip):
        if name in cis:
            return name
        if name in by_sys:
            return by_sys[name]
        if ip in by_ip:
            aliases.add((name, by_ip[ip]))
            return by_ip[ip]
        return None

    # ---- observed traffic
    days, seen = {}, set()
    for fname in ("flows_hourly.jsonl", "flows_daily.jsonl", "netflow_hourly.jsonl", "netflow_daily.jsonl"):
        for rec in _jsonl(j("observed", fname)):
            if rec.get("health_check"):
                continue
            s, d = resolve(rec["src_name"], rec["src_ip"]), resolve(rec["dst_name"], rec["dst_ip"])
            if s is None or d is None:
                continue
            day = rec.get("day") or rec["hour"][:10]
            days.setdefault((s, d), set()).add(day)
            seen.update((s, d))
    replic = {}
    for rec in _jsonl(j("observed", "replication_status.jsonl")):
        replic.setdefault((rec["source_array"], rec["target_array"]), []).append(rec)

    declared_dep = {frozenset((r["parent"], r["child"])) for r in rels if r["type"] == DEP}
    for (s, d), ds in days.items():
        if len(ds) >= 3 and frozenset((s, d)) not in declared_dep:
            findings.add(("undocumented", s, d))

    def retired(n):
        return cis[n]["status"] == "retired"

    for r in rels:
        a, b = r["parent"], r["child"]
        if r["failover"] == "true" or retired(a) or retired(b):
            continue
        if r["type"] == DEP and (a, b) not in days and (b, a) not in days:
            findings.add(("stale", a, b, DEP))
        if r["type"] == REP and (a, b) not in replic:
            findings.add(("stale", a, b, REP))

    linked = {r["parent"] for r in rels} | {r["child"] for r in rels}
    for n in cis:
        if retired(n) and n in linked:
            findings.add(("retired_linked", n))

    # ---- ownership evidence
    totals = {}
    for e in _csv(j("evidence", "ownership_evidence.csv")):
        totals.setdefault(e["ci"], {}).setdefault(e["team"], 0)
        totals[e["ci"]][e["team"]] += int(e["incidents_resolved"]) + int(e["changes_implemented"])
    for ci, teams in totals.items():
        best = sorted(teams.items(), key=lambda kv: -kv[1])
        if len(best) > 1 and best[0][1] == best[1][1]:
            continue
        if ci in cis and best[0][0] != cis[ci]["support_group"]:
            findings.add(("owner", ci))

    # ---- discovery
    by_serial = {r["serial_number"]: n for n, r in cis.items() if r["serial_number"]}
    by_host_ip = {(n, r["ip_address"]): n for n, r in cis.items() if r["ip_address"]}
    discovered = set()
    for rec in _jsonl(j("discovery", "scan_results.jsonl")):
        ci = by_serial.get(rec["serial_number"]) if rec["serial_number"] else None
        if ci is None:
            ci = by_host_ip.get((rec["hostname"], rec["ip_address"]))
        if ci is None:
            findings.add(("shadow", rec["hostname"], rec["ip_address"]))
            continue
        discovered.add(ci)
        if rec["hostname"] != ci:
            aliases.add((rec["hostname"], ci))
        for field in ("os_version", "serial_number", "ip_address"):
            if cis[ci][field] and rec[field] and cis[ci][field] != rec[field]:
                findings.add(("attribute", ci, field))
    scanned = [(ipaddress.ip_network(c["subnet"]), c["site"]) for c in _csv(j("discovery", "scan_coverage.csv"))
               if c["scanned"] == "true"]
    for n, r in cis.items():
        if (r["status"] != "operational" or r["class"] not in DISCOVERABLE_CLASSES or r["count"]
                or not r["ip_address"] or n in discovered or n in seen):
            continue
        ip = ipaddress.ip_address(r["ip_address"])
        if any(ip in net and site == r["site"] for net, site in scanned):
            findings.add(("ghost", n))

    # ---- protection paths, backups, residency
    jobs = {}
    for job in _jsonl(j("observed", "backup_jobs.jsonl")):
        jobs.setdefault(job["object"], []).append(job)
    edges = {}
    for r in rels:
        if r["type"] == STORES:
            edges.setdefault(r["parent"], set()).add(r["child"])
    for obj, js in jobs.items():
        for job in js:
            if job["status"] == "success":
                edges.setdefault(obj, set()).add(job["target"])
    for (a, b) in replic:
        edges.setdefault(a, set()).add(b)
    for n, r in cis.items():
        if r["status"] != "operational" or not r["backup_policy"]:
            continue
        ok = [j_ for j_ in jobs.get(n, []) if j_["status"] == "success" and _t(j_["end"]) > END - timedelta(days=7)]
        if not ok:
            findings.add(("backup_failing", n))
        if r["handles_phi"] == "true" and r["backup_offsite"] == "true":
            reach, todo = set(), [n]
            while todo:
                x = todo.pop()
                for y in edges.get(x, ()):
                    if y not in reach:
                        reach.add(y)
                        todo.append(y)
            if not any(y in cis and cis[y]["site"] != r["site"] for y in reach):
                findings.add(("protection", n))
    phi_on = {r["child"] for r in rels if r["type"] == STORES and cis[r["parent"]]["handles_phi"] == "true"}
    india = {n for n, r in cis.items() if r["site"] == "cloud-central-india" or r["data_residency"] == "IN"}
    for (a, b) in replic:
        if b in india and (a in phi_on or cis.get(a, {}).get("handles_phi") == "true"):
            findings.add(("residency", a, b))
    for (s, d) in days:
        if d in india and cis[s]["handles_phi"] == "true" and s not in india:
            findings.add(("residency", s, d))

    # ---- access
    users = {u["id"]: u for u in _csv(j("identity", "users.csv"))}
    groups = {g["id"]: g["roles"].split(";") for g in _csv(j("identity", "groups.csv"))}
    member = {}
    for m in _csv(j("identity", "memberships.csv")):
        member.setdefault(m["user"], []).append(m["group"])
    grants = {}
    for g in _csv(j("identity", "grants.csv")):
        grants.setdefault(g["role"], {})
        grants[g["role"]][g["target"]] = max(grants[g["role"]].get(g["target"], 0), LEVEL[g["level"]])
    windows = [(c["cmdb_ci"], _t(c["start_date"]), _t(c["end_date"])) for c in _csv(j("cmdb", "change_request.csv"))
               if c["approval"] == "approved"]
    for e in _jsonl(j("observed", "access_log.jsonl")):
        u, tgt = users.get(e["user"]), e["target"]
        key = ("access", e["user"], tgt)
        if u is None or u["status"] != "active":
            findings.add(key)
            continue
        lvl = max((grants.get(role, {}).get(tgt, 0) for g in member.get(e["user"], []) for role in groups[g]),
                  default=0)
        if lvl < LEVEL[e["action"]]:
            findings.add(key)
            continue
        c = cis.get(tgt)
        if (e["action"] == "admin" and c and c["class"].startswith("cmdb_ci_db_")
                and c["environment"] == "production"):
            t = _t(e["ts"])
            if not any(ci == tgt and s <= t <= en for ci, s, en in windows):
                findings.add(key)
    return {"findings": findings, "aliases": aliases}


def expected(data):
    """Findings and aliases the ground truth says must (and must not) appear."""
    with open(os.path.join(data, "ground_truth.json"), encoding="utf-8") as f:
        gt = json.load(f)
    must, must_not, aliases = set(), set(), set()
    for e in gt:
        keys = {tuple(k) for k in e["finding_keys"]}
        if e["expected_finding"] == "none":
            must_not |= keys
        elif e["expected_finding"] == "resolve_as_same_ci":
            aliases |= keys
        else:
            must |= keys
    return gt, must, must_not, aliases
