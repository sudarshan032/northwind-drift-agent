# Agent evaluation (gpt-6-luna)

## Held-out set (gpt-6-luna, run once)

Run 2026-10-16 23:10 IST with `python -m agent.eval`: one live run per scenario (ingest/scenarios.py), against the demo graph, memory graph `northwind_memory_eval` (empty at start). Correct = right root cause, accepted risk, expected owner warned and the verdict passed the grounding checks.

| Scenario | Root cause | Risk | Owners warned | Grounding | Steps | Cost | Time | Actions taken |
|---|---|---|---|---|---|---|---|---|
| H1-protection | yes | yes (high) | yes | yes | 8 | $0.0015 | 29.5 s | open_ticket |
| H2-residency | yes | yes (high) | yes | yes | 7 | $0.0016 | 26.2 s | notify_owner, notify_owner |
| H3-access | yes | yes (high) | yes | yes | 6 | $0.0008 | 24.1 s | open_ticket |
| H4-shadow | yes | yes (medium) | no | yes | 8 | $0.0016 | 28.2 s | open_ticket |

**3/4 correct.** Total $0.0054, mean $0.0014 per investigation.


**How to read these results.** S1-S5 were used during development: the first run scored 2/5, and after adding two
tools (`stale_links`, `findings_by_type`) and clearer risk rules the second run scored 4/5 (both tables below). The
held-out set H1-H4 (drift types D9, D11, D12, D6, not used while tuning) was added afterwards, with its expected
answers committed before the run (commit 2591872), and run once with the same tools and prompt; nothing was changed
after seeing its results.

H4: the agent found VENDOR-RAS-GW and gave an accepted risk, but warned NW-Imaging-Systems instead of the H03 site IT
team or NW-SecOps, so it counts as wrong.

## Development set S1-S5, run 2 (current tools and prompt)

Run 2026-10-16 22:43 IST with `python -m agent.eval`: one live run per scenario (ingest/scenarios.py), against the demo graph, memory graph `northwind_memory_eval` (empty at start). Correct = right root cause, accepted risk, expected owner warned and the verdict passed the grounding checks.

| Scenario | Root cause | Risk | Owners warned | Grounding | Steps | Cost | Time | Actions taken |
|---|---|---|---|---|---|---|---|---|
| S1-headline | yes | yes (high) | yes | yes | 9 | $0.0032 | 36.9 s | add_change_note, request_cab_review |
| S2-backup | yes | yes (high) | yes | yes | 7 | $0.0010 | 24.5 s | open_ticket |
| S3-owner | yes | yes (medium) | yes | yes | 8 | $0.0011 | 29.1 s | notify_owner, open_ticket |
| S4-fhir-app | yes | yes (high) | yes | yes | 5 | $0.0009 | 19.2 s | open_ticket |
| S5-stale | yes | yes (low) | no | yes | 9 | $0.0021 | 31.0 s | add_change_note |

**4/5 correct.** Total $0.0084, mean $0.0017 per investigation.

S5: the agent found the stale links and gave the right risk, but warned the owners of portal-app and sched-app
rather than NW-DBA (the owner of sched-db-01), so it counts as wrong. Luna scored 4/5, so the escalation model was
not used (AGENT_V2_SPEC 7).

## Development set S1-S5, run 1 (first version, same day, kept as measured)

| Scenario | Root cause | Risk | Owners warned | Grounding | Steps | Cost | Time | Actions taken |
|---|---|---|---|---|---|---|---|---|
| S1-headline | yes | yes (high) | yes | yes | 9 | $0.0032 | 35.4 s | add_change_note |
| S2-backup | yes | yes (high) | yes | yes | 7 | $0.0010 | 27.6 s | open_ticket |
| S3-owner | yes | no (low) | yes | yes | 9 | $0.0012 | 32.4 s | notify_owner, open_ticket, propose_cmdb_fix |
| S4-fhir-app | no | no (medium) | no | yes | 9 | $0.0010 | 29.7 s | none |
| S5-stale | no | no (medium) | no | yes | 9 | $0.0020 | 30.6 s | propose_cmdb_fix |

**2/5 correct.** Total $0.0084, mean $0.0017 per investigation.

What changed between the runs (tools and prompt, not the scenarios or the scoring):
- two MCP tools: `stale_links` (CMDB dependencies with no traffic inside a declared radius) and `findings_by_type`
  (estate-wide findings, e.g. D14 unregistered apps); in run 1 no tool could list unregistered apps or stale
  links, so S4 and S5 could not be solved;
- the prompt's risk rules now distinguish changes (including "the CMDB overstates the impact") from incidents
  (degraded clinical service = medium), which fixed S3's risk.
- A tool error found in the recorded control run (`get_neighbourhood` failed on an argument-name clash) was fixed
  and that run re-recorded; it did not affect the scored runs.

