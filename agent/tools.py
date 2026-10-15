"""Agent tools: thin wrappers over graph.queries plus create_finding,
propose_fix and page_owner. Tool results are JSON (truncated for the LLM).

Read tools never modify anything. Write tools touch only :Finding nodes and
the notifications feed; CMDB files and declared edges are off limits.
"""

import json
import os
from datetime import datetime, timezone

from agent import findings as F
from graph import queries as Q

MAX_CHARS = 30000


def _s(props, required=()):
    return {"type": "object", "properties": props, "required": list(required), "additionalProperties": False}


STR, INT = {"type": "string"}, {"type": "integer"}
LIST = {"type": "array", "items": {"type": "string"}}
SPECS = {
    "detect": ("Run one drift detector (D1, D2, D4-D12, D14) on the graph; returns finding rows with keys.",
               _s({"drift_type": STR}, ["drift_type"])),
    "radius": ("Blast radius if a component fails. layer=declared (CMDB only) or real (structure + observed).",
               _s({"component": STR, "layer": {"type": "string", "enum": ["declared", "real"]}},
                  ["component", "layer"])),
    "impact_path": ("Ordered impact path (node names and rules) from a failed component to an affected one.",
                    _s({"source": STR, "target": STR, "layer": {"type": "string", "enum": ["declared", "real"]}},
                       ["source", "target"])),
    "change_review": ("Declared vs real radius of a change's targets, hidden (undocumented) edges, risk level.",
                      _s({"change_id": STR}, ["change_id"])),
    "changes_on": ("Change requests (past and scheduled) targeting any of the components.",
                   _s({"components": LIST}, ["components"])),
    "shared_dependencies": ("Components that every given route/component needs in the real graph.",
                            _s({"routes": LIST}, ["routes"])),
    "search_components": ("Find components whose name or description contains a text.", _s({"text": STR}, ["text"])),
    "get_component": ("CMDB attributes, site and declared owner of one component.", _s({"name": STR}, ["name"])),
    "get_neighbourhood": ("Edges within depth (1-2) hops of a component.",
                          _s({"name": STR, "depth": INT}, ["name"])),
    "who_to_page": ("Declared (MANAGED_BY) vs observed (OBSERVED_OWNER) owner per component.",
                    _s({"components": LIST}, ["components"])),
    "protection_check": ("PHI components whose backup/replication chain is broken.", _s({})),
    "access_check": ("Accesses with no grant path, by disabled users, or admin outside change windows.", _s({})),
    "critical_nodes": ("Top 10 components by betweenness on the observed call graph.", _s({})),
    "unregistered_app_path": ("Access path group -> users -> unregistered app -> API -> data.",
                              _s({"app": STR}, ["app"])),
    "list_findings": ("Stored findings (optionally by status: open, approved, rejected).",
                      _s({"status": STR})),
    "create_finding": ("Record a finding (type, key, explanation, severity) about components.",
                       _s({"drift_type": STR, "key": LIST, "explanation": STR, "severity": STR,
                           "components": LIST}, ["drift_type", "key", "explanation"])),
    "propose_fix": ("Attach a proposed CMDB JSON patch to a finding (applied only after human approval).",
                    _s({"finding_id": STR, "patch": {"type": "array", "items": {"type": "object"}}},
                       ["finding_id", "patch"])),
    "page_owner": ("Page a team (mock): writes to notifications.jsonl and the in-app feed.",
                   _s({"team": STR, "message": STR, "components": LIST}, ["team", "message"])),
}
READ_ONLY = [n for n in SPECS if n not in ("create_finding", "propose_fix", "page_owner")]


