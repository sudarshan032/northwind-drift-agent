"""AGENT_SPEC phase 2: agent jobs with a deterministic fake LLM (no key)."""

import hashlib
import json
import os
import shutil

import pytest

from agent import approval, findings as F, flows
from agent.llm import JOB_CAPS, NO_KEY, UNAVAILABLE, FakeLLM, LLMUnavailable
from agent.tools import READ_ONLY, SPECS, Toolbox
from graph import queries as Q
from tests import oracle
from tests.conftest import _load

pytestmark = pytest.mark.falkordb


def _hash_dir(d):
    h = hashlib.sha256()
    for name in sorted(os.listdir(d)):
        with open(os.path.join(d, name), "rb") as f:
            h.update(name.encode() + f.read())
    return h.hexdigest()


@pytest.fixture(scope="module")
def env(drift_data, tmp_path_factory):
    """A private copy of the data and graph (approval edits both)."""
    data = str(tmp_path_factory.mktemp("approve") / "data")
    shutil.copytree(drift_data, data)
    ld = _load(data, "northwind_test_agent")
    runtime = str(tmp_path_factory.mktemp("runtime"))
    yield ld.g, data, runtime
    ld.g.delete()


def test_triage_findings_and_ranking(env, drift_data):
    g, data, _ = env
    def batch(system, messages, tools):  # one call explains the whole top 10
        items = json.loads(messages[0]["content"])
        return {"text": json.dumps({"explanations": [{"id": f["id"], "text": f"Scripted explanation {i}."}
                                                     for i, f in enumerate(items)]})}
    llm = FakeLLM([batch])
    res = flows.triage(g, llm)
    top = res["top"]
    assert len(top) == 10 and top[0]["key"] == ["undocumented_group", "lrc-db-01"]
    assert top[0]["severity"] == "critical" and len(top[0]["keys"]) == 51
    assert all(f["explained_by"] == "llm" for f in top)
    assert len(llm.calls) == 1 and res["notice"] is None
    # every ground-truth finding key is covered by exactly the stored findings
    _, must, _, _ = oracle.expected(drift_data)
    stored = F.list_findings(g, limit=1000)
    assert {tuple(k) for f in stored for k in f["keys"]} == must
    head = F.get_finding(g, top[0]["id"])
    assert "lrc-db-01" in head["subjects"] and len(head["patch"]) == 51
    # LLM explanations are grounded on the facts it was given
    assert "lrc-db-01" in llm.calls[0]["messages"][0]["content"]


def test_llm_unavailable_falls_back_to_templates(env):
    g, _, _ = env
    res = flows.triage(g, FakeLLM([LLMUnavailable("429")]), top_n=3)
    assert res["notice"] == UNAVAILABLE and all(f["explained_by"] == "template" for f in res["top"])
    out = flows.change_review(g, "CHG-HEADLINE", FakeLLM([LLMUnavailable("budget")]))
    assert out["notice"] == UNAVAILABLE and "HIGH" in out["explanation"] and out["facts"]["risk"] == "high"
    out = flows.chat(g, "What is lrc-db-01?", FakeLLM([LLMUnavailable("budget")]))
    assert out["answer"].startswith(UNAVAILABLE)


def test_job_caps(env):
    g, _, _ = env
    llm = FakeLLM([{"tool": "search_components", "input": {"text": "lrc"}}] * 10)
    out = flows.chat(g, "loop forever", llm)
    assert len(llm.calls) == JOB_CAPS["chat"] == 4 and out["answer"].startswith("(stopped")
    assert [c["tool_choice"] for c in llm.calls] == [None, None, None, "none"]  # last call must answer
    llm = FakeLLM([{"tool": "search_components", "input": {"text": "lrc"}}] * 10)
    flows.incident(g, "loop forever", llm)
    assert len(llm.calls) == JOB_CAPS["incident"] == 6


def test_triage_without_llm_uses_templates(env):
    g, _, _ = env
    res = flows.triage(g, None, top_n=3)
    assert res["top"][0]["explained_by"] == "template" and "lrc-db-01" in res["top"][0]["explanation"]


def test_change_review_flow(env):
    g, _, _ = env
    out = flows.change_review(g, "CHG-HEADLINE", FakeLLM([{"text": "Scripted review."}]))
    f = out["facts"]
    assert f["risk"] == "high" and f["declared_services"] == ["svc-lab-results"]
    assert f["real"]["hospitals"] == 51 and len(f["hidden_edges"]) >= 51
    warn = {o["component"]: o for o in f["warn"]}
    assert warn["lrc-db-01"]["observed_owner"] == "HYD-Integration-Support" and warn["lrc-db-01"]["differs"]
    assert out["explanation"] == "Scripted review."
    assert "HIGH" in flows.change_review(g, "CHG-HEADLINE", None)["explanation"]


