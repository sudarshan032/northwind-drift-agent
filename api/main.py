"""FastAPI service (AGENT_SPEC phase 3). Run: uvicorn api.main:app

Config (env): FALKORDB_HOST, FALKORDB_PORT, FALKORDB_USERNAME, FALKORDB_PASSWORD, FALKORDB_TLS,
DEPLOY_MODE (local | public), NORTHWIND_GRAPH (northwind), CRON_SECRET (Vercel cron),
NORTHWIND_DATA (data), NORTHWIND_RUNTIME (runtime), LLM_PROVIDER, OPENROUTER_API_KEY,
NORTHWIND_MODELS, ANTHROPIC_API_KEY.
The UI is served from ui/ at /.
"""

import time

T_IMPORT = time.perf_counter()  # cold-start log line (SITE_SPEC 5.1)

import contextvars  # noqa: E402
import datetime  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI, Header, HTTPException, Query, Request  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from agent import approval, findings as F, flows  # noqa: E402  (LangGraph loads on a flow's first run)
from agent.llm import NO_KEY, get_llm  # noqa: E402
from agent.tools import notifications as read_notifications
from graph import queries as Q  # noqa: E402
from graph.connection import connect  # noqa: E402

log = logging.getLogger("northwind.api")

GRAPH = os.environ.get("NORTHWIND_GRAPH", "northwind")
PUBLIC = os.environ.get("DEPLOY_MODE", "local") == "public"  # Vercel: no disk writes, cached AI, approvals in the DB
DATA = os.environ.get("NORTHWIND_DATA", "data")
RUNTIME = os.environ.get("NORTHWIND_RUNTIME", "runtime")
UI = os.path.join(os.path.dirname(__file__), "..", "ui")



@asynccontextmanager
async def lifespan(app):
    get_llm()  # provider + model selection at startup (logged)
    yield


app = FastAPI(title="Northwind drift agent", version="0.1.0", lifespan=lifespan,
              description="CMDB drift detection on FalkorDB (fictional Northwind Health data)")
_db = None
IMPORT_MS = (time.perf_counter() - T_IMPORT) * 1000
_req = contextvars.ContextVar("req", default=None)  # per request: db seconds, ?v=, cache policy
_cold = [True]


class TimedGraph:
    """The FalkorDB graph handle, adding each query's time to the request's Server-Timing `db` entry."""

    def __init__(self, g):
        self._g = g

    def __getattr__(self, k):
        return getattr(self._g, k)

    def query(self, *a, **k):
        t = time.perf_counter()
        try:
            return self._g.query(*a, **k)
        finally:
            r = _req.get()
            if r is not None:
                r["db"] += time.perf_counter() - t


def graph():
    global _db
    if _db is None:
        _db = connect()
    return TimedGraph(_db.select_graph(GRAPH))


def memory(user=None):
    """The agent's memory graph (agent.memory), next to the CMDB graph in the same FalkorDB."""
    from agent.memory import Memory
    graph()
    base = "northwind_memory" if GRAPH == "northwind" else f"{GRAPH}_memory"  # one memory per CMDB graph
    return Memory(TimedDB(_db), user, base)


class TimedDB:
    def __init__(self, db):
        self._db = db

    def select_graph(self, name):
        return TimedGraph(self._db.select_graph(name))

    def list_graphs(self):
        return self._db.list_graphs()


# Edge caching (SITE_SPEC 5.3). "versioned": the URL carries the current data version (?v=), so the response
# can be kept until the version changes (the browser keeps it too, so revisiting a page needs no request); "static": data that only changes with a deployment.
CACHE = {"versioned": "public, max-age=3600, s-maxage=86400, stale-while-revalidate=604800",
         "static": "public, max-age=300, s-maxage=3600, stale-while-revalidate=86400"}


@app.middleware("http")
async def server_timing(request, call_next):
    r = {"db": 0.0, "v": request.query_params.get("v"), "cache": None, "stored": None}
    if _cold[0] and PUBLIC and not request.url.path.startswith(("/static/", "/cron/")):
        import asyncio
        try:  # a dirty demo left behind by an earlier instance is restored before the first answer
            await asyncio.to_thread(auto_reset, graph(), True)
        except Exception as e:  # never block the request on it
            log.warning("cold-start reset check failed: %s", type(e).__name__)
    tok = _req.set(r)
    t = time.perf_counter()
    try:
        resp = await call_next(request)
    finally:
        _req.reset(tok)
    total = (time.perf_counter() - t) * 1000
    db = r["db"] * 1000
    resp.headers["Server-Timing"] = (f"db;dur={db:.1f}, compute;dur={max(0.0, total - db):.1f}, total;dur={total:.1f}"
                                     + (f", stored;desc={r['stored']}" if r["stored"] else ""))
    if request.method == "GET" and resp.status_code == 200 and r["cache"] in CACHE:
        resp.headers["Cache-Control"] = CACHE[r["cache"]]
    elif not request.url.path.startswith("/static/") and request.url.path != "/":
        resp.headers["Cache-Control"] = "no-store"
    if _cold[0]:
        _cold[0] = False
        log.warning("cold start: imports %.0f ms, first request %s %s %.0f ms (db %.0f ms)", IMPORT_MS,
                    request.method, request.url.path, total, db)
    return resp


