"""Benchmark runner (BENCH_SPEC 1 and 4). Resumable: every (size, system, benchmark) step writes its result
file when done and is skipped when that file exists.

    python -m bench.run --sizes 250000            # the pilot
    python -m bench.run --sizes 50000,500000,5000000 --background-safe

Progress: bench/logs/run.log and bench/STATUS.json (current step, % done, elapsed, ETA, last error).
At the end: bench/DONE or bench/FAILED and a macOS notification.
"""

import argparse
import json
import multiprocessing as mp
import os
import random
import statistics
import subprocess
import sys
import threading
import time
import traceback

from bench import config as C
from bench import hybrid, infra, load_falkor, load_falkor_bulk, load_pg, queries, scale

STEPS_V1 = ["generate", "falkordb_load", "postgres_load", "depth", "change_review", "concurrency_falkordb",
            "concurrency_postgres", "resources", "cleanup"]
# session 2 additions: bulk loader vs COPY, raw-relationship traversal, server-side times for both systems
STEPS_V2 = ["falkordb_bulk_load", "postgres_copy_load", "depth_raw", "server_times", "cleanup_v2"]
# hybrid experiment (A FalkorDB full radius, B PostgreSQL full radius, C FalkorDB ids + PostgreSQL joins,
# D FalkorDB counts only), at the busiest storage array and dc1-host-07, depths 6 and 10
STEPS_V3 = ["hybrid", "cleanup_v3"]
# PostgreSQL server time from pg_stat_statements (real prepared executions), to check the EXPLAIN-based numbers
STEPS_V4 = ["pg_stat_check", "cleanup_v4"]
STEPS = STEPS_V1 + STEPS_V2 + STEPS_V3 + STEPS_V4


# ---------------------------------------------------------------- bookkeeping

class Run:
    def __init__(self, sizes):
        self.sizes, self.t0 = sizes, time.time()
        self.total = len(sizes) * len(STEPS)
        self.done_steps, self.step_times = 0, []
        os.makedirs(C.LOGS, exist_ok=True)
        self.logf = open(os.path.join(C.LOGS, "run.log"), "a", buffering=1)
        self.last_error = None

    def log(self, msg):
        line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
        self.logf.write(line + "\n")
        print(line, flush=True)

    def status(self, step, state="running"):
        el = time.time() - self.t0
        pct = 100 * self.done_steps / self.total
        # ETA: weight later sizes by node count (time grows roughly linearly with size)
        eta = None
        if self.step_times:
            done_nodes = sum(s for s, _ in self.step_times)
            spent = sum(t for _, t in self.step_times)
            left = sum(sz for sz in self.sizes for _ in STEPS) - done_nodes
            eta = round(spent / max(done_nodes, 1) * max(left, 0))
        st = {"state": state, "current_step": step, "percent_done": round(pct, 1), "steps_done": self.done_steps,
              "steps_total": self.total, "elapsed_s": round(el), "eta_s": eta, "sizes": self.sizes,
              "last_error": self.last_error, "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        tmp = C.STATUS + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f, indent=1)
        os.replace(tmp, C.STATUS)


def result_path(size, name):
    return os.path.join(C.RESULTS, str(size), f"{name}.jsonl")


def write_result(size, name, rows, context):
    os.makedirs(os.path.join(C.RESULTS, str(size)), exist_ok=True)
    tmp = result_path(size, name) + ".tmp"
    with open(tmp, "w") as f:
        for r in rows:
            f.write(json.dumps({**context, **r}, default=str) + "\n")
    os.replace(tmp, result_path(size, name))  # appears only when complete (resumable)


def stats(xs):
    xs = sorted(xs)
    pct = lambda p: xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]
    return {"median_ms": round(statistics.median(xs), 3), "p95_ms": round(pct(95), 3), "p99_ms": round(pct(99), 3),
            "min_ms": round(xs[0], 3), "max_ms": round(xs[-1], 3), "runs": len(xs)}


