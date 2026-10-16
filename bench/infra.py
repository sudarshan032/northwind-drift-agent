"""Benchmark containers (same memory/CPU limits for both systems), resource sampling, hardware info."""

import json
import platform
import subprocess
import threading
import time

from bench import config as C


def sh(*cmd, check=True):
    return subprocess.run(cmd, capture_output=True, text=True, check=check).stdout.strip()


def running(name):
    return sh("docker", "ps", "-q", "-f", f"name=^{name}$", check=False) != ""


def start_falkordb():
    f = C.FALKOR
    if not running(f["container"]):
        sh("docker", "rm", "-f", f["container"], check=False)
        sh("docker", "run", "-d", "--name", f["container"], "--memory", C.MEMORY, "--memory-swap", C.MEMORY,
           "--cpus", C.CPUS, "-p", f"{f['port']}:6379", "-v", f"{f['volume']}:/var/lib/falkordb/data",
           "-e", f"FALKORDB_ARGS={f['args']}", f["image"])
    from falkordb import FalkorDB
    for _ in range(120):
        try:
            db = FalkorDB(host="localhost", port=f["port"], socket_timeout=None)
            db.connection.ping()
            db.connection.config_set("save", "")  # snapshots only when measuring disk size (no stalls mid-run)
            return db
        except Exception:
            time.sleep(1)
    raise RuntimeError("FalkorDB container did not start")


def start_postgres():
    p = C.PG
    if not running(p["container"]):
        sh("docker", "rm", "-f", p["container"], check=False)
        args = []
        for k, v in p["settings"].items():
            args += ["-c", f"{k}={v}"]
        sh("docker", "run", "-d", "--name", p["container"], "--memory", C.MEMORY, "--memory-swap", C.MEMORY,
           "--cpus", C.CPUS, "--shm-size", "1g", "-p", f"{p['port']}:5432",
           "-v", f"{p['volume']}:/var/lib/postgresql", "-e", f"POSTGRES_USER={p['user']}",
           "-e", f"POSTGRES_PASSWORD={p['password']}", "-e", f"POSTGRES_DB={p['db']}", p["image"], *args)
    import psycopg
    for _ in range(120):
        try:
            with psycopg.connect(pg_dsn(), connect_timeout=3) as c:
                c.execute("SELECT 1")
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError("PostgreSQL container did not start")


def pg_dsn():
    p = C.PG
    return f"host=localhost port={p['port']} user={p['user']} password={p['password']} dbname={p['db']}"


def mem_bytes(container):
    """Current memory use of a container (docker stats), in bytes."""
    out = sh("docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", container, check=False)
    if not out:
        return 0
    used = out.split("/")[0].strip()
    units = {"B": 1, "KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "KB": 1e3, "MB": 1e6, "GB": 1e9}
    for u in sorted(units, key=len, reverse=True):
        if used.endswith(u):
            return int(float(used[: -len(u)]) * units[u])
    return 0


class Peak:
    """Sample container memory about once a second while a block runs; .peak in bytes per container."""

    def __init__(self, *containers):
        self.containers, self.peak, self._stop = containers, {c: 0 for c in containers}, threading.Event()

    def __enter__(self):
        def loop():
            while not self._stop.is_set():
                for c in self.containers:
                    self.peak[c] = max(self.peak[c], mem_bytes(c))
                self._stop.wait(0.5)
        self._t = threading.Thread(target=loop, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join()
        for c in self.containers:
            self.peak[c] = max(self.peak[c], mem_bytes(c))


def hardware():
    def sysctl(k):
        return sh("sysctl", "-n", k, check=False)
    info = json.loads(sh("docker", "info", "--format", "{{json .}}", check=False) or "{}")
    return {"cpu": sysctl("machdep.cpu.brand_string") or platform.processor(), "cores": sysctl("hw.ncpu"),
            "ram_bytes": int(sysctl("hw.memsize") or 0), "os": platform.platform(),
            "docker": info.get("ServerVersion"), "docker_vm_memory_bytes": info.get("MemTotal"),
            "docker_vm_cpus": info.get("NCPU"), "container_memory": C.MEMORY, "container_cpus": C.CPUS,
            "disk": sh("df", "-h", C.ROOT, check=False).splitlines()[-1]}
