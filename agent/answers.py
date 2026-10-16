"""Public-site chat (SITE_SPEC 4): answers that cost no AI request first, then a small shared live allowance.

Order: small talk -> cached AI answer (the prewarmed suggested questions) -> rule-based answer from the graph
-> unclear question (welcome) -> live AI within the allowance -> one friendly sentence when it is used up.

Allowance: at most GLOBAL_MAX live answers per UTC day across all visitors and VISITOR_MAX per visitor, counted
in FalkorDB (`:Allowance` nodes). A visitor is an HMAC of the IP address and the day; the raw IP is never stored.
Each live answer may use LIVE_STEPS model calls, and none is made while the OpenRouter key has RESERVE or fewer
free requests left today, so the owner's last requests stay untouched.
"""

import datetime
import hashlib
import hmac
import os
import re

from agent import findings as F, grounding as G
from graph import queries as Q

GLOBAL_MAX, VISITOR_MAX, LIVE_STEPS, RESERVE = 15, 3, 2, 20

CHIPS = ["What breaks if dc1-host-07 is patched?", "Why is CHG-HEADLINE high risk?",
         "Which systems hold patient data without an off-site copy?", "Who should be warned about lrc-db-01?",
         "What is the biggest single point of failure?", "Which apps are used but not in the CMDB?"]
EXAMPLES = ["What breaks if lrc-db-01 fails?", "Who owns emr-db-01?", "Show the owner drift findings"]

SMALL_TALK = re.compile(r"^\s*(hi|hello|hey|hiya|yo|thanks|thank you|thx|cheers|ok|okay|cool|great|nice|bye|"
                        r"good (morning|afternoon|evening)|how are you|who are you|what are you|what can you do|"
                        r"help|test)\b[\s\w!?.,']{0,20}$", re.I)
DOMAIN = re.compile(r"cmdb|\bci\b|drift|finding|depend|break|fail|patch|risk|change|owner|own|warn|call|page|"
                    r"hospital|server|host|database|\bdb\b|interface|hl7|app|service|backup|replica|off-?site|"
                    r"patient|phi|data|storage|array|network|access|impact|radius|outage|down|incident|critical|"
                    r"source|evidence|emergency|lab|pharmacy|ehr|emr|fhir", re.I)
CHANGE_ID = re.compile(r"\bCHG[-A-Z0-9]*[A-Z0-9]", re.I)
BREAKS = re.compile(r"what (breaks|fails|stops|goes down|is affected|is impacted|happens)|blast radius|impact of|"
                    r"if .* (fails|is patched|goes down|is rebooted|is down|is taken down)", re.I)
OWNS = re.compile(r"who (owns|manages|runs|supports|should (i|we) (call|page|warn)|should be (warned|called|paged)|"
                  r"to (call|page|warn))|owner of|owners? for", re.I)
DEPENDS = re.compile(r"what (depends on|uses|needs)|who (depends on|uses)|depend(s|ing)? on|used by|consumers of", re.I)
RISKY = re.compile(r"risk|safe|dangerous|why .*(high|medium|low)", re.I)
FINDINGS = re.compile(r"finding|drift", re.I)
UNREGISTERED = re.compile(r"not in the cmdb|missing from the cmdb|unregistered|shadow it", re.I)
SEVERITIES = ("critical", "high", "medium", "low")


def _reply(answer, by, cis=(), paths=()):
    return {"answer": answer, "notice": None, "mismatches": [], "cis": list(cis), "steps": [], "paths": list(paths),
            "answered_by": by}


def welcome(unclear=False):
    lead = ("I am not sure what you are asking. " if unclear else "Hello. ") + (
        "I answer questions about Northwind Health's fictional IT estate from the graph, for example:")
    return {**_reply(lead + "\n\n" + "\n".join(f"- {q}" for q in EXAMPLES), "welcome"), "examples": EXAMPLES}


def _counts(c):
    return (f"{c['hospitals']} hospitals, {c['departments']} departments, {c['routes']} interfaces and "
            f"{c['business_services']} business services ({c['components']} components in all)")


LABELS = {"BusinessService": "business services", "ServiceOffering": "service offerings", "Interface": "interfaces",
          "Application": "applications", "Database": "databases", "VM": "virtual machines", "Host": "servers",
          "StorageArray": "storage arrays", "BackupTarget": "backup targets", "NetworkDevice": "network devices",
          "MedicalDevice": "medical devices", "Endpoint": "endpoints", "EndpointGroup": "endpoint groups",
          "ExternalService": "external services"}


def _safety(names, real):
    """Patient-safety services: business services by name, service offerings as a count."""
    svcs = [n for n in names if n in real["business_services"]]
    offs = len(names) - len(svcs)
    parts = [", ".join(f"`{s}`" for s in svcs)] if svcs else []
    if offs:
        parts.append(f"{offs} high-safety service offerings")
    return " and ".join(parts), svcs


