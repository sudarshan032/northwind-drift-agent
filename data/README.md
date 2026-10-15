# Northwind Health synthetic data (FICTIONAL)

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
python -m ingest.generate --seed 42 --out data
```

- `SEED=42`, `SNAPSHOT_END=2026-10-14T23:59:59Z`, observation window 14 days.
- Drift injection: on. Subset: none (full world).
- Same seed gives byte-identical files.
- `truth/world.json` is the internal truth model (tests only; never loaded).
- `ground_truth.json` lists every deliberately injected drift.
- With `--gzip`, `.jsonl` / `.json` files are written as `.jsonl.gz` / `.json.gz`
  (same content; loader and checker read both).
- `--subset demo`: all 51 hospitals, both DCs, both cloud regions, GCC-HYD; no clinics
  or non-acute sites (shadow-IT and ghost cases that sit at clinics in the full world
  move to community hospitals). Same drift catalogue and headline as the full data.

See `DATA_SPEC.md` in the repository root for the full model.
