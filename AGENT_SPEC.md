# AGENT_SPEC: verification, detection, agent, API and UI (Northwind Health)

This file supersedes SPEC.md sections 6–13. The data model is defined by DATA_SPEC.md plus the decisions in DECISIONS.md and REPORT.md. Work on branch `northwind-data-phase` (or a new branch from it). Run rules: same as RUN_PROMPT.md sections 3 (decide yourself, log in DECISIONS.md, ask only when genuinely blocked) and 9 (PROGRESS.md, re-read specs after compaction, test after every change). Commit after each phase. Never push.

Where a name in this file (label, edge type, property, file) differs from what the generator and loader actually produce, the actual implementation wins: adapt this spec to it and log the mapping in DECISIONS.md. Do not rename the data model to match this file.

## Phase 0: verify the data (must pass before Phase 1)

1. If any of these follow-up tasks are not yet done, do them first:
   - (a) DATA_SPEC section 2 table updated to the research imaging ranges.
   - (b) `--subset demo` = all 51 hospitals, both DCs, both cloud regions, GCC-HYD; excludes clinics and non-acute sites; all 51 `ORU-CRIT-*` routes present; test updated.
   - (c) Storage and connects relationship types use ServiceNow standard `Provides storage for::Stored on` and `Connects to::Connected by`; others stay documented custom types.
   - (d) Full upstream path from `dc1-host-07` to each affected hospital's Emergency department and the "Emergency care" business service exists in the graph. If the business service → route link or the route → department link is missing in the truth model, add it (declared by the service owner, so it appears in both truth and CMDB) and log it.
   - (e) Per-drift-type breakdown of all findings in REPORT.md.
   - (f) FHIR layer: patient portal → EMR FHIR API, analytics → EMR FHIR bulk export, EMR FHIR APIs per hospital as appropriate, plus one new drift **D14**: an unregistered third-party SMART on FHIR app used by H17 cardiology, with real traffic and an access path through a user group, and no CMDB record. Update ground_truth.json, oracle and tests.
2. Run the full test suite, a clean regenerate, and a fresh load. All must pass.
3. Write `VERIFY.md` with: test output; node/edge counts; the declared vs observed blast radius for `dc1-host-07` (counts of routes, applications, departments, hospitals, business services); the D14 path; the per-type finding breakdown. Commit.

## Phase 1: detection queries in Cypher (the graph must be load-bearing)

Put every query in `graph/queries.py`, one function each, parameterised, each returning rows plus, where relevant, paths as ordered lists of node names and edge types.

1. **Detectors**, one per drift type D1, D2, D4–D12, D14, implementing the same rules as `tests/oracle.py` (RUN_PROMPT 4.1) but as Cypher on the loaded graph. Python may only orchestrate and format; the logic must be in the queries. D3 must not appear; D13 must be returned as resolved aliases, not findings.
2. **Impact semantics** (used by both blast-radius queries). Impact flows from a failed component to everything that needs it:
   - things that run on or store on it (`RUNS_ON`, `STORES_ON`, or the actual storage edge name);
   - things that depend on it: declared `DEPENDS_ON`, or observed calls where the caller needs the callee (for example a route staging into `lrc-db-01`);
   - for an interface route, its data consumer (target system) and the department(s) it serves;
   - containers: a business service is impacted when anything it `CONTAINS` is impacted.
   Implement this as one explicit, documented set of edge types and directions. Where decision #7 (either-direction matching for interface legs) affects this, define the direction rule once in `graph/impact.py` and reuse it.
3. **Declared radius**: impact traversal using only declared edges. **Real radius**: declared structural edges plus observed edges. Both return: affected components by type, affected departments, affected hospitals, affected business services, and the paths. Cap depth at 10; per-query timeout (below).
4. **Change review**: for a change id, compute declared and real radius of its targets, list the undocumented edges inside the real radius, and return a risk level: `high` if the real radius reaches a business service with `patient_safety_impact=high` that the declared radius does not; `medium` if the real radius is strictly larger; else `low`.
5. **Protection path check**: for every component with `handles_phi=true`, whether its storage path reaches a different site (replica) and the immutable vault, plus the last successful backup. Returns the broken chains.
6. **Access check**: observed accesses with no declared `MEMBER_OF → HAS_ROLE → GRANTS` path, and accesses outside approved change windows for admin actions.
7. **Who to page**: for affected components, the observed owner (`OBSERVED_OWNER`) and the declared owner (`MANAGED_BY`), flagging where they differ.
8. **Critical nodes**: betweenness centrality on the observed call graph with a fixed `samplingSeed: 42`, top 10. Verify the exact FalkorDB procedure signature in docs.falkordb.com before use. If it is too slow on the full graph, restrict to Application, Interface, Database, VM and Host labels and log it.

Timeouts: raise FalkorDB's default query timeout in docker-compose to 10 s (verify the config parameter name in the FalkorDB docs) and pass a 5 s timeout plus a LIMIT on every agent tool query. On timeout a tool returns a clear "query timed out" result instead of raising.

