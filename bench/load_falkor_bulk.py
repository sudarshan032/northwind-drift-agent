"""Load a size with FalkorDB's bulk loader (`falkordb-bulk-insert`, GRAPH.BULK), for the load comparison with
PostgreSQL COPY. Same nodes, labels, properties and edges as bench/load_falkor.py; then the same indexes,
impact layer and impact-edge export (load_falkor.finish).

Steps timed separately: CSV conversion to the loader's typed per-label / per-type files, the bulk insert,
index creation, the impact-layer build.
"""

import csv
import os
import shutil
import subprocess
import sys
import time

from bench import config as C
from bench.load_falkor import graph_name, finish
from bench.scale import NODE_PROPS

TYPES = {"count": "INT"}


def convert(size):
    d = os.path.join(C.DATA, str(size))
    out = os.path.join(d, "bulk")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    files, writers = {}, {}
    header = ["bid:ID"] + [f"{p}:{TYPES.get(p, 'STRING')}" for p in NODE_PROPS]
    with open(os.path.join(d, "nodes.csv"), newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lbl = r["labels"]
            if lbl not in writers:
                fh = open(os.path.join(out, f"N_{lbl.replace(':', '_')}.csv"), "w", newline="", encoding="utf-8")
                files[("N", lbl)] = fh
                writers[lbl] = csv.writer(fh)
                writers[lbl].writerow(header)
            writers[lbl].writerow([r["bid"]] + [r[p] for p in NODE_PROPS])
    ew = {}
    with open(os.path.join(d, "edges.csv"), newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        next(rd)
        for a, b, t, fo, hc in rd:
            if t not in ew:
                fh = open(os.path.join(out, f"R_{t}.csv"), "w", newline="", encoding="utf-8")
                files[("R", t)] = fh
                ew[t] = csv.writer(fh)
                ew[t].writerow([":START_ID", ":END_ID", "failover:BOOL", "health_check:BOOL"])
            ew[t].writerow([a, b, fo.lower(), hc.lower()])
    for fh in files.values():
        fh.close()
    return out, sorted(lbl for kind, lbl in files if kind == "N"), sorted(t for kind, t in files if kind == "R")


def load(db, size, log=print):
    name = graph_name(size)
    if name in db.list_graphs():
        db.select_graph(name).delete()
    t0 = time.perf_counter()
    out, labels, types = convert(size)
    conv_s = time.perf_counter() - t0
    cmd = [os.path.join(os.path.dirname(sys.executable), "falkordb-bulk-insert"), name, "-u",
           f"redis://localhost:{C.FALKOR['port']}", "-d", "-j", "INTEGER"]
    for lbl in labels:
        cmd += ["-N", lbl, os.path.join(out, f"N_{lbl.replace(':', '_')}.csv")]
    for t in types:
        cmd += ["-R", t, os.path.join(out, f"R_{t}.csv")]
    t1 = time.perf_counter()
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"bulk insert failed: {p.stderr[-800:]}")
    bulk_s = time.perf_counter() - t1
    log(f"falkordb bulk {size}: inserted in {bulk_s:.1f}s")
    g = db.select_graph(name)
    fin = finish(g, size, indexes_for=sorted({lbl.split(':')[0] for lbl in labels}))
    n = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
    m = g.query("MATCH ()-[e]->() WHERE NOT type(e) STARTS WITH 'IMPACTS' AND NOT type(e) STARTS WITH 'AFFECTS' "
                "RETURN count(e)").result_set[0][0]
    shutil.rmtree(out, ignore_errors=True)
    return {"nodes": n, "edges": m, "impact_edges": fin["impact_edges"], "loader": "falkordb-bulk-insert", "seconds": {
        "convert_csv": round(conv_s, 1), "bulk_insert": round(bulk_s, 1), "indexes": fin["indexes"],
        "impact_build": fin["impact_build"], "export_impact_edges": fin["export_impact_edges"],
        "load_total": round(bulk_s + fin["indexes"] + fin["impact_build"], 1),
        "load_total_with_convert": round(conv_s + bulk_s + fin["indexes"] + fin["impact_build"], 1)}}
