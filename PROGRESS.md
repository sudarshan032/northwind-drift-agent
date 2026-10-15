# Progress log (autonomous data-phase run)

Re-read this file, RUN_PROMPT.md and DATA_SPEC.md after any context compaction.

## M1 Scaffold — done
- pyproject.toml (+ uv.lock), Python 3.11 via uv, docker-compose.yml (falkordb/falkordb:v4.22.0), .gitignore, data/README.md.
- FalkorDB answers `RETURN 1+1`.
- Next: M2 truth model (ingest/world.py) + plausibility tests.

## M2 Truth model — done
- ingest/world.py; tests/test_world.py (9.4 + footprint/hygiene).

## M3+M4 Views, oracle, drift — done (one commit, DECISIONS #27)
- ingest/views.py, ingest/drift.py, ingest/generate.py, tests/oracle.py, tests/test_views.py.
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
