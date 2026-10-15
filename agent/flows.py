"""Agent jobs as LangGraph flows (AGENT_SPEC phase 2).

1. triage          detect -> build findings -> explain top N -> store -> rank
2. change_review   review -> owners -> explain
3. incident        tool-using agent loop (search, shared dependencies, changes, paths)
4. ambiguity       evidence -> LLM judgement per D13 alias / D14 app -> stored on a Finding
5. chat            tool-using agent loop over read-only tools

Every flow takes an LLM (agent.llm.ClaudeLLM or FakeLLM). Without one,
triage and change review fall back to template text; the open-ended jobs
return agent.llm.NO_KEY. The LLM never writes CMDB files or declared edges.
"""

import json
import re
from typing import TypedDict

from langgraph.graph import END, StateGraph

from agent import findings as F
from agent.llm import NO_KEY
from agent.tools import READ_ONLY, Toolbox
from graph import queries as Q

GROUNDING = ("Use only facts from the tool output you are given or that you retrieve with tools. "
             "Never invent component names, counts, dates or paths. If a tool returns nothing, say so.")

# --------------------------------------------------------------------------
# Generic tool-using loop (used by incident and chat)
# --------------------------------------------------------------------------


class LoopState(TypedDict):
    messages: list
    steps: int
    answer: str


def tool_loop(llm, box, system, tool_names, max_steps=8):
    """LangGraph agent loop: model -> (tools -> model)* -> answer."""
    specs = box.specs(tool_names)

    def model(state):
        reply = llm.respond(system, state["messages"], specs)
        msgs = state["messages"] + [{"role": "assistant", "content": reply.content}]
        return {"messages": msgs, "steps": state["steps"] + 1,
                "answer": reply.text if not reply.tool_calls else state["answer"]}

    def tools(state):
        last = state["messages"][-1]["content"]
        calls = [b for b in last if (b.get("type") if isinstance(b, dict) else b.type) == "tool_use"]
        results = []
        for b in calls:
            bid, name, args = (b["id"], b["name"], b["input"]) if isinstance(b, dict) else (b.id, b.name, b.input)
            res = box.call(name, args) if name in tool_names else {"error": f"tool {name} not allowed"}
            results.append({"type": "tool_result", "tool_use_id": bid, "content": box.text(res),
                            **({"is_error": True} if isinstance(res, dict) and res.get("error") else {})})
        return {"messages": state["messages"] + [{"role": "user", "content": results}]}

    def route(state):
        last = state["messages"][-1]["content"]
        has_calls = any((b.get("type") if isinstance(b, dict) else b.type) == "tool_use" for b in last)
        if has_calls and state["steps"] < max_steps:
            return "tools"
        return END

    sg = StateGraph(LoopState)
    sg.add_node("model", model)
    sg.add_node("tools", tools)
    sg.set_entry_point("model")
    sg.add_conditional_edges("model", route, {"tools": "tools", END: END})
    sg.add_edge("tools", "model")
    return sg.compile()


def run_loop(llm, box, system, question, tool_names, max_steps=8):
    app = tool_loop(llm, box, system, tool_names, max_steps)
    out = app.invoke({"messages": [{"role": "user", "content": question}], "steps": 0, "answer": ""},
                     {"recursion_limit": 4 * max_steps + 4})
    answer = out["answer"] or "(stopped: step limit reached before a final answer)"
    return {"answer": answer, "steps": [{"tool": t["tool"], "input": t["input"]} for t in box.trace],
            "paths": cited_paths(box.trace)}


def cited_paths(trace):
    """Paths the agent actually retrieved (for citations)."""
    out = []
    for t in trace:
        r = t["result"]
        if t["tool"] == "impact_path" and isinstance(r, dict) and r.get("nodes"):
            out.append(" -> ".join(r["nodes"]))
        if t["tool"] == "unregistered_app_path" and isinstance(r, list):
            out += [f"{x['grp']} -> users({x['users']}) -> {x['app']} -> {x['api']} -> {', '.join(x['data'])}"
                    for x in r]
    return out


# --------------------------------------------------------------------------
# 1. Drift triage
# --------------------------------------------------------------------------

EXPLAIN = ("You explain CMDB drift findings for a hospital IT team in 2-4 plain sentences: what is wrong, why it "
           "matters for patients or data, and what the proposed fix does. " + GROUNDING)


class TriageState(TypedDict, total=False):
    detections: dict
    findings: list
    top: list


