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
    ld.load()
    first = ld.counts()
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
    assert counts["total_nodes"] == rep["total_nodes"]
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
    assert ld.unresolved == {}


def test_headline_path_in_graph(loaded):
    ld, _ = loaded
    n = ld.g.query("MATCH (i:Interface)-[:OBSERVED_CALLS]->(:Component {name: 'lrc-db-01'}) "
                   "WHERE i.name STARTS WITH 'ORU-CRIT-' RETURN count(DISTINCT i)").result_set[0][0]
    assert n >= 40
    declared = ld.g.query("MATCH (x:Component)-[:DEPENDS_ON]->(:Component {name: 'lrc-db-01'}) "
                          "RETURN collect(x.name)").result_set[0][0]
    assert declared == ["lis-reporting"]
