"""LLM access. `ClaudeLLM` calls Claude through the official anthropic SDK;
`FakeLLM` replays scripted responses so every test runs without a key.

Both return `Reply(text, tool_calls, content, stop_reason)`, where `content`
is what to append to the conversation as the assistant turn.
"""

import json
import os
from dataclasses import dataclass, field

DEFAULT_MODEL = "claude-sonnet-5-5"


@dataclass
class Reply:
    text: str = ""
    tool_calls: list = field(default_factory=list)  # [{"id", "name", "input"}]
    content: list = field(default_factory=list)
    stop_reason: str = "end_turn"


class ClaudeLLM:
    """Claude via the Messages API with client-side tools (manual loop is in
    agent.flows). Server-side refusal fallback is enabled ("default" routing)."""

    def __init__(self, model=None):
        import anthropic
        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("NORTHWIND_MODEL", DEFAULT_MODEL)

    def respond(self, system, messages, tools=None):
        kwargs = dict(model=self.model, max_tokens=16000, system=system, messages=messages,
                      betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        if tools:
            kwargs["tools"] = tools
        resp = self.client.beta.messages.create(**kwargs)
        if resp.stop_reason == "refusal":
            return Reply(text="The model declined this request.", content=resp.content, stop_reason="refusal")
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [{"id": b.id, "name": b.name, "input": b.input} for b in resp.content if b.type == "tool_use"]
        return Reply(text=text, tool_calls=calls, content=resp.content, stop_reason=resp.stop_reason)


class FakeLLM:
    """Deterministic scripted LLM. `script` items are Reply objects, dicts
    ({"text"} or {"tool": name, "input": {...}}), or callables
    (system, messages, tools) -> item. When the script runs out it answers
    with a fixed final text."""

    def __init__(self, script=None, final="(scripted) done."):
        self.script = list(script or [])
        self.final = final
        self.calls = []

    def respond(self, system, messages, tools=None):
        self.calls.append({"system": system, "messages": json.loads(json.dumps(messages, default=str))})
        item = self.script.pop(0) if self.script else {"text": self.final}
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


def get_llm():
    """Real Claude when a key is configured, else None (callers degrade)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeLLM()
    return None


NO_KEY = "Add ANTHROPIC_API_KEY (see .env.example) for the live agent; detection, radius and change review work without it."
