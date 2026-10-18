"""Self-healing for the public demo: FalkorDB Cloud's free tier has no persistence, so a restart can leave the graph
empty. `state` checks that the demo graph is fully loaded; `drive` reloads it from the bundled demo subset
(`data_demo/normalised` + `data_demo/identity`, the same load as deploy.load_cloud), one step at a time within a
time budget, so a reload that does not fit one function call carries on at the next call.

Lock and progress live in a separate graph (`<graph>_restore`, one `:Restore` node), because step 0 deletes the
demo graph. Claiming is one query (`until` is a lease), so only one instance reloads at a time; a crashed
instance's lease runs out after LEASE_S and the next caller resumes at the step it reached (every load step MERGEs).
The demo graph counts as loaded when it has `:Meta {key: 'loaded'}` (written as the last step) and at least the
bundled number of components.
"""

import json
import logging
import os
import time
import uuid

from ingest.load import Loader, read_csv

log = logging.getLogger("northwind.recover")

DATA = os.environ.get("NORTHWIND_DATA", "data_demo")
LEASE_S = 90
CRITICAL = os.path.join(os.path.dirname(__file__), "critical.json")  # deploy.critical's result for the demo subset
LOAD_STEPS = ("indexes", "reference", "components", "relationships", "changes", "identity", "discovery", "flows",
              "replication", "backups", "access", "evidence", "write_aliases", "impact_layers", "tag_layers")
STEPS = ("wipe",) + LOAD_STEPS + ("critical", "triage", "finish")
_expected = {}


def expected(data=DATA):
    """Components in the bundled demo subset (6,636)."""
    if data not in _expected:
        _expected[data] = sum(1 for _ in read_csv(os.path.join(data, "normalised", "cmdb_ci.csv")))
    return _expected[data]


def lock_name(graph):
    return f"{graph}_restore"


def state(db, graph, data=DATA):
    """'ok', 'restoring' (a reload is under way) or 'missing' (empty, partial or never marked as loaded)."""
    graphs = db.list_graphs()
    if lock_name(graph) in graphs:
        if db.select_graph(lock_name(graph)).query("MATCH (r:Restore) RETURN count(r)").result_set[0][0]:
            return "restoring"
    if graph not in graphs:
        return "missing"
    loaded, n = db.select_graph(graph).query(
        "OPTIONAL MATCH (m:Meta {key: 'loaded'}) WITH count(m) AS loaded "
        "OPTIONAL MATCH (c:Component) RETURN loaded, count(c)").result_set[0]
    return "ok" if loaded and n >= expected(data) else "missing"


def mark_loaded(g, data=DATA):
    g.query("MERGE (m:Meta {key: 'loaded'}) SET m.components = $n, m.at = $t", {"n": expected(data), "t": time.time()})


def ensure_memory(db, name):
    """The memory graph exists (empty after a reset or a restart); an index on :Ref creates it."""
    if name not in db.list_graphs():
        db.select_graph(name).query("CREATE INDEX FOR (r:Ref) ON (r.name)")


def _claim(db, graph, me):
    rows = db.select_graph(lock_name(graph)).query(
        "MERGE (r:Restore {key: 'demo'}) ON CREATE SET r.step = 0, r.until = 0.0, r.started = $now "
        "WITH r WHERE r.until < $now SET r.until = $now + $lease, r.owner = $me RETURN r.step, r.started",
        {"now": time.time(), "lease": LEASE_S, "me": me}).result_set
    return rows[0] if rows else None


def drive(db, graph, budget_s, triage, after, memory_graph, data=DATA):
    """Run reload steps until done or `budget_s` is used up. triage(g) builds the findings, after(g) resets the
    app's caches and data version. Returns None when the demo is fine, else a progress dict."""
    st = state(db, graph, data)
    if st == "ok":
        ensure_memory(db, memory_graph)
        return None
    me, t0 = uuid.uuid4().hex[:8], time.time()
    claim = _claim(db, graph, me)
    if claim is None:  # another instance holds the lease
        return {"status": "restoring", "by": "another instance"}
    step, started = claim
    lock = db.select_graph(lock_name(graph))
    if step == 0:
        log.warning("demo graph %s is %s: reloading from %s", graph, st, data)
    ld, first = None, step
    while step < len(STEPS):
        if step > first and time.time() - t0 > budget_s:  # at least one step per call
            lock.query("MATCH (r:Restore {owner: $me}) SET r.until = 0.0", {"me": me})  # next caller continues
            return {"status": "restoring", "step": step, "of": len(STEPS), "next": STEPS[step]}
        name = STEPS[step]
        t = time.perf_counter()
        if name == "wipe":
            for gname in (graph, memory_graph):
                if gname in db.list_graphs():
                    db.select_graph(gname).delete()
        elif name in LOAD_STEPS:
            if ld is None:
                ld = Loader(data, graph, db=db)
                ld.prepare()
            getattr(ld, name)()
        elif name == "critical":
            with open(CRITICAL, encoding="utf-8") as f:
                rows = json.load(f)
            db.select_graph(graph).query("MERGE (m:Meta {key: 'critical'}) SET m.json = $j", {"j": json.dumps(rows)})
        elif name == "triage":
            triage(db.select_graph(graph))
        elif name == "finish":
            g = db.select_graph(graph)
            mark_loaded(g, data)
            ensure_memory(db, memory_graph)
            after(g)
        log.warning("reload step %s done in %.1f s", name, time.perf_counter() - t)
        step += 1
        held = lock.query("MATCH (r:Restore {owner: $me}) SET r.step = $s, r.until = $u RETURN count(r)",
                          {"me": me, "s": step, "u": time.time() + LEASE_S}).result_set[0][0]
        if not held:  # the lease ran out during a step and another instance took over
            return {"status": "restoring", "by": "another instance"}
    lock.delete()
    return {"status": "restored", "seconds": round(time.time() - started, 1)}
