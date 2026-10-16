# Build spec: CMDB drift agent on FalkorDB

Hand this whole file to Claude Code. Do not start implementation before Oct 15, 12:01 AM IST (hackathon rule). Project working name: `drift-agent` (rename later).

## 1. What we are building

An agent that compares what a CMDB *declares* about service dependencies with what live OpenTelemetry traces *prove*, stores both in one FalkorDB graph, and:

1. Finds drift: undocumented dependencies, stale declared dependencies, wrong owners.
2. Computes the real blast radius of a failing component vs the declared one.
3. Checks a pending change against the real graph.
4. Explains every finding with the exact graph path and evidence.
5. Acts: pages the right owner (mocked), and proposes CMDB fixes that are applied only after human approval.

Demo moment: "CMDB says this outage hits 2 services. The real graph says 6. Here is the path."

Hackathon tracks to name on submission: Track 1 (primary), Track 3 (ownership / keeping knowledge current).

## 2. Hard constraints

- Only public or synthetic data. The CMDB is synthetic and must be labelled as such everywhere.
- FalkorDB must be load-bearing: all drift, blast radius, ownership and change checks are Cypher queries on the graph. No duplicate logic in Python.
- Judges must be able to run it with one command: `docker compose up` in **replay mode** (recorded traces), no OpenTelemetry demo required.
- The LLM never writes to the CMDB or graph directly. Writes happen only through the approve endpoint.
- Agent tools use fixed, parameterised Cypher queries (explainable, safe). An optional read-only text-to-Cypher tool may be added last.
- Keep it small. No auth, no multi-user, no Kubernetes.

## 3. Stack

