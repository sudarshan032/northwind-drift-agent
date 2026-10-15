"""apm_otel: OpenTelemetry Collector `servicegraph` connector scraped by Prometheus.

Raw (data/raw/apm_otel/):
- scrapes/hourly/<YYYY-MM-DDTHH>.prom and scrapes/daily/<YYYY-MM-DD>.prom:
  Prometheus text exposition (version 0.0.4) of the servicegraph metrics
  `traces_service_graph_request_total`, `traces_service_graph_request_failed_total`
  (counters) and `traces_service_graph_request_server_seconds` (histogram:
  _bucket{le}, _sum, _count), labels `client`, `server`, `connection_type`
  ("database" for calls to databases), sample timestamps in ms. Counters are
  cumulative from the collector start (window start). Hourly scrapes cover
  pairs touching a demo site or a central system, daily scrapes the rest
  (RUN_PROMPT 4.2). Histogram buckets follow a configured
  `latency_histogram_buckets: [10ms, 50ms, 100ms, 500ms, 1s, 5s]`.
- otlp_spans_sample.json: OTLP/JSON ExportTraceServiceRequest
  (resourceSpans -> scopeSpans -> spans) for demo-site services over two hours.

Connector: one evidence edge per (client, server): count = sum of counter
increases, errors from the failed counter, p95_ms by linear interpolation
inside the histogram bucket holding the 95th percentile, days_seen = distinct
UTC days with a non-zero increase.
"""

import json
import os
import re
from datetime import timedelta

from ingest.sources.common import iso, raw_dir

BUCKETS = [0.01, 0.05, 0.1, 0.5, 1.0, 5.0]  # seconds; +Inf added
SHAPE = [(0.4, 0.50), (0.7, 0.30), (1.0, 0.15), (2.0, 0.04), (4.0, 0.01)]  # (x p95, share of requests)
METRICS = [("traces_service_graph_request_total", "counter", "Total count of requests between two nodes"),
           ("traces_service_graph_request_failed_total", "counter", "Total count of failed requests between two nodes"),
           ("traces_service_graph_request_server_seconds", "histogram",
            "Time for a request between two nodes as seen from the server")]
LINE = re.compile(r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)\{(?P<labels>[^}]*)\} (?P<value>\S+)(?: (?P<ts>\d+))?$')
LABEL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


def split_latency(n, p95_ms):
    """Deterministic split of n requests over the buckets (+Inf), and their total seconds."""
    counts = [0] * (len(BUCKETS) + 1)
    total, assigned = 0.0, 0
    for i, (mult, share) in enumerate(SHAPE):
        k = round(n * share) if i < len(SHAPE) - 1 else n - assigned
        k = max(0, min(k, n - assigned))
        assigned += k
        sec = p95_ms * mult / 1000.0
        b = next((j for j, ub in enumerate(BUCKETS) if sec <= ub), len(BUCKETS))
        counts[b] += k
        total += k * sec
    return counts, total


def _labels(client, server, conn, failed="false"):
    """servicegraph labels; an empty label value is the same as no label in Prometheus, so it is left out."""
    ct = f',connection_type="{conn}"' if conn else ""
    return f'client="{client}"{ct},failed="{failed}",server="{server}"'


def write(series, out):
    """series: {(client, server, conn, granularity): [(t, n, errors, p95_ms), ...]} with t the
    period start (hour or day). Writes one exposition file per scrape."""
    d = raw_dir(out, "apm_otel")
    files = 0
    for gran in ("hourly", "daily"):
        sd = os.path.join(d, "scrapes", gran)
        os.makedirs(sd, exist_ok=True)
        keys = sorted(k for k in series if k[3] == gran)
        state = {k: [0, 0, [0] * (len(BUCKETS) + 1), 0.0] for k in keys}  # total, failed, buckets, sum
        by_t = {}
        for k in keys:
            for t, n, err, p95 in series[k]:
                by_t.setdefault(t, []).append((k, n, err, p95))
        seen = []
        for t in sorted(by_t):
            for k, n, err, p95 in by_t[t]:
                st = state[k]
                st[0] += n
                st[1] += err
                counts, total = split_latency(n, p95)
                st[2] = [a + b for a, b in zip(st[2], counts)]
                st[3] += total
                if k not in seen:
                    seen.append(k)
            step = timedelta(hours=1) if gran == "hourly" else timedelta(days=1)
            ts = int((t + step).timestamp() * 1000)  # scraped at the end of the period
            lines = []
            for name, typ, help_ in METRICS:
                lines += [f"# HELP {name} {help_}", f"# TYPE {name} {typ}"]
                for k in sorted(seen):
                    st = state[k]
                    lab = _labels(k[0], k[1], k[2], "true" if "failed" in name else "false")
                    if typ == "counter":
                        lines.append(f"{name}{{{lab}}} {st[0] if 'failed' not in name else st[1]} {ts}")
                    else:
                        cum = 0
                        for ub, c in zip([f"{b:g}" for b in BUCKETS] + ["+Inf"], st[2]):
                            cum += c
                            lines.append(f'{name}_bucket{{{lab},le="{ub}"}} {cum} {ts}')
                        lines.append(f"{name}_sum{{{lab}}} {st[3]:.3f} {ts}")
                        lines.append(f"{name}_count{{{lab}}} {st[0]} {ts}")
            stamp = t.strftime("%Y-%m-%dT%H") if gran == "hourly" else t.strftime("%Y-%m-%d")
            with open(os.path.join(sd, f"{stamp}.prom"), "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")
            files += 1
    return files


