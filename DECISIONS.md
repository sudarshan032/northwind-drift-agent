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

## 4. Generator architecture
- Q: How to guarantee "views disagree only where drift was injected"?
- Options: generate each view independently with shared seeds; build one truth model and derive views; mutate files after writing.
- Chose: `world.py` builds one truth model; `views.py` derives every view from it plus a drift *plan*; `drift.py` builds the plan (edits + `ground_truth`). With the empty plan views agree by construction. Why: one place to reason about consistency; tests compare drift-off vs drift-on runs of the same world.
- Determinism: stdlib `random.Random("<seed>:<subsystem>")` per subsystem (string seeds are hash-randomisation independent), sorted outputs, fixed CSV/JSON formatting.

## 5. ground_truth.json format
- Kept the DATA_SPEC fields `{id, type, description, ci_ids, sources, expected_finding}` and added `finding_keys`: the exact findings the oracle must report (D3: the findings that must NOT appear; D13: `[alias, ci]` pairs). `ci_ids` hold CI *names* (unique, readable; sys_ids are derivable from cmdb_ci.csv). `expected_finding` is the finding kind (`undocumented_dependency`, ...), `none` for D3, `resolve_as_same_ci` for D13. Why: test 9.3 needs a machine-checkable "exactly these" comparison.

## 6. How interfaces show up in observed data
- Q: Interfaces have no IP or process of their own. How do flows reach `ORU-CRIT-*`?
- Chose: the interface engine reports per-route statistics (as Rhapsody/Cloverleaf/Mirth do): `source -> route` (inbound) and `route -> target` / `route -> staging DB` (outbound) records, with the route name as endpoint name and the engine VIP as IP. Why: makes "ORU-CRIT-H03 -[:OBSERVED_CALLS]-> lrc-db-01" a one-hop observed fact, which is exactly the headline.

## 7. Dependency matching is undirected
- Declared `Depends on` is parent -> child (route depends on its source app), but the observed leg runs source -> route (data push). D1 (undocumented) and D2 (stale) therefore match a flow to a declared `Depends on` in either direction. Why: a CMDB relationship documents the pair; direction semantics differ by protocol.

## 8. Scope of the stale rule
- Stale = declared `Depends on` (flows/netflow) or `Replicates to` (replication status) with no observation in 14 days, failover=false, neither end retired. `Backed up by` is covered by the D9/D10 rules; `Contains`, `Runs on`, `Stores on`, `Connects through` are structural and not traffic-observable. Business services group applications with `Contains` for the same reason.

## 9. Located at / Managed by
- Q: DATA_SPEC lists `Located at` and `Managed by` as relationship types.
- Chose: express them as the `site` and `support_group` reference columns of `cmdb_ci` (ServiceNow stores location and support group as reference fields; research R6), and have the loader create `LOCATED_AT` / `MANAGED_BY` edges from them. The loader still maps those type strings if they ever appear in cmdb_rel_ci. Why: avoids two copies of the same fact that could disagree.

## 10. Test 9.4 "every app's chain reaches a storage array" (spec clarification)
- DATA_SPEC section 2 gives critical access hospitals 0 arrays and community hospitals 0–1, while section 3 describes the app → DB → VM → host → array chain for *central* systems.
- Chose: central apps/DBs (and local apps at sites with an array) must reach a `StorageArray`; local apps at array-less sites run on physical servers with direct-attached disks and must reach a `Host` at their own site. Cloud VMs run on availability zones (`cmdb_ci_availability_zone`, label Host) that store on cloud block storage. Test implements exactly this.

## 11. What discovery can see
- Discoverable = individual network devices (router/firewall/switch), hosts, VMs, arrays, backup appliances, medical devices, demo-site workstations/printers/laptops. Not discoverable: applications, DB instances, interfaces, business services, SaaS, group CIs (endpoint groups, AP groups, bedside fleets), IP-network segments, cloud zones/storage, the cloud vault.
- Non-demo user subnets are scanned in `aggregate` mode (counts only, matching endpoint-group counts) because endpoints there are group CIs by design (DATA_SPEC 1.2). Medical segments are scanned **passively** (active scans can disrupt medical devices). Every subnet is `scanned=true` so D7 ghosts are meaningful.

## 12. D9 mechanism (protection-path drift)
- Chose: the CMDB says `backup_offsite=true` (policy `bp-tier1-daily-offsite`, Backed up by the DC-PRI appliance) but the observed jobs are storage snapshots on the same-site array (`snap-local-daily` → `arr-h17-01`), and local arrays do not replicate. Jobs succeed, so D10 does not fire; the path never leaves the site, so D9 does. Targets: `h17-labmw-db` (required) plus 3 local PHI databases at other hospitals with arrays. Why: "snapshots mistaken for backups" is a classic real gap, and it keeps D9/D10 disjoint.

## 13. D10 timing
- Last success on window day 1–5 (≥ 8 days before SNAPSHOT_END), failures every night since. Transient noise failures are on days 2–9 and always followed by a success the next night, so they never reach the 7-day threshold.

## 14. D11 data residency
- An ad-hoc replication (`vol-dwh-db-01-export`, last 3 days) from cloud storage `stor-usw-01`, which stores PHI `dwh-db-01`, into `stor-cin-01` in cloud-central-india. Replication is not a flow, so it does not also count as D1.

## 15. D12 access cases
- (a) last active member of `grp-hyd-clinical-apps-support`: admin on `emr-db-01` at ~21:00Z on 11–12 Oct (approved emr-db-01 windows are 09:30–12:30Z); the role grants admin, so only the change-window rule catches it. (b) first disabled H03 user (HR status) logging in to `emr-app`. (c) an H17 radiology technologist reading `pharm-app` (no grant). (d) a GCC cloud-ops account writing to `billing-db-01` (no grant). Legitimate DBA / EMR-support admin work happens inside approved, closed changes (noise that must not fire).

## 16. D13 naming mismatches
- Discovery hostnames that differ but match by serial (`LIS-PROD-02.nw.local` = `dc1-vm-lis02`, `H17-CORE-SW01`, `DC1ESX12.nw.local`, `H44-DR-ROOM1`) and netflow names that are DICOM AE titles resolved by IP (`H03CT2` = `h03-ct-02`, `H44XR1` = `h44-xray-01`). The loader stores them in `Component.aliases`.

## 17. D3 failover trap: exactly 4
- Truth has exactly four failover relationships: `emr-app -> emr-db-dr`, `ie-app -> ie-app-dr`, `pacs-app -> pacs-vna-dr` (Depends on) and the reverse/failback replication `arr-dr-01 -> arr-pri-a-01`. A planned fifth (`lis-app -> lis-db-dr`) was dropped to match the catalogue count; `lis-db-dr` is still fed by array replication.

## 18. D5 owner drift story
- Support for `lrc-db-01`, `ie-db-01` (→ HYD-Integration-Support), `portal-app`, `dwh-etl-app` (→ HYD-Cloud-Ops) and `siem-app` (→ HYD-Cybersecurity) moved to Hyderabad; the CMDB still lists the former US team. `ris-db-01` is run by NW-DBA but the CMDB lists the app team. Evidence = ownership_evidence.csv; the true team always dominates, service-desk rows are small noise.

