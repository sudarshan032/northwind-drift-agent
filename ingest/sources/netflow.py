"""netflow: IPFIX / NetFlow v9 collector, nfdump aggregated CSV output.

Raw (data/raw/netflow/nfdump_hourly.csv, nfdump_daily.csv): one aggregated
record per conversation direction per hour (demo sites and central systems)
or per day (elsewhere), in the nfdump 1.7 `-o csv` layout: firstSeen,
duration, proto (6 = TCP), srcAddr, srcPort, dstAddr, dstPort, packets,
bytes, flows; plus three collector enrichment columns: srcDns / dstDns
(reverse-DNS names) and tag (application classification; `monitoring-probe`
for health checks). Times are UTC.

Connector: the server side is the endpoint on a well-known or registered
service port (ingest.config.SERVICE_PORTS); an ephemeral port
(>= EPHEMERAL_PORT_MIN) marks the client. The server endpoint resolves to the
application/database listening on ip:port (discovery data), else to the host.
Evidence is aggregated per (client, server, server port): bytes, packets,
days_seen, first/last seen. Probe-tagged records are excluded.
"""

import os

from ingest.config import EPHEMERAL_PORT_MIN, SERVICE_PORTS
from datetime import timedelta

from ingest.sources.common import parse_ts, raw_dir, read_csv, write_csv

COLUMNS = ["firstSeen", "duration", "proto", "srcAddr", "srcPort", "dstAddr", "dstPort", "packets", "bytes", "flows",
           "srcDns", "dstDns", "tag"]


def fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S.000")


def row(r, rev=False):
    a, b = ("da", "sa") if rev else ("sa", "da")
    pa, pb = ("dp", "sp") if rev else ("sp", "dp")
    nb = max(64, r["bytes"] // 20) if rev else r["bytes"]
    return {"firstSeen": fmt(r["start"]), "duration": f"{(r['end'] - r['start']).total_seconds():.3f}", "proto": 6,
            "srcAddr": r[a], "srcPort": r[pa], "dstAddr": r[b], "dstPort": r[pb],
            "packets": max(1, r["pkts"] // 2) if rev else r["pkts"], "bytes": nb, "flows": 1,
            "srcDns": r[f"{a}_dns"], "dstDns": r[f"{b}_dns"], "tag": r.get("tag", "")}


def write(records, out):
    """records: dicts with granularity, start, end, sa, da, sp, dp, pkts, bytes, sa_dns, da_dns, tag."""
    d = raw_dir(out, "netflow")
    n = {}
    for gran in ("hourly", "daily"):
        rows = []
        for r in records:
            if r["granularity"] != gran:
                continue
            if r.get("reverse"):  # server -> client direction of the same conversation (~5% of the bytes)
                rows.append(row(r, rev=True))
            rows.append(row(r))
        write_csv(os.path.join(d, f"nfdump_{gran}.csv"), rows, COLUMNS)
        n[gran] = len(rows)
    return n


def server_side(sp, dp):
    """'dst' or 'src' (which endpoint is the server), by service-port rule."""
    if dp in SERVICE_PORTS and sp >= EPHEMERAL_PORT_MIN:
        return "dst"
    if sp in SERVICE_PORTS and dp >= EPHEMERAL_PORT_MIN:
        return "src"
    if dp in SERVICE_PORTS:
        return "dst"
    if sp in SERVICE_PORTS:
        return "src"
    return "dst" if dp < sp else "src"


def read(data, ident):
    d = os.path.join(data, "raw", "netflow")
    agg = {}
    for gran in ("hourly", "daily"):
        for i, r in enumerate(read_csv(os.path.join(d, f"nfdump_{gran}.csv"))):
            if r["tag"] == "monitoring-probe":
                continue
            sp, dp = int(r["srcPort"]), int(r["dstPort"])
            if server_side(sp, dp) == "dst":
                c_ip, c_dns, s_ip, s_dns, port = r["srcAddr"], r["srcDns"], r["dstAddr"], r["dstDns"], dp
            else:
                c_ip, c_dns, s_ip, s_dns, port = r["dstAddr"], r["dstDns"], r["srcAddr"], r["srcDns"], sp
            first = r["firstSeen"][:19]
            last = (parse_ts(first) + timedelta(seconds=float(r["duration"]))).strftime("%Y-%m-%d %H:%M:%S")
            key = (c_ip, s_ip, port)
            e = agg.setdefault(key, {"bytes": 0, "packets": 0, "days": set(), "first": first, "last": last,
                                     "c_dns": c_dns, "s_dns": s_dns, "record": f"nfdump_{gran}.csv#{i + 2}"})
            e["bytes"] += int(r["bytes"])
            e["packets"] += int(r["packets"])
            e["days"].add(first[:10])
            e["first"], e["last"] = min(e["first"], first), max(e["last"], last)
    rows = []
    for (c_ip, s_ip, port), e in sorted(agg.items()):
        c_ci, c_rule = ident.match(name=e["c_dns"], ip=c_ip)
        s_ci, s_rule = ident.match_service(s_ip, port, name=e["s_dns"])
        rows.append({"source": "netflow", "source_record": e["record"], "client": c_ci, "client_name": e["c_dns"],
                     "client_ip": c_ip, "client_rule": c_rule, "server": s_ci, "server_name": e["s_dns"],
                     "server_ip": s_ip, "server_rule": s_rule, "port": port, "bytes": e["bytes"],
                     "packets": e["packets"], "days": sorted(e["days"]),
                     "first_seen": e["first"].replace(" ", "T")[:19] + "Z",
                     "last_seen": e["last"].replace(" ", "T")[:19] + "Z"})
    return rows
