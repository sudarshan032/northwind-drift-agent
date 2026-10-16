"""AGENT_V2_SPEC with the fake LLM only: tool loop and step cap, MCP tools, actions, memory and recall,
rejected-fact suppression, cost caps, trace streaming, OpenAI key fallback (against the local FalkorDB)."""

import json
import logging
import shutil

import anyio
import pytest
from fastapi.testclient import TestClient

import api.main as M
from agent import actions as A, guardian as Gd, llm as L, mcp_tools as T, spend
from agent.memory import Memory
from tests.conftest import _load

pytestmark = pytest.mark.falkordb
GRAPH = "northwind_test_agent2"


@pytest.fixture(scope="module")
def env(drift_data, tmp_path_factory):
    data = str(tmp_path_factory.mktemp("a2") / "data")
    shutil.copytree(drift_data, data)
    ld = _load(data, GRAPH)
    saved = (M.GRAPH, M.DATA, M.RUNTIME, M.PUBLIC, M._db)
    M.GRAPH, M.DATA, M.PUBLIC, M._db = GRAPH, data, True, None
    M.RUNTIME = str(tmp_path_factory.mktemp("a2_rt"))
    cache = str(tmp_path_factory.mktemp("a2_cache"))
    M.app.state.llm = L.CachedLLM(cache)
    client = TestClient(M.app)
    client.post("/scan")
    mem = M.memory()
    mem.reset()
    T.configure(M.graph, lambda: mem)
    yield client, ld.g, mem
    mem.reset()
    M.GRAPH, M.DATA, M.RUNTIME, M.PUBLIC, M._db = saved
    M.app.state.llm = M.app.state.agent_llm = None
    ld.g.delete()


VERDICT = {"risk": "high", "root_cause": "lrc-db-01", "reasons": ["dc1-host-07 reaches 51 hospitals."],
           "hidden_links": ["ORU-CRIT-H01 -> lrc-db-01"], "warn": [], "actions": ["Work note added."],
           "evidence": ["radius"]}


def script():
    return L.FakeLLM([{"tool": "get_change", "input": {"change_id": "CHG-HEADLINE", "why": "find the target"}},
                      {"tool": "radius", "input": {"component": "dc1-host-07", "layer": "real", "why": "real impact"}},
                      {"tool": "hidden_links", "input": {"component": "dc1-host-07", "why": "they differ"}},
                      {"tool": "add_change_note", "input": {"change_id": "CHG-HEADLINE", "note": "51 hospitals",
                                                            "why": "record the real impact"}},
                      {"text": "enough evidence"}, {"text": json.dumps(VERDICT)}])


def run(g, mem, kind, trigger, llm, **k):
    return anyio.run(lambda: Gd.collect(g, mem, kind, trigger, llm, T.server, **k))


def test_mcp_lists_and_calls_tools(env):
    from mcp import Client
    _, g, _ = env

    async def go():
        async with Client(T.server) as c:
            tools = {t.name: t for t in (await c.list_tools()).tools}
            r = await c.call_tool("radius", {"component": "dc1-host-07", "layer": "real"})
            bad = await c.call_tool("radius", {"component": "dc1-host-07", "layer": "sideways"})
            return tools, json.loads(r.content[0].text), bad.is_error
    tools, res, bad = anyio.run(go)
    want = {"radius", "hidden_links", "impact_path", "who_to_page", "protection_check", "access_check",
            "shared_dependencies", "changes_on", "critical_nodes", "get_component", "get_neighbourhood",
            "search_components", "recall", "get_change", "findings_about", "unregistered_app_path"}
    assert set(tools) == want and all(len(t.description) > 30 for t in tools.values())
    assert not set(tools) & set(A.SPECS)  # action tools are not on the public MCP server
    assert res["counts"]["hospitals"] == 51 and bad


def test_mcp_http_endpoint(env):
    client, _, _ = env
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                    headers={"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"})
    assert r.status_code == 200 and "hidden_links" in r.text


def test_tool_loop_trace_actions_and_memory(env):
    _, g, mem = env
    ev = run(g, mem, "change", "CHG-HEADLINE", script())
    types = [e["type"] for e in ev]
    assert types[0] == "start" and types[-2:] == ["verdict", "done"]
    steps = [e for e in ev if e["type"] == "step"]
    assert [s["tool"] for s in steps] == ["get_change", "radius", "hidden_links", "add_change_note"]
    assert steps[2]["why"] == "they differ" and steps[2]["summary"].startswith("51 hidden links")
    assert steps[3]["action"] and ev[-2]["valid"] and ev[-2]["verdict"]["risk"] == "high"
    notes = A.Actions(g).taken("CHG-HEADLINE")
    assert any(n["kind"] == "change_note" and n["note"] == "51 hospitals" for n in notes)
    inv = mem.investigation(ev[-1]["id"])
    assert inv["verdict"]["risk"] == "high" and [s["tool"] for s in inv["steps"]][:2] == ["get_change", "radius"]
    assert mem.recall(["lrc-db-01"])["investigations"][0]["id"] == ev[-1]["id"]


