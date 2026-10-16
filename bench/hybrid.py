"""Hybrid experiment (owner's request, 16 Oct): who should compute what in the full blast-radius question.

A  FalkorDB full radius: the project's RADIUS query (traversal plus consumers, departments, hospitals).
B  PostgreSQL full radius: the recursive CTE with the same lookups.
C  Hybrid: FalkorDB's BFS returns the impacted node ids only; PostgreSQL joins them to node attributes and
   computes consumers, departments and hospitals.
D  FalkorDB counts only: the same full radius computed in FalkorDB, returning only the four counts plus at
   most 10 example paths (shortest impact path from the start to the first 10 impacted components).

Gate: the four counts (impacted, consumers, departments, hospitals) are identical for A, B, C and D.
"""

from bench.load_pg import schema
from bench.queries import LAYERS, pg_radius

FALKOR_IDS = ("MATCH (f:Component {name: $name}) CALL algo.bfs(f, $depth, $rel) YIELD nodes "
              "RETURN [n IN nodes | n.bid]")

PG_DECORATE = """
WITH hit AS (SELECT DISTINCT unnest(%(ids)s::bigint[]) AS id),
term AS (SELECT DISTINCT e.dst AS id FROM hit JOIN {s}.edge e ON e.src = hit.id AND e.type = %(term)s),
depts AS (SELECT t.id FROM term t JOIN {s}.node n ON n.id = t.id AND n.kind = 'Department'
          UNION SELECT e.dst FROM hit JOIN {s}.edge e ON e.src = hit.id AND e.type = 'IN_DEPARTMENT'),
sites AS (SELECT e.src AS id FROM depts JOIN {s}.edge e ON e.dst = depts.id AND e.type = 'CONTAINS'
          UNION SELECT e.dst FROM hit JOIN {s}.edge e ON e.src = hit.id AND e.type = 'LOCATED_AT')
SELECT (SELECT coalesce(array_agg(n.name), '{{}}') FROM hit JOIN {s}.node n USING (id)),
       (SELECT coalesce(array_agg(n.name), '{{}}') FROM term JOIN {s}.node n USING (id) WHERE n.kind <> 'Department'),
       (SELECT coalesce(array_agg(n.name), '{{}}') FROM depts JOIN {s}.node n USING (id)),
       (SELECT coalesce(array_agg(n.name), '{{}}') FROM sites JOIN {s}.node n USING (id) WHERE n.type LIKE '%%hospital')
"""

COUNTS = """
MATCH (f:Component {name: $name})
CALL algo.bfs(f, $depth, $rel) YIELD nodes
UNWIND nodes AS n
WITH DISTINCT n
OPTIONAL MATCH (n)-[:%(term)s]->(t)
OPTIONAL MATCH (n)-[:IN_DEPARTMENT]->(nd:Department)
OPTIONAL MATCH (n)-[:LOCATED_AT]->(ns:Site)
UNWIND [CASE WHEN t:Department THEN t END, nd] AS d
OPTIONAL MATCH (ds:Site)-[:CONTAINS]->(d)
UNWIND [ds, ns] AS s
WITH count(DISTINCT n) AS impacted, count(DISTINCT CASE WHEN NOT t:Department THEN t END) AS consumers,
     count(DISTINCT d) AS departments, count(DISTINCT CASE WHEN s.type ENDS WITH 'hospital' THEN s END) AS hospitals,
     collect(DISTINCT n)[0..10] AS ex
MATCH (f:Component {name: $name})
UNWIND ex AS x
WITH impacted, consumers, departments, hospitals, shortestPath((f)-[:%(rel)s*1..10]->(x)) AS p
RETURN impacted, consumers, departments, hospitals, collect([y IN nodes(p) | y.name]) AS paths
"""
# no impacted node -> no row: every count is then zero


def counts(res):
    return {k: len(res[k]) for k in ("impacted", "consumers", "departments", "hospitals")}


def a(g, name, depth):
    """(counts, server_ms) for A. falkor_radius is reused for the result; one more query gives the server time."""
    from graph.queries import RADIUS
    r = g.query(RADIUS % {"term": "AFFECTS_REAL"}, {"name": name, "depth": depth, "rel": "IMPACTS_REAL"})
    if not r.result_set:
        return {"impacted": 0, "consumers": 0, "departments": 0, "hospitals": 0}, r.run_time_ms
    imp, cons, depts, hosp = r.result_set[0]
    return {"impacted": len({x["name"] for x in imp}), "consumers": len(set(cons)), "departments": len(set(depts)),
            "hospitals": len(set(hosp))}, r.run_time_ms


def b(cur, size, name, depth):
    return counts(pg_radius(cur, size, name, depth))


def c(g, cur, size, name, depth):
    """Hybrid: ids from FalkorDB, decoration in PostgreSQL. Returns counts and both server times."""
    r = g.query(FALKOR_IDS, {"name": name, "depth": depth, "rel": "IMPACTS_REAL"})
    ids = r.result_set[0][0] if r.result_set else []
    cur.execute(PG_DECORATE.format(s=schema(size)), {"ids": ids, "term": LAYERS["real"][1]}, prepare=True)
    imp, cons, depts, hosp = cur.fetchone()
    return ({"impacted": len(set(imp)), "consumers": len(set(cons)), "departments": len(set(depts)),
             "hospitals": len(set(hosp))}, r.run_time_ms, ids)


def c_pg_server_ms(cur, size, ids):
    cur.execute("EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, FORMAT JSON) " + PG_DECORATE.format(s=schema(size)),
                {"ids": ids, "term": LAYERS["real"][1]})
    return cur.fetchone()[0][0]["Execution Time"]


def d(g, name, depth):
    r = g.query(COUNTS % {"term": "AFFECTS_REAL", "rel": "IMPACTS_REAL"},
                {"name": name, "depth": depth, "rel": "IMPACTS_REAL"})
    if not r.result_set:
        return {"impacted": 0, "consumers": 0, "departments": 0, "hospitals": 0}, r.run_time_ms, []
    imp, cons, depts, hosp, paths = r.result_set[0]
    return ({"impacted": imp, "consumers": cons, "departments": depts, "hospitals": hosp}, r.run_time_ms,
            [p for p in paths if p])
