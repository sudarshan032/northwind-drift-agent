"""Generator entry point.

    python -m ingest.generate --seed 42 --out data/            # full world, drift on
    python -m ingest.generate --seed 42 --out x/ --no-drift    # all views agree
    python -m ingest.generate --seed 42 --out data_demo/ --subset demo

Same seed -> byte-identical output.
"""

import argparse
import csv
import gzip
import json
import os
import shutil
import time

from ingest import connect, drift, export, views
from ingest.world import SEED, build_world

README = """# Northwind Health synthetic data (FICTIONAL)

**Everything in this directory is synthetic.** "Northwind Health" is a fictional
organisation. Its footprint (51 hospitals, ~1,000 clinics, ~800 non-acute sites,
7 western US states, a capability centre in Hyderabad) is sized on the public
footprint of a large US health system, but no real organisation's systems,
people, network addresses or incidents are represented. City names are real US
places used only as locations; vendor product names are used only as models.

- No personal data: users are pseudonymous IDs (`u-h03-00412`) with role labels.
  There are no human names, no emails and no patients.
- IP addresses come from private RFC 1918 ranges (external SaaS uses the
  RFC 5737 documentation range 203.0.113.0/24).

## How it was generated

```
{command}
```

- `SEED={seed}`, `SNAPSHOT_END=2026-10-14T23:59:59Z`, observation window 14 days.
- Drift injection: {drift}. Subset: {subset}.
- Same seed gives byte-identical files.
- `truth/world.json` is the internal truth model (tests only; never loaded).
- `raw/<source>/` holds each discovery source's native export (ServiceNow CSV/IRE JSON,
  Prometheus text, OTLP JSON, nfdump CSV, Mirth channel XML, Veeam sessions, array API JSON,
  Entra sign-ins, SQL Server audit, HR worker export, ITSM history); `normalised/` is what the
  connectors (`python -m ingest.connect`) make of them, and what the loader reads.
- `ground_truth.json` lists every deliberately injected drift.
- With `--gzip`, `.jsonl` / `.json` files are written as `.jsonl.gz` / `.json.gz`
  (same content; loader and checker read both).
- `--subset demo`: all 51 hospitals, both DCs, both cloud regions, GCC-HYD; no clinics
  or non-acute sites (shadow-IT and ghost cases that sit at clinics in the full world
  move to community hospitals). Same drift catalogue and headline as the full data.

See `docs/specs/DATA_SPEC.md` for the full model.
"""


def write_csv(path, rows, columns=None):
    columns = columns or (list(rows[0].keys()) if rows else [])
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: views.fmt_value(row.get(k, "")) for k in columns})


def subset_world(world):
    """Demo subset (AGENT_SPEC 0.1b): all 51 hospitals, both data centres,
    both cloud regions, GCC-HYD and external SaaS; no clinics or non-acute
    sites. Site-specific interfaces and groups follow their site."""
    keep_sites = {s["id"] for s in world["sites"] if s["type"] not in ("clinic", "non_acute")} | {"EXTERNAL"}
    dropped = {s["id"] for s in world["sites"]} - keep_sites

    def keep_ci(c):
        if c["site"] not in keep_sites:
            return False
        tail = c["name"].rsplit("-", 1)[-1]
        return not (c["class"] == "u_cmdb_ci_hl7_interface" and tail in dropped)

    cis = [c for c in world["cis"] if keep_ci(c)]
    names = {c["name"] for c in cis}
    out = dict(world)
    out["sites"] = [s for s in world["sites"] if s["id"] in keep_sites]
    out["subnets"] = [s for s in world["subnets"] if s["site"] in keep_sites]
    out["departments"] = [d for d in world["departments"] if d["site"] in keep_sites]
    out["cis"] = cis
    out["rels"] = [r for r in world["rels"] if r["parent"] in names and r["child"] in names]
    out["changes"] = [c for c in world["changes"] if c["cmdb_ci"] in names]
    idn = world["identity"]
    users = [u for u in idn["users"] if u["site"] in keep_sites]
    uids = {u["id"] for u in users}
    out["identity"] = {"users": users, "groups": [g for g in idn["groups"] if g["site"] in keep_sites],
                       "roles": idn["roles"], "grants": [g for g in idn["grants"] if g["target"] in names],
                       "memberships": [m for m in idn["memberships"] if m["user"] in uids]}
    out["meta"] = dict(world["meta"], subset="demo", demo_sites=[s for s in world["meta"]["demo_sites"]
                                                                 if s in keep_sites])
    return out


