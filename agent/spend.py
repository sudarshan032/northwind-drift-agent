"""Agent cost guard (AGENT_V2_SPEC 1): dollars per call from token counts, a daily cap across all keys
(AGENT_DAILY_USD, default 1.00) and a per-visitor limit on the public site (AGENT_VISITOR_RUNS, default 5 per
day, hashed IP). Counted in the CMDB graph as `:AgentSpend` nodes (not touched by the demo reset)."""

import datetime
import os

from agent.answers import visitor_key


def cap():
    return float(os.environ.get("AGENT_DAILY_USD", "1.00"))


def visitor_max():
    return int(os.environ.get("AGENT_VISITOR_RUNS", "5"))


def day():
    return datetime.datetime.now(datetime.UTC).date().isoformat()


def today(g):
    rows = g.query("MATCH (s:AgentSpend {key: $k}) RETURN s.usd", {"k": f"usd:{day()}"}).result_set
    return float(rows[0][0]) if rows else 0.0


def start(g, ip):
    """Book one investigation for a visitor: (True, None) or (False, 'cap' | 'visitor')."""
    d = day()
    g.query("MATCH (s:AgentSpend) WHERE s.day <> $d DELETE s", {"d": d})
    if today(g) >= cap():
        return False, "cap"
    rows = g.query("MERGE (v:AgentSpend {key: $k}) ON CREATE SET v.runs = 0, v.day = $d "
                   "WITH v WHERE v.runs < $max SET v.runs = v.runs + 1 RETURN v.runs",
                   {"k": f"visitor:{visitor_key(ip, d)}", "d": d, "max": visitor_max()}).result_set
    return (True, None) if rows else (False, "visitor")


def charge(g, usd):
    """Add a call's cost; False once today's total reaches the cap (the investigation then stops)."""
    d = day()
    rows = g.query("MERGE (s:AgentSpend {key: $k}) ON CREATE SET s.usd = 0.0, s.day = $d "
                   "SET s.usd = s.usd + $u RETURN s.usd", {"k": f"usd:{d}", "d": d, "u": float(usd)}).result_set
    return float(rows[0][0]) < cap()


MESSAGES = {
    "cap": "Today's budget for live agent runs is used up, so this shows the recorded run instead.",
    "visitor": "You have used your live agent runs for today, so this shows the recorded run instead.",
    "off": "The live agent is not configured on this site, so this shows the recorded run instead.",
}
