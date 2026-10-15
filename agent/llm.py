"""LLM access. Provider switch LLM_PROVIDER=openrouter|anthropic|fake (unset:
openrouter if OPENROUTER_API_KEY, else anthropic if ANTHROPIC_API_KEY, else
no LLM and callers degrade to rule-based text).

- `OpenRouterLLM`: free models through OpenRouter's OpenAI-compatible chat
  completions API (raw httpx), with model selection from the models API, the
  `models` fallback list, a persistent daily request budget, one retry after
  a 429, and an on-disk response cache (RESEARCH_NOTES R19).
- `ClaudeLLM`: Claude through the official anthropic SDK.
- `FakeLLM`: scripted responses so every test runs without a key.

All return `Reply(text, tool_calls, content, stop_reason)`. Conversations are
kept in Anthropic block form (text / tool_use / tool_result); OpenRouterLLM
converts to and from OpenAI chat messages at the edge.
"""

import datetime
import functools
import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field

log = logging.getLogger("northwind.llm")

DEFAULT_MODEL = "claude-sonnet-5-5"
OPENROUTER = "https://openrouter.ai/api/v1"
DEFAULT_FREE_MODELS = ("nvidia/nemotron-3-super-120b-a12b:free,google/gemma-4-31b-it:free,nvidia/nemotron-3-ultra-550b-a55b:free,"
                       "google/gemma-4-26b-a4b-it:free,thinkingmachines/inkling:free")
DAILY_LIMIT = 50   # free models without purchased credits (R19)
MINUTE_LIMIT = 20
JOB_CAPS = {"triage": 1, "change_review": 1, "incident": 6, "chat": 4}
MAX_MODELS = 3     # OpenRouter rejects a longer `models` array (HTTP 400, observed; R19)
UNAVAILABLE = "AI explanation unavailable (free-tier limit), showing rule-based explanation"


class LLMUnavailable(Exception):
    """Budget exhausted, job cap reached, every model failed, or (public mode) no cached answer."""

    def __init__(self, msg="", notice=None):
        super().__init__(msg)
        self.notice = notice or UNAVAILABLE


NOT_CACHED = "No cached AI explanation for this request, showing rule-based explanation"


def notice_of(llm):
    """The fallback sentence this LLM's failures should show (public mode differs)."""
    return getattr(llm, "notice", UNAVAILABLE)


@dataclass
class Reply:
    text: str = ""
    tool_calls: list = field(default_factory=list)  # [{"id", "name", "input"}]
    content: list = field(default_factory=list)
    stop_reason: str = "end_turn"
    model: str = ""


# --------------------------------------------------------------------------
# Budget, cache, per-job cap
# --------------------------------------------------------------------------


class Budget:
    """Persistent request counter per UTC day plus a rolling one-minute window.
    `take()` is called before every HTTP attempt, because failed requests also
    count against the free tier."""

    def __init__(self, path, daily=DAILY_LIMIT, per_minute=MINUTE_LIMIT, now=time.time):
        self.path, self.daily, self.per_minute, self.now = path, daily, per_minute, now
        self.lock = threading.Lock()

    def _today(self):
        return datetime.datetime.fromtimestamp(self.now(), datetime.UTC).date().isoformat()

    def state(self):
        try:
            with open(self.path) as f:
                s = json.load(f)
        except (OSError, ValueError):
            s = {}
        if s.get("date") != self._today():
            s = {"date": self._today(), "used": 0, "recent": []}
        s["recent"] = [t for t in s.get("recent", []) if self.now() - t < 60]
        return s

    def remaining(self):
        return max(0, self.daily - self.state()["used"])

    def take(self):
        with self.lock:
            s = self.state()
            if s["used"] >= self.daily:
                raise LLMUnavailable("daily request budget exhausted")
            if len(s["recent"]) >= self.per_minute:
                raise LLMUnavailable("per-minute request limit reached")
            s["used"] += 1
            s["recent"].append(self.now())
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w") as f:
                json.dump(s, f)