# Stored results (SITE_SPEC 5.2): computed once per data version and kept in FalkorDB as `:Stored` nodes.
# The version is a timestamp token in a `:Meta` node, replaced on approve, reject, scan and reset; a token
# is never reused, so edge-cached URLs with an old ?v= can never show newer data under an old key.
STORE_CAP = 200  # nodes; bounds the space a visitor can fill with distinct component reviews
DEPLOY = os.environ.get("VERCEL_DEPLOYMENT_ID") or os.environ.get("VERCEL_GIT_COMMIT_SHA") or "local"


def version(g):
    rows = g.query("MATCH (m:Meta {key: 'version'}) RETURN m.v").result_set
    return rows[0][0] if rows else bump(g)


def bump(g):
    v = str(time.time_ns())
    g.query("MERGE (m:Meta {key: 'version'}) SET m.v = $v", {"v": v})
    g.query("MATCH (s:Stored) DELETE s")
    return v


# Automatic demo reset (public mode). Every write a visitor can make (approve, reject, a live agent run) marks the
# demo dirty in a `:Meta {key: 'dirty'}` node (first and last write). `auto_reset` restores the demo once it has been
# dirty for an hour, or on a cold start; it waits until nobody has written for IDLE_S so a visitor's walkthrough is
# not reset under them. The node is deleted in the same query that claims the reset, so concurrent instances
# (Vercel runs several) reset at most once.
RESET_EVERY_S = 3600
IDLE_S = 600


def touch(g):
    g.query("MERGE (m:Meta {key: 'dirty'}) ON CREATE SET m.since = $t SET m.last = $t", {"t": time.time()})


def auto_reset(g, cold=False):
    """Reset if dirty and due (dirty for RESET_EVERY_S, or on a cold start) and idle for IDLE_S; else None."""
    t = time.time()
    claimed = g.query("MATCH (m:Meta {key: 'dirty'}) WHERE m.last <= $idle AND ($cold OR m.since <= $due) "
                      "DELETE m RETURN count(m)",
                      {"idle": t - IDLE_S, "due": t - RESET_EVERY_S, "cold": cold}).result_set
    if not claimed or not claimed[0][0]:
        return None
    log.warning("automatic demo reset (%s)", "cold start" if cold else "hourly")
    return reset_demo()


def stored(g, key, fn):
    """fn() once per data version (and deployment); later calls read the stored JSON."""
    r = _req.get() or {}
    v = version(g)
    if r.get("v") == v:
        r["cache"] = "versioned"
    k = f"{DEPLOY}:{key}"
    hit = g.query("MATCH (s:Stored {key: $k, v: $v}) RETURN s.json LIMIT 1", {"k": k, "v": v}).result_set
    if hit:
        r["stored"] = "hit"
        return json.loads(hit[0][0])
    val = fn()
    g.query("OPTIONAL MATCH (s:Stored) WITH count(s) AS n WHERE n < $cap CREATE (:Stored {key: $k, v: $v, json: $j})",
            {"cap": STORE_CAP, "k": k, "v": v, "j": json.dumps(val, default=str)})
    r["stored"] = "miss"
    return val


def static_cache():
    r = _req.get()
    if r is not None:
        r["cache"] = "static"


def llm():
    m = getattr(app.state, "llm", None) or get_llm()  # tests inject a FakeLLM via app.state.llm
    if m is not None and hasattr(m, "over_cap") and getattr(m, "charge", None) is None:
        from agent import spend
        m.charge = lambda usd: spend.charge(graph(), usd)  # one daily dollar cap for the agent and these jobs
    return m


def check(res):
    if isinstance(res, dict) and res.get("error") == "query timed out":
        raise HTTPException(504, res)
    return res


class Symptom(BaseModel):
    symptom: str


class Message(BaseModel):
    message: str


class Decision(BaseModel):
    approver: str = "human"
    reason: str = ""


