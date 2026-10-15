"""Write VERIFY.md (AGENT_SPEC phase 0.3) from a loaded graph, the data
directory and a saved `pytest -v` log.

    python -m tests.verify_report --data data --graph northwind --pytest-log x.txt --out VERIFY.md
"""

import argparse
import json
import os
from collections import Counter

from falkordb import FalkorDB

from graph import queries as Q
from ingest.load import Loader
from tests import oracle


def build(data, graph, log, out):
    host, port = os.environ.get("FALKORDB_HOST", "localhost"), int(os.environ.get("FALKORDB_PORT", 6379))
    g = FalkorDB(host=host, port=port).select_graph(graph)
    ld = Loader(data, graph, host, port)
    counts = ld.counts()
    with open(log) as f:
        lines = f.read().splitlines()
    tests = [x.split(" PASSED")[0].split(" FAILED")[0] + (" PASSED" if "PASSED" in x else " FAILED")
             for x in lines if " PASSED" in x or " FAILED" in x]
    summary = [x for x in lines if " passed" in x or " failed" in x][-1].strip("= ")
    dec, real = Q.radius(g, "dc1-host-07", "declared"), Q.radius(g, "dc1-host-07", "real")
    routes = real["by_label"].get("Interface", [])
    p_svc = Q.impact_path(g, "dc1-host-07", "svc-emergency-care")
    p_ed = Q.impact_path(g, "dc1-host-07", "H17-ED", via_routes=routes)
    p_dec = Q.impact_path(g, "dc1-host-07", "svc-lab-results", layer="declared")
    d14 = Q.unregistered_app_path(g, "cardiorisk.example.com")
    gt, must, must_not, aliases = oracle.expected(data)
    res = oracle.check(data)
    kind_of = {}
    for e in gt:
        for k in e["finding_keys"]:
            kind_of[tuple(k)] = e["type"]
    found = Counter(kind_of.get(f, "UNEXPECTED") for f in res["findings"])
    entries = Counter(e["type"] for e in gt)
    keys = Counter(kind_of[tuple(k)] for e in gt if e["expected_finding"] not in ("none", "resolve_as_same_ci")
                   for k in e["finding_keys"])
    exact = res["findings"] == must and not (must_not & res["findings"]) and res["aliases"] == aliases
    checks = [
        ("Full test suite passes", "failed" not in summary and "error" not in summary),
        ("Oracle findings == ground_truth.json keys (D3 silent, D13 resolved)", exact),
        ("Declared radius of dc1-host-07: lab reporting only, no Emergency care",
         dec["business_services"] == ["svc-lab-results"] and "svc-emergency-care" not in dec["business_services"]),
        ("Real radius: 51 ORU-CRIT routes, 51 hospitals, Emergency care",
         real["counts"]["routes"] == 51 and real["counts"]["hospitals"] == 51
         and "svc-emergency-care" in real["business_services"]),
        ("D14 path group -> users -> app -> emr-fhir-api -> emr-db-01",
         bool(d14) and d14[0]["api"] == "emr-fhir-api" and "emr-db-01" in d14[0]["data"]),
        ("Discovered without IDENTIFIES == D6 count",
         g.query("MATCH (d:Discovered) WHERE NOT (d)-[:IDENTIFIES]->(:Component) RETURN count(d)").result_set[0][0]
         == entries["D6"]),
    ]
    md = ["# VERIFY: Phase 0 data verification", "",
          f"Data: `{data}` (fresh regenerate, SEED=42); graph `{graph}` (fresh load).", "",
          "## Checks", "", "| Check | Result |", "|---|---|"]
    md += [f"| {c} | {'PASS' if ok else 'FAIL'} |" for c, ok in checks]
    md += ["", f"**All checks passing: {'yes' if all(ok for _, ok in checks) else 'NO'}**", "",
           "## Test output", "", "```", *tests, summary, "```", "",
           "## Graph size", "", f"Total nodes **{counts['total_nodes']:,}**, total edges **{counts['total_edges']:,}**.",
           "", "| Node label | Count |", "|---|---|"]
    md += [f"| {k} | {v:,} |" for k, v in counts["nodes_by_label"].items()]
    md += ["", "| Edge type | Count |", "|---|---|"]
    md += [f"| {k} | {v:,} |" for k, v in counts["edges_by_type"].items()]
    md += ["", "## Blast radius of `dc1-host-07` (target of CHG-HEADLINE)", "",
           "| | Declared (CMDB) | Real (declared structure + observed) |", "|---|---|---|"]
    for k in ("routes", "applications", "departments", "hospitals", "business_services", "components"):
        md.append(f"| {k.replace('_', ' ')} | {dec['counts'][k]} | {real['counts'][k]} |")
    md += ["", f"- Declared business services: {', '.join(dec['business_services'])}; applications: "
           f"{', '.join(dec['by_label'].get('Application', []))}.",
           f"- Real high-safety business services: {len(real['high_safety_services'])} "
           f"(incl. `svc-emergency-care`); data consumer affected: {', '.join(real['consumers'])}.",
           f"- Declared path: `{' -> '.join(p_dec['nodes'])}` ({', '.join(p_dec['rules'])})",
           f"- Real path to Emergency care: `{' -> '.join(p_svc['nodes'])}` ({', '.join(p_svc['rules'])})",
           f"- Real path to the H17 Emergency department: `{' -> '.join(p_ed['nodes'])}` ({', '.join(p_ed['rules'])})",
           "", "## D14 path (unregistered SMART on FHIR app)", "", "```",
           "(:Group)<-[:MEMBER_OF]-(:User)-[:OBSERVED_ACCESS]->(:UnregisteredEndpoint)"
           "-[:OBSERVED_CALLS]->(:Component)-[:OBSERVED_CALLS]->(:Database)", json.dumps(d14, indent=1), "```", "",
           "## Findings by drift type", "",
           "| Type | Ground-truth entries | Expected finding keys | Oracle findings |", "|---|---|---|---|"]
    for t in sorted(entries, key=lambda x: int(x[1:].split("-")[0])):
        exp = "none (must stay silent)" if t == "D3" else "aliases" if t == "D13" else keys[t]
        got = (f"{len(must_not & res['findings'])} flagged" if t == "D3" else
               f"{len(res['aliases'])} aliases resolved" if t == "D13" else found[t])
        md.append(f"| {t} | {entries[t]} | {exp} | {got} |")
    md += ["", f"Total findings: {len(res['findings'])}; unexpected: {found['UNEXPECTED']}.", ""]
    with open(out, "w") as f:
        f.write("\n".join(md))
    return all(ok for _, ok in checks)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data")
    ap.add_argument("--graph", default="northwind")
    ap.add_argument("--pytest-log", required=True)
    ap.add_argument("--out", default="VERIFY.md")
    a = ap.parse_args()
    ok = build(a.data, a.graph, a.pytest_log, a.out)
    print("all checks passing:", ok)
    raise SystemExit(0 if ok else 1)
