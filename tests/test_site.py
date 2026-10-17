"""SITE_SPEC: overview and tour, glossary, public chat (cached chips, rules, small talk, live allowance),
Server-Timing, edge-cache headers and the data version (against the local FalkorDB)."""

import json
import os
import re
import shutil
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

import api.main as M
from agent import answers as A, flows, llm as L
from tests.conftest import _load

UI = os.path.join(os.path.dirname(__file__), "..", "ui")


def _read(name):
    with open(os.path.join(UI, name), encoding="utf-8") as f:
        return f.read()


def test_overview_and_tour_markup():
    html, js = _read("index.html"), _read("app.js")
    assert 'id="tab-btn-overview"' in html and html.index("tab-btn-overview") < html.index("tab-btn-change")
    text = re.sub(r"<[^>]+>", "", html)
    assert "Watch the agent review a change" in text and "Graph Hacks 2026 (WeMakeDevs × FalkorDB)" in text
    assert "Your CMDB says this patch is safe. Your hospital network disagrees." in text
    assert 'id="tour-start"' in html and 'role="dialog"' in html and 'id="tour-next"' in html
    assert len(re.findall(r'class="flow4"|<li><h3>The (record|evidence|gap|agent)</h3>', html)) == 5
    assert "uses lrc-db-01, not in CMDB," in html and "all 51 hospitals" in html
    assert js.count("{ title: ") == 7 and 'TABS = ["overview"' in js  # seven tour steps (two agent steps), overview first
    for n in ("38 ms", "357 ms", "1,725", "7,895", "714 ms, against 944 ms"):  # measured numbers (bench/RESULTS.md)
        assert n in html


def test_glossary_terms_accessible():
    g = json.loads(_read("glossary.json"))
    html, js = _read("index.html"), _read("app.js")
    want = {"cmdb", "ci", "drift", "blast-radius", "declared-observed", "hl7", "interface", "service-offering",
            "business-service", "discovery-source", "change-request", "agent", "mcp", "memory-graph", "fhir"}
    assert set(g["terms"]) == want and all(len(v) == 2 and v[1] for v in g["terms"].values())
    used = set(re.findall(r'data-term="([^"]+)"', html))
    assert used and used <= want
    # every term is a real button (keyboard focusable) and gets aria-describedby to its definition
    assert all(m.startswith('<button type="button" class="term"') for m in re.findall(r'<[^<>]*data-term="[^"]+"', html))
    assert 'setAttribute("aria-describedby", `def-${b.dataset.term}`)' in js
    assert set(g["drift_types"]) >= {f"D{i}" for i in (1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14)}
    assert len(g["sources"]) == 11


def test_public_start_skips_langgraph():
    code = "import api.main, sys; print('langgraph' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         env={**os.environ, "DEPLOY_MODE": "public"}, cwd=os.path.join(UI, ".."))
    assert out.stdout.strip() == "False", out.stderr


def test_quota_reserve():
    class S:
        def __init__(self, **k):
            self.s = k

        def status(self):
            return self.s
    assert not A.quota_left(S(provider="openrouter", source="openrouter /key", remaining=A.RESERVE))
    assert A.quota_left(S(provider="openrouter", source="openrouter /key", remaining=A.RESERVE + 1))
    assert not A.quota_left(S(provider="openrouter", source="local", remaining=50))  # unknown: keep the owner's
    assert A.quota_left(L.FakeLLM())


class Recorder:
    """Prewarm stand-in: answers with a FakeLLM and writes each reply into a CachedLLM bundle."""

    def __init__(self, fake, cached):
        self.fake, self.cached = fake, cached

    def respond(self, system, messages, tools=None, tool_choice=None):
        r = self.fake.respond(system, messages, tools, tool_choice)
        msgs, specs = L.to_openai(system, messages, tools)
        c = self.cached
        c.cache.put(c.cache.key(c.models, system, msgs, specs + ([tool_choice] if tool_choice else [])), r)
        return r


