# DATA_SPEC: Northwind Health synthetic world

This file supersedes SPEC.md section 4 (data sources) and section 5 (graph schema). Agent, API, UI and query sections of SPEC.md will be revised later. Scope of this file: the data generator, the graph loader, and generator tests.

## 0. Ground rules

- **Fictional organisation:** "Northwind Health". Footprint is sized on the public footprint of a large US health system (51 hospitals, ~1,000 clinics, ~800 non-acute sites, 7 western states, an India capability centre in Hyderabad). No real organisation's systems, people, or incidents are represented. State this in the README and in `data/README.md`.
- **No realistic personal data.** Users are pseudonymous IDs (`u-h03-00412`) with role labels. No generated human names, emails, or patient data. Patients do not exist in this dataset.
- **One true world first.** The generator builds a single internally consistent "truth" model, then derives every view from it. Views may only disagree where drift was deliberately injected.
- **Deterministic.** Everything is driven by `SEED=42` and `SNAPSHOT_END=2026-10-14T23:59:59Z`. Same seed → byte-identical output. Observation window: 14 days ending at `SNAPSHOT_END`.
- **Every injected drift is recorded** in `data/ground_truth.json`.

## 1. Footprint

### 1.1 Sites

| Site type | Count | Notes |
|---|---|---|
| Large hospital (tertiary, 400+ beds) | 10 | Full departments, local servers and storage |
| Community hospital (100–399 beds) | 25 | Core departments, a few local servers |
| Critical access / small hospital (<100 beds) | 16 | Minimal local IT |
| Clinic | 1,000 | No servers; network, endpoints, some imaging/lab devices |
| Non-acute site | 800 | Types: home health, hospice, skilled nursing, senior living, rehab, behavioural health, admin office |
| Primary data centre `DC-PRI` | 1 | Washington. Two isolated rooms (A, B) for sync mirroring |
| DR data centre `DC-DR` | 1 | Texas. Async replicas, quiet until failover |
| Cloud region `cloud-us-west` | 1 | Zones a, b, c. Portal, analytics, newer apps, immutable backup vault |
| Cloud region `cloud-central-india` | 1 | Tools for the Hyderabad team. Must never hold US patient data |
| Hyderabad capability centre `GCC-HYD` | 1 | Support teams for cloud, clinical apps, integration, cybersecurity |

States: Alaska, California, Montana, New Mexico, Oregon, Texas, Washington. Use real US city names for site locations, distributed plausibly (most sites in WA, OR, CA). Hospital IDs `H01`–`H51`, clinics `C0001`–`C1000`, non-acute `N001`–`N800`.

### 1.2 Demo sites (full detail)

- `H03` large hospital (Seattle, WA)
- `H17` community hospital (Portland, OR)
- `H44` critical access hospital (a small Montana town)
- `GCC-HYD`

At demo sites every workstation, printer and user account is an individual CI or identity node, with serial numbers. Everywhere else, endpoints and users are group nodes with a `count`.

## 2. Inventory per site type (the truth model)

Ranges are inclusive; pick deterministically from the seed. "Group" means one group CI with `count`, except at demo sites where it expands into individual CIs.

| CI | Large | Community | Critical access | Clinic | Non-acute |
|---|---|---|---|---|---|
| Departments | 18–24 | 10–14 | 5–7 | 1–3 | 1 |
| WAN routers (SD-WAN) | 2 | 2 | 1 | 1 | 1 (or LTE gateway) |
| Firewalls | 2 | 1 | 1 | 1 | 0–1 |
| Core / distribution switches | 4–6 | 2 | 1 | 1 | 0 |
| Access switches | 30–50 | 10–20 | 3–6 | 1–2 | 0–1 |
| Wi-Fi APs | group | group | group | group | group |
| Medical-device network segment | 1 | 1 | 1 | 0–1 | 0 |
| Local servers | 10–14 | 4–7 | 1–2 | 0 | 0 |
| Local storage array | 1 | 0–1 | 0 | 0 | 0 |
| CT | 2–4 | 1–2 | 0–1 | 0 | 0 |
| MRI | 1–3 | 0–1 | 0 | 0 | 0 |
| X-ray | 5–8 | 2–4 | 1–2 | 0–1 (30% of clinics) | 0 |
| Ultrasound | 8–12 | 3–6 | 1–2 | 0–1 | 0 |
| Patient monitor gateways | 6–10 | 2–4 | 1 | 0 | 0 |
| Infusion pump server | 1 | 1 | 0–1 | 0 | 0 |
| Lab analysers | 12–20 | 4–8 | 1–3 | 0–2 | 0 |
| Workstations | 600–1,000 | 150–350 | 40–80 | 10–30 | 3–15 (laptops/tablets) |
| Printers | 100–200 | 30–60 | 8–15 | 2–5 | 1–3 |

