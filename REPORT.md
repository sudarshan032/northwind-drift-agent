# REPORT: Northwind Health data phase (autonomous run, 2026-10-15)

## Status: done

Every RUN_PROMPT milestone is complete and committed on branch `northwind-data-phase`; `master` is untouched.

- The generator, loader, tests, `docker-compose.yml`, research check and two independent reviews are finished.
- Both clean-room verification runs passed, and their data hashes are identical.
- Nothing is incomplete.

There are deliberate deviations from DATA_SPEC. Each one is logged in `DECISIONS.md`, and the main ones are listed under "For the owner" below.

Commits:
```
74ad6a1 Progress log update
d141c85 Review #2 fixes: local DICOM routes, weekly tier-3 backups, group count, stronger determinism/D3/discovery/residency tests
f0bd388 Review #1 fixes: exact endpoint totals, stricter inventory/protection tests, DICOM source/via, CAH vitals, SSO links, coverage gaps
7ccbde6 M6 research: realism fixes (imaging ranges, bedside fleets, HL7 volumes/ADT mix), RESEARCH_NOTES, decisions log
470005a M5 loader: idempotent batched FalkorDB load (UNWIND/MERGE), discovery identification, aliases; gzip demo subset committed
2078c5f M3+M4 views, oracle and drift (M3 and M4 in one commit, DECISIONS #27)
11af174 M2 truth model: deterministic Northwind world (SEED=42) + physical plausibility tests
165ad7c M1 scaffold: uv/Python 3.11 env, FalkorDB compose, data README
```

## How to run

```bash
cd graph_hacks
git checkout northwind-data-phase
docker compose up -d --wait                       # falkordb/falkordb:v4.22.0, ports 6379 + 3000 (browser UI), volume falkordb_data
uv venv --python 3.11 && uv sync --frozen         # Python 3.11.14, pinned deps (pyproject.toml + uv.lock)
uv run python -m ingest.generate --seed 42 --out data/
uv run python -m pytest -v                        # 28 tests; the loader tests need FalkorDB
uv run python -m ingest.load --data data/ --graph northwind --fresh
uv run python -m tests.sanity_queries --graph northwind --data data/
```

Other options:
- `--no-drift` generates views that all agree with each other.
- `--subset demo --gzip` produces the committed `data_demo/` subset: WA and OR sites, both data centres, both cloud regions and GCC-HYD, 9.3 MB, gzip-compressed and byte-stable.
- `--fresh` deletes the graph before loading. MERGE alone never removes nodes left over from an older data set.

`data/` is about 144 MB, so it is git-ignored apart from `data/README.md`.

## Test output (final clean-room run 2)

```
tests/test_loader.py::test_load_is_idempotent PASSED
tests/test_loader.py::test_graph_matches_files PASSED
tests/test_loader.py::test_unidentified_discovered_equals_d6 PASSED
tests/test_loader.py::test_d13_aliases_resolved PASSED
tests/test_loader.py::test_headline_path_in_graph PASSED
tests/test_views.py::test_determinism PASSED
tests/test_views.py::test_determinism_across_processes PASSED
tests/test_views.py::test_every_discoverable_ci_discovered_without_drift PASSED
tests/test_views.py::test_views_agree_without_drift PASSED
tests/test_views.py::test_protection_and_backups_without_drift PASSED
tests/test_views.py::test_drift_catalogue_exact PASSED
tests/test_views.py::test_no_phi_in_central_india_except_d11 PASSED
tests/test_views.py::test_headline_scenario PASSED
tests/test_views.py::test_size_report PASSED
tests/test_views.py::test_demo_subset_reproducible_and_exact PASSED
tests/test_world.py::test_footprint PASSED
tests/test_world.py::test_every_hospital_has_emergency PASSED
tests/test_world.py::test_department_counts_by_tier PASSED
tests/test_world.py::test_devices_only_in_existing_departments PASSED
tests/test_world.py::test_no_mri_or_ct_outside_hospitals PASSED
tests/test_world.py::test_medical_devices_on_medical_segment PASSED
tests/test_world.py::test_inventory_ranges PASSED
tests/test_world.py::test_vms_run_on_hosts_at_same_site PASSED
tests/test_world.py::test_app_chains_reach_storage PASSED
tests/test_world.py::test_no_phi_in_central_india PASSED
tests/test_world.py::test_ids_unique_and_wellformed PASSED
tests/test_world.py::test_no_personal_data PASSED
tests/test_world.py::test_headline_chain_in_truth PASSED
============================= 28 passed in 30.64s ==============================
```

How the tests map to DATA_SPEC section 9:

| Spec test | Tests |
|---|---|
| 9.1 Determinism | `test_determinism`, `test_determinism_across_processes`; also identical hashes across the two clean-room runs |
| 9.2 Views agree without drift | `test_views_agree_without_drift`, `test_protection_and_backups_without_drift`, `test_every_discoverable_ci_discovered_without_drift` |
| 9.3 Drift catalogue | `test_drift_catalogue_exact` |
| 9.4 Physical plausibility | `test_world.py`, `test_no_phi_in_central_india_except_d11` |
| 9.5 Headline scenario | `test_headline_scenario`, `test_headline_path_in_graph` |
| 9.6 Size | `test_size_report` |
| 9.7 Loader idempotency | `test_load_is_idempotent` |

For 9.2 and 9.3, the reference checker `tests/oracle.py` (the RUN_PROMPT 4.1 rules) finds:
- 0 findings with drift off;
- with drift on, exactly the `ground_truth.json` keys: 108 findings, no D3 hits, and the 6 D13 aliases resolved.

## Size report (full data, SEED=42)

**Totals**

