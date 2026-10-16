"""The benchmark questions on both systems, computing the same thing (BENCH_SPEC 3 and 4).

Two query kinds. "radius": the full question. FalkorDB: the project's real-radius query (graph/queries.py RADIUS): one `algo.bfs` call over the
materialised IMPACTS_* edges up to the depth, then the terminal AFFECTS_* edges, departments and hospitals.

PostgreSQL: a recursive CTE over the same IMPACTS_* edges. Each round of the recursion is one self-join of
the frontier with `edge` (index on (src, type)); `UNION` removes duplicate (node, depth) rows; a depth
counter caps the rounds. The same terminal, department and hospital lookups follow.
"reach": the traversal alone (the set of names reached), on both systems.
"""

from bench.load_falkor import graph_name
from bench.load_pg import schema
from graph.queries import RADIUS

LAYERS = {"real": ("IMPACTS_REAL", "AFFECTS_REAL"), "declared": ("IMPACTS_DECLARED", "AFFECTS_DECLARED")}

PG_RADIUS = """
WITH RECURSIVE r(id, d) AS (
    SELECT id, 0 FROM {s}.node WHERE name = %(name)s AND kind = 'Component'
  UNION
    SELECT e.dst, r.d + 1 FROM r JOIN {s}.edge e ON e.src = r.id AND e.type = %(rel)s WHERE r.d < %(depth)s
),
start AS (SELECT id FROM {s}.node WHERE name = %(name)s AND kind = 'Component'),
hit AS (SELECT DISTINCT id FROM r WHERE id NOT IN (SELECT id FROM start)),
term AS (SELECT DISTINCT e.dst AS id FROM hit JOIN {s}.edge e ON e.src = hit.id AND e.type = %(term)s),
depts AS (SELECT t.id FROM term t JOIN {s}.node n ON n.id = t.id AND n.kind = 'Department'
          UNION SELECT e.dst FROM hit JOIN {s}.edge e ON e.src = hit.id AND e.type = 'IN_DEPARTMENT'),
sites AS (SELECT e.src AS id FROM depts JOIN {s}.edge e ON e.dst = depts.id AND e.type = 'CONTAINS'
          UNION SELECT e.dst FROM hit JOIN {s}.edge e ON e.src = hit.id AND e.type = 'LOCATED_AT')
SELECT (SELECT coalesce(array_agg(n.name), '{{}}') FROM hit JOIN {s}.node n USING (id)),
       (SELECT coalesce(array_agg(n.name), '{{}}') FROM term JOIN {s}.node n USING (id) WHERE n.kind <> 'Department'),
       (SELECT coalesce(array_agg(n.name), '{{}}') FROM depts JOIN {s}.node n USING (id)),
       (SELECT coalesce(array_agg(n.name), '{{}}') FROM sites JOIN {s}.node n USING (id)
         WHERE n.type LIKE '%%hospital')
"""


FALKOR_REACH = ("MATCH (f:Component {name: $name}) CALL algo.bfs(f, $depth, $rel) YIELD nodes "
                "RETURN [n IN nodes | n.name]")
PG_REACH = """
WITH RECURSIVE r(id, d) AS (
    SELECT id, 0 FROM {s}.node WHERE name = %(name)s AND kind = 'Component'
  UNION
    SELECT e.dst, r.d + 1 FROM r JOIN {s}.edge e ON e.src = r.id AND e.type = %(rel)s WHERE r.d < %(depth)s
)
SELECT coalesce(array_agg(n.name), '{{}}') FROM (SELECT DISTINCT id FROM r) h JOIN {s}.node n USING (id)
WHERE n.name <> %(name)s OR n.kind <> 'Component'
"""


def falkor_reach(g, name, depth, layer="real"):
    """The traversal alone: names reached by one BFS call (no decoration)."""
    rows = g.query(FALKOR_REACH, {"name": name, "depth": depth, "rel": LAYERS[layer][0]}).result_set
    return {"impacted": set(rows[0][0]) if rows else set()}


def pg_reach(cur, size, name, depth, layer="real"):
    cur.execute(PG_REACH.format(s=schema(size)), {"name": name, "depth": depth, "rel": LAYERS[layer][0]},
                prepare=True)
    return {"impacted": set(cur.fetchone()[0])}


