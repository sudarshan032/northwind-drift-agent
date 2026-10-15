"""DATA_SPEC 9.4 physical plausibility (on the truth model) plus basic
footprint and hygiene checks."""

import re
from collections import Counter

from ingest.world import CLASS_LABEL


def _idx(world):
    by = {c["name"]: c for c in world["cis"]}
    out = {}
    for r in world["rels"]:
        out.setdefault((r["parent"], r["type"]), []).append(r["child"])
    return by, out


def test_footprint(world):
    types = Counter(s["type"] for s in world["sites"])
    assert types["large_hospital"] == 10 and types["community_hospital"] == 25
    assert types["critical_access_hospital"] == 16 and types["clinic"] == 1000 and types["non_acute"] == 800
    assert {s["state"] for s in world["sites"] if s["state"]} == {"AK", "CA", "MT", "NM", "OR", "TX", "WA"}
    site = {s["id"]: s for s in world["sites"]}
    assert (site["H03"]["city"], site["H03"]["type"]) == ("Seattle", "large_hospital")
    assert (site["H17"]["city"], site["H17"]["type"]) == ("Portland", "community_hospital")
    assert (site["H44"]["state"], site["H44"]["type"]) == ("MT", "critical_access_hospital")


def test_every_hospital_has_emergency(world):
    hospitals = {s["id"] for s in world["sites"] if s["type"].endswith("hospital")}
    with_ed = {d["site"] for d in world["departments"] if d["code"] == "ED"}
    assert hospitals <= with_ed


def test_department_counts_by_tier(world):
    n = Counter(d["site"] for d in world["departments"])
    rng = {"large_hospital": (18, 24), "community_hospital": (10, 14), "critical_access_hospital": (5, 7),
           "clinic": (1, 3), "non_acute": (1, 1)}
    for s in world["sites"]:
        if s["type"] in rng:
            lo, hi = rng[s["type"]]
            assert lo <= n[s["id"]] <= hi, s["id"]


def test_devices_only_in_existing_departments(world):
    depts = {d["id"]: d for d in world["departments"]}
    for c in world["cis"]:
        if c["department"]:
            assert c["department"] in depts, c["name"]
            assert depts[c["department"]]["site"] == c["site"], c["name"]
        if c["label"] == "MedicalDevice":
            assert c["department"], c["name"]


def test_no_mri_or_ct_outside_hospitals(world):
    site = {s["id"]: s for s in world["sites"]}
    for c in world["cis"]:
        if re.search(r"-(ct|mri)-\d+$", c["name"]):
            assert site[c["site"]]["type"].endswith("hospital"), c["name"]


def test_medical_devices_on_medical_segment(world):
    by, out = _idx(world)
    devices = [c for c in world["cis"] if c["label"] == "MedicalDevice"]
    for c in devices:
        segs = out[(c["name"], "Connects through::Connects")]
        assert any(by[s]["class"] == "cmdb_ci_ip_network" and by[s]["site"] == c["site"] for s in segs), c["name"]
    share = sum(not c["patchable"] for c in devices) / len(devices)
    assert 0.35 <= share <= 0.45, share


# DATA_SPEC section 2 table as literals. Imaging rows (ct, mri, xray, us) use the
# research-backed ranges that replaced the table values (RESEARCH_NOTES R2, DECISIONS #31).
SPEC_INVENTORY = {
    #                    wan     fw      core    access    medseg  servers   array   ct      mri     xray     us       mon      pump    lab       ws          prn
    "large_hospital":   [(2, 2), (2, 2), (4, 6), (30, 50), (1, 1), (10, 14), (1, 1), (3, 5), (2, 4), (8, 15), (10, 20), (6, 10), (1, 1), (12, 20), (600, 1000), (100, 200)],
    "community_hospital": [(2, 2), (1, 1), (2, 2), (10, 20), (1, 1), (4, 7), (0, 1), (1, 2), (1, 1), (3, 6), (4, 8), (2, 4), (1, 1), (4, 8), (150, 350), (30, 60)],
    "critical_access_hospital": [(1, 1), (1, 1), (1, 1), (3, 6), (1, 1), (1, 2), (0, 0), (0, 1), (0, 0), (1, 2), (1, 1), (1, 1), (0, 1), (1, 3), (40, 80), (8, 15)],
    "clinic":           [(1, 1), (1, 1), (1, 1), (1, 2), (0, 1), (0, 0), (0, 0), (0, 0), (0, 0), (0, 1), (0, 1), (0, 0), (0, 0), (0, 2), (10, 30), (2, 5)],
    "non_acute":        [(1, 1), (0, 1), (0, 0), (0, 1), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (0, 0), (3, 15), (1, 3)],
}
ROWS = ["wan", "fw", "core", "access", "medseg", "servers", "array", "ct", "mri", "xray", "us", "mon", "pump",
        "lab", "ws", "prn"]
