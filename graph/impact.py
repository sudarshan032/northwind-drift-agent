"""Impact semantics (AGENT_SPEC 1.2), defined once and materialised in Cypher.

An edge `(a)-[:IMPACTS_*]->(b)` means "if a fails, b is impacted". Two layers:

    IMPACTS_DECLARED / AFFECTS_DECLARED   built from declared (CMDB) edges only
    IMPACTS_REAL     / AFFECTS_REAL       declared structural edges + observed edges

`IMPACTS_*` edges propagate (traversed transitively). `AFFECTS_*` edges are
terminal: a route's data consumer and the departments it serves are affected,
but impact does not spread onward from them (the EMR does not go down because
one result feed stops).

Rules (direction a -> b):

| rule        | graph pattern                                     | layers    |
|-------------|---------------------------------------------------|-----------|
| runs_on     | (b)-[:RUNS_ON]->(a)                               | both      |
| stores_on   | (b)-[:STORES_ON]->(a)                             | both      |
| hosts_route | (a:Application)-[:CONTAINS]->(b:Interface)        | both      |
| contains    | (b:BusinessService)-[:CONTAINS]->(a)              | both      |
| depends_on  | (b)-[:DEPENDS_ON {failover:false}]->(a)           | declared  |
| calls       | (b)-[:OBSERVED_CALLS]->(a), a not an Interface    | real      |
|             | (excluding health checks and fire-and-forget syslog)|           |
| feeds_route | (a)-[:OBSERVED_CALLS]->(b:Interface)              | real      |
| netflow     | (b)-[:OBSERVED_NETFLOW]->(a)                      | real      |
| consumer    | route -> its target system (terminal)             | both      |
| serves      | route -> department it serves (terminal)          | both      |

The `feeds_route` rule is the single place where DECISIONS #7 matters: an
observed leg `source -> route` is a push, so the *route* needs its source
(not the other way round). Every other observed call means the caller needs
the callee. Health-check calls and failover links never carry impact.
"""

DIRECTION_RULE = "observed call (x)->(y): y needs x if y is an Interface, else x needs y"

RULES = {
    "declared": [
        ("runs_on", "MATCH (b)-[:RUNS_ON]->(a)"),
        ("stores_on", "MATCH (b)-[:STORES_ON]->(a)"),
        ("hosts_route", "MATCH (a:Application)-[:CONTAINS]->(b:Interface)"),
        ("contains", "MATCH (b:BusinessService)-[:CONTAINS]->(a)"),
        ("depends_on", "MATCH (b:Component)-[e:DEPENDS_ON]->(a:Component) WHERE e.failover = false"),
    ],
    "real": [
        ("runs_on", "MATCH (b)-[:RUNS_ON]->(a)"),
        ("stores_on", "MATCH (b)-[:STORES_ON]->(a)"),
        ("hosts_route", "MATCH (a:Application)-[:CONTAINS]->(b:Interface)"),
        ("contains", "MATCH (b:BusinessService)-[:CONTAINS]->(a)"),
        ("calls", "MATCH (b)-[e:OBSERVED_CALLS]->(a) WHERE NOT a:Interface AND NOT coalesce(e.health_check, false) "
                  "AND NOT e.protocol IN ['syslog-tls']"),
        ("feeds_route", "MATCH (a)-[e:OBSERVED_CALLS]->(b:Interface) WHERE NOT coalesce(e.health_check, false)"),
        ("netflow", "MATCH (b)-[:OBSERVED_NETFLOW]->(a)"),
    ],
}
TERMINAL = {
    "declared": [
        ("consumer", "MATCH (a:Interface)-[e:DEPENDS_ON]->(b:Component) WHERE b.name = a.target AND e.failover = false"),
        ("serves", "MATCH (a:Interface)-[:IN_DEPARTMENT]->(b:Department)"),
    ],
    "real": [
        ("consumer", "MATCH (a:Interface)-[e:OBSERVED_CALLS]->(b:Component) WHERE b.name = a.target"),
        ("serves", "MATCH (a:Interface)-[:IN_DEPARTMENT]->(b:Department)"),
    ],
}


def statements():
    """Cypher that (re)builds the impact layers. Idempotent (MERGE)."""
    out = []
    for layer in ("declared", "real"):
        up = layer.upper()
        out.append(f"MATCH ()-[e:IMPACTS_{up}|AFFECTS_{up}]->() DELETE e")
        for name, match in RULES[layer]:
            out.append(f"{match} WITH DISTINCT a, b WHERE a <> b "
                       f"MERGE (a)-[e:IMPACTS_{up}]->(b) SET e.rule = coalesce(e.rule, '{name}')")
        for name, match in TERMINAL[layer]:
            out.append(f"{match} WITH DISTINCT a, b MERGE (a)-[e:AFFECTS_{up}]->(b) SET e.rule = '{name}'")
    return out


def build(graph):
    """Rebuild both impact layers on a falkordb Graph."""
    for q in statements():
        graph.query(q)
