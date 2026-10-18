"""The Change Guardian (AGENT_V2_SPEC 2): an agent that investigates a change or an incident, decides and acts.

The model chooses every step. It sees the read-only graph tools through MCP (agent.mcp_tools, listed and called
with an MCP client) plus the action tools (agent.actions), each with a required `why` argument so the trace can
show its reason. At most `max_steps` model calls; the last one has no tools and must return the structured
verdict {risk, root_cause, reasons, hidden_links, warn, actions, evidence}. The verdict is checked with the
grounding rules (every number and CI name must appear in a tool result; an incident's named change must target
the most specific shared dependency). A rejected verdict is replaced by a rule-based one and the rejection is
shown in the trace. hidden_links is a direct tool result, so it is filled from the hidden_links tool calls, not
written by the model. Each finished investigation is written to the memory graph (agent.memory).

`investigate()` is an async generator of events (start, memory, note, step, verdict, done), streamed to the UI.
"""

import asyncio
import json
import re
import time

import anyio

from agent import actions as A, grounding as G
from agent.llm import LLMUnavailable
from graph import queries as Q

MAX_STEPS = 10
WHY = {"type": "string", "description": "One sentence: why this tool, now, given what you found so far."}

SYSTEM = """You are the Change Guardian of Northwind Health's IT (a fictional US hospital system). You investigate
with tools over one graph that holds the CMDB's declared dependencies and what discovery evidence actually observed,
then decide and act. You choose each next tool from what you have found so far; every call needs a short `why`.

For a change review the usual route is: get_change; radius of each target in the declared and the real layer; if
they differ, hidden_links and the evidence source of each; who_to_page for the newly reached components (owners in
the CMDB vs observed); if an impacted component holds patient data, protection_check or findings_about; recall memory
about the target and the impacted components (mention facts people confirmed; never re-raise what they rejected);
changes_on the shared components. Compare the declared and real counts in both directions: when the CMDB
claims a much larger radius than the evidence shows, call stale_links (dependencies with no traffic) and say the
CMDB overstates the impact. Then act where the graph supports it: add_change_note with the real impact,
request_cab_review when the real risk is higher than recorded, notify_owner for owners who must be warned
(prefer the observed owner when it differs), propose_cmdb_fix for hidden links that should be recorded.

For an incident: find the affected components (search_components), what they share (shared_dependencies), recent
changes on it (changes_on), open findings about it (findings_about, or findings_by_type for estate-wide problems such
as an unregistered app (D14), failing backups (D10) or access drift (D12)), who owns it (who_to_page), recall memory;
then open_ticket or notify_owner for the team that should act. The root cause is the component (or app) whose
problem explains the symptom.

Risk for a change: high when the real impact reaches patient-safety services the CMDB does not connect to the
target; medium when the real impact is larger than recorded; low when it is not (also when the CMDB overstates it).
Risk for an incident: high when patient data is exposed, unprotected or cannot be restored, or a patient-safety
service is down; medium when a clinical service is degraded or slowed (for example tickets routed to the wrong team);
low when no service is affected.
Use only numbers and names that appear in tool results, written exactly as the results give them. You may
summarise ("51 hidden links to lrc-db-01") but never invent an identifier or a label for a link (such as
"interface-to-lrc-db-01"). You have at most {steps} model calls; the last one has no tools and must give the verdict,
so gather evidence early and keep calls few."""

FINAL = ("Tools are now disabled. From the evidence below give your verdict as JSON with exactly these keys: "
         "risk (high|medium|low), root_cause (the component or change most responsible, or null), reasons (short "
         "sentences), warn (objects with team, component, why), actions (what you did or recommend), evidence (tool "
         "names you relied on). Use only names and numbers from the evidence, written exactly as it gives them; you may "
         "summarise (\"51 hidden links to lrc-db-01\") but never invent identifiers. The hidden links are listed "
         "from the hidden_links tool results, so do not write them.")

