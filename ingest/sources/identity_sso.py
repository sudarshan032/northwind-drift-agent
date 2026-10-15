"""identity_sso: Microsoft Entra ID sign-in logs (Graph `signIn` resource).

Raw (data/raw/identity_sso/signins.jsonl): id, createdDateTime,
userPrincipalName (the pseudonymous worker ID, no domain), userId,
appDisplayName, appId, ipAddress, clientAppUsed, deviceDetail {deviceId,
displayName (workstation), operatingSystem, browser}, status {errorCode,
failureReason}, conditionalAccessStatus.

Connector: user -> application access edges from successful sign-ins
(errorCode 0): count, first/last seen, workstations.
"""

import os

from ingest.sources.common import raw_dir, read_jsonl, write_jsonl


def write(signins, out):
    return write_jsonl(os.path.join(raw_dir(out, "identity_sso"), "signins.jsonl"), signins)


def read(data, ident):
    agg = {}
    for i, s in enumerate(read_jsonl(os.path.join(data, "raw", "identity_sso", "signins.jsonl"))):
        if s["status"]["errorCode"] != 0:
            continue
        app, rule = ident.match(name=s["appDisplayName"])
        key = (s["userPrincipalName"], app or s["appDisplayName"])
        e = agg.setdefault(key, {"count": 0, "first": s["createdDateTime"], "last": s["createdDateTime"],
                                 "workstations": set(), "registered": app is not None, "rule": rule,
                                 "record": f"signins.jsonl#{i + 1} id={s['id']}"})
        e["count"] += 1
        e["first"], e["last"] = min(e["first"], s["createdDateTime"]), max(e["last"], s["createdDateTime"])
        e["workstations"].add(s["deviceDetail"]["displayName"])
    return [{"source": "identity_sso", "source_record": e["record"], "user": u, "target": t, "action": "login",
             "registered": e["registered"], "target_rule": e["rule"], "count": e["count"], "failures": 0,
             "first_seen": e["first"], "last_seen": e["last"], "workstations": sorted(e["workstations"]), "times": []}
            for (u, t), e in sorted(agg.items())]