def write_spans(spans_by_pair, out, rng):
    """OTLP/JSON sample: spans_by_pair {(client, server, conn): [(start_dt, dur_ms, error), ...]}."""
    d = raw_dir(out, "apm_otel")

    def hexid(n):
        return "".join(rng.choice("0123456789abcdef") for _ in range(n))

    def attr(k, v):
        return {"key": k, "value": {"intValue": str(v)} if isinstance(v, int) else {"stringValue": v}}

    by_service = {}
    for (client, server, conn), calls in sorted(spans_by_pair.items()):
        for start, dur, err in calls:
            trace, cid = hexid(32), hexid(16)
            s0 = str(int(start.timestamp() * 1e9))
            s1 = str(int((start + timedelta(milliseconds=dur)).timestamp() * 1e9))
            status = {"code": 2} if err else {"code": 0}
            if conn == "database":
                by_service.setdefault(client, []).append({
                    "traceId": trace, "spanId": cid, "name": "SQL query", "kind": 3, "startTimeUnixNano": s0,
                    "endTimeUnixNano": s1, "attributes": [attr("db.system", "mssql" if not server.startswith(
                        ("emr-db", "portal-db", "dwh-db", "gcc-tools")) else "intersystems_cache"),
                        attr("db.name", server), attr("server.address", server)], "status": status})
            else:
                by_service.setdefault(client, []).append({
                    "traceId": trace, "spanId": cid, "name": f"call {server}", "kind": 3, "startTimeUnixNano": s0,
                    "endTimeUnixNano": s1, "attributes": [attr("server.address", server), attr("peer.service", server)],
                    "status": status})
                by_service.setdefault(server, []).append({
                    "traceId": trace, "spanId": hexid(16), "parentSpanId": cid, "name": f"handle {client}", "kind": 2,
                    "startTimeUnixNano": s0, "endTimeUnixNano": s1, "attributes": [], "status": status})
    doc = {"resourceSpans": [{"resource": {"attributes": [attr("service.name", svc),
                                                          attr("deployment.environment", "production")]},
                              "scopeSpans": [{"scope": {"name": "northwind.synthetic", "version": "1.0"},
                                              "spans": spans}]}
                             for svc, spans in sorted(by_service.items())]}
    with open(os.path.join(d, "otlp_spans_sample.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=1)
    return sum(len(s) for s in by_service.values())


def parse_exposition(text):
    """Minimal Prometheus text parser: [(name, labels dict, value, ts_ms)] (HELP/TYPE skipped)."""
    out = []
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = LINE.match(line)
        if not m:
            raise ValueError(f"bad exposition line: {line[:120]}")
        labels = dict(LABEL.findall(m.group("labels")))
        out.append((m.group("name"), labels, float(m.group("value")), int(m.group("ts") or 0)))
    return out


def p95_from_buckets(buckets):
    """buckets: [(upper_bound_s, cumulative_count)] sorted, last is +Inf. Linear interpolation."""
    total = buckets[-1][1]
    if total == 0:
        return 0.0
    target, prev_ub, prev_c = 0.95 * total, 0.0, 0
    for ub, c in buckets:
        if c >= target:
            if ub == float("inf"):
                return prev_ub * 1000.0
            frac = (target - prev_c) / (c - prev_c) if c > prev_c else 0
            return (prev_ub + frac * (ub - prev_ub)) * 1000.0
        prev_ub, prev_c = ub, c
    return prev_ub * 1000.0


def read(data, ident):
    """Scrapes -> one evidence row per (client, server)."""
    d = os.path.join(data, "raw", "apm_otel", "scrapes")
    edges = {}
    for gran in ("hourly", "daily"):
        last = {}
        for fn in sorted(os.listdir(os.path.join(d, gran))):
            with open(os.path.join(d, gran, fn), encoding="utf-8") as f:
                samples = parse_exposition(f.read())
            for name, lab, value, ts in samples:
                key = (lab["client"], lab["server"])
                e = edges.setdefault(key, {"count": 0, "errors": 0, "days": set(), "first": None, "last": None,
                                           "buckets": {}, "conn": lab.get("connection_type", ""),
                                           "record": f"{gran}/{fn}"})
                if name == "traces_service_graph_request_total":
                    inc = value - last.get((key, "t"), 0)
                    last[(key, "t")] = value
                    if inc > 0:
                        e["count"] += int(inc)
                        day = iso_day(ts, gran)
                        e["days"].add(day)
                        period = period_start(ts, gran)
                        e["first"] = min(e["first"] or period, period)
                        e["last"] = max(e["last"] or period, period)
                elif name == "traces_service_graph_request_failed_total":
                    inc = value - last.get((key, "f"), 0)
                    last[(key, "f")] = value
                    e["errors"] += int(max(inc, 0))
                elif name.endswith("_bucket"):
                    e["buckets"][lab["le"]] = value  # cumulative: the last scrape holds the window total
    rows = []
    for (client, server), e in sorted(edges.items()):
        if not e["count"]:
            continue
        b = sorted(((float("inf") if k == "+Inf" else float(k)), v) for k, v in e["buckets"].items())
        c_ci, c_rule = ident.match(name=client)
        s_ci, s_rule = ident.match(name=server)
        rows.append({"source": "apm_otel", "source_record": f"{e['record']} client={client} server={server}",
                     "client": c_ci, "client_name": client, "client_rule": c_rule, "server": s_ci,
                     "server_name": server, "server_rule": s_rule, "connection_type": e["conn"],
                     "count": e["count"], "errors": e["errors"], "p95_ms": round(p95_from_buckets(b), 1),
                     "days": sorted(e["days"]), "first_seen": e["first"], "last_seen": e["last"]})
    return rows


def iso_day(ts_ms, gran):
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) - (timedelta(hours=1) if gran == "hourly"
                                                                   else timedelta(days=1))
    return dt.strftime("%Y-%m-%d")


def period_start(ts_ms, gran):
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) - (timedelta(hours=1) if gran == "hourly"
                                                                   else timedelta(days=1))
    return iso(dt)


