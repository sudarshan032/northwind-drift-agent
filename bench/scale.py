"""Scale generator (BENCH_SPEC 2): grow the Northwind Health world by adding regional divisions.

1. `base`: generate the seed-42 world with the project's generator, load it with the project's loader into
   the benchmark FalkorDB, and export every node (labels and the properties the impact rules and the radius
   query read) and every non-derived edge. The impact layer is not exported: it is rebuilt per size with
   the project's own rules (graph/impact.py).
2. `generate(size)`: start from the base, replace each endpoint-group CI by its individual endpoints (as a
   full CMDB holds them), then add regional divisions. A region is a copy of every hospital, clinic and
   non-acute site with everything that belongs to them (site CIs, departments, the per-hospital interfaces
   and service offerings, users, groups, discovery records), sharing the enterprise data centres, central
   applications, support groups and changes. Regions beyond the first also get a small regional data centre.
   The headline chain (dc1-host-07 -> dc1-vm-lrc01 -> lrc-db-01 -> every hospital's ORU-CRIT route) is
   central, so it is present at every size and grows with the number of hospitals.
   The 50k size is the base world itself (49,063 nodes, endpoint groups kept), i.e. today's demo world.

Output, streamed region by region: data/<size>/nodes.csv, edges.csv, meta.json. Deterministic: base order
plus fixed region numbering; no randomness.
"""

import csv
import gzip
import json
import os
import resource
import shutil
import sys
import time

from bench import config as C

REGIONAL_SITE_TYPES = {"large_hospital", "community_hospital", "critical_access_hospital", "clinic", "non_acute"}
NODE_PROPS = ["name", "id", "key", "number", "site", "type", "patient_safety_impact", "target", "department",
              "count", "status"]
NODE_COLUMNS = ["bid", "labels", "nkey"] + NODE_PROPS
EDGE_COLUMNS = ["src", "dst", "type", "failover", "health_check"]
KEY_PROP = {"Site": "id", "Department": "id", "User": "id", "Group": "id", "Role": "id", "Discovered": "key",
            "Change": "number"}  # everything else is keyed by name
BASE = os.path.join(C.DATA, "base.json.gz")


def primary(labels):
    return labels[0]


def key_of(labels, props):
    return props.get(KEY_PROP.get(labels[0], "name"))


# ---------------------------------------------------------------- base export (project generator + loader)

def build_base(falkor_db, graph="bench_base"):
    from ingest.generate import generate
    from ingest.load import Loader
    src = os.path.join(C.DATA, "base_src")
    shutil.rmtree(src, ignore_errors=True)
    t = time.perf_counter()
    generate(C.SEED, src, inject=True)
    if graph in falkor_db.list_graphs():
        falkor_db.select_graph(graph).delete()
    ld = Loader(src, graph, "localhost", C.FALKOR["port"])
    ld.load()
    g = falkor_db.select_graph(graph)
    cols = ", ".join(f"n.{p}" for p in NODE_PROPS)
    nodes = [{"iid": r[0], "labels": r[1], "props": {p: v for p, v in zip(NODE_PROPS, r[2:]) if v is not None}}
             for r in g.query(f"MATCH (n) RETURN id(n), labels(n), {cols} ORDER BY id(n)").result_set]
    edges = [list(r) for r in g.query(
        "MATCH (a)-[e]->(b) WHERE NOT type(e) STARTS WITH 'IMPACTS' AND NOT type(e) STARTS WITH 'AFFECTS' "
        "RETURN id(a), id(b), type(e), e.failover, e.health_check ORDER BY id(a), id(b), type(e)").result_set]
    g.delete()
    shutil.rmtree(src, ignore_errors=True)
    os.makedirs(C.DATA, exist_ok=True)
    with gzip.open(BASE, "wt", encoding="utf-8") as f:
        json.dump({"nodes": nodes, "edges": edges, "seconds": round(time.perf_counter() - t, 1)}, f)
    return len(nodes), len(edges)


# ---------------------------------------------------------------- scale-up