VERDICT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["risk", "root_cause", "reasons", "warn", "actions", "evidence"],
    "properties": {
        "risk": {"type": "string", "enum": ["high", "medium", "low"]},
        "root_cause": {"type": ["string", "null"]},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "warn": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                            "required": ["team", "component", "why"],
                                            "properties": {"team": {"type": "string"}, "component": {"type": "string"},
                                                           "why": {"type": "string"}}}},
        "actions": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
}


def with_why(spec):
    s = json.loads(json.dumps(spec))
    sch = s["input_schema"]
    sch.setdefault("properties", {})["why"] = WHY
    sch["required"] = list(dict.fromkeys(list(sch.get("required", [])) + ["why"]))
    sch.pop("title", None)
    return s


def trigger_text(kind, trigger):
    if kind == "change":
        return f"Review change request {trigger} before it is approved: what does it really affect, and what should we do?"
    return f"Incident symptom reported: {trigger}. Find the likely cause and who should act."


def parse_json(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


def summarise(name, res):
    """One short line for the trace."""
    if isinstance(res, dict) and res.get("error"):
        return f"Error: {res['error']}"
    if name == "radius" and isinstance(res, dict) and res.get("counts"):
        return f"{res['layer']} radius: " + G.counts_text(res["counts"])
    if name == "hidden_links" and isinstance(res, dict):
        src = sorted({x.get("source") or "" for x in res.get("hidden_links", [])} - {""})
        return f"{res.get('count', 0)} hidden links" + (f", seen by {', '.join(src)}" if src else "")
    if name == "recall" and isinstance(res, dict):
        return (f"{len(res.get('confirmed', []))} confirmed facts, {len(res.get('rejected', []))} rejected, "
                f"{len(res.get('investigations', []))} earlier investigations")
    if name == "get_change" and isinstance(res, dict):
        return f"{res.get('number')}: {res.get('description')} ({res.get('state')}), targets {', '.join(res.get('targets', []))}"
    if name in A.SPECS:
        return A.summary(name, res)
    return G.step_found(name, res)


def tool_hidden_links(trace):
    """Hidden links exactly as the hidden_links tool returned them ("src -> dst"), in order, without repeats.
    The verdict's list is filled from these, never from the model's output."""
    out = []
    for t in trace:
        if t["tool"] == "hidden_links" and isinstance(t["result"], dict):
            out += [f"{e['src']} -> {e['dst']}" for e in t["result"].get("hidden_links") or []
                    if isinstance(e, dict) and e.get("src") and e.get("dst")]
    return list(dict.fromkeys(out))


def flatten(v):
    parts = [v.get("root_cause") or ""] + list(v.get("reasons") or []) + list(v.get("hidden_links") or [])
    parts += [f"{w.get('team')} {w.get('component')} {w.get('why')}" for w in v.get("warn") or []]
    return "\n".join(parts + list(v.get("actions") or []))


def check_verdict(g, kind, v, trace, trigger):
    """None when the verdict passes, else the reason it was rejected."""
    if not isinstance(v, dict) or v.get("risk") not in ("high", "medium", "low"):
        return "the verdict is not valid JSON with a risk level"
    facts = {"trigger": trigger, "tool_calls": trace}
    _, bad = G.grounded(flatten(v), facts, G.graph_names(g), f"guardian {kind} {trigger}")
    if bad:
        return "numbers or names not in the evidence: " + "; ".join(bad[:5])
    if kind == "incident":
        from agent.flows import CHANGE_ID, likely_causes
        causes = likely_causes(g, trace)
        named = CHANGE_ID.findall(flatten(v))
        if causes is not None and named and named[0] not in {c["number"] for c in causes}:
            return f"it names {named[0]}, which does not target the most specific shared dependency"
    return None


def rule_verdict(g, kind, trigger, trace):
    v = {"risk": "medium", "root_cause": None, "reasons": [G.summarise_trace(trace)], "hidden_links": [], "warn": [],
         "actions": [], "evidence": sorted({t["tool"] for t in trace})}
    if kind == "change":
        r = Q.change_review(g, trigger)
        if r.get("found"):
            v["risk"] = r["risk"]
            v["hidden_links"] = [f"{e['src']} -> {e['dst']}" for e in r["undocumented_edges"]][:10]
            v["root_cause"] = (r["targets"] or [None])[0]
    for t in trace:
        if t["tool"] == "who_to_page" and isinstance(t["result"], list):
            v["warn"] += [{"team": o.get("observed_owner") or o.get("declared_owner"), "component": o["component"],
                           "why": "owner of an impacted component"} for o in t["result"]
                          if o.get("observed_owner") or o.get("declared_owner")][:6]
    return v


def components_of(g, kind, trigger, trace, verdict):
    names = G.graph_names(g)
    text = json.dumps([trigger, verdict, [t["input"] for t in trace]], default=str)
    out = G.mentioned(text, names)
    if kind == "change":
        r = Q.run(g, Q.CHANGE_TARGETS, {"id": trigger})
        out += r[0]["targets"] if r else []
    return sorted(set(out))


def change_targets(g, trigger):
    r = Q.run(g, Q.CHANGE_TARGETS, {"id": trigger})
    return r[0]["targets"] if r else []


def memory_context(g, memory, kind, trigger):
    """What memory already says about the change's target and its real impact (shown before the first step)."""
    if kind != "change":
        return None
    names = set()
    for t in change_targets(g, trigger):
        r = Q.radius(g, t, "real")
        names |= {t} | ({x["name"] for x in r["impacted"]} if r.get("found") else set())
    return memory.recall(sorted(names)) if names else None


HEARTBEAT_S = 5


def call_tool_sync(server, name, args):
    """One MCP tool call on its own event loop (run in a worker thread): the stream's loop only polls."""
    from mcp import Client

    async def go():
        async with Client(server) as c:
            return await c.call_tool(name, args)
    return anyio.run(go)


_POOL = None


async def beat_thread(fn, every=None):
    """Run a blocking call (the model, or a tool call on its own loop) in a plain worker thread and poll it,
    yielding ("heartbeat", None) every `every` seconds while waiting, then ("result", value). Keeps the stream alive
    through proxies and clients. (An extra asyncio task for the MCP call stalled the stream on Vercel's runtime.)"""
    global _POOL
    import concurrent.futures
    if _POOL is None:
        _POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="guardian-model")
    fut, last = _POOL.submit(fn), time.monotonic()
    while not fut.done():
        await asyncio.sleep(0.1)
        if time.monotonic() - last >= (every or HEARTBEAT_S):
            last = time.monotonic()
            yield "heartbeat", None
    yield "result", fut.result()