- Python 3.11 inside the api container (python:3.11 image; host Python version doesn't matter), FastAPI backend, `falkordb` Python client.
- FalkorDB via the official Docker image.
- Agent: LangGraph. LLM provider via env vars (default Anthropic Claude; key in `.env`, never committed).
- UI: single-page app using Cytoscape.js for the graph (Vite + React, or plain HTML/JS if faster).
- Optional stretch: expose the agent tools as an MCP server.

## 4. Data sources

### 4.1 Observed layer (traces)

Two modes:

- **Live mode:** run the public OpenTelemetry demo (`open-telemetry/opentelemetry-demo`). Add an OTel Collector file exporter (or a second OTLP exporter pointing at our ingester) so we receive raw spans.
- **Replay mode (default):** a recorded span snapshot committed at `data/traces/snapshot.jsonl` (target: 10–20 minutes of traffic, compressed if large). Live mode is used once to record it.

Edge extraction rules (implement in `ingest/traces.py`):

- Parent span in service A, child span in service B, A ≠ B → `A -[:OBSERVED_CALLS]-> B`.
- CLIENT spans with `db.system`, `messaging.system`, `server.address` or `peer.service` → edge to an `:Infra` node (e.g. postgres, kafka, valkey). This makes un-instrumented dependencies visible.
- Messaging: producer → queue, queue → consumer. Consumers may be linked via span links rather than parent-child; handle both.
- Aggregate by `service.name` only. Never create nodes per pod, container or IP.
- Edge properties: `count`, `error_count`, `p95_ms`, `first_seen`, `last_seen`, `protocol`.

Do not hardcode service names. Derive them from the traces (names differ between demo versions).

### 4.2 Declared layer (synthetic CMDB)

Generate `data/cmdb/` as CSVs shaped like ServiceNow tables:

- `cmdb_ci.csv`: `sys_id, name, class (service|database|queue|cache), criticality, owner_team, status (operational|retired)`
- `cmdb_rel_ci.csv`: `parent, child, type ("Depends on::Used by"), failover (true|false)`
- `teams.csv`: `team, oncall, channel` (fake names only)

Generator (`ingest/make_cmdb.py`) starts from the observed graph, then injects **deliberate, seeded, documented drift**:

- Remove 3 real edges (become "undocumented dependencies"), including at least one on the payment/checkout path so the blast-radius demo works.
- Add 2 fake edges with no traffic (become "stale").
- Add 1 edge with no traffic but `failover=true` (must NOT be flagged; proves we handle the failover trap).
- Add 1 retired service still holding relationships.
- Set a wrong `owner_team` on 2 services.

Write the injected drift to `data/cmdb/ground_truth.json` so tests can check detection accuracy.

Generator rules that the tests depend on:
- Declare **every** observed edge, Service and Infra alike, except the 3 removed ones. Otherwise the undocumented query returns more than 3.
- Owners: write the true team for every service to `codeowners.csv`; put the wrong team in `cmdb_ci.owner_team` only for the 2 drifted services.

### 4.3 Ownership evidence

`data/codeowners.csv`: `service, team`, a synthetic stand-in for repo CODEOWNERS, always holding the true owner. Mismatch between this and `cmdb_ci.owner_team` = ownership drift.

## 5. Graph schema (FalkorDB, graph name `drift`)

Nodes:
- `(:Service {name, criticality, status})`
- `(:Infra {name, kind})` with kind in db, queue, cache, external
- `(:Team {name, oncall, channel})`
- `(:Change {id, title, status, planned_at})`
- `(:Finding {id, type, severity, status, explanation, evidence_json, path_json, proposed_fix_json, created_at})` with status in open, approved, rejected

Edges:
- `-[:DECLARED_DEPENDS_ON {failover}]->` from CMDB
- `-[:OBSERVED_CALLS {count, error_count, p95_ms, first_seen, last_seen, protocol}]->` from traces
- `(:Team)-[:DECLARED_OWNS]->(:Service)` from CMDB
- `(:Team)-[:CODE_OWNS]->(:Service)` from codeowners
- `(:Change)-[:TARGETS]->(:Service|:Infra)`
- `(:Finding)-[:ABOUT]->(node)`

Every `:Service` and `:Infra` node also carries a shared `:Component` label. FalkorDB only uses an index when the pattern has a label, so all queries match `(x:Component {name: ...})`, never an unlabelled `(x {name: ...})`.

Create indexes on `Component.name`, `Team.name`, `Finding.id`. Ingestion must be idempotent (`MERGE`).

## 6. Core queries (put all in `graph/queries.py`, one function each)

Verify syntax against docs.falkordb.com before use; adjust if FalkorDB differs from Neo4j Cypher.

```cypher
// Undocumented dependency: seen in traces, missing in CMDB
MATCH (a:Component)-[o:OBSERVED_CALLS]->(b:Component)
WHERE NOT (a)-[:DECLARED_DEPENDS_ON]->(b)
RETURN a.name AS from, b.name AS to, o.count AS calls, o.last_seen AS last_seen

// Stale declared dependency: in CMDB, no recent traffic, not a failover path,
// not touching a retired component (those are reported by the retired query,
// otherwise the retired service's edges would also count as stale and break "exactly 2").
// $cutoff = latest last_seen in the loaded snapshot minus the window,
// never "now" (in replay mode "now" would make every edge look stale)
MATCH (a:Component)-[d:DECLARED_DEPENDS_ON]->(b:Component)
OPTIONAL MATCH (a)-[o:OBSERVED_CALLS]->(b)
WITH a, b, d, o
WHERE coalesce(d.failover, false) = false
  AND coalesce(a.status, '') <> 'retired'
  AND coalesce(b.status, '') <> 'retired'
  AND (o IS NULL OR o.last_seen < $cutoff)
RETURN a.name AS from, b.name AS to

// Real blast radius: everything upstream of the failing node via real calls
// (up <> f: a call cycle must not list the failed node as affected by itself)
MATCH p = (up:Component)-[:OBSERVED_CALLS*1..6]->(f:Component {name: $failed})
WHERE up <> f
RETURN DISTINCT up.name AS affected, min(length(p)) AS hops

// Declared blast radius: same traversal on CMDB edges
MATCH p = (up:Component)-[:DECLARED_DEPENDS_ON*1..6]->(f:Component {name: $failed})
WHERE up <> f
RETURN DISTINCT up.name AS affected, min(length(p)) AS hops

// Ownership drift
MATCH (t1:Team)-[:DECLARED_OWNS]->(s:Service)<-[:CODE_OWNS]-(t2:Team)
WHERE t1 <> t2
RETURN s.name AS service, t1.name AS cmdb_owner, t2.name AS actual_owner

// Who to page for a set of affected services
MATCH (t:Team)-[:CODE_OWNS]->(s:Service)
WHERE s.name IN $affected
RETURN t.name AS team, t.oncall AS oncall, collect(s.name) AS services

// Change risk: undocumented edges inside the real impact zone of a change
MATCH (c:Change {id: $change_id})-[:TARGETS]->(x)
MATCH p = (up:Component)-[:OBSERVED_CALLS*1..6]->(x)
WITH p, relationships(p) AS rels
UNWIND rels AS r
WITH p, r, startNode(r) AS a, endNode(r) AS b
WHERE NOT (a)-[:DECLARED_DEPENDS_ON]->(b)
RETURN DISTINCT a.name AS from, b.name AS to
// Path enumeration is fine at ~20 services; would need bounding on large graphs

// Retired component still holding declared relationships
MATCH (r:Component {status: 'retired'})-[d:DECLARED_DEPENDS_ON]-(other:Component)
RETURN r.name AS retired, collect(DISTINCT other.name) AS still_linked_to
```

Also return full paths (node names in order) for every blast-radius result so the UI can highlight them.

Graph algorithm (sponsor feature, required): betweenness centrality on the observed layer to rank single points of failure. Fix the seed so results are identical across runs, demo and tests:

```cypher
CALL algo.betweenness({nodeLabels: ['Component'], relationshipTypes: ['OBSERVED_CALLS'], samplingSeed: 42})
YIELD node, score
RETURN node.name AS name, score ORDER BY score DESC LIMIT 3
```

Do not use PageRank: it takes positional arguments with a single label only. Show the top 3 in the UI and let the agent call it as a tool.

## 7. Agent (LangGraph)

Tools (thin wrappers over section 6 functions, each returns rows plus paths):
`find_undocumented`, `find_stale`, `find_owner_drift`, `blast_radius(failed)` (returns declared vs real plus paths), `who_to_page(affected)`, `check_change(change_id)`, `critical_nodes()`, `create_finding(...)`, `page_owner(team, message)` (mock: writes to `notifications.log` and an in-app feed; optional Slack webhook via env).

Entry modes:
1. **Drift scan:** run all three drift tools, create one `Finding` per issue with explanation, evidence (call counts, last_seen) and a proposed CMDB fix as JSON patch.
2. **Incident:** input = failing node. Compare declared vs real blast radius, explain the difference with paths, page owners of all really affected services.
3. **Change review:** input = change id. Return risk verdict (low/medium/high) with the undocumented edges in its impact zone.
4. **Chat:** free-form questions answered only via tools; the answer must cite the paths it used.

Explanations must be grounded only in tool output. If a tool returns nothing, say so.

## 8. API (FastAPI)

- `POST /ingest` (mode: replay|live) rebuilds the graph
- `GET /graph?layer=declared|observed|diff` nodes and edges for UI
- `POST /scan` runs drift scan, returns findings
- `POST /incident {failed}` returns declared vs real radius, paths, pages
- `POST /change/{id}/review`
- `GET /findings`, `POST /findings/{id}/approve`, `POST /findings/{id}/reject`
  Approve applies the proposed fix to the CMDB CSVs and the declared layer, then marks the finding approved. Log every approval in `audit.log`.
- `POST /chat {message}`
- `GET /critical`

## 9. UI (one page)

- Graph view with layer toggle. Diff colouring: edge in both = neutral, observed only = red (undocumented), declared only = grey dashed (stale), failover = blue dashed.
- "Simulate failure" dropdown: shows declared radius count vs real radius count side by side, highlights the real paths.
- Findings panel: each finding shows explanation, evidence, path, Approve / Reject buttons.
- Notifications feed (who got paged, why).
- Critical nodes card (top 3 from the algorithm).
- Chat box for the agent.
- Clean and readable over fancy. Must look good in a 2-minute screen recording.

## 10. Repo layout

```
drift-agent/
  docker-compose.yml        # falkordb, api, ui (replay mode default)
  .env.example
  data/traces/snapshot.jsonl
  data/cmdb/*.csv, ground_truth.json
  data/codeowners.csv
  ingest/traces.py, make_cmdb.py, load.py
  graph/schema.py, queries.py
  agent/graph.py, tools.py, prompts.py
  api/main.py
  ui/
  tests/
  docs/architecture.png, data-model.md
  README.md
```

## 11. Tests (must pass)

- Drift scan finds exactly the injected drift in `ground_truth.json` (3 undocumented, 2 stale, 2 owner drifts, retired service flagged via the retired query) and does NOT flag the failover edge.
- Stale detection gives the same result whatever date the tests run on (cutoff derived from the snapshot).
- `critical_nodes()` returns the same top 3 on every run.
- Blast radius for the chosen demo node returns more services in the real layer than the declared layer.
- Ingestion run twice produces the same graph (idempotent).
- Approve endpoint updates CMDB CSV and graph; reject changes nothing.

## 12. README (judged; required by the rules)

- Problem in 3 lines, framed for developers: "what breaks when this fails, and who do I call".
- Architecture diagram.
- Graph data model (nodes, edges, why two layers).
- Every Cypher query from section 6 with a one-line explanation.
- Why a graph: multi-hop traversal, two edge layers on the same nodes, built-in algorithms.
- How to run (replay mode, one command) and how to run live mode.
- Data disclosure: OpenTelemetry demo is public; CMDB, teams and codeowners are synthetic.
- AI usage disclosure: built with Claude Code; list what was AI-generated vs designed/reviewed by the author.
- Known limits: sampling, async context loss, failover paths, why fixes require approval.
- Prior art: ServiceNow Service Graph Connectors, Now Assist for CMDB; how this differs (open source, declared vs observed kept separate, path-level explanations).

## 13. Schedule

- **Day 1 (Oct 15):** docker compose with FalkorDB, run OTel demo once and record snapshot, trace ingestion, CMDB generator, load both layers, drift queries passing tests.
- **Day 2 (Oct 16):** blast radius, ownership, change check, algorithm, LangGraph agent with all tools, API endpoints.
- **Day 3 (Oct 17):** UI, approve/reject flow, notifications, end-to-end demo path rehearsed.
- **Day 4 (Oct 18):** polish, README, architecture diagram, 2-minute demo video, blog post for the side prize, submit early.

Cut order if late: chat box, MCP server, live mode, change review. Never cut: drift scan, blast radius diff, path highlighting, approval flow, README.