@app.get("/health")
def health():
    g = graph()
    n = g.query("MATCH (n:Component) RETURN count(n)").result_set[0][0]
    return {"status": "ok", "graph": GRAPH, "components": n, "llm": llm() is not None,
            "mode": "public" if PUBLIC else "local",
            "llm_message": None if llm() else NO_KEY}


@app.get("/api/state")
def state():
    """Everything the UI needs to start, in one uncached call: mode, data version, AI status, chat chips."""
    from agent.answers import CHIPS
    g = graph()
    m = llm()
    status = m.status() if m is not None and hasattr(m, "status") else {"provider": "none", "models": []}
    from agent import spend
    return {"mode": "public" if PUBLIC else "local", "version": version(g), "llm": status, "chips": CHIPS,
            "agent": {"spend_today": round(spend.today(g), 4), "cap": spend.cap(), "live": agent_llm() is not None,
                      "model": getattr(agent_llm(), "model", None)}}


@app.get("/llm")
def llm_status():
    """Provider, models and remaining free-model requests today (UI header)."""
    m = llm()
    if PUBLIC:
        static_cache()  # cached answers only: changes with a deployment
    return m.status() if m is not None and hasattr(m, "status") else {"provider": "none", "models": [],
                                                                       "remaining": None}


@app.post("/scan")
def scan():
    g = graph()
    res = flows.triage(g, llm(), rejected=memory().rejected())
    bump(g)
    return {"total": res["total"], "by_type": res["by_type"], "top": res["top"], "notice": res["notice"],
            "llm": llm() is not None}


LIST_FIELDS = ("id", "type", "kind", "severity", "score", "explanation", "action", "status", "resolved", "sources",
               "subjects", "key")


@app.get("/findings")
def list_findings(status: str | None = None, limit: int = Query(100, le=500), view: str | None = None):
    """`view=list`: only the fields the findings table shows (SITE_SPEC 5.5); the drawer loads one finding."""
    g = graph()

    def compute():
        fs = F.list_findings(g, status, limit)
        return [{k: f.get(k) for k in LIST_FIELDS} for f in fs] if view == "list" else fs
    return stored(g, f"findings:{status}:{limit}:{view}", compute)


@app.get("/findings/{fid}")
def get_finding(fid: str):
    g = graph()

    def compute():
        f = F.get_finding(g, fid)
        if f is None:
            raise HTTPException(404, "no such finding")
        return {**f, "diff": patch_diff(g, f)}
    return stored(g, f"finding:{fid}", compute)


def patch_diff(g, f):
    """Before/after rows for a finding's proposed CMDB update (UI drawer)."""
    rows = []
    for op in f.get("patch") or []:
        path = op.get("path", "")
        if path.startswith("/cmdb_ci/") and op["op"] == "replace":
            _, _, ci, field = path.split("/", 3)
            cur = (Q.get_component(g, ci) or {}).get("props", {}).get(field)
            rows.append({"what": f"{ci} {field}", "before": cur, "after": op["value"]})
        elif path == "/cmdb_ci/-":
            v = op["value"]
            rows.append({"what": f"CI {v['name']}", "before": None,
                         "after": ", ".join(f"{k} {v[k]}" for k in ("class", "site", "status", "ip_address") if v.get(k))})
        elif path == "/cmdb_rel_ci/-":
            v = op["value"]
            rows.append({"what": "Relationship", "before": None,
                         "after": f"{v['parent']} {v['type'].split('::')[0]} {v['child']}"})
        elif path == "/cmdb_rel_ci" and op["op"] == "remove":
            m = op.get("match", {})
            before = (f"{m['parent']} {m['type'].split('::')[0]} {m['child']}" if "parent" in m
                      else f"every relationship of {m.get('any_end')}")
            rows.append({"what": "Relationship", "before": before, "after": None})
    return rows


@app.post("/findings/{fid}/approve")
def approve(fid: str, d: Decision | None = None):
    _risk.clear()
    g = graph()
    res = (approval.approve(g, None, approval.GraphRecords(g), fid, (d or Decision()).approver) if PUBLIC
           else approval.approve(g, DATA, RUNTIME, fid, (d or Decision()).approver))
    if res.get("error"):
        raise HTTPException(404 if res["error"] == "no such finding" else 409, res)
    learn(g, fid, "approve", (d or Decision()).approver)
    touch(g)
    bump(g)
    return res


@app.post("/findings/{fid}/reject")
def reject(fid: str, d: Decision | None = None):
    d = d or Decision()
    _risk.clear()
    g = graph()
    res = approval.reject(g, approval.GraphRecords(g) if PUBLIC else RUNTIME, fid, d.approver, d.reason)
    if res.get("error"):
        raise HTTPException(404, res)
    learn(g, fid, "reject", d.approver, d.reason)
    touch(g)
    bump(g)
    return res


