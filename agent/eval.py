"""Live evaluation of the Change Guardian (AGENT_V2_SPEC 7).

    python -m agent.eval [--model gpt-6-luna] [--graph northwind] [--record]

Runs the agent once per scenario (ingest.scenarios: the headline change plus four more) against the graph named by
NORTHWIND_GRAPH / FALKORDB_* (.env), with its own memory graph (northwind_memory_eval), and scores: root cause,
risk, owners warned, grounding passed, steps, cost. Writes docs/records/AGENT_EVAL.md and, with --record, the
runs as recorded demo runs in deploy/agent_runs/. Spends live tokens: run it only when asked.
"""

import argparse
import json
import os
import time

import anyio

from agent import guardian, mcp_tools, spend
from agent.llm import OpenAILLM
from agent.memory import Memory
from ingest.scenarios import SCENARIOS

OUT = os.path.join("docs", "records", "AGENT_EVAL.md")
RUNS = os.path.join("deploy", "agent_runs")
EXTRA = [{"id": "control", "kind": "change", "trigger": "CHG0031513"}]  # demo flow, recorded but not scored


def score(s, ev):
    v = next(e for e in ev if e["type"] == "verdict")
    done = next(e for e in ev if e["type"] == "done")
    verdict = v["verdict"] or {}
    cause_text = " ".join([verdict.get("root_cause") or ""] + list(verdict.get("reasons") or []))
    teams = {w.get("team") for w in verdict.get("warn") or []}
    notes = [e["text"] for e in ev if e["type"] == "note"]
    return {"id": s["id"], "kind": s["kind"], "root_cause": any(c in cause_text for c in s["root_cause_any"]),
            "risk": verdict.get("risk") in s["risk_ok"], "risk_given": verdict.get("risk"),
            "owners": all(t in teams for t in s["warn"]), "grounding": v["valid"], "rejected": v["rejected"] or v["stopped"],
            "steps": done["steps"], "cost": done["cost"], "seconds": round(done["ms"] / 1000, 1),
            "actions": [a["tool"] for a in v["actions_taken"]], "notes": notes,
            "correct": None}


def slug(kind, trigger):
    import re
    return f"{kind}-" + re.sub(r"[^a-z0-9]+", "-", trigger.lower()).strip("-")[:80]


def main():
    from agent.prewarm import load_env
    from graph.connection import connect
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=os.environ.get("AGENT_MODEL", "gpt-6-luna"))
    ap.add_argument("--graph", default=None)
    ap.add_argument("--record", action="store_true")
    a = ap.parse_args()
    load_env()
    db = connect()
    g = db.select_graph(a.graph or os.environ.get("NORTHWIND_GRAPH", "northwind"))
    mem = Memory(db, "eval")
    mem.reset()
    mcp_tools.configure(lambda: g, lambda: mem)
    llm = OpenAILLM(model=a.model)
    rows, runs = [], {}
    for s in SCENARIOS + (EXTRA if a.record else []):
        t = time.time()
        ev = anyio.run(lambda: guardian.collect(g, mem, s["kind"], s["trigger"], llm, mcp_tools.server,
                                                charge=lambda u: spend.charge(g, u)))
        runs[slug(s["kind"], s["trigger"])] = ev
        if "root_cause_any" in s:
            r = score(s, ev)
            r["correct"] = r["root_cause"] and r["risk"] and r["owners"] and r["grounding"]
            rows.append(r)
            print(json.dumps(r), flush=True)
        else:
            print(json.dumps({"recorded": s["trigger"], "s": round(time.time() - t, 1)}), flush=True)
    if a.record:
        os.makedirs(RUNS, exist_ok=True)
        for k, ev in runs.items():
            with open(os.path.join(RUNS, k + ".json"), "w", encoding="utf-8") as f:
                json.dump(ev, f, indent=1, default=str)
    ok = sum(1 for r in rows if r["correct"])
    yes = lambda b: "yes" if b else "no"
    lines = [f"# Agent evaluation ({a.model})", "",
             f"Run {time.strftime('%Y-%m-%d %H:%M %Z')} with `python -m agent.eval`: one live run per scenario "
             "(ingest/scenarios.py), against the demo graph, memory graph `northwind_memory_eval` (empty at start). "
             "Correct = right root cause, accepted risk, expected owner warned and the verdict passed the grounding "
             "checks.", "",
             "| Scenario | Root cause | Risk | Owners warned | Grounding | Steps | Cost | Time | Actions taken |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {yes(r['root_cause'])} | {yes(r['risk'])} ({r['risk_given']}) | {yes(r['owners'])} | "
                     f"{yes(r['grounding'])} | {r['steps']} | ${r['cost']:.4f} | {r['seconds']} s | "
                     f"{', '.join(r['actions']) or 'none'} |")
    total = sum(r["cost"] for r in rows)
    lines += ["", f"**{ok}/{len(rows)} correct.** Total ${total:.4f}, mean ${total / max(len(rows), 1):.4f} per "
              "investigation.", ""]
    for r in rows:
        if r["rejected"] or r["notes"]:
            lines.append(f"- {r['id']}: {r['rejected'] or ''} {'; '.join(r['notes'])}".rstrip())
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"{ok}/{len(rows)} correct, ${total:.4f}")


if __name__ == "__main__":
    main()
