"""Agent scenarios (AGENT_V2_SPEC 6): five investigations with a symptom (or a change) and the known answer,
built on drifts already in the catalogue (no new drift, no changed counts). Written next to ground_truth.json as
scenarios.json; agent.eval scores the live agent against them.

held_out: true marks scenarios added after the agent's tools and prompt were final, run once and never used to
tune anything (S1-S5 were used during development).

root_cause_any: the answer is right if the verdict names one of these components as the cause (root_cause field,
or the reasons when the cause is a relationship). risk_ok: accepted risk levels. warn: teams that must all be warned; warn_any: at least one of these.
"""

SCENARIOS = [
    {"id": "S1-headline", "kind": "change", "trigger": "CHG-HEADLINE", "drift": ["D1-HEADLINE"],
     "root_cause_any": ["lrc-db-01"], "risk_ok": ["high"], "warn": ["HYD-Integration-Support"],
     "answer": "Patching dc1-host-07 takes down lrc-db-01, where all 51 ORU-CRIT interfaces stage critical results "
               "(interface engine config, not in the CMDB): Emergency care at 51 hospitals. The observed owner of "
               "lrc-db-01 is HYD-Integration-Support, not NW-Lab-Systems."},
    {"id": "S2-backup", "kind": "incident", "drift": ["D10-01"],
     "trigger": "Radiology at H02 needs the PACS image cache database restored after a disk fault, but the backup "
                "console has shown a failed job for it every night this week.",
     "root_cause_any": ["h02-pacs-cache-db"], "risk_ok": ["high"], "warn": ["NW-DBA"],
     "answer": "h02-pacs-cache-db holds patient data and its backups have failed for more than 7 days, although the "
               "CMDB says it is backed up; NW-DBA owns it."},
    {"id": "S3-owner", "kind": "incident", "drift": ["D5-05"],
     "trigger": "Tickets about slow RIS database queries wait for days in the NW-Imaging-Systems queue, who say the "
                "database is not theirs; radiology worklists are late at several hospitals.",
     "root_cause_any": ["ris-db-01"], "risk_ok": ["medium", "high"], "warn": ["NW-DBA"],
     "answer": "ris-db-01 is recorded under NW-Imaging-Systems, but NW-DBA resolves its incidents and implements its "
               "changes: route the tickets to NW-DBA and fix the CMDB owner."},
    {"id": "S4-fhir-app", "kind": "incident", "drift": ["D14-01"],
     "trigger": "Security noticed an unfamiliar app reading patient records through the EMR's FHIR API, launched by "
                "cardiology users at H17.",
     "root_cause_any": ["cardiorisk.example.com"], "risk_ok": ["high"], "warn": ["NW-EMR-Apps"],
     "answer": "cardiorisk.example.com, an unregistered SMART on FHIR app used by H17 cardiology, reads EMR data via "
               "emr-fhir-api with no CMDB record or vendor review: a security matter for the emr-fhir-api owners."},
    {"id": "S5-stale", "kind": "change", "trigger": "CHG0031249", "drift": ["D2-01", "D2-04"],
     "root_cause_any": ["ris-app", "dwh-etl-app"], "risk_ok": ["low"], "warn": ["NW-DBA"],
     "answer": "The CMDB says ris-app and dwh-etl-app depend on sched-db-01, so its declared radius looks large, but "
               "neither link has traffic in 14 days (stale): the real impact is small and the risk low."},
    # held out (added 16 Oct after tuning; expected answers written before the run)
    {"id": "H1-protection", "kind": "incident", "drift": ["D9-02"], "held_out": True,
     "trigger": "A ransomware drill at H38 asks whether the PACS image cache database can be recovered from an "
                "off-site copy; the CMDB says it has one.",
     "root_cause_any": ["h38-pacs-cache-db"], "risk_ok": ["high"], "warn": [], "warn_any": ["NW-DBA"],
     "answer": "h38-pacs-cache-db holds patient data but is only protected by snapshots on arr-h38-01 at the same "
               "site; there is no off-site copy, although the CMDB says backup_offsite=true. NW-DBA owns it."},
    {"id": "H2-residency", "kind": "incident", "drift": ["D11-01"], "held_out": True,
     "trigger": "The privacy office asks whether an analytics extract with patient data is being copied to storage "
                "outside the United States.",
     "root_cause_any": ["dwh-db-01", "stor-cin-01"], "risk_ok": ["high"], "warn": [],
     "warn_any": ["NW-DBA", "NW-Cloud-Platform"],
     "answer": "An ad-hoc replication copies the PHI analytics volume of dwh-db-01 from stor-usw-01 to stor-cin-01 in "
               "cloud-central-india."},
    {"id": "H3-access", "kind": "incident", "drift": ["D12-01"], "held_out": True,
     "trigger": "Database audit shows admin queries on the EMR production database last night from an offshore support "
                "account, and no change was scheduled.",
     "root_cause_any": ["emr-db-01"], "risk_ok": ["high"], "warn": [],
     "warn_any": ["NW-DBA", "NW-SecOps", "HYD-Cybersecurity"],
     "answer": "A GCC-HYD clinical-apps support account ran admin queries on emr-db-01 outside any approved change "
               "window (access drift)."},
    {"id": "H4-shadow", "kind": "incident", "drift": ["D6-10"], "held_out": True,
     "trigger": "A network scan at H03 found an unknown remote-access gateway next to the imaging network.",
     "root_cause_any": ["VENDOR-RAS-GW"], "risk_ok": ["high", "medium"], "warn": [],
     "warn_any": ["NW-Site-IT-H03", "NW-SecOps"],
     "answer": "VENDOR-RAS-GW, a remote service gateway at H03 (10.103.8.241), is on the network with no CMDB record "
               "(shadow IT)."},
]