Local server roles (choose from): PACS cache, modality worklist broker, lab analyser middleware, print server, nurse call server, monitor gateway server, pump server, badge/RTLS server, local file server, edge EMR cache. Each local server runs on a VM or physical host at that site.

Rules:
- Departments use real hospital department names (Emergency, ICU, Radiology, Laboratory, Pharmacy, Surgery, Cardiology, Oncology, Maternity, Pediatrics, Outpatient, Admissions, Billing, etc.), only those plausible for the tier.
- A site never has a device whose department doesn't exist there (no MRI at a clinic).
- Every medical device belongs to a department, connects through the site's medical-device segment, and is `patchable=false` with probability 0.4 (realistic for medical devices).

## 3. Central systems (the truth model)

All in `DC-PRI` unless stated. Each application has: app CI → one or more DB instances → VMs → physical hosts → datastores → storage arrays.

| System | Notes |
|---|---|
| EMR (enterprise instance) | Single instance for all sites. App tier 8 VMs, DB on sync-mirrored arrays (room A ↔ room B), async replica to `DC-DR` |
| Interface engine | 2-node cluster. Hosts every HL7/FHIR route (section 4) |
| Lab results cache DB `lrc-db-01` | Used by the interface engine to stage critical lab results. Central to the headline drift |
| LIS (lab) | DB + app VMs |
| RIS (radiology orders) | DB + app VMs |
| PACS / VNA archive | Large storage on its own arrays; async replica to `DC-DR` |
| Pharmacy | DB + app VMs |
| Billing / revenue cycle | DB + app VMs; connects to an external claims clearinghouse (external CI) |
| Scheduling | DB + app VMs |
| Patient portal | `cloud-us-west`, zones a/b |
| Analytics / data warehouse | `cloud-us-west`; receives nightly extracts from EMR |
| Identity: AD domain controllers (4, two per US DC) and SSO | Every app authenticates through SSO |
| SIEM and monitoring | `cloud-us-west` |
| Backup system | Backup servers in `DC-PRI` writing to a backup appliance in `DC-PRI`, plus copy to an immutable vault in `cloud-us-west` |
| Email / collaboration | External SaaS CI |
| ERP / HR | External SaaS CI (provides user status for identity checks) |
| Ticketing / ITSM tool | External SaaS CI |
| GCC tooling (dev/test, automation) | `cloud-central-india`. Never stores patient data |

Storage arrays: `arr-pri-a-01..04` (room A), `arr-pri-b-01..04` (room B), `arr-dr-01..06`, plus one local array per large hospital and some community hospitals. Attributes: `raid_level` (RAID6 or RAID10), `capacity_tb`, `site`, `encrypted`.

## 4. Interfaces (HL7 / FHIR / DICOM)

Model each interface as an `Interface` CI with `message_type`, `protocol`, `source`, `target`, `via` (interface engine), `criticality`, `patient_safety_impact`.

Enterprise interfaces (EMR ↔ central systems): ADT (EMR → LIS, RIS, pharmacy, billing, scheduling), ORM orders (EMR → LIS, EMR → RIS), ORU results (LIS → EMR, RIS → EMR), RDE pharmacy orders, DFT charges (EMR → billing), MDM documents.

Site-level interfaces (one set per hospital): lab analyser middleware → LIS (results), modality → PACS cache (DICOM images), PACS cache → central PACS, monitor gateway → EMR (vitals), and `ORU-CRIT-<site>`: **critical lab results route for the Emergency department**, LIS → interface engine → EMR ED worklist. In truth, every `ORU-CRIT-*` route stages messages in `lrc-db-01`.

## 5. Identity and access

- Groups per site per department per role, e.g. `grp-H17-ED-nurse`, `grp-H03-LAB-tech`, plus enterprise groups (`grp-integration-eng`, `grp-dba`, `grp-hyd-clinical-apps-support`, `grp-hyd-cloud-ops`, `grp-secops`).
- Roles grant access to applications or databases with a level (`read`, `write`, `admin`).
- Declared access path: `User -MEMBER_OF-> Group -HAS_ROLE-> Role -GRANTS {level}-> App|DB`.
- Approved change windows: admin access to production DBs is only legitimate inside an approved change in `change_request.csv`.
- Demo sites and `GCC-HYD`: individual users (realistic counts for the site size; GCC-HYD ~300 users) and individual workstations. Users have `status` (active / disabled) sourced from the HR system.
- Elsewhere: group nodes with `count` only.

