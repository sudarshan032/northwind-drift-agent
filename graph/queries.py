"""Parameterised Cypher queries on the `northwind` graph (AGENT_SPEC phase 1).

Every function takes a falkordb Graph, runs fixed Cypher, and returns plain
rows (dicts). Python only orchestrates and formats; selection logic is in the
queries. Every query runs with a timeout (default 5 s) and a LIMIT; on
timeout the function returns {"error": "query timed out", ...} instead of
raising.
"""

TIMEOUT_MS = 5000
LIMIT = 5000


class QueryTimeout(Exception):
    pass


def run(g, query, params=None, timeout=TIMEOUT_MS):
    """Run a read query; raise QueryTimeout on FalkorDB's timeout error."""
    try:
        res = g.query(query, params or {}, timeout=timeout)
    except Exception as e:  # falkordb raises redis ResponseError
        if "timed out" in str(e).lower():
            raise QueryTimeout(str(e)) from e
        raise
    return [dict(zip(res.header_names if hasattr(res, "header_names") else
                     [h[1] if isinstance(h, (list, tuple)) else h for h in res.header], row))
            for row in res.result_set]


def guarded(fn):
    """Decorator: turn a timeout into a clear result for agent tools."""
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except QueryTimeout:
            return {"error": "query timed out", "query": fn.__name__}
    wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
    return wrapper


# --------------------------------------------------------------------------
# Blast radius (impact layers built by graph.impact)
# --------------------------------------------------------------------------

RADIUS = """
MATCH (f:Component {name: $name})
CALL algo.bfs(f, $depth, $rel) YIELD nodes
WITH f, nodes
UNWIND nodes AS n
WITH f, collect(DISTINCT n) AS hit
UNWIND hit AS n
OPTIONAL MATCH (n)-[:%(term)s]->(t)
OPTIONAL MATCH (n)-[:IN_DEPARTMENT]->(nd:Department)
OPTIONAL MATCH (n)-[:LOCATED_AT]->(ns:Site)
WITH f, hit, collect(DISTINCT t) AS term, collect(DISTINCT nd) AS ndepts, collect(DISTINCT ns) AS nsites
WITH f, hit, [x IN term WHERE NOT x:Department] AS consumers,
     [x IN term WHERE x:Department] + ndepts AS depts, nsites
UNWIND (CASE WHEN size(depts) = 0 THEN [null] ELSE depts END) AS d
OPTIONAL MATCH (ds:Site)-[:CONTAINS]->(d)
WITH f, hit, consumers, nsites, collect(DISTINCT d) AS depts, collect(DISTINCT ds) AS dsites
RETURN [x IN hit | {name: x.name, label: [l IN labels(x) WHERE l <> 'Component'][0],
                    safety: x.patient_safety_impact}] AS impacted,
       [x IN consumers | x.name] AS consumers,
       [x IN depts | x.id] AS departments,
       [x IN dsites + nsites WHERE x.type ENDS WITH 'hospital' | x.id] AS hospitals
"""


@guarded
def radius(g, name, layer="real", depth=10, timeout=TIMEOUT_MS):
    """Blast radius of `name` failing. layer = declared | real."""
    up = {"declared": "DECLARED", "real": "REAL"}[layer]
    rows = run(g, RADIUS % {"term": f"AFFECTS_{up}"}, {"name": name, "depth": depth, "rel": f"IMPACTS_{up}"},
               timeout)
    if not rows:
        return {"component": name, "layer": layer, "found": False}
    r = rows[0]
    impacted = sorted(r["impacted"], key=lambda x: x["name"])
    by_label = {}
    for x in impacted:
        by_label.setdefault(x["label"], []).append(x["name"])
    services = sorted(by_label.get("BusinessService", []))
    return {
        "component": name, "layer": layer, "found": True,
        "impacted": impacted, "by_label": {k: sorted(v) for k, v in sorted(by_label.items())},
        "counts": {"components": len(impacted), "routes": len(by_label.get("Interface", [])),
                   "applications": len(by_label.get("Application", [])),
                   "departments": len(set(r["departments"])), "hospitals": len(set(r["hospitals"])),
                   "business_services": len(services)},
        "consumers": sorted(set(r["consumers"])), "departments": sorted(set(r["departments"])),
        "hospitals": sorted(set(r["hospitals"])), "business_services": services,
        "high_safety_services": sorted(x["name"] for x in impacted
                                       if x["label"] == "BusinessService" and x["safety"] == "high"),
    }


PATH = """
MATCH (a:Component {name: $src}), (b {name: $dst})
WITH a, b, shortestPath((a)-[:%(rel)s*..10]->(b)) AS p
WHERE p IS NOT NULL
RETURN [n IN nodes(p) | coalesce(n.name, n.id)] AS nodes, [e IN relationships(p) | e.rule] AS rules
"""

LAST_HOP = """
MATCH (r)-[e:%(term)s]->(t) WHERE (CASE WHEN t:Department THEN t.id ELSE t.name END) = $dst AND r.name IN $routes
RETURN r.name AS via, e.rule AS rule ORDER BY via LIMIT 1
"""


@guarded
def impact_path(g, src, dst, layer="real", via_routes=None, timeout=TIMEOUT_MS):
    """Ordered impact path src -> dst as node names and rules. For terminal
    targets (departments, data consumers) the last hop goes through one of
    `via_routes` (the impacted routes)."""
    up = layer.upper()
    rows = run(g, PATH % {"rel": f"IMPACTS_{up}"}, {"src": src, "dst": dst}, timeout)
    if rows:
        return rows[0]
    if via_routes:
        hop = run(g, LAST_HOP % {"term": f"AFFECTS_{up}"}, {"dst": dst, "routes": via_routes}, timeout)
        if hop:
            head = run(g, PATH % {"rel": f"IMPACTS_{up}"}, {"src": src, "dst": hop[0]["via"]}, timeout)
            if head:
                return {"nodes": head[0]["nodes"] + [dst], "rules": head[0]["rules"] + [hop[0]["rule"]]}
    return None


D14_PATH = """
MATCH (grp:Group)<-[:MEMBER_OF]-(u:User)-[a:OBSERVED_ACCESS]->(x:UnregisteredEndpoint)
      -[c:OBSERVED_CALLS]->(api:Component)-[d:OBSERVED_CALLS]->(db:Database)
WHERE x.name = $app
RETURN grp.id AS grp, count(DISTINCT u) AS users, sum(a.count) AS launches, x.name AS app, x.ip_address AS ip,
       api.name AS api, max(c.count) AS api_calls, max(c.days_seen) AS days_seen, collect(DISTINCT db.name) AS data
ORDER BY users DESC LIMIT 5
"""


@guarded
def unregistered_app_path(g, app, timeout=TIMEOUT_MS):
    """Access path for an unregistered app: group -> users -> app -> API -> data."""
    return run(g, D14_PATH, {"app": app}, timeout)
