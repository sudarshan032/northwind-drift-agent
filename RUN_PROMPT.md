# Autonomous run: Northwind Health data phase

You are working unattended. The owner (Mourya) is away and will review results when he returns. Work until the goal below is fully met, verified twice, and reported. Do not stop early, and do not stop to ask questions except under the single condition in section 3.

## 1. Goal

Implement the data generator, graph loader, and generator tests described in `DATA_SPEC.md`, plus a `docker-compose.yml` running FalkorDB. Then research-check the data for realism, fix what the research shows is unrealistic (within the rules below), re-verify everything from a clean state, and write `REPORT.md`.

Out of scope for this run: the agent, API, UI, MCP server, and graph queries beyond what the loader and tests need. Do not build them, even if you finish early. If you finish early, spend the time on deeper verification and review (section 6).

## 2. Files and precedence

- `DATA_SPEC.md` is the source of truth for this run. It supersedes `SPEC.md` sections 4 and 5.
- From `SPEC.md`, use only: the hard constraints (synthetic data only, idempotent loading, single-command docker), the stack choice (Python 3.11, falkordb Python client), and the FalkorDB syntax notes. **Ignore SPEC.md sections 6, 7, 8, 9, 11 and 13** for this run: they describe the old online-shop scenario and their tests do not apply.
- Do not run or download the OpenTelemetry demo app. It is no longer part of the plan.
- Where this file and `DATA_SPEC.md` conflict, this file wins (it contains clarifications made after DATA_SPEC.md was written).

## 3. Decision policy: no questions

- Make every decision yourself. For each non-trivial decision, append an entry to `DECISIONS.md`: the question, options considered, what you chose, and why. Prefer the option most realistic for a large US health system and most consistent with `DATA_SPEC.md`.
- If you are unsure about a real-world fact (hospital departments, HL7 message types, device counts, backup practices, naming conventions), research it on the web, cite the source in `RESEARCH_NOTES.md`, and decide.
- **Ask the owner only if you are genuinely blocked by something outside your control** that no decision can resolve: for example Docker is not installed or not running and cannot be started, or a required credential is missing. In that case, first finish everything else that does not depend on it, then ask one clear question.
- Never weaken a test, delete a test, or change an assertion just to make it pass. If a test reveals a spec problem, fix the generator or record a justified spec clarification in `DECISIONS.md` and adjust the test to match the clarified spec, not the buggy output.

## 4. Clarifications to DATA_SPEC.md (apply these)

1. **Noise vs. agreement.** Test 9.2 ("views agree exactly without drift") compares **findings**, not raw records. Implement a pure-Python reference checker in `tests/oracle.py` that computes findings from the three views using the same rules the future agent will use: a missing declared relationship needs observed traffic on ≥3 distinct days; a stale relationship means no observed traffic in the whole 14-day window and `failover=false` and neither end retired; a backup failure means no successful job in the last 7 days; transient failures followed by success within 48 hours are not findings; flows tagged `health_check=true` are ignored. With drift off, the oracle must return zero findings. With drift on, it must return exactly the `ground_truth.json` entries (D3 entries none, D13 entries resolved as the same CI).
2. **Observed data granularity and size budget.** Hourly records only for edges that touch a demo site (H03, H17, H44, GCC-HYD) or a central system in DC-PRI, DC-DR or the cloud regions. All other edges use daily records (`flows_daily.jsonl`, `netflow_daily.jsonl`). Keep the whole `data/` directory under 500 MB uncompressed. If it would exceed that, coarsen granularity further and log the decision.
3. **Identification keys for observed data.** Every flow and netflow record carries `src_name`, `src_ip`, `dst_name`, `dst_ip`. The loader identifies the endpoint CI by exact `name` (or CMDB `sys_id` alias), then by `ip_address`. D13 cases must be resolvable through IP or serial number, and the loader records them as resolved name aliases (store an `aliases` list property on the CI) rather than creating new nodes.
4. **Every hospital has an Emergency department** (US critical access hospitals must provide 24/7 emergency care). So there are 51 `ORU-CRIT-*` routes; the headline drift needs ≥40 of them to depend on `lrc-db-01` in the observed view (all 51 is fine).
5. **Size expectations.** DATA_SPEC section 9 test 6 ranges are guidance. Do not pad or trim the world to hit a number. The test fails only outside 30k–300k nodes. Report the actual counts and whether they are realistic.
6. **Generation time.** Full generation must finish in under 10 minutes on a laptop (DATA_SPEC said 2; relaxed).
7. **Python environment.** The host has Python 3.14, which many data libraries do not support well. Use `uv` with Python 3.11 (`uv python install 3.11`, `uv venv --python 3.11`). If `uv` is unavailable and cannot be installed, run the generator and tests inside a `python:3.11-slim` container via docker compose. Pin dependency versions in `pyproject.toml` or `requirements.txt`.
8. **Git.** Run `git init` if needed. Commit after each milestone in section 5 with a clear message. Never push, never add remotes. Add `data/` to `.gitignore` if it exceeds 50 MB; in that case also generate and commit a small demo subset (`--subset demo`) under `data_demo/`.
9. **Docker.** FalkorDB via the official `falkordb/falkordb` image, pinned to a specific version tag (not `latest`). Expose port 6379 and the browser UI port. Use a named volume.
10. **Personal data.** No generated human names or emails anywhere. Users are IDs like `u-h03-00412` with role labels.