def _last_result(messages):
    block = messages[-1]["content"][-1]
    return json.loads(block["content"])


def test_incident_headline(env):
    g, _, _ = env
    routes = ["ORU-CRIT-H03", "ORU-CRIT-H17", "ORU-CRIT-H22"]

    def pick_changes(system, messages, tools):
        shared = [r["component"] for r in _last_result(messages)]
        assert "lrc-db-01" in shared and "dc1-host-07" in shared
        return {"tool": "changes_on", "input": {"components": shared}}

    def conclude(system, messages, tools):
        return {"text": "Likely cause: lrc-db-01 on dc1-host-07 is shared by all three routes; CHG-HEADLINE patches "
                        "dc1-host-07. Path: dc1-host-07 -> dc1-vm-lrc01 -> lrc-db-01 -> ORU-CRIT-H03 -> H03-ED."}

    llm = FakeLLM([{"tool": "search_components", "input": {"text": "ORU-CRIT-H03"}},
                   {"tool": "shared_dependencies", "input": {"routes": routes}},
                   pick_changes,
                   {"tool": "impact_path", "input": {"source": "dc1-host-07", "target": "H03-ED"}},
                   {"tool": "who_to_page", "input": {"components": ["lrc-db-01", "dc1-host-07"]}},
                   conclude])
    out = flows.incident(g, "ED critical results delayed at H03, H17 and H22", llm)
    assert [s["tool"] for s in out["steps"]] == ["search_components", "shared_dependencies", "changes_on",
                                                 "impact_path", "who_to_page"]
    assert "dc1-host-07 -> dc1-vm-lrc01 -> lrc-db-01 -> ORU-CRIT-H03 -> H03-ED" in out["paths"]
    changes = [m for m in llm.calls[3]["messages"] if m["role"] == "user"][-1]["content"][0]["content"]
    assert "CHG-HEADLINE" in changes
    assert "CHG-HEADLINE" in out["answer"]


def test_ambiguity_judgement(env):
    g, _, _ = env

    def judge(system, messages, tools):
        case = json.loads(messages[0]["content"])
        if case["kind"] == "D14":
            return {"text": json.dumps({"decision": "possible security risk, escalate",
                                        "reasons": ["no CMDB record", "reads EMR data via FHIR"]})}
        return {"text": json.dumps({"decision": "same CI, add alias", "reasons": ["serial or IP matches"]})}

    out = flows.ambiguity(g, FakeLLM([judge] * 10))
    kinds = sorted(c["kind"] for c in out)
    assert kinds == ["D13"] * 6 + ["D14"]
    d14 = next(c for c in out if c["kind"] == "D14")
    stored = F.get_finding(g, d14["finding"])
    assert stored["judgement"]["decision"] == "possible security risk, escalate"
    assert flows.parse_judgement("no json here")["decision"] == "needs human review"


def test_chat_cites_and_says_nothing_found(env):
    g, _, _ = env
    llm = FakeLLM([{"tool": "search_components", "input": {"text": "no-such-thing-xyz"}},
                   lambda s, m, t: {"text": "Nothing was found: " + json.dumps(_last_result(m))}])
    out = flows.chat(g, "What is no-such-thing-xyz?", llm)
    assert "no results" in out["answer"]
    llm2 = FakeLLM([{"tool": "impact_path", "input": {"source": "dc1-host-07", "target": "svc-emergency-care"}},
                    {"text": "See the cited path."}])
    out2 = flows.chat(g, "How does dc1-host-07 reach Emergency care?", llm2)
    assert out2["paths"] and out2["paths"][0].endswith("svc-emergency-care")


def test_no_key_degrades():
    assert flows.incident(None, "x", None)["answer"] == NO_KEY
    assert flows.chat(None, "x", None)["answer"] == NO_KEY


def test_tools_cannot_write_cmdb(env):
    g, data, runtime = env
    assert set(SPECS) - set(READ_ONLY) == {"create_finding", "propose_fix", "page_owner"}
    before = _hash_dir(os.path.join(data, "raw", "cmdb_export"))
    box = Toolbox(g, runtime)
    box.call("create_finding", {"drift_type": "D2", "key": ["stale", "a", "b"], "explanation": "x"})
    box.call("page_owner", {"team": "HYD-Integration-Support", "message": "lrc-db-01 at risk"})
    assert _hash_dir(os.path.join(data, "raw", "cmdb_export")) == before
    with open(os.path.join(runtime, "notifications.jsonl")) as f:
        assert json.loads(f.readline())["team"] == "HYD-Integration-Support"