def triage(g, llm=None, top_n=10):
    def detect(s):
        return {"detections": Q.detect_all(g)}

    def build(s):
        return {"findings": F.build(g, s["detections"])}

    def explain(s):
        fs = s["findings"]
        if llm is not None:
            for f in fs[:top_n]:
                facts = {k: f[k] for k in ("kind", "key", "severity", "evidence", "path", "patch")}
                reply = llm.respond(EXPLAIN, [{"role": "user", "content": json.dumps(facts, default=str)[:12000]}])
                if reply.text:
                    f["explanation"], f["explained_by"] = reply.text, "llm"
        return {"findings": fs}

    def store(s):
        F.store(g, s["findings"])
        return {"top": s["findings"][:top_n]}

    sg = StateGraph(TriageState)
    for name, fn in (("detect", detect), ("build", build), ("explain", explain), ("store", store)):
        sg.add_node(name, fn)
    sg.set_entry_point("detect")
    sg.add_edge("detect", "build")
    sg.add_edge("build", "explain")
    sg.add_edge("explain", "store")
    sg.add_edge("store", END)
    out = sg.compile().invoke({})
    return {"total": len(out["findings"]), "top": out["top"],
            "by_type": {t: sum(1 for f in out["findings"] if f["type"] == t) for t in sorted(F.KIND)}}


# --------------------------------------------------------------------------
# 2. Change review
# --------------------------------------------------------------------------

CHANGE = ("You review a pending change for a hospital. Compare what the CMDB says will be affected (declared radius) "
          "with what the observed graph shows (real radius). Name the hidden (undocumented) dependencies, the risk "
          "level, the hospitals and services at stake, and exactly who to warn (prefer the observed owner where it "
          "differs from the CMDB owner). " + GROUNDING)


class ChangeState(TypedDict, total=False):
    review: dict
    owners: list
    explanation: str


def change_review(g, change_id, llm=None):
    def review(s):
        return {"review": Q.change_review(g, change_id)}

    def owners(s):
        r = s["review"]
        if not r.get("found"):
            return {"owners": []}
        names = list(r["targets"])
        for t in r["per_target"]:
            names += [x["name"] for x in t["real"]["impacted"] if x["label"] in ("Database", "VM", "Application")]
            names += t["real"]["consumers"] + t["real"]["high_safety_services"][:1]
        return {"owners": Q.who_to_page(g, sorted(set(names)))}

    def explain(s):
        r = s["review"]
        if not r.get("found"):
            return {"explanation": f"No change {change_id} found."}
        facts = summarise_review(r, s["owners"])
        if llm is None:
            return {"explanation": template_review(facts)}
        reply = llm.respond(CHANGE, [{"role": "user", "content": json.dumps(facts, default=str)[:12000]}])
        return {"explanation": reply.text or template_review(facts)}

    sg = StateGraph(ChangeState)
    sg.add_node("review", review)
    sg.add_node("owners", owners)
    sg.add_node("explain", explain)
    sg.set_entry_point("review")
    sg.add_edge("review", "owners")
    sg.add_edge("owners", "explain")
    sg.add_edge("explain", END)
    out = sg.compile().invoke({})
    return {"change": change_id, "facts": summarise_review(out["review"], out["owners"])
            if out["review"].get("found") else None, "explanation": out["explanation"]}


def summarise_review(r, owners):
    t0 = r["per_target"][0] if r.get("per_target") else None
    dec, real = (t0["declared"], t0["real"]) if t0 else ({}, {})
    return {
        "change": r["change"], "description": r.get("description"), "start_date": r.get("start_date"),
        "targets": r["targets"], "risk": r["risk"],
        "declared": dec.get("counts"), "real": real.get("counts"),
        "declared_services": dec.get("business_services"), "newly_reached_high_safety_services":
            r["newly_reached_high_safety_services"][:10],
        "hospitals": real.get("hospitals"), "hidden_edges": [f"{e['src']} -> {e['dst']}" for e in r["undocumented_edges"]],
        "warn": [o for o in owners if o["declared_owner"] or o["observed_owner"]],
    }


def template_review(f):
    warn = sorted({o["observed_owner"] or o["declared_owner"] for o in f["warn"]})
    return (f"{f['change']} ({f['description']}) is {f['risk'].upper()} risk. The CMDB says it affects "
            f"{f['declared']['components']} components and {f['declared']['business_services']} business service(s) "
            f"({', '.join(f['declared_services'])}). The observed graph says {f['real']['components']} components, "
            f"{f['real']['routes']} routes, {f['real']['hospitals']} hospitals and {f['real']['business_services']} "
            f"business services, including {', '.join(f['newly_reached_high_safety_services'][:3])}. "
            f"{len(f['hidden_edges'])} hidden edges, e.g. {', '.join(f['hidden_edges'][:3])}. Warn: {', '.join(warn)}.")