## 19. D1 picks and noise
- D1 (besides the headline): pharmacy reading LIS DB, billing's nightly direct EMR DB pull, portal → scheduling API, analytics extract from PACS DB, H03 RTLS → EMR, H03 MRI → PACS cache (netflow), H17 lab analyser → middleware (netflow). All run on ≥ 3 days.
- Noise: 6 one-off `health_check=true` probes from `mon-app`, and two undeclared ad-hoc flows on 1–2 days (`lis-reporting -> pharm-db-01`, `sched-app -> ris-app`) that the ≥ 3-day rule must ignore.

## 20. Hybrid backup for cloud databases
- Cloud PHI databases (portal, DWH) are backed up through the DC-PRI backup appliance, so their protection path reaches DC-DR and the immutable vault like on-prem systems (test 9.2: "all protection paths reach DC-DR and the cloud vault").

## 21. Infusion pump server
- DATA_SPEC lists both an "Infusion pump server" inventory row and a "pump server" local-server role. Modelled once, as a vendor appliance (`cmdb_ci_med_therapeutic_device`, Pharmacy department, on the medical segment). Not repeated as a local server role.

## 22. CHG-HEADLINE
- Number literally `CHG-HEADLINE` (tests and the demo look it up), state `scheduled`, approval `requested`, window 2026-10-17 10:00–12:00Z (03:00 Pacific). Other changes use ServiceNow-style `CHG00xxxxx`. dc1-host-07 runs nothing but `dc1-vm-lrc01`, so the declared blast radius really is LIS reporting only.

## 23. Observed granularity and size
- Hourly only for legs touching H03/H17/H44/GCC-HYD or DC-PRI/DC-DR/cloud (RUN_PROMPT 4.2), daily otherwise; hourly records only for hours with traffic. Full data set ≈ 143 MB (< 500 MB), so no further coarsening.
- data/ exceeds 50 MB → ignored by git (`data/README.md` kept). `data_demo/` (`--subset demo --gzip`, ≈ 9 MB) is committed. `--gzip` writes `.jsonl.gz` / `world.json.gz` with gzip mtime=0 so output stays byte-identical; loader and oracle read `.gz` transparently. A test asserts the committed subset equals a fresh run.

## 24. Demo subset
- WA + OR sites, DC-PRI, DC-DR, both cloud regions, GCC-HYD, external SaaS (DATA_SPEC 10). Site-specific interfaces follow their hospital. The drift plan is re-drawn on the subset with the same catalogue; H44 (Montana) is outside it, so the subset has 4 D13 cases instead of 5 and 24 ORU-CRIT routes in the headline. Full-world tests are the authority.

## 25. Identity sizing
- Demo-site users ≈ 3–4 per workstation per department (shared clinical workstations), giving 2,696 at H03, 1,115 at H17, 150 at H44 and 300 at GCC-HYD (final data). 3% of accounts are `disabled` (HR status from `ext-erp-hr-saas`) and never log in, except D12(b). Non-demo groups carry `member_count` only.

## 26. Loader identification
- Discovery identification (serial, else hostname + IP) and flow endpoint resolution (name/sys_id, else unique IP) are computed in Python in the loader, then written as `IDENTIFIES {matched_by}` edges and `Component.aliases`. Discovered attributes stay on `:Discovered`. Unresolvable flow endpoints are counted and reported (zero in the generated data).

## 27. Milestones 3 and 4 in one commit
- The views, oracle and drift plan were written together because the plan interface shapes the view functions; committed as one "M3+M4" commit after 9.2 and 9.3 both passed. Logged here for transparency.

## 28. Research-driven changes (see RESEARCH_NOTES.md)
- Imaging ranges raised (R2), bedside pump/monitor fleets added as group CIs (R2), CAH beds ≤ 25 (R1), HL7 volume calibration and ADT trigger-event mix (R3), ServiceNow class names (R6). None changes the drift catalogue (counts/types verified by test 9.3).

## 29. Kept as specified despite research
- Relationship type strings (`Stores on::Stores`, `Backed up by::Backs up`, `Replicates to::Replica of`, `Connects through::Connects`) and AD group names (`grp-H17-ED-nurse`) stay as DATA_SPEC defines them; the realistic alternatives are listed for the owner in REPORT.md.

## 30. Graph size vs DATA_SPEC guidance
- Actual ≈ 48–49k nodes / ≈ 140k edges, below the DATA_SPEC 9.6 guidance (80–150k / 300–700k). Per RUN_PROMPT 4.5 the world was not padded: the inventory table (group CIs outside demo sites) drives the size. Test 9.6 fails only outside 30k–300k nodes.

## 31. Deviation from the DATA_SPEC inventory table
- Imaging device ranges in section 2 replaced by research-backed ranges (R2). Other rows unchanged.

## 32. External addresses
- SaaS / clearinghouse endpoints use the RFC 5737 documentation range 203.0.113.0/24; everything internal is RFC 1918.