def expand_endpoints(nodes, edges):
    """Endpoint-group CIs -> one Endpoint CI per member (same site, department, switch, support group)."""
    out_nodes, members = [], {}
    next_iid = max(n["iid"] for n in nodes) + 1
    for n in nodes:
        if n["labels"] == ["Component", "EndpointGroup"]:
            ids = []
            for i in range(int(n["props"].get("count") or 1)):
                p = {k: v for k, v in n["props"].items() if k != "count"}
                p["name"] = f"{n['props']['name']}-{i + 1:03d}"
                out_nodes.append({"iid": next_iid, "labels": ["Component", "Endpoint"], "props": p})
                ids.append(next_iid)
                next_iid += 1
            members[n["iid"]] = ids
        else:
            out_nodes.append(n)
    out_edges = []
    for a, b, t, fo, hc in edges:
        for x in members.get(a, [a]):
            for y in members.get(b, [b]):
                out_edges.append([x, y, t, fo, hc])
    return out_nodes, out_edges


def classify(nodes, edges):
    """iid -> True for nodes that belong to a hospital, clinic or non-acute site (copied per region)."""
    by_iid = {n["iid"]: n for n in nodes}
    reg_sites = {n["props"]["id"] for n in nodes if n["labels"] == ["Site"]
                 and n["props"].get("type") in REGIONAL_SITE_TYPES}
    dept_site = {}
    for a, b, t, _, _ in edges:
        if t == "CONTAINS" and by_iid[a]["labels"] == ["Site"] and by_iid[b]["labels"] == ["Department"]:
            dept_site[by_iid[b]["props"]["id"]] = by_iid[a]["props"]["id"]
    regional = {}
    for n in nodes:
        lab, p = n["labels"][0], n["props"]
        if lab == "Site":
            r = p.get("id") in reg_sites
        elif lab == "Department":
            r = dept_site.get(p.get("id")) in reg_sites
        elif lab in ("Role", "SupportGroup", "Change", "UnregisteredEndpoint"):
            r = False
        else:  # site CIs, per-hospital routes (department) and offerings (site), users, groups, discovery
            r = p.get("site") in reg_sites or dept_site.get(p.get("department")) in reg_sites
        regional[n["iid"]] = r
    return regional, reg_sites, dept_site


def renamed(props, labels, k, reg_sites, dept_site):
    """Region k's copy of a regional node's properties (identifiers prefixed, central references kept)."""
    if k == 1:
        return dict(props)
    pre = f"r{k}-"
    p = dict(props)
    for f in ("name", "id", "key"):
        if f in p and isinstance(p[f], str):
            p[f] = pre + p[f]
    if p.get("site") in reg_sites:
        p["site"] = pre + p["site"]
    if p.get("department") is not None and dept_site.get(p["department"]) in reg_sites:
        p["department"] = pre + p["department"]
    return p


def regional_dc(k):
    """Nodes and edges (by local name) of region k's data centre: 4 servers, 8 VMs, an array, a backup target."""
    site = f"R{k}-RDC"
    ns = [(["Site"], {"id": site, "name": site, "type": "data_centre"})]
    es = []
    for h in range(1, 5):
        host = f"r{k}-rdc-host-{h:02d}"
        ns.append((["Component", "Host"], {"name": host, "site": site, "status": "operational",
                                           "patient_safety_impact": "medium"}))
        es.append((host, f"r{k}-rdc-arr-01", "STORES_ON"))
        for v in (1, 2):
            vm = f"r{k}-rdc-vm-{(h - 1) * 2 + v:02d}"
            ns.append((["Component", "VM"], {"name": vm, "site": site, "status": "operational"}))
            es.append((vm, host, "RUNS_ON"))
    ns.append((["Component", "StorageArray"], {"name": f"r{k}-rdc-arr-01", "site": site, "status": "operational"}))
    ns.append((["Component", "BackupTarget"], {"name": f"r{k}-rdc-bkp-01", "site": site, "status": "operational"}))
    return ns, es


