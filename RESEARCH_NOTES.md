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

---

# REALISM_SPEC: real tool export formats (researched 2026-10-16)

Each section lists what was verified and how the generator follows it. **[verified]**
= official docs or vendor source code; **[secondary]** = vendor SDK, community or
third-party integration; **[unverified]** = not confirmed. Deviations are logged in
DECISIONS #57–#70.

## R9. ServiceNow table exports (cmdb_export, itsm_history)
- CSV list exports use field names as headers; by default reference and choice columns
  show display values; `sysparm_display_value=false` ("export with sys_id") gives
  32-hex sys_ids and raw choice codes. [verified / secondary]
  https://www.servicenow.com/docs/r/7LhbZn3yz3Q58qhjZoU9iQ/aCxgzUZg5t5zw62sOHOL8A ,
  https://www.servicenow.com/community/itom-forum/now-knowledge-exchange-export-excel-csv-with-sys-id/td-p/2821700
- `cmdb_ci` fields (sys_id, name, sys_class_name, serial_number, ip_address, mac_address,
  os, os_version, model_id, manufacturer, location, department, support_group,
  assignment_group, install_status, operational_status, used_for, busines_criticality,
  short_description); `install_status` 1 Installed … 7 Retired; `operational_status` 1
  Operational … 6 Retired. [secondary: Ansible ServiceNow collection]
  https://raw.githubusercontent.com/ansible-collections/servicenow.itsm/main/plugins/module_utils/configuration_item.py ,
  https://davidmac.pro/posts/2026-02-22-sn-ci-status-fields/
- `cmdb_rel_ci` (sys_id, parent, child, type, port, percent_outage, connection_strength),
  `cmdb_rel_type` (sys_id, name, parent_descriptor, child_descriptor); `Depends on::Used by`
  has the well-known sys_id 1a9cb166f1571100a92eb60da2bce5c5. [verified docs / secondary]
  https://www.servicenow.com/docs/r/K~TMjGrGv2CmYjaGLbVoJQ/gHIPLkI4~BHd6TBZesIWXQ
- `change_request` state −5 New … −2 Scheduled, −1 Implement, 0 Review, 3 Closed, 4 Canceled;
  risk 1 Very High … 4 Low; `incident` state 1 New … 6 Resolved, 7 Closed. [secondary]
  https://raw.githubusercontent.com/ansible-collections/servicenow.itsm/main/plugins/module_utils/change_request.py ,
  https://raw.githubusercontent.com/ansible-collections/servicenow.itsm/main/plugins/module_utils/incident.py
- CSDM: the service offering class is `service_offering` (parent = business service). [verified]
  https://www.servicenow.com/docs/r/7LhbZn3yz3Q58qhjZoU9iQ/lgk7xCyuQ9Z4bLzajL5utg
- Security incidents (`sn_si_incident`): state names Draft … Closed confirmed, numeric codes [unverified].

**Generator:** raw-value exports (sys_id references, numeric `install_status` /
`operational_status`, change `state` and `risk` codes, incident state 7); `cmdb_ci`
adds `operational_status`, `assignment_group`, `mac_address`; `cmdb_rel_ci` adds `port`,
`percent_outage`, `connection_strength`; `cmdb_rel_type` exported; per-site services
classed `service_offering`. Custom fields are `u_*`. Manufacturer and model are exported as
text (a real raw export would reference core_company / cmdb_model, which are not exported).

## R10. ServiceNow IRE payload (discovery_scanner)
- `POST /api/now/identifyreconcile`: `items[]` (className, values, lookup[], internal_id,
  sys_object_source_info), `referenceItems[]`, `relations[]` (type, parent/child as
  positions in `items`). Identification rules match serial number, then name, then IP/MAC
  (the identifier rules per class). [verified]
  https://www.servicenow.com/docs/r/eJIqiaxRKVcMaR8XQU6_oA/AawFLNaPF5h3FoI97~DLtg ,
  https://www.servicenow.com/docs/r/LZHoJyH9VPKmhdIph_6gSw/WMrpD2qQz3IbhO1aOIa~Jg
- SQL Server instances carry `tcp_port` / `instance_name`; TCP observations land in `cmdb_tcp`.
  [secondary] https://www.servicenow.com/community/itom-articles/servicenow-sql-server-discovery-troubleshooting-the-hidden-tcp/ta-p/3369338