PATTERNS = {"wan": r"-wan-\d+$", "fw": r"-fw-\d+$", "core": r"-core-\d+$", "access": r"-acc-\d+$",
            "medseg": r"-medseg$", "servers": r"^[hcn]\d+-(vm|srv)-\d+$", "array": r"^arr-[hcn]\d+-",
            "ct": r"-ct-\d+$", "mri": r"-mri-\d+$", "xray": r"-xray-\d+$", "us": r"-us-\d+$",
            "mon": r"-mon-\d+$", "pump": r"-pump-\d+$", "lab": r"-lab-\d+$"}


def test_inventory_ranges(world):
    """Every row of the DATA_SPEC section 2 table, for every site type."""
    per = Counter()
    for c in world["cis"]:
        for k, p in PATTERNS.items():
            if re.search(p, c["name"]):
                per[(c["site"], k)] += 1
        n = int(c["count"]) if c["count"] != "" else 1
        if c["class"] == "cmdb_ci_computer" or c["name"].endswith("-ws"):
            per[(c["site"], "ws")] += n
        if c["class"] == "cmdb_ci_printer" or c["name"].endswith("-prn"):
            per[(c["site"], "prn")] += n
    for s in world["sites"]:
        if s["type"] in SPEC_INVENTORY:
            for k, (lo, hi) in zip(ROWS, SPEC_INVENTORY[s["type"]]):
                assert lo <= per[(s["id"], k)] <= hi, (s["id"], k, per[(s["id"], k)])
    clinics = [s["id"] for s in world["sites"] if s["type"] == "clinic"]
    share = sum(per[(c, "xray")] for c in clinics) / len(clinics)
    assert 0.25 <= share <= 0.35, share  # "30% of clinics" have X-ray


def test_vms_run_on_hosts_at_same_site(world):
    by, out = _idx(world)
    for c in world["cis"]:
        if c["label"] == "VM":
            hosts = out[(c["name"], "Runs on::Runs")]
            assert len(hosts) == 1 and by[hosts[0]]["label"] == "Host", c["name"]
            assert by[hosts[0]]["site"] == c["site"], c["name"]


def _reaches(start, out, by, label):
    todo, seen = [start], set()
    while todo:
        x = todo.pop()
        for t in ("Runs on::Runs", "Stores on::Stores"):
            for y in out.get((x, t), []):
                if by[y]["label"] == label:
                    return True
                if y not in seen:
                    seen.add(y)
                    todo.append(y)
    return False


def test_app_chains_reach_storage(world):
    """Central apps and DBs reach a storage array. Local apps reach a host at
    their site, and the site's array when it has one (DECISIONS.md #10)."""
    by, out = _idx(world)
    arrays = {c["site"] for c in world["cis"] if c["label"] == "StorageArray"}
    for c in world["cis"]:
        if c["label"] not in ("Application", "Database") or c["site"] == "EXTERNAL":
            continue
        if c["site"] in arrays:
            assert _reaches(c["name"], out, by, "StorageArray"), c["name"]
        else:
            assert _reaches(c["name"], out, by, "Host"), c["name"]


def test_no_phi_in_central_india(world):
    by, _ = _idx(world)
    for c in world["cis"]:
        if c["site"] == "cloud-central-india":
            assert not c["handles_phi"] and c["data_residency"] == "IN", c["name"]
    for r in world["rels"]:
        for leg in r["obs"]:
            if by[leg["dst"]]["site"] == "cloud-central-india":
                assert not by[leg["src"]]["handles_phi"], leg
        if r["type"].startswith("Replicates") and by[r["child"]]["site"] == "cloud-central-india":
            raise AssertionError(r)


def test_ids_unique_and_wellformed(world):
    names = [c["name"] for c in world["cis"]]
    assert len(names) == len(set(names))
    sys_ids = [c["sys_id"] for c in world["cis"]]
    assert len(set(sys_ids)) == len(sys_ids) and all(re.fullmatch(r"[0-9a-f]{32}", s) for s in sys_ids)
    serials = [c["serial_number"] for c in world["cis"] if c["serial_number"]]
    assert len(serials) == len(set(serials))
    ips = [c["ip_address"] for c in world["cis"] if c["ip_address"]]
    assert len(ips) == len(set(ips))
    assert {c["class"] for c in world["cis"]} <= set(CLASS_LABEL)


def test_no_personal_data(world):
    for u in world["identity"]["users"]:
        assert re.fullmatch(r"u-[a-z0-9]+-\d{5}", u["id"]), u["id"]
        assert set(u) == {"id", "site", "department", "job_role", "status", "status_source",
                          "primary_group", "workstation"}
    hyd = [u for u in world["identity"]["users"] if u["site"] == "GCC-HYD"]
    assert 250 <= len(hyd) <= 350


def test_headline_chain_in_truth(world):
    by, out = _idx(world)
    assert out[("lrc-db-01", "Runs on::Runs")] == ["dc1-vm-lrc01"]
    assert out[("dc1-vm-lrc01", "Runs on::Runs")] == ["dc1-host-07"]
    on_host = [r["parent"] for r in world["rels"] if r["child"] == "dc1-host-07" and r["type"] == "Runs on::Runs"]
    assert on_host == ["dc1-vm-lrc01"]
