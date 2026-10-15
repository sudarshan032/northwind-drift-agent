"""Every detection threshold in one place (REALISM_SPEC 4). The README lists them."""

SNAPSHOT_END = "2026-10-14T23:59:59Z"       # end of the observation window (replay data: never "now")
WINDOW_DAYS = 14                             # observed evidence window
MIN_DAYS_UNDOCUMENTED = 3                    # D1 / D14: traffic on >= 3 distinct UTC days
BACKUP_MAX_AGE_DAYS = 7                      # D10: no successful backup within 7 days
BACKUP_CUTOFF = "2026-10-07T23:59:59Z"       # SNAPSHOT_END - BACKUP_MAX_AGE_DAYS
TRANSIENT_FAILURE_HOURS = 48                 # a Failed job followed by Success within 48 h is noise
OWNERSHIP_WINDOW_DAYS = 90                   # D5: ITSM history window for the observed owner
RPO_BREACH_MIN_HOURS = 1                     # replication lag above RPO for longer than this is evidence
EPHEMERAL_PORT_MIN = 49152                   # IANA dynamic/private range: client side of a flow
# Well-known / registered service ports: the endpoint on one of these is the server side.
SERVICE_PORTS = {104: "dicom", 443: "https", 587: "smtp", 631: "ipp", 636: "ldaps", 1433: "mssql",
                 1521: "oracle", 1972: "iris", 2049: "ddboost", 2575: "hl7", 5000: "astm", 5432: "postgres",
                 6514: "syslog-tls", 22: "sftp", 88: "kerberos"}
# Fire-and-forget telemetry sinks: calls into them carry no impact (a SIEM outage does not break SSO).
TELEMETRY_SINKS = ("siem-app",)
# CMDB write-back boundary (REALISM_SPEC 2): the only CI attributes a finding may patch.
CMDB_PATCH_FIELDS = {"os_version", "serial_number", "ip_address", "support_group", "status"}