SUBDIRS = ("truth", "raw", "normalised", "identity", "cmdb", "discovery", "observed", "evidence")


def gzip_tree(out):
    """--gzip: compress every data file in place (gzip mtime=0, byte-stable); readers accept .gz."""
    for d, _, files in os.walk(out):
        for f in sorted(files):
            p = os.path.join(d, f)
            if f.endswith((".gz", ".md")) or f in ("ground_truth.json", "sources.json"):
                continue
            with open(p, "rb") as src, open(p + ".gz", "wb") as dst:
                with gzip.GzipFile(filename="", mode="wb", fileobj=dst, mtime=0) as z:
                    shutil.copyfileobj(src, z)
            os.remove(p)


def generate(seed=SEED, out="data", inject=True, subset=None, command=None, gz=False):
    """Truth model -> drift plan -> raw source exports -> connectors -> normalised evidence."""
    t0 = time.time()
    world = build_world(seed)
    if subset == "demo":
        world = subset_world(world)
    plan = drift.plan_drift(world) if inject else drift.empty_plan()
    if os.path.isdir(out):
        for sub in SUBDIRS:
            shutil.rmtree(os.path.join(out, sub), ignore_errors=True)
        for f in ("ground_truth.json", "scenarios.json", "README.md"):
            if os.path.exists(os.path.join(out, f)):
                os.remove(os.path.join(out, f))
    os.makedirs(os.path.join(out, "truth"), exist_ok=True)
    with open(os.path.join(out, "truth", "world.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(world, f, sort_keys=True, separators=(",", ":"))
    views.prepare(world)
    counts = export.write_all(world, plan, out)
    with open(f"{out}/ground_truth.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(plan["ground_truth"], f, indent=1, sort_keys=True)
        f.write("\n")
    from ingest.scenarios import SCENARIOS  # agent scenarios on existing drifts (ground_truth.json stays a list)
    with open(f"{out}/scenarios.json", "w", encoding="utf-8", newline="\n") as f:
        json.dump(SCENARIOS, f, indent=1, sort_keys=True)
        f.write("\n")
    evidence = connect.run(out)
    cmd = command or f"python -m ingest.generate --seed {seed} --out {out}" + \
        ("" if inject else " --no-drift") + (f" --subset {subset}" if subset else "") + (" --gzip" if gz else "")
    with open(f"{out}/README.md", "w", encoding="utf-8", newline="\n") as f:
        f.write(README.format(command=cmd, seed=seed, drift="on" if inject else "off",
                              subset=subset or "none (full world)"))
    if gz:
        gzip_tree(out)
    return {"seconds": round(time.time() - t0, 1), "raw_records": counts, "evidence_rows": evidence}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out", default="data")
    ap.add_argument("--no-drift", action="store_true", help="disable drift injection (views agree)")
    ap.add_argument("--subset", choices=["demo"], help="demo subset: 51 hospitals, both DCs, clouds, GCC-HYD")
    ap.add_argument("--gzip", action="store_true", help="write .jsonl/.json files gzip-compressed (byte-stable)")
    a = ap.parse_args()
    out = a.out.rstrip("/") or "."
    res = generate(a.seed, out, inject=not a.no_drift, subset=a.subset, gz=a.gzip)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