| What | Value |
|---|---|
| Graph nodes | 49,056 |
| Graph edges | 139,693 |
| `data/` size | 144 MB (budget 500 MB) |
| Generation time | 3.8 s (budget 10 min) |
| Load time | 2.8 s in the loader (3.3 s wall clock) |

**Site types:** 10 large hospitals, 25 community hospitals, 16 critical access hospitals, 1,000 clinics, 800 non-acute sites, 2 data centres, 2 cloud regions, 1 GCC, and 1 "EXTERNAL" pseudo-location for SaaS.

**CIs per site type (21,614 CIs):**

| Site type | CIs |
|---|---|
| Clinic | 11,074 |
| Non-acute | 4,063 |
| Large hospital | 2,655 |
| Community hospital | 2,510 |
| Critical access hospital | 587 |
| Data centre | 370 |
| GCC | 325 |
| Cloud region | 26 |
| External | 4 |

**CIs per class:**

| Class | Count | Class | Count |
|---|---|---|---|
| u_cmdb_ci_endpoint_group | 6,778 | cmdb_ci_vmware_instance | 235 |
| cmdb_ci_ip_switch | 3,850 | cmdb_ci_med_patient_monitoring | 223 |
| cmdb_ci_ip_router | 1,892 | cmdb_ci_esx_server | 92 |
| cmdb_ci_wap_network | 1,852 | cmdb_ci_win_server | 90 |
| cmdb_ci_ip_firewall | 1,525 | cmdb_ci_med_therapeutic_device | 89 |
| cmdb_ci_computer | 1,412 | cmdb_ci_db_mssql_instance | 73 |
| cmdb_ci_med_diagnostic_imaging | 1,374 | cmdb_ci_service_business | 61 |
| cmdb_ci_ip_network | 639 | cmdb_ci_storage_server | 37 |
| cmdb_ci_med_lab_equipment | 589 | cmdb_ci_vm_instance | 10 |
| cmdb_ci_appl | 269 | others (DB, cloud, zones, backup) | 24 |
| u_cmdb_ci_hl7_interface | 253 | cmdb_ci_printer | 252 |

**Edges per type:**

| Edge | Count | Edge | Count |
|---|---|---|---|
| LOCATED_AT | 21,614 | DEPENDS_ON | 3,567 |
| MANAGED_BY | 21,614 | OBSERVED_NETFLOW | 2,626 |
| CONNECTS_THROUGH | 18,796 | OBSERVED_CALLS | 1,030 |
| OBSERVED_ACCESS | 18,189 | RUNS_ON | 640 |
| OBSERVED_OWNER | 14,452 | STORES_ON | 178 |
| IDENTIFIES | 11,564 | BACKED_UP_BY / OBSERVED_BACKUP_TO | 136 / 136 |
| IN_DEPARTMENT | 10,768 | GRANTS | 117 |
| HAS_ROLE | 6,118 | TARGETS | 22 |
| MEMBER_OF | 4,261 | REPLICATES_TO / OBSERVED_REPLICATES_TO | 12 / 12 |
| CONTAINS | 3,841 | | |

**Observed records:**

| File | Records |
|---|---|
| flows_hourly | 302,792 |
| flows_daily | 1,274 |
| netflow_hourly | 183,397 |
| netflow_daily | 20,062 |
| backup_jobs | 1,880 |
| replication_status | 8,472 |
| access_log | 95,929 |
| traces_sample (OTLP) | 45 |

**Are these numbers realistic?**
- The site footprint, the equipment at each site, and the user counts at demo sites are realistic: H03 has 2,696 users, H17 1,115, H44 150 and GCC-HYD 300.
- The graph is smaller than the DATA_SPEC 9.6 guidance (80–150k nodes, 300–700k edges) and just under DATA_SPEC's own 50k fail line. It is within the RUN_PROMPT 4.5 bound of 30k–300k nodes, and I did not pad it, as RUN_PROMPT says. The size comes from the inventory table: outside the demo sites, endpoints, access points and bedside fleets are single group nodes with a `count`.

## Findings by drift type (updated for AGENT_SPEC Phase 0)

Full data, drift on. Every count below is both the `ground_truth.json` expectation and what the oracle finds (exact match; VERIFY.md has the current run).

| Type | Entries | Finding keys | What |
|---|---|---|---|
| D1 | 8 | 58 | undocumented dependencies (headline = 51 ORU-CRIT → lrc-db-01 keys, plus 7 single ones) |
| D2 | 6 | 6 | stale CMDB relationships |
| D3 | 4 | 0 (must stay silent) | failover relationships, not stale |
| D4 | 3 | 3 | retired CIs still linked |
| D5 | 6 | 6 | owner drift |
| D6 | 10 | 10 | shadow IT (discovered, not in CMDB) |
| D7 | 5 | 5 | ghost CIs |
| D8 | 8 | 8 | attribute drift |
| D9 | 4 | 4 | protection-path drift |
| D10 | 3 | 3 | backups failing > 7 days |
| D11 | 1 | 1 | PHI replicated into cloud-central-india |
| D12 | 4 | 4 | access drift |
| D13 | 5 | 6 aliases resolved | naming mismatches (not findings) |
| D14 | 1 | 1 | unregistered SMART on FHIR app (new in AGENT_SPEC 0.1f) |

Total: 109 findings, 0 unexpected.

## Sanity queries (clean-room run 2; identical in run 1)

**1. Node counts per label**
```
Application 269, BackupTarget 3, BusinessService 61, Change 22, Component 21614, Database 79,
Department 3526, Discovered 11574, Endpoint 1664, EndpointGroup 6778, ExternalService 4, Group 6118,
Host 186, Interface 253, MedicalDevice 2275, NetworkDevice 9758, Role 20, Site 1857, StorageArray 39,
SupportGroup 64, User 4261, VM 245
```