@pytest.fixture(scope="module")
def site(drift_data, tmp_path_factory):
    data = str(tmp_path_factory.mktemp("site") / "data")
    shutil.copytree(drift_data, data)
    ld = _load(data, "northwind_test_site")
    cache = str(tmp_path_factory.mktemp("site_cache"))
    with open(os.path.join(cache, "models.json"), "w") as f:
        json.dump(["m/x:free"], f)
    cached = L.CachedLLM(cache)
    chip = A.CHIPS[0]
    flows.chat(ld.g, chip, Recorder(L.FakeLLM([{"tool": "radius", "input": {"component": "dc1-host-07",
                                                                             "layer": "real"}},
                                                {"text": "Patching dc1-host-07 reaches 51 hospitals."}]), cached))
    saved = (M.GRAPH, M.DATA, M.RUNTIME, M.PUBLIC, M._db)
    M.GRAPH, M.DATA, M.PUBLIC, M._db = "northwind_test_site", data, True, None
    M.RUNTIME = str(tmp_path_factory.mktemp("site_rt"))
    M.app.state.llm = L.CachedLLM(cache)
    yield TestClient(M.app), ld.g
    M.GRAPH, M.DATA, M.RUNTIME, M.PUBLIC, M._db = saved
    M.app.state.llm = None
    M.app.state.live_llm = None
    ld.g.delete()


def _ask(client, q, ip="203.0.113.7"):
    return client.post("/chat", json={"message": q}, headers={"x-real-ip": ip}).json()


@pytest.mark.falkordb
def test_chip_cached_rules_and_small_talk(site):
    client, _ = site
    st = client.get("/api/state").json()
    assert st["chips"] == A.CHIPS and len(st["chips"]) == 6 and st["mode"] == "public"
    r = _ask(client, A.CHIPS[0])
    assert r["answered_by"] == "cached_ai" and r["answer"] == "Patching dc1-host-07 reaches 51 hospitals."
    assert r["steps"][0]["tool"] == "radius" and r["notice"] is None  # grounding check passed
    r = _ask(client, "What breaks if lrc-db-01 fails?")
    assert r["answered_by"] == "graph" and "51 hospitals" in r["answer"] and r["cis"][0] == "lrc-db-01"
    r = _ask(client, "Who owns lrc-db-01?")
    assert r["answered_by"] == "graph" and "lrc-db-01" in r["answer"] and "CMDB owner" in r["answer"]
    r = _ask(client, "What depends on dc1-host-07?")
    assert r["answered_by"] == "graph" and "depend on **dc1-host-07**" in r["answer"]
    r = _ask(client, "Is CHG-HEADLINE risky?")
    assert r["answered_by"] == "graph" and "**high risk**" in r["answer"]
    client.post("/scan")
    r = _ask(client, "Show the D5 findings")
    assert r["answered_by"] == "graph" and "owner drift findings" in r["answer"]
    for q in ("hi", "Thanks!", "banana bread recipe please"):
        r = _ask(client, q)
        assert r["answered_by"] == "welcome" and len(r["examples"]) == 3 and not r["steps"]


