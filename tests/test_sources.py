"""REALISM_SPEC 8: every connector parses its tool's raw format, produces the
expected evidence for hand-checked records, and records how it identified CIs."""

import csv
import json
import os
import re
import xml.etree.ElementTree as ET

import pytest
from prometheus_client.parser import text_string_to_metric_families

from ingest.sources import apm_otel, cmdb_export, interface_engine
from ingest.sources.common import read_csv, read_jsonl

HEX32 = re.compile(r"^[0-9a-f]{32}$")


def norm(data, f):
    return list(read_jsonl(os.path.join(data, "normalised", f)))


@pytest.fixture(scope="module")
def gt(drift_data):
    with open(os.path.join(drift_data, "ground_truth.json")) as f:
        return json.load(f)


# ---- cmdb_export -------------------------------------------------------

def test_cmdb_export_references_are_sys_ids_and_round_trip(drift_data, tmp_path):
    d = os.path.join(drift_data, "raw", "cmdb_export")
    rels = read_csv(os.path.join(d, "cmdb_rel_ci.csv"))
    types = {r["sys_id"]: r["name"] for r in read_csv(os.path.join(d, "cmdb_rel_type.csv"))}
    cis = {r["sys_id"] for r in read_csv(os.path.join(d, "cmdb_ci.csv"))}
    assert all(HEX32.match(r["parent"]) and r["parent"] in cis and r["child"] in cis and r["type"] in types
               for r in rels)
    assert types["1a9cb166f1571100a92eb60da2bce5c5"] == "Depends on::Used by"  # OOB sys_id
    ci = read_csv(os.path.join(d, "cmdb_ci.csv"))
    assert {r["install_status"] for r in ci} == {"1", "7"} and "sys_class_name" in ci[0]
    assert {r["sys_class_name"] for r in ci if r["name"].startswith("svc-h0")} == {"service_offering"}
    # writing what the connector read reproduces the export byte for byte
    os.makedirs(tmp_path / "raw")
    cmdb_export.write(cmdb_export.read(drift_data), str(tmp_path))
    for f in os.listdir(d):
        with open(os.path.join(d, f), "rb") as a, open(tmp_path / "raw" / "cmdb_export" / f, "rb") as b:
            assert a.read() == b.read(), f


# ---- discovery_scanner -------------------------------------------------

def test_ire_payload_and_identification(drift_data, gt):
    d = os.path.join(drift_data, "raw", "discovery_scanner")
    with open(os.path.join(d, "ire_batch_0001.json")) as f:
        payload = json.load(f)
    assert set(payload) == {"items", "relations"}
    assert all({"className", "values", "sys_object_source_info"} <= set(i) for i in payload["items"])
    assert all(isinstance(r["parent"], int) and isinstance(r["child"], int) for r in payload["relations"])
    disc = {r["hostname"]: r for r in norm(drift_data, "discovered.jsonl")}
    assert disc["LIS-PROD-02.nw.local"]["ci"] == "dc1-vm-lis02"
    assert disc["LIS-PROD-02.nw.local"]["match_rule"] == "serial_number"  # D13: renamed, same serial
    assert disc["raspberrypi"]["ci"] is None  # D6 shadow IT candidate
    serial_drift = [e["ci_ids"][0] for e in gt if e["type"] == "D8" and e["finding_keys"][0][2] == "serial_number"]
    assert all(next(r for r in disc.values() if r["ci"] == ci)["match_rule"] == "name" for ci in serial_drift)
    assert disc["h03-ct-01"]["source"] == "discovery_scanner" and disc["h03-ct-01"]["source_record"].startswith("ire_batch_")
    listening = {(r["ci"], r["port"]) for r in norm(drift_data, "listening.jsonl")}
    assert ("lrc-db-01", 1433) in listening and ("h03-pacs-cache", 104) in listening


# ---- apm_otel ----------------------------------------------------------

def test_prometheus_text_parses_with_standard_parser(drift_data):
    d = os.path.join(drift_data, "raw", "apm_otel", "scrapes", "hourly")
    with open(os.path.join(d, sorted(os.listdir(d))[-1])) as f:
        text = f.read()
    fams = {m.name: m for m in text_string_to_metric_families(text)}
    assert {"traces_service_graph_request", "traces_service_graph_request_failed",
            "traces_service_graph_request_server_seconds"} <= set(fams)
    assert fams["traces_service_graph_request_server_seconds"].type == "histogram"
    sample = fams["traces_service_graph_request"].samples[0]
    assert {"client", "server", "failed"} <= set(sample.labels) and sample.timestamp is not None


