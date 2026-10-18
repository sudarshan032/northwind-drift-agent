"""Public deployment mode (Vercel + FalkorDB Cloud), against the local FalkorDB:
cached AI only, no file writes, approvals and their undo log in the graph, demo reset."""

import hashlib
import json
import os
import shutil

import pytest
from fastapi.testclient import TestClient

import api.main as M
from agent import llm as L
from deploy import recover
from tests.conftest import _load

pytestmark = pytest.mark.falkordb


def _hash_tree(d):
    h = hashlib.sha256()
    for root, _, files in sorted(os.walk(d)):
        for f in sorted(files):
            with open(os.path.join(root, f), "rb") as fh:
                h.update(f.encode() + fh.read())
    return h.hexdigest()


@pytest.fixture(scope="module")
def public(drift_data, tmp_path_factory):
    data = str(tmp_path_factory.mktemp("pub") / "data")
    shutil.copytree(drift_data, data)
    ld = _load(data, "northwind_test_public")
    recover.mark_loaded(ld.g, data)  # as deploy.load_cloud does
    runtime = str(tmp_path_factory.mktemp("pub_rt"))
    cache = str(tmp_path_factory.mktemp("pub_cache"))
    with open(os.path.join(cache, "models.json"), "w") as f:
        json.dump(["m/x:free"], f)
    saved = (M.GRAPH, M.DATA, M.RUNTIME, M.PUBLIC, M._db)
    M.GRAPH, M.DATA, M.RUNTIME, M.PUBLIC, M._db = "northwind_test_public", data, runtime, True, None
    M._risk.clear()
    M.app.state.llm = L.CachedLLM(cache)
    os.environ["CRON_SECRET"] = "test-secret"
    yield TestClient(M.app), data, runtime, cache
    os.environ.pop("CRON_SECRET", None)
    M.GRAPH, M.DATA, M.RUNTIME, M.PUBLIC, M._db = saved
    M.app.state.llm = None
    M._risk.clear()
    ld.g.delete()


def test_cached_llm_only(tmp_path):
    with open(tmp_path / "models.json", "w") as f:
        json.dump(["m/x:free"], f)
    c = L.CachedLLM(str(tmp_path))
    msgs, specs = L.to_openai("sys", [{"role": "user", "content": "q"}], None)
    c.cache.put(c.cache.key(["m/x:free"], "sys", msgs, specs), L.Reply(text="from the bundle", model="m/x:free"))
    assert c.respond("sys", [{"role": "user", "content": "q"}]).text == "from the bundle"
    with pytest.raises(L.LLMUnavailable) as e:
        c.respond("sys", [{"role": "user", "content": "not cached"}])
    assert e.value.notice == L.NOT_CACHED
    assert c.status()["provider"] == "cached" and c.status()["cached_answers"] == 1


def test_public_provider_is_cached(monkeypatch):
    monkeypatch.setenv("DEPLOY_MODE", "public")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-ignored")
    L.get_llm.cache_clear()
    assert L.provider() == "cached" and isinstance(L.get_llm(), L.CachedLLM)


def test_public_health_review_and_cached_notice(public):
    client, _, _, _ = public
    h = client.get("/api/health").json()
    assert h["db"] == "connected" and h["mode"] == "public" and h["components"] > 20000
    assert client.get("/llm").json()["provider"] == "cached"
    r = client.post("/changes/CHG-HEADLINE/review").json()
    assert r["facts"]["risk"] == "high" and r["notice"] == L.NOT_CACHED  # nothing cached: rule-based, labelled
    assert "HIGH" in r["explanation"]