def test_step_cap_and_invalid_verdict(env):
    _, g, mem = env
    llm = L.FakeLLM([{"tool": "radius", "input": {"component": "lrc-db-01", "layer": "real", "why": "x"}}] * 3,
                    final=json.dumps({**VERDICT, "reasons": ["It reaches 9999 hospitals."]}))
    ev = run(g, mem, "change", "CHG-HEADLINE", llm, max_steps=4)
    assert len(llm.calls) == 4 and llm.calls[-1]["tools"] is None  # 3 tool calls, last call without tools
    v = ev[-2]
    assert not v["valid"] and "9999" in v["rejected"] and v["verdict"]["risk"] == "high"  # rule-based instead
    assert len([e for e in ev if e["type"] == "step"]) == 3


def test_each_action(env):
    _, g, _ = env
    a = A.Actions(g, "INV-test")
    assert a.call("add_change_note", {"change_id": "CHG0031513", "note": "n"})["recorded"] == "change_note"
    assert a.call("request_cab_review", {"change_id": "CHG0031513", "reason": "r"})["state"] == "CAB review requested"
    assert a.call("notify_owner", {"team": "NW-DBA", "component": "emr-db-01", "reason": "r"})["team"] == "NW-DBA"
    assert a.call("open_ticket", {"component": "emr-db-01", "short_description": "s", "priority": "3 - Moderate",
                                  "assignment_group": "NW-DBA"})["recorded"] == "ticket"
    fix = a.call("propose_cmdb_fix", {"components": ["lrc-db-01"], "reason": "hidden links"})
    assert fix["proposed"] and all(i.startswith("F-") for i in fix["proposed"])
    assert a.call("add_change_note", {"change_id": "NOPE", "note": "n"})["error"]
    kinds = {x["kind"] for x in a.taken() if x.get("investigation") == "INV-test"}
    assert kinds == {"change_note", "cab_review", "notification", "ticket"}


def test_memory_isolation_and_recall(env):
    client, _, _ = env
    alice, bob = M.memory("alice"), M.memory("bob")
    for m in (alice, bob):
        m.reset()
    alice.fact(["emr-db-01"], "emr-db-01 is patched on Sundays.", "confirmed", "test")
    assert alice.recall(["emr-db-01"])["confirmed"][0]["statement"] == "emr-db-01 is patched on Sundays."
    assert bob.recall(["emr-db-01"])["confirmed"] == [] and alice.name == f"{GRAPH}_memory_alice"
    assert client.get("/agent/memory", params={"components": "emr-db-01", "user": "alice"}).json()["confirmed"]
    alice.reset()


def test_learning_loop_suppression_and_recall_after_reset(env):
    client, g, mem = env
    fs = client.get("/findings?limit=500").json()
    head = next(f for f in fs if f["type"] == "D1" and "lrc-db-01" in f["subjects"])
    stale = next(f for f in fs if f["type"] == "D2")
    assert client.post(f"/findings/{head['id']}/approve").status_code == 200
    assert client.post(f"/findings/{stale['id']}/reject", json={"approver": "t", "reason": "known"}).status_code == 200
    assert client.post("/reset", params={"scope": "data"}).json()["scope"] == "data"
    after = {f["id"]: f for f in client.get("/findings?limit=500").json()}
    assert after[stale["id"]]["status"] == "rejected" and after[head["id"]]["status"] == "open"  # not re-raised
    M.app.state.agent_llm = script()
    try:
        lines = [json.loads(x) for x in client.post("/agent/investigate", json={"kind": "change",
                                                                                "trigger": "CHG-HEADLINE"}).text.splitlines()]
    finally:
        M.app.state.agent_llm = None
    memo = next(e for e in lines if e["type"] == "memory")
    assert any("A person confirmed 51 hidden links to lrc-db-01" in f["statement"] for f in memo["confirmed"])
    assert [e["type"] for e in lines][-2:] == ["verdict", "done"]
    assert client.post("/reset", params={"scope": "memory"}).json()["scope"] == "memory"
    assert mem.recall(["lrc-db-01"])["confirmed"] == []
    client.post("/reset")


