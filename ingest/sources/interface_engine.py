"""interface_engine: HL7/DICOM interface engine (Mirth Connect / NextGen Connect).

Raw (data/raw/interface_engine/):
- channels.xml: channel export (<list><channel>...), one channel per route.
  <sourceConnector> with transportName "TCP Listener" (MLLP) or "DICOM
  Listener" and its listener port; <destinationConnectors><connector> with
  transportName "TCP Sender" (remoteAddress, remotePort), "Database Writer"
  (driver, JDBC url) or "DICOM Sender" (host, port). Property class names
  follow Mirth's connector classes.
- channel_statistics.csv: per channel, per connector, per day: received,
  filtered, sent, errored, queued, plus the channel's default SOURCE metadata
  column (the sending application, from MSH-3) for the source connector.

Connector: each channel = the Interface CI of the same name. Each destination
connector = an evidence edge route -> destination: TCP/DICOM sender host:port
and Database Writer JDBC host:port resolve through identification (the
database listening on that port), so `ORU-CRIT-* -> lrc-db-01` is discovered
from the Database Writer URL. Statistics give count (sent), errors (errored)
and days_seen; the source connector's statistics give the inbound edge from
the sending system.
"""

import os
import re
import xml.etree.ElementTree as ET
from xml.dom import minidom

from ingest.sources.common import raw_dir, read_csv, write_csv

PROPS = {"TCP Listener": "com.mirth.connect.connectors.tcp.TcpReceiverProperties",
         "DICOM Listener": "com.mirth.connect.connectors.dimse.DICOMReceiverProperties",
         "TCP Sender": "com.mirth.connect.connectors.tcp.TcpDispatcherProperties",
         "DICOM Sender": "com.mirth.connect.connectors.dimse.DICOMDispatcherProperties",
         "Database Writer": "com.mirth.connect.connectors.jdbc.DatabaseDispatcherProperties"}
JDBC = re.compile(r"^jdbc:(?P<kind>\w+)://(?P<host>[^:;/]+)(?::(?P<port>\d+))?")
STAT_COLUMNS = ["serverId", "channelId", "channel", "metaDataId", "connector", "connector_type", "date", "SOURCE",
                "received", "filtered", "sent", "error", "queued"]  # ChannelStatistics fields + SOURCE metadata


def write(channels, stats, out):
    """channels: [{name, id, description, source: {type, port}, destinations: [{name, type, host, port, url,
    database}]}]; stats: rows with STAT_COLUMNS."""
    d = raw_dir(out, "interface_engine")
    root = ET.Element("list")
    for c in channels:
        ch = ET.SubElement(root, "channel", version="4.5.0")
        ET.SubElement(ch, "id").text = c["id"]
        ET.SubElement(ch, "name").text = c["name"]
        ET.SubElement(ch, "description").text = c["description"]
        src = ET.SubElement(ch, "sourceConnector", version="4.5.0")
        ET.SubElement(src, "metaDataId").text = "0"
        ET.SubElement(src, "name").text = "sourceConnector"
        p = ET.SubElement(src, "properties", {"class": PROPS[c["source"]["type"]], "version": "4.5.0"})
        lp = ET.SubElement(p, "listenerConnectorProperties", version="4.5.0")
        ET.SubElement(lp, "host").text = "0.0.0.0"
        ET.SubElement(lp, "port").text = str(c["source"]["port"])
        ET.SubElement(src, "transportName").text = c["source"]["type"]
        ET.SubElement(src, "mode").text = "SOURCE"
        dests = ET.SubElement(ch, "destinationConnectors")
        for i, dst in enumerate(c["destinations"], 1):
            con = ET.SubElement(dests, "connector", version="4.5.0")
            ET.SubElement(con, "metaDataId").text = str(i)
            ET.SubElement(con, "name").text = dst["name"]
            p = ET.SubElement(con, "properties", {"class": PROPS[dst["type"]], "version": "4.5.0"})
            if dst["type"] == "Database Writer":
                ET.SubElement(p, "driver").text = "com.microsoft.sqlserver.jdbc.SQLServerDriver"
                ET.SubElement(p, "url").text = dst["url"]
                ET.SubElement(p, "query").text = "INSERT INTO critical_result_stage (msg_id, mrn_hash, obx) VALUES (?, ?, ?)"
            elif dst["type"] == "TCP Sender":
                ET.SubElement(p, "remoteAddress").text = dst["host"]
                ET.SubElement(p, "remotePort").text = str(dst["port"])
            else:  # DICOM Sender
                ET.SubElement(p, "host").text = dst["host"]
                ET.SubElement(p, "port").text = str(dst["port"])
            ET.SubElement(con, "transportName").text = dst["type"]
            ET.SubElement(con, "mode").text = "DESTINATION"
            ET.SubElement(con, "enabled").text = "true"
        md = ET.SubElement(ch, "properties", version="4.5.0")
        cols = ET.SubElement(md, "metaDataColumns")
        for name, mapping in (("SOURCE", "mirth_source"), ("TYPE", "mirth_type")):
            mc = ET.SubElement(cols, "metaDataColumn")
            ET.SubElement(mc, "name").text = name
            ET.SubElement(mc, "type").text = "STRING"
            ET.SubElement(mc, "mappingName").text = mapping
    xml = minidom.parseString(ET.tostring(root, encoding="unicode")).toprettyxml(indent="  ")
    with open(os.path.join(d, "channels.xml"), "w", encoding="utf-8", newline="\n") as f:
        f.write(xml)
    write_csv(os.path.join(d, "channel_statistics.csv"), stats, STAT_COLUMNS)
    return len(channels)


