"""Load a generated size into PostgreSQL: node and edge tables via COPY, the exact impact edges FalkorDB
materialised (impact_edges.csv), the indexes BENCH_SPEC 3 lists, then VACUUM ANALYZE."""

import os
import time

from bench import config as C
from bench.infra import pg_dsn

DDL = """
DROP SCHEMA IF EXISTS {s} CASCADE;
CREATE SCHEMA {s};
CREATE TABLE {s}.node (id bigint, name text, kind text, label text, site text, type text,
                       patient_safety_impact text, target text, department text, status text);
CREATE TABLE {s}.edge (src bigint, dst bigint, type text, health_check boolean);
"""
INDEXES = [
    "CREATE UNIQUE INDEX ON {s}.node (id)",
    "CREATE INDEX ON {s}.node (name)",
    "CREATE INDEX ON {s}.edge (src)",
    "CREATE INDEX ON {s}.edge (dst)",
    "CREATE INDEX ON {s}.edge (src, type)",
    "CREATE INDEX ON {s}.edge (dst, type)",
]


def schema(size):
    return f"s{size}"


def connect(autocommit=True):
    import psycopg  # bench dependency group only
    return psycopg.connect(pg_dsn(), autocommit=autocommit)


def _copy(cur, sql, path, convert):
    with open(path, encoding="utf-8") as f, cur.copy(sql) as cp:
        next(f)
        import csv
        for row in csv.reader(f):
            cp.write_row(convert(row))


def load(size, log=print):
    d = os.path.join(C.DATA, str(size))
    s = schema(size)
    t0 = time.perf_counter()
    with connect() as c, c.cursor() as cur:
        cur.execute(DDL.format(s=s))
        # nodes.csv: bid, labels, key, name, id, key, number, site, type, safety, target, department, count, status
        def node(r):
            labels = r[1].split(":")
            return (int(r[0]), r[2], labels[0], labels[-1], r[7] or None, r[8] or None, r[9] or None,
                    r[10] or None, r[11] or None, r[13] or None)
        _copy(cur, f"COPY {s}.node (id, name, kind, label, site, type, patient_safety_impact, target, department, "
                   "status) FROM STDIN", os.path.join(d, "nodes.csv"), node)
        nodes_s = time.perf_counter() - t0
        _copy(cur, f"COPY {s}.edge (src, dst, type, health_check) FROM STDIN", os.path.join(d, "edges.csv"),
              lambda r: (int(r[0]), int(r[1]), r[2], None if r[4] == "" else r[4] == "True"))
        _copy(cur, f"COPY {s}.edge (src, dst, type) FROM STDIN", os.path.join(d, "impact_edges.csv"),
              lambda r: (int(r[0]), int(r[1]), r[2]))
        edges_s = time.perf_counter() - t0 - nodes_s
        t1 = time.perf_counter()
        for q in INDEXES:
            cur.execute(q.format(s=s))
        cur.execute(f"VACUUM ANALYZE {s}.node")
        cur.execute(f"VACUUM ANALYZE {s}.edge")
        idx_s = time.perf_counter() - t1
        n = cur.execute(f"SELECT count(*) FROM {s}.node").fetchone()[0]
        m = cur.execute(f"SELECT count(*) FROM {s}.edge").fetchone()[0]
    return {"nodes": n, "edges_including_impact": m, "seconds": {
        "nodes_copy": round(nodes_s, 1), "edges_copy": round(edges_s, 1), "indexes_analyze": round(idx_s, 1),
        "load_total": round(time.perf_counter() - t0, 1)}}


def loaded(size):
    """Node count of the loaded schema; 0 when absent or from before the health_check column."""
    with connect() as c:
        r = c.execute("SELECT to_regclass(%s)", (f"{schema(size)}.node",)).fetchone()[0]
        col = c.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = %s AND table_name = 'edge' "
                        "AND column_name = 'health_check'", (schema(size),)).fetchone()
        return c.execute(f"SELECT count(*) FROM {schema(size)}.node").fetchone()[0] if r and col else 0


def disk_bytes(size):
    with connect() as c:
        return int(c.execute("SELECT sum(pg_total_relation_size(c.oid)) FROM pg_class c JOIN pg_namespace n "
                         "ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind IN ('r')",
                         (schema(size),)).fetchone()[0] or 0)


def drop(size):
    with connect() as c:
        c.execute(f"DROP SCHEMA IF EXISTS {schema(size)} CASCADE")