async def investigate(g, memory, kind, trigger, llm, mcp_server, *, max_steps=MAX_STEPS, charge=None, user=None,
                      deadline_s=45, heartbeat_s=None):
    """Run one investigation; yields events. `charge(usd) -> bool` books each call's cost (False: cap reached)."""
    from mcp import Client
    t0 = time.perf_counter()
    model = getattr(llm, "model", None) or type(llm).__name__
    yield {"type": "start", "kind": kind, "trigger": trigger, "model": model, "max_steps": max_steps}
    mem = memory_context(g, memory, kind, trigger)
    if mem and (mem["confirmed"] or mem["rejected"] or mem["investigations"]):
        yield {"type": "memory", **mem}
    acts = A.Actions(g, investigation=f"INV-{time.time_ns()}")
    trace, steps, total, stop_reason = [], [], 0.0, None
    system = SYSTEM.replace("{steps}", str(max_steps))
    messages = [{"role": "user", "content": trigger_text(kind, trigger)}]
    async with Client(mcp_server) as client:
        listed = await client.list_tools()
        mcp_specs = [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema}
                     for t in listed.tools]
        specs = [with_why(s) for s in mcp_specs + A.specs()]
        n = 0
        while n < max_steps - 1:
            if time.perf_counter() - t0 > deadline_s:  # leave time for the verdict inside the 60 s function limit
                break
            n += 1
            seen = len(getattr(llm, "events", []))
            try:
                async for tag, val in beat_thread(lambda: llm.respond(system, messages, specs), heartbeat_s):
                    if tag == "heartbeat":
                        yield {"type": "heartbeat"}
                    else:
                        reply = val
            except LLMUnavailable as e:
                stop_reason = f"model unavailable ({e})"
                break
            for ev in getattr(llm, "events", [])[seen:]:
                yield {"type": "note", "text": ev}
            total += reply.cost
            if charge is not None and not charge(reply.cost):
                stop_reason = "today's agent spending cap was reached"
                break
            if not reply.tool_calls:
                break
            messages.append({"role": "assistant", "content": reply.content})
            results = []
            for i, call in enumerate(reply.tool_calls):
                args = dict(call["input"])
                why = str(args.pop("why", "")).strip()
                ts = time.perf_counter()
                if call["name"] in A.SPECS:
                    res = acts.call(call["name"], args)
                    text = json.dumps(res, default=str)
                else:
                    async for tag, val in beat_thread(lambda: call_tool_sync(mcp_server, call["name"], args), heartbeat_s):
                        if tag == "heartbeat":
                            yield {"type": "heartbeat"}
                        else:
                            r = val
                    text = "".join(getattr(c, "text", "") for c in r.content)
                    try:
                        res = json.loads(text)
                    except ValueError:
                        res = {"error": text[:300]} if r.is_error else text
                trace.append({"tool": call["name"], "input": args, "result": res})
                step = {"n": n, "tool": call["name"], "why": why, "args": args, "summary": summarise(call["name"], res),
                        "ms": round((time.perf_counter() - ts) * 1000), "cost": round(reply.cost if i == 0 else 0, 6),
                        "action": call["name"] in A.SPECS}
                steps.append(step)
                yield {"type": "step", **step}
                results.append({"type": "tool_result", "tool_use_id": call["id"], "content": text[:30000]})
            messages.append({"role": "user", "content": results})
    # final step: no tools, structured verdict
    verdict, rejected = None, None
    if stop_reason is None:
        evidence = "\n".join(f"- {t['tool']}({json.dumps(t['input'], default=str)}) -> "
                             f"{json.dumps(t['result'], default=str)[:6000]}" for t in trace)
        prompt = f"{trigger_text(kind, trigger)}\n\nEvidence (tool results):\n{evidence or '(none)'}\n\n{FINAL}"
        try:
            kw = {"schema": VERDICT_SCHEMA} if getattr(llm, "supports_schema", False) else {}
            async for tag, val in beat_thread(
                    lambda: llm.respond(system, [{"role": "user", "content": prompt[:60000]}], None, **kw), heartbeat_s):
                if tag == "heartbeat":
                    yield {"type": "heartbeat"}
                else:
                    reply = val
            total += reply.cost
            if charge is not None:
                charge(reply.cost)
            verdict = parse_json(reply.text)
            if isinstance(verdict, dict):  # a direct tool result: taken from the tools, not from the model
                verdict["hidden_links"] = tool_hidden_links(trace)
            rejected = check_verdict(g, kind, verdict, trace, trigger)
        except LLMUnavailable as e:
            stop_reason = f"model unavailable ({e})"
    if stop_reason or rejected:
        verdict = rule_verdict(g, kind, trigger, trace)
    out = {"type": "verdict", "verdict": verdict, "valid": not (stop_reason or rejected),
           "rejected": rejected, "stopped": stop_reason,
           "actions_taken": [s for s in steps if s["action"]]}
    yield out
    inv = {"id": acts.inv, "kind": kind, "trigger": trigger, "model": model, "cost": round(total, 6),
           "verdict": verdict, "steps": steps, "valid": out["valid"],
           "components": components_of(g, kind, trigger, trace, verdict)}
    try:
        memory.record(inv)
    except Exception as e:  # memory is best effort; the answer still stands
        G.log.warning("guardian: memory write failed (%s)", type(e).__name__)
    yield {"type": "done", "id": acts.inv, "cost": round(total, 6), "steps": len(steps), "model": model,
           "ms": round((time.perf_counter() - t0) * 1000)}


async def collect(*a, **k):
    return [e async for e in investigate(*a, **k)]
