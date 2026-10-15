# Decisions log

Each entry: question, options, choice, why.

## 1. Branch for this run
- Q: Commit straight to `master`?
- Options: master; new branch.
- Chose: branch `northwind-data-phase` off `master`. Why: owner reviews on return; master stays untouched and the branch can be fast-forward merged.

## 2. FalkorDB image tag
- Q: Which pinned tag?
- Options: `6.0.2` (newest major, 1 week old), `v4.22.0` (latest 4.x, 2026-09-30).
- Chose: `v4.22.0`. Why: newest release of the long-running 4.x line; a fresh major (6.0.x) is riskier for an unattended run. Persistence dir verified as `/var/lib/falkordb/data` (named volume `falkordb_data`).

## 3. Python dependencies
- Chose: `falkordb==1.7.1` (1.8.0 was published the day before this run), `pytest==9.1.1`; transitive pins in `uv.lock`. Generator itself uses only the standard library (fewer moving parts, byte-stable output).
