"""Three sanity queries against a loaded graph (RUN_PROMPT 7.4).

    python -m tests.helpers.sanity_queries --graph northwind --data data/
"""

import argparse
import json
import os

from falkordb import FalkorDB


def run(graph, data):
    g = FalkorDB(host=os.environ.get("FALKORDB_HOST", "localhost"),
                 port=int(os.environ.get("FALKORDB_PORT", 6379))).select_graph(graph)
    labels = g.query("MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) ORDER BY l").result_set
    path = g.query(
        "MATCH p = (i:Interface {name: 'ORU-CRIT-H03'})-[c:OBSERVED_CALLS]->(d:Component {name: 'lrc-db-01'})"
        "-[:RUNS_ON]->(v:VM)-[:RUNS_ON]->(h:Host) "
        "RETURN [n IN nodes(p) | n.name], [r IN relationships(p) | type(r)], c.count, c.days_seen, c.last_seen"
    ).result_set
    unmatched = g.query("MATCH (d:Discovered) WHERE NOT (d)-[:IDENTIFIES]->(:Component) "
                        "RETURN count(d)").result_set[0][0]
    with open(os.path.join(data, "ground_truth.json")) as f:
        d6 = sum(1 for e in json.load(f) if e["type"] == "D6")
    return {"nodes_per_label": dict(labels), "oru_crit_h03_to_lrc_db_01": path,
            "discovered_without_identifies": unmatched, "d6_count": d6}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="northwind")
    ap.add_argument("--data", default="data")
    a = ap.parse_args()
    print(json.dumps(run(a.graph, a.data), indent=1))
