"""Read-only graph tools as an MCP server (AGENT_V2_SPEC 3).

- Streamable HTTP at /mcp (mounted by api.main, stateless JSON responses, so it runs on Vercel functions).
- stdio for local MCP clients:  python -m agent.mcp_tools   (FALKORDB_* and NORTHWIND_GRAPH from the env)
- In process: the Change Guardian lists and calls these tools through an MCP client (`mcp.Client(server)`).

Every tool returns JSON text. Nothing here writes; the action tools live in agent.actions.
"""

import json
import os
from typing import Literal

from mcp.server.mcpserver import MCPServer

from agent import findings as F
from agent.tools import Toolbox
from graph import queries as Q

server = MCPServer("northwind-graph", instructions=(
    "Read-only tools over Northwind Health's CMDB drift graph (fictional data): the CMDB's declared "
    "dependencies plus what discovery evidence observed. Use them to find the real impact of a change or the cause "
    "of an incident, and recall what the agent and people learned before."))

_graph = None   # () -> CMDB graph; set by configure()
_memory = None  # () -> agent.memory.Memory


def configure(graph_fn, memory_fn):
    global _graph, _memory
    _graph, _memory = graph_fn, memory_fn


def _box():
    if _graph is None:  # stdio / standalone use
        from graph.connection import connect
        db = connect()
        from agent.memory import Memory
        configure(lambda: db.select_graph(os.environ.get("NORTHWIND_GRAPH", "northwind")), lambda: Memory(db))
    return Toolbox(_graph())


def _run(tool, **args):
    box = _box()
    return box.text(box.call(tool, args))


@server.tool(description="Blast radius if a component fails or is taken down: layer 'declared' uses only the "
                         "CMDB's relationships, 'real' adds what discovery evidence observed. Returns counts "
                         "(hospitals, departments, interfaces, services) and component names by type.")
def radius(component: str, layer: Literal["declared", "real"]) -> str:
    return _run("radius", component=component, layer=layer)


@server.tool(description="Hidden links around a component: dependencies seen in the evidence (on at least 3 days) "
                         "inside its real blast radius that have no CMDB relationship, with the evidence source "
                         "(e.g. interface engine, netflow), message counts and dates.")
def hidden_links(component: str) -> str:
    box = _box()
    r = Q.radius(box.g, component, "real")
    if not r.get("found"):
        return json.dumps({"error": f"no component {component}"})
    names = sorted({component} | {x["name"] for x in r["impacted"]} | set(r["consumers"]))
    rows = Q.run(box.g, Q.UNDOCUMENTED_IN, {"names": names, "limit": 200, "min_days": Q.MIN_DAYS_UNDOCUMENTED})
    return box.text({"component": component, "hidden_links": rows, "count": len(rows)})


@server.tool(description="Shortest impact path (node names and rules) from a failing component to an affected "
                         "component or service, in the declared or real layer.")
def impact_path(source: str, target: str, layer: Literal["declared", "real"] = "real") -> str:
    return _run("impact_path", source=source, target=target, layer=layer)


@server.tool(description="Who to page: for each component, the CMDB (declared) owner and the observed owner (the "
                         "team that really resolves its incidents and implements its changes), and whether they differ.")
def who_to_page(components: list[str]) -> str:
    return _run("who_to_page", components=components)


@server.tool(description="Patient-data (PHI) systems whose backup or replication chain does not reach another "
                         "site, although the CMDB says it does.")
def protection_check() -> str:
    return _run("protection_check")


@server.tool(description="Access drift: accesses with no grant path, by disabled users, or admin work outside "
                         "approved change windows.")
def access_check() -> str:
    return _run("access_check")


@server.tool(description="Components that every given interface/component needs in the real graph (use it to "
                         "find the common cause of a multi-site symptom).")
def shared_dependencies(routes: list[str]) -> str:
    return _run("shared_dependencies", routes=routes)


@server.tool(description="Change requests (past and scheduled) that target any of the components.")
def changes_on(components: list[str]) -> str:
    return _run("changes_on", components=components)


@server.tool(description="Top 10 components by betweenness centrality on the observed call graph (single points "
                         "of failure).")
def critical_nodes() -> str:
    return _run("critical_nodes")


@server.tool(description="CMDB record of one component: class, site, status, owner, attributes.")
def get_component(name: str) -> str:
    return _run("get_component", name=name)


@server.tool(description="A change request: number, description, state, start date and the components it targets.")
def get_change(change_id: str) -> str:
    box = _box()
    rows = Q.run(box.g, Q.CHANGE_TARGETS, {"id": change_id})
    return box.text(rows[0] if rows else {"error": f"no change {change_id}"})


@server.tool(description="Edges within 1-2 hops of a component (declared and observed), bounded.")
def get_neighbourhood(name: str, depth: int = 1) -> str:
    return _run("get_neighbourhood", name=name, depth=max(1, min(2, depth)))


@server.tool(description="Find components whose name or description contains a text (e.g. a site code or an "
                         "interface family such as ORU-CRIT).")
def search_components(text: str) -> str:
    return _run("search_components", text=text)


@server.tool(description="Open drift findings about the given components (type, severity, explanation, proposed "
                         "action), e.g. failing backups, owner drift, an unregistered app.")
def findings_about(components: list[str]) -> str:
    box = _box()
    want = set(components)
    rows = [{k: f.get(k) for k in ("id", "type", "kind", "severity", "status", "action", "explanation", "subjects")}
            for f in F.list_findings(box.g, None, 500) if want & set(f.get("subjects") or [])
            or (f.get("key") and want & set(map(str, f["key"])))]
    return box.text(rows or {"result": "no findings"})


@server.tool(description="Stale links around a component: CMDB dependencies (parent depends on child) inside its "
                         "declared radius with no traffic seen in the observed period, i.e. links that make the CMDB "
                         "overstate the impact. Compare with radius declared vs real.")
def stale_links(component: str) -> str:
    box = _box()
    r = Q.radius(box.g, component, "declared")
    if not r.get("found"):
        return json.dumps({"error": f"no component {component}"})
    names = {component} | {x["name"] for x in r["impacted"]}
    rows = [{"parent": k[1], "child": k[2], "relationship": k[3]} for k in (x["key"] for x in Q.detect(box.g, "D2"))
            if k[1] in names or k[2] in names]
    return box.text({"component": component, "stale_links": rows, "count": len(rows)})


@server.tool(description="Open drift findings of one type across the estate (D1 undocumented dependency, D2 stale "
                         "relationship, D5 owner drift, D6 shadow IT, D9 protection-path drift, D10 backups failing, "
                         "D11 data residency, D12 access drift, D14 unregistered SMART on FHIR app).")
def findings_by_type(drift_type: str) -> str:
    box = _box()
    rows = [{k: f.get(k) for k in ("id", "type", "kind", "severity", "status", "explanation", "subjects", "key")}
            for f in F.list_findings(box.g, None, 500) if f.get("type") == drift_type]
    return box.text(rows or {"result": "no findings"})


@server.tool(description="Access path of an unregistered app: group -> users -> app -> API -> data.")
def unregistered_app_path(app: str) -> str:
    return _run("unregistered_app_path", app=app)


@server.tool(description="Recall memory about components: earlier investigations and their verdicts, facts people "
                         "confirmed (e.g. approved hidden links) and facts they rejected (do not re-raise those).")
def recall(components: list[str]) -> str:
    _box()
    return json.dumps(_memory().recall(components), default=str)[:30000]


def names():
    return sorted(t.name for t in server._tool_manager.list_tools())


if __name__ == "__main__":
    server.run("stdio")
