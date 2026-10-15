"""storage_telemetry: array management REST API (Pure Storage FlashArray, REST 2.x).

Raw (data/raw/storage_telemetry/replication_samples.jsonl): hourly samples, one
per replicated volume group: async replication as `pod-replica-links`
records (id, direction, local_pod, remote_pod, remotes [{name, resource_type:
arrays}], paused, status replicating | baselining | idle | paused |
unhealthy, lag in ms, recovery_point in epoch ms; `_polled_array` is the
array the sample was read from), synchronous mirroring as `pods` records
(ActiveCluster: arrays with status, lag 0).

Connector, per replication pair: observed mode, max_lag_s, last_transfer,
state, volumes; and lag above the RPO of the CIs stored on the source array
for longer than RPO_BREACH_MIN_HOURS (evidence for the protection check).
"""

import os
from datetime import datetime, timezone

from ingest.config import RPO_BREACH_MIN_HOURS
from ingest.sources.common import iso, raw_dir, read_jsonl, write_jsonl


def write(samples, out):
    d = raw_dir(out, "storage_telemetry")
    return write_jsonl(os.path.join(d, "replication_samples.jsonl"), samples)


def read(data, ident, rpo_by_array):
    """rpo_by_array: {array: min RPO minutes of PHI/data CIs stored on it} (from the CMDB export)."""
    agg = {}
    for i, s in enumerate(read_jsonl(os.path.join(data, "raw", "storage_telemetry", "replication_samples.jsonl"))):
        if s["endpoint"] == "pods":  # ActiveCluster: synchronous, both arrays online
            src_name, tgt_name = s["arrays"][0]["name"], s["arrays"][1]["name"]
            mode, lag_s, state = "sync", 0, "in_sync" if all(a["status"] == "online" for a in s["arrays"]) else "degraded"
            vol, t = s["name"], s["_sampled_at"]
        else:
            src_name, tgt_name = s["_polled_array"], s["remotes"][0]["name"]
            mode, lag_s, state = "async", s["lag"] / 1000.0, s["status"]
            vol, t = s["local_pod"]["name"], s["_sampled_at"]
        src, _ = ident.match(name=src_name)
        tgt, _ = ident.match(name=tgt_name)
        e = agg.setdefault((src, tgt), {"mode": mode, "max_lag_s": 0, "last": t, "state": state, "volumes": set(),
                                        "hours_over": {}, "record": f"replication_samples.jsonl#{i + 1}"})
        e["max_lag_s"] = max(e["max_lag_s"], lag_s)
        if t >= e["last"]:
            e["last"], e["state"] = t, state
        e["volumes"].add(vol)
        rpo = rpo_by_array.get(src)
        if rpo and lag_s > rpo * 60:
            e["hours_over"].setdefault(vol, []).append(t)
    rows = []
    for (src, tgt), e in sorted(agg.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        breaches = []
        for v, ts in sorted(e["hours_over"].items()):  # continuous runs of hourly samples above the RPO
            run = [ts[0]]
            for t in sorted(ts)[1:] + [None]:
                if t is not None and _hours(run[-1], t) == 1:
                    run.append(t)
                    continue
                if len(run) > RPO_BREACH_MIN_HOURS:
                    breaches.append({"volume": v, "hours": len(run), "first": run[0]})
                run = [t] if t else []
        rows.append({"source": "storage_telemetry", "source_record": e["record"], "source_array": src,
                     "target_array": tgt, "mode": e["mode"], "max_lag_s": round(e["max_lag_s"]),
                     "last_transfer": e["last"], "state": e["state"], "volumes": sorted(e["volumes"]),
                     "rpo_breaches": breaches})
    return rows


def _hours(a, b):
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    return round((datetime.strptime(b, fmt) - datetime.strptime(a, fmt)).total_seconds() / 3600)


def ms(dt):
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000) if dt.tzinfo is None else int(dt.timestamp() * 1000)


def sample(t, vol, src, tgt, mode, lag_s, state):
    """One raw record in the array API's shape."""
    if mode == "sync":
        return {"_sampled_at": iso(t), "endpoint": "pods", "name": f"pod-{vol}", "arrays": [
            {"name": src, "status": "online", "frozen_at": None}, {"name": tgt, "status": "online", "frozen_at": None}],
            "promotion_status": "promoted", "requested_promotion_state": "promoted"}
    return {"_sampled_at": iso(t), "_polled_array": src, "endpoint": "pod-replica-links",
            "id": f"{vol}:{tgt}", "direction": "outbound", "local_pod": {"id": vol, "name": f"pod-{vol}"},
            "remote_pod": {"id": f"{vol}-dr", "name": f"pod-{vol}"}, "remotes": [{"id": tgt, "name": tgt,
                                                                               "resource_type": "arrays"}],
            "paused": False, "status": state, "lag": int(lag_s * 1000), "recovery_point": ms(t) - int(lag_s * 1000)}


__all__ = ["write", "read", "sample", "datetime"]