class Cache:
    """One JSON file per response, keyed by model list + prompt hash."""

    def __init__(self, path):
        self.path = path

    def key(self, models, system, messages, tools):
        blob = json.dumps([models, system, messages, tools], sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()

    def get(self, k):
        try:
            with open(os.path.join(self.path, f"{k}.json")) as f:
                return Reply(**json.load(f))
        except (OSError, ValueError, TypeError):
            return None

    def put(self, k, reply):
        os.makedirs(self.path, exist_ok=True)
        with open(os.path.join(self.path, f"{k}.json"), "w") as f:
            json.dump(asdict(reply), f)


class Capped:
    """Per-job call cap: the (n+1)th call raises LLMUnavailable."""

    def __init__(self, llm, n):
        self.llm, self.n, self.used = llm, n, 0
        self.notice = notice_of(llm)

    def respond(self, system, messages, tools=None, tool_choice=None):
        if self.used >= self.n:
            raise LLMUnavailable("per-job request cap reached")
        self.used += 1
        return self.llm.respond(system, messages, tools, **({"tool_choice": tool_choice} if tool_choice else {}))


def capped(llm, job):
    return None if llm is None else Capped(llm, JOB_CAPS[job])


# --------------------------------------------------------------------------
# OpenRouter
# --------------------------------------------------------------------------


def select_models(preferences, catalog):
    """Free (:free id, zero prompt and completion price) tool-capable models.
    Returns (qualified preferences in order, or else the qualifying model with
    the largest context followed by the next by context; at most MAX_MODELS),
    and all qualifying models."""
    def ok(m):
        p = m.get("pricing") or {}
        try:
            free = float(p.get("prompt", 1)) == 0 and float(p.get("completion", 1)) == 0
        except (TypeError, ValueError):
            free = False
        return m["id"].endswith(":free") and free and "tools" in (m.get("supported_parameters") or [])
    good = sorted((m for m in catalog if ok(m)), key=lambda m: (-(m.get("context_length") or 0), m["id"]))
    ids = {m["id"] for m in good}
    chosen = [p for p in preferences if p in ids]
    return (chosen or [m["id"] for m in good])[:MAX_MODELS], [m["id"] for m in good]


def to_openai(system, messages, tools):
    out = [{"role": "system", "content": system}]
    for m in messages:
        c = m["content"]
        if isinstance(c, str):
            out.append({"role": m["role"], "content": c})
            continue
        blocks = [b if isinstance(b, dict) else b.model_dump() for b in c]
        if m["role"] == "user":
            for b in blocks:
                if b["type"] == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]})
                elif b["type"] == "text":
                    out.append({"role": "user", "content": b["text"]})
        else:
            text = "".join(b["text"] for b in blocks if b["type"] == "text")
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                     for b in blocks if b["type"] == "tool_use"]
            out.append({"role": "assistant", "content": text or None, **({"tool_calls": calls} if calls else {})})
    specs = [{"type": "function", "function": {"name": t["name"], "description": t["description"],
                                                "parameters": t["input_schema"]}} for t in tools or []]
    return out, specs


def from_openai(body):
    msg = body["choices"][0]["message"]
    text = msg.get("content") or ""
    calls = []
    for tc in msg.get("tool_calls") or []:
        try:
            args = json.loads(tc["function"].get("arguments") or "{}")
        except ValueError:
            args = {}
        calls.append({"id": tc["id"], "name": tc["function"]["name"], "input": args if isinstance(args, dict) else {}})
    content = ([{"type": "text", "text": text}] if text else []) + [
        {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]} for c in calls]
    return Reply(text=text, tool_calls=calls, content=content, stop_reason="tool_use" if calls else "end_turn",
                 model=body.get("model", ""))