# --------------------------------------------------------------------------
# 3. Incident investigation
# --------------------------------------------------------------------------

INCIDENT = ("You investigate a hospital IT incident from a symptom. Method: (1) find the affected routes or "
            "components with search_components; (2) call shared_dependencies on them to see what they all need in "
            "the real graph; (3) call changes_on for the shared components; (4) show an impact_path from the likely "
            "cause to one symptom; (5) name who to page with who_to_page. Report the likely cause, the evidence and "
            "the path. " + GROUNDING)
INCIDENT_TOOLS = ["search_components", "shared_dependencies", "changes_on", "radius", "impact_path", "who_to_page",
                  "get_component", "get_neighbourhood"]


def incident(g, symptom, llm=None):
    if llm is None:
        return {"answer": NO_KEY, "steps": [], "paths": []}
    return run_loop(llm, Toolbox(g), INCIDENT, symptom, INCIDENT_TOOLS)


# --------------------------------------------------------------------------
# 4. Ambiguity judgement (D13 aliases, D14 unregistered app)
# --------------------------------------------------------------------------

DECISIONS = ("same CI, add alias", "real undocumented dependency", "possible security risk, escalate")
JUDGE = ("You judge ambiguous CMDB evidence. Choose exactly one decision: " + "; ".join(f'"{d}"' for d in DECISIONS)
         + '. Reply with JSON only: {"decision": "...", "reasons": ["...", "..."]}. ' + GROUNDING)


def ambiguity(g, llm=None):
    cases = []
    for a in Q.resolved_aliases(g):
        comp = Q.get_component(g, a["ci"])
        cases.append({"kind": "D13", "key": ["alias", a["alias"], a["ci"]], "components": [a["ci"]],
                      "evidence": {"alias": a["alias"], "resolved_to": a["ci"],
                                   "how": "serial number match (discovery) or unique IP match (flows)",
                                   "component": {k: (comp or {}).get("props", {}).get(k) for k in
                                                 ("class", "site", "ip_address", "serial_number", "aliases")}}})
    for r in Q.detect(g, "D14"):
        cases.append({"kind": "D14", "key": r["key"], "components": ["emr-fhir-api"],
                      "evidence": {"detector": r, "access_path": Q.unregistered_app_path(g, r["key"][1])}})
    out = []
    for c in cases:
        if llm is None:
            j = {"decision": None, "reasons": [NO_KEY], "by": "none"}
        else:
            reply = llm.respond(JUDGE, [{"role": "user", "content": json.dumps(c, default=str)[:12000]}])
            j = parse_judgement(reply.text)
        fid = F.finding_id(c["kind"], c["key"])
        if F.get_finding(g, fid) is None:
            F.store(g, [{"id": fid, "type": c["kind"], "kind": "naming mismatch" if c["kind"] == "D13" else
                         F.KIND["D14"], "key": c["key"], "keys": [c["key"]], "subjects": c["components"],
                         "severity": "low" if c["kind"] == "D13" else "high", "score": 0, "evidence": c["evidence"],
                         "path": None, "patch": [], "explanation": "", "explained_by": "agent"}])
        F.set_fields(g, fid, judgement=j, decision=j.get("decision") or "")
        out.append({"finding": fid, **c, "judgement": j})
    return out


def parse_judgement(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        j = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        j = {}
    if j.get("decision") not in DECISIONS:
        return {"decision": "needs human review", "reasons": ["model reply did not contain a valid decision"],
                "raw": (text or "")[:500], "by": "llm"}
    return {"decision": j["decision"], "reasons": [str(x) for x in j.get("reasons", [])][:6], "by": "llm"}


# --------------------------------------------------------------------------
# 5. Chat
# --------------------------------------------------------------------------

CHAT = ("You answer questions about Northwind Health's IT estate using only the tools. Cite the graph paths and tool "
        "results you used. If a tool returns nothing, say that nothing was found. Do not modify anything. " + GROUNDING)


def chat(g, message, llm=None):
    if llm is None:
        return {"answer": NO_KEY, "steps": [], "paths": []}
    return run_loop(llm, Toolbox(g), CHAT, message, READ_ONLY)