def learn(g, fid, decision, approver, comment=""):
    """Human feedback -> a confirmed or rejected Fact in the memory graph (AGENT_V2_SPEC 4)."""
    from agent.memory import evidence_hash
    f = F.get_finding(g, fid)
    if f is None:
        return
    n, subj = len(f.get("keys") or [f.get("key")]), (f.get("subjects") or ["?"])
    verb = "confirmed" if decision == "approve" else "rejected"
    date = datetime.date.today().isoformat()
    what = (f"{n} hidden link{'s' if n != 1 else ''} to {subj[0]}" if f["type"] == "D1"
            else f"the {f['kind']} finding on {subj[0]}")
    try:
        memory().feedback(fid, decision, approver, comment, names=subj,
                          statement=f"A person {verb} {what} ({fid}) on {date}.", evidence=evidence_hash(f["evidence"]))
    except Exception as e:  # memory is best effort; the approval itself stands
        log.warning("memory feedback failed (%s)", type(e).__name__)


UI_NAMES = {"BusinessService": "Business Service", "ServiceOffering": "Service Offering", "Application": "Application",
            "Interface": "Interface (HL7)", "Database": "Database", "VM": "Virtual Machine", "Host": "Server",
            "StorageArray": "Storage Array", "BackupTarget": "Backup Target", "NetworkDevice": "Network Device",
            "MedicalDevice": "Medical Device", "Endpoint": "Endpoint", "EndpointGroup": "Endpoint Group",
            "ExternalService": "External Service", "Department": "Department"}
REL_NAMES = {"CONTAINS": "Contains", "DEPENDS_ON": "Depends on", "RUNS_ON": "Runs on", "STORES_ON": "Stored on",
             "REPLICATES_TO": "Replicates to", "BACKED_UP_BY": "Backed up by", "CONNECTS_THROUGH": "Connects to",
             "IN_DEPARTMENT": "Serves department"}
EVIDENCE = {"OBSERVED_CALLS", "OBSERVED_NETFLOW", "OBSERVED_REPLICATES_TO", "OBSERVED_BACKUP_TO"}


def dependency_edge(a, b, edges):
    """One CMDB-direction edge (parent -> child) for a node pair, with its layer status:
    confirmed (in the CMDB and seen in evidence), observed_only (dashed red), declared_only (no evidence),
    failover, or structural (declared, not traffic-observable)."""
    declared = [e for e in edges if e["type"] in REL_NAMES]
    evidence = [e for e in edges if e["type"] in EVIDENCE]
    ev = None
    if evidence:
        x = evidence[0]
        # caller depends on callee; an inbound interface leg (source -> route) means the route depends on the source
        parent, child = ((x["end"], x["start"]) if x.get("direction") == "inbound" else (x["start"], x["end"]))
        ev = {"source": x["source"], "count": x["count"], "errors": x["errors"], "days_seen": x["days_seen"],
              "first_seen": x["first_seen"], "last_seen": x["last_seen"], "port": x["port"],
              "connector": x["connector_type"], "endpoint": x["endpoint"], "type": x["type"]}
    if declared:
        d = declared[0]
        parent, child = d["start"], d["end"]
        rel = REL_NAMES[d["type"]]
        traffic = d["type"] in ("DEPENDS_ON", "REPLICATES_TO", "BACKED_UP_BY")
        status = ("failover" if d["failover"] else "confirmed" if ev else "declared_only" if traffic else "structural")
        layers = ["CMDB"] + (["evidence"] if ev else [])
    else:
        rel = "Depends on" if ev and ev["type"] != "OBSERVED_REPLICATES_TO" else "Replicates to"
        status, layers = "observed_only", ["evidence"]
        rel = f"{rel} (observed: {ev['source'].replace('_', ' ')}, not in CMDB)"
    return {"source": parent, "target": child, "label": rel, "status": status, "layers": layers, "evidence": ev}


IMPACT_PAIRS = """
MATCH (a:Component)-[:IMPACTS_REAL|IMPACTS_DECLARED]->(b:Component)
WHERE a.name IN $n AND b.name IN $n
RETURN DISTINCT a.name AS a, b.name AS b
"""


