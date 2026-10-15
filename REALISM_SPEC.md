# REALISM_SPEC: real-world sources, CMDB boundaries, and a conventional UI

Goal: the project must mimic how a real hospital's IT data actually arrives, not just "synthetic data in a graph". Every input looks like the export of a real tool, a connector turns it into evidence with explicit calculations, the CMDB only ever receives CMDB-appropriate facts, and the UI uses ServiceNow conventions anyone in IT operations recognises.

Work on a new branch `realism` from `northwind-data-phase`. Run rules: RUN_PROMPT.md sections 3 and 9 (decide yourself, log in DECISIONS.md, research facts and cite them in RESEARCH_NOTES.md, ask only if genuinely blocked; keep PROGRESS.md). The demo must keep working at every commit. Never push.

Where this file conflicts with earlier specs, this file wins.

## 1. Non-negotiables (must still hold at the end)

- The truth model (`ingest/world.py`) and the drift catalogue (D1–D14, same entries and counts) stay the same. Only how views are written and read changes.
- All existing tests keep passing (adapt them to new formats, never weaken them). Cypher detectors still return exactly the ground-truth finding keys.
- Headline numbers unchanged: for `dc1-host-07`, declared radius reaches lab results only; real radius reaches 51 interfaces, 51 Emergency departments, Emergency care. `CHG-HEADLINE` = high; `CHG0031513` = low.
- No personal data: pseudonymous IDs only, no names, no email addresses (use bare IDs such as `u-h03-00412` wherever a real format has a user field).
- Generation under 10 minutes, `data/` under 500 MB, deterministic (seed 42).

## 2. Three layers in the graph, and the CMDB boundary

The graph keeps three clearly separated layers. Make the separation explicit in labels or a `layer` property, the README and the UI.

1. **CMDB layer (declared):** CIs, their attributes and their relationships, exactly as exported from the CMDB.
2. **Evidence layer (discovered/observed):** what each discovery source proves, each item carrying `source`, `first_seen`, `last_seen`, and the measurements the source provides.
3. **Reconciliation layer:** matches between evidence and CIs (identification), findings, and proposed actions.

**CMDB write-back rule:** a finding may propose a CMDB patch only when it concerns a CI's existence, one of its CMDB attributes (`os_version`, `serial_number`, `ip_address`, `support_group`, `status`), or a CMDB relationship. Metrics, events, job runs, logins and lag values are never written to the CMDB. Every other finding proposes a ticket instead:

| Finding | Action |
|---|---|
| D1 undocumented dependency, D2 stale relationship, D4 retired CI still linked, D5 owner drift, D6 shadow IT (register CI), D7 ghost (retire CI after confirmation), D8 attribute drift, D14 unregistered app (register only if approved; otherwise security ticket) | CMDB patch (human approval) |
| D9 protection gap, D10 backups failing | Incident ticket to the storage/backup team |
| D11 data residency | Security incident ticket + compliance notification |
| D12 access drift | Security incident ticket |

Implement ticket proposals as JSON in the ServiceNow `incident` / `sn_si_incident` / `change_request` shape (short_description, cmdb_ci, assignment_group, priority, description with evidence), written to `tickets.jsonl` on approval. No real ticketing integration.

## 3. Discovery sources: real formats and connectors

Replace the current generic observed files with source-native exports. Before writing each format, research the real tool's documented export/API format, cite it in RESEARCH_NOTES.md, and match field names and structure as closely as practical. Where a real format is proprietary, use the vendor's documented API/report fields and say so.

Each source gets a connector in `ingest/sources/<source>.py` that reads the raw files and produces normalised evidence rows. The loader loads only normalised evidence (plus raw-format samples are kept for the README and tests). Every evidence row carries `source` (one of the names below) and `source_record` (enough to trace back to the raw record).

