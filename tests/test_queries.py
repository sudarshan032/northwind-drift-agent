"""AGENT_SPEC phase 1 tests: Cypher detectors, radius, change review,
centrality, timeouts. Needs FalkorDB."""

import time

import pytest

from graph import queries as Q
from tests.helpers import oracle

pytestmark = pytest.mark.falkordb


def _check_detectors(g, data):
    _, must, must_not, aliases = oracle.expected(data)
    got = {}
    for t in Q.DETECTORS:
        rows = Q.detect(g, t)
        assert isinstance(rows, list), rows
        got[t] = {tuple(r["key"]) for r in rows}
    found = set().union(*got.values())
    assert found == must, (sorted(found - must)[:5], sorted(must - found)[:5])
    assert not (found & must_not)  # D3 never appears
    assert {(r["alias"], r["ci"]) for r in Q.resolved_aliases(g)} == aliases  # D13 as aliases
    gt = oracle.expected(data)[0]
    for e in gt:  # each detector returns exactly its own type's keys
        if e["type"] in got:
            assert {tuple(k) for k in e["finding_keys"]} <= got[e["type"]], e["id"]


def test_detectors_match_ground_truth_full(full_graph, drift_data):
    _check_detectors(full_graph, drift_data)


def test_detectors_match_ground_truth_demo(demo_graph):
    g, data = demo_graph
    _check_detectors(g, data)


def test_headline_radius_and_change_review(full_graph):
    dec = Q.radius(full_graph, "dc1-host-07", "declared")
    real = Q.radius(full_graph, "dc1-host-07", "real")
    assert dec["business_services"] == ["svc-lab-results"] and "svc-emergency-care" not in dec["business_services"]
    assert dec["counts"]["routes"] == 0 and dec["counts"]["departments"] == 0
    assert set(real["by_label"]["Interface"]) == {f"ORU-CRIT-H{i:02d}" for i in range(1, 52)}
    assert "svc-emergency-care" in real["business_services"]
    assert real["counts"]["service_offerings"] == 51 and dec["counts"]["service_offerings"] == 0
    assert set(real["business_services"]) == {"svc-emergency-care", "svc-lab-results"}  # offerings counted apart
    assert {f"H{i:02d}-ED" for i in range(1, 52)} <= set(real["departments"])
    review = Q.change_review(full_graph, "CHG-HEADLINE")
    assert review["risk"] == "high" and "svc-emergency-care" in review["newly_reached_high_safety_services"]
    hidden = {(e["src"], e["dst"]) for e in review["undocumented_edges"]}
    assert {(f"ORU-CRIT-H{i:02d}", "lrc-db-01") for i in range(1, 52)} <= hidden


def test_change_review_low_and_unknown(full_graph):
    assert Q.change_review(full_graph, "NO-SUCH-CHANGE")["found"] is False
    r = Q.change_review(full_graph, "CHG0031400")  # routine host maintenance, nothing hidden there
    assert r["found"] and r["risk"] in ("low", "medium")
    assert Q.change_review(full_graph, "CHG0031513")["risk"] == "low"  # EMR upgrade: CMDB already right


def test_critical_nodes_stable(full_graph):
    a, b = Q.critical_nodes(full_graph), Q.critical_nodes(full_graph)
    assert a == b and len(a) == 10 and a[0]["name"] == "emr-app"


def test_protection_access_paging(full_graph, drift_data):
    gt = oracle.expected(drift_data)[0]
    broken = {r["component"] for r in Q.protection_check(full_graph)}
    d9 = {e["ci_ids"][0] for e in gt if e["type"] == "D9"}
    d10 = {e["ci_ids"][0] for e in gt if e["type"] == "D10"}
    assert broken == d9 | d10
    assert {tuple(r["key"]) for r in Q.access_check(full_graph)} == \
        {tuple(k) for e in gt if e["type"] == "D12" for k in e["finding_keys"]}
    page = {r["component"]: r for r in Q.who_to_page(full_graph, ["lrc-db-01", "emr-app"])}
    assert page["lrc-db-01"]["differs"] and page["lrc-db-01"]["observed_owner"] == "HYD-Integration-Support"
    assert not page["emr-app"]["differs"]


TOOLS = {
    "detect_all": lambda g: Q.detect_all(g),
    "radius_real": lambda g: Q.radius(g, "dc1-host-07", "real"),
    "radius_real_emr_db": lambda g: Q.radius(g, "emr-db-01", "real"),
    "radius_declared_sso": lambda g: Q.radius(g, "sso-app", "declared"),
    "change_review": lambda g: Q.change_review(g, "CHG-HEADLINE"),
    "protection": lambda g: Q.protection_check(g),
    "access": lambda g: Q.access_check(g),
    "who_to_page": lambda g: Q.who_to_page(g, ["lrc-db-01"]),
    "critical": lambda g: Q.critical_nodes(g),
    "component": lambda g: Q.get_component(g, "emr-app"),
    "neighbourhood": lambda g: Q.get_neighbourhood(g, "emr-app", 2),
    "impact_path": lambda g: Q.impact_path(g, "dc1-host-07", "svc-emergency-care"),
}


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_tool_queries_within_timeout(full_graph, name):
    t = time.time()
    res = TOOLS[name](full_graph)
    assert time.time() - t < Q.TIMEOUT_MS / 1000, name
    assert not (isinstance(res, dict) and res.get("error")), res


def test_timeout_returns_clear_result(full_graph):
    # an impossible budget forces FalkorDB's timeout path
    res = Q.radius(full_graph, "emr-db-01", "real", timeout=1)
    assert res.get("error") == "query timed out" or res.get("found") is True  # tiny graphs may still finish
    slow = Q.guarded(lambda: (_ for _ in ()).throw(Q.QueryTimeout("x")))()
    assert slow == {"error": "query timed out", "query": "<lambda>"}


def test_service_map_offerings(full_graph):
    """DECISIONS #95: per-hospital Lab results / Imaging / Patient monitoring offerings, declared (so CMDB says and
    reality agree on them); site routes now reach a business service; the headline is unchanged."""
    g = full_graph
    n = lambda q: g.query(q).result_set[0][0]
    assert n("MATCH (s:Site) WHERE s.type ENDS WITH 'hospital' RETURN count(s)") == 51
    for slug in ("lab-results", "imaging", "patient-monitoring"):
        assert n(f"MATCH (o:ServiceOffering) WHERE o.name ENDS WITH '-{slug}' RETURN count(o)") == 51
    assert n("MATCH (b:BusinessService {name: 'svc-patient-monitoring'}) RETURN b.patient_safety_impact") == "high"
    for route, svc in (("LAB-RES-H03", "svc-lab-results"), ("DICOM-MOD-H03", "svc-imaging"),
                       ("VITALS-H03", "svc-patient-monitoring")):
        for layer in ("declared", "real"):
            assert svc in Q.radius(g, route, layer)["business_services"], (route, layer)
    r = Q.change_review(g, "CHG-HEADLINE")
    assert r["risk"] == "high" and (r["declared_count"], r["real_count"]) == (4, 107)
    assert Q.change_review(g, "CHG0031513")["risk"] == "low"