def test_apm_counts_errors_p95_days(drift_data):
    calls = {(r["client"], r["server"]): r for r in norm(drift_data, "calls.jsonl") if r["source"] == "apm_otel"}
    e = calls[("emr-app", "emr-db-01")]
    d = os.path.join(drift_data, "raw", "apm_otel", "scrapes", "hourly")
    with open(os.path.join(d, sorted(os.listdir(d))[-1])) as f:
        last = {(lab["client"], lab["server"], n): v for n, lab, v, _ in apm_otel.parse_exposition(f.read())}
    assert e["count"] == last[("emr-app", "emr-db-01", "traces_service_graph_request_total")]  # cumulative counter
    assert e["errors"] == last[("emr-app", "emr-db-01", "traces_service_graph_request_failed_total")]
    assert len(e["days"]) == 14 and e["client_rule"] == e["server_rule"] == "name" and e["connection_type"] == "database"
    assert 0 < e["p95_ms"] < 50  # superserver latency ~6 ms
    # linear interpolation inside the bucket that holds the 95th percentile
    assert apm_otel.p95_from_buckets([(0.01, 50), (0.05, 90), (0.1, 100), (float("inf"), 100)]) == pytest.approx(75.0)


def test_otlp_json_mapping_and_agreement_with_metrics(drift_data):
    with open(os.path.join(drift_data, "raw", "apm_otel", "otlp_spans_sample.json")) as f:
        doc = json.load(f)
    span = doc["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    assert set(doc) == {"resourceSpans"} and {"traceId", "spanId", "name", "kind", "startTimeUnixNano",
                                               "endTimeUnixNano", "attributes", "status"} <= set(span)
    assert re.fullmatch(r"[0-9a-f]{32}", span["traceId"]) and re.fullmatch(r"[0-9a-f]{16}", span["spanId"])
    assert isinstance(span["kind"], int) and isinstance(span["startTimeUnixNano"], str)
    assert all(set(a["value"]) <= {"stringValue", "intValue"} for a in span["attributes"])
    from_spans = apm_otel.spans_to_edges(drift_data)
    assert len(from_spans) > 20
    d = os.path.join(drift_data, "raw", "apm_otel", "scrapes", "hourly")
    hours = sorted({h for _, _, h in from_spans})
    for h in hours:  # metric increase over each sampled hour == spans seen in that hour
        def totals(fname):
            with open(os.path.join(d, fname)) as f:
                return {(lab["client"], lab["server"]): v for n, lab, v, _ in apm_otel.parse_exposition(f.read())
                        if n == "traces_service_graph_request_total"}
        prev_file = sorted(x for x in os.listdir(d) if x < f"{h}.prom")[-1]
        now, before = totals(f"{h}.prom"), totals(prev_file)
        for (c, s, hh), n in from_spans.items():
            if hh == h:
                assert now[(c, s)] - before.get((c, s), 0) == n, (c, s, h)


# ---- netflow -----------------------------------------------------------

def test_netflow_server_side_by_port_and_aliases(drift_data):
    with open(os.path.join(drift_data, "raw", "netflow", "nfdump_hourly.csv")) as f:
        header = next(csv.reader(f))
    assert header[:10] == ["firstSeen", "duration", "proto", "srcAddr", "srcPort", "dstAddr", "dstPort", "packets",
                           "bytes", "flows"]
    rows = norm(drift_data, "netflow.jsonl")
    mri = next(r for r in rows if r["client"] == "h03-mri-01" and r["server"] == "h03-pacs-cache")
    assert mri["port"] == 104 and mri["server_rule"] == "ip_port" and mri["client_rule"] == "name"
    assert not [r for r in rows if r["server"] == "h03-mri-01"]  # both directions folded into one client->server edge
    ct = next(r for r in rows if r["client"] == "h03-ct-02")
    assert ct["client_name"] == "H03CT2" and ct["client_rule"] == "ip_address"  # D13: DICOM AE title in reverse DNS
    assert not [r for r in rows if r["client"] and r["client"].startswith("usw-vm-mon")]  # probes excluded
    d14 = [r for r in rows if r["client"] is None]
    assert {r["client_name"] for r in d14} == {"cardiorisk.example.com"} and {r["server"] for r in d14} == \
        {"emr-fhir-api", "sso-app"}


# ---- interface_engine --------------------------------------------------

def test_oru_crit_database_writer_resolves_to_lrc_db(drift_data):
    root = ET.parse(os.path.join(drift_data, "raw", "interface_engine", "channels.xml")).getroot()
    crit = [c for c in root.findall("channel") if c.findtext("name").startswith("ORU-CRIT-")]
    assert len(crit) == 51
    for c in crit:
        dbw = [k for k in c.find("destinationConnectors") if k.findtext("transportName") == "Database Writer"]
        assert len(dbw) == 1 and interface_engine.JDBC.match(dbw[0].find("properties").findtext("url"))
        assert dbw[0].find("properties").get("class").endswith("DatabaseDispatcherProperties")
    edges = [r for r in norm(drift_data, "calls.jsonl") if r["source"] == "interface_engine"
             and r["server"] == "lrc-db-01"]
    assert {r["client"] for r in edges} == {c.findtext("name") for c in crit}
    assert all(r["server_rule"] == "jdbc_url+ip_port" and r["connector_type"] == "Database Writer" for r in edges)
    rels = read_csv(os.path.join(drift_data, "normalised", "cmdb_rel_ci.csv"))
    assert not [r for r in rels if r["child"] == "lrc-db-01" and r["parent"].startswith("ORU-CRIT-")]
    inbound = next(r for r in norm(drift_data, "calls.jsonl") if r["source"] == "interface_engine"
                   and r["server"] == "ORU-CRIT-H03" and r["direction"] == "inbound")
    assert inbound["client"] == "lis-app"  # from the channel's SOURCE metadata


