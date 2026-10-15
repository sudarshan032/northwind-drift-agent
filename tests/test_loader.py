"""DATA_SPEC 9.7: loading twice yields identical node/edge counts.
Needs FalkorDB (`docker compose up -d`)."""

import json
import os

import pytest
from falkordb import FalkorDB

from ingest.load import Loader
from tests.test_views import size_report

GRAPH = "northwind_test"
pytestmark = pytest.mark.falkordb


@pytest.fixture(scope="module")
def loaded(drift_data):
    host = os.environ.get("FALKORDB_HOST", "localhost")
    db = FalkorDB(host=host, port=int(os.environ.get("FALKORDB_PORT", 6379)))
    if GRAPH in db.list_graphs():
        db.select_graph(GRAPH).delete()
    ld = Loader(drift_data, GRAPH, host=host, port=int(os.environ.get("FALKORDB_PORT", 6379)))
    truth, hidden = os.path.join(drift_data, "truth"), os.path.join(drift_data, ".truth-hidden")
    os.rename(truth, hidden)  # DATA_SPEC 8: the loader never reads truth/world.json
    try:
        ld.load()
    finally:
        os.rename(hidden, truth)
    first = ld.counts()
    print("\nGRAPH COUNTS", json.dumps(first, indent=1))
    yield ld, first
    db.select_graph(GRAPH).delete()


def test_load_is_idempotent(loaded, drift_data):
    ld, first = loaded
    ld2 = Loader(drift_data, GRAPH, host=os.environ.get("FALKORDB_HOST", "localhost"),
                 port=int(os.environ.get("FALKORDB_PORT", 6379)))
    ld2.load()
    assert ld2.counts() == first


def test_graph_matches_files(loaded, drift_data):
    _, counts = loaded
    rep = size_report(drift_data)
    assert counts["total_nodes"] == rep["total_nodes"] + counts["nodes_by_label"]["UnregisteredEndpoint"]
    assert counts["nodes_by_label"]["UnregisteredEndpoint"] == 1  # D14
    assert counts["nodes_by_label"]["Discovered"] == rep["discovered"]
    assert counts["nodes_by_label"]["Component"] == sum(rep["by_class"].values())


def test_unidentified_discovered_equals_d6(loaded, drift_data):
    ld, _ = loaded
    n = ld.g.query("MATCH (d:Discovered) WHERE NOT (d)-[:IDENTIFIES]->(:Component) RETURN count(d)").result_set[0][0]
    with open(os.path.join(drift_data, "ground_truth.json")) as f:
        d6 = sum(1 for e in json.load(f) if e["type"] == "D6")
    assert n == d6 == 10


def test_d13_aliases_resolved(loaded, drift_data):
    ld, _ = loaded
    with open(os.path.join(drift_data, "ground_truth.json")) as f:
        pairs = [tuple(k) for e in json.load(f) if e["type"] == "D13" for k in e["finding_keys"]]
    for alias, ci in pairs:
        res = ld.g.query("MATCH (c:Component {name: $n}) RETURN c.aliases", {"n": ci}).result_set
        assert res and alias in (res[0][0] or []), (alias, ci)
    assert set(ld.unresolved) == {"cardiorisk.example.com"}  # D14 only


def test_headline_path_in_graph(loaded):
    ld, _ = loaded
    n = ld.g.query("MATCH (i:Interface)-[:OBSERVED_CALLS]->(:Component {name: 'lrc-db-01'}) "
                   "WHERE i.name STARTS WITH 'ORU-CRIT-' RETURN count(DISTINCT i)").result_set[0][0]
    assert n >= 40
    declared = ld.g.query("MATCH (x:Component)-[:DEPENDS_ON]->(:Component {name: 'lrc-db-01'}) "
                          "RETURN collect(x.name)").result_set[0][0]
    assert declared == ["lis-reporting"]


def test_headline_radius(loaded):
    """AGENT_SPEC 0.1d: full upstream path dc1-host-07 -> each hospital's ED
    and Emergency care (real), lab reporting only (declared)."""
    from graph import queries as Q
    ld, _ = loaded
    dec, real = Q.radius(ld.g, "dc1-host-07", "declared"), Q.radius(ld.g, "dc1-host-07", "real")
    assert dec["business_services"] == ["svc-lab-results"] and dec["by_label"]["Application"] == ["lis-reporting"]
    assert dec["counts"]["departments"] == dec["counts"]["hospitals"] == dec["counts"]["routes"] == 0
    assert real["counts"]["routes"] == 51 and real["counts"]["hospitals"] == 51
    assert "svc-emergency-care" in real["business_services"]
    assert {f"H{i:02d}-ED" for i in range(1, 52)} <= set(real["departments"])
    p = Q.impact_path(ld.g, "dc1-host-07", "H17-ED", via_routes=real["by_label"]["Interface"])
    assert p["nodes"] == ["dc1-host-07", "dc1-vm-lrc01", "lrc-db-01", "ORU-CRIT-H17", "H17-ED"]


def test_d14_path(loaded):
    from graph import queries as Q
    ld, _ = loaded
    rows = Q.unregistered_app_path(ld.g, "cardiorisk.example.com")
    assert rows and rows[0]["grp"] == "grp-H17-CARD-physician" and rows[0]["api"] == "emr-fhir-api"
    assert rows[0]["days_seen"] >= 3 and "emr-db-01" in rows[0]["data"]
