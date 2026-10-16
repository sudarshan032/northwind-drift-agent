# CLAUDE.md: Northwind drift agent (FalkorDB "Graph Hacks" hackathon, Oct 15–18 2026)

Read this first in every session. Keep it short; details live in the files listed below.

## What this project is
A CMDB drift agent for a fictional US health system, "Northwind Health" (51 hospitals, ~1,000 clinics, ~800 non-acute sites, 2 data centres, 2 cloud regions, a Hyderabad GCC). It compares what the CMDB declares with what discovery evidence shows, stores both in one FalkorDB graph, finds drift, computes declared vs real blast radius, reviews changes, and proposes human-approved fixes (CMDB patches or tickets).

Headline: CHG-HEADLINE patches `dc1-host-07`. CMDB says it affects lab reporting only; the interface engine's channel config shows all 51 `ORU-CRIT-*` interfaces stage in `lrc-db-01` on that host, so it really reaches 51 hospitals' Emergency departments and Emergency care. CHG0031513 (emr-app) is the low-risk control.

All data is synthetic, deterministic (seed 42), no personal data.

## Where things are
- Specs in [docs/specs/](docs/specs/README.md) (history, newest wins on conflict): SPEC.md (original, mostly superseded) → DATA_SPEC.md → RUN_PROMPT.md → AGENT_SPEC.md → REALISM_SPEC.md → UI_SPEC.md → BENCH_SPEC.md.md.
- Records in docs/records/: [DECISIONS.md](docs/records/DECISIONS.md) (numbered decisions), [RESEARCH_NOTES.md](docs/records/RESEARCH_NOTES.md) (sources), [PROGRESS.md](docs/records/PROGRESS.md), [REPORT.md](docs/records/REPORT.md), [VERIFY.md](docs/records/VERIFY.md), the prep-session log; plus [README.md](README.md) at the root.
- Design and media: docs/design/ui-plan.md, docs/screenshots/ (before/ = old UI), docs/media/demo.webm.
- Code: `ingest/` (world.py truth model, generate.py, drift.py, views, sources/ connectors, load.py, config.py thresholds), `graph/` (queries.py, impact.py, connection.py), `agent/` (LLM jobs, prewarm), `api/main.py`, `deploy/` (cloud loader, cached AI bundle), `ui/`, `index.py` (Vercel entry, must stay at the root), `tests/` (helpers/ = oracle, sanity queries, VERIFY writer), `bench/` (scale benchmark; [bench/RESULTS.md](bench/RESULTS.md)).
- Data: `data/` (full, git-ignored, regenerate with `uv run python -m ingest.generate --seed 42 --out data/`), `data_demo/` (committed subset).

## Commands
- Env: `uv sync --frozen` (Python 3.11 via uv). Tests: `uv run python -m pytest -q`.
- Local stack: `docker compose up --build` (FalkorDB + seed + API/UI on :8000).
- Load: `uv run python -m ingest.load --data data/ --graph northwind --fresh`.
- Bench: `./bench/start.sh <sizes>`; progress `cat bench/STATUS.json`; stop `pkill -f bench.run`; check `uv run --group bench python -m bench.verify`.
- Sanity queries / VERIFY: `uv run python -m tests.helpers.sanity_queries --graph northwind --data data/`, `uv run python -m tests.helpers.verify_report --data data --graph northwind --pytest-log x.txt` (writes docs/records/VERIFY.md).
- Prewarm AI cache: `uv run python -m agent.prewarm`.

## Hard rules
- Never print, log or commit secrets (.env holds FalkorDB Cloud, OpenRouter, Vercel credentials). Scan before any push.
- Never push, deploy or change repo visibility unless the owner asks in this session.
- OpenRouter free tier: 50 requests/day, failed calls count. Tests and development use the fake LLM only. Live calls only via prewarm when asked.
- Benchmark: never re-run or overwrite finished steps (results in `bench/results/`); never delete data a pending step needs; results are reported as measured, including where PostgreSQL wins.
- Do not change the truth model or drift catalogue (D1–D14). All tests must keep passing; never weaken a test.
- `docker-compose.yml` must always use the local FalkorDB, never the cloud one.

## Working style
- Work autonomously: decide, log non-trivial decisions in docs/records/DECISIONS.md, research uncertain facts and cite them in docs/records/RESEARCH_NOTES.md. Ask the owner only if genuinely blocked.
- Keep docs/records/PROGRESS.md updated after each milestone; re-read it and this file after context compaction.
- Save tokens: run long jobs in the background, write status to a file, end the session instead of polling; read files selectively.
- Report plainly and briefly: what was done, numbers, what is open. The owner reviews rather than codes; explain in simple words.

## Known facts and pitfalls
- FalkorDB Cloud free tier: 100 MB, no TLS (connect with FALKORDB_TLS=false). Instance in AWS ap-south-1; Vercel functions run in bom1.
- Live demo: https://northwind-drift-agent.vercel.app (public mode: cached AI answers only, approvals stored in FalkorDB, daily reset).
- Git author must be the owner (noreply address set). Local branch `backup/pre-author-rewrite` must never be pushed.
- FalkorDB quirks: no nested reduce(); unlabelled patterns skip indexes (use `:Component`); default query timeout raised to 10 s.

## Current priority (Oct 16)
Experimentation only, finished and written up by 16:00 IST. Do not touch the website, README or blog until the owner says so.