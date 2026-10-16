"""Agent scenarios (AGENT_V2_SPEC 6): five investigations with a symptom (or a change) and the known answer,
built on drifts already in the catalogue (no new drift, no changed counts). Written next to ground_truth.json as
scenarios.json; agent.eval scores the live agent against them.

root_cause_any: the answer is right if the verdict names one of these components as the cause (root_cause field,
or the reasons when the cause is a relationship). risk_ok: accepted risk levels. warn: teams that must be warned.
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
]