def falkor_radius(g, name, depth, layer="real"):
    rel, term = LAYERS[layer]
    rows = g.query(RADIUS % {"term": term}, {"name": name, "depth": depth, "rel": rel}).result_set
    if not rows:
        return {"impacted": set(), "consumers": set(), "departments": set(), "hospitals": set()}
    imp, cons, depts, hosp = rows[0]
    return {"impacted": {x["name"] for x in imp}, "consumers": set(cons), "departments": set(depts),
            "hospitals": set(hosp)}


def pg_radius(cur, size, name, depth, layer="real"):
    rel, term = LAYERS[layer]
    cur.execute(PG_RADIUS.format(s=schema(size)), {"name": name, "depth": depth, "rel": rel, "term": term},
                prepare=True)
    imp, cons, depts, hosp = cur.fetchone()
    return {"impacted": set(imp), "consumers": set(cons), "departments": set(depts), "hospitals": set(hosp)}


def pg_explain(cur, size, name, depth):
    rel, term = LAYERS["real"]
    cur.execute("EXPLAIN (ANALYZE, BUFFERS) " + PG_RADIUS.format(s=schema(size)),
                {"name": name, "depth": depth, "rel": rel, "term": term})
    return "\n".join(r[0] for r in cur.fetchall())


def change_review(radius, name):
    """Declared vs real radius of one target and the difference (the change-review core)."""
    dec, real = radius(name, 10, "declared"), radius(name, 10, "real")
    return {"declared": len(dec["impacted"]), "real": len(real["impacted"]),
            "hidden": len(real["impacted"] - dec["impacted"]),
            "hospitals_declared": len(dec["hospitals"]), "hospitals_real": len(real["hospitals"])}


def falkor_graph(db, size):
    return db.select_graph(graph_name(size))


# ---------------------------------------------------------------- raw relationships (no pre-built impact edges)
# The real-layer impact rules of graph/impact.py, applied per relationship type while walking:
#   runs_on, stores_on, netflow : (b)-[:RUNS_ON|STORES_ON|OBSERVED_NETFLOW]->(a)         a fails -> b impacted
#   hosts_route                 : (a:Application)-[:CONTAINS]->(b:Interface)
#   contains                    : (b:BusinessService|ServiceOffering)-[:CONTAINS]->(a)
#   calls                       : (b)-[e:OBSERVED_CALLS]->(a), a not an Interface, not a telemetry sink, not a health check
#   feeds_route                 : (a)-[e:OBSERVED_CALLS]->(b:Interface), not a health check
# FalkorDB has no BFS procedure that mixes relationship types and directions with per-hop conditions, so the
# best shape is one Cypher query per hop (frontier passed as internal ids, sought directly) (the frontier in, the next frontier out, all rules in
# one UNION); the client keeps the visited set. PostgreSQL does it in one recursive CTE whose recursive term
# joins the frontier once with a LATERAL union of the rules.

from ingest.config import TELEMETRY_SINKS  # noqa: E402

FALKOR_HOP = """
UNWIND $f AS i MATCH (a) WHERE id(a) = i WITH a MATCH (a)<-[:RUNS_ON|STORES_ON|OBSERVED_NETFLOW]-(b) WHERE b <> a
RETURN DISTINCT id(b) AS b
UNION
UNWIND $f AS i MATCH (a) WHERE id(a) = i WITH a WHERE a:Application MATCH (a)-[:CONTAINS]->(b:Interface) RETURN DISTINCT id(b) AS b
UNION
UNWIND $f AS i MATCH (a) WHERE id(a) = i WITH a MATCH (a)<-[:CONTAINS]-(b:BusinessService) RETURN DISTINCT id(b) AS b
UNION
UNWIND $f AS i MATCH (a) WHERE id(a) = i WITH a MATCH (a)<-[:CONTAINS]-(b:ServiceOffering) RETURN DISTINCT id(b) AS b
UNION
UNWIND $f AS i MATCH (a) WHERE id(a) = i WITH a WHERE NOT a:Interface AND NOT a.name IN $sinks
MATCH (a)<-[e:OBSERVED_CALLS]-(b) WHERE b <> a AND NOT coalesce(e.health_check, false) RETURN DISTINCT id(b) AS b
UNION
UNWIND $f AS i MATCH (a) WHERE id(a) = i WITH a MATCH (a)-[e:OBSERVED_CALLS]->(b:Interface)
WHERE b <> a AND NOT coalesce(e.health_check, false) RETURN DISTINCT id(b) AS b
"""