**2. Observed path from an `ORU-CRIT-*` route to `lrc-db-01`** (and on to the host that CHG-HEADLINE patches)
```
MATCH p = (i:Interface {name:'ORU-CRIT-H03'})-[c:OBSERVED_CALLS]->(d:Component {name:'lrc-db-01'})
          -[:RUNS_ON]->(v:VM)-[:RUNS_ON]->(h:Host)
=> ["ORU-CRIT-H03", "lrc-db-01", "dc1-vm-lrc01", "dc1-host-07"]
   ["OBSERVED_CALLS", "RUNS_ON", "RUNS_ON"]   count=3767  days_seen=14  last_seen=2026-10-14T23:00:00Z
```
All 51 `ORU-CRIT-*` routes have this edge. In the CMDB, only `lis-reporting` depends on `lrc-db-01`.

**3. `:Discovered` nodes with no `IDENTIFIES` edge:** 10, which equals the D6 count of 10.

## Decisions (top 10 of 34, see DECISIONS.md)

1. **One truth, then views, then a drift plan** (#4). `world.py` builds a single world, and every view is derived from it plus a drift plan. With the empty plan, the views agree by construction. Randomness uses standard-library generators seeded by string per subsystem, so output is byte-stable across processes.
2. **Machine-checkable ground truth** (#5). `ground_truth.json` gains a `finding_keys` field, so test 9.3 can assert "exactly these findings". D3 entries list the findings that must *not* appear.
3. **Interfaces appear as route-level engine statistics** (#6). The engine records `source → route → target`, and `ORU-CRIT-* → lrc-db-01` for the staging step. This makes the headline a one-hop observed fact.
4. **Dependencies match in either direction** (#7). The undocumented and stale rules accept a declared dependency in either direction, because interface legs push data opposite to the direction of the dependency.
5. **D9 is "snapshots mistaken for backups"** (#12). Successful jobs that only take snapshots on the same site's array keep D9 and D10 from overlapping. D10 means at least 8 days without a success (#13).
6. **App chains at sites without an array end at a physical host** (#10). The spec gives critical access hospitals 0 arrays, so their apps run on servers with their own disks; this is a clarification of 9.4. Location, department and support group are reference columns on `cmdb_ci`, as in ServiceNow (#9).
7. **Discovery scope** (#11). Group CIs are reconciled through aggregate subnet coverage. Medical segments are scanned passively. 20 clinic user subnets are not scanned at all, which makes the ghost rule's coverage condition meaningful.
8. **Exactly 4 failover relationships in the truth** (#17). These are the D3 cases. Cloud PHI databases are backed up through the DC-PRI appliance, so every protection path reaches both DC-DR and the cloud vault (#20).
9. **Hourly vs daily, and the git split** (#23, #34). Hourly records are kept only for demo and central edges, and local-only DICOM routes stay at their hospital. `data/` is git-ignored; `data_demo/` is a gzip copy with mtime=0 and a test confirms it is reproducible.
10. **Two fresh-context reviews** (#33, #34). Review 1 had 20 findings and review 2 had no high-severity findings; every valid item was fixed. The fixes include two real bugs (department rounding broke the endpoint totals, and ADT error counts were copied into each split) and several tests that had been weaker than the spec. The remaining items are justified in the log.

## Research (see RESEARCH_NOTES.md, with sources)

Changed because of research:

| Area | Change | Sources |
|---|---|---|
| Imaging counts | Raised to modern ranges. Large hospital: CT 3–5, MRI 2–4, X-ray 8–15, ultrasound 10–20. Community hospital: MRI 1, X-ray 3–6, ultrasound 4–8. Critical access: ultrasound 1. | IMV/AuntMinnie, BMC study of German hospitals |
| Bedside device fleets | Added a pump-fleet and a monitor-fleet group CI per hospital, with a `count`. A 600-bed hospital previously had about 0.1 networked device per bed; sources put it at 10–15 per bed. | Extreme Networks, BMC |
| Critical access hospital beds | At most 25, and every hospital has an ED. | CMS CAH conditions of participation, RHIhub |
| HL7 | Volumes calibrated to about 1,000 messages per bed per day. ADT traffic is split across trigger events, with A08 the most common. Lab orders use OML^O21; radiology uses ORM^O01, which is deprecated but universal. | HL7 tables 0354/0003, FHIR community volumes |
| Backups | RPO/RTO tiers set (tier 0 is sync with RTO 1 h), the 3-2-1-1-0 layout used, and tier 3 backed up weekly. | HHS 405(d), Veeam, InterSystems mirroring |
| ServiceNow class names | Real names used (`cmdb_ci_storage_server`, `cmdb_ci_med_*`), with `u_` prefixes on custom classes and lowercase-hex sys_ids. | ServiceNow docs |

Checked with no change needed: DICOM flows (worklist, C-STORE, port 104, study sizes), department vocabulary, and LTE at small sites.

## Known limitations and owner decisions

**For the owner to decide later:**

1. **Relationship type strings.** Resolved in AGENT_SPEC Phase 0: storage and connects now use the ServiceNow standard `Provides storage for::Stored on` and `Connects to::Connected by`; `Backed up by` and `Replicates to` stay documented custom types.
2. **AD group names.** These follow the spec (`grp-H17-ED-nurse`). Real AGDLP naming would be `GG_<site>_<dept>_<role>` nested into `DL_<resource>_<RO|RW>`.
3. **Graph size.** About 49k nodes and 140k edges, below the DATA_SPEC guidance. To grow it realistically, expand endpoints into individual nodes at more sites rather than padding.
4. **Located at / Managed by.** These are columns, not `cmdb_rel_ci` rows. If the agent should read them from the relationship table, emit the rows and exclude them from the D4 "retired still linked" rule.
5. **Demo subset.** Resolved in AGENT_SPEC Phase 0: the subset is now all 51 hospitals plus DCs, clouds and GCC-HYD, with the full catalogue and headline.
6. **Imaging ranges.** Resolved: DATA_SPEC section 2 now carries the research ranges.

**Limitations:**
- Workstations and printers per bed at the largest hospitals are below the research estimates; the spec table ranges were kept.
- There is no datastore layer: hosts link to arrays directly, because there is no Datastore label.
- There are no 15-minute log backups.
- RDS and RAS pharmacy messages are not modelled.
- The OTLP trace sample (45 traces, 2 hours, demo sites) is illustrative. The loader does not ingest it, because flows already carry the same edges.
- FalkorDB keeps its default query timeout of 1 s. The loader's batches finish well within it, but heavy agent traversals later may need `TIMEOUT_DEFAULT` raised.
- The checkpoint log `drift-agent_checkpoint_20261012.log` is committed in the repo along with the run inputs.

---

# Phase 2: AGENT_SPEC run (verification, detection, agent, API, UI, packaging)

## Status

| Phase | Status | Where |
|---|---|---|
| 0 Verify the data | **Done.** Follow-ups (a)–(f) all done; `VERIFY.md` passes every check after a clean regenerate and fresh load | `VERIFY.md`, DECISIONS #35–#41 |
| 1 Detection in Cypher | **Done.** 12 detectors exactly match `ground_truth.json` on the full data and the demo subset; impact radius, change review, protection, access, paging and centrality are all in place | `graph/queries.py`, `graph/impact.py`, DECISIONS #42–#45 |
| 2 Agent (LangGraph) | **Done, tested with the fake LLM only.** All 5 jobs and the approval flow work. No `ANTHROPIC_API_KEY` exists on this machine, so the live-LLM manual check is still open (see below) | `agent/`, DECISIONS #46–#50 |
| 3 API | **Done.** All endpoints from the spec, plus `/changes` and `/hospitals` | `api/main.py`, DECISIONS #51 |
| 4 UI | **Done.** Change review screen, hospital map (the stretch goal), findings with approve/reject, incident box, chat, notifications, critical nodes, light and dark modes | `ui/index.html`, `docs/*.png`, DECISIONS #52 |
| 5 Packaging and README | **Done.** `docker compose up --build` runs FalkorDB, then a one-shot seed (generate + load), then the API and UI on :8000. It works without a key. The README has every judged section | `Dockerfile`, `docker-compose.yml`, `README.md` |

Nothing was cut.

## Clean-room verification (run 4)

The script tears down Docker volumes, deletes `data/`, `.venv` and caches, then rebuilds from the committed files with `uv sync --frozen`. It then:
- generates the data and runs the test suite;
- loads the graph twice and confirms the loads are idempotent (49,063 nodes and 150,400 edges both times);
- regenerates `VERIFY.md`, which passes every check;
- confirms the data file hashes are identical to run 3;
- builds and starts the full stack from scratch, where the review of `CHG-HEADLINE` returns `high` with 51 routes and 51 hospitals.

## Test output (clean-room run 4)

```
tests/test_agent.py::test_triage_findings_and_ranking PASSED
tests/test_agent.py::test_triage_without_llm_uses_templates PASSED
tests/test_agent.py::test_change_review_flow PASSED
tests/test_agent.py::test_incident_headline PASSED
tests/test_agent.py::test_ambiguity_judgement PASSED
tests/test_agent.py::test_chat_cites_and_says_nothing_found PASSED
tests/test_agent.py::test_no_key_degrades PASSED
tests/test_agent.py::test_tools_cannot_write_cmdb PASSED
tests/test_agent.py::test_approve_and_reject PASSED
tests/test_api.py::test_health_without_key PASSED
tests/test_api.py::test_change_review_endpoint PASSED
tests/test_api.py::test_scan_findings_approve_reject PASSED
tests/test_api.py::test_radius_subgraph_critical_hospitals PASSED
tests/test_api.py::test_agent_endpoints_with_fake_llm PASSED
tests/test_api.py::test_ui_served PASSED
tests/test_loader.py::test_load_is_idempotent PASSED
tests/test_loader.py::test_graph_matches_files PASSED
tests/test_loader.py::test_unidentified_discovered_equals_d6 PASSED
tests/test_loader.py::test_d13_aliases_resolved PASSED
tests/test_loader.py::test_headline_path_in_graph PASSED
tests/test_loader.py::test_headline_radius PASSED
tests/test_loader.py::test_d14_path PASSED
tests/test_queries.py::test_detectors_match_ground_truth_full PASSED
tests/test_queries.py::test_detectors_match_ground_truth_demo PASSED
tests/test_queries.py::test_headline_radius_and_change_review PASSED
tests/test_queries.py::test_change_review_low_and_unknown PASSED
tests/test_queries.py::test_critical_nodes_stable PASSED
tests/test_queries.py::test_protection_access_paging PASSED
tests/test_queries.py::test_tool_queries_within_timeout
tests/test_queries.py::test_tool_queries_within_timeout
tests/test_queries.py::test_tool_queries_within_timeout
tests/test_queries.py::test_tool_queries_within_timeout
tests/test_queries.py::test_tool_queries_within_timeout
tests/test_queries.py::test_tool_queries_within_timeout
tests/test_queries.py::test_tool_queries_within_timeout
tests/test_queries.py::test_tool_queries_within_timeout
tests/test_queries.py::test_tool_queries_within_timeout
tests/test_queries.py::test_tool_queries_within_timeout
tests/test_queries.py::test_tool_queries_within_timeout
tests/test_queries.py::test_tool_queries_within_timeout
tests/test_queries.py::test_timeout_returns_clear_result PASSED
tests/test_views.py::test_determinism PASSED
tests/test_views.py::test_determinism_across_processes PASSED
tests/test_views.py::test_every_discoverable_ci_discovered_without_drift PASSED
tests/test_views.py::test_views_agree_without_drift PASSED
tests/test_views.py::test_protection_and_backups_without_drift PASSED
tests/test_views.py::test_drift_catalogue_exact PASSED
tests/test_views.py::test_no_phi_in_central_india_except_d11 PASSED
tests/test_views.py::test_headline_scenario PASSED
tests/test_views.py::test_size_report PASSED
tests/test_views.py::test_demo_subset_reproducible_and_exact PASSED
tests/test_world.py::test_footprint PASSED
tests/test_world.py::test_every_hospital_has_emergency PASSED
tests/test_world.py::test_department_counts_by_tier PASSED
tests/test_world.py::test_devices_only_in_existing_departments PASSED
tests/test_world.py::test_no_mri_or_ct_outside_hospitals PASSED
tests/test_world.py::test_medical_devices_on_medical_segment PASSED
tests/test_world.py::test_inventory_ranges PASSED
tests/test_world.py::test_vms_run_on_hosts_at_same_site PASSED
tests/test_world.py::test_app_chains_reach_storage PASSED
tests/test_world.py::test_no_phi_in_central_india PASSED
tests/test_world.py::test_ids_unique_and_wellformed PASSED
tests/test_world.py::test_no_personal_data PASSED
tests/test_world.py::test_headline_chain_in_truth PASSED
64 passed in 66.33s (0:01:06) 
```

## Heaviest queries (full graph, median of 3 runs)

| Query | ms |
|---|---|
| detect D12 (access rule, every admin event against change windows) | 552 |
| radius real arr-pri-a-01 (a shared storage array) | 381 |
| radius real sso-app | 380 |
| detect D2 | 139 |
| detect D6 | 132 |
| critical_nodes (betweenness, samplingSize 5000) | 120 |
| detect D1 | 99 |
| detect D7 | 86 |

Every agent tool query has a 5 s budget, and a test asserts it on the full data. FalkorDB's default query timeout is 10 s.

## Headline numbers

- **`dc1-host-07`, declared radius:** 4 components and 1 business service (`svc-lab-results`); 0 routes, 0 departments, 0 hospitals.
- **`dc1-host-07`, real radius:** 107 components: 51 `ORU-CRIT-*` routes, 51 Emergency departments, 51 hospitals and 53 business services, including `svc-emergency-care`.
- **`CHG-HEADLINE` review:** `high`, with 51 hidden edges. Warn `HYD-Integration-Support` (the observed owner of `lrc-db-01`; the CMDB says `NW-Lab-Systems`), plus `NW-Datacenter-Infra` and `NW-EMR-Apps`.
- **Triage:** 59 findings. The top finding, rated critical, is "51 components call lrc-db-01 but the CMDB records none of these dependencies"; its patch adds the 51 relationships.
- **After approving that finding:** the declared radius of `dc1-host-07` reaches Emergency care, and the review of `CHG-HEADLINE` drops to `low`. There is no hidden risk left, so the change can go ahead with the right people warned.

## Screenshots

- `docs/change-review-light.png`: change review, light mode (also embedded in the README)
- `docs/change-review-dark.png`: change review, dark mode
- `docs/findings.png`: ranked findings panel

## Decisions summary (AGENT_SPEC run, DECISIONS #35–#52)

1. **ServiceNow relationship types.** Storage uses `Provides storage for::Stored on` (the array is the parent) and network uses `Connects to::Connected by`. The graph edge names are unchanged, as the spec allows.
2. **Demo subset.** It now holds all 51 hospitals with the full catalogue and headline. Clinic-based D6/D7 cases move to community hospitals inside the subset.
3. **Department and Emergency care links.** Routes carry the department they serve. A new service `svc-emergency-care` contains each hospital's ED critical-results service. These links are declared by the service owner, so they are not drift.
4. **FHIR layer and D14.** `emr-fhir-api` serves the portal, Bulk Data export and RTLS. D14 is the third-party `cardiorisk.example.com` app, launched by H17 cardiology. Endpoints with no CMDB record are loaded as `:UnregisteredEndpoint`.
5. **Impact semantics.** These are one rule table, materialised in Cypher as `IMPACTS_*` (propagating) and `AFFECTS_*` (terminal) edges. The either-direction rule from DECISIONS #7 lives in exactly one rule. Syslog shipping and health checks carry no impact.
6. **Exact Cypher detectors.** These need loader enrichments: per-edge `days` lists, timestamps on admin access events, and each CI's `scanned_subnet`. FalkorDB quirks are worked around and documented.
7. **Change risk and centrality.** Risk is `high` when a high-safety business service appears only in the real radius. Betweenness covers all Components with samplingSize 5000, which is exact and stable.
8. **Agent.** It uses the official `anthropic` SDK (`claude-sonnet-5-5`, server-side refusal fallback) behind a fake-LLM interface; LangGraph is used for orchestration only. Triage groups related rows, scores severity, and has the LLM rewrite only the top 10 explanations, from the finding facts alone.
9. **Approval.** Each finding gets a JSON-Patch-style CMDB change. Approval applies it to the CSVs and to the declared edges, rebuilds the impact layer, re-runs the detector, and appends to `audit.jsonl`. For D10, D11 and D12 the fix is operational, so they carry no CMDB patch.
10. **Packaging.** One image serves both the seed job and the API. The UI is a single static page (Cytoscape.js 3.30.2 from cdnjs, plus a hand-drawn SVG map) with no build step.

## For the owner to decide

1. **Run the live-LLM check.** Put `ANTHROPIC_API_KEY` in `.env`, run `docker compose up`, then use the UI's Incident tab with "ED critical results delayed at H03, H17 and H22". Confirm that the real model follows the tool method and names `lrc-db-01` and `CHG-HEADLINE`. Also try the ambiguity judgements and chat. None of this has been run against the real model yet.
2. **Model choice.** The default is `claude-sonnet-5-5`, because the spec asked for a current Sonnet. `claude-opus-5-5` is stronger and costs about twice as much; switch with `NORTHWIND_MODEL`.
3. **Severity weights.** The weights in `agent/findings.py` (`IMPACT_TYPES`, `EXPOSURE`) are a judgement call. Today three of the six ownership (D5) findings rank critical, because the databases they are about have a large real radius; the other three rank low.
4. **Seeding resets state.** Each `docker compose up` regenerates the data and reloads it fresh, which discards approvals and findings. That suits a repeatable demo. Remove `--fresh` and the generate step from the seed if state should persist.
5. **Grouping rule.** D1 rows are grouped when three or more callers share one callee. A different grouping, or none, would change how the findings list reads.
6. **Map.** It is a schematic plot from city coordinates, not a tile map. Leaflet with an OpenStreetMap layer would look better but adds a dependency and an external tile service.

## Realism (REALISM_SPEC run, branch `realism`)

Every input is now the export of a real tool in that tool's documented format. The generator writes raw files to
`data/raw/<source>/`; one connector per source (`ingest/sources/`) parses only those files, applies ServiceNow-IRE-style
identification and writes normalised evidence to `data/normalised/`; the loader reads only normalised evidence. The
truth model (`world.py`) and the drift catalogue D1–D14 are unchanged, the oracle is still exact (109 ground-truth
findings, 6 aliases, 0 discovered without drift) and the headline is unchanged.

### Formats per source

| Source | Stands in for | Raw format | Researched in |
|---|---|---|---|
| `cmdb_export` | ServiceNow CMDB | table CSV exports with raw choice codes and sys_id references (`cmdb_rel_ci` parent/child/type; OOB `Depends on::Used by` sys_id `1a9cb166f1571100a92eb60da2bce5c5`); custom HL7 class `u_cmdb_ci_hl7_interface`; `write(read())` byte-identical | R9 |
| `discovery_scanner` | ServiceNow Discovery | IRE payload JSON (`items[className, values, lookup, sys_object_source_info]`, `relations`), ~600 items per batch, listening services as `tcp_port` items with `Runs on::Runs` | R10 |
| `apm_otel` | OTel Collector `servicegraph` -> Prometheus | Prometheus text exposition scrapes (`traces_service_graph_request_total`, `_failed_total`, `_request_server_seconds` histogram, buckets 10 ms–5 s); OTLP/JSON span sample | R11 |
| `netflow` | IPFIX / NetFlow v9 via nfdump | nfdump 1.7 `-o csv` aggregated records plus srcDns/dstDns/tag enrichment columns | R12 |
| `interface_engine` | Mirth Connect 4.5 | channel export XML (TCP Listener/Sender, Database Writer with JDBC URL, DICOM Listener/Sender, Mirth property classes) + daily ChannelStatistics per connector with SOURCE metadata | R13 |
| `backup_software` | Veeam B&R REST v1 | `/api/v1/sessions` JSON (BackupJob, BackupCopyJob, `isStorageSnapshot`, Success/Warning/Failed) | R14 |
| `storage_telemetry` | Pure FlashArray REST 2.x | `pod-replica-links` (async lag, recovery_point) and `pods` (sync) samples, hourly | R15 |
| `identity_sso` | Microsoft Entra ID | Graph `signIn` JSON lines (`userPrincipalName` = bare pseudonymous ID, `status.errorCode`) | R16 |
| `db_audit` | SQL Server Audit | `sys.fn_get_audit_file` columns as CSV (action_id SL/UP/IN/AL…) | R17 |
| `hr_feed` | Workday RaaS worker report | CSV with Employee_ID, Worker_Status, Active / Terminated / On_Leave flags, no names | R18 |
| `itsm_history` | ServiceNow incident / change_request | table CSV exports, raw state codes, 90-day window | R9 |

All URLs and quoted field definitions are in `RESEARCH_NOTES.md` R9–R18.

### What else changed

- **Thresholds in one module** (`ingest/config.py`): 3 distinct days for undocumented traffic, 7-day backup age,
  48 h transient-failure window, 90-day ownership window, RPO breach > 1 h, ephemeral ports ≥ 49152, telemetry sinks.
- **CMDB write-back rule**: only CI existence, `os_version`, `serial_number`, `ip_address`, `support_group`, `status`
  and relationships may be patched; enforced in `agent/approval.validate`. Patches edit the raw CMDB export and are
  recorded as a closed `change_request`; everything else becomes a ServiceNow-shaped `incident` or `sn_si_incident`
  in `runtime/tickets.jsonl`.
- **CSDM**: 51 per-hospital ED critical-results services are Service Offerings contained by `svc-emergency-care`;
  offerings and business services are counted separately everywhere.
- **Three layers**: every node and edge carries `layer` (declared / evidence / reconciliation); evidence edges carry
  `source` and `source_record` pointing at the raw record.
- **UI**: the change review is a conventional tiered CMDB dependency view (CMDB-direction arrows labelled with the
  relationship, impact by red fill, Reality / CMDB-says toggle, evidence tooltips, legend). New Sources tab shows one
  raw record per source next to the evidence row it produced. Findings show source chips and patch-vs-ticket.
- **Decisions** #53–#70 in `DECISIONS.md` (thresholds, write-back, offerings, layers, pipeline, identification order,
  per-source modelling choices, D9/D10 boundary, RPO evidence, dependency view, Sources tab, hidden edges use the
  3-day rule).

### Tests and verification

- 79 tests pass (`pytest -q`: `79 passed in 135.28s`). New `tests/test_sources.py` (11 tests): CMDB references are
  sys_ids and round-trip; IRE payload and identification rules; Prometheus text parses with `prometheus_client`'s
  standard parser; APM count / errors / p95 / days hand-checked; OTLP span mapping agrees with the metrics; netflow
  server side by port and aliases; the `ORU-CRIT` Database Writer JDBC URL resolves to `lrc-db-01`; backup snapshots
  and copies; identity sources; ITSM owner window; every evidence row has a source.
- Clean-room run from a deleted volume and venv at commit `dde7f5d`: 79 passed, loader idempotent (49,063 nodes,
  142,154 edges), `VERIFY.md` all checks passing, full Docker stack review of `CHG-HEADLINE` = high.
- **Size and time**: generation (export + connectors) 15.9 s, load 3.7 s; `data/` 388 MB (raw `apm_otel` 199 MB,
  `identity_sso` 60 MB, `netflow` 42 MB, `itsm_history` 16 MB; normalised 20 MB); committed `data_demo/` 25 MB gzipped.

### Headline numbers (unchanged truth, counted with offerings separate)

- **`CHG-HEADLINE` / `dc1-host-07`**: CMDB says 4 components, 0 interfaces, 0 hospitals, 0 departments,
  1 business service (`svc-lab-results`), 0 offerings. Reality says 107 components, 51 interfaces, 51 hospitals,
  51 Emergency departments, 2 business services (incl. `svc-emergency-care`), 51 service offerings. Risk **high**,
  51 hidden edges, found by the interface engine's Database Writer URL.
- **`CHG0031513` / `emr-app`**: 231 components, 115 interfaces, 51 hospitals, 124 departments, 2 business services,
  51 offerings in both views; no hidden edges; risk **low**.
- **After approving `F-D1-1b5bca2812`** on the running stack: 51 relationship ops applied, `CHG-HEADLINE` re-reviews
  as **low** with 0 hidden edges.

### Screenshots

- `docs/change-review-light.png`, `docs/change-review-dark.png`: CHG-HEADLINE dependency view
- `docs/change-review-low.png`: CHG0031513
- `docs/sources.png`: Sources tab with the Mirth Database Writer connector and the evidence row it produced
- `docs/findings.png`: the D1 finding with its sources and proposed CMDB patch

### Still simplified compared with a real deployment

- IRE payloads are written in fixed ~600-item batches rather than per-probe results; no MAC-address matching.
- Histogram buckets are one configured set; real collectors may differ per deployment.
- nfdump enrichment columns (reverse DNS, tag) stand in for collector-side enrichment that varies by product.
- Veeam task-level fields are flattened into the session record.
- Manufacturer and model are text, not references to `core_company` / `cmdb_model`.
- `sn_si_incident` numeric state values were not verifiable from public documentation; tickets use labels.
- Approvals change the generated CMDB export and reset on reseed; there is no real ticketing integration.
- The live-LLM check is still pending: no Anthropic API key is available on this host or in the credential vault, so
  all agent tests use the scripted fake LLM.

## UI redesign (UI_SPEC run, branch `ui-redesign`)

### What changed

- **Design system.** `ui/tokens.css` holds every colour, size, spacing, radius, line, shadow and motion value
  (light, dark via `prefers-color-scheme`, manual `data-theme` toggle, reduced motion). Colour carries one idea:
  blue for what the CMDB declares, red for evidence that contradicts it; everything else is neutral. Type is
  Atkinson Hyperlegible Next for the UI and Atkinson Hyperlegible Mono for CI names, change numbers and IPs only,
  on a 13/15/17/21/28/40 scale. Borders instead of shadows; radius 6 for controls, 10 for panels, 0 for the graph and
  tables. No literal outside the token file except the 960-px breakpoint.
- **Change review.** A list of upcoming and past changes with risk labels replaces the dropdown and the Review
  button. The verdict sentence is computed from the counts ("This change reaches 51 hospitals. The CMDB knows about
  none of them."; low-risk: "The CMDB matches the evidence for this change. Impact is known: 231 components."). A
  CMDB says / Reality / Missing table replaces the number tiles. Then the dependency view, hidden dependencies with
  their source, who to warn, and the AI explanation or a clearly labelled rule-based one.
- **Dependency view.** Hand-placed SVG instead of Cytoscape: tiers with names in a left gutter, CMDB-direction
  arrows, siblings grouped ("51 interfaces (ORU-CRIT-H*)", click or Enter lists them), dashed red observed-only
  edges always labelled, dotted declared-only, dash-dot failover, red fill driven by the Reality / CMDB says toggle,
  tooltips on hover and focus. The headline went from 51 crossing lines to an eight-box chain. On phones it becomes
  the impact path as a list. Chat and incident answers link to `#ci=name` (what breaks if that CI fails), with the
  cited path highlighted.
- **Findings** are a table filtered by severity, drift type, source, action and status; a drawer shows the
  explanation, evidence, path, the before/after diff of a CMDB update or the ticket preview, and "Approve CMDB
  update" / "Create ticket" / "Reject", confirmed by a toast with the same verb. **Incident** shows a numbered list
  of what the agent checked, which query and what it found, then the conclusion. **Sources** opens a raw record
  beside its evidence row. **Chat** is a single conversation column.
- **API additions** (DECISIONS #80): risk per change, hidden dependencies with sources, the whole radius for
  grouping, a CI-failure review, finding diffs, per-step results. A backend bug surfaced: reviews of changes on
  network devices crashed (`radius` said not-found for a CI with no impact edges); fixed.
- Removed from the screen: hospital map, critical nodes, notifications feed (not in UI_SPEC; endpoints remain).

### Process

Before-screenshots of every tab at 1440 and 390 px: `docs/ui-before/`. Plan with tokens, wireframes, components
and the avoid-list review: `docs/ui-plan.md` (section 6 lists what was revised: no KPI tiles, no all-caps headers,
no middle-dot strings, cards only where they mean something, static skeletons). Four self-critique rounds are
written up in `docs/ui-plan.md` section 7. After-screenshots of every tab, light and dark, 1440 and 390 px, plus the
expanded group, the finding drawer, the source sample and the CI view: `docs/ui-after/` (38 screenshots plus 6 demo stills). The README
screenshots now point at `docs/ui-after/`; the old `docs/*.png` files referenced in earlier sections of this report
were replaced.

### Accessibility results

- axe-core 4.10.2 on 9 views x light/dark x 1440/390 (36 runs) plus the expanded group list: **0 violations at any
  impact level** (serious and critical issues found during the build, and how they were fixed, are in
  `docs/ui-plan.md`).
- Contrast: every text/background token pair measured with the WCAG formula; body text at least 4.5:1 in both
  themes (lowest: caution on surface 5.0:1 light; signal on the impacted tint 4.9:1), graph lines at least 4.3:1.
  Risk and severity always have a text label; edge types differ by dash pattern as well as colour.
- Keyboard: every action is reachable (tabs with arrow keys, change list, toggle, every graph box and line, group
  lists, findings rows, drawer with focus trap, Esc, focus returned). Visible focus ring 2 px `--declared`, offset
  2 px.

### Demo check

Recorded at 1440 x 900 (`docs/ui-after/demo.webm`, stills `demo-1` to `demo-6`): CHG0031513 low -> CHG-HEADLINE
high -> 51-interface group expanded -> top finding -> "Approve CMDB update" -> toast "CMDB updated" ->
CHG-HEADLINE re-reviews as low ("Impact is known: 107 components."). Readable without zooming. The data and graph
were regenerated afterwards so the repository's demo state is unchanged.

### Tests

93 passed (`pytest -q`). `test_ui_served` checked for Cytoscape and was rewritten for the new files; a new test
covers every API addition; the incident test checks the new step results.

### Not done or different from the spec

- Departments are not a graph tier (UI_SPEC's tier list has none); they are counted in the comparison table.
- Group label uses parentheses, not a middle dot, and the real common prefix (`ORU-CRIT-H*`).
- Medical devices, endpoints and external services share the Application tier; network devices the Server tier.
- Dates follow the browser locale ("Sat, Oct 17, 03:30 PM GMT+5:30"), not the exact "Fri 17 Oct, 03:00 Pacific"
  wording.
- AI text quality is the free model's: the cached incident answer names the wrong change (`CHG0031513`) while every
  name and number in it is real, so the grounding check passes it. Change review risk, verdict and table never use
  the LLM.
- Screenshots were taken with Chromium only; no Safari or Firefox pass.

## Public deployment (branch `deploy`)

- **Live:** https://northwind-drift-agent.vercel.app (Vercel Hobby, function `bom1`, 35.6 MB bundle) on FalkorDB
  Cloud free tier (AWS ap-south-1). Public mode: cached AI answers only, no disk writes, approvals and an undo log
  in the graph, "Reset demo" plus a daily cron reset. Steps to reproduce: README "Deploy your own".
- **Connection:** plain TCP with username and password works (421-634 ms first connect from a laptop); TLS times
  out (the free tier has no TLS), so `FALKORDB_TLS=false`.
- **One-time load from a laptop:** 17,132 nodes, 63,378 edges in 12 s; triage 6 s; 59 findings.
- **Memory (free tier 100 MB):** `GRAPH.MEMORY USAGE` 21 MB; `used_memory` 32 MB steady; 48 MB peak while serving
  (triage, sampled every 20 ms); 47 MB peak during the load. A pristine second copy for reset was tried first and
  rejected (59 MB steady, 89 MB peak, DECISIONS #85).
- **Change reviews on the cloud database:** CHG-HEADLINE high (declared 4 components, 0 hospitals; real 107
  components, 51 interfaces, 51 hospitals, 51 departments, 2 business services, 51 offerings), cached AI
  explanation; CHG0031513 low (231 components in both views), cached AI explanation.
- **Live latency per call (laptop in India to bom1):** static files 0.4-0.6 s; health, changes, findings, sources,
  CI review 0.16-0.28 s; change reviews 0.32-0.45 s; incident (cached, 5 steps) 0.30 s; chat 0.17 s; approve
  1.1 s; scan 4.0 s; reset 4.2 s. Every call is far inside the 60-second function limit (Hobby maximum 300 s).
- **Tests:** 97 passed, including `tests/test_public.py` (cached-only LLM, public provider switch, health, approvals
  of a relationship, an owner attribute, a shadow CI and an unregistered app plus a ticket, all reverted by the cron
  reset, with no file written).
- **Not done:** `vercel dev` / local `vercel build` could not create the build venv with the uv-managed Python 3.12
  (`ensurepip` failed); public mode was tested with the TestClient against the local FalkorDB and then on the real
  deployment instead. Triage and the demo chat question have no cached AI answer (rule-based text is shown).