# ---- backup_software / storage_telemetry --------------------------------

def test_backups_snapshots_and_copies(drift_data, gt):
    rows = norm(drift_data, "backups.jsonl")
    d9 = {e["ci_ids"][0] for e in gt if e["type"] == "D9"}
    for obj in d9:
        mine = [r for r in rows if r["object"] == obj]
        assert mine and all(r["job_type"] == "snapshot" and r["last_success"] is None for r in mine)
    emr = {r["target"]: r for r in rows if r["object"] == "emr-db-01"}
    assert set(emr) == {"bkp-appl-pri-01", "bkp-appl-dr-01", "bkp-vault-usw-01"}
    assert emr["bkp-vault-usw-01"]["job_type"] == "backup_copy" and emr["bkp-appl-pri-01"]["last_success"]
    with open(os.path.join(drift_data, "raw", "backup_software", "sessions.json")) as f:
        s = json.load(f)["data"][0]
    assert {"id", "name", "jobId", "sessionType", "creationTime", "endTime", "state", "result"} <= set(s)
    repl = {(r["source_array"], r["target_array"]): r for r in norm(drift_data, "replication.jsonl")}
    assert repl[("bkp-appl-pri-01", "bkp-vault-usw-01")]["source"] == "backup_software"
    assert repl[("arr-pri-a-01", "arr-pri-b-01")]["mode"] == "sync" and repl[("arr-pri-a-01", "arr-dr-01")]["mode"] == "async"
    assert repl[("arr-pri-a-01", "arr-dr-01")]["rpo_breaches"] == []  # single-hour spikes are not a breach


# ---- identity_sso / db_audit / hr_feed / itsm_history -------------------

def test_identity_sources(drift_data, gt):
    raw = list(read_jsonl(os.path.join(drift_data, "raw", "identity_sso", "signins.jsonl")))
    assert all("@" not in s["userPrincipalName"] for s in raw)  # bare pseudonymous IDs, no email/domain
    assert any(s["status"]["errorCode"] == 50126 for s in raw)  # failed sign-ins exist ...
    access = norm(drift_data, "access.jsonl")
    assert sum(a["count"] for a in access if a["source"] == "identity_sso") == \
        sum(1 for s in raw if s["status"]["errorCode"] == 0)  # ... and only successes become access evidence
    d12a = next(e for e in gt if e["type"] == "D12" and e["ci_ids"] == ["emr-db-01"])["finding_keys"][0][1]
    adm = next(a for a in access if a["user"] == d12a and a["target"] == "emr-db-01" and a["action"] == "admin")
    assert adm["source"] == "db_audit" and len(adm["times"]) == 4
    with open(os.path.join(drift_data, "raw", "hr_feed", "workers.csv")) as f:
        header = next(csv.reader(f))
    assert not {"Worker", "Email_-_Primary_Work"} & set(header)  # no personal data
    workers = read_csv(os.path.join(drift_data, "normalised", "workers.csv"))
    with open(os.path.join(drift_data, "truth", "world.json")) as f:
        disabled = sum(1 for u in json.load(f)["identity"]["users"] if u["status"] != "active")
    assert sum(1 for w in workers if w["status"] == "terminated") == disabled


def test_itsm_owner_window(drift_data, gt):
    owners = norm(drift_data, "owners.jsonl")
    assert all(o["period_days"] == 90 and o["last_activity"] >= "2026-07-16" for o in owners)
    lrc = sorted((o for o in owners if o["ci"] == "lrc-db-01"), key=lambda o: -(o["incidents_resolved"] +
                                                                                 o["changes_implemented"]))
    assert lrc[0]["team"] == "HYD-Integration-Support"  # D5: the real owner


def test_every_evidence_row_has_source(drift_data):
    names = {"discovery_scanner", "apm_otel", "netflow", "interface_engine", "backup_software", "storage_telemetry",
             "identity_sso", "db_audit", "hr_feed", "itsm_history"}
    for f in ("discovered.jsonl", "calls.jsonl", "netflow.jsonl", "backups.jsonl", "replication.jsonl",
              "access.jsonl", "owners.jsonl"):
        for r in norm(drift_data, f):
            assert r["source"] in names and r["source_record"], (f, r)
