"""One-time load of the demo subset into FalkorDB Cloud (run from a laptop).

    python -m deploy.load_cloud [--data data_demo] [--graph northwind] [--check-only]

Reads FALKORDB_HOST / PORT / USERNAME / PASSWORD (and optional FALKORDB_TLS)
from .env or the environment; never prints them. Steps:
1. connect with TLS and without, and keep whichever the instance accepts;
2. load the demo subset (all 51 hospitals, the full drift catalogue) fresh;
3. run triage with the cached AI answers so findings exist on first visit;
4. measure memory (GRAPH.MEMORY USAGE per graph, INFO memory) against the
   free tier's 100 MB, before and after the heaviest demo queries.
"""

import argparse
import json
import os
import time

from deploy import env

FREE_TIER_MB = 100


def try_connect():
    from graph.connection import connect
    out = {}
    for tls in ("false", "true"):
        os.environ["FALKORDB_TLS"] = tls
        t = time.perf_counter()
        try:
            db = connect(timeout=10)
            db.connection.ping()
            out[tls] = {"ok": True, "connect_ms": round((time.perf_counter() - t) * 1000)}
        except Exception as e:  # message may name the host, never the password
            out[tls] = {"ok": False, "error": type(e).__name__}
    use = "true" if out["true"]["ok"] else "false" if out["false"]["ok"] else None
    if use is None:
        raise SystemExit(f"cannot connect: {out}")
    os.environ["FALKORDB_TLS"] = use
    return out, use == "true"


def memory(db, graphs):
    info = db.connection.info("memory")
    per = {}
    for g in graphs:
        if g in db.list_graphs():
            r = db.connection.execute_command("GRAPH.MEMORY", "USAGE", g)
            per[g] = dict(zip(r[0::2], r[1::2]))["total_graph_sz_mb"]
    return {"graphs_mb": per, "used_memory_mb": round(info["used_memory"] / 2**20, 1),
            "used_memory_peak_mb": round(info["used_memory_peak"] / 2**20, 1),
            "maxmemory_mb": round(info.get("maxmemory", 0) / 2**20, 1) or None}


def peak(fn):
    """Run fn while sampling used_memory every 20 ms on a second connection; return (result, peak MB)."""
    import threading
    from graph.connection import connect
    mon, out, stop = connect(), [], threading.Event()

    def watch():
        while not stop.is_set():
            out.append(mon.connection.info("memory")["used_memory"] / 2**20)
            time.sleep(0.02)
    th = threading.Thread(target=watch)
    th.start()
    try:
        res = fn()
    finally:
        time.sleep(0.2)
        stop.set()
        th.join()
    return res, round(max(out), 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data_demo")
    ap.add_argument("--graph", default=os.environ.get("NORTHWIND_GRAPH", "northwind"))
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()
    env.load()
    conn, tls = try_connect()
    report = {"connection": conn, "tls": tls}
    from graph.connection import connect
    db = connect()
    report["memory_before"] = memory(db, [a.graph])
    if a.check_only:
        print(json.dumps(report, indent=1))
        return
    from agent import flows
    from agent.llm import CachedLLM
    from ingest.load import Loader
    for g in db.list_graphs():  # the free tier holds only this demo
        db.select_graph(g).delete()
    t = time.perf_counter()
    ld = Loader(a.data, a.graph, os.environ["FALKORDB_HOST"], int(os.environ["FALKORDB_PORT"]))
    _, load_peak = peak(ld.load)
    stats = ld.counts()
    report["load"] = {"nodes": stats["total_nodes"], "edges": stats["total_edges"],
                      "seconds": round(time.perf_counter() - t, 1), "peak_mb": load_peak}
    g = db.select_graph(a.graph)
    t = time.perf_counter()
    tri, tri_peak = peak(lambda: flows.triage(g, CachedLLM()))
    report["triage"] = {"findings": tri["total"], "notice": tri["notice"], "seconds": round(time.perf_counter() - t, 1),
                        "peak_mb": tri_peak,
                        "explained_by_llm": sum(1 for f in tri["top"] if f["explained_by"] == "llm")}
    report["memory_loaded"] = memory(db, [a.graph])
    timings = {}
    for cid in ("CHG-HEADLINE", "CHG0031513"):
        t = time.perf_counter()
        r, pk = peak(lambda: flows.change_review(g, cid, CachedLLM()))
        timings[cid] = {"peak_mb": pk, "risk": r["facts"]["risk"], "real": r["facts"]["real"], "declared": r["facts"]["declared"],
                        "cached_ai": r["notice"] is None, "seconds": round(time.perf_counter() - t, 2)}
    report["reviews"] = timings
    report["memory_after_queries"] = memory(db, [a.graph])
    serving = max([tri_peak] + [r["peak_mb"] for r in timings.values()])
    report["fits_free_tier"] = {"limit_mb": FREE_TIER_MB, "steady_mb": report["memory_after_queries"]["used_memory_mb"],
                                "serving_peak_mb": serving, "headroom_mb": round(FREE_TIER_MB - serving, 1),
                                "one_time_load_peak_mb": load_peak}
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