def _breaks(g, ci):
    real, dec = Q.radius(g, ci, "real"), Q.radius(g, ci, "declared")
    if not real.get("found"):
        return None
    hs = real["high_safety_services"]
    text = (f"If **{ci}** fails or is patched, it reaches {_counts(real['counts'])}. "
            f"The CMDB alone knows about {dec['counts']['hospitals']} hospitals and {dec['counts']['components']} "
            f"components.")
    if hs:
        line, svcs = _safety(hs, real)
        text += f" Patient-safety services in reach: {line}."
        return _reply(text, "graph", [ci] + svcs)
    return _reply(text, "graph", [ci])


def _owners(g, ci):
    real = Q.radius(g, ci, "real")
    if not real.get("found"):
        return None
    near = [x["name"] for x in real["impacted"] if x["label"] in ("Database", "VM", "Application")][:12]
    rows = Q.who_to_page(g, [ci] + near)
    rows = [r for r in rows if r["declared_owner"] or r["observed_owner"]] if isinstance(rows, list) else []
    if not rows:
        return _reply(f"No owner is recorded for **{ci}** or the systems it affects.", "graph", [ci])
    lines = [f"- `{r['component']}`: CMDB owner {r['declared_owner'] or 'none recorded'}"
             + (f", but the team that actually works on it is {r['observed_owner']}" if r["differs"] else "")
             for r in sorted(rows, key=lambda r: (not r["differs"], r["component"]))[:8]]
    return _reply(f"Teams to warn about **{ci}** and the systems it affects:\n\n" + "\n".join(lines), "graph",
                  [r["component"] for r in rows[:8]])


def _depends(g, ci):
    real, dec = Q.radius(g, ci, "real"), Q.radius(g, ci, "declared")
    if not real.get("found"):
        return None
    by = {k: len(v) for k, v in real["by_label"].items()}
    parts = ", ".join(f"{n} {LABELS.get(k, k) if n != 1 else LABELS.get(k, k)[:-1]}" for k, n in sorted(by.items(), key=lambda x: -x[1])[:5])
    svcs = real["business_services"][:5]
    text = (f"In reality {real['counts']['components']} components depend on **{ci}** ({parts or 'none'}); "
            f"the CMDB records {dec['counts']['components']}.")
    if svcs:
        text += " Business services among them: " + ", ".join(f"`{s}`" for s in svcs) + "."
    return _reply(text, "graph", [ci] + svcs)


def _change(g, cid):
    r = Q.change_review(g, cid)
    if not r.get("found"):
        return None
    hi = r["newly_reached_high_safety_services"]
    real = {"business_services": [s for t in r["per_target"] for s in t["real"]["business_services"]]}
    line, svcs = _safety(hi, real)
    why = (f"its real impact reaches patient-safety services the CMDB does not connect to it: {line}" if hi else
           "its real impact is larger than the CMDB says" if r["risk"] == "medium" else
           "the CMDB and the evidence agree on its impact")
    text = (f"**{cid}** ({r['description']}) is **{r['risk']} risk** because {why}. The CMDB counts "
            f"{r['declared_count']} affected components, the evidence {r['real_count']}, with "
            f"{len(r['undocumented_edges'])} hidden dependencies.")
    return _reply(text, "graph", list(r["targets"]) + svcs)


def _findings(g, q):
    t = re.search(r"\bD(\d{1,2})\b", q, re.I)
    sev = next((s for s in SEVERITIES if re.search(rf"\b{s}\b", q, re.I)), None)
    kind = next((k for k, v in F.KIND.items() if v in q.lower()), None)
    dtype = f"D{t.group(1)}" if t else kind
    if not (dtype or sev):
        return None
    fs = [f for f in F.list_findings(g, "open", 500) if (not dtype or f["type"] == dtype)
          and (not sev or f["severity"] == sev)]
    label = " ".join(x for x in (sev, F.KIND.get(dtype, dtype) if dtype else None) if x)
    if not fs:
        return _reply(f"There are no open {label} findings.", "graph")
    subjects = [s for f in fs for s in (f.get("subjects") or [])[:1]]
    lines = [f"- {f['severity']}: `{(f.get('subjects') or ['?'])[0]}`, {f['kind']}" for f in fs[:8]]
    more = f"\n\nAnd {len(fs) - 8} more in the Findings tab." if len(fs) > 8 else ""
    return _reply(f"{len(fs)} open {label} findings:\n\n" + "\n".join(lines) + more, "graph", subjects[:8])


