"""Benchmark scale generator: endpoint expansion and region classification (pure functions, no database)."""

from bench import scale
from bench.run import stats


def _world():
    nodes = [
        {"iid": 1, "labels": ["Site"], "props": {"id": "H03", "type": "community_hospital"}},
        {"iid": 2, "labels": ["Site"], "props": {"id": "DC-PRI", "type": "data_centre"}},
        {"iid": 3, "labels": ["Department"], "props": {"id": "H03-ED"}},
        {"iid": 4, "labels": ["Component", "Interface"], "props": {"name": "ORU-CRIT-H03", "site": "DC-PRI",
                                                                    "department": "H03-ED", "target": "emr-app"}},
        {"iid": 5, "labels": ["Component", "Database"], "props": {"name": "lrc-db-01", "site": "DC-PRI"}},
        {"iid": 6, "labels": ["Component", "EndpointGroup"], "props": {"name": "h03-ed-prn", "site": "H03", "count": 3}},
        {"iid": 7, "labels": ["Component", "NetworkDevice"], "props": {"name": "h03-core-01", "site": "H03"}},
    ]
    edges = [[1, 3, "CONTAINS", None, None], [4, 5, "OBSERVED_CALLS", None, False],
             [4, 3, "IN_DEPARTMENT", None, None], [6, 7, "CONNECTS_THROUGH", None, None]]
    return nodes, edges


def test_expand_and_classify():
    nodes, edges = scale.expand_endpoints(*_world())
    eps = [n for n in nodes if n["labels"] == ["Component", "Endpoint"]]
    assert [n["props"]["name"] for n in eps] == ["h03-ed-prn-001", "h03-ed-prn-002", "h03-ed-prn-003"]
    assert sum(1 for e in edges if e[2] == "CONNECTS_THROUGH") == 3 and not any(n["iid"] == 6 for n in nodes)
    regional, reg_sites, dept_site = scale.classify(nodes, edges)
    by = {n["props"].get("name") or n["props"]["id"]: regional[n["iid"]] for n in nodes}
    assert by["H03"] and by["H03-ED"] and by["ORU-CRIT-H03"] and by["h03-core-01"] and by["h03-ed-prn-001"]
    assert not by["DC-PRI"] and not by["lrc-db-01"]  # central: shared by every region
    p = scale.renamed({"name": "ORU-CRIT-H03", "site": "DC-PRI", "department": "H03-ED", "target": "emr-app"},
                      ["Component", "Interface"], 2, reg_sites, dept_site)
    assert p == {"name": "r2-ORU-CRIT-H03", "site": "DC-PRI", "department": "r2-H03-ED", "target": "emr-app"}


def test_stats():
    s = stats([float(x) for x in range(1, 101)])
    assert s["median_ms"] == 50.5 and s["p95_ms"] == 95 and s["runs"] == 100


def test_committed_results_verify():
    """The recorded benchmark run is complete: every step, gate passed, no errors, memory under the limit."""
    from bench.verify import verify
    problems, report = verify([50000, 250000, 500000, 3500000])
    assert problems == [] and all(r["gate"] == "passed" for r in report.values())
