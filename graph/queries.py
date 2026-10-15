"""Parameterised Cypher queries on the `northwind` graph (AGENT_SPEC phase 1).

Every function takes a falkordb Graph, runs fixed Cypher, and returns plain
rows (dicts). Python only orchestrates and formats; selection logic is in the
queries. Every query runs with a timeout (default 5 s) and a LIMIT; on
timeout the function returns {"error": "query timed out", ...} instead of
raising.
"""

TIMEOUT_MS = 5000
SERVICE_LABELS = ("BusinessService", "ServiceOffering")  # CSDM: counted separately, both can be high-safety
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
    offerings = sorted(by_label.get("ServiceOffering", []))
    return {
        "component": name, "layer": layer, "found": True,
        "impacted": impacted, "by_label": {k: sorted(v) for k, v in sorted(by_label.items())},
        "counts": {"components": len(impacted), "routes": len(by_label.get("Interface", [])),
                   "applications": len(by_label.get("Application", [])),
                   "departments": len(set(r["departments"])), "hospitals": len(set(r["hospitals"])),
                   "business_services": len(services), "service_offerings": len(offerings)},
        "consumers": sorted(set(r["consumers"])), "departments": sorted(set(r["departments"])),
        "hospitals": sorted(set(r["hospitals"])), "business_services": services, "service_offerings": offerings,
        "high_safety_services": sorted(x["name"] for x in impacted if x["label"] in SERVICE_LABELS
                                       and x["safety"] == "high"),
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


# --------------------------------------------------------------------------
# Drift detectors (same rules as tests/oracle.py, RUN_PROMPT 4.1), one per type.
# Each returns rows with a `key` list identical to ground_truth finding_keys.
# --------------------------------------------------------------------------

from ingest.config import BACKUP_CUTOFF, MIN_DAYS_UNDOCUMENTED, SNAPSHOT_END  # noqa: E402,F401
DISCOVERABLE = sorted(__import__("ingest.world", fromlist=["DISCOVERABLE_CLASSES"]).DISCOVERABLE_CLASSES)
REL_TYPE_NAME = {"DEPENDS_ON": "Depends on::Used by", "REPLICATES_TO": "Replicates to::Replica of"}

# Union of distinct days over all (non health-check) observed edges of a pair.
# (FalkorDB does not allow a reduce() nested in another reduce(), hence two steps.)
_FLAT = "reduce(acc = [], l IN dl | acc + l)"
_DAYS = "reduce(s = [], d IN flat | CASE WHEN d IN s THEN s ELSE s + [d] END)"

DETECTORS = {
    # D1: observed traffic between two CIs on >= 3 distinct days, no declared DEPENDS_ON either way.
    "D1": f"""
        MATCH (a:Component)-[o:OBSERVED_CALLS|OBSERVED_NETFLOW]->(b:Component)
        WHERE NOT coalesce(o.health_check, false)
        WITH a, b, collect(o.days) AS dl
        WITH a, b, {_FLAT} AS flat
        WITH a, b, {_DAYS} AS days WHERE size(days) >= $min_days
        OPTIONAL MATCH (a)-[dep:DEPENDS_ON]-(b)
        WITH a, b, days, count(dep) AS declared WHERE declared = 0
        RETURN ['undocumented', a.name, b.name] AS key, size(days) AS days_seen
        ORDER BY key LIMIT $limit""",
    # D2: declared DEPENDS_ON / REPLICATES_TO, failover=false, neither end retired, nothing observed.
    "D2": """
        MATCH (a:Component)-[d:DEPENDS_ON|REPLICATES_TO]->(b:Component)
        WHERE d.failover = false AND a.status <> 'retired' AND b.status <> 'retired'
        OPTIONAL MATCH (a)-[o:OBSERVED_CALLS|OBSERVED_NETFLOW]-(b) WHERE NOT coalesce(o.health_check, false)
        OPTIONAL MATCH (a)-[rp:OBSERVED_REPLICATES_TO]->(b)
        WITH a, b, d, count(o) AS traffic, count(rp) AS repl
        WHERE (type(d) = 'DEPENDS_ON' AND traffic = 0) OR (type(d) = 'REPLICATES_TO' AND repl = 0)
        RETURN ['stale', a.name, b.name, type(d)] AS key
        ORDER BY a.name, b.name LIMIT $limit""",
    # D4: retired CI still holding CMDB relationships.
    "D4": """
        MATCH (r:Component {status: 'retired'})
              -[e:CONTAINS|DEPENDS_ON|RUNS_ON|STORES_ON|REPLICATES_TO|BACKED_UP_BY|CONNECTS_THROUGH]-(o:Component)
        WITH r, collect(DISTINCT o.name) AS linked
        RETURN ['retired_linked', r.name] AS key, linked ORDER BY r.name LIMIT $limit""",
    # D5: dominant team in ownership evidence differs from the CMDB support group (ties ignored).
    "D5": """
        MATCH (g:SupportGroup)-[o:OBSERVED_OWNER]->(c:Component)
        WITH c, g.name AS team, o.incidents_resolved + o.changes_implemented AS w
        ORDER BY w DESC, team
        WITH c, collect({team: team, w: w}) AS ts
        WHERE size(ts) = 1 OR ts[0].w > ts[1].w
        MATCH (c)-[:MANAGED_BY]->(m:SupportGroup)
        WHERE m.name <> ts[0].team
        RETURN ['owner', c.name] AS key, m.name AS cmdb_owner, ts[0].team AS observed_owner, ts[0].w AS evidence
        ORDER BY c.name LIMIT $limit""",
    # D6: discovered device that identifies no CI.
    "D6": """
        MATCH (d:Discovered) WHERE NOT (d)-[:IDENTIFIES]->(:Component)
        RETURN ['shadow', d.hostname, d.ip_address] AS key, d.site AS site, d.model AS model
        ORDER BY d.site, d.hostname LIMIT $limit""",
    # D7: operational discoverable CI in a scanned subnet, never discovered, no traffic.
    "D7": """
        MATCH (c:Component)
        WHERE c.status = 'operational' AND c.class IN $classes AND c.count IS NULL
              AND c.scanned_subnet IS NOT NULL AND NOT (c)<-[:IDENTIFIES]-(:Discovered)
        OPTIONAL MATCH (c)-[o:OBSERVED_CALLS|OBSERVED_NETFLOW]-() WHERE NOT coalesce(o.health_check, false)
        WITH c, count(o) AS traffic WHERE traffic = 0
        RETURN ['ghost', c.name] AS key, c.site AS site, c.ip_address AS ip ORDER BY c.name LIMIT $limit""",
    # D8: identified CI whose os_version / serial_number / ip_address differs from discovery.
    "D8": """
        MATCH (d:Discovered)-[:IDENTIFIES]->(c:Component)
        UNWIND ['os_version', 'serial_number', 'ip_address'] AS f
        WITH c, d, f WHERE c[f] IS NOT NULL AND d[f] IS NOT NULL AND c[f] <> d[f]
        RETURN ['attribute', c.name, f] AS key, c[f] AS cmdb, d[f] AS discovered ORDER BY c.name, f LIMIT $limit""",
    # D9: PHI CI with backup_offsite=true whose storage/backup/replication path never leaves its site.
    "D9": """
        MATCH (c:Component)
        WHERE c.status = 'operational' AND c.handles_phi = true AND c.backup_offsite = true
              AND c.backup_policy IS NOT NULL
        OPTIONAL MATCH p = (c)-[:STORES_ON|OBSERVED_BACKUP_TO|OBSERVED_REPLICATES_TO*1..6]->(x:Component)
        WHERE x.site <> c.site
              AND all(r IN relationships(p) WHERE type(r) <> 'OBSERVED_BACKUP_TO' OR r.last_success IS NOT NULL)
        WITH c, count(x) AS offsite WHERE offsite = 0
        OPTIONAL MATCH (c)-[b:OBSERVED_BACKUP_TO]->(t)
        WITH c, collect(t.name) AS backup_targets
        RETURN ['protection', c.name] AS key, c.site AS site, backup_targets ORDER BY key LIMIT $limit""",
    # D10: CI with a backup policy and no successful backup in the last 7 days.
    "D10": """
        MATCH (c:Component) WHERE c.status = 'operational' AND c.backup_policy IS NOT NULL
        OPTIONAL MATCH (c)-[b:OBSERVED_BACKUP_TO]->()
        WITH c, max(b.last_success) AS last_success
        WHERE last_success IS NULL OR last_success <= $cutoff
        RETURN ['backup_failing', c.name] AS key, last_success ORDER BY c.name LIMIT $limit""",
    # D11: PHI data replicated or sent into cloud-central-india.
    "D11": """
        MATCH (a:Component)-[o:OBSERVED_REPLICATES_TO]->(b:Component)
        WHERE b.site = 'cloud-central-india' OR b.data_residency = 'IN'
        OPTIONAL MATCH (p:Component)-[:STORES_ON]->(a) WHERE p.handles_phi = true
        WITH a, b, o, count(p) AS phi_stored WHERE a.handles_phi = true OR phi_stored > 0
        RETURN ['residency', a.name, b.name] AS key, 'replication' AS via, o.volumes AS detail
        UNION
        MATCH (a:Component)-[o:OBSERVED_CALLS]->(b:Component)
        WHERE (b.site = 'cloud-central-india' OR b.data_residency = 'IN') AND a.handles_phi = true
              AND NOT (a.site = 'cloud-central-india' OR a.data_residency = 'IN')
        RETURN ['residency', a.name, b.name] AS key, 'flow' AS via, [o.msg_type] AS detail""",
    # D12: access by a disabled user, with no grant path, or admin on a production DB outside a window.
    "D12": """
        MATCH (u:User)-[e:OBSERVED_ACCESS]->(c:Component)
        OPTIONAL MATCH (u)-[:MEMBER_OF]->(:Group)-[:HAS_ROLE]->(:Role)-[gr:GRANTS]->(c)
        WITH u, c, e, max(CASE gr.level WHEN 'read' THEN 1 WHEN 'write' THEN 2 WHEN 'admin' THEN 3 ELSE 0 END) AS lvl
        WITH u, c, e, coalesce(lvl, 0) AS lvl,
             CASE e.action WHEN 'admin' THEN 3 WHEN 'write' THEN 2 ELSE 1 END AS need
        UNWIND (CASE WHEN size(e.times) = 0 THEN [null] ELSE e.times END) AS t
        OPTIONAL MATCH (ch:Change)-[:TARGETS]->(c)
        WHERE t IS NOT NULL AND ch.approval = 'approved' AND ch.start_date <= t AND t <= ch.end_date
        WITH u, c, e, lvl, need, t, count(ch) AS windows
        WITH u, c, lvl, need,
             sum(CASE WHEN t IS NOT NULL AND windows = 0 AND c:Database AND c.environment = 'production'
                      THEN 1 ELSE 0 END) AS outside
        WITH u, c, CASE WHEN u.status <> 'active' THEN 'disabled_user'
                        WHEN lvl < need THEN 'no_grant_path'
                        WHEN outside > 0 THEN 'admin_outside_change_window' END AS reason
        WHERE reason IS NOT NULL
        WITH u, c, collect(DISTINCT reason) AS reasons
        RETURN ['access', u.id, c.name] AS key, reasons ORDER BY key LIMIT $limit""",
    # D14: endpoint with no CMDB record carrying traffic on >= 3 distinct days.
    "D14": f"""
        MATCH (x:UnregisteredEndpoint)-[o:OBSERVED_CALLS|OBSERVED_NETFLOW]-()
        WHERE NOT coalesce(o.health_check, false)
        WITH x, collect(o.days) AS dl
        WITH x, {_FLAT} AS flat
        WITH x, {_DAYS} AS days WHERE size(days) >= $min_days
        OPTIONAL MATCH (u:User)-[a:OBSERVED_ACCESS]->(x)
        WITH x, days, count(DISTINCT u) AS users
        RETURN ['unregistered_app', x.name] AS key, size(days) AS days_seen, users ORDER BY key LIMIT $limit""",
}

ALIASES = """
MATCH (c:Component) WHERE c.aliases IS NOT NULL
UNWIND c.aliases AS a
RETURN a AS alias, c.name AS ci ORDER BY ci, alias LIMIT $limit
"""


@guarded
def detect(g, drift_type, timeout=TIMEOUT_MS, limit=LIMIT):
    """Run one detector; rows carry `key` (ground-truth format)."""
    rows = run(g, DETECTORS[drift_type], {"limit": limit, "classes": DISCOVERABLE, "cutoff": BACKUP_CUTOFF,
                                          "min_days": MIN_DAYS_UNDOCUMENTED}, timeout)
    for r in rows:
        if r["key"][0] == "stale":
            r["key"][3] = REL_TYPE_NAME[r["key"][3]]
    return rows


@guarded
def resolved_aliases(g, timeout=TIMEOUT_MS, limit=LIMIT):
    """D13: names resolved to the same CI by serial or IP (not findings)."""
    return run(g, ALIASES, {"limit": limit}, timeout)


def detect_all(g, timeout=TIMEOUT_MS):
    """All detectors: {type: rows} plus D13 aliases. D3 never appears."""
    out = {t: detect(g, t, timeout=timeout) for t in DETECTORS}
    out["D13"] = resolved_aliases(g, timeout=timeout)
    return out


# --------------------------------------------------------------------------
# Change review, protection, access, paging, centrality, lookups
# --------------------------------------------------------------------------

CHANGE_TARGETS = """
MATCH (ch:Change {number: $id})-[:TARGETS]->(x:Component)
RETURN ch.number AS number, ch.short_description AS description, ch.state AS state, ch.approval AS approval,
       ch.start_date AS start_date, collect(x.name) AS targets
"""

UNDOCUMENTED_IN = """
MATCH (a:Component)-[o:OBSERVED_CALLS|OBSERVED_NETFLOW]->(b:Component)
WHERE a.name IN $names AND b.name IN $names AND NOT coalesce(o.health_check, false)
OPTIONAL MATCH (a)-[d:DEPENDS_ON]-(b)
WITH a, b, o, count(d) AS declared WHERE declared = 0
RETURN a.name AS src, b.name AS dst, type(o) AS observed_as, o.msg_type AS msg_type, o.count AS count,
       o.last_seen AS last_seen
ORDER BY src, dst LIMIT $limit
"""


def change_review(g, change_id, timeout=TIMEOUT_MS):
    """Declared vs real radius of a change's targets, the undocumented edges
    inside the real radius, and a risk level:
    high   real radius reaches a patient_safety_impact=high business service the declared one does not
    medium real radius strictly larger; low otherwise."""
    try:
        ch = run(g, CHANGE_TARGETS, {"id": change_id}, timeout)
        if not ch:
            return {"change": change_id, "found": False}
        ch = ch[0]
        out = {"change": change_id, "found": True, **ch, "targets": sorted(ch["targets"]), "per_target": []}
        dec_all, real_all, dec_hi, real_hi, names = set(), set(), set(), set(), set()
        for t in out["targets"]:
            d, r = radius(g, t, "declared", timeout=timeout), radius(g, t, "real", timeout=timeout)
            for x in (d, r):
                if "error" in x:
                    return {"change": change_id, **x}
            dec_all |= {x["name"] for x in d["impacted"]}
            real_all |= {x["name"] for x in r["impacted"]}
            dec_hi |= set(d["high_safety_services"])
            real_hi |= set(r["high_safety_services"])
            names |= {t} | {x["name"] for x in r["impacted"]} | set(r["consumers"])
            out["per_target"].append({"target": t, "declared": d, "real": r})
        hidden = run(g, UNDOCUMENTED_IN, {"names": sorted(names), "limit": LIMIT}, timeout)
        newly_hi = sorted(real_hi - dec_hi)
        risk = "high" if newly_hi else "medium" if len(real_all) > len(dec_all) else "low"
        out.update(risk=risk, newly_reached_high_safety_services=newly_hi, undocumented_edges=hidden,
                   declared_count=len(dec_all), real_count=len(real_all))
        return out
    except QueryTimeout:
        return {"error": "query timed out", "query": "change_review"}


PROTECTION = """
MATCH (c:Component)
WHERE c.status = 'operational' AND c.handles_phi = true AND c.backup_policy IS NOT NULL
OPTIONAL MATCH p = (c)-[:STORES_ON|OBSERVED_BACKUP_TO|OBSERVED_REPLICATES_TO*1..6]->(x:Component)
WHERE all(r IN relationships(p) WHERE type(r) <> 'OBSERVED_BACKUP_TO' OR r.last_success IS NOT NULL)
WITH c, collect(DISTINCT x) AS reach
OPTIONAL MATCH (c)-[b:OBSERVED_BACKUP_TO]->()
WITH c, reach, max(b.last_success) AS last_success
WITH c, last_success, [x IN reach WHERE x.site <> c.site | x.name] AS offsite,
     [x IN reach WHERE x:BackupTarget AND x.immutable = true | x.name] AS vault
WHERE size(offsite) = 0 OR size(vault) = 0 OR last_success IS NULL OR last_success <= $cutoff
RETURN c.name AS component, c.site AS site, size(offsite) > 0 AS reaches_other_site,
       size(vault) > 0 AS reaches_immutable_vault, last_success, c.backup_offsite AS cmdb_backup_offsite
ORDER BY component LIMIT $limit
"""


@guarded
def protection_check(g, timeout=TIMEOUT_MS, limit=LIMIT):
    """PHI components whose protection chain is broken: no copy at another
    site, no copy in the immutable vault, or no successful backup in 7 days."""
    return run(g, PROTECTION, {"cutoff": BACKUP_CUTOFF, "limit": limit}, timeout)


@guarded
def access_check(g, timeout=TIMEOUT_MS):
    """Observed accesses with no MEMBER_OF -> HAS_ROLE -> GRANTS path, by
    disabled users, or admin outside approved change windows (with reasons)."""
    return detect(g, "D12", timeout=timeout)


WHO_TO_PAGE = """
MATCH (c:Component) WHERE c.name IN $names
OPTIONAL MATCH (c)-[:MANAGED_BY]->(m:SupportGroup)
OPTIONAL MATCH (g:SupportGroup)-[o:OBSERVED_OWNER]->(c)
WITH c, m, g, o.incidents_resolved + o.changes_implemented AS w ORDER BY w DESC
WITH c, m, collect(g.name)[0] AS observed
RETURN c.name AS component, m.name AS declared_owner, observed AS observed_owner,
       observed IS NOT NULL AND observed <> m.name AS differs
ORDER BY component LIMIT $limit
"""


@guarded
def who_to_page(g, names, timeout=TIMEOUT_MS, limit=LIMIT):
    """Declared (MANAGED_BY) vs observed (OBSERVED_OWNER) owner per component."""
    return run(g, WHO_TO_PAGE, {"names": list(names), "limit": limit}, timeout)


CRITICAL = """
CALL algo.betweenness({nodeLabels: $labels, relationshipTypes: ['OBSERVED_CALLS'], samplingSize: 5000,
                       samplingSeed: 42})
YIELD node, score
RETURN node.name AS name, [l IN labels(node) WHERE l <> 'Component'][0] AS label, score
ORDER BY score DESC, name LIMIT $limit
"""
CRITICAL_LABELS = ["Component"]  # full observed graph is fast (~0.1 s); samplingSize 5000 >= nodes => exact


@guarded
def critical_nodes(g, limit=10, timeout=TIMEOUT_MS):
    """Top components by betweenness on the observed call graph (fixed seed)."""
    return run(g, CRITICAL, {"labels": CRITICAL_LABELS, "limit": limit}, timeout)


COMPONENT = """
MATCH (c:Component {name: $name})
OPTIONAL MATCH (c)-[:LOCATED_AT]->(s:Site)
OPTIONAL MATCH (c)-[:MANAGED_BY]->(m:SupportGroup)
RETURN properties(c) AS props, [l IN labels(c) WHERE l <> 'Component'][0] AS label, s.id AS site,
       s.full_name AS site_name, m.name AS managed_by
"""


@guarded
def get_component(g, name, timeout=TIMEOUT_MS):
    rows = run(g, COMPONENT, {"name": name}, timeout)
    return rows[0] if rows else None


NEIGHBOURHOOD = """
MATCH (c {name: $name}) WHERE c:Component OR c:UnregisteredEndpoint
MATCH p = (c)-[*1..%(depth)d]-(n)
WHERE all(r IN relationships(p) WHERE NOT type(r) IN ['IMPACTS_DECLARED', 'IMPACTS_REAL', 'AFFECTS_DECLARED',
                                                    'AFFECTS_REAL', 'LOCATED_AT', 'MANAGED_BY', 'OBSERVED_OWNER'])
UNWIND relationships(p) AS r
WITH DISTINCT r, startNode(r) AS a, endNode(r) AS b LIMIT $limit
RETURN coalesce(a.name, a.id, a.number, a.hostname) AS src, [l IN labels(a) WHERE l <> 'Component'][0] AS src_label,
       type(r) AS type, coalesce(b.name, b.id, b.number, b.hostname) AS dst,
       [l IN labels(b) WHERE l <> 'Component'][0] AS dst_label,
       r.failover AS failover
"""


@guarded
def get_neighbourhood(g, name, depth=1, limit=300, timeout=TIMEOUT_MS):
    """Edges within `depth` (<= 2) hops of a component, for the agent and UI."""
    depth = max(1, min(int(depth), 2))
    return run(g, NEIGHBOURHOOD % {"depth": depth}, {"name": name, "limit": limit}, timeout)


# --------------------------------------------------------------------------
# Incident support: what affected routes share, recent changes, search
# --------------------------------------------------------------------------

SHARED = """
MATCH (x)-[:IMPACTS_REAL*1..6]->(r:Interface)
WHERE r.name IN $routes
WITH x, collect(DISTINCT r.name) AS hits
WHERE size(hits) = size($routes)
RETURN x.name AS component, [l IN labels(x) WHERE l <> 'Component'][0] AS label, x.site AS site,
       x.patient_safety_impact AS safety
ORDER BY label, component LIMIT $limit
"""


@guarded
def shared_dependencies(g, routes, timeout=TIMEOUT_MS, limit=200):
    """Components every given route needs in the real graph (common upstream
    causes for a multi-site symptom)."""
    return run(g, SHARED, {"routes": list(routes), "limit": limit}, timeout)


RECENT_CHANGES = """
MATCH (ch:Change)-[:TARGETS]->(x:Component)
WHERE x.name IN $names
RETURN ch.number AS number, ch.short_description AS description, ch.state AS state, ch.approval AS approval,
       ch.start_date AS start_date, ch.end_date AS end_date, x.name AS target
ORDER BY start_date DESC LIMIT $limit
"""


@guarded
def changes_on(g, names, timeout=TIMEOUT_MS, limit=50):
    """Changes (past and scheduled) targeting any of the components."""
    return run(g, RECENT_CHANGES, {"names": list(names), "limit": limit}, timeout)


SEARCH = """
MATCH (c:Component) WHERE toLower(c.name) CONTAINS toLower($text) OR toLower(coalesce(c.description, '')) CONTAINS toLower($text)
RETURN c.name AS name, [l IN labels(c) WHERE l <> 'Component'][0] AS label, c.site AS site,
       c.description AS description ORDER BY name LIMIT $limit
"""


@guarded
def search_components(g, text, timeout=TIMEOUT_MS, limit=25):
    """Find components whose name or description contains `text`."""
    return run(g, SEARCH, {"text": text, "limit": limit}, timeout)


# --------------------------------------------------------------------------
# UI support: classify node pairs by layer
# --------------------------------------------------------------------------

EDGE_LAYERS = """
UNWIND $pairs AS p
MATCH (a {name: p[0]}), (b) WHERE (b.name = p[1] OR (b:Department AND b.id = p[1]))
      AND (a:Component OR a:UnregisteredEndpoint)
OPTIONAL MATCH (a)-[d:DEPENDS_ON|RUNS_ON|STORES_ON|CONTAINS|REPLICATES_TO|BACKED_UP_BY|CONNECTS_THROUGH|IN_DEPARTMENT]-(b)
OPTIONAL MATCH (a)-[o:OBSERVED_CALLS|OBSERVED_NETFLOW|OBSERVED_REPLICATES_TO|OBSERVED_BACKUP_TO]-(b)
WITH p, collect(DISTINCT type(d)) AS declared, collect(DISTINCT type(o)) AS observed,
     max(CASE WHEN d.failover = true THEN 1 ELSE 0 END) AS fo
RETURN p[0] AS src, p[1] AS dst, declared, observed, fo = 1 AS failover
"""


@guarded
def edge_layers(g, pairs, timeout=TIMEOUT_MS):
    """For each [src, dst]: declared edge types, observed edge types, failover.
    UI colouring: observed-only red, declared-only grey dashed, failover blue dashed; `structural`
    (runs on, stores on, contains, department) is declared and not traffic-observable."""
    rows = run(g, EDGE_LAYERS, {"pairs": [list(p) for p in pairs]}, timeout)
    traffic = {"DEPENDS_ON", "REPLICATES_TO", "BACKED_UP_BY"}  # declared edges that traffic should confirm
    for r in rows:
        dec = set(r["declared"])
        r["layer"] = ("failover" if r["failover"] else "both" if dec and r["observed"]
                      else "observed_only" if r["observed"]
                      else "declared_only" if dec & traffic
                      else "structural" if dec else "derived")
    return rows
