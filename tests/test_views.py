"""DATA_SPEC 9.1, 9.2, 9.3, 9.5, 9.6: determinism, agreement without drift,
exact drift catalogue, headline scenario, size report."""

import csv
import hashlib
import json
import os
from collections import Counter

from ingest.generate import generate
from tests import oracle

CATALOGUE = {"D1": 8, "D2": 6, "D3": 4, "D4": 3, "D5": 6, "D6": 10, "D7": 5, "D8": 8, "D9": 4,
             "D10": 3, "D11": 1, "D12": 4, "D13": 5}


def hashes(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            p = os.path.join(d, f)
            with open(p, "rb") as fh:
                out[os.path.relpath(p, root)] = hashlib.sha256(fh.read()).hexdigest()
    return out


def test_determinism(drift_data, tmp_path):
    """9.1: two runs with SEED=42 produce identical file hashes."""
    generate(42, str(tmp_path), inject=True, command="x")
    a, b = hashes(drift_data), hashes(str(tmp_path))
    a.pop("README.md"), b.pop("README.md")  # README embeds the output path
    assert a == b and len(a) >= 20


def test_views_agree_without_drift(clean_data):
    """9.2: with drift off the oracle reports nothing."""
    res = oracle.check(clean_data)
    assert res["findings"] == set()
    assert res["aliases"] == set()
    with open(os.path.join(clean_data, "ground_truth.json")) as f:
        assert json.load(f) == []


def test_drift_catalogue_exact(drift_data):
    """9.3: findings equal the ground truth exactly, by type and count."""
    gt, must, must_not, aliases = oracle.expected(drift_data)
    assert Counter(e["type"] for e in gt) == CATALOGUE
    headline = [e for e in gt if e["id"] == "D1-HEADLINE"]
    assert len(headline) == 1 and headline[0]["type"] == "D1"
    for e in gt:
        assert set(e) >= {"id", "type", "description", "ci_ids", "sources", "expected_finding"}
        if e["type"] == "D3":
            assert e["expected_finding"] == "none"
        if e["type"] == "D13":
            assert e["expected_finding"] == "resolve_as_same_ci"
    res = oracle.check(drift_data)
    assert res["findings"] - must == set(), "unexpected findings"
    assert must - res["findings"] == set(), "missed drift"
    assert must_not & res["findings"] == set(), "failover trap flagged"
    assert res["aliases"] == aliases, "naming mismatches not resolved"
    # D13 CIs must not surface as undocumented / shadow / ghost
    d13 = {ci for e in gt if e["type"] == "D13" for ci in e["ci_ids"]}
    for f in res["findings"]:
        assert not (set(f[1:]) & d13 and f[0] in ("undocumented", "shadow", "ghost")), f


def _csv(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def test_headline_scenario(drift_data):
    """9.5: ORU-CRIT routes reach lrc-db-01 in observed data for >= 40
    hospitals; CMDB links lrc-db-01 only to LIS reporting; CHG-HEADLINE
    targets dc1-host-07."""
    routes = set()
    with open(os.path.join(drift_data, "observed", "flows_hourly.jsonl")) as f:
        for line in f:
            if '"lrc-db-01"' in line:
                rec = json.loads(line)
                if rec["src_name"].startswith("ORU-CRIT-") and rec["dst_name"] == "lrc-db-01":
                    routes.add(rec["src_name"])
    assert len(routes) >= 40, len(routes)
    rels = _csv(os.path.join(drift_data, "cmdb", "cmdb_rel_ci.csv"))
    users_of_lrc = {r["parent"] for r in rels if r["child"] == "lrc-db-01" and r["type"] == "Depends on::Used by"}
    assert users_of_lrc == {"lis-reporting"}
    assert not [r for r in rels if r["parent"] == "lrc-db-01" and r["type"] == "Depends on::Used by"]
    runs = {(r["parent"], r["child"]) for r in rels if r["type"] == "Runs on::Runs"}
    assert ("lrc-db-01", "dc1-vm-lrc01") in runs and ("dc1-vm-lrc01", "dc1-host-07") in runs
    chg = [c for c in _csv(os.path.join(drift_data, "cmdb", "change_request.csv")) if c["number"] == "CHG-HEADLINE"]
    assert len(chg) == 1 and chg[0]["cmdb_ci"] == "dc1-host-07" and chg[0]["state"] == "scheduled"


def size_report(data):
    """Expected graph size computed from the files (what the loader creates)."""
    j = lambda *p: os.path.join(data, *p)  # noqa: E731
    cis = _csv(j("cmdb", "cmdb_ci.csv"))
    nodes = Counter(c["class"] for c in cis)
    n_disc = sum(1 for _ in open(j("discovery", "scan_results.jsonl")))
    ref = {t: len(_csv(j(*p))) for t, p in {
        "Site": ("cmdb", "cmn_location.csv"), "Department": ("cmdb", "cmn_department.csv"),
        "SupportGroup": ("cmdb", "sys_user_group.csv"), "Change": ("cmdb", "change_request.csv"),
        "User": ("identity", "users.csv"), "Group": ("identity", "groups.csv"),
        "Role": ("identity", "roles.csv")}.items()}
    total_nodes = len(cis) + n_disc + sum(ref.values())
    rels = len(_csv(j("cmdb", "cmdb_rel_ci.csv")))
    return {"by_class": dict(nodes), "discovered": n_disc, "reference": ref, "total_nodes": total_nodes,
            "cmdb_relationships": rels}


def test_size_report(drift_data):
    """9.6: print counts; fail only outside 30k-300k nodes (RUN_PROMPT 4.5)."""
    rep = size_report(drift_data)
    sites = Counter(r["type"] for r in _csv(os.path.join(drift_data, "cmdb", "cmn_location.csv")))
    print("\nSIZE REPORT", json.dumps({**rep, "sites_by_type": dict(sites)}, indent=1, sort_keys=True))
    assert 30_000 <= rep["total_nodes"] <= 300_000, rep["total_nodes"]
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(drift_data) for f in fs)
    assert size < 500 * 1024 * 1024, size