**Generator:** one payload per scan batch of ~600 items (the API accepts multi-item
payloads); device items with a `cmdb_ci_network_adapter` lookup; application and database
items with `tcp_port` related `Runs on::Runs` to their host item (listening services).

## R11. OpenTelemetry servicegraph, Prometheus text, OTLP/JSON (apm_otel)
- servicegraph metrics `traces_service_graph_request_total`,
  `traces_service_graph_request_failed_total`, histograms `..._request_server` /
  `..._request_client` (seconds); labels client, server, connection_type (empty,
  messaging_system, database, virtual_node) and failed; database server node named from
  db.name / db.namespace; default buckets 2 ms … 15 s, configurable via
  `latency_histogram_buckets`. [verified: source + README]
  https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/main/connector/servicegraphconnector/README.md ,
  https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/main/connector/servicegraphconnector/connector.go ,
  https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/main/connector/servicegraphconnector/internal/store/edge.go
- Prometheus text exposition: `# HELP`, `# TYPE`, `name{labels} value [timestamp_ms]`,
  histogram `_bucket{le}` cumulative, `_sum`, `_count`. [verified]
  https://prometheus.io/docs/instrumenting/exposition_formats/
- OTLP/JSON: protobuf JSON mapping with lowerCamelCase keys, hex traceId/spanId, integer
  enums (kind, status.code), 64-bit integers (`*UnixNano`, intValue) as strings. [verified]
  https://opentelemetry.io/docs/specs/otlp/ ,
  https://raw.githubusercontent.com/open-telemetry/opentelemetry-proto/main/examples/trace.json

**Generator:** hourly/daily scrapes with ms timestamps, cumulative counters, labels
client / connection_type (omitted when empty, which Prometheus treats as absent) / failed /
server, configured buckets 10 ms–5 s (+Inf) to keep the data set under 500 MB; the
`_seconds` suffix on the histogram name is inferred from the Prometheus exporter's unit
handling [unverified]. Span sample (2 h, demo hospitals) obeys the OTLP mapping; tests parse
the scrapes with `prometheus_client` and check that span counts equal metric increases.

## R12. nfdump CSV (netflow)
- nfdump 1.6.x `-o csv`: 48 columns `ts,te,td,sa,da,sp,dp,pr,flg,fwd,stos,ipkt,ibyt,opkt,obyt,…`
  plus a Summary footer; nfdump 1.7.x: `firstSeen,duration,proto,srcAddr,srcPort,dstAddr,
  dstPort,packets,bytes,flows` with millisecond timestamps and numeric protocol. [verified: source]
  https://raw.githubusercontent.com/phaag/nfdump/master/src/output/output_csv.c ,
  https://raw.githubusercontent.com/phaag/nfdump/v1.6.25/bin/output_csv.c
- IANA: 104 DICOM, 1433 ms-sql-s, 1972 intersys-cache, 2575 hl7, 636 ldaps; dynamic ports
  49152–65535. [verified] https://www.iana.org/assignments/service-names-port-numbers/service-names-port-numbers.csv
- IPFIX element names (flowStartMilliseconds, sourceIPv4Address, destinationTransportPort,
  packetDeltaCount, octetDeltaCount) for comparison. [verified]
  https://www.iana.org/assignments/ipfix/ipfix-information-elements.csv