def review_graph(g, review):
    """Dependency view for a change (or a CI failing): every CI in the declared or real radius of the
    targets, with each impact-layer pair drawn as its CMDB-direction edge (relationship name, layer status,
    evidence). Departments are not drawn (they are counted in the comparison table). `paths` holds one real
    and one declared path from the target to its most important service, for the small-screen list."""
    real_nodes, declared_nodes, paths = set(), set(), []
    for t in review["per_target"]:
        src, dec, real = t["target"], t["declared"], t["real"]
        real_nodes |= {x["name"] for x in real.get("impacted", [])}
        declared_nodes |= {x["name"] for x in dec.get("impacted", [])}
        for layer, r in (("real", real), ("declared", dec)):
            svc = (r.get("high_safety_services") or r.get("business_services") or [None])[0]
            p = Q.impact_path(g, src, svc, layer) if svc else None
            if p:
                paths.append({"layer": layer, **p})
    targets = set(review["targets"])
    names = sorted(targets | real_nodes | declared_nodes)
    pairs = {tuple(sorted((r["a"], r["b"]))) for r in Q.run(g, IMPACT_PAIRS, {"n": names})}
    edges = {}
    for e in check(Q.set_edges(g, names)):
        k = tuple(sorted((e["start"], e["end"])))
        if k in pairs:  # only relationships that carry impact
            edges.setdefault(k, []).append(e)
    drawn = [dependency_edge(a, b, es) for (a, b), es in sorted(edges.items())]
    labels = {r["name"]: r["label"] for r in Q.run(g, "MATCH (c:Component) WHERE c.name IN $n RETURN c.name AS name, "
                                                      "[l IN labels(c) WHERE l <> 'Component'][0] AS label",
                                                 {"n": names})}
    return {"nodes": [{"id": n, "label": labels.get(n), "class": UI_NAMES.get(labels.get(n)), "changed": n in targets,
                       "impacted_real": n in real_nodes, "impacted_declared": n in declared_nodes}
                      for n in names if n in labels],
            "edges": [e for e in drawn if e["source"] in labels and e["target"] in labels], "paths": paths}


def review_payload(g, review, out):
    hidden = [{"from": e["src"], "to": e["dst"], "source": e.get("source"), "connector": e.get("connector"),
               "count": e.get("count"), "days_seen": e.get("days_seen"), "first_seen": e.get("first_seen"),
               "last_seen": e.get("last_seen")} for e in review.get("undocumented_edges", [])]
    return {**out, "hidden_dependencies": hidden, "graph": review_graph(g, review), "llm": llm() is not None}


@app.get("/sources")
def sources():
    """Discovery sources: what each reveals, the tool it stands in for, raw format, counts, samples."""
    path = os.path.join(DATA, "normalised", "sources.json")
    if not os.path.exists(path):
        raise HTTPException(404, "run the connectors first (python -m ingest.connect)")
    static_cache()
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_risk = {}  # change number -> rule-based risk; cleared by approvals and rejections


@app.get("/changes")
def changes():
    g = graph()
    return stored(g, "changes", lambda: _changes(g))


def _changes(g):
    rows = Q.run(g, "MATCH (c:Change)-[:TARGETS]->(x) RETURN c.number AS number, c.short_description AS "
                    "description, c.state AS state, c.start_date AS start_date, x.name AS target "
                    "ORDER BY c.state DESC, c.start_date DESC")
    for r in rows:
        if r["number"] not in _risk:
            _risk[r["number"]] = Q.change_review(g, r["number"]).get("risk")
        r["risk"] = _risk[r["number"]]
    return rows


@app.post("/changes/{cid}/review")
def change_review(cid: str):
    g = graph()
    return stored(g, f"review:{cid}", lambda: _change_review(g, cid))


@app.get("/changes/{cid}/review")
def change_review_get(cid: str):
    """The same review as POST, cacheable at the edge with ?v=<version> (the UI uses this)."""
    return change_review(cid)


def _change_review(g, cid):
    out = flows.change_review(g, cid, llm())
    if out["facts"] is None:
        raise HTTPException(404, f"no change {cid}")
    review = check(Q.change_review(g, cid))
    return review_payload(g, review, out)


@app.get("/components/{name}/review")
def component_review(name: str):
    """What breaks if one CI fails: the change-review shape without a change and without the LLM."""
    g = graph()
    return stored(g, f"component:{name}", lambda: _component_review(g, name))


def _component_review(g, name):
    dec, real = check(Q.radius(g, name, "declared")), check(Q.radius(g, name, "real"))
    if not real.get("found"):
        raise HTTPException(404, f"no component {name}")
    names = sorted({name} | {x["name"] for x in real["impacted"]} | set(real["consumers"]))
    hidden = Q.run(g, Q.UNDOCUMENTED_IN, {"names": names, "limit": Q.LIMIT, "min_days": Q.MIN_DAYS_UNDOCUMENTED})
    review = {"targets": [name], "per_target": [{"target": name, "declared": dec, "real": real}],
              "undocumented_edges": hidden}
    owners = Q.who_to_page(g, sorted({name} | {x["name"] for x in real["impacted"]
                                               if x["label"] in ("Database", "VM", "Application")}))
    facts = {"component": name, "declared": dec["counts"], "real": real["counts"],
             "declared_services": dec["business_services"], "hidden_edges": [f"{e['src']} -> {e['dst']}" for e in hidden],
             "warn": [o for o in owners if o["declared_owner"] or o["observed_owner"]]}
    return review_payload(g, review, {"component": name, "facts": facts, "explanation": None, "notice": None})