| Source name | Real-world tool it stands in for | Raw format to generate | Window |
|---|---|---|---|
| `cmdb_export` | ServiceNow CMDB table export | CSV exports of `cmdb_ci`, `cmdb_rel_ci` (parent/child/type as **sys_id references**, as in a real export), `cmdb_rel_type`, `cmn_location`, `cmn_department`, `sys_user_group`, `change_request` | current state |
| `discovery_scanner` | ServiceNow Discovery / BMC Helix Discovery | ServiceNow IRE (Identification and Reconciliation) payload JSON: `{"items": [{"className", "values": {...}}], "relations": [...]}`, one payload per scan batch, plus scan schedule/coverage records | last scan per subnet |
| `apm_otel` | OpenTelemetry Collector with the `servicegraph` connector, feeding Prometheus | (a) Prometheus/OpenMetrics text exposition of `traces_service_graph_request_total`, `traces_service_graph_request_failed_total` and the `traces_service_graph_request_server_seconds` histogram, with `client`/`server` labels, scraped hourly for demo sites and central systems, daily elsewhere; (b) a small OTLP/JSON span sample (`resourceSpans → scopeSpans → spans`, with `traceId`, `spanId`, `parentSpanId`, `kind`, `service.name`, `server.address`, `db.system`) for demo sites | 14 days |
| `netflow` | IPFIX / NetFlow v9 collector (e.g. nfdump aggregated output) | Aggregated flow records per hour (demo + central) or day (elsewhere): first/last seen, src IP, dst IP, src port, dst port, protocol, packets, bytes | 14 days |
| `interface_engine` | HL7 interface engine (e.g. Mirth Connect / Rhapsody / Cloverleaf) | (a) **Channel configuration export**: per channel (= HL7 route): name, source connector (type, listening port), destination connectors (type: TCP/MLLP sender with host:port, Database Writer with a JDBC URL naming the database, HTTP sender for FHIR); (b) **channel statistics** per channel per destination per day: received, filtered, sent, errored, queued | 14 days |
| `backup_software` | Backup & recovery software (e.g. Veeam / Commvault job session report) | Job session records: job name, job type (backup, backup copy, snapshot-only), protected object, start, end, result (Success / Warning / Failed), target repository, bytes transferred | 14 days |
| `storage_telemetry` | Array management REST API (e.g. Pure Storage FlashArray protection groups) | Protection group / replication pair records: source array, target array, mode (sync/async), last transfer time, lag seconds, state, sampled hourly | 14 days |
| `identity_sso` | Entra ID / SSO sign-in logs | Sign-in records: createdDateTime, userPrincipalName (pseudonymous ID, no domain), appDisplayName, ipAddress, deviceDetail (workstation name), status (success/failure, errorCode) | 14 days |
| `db_audit` | SQL Server Audit / database audit logs | Audit records: event_time, action_id, server_principal_name (pseudonymous), database_name, object_name, statement class (SELECT / DML / DDL / admin), succeeded | 14 days, demo sites and GCC-HYD |
| `hr_feed` | HR system worker export (e.g. Workday-style) | Worker records: worker ID (same pseudonymous ID), employment status (active / terminated / leave), effective date | current state |
| `itsm_history` | ServiceNow `incident` and `change_request` history | Incident and change records: number, cmdb_ci (sys_id), assignment_group, resolved_by_group / implemented_by_group, opened_at, closed_at, state | **90 days** |

## 4. Connector calculations (implement exactly, document each in the README)

Identification (all connectors): match evidence to a CI the way ServiceNow's IRE does: first `serial_number`, then `name` (case-insensitive, aliases included), then `ip_address` (MAC where present). Record which rule matched. Unmatched evidence becomes an `UnregisteredEndpoint` / shadow-IT candidate.

