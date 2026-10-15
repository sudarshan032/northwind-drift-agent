# Research notes: realism check for the Northwind Health generator

Researched 2026-10-15 with web sources. Confidence tags: **[verified]** = stated in
the cited page; **[secondary]** = vendor, forum or search-result summary;
**[inferred]** = convention / reasoning, no authoritative number found.
Each section ends with what changed in the generator (or why nothing did).

## R1. Departments by hospital tier, critical access hospitals

- CMS CAH conditions: at most **25 inpatient beds**, average acute stay ≤ 96 h,
  > 35 miles from another hospital (15 in mountains), **24/7 emergency services**
  with staff on site within 30 minutes. [verified]
  https://www.cms.gov/medicare/health-safety-standards/certification-compliance/critical-access-hospitals
- 1,386 CAHs nationally; 64.3% run a Rural Health Clinic; Montana has ~50. [verified]
  https://www.ruralhealthinfo.org/topics/critical-access-hospitals
- Canonical department (cost-centre) names: CMS-2552-10 Worksheet A list (Emergency,
  ICU, Operating Room, Delivery Room & Labor Room, Radiology-Diagnostic, CT Scan, MRI,
  Laboratory, Pharmacy, Respiratory Therapy, Physical Therapy, Renal Dialysis,
  Cardiac Catheterization, Admitting, Medical Records, ...). [verified]
  https://m.nber.org/hcris/2552-10/HOSP2010_CSTCODES.pdf
- Clinic types: CMS Place of Service codes (11 office, 20 urgent care, 19/22
  outpatient hospital, 50 FQHC, 72 RHC, 81 lab ...). [verified]
  https://www.cms.gov/medicare/coding-billing/place-of-service-codes/code-sets

**Changed:** CAH beds generated as 12–25 (DATA_SPEC said "<100"). Every hospital has
an Emergency department (also RUN_PROMPT 4.4). Department vocabulary already matched
the cost-centre names. Tier pools (large: ICU, CATH, IR, NICU, TRAUMA ...; CAH: ED,
Med-Surg/swing beds, Lab, Radiology, Pharmacy + optional Outpatient/Rehab) were
checked against the list: no change needed.

## R2. Networked medical devices and imaging per hospital

- "10–15 connected medical devices per bed", IV pumps ≈ 38% of the IoT footprint.
  [secondary] https://extremenetworks.com/resources/blogs/drivers-disruptors-in-healthcare-intelligent-network-edge
- 10 German university hospitals: mean 25,150 medical devices, ~14–18% networked;
  radiology and ICU most networked. [verified] https://pmc.ncbi.nlm.nih.gov/articles/PMC7526356
- CT by bed size (IMV 2001, a floor): 400+ beds: 51% had > 2 CTs; 200–399 beds:
  58% had 1, 37% had 2. [verified]
  https://www.auntminnie.com/industry-news/market-analysis/article/15564906/auntminnies-imv-marketstat-20-distribution-of-ct-sites
- Inferred modern ranges: 400+ beds CT 3–5, MRI 2–4, X-ray 8–15, US 10–20;
  100–399 beds CT 1–2, MRI 1, X-ray 3–6, US 4–8; CAH CT 0–1, MRI 0, X-ray 1–2, US 1. [inferred]
- Workstations ≈ 1.5–2.5 per bed, printers 0.3–0.5 per bed, APs ≈ 1 per 2–4 beds. [inferred]