def _unregistered(g):
    fs = [f for f in F.list_findings(g, "open", 500) if f["type"] in ("D6", "D14")]
    if not fs:
        return _reply("Every system seen in the evidence is in the CMDB.", "graph")
    lines = [f"- `{(f.get('subjects') or [f['key'][1]])[0]}`: {f['kind']}" for f in fs[:10]]
    more = f"\n\nAnd {len(fs) - 10} more in the Findings tab." if len(fs) > 10 else ""
    return _reply(f"{len(fs)} systems are seen in use or on the network but are not in the CMDB:\n\n"
                  + "\n".join(lines) + more, "graph", [s for f in fs for s in (f.get("subjects") or [])[:1]][:10])


def rule_answer(g, q):
    """Common questions answered from one graph query and a template; None when no rule fits."""
    if UNREGISTERED.search(q):
        return _unregistered(g)
    m = CHANGE_ID.search(q)
    if m and (RISKY.search(q) or len(q.split()) <= 4):
        r = _change(g, m.group(0).upper())
        if r:
            return r
    cis = [c for c in G.mentioned(q, G.graph_names(g)) if not CHANGE_ID.fullmatch(c)]
    if cis:
        ci = cis[0]
        for rx, fn in ((OWNS, _owners), (DEPENDS, _depends), (BREAKS, _breaks)):
            if rx.search(q):
                r = fn(g, ci)
                if r:
                    return r
    if FINDINGS.search(q):
        return _findings(g, q)
    return None


def visitor_key(ip, day):
    secret = (os.environ.get("ALLOWANCE_SALT") or os.environ.get("CRON_SECRET") or "northwind").encode()
    return hmac.new(secret, f"{day}|{ip}".encode(), hashlib.sha256).hexdigest()[:20]


TAKE = """
MERGE (a:Allowance {key: $gk}) ON CREATE SET a.used = 0, a.day = $day
MERGE (v:Allowance {key: $vk}) ON CREATE SET v.used = 0, v.day = $day
WITH a, v WHERE a.used < $gmax AND v.used < $vmax
SET a.used = a.used + 1, v.used = v.used + 1
RETURN a.used, v.used
"""


def take_allowance(g, ip, day=None):
    """One live answer from the shared allowance: (True, None) or (False, 'site' | 'visitor')."""
    day = day or datetime.datetime.now(datetime.UTC).date().isoformat()
    g.query("MATCH (a:Allowance) WHERE a.day <> $day DELETE a", {"day": day})
    vk = f"visitor:{visitor_key(ip, day)}"
    p = {"gk": f"site:{day}", "vk": vk, "day": day, "gmax": GLOBAL_MAX, "vmax": VISITOR_MAX}
    if g.query(TAKE, p).result_set:
        return True, None
    used = dict(g.query("MATCH (a:Allowance) WHERE a.key IN [$gk, $vk] RETURN a.key, a.used", p).result_set)
    return False, "site" if used.get(p["gk"], 0) >= GLOBAL_MAX else "visitor"


def quota_left(llm):
    """False when the OpenRouter key is at or below the owner's reserve (or its count is unknown)."""
    if hasattr(llm, "over_cap"):  # OpenAI: the shared daily dollar cap
        return not llm.over_cap()
    s = llm.status() if hasattr(llm, "status") else {}
    if s.get("provider") != "openrouter":
        return True
    return s.get("source") == "openrouter /key" and (s.get("remaining") or 0) > RESERVE


LIMIT_TEXT = {
    "site": "Today's live AI answers for this demo are used up. The suggested questions still work, and questions "
            "like the examples below are answered straight from the graph.",
    "visitor": "You have used your live AI answers for today. The suggested questions still work, and questions "
               "like the examples below are answered straight from the graph.",
    "off": "Live AI answers are not available on this site right now. The suggested questions still work, and "
           "questions like the examples below are answered straight from the graph.",
}


def limited(reason):
    return {**_reply(LIMIT_TEXT[reason] + "\n\n" + "\n".join(f"- {q}" for q in EXAMPLES), "limit"),
            "examples": EXAMPLES}


def public_chat(g, q, cached, ip, live=None):
    from agent import flows
    from agent.llm import NOT_CACHED
    if SMALL_TALK.match(q) or len(q.strip()) < 3:
        return welcome()
    if cached is not None:
        out = flows.chat(g, q, cached)
        if out["notice"] != NOT_CACHED:
            return {**out, "answered_by": "cached_ai"}
    r = rule_answer(g, q)
    if r:
        return r
    if not (DOMAIN.search(q) or CHANGE_ID.search(q) or G.mentioned(q, G.graph_names(g))):
        return welcome(unclear=True)
    if live is None:
        return limited("off")
    if not quota_left(live):
        return limited("site")
    ok, why = take_allowance(g, ip)
    if not ok:
        return limited(why)
    return {**flows.chat(g, q, live, max_steps=LIVE_STEPS), "answered_by": "live_ai"}
