"""Agent action tools (AGENT_V2_SPEC 3). Not on the public MCP server; every action writes to the CMDB graph.

Notes, CAB flags, notifications and tickets are recorded at once (as `:Record` nodes, shown in the UI).
`propose_cmdb_fix` only marks the matching approval-gated findings as proposed by the agent: the CMDB changes
only when a person approves them, as before.
"""

import json

from agent import findings as F
from agent.approval import GraphRecords

KINDS = ("change_note", "cab_review", "notification", "ticket")
REASON = {"type": "string", "description": "Why, citing what the graph showed (one or two sentences)."}

SPECS = {
    "add_change_note": ("Add a ServiceNow-style work note to a change request, e.g. the real impact the CMDB misses.",
                        {"change_id": {"type": "string"}, "note": REASON}, ["change_id", "note"]),
    "request_cab_review": ("Flag a change for the Change Advisory Board because its real risk is higher than recorded.",
                           {"change_id": {"type": "string"}, "reason": REASON}, ["change_id", "reason"]),
    "notify_owner": ("Send a (mock) notification to a support group about a component, with the reason.",
                     {"team": {"type": "string"}, "component": {"type": "string"}, "reason": REASON},
                     ["team", "component", "reason"]),
    "propose_cmdb_fix": ("Propose the CMDB fix for components with open findings (a person must approve it).",
                         {"components": {"type": "array", "items": {"type": "string"}}, "reason": REASON},
                         ["components", "reason"]),
    "open_ticket": ("Open an incident ticket (ServiceNow incident shape) for a component.",
                    {"component": {"type": "string"}, "short_description": {"type": "string"},
                     "priority": {"type": "string", "enum": ["1 - Critical", "2 - High", "3 - Moderate", "4 - Low"]},
                     "assignment_group": {"type": "string"}},
                    ["component", "short_description", "priority", "assignment_group"]),
}


def specs():
    return [{"name": n, "description": d, "input_schema": {"type": "object", "properties": p, "required": r,
                                                            "additionalProperties": False}}
            for n, (d, p, r) in SPECS.items()]


class Actions:
    def __init__(self, g, investigation=""):
        self.g, self.rec, self.inv = g, GraphRecords(g), investigation

    def call(self, name, args):
        fn = getattr(self, name, None)
        if name not in SPECS or fn is None:
            return {"error": f"unknown action {name}"}
        try:
            return fn(**args)
        except TypeError as e:
            return {"error": f"bad arguments: {e}"}

    def _add(self, kind, body):
        self.rec.add(kind, {**body, "by": "agent", "investigation": self.inv})
        return {"recorded": kind, **body}

    def add_change_note(self, change_id, note):
        if not self.g.query("MATCH (c:Change {number: $n}) RETURN c", {"n": change_id}).result_set:
            return {"error": f"no change {change_id}"}
        return self._add("change_note", {"change": change_id, "note": note})

    def request_cab_review(self, change_id, reason):
        if not self.g.query("MATCH (c:Change {number: $n}) RETURN c", {"n": change_id}).result_set:
            return {"error": f"no change {change_id}"}
        return self._add("cab_review", {"change": change_id, "reason": reason, "state": "CAB review requested"})

    def notify_owner(self, team, component, reason):
        return self._add("notification", {"team": team, "components": [component], "message": reason,
                                          "channel": "agent-notification"})

    def propose_cmdb_fix(self, components, reason):
        want = set(components)
        hits = [f for f in F.list_findings(self.g, "open", 500) if want & set(f.get("subjects") or [])
                and f.get("action") == "cmdb_patch"]
        for f in hits:
            F.set_fields(self.g, f["id"], proposed_by="agent", agent_reason=reason)
        return {"proposed": [f["id"] for f in hits], "note": "the CMDB changes only after a person approves"}

    def open_ticket(self, component, short_description, priority, assignment_group):
        return self._add("ticket", {"table": "incident", "cmdb_ci": component, "short_description": short_description,
                                    "priority": priority, "assignment_group": assignment_group, "state": "New"})

    def taken(self, change=None, limit=50):
        out = []
        for k in KINDS:
            out += [{"kind": k, **r} for r in self.rec.list(k, limit) if r.get("by") == "agent"
                    and (change is None or r.get("change") == change)]
        return out


def summary(name, res):
    if isinstance(res, dict) and res.get("error"):
        return f"Error: {res['error']}"
    if name == "propose_cmdb_fix":
        return f"Proposed {len(res['proposed'])} CMDB fix(es) for approval"
    return {"add_change_note": "Work note added to the change", "request_cab_review": "CAB review requested",
            "notify_owner": f"Notified {res.get('team')}", "open_ticket": "Ticket opened"}.get(name, json.dumps(res)[:120])