## 5. Milestones (do them in order; commit after each)

1. **Scaffold:** repo layout, `pyproject.toml`, Python 3.11 env, `docker-compose.yml`, `.gitignore`, `data/README.md` with the fictional disclaimer. FalkorDB container starts and answers a test query.
2. **Truth model:** `ingest/world.py` builds the full Northwind world deterministically from `SEED=42`: sites, departments, inventory, central systems, storage, interfaces, identity. Unit tests for physical plausibility (DATA_SPEC 9.4).
3. **Views:** derive the CMDB, discovery, observed, identity and evidence files from the truth model, with drift disabled. Implement `tests/oracle.py`. Test 9.2 passes (zero findings).
4. **Drift injection:** implement the full DATA_SPEC section 7 catalogue and `ground_truth.json`. Tests 9.1, 9.3 and 9.5 pass.
5. **Loader:** `ingest/load.py` maps everything into FalkorDB per DATA_SPEC section 8, batched, idempotent. Test 9.7 passes. Print the size report (9.6).
6. **Research check:** see section 6.
7. **Clean-room verification:** see section 7.
8. **Report:** write `REPORT.md` (section 8).

## 6. Research and review (after milestone 5)

1. Research realism against public sources and record findings with links in `RESEARCH_NOTES.md`. Cover at least: hospital department lists by hospital size; typical networked medical devices per bed size; HL7 v2 message types used for ADT, orders, results, pharmacy and charges; DICOM and PACS flow patterns; common backup and replication practices in healthcare (RPO/RTO tiers, immutable backups); realistic ServiceNow CMDB class names and relationship types; typical Active Directory group naming conventions.
2. Where research shows something in the generator is unrealistic, fix it, provided the fix does not change the drift catalogue counts or types. Log each change in `DECISIONS.md` with the source.
3. Run an independent review: start a subagent with fresh context, give it only `DATA_SPEC.md`, this file, and the code, and ask it to list every place where the implementation does not match the spec or the clarifications. Fix every valid finding. Repeat the review once more after fixes.

## 7. Clean-room verification (twice)

Do this full sequence two times; both runs must pass completely:
1. `docker compose down -v`, delete generated `data/` and any caches.
2. Recreate the environment from the committed files only.
3. Generate data, run all tests, load into FalkorDB, run the loader idempotency test.
4. Run three sanity queries against FalkorDB and save their output in `REPORT.md`: node counts per label; the observed path from one `ORU-CRIT-*` route to `lrc-db-01`; the number of `:Discovered` nodes with no `IDENTIFIES` edge (must equal the D6 count).
5. Compare file hashes between the two runs (determinism).

## 8. REPORT.md contents

- Status: done / partially done, with anything incomplete and why.
- How to run (exact commands).
- Test output (final run).
- Size report: counts per CI class, per site type, per edge type, total nodes and edges, data directory size, generation and load times.
- The three sanity query outputs.
- Summary of `DECISIONS.md` (top 10 decisions) and `RESEARCH_NOTES.md` (what changed due to research).
- Known limitations and anything the owner should decide later.

## 9. Working habits for a long run

- Keep a short running log in `PROGRESS.md`: milestone, what's done, what's next. Update it at least after every milestone, so a resumed or compacted session can pick up where it left off. Re-read `PROGRESS.md`, this file, and `DATA_SPEC.md` after any context compaction.
- Run the test suite after every meaningful change.
- Prefer simple, readable code with docstrings: the owner must be able to explain the data model to judges.
- Do not delete files that existed before this session other than generated data and caches.