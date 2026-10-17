# Progress log (autonomous data-phase run)

Re-read this file, RUN_PROMPT.md and DATA_SPEC.md after any context compaction.

## M1 Scaffold — done
- pyproject.toml (+ uv.lock), Python 3.11 via uv, docker-compose.yml (falkordb/falkordb:v4.22.0), .gitignore, data/README.md.
- FalkorDB answers `RETURN 1+1`.
- Next: M2 truth model (ingest/world.py) + plausibility tests.

## M2 Truth model — done
- ingest/world.py; tests/test_world.py (9.4 + footprint/hygiene).

## M3+M4 Views, oracle, drift — done (one commit, DECISIONS #27)
- ingest/views.py, ingest/drift.py, ingest/generate.py, tests/helpers/oracle.py, tests/test_views.py.
- 9.1 determinism, 9.2 zero findings without drift, 9.3 exact catalogue, 9.5 headline, 9.6 size.

## M5 Loader — done
- ingest/load.py; tests/test_loader.py (9.7 idempotency, D6 count, D13 aliases, headline path).
- Full load ≈ 3 s.

## M6 Research — in progress
- RESEARCH_NOTES.md written; fixes applied (imaging ranges, fleets, HL7 volumes/mix).
- Next: independent spec-conformance review (subagent) x2, fix findings.
- Review #1 (20 findings) and review #2 (no high findings) done; fixes in DECISIONS #33, #34. 28 tests pass.

## M7 Clean-room verification — in progress
- Script: scratchpad cleanroom.sh (down -v, delete data/.venv/caches, uv sync --frozen, generate, pytest, load twice, sanity queries, hashes).
- Run 1 and run 2: 28/28 tests, loader idempotent (49,056 nodes / 139,693 edges), sanity queries OK, data hashes identical.

## M8 Report — done
- REPORT.md written. All milestones complete.

# AGENT_SPEC run
## Phase 0 — data follow-ups (a)-(f) done; impact layer (graph/impact.py), radius/path queries (graph/queries.py); VERIFY.md via tests/helpers/verify_report.py after a clean-room run.
## Phase 0 — done (VERIFY.md all PASS, commit d8a7061)
## Phase 1 — done: graph/queries.py detectors (exact vs ground truth, full + demo), radius, paths, change review, protection, access, who-to-page, critical nodes; tests/test_queries.py.
- Next: Phase 2 agent (LangGraph, fake LLM for tests).
## Phase 2 — done: agent/ (llm, tools, findings, approval, flows); tests/test_agent.py with FakeLLM; 58 tests pass. No API key: live check pending.
- Next: Phase 3 API (FastAPI).
## Phase 3 — done: api/main.py, tests/test_api.py.
## Phase 4 — done: ui/index.html; screenshots in docs/.
# REALISM run
- Step A done: config module, write-back rule + tickets, CSDM offerings, layer tags. Next: source-native raw formats + connectors.
- Realism steps A-D done (config, write-back+tickets, offerings, layers; 11 source-native formats + connectors; connector tests; dependency view + Sources tab). Next: README, screenshots, clean-room, REPORT Realism section.
- 2026-10-16 Realism complete: docs, screenshots, clean-room run 5 (79 passed, VERIFY all passing), REPORT Realism section.
- 2026-10-16 Grounding + final no-tools loop step; prewarm used 11 requests; master fast-forwarded to realism (0c851fe); fresh clone + docker compose up --build: CHG-HEADLINE high without keys.
- 2026-10-16 UI redesign (UI_SPEC): tokens, SVG dependency view with grouping, all screens, axe 0 violations, demo recorded; 93 tests.
- 2026-10-16 Public deployment on Vercel + FalkorDB Cloud (deploy branch); 97 tests.
- 2026-10-16 Benchmark: generator, loaders, runner, analysis; 250k pilot passed the correctness gate; sizes 50k/500k/3.5M; full run started in the background.
- 2026-10-16 Benchmark verified (gate passed at 4 sizes), charts, RESULTS.md, README, Scale tab, redeploy.
- 2026-10-16 Session 2 experiments: arm64 check, bulk loader vs COPY, raw relationships, server-side times, hybrid A-D; RESULTS.md + charts; website/README untouched.
- 2026-10-16 Client overhead measured (current client, hiredis, redis-cli); hiredis added to dependencies.
- 2026-10-16 Repository reorganised (docs/specs, docs/records, docs/design, docs/screenshots, docs/media, tests/helpers, LICENSE).
- 2026-10-16 SITE_SPEC: overview, guided tour, glossary, plain-language screens, public chat routing + live allowance, Server-Timing, stored results, edge caching; prewarm of the chips.
- 2026-10-16 Paired client re-run (pure-Python parser vs hiredis, same session) at 536k and 3.47M; unpaired hiredis attempt archived; RESULTS.md section added.
- 2026-10-16 AGENT_V2 sections 1-6 built with the fake LLM (OpenAI client, MCP tools, actions, memory, guardian, trace UI, scenarios); waiting for OpenAI keys for the eval.
- 2026-10-16 AGENT_V2 eval 4/5 (gpt-6-luna; first run 2/5), recorded runs, preview checked (live run, /mcp 18 tools, memory recall after data reset), merged to master.
- 2026-10-16 Held-out scenarios H1-H4 run once: 3/4; README tracks section.
- 2026-10-17 Service-map fix (per-hospital offerings), data regenerated, local and cloud reloaded, eval v2 data 7/9.
- 2026-10-17 Explanations and chat on OpenAI gpt-6-luna; prewarm $0.0063, all grounded.
- 2026-10-17 Site polish: hero, 7-step tour with the agent, visible memory, plain trace steps, accuracy wording, numbers footnote, proof lines, glossary, footer.
- 2026-10-17 Tour always replays the recorded run; heartbeat events every 5 s while the agent waits.
- 2026-10-17 UI polish (header buttons right, hero with chain, memory only in its block, verdict labels and action list, tour card placement); plain D9/D11 explanations, triage re-prewarmed ($0.0008).