**Generator:** nfdump 1.7 layout, both directions of every aggregated conversation, UTC,
plus three collector-enrichment columns (`srcDns`, `dstDns`, `tag`) that nfdump itself does
not emit (flow analysers add reverse DNS and application tags; DECISIONS #61).

## R13. Mirth Connect (interface_engine)
- Channel XML: `<channel>` with `<sourceConnector>` / `<destinationConnectors><connector>`,
  `<properties class=…>` and `<transportName>`: TCP Listener (TcpReceiverProperties,
  listenerConnectorProperties host/port), TCP Sender (TcpDispatcherProperties, remoteAddress,
  remotePort), Database Writer (DatabaseDispatcherProperties, driver, url), HTTP Sender (host
  URL), DICOM Listener / Sender (DICOMReceiver/DispatcherProperties, host, port). [verified: source]
  https://raw.githubusercontent.com/nextgenhealthcare/connect/development/server/src/com/mirth/connect/connectors/tcp/TcpReceiverProperties.java ,
  https://raw.githubusercontent.com/nextgenhealthcare/connect-examples/master/Channels/Validate%20XSD%20Source%20Filter/Example%20-%20Validate%20XSD.xml
- `GET /api/channels/statistics`: serverId, channelId, received, sent, error, filtered, queued;
  default metadata columns SOURCE and TYPE. [verified: source]
  https://raw.githubusercontent.com/nextgenhealthcare/connect/development/core-models/src/com/mirth/connect/model/ChannelStatistics.java ,
  https://raw.githubusercontent.com/nextgenhealthcare/connect/development/core-models/src/com/mirth/connect/model/util/DefaultMetaData.java

**Generator:** one `<channel>` per HL7/DICOM route; Database Writer JDBC URL
`jdbc:sqlserver://<host>:1433;databaseName=…` for the critical-results staging; daily
statistics per connector (`metaDataId`) with the ChannelStatistics field names, and the
SOURCE metadata value (sending application) on the source connector rows.

## R14. Veeam Backup & Replication REST v1 (backup_software)
- `/api/v1/sessions`: id, name, jobId, sessionType (BackupJob, BackupCopyJob, …),
  creationTime, endTime, state (Stopped, Working, …), progressPercent, result {result: None |
  Success | Warning | Failed, message, isCanceled}, progress {transferredSize, …}; snapshot-only
  jobs have `isStorageSnapshot: true` in their job state. [verified: OpenAPI in the docs]
  https://helpcenter.veeam.com/references/vbr/13/rest/1.2-rev1/tag/Sessions
- Commvault equivalent: jobSummary (jobId, status, jobType, backupLevelName, …). [secondary]
  https://raw.githubusercontent.com/Commvault/cvpysdk/master/cvpysdk/job.py

**Generator:** a sessions page `{"data": [...], "pagination": {...}}`; per-object task fields
flattened onto each session (objectName, repositoryName, sourceRepositoryName) because the
model has one job per protected object; `isStorageSnapshot` carried on the session.

## R15. Pure Storage FlashArray REST 2.x (storage_telemetry)
- Pod replica links: id, direction, lag (ms), local_pod, remote_pod, remotes [{name,
  resource_type}], paused, recovery_point (ms; null while baselining), status replicating |
  baselining | paused | quiescing | quiesced | idle | unhealthy. [verified: OpenAPI models]
  https://raw.githubusercontent.com/PureStorage-OpenConnect/py-pure-client/master/pypureclient/flasharray/_common/models/pod_replica_link_v_2_52.py

**Generator:** hourly `pod-replica-links` samples for async pairs (ActiveDR-style) and `pods`
samples for synchronous room A/B mirrors (ActiveCluster); `_polled_array` / `_sampled_at`
record which array was read and when (collector metadata).

## R16. Microsoft Entra ID sign-ins (identity_sso)
- Graph `signIn`: id, createdDateTime, userPrincipalName, userId, appDisplayName, appId,
  ipAddress, clientAppUsed, conditionalAccessStatus, isInteractive, resourceDisplayName,
  status {errorCode, failureReason, additionalDetails}, deviceDetail {deviceId, displayName,
  operatingSystem, browser}, location {city, state, countryOrRegion}; error 50126 = invalid
  credentials. [verified]
  https://learn.microsoft.com/en-us/graph/api/resources/signin?view=graph-rest-1.0 ,
  https://learn.microsoft.com/en-us/entra/identity-platform/reference-error-codes

**Generator:** those fields with `userPrincipalName` = the bare pseudonymous ID (no domain,
no `userDisplayName`); ~1% failed sign-ins (50126) followed by a success.

## R17. SQL Server Audit (db_audit)
- `sys.fn_get_audit_file` columns (event_time UTC, sequence_number, action_id, succeeded,
  session_id, server_principal_name, database_principal_name, server_instance_name,
  database_name, schema_name, object_name, statement, class_type, client_ip,
  application_name, …); action_id codes SL, IN, UP, DL, EX, AL, CR, DR, LGIS, LGIF;
  class_type U (table). [verified columns; codes secondary]
  https://learn.microsoft.com/en-us/sql/relational-databases/system-functions/sys-fn-get-audit-file-transact-sql?view=sql-server-ver16 ,
  https://raw.githubusercontent.com/elastic/integrations/main/packages/microsoft_sqlserver/data_stream/audit/elasticsearch/ingest_pipeline/default.yml

**Generator:** a CSV of those columns (7 fractional digits), principals are pseudonymous IDs;
IRIS / Oracle / PostgreSQL audit exports are mapped into the same columns.

## R18. Workday worker export (hr_feed)
- Get_Workers: Worker_Reference (Employee_ID), status derived from the Active, Terminated and
  On_Leave flags (a worker on leave is still Active), Hire_Date, Termination_Date; RaaS
  column names are report-defined. [verified Get_Workers doc; RaaS names unverified]
  https://community.workday.com/sites/default/files/file-hosting/productionapi/Human_Resources/v45.0/Get_Workers.html

**Generator:** RaaS-style CSV with Employee_ID, Worker_Type, Worker_Status, the three flags,
Hire_Date, Termination_Date, Effective_Date, Supervisory_Organization; no worker names or
emails (DATA_SPEC privacy rule).

## R19. OpenRouter free models (LLM provider)

Checked 2026-10-16 against the live API and the docs.

- **API.** OpenAI-compatible base URL `https://openrouter.ai/api/v1`, `POST /chat/completions`,
  `Authorization: Bearer <OPENROUTER_API_KEY>`; optional attribution headers `HTTP-Referer` and
  `X-OpenRouter-Title`. https://openrouter.ai/docs/quickstart
- **Models API.** `GET /api/v1/models` (no key needed) returns `data[]` with `id`, `context_length`,
  `pricing.prompt` / `pricing.completion` as decimal strings (USD per token) and `supported_parameters`, which
  contains `tools` (and often `tool_choice`) for tool-calling models. Live call on 2026-10-16: 463 models, 14
  free tool-capable ones (`:free` suffix, both prices `"0"`), contexts 65,536 to 1,048,576.
  https://openrouter.ai/docs/guides/community/for-providers.md (pricing fields are strings; feature list
  includes `tools`) and the live response.
