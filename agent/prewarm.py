"""Run the demo flows once so every LLM response is cached on disk; later
demo runs of the same flows cost zero requests.

    python -m agent.prewarm            (local; reads .env if present)
    docker compose exec api python -m agent.prewarm
"""

import json
import os


from agent import flows
from agent.answers import CHIPS
from agent.llm import get_llm
from graph.connection import connect

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
    g = connect().select_graph(os.environ.get("NORTHWIND_GRAPH", "northwind"))  # FALKORDB_* incl. cloud auth
    bundle = os.path.join("deploy", "llm_cache", "models.json")
    if os.path.exists(bundle) and hasattr(llm, "models"):  # keep the keys of every answer already in the bundle
        with open(bundle) as f:
            llm.models = json.load(f)
    if getattr(llm, "cache", None) is not None:  # the model list the cache keys are hashed with (deploy bundle)
        with open(os.path.join(llm.cache.path, "models.json"), "w") as f:
            json.dump(llm.models, f)
    before = llm.status().get("remaining")
    out = {}
    r = flows.incident(g, DEMO_SYMPTOM, llm)  # most important answer first, while the quota is fresh
    out["incident"] = {"notice": r["notice"], "mismatches": r["mismatches"], "steps": len(r["steps"]),
                       "likely_cause": r.get("likely_cause"), "answer": r["answer"]}
    out["triage"] = flows.triage(g, llm)["notice"]
    r = flows.chat(g, DEMO_QUESTION, llm)
    out["chat"] = {"notice": r["notice"], "mismatches": r["mismatches"], "steps": len(r["steps"]),
                   "answer": r["answer"][:300]}
    for q in CHIPS:  # the chat's suggested questions (SITE_SPEC 4.1)
        r = flows.chat(g, q, llm)
        out[q] = {"notice": r["notice"], "mismatches": r["mismatches"], "tools": [s["tool"] for s in r["steps"]],
                  "answer": r["answer"][:300]}
    for c in DEMO_CHANGES:
        r = flows.change_review(g, c, llm)
        out[c] = {"notice": r["notice"], "mismatches": r["mismatches"]}
    llm._key_info = (0, None)  # force a fresh key-info read
    out["requests_remaining"] = {"before": before, "after": llm.status().get("remaining")}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
