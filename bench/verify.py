"""Check a finished run (BENCH_SPEC 6.1): every step present, correctness gate passed, no errors, memory under
the container limit. Exit 1 on any problem.

    python -m bench.verify [--sizes 50000,250000,500000,3500000]
"""

import argparse
import json
import os
import sys

from bench import config as C
from bench.run import STEPS


def verify(sizes):
    problems, report = [], {}
    limit = float(C.MEMORY.rstrip("g")) * 2**30
    for size in sizes:
        d = os.path.join(C.RESULTS, str(size))
        r = {}
        for s in STEPS:
            p = os.path.join(d, f"{s}.jsonl")
            if not os.path.exists(p):
                problems.append(f"{size}: missing {s}")
                continue
            r[s] = [json.loads(x) for x in open(p)]
        if os.path.exists(os.path.join(d, "gate_failure.jsonl")):
            problems.append(f"{size}: gate_failure.jsonl present")
        gate = [x for x in r.get("depth", []) if x.get("gate")]
        if not gate or gate[0]["gate"] != "passed":
            problems.append(f"{size}: correctness gate not passed")
        timed = [x for x in r.get("depth", []) if "start" in x]
        expected = 2 * 2 * len(C.DEPTHS) * (len(C.START_POINTS) + 1)
        if len(timed) != expected:
            problems.append(f"{size}: {len(timed)} timed depth rows, expected {expected}")
        for x in r.get("concurrency_falkordb", []) + r.get("concurrency_postgres", []):
            if x["errors"]:
                problems.append(f"{size}: {x['system']} {x['clients']} clients had {x['errors']} errors")
        peaks = [x.get("peak_memory_bytes", 0) for rows in r.values() for x in rows]
        if peaks and max(peaks) > limit:
            problems.append(f"{size}: peak memory {max(peaks) / 2**30:.2f} GB over the {C.MEMORY} limit")
        cr = r.get("change_review", [])
        if len(cr) == 2 and any(cr[0][k] != cr[1][k] for k in ("declared", "real", "hidden", "hospitals_real")):
            problems.append(f"{size}: change review results differ between systems")
        report[size] = {"steps": len(r), "gate": gate[0]["gate"] if gate else None, "timed_rows": len(timed),
                        "max_peak_gb": round(max(peaks) / 2**30, 2) if peaks else None}
    return problems, report


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default="50000,250000,500000,3500000")
    probs, rep = verify([int(s) for s in ap.parse_args().sizes.split(",")])
    print(json.dumps({"report": rep, "problems": probs}, indent=1))
    sys.exit(1 if probs else 0)