def notify(msg):
    subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "Northwind benchmark"'],
                   check=False, capture_output=True)


# ---------------------------------------------------------------- start points

def storage_array(db, size):
    """The busiest storage array: most components in its real radius (chosen per size, logged)."""
    g = queries.falkor_graph(db, size)
    rows = g.query("MATCH (a:StorageArray)-[:IMPACTS_REAL]->() RETURN a.name, count(*) AS n "
                   "ORDER BY n DESC, a.name LIMIT 5").result_set
    best, best_n = None, -1
    for name, _ in rows:
        n = len(queries.falkor_radius(g, name, 10)["impacted"])
        if n > best_n:
            best, best_n = name, n
    return best


def random_starts(db, size, n=500):
    g = queries.falkor_graph(db, size)
    names = [r[0] for r in g.query("MATCH (a:Component)-[:IMPACTS_REAL]->() RETURN DISTINCT a.name "
                                   "ORDER BY a.name").result_set]
    rnd = random.Random(C.SEED)
    return rnd.sample(names, min(n, len(names)))


# ---------------------------------------------------------------- concurrency (separate processes)

def _client_proc(system, size, starts, threads, seconds, depth, seed, out):
    import psycopg
    from falkordb import FalkorDB
    lat, errors, lock = [], [], threading.Lock()
    deadline = [None]
    # the window starts when every client of this process is connected
    barrier = threading.Barrier(threads, action=lambda: deadline.__setitem__(0, time.perf_counter() + seconds))

    def client(i):
        rnd = random.Random(seed * 1000 + i)
        try:
            if system == "falkordb":
                g = FalkorDB(host="localhost", port=C.FALKOR["port"]).select_graph(load_falkor.graph_name(size))
                run = lambda nm: queries.falkor_radius(g, nm, depth)
            else:
                conn = psycopg.connect(infra.pg_dsn(), autocommit=True)
                cur = conn.cursor()
                run = lambda nm: queries.pg_radius(cur, size, nm, depth)
        except Exception as e:
            with lock:
                errors.append(f"connect: {e}")
            barrier.wait()
            return
        barrier.wait()
        mine = []
        while time.perf_counter() < deadline[0]:
            nm = rnd.choice(starts)
            t = time.perf_counter()
            try:
                run(nm)
                mine.append((time.perf_counter() - t) * 1000)
            except Exception as e:
                with lock:
                    errors.append(str(e)[:200])
        with lock:
            lat.extend(mine)

    ts = [threading.Thread(target=client, args=(i,)) for i in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    out.put({"latencies": lat, "errors": errors})


def concurrency(system, size, starts, clients, seconds=C.CONCURRENCY_SECONDS, depth=C.CONCURRENCY_DEPTH):
    procs = min(clients, 10)  # Python clients spread over processes so the client is not the bottleneck
    per = [clients // procs + (1 if i < clients % procs else 0) for i in range(procs)]
    q = mp.get_context("spawn").Queue()
    ps = [mp.get_context("spawn").Process(target=_client_proc,
                                          args=(system, size, starts, per[i], seconds, depth, i, q))
          for i in range(procs)]
    t0 = time.perf_counter()
    for p in ps:
        p.start()
    res = [q.get() for _ in ps]
    for p in ps:
        p.join()
    wall = time.perf_counter() - t0
    lat = [x for r in res for x in r["latencies"]]
    errs = [x for r in res for x in r["errors"]]
    out = {"clients": clients, "seconds": seconds, "depth": depth, "queries": len(lat),
           "throughput_qps": round(len(lat) / seconds, 2), "errors": len(errs), "error_samples": errs[:5],
           "wall_s": round(wall, 1)}
    if lat:
        s = stats(lat)
        out.update({"p50_ms": s["median_ms"], "p95_ms": s["p95_ms"], "p99_ms": s["p99_ms"]})
    return out


# ---------------------------------------------------------------- steps

def median(xs):
    return round(statistics.median(xs), 3)


def run_size_v2(run, size, ctx):
    """Session 2 steps (BENCH_SPEC 6 addendum). Each is resumable like the first-session steps."""
    db = infra.start_falkordb()
    infra.start_postgres()
    fdb, pgc = C.FALKOR["container"], C.PG["container"]
    if not os.path.exists(os.path.join(C.DATA, str(size), "meta.json")):  # generated files removed: same seed, same data
        run.log(f"{size}: regenerating the data files")
        scale.generate(size)
    with open(os.path.join(C.DATA, str(size), "meta.json")) as f:
        meta = json.load(f)
    array = next(json.loads(x) for x in open(result_path(size, "depth")) if '"gate"' in x)["storage_array"]
    starts = C.START_POINTS + [array]

    def step(name, fn):
        if os.path.exists(result_path(size, name)):
            run.log(f"{size} {name}: done earlier, skipped")
        else:
            run.status(f"{size}:{name}")
            run.log(f"{size} {name}: start")
            t = time.time()
            rows = fn()
            write_result(size, name, rows, {**ctx, "size": size, "step": name})
            run.step_times.append((size, time.time() - t))
            run.log(f"{size} {name}: done in {time.time() - t:.0f}s")
        run.done_steps += 1
        run.status(f"{size}:{name}")

    def ensure():
        if load_falkor.loaded(db, size) != meta["nodes"]:
            run.log(f"{size}: (re)loading FalkorDB with the bulk loader")
            load_falkor_bulk.load(db, size, run.log)
        if load_pg.loaded(size) != meta["nodes"]:
            run.log(f"{size}: (re)loading PostgreSQL")
            load_pg.load(size, run.log)
        return queries.falkor_graph(db, size), load_pg.connect()

    def bulk():
        with infra.Peak(fdb) as pk:
            r = load_falkor_bulk.load(db, size, run.log)
        return [{"system": "falkordb", **r, "peak_memory_bytes": pk.peak[fdb],
                 "graph_memory_mb": load_falkor.memory_mb(db, size)}]
    step("falkordb_bulk_load", bulk)

    def copy():
        with infra.Peak(pgc) as pk:
            r = load_pg.load(size, run.log)
        return [{"system": "postgres", "loader": "COPY", **r, "peak_memory_bytes": pk.peak[pgc]}]
    step("postgres_copy_load", copy)

    def depth_raw():
        g, conn = ensure()
        cur = conn.cursor()
        rows, failures = [], []
        for name in starts:
            for d in C.DEPTHS:
                ref = queries.falkor_reach(g, name, d)["impacted"]  # BFS over the pre-built impact edges
                fr, pr = queries.falkor_raw(g, name, d), queries.pg_raw(cur, size, name, d)
                if not (fr["impacted"] == pr["impacted"] == ref):
                    failures.append({"kind": "raw", "start": name, "depth": d, "diff": {
                        "only_impact_edges": sorted(ref - fr["impacted"] - pr["impacted"])[:20],
                        "only_falkordb": sorted(fr["impacted"] - pr["impacted"])[:20],
                        "only_postgres": sorted(pr["impacted"] - fr["impacted"])[:20]}})
                    continue
                for system in ("falkordb", "postgres"):
                    for _ in range(C.WARMUP):
                        queries.falkor_raw(g, name, d) if system == "falkordb" else queries.pg_raw(cur, size, name, d)
                    e2e, srv, nq = [], [], 0
                    ctr = fdb if system == "falkordb" else pgc
                    with infra.Peak(ctr) as pk:
                        for _ in range(C.RUNS):
                            t = time.perf_counter()
                            if system == "falkordb":
                                r = queries.falkor_raw(g, name, d)
                                srv.append(r["_server_ms"])
                                nq = r["_queries"]
                            else:
                                queries.pg_raw(cur, size, name, d)
                            e2e.append((time.perf_counter() - t) * 1000)
                        if system == "postgres":
                            srv = [queries.pg_server_ms(cur, size, "raw", name, d) for _ in range(C.RUNS)]
                    rows.append({"system": system, "kind": "raw", "start": name, "depth": d, **stats(e2e),
                                 "server_median_ms": median(srv), "server_p95_ms": stats(srv)["p95_ms"],
                                 "reached": len(ref), "round_trips": nq if system == "falkordb" else 1,
                                 "peak_memory_bytes": pk.peak[ctr]})
        if failures:
            write_result(size, "gate_failure_raw", failures, {**ctx, "size": size})
            raise RuntimeError(f"raw-relationship correctness gate failed for {len(failures)} cases at {size}")
        rows.append({"system": "both", "gate": "passed", "pairs_checked": len(starts) * len(C.DEPTHS),
                     "compared_with": "BFS over the pre-built IMPACTS_REAL edges", "storage_array": array})
        return rows
    step("depth_raw", depth_raw)

    def server_times():
        g, conn = ensure()
        cur = conn.cursor()
        rows = []
        for kind in ("radius", "reach"):
            for name in starts:
                for d in C.DEPTHS:
                    queries.falkor_server_ms(g, kind, name, d)
                    queries.pg_server_ms(cur, size, kind, name, d)
                    f = [queries.falkor_server_ms(g, kind, name, d) for _ in range(C.RUNS)]
                    p = [queries.pg_server_ms(cur, size, kind, name, d) for _ in range(C.RUNS)]
                    for system, xs, how in (("falkordb", f, "query statistics (run_time_ms)"),
                                            ("postgres", p, "EXPLAIN (ANALYZE, TIMING OFF) execution time")):
                        rows.append({"system": system, "kind": kind, "start": name, "depth": d,
                                     "server_median_ms": median(xs), "server_p95_ms": stats(xs)["p95_ms"],
                                     "measured_by": how})
        return rows
    step("server_times", server_times)

    def cleanup():
        if load_falkor.graph_name(size) in db.list_graphs():
            db.select_graph(load_falkor.graph_name(size)).delete()
        load_pg.drop(size)
        db.connection.execute_command("SAVE")
        return [{"system": "both", "dropped": True}]
    step("cleanup_v2", cleanup)


def run_size_v3(run, size, ctx, deadline=None):
    """Hybrid experiment; reuses the generated files (bulk loader + COPY when the databases were dropped)."""
    db = infra.start_falkordb()
    infra.start_postgres()
    with open(os.path.join(C.DATA, str(size), "meta.json")) as f:
        meta = json.load(f)
    array = next(json.loads(x) for x in open(result_path(size, "depth")) if '"gate"' in x)["storage_array"]

    def step(name, fn):
        if os.path.exists(result_path(size, name)):
            run.log(f"{size} {name}: done earlier, skipped")
        elif deadline and time.time() > deadline:
            run.log(f"{size} {name}: not run (past the measurement deadline)")
        else:
            run.status(f"{size}:{name}")
            run.log(f"{size} {name}: start")
            t = time.time()
            rows = fn()
            write_result(size, name, rows, {**ctx, "size": size, "step": name})
            run.log(f"{size} {name}: done in {time.time() - t:.0f}s")
        run.done_steps += 1
        run.status(f"{size}:{name}")

    def experiment():
        if load_falkor.loaded(db, size) != meta["nodes"]:
            run.log(f"{size}: reloading FalkorDB (bulk loader, existing files)")
            load_falkor_bulk.load(db, size, run.log)
        if load_pg.loaded(size) != meta["nodes"]:
            run.log(f"{size}: reloading PostgreSQL (COPY, existing files)")
            load_pg.load(size, run.log)
        g = queries.falkor_graph(db, size)
        conn = load_pg.connect()
        cur = conn.cursor()
        rows, failures = [], []
        for name in (array, "dc1-host-07"):
            for dp in (6, 10):
                ca, _ = hybrid.a(g, name, dp)
                cb = hybrid.b(cur, size, name, dp)
                cc, _, ids = hybrid.c(g, cur, size, name, dp)
                cd, _, paths = hybrid.d(g, name, dp)
                if not (ca == cb == cc == cd):
                    failures.append({"start": name, "depth": dp, "A": ca, "B": cb, "C": cc, "D": cd})
                    continue
                variants = {
                    "A_falkordb_full": lambda: hybrid.a(g, name, dp)[1],
                    "B_postgres_full": lambda: (hybrid.b(cur, size, name, dp), None)[1],
                    "C_hybrid": lambda: hybrid.c(g, cur, size, name, dp)[1],
                    "D_falkordb_counts": lambda: hybrid.d(g, name, dp)[1],
                }
                for vname, fn in variants.items():
                    for _ in range(C.WARMUP):
                        fn()
                    e2e, srv = [], []
                    for _ in range(C.RUNS):
                        t = time.perf_counter()
                        sv = fn()
                        e2e.append((time.perf_counter() - t) * 1000)
                        if sv is not None:
                            srv.append(sv)
                    if vname == "B_postgres_full":
                        srv = [queries.pg_server_ms(cur, size, "radius", name, dp) for _ in range(C.RUNS)]
                    pg_part = None
                    if vname == "C_hybrid":  # server time = FalkorDB BFS + PostgreSQL decoration
                        pg = [hybrid.c_pg_server_ms(cur, size, ids) for _ in range(C.RUNS)]
                        pg_part = median(pg)
                        srv = [x + y for x, y in zip(srv, pg)]
                    rows.append({"variant": vname, "start": name, "depth": dp, **stats(e2e),
                                 "server_median_ms": median(srv), "server_p95_ms": stats(srv)["p95_ms"],
                                 **({"server_postgres_part_ms": pg_part,
                                     "server_falkordb_part_ms": round(median(srv) - pg_part, 3)} if pg_part else {}),
                                 "counts": ca, "example_paths": len(paths) if vname == "D_falkordb_counts" else None})
        if failures:
            write_result(size, "gate_failure_hybrid", failures, {**ctx, "size": size})
            raise RuntimeError(f"hybrid correctness gate failed for {len(failures)} cases at {size}")
        rows.append({"variant": "gate", "gate": "passed", "cases": 4, "compared": "counts of A, B, C, D"})
        cur.close()
        conn.close()
        return rows
    step("hybrid", experiment)

    def cleanup():
        if load_falkor.graph_name(size) in db.list_graphs():
            db.select_graph(load_falkor.graph_name(size)).delete()
        load_pg.drop(size)
        return [{"system": "both", "dropped": True}]
    step("cleanup_v3", cleanup)


def run_size_v4(run, size, ctx, deadline=None):
    db = infra.start_falkordb()
    infra.start_postgres()
    with open(os.path.join(C.DATA, str(size), "meta.json")) as f:
        meta = json.load(f)
    array = next(json.loads(x) for x in open(result_path(size, "depth")) if '"gate"' in x)["storage_array"]

    def step(name, fn):
        if os.path.exists(result_path(size, name)):
            run.log(f"{size} {name}: done earlier, skipped")
        elif deadline and time.time() > deadline:
            run.log(f"{size} {name}: not run (past the measurement deadline)")
        else:
            run.log(f"{size} {name}: start")
            run.status(f"{size}:{name}")
            t = time.time()
            write_result(size, name, fn(), {**ctx, "size": size, "step": name})
            run.log(f"{size} {name}: done in {time.time() - t:.0f}s")
        run.done_steps += 1
        run.status(f"{size}:{name}")

    def check():
        if load_falkor.loaded(db, size) != meta["nodes"]:
            load_falkor_bulk.load(db, size, run.log)
        if load_pg.loaded(size) != meta["nodes"]:
            load_pg.load(size, run.log)
        g = queries.falkor_graph(db, size)
        conn = load_pg.connect()
        cur = conn.cursor()
        cur.execute("CREATE EXTENSION IF NOT EXISTS pg_stat_statements")
        from bench.load_pg import schema
        from ingest.config import TELEMETRY_SINKS
        rows = []
        for name in (array, "dc1-host-07"):
            for dp in (6, 10):
                ids = g.query(hybrid.FALKOR_IDS, {"name": name, "depth": dp, "rel": "IMPACTS_REAL"}).result_set[0][0]
                cases = {
                    "B_postgres_full": (queries.PG_RADIUS, {"name": name, "depth": dp, "rel": "IMPACTS_REAL", "term": "AFFECTS_REAL"}),
                    "reach": (queries.PG_REACH, {"name": name, "depth": dp, "rel": "IMPACTS_REAL"}),
                    "raw": (queries.PG_RAW, {"name": name, "depth": dp, "sinks": list(TELEMETRY_SINKS)}),
                    "C_postgres_part": (hybrid.PG_DECORATE, {"ids": ids, "term": "AFFECTS_REAL"}),
                }
                for case, (sql, params) in cases.items():
                    q = sql.format(s=schema(size))
                    for _ in range(C.WARMUP):
                        cur.execute(q, params, prepare=True)
                        cur.fetchall()
                    cur.execute("SELECT pg_stat_statements_reset()")
                    e2e = []
                    for _ in range(C.RUNS):
                        t = time.perf_counter()
                        cur.execute(q, params, prepare=True)
                        cur.fetchall()
                        e2e.append((time.perf_counter() - t) * 1000)
                    st = cur.execute("SELECT calls, mean_exec_time, stddev_exec_time, min_exec_time, max_exec_time "
                                     "FROM pg_stat_statements WHERE query LIKE %s AND query NOT LIKE %s "
                                     "ORDER BY calls DESC LIMIT 1", (f"%{schema(size)}.%", "%pg_stat%")).fetchone()
                    rows.append({"system": "postgres", "case": case, "start": name, "depth": dp, **stats(e2e),
                                 "server_mean_ms": round(st[1], 3) if st else None,
                                 "server_stddev_ms": round(st[2], 3) if st else None, "calls": st[0] if st else 0,
                                 "measured_by": "pg_stat_statements (prepared executions)"})
        cur.close()
        conn.close()
        return rows
    step("pg_stat_check", check)

    def cleanup():
        if load_falkor.graph_name(size) in db.list_graphs():
            db.select_graph(load_falkor.graph_name(size)).delete()
        load_pg.drop(size)
        return [{"system": "both", "dropped": True}]
    step("cleanup_v4", cleanup)


def run_size(run, size, ctx):
    db = infra.start_falkordb()
    infra.start_postgres()
    fdb, pgc = C.FALKOR["container"], C.PG["container"]
    meta_path = os.path.join(C.DATA, str(size), "meta.json")

    def step(name, fn):
        if os.path.exists(result_path(size, name)):
            run.log(f"{size} {name}: done earlier, skipped")
        else:
            run.status(f"{size}:{name}")
            run.log(f"{size} {name}: start")
            t = time.time()
            rows = fn()
            write_result(size, name, rows, {**ctx, "size": size, "step": name})
            run.step_times.append((size, time.time() - t))
            run.log(f"{size} {name}: done in {time.time() - t:.0f}s")
        run.done_steps += 1
        run.status(f"{size}:{name}")

    def gen():
        if not os.path.exists(scale.BASE):
            run.log("building the base world (project generator + loader)")
            scale.build_base(db)
        meta = scale.generate(size)
        return [{"system": "generator", **meta}]
    step("generate", gen)
    with open(meta_path) as f:
        meta = json.load(f)

    def ensure_falkor():
        if load_falkor.loaded(db, size) != meta["nodes"]:
            run.log(f"{size}: (re)loading FalkorDB")
            return load_falkor.load(db, size, run.log)
        return None

    def ensure_pg():
        if load_pg.loaded(size) != meta["nodes"]:
            run.log(f"{size}: (re)loading PostgreSQL")
            return load_pg.load(size, run.log)
        return None

    def f_load():
        with infra.Peak(fdb) as pk:
            r = load_falkor.load(db, size, run.log)
        return [{"system": "falkordb", **r, "peak_memory_bytes": pk.peak[fdb],
                 "graph_memory_mb": load_falkor.memory_mb(db, size)}]
    step("falkordb_load", f_load)
    ensure_falkor()

    def p_load():
        with infra.Peak(pgc) as pk:
            r = load_pg.load(size, run.log)
        return [{"system": "postgres", **r, "peak_memory_bytes": pk.peak[pgc]}]
    step("postgres_load", p_load)
    ensure_pg()

    g = queries.falkor_graph(db, size)
    conn = load_pg.connect()
    cur = conn.cursor()
    array = storage_array(db, size)
    starts = C.START_POINTS + [array]

    def depth():
        rows, failures = [], []
        kinds = {"radius": (lambda n, d: queries.falkor_radius(g, n, d), lambda n, d: queries.pg_radius(cur, size, n, d)),
                 "reach": (lambda n, d: queries.falkor_reach(g, n, d), lambda n, d: queries.pg_reach(cur, size, n, d))}
        for kind, (fq, pq) in kinds.items():
            for name in starts:
                for d in C.DEPTHS:
                    f_res, p_res = fq(name, d), pq(name, d)
                    if any(f_res[k] != p_res[k] for k in f_res):  # correctness gate: no timing, stop the run
                        failures.append({"kind": kind, "start": name, "depth": d, "diff": {
                            k: {"only_falkordb": sorted(f_res[k] - p_res[k])[:20],
                                "only_postgres": sorted(p_res[k] - f_res[k])[:20]} for k in f_res}})
                        continue
                    for system, fn in (("falkordb", fq), ("postgres", pq)):
                        for _ in range(C.WARMUP):
                            fn(name, d)
                        times = []
                        ctr = fdb if system == "falkordb" else pgc
                        with infra.Peak(ctr) as pk:
                            for _ in range(C.RUNS):
                                t = time.perf_counter()
                                fn(name, d)
                                times.append((time.perf_counter() - t) * 1000)
                        row = {"system": system, "kind": kind, "start": name, "depth": d, **stats(times),
                               "reached": len(f_res["impacted"]), "hospitals": len(f_res.get("hospitals", ())),
                               "peak_memory_bytes": pk.peak[ctr]}
                        if system == "falkordb" and kind == "radius":  # server-side time, without client parsing
                            from graph.queries import RADIUS
                            row["server_ms"] = g.query(RADIUS % {"term": "AFFECTS_REAL"},
                                                       {"name": name, "depth": d, "rel": "IMPACTS_REAL"}).run_time_ms
                        rows.append(row)
        if failures:
            write_result(size, "gate_failure", failures, {**ctx, "size": size})
            raise RuntimeError(f"correctness gate failed for {len(failures)} (kind, start, depth) cases at {size}")
        rows.append({"system": "postgres", "explain": queries.pg_explain(cur, size, "dc1-host-07", 10),
                     "gate": "passed", "pairs_checked": 2 * len(starts) * len(C.DEPTHS), "storage_array": array})
        return rows
    step("depth", depth)

    def review():
        rows = []
        f = lambda nm: queries.change_review(lambda n, d, l: queries.falkor_radius(g, n, d, l), nm)
        p = lambda nm: queries.change_review(lambda n, d, l: queries.pg_radius(cur, size, n, d, l), nm)
        fr, pr = f("dc1-host-07"), p("dc1-host-07")
        if fr != pr:
            raise RuntimeError(f"change review differs at {size}: {fr} vs {pr}")
        for system, fn in (("falkordb", f), ("postgres", p)):
            for _ in range(C.WARMUP):
                fn("dc1-host-07")
            times = []
            for _ in range(C.RUNS):
                t = time.perf_counter()
                fn("dc1-host-07")
                times.append((time.perf_counter() - t) * 1000)
            rows.append({"system": system, "change": "CHG-HEADLINE", "target": "dc1-host-07", **stats(times), **fr})
        return rows
    step("change_review", review)

    pool = random_starts(db, size)
    for system, ctr in (("falkordb", fdb), ("postgres", pgc)):
        def conc(system=system, ctr=ctr):
            rows = []
            for n in C.CLIENTS:
                with infra.Peak(ctr) as pk:
                    r = concurrency(system, size, pool, n)
                rows.append({"system": system, **r, "peak_memory_bytes": pk.peak[ctr], "start_pool": len(pool)})
                run.log(f"{size} {system} {n} clients: {r.get('throughput_qps')} q/s, p95 {r.get('p95_ms')} ms, "
                        f"errors {r['errors']}")
            return rows
        step(f"concurrency_{system}", conc)

    def resources():
        db.connection.execute_command("SAVE")
        rdb = int(infra.sh("docker", "exec", fdb, "sh", "-c", "stat -c %s /var/lib/falkordb/data/dump.rdb") or 0)
        return [{"system": "falkordb", "disk_bytes": rdb, "graph_memory_mb": load_falkor.memory_mb(db, size),
                 "container_memory_bytes": infra.mem_bytes(fdb)},
                {"system": "postgres", "disk_bytes": load_pg.disk_bytes(size),
                 "container_memory_bytes": infra.mem_bytes(pgc)}]
    step("resources", resources)

    def cleanup():  # free memory and disk for the next size; results stay
        cur.close()
        conn.close()
        g.delete()
        load_pg.drop(size)
        db.connection.execute_command("SAVE")
        return [{"system": "both", "dropped": True}]
    step("cleanup", cleanup)


DEADLINE = None  # epoch seconds; --deadline HH:MM local time stops starting new measurement steps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default=",".join(str(s) for s in C.SIZES))
    ap.add_argument("--deadline", help="HH:MM local time after which no new measurement step starts")
    a = ap.parse_args()
    global DEADLINE
    if a.deadline:
        hh, mm = map(int, a.deadline.split(":"))
        lt = time.localtime()
        DEADLINE = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hh, mm, 0, 0, 0, -1))
    sizes = [int(s) for s in a.sizes.split(",")]
    for f in ("DONE", "FAILED"):
        if os.path.exists(os.path.join(C.ROOT, f)):
            os.remove(os.path.join(C.ROOT, f))
    run = Run(sizes)
    ctx = {"hardware": infra.hardware(), "falkordb": {"image": C.FALKOR["image"], "args": C.FALKOR["args"]},
           "postgres": {"image": C.PG["image"], "settings": C.PG["settings"]},
           "limits": {"memory": C.MEMORY, "cpus": C.CPUS}, "runs": C.RUNS, "warmup": C.WARMUP}
    run.log(f"run start: sizes {sizes}")
    try:
        for size in sizes:
            if all(os.path.exists(result_path(size, s)) for s in STEPS_V1):
                run.done_steps += len(STEPS_V1)
            else:
                run_size(run, size, ctx)
            if all(os.path.exists(result_path(size, s)) for s in STEPS_V2):
                run.done_steps += len(STEPS_V2)
            else:
                run_size_v2(run, size, ctx)
            if all(os.path.exists(result_path(size, s)) for s in STEPS_V3):
                run.done_steps += len(STEPS_V3)
            else:
                run_size_v3(run, size, ctx, deadline=DEADLINE)
            run_size_v4(run, size, ctx, deadline=DEADLINE)
        run.status("finished", "done")
        open(os.path.join(C.ROOT, "DONE"), "w").write(time.strftime("%Y-%m-%dT%H:%M:%S\n"))
        run.log("run finished")
        notify("Benchmark finished. See bench/STATUS.json")
    except Exception as e:
        run.last_error = f"{type(e).__name__}: {e}"
        run.log("FAILED: " + traceback.format_exc())
        run.status("failed", "failed")
        open(os.path.join(C.ROOT, "FAILED"), "w").write(run.last_error + "\n")
        notify("Benchmark FAILED. See bench/logs/run.log")
        sys.exit(1)


if __name__ == "__main__":
    main()
