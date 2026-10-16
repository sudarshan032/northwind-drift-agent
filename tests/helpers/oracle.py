"""Pure-Python reference checker.

Computes drift findings from the normalised evidence the connectors produce
(data/normalised, ingest.connect) plus the declared IAM export, never from
`truth/world.json`, using the same rules the Cypher detectors run on the
graph (thresholds from ingest.config):

- undocumented: evidence (APM, interface engine, netflow) between two CIs on
  >= MIN_DAYS_UNDOCUMENTED distinct days, no declared `Depends on` either way
- stale: declared `Depends on` / `Replicates to`, nothing observed in the
  window, failover=false, neither end retired
- retired_linked: retired CI still in any CMDB relationship
- owner: dominant team in ITSM history != CMDB support_group (ties ignored)
- shadow: discovered device that identified no CI
- ghost: operational, discoverable CI with an IP in a scanned subnet, never
  discovered, no traffic
- attribute: identified CI whose os_version / serial_number / ip_address
  differs from discovery
- protection: PHI CI claiming offsite backup whose storage, successful backup
  (snapshot-only does not count) and replication path never leaves its site
- backup_failing: CI with backup jobs and no successful backup in the last
  BACKUP_MAX_AGE_DAYS (this subsumes the 48 h transient rule)
- residency: PHI data replicated or sent into cloud-central-india
- access: access to a CI by a worker HR does not list as active, with no
  grant path, or admin on a production database outside an approved window
- unregistered_app: an evidence endpoint that matches no CI on >= 3 days
"""

import csv
import ipaddress
import json
import os
from datetime import datetime, timedelta, timezone

from ingest.config import BACKUP_MAX_AGE_DAYS, MIN_DAYS_UNDOCUMENTED, SNAPSHOT_END
from ingest.sources.common import open_text, read_jsonl
from ingest.world import DISCOVERABLE_CLASSES

