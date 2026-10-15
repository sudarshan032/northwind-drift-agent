# Northwind Health drift data (FICTIONAL)

Synthetic data generator, FalkorDB loader and tests for a CMDB drift agent.

**Everything here is fictional.** "Northwind Health" is an invented health system.
Its footprint (51 hospitals, 1,000 clinics, 800 non-acute sites across 7 western US
states, two data centres, two cloud regions and a capability centre in Hyderabad) is
sized like a large US health system, but no real organisation's systems, people,
addresses or incidents are represented. Users are pseudonymous IDs (`u-h03-00412`);
there are no names, emails or patients. See `data/README.md`.

## Run

```bash
docker compose up -d                                   # FalkorDB (6379) + browser UI (http://localhost:3000)
uv venv --python 3.11 && uv sync                       # Python 3.11 env, pinned deps (uv.lock)
uv run python -m ingest.generate --seed 42 --out data/ # ~4 s, ~145 MB, deterministic
uv run python -m pytest -q                             # generator + loader tests
uv run python -m ingest.load --data data/ --graph northwind --fresh
```

`data_demo/` (committed) is the WA + OR demo region, gzip-compressed:
`python -m ingest.generate --out data_demo --subset demo --gzip`; load it the same way.

## Layout

| Path | What |
|---|---|
| `ingest/world.py` | Truth model: sites, departments, inventory, central systems, interfaces, identity, changes |
| `ingest/views.py` | Derives CMDB, discovery, observed, identity and evidence views from the truth |
| `ingest/drift.py` | Drift catalogue D1–D13 as a plan applied to the views, plus `ground_truth.json` |
| `ingest/generate.py` | CLI entry point |
| `ingest/load.py` | Idempotent batched FalkorDB loader (graph `northwind`) |
| `tests/oracle.py` | Pure-Python reference checker: findings from the views using the agent's rules |
| `tests/` | DATA_SPEC section 9 tests |

Specs: `DATA_SPEC.md` (data model), `RUN_PROMPT.md` (clarifications). Decisions and
research: `DECISIONS.md`, `RESEARCH_NOTES.md`. Results: `REPORT.md`.