def test_public_approve_reset_no_files(public):
    client, data, runtime, cache = public
    before_data, before_cache = _hash_tree(data), _hash_tree(cache)
    client.post("/scan")
    fs = client.get("/findings?limit=500").json()
    d1, ticket = fs[0], next(f for f in fs if f["action"] == "ticket")
    owner = next(f for f in fs if f["type"] == "D5")
    shadow = next(f for f in fs if f["type"] == "D6")
    app = next(f for f in fs if f["type"] == "D14")  # registers an unregistered endpoint as a CI
    assert client.post(f"/findings/{d1['id']}/approve").json()["resolved"] is True
    for f in (owner, shadow, app, ticket):
        assert client.post(f"/findings/{f['id']}/approve").status_code == 200
    assert client.post("/changes/CHG-HEADLINE/review").json()["facts"]["risk"] == "low"
    g = M.graph()
    kinds = dict(g.query("MATCH (r:Record) RETURN r.kind, count(r)").result_set)
    assert kinds["undo"] == 4 and kinds["ticket"] >= 5 and kinds["audit"] == 5  # in the graph, not in files
    assert os.listdir(runtime) == [] and _hash_tree(data) == before_data and _hash_tree(cache) == before_cache
    assert client.get("/cron/reset").status_code == 401  # cron needs the secret
    r = client.get("/cron/reset", headers={"Authorization": "Bearer test-secret"}).json()
    assert r["status"] == "reset" and r["approvals_undone"] == 4 and r["findings"] == len(fs)
    assert client.post("/changes/CHG-HEADLINE/review").json()["facts"]["risk"] == "high"
    assert g.query("MATCH (r:Record) RETURN count(r)").result_set[0][0] == 0
    owner_now = g.query("MATCH (c:Component {name: $n}) RETURN c.support_group", {"n": owner["subjects"][0]}).result_set
    assert owner_now[0][0] == owner["evidence"]["rows"][0]["cmdb_owner"]  # attribute restored
    name = shadow["patch"][0]["value"]["name"]
    assert not g.query("MATCH (c:Component {name: $n}) RETURN c", {"n": name}).result_set  # registered CI removed
    assert g.query("MATCH (x:UnregisteredEndpoint {name: 'cardiorisk.example.com'}) RETURN count(x)").result_set[0][0] == 1
    assert {f["status"] for f in client.get("/findings?limit=500").json()} == {"open"}
    assert os.listdir(runtime) == [] and _hash_tree(data) == before_data


def test_automatic_reset_hourly_and_cold_start(public):
    client, _, _, _ = public
    g = M.graph()
    client.post("/scan")
    f = next(f for f in client.get("/findings?limit=500").json() if f["type"] == "D5")  # a CMDB patch
    assert client.post(f"/findings/{f['id']}/approve").status_code == 200
    assert client.get("/cron/tick").json()["reset"] is False  # dirty, but not yet an hour old
    assert M.auto_reset(g, cold=True) is None  # cold start, but someone wrote in the last 10 minutes
    t = M.time.time()
    g.query("MATCH (m:Meta {key: 'dirty'}) SET m.since = $s, m.last = $l", {"s": t - 3700, "l": t - 700})
    r = client.get("/cron/tick").json()
    assert r["status"] == "ok" and r["reset"] is True and r["approvals_undone"] == 1
    assert g.query("MATCH (r:Record) RETURN count(r)").result_set[0][0] == 0
    assert client.get("/cron/tick").json()["reset"] is False  # clean: nothing to do
    assert client.post(f"/findings/{f['id']}/approve").status_code == 200
    g.query("MATCH (m:Meta {key: 'dirty'}) SET m.since = $t, m.last = $t", {"t": M.time.time() - 700})
    assert M.auto_reset(g) is None  # dirty for 12 minutes: the hourly check waits
    assert M.auto_reset(g, cold=True)["approvals_undone"] == 1  # a cold start restores it
    assert not g.query("MATCH (m:Meta {key: 'dirty'}) RETURN m").result_set


def test_reload_after_database_restart(public):
    """FalkorDB Cloud free tier: no persistence. An empty graph is reloaded from the bundled data, in steps,
    behind a lock; meanwhile the API answers 503 "restarting" instead of errors."""
    client, data, _, _ = public
    client.post("/scan")
    before = len(client.get("/findings?limit=500").json())
    g = M.graph()
    n = g.query("MATCH (c:Component) RETURN count(c)").result_set[0][0]
    M._db.select_graph(M.GRAPH).delete()  # what a restart of the free instance leaves
    M._ok_until[0] = 0.0
    assert recover.state(M._db, M.GRAPH, data) == "missing"
    first = M.restore(0)  # a tiny budget: one step per call, the rest at the next call
    assert first["status"] == "restoring" and first["step"] == 1
    assert recover.state(M._db, M.GRAPH, data) == "restoring"
    r = client.get("/findings")
    assert r.status_code == 503 and r.json() == {"restarting": True, "detail": M.RESTARTING}
    held = recover._claim(M._db, M.GRAPH, "a")  # one instance holds the lease ...
    assert held is not None and recover._claim(M._db, M.GRAPH, "b") is None  # ... a second one waits
    M._db.select_graph(recover.lock_name(M.GRAPH)).query("MATCH (r:Restore) SET r.until = 0.0")  # lease ran out
    t = client.get("/cron/tick").json()
    assert t["status"] == "ok" and t["reloaded"] is True and t["components"] == n and t["memory_graph"] is True
    assert recover.lock_name(M.GRAPH) not in M._db.list_graphs()
    assert len(client.get("/findings?limit=500").json()) == before
    assert client.post("/changes/CHG-HEADLINE/review").json()["facts"]["risk"] == "high"
    assert client.get("/cron/tick").json()["reloaded"] is False  # healthy: nothing to do
