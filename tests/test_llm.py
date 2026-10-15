"""LLM provider switch, OpenRouter model selection, budget guard, cache and
429 handling. No network: httpx.MockTransport stands in for OpenRouter."""

import json

import httpx
import pytest

from agent import llm as L

FREE = {"prompt": "0", "completion": "0"}
CATALOG = [
    {"id": "a/big:free", "context_length": 1_000_000, "pricing": FREE, "supported_parameters": ["tools"]},
    {"id": "b/mid:free", "context_length": 262_144, "pricing": FREE, "supported_parameters": ["tools", "tool_choice"]},
    {"id": "c/notools:free", "context_length": 2_000_000, "pricing": FREE, "supported_parameters": ["temperature"]},
    {"id": "d/paid", "context_length": 3_000_000, "pricing": {"prompt": "0.000001", "completion": "0.000002"},
     "supported_parameters": ["tools"]},
    {"id": "e/priced:free", "context_length": 9_000_000, "pricing": {"prompt": "0.1", "completion": "0"},
     "supported_parameters": ["tools"]},
    {"id": "f/small:free", "context_length": 65_536, "pricing": FREE, "supported_parameters": ["tools"]},
]


def completion(text="ok", model="b/mid:free", tool=None):
    msg = {"role": "assistant", "content": text}
    if tool:
        msg = {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": tool, "arguments": '{"text": "lrc"}'}}]}
    return {"id": "gen-1", "model": model, "choices": [{"index": 0, "message": msg, "finish_reason": "stop"}]}


class Server:
    """Scripted OpenRouter: `chat` is a list of (status, body, headers)."""

    def __init__(self, chat, key=None):
        self.chat, self.key, self.posts = list(chat), key, []

    def __call__(self, request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": CATALOG})
        if request.url.path.endswith("/key"):
            return httpx.Response(200, json={"data": {"free_model_daily_requests": self.key}})
        self.posts.append(json.loads(request.content))
        status, body, headers = self.chat.pop(0)
        return httpx.Response(status, json=body, headers=headers)


def make(tmp_path, server, models="x/gone:free,b/mid:free,a/big:free", daily=50):
    m = L.OpenRouterLLM(key="sk-test", models=models, runtime=str(tmp_path),
                        client=httpx.Client(transport=httpx.MockTransport(server)), sleep=lambda s: None)
    m.budget.daily = daily
    return m


def test_provider_switch(monkeypatch, tmp_path):
    assert L.provider() == "none" and L.get_llm() is None
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    L.get_llm.cache_clear()
    assert isinstance(L.get_llm(), L.FakeLLM)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    L.get_llm.cache_clear()
    assert L.get_llm() is None  # selected but no key: degrade, never crash
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    L.get_llm.cache_clear()
    assert isinstance(L.get_llm(), L.ClaudeLLM)
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(L, "OpenRouterLLM", lambda: "openrouter-instance")
    L.get_llm.cache_clear()
    assert L.get_llm() == "openrouter-instance"
    monkeypatch.delenv("LLM_PROVIDER")  # unset: OpenRouter key wins over Anthropic key
    assert L.provider() == "openrouter"


def test_model_selection():
    chosen, available = L.select_models(["x/gone:free", "b/mid:free", "a/big:free", "d/paid"], CATALOG)
    assert chosen == ["b/mid:free", "a/big:free"]
    assert available == ["a/big:free", "b/mid:free", "f/small:free"]  # free, tools, by context
    chosen, _ = L.select_models(["x/gone:free", "c/notools:free"], CATALOG)
    assert chosen[0] == "a/big:free"  # none qualify: largest-context free tool model
    many = [{**CATALOG[0], "id": f"m{i}:free"} for i in range(6)]
    assert len(L.select_models([m["id"] for m in many], many)[0]) == L.MAX_MODELS == 3  # OpenRouter's cap


def test_fallback_models_and_message_conversion(tmp_path):
    srv = Server([(200, completion(tool="search_components"), {}), (200, completion("final"), {})])
    m = make(tmp_path, srv)
    assert m.models == ["b/mid:free", "a/big:free"]
    tools = [{"name": "search_components", "description": "find", "input_schema": {"type": "object"}}]
    r1 = m.respond("sys", [{"role": "user", "content": "q"}], tools)
    assert r1.tool_calls == [{"id": "call_1", "name": "search_components", "input": {"text": "lrc"}}]
    msgs = [{"role": "user", "content": "q"}, {"role": "assistant", "content": r1.content},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "[]"}]}]
    assert m.respond("sys", msgs, tools).text == "final"
    p = srv.posts[1]
    assert p["model"] == "b/mid:free" and p["models"] == ["b/mid:free", "a/big:free"]
    assert p["tools"][0]["function"]["name"] == "search_components"
    assert [x["role"] for x in p["messages"]] == ["system", "user", "assistant", "tool"]
    assert p["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"text": "lrc"}'


def test_cache_hit_costs_zero_requests(tmp_path):
    srv = Server([(200, completion("cached answer"), {})])
    m = make(tmp_path, srv)
    first = m.respond("sys", [{"role": "user", "content": "same prompt"}])
    left = m.budget.remaining()
    again = make(tmp_path, srv)  # new process, same runtime dir
    second = again.respond("sys", [{"role": "user", "content": "same prompt"}])
    assert second.text == first.text == "cached answer" and second.model == "b/mid:free"
    assert len(srv.posts) == 1 and again.budget.remaining() == left == 49


def test_budget_guard(tmp_path):
    clock = [1_800_000_000.0]
    b = L.Budget(str(tmp_path / "b.json"), daily=3, per_minute=2, now=lambda: clock[0])
    b.take(), b.take()
    with pytest.raises(L.LLMUnavailable, match="per-minute"):
        b.take()
    clock[0] += 61
    b.take()
    assert L.Budget(str(tmp_path / "b.json"), daily=3, now=lambda: clock[0]).remaining() == 0  # persisted
    with pytest.raises(L.LLMUnavailable, match="daily"):
        b.take()
    clock[0] += 86_400  # next UTC day resets
    assert b.remaining() == 3
    capped = L.Capped(L.FakeLLM(), 1)
    capped.respond("s", [])
    with pytest.raises(L.LLMUnavailable, match="cap"):
        capped.respond("s", [])
    assert L.JOB_CAPS == {"triage": 1, "change_review": 1, "incident": 6, "chat": 4}


def test_exhausted_budget_sends_nothing(tmp_path):
    srv = Server([])
    m = make(tmp_path, srv, daily=0)
    with pytest.raises(L.LLMUnavailable, match="daily"):
        m.respond("sys", [{"role": "user", "content": "q"}])
    assert srv.posts == []


def test_429_retries_once_then_falls_back(tmp_path):
    limited = (429, {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-min"}},
               {"retry-after": "3"})
    srv = Server([limited, limited, limited])
    slept = []
    m = make(tmp_path, srv)
    m.sleep = slept.append
    with pytest.raises(L.LLMUnavailable, match="429"):
        m.respond("sys", [{"role": "user", "content": "q"}])
    assert len(srv.posts) == 2 and slept == [3.0]  # one retry, never a loop
    assert m.budget.remaining() == 48  # failed requests count
    srv = Server([limited, (200, completion("after retry"), {})])
    m = make(tmp_path / "2", srv)
    assert m.respond("sys", [{"role": "user", "content": "q"}]).text == "after retry"


def test_status_reads_key_info(tmp_path):
    m = make(tmp_path, Server([], key={"used": 7, "limit": 50, "remaining": 43}))
    s = m.status()
    assert s["remaining"] == 43 and s["limit"] == 50 and s["source"] == "openrouter /key"