def spans_to_edges(data):
    """Standard rule on the OTLP sample: CLIENT span whose child SERVER span is in another
    service -> edge; CLIENT span with db.system / server.address -> edge to that database/host.
    Returns {(client, server, hour): count}."""
    with open(os.path.join(data, "raw", "apm_otel", "otlp_spans_sample.json"), encoding="utf-8") as f:
        doc = json.load(f)
    service_of, spans = {}, []
    for rs in doc["resourceSpans"]:
        svc = next(a["value"]["stringValue"] for a in rs["resource"]["attributes"] if a["key"] == "service.name")
        for ss in rs["scopeSpans"]:
            for s in ss["spans"]:
                service_of[s["spanId"]] = svc
                spans.append((svc, s))
    children = {}
    for svc, s in spans:
        if s.get("parentSpanId"):
            children.setdefault(s["parentSpanId"], []).append((svc, s))
    out = {}
    from datetime import datetime, timezone
    for svc, s in spans:
        if s["kind"] != 3:
            continue
        attrs = {a["key"]: next(iter(a["value"].values())) for a in s["attributes"]}
        server = next((csvc for csvc, c in children.get(s["spanId"], []) if c["kind"] == 2 and csvc != svc), None)
        if server is None and ("db.system" in attrs or "server.address" in attrs):
            server = attrs.get("db.name") or attrs["server.address"]
        if server:
            hour = datetime.fromtimestamp(int(s["startTimeUnixNano"]) / 1e9, tz=timezone.utc).strftime("%Y-%m-%dT%H")
            out[(svc, server, hour)] = out.get((svc, server, hour), 0) + 1
    return out
