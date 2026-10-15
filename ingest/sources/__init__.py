"""Discovery sources (REALISM_SPEC 3). Each module writes its tool's native
raw format (`write`, generator side, uses the truth model) and parses it back
into normalised evidence (`read`, connector side, raw files only).

SOURCES describes each source for the README and the UI's Sources tab.
"""

SOURCES = [
    {"name": "cmdb_export", "tool": "ServiceNow CMDB table export",
     "reveals": "Declared CIs, attributes, relationships, locations, departments, groups, changes",
     "raw": "CSV list exports; references as sys_ids (cmdb_rel_ci parent/child/type)", "window": "current state",
     "dir": "cmdb_export"},
    {"name": "discovery_scanner", "tool": "ServiceNow Discovery / BMC Helix Discovery",
     "reveals": "Devices that exist (serial, hostname, IP, OS) and services listening on host ports",
     "raw": "IRE payload JSON {items[className, values], relations} per scan batch + scan schedule",
     "window": "last scan per subnet", "dir": "discovery_scanner"},
    {"name": "apm_otel", "tool": "OpenTelemetry Collector servicegraph connector -> Prometheus",
     "reveals": "Application-to-application and application-to-database calls, errors, latency",
     "raw": "Prometheus text exposition scrapes (traces_service_graph_*) + OTLP/JSON span sample",
     "window": "14 days", "dir": "apm_otel"},
    {"name": "netflow", "tool": "IPFIX / NetFlow v9 collector (nfdump aggregated CSV)",
     "reveals": "Network conversations, including un-instrumented devices (modalities, analysers, pumps)",
     "raw": "nfdump -o csv aggregated records (ts, te, sa, da, sp, dp, pr, ipkt, ibyt)", "window": "14 days",
     "dir": "netflow"},
    {"name": "interface_engine", "tool": "HL7 interface engine (Mirth Connect / NextGen Connect)",
     "reveals": "Each HL7/DICOM route's real destinations, including database writers, and message volumes",
     "raw": "Mirth channel export XML + daily channel/connector statistics CSV", "window": "14 days",
     "dir": "interface_engine"},
    {"name": "backup_software", "tool": "Backup & recovery software (Veeam job session report)",
     "reveals": "Which objects are really backed up, copied off-site or to the immutable vault, and results",
     "raw": "Job session CSV (job, type, object, start, end, result, repository, bytes)", "window": "14 days",
     "dir": "backup_software"},
    {"name": "storage_telemetry", "tool": "Array management REST API (Pure Storage FlashArray)",
     "reveals": "Array replication pairs: mode, lag, last transfer, state", "raw": "Protection group / replica link JSON samples (hourly)",
     "window": "14 days", "dir": "storage_telemetry"},
    {"name": "identity_sso", "tool": "Microsoft Entra ID sign-in logs",
     "reveals": "Which users sign in to which applications, from which workstation", "raw": "Graph signIn JSON lines",
     "window": "14 days", "dir": "identity_sso"},
    {"name": "db_audit", "tool": "SQL Server Audit (sys.fn_get_audit_file)",
     "reveals": "Who reads, writes and administers production databases", "raw": "Audit record CSV",
     "window": "14 days, demo sites and GCC-HYD", "dir": "db_audit"},
    {"name": "hr_feed", "tool": "HR worker export (Workday-style)", "reveals": "Employment status behind each account",
     "raw": "Worker CSV (Worker_ID, status, effective date)", "window": "current state", "dir": "hr_feed"},
    {"name": "itsm_history", "tool": "ServiceNow incident and change_request history",
     "reveals": "Which team really resolves incidents and implements changes per CI",
     "raw": "incident / change_request CSV exports", "window": "90 days", "dir": "itsm_history"},
]
NAMES = [s["name"] for s in SOURCES]