def test_cost_caps(env, monkeypatch):
    client, g, _ = env
    g.query("MATCH (s:AgentSpend) DELETE s")
    monkeypatch.setenv("AGENT_DAILY_USD", "0.01")
    monkeypatch.setenv("AGENT_VISITOR_RUNS", "2")
    assert spend.start(g, "198.51.100.1") == (True, None) and spend.start(g, "198.51.100.1") == (True, None)
    assert spend.start(g, "198.51.100.1") == (False, "visitor") and spend.start(g, "198.51.100.2")[0]
    assert spend.charge(g, 0.004) and not spend.charge(g, 0.007)  # 0.011 >= cap
    assert spend.start(g, "198.51.100.3") == (False, "cap") and abs(spend.today(g) - 0.011) < 1e-9
    priced = L.FakeLLM([L.Reply(text="", tool_calls=[{"id": "c1", "name": "radius", "input": {
        "component": "lrc-db-01", "layer": "real", "why": "x"}}], content=[{"type": "tool_use", "id": "c1",
        "name": "radius", "input": {"component": "lrc-db-01", "layer": "real", "why": "x"}}], cost=0.5)])
    ev = run(g, M.memory(), "change", "CHG-HEADLINE", priced, charge=lambda u: spend.charge(g, u))
    assert ev[-2]["stopped"] == "today's agent spending cap was reached" and not ev[-2]["valid"]
    M.app.state.agent_llm = script()
    try:
        r = client.post("/agent/investigate", json={"kind": "change", "trigger": "CHG-HEADLINE"})
    finally:
        M.app.state.agent_llm = None
    first = json.loads(r.text.splitlines()[0])
    assert first["type"] == "notice" and "budget" in first["text"]
    assert client.get("/api/state").json()["agent"]["cap"] == 0.01
    g.query("MATCH (s:AgentSpend) DELETE s")


class FakeHTTP:
    def __init__(self, statuses):
        self.statuses, self.keys = list(statuses), []

    def post(self, url, json=None, headers=None):
        self.keys.append(headers["Authorization"])
        code = self.statuses.pop(0)
        body = {"model": "gpt-6-luna", "output": [{"type": "message", "content": [{"type": "output_text",
                                                                                   "text": "ok"}]}],
                "usage": {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 200}, "output_tokens": 100}}

        class R:
            status_code = code
            text = "Incorrect API key provided: sk-...abcd"

            def json(self):
                return body
        return R()


def test_openai_key_fallback_never_leaks(caplog):
    caplog.set_level(logging.WARNING)
    http = FakeHTTP([401, 429, 200])
    llm = L.OpenAILLM(keys="sk-first-secret,sk-second-secret,sk-third-secret", client=http)
    r = llm.respond("s", [{"role": "user", "content": "q"}])
    assert r.text == "ok" and llm.events == ["switched to backup key 1", "switched to backup key 2"]
    assert abs(r.cost - (800 * 0.10 + 200 * 0.01 + 100 * 0.50) / 1e6) < 1e-12
    assert "secret" not in caplog.text and http.keys[-1] == "Bearer sk-third-secret"
    with pytest.raises(L.LLMUnavailable) as e:
        L.OpenAILLM(keys="sk-a-secret", client=FakeHTTP([402])).respond("s", [{"role": "user", "content": "q"}])
    assert "secret" not in str(e.value)


def test_scenarios_match_the_drift_data(env, drift_data):
    """Each scenario's cause and owner are what the graph and ground_truth.json say (no new drift)."""
    import os
    from graph import queries as Q
    from ingest.scenarios import SCENARIOS
    _, g, _ = env
    gt = {e["id"]: e for e in json.load(open(os.path.join(drift_data, "ground_truth.json")))}
    assert json.load(open(os.path.join(drift_data, "scenarios.json"))) == SCENARIOS
    assert len(SCENARIOS) == 5 and sum(s["kind"] == "incident" for s in SCENARIOS) == 3
    owner_of = {"S1-headline": "lrc-db-01", "S2-backup": "h02-pacs-cache-db", "S3-owner": "ris-db-01",
                "S4-fhir-app": "emr-fhir-api", "S5-stale": "sched-db-01"}
    for s in SCENARIOS:
        assert all(d in gt for d in s["drift"])
        cis = {c for d in s["drift"] for c in gt[d]["ci_ids"]} | {k for d in s["drift"] for key in gt[d].get("finding_keys", []) for k in key}
        assert set(s["root_cause_any"]) & (cis | {"cardiorisk.example.com"}), s["id"]
        o = Q.who_to_page(g, [owner_of[s["id"]]])[0]
        assert s["warn"][0] in (o["declared_owner"], o["observed_owner"]), s["id"]
    assert Q.change_review(g, "CHG0031249")["risk"] == "low" and Q.change_review(g, "CHG-HEADLINE")["risk"] == "high"