def test_approve_and_reject(env):
    g, data, runtime = env
    flows.triage(g, None)
    head = F.list_findings(g, status="open", limit=1)[0]
    assert head["key"] == ["undocumented_group", "lrc-db-01"]
    owner = next(f for f in F.list_findings(g, limit=500) if f["type"] == "D5")
    other = next(f for f in F.list_findings(g, limit=500) if f["type"] == "D2")
    rels_before = sum(1 for _ in open(os.path.join(data, "raw", "cmdb_export", "cmdb_rel_ci.csv")))
    res = approval.approve(g, data, runtime, head["id"])
    assert res["status"] == "approved" and res["resolved"] is True
    assert sum(1 for _ in open(os.path.join(data, "raw", "cmdb_export", "cmdb_rel_ci.csv"))) == rels_before + 51
    assert not [r for r in Q.detect(g, "D1") if r["key"][2] == "lrc-db-01"]
    dec = Q.radius(g, "dc1-host-07", "declared")  # the CMDB now tells the truth
    assert "svc-emergency-care" in dec["business_services"]
    assert approval.approve(g, data, runtime, head["id"])["error"] == "finding is approved"
    res = approval.approve(g, data, runtime, owner["id"])
    assert res["resolved"] is True
    before = _hash_dir(os.path.join(data, "raw", "cmdb_export"))
    assert approval.reject(g, runtime, other["id"], reason="needed for failover tests")["status"] == "rejected"
    assert _hash_dir(os.path.join(data, "raw", "cmdb_export")) == before and F.get_finding(g, other["id"])["status"] == "rejected"
    with open(os.path.join(runtime, "audit.jsonl")) as f:
        actions = [json.loads(x)["action"] for x in f]
    assert actions == ["approve", "approve", "reject"]


def test_write_back_rule(env):
    """REALISM_SPEC 2/8: D9-D12 approvals write tickets, never CMDB files; CMDB
    patches touch only CMDB-appropriate fields and relationships."""
    g, data, runtime = env
    flows.triage(g, None)
    open_ = F.list_findings(g, status="open", limit=500)
    tickets_before = 0
    path = os.path.join(runtime, "tickets.jsonl")
    if os.path.exists(path):
        tickets_before = sum(1 for _ in open(path))
    for t in ("D9", "D10", "D11", "D12"):
        f = next(x for x in open_ if x["type"] == t)
        assert f["action"] == "ticket" and f["patch"] == [] and f["sources"]
        before = _hash_dir(os.path.join(data, "raw", "cmdb_export"))
        res = approval.approve(g, data, runtime, f["id"])
        assert res["ticket"]["table"] == ("incident" if t in ("D9", "D10") else "sn_si_incident")
        assert _hash_dir(os.path.join(data, "raw", "cmdb_export")) == before
    assert sum(1 for _ in open(path)) == tickets_before + 4
    for t in ("D2", "D4", "D6", "D7", "D8"):
        f = next(x for x in open_ if x["type"] == t)
        assert f["action"] == "cmdb_patch"
        for op in f["patch"]:
            approval.validate(op)  # every proposed op passes the write-back rule
    with pytest.raises(ValueError):
        approval.validate({"op": "replace", "path": "/cmdb_ci/emr-db-01/backup_offsite", "value": "false"})
    with pytest.raises(ValueError):
        approval.validate({"op": "replace", "path": "/cmdb_ci/emr-db-01/last_backup", "value": "x"})
    d14 = next(x for x in open_ if x["type"] == "D14")
    assert approval.reject(g, runtime, d14["id"])["ticket"]["table"] == "sn_si_incident"


def test_layers_tagged(env):
    g, _, _ = env
    q = lambda s: g.query(s).result_set[0][0]  # noqa: E731
    assert q("MATCH (n:Component) WHERE n.layer <> 'declared' RETURN count(n)") == 0
    assert q("MATCH ()-[e:OBSERVED_CALLS]->() WHERE e.layer <> 'evidence' RETURN count(e)") == 0
    assert q("MATCH ()-[e:IDENTIFIES]->() WHERE e.layer = 'reconciliation' RETURN count(e)") > 10000
