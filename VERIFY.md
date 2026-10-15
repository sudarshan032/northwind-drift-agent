# VERIFY: Phase 0 data verification

Data: `data` (fresh regenerate, SEED=42); graph `northwind` (fresh load).

## Checks

| Check | Result |
|---|---|
| Full test suite passes | PASS |
| Oracle findings == ground_truth.json keys (D3 silent, D13 resolved) | PASS |
| Declared radius of dc1-host-07: lab reporting only, no Emergency care | PASS |
| Real radius: 51 ORU-CRIT routes, 51 hospitals, Emergency care | PASS |
| D14 path group -> users -> app -> emr-fhir-api -> emr-db-01 | PASS |
| Discovered without IDENTIFIES == D6 count | PASS |

**All checks passing: yes**

## Test output

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
tests/test_agent.py::test_write_back_rule PASSED
tests/test_agent.py::test_layers_tagged PASSED
tests/test_api.py::test_health_without_key PASSED
tests/test_api.py::test_change_review_endpoint PASSED
tests/test_api.py::test_scan_findings_approve_reject PASSED
tests/test_api.py::test_radius_subgraph_critical_hospitals PASSED
tests/test_api.py::test_agent_endpoints_with_fake_llm PASSED
tests/test_api.py::test_ui_served PASSED
tests/test_api.py::test_sources_endpoint PASSED
tests/test_api.py::test_findings_show_sources_and_action PASSED
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
tests/test_queries.py::test_tool_queries_within_timeout[access] PASSED
tests/test_queries.py::test_tool_queries_within_timeout[change_review] PASSED
tests/test_queries.py::test_tool_queries_within_timeout[component] PASSED
tests/test_queries.py::test_tool_queries_within_timeout[critical] PASSED
tests/test_queries.py::test_tool_queries_within_timeout[detect_all] PASSED
tests/test_queries.py::test_tool_queries_within_timeout[impact_path] PASSED
tests/test_queries.py::test_tool_queries_within_timeout[neighbourhood] PASSED
tests/test_queries.py::test_tool_queries_within_timeout[protection] PASSED
tests/test_queries.py::test_tool_queries_within_timeout[radius_declared_sso] PASSED
tests/test_queries.py::test_tool_queries_within_timeout[radius_real] PASSED
tests/test_queries.py::test_tool_queries_within_timeout[radius_real_emr_db] PASSED
tests/test_queries.py::test_tool_queries_within_timeout[who_to_page] PASSED
tests/test_queries.py::test_timeout_returns_clear_result PASSED
tests/test_sources.py::test_cmdb_export_references_are_sys_ids_and_round_trip PASSED
tests/test_sources.py::test_ire_payload_and_identification PASSED
tests/test_sources.py::test_prometheus_text_parses_with_standard_parser PASSED
tests/test_sources.py::test_apm_counts_errors_p95_days PASSED
tests/test_sources.py::test_otlp_json_mapping_and_agreement_with_metrics PASSED
tests/test_sources.py::test_netflow_server_side_by_port_and_aliases PASSED
tests/test_sources.py::test_oru_crit_database_writer_resolves_to_lrc_db PASSED
tests/test_sources.py::test_backups_snapshots_and_copies PASSED
tests/test_sources.py::test_identity_sources PASSED
tests/test_sources.py::test_itsm_owner_window PASSED
tests/test_sources.py::test_every_evidence_row_has_source PASSED
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
79 passed in 136.97s (0:02:16)
```

## Graph size

Total nodes **49,063**, total edges **142,154**.

| Node label | Count |
|---|---|
| Application | 270 |
| BackupTarget | 3 |
| BusinessService | 11 |
| Change | 22 |
| Component | 21,618 |
| Database | 79 |
| Department | 3,526 |
| Discovered | 11,576 |
| Endpoint | 1,664 |
| EndpointGroup | 6,778 |
| ExternalService | 4 |
| Group | 6,118 |
| Host | 186 |
| Interface | 253 |
| MedicalDevice | 2,275 |
| NetworkDevice | 9,758 |
| Role | 20 |
| ServiceOffering | 51 |
| Site | 1,857 |
| StorageArray | 39 |
| SupportGroup | 64 |
| UnregisteredEndpoint | 1 |
| User | 4,261 |
| VM | 247 |

| Edge type | Count |
|---|---|
| AFFECTS_DECLARED | 491 |
| AFFECTS_REAL | 491 |
| BACKED_UP_BY | 136 |
| CONNECTS_THROUGH | 18,796 |
| CONTAINS | 3,893 |
| DEPENDS_ON | 3,570 |
| GRANTS | 117 |
| HAS_ROLE | 6,118 |
| IDENTIFIES | 11,566 |
| IMPACTS_DECLARED | 4,670 |
| IMPACTS_REAL | 4,718 |
| IN_DEPARTMENT | 11,007 |
| LOCATED_AT | 21,618 |
| MANAGED_BY | 21,618 |
| MEMBER_OF | 4,261 |
| OBSERVED_ACCESS | 9,780 |
| OBSERVED_BACKUP_TO | 400 |
| OBSERVED_CALLS | 993 |
| OBSERVED_NETFLOW | 2,628 |
| OBSERVED_OWNER | 14,415 |
| OBSERVED_REPLICATES_TO | 12 |
| REPLICATES_TO | 12 |
| RUNS_ON | 644 |
| STORES_ON | 178 |
| TARGETS | 22 |

## Blast radius of `dc1-host-07` (target of CHG-HEADLINE)

| | Declared (CMDB) | Real (declared structure + observed) |
|---|---|---|
| routes | 0 | 51 |
| applications | 1 | 1 |
| departments | 0 | 51 |
| hospitals | 0 | 51 |
| business services | 1 | 2 |
| service offerings | 0 | 51 |
| components | 4 | 107 |

- Declared business services: svc-lab-results; applications: lis-reporting.
- Real high-safety business services: 52 (incl. `svc-emergency-care`); data consumer affected: emr-app.
- Declared path: `dc1-host-07 -> dc1-vm-lrc01 -> lrc-db-01 -> lis-reporting -> svc-lab-results` (runs_on, runs_on, depends_on, contains)
- Real path to Emergency care: `dc1-host-07 -> dc1-vm-lrc01 -> lrc-db-01 -> ORU-CRIT-H51 -> svc-h51-ed-critical-results -> svc-emergency-care` (runs_on, runs_on, calls, contains, contains)
- Real path to the H17 Emergency department: `dc1-host-07 -> dc1-vm-lrc01 -> lrc-db-01 -> ORU-CRIT-H17 -> H17-ED` (runs_on, runs_on, calls, serves)

## D14 path (unregistered SMART on FHIR app)

```
(:Group)<-[:MEMBER_OF]-(:User)-[:OBSERVED_ACCESS]->(:UnregisteredEndpoint)-[:OBSERVED_CALLS]->(:Component)-[:OBSERVED_CALLS]->(:Database)
[
 {
  "grp": "grp-H17-CARD-physician",
  "users": 19,
  "launches": 32.0,
  "app": "cardiorisk.example.com",
  "ip": "203.0.113.250",
  "api": "emr-fhir-api",
  "api_calls": 18928,
  "days_seen": 8,
  "data": [
   "emr-db-01"
  ],
  "sources": [
   "netflow"
  ]
 }
]
```

## Findings by drift type

| Type | Ground-truth entries | Expected finding keys | Oracle findings |
|---|---|---|---|
| D1 | 8 | 58 | 58 |
| D2 | 6 | 6 | 6 |
| D3 | 4 | none (must stay silent) | 0 flagged |
| D4 | 3 | 3 | 3 |
| D5 | 6 | 6 | 6 |
| D6 | 10 | 10 | 10 |
| D7 | 5 | 5 | 5 |
| D8 | 8 | 8 | 8 |
| D9 | 4 | 4 | 4 |
| D10 | 3 | 3 | 3 |
| D11 | 1 | 1 | 1 |
| D12 | 4 | 4 | 4 |
| D13 | 5 | aliases | 6 aliases resolved |
| D14 | 1 | 1 | 1 |

Total findings: 109; unexpected: 0.
