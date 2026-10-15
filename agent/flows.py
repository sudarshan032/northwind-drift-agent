"""Agent jobs as LangGraph flows (AGENT_SPEC phase 2).

1. triage          detect -> build findings -> explain top N -> store -> rank
2. change_review   review -> owners -> explain
3. incident        tool-using agent loop (search, shared dependencies, changes, paths)
4. ambiguity       evidence -> LLM judgement per D13 alias / D14 app -> stored on a Finding
5. chat            tool-using agent loop over read-only tools

Every flow takes an LLM (agent.llm OpenRouterLLM, ClaudeLLM or FakeLLM) and
wraps it in a per-job call cap (agent.llm.JOB_CAPS). Without an LLM, or when
the budget is exhausted or every model fails (LLMUnavailable), triage and
change review fall back to template text with agent.llm.UNAVAILABLE as the
notice; the open-ended jobs return NO_KEY or the notice. Detection, radius
and the change review facts never depend on the LLM. The LLM never writes
CMDB files or declared edges.
"""

import json
import re
from typing import TypedDict

from langgraph.graph import END, StateGraph

from agent import findings as F, grounding as G
from agent.llm import JOB_CAPS, NO_KEY, UNAVAILABLE, LLMUnavailable, capped
from agent.tools import READ_ONLY, Toolbox
from graph import queries as Q

GROUNDING = ("Use only the numbers, names, dates and risk levels that appear in the facts or tool results you are "
             "given; never compute, round, estimate or invent counts, component names, dates or paths. If a tool "
             "returns nothing, say so. Every number and name you write is checked against the facts.")
DISCARDED = "AI explanation discarded (numbers or names not in the facts), showing rule-based explanation"

# --------------------------------------------------------------------------
# Generic tool-using loop (used by incident and chat)
# --------------------------------------------------------------------------


class LoopState(TypedDict):
    messages: list
    steps: int
    answer: str


def final_prompt(question, box, limit=40000):
    """The last allowed call: no tools, the question plus every tool result so far."""
    ev = "\n".join(f"- {t['tool']}({json.dumps(t['input'], default=str)}) -> {box.text(t['result'])[:8000]}"
                    for t in box.trace)
    return (f"{question}\n\nEvidence gathered with tools (JSON results):\n{ev or '(none)'}"[:limit]
            + "\n\nTools are now disabled. Give your final answer from this evidence only.")


def tool_loop(llm, box, system, tool_names, max_steps, question):
    """LangGraph agent loop: model -> (tools -> model)* -> answer. The model
    is told its step budget; the final allowed call has tools disabled and
    gets the gathered evidence as plain text, so it must answer."""
    specs = box.specs(tool_names)
    system = (f"{system} You have at most {max_steps} model calls in this job; tools are disabled on the last one, "
              "so gather evidence early and keep calls few.")

    def model(state):
        last = max_steps > 1 and state["steps"] == max_steps - 1
        msgs = [{"role": "user", "content": final_prompt(question, box)}] if last else state["messages"]
        try:
            reply = llm.respond(system, msgs, None if last else specs)
        except LLMUnavailable:
            return {"steps": state["steps"] + 1, "answer": ""}
        text = "" if "<tool_call>" in reply.text else reply.text  # a tool call written as text is no answer
        return {"messages": state["messages"] + [{"role": "assistant", "content": reply.content}],
                "steps": state["steps"] + 1, "answer": text if (last or not reply.tool_calls) else state["answer"]}

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
        if state["messages"][-1]["role"] != "assistant":  # model call was unavailable
            return END
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


def run_loop(llm, box, system, question, tool_names, max_steps, job):
    app = tool_loop(llm, box, system, tool_names, max_steps, question)
    out = app.invoke({"messages": [{"role": "user", "content": question}], "steps": 0, "answer": ""},
                     {"recursion_limit": 4 * max_steps + 4})
    facts = {"question": question, "tool_calls": [{"tool": t["tool"], "input": t["input"], "result": t["result"]}
                                                  for t in box.trace]}
    answer, notice, bad = out["answer"], None, []
    if not answer:  # unavailable, step limit or unusable final reply
        notice = UNAVAILABLE
    else:
        answer, bad = G.grounded(answer, facts, G.graph_names(box.g), job)
        notice = DISCARDED if bad else None
    if answer is None or not out["answer"]:
        answer = G.summarise_trace(box.trace, question)
    return {"answer": answer, "notice": notice, "mismatches": bad,
            "steps": [{"tool": t["tool"], "input": t["input"]} for t in box.trace], "paths": cited_paths(box.trace)}


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

