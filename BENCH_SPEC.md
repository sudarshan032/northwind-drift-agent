# BENCH_SPEC: does the graph approach hold up as the hospital system grows?

Goal: prove, with measured numbers, how blast-radius questions behave as data size, query depth and concurrent users grow, comparing FalkorDB with a properly tuned PostgreSQL on the same data, same questions, same laptop. Publish the results honestly, whichever system wins at each point.

Run rules: RUN_PROMPT.md sections 3 and 9. Work on branch `bench` from master. Keep all existing tests passing; the live demo and `docker compose up` must not change. Never push without being asked. Do not spend OpenRouter requests (no LLM is needed here).

## 1. Token-saving run plan (mandatory)

**Session 1 (this session):**
1. Build the scale generator, both loaders, the benchmark runner and the analysis script (sections 2–5).
2. Run the **pilot at 5% of the largest size (≈250k nodes)** end to end: generate, load both databases, run every benchmark, verify identical result sets, write results.
3. From the pilot, measure peak memory and disk for FalkorDB, PostgreSQL and the generator, and extrapolate to 5M nodes. Check the laptop's free RAM, free disk and Docker's memory limit. If 5M will not fit, choose the largest size that will (keeping a 25% safety margin), and log the decision in DECISIONS.md.
4. Start the full run **in the background** with `nohup` (or equivalent), detached from this session, writing progress to `bench/logs/run.log` and a machine-readable `bench/STATUS.json` (current step, % done, elapsed, ETA, last error). When finished (success or failure), it sends a macOS notification (`osascript -e 'display notification ...'`) and writes `bench/DONE` or `bench/FAILED`.
5. Report: pilot results, the size plan, expected duration, and how to check progress (`cat bench/STATUS.json`). Then **end the session**. Do not poll or wait for the background run.

**Session 2 (started later by the owner):** reads STATUS.json and the results, verifies, analyses and publishes (section 6).

The background runner must be **resumable**: each (size, system, benchmark) step writes its result file when done and is skipped if it already exists, so a crash or a reboot does not repeat finished work.

## 2. Sizes and the scale generator

Sizes: **50k, 500k, 5M nodes** (plus the 250k pilot). Grow the world realistically, never by padding with meaningless nodes:
- More regions: additional regional divisions of Northwind Health, each with its own hospitals, clinics and non-acute sites, using the same tier mix and inventory rules as now, sharing the enterprise data centres and central systems (as a real multi-region health system would) plus regional data centres at larger sizes.
- Individual endpoints (workstations, printers, devices) instead of group CIs, as a full CMDB would hold them.
- Keep the same relationship types and the same impact rules. Keep the headline chain present in every size so `dc1-host-07` is always a meaningful test point.

Generation must be deterministic (seed 42) and stream to disk (do not hold 5M nodes in memory at once if avoidable). Record nodes, edges and generation time per size.

## 3. The two systems

- **FalkorDB:** the same version as the project (Docker), with the project's loader and indexes. Blast radius uses the project's real-radius query shape (BFS over the impact edges).
- **PostgreSQL:** current stable release in Docker. Tuning (log every setting):
  - tables `node(id, name, label, …)` and `edge(src, dst, type)`, loaded with `COPY`;
  - indexes on `edge(src)`, `edge(dst)`, `edge(src, type)`, `edge(dst, type)` and `node(name)`;
  - `ANALYZE` after load; `shared_buffers`, `work_mem` and `effective_cache_size` set sensibly for the laptop and the Docker memory limit;
  - the recursive query uses `UNION` (not `UNION ALL`) so each node is visited once, with a depth counter and a depth cap, and is checked with `EXPLAIN ANALYZE` to confirm index use;
  - queries run as prepared statements; caches warmed before timing.
- Both systems get the **same Docker memory/CPU limits**.
- **Same edges:** export the exact impact edges FalkorDB traverses (the materialised impact layer) to PostgreSQL, so both compute the same thing.

## 4. Benchmarks

For each size:
1. **Depth:** blast radius from three start points (`dc1-host-07`, `emr-app`, a busy storage array) at depths 2, 4, 6 and 10. Run 20 times after warm-up; record median, p95 and the number of nodes reached.
2. **Correctness gate:** for every (start, depth), the sets of impacted node IDs from FalkorDB and PostgreSQL must be identical. If not, the run stops and logs the difference; no timing from that pair is reported.
3. **Concurrency:** 10, 50 and 100 simultaneous clients, each running real-radius queries on randomly chosen start points (fixed seed) for 60 s. Record throughput, p50, p95 and p99, and errors.
4. **Change review end-to-end:** the declared-vs-real comparison for CHG-HEADLINE (two radius queries plus the difference) on each system.
5. **Resources:** peak memory and on-disk size per system per size, and load time.

Save raw results as JSON lines in `bench/results/` with full context (size, system, versions, settings, hardware info).

## 5. Fairness and honesty rules

- No result is hidden. If PostgreSQL is faster somewhere, report it plainly.
- Report hardware (CPU, RAM, disk), Docker limits and all settings.
- Note what the benchmark does not cover (e.g. write-heavy workloads, distributed setups).
- The analysis must explain the query shapes in plain words: one BFS call in FalkorDB vs one recursive self-join round per hop in SQL.

## 6. Session 2 work (analysis and publishing)

1. Verify: all steps done, correctness gate passed everywhere, no errors; rerun any failed step.
2. Charts (PNG and SVG, light and dark): query time vs depth per size; query time vs size at depth 6 and 10; concurrency p95 vs clients; memory vs size.
3. `bench/RESULTS.md`: setup, tables, charts, plain-language findings, limits, how to reproduce (`python -m bench.run --sizes …`).
4. README "Why a graph": a short summary with the two key charts and the headline numbers.
5. Website: a static "Scale" tab (no live computation) showing the charts, the key numbers, the hardware used and a link to RESULTS.md; it loads from a static JSON bundled with the site. Redeploy to Vercel.
6. Keep all tests passing, update DECISIONS.md and REPORT.md, commit.