class OpenRouterLLM:
    def __init__(self, key=None, models=None, runtime=None, client=None, sleep=time.sleep):
        import httpx
        self.key = key or os.environ["OPENROUTER_API_KEY"]
        self.http = client or httpx.Client(timeout=120)
        self.sleep = sleep
        runtime = runtime or os.environ.get("NORTHWIND_RUNTIME", "runtime")
        self.budget = Budget(os.path.join(runtime, "llm_budget.json"),
                             daily=int(os.environ.get("NORTHWIND_DAILY_REQUESTS", DAILY_LIMIT)))
        self.cache = Cache(os.path.join(runtime, "llm_cache"))
        prefs = [m.strip() for m in (models or os.environ.get("NORTHWIND_MODELS") or DEFAULT_FREE_MODELS).split(",")
                 if m.strip()]
        try:
            r = self.http.get(f"{OPENROUTER}/models")
            r.raise_for_status()
            self.models, available = select_models(prefs, r.json()["data"])
            log.warning("OpenRouter: %d free tool-capable models available: %s", len(available), ", ".join(available))
            skipped = [p for p in prefs if p not in available]
            if skipped:
                log.warning("OpenRouter: not free or no tool support, skipped: %s", ", ".join(skipped))
        except Exception as e:  # offline: trust the configured list
            log.warning("OpenRouter models API unavailable (%s); using NORTHWIND_MODELS as given", e)
            self.models = prefs[:MAX_MODELS]
        log.warning("OpenRouter: using %s (fallbacks: %s)", self.models[0] if self.models else None,
                    ", ".join(self.models[1:]) or "none")
        self._key_info = (0, None)

    def headers(self):
        return {"Authorization": f"Bearer {self.key}", "X-OpenRouter-Title": "Northwind drift agent"}

    def respond(self, system, messages, tools=None, tool_choice=None):
        if not self.models:
            raise LLMUnavailable("no free tool-capable model available")
        msgs, specs = to_openai(system, messages, tools)
        k = self.cache.key(self.models, system, msgs, specs + ([tool_choice] if tool_choice else []))
        hit = self.cache.get(k)
        if hit is not None:
            return hit
        payload = {"model": self.models[0], "models": self.models, "messages": msgs, "max_tokens": 8000}
        if specs:
            payload["tools"] = specs
            if tool_choice:
                payload["tool_choice"] = tool_choice  # "none" on a job's last allowed call
        for attempt in (1, 2):  # at most one retry, only after a 429
            self.budget.take()
            r = self.http.post(f"{OPENROUTER}/chat/completions", json=payload, headers=self.headers())
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            code = (body.get("error") or {}).get("code") or r.status_code
            if code == 429 and attempt == 1:
                try:
                    wait = float(r.headers.get("retry-after", 5))
                except ValueError:
                    wait = 5
                self.sleep(min(wait, 20))
                continue
            if r.status_code != 200 or "error" in body or not body.get("choices"):
                err = f"OpenRouter error {code}: {(body.get('error') or {}).get('message', '')[:300]}"
                log.warning(err)
                raise LLMUnavailable(err)
            reply = from_openai(body)
            self.cache.put(k, reply)
            return reply
        raise LLMUnavailable("rate limited")  # pragma: no cover

    def status(self):
        """Remaining free-model requests today: the key-info endpoint when it
        answers (cached 30 s), else the local counter."""
        t, info = self._key_info
        if time.time() - t > 30:
            try:
                r = self.http.get(f"{OPENROUTER}/key", headers=self.headers(), timeout=10)
                info = r.json()["data"].get("free_model_daily_requests") if r.status_code == 200 else None
            except Exception:
                info = None
            self._key_info = (time.time(), info)
        local = self.budget.remaining()
        remote = info.get("remaining") if info else None
        return {"provider": "openrouter", "models": self.models,
                "remaining": local if remote is None else min(local, remote),
                "limit": (info or {}).get("limit", self.budget.daily), "source": "openrouter /key" if info else "local"}


class CachedLLM:
    """Public mode: answers only from a read-only bundle of cached responses
    (`deploy/llm_cache/`, made by `python -m deploy.bundle_cache`). Never calls
    a model, never writes. A miss raises LLMUnavailable with NOT_CACHED."""

    notice = NOT_CACHED

    def __init__(self, path=None):
        self.path = path or os.environ.get("NORTHWIND_LLM_CACHE", os.path.join("deploy", "llm_cache"))
        self.cache = Cache(self.path)
        try:
            with open(os.path.join(self.path, "models.json")) as f:
                self.models = json.load(f)
        except (OSError, ValueError):
            self.models = []

    def respond(self, system, messages, tools=None, tool_choice=None):
        msgs, specs = to_openai(system, messages, tools)
        hit = self.cache.get(self.cache.key(self.models, system, msgs, specs + ([tool_choice] if tool_choice else [])))
        if hit is None:
            raise LLMUnavailable("not in the cached answers", notice=NOT_CACHED)
        return hit

    def status(self):
        n = len([f for f in os.listdir(self.path) if f.endswith(".json") and f != "models.json"]) \
            if os.path.isdir(self.path) else 0
        return {"provider": "cached", "models": self.models, "remaining": None, "cached_answers": n}