@app.post("/incident")
def incident(s: Symptom):
    return flows.incident(graph(), s.symptom, llm())


@app.get("/radius")
def radius(component: str, layer: str = Query("real", pattern="^(declared|real)$")):
    g = graph()

    def compute():
        res = check(Q.radius(g, component, layer))
        if not res.get("found"):
            raise HTTPException(404, f"no component {component}")
        return res
    return stored(g, f"radius:{component}:{layer}", compute)


@app.get("/subgraph")
def subgraph(component: str, depth: int = Query(1, ge=1, le=2), limit: int = Query(150, le=300)):
    """Bounded neighbourhood (never the whole graph)."""
    g = graph()
    return stored(g, f"subgraph:{component}:{depth}:{limit}", lambda: _subgraph(g, component, depth, limit))


def _subgraph(g, component, depth, limit):
    edges = check(Q.get_neighbourhood(g, component, depth, limit=limit))
    if not edges:
        raise HTTPException(404, f"no component {component}")
    nodes = {}
    for e in edges:
        nodes[e["src"]] = e["src_label"] or "Node"
        nodes[e["dst"]] = e["dst_label"] or "Node"
    return {"nodes": [{"id": k, "label": v} for k, v in sorted(nodes.items())],
            "edges": [{"source": e["src"], "target": e["dst"], "type": e["type"],
                       "layer": "failover" if e["failover"] else "observed" if e["type"].startswith("OBSERVED")
                       else "declared"} for e in edges], "truncated": len(edges) >= limit}


@app.get("/critical")
def critical():
    g = graph()
    return stored(g, "critical", lambda: check(Q.critical_nodes(g)))


@app.get("/notifications")
def notifications():
    return approval.GraphRecords(graph()).list("notification") if PUBLIC else read_notifications(RUNTIME)


@app.get("/api/health")
def api_health():
    """Database connectivity for monitoring (Vercel); 503 when FalkorDB is unreachable."""
    import time
    from fastapi.responses import JSONResponse
    t = time.perf_counter()
    try:
        g = graph()
        n = g.query("MATCH (c:Component) RETURN count(c)").result_set[0][0]
    except Exception as e:  # report, never leak connection details
        return JSONResponse({"status": "error", "db": "unreachable", "error": type(e).__name__,
                             "mode": "public" if PUBLIC else "local"}, status_code=503)
    return {"status": "ok", "db": "connected", "mode": "public" if PUBLIC else "local", "graph": GRAPH,
            "components": n, "latency_ms": round((time.perf_counter() - t) * 1000)}


def reset_demo(scope="all"):
    """Public mode: undo every approval (newest first) from the undo log in the graph, drop the records and
    findings, rebuild the impact layer and re-run triage, so the demo is back in its loaded state.
    scope: "all" (data and agent memory), "data" (keep memory: the agent can recall what people approved),
    "memory" (agent memory only)."""
    if scope in ("all", "memory"):
        memory().reset()
    if scope == "memory":
        return {"status": "reset", "scope": "memory", "approvals_undone": 0}
    if not PUBLIC:
        raise HTTPException(409, "reset is only available in public mode; reload the local stack instead")
    from agent import grounding
    from graph import impact
    g = graph()
    logs = approval.GraphRecords(g).list("undo", limit=10000)  # newest first
    for log in logs:
        approval.revert(g, log["ops"])
    g.query("MATCH (r:Record) DELETE r")
    g.query("MATCH (f:Finding) DETACH DELETE f")
    if scope == "all":  # "data" keeps memory, so the demo stays dirty until memory is cleared too
        g.query("MATCH (m:Meta {key: 'dirty'}) DELETE m")
    impact.build(g)
    res = flows.triage(g, llm(), rejected=memory().rejected())
    bump(g)
    _risk.clear()
    grounding._names.pop(GRAPH, None)
    return {"status": "reset", "scope": scope, "approvals_undone": len(logs), "findings": res["total"]}


@app.post("/reset")
def reset(scope: str = Query("all", pattern="^(all|data|memory)$")):
    return reset_demo(scope)


