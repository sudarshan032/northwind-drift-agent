"""FastAPI service (AGENT_SPEC phase 3). Run: uvicorn api.main:app

Config (env): FALKORDB_HOST, FALKORDB_PORT, NORTHWIND_GRAPH (northwind),
NORTHWIND_DATA (data), NORTHWIND_RUNTIME (runtime), ANTHROPIC_API_KEY.
The UI is served from ui/ at /.
"""

import os

from falkordb import FalkorDB
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agent import approval, findings as F, flows
from agent.llm import NO_KEY, get_llm
from agent.tools import notifications as read_notifications
from graph import queries as Q

GRAPH = os.environ.get("NORTHWIND_GRAPH", "northwind")
DATA = os.environ.get("NORTHWIND_DATA", "data")
RUNTIME = os.environ.get("NORTHWIND_RUNTIME", "runtime")
UI = os.path.join(os.path.dirname(__file__), "..", "ui")

app = FastAPI(title="Northwind drift agent", version="0.1.0",
              description="CMDB drift detection on FalkorDB (fictional Northwind Health data)")
_db = None


def graph():
    global _db
    if _db is None:
        _db = FalkorDB(host=os.environ.get("FALKORDB_HOST", "localhost"),
                       port=int(os.environ.get("FALKORDB_PORT", 6379)))
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
            "llm_message": None if llm() else NO_KEY}


@app.post("/scan")
def scan():
    res = flows.triage(graph(), llm())
    return {"total": res["total"], "by_type": res["by_type"], "top": res["top"],
            "llm": llm() is not None}


@app.get("/findings")
def list_findings(status: str | None = None, limit: int = Query(100, le=500)):
    return F.list_findings(graph(), status, limit)


@app.get("/findings/{fid}")
def get_finding(fid: str):
    f = F.get_finding(graph(), fid)
    if f is None:
        raise HTTPException(404, "no such finding")
    return f


@app.post("/findings/{fid}/approve")
def approve(fid: str, d: Decision | None = None):
    res = approval.approve(graph(), DATA, RUNTIME, fid, (d or Decision()).approver)
    if res.get("error"):
        raise HTTPException(404 if res["error"] == "no such finding" else 409, res)
    return res


@app.post("/findings/{fid}/reject")
def reject(fid: str, d: Decision | None = None):
    d = d or Decision()
    res = approval.reject(graph(), RUNTIME, fid, d.approver, d.reason)
    if res.get("error"):
        raise HTTPException(404, res)
    return res


def review_graph(g, review):
    """Small highlighted graph for the change review screen: declared path to
    each declared service, real paths to Emergency care and to a sample of
    hospital EDs, with every edge classified by layer."""
    paths = []
    for t in review["per_target"]:
        src, dec, real = t["target"], t["declared"], t["real"]
        for svc in dec.get("business_services", [])[:3]:
            p = Q.impact_path(g, src, svc, "declared")
            if p:
                paths.append({"layer": "declared", **p})
        routes = real.get("by_label", {}).get("Interface", [])
        for svc in real.get("high_safety_services", [])[:1]:
            p = Q.impact_path(g, src, "svc-emergency-care" if "svc-emergency-care" in real["business_services"] else svc)
            if p:
                paths.append({"layer": "real", **p})
        for dept in [d for d in real.get("departments", []) if d.split("-")[0] in ("H03", "H17", "H44", "H22")]:
            p = Q.impact_path(g, src, dept, via_routes=routes)
            if p:
                paths.append({"layer": "real", **p})
    pairs = sorted({(a, b) for p in paths for a, b in zip(p["nodes"], p["nodes"][1:])})
    layers = {(e["src"], e["dst"]): e for e in Q.edge_layers(g, pairs)} if pairs else {}
    nodes = sorted({n for p in paths for n in p["nodes"]})
    labels = {r["name"]: r["label"] for r in Q.run(g, "MATCH (c:Component) WHERE c.name IN $n RETURN c.name AS name, "
                                                      "[l IN labels(c) WHERE l <> 'Component'][0] AS label",
                                                 {"n": nodes})}
    return {"nodes": [{"id": n, "label": labels.get(n, "Department")} for n in nodes],
            "edges": [{"source": a, "target": b, **{k: layers.get((a, b), {}).get(k) for k in
                                                    ("layer", "declared", "observed")}} for a, b in pairs],
            "paths": paths}


@app.get("/changes")
def changes():
    return Q.run(graph(), "MATCH (c:Change)-[:TARGETS]->(x) RETURN c.number AS number, c.short_description AS "
                          "description, c.state AS state, c.start_date AS start_date, x.name AS target "
                          "ORDER BY c.state DESC, c.start_date DESC")


@app.post("/changes/{cid}/review")
def change_review(cid: str):
    g = graph()
    out = flows.change_review(g, cid, llm())
    if out["facts"] is None:
        raise HTTPException(404, f"no change {cid}")
    review = check(Q.change_review(g, cid))
    return {**out, "graph": review_graph(g, review), "llm": llm() is not None}


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
    return read_notifications(RUNTIME)


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