- **Fallbacks.** `models: [...]` is "an array of model IDs in priority order"; by default any error can trigger a
  fallback, including rate limiting and downtime; the response's `model` field names the model that answered and
  billing follows it. https://openrouter.ai/docs/guides/routing/model-fallbacks . The page sets no limit for
  `models`, but the live API answered HTTP 400 "'models' array must have 3 items or fewer." to a five-model
  list (2026-10-16), so at most 3 are sent.
- **Free limits.** Free variants (`:free`): 20 requests per minute; 50 per day with less than 10 credits
  purchased, 1,000 per day with at least 10. https://openrouter.ai/docs/api-reference/limits . The page does not
  say whether failed requests count; the owner's brief says they do, so the budget counts every attempt.
- **Key info.** `GET /api/v1/key` returns `data.limit`, `limit_remaining`, `usage`, `usage_daily`,
  `is_free_tier` and `free_model_daily_requests {used, limit, remaining}` for the current UTC day; the old
  `rate_limit` object is deprecated. https://openrouter.ai/docs/api-reference/limits . Live call: `is_free_tier:
  true`, `free_model_daily_requests: {used: 0, limit: 50, remaining: 50}`.
- **Errors.** Shape `{"error": {"code", "message", "metadata?"}}`; 429 = rate limited; 429 and 503 may carry
  `Retry-After` (seconds). A non-streaming 200 can contain only an `error` object and no `choices`, so the body
  must be checked. https://openrouter.ai/docs/api-reference/errors . Observed live: a 429 "Provider returned
  error" when the upstream free provider was saturated, even with fallbacks.
- **Privacy.** "If you opt out of training in your account settings, OpenRouter will not route to providers that
  train", with "separate settings for paid and free models"; OpenRouter works with providers to avoid training
  "but there are exceptions". https://openrouter.ai/docs/guides/privacy/provider-logging ; OpenRouter does not log
  prompts by default: https://openrouter.ai/support . Third-party survey of free endpoint retention (unverified,
  context only): https://www.truefoundry.com/blog/openrouter-free-models-whats-actually-free-and-what-it-costs-you
