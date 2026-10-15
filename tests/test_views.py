"""DATA_SPEC 9.1, 9.2, 9.3, 9.5, 9.6: determinism, agreement without drift,
exact drift catalogue, headline scenario, size report."""

import csv
import hashlib
import json
import os
import subprocess
import sys
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


def test_determinism_across_processes(drift_data, tmp_path):
    """9.1 in a fresh interpreter with a different hash seed."""
    root = os.path.join(os.path.dirname(__file__), "..")
    env = dict(os.environ, PYTHONHASHSEED="12345")
    subprocess.run([sys.executable, "-m", "ingest.generate", "--seed", "42", "--out", str(tmp_path)],
                   cwd=root, env=env, check=True, capture_output=True)
    a, b = hashes(drift_data), hashes(str(tmp_path))
    a.pop("README.md"), b.pop("README.md")
    assert a == b


def test_every_discoverable_ci_discovered_without_drift(clean_data):
    """9.2 (discovery part), checked directly: every individual discoverable
    CMDB CI has a scan record (exempt: CIs with no IP, i.e. the cloud vault)."""
    from ingest.world import DISCOVERABLE_CLASSES
    cis = _csv(os.path.join(clean_data, "cmdb", "cmdb_ci.csv"))
    with open(os.path.join(clean_data, "discovery", "scan_results.jsonl")) as f:
        serials = {json.loads(line)["serial_number"] for line in f}
    want = [c for c in cis if c["class"] in DISCOVERABLE_CLASSES and not c["count"]]
    exempt = {c["name"] for c in want if not c["ip_address"]}
    assert exempt == {"bkp-vault-usw-01"}
    missing = [c["name"] for c in want if c["name"] not in exempt and c["serial_number"] not in serials]
    assert missing == [] and len(want) > 10_000


def test_views_agree_without_drift(clean_data):
    """9.2: with drift off the oracle reports nothing."""
    res = oracle.check(clean_data)
    assert res["findings"] == set()
    assert res["aliases"] == set()
    with open(os.path.join(clean_data, "ground_truth.json")) as f:
        assert json.load(f) == []


def test_protection_and_backups_without_drift(clean_data):
    """9.2 (protection part): every PHI data holder reaches DC-DR and the
    immutable cloud vault, and every declared `Backed up by` has jobs to that
    target (and nothing is backed up elsewhere)."""
    cis = {r["name"]: r for r in _csv(os.path.join(clean_data, "cmdb", "cmdb_ci.csv"))}
    reach = oracle.protection_paths(clean_data)
    holders = [n for n, r in cis.items() if r["handles_phi"] == "true" and r["backup_offsite"] == "true"]
    assert len(holders) > 50
    for n in holders:
        sites = {cis[x]["site"] for x in reach.get(n, ()) if x in cis}
        assert "DC-DR" in sites and "bkp-vault-usw-01" in reach[n], n
    declared = {(r["parent"], r["child"]) for r in _csv(os.path.join(clean_data, "cmdb", "cmdb_rel_ci.csv"))
                if r["type"] == "Backed up by::Backs up"}
    with open(os.path.join(clean_data, "observed", "backup_jobs.jsonl")) as f:
        observed = {(j["object"], j["target"]) for j in map(json.loads, f)}
    assert declared == observed and declared


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
    # D3 pairs really are silent (not just skipped by the failover rule)
    d3 = {frozenset(e["ci_ids"]) for e in gt if e["type"] == "D3"}
    for fname in ("flows_hourly.jsonl", "flows_daily.jsonl", "netflow_hourly.jsonl", "netflow_daily.jsonl"):
        with open(os.path.join(drift_data, "observed", fname)) as f:
            for line in f:
                r = json.loads(line)
                assert frozenset((r["src_name"], r["dst_name"])) not in d3, r
    with open(os.path.join(drift_data, "observed", "replication_status.jsonl")) as f:
        for line in f:
            r = json.loads(line)
            assert [r["source_array"], r["target_array"]] not in [e["ci_ids"] for e in gt if e["type"] == "D3"], r
    res = oracle.check(drift_data)
    assert res["findings"] - must == set(), "unexpected findings"
    assert must - res["findings"] == set(), "missed drift"
    assert must_not & res["findings"] == set(), "failover trap flagged"
    assert res["aliases"] == aliases, "naming mismatches not resolved"
    # D13 CIs must not surface as undocumented / shadow / ghost
    d13 = {ci for e in gt if e["type"] == "D13" for ci in e["ci_ids"]}
    for f in res["findings"]:
        assert not (set(f[1:]) & d13 and f[0] in ("undocumented", "shadow", "ghost")), f


def test_no_phi_in_central_india_except_d11(drift_data):
    """9.4 on the generated drift-on files: nothing in cloud-central-india
    holds PHI, and the only PHI movement into it is the D11 entry."""
    cis = {r["name"]: r for r in _csv(os.path.join(drift_data, "cmdb", "cmdb_ci.csv"))}
    assert all(r["handles_phi"] == "false" for r in cis.values() if r["site"] == "cloud-central-india")
    res = oracle.check(drift_data)["findings"]
    gt = oracle.expected(drift_data)[0]
    d11 = {tuple(k) for e in gt if e["type"] == "D11" for k in e["finding_keys"]}
    assert {f for f in res if f[0] == "residency"} == d11 and len(d11) == 1


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
    locs = {r["sys_id"]: r["type"] for r in _csv(os.path.join(drift_data, "cmdb", "cmn_location.csv"))}
    cis = _csv(os.path.join(drift_data, "cmdb", "cmdb_ci.csv"))
    extra = {"sites_by_type": dict(Counter(locs.values())),
             "cis_by_site_type": dict(Counter(locs[c["site"]] for c in cis)),
             "declared_edges": rep["cmdb_relationships"] + sum(1 for c in cis for k in ("site", "support_group",
                                                                                     "department") if c[k])}
    print("\nSIZE REPORT", json.dumps({**rep, **extra}, indent=1, sort_keys=True))
    print("(observed and total edge counts are printed by the loader / test_loader)")
    assert 30_000 <= rep["total_nodes"] <= 300_000, rep["total_nodes"]
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(drift_data) for f in fs)
    assert size < 500 * 1024 * 1024, size


def test_demo_subset_reproducible_and_exact(tmp_path):
    """The committed data_demo/ equals a fresh `--subset demo --gzip` run, and
    the oracle finds exactly its ground truth."""
    generate(42, str(tmp_path), inject=True, subset="demo", gz=True)
    fresh = hashes(str(tmp_path))
    fresh.pop("README.md")
    repo = os.path.join(os.path.dirname(__file__), "..", "data_demo")
    committed = hashes(repo)
    committed.pop("README.md", None)
    assert fresh == committed
    gt, must, must_not, aliases = oracle.expected(str(tmp_path))
    res = oracle.check(str(tmp_path))
    assert res["findings"] == must and not (must_not & res["findings"]) and res["aliases"] == aliases
    assert {k: v for k, v in Counter(e["type"] for e in gt).items() if k != "D13"} == \
        {k: v for k, v in CATALOGUE.items() if k != "D13"}  # H44 (one D13 case) is outside WA/OR
    assert Counter(e["type"] for e in gt)["D13"] == 4
    # headline in the subset: one route per WA/OR hospital (24), all staged in lrc-db-01
    headline = next(e for e in gt if e["id"] == "D1-HEADLINE")
    assert len(headline["finding_keys"]) == 24
