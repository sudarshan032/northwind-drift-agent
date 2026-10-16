"""Paired client runs (owner's request, 16 Oct): the same FalkorDB measurement with redis-py's pure-Python reply
parser and with hiredis, back to back in one session, so both see the same machine, container and graph.

The parser is chosen per child process: blocking the `hiredis` module before redis-py is first imported makes
redis-py fall back to its Python parser. Every child checks that it got the parser it asked for.
"""

import os
import sys
import time


def _parser(name):
    if name == "python":
        sys.modules["hiredis"] = None
    import redis.utils
    if redis.utils.HIREDIS_AVAILABLE != (name == "hiredis"):
        raise RuntimeError(f"wanted the {name} parser, redis-py has hiredis={redis.utils.HIREDIS_AVAILABLE}")


def client_proc(parser, *args):
    try:
        _parser(parser)
    except RuntimeError as e:  # report instead of hanging the parent's queue
        args[-1].put({"latencies": [], "errors": [str(e)]})
        return
    from bench.run import _client_proc
    _client_proc(*args)


def hybrid_proc(parser, size, cases, out):
    """Hybrid C and counts-only D for each (start, depth): counts, then RUNS timed runs after WARMUP."""
    try:
        _parser(parser)
    except RuntimeError as e:
        out.put([{"client": parser, "error": str(e)}])
        return
    from bench import config as C, hybrid, load_pg, queries, run as R
    from bench.infra import start_falkordb
    g, conn = queries.falkor_graph(start_falkordb(), size), load_pg.connect()
    cur = conn.cursor()
    rows = []
    for name, dp in cases:
        cc, _, ids = hybrid.c(g, cur, size, name, dp)
        cd, _, paths = hybrid.d(g, name, dp)
        for vname, fn in (("C_hybrid", lambda: hybrid.c(g, cur, size, name, dp)[1]),
                          ("D_falkordb_counts", lambda: hybrid.d(g, name, dp)[1])):
            for _ in range(C.WARMUP):
                fn()
            load = os.getloadavg()[0]
            e2e, srv = [], []
            for _ in range(C.RUNS):
                t = time.perf_counter()
                srv.append(fn())
                e2e.append((time.perf_counter() - t) * 1000)
            extra = {}
            if vname == "C_hybrid":
                pg = [hybrid.c_pg_server_ms(cur, size, ids) for _ in range(C.RUNS)]
                extra["server_postgres_part_ms"] = R.median(pg)
                srv = [x + y for x, y in zip(srv, pg)]
            rows.append({"client": parser, "variant": vname, "start": name, "depth": dp, **R.stats(e2e),
                         "server_median_ms": R.median(srv), **extra, "counts": cc, "counts_d": cd,
                         "example_paths": len(paths) if vname == "D_falkordb_counts" else None,
                         "load_avg_1m": round(load, 2)})
    conn.close()
    out.put(rows)
