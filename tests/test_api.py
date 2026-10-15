"""AGENT_SPEC phase 3: API endpoints (FastAPI TestClient, fake LLM)."""

import shutil

import pytest
from fastapi.testclient import TestClient

import api.main as M
from agent.llm import NO_KEY, FakeLLM
from tests.conftest import _load

pytestmark = pytest.mark.falkordb


@pytest.fixture(scope="module")
def client(drift_data, tmp_path_factory):
    data = str(tmp_path_factory.mktemp("api") / "data")
    shutil.copytree(drift_data, data)
    ld = _load(data, "northwind_test_api")
    M.GRAPH, M.DATA, M.RUNTIME = "northwind_test_api", data, str(tmp_path_factory.mktemp("rt"))
    M.app.state.llm = None
    yield TestClient(M.app)
    ld.g.delete()


def test_health_without_key(client):
    r = client.get("/health").json()
    assert r["status"] == "ok" and r["components"] > 20000 and r["llm"] is False and r["llm_message"] == NO_KEY


def test_change_review_endpoint(client):
    r = client.post("/changes/CHG-HEADLINE/review").json()
    assert r["facts"]["risk"] == "high"
    g = r["graph"]
    assert {"dc1-host-07", "lrc-db-01"} <= {n["id"] for n in g["nodes"]}
    edges = {(e["source"], e["target"]): e for e in g["edges"]}  # CMDB direction: parent -> child
    assert edges[("dc1-vm-lrc01", "dc1-host-07")]["label"] == "Runs on" and \
        edges[("dc1-vm-lrc01", "dc1-host-07")]["status"] == "structural"
    hidden = edges[("ORU-CRIT-H03", "lrc-db-01")]  # the hidden dependency, dashed red
    assert hidden["status"] == "observed_only" and hidden["layers"] == ["evidence"]
    assert hidden["evidence"]["source"] == "interface_engine" and hidden["evidence"]["connector"] == "Database Writer"
    assert hidden["label"] == "Depends on (observed: interface engine, not in CMDB)"
    assert edges[("lis-reporting", "lrc-db-01")]["status"] == "confirmed"
    nodes = {n["id"]: n for n in g["nodes"]}
    assert nodes["dc1-host-07"]["changed"] and nodes["ORU-CRIT-H03"]["impacted_real"]
    assert not nodes["ORU-CRIT-H03"]["impacted_declared"] and nodes["svc-h51-ed-critical-results"]["class"] == "Service Offering"
    low = client.post("/changes/CHG0031513/review").json()["facts"]
    assert low["risk"] == "low" and low["hidden_edges"] == []
    assert client.post("/changes/NOPE/review").status_code == 404


def test_scan_findings_approve_reject(client):
    r = client.post("/scan").json()
    assert r["total"] > 50 and r["top"][0]["key"] == ["undocumented_group", "lrc-db-01"]
    fs = client.get("/findings").json()
    head = fs[0]
    assert client.get(f"/findings/{head['id']}").json()["type"] == "D1"
    assert client.get("/findings/F-nope").status_code == 404
    ok = client.post(f"/findings/{head['id']}/approve", json={"approver": "test"}).json()
    assert ok["status"] == "approved" and ok["resolved"] is True
    # once the CMDB records the 51 dependencies, declared == real: the change is no longer hidden risk
    assert client.post("/changes/CHG-HEADLINE/review").json()["facts"]["risk"] == "low"
    assert client.post(f"/findings/{head['id']}/approve").status_code == 409
    other = next(f for f in fs if f["status"] == "open" and f["id"] != head["id"])
    assert client.post(f"/findings/{other['id']}/reject", json={"reason": "no"}).json()["status"] == "rejected"
    assert client.get("/findings", params={"status": "rejected"}).json()[0]["id"] == other["id"]


def test_radius_subgraph_critical_hospitals(client):
    real = client.get("/radius", params={"component": "dc1-host-07", "layer": "real"}).json()
    assert real["counts"]["hospitals"] == 51
    assert client.get("/radius", params={"component": "nope"}).status_code == 404
    assert client.get("/radius", params={"component": "x", "layer": "bad"}).status_code == 422
    sub = client.get("/subgraph", params={"component": "lrc-db-01", "depth": 2}).json()
    assert 0 < len(sub["edges"]) <= 150 and any(e["layer"] == "observed" for e in sub["edges"])
    assert client.get("/subgraph", params={"component": "lrc-db-01", "depth": 3}).status_code == 422
    crit = client.get("/critical").json()
    assert len(crit) == 10
    hs = client.get("/hospitals", params={"component": "dc1-host-07"}).json()
    assert len(hs) == 51 and all(h["lat"] is not None for h in hs) and all(h["in_radius"] for h in hs)


def test_agent_endpoints_with_fake_llm(client):
    M.app.state.llm = FakeLLM([{"tool": "radius", "input": {"component": "lrc-db-01", "layer": "real"}},
                               {"text": "lrc-db-01 reaches 51 hospitals."}])
    try:
        r = client.post("/chat", json={"message": "What breaks if lrc-db-01 fails?"}).json()
        assert r["answer"] == "lrc-db-01 reaches 51 hospitals." and r["steps"][0]["tool"] == "radius"
        M.app.state.llm = FakeLLM([{"text": "No tools needed."}])
        r = client.post("/incident", json={"symptom": "ED results delayed at H03"}).json()
        assert r["answer"] == "No tools needed."
    finally:
        M.app.state.llm = None
    assert client.post("/chat", json={"message": "hi"}).json()["answer"] == NO_KEY
    assert client.get("/notifications").json() == []


def test_ui_served(client):
    html = client.get("/").text
    assert "cytoscape/3.30.2/cytoscape.min.js" in html and 'id="cy"' in html and "/changes/" in html
    assert "prefers-color-scheme: dark" in html


def test_sources_endpoint(client):
    rows = client.get("/sources").json()
    assert [r["name"] for r in rows] == ["cmdb_export", "discovery_scanner", "apm_otel", "netflow", "interface_engine",
                                         "backup_software", "storage_telemetry", "identity_sso", "db_audit", "hr_feed",
                                         "itsm_history"]
    assert all(r["sample"]["raw"] and r["sample"]["evidence"] and r["raw_records"] > 0 for r in rows)
    ie = next(r for r in rows if r["name"] == "interface_engine")
    assert "Database Writer" in ie["sample"]["raw"] and ie["sample"]["evidence"]["server"] == "lrc-db-01"


def test_findings_show_sources_and_action(client):
    client.post("/scan")
    fs = client.get("/findings", params={"limit": 500}).json()
    by = {f["type"]: f for f in fs}
    head = next(f for f in fs if f["key"] == ["undocumented_group", "lrc-db-01"])
    assert head["action"] == "cmdb_patch" and head["sources"] == ["cmdb_export", "interface_engine"]
    assert by["D10"]["action"] == "ticket" and by["D10"]["ticket"]["table"] == "incident"
    assert by["D11"]["ticket"]["table"] == "sn_si_incident"
