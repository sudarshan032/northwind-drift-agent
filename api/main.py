"""FastAPI service (AGENT_SPEC phase 3). Run: uvicorn api.main:app

Config (env): FALKORDB_HOST, FALKORDB_PORT, FALKORDB_USERNAME, FALKORDB_PASSWORD, FALKORDB_TLS,
DEPLOY_MODE (local | public), NORTHWIND_GRAPH (northwind), CRON_SECRET (Vercel cron),
NORTHWIND_DATA (data), NORTHWIND_RUNTIME (runtime), LLM_PROVIDER, OPENROUTER_API_KEY,
NORTHWIND_MODELS, ANTHROPIC_API_KEY.
The UI is served from ui/ at /.
"""

import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import approval, findings as F, flows
from agent.llm import NO_KEY, get_llm
from agent.tools import notifications as read_notifications
from graph import queries as Q
from graph.connection import connect

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


def graph():
    global _db
    if _db is None:
        _db = connect()
    return _db.select_graph(GRAPH)


def llm():
    return getattr(app.state, "llm", None) or get_llm()  # tests inject a FakeLLM via app.state.llm


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


@app.get("/llm")
def llm_status():
    """Provider, models and remaining free-model requests today (UI header)."""
    m = llm()
    return m.status() if m is not None and hasattr(m, "status") else {"provider": "none", "models": [],
                                                                       "remaining": None}


@app.post("/scan")
def scan():
    res = flows.triage(graph(), llm())
    return {"total": res["total"], "by_type": res["by_type"], "top": res["top"], "notice": res["notice"],
            "llm": llm() is not None}


@app.get("/findings")
def list_findings(status: str | None = None, limit: int = Query(100, le=500)):
    return F.list_findings(graph(), status, limit)


@app.get("/findings/{fid}")
def get_finding(fid: str):
    g = graph()
    f = F.get_finding(g, fid)
    if f is None:
        raise HTTPException(404, "no such finding")
    return {**f, "diff": patch_diff(g, f)}


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
    return res


@app.post("/findings/{fid}/reject")
def reject(fid: str, d: Decision | None = None):
    d = d or Decision()
    _risk.clear()
    g = graph()
    res = approval.reject(g, approval.GraphRecords(g) if PUBLIC else RUNTIME, fid, d.approver, d.reason)
    if res.get("error"):
        raise HTTPException(404, res)
    return res


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
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_risk = {}  # change number -> rule-based risk; cleared by approvals and rejections


@app.get("/changes")
def changes():
    g = graph()
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
    out = flows.change_review(g, cid, llm())
    if out["facts"] is None:
        raise HTTPException(404, f"no change {cid}")
    review = check(Q.change_review(g, cid))
    return review_payload(g, review, out)


@app.get("/components/{name}/review")
def component_review(name: str):
    """What breaks if one CI fails: the change-review shape without a change and without the LLM."""
    g = graph()
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
    res = check(Q.radius(graph(), component, layer))
    if not res.get("found"):
        raise HTTPException(404, f"no component {component}")
    return res


@app.get("/subgraph")
def subgraph(component: str, depth: int = Query(1, ge=1, le=2), limit: int = Query(150, le=300)):
    """Bounded neighbourhood (never the whole graph)."""
    g = graph()
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
    return check(Q.critical_nodes(graph()))


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


def reset_demo():
    """Public mode: undo every approval (newest first) from the undo log in the graph, drop the records and
    findings, rebuild the impact layer and re-run triage, so the demo is back in its loaded state."""
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
    impact.build(g)
    res = flows.triage(g, llm())
    _risk.clear()
    grounding._names.pop(GRAPH, None)
    return {"status": "reset", "approvals_undone": len(logs), "findings": res["total"]}


@app.post("/reset")
def reset():
    return reset_demo()


@app.get("/cron/reset")
def cron_reset(authorization: str | None = Header(default=None)):
    """Daily Vercel cron (Hobby: once per day). Vercel sends `Authorization: Bearer $CRON_SECRET`."""
    secret = os.environ.get("CRON_SECRET")
    if not secret or authorization != f"Bearer {secret}":
        raise HTTPException(401, "cron secret required")
    return reset_demo()


@app.post("/chat")
def chat(m: Message):
    return flows.chat(graph(), m.message, llm())


@app.get("/hospitals")
def hospitals(component: str | None = None, layer: str = "real"):
    """Hospitals with coordinates; `in_radius` when `component` is given."""
    from api.geo import CITY_LATLON
    g = graph()
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


if os.path.isdir(UI):
    app.mount("/static", StaticFiles(directory=UI), name="static")

    @app.get("/")
    def index():
        return FileResponse(os.path.join(UI, "index.html"))