@pytest.mark.falkordb
def test_live_allowance(site, monkeypatch):
    client, g = site
    q = "Which databases have the most errors?"  # domain question: not cached, no rule fits
    M.app.state.live_llm = None
    monkeypatch.setattr(M, "_live", [None])  # no key configured
    assert _ask(client, q)["answered_by"] == "limit"
    g.query("MATCH (a:Allowance) DELETE a")
    monkeypatch.setattr(A, "GLOBAL_MAX", 3)
    monkeypatch.setattr(A, "VISITOR_MAX", 2)
    live = L.FakeLLM(final="Live answer from the tools.")
    M.app.state.live_llm = live
    try:
        assert _ask(client, q, "10.0.0.1")["answered_by"] == "live_ai"
        assert _ask(client, q, "10.0.0.1")["answered_by"] == "live_ai"
        r = _ask(client, q, "10.0.0.1")  # per-visitor limit
        assert r["answered_by"] == "limit" and r["answer"].startswith("You have used your live AI answers")
        assert _ask(client, q, "10.0.0.2")["answered_by"] == "live_ai"
        r = _ask(client, q, "10.0.0.3")  # site-wide limit
        assert r["answered_by"] == "limit" and r["answer"].startswith("Today's live AI answers")
        assert 3 <= len(live.calls) <= 3 * A.LIVE_STEPS  # three live answers, each capped at LIVE_STEPS calls
        keys = [k for (k,) in g.query("MATCH (a:Allowance) RETURN a.key").result_set]
        assert len(keys) == 4 and not any("10.0.0" in k for k in keys)  # hashed, never the raw IP
        used = dict(g.query("MATCH (a:Allowance) RETURN a.key, a.used").result_set)
        assert sorted(used.values()) == [0, 1, 2, 3]  # site 3; visitors 2, 1 and 0 (refused)
    finally:
        M.app.state.live_llm = None


@pytest.mark.falkordb
def test_timing_cache_headers_and_version(site):
    client, _ = site
    v1 = client.get("/api/state").json()["version"]
    r = client.get(f"/changes?v={v1}")
    assert r.status_code == 200 and "s-maxage=" in r.headers["cache-control"]
    assert "stale-while-revalidate=" in r.headers["cache-control"]
    st = r.headers["server-timing"]
    assert re.search(r"db;dur=[\d.]+, compute;dur=[\d.]+, total;dur=[\d.]+", st)
    r2 = client.get(f"/changes/CHG-HEADLINE/review?v={v1}")
    assert r2.headers["cache-control"].startswith("public") and r2.json()["facts"]["risk"] == "high"
    assert "stored;desc=hit" in client.get(f"/changes/CHG-HEADLINE/review?v={v1}").headers["server-timing"]
    assert client.get("/changes?v=stale").headers["cache-control"] == "no-store"  # never cache under a wrong key
    assert client.get("/api/state").headers["cache-control"] == "no-store"
    assert "s-maxage=" in client.get("/sources").headers["cache-control"]
    assert client.post("/changes/CHG-HEADLINE/review").headers["cache-control"] == "no-store"
    assert "server-timing" in client.get("/api/health").headers
    client.post("/scan")
    fs = client.get("/findings?limit=500&view=list").json()
    head = next(f for f in fs if f["type"] == "D1" and "lrc-db-01" in f["subjects"])
    assert set(head) <= set(M.LIST_FIELDS) and "patch" not in head  # list view: only what the table shows
    v2 = client.get("/api/state").json()["version"]
    assert client.post(f"/findings/{head['id']}/approve").status_code == 200
    v3 = client.get("/api/state").json()["version"]
    assert len({v1, v2, v3}) == 3
    assert client.get(f"/changes/CHG-HEADLINE/review?v={v3}").json()["facts"]["risk"] == "low"  # recomputed
    assert client.post("/reset").json()["status"] == "reset"
    v4 = client.get("/api/state").json()["version"]
    assert v4 != v3 and client.get(f"/changes/CHG-HEADLINE/review?v={v4}").json()["facts"]["risk"] == "high"


@pytest.mark.falkordb
def test_critical_nodes_reads_stored_result(site):
    """FalkorDB Cloud never runs algo.betweenness: a stored result (deploy.critical) answers instead."""
    from deploy import critical
    from graph import queries as Q
    _, g = site
    live = Q.critical_nodes(g)
    critical.store(g, [{"name": "stored-x", "label": "Application", "score": 1.0}])
    try:
        assert Q.critical_nodes(g) == [{"name": "stored-x", "label": "Application", "score": 1.0}]
    finally:
        g.query("MATCH (m:Meta {key: 'critical'}) DELETE m")
    assert Q.critical_nodes(g) == live