## 6. Output files

```
data/
  README.md                  # fictional disclaimer, how generated, seed
  truth/world.json           # full truth model (for tests only, never loaded as a "view")
  cmdb/                      # DECLARED view (ServiceNow-shaped CSV)
    cmdb_ci.csv
    cmdb_rel_ci.csv
    cmn_location.csv
    cmn_department.csv
    sys_user_group.csv       # support groups / owners
    change_request.csv       # pending + recent changes (includes the headline change)
  discovery/                 # DISCOVERED view (what scanners found)
    scan_results.jsonl       # one record per discovered device: serial, hostname, ip, os, model, site, last_scanned
    scan_coverage.csv        # subnet, site, scanned (true/false), last_scan
  observed/                  # OBSERVED view (what actually happens)
    flows_hourly.jsonl       # app/interface traffic aggregates: source, target, protocol, msg_type, count, errors, p95_ms, hour
    traces_sample.otlp.jsonl # real OTLP-JSON spans for demo sites, 2 hours, for realism and the ingestion demo
    netflow_hourly.jsonl     # network-level flows incl. un-instrumented devices: src, dst, port, bytes, hour
    backup_jobs.jsonl        # object, policy, target, status, start, end, bytes
    replication_status.jsonl # source volume/array, target, mode (sync|async), lag_seconds, state, hour
    access_log.jsonl         # demo sites + GCC-HYD only: ts, user, workstation, target, action, result
  identity/
    users.csv, groups.csv, memberships.csv, roles.csv, grants.csv
  evidence/
    ownership_evidence.csv   # who actually resolves incidents/changes per CI (synthetic stand-in for ITSM history)
  ground_truth.json
```

CMDB CSV columns must include at least:
- `cmdb_ci.csv`: `sys_id, name, class, site, department, environment, status (operational|retired), support_group, criticality, handles_phi, data_residency, serial_number, os, os_version, model, ip_address, rpo_minutes, rto_hours, backup_policy, backup_offsite (true|false), patchable, patient_safety_impact`
- `cmdb_rel_ci.csv`: `parent, child, type, failover (true|false)` with types: `Contains::Contained by`, `Depends on::Used by`, `Runs on::Runs`, `Stores on::Stores`, `Replicates to::Replica of`, `Backed up by::Backs up`, `Connects through::Connects`, `Located at`, `Managed by`

## 7. Drift catalogue (inject exactly these; record each in ground_truth.json)

| ID | Drift type | Count | How it shows up | Must be found by |
|---|---|---|---|---|
| D1 | Undocumented dependency | 8 | Flow exists, CMDB has no relationship | flows / netflow vs CMDB |
| D1-HEADLINE | All `ORU-CRIT-*` routes depend on `lrc-db-01`, which runs on VM on host `dc1-host-07`. CMDB links `lrc-db-01` only to LIS reporting. A pending change `CHG-HEADLINE` patches `dc1-host-07` | 1 (counts as 1 of the 8) | Declared radius: LIS reporting only. Real radius: ED critical results at ≥40 hospitals | flows vs CMDB + change |
| D2 | Stale relationship | 6 | CMDB relationship, no flow in window, not failover | CMDB vs flows |
| D3 | Failover trap (must NOT be flagged) | 4 | DR replication / failover interfaces with `failover=true`, no traffic | — |
| D4 | Retired CI still linked | 3 | `status=retired`, still has relationships | CMDB |
| D5 | Owner drift | 6 | CMDB `support_group` ≠ team in `ownership_evidence.csv` | evidence vs CMDB |
| D6 | Discovered but not in CMDB (shadow IT) | 10 | In scan_results, no matching CI (incl. 2 unregistered servers at clinics, 1 unregistered imaging workstation) | discovery vs CMDB |
| D7 | In CMDB but never discovered (ghost) | 5 | CI in a scanned subnet, absent from scans, no flows | CMDB vs discovery |
| D8 | Attribute drift | 8 | CMDB `os_version` / `serial_number` / `ip_address` differs from discovery | discovery vs CMDB |
| D9 | Protection-path drift | 4 | PHI system whose storage path never reaches another site, while CMDB says `backup_offsite=true` (incl. a lab middleware DB at `H17`) | replication + backup vs CMDB |
| D10 | Backups failing | 3 | CMDB says backed up; last successful job > 7 days before SNAPSHOT_END | backup_jobs |
| D11 | Data residency | 1 | A replication or extract copies a PHI dataset into `cloud-central-india` | replication / flows |
| D12 | Access drift | 4 | Observed access with no declared grant path, incl. (a) a GCC-HYD support account running admin queries on EMR prod DB outside any approved change window, (b) a disabled user's account still logging in at H03 | access_log vs identity |
| D13 | Naming mismatch (not a real problem) | 5 | Same CI with different names across CMDB / discovery / flows, e.g. `LIS-HYD...` vs `lis-prod-02`; serial number matches | must be resolved, not reported as D1/D6/D7 |