## 33. Review #1 fixes and clarifications
Independent fresh-context review (DATA_SPEC + RUN_PROMPT + code only) found 20 items. Fixed:
- Endpoint totals now split across departments by largest remainder, so site totals stay inside the DATA_SPEC ranges (rounding had pushed some clinics to 6 printers / 9 workstations).
- `test_inventory_ranges` now holds the section 2 table as literals for every row and site type (imaging rows = research ranges) and checks the 30% clinic X-ray share.
- 9.2 additionally asserts every PHI data holder reaches DC-DR **and** `bkp-vault-usw-01`, and declared `Backed up by` pairs equal observed backup-job pairs.
- Size report adds CI counts per site type and declared edge totals; the loader test prints full graph counts.
- ADT event split now draws errors per split (was copying the hour's errors into each split).
- DICOM interfaces: `source` = site medical-device segment (many modalities), `via` = receiving PACS node (DICOM does not go through the HL7 engine).
- Critical access hospitals get a `VITALS-Hxx` route from their monitor gateway device via the interface engine. They have no `DICOM-FWD` (no PACS cache: modalities store straight to central PACS) and their `LAB-RES` source is `labmw-central` (small sites commonly use centrally hosted middleware).
- SSO dependencies added for `gcc-automation-app`, `gcc-devtest-app`, `pacs-vna-01`. DR standbys (`ie-app-dr`, `pacs-vna-dr`) keep none: adding failover links would change the D3 count of exactly 4.
- GCC-HYD has 8 individual printers; non-acute endpoints are laptops/tablets; cloud storage records `raid_level=RAID6` (provider-managed, RAID6-equivalent).
- 20 clinic user subnets (every 50th clinic) are `scanned=false`, so the ghost rule's coverage condition is exercised.
- D6 `ci_ids` hold the shadow hostname; D13 `sources` reflect the actual channel.
- Loader test hides `truth/` while loading (enforces "never reads world.json").
- Oracle documents why the 48-hour transient rule is subsumed by "no success in 7 days".
- Group ids follow the spec example (`grp-H03-LAB-tech`, `grp-H17-RAD-tech`).
Kept, with reasons (also DECISIONS #9, #10, #21, #29, #31): Located at / Managed by as reference columns; app chains at array-less sites end at a physical host (DAS); research-backed imaging ranges and bedside fleets; no separate datastore CI class (DATA_SPEC section 8 has no Datastore label: hosts `STORES_ON` arrays directly, DBs `STORES_ON` the array holding their volume); availability zones labelled Host; Site `CONTAINS` Department; OML^O21 for lab orders and the extra SIU scheduling feed (research R3); portal and RTLS FHIR APIs call the EMR directly (API calls, not engine-hosted HL7 routes); demo subset has 4 D13 cases (H44 is outside WA/OR).

## 34. Review #2 fixes and clarifications
Second fresh-context review: no high-severity findings. Fixed:
- `DICOM-MOD-Hxx` routes whose target is a local PACS cache now sit at the hospital (so their records are daily at non-demo sites, per RUN_PROMPT 4.2) and carry the medical-segment gateway (`10.1xx.8.1`) as their IP instead of the PACS cache's (src_ip no longer equals dst_ip).
- Tier-3 (`bp-tier3-weekly-offsite`) objects get weekly Sunday jobs only; they are excluded from transient-failure noise so a failed weekly job can never cross the 7-day rule.
- `groups.csv` / `:Group` use `count` (DATA_SPEC 5 wording) instead of `member_count`.
- Tests: determinism across processes with a different `PYTHONHASHSEED`; D3 pairs have no flow/netflow and the failback replication pair has no replication records; every discoverable CI is discovered without drift (named exemption: `bkp-vault-usw-01`, no IP); no PHI in cloud-central-india on the generated drift files except the single D11 finding; demo subset asserts 24 headline routes and 4 D13 cases (documented in its README).
- `OML^O21` on `ORM-EMR-LIS` now has an inline rationale.
Clarified, not changed:
- Flow/netflow column names are `src_name/src_ip/dst_name/dst_ip` (RUN_PROMPT 4.3 identification keys) rather than DATA_SPEC 6's `source/target`/`src/dst`; duplicating them would only add bytes.
- No datastore records: DATA_SPEC 8 has no Datastore label; host `STORES_ON` array stands for the datastore mount (#33).
- Some site traffic is plain app-to-app dependencies, not engine-hosted routes: MWL broker → RIS order pulls, nurse-call → engine ADT feed, RTLS / portal FHIR API calls. Interface CIs are the HL7/DICOM routes DATA_SPEC 4 enumerates.
- Critical access hospitals have no PACS cache (no `DICOM-FWD`) and use central lab middleware (#33).
- Applications without their own database: `sso-app` (directory-backed), `siem-app`, `mon-app` (SaaS-style log/metric stores), `bkp-app` (catalogue on the appliance), `gcc-automation-app`, `pacs-vna-01` (image store on arr-pri-a-04), DR standbys. DR standbys (`ie-app-dr`, `pacs-vna-dr`) have no SSO link: a failover link would change the D3 count of exactly 4.
- The admin-clerk role grants to external SaaS (ERP/HR, email): SaaS records are CIs in the CMDB, so `Role -GRANTS-> ExternalService` follows the same model.
- DR AD domain controllers carry live traffic: AD is multi-master, so they are not "quiet until failover" like the DR databases; the DR site is otherwise silent.
- Graph size: ~49k nodes / ~140k edges, below DATA_SPEC 9.6 guidance (and its own 50k fail line); RUN_PROMPT 4.5 (fail only outside 30k–300k, do not pad) governs (#30).

# AGENT_SPEC run (2026-10-15)

## 35. Phase 0 (a): DATA_SPEC section 2 imaging rows
- DATA_SPEC table now carries the research ranges (CT 3–5/1–2/0–1, MRI 2–4/1/0, X-ray 8–15/3–6/1–2, US 10–20/4–8/1), matching the generator and `test_inventory_ranges`.

## 36. Phase 0 (b): demo subset = all hospitals
- `--subset demo`: all 51 hospitals, DC-PRI, DC-DR, both cloud regions, GCC-HYD, external SaaS; no clinics or non-acute sites. Same catalogue (D13 = 5, D14 = 1) and headline (51 routes). D6/D7 cases that sit at clinics in the full world move to community hospitals in the subset (no clinics exist there). data_demo/ is 8.3 MB gzip.

## 37. Phase 0 (c): ServiceNow standard relationship types
- `Stores on::Stores` → `Provides storage for::Stored on` with ServiceNow semantics (parent = storage provider, child = consumer); `Connects through::Connects` → `Connects to::Connected by` (parent = device, child = switch/segment). `Replicates to::Replica of` and `Backed up by::Backs up` stay documented custom types.
- Graph edge names are unchanged (`STORES_ON` consumer → storage, `CONNECTS_THROUGH`): AGENT_SPEC says the implementation's names win; the loader flips storage rows so graph direction stays as before.

## 38. Phase 0 (d): department and Emergency care links
- Site-level routes now carry the hospital department they serve in `cmdb_ci.department` (ORU-CRIT → ED, LAB-RES → LAB, DICOM → RAD, VITALS → ICU or ED) → graph `IN_DEPARTMENT` → `Department` ← `CONTAINS` ← `Site`. Routes live at DC-PRI, so the plausibility test exempts Interfaces from "department site == CI site".
- New enterprise business service `svc-emergency-care` (patient_safety_impact = high) `Contains` each hospital's `svc-hNN-ed-critical-results` (now also high), which `Contains` its `ORU-CRIT-Hnn`. Declared by the service owner, so present in truth and CMDB (not drift).

## 39. Phase 0 (f): FHIR layer and D14
- New `emr-fhir-api` (EMR FHIR R4 / SMART on FHIR / Bulk Data, 2 VMs, tier 0) → emr-db-01, sso-app. `portal-app` → emr-fhir-api (FHIR R4, replaces portal → emr-app), `dwh-etl-app` → emr-fhir-api (`FHIR Bulk $export`, nightly), each large hospital's RTLS → emr-fhir-api (D1 candidate becomes `h03-rtls → emr-fhir-api`).
- D14: `cardiorisk.example.com` (203.0.113.250), a third-party SMART on FHIR app. Weekdays Oct 5–14, 08:00–17:00 Pacific, it calls emr-fhir-api (FHIR reads) and sso-app (SMART launch tokens); H17 cardiology physicians (`grp-H17-CARD-physician`, CARD forced into H17's departments) launch it (`access_log` action `launch`). No CMDB record, no scan record.
- Rule (oracle and loader): a flow endpoint that resolves to no CI by name, sys_id or IP and carries traffic on ≥ 3 days is an `unregistered_app` finding. Accesses to non-CI targets are excluded from the D12 access rule (they belong to D14). The loader stores such endpoints as `:UnregisteredEndpoint` nodes with `OBSERVED_CALLS` / `OBSERVED_ACCESS` edges.

## 40. Impact semantics as a derived edge layer
- AGENT_SPEC 1.2 rules are materialised by Cypher (`graph/impact.py`) as `IMPACTS_DECLARED` / `IMPACTS_REAL` (propagating) and `AFFECTS_DECLARED` / `AFFECTS_REAL` (terminal: a route's data consumer and the departments it serves). The loader rebuilds them at the end of every load (delete + MERGE, idempotent). Why: radius queries become single-type directed traversals (`algo.bfs(start, 10, 'IMPACTS_REAL')`), the direction rule of DECISIONS #7 lives in exactly one Cypher rule (`feeds_route`), and the paths are `shortestPath` over one edge type. Terminal edges stop the EMR (a data consumer) from dragging every EMR dependant into the radius.
- Business services propagate impact upward through `CONTAINS`; the interface engine impacts the routes it `CONTAINS`. Failover links and health checks never carry impact.

## 41. FalkorDB timeouts
- docker-compose sets `FALKORDB_ARGS="TIMEOUT_MAX 60000 TIMEOUT_DEFAULT 10000 MAX_QUEUED_QUERIES 25"` (parameter names verified at docs.falkordb.com/getting-started/configuration). This replaces the image's deprecated `TIMEOUT 1000` and its `RESULTSET_SIZE 10000` default (now unlimited; every agent query has its own LIMIT). Agent tool queries pass a 5 s per-query timeout and return `{"error": "query timed out"}` instead of raising.

## 42. Phase 1 detectors: loader enrichments for exact Cypher
- `OBSERVED_CALLS` / `OBSERVED_NETFLOW` carry `days` (sorted distinct dates) so the ≥ 3-day rule unions days across the per-msg-type edges of a pair, exactly like the oracle. `OBSERVED_ACCESS` admin edges carry `times` so the change-window rule checks every admin event. Each CI gets `scanned_subnet` (its IP's scanned CIDR, computed at ingest: IP-in-CIDR is not expressible in Cypher) for D7.
- FalkorDB quirks worked around: no `reduce` nested in `reduce` (two WITH steps); pattern predicates after `WITH` and filtered pattern comprehensions fail ("Type mismatch", "Unable to resolve filtered alias"), so those use `OPTIONAL MATCH … count() = 0`.
- Detectors are in `graph/queries.py:DETECTORS` (D1, D2, D4–D12, D14) plus `resolved_aliases` (D13); D3 has no detector and the failover rule keeps it silent. Tests assert exact equality with ground truth on the full data and the demo subset.
- D10 cutoff is the constant `SNAPSHOT_END - 7 days` (2026-10-07T23:59:59Z), never "now" (replay data).

## 43. Change review risk
- high: the real radius reaches a `patient_safety_impact=high` business service the declared radius does not; medium: real radius has strictly more components; else low. Undocumented edges = observed calls/netflow between nodes of the real radius (targets, impacted, consumers) with no declared `DEPENDS_ON` either way.

## 44. Critical nodes
- `algo.betweenness({nodeLabels: ['Component'], relationshipTypes: ['OBSERVED_CALLS'], samplingSize: 5000, samplingSeed: 42})` (signature verified at docs.falkordb.com/algorithms/betweenness-centrality). The full observed call graph takes ~0.1 s, so no label restriction was needed; samplingSize 5000 ≥ node count samples every source (exact, and seed-independent).

## 45. Who to page / protection / access
- Observed owner = team with the largest incidents + changes in `OBSERVED_OWNER`; flagged when it differs from `MANAGED_BY`. Protection check returns PHI components with no copy at another site, no copy in an immutable vault, or no success in 7 days (= D9 ∪ D10 here). Access check = the D12 detector with reasons (`disabled_user`, `no_grant_path`, `admin_outside_change_window`).

## 46. LLM access
- Claude is called through the official `anthropic` SDK (`client.beta.messages.create`) behind a two-method interface (`agent/llm.py`): `ClaudeLLM` (model from `NORTHWIND_MODEL`, default `claude-sonnet-5-5` because AGENT_SPEC asks for a current Sonnet; server-side refusal fallback `fallbacks="default"` with beta `server-side-fallback-2026-07-01`; `tool_choice` left at auto because Sonnet 5.5 rejects forced tool use) and `FakeLLM` (scripted replies; items can be callables that read the previous tool result). LangGraph (`langgraph==1.2.14`) runs the flows; it is used for orchestration only, not for model access.
- No `ANTHROPIC_API_KEY` exists on this machine, so every test and check ran with `FakeLLM`. The live-LLM incident check from AGENT_SPEC 2.3 is still open (REPORT, owner action).

## 47. Triage design
- Detector rows become findings; D1 rows sharing one callee with ≥ 3 callers are grouped (the 51 ORU-CRIT → lrc-db-01 rows become one finding whose patch adds 51 relationships). Every ground-truth key is covered by exactly one stored finding (test).
- Severity: failure-type findings (D1, D5, D8, D9, D10) = max patient_safety_impact in the real radius of the primary CI (high 3, medium 2, low 1) + log10(radius size + 1) + type weight (D1 +1, D9/D10 +1.5, grouped +0.5). Exposure findings get fixed weights: D11 and D14 5.0, D12 4.5 (disabled user or admin outside window) or 3.5, D6 2.5, D2/D4/D7 1.5. ≥ 5 critical, ≥ 3.5 high, ≥ 2 medium.
- All findings get a template explanation built from detector output; the top 10 are rewritten by the LLM from the finding facts only (no tools, so it cannot invent). Stored as `(:Finding)-[:ABOUT]->(:Component)`; status and created_at survive re-scans.

## 48. Proposed patches and approval
- JSON-Patch-style ops on `/cmdb_rel_ci` and `/cmdb_ci/<name>/<field>` (agent/findings.py docstring). D1 add `Depends on`, D2 remove, D4 remove all CMDB relationships of the retired CI, D5 support_group = observed owner, D7 status = retired, D8 attribute = discovered value, D9 backup_offsite = false (CMDB states the truth; remediation is operational), D6 register the device (owner to confirm), D14 register the app as an external service pending vendor review. D10, D11 and D12 have no CMDB patch: their fix is operational (page owner / security).
- `approve` applies ops to the CSVs and the declared graph edges, rebuilds the impact layers, re-runs the finding's detector and records `resolved`. `reject` changes only the finding status. Both append to `runtime/audit.jsonl`. After approving the headline finding, the declared radius of dc1-host-07 reaches Emergency care (test).

## 49. Real-impact refinement: telemetry
- Observed calls over `syslog-tls` (log shipping to the SIEM) no longer carry impact: they are fire-and-forget, and counting them made the SIEM a dependency of every system that logs.

## 50. Incident, ambiguity, chat
- Incident and chat are a LangGraph tool loop (model ↔ tools, max 8 steps). Incident has a prescribed method in its system prompt (search → shared_dependencies → changes_on → impact_path → who_to_page). New queries `shared_dependencies` (components every given route needs over IMPACTS_REAL), `changes_on`, `search_components`.
- Ambiguity judgement covers the 6 D13 aliases and the D14 app; the LLM must return JSON with one of the three AGENT_SPEC decisions; anything else becomes "needs human review". The judgement is stored on a Finding (`judgement_json`, `decision`).
- Tool results are cut at 30,000 characters for the model; empty results are returned as `{"result": "no results"}` so the model can say nothing was found.

## 51. API
- FastAPI app `api/main.py` with the AGENT_SPEC endpoints plus `GET /changes` (change picker), `GET /hospitals` (map: 51 hospitals with approximate public city coordinates in `api/geo.py`, flagged when inside a component's radius). `/subgraph` is capped at depth 2 and 300 edges. A FalkorDB timeout becomes HTTP 504 with `{"error": "query timed out"}`. Tests inject `FakeLLM` through `app.state.llm`; with no key, agent endpoints return the "add ANTHROPIC_API_KEY" message and everything else works.
- `/changes/{id}/review` also returns a small highlighted graph: the declared path to each declared service plus the real paths to Emergency care and to the EDs of H03, H17, H22, H44, with each edge classified `both` / `observed_only` / `declared_only` / `failover` / `derived` (`graph/queries.py:edge_layers`).

## 52. UI
- One static page (`ui/index.html`, served by the API at `/`), no build step: Cytoscape.js 3.30.2 from cdnjs for the path graph, a hand-drawn SVG map (equirectangular, Alaska inset, state labels) instead of a tile map (no extra dependency or API key). Tabs: Change review (default, opens on CHG-HEADLINE), Findings (approve/reject), Incident, Chat; `#findings` etc. deep-link a tab. Light/dark via `prefers-color-scheme` plus a toggle.
- Edge colours: observed only = red, declared only (a traffic-confirmable `Depends on`/`Replicates to`/`Backed up by` with no observation) = grey dashed, failover = blue dashed, declared and observed or structural (runs on, stores on, contains, department) = neutral solid.
- Screenshots: `docs/change-review-light.png`, `docs/change-review-dark.png`, `docs/findings.png` (headless Chrome against the full data).

# REALISM_SPEC run (2026-10-16), branch `realism`

## 53. Thresholds in one module
- `ingest/config.py` holds every threshold (3 distinct days, 7-day backup rule and its cutoff, 48 h transient rule, 90-day ownership window, 1 h RPO-breach duration, ephemeral port floor, service ports) and the CMDB write-back field allowlist. Detectors pass them as Cypher parameters.

## 54. CMDB write-back rule and tickets
- A finding may patch only CI existence (register / retire), `os_version`, `serial_number`, `ip_address`, `support_group`, `status`, or CMDB relationships; `agent/approval.validate` rejects anything else. D9's former `backup_offsite=false` patch is gone.
- D9/D10 propose an `incident` to NW-Backup-Storage, D11 an `sn_si_incident` to NW-SecOps plus a compliance notification to NW-Privacy-Compliance, D12 an `sn_si_incident` (priority 1 if admin outside a change window). Approving writes the ticket to `runtime/tickets.jsonl` (ServiceNow field names: short_description, cmdb_ci, assignment_group, priority, category, description, state) and leaves the CMDB untouched; `resolved` stays false (the problem persists until the team acts).
- Approved CMDB patches are also recorded as a closed standard `change_request` in `tickets.jsonl` (CMDB changes are changes). Rejecting a D14 registration raises its `sn_si_incident`.

## 55. CSDM service offerings (no truth change)
- The truth model is unchanged; the CMDB export classifies the 51 `svc-hNN-ed-critical-results` CIs as `service_offering` (graph label `ServiceOffering`), contained by the business service `svc-emergency-care`. Impact `contains` covers both; radius counts report `business_services` and `service_offerings` separately (headline real: 2 business services, 51 offerings; declared: 1 and 0).

## 56. Layers
- Loader tags every node and edge with `layer`: `declared` (CMDB export + IAM), `evidence` (discovered/observed: `:Discovered`, `:UnregisteredEndpoint`, `OBSERVED_*`), `reconciliation` (`IDENTIFIES`, impact layers, `:Finding`, `ABOUT`).

## 57. Pipeline: raw exports -> connectors -> normalised evidence -> graph
- The generator (`ingest/export.py`) renders the unchanged truth model + drift plan into each source's native files under `data/raw/<source>/`. Connectors (`ingest/sources/<source>.py`, orchestrated by `python -m ingest.connect`) read only those files and write `data/normalised/`; the loader and the oracle read only `normalised/` (plus the declared IAM export in `data/identity/`). Every evidence row has `source` and `source_record` (file + row/item/ID). Each source module holds both halves of its format (writer + connector) so format and parser change together.
- `generate` runs the connectors at the end, so `data/` is always complete. Generation ≈ 15 s; full data 388 MB (apm_otel 199 MB) — under 500 MB.

## 58. Identification (ServiceNow IRE order)
- `ingest/sources/identify.py`: serial_number, then name (case-insensitive; serial-matched discovery hostnames become aliases), then sys_id, then IP (only IPs owned by exactly one CI); server ends of network evidence first try `ip:port` against the discovery scanner's listening services (Service Mapping style), which is how device netflow resolves to the PACS cache / lab middleware application rather than its VM. The rule used is stored on every row (`match_rule`, `client_rule`, `server_rule`) and on `IDENTIFIES {matched_by}`. Names resolved only by IP become aliases (D13 DICOM AE titles). D8 serial drift now identifies by name (was hostname + IP); results are unchanged.

## 59. cmdb_export
- Raw-value export (`sysparm_display_value=false` style): references as sys_ids, choices as codes (install_status 1/7, operational_status 1/6, change state −2/3, risk 2–4); `Depends on::Used by` uses its OOB sys_id; other reference tables get deterministic sys_ids. Manufacturer/model are exported as text (no core_company / cmdb_model export). `failover` and replication `mode` are custom `u_failover`, `u_replication_mode`. `cmdb_export.write(cmdb_export.read(x))` reproduces the export byte for byte (test).
- Approval edits the CMDB *export* (read -> patch -> write) and re-runs the cmdb_export connector, so the raw export remains the system of record.

## 60. discovery_scanner
- IRE payloads batched at ~600 items (the API accepts multi-item payloads; one payload per host would mean ~12k files). Devices carry a `cmdb_ci_network_adapter` lookup; listening services are `cmdb_ci_appl` / DB-instance items with `tcp_port`, related `Runs on::Runs` to the host item (research R10 shows tcp_port on SQL instances; cmdb_tcp rows were not added).

## 61. apm_otel and netflow details
- servicegraph: configured `latency_histogram_buckets` [10ms, 50ms, 100ms, 500ms, 1s, 5s] instead of the 16 defaults (size), `failed` label included, empty `connection_type` omitted (equivalent in Prometheus). Scrapes contain every series seen so far (cumulative counters), timestamps in ms at the end of each period. Calls into telemetry sinks (`config.TELEMETRY_SINKS`, the SIEM) carry no impact (replaces the old syslog-protocol rule: servicegraph has no protocol label).
- netflow: nfdump 1.7 CSV layout (smaller than 1.6's 48 columns), both directions per conversation, plus collector enrichment columns `srcDns`/`dstDns`/`tag`; health-check probes are tagged `monitoring-probe` by the collector and dropped by the connector. D14's external app is seen here (its IP has no CMDB record -> `:UnregisteredEndpoint` by reverse-DNS name) and in sign-ins.

## 62. interface_engine
- All HL7 and DICOM routes are channels in one Mirth export (DICOM routes use DICOM Listener/Sender connectors; the CMDB still records the PACS node as `via`). Statistics are daily per connector with ChannelStatistics field names (`error`, not `errored`) and the SOURCE metadata column naming the sending application for the inbound edge; DICOM-MOD channels have no single sender (modalities), so no inbound edge — as before. Counts are now daily and `count` = sent (errors separate); the 3-day rule is unaffected.

## 63. backup_software and the D9/D10 boundary
- Veeam REST session JSON; one job per protected object with task fields flattened (objectName, repositoryName, sourceRepositoryName). Successful backups are followed by Backup Copy sessions to the DR appliance and the immutable vault; those copies also prove appliance -> DR / vault replication (the array API does not cover backup repositories).
- REALISM_SPEC: snapshot-only sessions never count as a backup. To keep the catalogue unchanged, D10 ("backups failing") applies to objects that have backup jobs (not snapshot-only) and no success in 7 days; an object with only snapshots is a protection gap (D9), not a failing backup. The oracle and the Cypher detector use the same rule.
- Backup sessions are capped at 10 hours (incremental-forever); the old model let the 900 TB VNA's full backup "end" in 2027.

## 64. storage_telemetry RPO evidence
- Lag above the RPO of the CIs stored on the source array counts only as a continuous run longer than `RPO_BREACH_MIN_HOURS` (1 h) of hourly samples; single-hour spikes are noise. RPO 0 (sync mirrors) is checked by the pod state, not lag. No breaches occur in the generated data; the protection check reports them when present.

## 65. Identity sources
- The declared IAM export (`data/identity/users.csv`) no longer carries employment status (`account_enabled=true` for all: the D12 terminated user's account was never disabled); status comes from the HR feed's Workday flags. Sign-ins create user -> application access edges (`login`), the database audit creates read/write/admin edges (admin with every timestamp). D12's reason `disabled_user` is now `inactive_in_hr`. App-level read/write events of the old access log are represented as additional sign-ins (an SSO session per app use).

## 66. itsm_history
- 90 days of incident and change history generated from the ownership evidence (incidents 2–8, changes 1–3 per CI per team, service-desk noise 1 incident), so the dominant team still always wins and ties do not occur. `assignment_group` on a closed record is the resolving / implementing group (OOB has `resolved_by` as a user only). Raw state codes (incident 7, change 3).

## 67. CMDB write-back enforcement
- See #54; enforced in `agent/approval.validate` and tested: D9–D12 approvals write `incident` / `sn_si_incident` tickets and never touch the CMDB export; D1/D2/D4–D8 patches pass the allowlist; `backup_offsite` or other non-CMDB fields are rejected.

## 68. Dependency view
- Change review draws a bounded subgraph (declared path to each declared service, real paths to Emergency care / high-safety services and to the EDs of H03, H17, H22, H44) as a conventional CMDB dependency view: fixed tiers (business service, service offering, interface, department, application, database, VM, server, storage), CMDB-direction arrows labelled with the relationship name, a barycentre ordering inside tiers, impact by red fill (toggle Reality / CMDB says), changed component outlined. Edge status: confirmed (solid grey), observed only (dashed red, label "Depends on (observed: <source>, not in CMDB)"), declared without evidence (grey dotted), failover (blue dashed); structural relationships (runs on, stored on, contains, department) are solid grey. Hover/tap shows layers, source, counts, days seen, first/last seen.

## 69. Sources tab
- `normalised/sources.json` (written by the connectors) lists, per source, the tool it stands in for, what it reveals, the raw format, raw record count, evidence rows, window, and one raw record next to the evidence row it produced; served at `GET /sources`.

## 70. Hidden edges use the 3-day rule
- Change review's "undocumented edges inside the real radius" now applies the same ≥ 3-day rule as D1, so a one-off ad-hoc flow (noise) no longer shows as a hidden dependency (CHG0031513: 0 hidden edges, low risk).

## 71. OpenRouter free models as the default live provider
- `LLM_PROVIDER=openrouter|anthropic|fake`; unset picks OpenRouter if `OPENROUTER_API_KEY` is set, then Anthropic, else no LLM. `fake` is the scripted `FakeLLM` (no network). Tests force no provider through an autouse fixture, whatever the shell or `.env` holds.
- OpenRouter is called with raw `httpx` (already a dependency of the `anthropic` SDK, now declared) against the OpenAI-compatible chat completions API rather than adding the `openai` package; OpenRouter's `models` fallback array is a non-OpenAI field anyway. Conversations stay in Anthropic block form inside the flows; `agent/llm.py` converts to and from OpenAI messages (`tool_calls`, `role: tool`) at the edge, so the flows and the Claude path are unchanged.
- The provider is built once at API startup (`get_llm` is cached) so model selection runs once and is logged.

## 72. Model selection
- Keep a model only if its ID ends in `:free`, both prices parse to 0 and `supported_parameters` contains `tools` (R19). Preference order from `NORTHWIND_MODELS`; if none qualify, the largest-context qualifying model plus the next two by context. The kept list is sent as `models` (OpenRouter fallbacks) with the first as `model`. Offline at startup: use the configured list as given.
- Default preference list has five models from three vendors: free endpoints were seen returning 429 from the upstream provider while others were fine. OpenRouter accepts at most 3 entries in `models` (live 400, R19), so the first 3 qualifying models are sent; the others are spares if a preferred model stops being free or tool-capable.

## 73. Budget guard
- A persistent per-UTC-day counter plus a one-minute window in `runtime/llm_budget.json`, taken before every HTTP attempt (failed attempts count, per the brief; the docs are silent, R19). Default daily limit 50 (`NORTHWIND_DAILY_REQUESTS`), 20 per minute. Requests past either are never sent.
- Per-job caps (`JOB_CAPS`): triage 1, change review 1, incident 6, chat 4 per message. Enforced by a `Capped` wrapper and by the tool loop's step limit; the last allowed call is sent with `tool_choice: "none"` and a one-line "answer now" instruction so a capped loop ends in an answer, not "(stopped)".
- At most one retry, only after a 429, waiting `Retry-After` (default 5 s, capped at 20 s). Every other error fails over to rule-based text immediately. A 200 whose body is an `error` object is treated as an error (R19).
- The UI header shows remaining free requests from `GET /api/v1/key` `free_model_daily_requests.remaining` (cached 30 s), taking the smaller of that and the local counter.

## 74. Saving requests
- Triage explains the top 10 in one batched call returning `{"explanations": [{"id", "text"}]}`; findings the reply misses keep their template text. The triage test now scripts one batched reply instead of ten.
- Every OpenRouter response is cached on disk (`runtime/llm_cache/<sha256>.json`) keyed by the model list plus a hash of system prompt, messages, tools and tool_choice. Tool loops are deterministic given the graph, so each step of a repeated incident or chat hits the cache.
- `python -m agent.prewarm` runs triage, the two demo change reviews, the demo incident and the demo chat question (the UI defaults) once. The Claude path is not cached: it is not on a free-tier budget and its SDK content blocks are not plain JSON.
- Ambiguity judgement (no UI or API entry point) keeps one call per case; it is not on the budget-critical path.

## 75. Graceful fallback
- Budget exhausted, cap reached, or all models failed raise `LLMUnavailable`. Triage and change review keep their template explanation and return `notice` = "AI explanation unavailable (free-tier limit), showing rule-based explanation"; incident and chat return that notice as the answer with whatever tool steps ran; ambiguity stores no decision with the notice as reason. Detection, radius, change review facts and risk are computed before and without the LLM.

## 76. Tool loop: step budget in the prompt, final call without tools
- Incident and chat tell the model its budget ("at most N model calls; tools are disabled on the last one"). The last allowed call is sent with no tools and a single user message holding the question plus every tool result gathered so far (each truncated to 8,000 characters, 40,000 in all), so it can only answer. This replaces `tool_choice: "none"`, which the live run showed a free model ignoring (it wrote a tool call as text), and avoids sending tool-call history without tool definitions.
- If the loop ends with no usable answer (provider unavailable, a tool call written as text, step limit), the answer is a rule-based summary built from the tool results (`agent/grounding.summarise_trace`): shared dependencies, changes on them, the impact path, who to page, matches and radius counts, with the free-tier notice.

## 77. Numbers and CI-name grounding
- Every LLM job gets its computed facts as structured JSON (triage: each finding's kind, key, subjects, severity, path, evidence; change review: the review summary with counts, names, risk and owners; incident and chat: the question and tool results), and the prompt says to use only those values.
- `agent/grounding.check` validates the prose: every number must occur in the facts (a value, a number inside a fact string, or a fact list's length), and every CI name must occur in the facts. A CI name is any hyphenated token that is a node name in the graph, or that looks like one (three or more parts with a digit), so an invented `lrc-db-02` is caught even though no such node exists. Dates, times, list markers and numbers inside names are ignored; Unicode hyphens are normalised.
- On any mismatch the prose is discarded, the rule-based text is shown with "AI explanation discarded (numbers or names not in the facts), showing rule-based explanation", the mismatches are logged (`northwind.grounding`) and returned (`mismatches`). Triage checks each finding's explanation separately.
- Limit: the check is value-level, not context-level; a number that exists in the facts under another label passes (e.g. quoting a declared 0 as a real count). Ambiguity judgements are not checked: they are a fixed decision label plus reasons, and are not shown as an explanation.
- Live pre-warm: the batched triage reply was cut off at `max_tokens` (the free reasoning model spent part of its budget thinking), so no explanation parsed. `parse_explanations` now keeps every complete `{"id", "text"}` item of a truncated reply, and `max_tokens` is raised from 4,000 to 8,000 (not part of the cache key).

## 78. UI rebuilt without a graph library
- Cytoscape is dropped. A canvas graph cannot be reached by keyboard; with sibling grouping the dependency view is small (8 boxes for the headline, at most 8 per tier), so hand-placed SVG is enough and every box and line is a focusable element with a tooltip. Layout: tiers top-down (business service to storage array), three barycentre sweeps, and a placeholder slot in every tier a long edge crosses so lines never run through boxes.
- Files: `ui/tokens.css` (every colour, size, spacing, radius, line, shadow and motion value), `ui/app.css` (token references only), `ui/app.js`, `ui/index.html`. The single literal outside tokens is the 960-px breakpoint (CSS cannot use a custom property in a media condition).

## 79. Grouping rule
- A box's signature is its class, its impact flags, and the neighbours it shares with other boxes of its class. Six or more boxes with the same signature (UI_SPEC: "more than 5") become one group with a count badge and the members' common name prefix ("51 interfaces (ORU-CRIT-H*)"). Matching on shared neighbours, not on exact parent and child, is what lets 51 parallel chains (offering -> interface -> department) collapse even though each interface has its own offering. A tier with more than 8 boxes after grouping folds the least important into "N more ...".
- Departments are not drawn: UI_SPEC's tier list has no department tier; they are counted in the comparison table. Medical devices, endpoints and external services sit in the Application tier, network devices in the Server tier, backup targets in the Storage tier.

## 80. API additions for the UI
- `GET /changes` returns the rule-based risk per change (cached until an approval or rejection) for the change list.
- The review graph is now the whole declared and real radius (impact-layer pairs mapped to their CMDB / evidence edges with one set query, 0.2 s for the headline, 0.4 s for a 785-CI radius) instead of a few sampled paths, so the UI can group siblings; `paths` keeps one real and one declared path for the phone list. `hidden_dependencies` carries each undocumented edge's source, connector, days seen and first/last seen.
- `GET /components/{name}/review`: what breaks if one CI fails, in the change-review shape and without the LLM (chat links open it).
- `GET /findings/{id}` adds `diff` rows (what, before, after); attribute "before" values come from the CMDB record.
- Incident and chat steps add `found` (one line per tool result) and the answer's `cis` (graph names it mentions).
- Bug fix: `radius` returned not-found for an existing CI with no impact edges, which crashed the review of three changes on network devices (`CHG0031526`, `CHG0031444`, `CHG0031411`). It now returns an empty radius.

## 81. Removed from the UI
- The hospital map, the critical-nodes list and the notifications feed are not in UI_SPEC's change-review layout and were removed from the screen. Their endpoints (`/hospitals`, `/critical`, `/notifications`) stay.

## 82. Copy and states
- Group label "51 interfaces (ORU-CRIT-H*)" instead of UI_SPEC's example "51 interfaces · ORU-CRIT-*": the avoid-list bans middle-dot strings, and the pattern is the members' real common prefix.
- Dates use the browser locale with the time zone shown (`Intl.DateTimeFormat`, `timeZoneName: short`).
- Errors: an unreachable API and a failing FalkorDB get different sentences, both saying to start the stack with `docker compose up`. Loading uses static skeleton blocks, never a page spinner or animation.
- Long AI explanations are clamped to 320 px with "Show the full explanation"; the fallback is labelled "Rule-based explanation" with the free-tier notice inline.

## 83. Test changes
- `test_ui_served` asserted Cytoscape and `#cy`, which no longer exist; it now checks the shell, the token file (light, dark, `data-theme`, reduced motion), the component CSS and the script. New `test_ui_review_additions` covers every API addition above, and the incident test checks `found` and `cis`.

## 84. Public deployment shape (Vercel Hobby + FalkorDB Cloud free)
- One Vercel Function: the FastAPI app via a root `index.py` (the local `vercel build` rejected `api/main.py` as a `functions` key, and a root entrypoint avoids the legacy `/api` file-function rules). The UI's `/static` mount is promoted to the CDN by Vercel. Region `bom1` (Mumbai = AWS ap-south-1, where the FalkorDB instance runs); Hobby allows one region, and it is accepted. `maxDuration` 60 s (Hobby maximum 300 s); the slowest live call is reset at 4.2 s.
- `DEPLOY_MODE=public` switches: FalkorDB connection from `FALKORDB_HOST/PORT/USERNAME/PASSWORD/TLS` (one rule in `graph/connection.py`, also used by the loader); the LLM is `CachedLLM` (bundled answers, read-only, labelled "Cached AI explanation"; misses fall back to the rule-based text with their own notice); approvals write to the graph; `/reset` and `/cron/reset` are enabled. Nothing is generated on startup in any mode (the API never generated data; the Docker seed job does that locally).
- TLS defaults to on in public mode, but the free tier has none (live TLS connection times out), so the deployment sets `FALKORDB_TLS=false`. Credentials therefore travel unencrypted between Vercel `bom1` and the instance inside AWS ap-south-1; acceptable for synthetic data, not for real data (Startup tier adds TLS).
- The local stack is pinned to its own FalkorDB in `docker-compose.yml` (`FALKORDB_PORT`, empty username/password, `FALKORDB_TLS=false`, `DEPLOY_MODE=local`), because `.env` now also holds the cloud settings and `env_file` would otherwise send the API to the cloud port.

## 85. Demo reset by undo log, not a pristine copy
- First design: keep `northwind_pristine` and `GRAPH.COPY` it back. Measured on the free instance: both graphs = 59 MB steady, triage peaked at 89 MB and the copy at 83 MB, and `used_memory_peak` reached 107 MB during the first load (over the 100 MB `maxmemory`). Not "well under".
- Chosen: every public-mode approval records the inverse of each operation it applies (`apply_op(..., undo)`: delete an added relationship, re-create removed relationships with their properties, restore an attribute and its `MANAGED_BY` group, delete a registered CI, or turn a registered endpoint back into an `:UnregisteredEndpoint` with its old properties) as an `undo` `:Record`. Reset applies the logs newest first, deletes all `:Record` and `:Finding` nodes, rebuilds the impact layer and re-runs triage (idempotent, so a repeated cron delivery is harmless). One graph: 32 MB steady, 48 MB peak while serving (triage), 47 MB peak during the one-time load, so 52 MB headroom. Nothing was trimmed from the demo subset.
- Reset is public (any visitor can press "Reset demo"); the cron path needs `CRON_SECRET`.

## 86. Cached AI answers for the public demo
- `deploy/bundle_cache.py` copies `runtime/llm_cache/` (39 answers, 188 KB, synthetic data only) to `deploy/llm_cache/` with `models.json` (the model list the keys were hashed with). On the cloud graph the cached answers hit for both demo change reviews and the demo incident; triage and the demo chat question miss (their prompts changed after the cache was filled, and the OpenRouter daily quota was used up), so those show rule-based text. Refreshing the bundle needs one prewarm with a free key.

## 87. Incident reasoning check
- After the incident job, the first change the answer names must be a change on the **most specific** shared dependency: the shared dependencies come from the `shared_dependencies` tool results, `changes_on` lists the changes on them, and among the changed targets only those impacting the fewest interfaces in the real graph count as likely causes. A plain "targets a shared dependency" test is not enough: for the headline symptom the shared set has 216 components, including `emr-app`, which every route depends on, so CHG0031513 (an `emr-app` upgrade) would pass; `dc1-host-07` (51 interfaces) is narrower than `emr-app` (115), so only CHG-HEADLINE qualifies.
- On a mismatch the prose is discarded, the rule-based summary is shown, led by the most specific changed dependency, and the mismatch is logged (`northwind.grounding`). The cached free-model incident answer, which blamed CHG0031513, was removed from the cache and the bundle so it is regenerated.

## 88. Benchmark design (BENCH_SPEC)
- **Scale generator.** The base is the seed-42 world built with the project's generator and loaded with the project's loader, then exported (labels, the properties the impact rules and the radius query read, every non-derived edge). Larger sizes replace the 6,778 endpoint-group CIs by their 48,161 individual endpoints and add regional divisions: a region copies every hospital, clinic and non-acute site with what belongs to it (site CIs, departments, the per-hospital interfaces and offerings, users, groups, discovery records) and shares the 1,398 central nodes (data centres, central applications, support groups, changes). From 500k nodes each extra region also gets a small regional data centre (4 servers, 8 VMs, an array, a backup target). The 50k size is the base world as it is (49,063 nodes). Sizes are whole regions, so actual counts are near, not exactly, the nominal sizes (pilot: 3 regions = 268,542 nodes, 883,794 edges). The headline chain is central, so `dc1-host-07` reaches every region's ORU-CRIT routes.
- **Lean FalkorDB loader.** The project's loader reads connector output (388 MB of raw-derived files for the 49k world), which does not scale to millions of nodes; the benchmark loader creates the same labels, relationship types and indexes (plus an integer `bid` per node for edge matching) in UNWIND batches, then runs the project's own impact rules (`graph/impact.build`). Node properties are limited to those the rules and the radius query use.
- **Same edges.** The impact edges FalkorDB materialised are exported and copied into PostgreSQL next to all generated edges, so both systems traverse identical edge sets.
- **Same question.** FalkorDB runs the project's `RADIUS` query unchanged (one `algo.bfs` call plus terminal, department and hospital lookups); PostgreSQL runs an equivalent recursive CTE (`UNION`, depth counter, index on `edge(src, type)`) with the same lookups, as a prepared statement. The correctness gate compares all four result sets (impacted, consumers, departments, hospitals), not just the impacted set. `UNION` de-duplicates (node, depth) pairs, so a node reached at two depths is expanded twice; a recursive CTE cannot keep a visited set across rounds.
- **Containers.** Both systems run in their own containers with the same limits (4 GB memory, no swap, 4 CPUs) inside Docker Desktop's 9.7 GB VM; PostgreSQL 18.6 settings are in `bench/config.py` and in every result row. FalkorDB snapshots are disabled during the run (no fork stalls) and taken only to measure disk size.
- **Concurrency clients** run in up to 10 Python processes with threads inside each, one connection per client, random start points (seed 42) from the components that have impact edges, at depth 10.

## 89. Pilot findings and the size plan
- **Radius query fix (project-wide).** The first pilot showed the project's `RADIUS` query spending ~2.9 s of 2.97 s in two aggregations that grouped by the whole impacted list (`WITH f, hit, collect(...)`), i.e. hashing an 8,189-element list per row. The BFS itself took 1.8 ms. `RADIUS` now does one final aggregation without grouping keys (same four result sets; all project tests pass; `radius()` checks existence when the impacted list is empty, since an aggregate always returns a row). Big-radius time fell from 2,966 ms to 132 ms. The first pilot's results are kept in `bench/results/archive/250000-project-radius-v1/`.
- **Two query kinds.** "radius" is the full question (traversal plus consumers, departments, hospitals), "reach" is the traversal alone (names reached). Both pass the correctness gate. FalkorDB's server-side time for "radius" is recorded too (`server_ms`), because the Python client spends a large share decoding the nested result (pilot: 58 ms server vs 138 ms end to end for the 8,189-node radius); all headline timings are end to end from the same Python client for both systems.
- **Memory measurements (4 GB containers).** FalkorDB: 370 MB peak at 268,542 nodes / 883,794 edges (pilot) and 927 MB at 981,076 nodes / 3,230,746 edges (a one-off calibration load); linear fit about 160 MB + 0.78 KB per node, so 5M nodes would peak at about 4.0 GB, over the 4 GB limit. PostgreSQL: 186 MB peak during load, 164 MB on disk at the pilot size (about 3 GB on disk at 5M, memory capped by its settings). Generator: 250 MB peak RSS at every size (only the base world is held in memory). Laptop: Apple M4, 10 cores, 32 GB RAM, 147 GB free disk; Docker Desktop VM 9.7 GB, which both 4 GB containers and the project stack share, so the limits cannot simply be raised for both.
- **Chosen sizes: 50k, 500k and 3.5M nodes** (plus the 250k pilot). 3.5M is the largest whole-region size whose predicted FalkorDB peak (about 2.9 GB) stays under 75% of the 4 GB limit (25% margin). Not run: 5M.
- **Background run** under `caffeinate -is` (no idle or system sleep while it runs), detached with `nohup`.