END = datetime.strptime(SNAPSHOT_END, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
LEVEL = {"read": 1, "login": 1, "write": 2, "admin": 3}
DEP, REP, STORES = "Depends on::Used by", "Replicates to::Replica of", "Provides storage for::Stored on"


def _csv(path):
    with open_text(path) as f:
        return list(csv.DictReader(f))


def _t(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def check(data):
    """Return {"findings": set of tuples, "aliases": set of (alias, ci)}."""
    n = lambda f: os.path.join(data, "normalised", f)  # noqa: E731
    cis = {r["name"]: r for r in _csv(n("cmdb_ci.csv"))}
    rels = _csv(n("cmdb_rel_ci.csv"))
    findings = set()
    aliases = {(r["alias"], r["ci"]) for r in read_jsonl(n("aliases.jsonl"))}

    # ---- traffic evidence (APM, interface engine, netflow)
    days, seen, unresolved = {}, set(), {}
    for fname in ("calls.jsonl", "netflow.jsonl"):
        for r in read_jsonl(n(fname)):
            for side in ("client", "server"):
                if r[side] is None and r.get(f"{side}_name"):
                    unresolved.setdefault(r[f"{side}_name"], set()).update(r["days"])
            if r["client"] and r["server"]:
                days.setdefault((r["client"], r["server"]), set()).update(r["days"])
                seen.update((r["client"], r["server"]))
    for name, ds in unresolved.items():
        if len(ds) >= MIN_DAYS_UNDOCUMENTED:
            findings.add(("unregistered_app", name))
    replic = {}
    for r in read_jsonl(n("replication.jsonl")):
        replic[(r["source_array"], r["target_array"])] = r

    declared_dep = {frozenset((r["parent"], r["child"])) for r in rels if r["type"] == DEP}
    for (s, d), ds in days.items():
        if len(ds) >= MIN_DAYS_UNDOCUMENTED and frozenset((s, d)) not in declared_dep:
            findings.add(("undocumented", s, d))

    def retired(x):
        return cis[x]["status"] == "retired"

    for r in rels:
        a, b = r["parent"], r["child"]
        if r["failover"] == "true" or retired(a) or retired(b):
            continue
        if r["type"] == DEP and (a, b) not in days and (b, a) not in days:
            findings.add(("stale", a, b, DEP))
        if r["type"] == REP and (a, b) not in replic:
            findings.add(("stale", a, b, REP))
    linked = {r["parent"] for r in rels} | {r["child"] for r in rels}
    for x in cis:
        if retired(x) and x in linked:
            findings.add(("retired_linked", x))

    # ---- ownership (ITSM history)
    totals = {}
    for e in read_jsonl(n("owners.jsonl")):
        totals.setdefault(e["ci"], {}).setdefault(e["team"], 0)
        totals[e["ci"]][e["team"]] += e["incidents_resolved"] + e["changes_implemented"]
    for ci, teams in totals.items():
        best = sorted(teams.items(), key=lambda kv: -kv[1])
        if len(best) > 1 and best[0][1] == best[1][1]:
            continue
        if ci in cis and best[0][0] != cis[ci]["support_group"]:
            findings.add(("owner", ci))

    # ---- discovery (identification done by the connector)
    discovered = set()
    for rec in read_jsonl(n("discovered.jsonl")):
        ci = rec["ci"]
        if ci is None:
            findings.add(("shadow", rec["hostname"], rec["ip_address"]))
            continue
        discovered.add(ci)
        for field in ("os_version", "serial_number", "ip_address"):
            if cis[ci][field] and rec[field] and cis[ci][field] != rec[field]:
                findings.add(("attribute", ci, field))
    scanned = [(ipaddress.ip_network(c["subnet"]), c["site"]) for c in _csv(n("scan_coverage.csv"))
               if c["scanned"] == "true"]
    for x, r in cis.items():
        if (r["status"] != "operational" or r["class"] not in DISCOVERABLE_CLASSES or r["count"]
                or not r["ip_address"] or x in discovered or x in seen):
            continue
        ip = ipaddress.ip_address(r["ip_address"])
        if any(ip in net and site == r["site"] for net, site in scanned):
            findings.add(("ghost", x))

    # ---- protection, backups, residency
    edges, last_ok, has_jobs = {}, {}, set()
    for r in rels:
        if r["type"] == STORES:  # child is stored on parent
            edges.setdefault(r["child"], set()).add(r["parent"])
    for b in read_jsonl(n("backups.jsonl")):
        if b["last_success"]:  # snapshot-only sessions never set last_success
            edges.setdefault(b["object"], set()).add(b["target"])
        if b["job_type"] == "backup":
            has_jobs.add(b["object"])
            if b["last_success"]:
                last_ok[b["object"]] = max(last_ok.get(b["object"], ""), b["last_success"])
    for (a, b) in replic:
        edges.setdefault(a, set()).add(b)
    cutoff = END - timedelta(days=BACKUP_MAX_AGE_DAYS)
    for x, r in cis.items():
        if r["status"] != "operational" or not r["backup_policy"]:
            continue
        if x in has_jobs and (x not in last_ok or _t(last_ok[x]) <= cutoff):
            findings.add(("backup_failing", x))
        if r["handles_phi"] == "true" and r["backup_offsite"] == "true":
            reach, todo = set(), [x]
            while todo:
                y = todo.pop()
                for z in edges.get(y, ()):
                    if z not in reach:
                        reach.add(z)
                        todo.append(z)
            if not any(z in cis and cis[z]["site"] != r["site"] for z in reach):
                findings.add(("protection", x))
    phi_on = {r["parent"] for r in rels if r["type"] == STORES and cis[r["child"]]["handles_phi"] == "true"}
    india = {x for x, r in cis.items() if r["site"] == "cloud-central-india" or r["data_residency"] == "IN"}
    for (a, b) in replic:
        if b in india and (a in phi_on or cis.get(a, {}).get("handles_phi") == "true"):
            findings.add(("residency", a, b))
    for (s, d) in days:
        if d in india and cis[s]["handles_phi"] == "true" and s not in india:
            findings.add(("residency", s, d))

    # ---- access (identity_sso + db_audit evidence, HR status, declared grants)
    status = {w["id"]: w["status"] for w in _csv(n("workers.csv"))}
    users = {u["id"] for u in _csv(os.path.join(data, "identity", "users.csv"))}
    groups = {g["id"]: g["roles"].split(";") for g in _csv(os.path.join(data, "identity", "groups.csv"))}
    member = {}
    for m in _csv(os.path.join(data, "identity", "memberships.csv")):
        member.setdefault(m["user"], []).append(m["group"])
    grants = {}
    for g in _csv(os.path.join(data, "identity", "grants.csv")):
        grants.setdefault(g["role"], {})
        grants[g["role"]][g["target"]] = max(grants[g["role"]].get(g["target"], 0), LEVEL[g["level"]])
    windows = [(c["cmdb_ci"], _t(c["start_date"]), _t(c["end_date"])) for c in _csv(n("change_request.csv"))
               if c["approval"] == "approved"]
    for e in read_jsonl(n("access.jsonl")):
        tgt = e["target"]
        if tgt not in cis:  # access to something with no CMDB record: covered by unregistered_app
            continue
        key = ("access", e["user"], tgt)
        if e["user"] not in users or status.get(e["user"]) != "active":
            findings.add(key)
            continue
        lvl = max((grants.get(role, {}).get(tgt, 0) for g in member.get(e["user"], []) for role in groups[g]),
                  default=0)
        if lvl < LEVEL[e["action"]]:
            findings.add(key)
            continue
        c = cis[tgt]
        if e["action"] == "admin" and c["class"].startswith("cmdb_ci_db_") and c["environment"] == "production":
            for t in map(_t, e["times"]):
                if not any(ci == tgt and s <= t <= en for ci, s, en in windows):
                    findings.add(key)
    return {"findings": findings, "aliases": aliases}


def protection_paths(data):
    """For every CI: the set of CIs reachable over Stores on (CMDB), successful
    backups and copies (backup_software) and replication (storage telemetry)."""
    n = lambda f: os.path.join(data, "normalised", f)  # noqa: E731
    edges = {}
    for r in _csv(n("cmdb_rel_ci.csv")):
        if r["type"] == STORES:
            edges.setdefault(r["child"], set()).add(r["parent"])
    for b in read_jsonl(n("backups.jsonl")):
        if b["last_success"]:
            edges.setdefault(b["object"], set()).add(b["target"])
    for r in read_jsonl(n("replication.jsonl")):
        edges.setdefault(r["source_array"], set()).add(r["target_array"])
    out = {}
    for start in list(edges):
        reach, todo = set(), [start]
        while todo:
            for y in edges.get(todo.pop(), ()):
                if y not in reach:
                    reach.add(y)
                    todo.append(y)
        out[start] = reach
    return out


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