@app.get("/cron/reset")
def cron_reset(authorization: str | None = Header(default=None)):
    """Daily Vercel cron (Hobby: once per day). Vercel sends `Authorization: Bearer $CRON_SECRET`."""
    secret = os.environ.get("CRON_SECRET")
    if not secret or authorization != f"Bearer {secret}":
        raise HTTPException(401, "cron secret required")
    return reset_demo()


@app.get("/cron/tick")
def cron_tick():
    """Hourly keep-alive and reset check (GitHub Actions schedule; Vercel Hobby crons run once a day). Public and
    safe to call: it reads one count, and resets only a demo that has been dirty for an hour and idle for 10 minutes
    (POST /reset is public anyway). The query also keeps the FalkorDB Cloud free instance in use; free instances
    unused for a day are stopped, and deleted after 7 days."""
    if not PUBLIC:
        raise HTTPException(409, "only in public mode")
    g = graph()
    n = g.query("MATCH (c:Component) RETURN count(c)").result_set[0][0]
    res = auto_reset(g)
    return {"status": "ok", "components": n, "reset": res is not None,
            **({"approvals_undone": res["approvals_undone"]} if res else {})}


_live = []


def live_llm():
    """Public mode: the live model for the small shared allowance (agent.answers), built on first use."""
    if not _live:
        from agent import spend
        from agent.llm import OpenAILLM, OpenRouterLLM
        if os.environ.get("OPENAI_API_KEYS") or os.environ.get("OPENAI_API_KEY"):
            m = OpenAILLM()
            m.charge = lambda usd: spend.charge(graph(), usd)
            m.capped = spend.today(graph()) >= spend.cap()
        elif os.environ.get("OPENROUTER_API_KEY"):
            m = OpenRouterLLM(runtime="/tmp/northwind")
        else:
            m = None
        _live.append(m)
    if _live[0] is not None and hasattr(_live[0], "capped"):
        from agent import spend
        _live[0].capped = spend.today(graph()) >= spend.cap()  # other instances may have spent today
    return _live[0]


@app.post("/chat")
def chat(m: Message, request: Request):
    if not PUBLIC:
        return flows.chat(graph(), m.message, llm())
    from agent import answers
    ip = (request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else ""))
    live = getattr(app.state, "live_llm", None) or live_llm()  # tests inject a FakeLLM
    return answers.public_chat(graph(), m.message.strip()[:500], llm(), ip, live)


@app.get("/hospitals")
def hospitals(component: str | None = None, layer: str = "real"):
    """Hospitals with coordinates; `in_radius` when `component` is given."""
    g = graph()
    return stored(g, f"hospitals:{component}:{layer}", lambda: _hospitals(g, component, layer))


def _hospitals(g, component, layer):
    from api.geo import CITY_LATLON
    rows = Q.run(g, "MATCH (s:Site) WHERE s.type ENDS WITH 'hospital' RETURN s.id AS id, s.full_name AS name, "
                    "s.city AS city, s.state AS state, s.type AS type ORDER BY id")
    hit = set()
    if component:
        r = check(Q.radius(g, component, layer))
        hit = set(r.get("hospitals", []))
    for r in rows:
        r["lat"], r["lon"] = CITY_LATLON.get((r["city"], r["state"]), (None, None))
        r["in_radius"] = r["id"] in hit
    return rows


# ---------------------------------------------------------------- agent v2 (AGENT_V2_SPEC)
RUNS = os.path.join(os.path.dirname(__file__), "..", "deploy", "agent_runs")
REPLAY_PACE_S = float(os.environ.get("AGENT_REPLAY_PACE_S", "0.7"))  # seconds per step in a tour replay
_agent = []


def agent_llm():
    """The live agent model (OpenAI, keys in OPENAI_API_KEYS / OPENAI_API_KEY), or None. Tests inject one."""
    if getattr(app.state, "agent_llm", None) is not None:
        return app.state.agent_llm
    if not _agent:
        from agent.llm import OpenAILLM
        has = os.environ.get("OPENAI_API_KEYS") or os.environ.get("OPENAI_API_KEY")
        _agent.append(OpenAILLM() if has else None)
    return _agent[0]


def run_slug(kind, trigger):
    import re
    return f"{kind}-" + re.sub(r"[^a-z0-9]+", "-", trigger.lower()).strip("-")[:80]


def recorded(kind, trigger):
    path = os.path.join(RUNS, run_slug(kind, trigger) + ".json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return None


class Investigate(BaseModel):
    kind: str = "change"  # change | incident
    trigger: str
    user: str | None = None
    replay: bool = False  # the guided tour: always the recorded run, never a visitor's live run


def _ip(request):
    return (request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else ""))