**Changed:**
1. Imaging ranges raised to the research ranges (large CT 2–4→3–5, MRI 1–3→2–4,
   X-ray 5–8→8–15, US 8–12→10–20; community MRI 0–1→1, X-ray 2–4→3–6, US 3–6→4–8;
   CAH US 1–2→1). DATA_SPEC section 2 table deviated on purpose (DECISIONS #31).
2. Bedside fleets added per hospital as two group CIs with `count`
   (`hNN-pump-fleet` 0.9–1.3 pump channels per bed, `hNN-monitor-fleet` 0.6–0.9 per
   bed). Before, a 600-bed hospital had ~0.1 networked device per bed; now ~2 per bed
   are represented without one node per pump (no padding). Passive scan coverage
   counts them.
3. Not changed: workstation and printer ranges stay at the DATA_SPEC table values
   (somewhat low per bed for 900-bed sites; noted as a limitation). APs already
   `beds // 3`.

## R3. HL7 v2 message types and volumes

- Structures and status from HL7 tables 0354/0003: ADT A01/A02/A03/A04/A08/A40 active,
  A34 deprecated; **ORM^O01 deprecated (2016) but still ubiquitous**, OML^O21 for lab
  orders; ORU^R01; RDE^O11, RDS^O13, RAS^O17; DFT^P03; MDM^T02; SIU^S12. [verified]
  https://hl7.eu/refactored/tab0354.html , https://hl7.eu/refactored/tab0003.html
- Volumes: ~500,000 messages/day for a large hospital; a 750-bed hospital + 100 clinics
  ~700,000/day incl. ~15,000 ADT per weekday; regional networks > 20M/day. [secondary]
  https://chat-archive.fhir.org/stream/179166-implementers/topic/Hospital.20HL7.20message.20traffic.html

**Changed:** lab orders use OML^O21 and radiology orders ORM^O01 (as designed);
ADT feeds now split traffic over trigger events (A08 40%, A04 20%, A01/A03 12%,
A02 8%, A40 2%) instead of a single type; enterprise HL7 rates ×8, site lab results
×5 and vitals ×3 so volumes land near ~1,000 messages/bed/day. Only counts changed;
record counts and drift are unaffected. RDS/RAS not modelled (not in DATA_SPEC).

## R4. DICOM / PACS flows

- IHE Scheduled Workflow: MWL C-FIND → MPPS → C-STORE → Storage Commitment → PACS → VNA
  → DR archive. [verified] https://dicom.nema.org/medical/dicom/2019e/output/chtml/part02/sect_B.4.2.html
- Ports 104 (traditional), 11112 (non-privileged), 2762 (DICOM TLS); DICOMweb
  QIDO/WADO/STOW/UPS-RS. [verified/secondary] https://www.dicomstandard.org/using/dicomweb ,
  https://www.connected.app/ports/2762
- Study sizes: CT ~774 images/study (2012), ~400–500 MB; MR 100 MB–GBs; DR/CR
  10–30 MB; US 5–60 MB. [verified/secondary] https://archive.rsna.org/2012/12022076.html ,
  https://edenmed.com/whitepapers/radiological-images-size-weight-and-quality

**Changed:** nothing; the generator already used MWL broker → modality → PACS cache →
central PACS/VNA → DR, port 104, and 450 MB / 300 MB / 20 MB / 30 MB per CT / MR /
X-ray / US study.

## R5. Backup and replication practice

- HHS 405(d): offline, encrypted backups, 3-2-1, backups not reachable from the network
  they protect. [verified] https://405d.hhs.gov/threats/405d-ransomware-threat.pdf ,
  https://hhscyber.hhs.gov/Documents/Posters_and_awareness/HHS-PrepareReactRecover-Ransomware.pdf
- 3-2-1-1-0 (one immutable/air-gapped copy, zero restore errors). [verified]
  https://bp.veeam.com/vb365/guide/design/3-2-1
- Tiers: tier 0 EHR DB RTO 0–1 h, RPO ≈ 0 (sync); ancillary (PACS, LIS) RTO 1–4 h;
  analytics same day. [secondary]
  https://www.accountablehq.com/post/ehr-disaster-recovery-best-practices-rto-rpo-and-hipaa-compliance
- InterSystems mirroring: sync failover pair + up to six async members for DR/reporting.
  [verified] https://community.intersystems.com/sites/default/files/documentation/Cache_Database_Mirroring_Executive_Overview-d08ecb34b09932af6ac561145a908fc5.pdf

**Changed:** tiers set to tier 0 RPO 0 / RTO 1 h, tier 1 RPO 15 min / RTO 4 h, tier 2
RPO 4 h / RTO 24 h, tier 3 RPO 24 h / RTO 72 h; EMR DB is InterSystems IRIS on
sync-mirrored room A/B arrays with async copy to DC-DR; backups follow 3-2-1-1-0
(DC-PRI appliance → DC-DR appliance + immutable cloud vault, isolated backup subnet);
daily incremental + Sunday full. Not modelled: 15-minute log backups (would add
volume, no drift value).

## R6. ServiceNow CMDB classes and relationship types

- Verified class names: `cmdb_ci_vmware_instance`, `cmdb_ci_vm_instance`,
  `cmdb_ci_esx_server`, `cmdb_ci_db_mssql_instance`, `cmdb_ci_db_ora_instance`,
  `cmdb_ci_storage_server` (array; **`cmdb_ci_san` not confirmed**), `cmdb_ci_ip_router`,
  `cmdb_ci_ip_switch`, `cmdb_ci_ip_firewall`, `cmdb_ci_wap_network`,
  `cmdb_ci_service_business`, medical devices **`cmdb_ci_med_device` and subclasses**
  (`cmdb_ci_med_diagnostic_imaging`, `_lab_equipment`, `_patient_monitoring`,
  `_therapeutic_device`); **`cmdb_ci_medical_device` does not exist**.
  https://www.servicenow.com/docs/r/LZHoJyH9VPKmhdIph_6gSw/MrxGJ1ymu0tR1YLAO8gLPw ,
  https://www.servicenow.com/community/cmdb-blog/disambiguation-of-cmdb-storage-classes/bc-p/3580302 ,
  https://www.servicenow.com/docs/r/WXkoCiODvb5QAWqlhQpYRQ/W5_P9TKVCGIwIV7INADRLA
- OOB relationship types: `Depends on::Used by`, `Runs on::Runs`, `Contains::Contained by`,
  `Hosted on::Hosts`, `Members::Member of`, `Manages::Managed by`, `Connects to::Connected by`,
  `Provides storage for::Stored on`, `Virtualized by::Virtualizes`. **Not OOB:**
  `Stores on::Stores`, `Backed up by::Backs up`, `Replicates to`, `Connects through`,
  `Located at` (location is a `cmn_location` reference field).
  https://www.servicenow.com/docs/bundle/washingtondc-servicenow-platform/page/product/configuration-management/concept/c_CIRelationships.html
- sys_id: 32-character GUID, lowercase hex in practice. [verified]
  https://www.servicenow.com/docs/r/platform-administration/c_UniqueRecordIdentifier.html

**Changed:** class names use the verified ServiceNow classes (`cmdb_ci_storage_server`,
`cmdb_ci_med_*`, etc.); custom classes carry the ServiceNow `u_` prefix
(`u_cmdb_ci_hl7_interface`, `u_cmdb_ci_backup_target`, `u_cmdb_ci_endpoint_group`);
sys_ids are 32 lowercase hex; location / department / support group are reference
columns on `cmdb_ci`, not relationship rows. **Not changed:** the relationship type
strings stay exactly as DATA_SPEC section 6 defines them (agent queries will rely on
them); in a real instance `Stores on`, `Backed up by`, `Replicates to` and
`Connects through` would be custom types. Owner decision listed in REPORT.md.

## R7. Active Directory group naming

- Microsoft group scopes (global / domain local / universal). [verified]
  https://learn.microsoft.com/en-us/windows-server/identity/ad-ds/manage/understand-security-groups
- AGDLP: accounts → global groups by role (`GG_<Site>_<Dept>_<Role>`) → domain local
  groups by resource (`DL_<Resource>_<RO|RW>`) → permissions. [secondary]

**Not changed:** DATA_SPEC section 5 prescribes `grp-<site>-<dept>-<role>` and the
enterprise group names verbatim; the model already follows AGDLP structurally
(User → Group by site/dept/role → Role → GRANTS on resource). Renaming to `GG_`/`DL_`
is an owner decision (REPORT.md).

## R8. Small-site networking

- Large clinic networks commonly run SD-WAN edge routers with LTE backup; small sites
  LTE-only. [secondary]

**Changed:** nothing; clinics have one SD-WAN router; ~half of home-health and hospice
offices use an LTE gateway as their only WAN device.
