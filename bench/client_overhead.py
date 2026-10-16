"""Client overhead at 3.47M nodes (owner's request, 16 Oct): FalkorDB end-to-end time for the traversal-only and
full-radius queries, measured with (1) the current Python client, (2) the same client with hiredis (C reply
parser), (3) redis-cli sending the identical GRAPH.QUERY ... --compact command (lowest possible client cost).

    python -m bench.client_overhead python|hiredis|redis-cli

Appends rows to bench/results/3500000/client_overhead.jsonl; earlier results are not touched.
"""

import json
import os
import shlex
import statistics
import subprocess
import sys
import time

from bench import config as C, infra, load_falkor, load_falkor_bulk
from bench.queries import FALKOR_REACH
from bench.run import stats
from graph.queries import RADIUS

SIZE = 3_500_000
STARTS = ["arr-pri-a-03", "dc1-host-07"]
DEPTH = 10
QUERIES = {"reach": FALKOR_REACH, "radius": RADIUS % {"term": "AFFECTS_REAL"}}
OUT = os.path.join(C.RESULTS, str(SIZE), "client_overhead.jsonl")


def cypher(kind, name):
    """The query text with parameters inlined as a CYPHER prefix, exactly as the Python client sends it."""
    return f"CYPHER name='{name}' depth={DEPTH} rel='IMPACTS_REAL' {QUERIES[kind]}"


def python_client(mode):
    import redis.utils
    if mode == "hiredis" and not redis.utils.HIREDIS_AVAILABLE:
        raise SystemExit("hiredis is not importable in this environment")
    db = infra.start_falkordb()
    g = db.select_graph(load_falkor.graph_name(SIZE))
    rows = []
    for name in STARTS:
        for kind, q in QUERIES.items():
            params = {"name": name, "depth": DEPTH, "rel": "IMPACTS_REAL"}
            for _ in range(C.WARMUP):
                g.query(q, params)
            e2e, srv = [], []
            for _ in range(C.RUNS):
                t = time.perf_counter()
                r = g.query(q, params)
                e2e.append((time.perf_counter() - t) * 1000)
                srv.append(r.run_time_ms)
            rows.append({"client": mode, "kind": kind, "start": name, "depth": DEPTH, **stats(e2e),
                         "server_median_ms": round(statistics.median(srv), 3),
                         "hiredis": redis.utils.HIREDIS_AVAILABLE, "redis_py": redis.__version__})
    return rows


def redis_cli():
    """redis-cli inside the FalkorDB container (the host has none): one process sends the command N times
    (-r), so process start-up is paid once; a PING run of the same length is subtracted. This also skips the
    Docker port-forwarding hop the Python client goes through, so it is a lower bound on client cost."""
    ctr = C.FALKOR["container"]

    def timed(cmd, n):
        script = f"s=$(date +%s%N); redis-cli -r {n} {cmd} > /dev/null; e=$(date +%s%N); echo $((e - s))"
        return int(subprocess.run(["docker", "exec", ctr, "sh", "-c", script], capture_output=True, text=True,
                                  check=True).stdout.strip()) / 1e6
    rows = []
    graph = load_falkor.graph_name(SIZE)
    base = statistics.median(timed("PING", C.RUNS) for _ in range(5))
    for name in STARTS:
        for kind in QUERIES:
            cmd = f"GRAPH.QUERY {graph} {shlex.quote(cypher(kind, name))} --compact"
            timed(cmd, C.WARMUP)
            per_run = [(timed(cmd, C.RUNS) - base) / C.RUNS for _ in range(5)]  # 5 batches of 20 queries
            rows.append({"client": "redis-cli", "kind": kind, "start": name, "depth": DEPTH,
                         "median_ms": round(statistics.median(per_run), 3), "min_ms": round(min(per_run), 3),
                         "max_ms": round(max(per_run), 3), "runs": C.RUNS * 5,
                         "method": "5 batches of redis-cli -r 20 inside the container, PING batch subtracted, "
                                   "median of the batch means"})
    return rows


if __name__ == "__main__":
    mode = sys.argv[1]
    db = infra.start_falkordb()
    meta = json.load(open(os.path.join(C.DATA, str(SIZE), "meta.json")))
    if load_falkor.loaded(db, SIZE) != meta["nodes"]:
        print("loading 3.47M with the bulk loader (existing files)")
        load_falkor_bulk.load(db, SIZE, print)
    rows = redis_cli() if mode == "redis-cli" else python_client(mode)
    with open(OUT, "a") as f:
        for r in rows:
            f.write(json.dumps({**r, "size": SIZE, "hardware": infra.hardware()}, default=str) + "\n")
    for r in rows:
        print(r["client"], r["kind"], r["start"], r["median_ms"], r.get("p95_ms"), r.get("server_median_ms"))
