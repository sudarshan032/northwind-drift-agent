"""Numbers and CI-name grounding for LLM prose, and rule-based summaries of
tool results (the fallback when prose is unavailable or fails the check).

`check(text, facts, names)` returns the mismatches: every number in the prose
must occur in the facts (as a value, inside a fact string, or as the length
of a fact list), and every CI name in the prose must occur in the facts. A CI
name is a token that is a known graph name (`names`) or looks like one
(three or more hyphen-separated parts with a digit, e.g. lrc-db-02).
ponytail: value-level check, not context-level: a count that exists in the
facts under another label (e.g. a declared 0 quoted as a real 0) passes.
"""

import json
import logging
import re

log = logging.getLogger("northwind.grounding")

HYPHENS = dict.fromkeys(map(ord, "‐‑‒–—−"), "-")
DATES = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?Z?)?|\b\d{1,2}:\d{2}(?::\d{2})?\b")
TOKEN = re.compile(r"[A-Za-z0-9_.]+(?:-[A-Za-z0-9_.]+)+")
LIST_MARK = re.compile(r"^\s*\d+[.)]\s", re.M)
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])")
_names = {}


def graph_names(g):
    """All node names in the graph, lower case -> as stored (cached per graph)."""
    if g is None:
        return {}
    if g.name not in _names:
        rows = g.query("MATCH (n) WHERE n.name IS NOT NULL RETURN n.name").result_set
        _names[g.name] = {r[0].lower(): r[0] for r in rows if isinstance(r[0], str)}
    return _names[g.name]


def mentioned(text, names):
    """Graph names that the prose mentions, as stored, in order of first mention."""
    out = []
    for tok in TOKEN.findall((text or "").translate(HYPHENS)):
        n = names.get(tok.strip("._").lower())
        if n and n not in out:
            out.append(n)
    return out


COUNT_NAMES = {"components": "components", "routes": "interfaces", "applications": "applications",
               "departments": "departments", "hospitals": "hospitals", "business_services": "business services",
               "service_offerings": "service offerings"}


def counts_text(c):
    return ", ".join(f"{v} {COUNT_NAMES.get(k, k.replace('_', ' '))}" for k, v in c.items())


def step_found(tool, r):
    """One line: what a tool call returned (incident and chat step lists)."""
    if isinstance(r, dict) and r.get("error"):
        return f"Error: {r['error']}"
    if r in (None, [], {}) or (isinstance(r, dict) and (r.get("found") is False or r.get("result") == "no results")):
        return "Nothing found."
    if isinstance(r, dict) and r.get("nodes"):
        return "Path: " + " -> ".join(r["nodes"])
    if isinstance(r, dict) and r.get("counts"):
        return counts_text(r["counts"])
    if isinstance(r, list):
        keys = ("component", "name", "number", "team")
        first = [str(next((x[k] for k in keys if isinstance(x, dict) and x.get(k)), "")) for x in r[:3]]
        first = [x for x in first if x]
        return f"{len(r)} result{'s' if len(r) != 1 else ''}" + (f": {', '.join(first)}" if first else "") + \
            (" and more" if len(r) > 3 else "")
    if isinstance(r, dict) and r.get("props"):
        return f"{r.get('label')} at {r.get('site')}, managed by {r.get('managed_by')}"
    return "Result returned."


def _num(s):
    try:
        v = float(s.replace(",", ""))
    except ValueError:
        return None
    return int(v) if v.is_integer() else v


def vocabulary(facts):
    """(numbers, lower-case strings and tokens) present in the facts."""
    nums, words = set(), set()

    def walk(x):
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            nums.add(int(x) if float(x).is_integer() else x)
        elif isinstance(x, str):
            t = x.translate(HYPHENS)
            words.add(t.lower())
            words.update(m.lower() for m in TOKEN.findall(t))
            for m in NUMBER.findall(TOKEN.sub(" ", DATES.sub(" ", t))):
                nums.add(_num(m))
            for m in TOKEN.findall(t):  # numbers that are whole parts of a name (H51 -> no, 01 -> 1)
                nums.update(_num(p) for p in m.split("-") if p.isdigit())
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(k), walk(v)
        elif isinstance(x, (list, tuple, set)):
            nums.add(len(x))
            for v in x:
                walk(v)
    walk(json.loads(json.dumps(facts, default=str)))
    return nums, words


def check(text, facts, names=()):
    t = (text or "").translate(HYPHENS)
    nums, words = vocabulary(facts)
    bad = []
    for tok in TOKEN.findall(DATES.sub(" ", t)):
        tok = tok.strip("._")
        low = tok.lower()
        parts = low.split("-")
        ci_like = low in names or (len(parts) >= 3 and any(c.isdigit() for c in low))
        if ci_like and low not in words:
            bad.append(f"CI name {tok} not in facts")
    rest = TOKEN.sub(" ", DATES.sub(" ", LIST_MARK.sub(" ", t)))
    for m in NUMBER.findall(rest):
        v = _num(m)
        if v is not None and v not in nums:
            bad.append(f"number {m} not in facts")
    return sorted(set(bad))


def grounded(text, facts, names, job):
    """The prose if it passes, else None (and the mismatch is logged)."""
    bad = check(text, facts, names)
    if bad:
        log.warning("%s: LLM prose discarded, %s", job, "; ".join(bad[:10]))
        return None, bad
    return text, []


def summarise_trace(trace, question=""):
    """Rule-based answer from the tool results an agent loop gathered."""
    out = []
    for t in trace:
        r, name = t["result"], t["tool"]
        if name == "shared_dependencies" and isinstance(r, list) and r:
            out.append("Shared by every affected route: " + ", ".join(f"{x['component']} ({x['label']})"
                                                                       for x in r[:8]) + ".")
        elif name == "changes_on" and isinstance(r, list) and r:
            out.append("Changes on those components: " + "; ".join(
                f"{c['number']} {c['description']} on {c['target']} ({c['state']}, {c['start_date']})" for c in r[:5])
                + ".")
        elif name == "impact_path" and isinstance(r, dict) and r.get("nodes"):
            out.append("Impact path: " + " -> ".join(r["nodes"]) + ".")
        elif name == "who_to_page" and isinstance(r, list) and r:
            out.append("Who to page: " + "; ".join(
                f"{o['component']}: {o.get('observed_owner') or o.get('declared_owner')}" for o in r[:6]) + ".")
        elif name == "search_components" and isinstance(r, list) and r:
            out.append("Matching components: " + ", ".join(x["name"] for x in r[:8]) + ".")
        elif name == "radius" and isinstance(r, dict) and r.get("counts"):
            out.append(f"If {r['component']} fails ({'CMDB says' if r['layer'] == 'declared' else 'reality'}): "
                       + counts_text(r["counts"]) + ".")
    if not out:
        return "Rule-based summary: no tool evidence was gathered."
    return "Rule-based summary from the tool results. " + " ".join(out)