def generate(size, out=None):
    out = out or os.path.join(C.DATA, str(size))
    os.makedirs(out, exist_ok=True)
    t0 = time.perf_counter()
    with gzip.open(BASE, "rt", encoding="utf-8") as f:
        base = json.load(f)
    nodes, edges = base["nodes"], base["edges"]
    expanded = size > len(nodes) * 1.5
    if expanded:
        nodes, edges = expand_endpoints(nodes, edges)
    regional, reg_sites, dept_site = classify(nodes, edges)
    n_reg = sum(regional.values())
    n_central = len(nodes) - n_reg
    rdc = len(regional_dc(2)[0]) if size >= 500_000 else 0
    regions = max(1, round((size - n_central + rdc) / (n_reg + rdc)))
    by_iid = {n["iid"]: n for n in nodes}
    bid_of = {}  # (iid, region) -> bid; central nodes use region 1
    bid = 0
    nf = open(os.path.join(out, "nodes.csv"), "w", newline="", encoding="utf-8")
    ef = open(os.path.join(out, "edges.csv"), "w", newline="", encoding="utf-8")
    nw, ew = csv.writer(nf), csv.writer(ef)
    nw.writerow(NODE_COLUMNS)
    ew.writerow(EDGE_COLUMNS)
    n_nodes = n_edges = 0
    sg = next(n["iid"] for n in nodes if n["labels"] == ["SupportGroup"] and n["props"]["name"] == "NW-Datacenter-Infra")

    def write_node(labels, props):
        nonlocal bid, n_nodes
        bid += 1
        nw.writerow([bid, ":".join(labels), key_of(labels, props)] + [props.get(p, "") for p in NODE_PROPS])
        n_nodes += 1
        return bid

    def write_edge(a, b, t, fo="", hc=""):
        nonlocal n_edges
        ew.writerow([a, b, t, "" if fo is None else fo, "" if hc is None else hc])
        n_edges += 1

    for k in range(1, regions + 1):
        local = {}
        for n in nodes:
            if k == 1 or regional[n["iid"]]:
                b = write_node(n["labels"], renamed(n["props"], n["labels"], k, reg_sites, dept_site))
                local[n["iid"]] = b
                if k == 1:
                    bid_of[n["iid"]] = b
        central = bid_of
        for a, b2, t, fo, hc in edges:
            if k > 1 and not (regional[a] or regional[b2]):
                continue  # central-to-central edges exist once
            write_edge(local.get(a, central[a]) if regional[a] or k == 1 else central[a],
                       local.get(b2, central[b2]) if regional[b2] or k == 1 else central[b2], t, fo, hc)
        if k > 1 and rdc:
            ns, es = regional_dc(k)
            names = {}
            for labels, props in ns:
                names[props.get("name")] = write_node(labels, props)
            for labels, props in ns:
                b = names[props["name"]]
                if labels[0] == "Component":
                    write_edge(b, names[f"R{k}-RDC"], "LOCATED_AT")
                    write_edge(b, central[sg], "MANAGED_BY")
            for x, y, t in es:
                write_edge(names[x], names[y], t)
            for n in nodes:  # the region's databases are backed up to its data centre
                if regional[n["iid"]] and n["labels"] == ["Component", "Database"]:
                    write_edge(local[n["iid"]], names[f"r{k}-rdc-bkp-01"], "BACKED_UP_BY")
        print(json.dumps({"region": k, "of": regions, "nodes": n_nodes, "edges": n_edges}), file=sys.stderr)
    nf.close()
    ef.close()
    labels = {}
    for n in nodes:
        labels[n["labels"][0]] = labels.get(n["labels"][0], 0) + 1
    meta = {"size": size, "nodes": n_nodes, "edges": n_edges, "regions": regions, "endpoints_expanded": expanded,
            "regional_nodes_per_region": n_reg, "central_nodes": n_central, "regional_dc_nodes": rdc,
            "hospitals": 51 * regions, "seconds": round(time.perf_counter() - t0, 1),
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,  # macOS: bytes
            "bytes_on_disk": sum(os.path.getsize(os.path.join(out, f)) for f in ("nodes.csv", "edges.csv")),
            "seed": C.SEED}
    with open(os.path.join(out, "meta.json"), "w") as f:
        json.dump(meta, f, indent=1)
    return meta


if __name__ == "__main__":
    print(json.dumps(generate(int(sys.argv[1])), indent=1))