Also add **realistic noise that must not produce findings**: hourly traffic variation, a few transient failed backup jobs that later succeed, weekend dips, one-off health-check flows.

`ground_truth.json` format: list of `{id, type, description, ci_ids, sources, expected_finding}`; D3 and D13 entries have `expected_finding: "none"` (D13: `"resolve_as_same_ci"`).

## 8. Graph mapping (FalkorDB, graph `northwind`)

- Every CI node has label `:Component` plus a class label: `:BusinessService, :Application, :Interface, :Database, :VM, :Host, :StorageArray, :BackupTarget, :NetworkDevice, :MedicalDevice, :Endpoint, :EndpointGroup, :ExternalService`.
- Reference nodes: `:Site, :Department, :SupportGroup, :User, :Group, :Role, :Change`.
- Declared edges (from CMDB / identity): `CONTAINS, DEPENDS_ON {failover}, RUNS_ON, STORES_ON, REPLICATES_TO {mode, failover}, BACKED_UP_BY, CONNECTS_THROUGH, LOCATED_AT, IN_DEPARTMENT, MANAGED_BY, MEMBER_OF, HAS_ROLE, GRANTS {level}, TARGETS (Change→CI)`.
- Observed edges: `OBSERVED_CALLS {count, errors, p95_ms, first_seen, last_seen, protocol, msg_type}` (flows), `OBSERVED_NETFLOW {bytes, port, last_seen}`, `OBSERVED_REPLICATES_TO {mode, max_lag_s, last_seen, state}`, `OBSERVED_BACKUP_TO {last_success, last_status}`, `OBSERVED_ACCESS {action, count, first_seen, last_seen}` (User→Component), `OBSERVED_OWNER` (SupportGroup→Component).
- Discovery: `:Discovered` nodes, linked by `IDENTIFIES` to a `:Component` when deterministic identification succeeds: first by `serial_number`, else by `hostname + ip_address`. Unmatched `:Discovered` nodes are candidates for D6. Store discovered attributes on the `:Discovered` node, never overwrite CMDB attributes.
- Indexes: `Component.name`, `Component.sys_id`, `Discovered.serial_number`, `Site.id`, `User.id`, `Group.id`.
- Ingestion is idempotent (`MERGE`), batched with `UNWIND` (batch size 1,000–5,000), and logs node/edge counts per label at the end.
- The loader never reads `truth/world.json`.

## 9. Generator tests (must pass before anything else is built)

1. **Determinism:** two runs with `SEED=42` produce identical file hashes.
2. **Consistency without drift:** with drift injection disabled, CMDB, discovery and observed views agree exactly (every declared relationship has matching observation, every CI is discovered, all protection paths reach `DC-DR` and the cloud vault, all accesses have grant paths).
3. **Drift catalogue:** with drift enabled, the diff between views equals exactly the entries in `ground_truth.json`, by type and count.
4. **Physical plausibility:** no device in a department that doesn't exist at its site; every VM runs on a host at the same site or data centre; every app's chain reaches a storage array; no PHI in `cloud-central-india` except D11.
5. **Headline scenario:** `ORU-CRIT-*` routes for ≥40 hospitals reach `lrc-db-01` in the observed view; the CMDB view links `lrc-db-01` only to LIS reporting; `CHG-HEADLINE` targets `dc1-host-07`.
6. **Size report:** print counts per CI class and per site type; total nodes expected roughly 80k–150k, edges roughly 300k–700k. Fail if outside 50k–250k nodes.
7. **Loader:** loading twice yields identical node/edge counts.

## 10. Notes

- Total size will exceed the FalkorDB Cloud free tier (100 MB). If we deploy live later, generate a "demo region" subset (WA + OR sites, both DCs, GCC-HYD) with a `--subset demo` flag.
- Commit generated data only for the demo subset if the full set is large; otherwise the generator command must reproduce it in under 2 minutes.
- Generator entry point: `python -m ingest.generate --seed 42 --out data/`; loader: `python -m ingest.load --data data/ --graph northwind`.