**Phase 1 tests:**
- Cypher detectors return exactly `ground_truth.json` finding keys (same as the oracle) on the full data and on the demo subset.
- `dc1-host-07`: declared radius reaches lab reporting only (no Emergency care); real radius reaches all 51 `ORU-CRIT-*` routes, the Emergency care business service and each hospital's Emergency department. `CHG-HEADLINE` review returns `high`.
- Critical nodes are identical across two runs.
- Every agent tool query finishes within its timeout on the full data.

## Phase 2: agent (LangGraph)

LLM via env (`ANTHROPIC_API_KEY`, model name in `.env`, default a current Claude Sonnet model). **All tests must run with a deterministic fake LLM** (scripted responses) so no key is needed for CI or judges' test runs. The real key is only needed for the live demo. Do not stop or ask if no key is present: build and test with the fake, and note it in REPORT.

Tools: thin wrappers over Phase 1 queries plus `create_finding`, `propose_fix`, `page_owner` (mock: writes to `notifications.jsonl` and the in-app feed), `get_component`, `get_neighbourhood(name, depth<=2)`. The LLM never writes to the CMDB files or declared graph edges.

Agent jobs (each a LangGraph flow):
1. **Drift triage**: run all detectors, then for each finding produce: a plain-language explanation grounded only in tool output, severity (weigh `patient_safety_impact`, real-radius size, PHI exposure), the evidence (counts, dates, path), and a proposed CMDB fix as a JSON patch. Store as `:Finding` nodes linked `ABOUT` the components. Rank and return the top 10.
2. **Change review**: given a change id, run change review and explain the difference between the declared and real radius, naming the hidden edges and who to warn.
3. **Incident investigation**: given a symptom ("ED critical results delayed at H03, H17 and H22"), find what the affected routes have in common (shared downstream components in the real graph), check recent changes on those components, and report the likely cause with paths. Must work for the headline symptom in tests (with the fake LLM scripting the tool choices; with the real LLM in a manual check).
4. **Ambiguity judgement**: for D13-style name mismatches and D14 (unregistered FHIR app), decide with reasons: "same CI, add alias", "real undocumented dependency", or "possible security risk, escalate". This is where the LLM is genuinely needed; log its reasoning on the Finding.
5. **Chat**: answers only via tools, cites the paths it used, says so when a tool returns nothing.

Approval flow: `approve` applies the proposed patch to the CMDB CSVs and the declared graph layer, re-runs the affected detector, marks the finding `approved`, and writes `audit.jsonl`. `reject` changes nothing except the finding status.

## Phase 3: API (FastAPI)

- `GET /health`
- `POST /scan` → triage results
- `GET /findings`, `GET /findings/{id}`, `POST /findings/{id}/approve`, `POST /findings/{id}/reject`
- `POST /changes/{id}/review`
- `POST /incident {symptom}`
- `GET /radius?component=&layer=declared|real`
- `GET /subgraph?component=&depth=` (bounded, for the UI; never return the whole graph)
- `GET /critical`
- `GET /notifications`
- `POST /chat {message}`

## Phase 4: UI (single page)

Never render the whole graph. Default view is the headline.

- **Change review screen (demo opener):** pick `CHG-HEADLINE`; side-by-side "CMDB says" vs "Reality" with the counts (routes, hospitals, departments, business services) and risk level; highlighted path graph (Cytoscape.js) with observed-only edges in red, declared-only in grey dashed, failover in blue dashed.
- **Hospital map (stretch, high demo value):** the 51 hospitals as dots by city (add a small static city → lat/long table from public data); dots turn red when inside the real radius.
- **Findings panel:** ranked findings with explanation, evidence, path, Approve / Reject.
- **Incident box:** type a symptom, see the investigation steps and result.
- **Notifications feed** and **critical nodes** card.
- **Chat.**
- Clean, readable, looks good in a 2-minute screen recording; works in light and dark mode.

## Phase 5: packaging and README

- `docker compose up` starts FalkorDB, a one-shot `seed` service (generate full data with seed 42, load it), the API and the UI. Works without an API key (agent features show a clear "add ANTHROPIC_API_KEY for live agent" message; detection, radius, change review and UI still work).
- README (judged): problem in 3 lines; architecture diagram; data model (layers, the three views, why declared and observed stay separate); every Cypher query with a one-line explanation; why a graph (multi-hop paths across layers, two edge layers on the same nodes, centrality); how to run; data disclosure (fictional Northwind Health, sized on a public US health-system footprint, all systems and incidents fictional, no personal data); AI-usage disclosure; known limits; prior art (ServiceNow Service Graph Connectors, Now Assist for CMDB) and how this differs.

## Schedule and cut order

- Day 2 (Oct 16): Phase 0, Phase 1, Phase 2 jobs 1 and 2.
- Day 3 (Oct 17): Phase 2 jobs 3–5, Phase 3, Phase 4 core screens.
- Day 4 (Oct 18): map stretch, packaging, README, video, blog.

Cut order if late: chat, map, incident investigation, ambiguity judgement UI. Never cut: Phase 0, Cypher detectors matching ground truth, declared vs real radius, change review screen with path, approval flow, README.

## Final report

Append a "Phase 2" section to REPORT.md: status per phase, test output, timings of the heaviest queries, screenshots of the change review screen (save under `docs/`), decisions summary, and anything the owner must decide.