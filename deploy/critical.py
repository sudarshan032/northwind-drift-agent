"""Store the critical-nodes result on FalkorDB Cloud, computed on the local FalkorDB (run from a laptop).

    python -m deploy.critical [--data data_demo] [--graph northwind]

algo.betweenness closed the cloud connection and restarted the instance (2026-10-16), so the cloud never runs
it: the demo subset is loaded into the local FalkorDB (docker compose), the same query runs there, and the rows
go to the cloud graph as `(:Meta {key: 'critical', json})`, which graph.queries.critical_nodes reads first.
"""

import argparse
import json
import os

from deploy import env

TMP = "northwind_critical_tmp"


def compute(data):
    """On the local FalkorDB (docker compose, no auth), whatever FALKORDB_* the cloud steps have set."""
    saved = {k: os.environ.pop(k) for k in ("FALKORDB_USERNAME", "FALKORDB_PASSWORD", "FALKORDB_TLS",
                                            "FALKORDB_HOST", "FALKORDB_PORT") if k in os.environ}
    try:
        return _compute(data)
    finally:
        os.environ.update(saved)


def _compute(data):
    from falkordb import FalkorDB
    from graph import queries as Q
    from ingest.load import Loader
    local = FalkorDB(host="localhost", port=6379)
    if TMP in local.list_graphs():
        local.select_graph(TMP).delete()
    Loader(data, TMP, host="localhost", port=6379).load()
    g = local.select_graph(TMP)
    try:
        return Q.critical_nodes(g), g.query("MATCH (c:Component) RETURN count(c)").result_set[0][0]
    finally:
        g.delete()


def store(g, rows):
    g.query("MERGE (m:Meta {key: 'critical'}) SET m.json = $j", {"j": json.dumps(rows)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data_demo")
    ap.add_argument("--graph", default=os.environ.get("NORTHWIND_GRAPH", "northwind"))
    a = ap.parse_args()
    rows, n_local = compute(a.data)
    env.load()
    from graph.connection import connect
    g = connect().select_graph(a.graph)
    n_cloud = g.query("MATCH (c:Component) RETURN count(c)").result_set[0][0]
    if n_cloud != n_local:
        raise SystemExit(f"cloud graph has {n_cloud} components, local copy {n_local}: not the same data")
    store(g, rows)
    print(json.dumps({"components": n_cloud, "stored": rows[:3], "rows": len(rows)}, indent=1))


if __name__ == "__main__":
    main()
