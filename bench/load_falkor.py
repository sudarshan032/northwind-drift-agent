"""Load a generated size into FalkorDB, build the impact layer with the project's rules, and export the
materialised impact edges for PostgreSQL (BENCH_SPEC 3: "same edges").

Same labels, relationship types and indexes as the project (ingest/load.py INDEXES), plus an integer `bid`
on every node so edges can be matched by index. The project loader itself reads connector output files
(388 MB for the 49k world), so the scaled sizes use this lean loader: nodes and edges in UNWIND batches.
"""

import csv
import json
import os
import time
from array import array

from bench import config as C
from bench.scale import NODE_PROPS
from graph import impact
from ingest.load import INDEXES

BATCH = 5000
IMPACT_TYPES = ["IMPACTS_REAL", "IMPACTS_DECLARED", "AFFECTS_REAL", "AFFECTS_DECLARED"]
INT_PROPS = {"count"}


def graph_name(size):
    return f"bench_{size}"


def load(db, size, log=print):
    d = os.path.join(C.DATA, str(size))
    name = graph_name(size)
    if name in db.list_graphs():
        db.select_graph(name).delete()
    g = db.select_graph(name)
    t0 = time.perf_counter()
    labels_seen = set()
    prim = array("B")  # bid -> primary label code
    codes = {}
    groups = {}
    n = 0

    def flush(lbl):
        rows = groups.pop(lbl, [])
        if rows:
            g.query(f"UNWIND $rows AS r CREATE (n:{lbl}) SET n = r", {"rows": rows})

    with open(os.path.join(d, "nodes.csv"), newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            lbl = r["labels"]
            p0 = lbl.split(":")[0]
            if p0 not in codes:
                codes[p0] = len(codes)
                g.query(f"CREATE INDEX FOR (n:{p0}) ON (n.bid)")
            if lbl not in labels_seen:
                labels_seen.add(lbl)
            b = int(r["bid"])
            while len(prim) <= b:
                prim.append(255)
            prim[b] = codes[p0]
            props = {"bid": b}
            for k in NODE_PROPS:
                v = r[k]
                if v != "":
                    props[k] = int(float(v)) if k in INT_PROPS else v
            groups.setdefault(lbl, []).append(props)
            if len(groups[lbl]) >= BATCH:
                flush(lbl)
            n += 1
            if n % 500_000 == 0:
                log(f"falkordb {size}: {n:,} nodes")
    for lbl in list(groups):
        flush(lbl)
    for label, prop in INDEXES:
        if label in codes:
            g.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
    nodes_s = time.perf_counter() - t0
    names = {v: k for k, v in codes.items()}
    egroups, m = {}, 0

    def eflush(k):
        rows = egroups.pop(k, [])
        if rows:
            sl, dl, t = k
            g.query(f"UNWIND $rows AS r MATCH (a:{sl} {{bid: r[0]}}) MATCH (b:{dl} {{bid: r[1]}}) "
                    f"CREATE (a)-[e:{t}]->(b) SET e.failover = r[2], e.health_check = r[3]", {"rows": rows})

    def flag(v):
        return None if v == "" else v == "True"

    with open(os.path.join(d, "edges.csv"), newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        next(rd)
        for a, b, t, fo, hc in rd:
            a, b = int(a), int(b)
            k = (names[prim[a]], names[prim[b]], t)
            egroups.setdefault(k, []).append([a, b, flag(fo), flag(hc)])
            if len(egroups[k]) >= BATCH:
                eflush(k)
            m += 1
            if m % 1_000_000 == 0:
                log(f"falkordb {size}: {m:,} edges")
    for k in list(egroups):
        eflush(k)
    edges_s = time.perf_counter() - t0 - nodes_s
    t1 = time.perf_counter()
    impact.build(g)  # the project's impact rules, materialised in Cypher
    impact_s = time.perf_counter() - t1
    t2 = time.perf_counter()
    counts = {}
    with open(os.path.join(d, "impact_edges.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["src", "dst", "type"])
        for t in IMPACT_TYPES:
            rows = g.query(f"MATCH (a)-[:{t}]->(b) RETURN a.bid, b.bid").result_set
            counts[t] = len(rows)
            w.writerows([a, b, t] for a, b in rows)
    export_s = time.perf_counter() - t2
    return {"nodes": n, "edges": m, "impact_edges": counts, "seconds": {
        "nodes": round(nodes_s, 1), "edges": round(edges_s, 1), "impact_build": round(impact_s, 1),
        "export_impact_edges": round(export_s, 1), "load_total": round(nodes_s + edges_s + impact_s, 1)}}


def loaded(db, size):
    """Node count of the loaded graph (0 if absent): resumed runs reload when it does not match."""
    name = graph_name(size)
    if name not in db.list_graphs():
        return 0
    return db.select_graph(name).query("MATCH (n) RETURN count(n)").result_set[0][0]


def memory_mb(db, size):
    r = db.connection.execute_command("GRAPH.MEMORY", "USAGE", graph_name(size))
    return dict(zip(r[0::2], r[1::2]))["total_graph_sz_mb"]


if __name__ == "__main__":
    import sys
    from bench.infra import start_falkordb
    print(json.dumps(load(start_falkordb(), int(sys.argv[1])), indent=1))