EXPLAIN = ("You explain CMDB drift findings for a hospital IT team. For each finding in the JSON list write 2-4 "
           "plain sentences: what is wrong, why it matters for patients or data, and what the proposed fix does. "
           'Reply with JSON only: {"explanations": [{"id": "<finding id>", "text": "..."}]}. ' + GROUNDING)


def parse_explanations(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        items = json.loads(m.group(0)).get("explanations", []) if m else []
    except (json.JSONDecodeError, AttributeError):
        items = []
    return {str(x["id"]): str(x["text"]) for x in items if isinstance(x, dict) and x.get("id") and x.get("text")}


class TriageState(TypedDict, total=False):
    detections: dict
    findings: list
    top: list
    notice: str


def triage(g, llm=None, top_n=10):
    job = capped(llm, "triage")

    def detect(s):
        return {"detections": Q.detect_all(g)}

    def build(s):
        return {"findings": F.build(g, s["detections"])}

    def explain(s):
        fs, notice = s["findings"], None
        if job is not None:  # one batched call for the top N (free-tier budget)
            facts = [{"id": f["id"], **{k: f[k] for k in ("kind", "key", "subjects", "severity", "path")},
                      "evidence": json.dumps(f["evidence"], default=str)[:1500], "patch_ops": len(f["patch"])}
                     for f in fs[:top_n]]
            try:
                got = parse_explanations(job.respond(EXPLAIN, [{"role": "user", "content": json.dumps(
                    facts, default=str)}]).text)
            except LLMUnavailable:
                got, notice = {}, UNAVAILABLE
            names, discarded = G.graph_names(g), 0
            for f in fs[:top_n]:
                if got.get(f["id"]):
                    facts = {k: f[k] for k in ("id", "kind", "key", "keys", "subjects", "severity", "evidence",
                                               "path", "patch", "explanation")}
                    text, bad = G.grounded(got[f["id"]], facts, names, f"triage {f['id']}")
                    if text:
                        f["explanation"], f["explained_by"] = text, "llm"
                    else:
                        discarded += 1  # mismatch logged by G.grounded
            if not got and notice is None:
                notice = "AI explanation unavailable (model reply not usable), showing rule-based explanation"
            elif discarded:
                notice = f"{DISCARDED} for {discarded} finding(s)"
        return {"findings": fs, "notice": notice}

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
    return {"total": len(out["findings"]), "top": out["top"], "notice": out.get("notice"),
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
    notice: str
    mismatches: list


def change_review(g, change_id, llm=None):
    job = capped(llm, "change_review")

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
        if job is None:
            return {"explanation": template_review(facts)}
        try:
            reply = job.respond(CHANGE, [{"role": "user", "content": json.dumps(facts, default=str)[:12000]}])
        except LLMUnavailable:
            return {"explanation": template_review(facts), "notice": UNAVAILABLE}
        if not reply.text:
            return {"explanation": template_review(facts), "notice": UNAVAILABLE}
        text, bad = G.grounded(reply.text, facts, G.graph_names(g), f"change_review {change_id}")
        if text is None:
            return {"explanation": template_review(facts), "notice": DISCARDED, "mismatches": bad}
        return {"explanation": text}

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
            if out["review"].get("found") else None, "explanation": out["explanation"], "notice": out.get("notice"),
            "mismatches": out.get("mismatches", [])}


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
            f"{f['real']['routes']} interfaces (HL7 routes), {f['real']['hospitals']} hospitals, {f['real']['business_services']} "
            f"business services and {f['real']['service_offerings']} service offerings"
            + (f", including {', '.join(f['newly_reached_high_safety_services'][:3])}, which the CMDB misses. "
               if f["newly_reached_high_safety_services"] else "; the CMDB already shows every high-safety service. ")
            + (f"{len(f['hidden_edges'])} hidden dependencies, e.g. {', '.join(f['hidden_edges'][:3])}. "
               if f["hidden_edges"] else "No hidden dependencies. ")
            + f"Warn: {', '.join(warn)}.")


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
    return run_loop(capped(llm, "incident"), Toolbox(g), INCIDENT, symptom, INCIDENT_TOOLS,
                    JOB_CAPS["incident"], "incident")


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
            try:
                reply = llm.respond(JUDGE, [{"role": "user", "content": json.dumps(c, default=str)[:12000]}])
                j = parse_judgement(reply.text)
            except LLMUnavailable:
                j = {"decision": None, "reasons": [UNAVAILABLE], "by": "none"}
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
    return run_loop(capped(llm, "chat"), Toolbox(g), CHAT, message, READ_ONLY, JOB_CAPS["chat"], "chat")

