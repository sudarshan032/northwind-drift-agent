"""Agent memory in FalkorDB (AGENT_V2_SPEC 4): a separate graph per tenant, next to the CMDB graph.

`northwind_memory` is the public demo's memory; `northwind_memory_<user>` isolates one user (FalkorDB holds many
graphs in one instance). Nodes:
  Investigation {id, kind, trigger, time, model, cost, verdict, recorded}  -[:HAS_STEP]-> Step {n, tool, args,
  summary, why, ms, cost}; -[:DECIDED]-> Decision {risk, actions}; -[:ABOUT]-> Ref {name}
  Fact {id, statement, status (confirmed | rejected | proposed), source, time, finding, evidence}  -[:ABOUT]-> Ref
  Feedback {finding, decision, comment, approver, time} -[:ON]-> Fact
`Ref {name}` stands for a component of the CMDB graph (a cross-graph reference by name).
"""

import hashlib
import json
import re
import time
from datetime import datetime, timezone

BASE = "northwind_memory"


def graph_name(user=None):
    if not user:
        return BASE
    return f"{BASE}_{re.sub(r'[^a-z0-9]+', '_', str(user).lower()).strip('_')[:40] or 'anon'}"


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def evidence_hash(evidence):
    return hashlib.sha1(json.dumps(evidence, sort_keys=True, default=str).encode()).hexdigest()[:12]


class Memory:
    def __init__(self, db, user=None):
        self.db, self.name = db, graph_name(user)
        self.g = db.select_graph(self.name)

    def q(self, cypher, params=None):
        return self.g.query(cypher, params or {}).result_set

    # ---- investigations ---------------------------------------------------
    def record(self, inv):
        """Store one finished investigation (its steps, decision and the components it touched); returns its id."""
        iid = inv.get("id") or f"INV-{time.time_ns()}"
        v = inv.get("verdict") or {}
        self.q("CREATE (i:Investigation {id: $id, kind: $kind, trigger: $trigger, time: $time, model: $model, "
               "cost: $cost, verdict: $verdict, recorded: $rec, valid: $valid})",
               {"id": iid, "kind": inv["kind"], "trigger": inv["trigger"], "time": inv.get("time") or now(),
                "model": inv.get("model", ""), "cost": float(inv.get("cost", 0)), "verdict": json.dumps(v),
                "rec": bool(inv.get("recorded")), "valid": bool(inv.get("valid", True))})
        steps = [{"n": s["n"], "tool": s["tool"], "args": json.dumps(s.get("args", {})), "summary": s.get("summary", ""),
                  "why": s.get("why", ""), "ms": s.get("ms", 0), "cost": float(s.get("cost", 0)),
                  "action": bool(s.get("action"))} for s in inv.get("steps", [])]
        if steps:
            self.q("MATCH (i:Investigation {id: $id}) UNWIND $steps AS s CREATE (i)-[:HAS_STEP]->(:Step {n: s.n, "
                   "tool: s.tool, args: s.args, summary: s.summary, why: s.why, ms: s.ms, cost: s.cost, "
                   "action: s.action})", {"id": iid, "steps": steps})
        self.q("MATCH (i:Investigation {id: $id}) CREATE (i)-[:DECIDED]->(:Decision {risk: $risk, actions: $actions})",
               {"id": iid, "risk": v.get("risk") or "", "actions": json.dumps(v.get("actions") or [])})
        self._about("Investigation", iid, inv.get("components") or [])
        return iid

    def _about(self, label, nid, names):
        if names:
            self.q(f"MATCH (x:{label} {{id: $id}}) UNWIND $names AS n MERGE (r:Ref {{name: n}}) MERGE (x)-[:ABOUT]->(r)",
                   {"id": nid, "names": sorted(set(names))})

    def investigations(self, limit=20):
        rows = self.q("MATCH (i:Investigation) RETURN i.id, i.kind, i.trigger, i.time, i.model, i.cost, i.verdict, "
                      "i.recorded ORDER BY i.time DESC, i.id DESC LIMIT $n", {"n": limit})
        return [{"id": r[0], "kind": r[1], "trigger": r[2], "time": r[3], "model": r[4], "cost": r[5],
                 "verdict": json.loads(r[6] or "{}"), "recorded": r[7]} for r in rows]

    def investigation(self, iid):
        head = [x for x in self.investigations(1000) if x["id"] == iid]
        if not head:
            return None
        rows = self.q("MATCH (:Investigation {id: $id})-[:HAS_STEP]->(s:Step) RETURN s ORDER BY s.n", {"id": iid})
        steps = []
        for (node,) in rows:
            p = dict(node.properties)
            p["args"] = json.loads(p.get("args") or "{}")
            steps.append(p)
        return {**head[0], "steps": steps}

    # ---- facts and feedback (the learning loop) ----------------------------
    def fact(self, names, statement, status, source, finding="", evidence=""):
        fid = f"FACT-{time.time_ns()}"
        self.q("CREATE (:Fact {id: $id, statement: $s, status: $st, source: $src, time: $t, finding: $f, "
               "evidence: $e})", {"id": fid, "s": statement, "st": status, "src": source, "t": now(), "f": finding,
                                  "e": evidence})
        self._about("Fact", fid, names)
        return fid

    def feedback(self, finding, decision, approver="human", comment="", names=(), statement="", evidence=""):
        """A human approved or rejected a finding: a confirmed or rejected Fact plus the Feedback on it."""
        status = "confirmed" if decision == "approve" else "rejected"
        fid = self.fact(names, statement or f"A person {'approved' if status == 'confirmed' else 'rejected'} "
                        f"finding {finding}", status, f"feedback from {approver}", finding, evidence)
        self.q("MATCH (f:Fact {id: $fid}) CREATE (:Feedback {finding: $finding, decision: $d, comment: $c, "
               "approver: $a, time: $t})-[:ON]->(f)",
               {"fid": fid, "finding": finding, "d": decision, "c": comment, "a": approver, "t": now()})
        return fid

    def recall(self, names, limit=10):
        """Prior investigations, confirmed and rejected facts about any of the components."""
        names = sorted(set(names))
        facts = self.q("MATCH (f:Fact)-[:ABOUT]->(r:Ref) WHERE r.name IN $n "
                       "RETURN DISTINCT f.statement, f.status, f.source, f.time, f.finding ORDER BY f.time DESC LIMIT $l",
                       {"n": names, "l": 3 * limit})
        invs = self.q("MATCH (i:Investigation)-[:ABOUT]->(r:Ref) WHERE r.name IN $n "
                      "RETURN DISTINCT i.id, i.kind, i.trigger, i.time, i.verdict ORDER BY i.time DESC LIMIT $l",
                      {"n": names, "l": limit})
        pick = lambda st: [{"statement": f[0], "source": f[2], "time": f[3], "finding": f[4]} for f in facts
                           if f[1] == st][:limit]
        return {"confirmed": pick("confirmed"), "rejected": pick("rejected"),
                "investigations": [{"id": i[0], "kind": i[1], "trigger": i[2], "time": i[3],
                                    "risk": json.loads(i[4] or "{}").get("risk")} for i in invs]}

    def rejected(self):
        """finding id -> evidence hash at the time a person rejected it (triage does not re-raise these)."""
        return {r[0]: r[1] for r in self.q("MATCH (f:Fact {status: 'rejected'}) WHERE f.finding <> '' "
                                           "RETURN f.finding, f.evidence")}

    def reset(self):
        if self.name in self.db.list_graphs():
            self.g.delete()
        self.g = self.db.select_graph(self.name)