def read(data, ident):
    d = os.path.join(data, "raw", "interface_engine")
    tree = ET.parse(os.path.join(d, "channels.xml"))
    dest_of = {}  # (channel, connector name) -> (ci, rule, endpoint text)
    for ch in tree.getroot().findall("channel"):
        name = ch.findtext("name")
        for con in ch.find("destinationConnectors").findall("connector"):
            t, p = con.findtext("transportName"), con.find("properties")
            if t == "Database Writer":
                m = JDBC.match(p.findtext("url"))
                host, port, rule_how = m.group("host"), int(m.group("port") or 1433), "jdbc_url"
                endpoint = p.findtext("url")
            elif t == "TCP Sender":
                host, port, rule_how = p.findtext("remoteAddress"), int(p.findtext("remotePort")), "tcp_sender"
                endpoint = f"{host}:{port}"
            else:
                host, port, rule_how = p.findtext("host"), int(p.findtext("port")), "dicom_sender"
                endpoint = f"{host}:{port}"
            host_ci, _ = ident.match(name=host)
            ip = ident_ip(ident, host_ci)
            ci, rule = ident.match_service(ip, port, name=host) if ip else ident.match(name=host)
            dest_of[(name, con.findtext("name"))] = (ci, f"{rule_how}+{rule}" if rule else None, endpoint, t)
    edges = {}
    for i, s in enumerate(read_csv(os.path.join(d, "channel_statistics.csv"))):
        route, rci_rule = ident.match(name=s["channel"])
        if s["connector"] == "sourceConnector":
            if not s["SOURCE"]:
                continue  # many anonymous senders (e.g. modalities): no single inbound edge
            sender, srule = ident.match(name=s["SOURCE"])
            key = (sender, route, "inbound", s["SOURCE"])
            n, err = int(s["received"]), 0
            rules, record = (srule, rci_rule), f"channel_statistics.csv#{i + 2} {s['channel']} SOURCE={s['SOURCE']}"
            endpoint, ctype = s["SOURCE"], s["connector_type"]
        else:
            dst, drule, endpoint, ctype = dest_of[(s["channel"], s["connector"])]
            key = (route, dst, "outbound", endpoint)
            n, err = int(s["sent"]), int(s["error"])
            rules, record = (rci_rule, drule), f"channels.xml {s['channel']}/{s['connector']}"
        e = edges.setdefault(key, {"count": 0, "errors": 0, "days": set(), "rules": rules, "record": record,
                                   "endpoint": endpoint, "type": ctype})
        e["count"] += n
        e["errors"] += err
        if n:
            e["days"].add(s["date"])
    rows = []
    for (client, server, direction, endpoint), e in sorted(edges.items(), key=lambda kv: [str(x) for x in kv[0]]):
        if not e["count"]:
            continue
        days = sorted(e["days"])
        rows.append({"source": "interface_engine", "source_record": e["record"], "client": client,
                     "client_name": endpoint if direction == "inbound" else client, "client_rule": e["rules"][0],
                     "server": server, "server_name": endpoint if direction == "outbound" else server,
                     "server_rule": e["rules"][1], "direction": direction, "connector_type": e["type"],
                     "endpoint": endpoint, "count": e["count"], "errors": e["errors"], "days": days,
                     "first_seen": f"{days[0]}T00:00:00Z", "last_seen": f"{days[-1]}T23:59:59Z"})
    return rows


def ident_ip(ident, ci):
    """IP of an identified host CI (reverse of the identifier's IP index)."""
    if ci is None:
        return None
    return next((ip for ip, n in ident.by_ip.items() if n == ci), None)
