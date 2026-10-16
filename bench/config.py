"""Benchmark settings, logged with every result (BENCH_SPEC 3 and 5)."""

import os

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, "data")          # generated graphs (gitignored)
RESULTS = os.path.join(ROOT, "results")    # JSON lines per (size, system, benchmark)
LOGS = os.path.join(ROOT, "logs")
STATUS = os.path.join(ROOT, "STATUS.json")

SEED = 42
PILOT = 250_000
SIZES = [50_000, 500_000, 3_500_000]       # 5M does not fit the 4 GB container with a 25% margin (DECISIONS #89)

# both systems get the same limits (docker run --memory/--cpus)
MEMORY = os.environ.get("BENCH_MEMORY", "4g")
CPUS = os.environ.get("BENCH_CPUS", "4")

FALKOR = {"image": "falkordb/falkordb:v4.22.0", "container": "bench-falkordb", "port": 6390,
          "volume": "bench_falkordb_data",
          # no query timeout for loading and long traversals; one worker thread per CPU given
          "args": f"TIMEOUT_MAX 0 TIMEOUT_DEFAULT 0 THREAD_COUNT {CPUS} MAX_QUEUED_QUERIES 1000"}
PG = {"image": "postgres:18.6", "container": "bench-postgres", "port": 5440, "volume": "bench_pg_data",
      "user": "bench", "password": "bench", "db": "bench",
      # sized for a 4 GB container: 25% shared_buffers, cache estimate ~75%, small per-sort memory
      # (100 concurrent clients), SSD costs, JIT off (it only adds compile time to short recursive queries)
      "settings": {"shared_buffers": "1GB", "effective_cache_size": "3GB", "work_mem": "32MB",
                   "maintenance_work_mem": "512MB", "max_connections": "250", "random_page_cost": "1.1",
                   "effective_io_concurrency": "200", "jit": "off", "max_wal_size": "4GB",
                   "checkpoint_timeout": "15min", "synchronous_commit": "off", "wal_level": "minimal",
                   "max_wal_senders": "0", "track_io_timing": "on",
                   # added for the server-time check (16 Oct): measures real prepared executions
                   "shared_preload_libraries": "pg_stat_statements", "pg_stat_statements.track": "all"}}

DEPTHS = [2, 4, 6, 10]
RUNS = 20
WARMUP = 3
CLIENTS = [10, 50, 100]
CONCURRENCY_SECONDS = 60
CONCURRENCY_DEPTH = 10
START_POINTS = ["dc1-host-07", "emr-app"]  # plus the busiest storage array, chosen from the base graph