- **apm_otel:** one evidence edge per (client, server) pair. `count` = sum of `traces_service_graph_request_total` increases; `errors` from the failed counter; `p95_ms` estimated from the histogram buckets (linear interpolation within the bucket); `days_seen` = distinct UTC days with a non-zero increase. The span sample is parsed with the standard rule (CLIENT span → SERVER span in another service = edge; CLIENT span with `db.system` or `server.address` = edge to that database/host) and must agree with the metrics for the sampled period (test).
- **netflow:** map src/dst IPs to CIs via identification. The **server side is the endpoint on a well-known or registered service port** (e.g. 104 DICOM, 1433 SQL Server, 1972 IRIS, 2575 HL7, 443, 636); ephemeral source ports (≥ 49152) mark the client side. Aggregate per (client CI, server CI, server port): bytes, packets, `days_seen`. Exclude flows tagged as health checks / monitoring probes.
- **interface_engine:** each channel = an Interface CI (matched by name). Each **destination connector** = an evidence edge from the interface to the destination: TCP sender host:port → identified CI; **Database Writer JDBC URL host/database → the database CI** (this is how the headline's `ORU-CRIT-* → lrc-db-01` is discovered); HTTP sender → API CI. Statistics give `count` (sent), `errors` (errored) and `days_seen`. Source connector + statistics give the inbound edge from the sending system.
- **backup_software:** per protected object: `last_success` (latest Success; Warning counts as success; snapshot-only jobs do not count as a backup), backup target, and whether a backup-copy job carried it to another site or to the immutable vault. A transient Failed followed by Success within 48 h is not a finding.
- **storage_telemetry:** per replication pair: observed `mode`, `max_lag_s`, `last_transfer`, `state`. Compare lag against the CI's RPO; lag above RPO for more than 1 hour in the window is reported in the protection check (as evidence, not a new drift type).
- **identity_sso + db_audit:** user → application access edges from successful sign-ins; user → database admin/DML actions from audit records. Compare with the declared grant path (MEMBER_OF → HAS_ROLE → GRANTS) and approved change windows; terminated/leave status from `hr_feed`.
- **itsm_history:** observed owner of a CI = the assignment group with the most resolved incidents plus implemented changes in 90 days; ties are not reported.

All thresholds (3 distinct days, 7-day backup rule, 48 h transient rule, 90-day ownership window) live in one config module and are listed in the README.

## 5. HL7 model (keep, with real provenance)

Keep interfaces as CMDB CIs registered by the integration team (custom class `u_cmdb_ci_hl7_interface`, which is what real ServiceNow customers do since there is no out-of-the-box class). What the CMDB lacks is the interfaces' internal destinations; these are discovered from the interface engine's channel configuration and statistics (section 3). The README states this explicitly.

## 6. ServiceNow conventions (names everywhere)

- Classes and labels shown in the UI: Business Service, Service Offering, Application, Interface (HL7), Database, Virtual Machine, Server, Storage Array, Backup Target, Network Device, Medical Device, Endpoint / Endpoint Group.
- Per-hospital services (`svc-hXX-ed-critical-results`) become **Service Offerings** (CSDM): class `service_offering`, graph label `ServiceOffering`, contained by the Business Service `svc-emergency-care`. Report offerings and business services separately in every count.
- Relationship names in the UI: Contains, Depends on, Runs on, Stored on, Replicates to, Backed up by, Connects to. Observed evidence is named by what it proves, e.g. "Depends on (observed: interface engine, not in CMDB)".

## 7. UI: conventional dependency view

- **Layout:** traditional CMDB dependency view. Business services at the top, then service offerings, interfaces/applications, databases, virtual machines, servers, storage at the bottom. Arrows always point in CMDB direction (parent → child) and are **labelled** with the relationship name.
- **Impact by colour, not arrows:** the changed component has a thick outline; impacted nodes are filled red; unaffected nodes stay neutral.
- **Edge styles:** solid grey = in the CMDB and confirmed by evidence; **dashed red = observed only, not in the CMDB**; grey dotted = in the CMDB, no evidence (stale candidate); blue dashed = failover relationship.
- **Hover/click on an edge:** relationship name, which layer(s) it is in, and for evidence the `source`, counts, `days_seen`, first/last seen.
- **Legend** always visible: shapes per class, edge styles, colours.
- **Findings:** each shows the evidence sources used and whether the proposed action is a CMDB patch or a ticket.
- **New "Sources" tab:** one row per discovery source: what it reveals, the real-world tool it stands in for, raw format, record count, window, and a "view sample record" button showing one raw record next to the evidence row it produced.
- Keep light and dark mode.

## 8. Tests to add

- Each connector: parses its raw format; produces the expected evidence for a few hand-checked records; identification rule recorded.
- Raw format validation: OTLP JSON follows the protobuf JSON mapping field names; IRE payload has `items`/`className`/`values`; Prometheus text parses with a standard parser.
- Span sample and servicegraph metrics agree for the sampled period.
- Interface engine: every `ORU-CRIT-*` channel has a Database Writer destination whose JDBC URL resolves to `lrc-db-01`; the CMDB has none of these relationships.
- Write-back rule: approving D9–D12 never changes CMDB files and writes a ticket; approving D1/D2/D4–D8 changes only CMDB-appropriate fields/relationships.
- Headline and CHG0031513 numbers unchanged; offerings and business services counted separately.

## 9. Deliverables

- README: "Where the data comes from" (section 3 table), "How evidence is calculated" (section 4), "What may be written back to the CMDB" (section 2), all thresholds.
- Updated screenshots: change review for CHG-HEADLINE and CHG0031513, Sources tab, one finding with sources.
- REPORT.md: a "Realism" section summarising formats, sources researched, decisions, and anything still simplified compared with a real deployment.