class Toolbox:
    def __init__(self, g, runtime="runtime"):
        self.g, self.runtime = g, runtime
        self.trace = []  # every tool call and its (full) result, for citations and tests

    def specs(self, names=None):
        return [{"name": n, "description": SPECS[n][0], "input_schema": SPECS[n][1]} for n in (names or SPECS)]

    def call(self, name, args):
        fn = getattr(self, f"_{name}", None)
        if fn is None:
            res = {"error": f"unknown tool {name}"}
        else:
            try:
                res = fn(**args)
            except TypeError as e:
                res = {"error": f"bad arguments: {e}"}
        if res in (None, [], {}):
            res = {"result": "no results"}
        self.trace.append({"tool": name, "input": args, "result": res})
        return res

    def text(self, res):
        s = json.dumps(res, default=str)
        return s if len(s) <= MAX_CHARS else s[:MAX_CHARS] + f'... (truncated, {len(s)} chars)'

    # ---- read tools -----------------------------------------------------
    def _detect(self, drift_type):
        return Q.detect(self.g, drift_type) if drift_type in Q.DETECTORS else {"error": f"no detector {drift_type}"}

    def _radius(self, component, layer="real"):
        r = Q.radius(self.g, component, layer)
        if r.get("found"):  # keep the payload small: counts + names by type
            r = {k: r[k] for k in ("component", "layer", "counts", "by_label", "departments", "hospitals",
                                    "business_services", "service_offerings", "high_safety_services", "consumers")}
        return r

    def _impact_path(self, source, target, layer="real"):
        routes = None
        if not target.startswith(("svc-",)):
            r = Q.radius(self.g, source, layer)
            routes = r.get("by_label", {}).get("Interface") if r.get("found") else None
        return Q.impact_path(self.g, source, target, layer, via_routes=routes)

    def _change_review(self, change_id):
        r = Q.change_review(self.g, change_id)
        if r.get("found"):
            r = dict(r)
            r["per_target"] = [{"target": t["target"], "declared": t["declared"]["counts"],
                                "real": t["real"]["counts"]} for t in r["per_target"]]
        return r

    def _changes_on(self, components):
        return Q.changes_on(self.g, components)

    def _shared_dependencies(self, routes):
        return Q.shared_dependencies(self.g, routes)

    def _search_components(self, text):
        return Q.search_components(self.g, text)

    def _get_component(self, name):
        return Q.get_component(self.g, name)

    def _get_neighbourhood(self, name, depth=1):
        return Q.get_neighbourhood(self.g, name, depth, limit=150)

    def _who_to_page(self, components):
        return Q.who_to_page(self.g, components)

    def _protection_check(self):
        return Q.protection_check(self.g)

    def _access_check(self):
        return Q.access_check(self.g)

    def _critical_nodes(self):
        return Q.critical_nodes(self.g)

    def _unregistered_app_path(self, app):
        return Q.unregistered_app_path(self.g, app)

    def _list_findings(self, status=None):
        return [{k: f.get(k) for k in ("id", "type", "kind", "severity", "status", "explanation", "subjects")}
                for f in F.list_findings(self.g, status, limit=50)]

    # ---- write tools (findings and notifications only) -------------------
    def _create_finding(self, drift_type, key, explanation, severity="medium", components=()):
        f = {"id": F.finding_id(drift_type, key), "type": drift_type, "kind": F.KIND.get(drift_type, drift_type),
             "key": key, "keys": [key], "subjects": list(components), "severity": severity, "score": 0,
             "evidence": {}, "path": None, "patch": [], "explanation": explanation, "explained_by": "agent"}
        F.store(self.g, [f])
        return {"id": f["id"], "status": "open"}

    def _propose_fix(self, finding_id, patch):
        if F.get_finding(self.g, finding_id) is None:
            return {"error": "no such finding"}
        F.set_fields(self.g, finding_id, patch=patch)
        return {"id": finding_id, "patch_ops": len(patch), "note": "applied only after human approval"}

    def _page_owner(self, team, message, components=()):
        os.makedirs(self.runtime, exist_ok=True)
        n = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "team": team, "message": message,
             "components": list(components), "channel": "mock-pager"}
        with open(os.path.join(self.runtime, "notifications.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(n) + "\n")
        return {"paged": team, "delivered": "mock"}


def notifications(runtime="runtime", limit=100):
    path = os.path.join(runtime, "notifications.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(x) for x in f.read().splitlines()[-limit:]][::-1]
