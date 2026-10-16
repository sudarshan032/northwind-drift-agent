# Specifications

The specs are kept as written, in the order they were written. Each later spec builds on the earlier ones; where
they conflict, **the newest wins**.

1. [SPEC.md](SPEC.md): the original build spec: a CMDB drift agent comparing a synthetic CMDB with recorded
   OpenTelemetry traces in one FalkorDB graph (drift, blast radius, change check, LangGraph agent, FastAPI, one-page
   UI, repo layout, tests, schedule). Mostly superseded by the specs below.
2. [DATA_SPEC.md](DATA_SPEC.md): the Northwind Health world (truth model, CMDB / discovery / observed / identity
   views, drift catalogue D1–D14). Supersedes SPEC.md sections 4 (data sources) and 5 (graph schema).
3. [RUN_PROMPT.md](RUN_PROMPT.md): instructions for the unattended data-phase run of DATA_SPEC.md (goal, file
   precedence, decision and research rules, tests, clean-room verification). Sections 3 and 9 (run rules) are
   reused by every later spec.
4. [AGENT_SPEC.md](AGENT_SPEC.md): detectors, Cypher queries, the agent jobs, approval, API and UI. Supersedes
   SPEC.md sections 6–13.
5. [REALISM_SPEC.md](REALISM_SPEC.md): every input as a real tool's native export plus connectors, CSDM service
   offerings, the CMDB write-back rule and a conventional dependency view. Supersedes how DATA_SPEC.md produced
   its views (they now come from raw exports through connectors) and AGENT_SPEC.md's change-review drawing.
6. [UI_SPEC.md](UI_SPEC.md): the UI redesign (design tokens, screens, accessibility, demo check). Supersedes the
   UI parts of AGENT_SPEC.md and REALISM_SPEC.md.
7. [BENCH_SPEC.md](BENCH_SPEC.md): the scale benchmark against PostgreSQL. Adds new work only; it supersedes
   nothing earlier.

Decisions taken while implementing them are in [../records/DECISIONS.md](../records/DECISIONS.md).
