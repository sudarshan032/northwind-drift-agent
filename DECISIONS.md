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
- Demo-site users ≈ 3–4 per workstation per department (shared clinical workstations), giving 2,655 at H03, 1,088 at H17, 157 at H44 and 300 at GCC-HYD. 3% of accounts are `disabled` (HR status from `ext-erp-hr-saas`) and never log in, except D12(b). Non-demo groups carry `member_count` only.

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