# --------------------------------------------------------------------------
# Claude and the fake
# --------------------------------------------------------------------------


class ClaudeLLM:
    """Claude via the Messages API with client-side tools (manual loop is in
    agent.flows). Server-side refusal fallback is enabled ("default" routing)."""

    def __init__(self, model=None):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("NORTHWIND_MODEL", DEFAULT_MODEL)

    def respond(self, system, messages, tools=None, tool_choice=None):
        import anthropic
        kwargs = dict(model=self.model, max_tokens=16000, system=system, messages=messages,
                      betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        if tools:
            kwargs["tools"] = tools
            if tool_choice:
                kwargs["tool_choice"] = {"type": tool_choice}
        try:
            resp = self.client.beta.messages.create(**kwargs)
        except anthropic.APIError as e:
            raise LLMUnavailable(str(e)[:200]) from e
        if resp.stop_reason == "refusal":
            return Reply(text="The model declined this request.", content=resp.content, stop_reason="refusal")
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [{"id": b.id, "name": b.name, "input": b.input} for b in resp.content if b.type == "tool_use"]
        return Reply(text=text, tool_calls=calls, content=resp.content, stop_reason=resp.stop_reason, model=self.model)

    def status(self):
        return {"provider": "anthropic", "models": [self.model], "remaining": None}


class FakeLLM:
    """Deterministic scripted LLM. `script` items are Reply objects, dicts
    ({"text"} or {"tool": name, "input": {...}}), callables
    (system, messages, tools) -> item, or exceptions to raise. When the script
    runs out it answers with a fixed final text."""

    def __init__(self, script=None, final="(scripted) done."):
        self.script = list(script or [])
        self.final = final
        self.calls = []

    def respond(self, system, messages, tools=None, tool_choice=None):
        self.calls.append({"system": system, "messages": json.loads(json.dumps(messages, default=str)),
                           "tools": [t["name"] for t in tools] if tools else None, "tool_choice": tool_choice})
        item = self.script.pop(0) if self.script else {"text": self.final}
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(system, messages, tools)
        if isinstance(item, Reply):
            return item
        if "tool" in item:
            tid = f"toolu_fake_{len(self.calls):03d}"
            block = {"type": "tool_use", "id": tid, "name": item["tool"], "input": item.get("input", {})}
            return Reply(text=item.get("text", ""), tool_calls=[{"id": tid, "name": item["tool"],
                                                                  "input": block["input"]}],
                         content=([{"type": "text", "text": item["text"]}] if item.get("text") else []) + [block],
                         stop_reason="tool_use")
        return Reply(text=item["text"], content=[{"type": "text", "text": item["text"]}])

    def status(self):
        return {"provider": "fake", "models": ["fake"], "remaining": None}


def provider():
    if os.environ.get("DEPLOY_MODE", "local") == "public":
        return "cached"  # public demo: cached answers only, whatever else is configured
    p = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if p:
        return p
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    return "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "none"


@functools.lru_cache(maxsize=1)
def get_llm():
    """The configured LLM, built once (model selection runs at startup), or
    None when no provider is configured (callers degrade)."""
    p = provider()
    if p == "openrouter" and os.environ.get("OPENROUTER_API_KEY"):
        return OpenRouterLLM()
    if p == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeLLM()
    if p == "fake":
        return FakeLLM(final="(fake provider) no live model configured.")
    if p == "cached":
        return CachedLLM()
    return None


NO_KEY = ("Set LLM_PROVIDER and OPENROUTER_API_KEY (free models) or ANTHROPIC_API_KEY (see .env.example) for the "
          "live agent; detection, radius and change review work without it.")
