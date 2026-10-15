"""Run the demo flows once so every LLM response is cached on disk; later
demo runs of the same flows cost zero requests.

    python -m agent.prewarm            (local; reads .env if present)
    docker compose exec api python -m agent.prewarm
"""

import json
import os

from falkordb import FalkorDB

from agent import flows
from agent.llm import get_llm

DEMO_CHANGES = ["CHG-HEADLINE", "CHG0031513"]
DEMO_SYMPTOM = "ED critical results delayed at H03, H17 and H22"  # ui/index.html defaults
DEMO_QUESTION = "What breaks if lrc-db-01 fails, and who should I call?"


def load_env(path=".env"):
    if os.path.exists(path):
        for line in open(path):
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                os.environ.setdefault(k, v)


def main():
    load_env()
    llm = get_llm()
    if llm is None:
        raise SystemExit("no LLM provider configured (see .env.example)")
    g = FalkorDB(host=os.environ.get("FALKORDB_HOST", "localhost"),
                 port=int(os.environ.get("FALKORDB_PORT", 6379))).select_graph(os.environ.get("NORTHWIND_GRAPH",
                                                                                               "northwind"))
    before = llm.status().get("remaining")
    out = {"triage": flows.triage(g, llm)["notice"]}
    for c in DEMO_CHANGES:
        r = flows.change_review(g, c, llm)
        out[c] = {"notice": r["notice"], "mismatches": r["mismatches"]}
    for job, r in (("incident", flows.incident(g, DEMO_SYMPTOM, llm)), ("chat", flows.chat(g, DEMO_QUESTION, llm))):
        out[job] = {"notice": r["notice"], "mismatches": r["mismatches"], "steps": len(r["steps"]),
                    "answer": r["answer"][:160]}
    llm._key_info = (0, None)  # force a fresh key-info read
    out["requests_remaining"] = {"before": before, "after": llm.status().get("remaining")}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
