"""Copy the on-disk LLM response cache into deploy/llm_cache/ for the public
deployment, where CachedLLM serves it read-only (no model calls, no writes).

    python -m deploy.bundle_cache [--from runtime/llm_cache]

models.json records the model list the cache keys were made with (the
OpenRouter preference list at the time), so lookups hash the same way.
"""

import argparse
import json
import os
import shutil

from agent.llm import DEFAULT_FREE_MODELS, MAX_MODELS

OUT = os.path.join("deploy", "llm_cache")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", default=os.path.join("runtime", "llm_cache"))
    ap.add_argument("--models", default=os.environ.get("NORTHWIND_MODELS") or DEFAULT_FREE_MODELS)
    a = ap.parse_args()
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    n = 0
    for f in sorted(os.listdir(a.src)):
        if f.endswith(".json"):
            shutil.copy(os.path.join(a.src, f), os.path.join(OUT, f))
            n += 1
    models = [m.strip() for m in a.models.split(",") if m.strip()][:MAX_MODELS]
    with open(os.path.join(OUT, "models.json"), "w") as f:
        json.dump(models, f)
    print(json.dumps({"cached_answers": n, "models": models, "dir": OUT}))


if __name__ == "__main__":
    main()
