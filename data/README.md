# Northwind Health synthetic data (FICTIONAL)

**Everything in this directory is synthetic.** "Northwind Health" is a fictional
organisation. Its footprint (51 hospitals, ~1,000 clinics, ~800 non-acute sites,
7 western US states, a capability centre in Hyderabad) is sized on the public
footprint of a large US health system, but no real organisation's systems,
people, network addresses or incidents are represented. City names are real US
places used only as locations.

- No personal data: users are pseudonymous IDs (`u-h03-00412`) with role labels.
  There are no human names, no emails and no patients.
- IP addresses are drawn from private RFC 1918 ranges.

## How it was generated

```
uv run python -m ingest.generate --seed 42 --out data/
```

- `SEED=42`, `SNAPSHOT_END=2026-10-14T23:59:59Z`, observation window 14 days.
- Same seed gives byte-identical files.
- `truth/world.json` is the internal truth model (tests only; never loaded).
- `ground_truth.json` lists every deliberately injected drift.

See `DATA_SPEC.md` in the repository root for the full model.