@app.post("/agent/investigate")
async def agent_investigate(body: Investigate, request: Request):
    """Run the Change Guardian; streams one JSON event per line (start, memory, note, step, verdict, done)."""
    from fastapi.responses import StreamingResponse
    from agent import guardian, mcp_tools, spend
    if body.kind not in ("change", "incident") or not body.trigger.strip():
        raise HTTPException(422, "kind must be change or incident, with a trigger")
    trigger = body.trigger.strip()[:300]
    g, mem = graph(), memory(body.user)
    mcp_tools.configure(graph, lambda: mem)
    live = agent_llm()
    run = recorded(body.kind, trigger)
    if body.replay and run:
        ok, why = False, "tour"
    else:
        ok, why = (False, "off") if live is None else spend.start(g, _ip(request))

    async def events():
        if ok:
            touch(g)  # a live run writes actions and memory
            async for e in guardian.investigate(g, mem, body.kind, trigger, live, mcp_tools.server,
                                                charge=lambda usd: spend.charge(g, usd)):
                yield json.dumps(e, default=str) + "\n"
        elif why == "tour":  # paced like a live stream, without waiting on the model
            import asyncio
            for e in run:
                if e["type"] == "heartbeat":
                    continue
                if e["type"] == "start":
                    yield json.dumps({**e, "recorded": True}) + "\n"
                    m = guardian.memory_context(g, mem, body.kind, trigger)
                    if m and (m["confirmed"] or m["rejected"] or m["investigations"]):
                        yield json.dumps({"type": "memory", **m}, default=str) + "\n"
                elif e["type"] != "memory":
                    yield json.dumps({**e, "recorded": True}, default=str) + "\n"
                await asyncio.sleep(REPLAY_PACE_S if e["type"] in ("step", "note") else 0.2)
        else:
            yield json.dumps({"type": "notice", "text": spend.MESSAGES[why] if run else
                              spend.MESSAGES[why].replace("this shows the recorded run instead",
                                                          "here is the rule-based review")}) + "\n"
            if run:
                for e in run:
                    if e["type"] == "start":
                        yield json.dumps({**e, "recorded": True}) + "\n"
                        m = guardian.memory_context(g, mem, body.kind, trigger)  # memory is live, not recorded
                        if m and (m["confirmed"] or m["rejected"] or m["investigations"]):
                            yield json.dumps({"type": "memory", **m}, default=str) + "\n"
                    elif e["type"] != "memory":
                        yield json.dumps({**e, "recorded": True}, default=str) + "\n"
            else:
                v = guardian.rule_verdict(g, body.kind, trigger, [])
                yield json.dumps({"type": "verdict", "verdict": v, "valid": False, "rejected": None,
                                  "stopped": "no live agent", "actions_taken": []}) + "\n"
        bump(g)  # actions write notes, flags and tickets

    return StreamingResponse(events(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.get("/agent/investigations")
def agent_investigations(user: str | None = None, limit: int = Query(20, le=100)):
    return memory(user).investigations(limit)


@app.get("/agent/investigations/{iid}")
def agent_investigation(iid: str, user: str | None = None):
    inv = memory(user).investigation(iid)
    if inv is None:
        raise HTTPException(404, "no such investigation")
    return inv


@app.get("/agent/actions")
def agent_actions(change: str | None = None):
    from agent.actions import Actions
    return Actions(graph()).taken(change)


@app.get("/agent/memory")
def agent_memory(components: str, user: str | None = None):
    return memory(user).recall([c for c in components.split(",") if c])


class MCPApp:
    """The read-only MCP server over streamable HTTP, stateless with JSON responses, one session manager per
    request (Vercel functions keep no task group between requests)."""

    async def __call__(self, scope, receive, send):
        from mcp.server.transport_security import TransportSecuritySettings
        from agent import mcp_tools
        mcp_tools.configure(graph, memory)
        sub = mcp_tools.server.streamable_http_app(
            streamable_http_path="/", stateless_http=True, json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
        async with mcp_tools.server._lowlevel_server._session_manager.run():
            await sub({**scope, "path": "/", "root_path": ""}, receive, send)


from starlette.routing import Route  # noqa: E402

app.router.routes.append(Route("/mcp", endpoint=MCPApp(), methods=["GET", "POST", "DELETE"]))  # no 307 to /mcp/
app.mount("/mcp", MCPApp())

if os.path.isdir(UI):
    app.mount("/static", StaticFiles(directory=UI), name="static")

    @app.get("/")
    def index():
        static_cache()
        return FileResponse(os.path.join(UI, "index.html"))
