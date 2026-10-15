"""Small helpers shared by source writers and connectors."""

import csv
import gzip
import io
import json
import os
from datetime import datetime, timezone


def raw_dir(out, source):
    d = os.path.join(out, "raw", source)
    os.makedirs(d, exist_ok=True)
    return d


def open_text(path, mode="r", gz=False):
    """Text file, plain or gzip (mtime=0 so output is byte-stable)."""
    if "w" in mode:
        if gz:
            raw = gzip.GzipFile(filename="", mode="wb", fileobj=open(path + ".gz", "wb"), mtime=0)
            return io.TextIOWrapper(raw, encoding="utf-8", newline="\n")
        return open(path, "w", encoding="utf-8", newline="\n")
    if not os.path.exists(path) and os.path.exists(path + ".gz"):
        return io.TextIOWrapper(gzip.open(path + ".gz", "rb"), encoding="utf-8", newline="")
    return open(path, encoding="utf-8", newline="")


def exists(path):
    return os.path.exists(path) or os.path.exists(path + ".gz")


def write_csv(path, rows, columns, gz=False):
    with open_text(path, "w", gz) as f:
        w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("true" if v is True else "false" if v is False else "" if v is None else v)
                        for k, v in ((k, r.get(k, "")) for k in columns)})


def read_csv(path):
    with open_text(path) as f:
        return list(csv.DictReader(f))


def write_jsonl(path, rows, gz=False):
    n = 0
    with open_text(path, "w", gz) as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
            n += 1
    return n


def read_jsonl(path):
    if not exists(path):
        return
    with open_text(path) as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(s):
    s = s.replace("Z", "+00:00")
    if " " in s and "T" not in s:
        s = s.replace(" ", "T")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