def falkor_raw(g, name, depth):
    """Frontier BFS over the raw relationships; returns reached names and the summed server time.
    Frontier nodes are passed as internal ids, which FalkorDB seeks directly (NodeByIdSeek)."""
    start = g.query("MATCH (f:Component {name: $n}) RETURN id(f)", {"n": name})
    server = start.run_time_ms
    if not start.result_set:
        return {"impacted": set(), "_server_ms": server, "_queries": 1}
    s = start.result_set[0][0]
    seen, frontier, q = {s}, [s], 1
    for _ in range(depth):
        if not frontier:
            break
        r = g.query(FALKOR_HOP, {"f": frontier, "sinks": list(TELEMETRY_SINKS)})
        server += r.run_time_ms
        q += 1
        frontier = [x[0] for x in r.result_set if x[0] not in seen]
        seen.update(frontier)
    seen.discard(s)
    names = g.query("UNWIND $ids AS i MATCH (n) WHERE id(n) = i RETURN n.name", {"ids": list(seen)})
    server += names.run_time_ms
    return {"impacted": {x[0] for x in names.result_set}, "_server_ms": server, "_queries": q + 1}


PG_RAW = """
WITH RECURSIVE r(id, d) AS (
    SELECT id, 0 FROM {s}.node WHERE name = %(name)s AND kind = 'Component'
  UNION
    SELECT x.nxt, r.d + 1
    FROM r JOIN {s}.node a ON a.id = r.id
    CROSS JOIN LATERAL (
        SELECT e.src AS nxt FROM {s}.edge e
         WHERE e.dst = r.id AND e.type IN ('RUNS_ON', 'STORES_ON', 'OBSERVED_NETFLOW')
      UNION ALL
        SELECT e.dst FROM {s}.edge e JOIN {s}.node b ON b.id = e.dst AND b.label = 'Interface'
         WHERE a.label = 'Application' AND e.src = r.id AND e.type = 'CONTAINS'
      UNION ALL
        SELECT e.src FROM {s}.edge e JOIN {s}.node b ON b.id = e.src AND b.label IN ('BusinessService', 'ServiceOffering')
         WHERE e.dst = r.id AND e.type = 'CONTAINS'
      UNION ALL
        SELECT e.src FROM {s}.edge e
         WHERE a.label <> 'Interface' AND NOT a.name = ANY(%(sinks)s) AND e.dst = r.id AND e.type = 'OBSERVED_CALLS'
           AND NOT coalesce(e.health_check, false)
      UNION ALL
        SELECT e.dst FROM {s}.edge e JOIN {s}.node b ON b.id = e.dst AND b.label = 'Interface'
         WHERE e.src = r.id AND e.type = 'OBSERVED_CALLS' AND NOT coalesce(e.health_check, false)
    ) x
    WHERE r.d < %(depth)s AND x.nxt <> r.id
)
SELECT coalesce(array_agg(n.name), '{{}}') FROM (SELECT DISTINCT id FROM r) h JOIN {s}.node n USING (id)
WHERE n.name <> %(name)s OR n.kind <> 'Component'
"""


def pg_raw(cur, size, name, depth):
    cur.execute(PG_RAW.format(s=schema(size)), {"name": name, "depth": depth, "sinks": list(TELEMETRY_SINKS)},
                prepare=True)
    return {"impacted": set(cur.fetchone()[0])}


# ---------------------------------------------------------------- server-side times

def falkor_server_ms(g, kind, name, depth):
    """FalkorDB's own execution time (query statistics), without network and client decoding."""
    if kind == "raw":
        return falkor_raw(g, name, depth)["_server_ms"]
    if kind == "reach":
        return g.query(FALKOR_REACH, {"name": name, "depth": depth, "rel": "IMPACTS_REAL"}).run_time_ms
    return g.query(RADIUS % {"term": "AFFECTS_REAL"}, {"name": name, "depth": depth, "rel": "IMPACTS_REAL"}).run_time_ms


def pg_server_ms(cur, size, kind, name, depth):
    """PostgreSQL's execution time from EXPLAIN (ANALYZE, TIMING OFF): runs the query, returns no rows."""
    sql = {"radius": PG_RADIUS, "reach": PG_REACH, "raw": PG_RAW}[kind].format(s=schema(size))
    rel, term = LAYERS["real"]
    cur.execute("EXPLAIN (ANALYZE, TIMING OFF, SUMMARY ON, FORMAT JSON) " + sql,
                {"name": name, "depth": depth, "rel": rel, "term": term, "sinks": list(TELEMETRY_SINKS)})
    return cur.fetchone()[0][0]["Execution Time"]
