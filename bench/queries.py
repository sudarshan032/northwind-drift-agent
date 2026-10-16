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
