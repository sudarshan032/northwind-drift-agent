"""Charts and the static bundle for the website's Scale tab (BENCH_SPEC 6.2 and 6.5).

    python -m bench.charts

Writes bench/charts/<chart>-<light|dark>.<png|svg> and ui/scale/ (SVG charts + scale.json + RESULTS.md copy).
FalkorDB is blue with circles, PostgreSQL amber with squares; the full radius is a solid line, the traversal
alone dashed, so colour is never the only cue.
"""

import json
import os
import shutil

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from bench import config as C  # noqa: E402

SIZES = [50_000, 250_000, 500_000, 3_500_000]
OUT = os.path.join(C.ROOT, "charts")
SITE = os.path.join(os.path.dirname(C.ROOT), "ui", "scale")
THEMES = {
    "light": {"bg": "#FFFFFF", "ink": "#18212B", "muted": "#55606B", "rule": "#D7DDE1",
              "falkordb": "#2F5D8A", "postgres": "#9A6400"},
    "dark": {"bg": "#1B232B", "ink": "#E7ECEF", "muted": "#A3AEB7", "rule": "#2E3943",
             "falkordb": "#7FA8D4", "postgres": "#E0A84A"},
}
MARK = {"falkordb": "o", "postgres": "s"}
NAME = {"falkordb": "FalkorDB", "postgres": "PostgreSQL"}
STYLE = {"radius": "-", "reach": "--"}
KIND = {"radius": "full radius", "reach": "traversal only"}


def rows(size, step):
    p = os.path.join(C.RESULTS, str(size), f"{step}.jsonl")
    return [json.loads(x) for x in open(p)] if os.path.exists(p) else []


def load():
    data = {}
    for s in SIZES:
        gen = rows(s, "generate")[0]
        dep = [r for r in rows(s, "depth") if "start" in r]
        gate = next(r for r in rows(s, "depth") if r.get("gate"))
        res = {r["system"]: r for r in rows(s, "resources")}
        data[s] = {"nodes": gen["nodes"], "edges": gen["edges"], "regions": gen["regions"],
                   "hospitals": gen["hospitals"], "depth": dep, "gate": gate["gate"],
                   "pairs": gate["pairs_checked"], "array": gate["storage_array"],
                   "conc": rows(s, "concurrency_falkordb") + rows(s, "concurrency_postgres"),
                   "review": rows(s, "change_review"),
                   "load": {"falkordb": rows(s, "falkordb_load")[0], "postgres": rows(s, "postgres_load")[0]},
                   "res": res, "hardware": gen.get("hardware"),
                   "raw": [r for r in rows(s, "depth_raw") if "start" in r],
                   "raw_gate": next((r["gate"] for r in rows(s, "depth_raw") if r.get("gate")), None),
                   "server": rows(s, "server_times"),
                   "bulk": (rows(s, "falkordb_bulk_load") or [None])[0],
                   "copy": (rows(s, "postgres_copy_load") or [None])[0],
                   "hybrid": hybrid_rows(s)}
    return data


def hybrid_rows(s):
    """Hybrid rows with PostgreSQL server times taken from pg_stat_statements (real prepared executions);
    the EXPLAIN-based figures are kept as `server_explain_ms` (EXPLAIN overstated the hybrid's PostgreSQL part)."""
    hy = [dict(r) for r in rows(s, "hybrid") if r.get("variant") != "gate"]
    pgss = {(r["case"], r["start"], r["depth"]): r["server_mean_ms"] for r in rows(s, "pg_stat_check")}
    for r in hy:
        key = (r["start"], r["depth"])
        if r["variant"] == "B_postgres_full" and ("B_postgres_full", *key) in pgss:
            r["server_explain_ms"], r["server_median_ms"] = r["server_median_ms"], pgss[("B_postgres_full", *key)]
        if r["variant"] == "C_hybrid" and ("C_postgres_part", *key) in pgss:
            r["server_explain_ms"] = r["server_median_ms"]
            r["server_postgres_part_ms"] = pgss[("C_postgres_part", *key)]
            r["server_median_ms"] = round(r["server_falkordb_part_ms"] + r["server_postgres_part_ms"], 3)
    return hy


def pick(dep, system, kind, start, depth):
    return next((r for r in dep if r["system"] == system and r.get("kind") == kind and r["start"] == start
                 and r["depth"] == depth), None)


def style(ax, t):
    ax.set_facecolor(t["bg"])
    for sp in ax.spines.values():
        sp.set_color(t["rule"])
    ax.tick_params(colors=t["muted"], labelsize=9)
    ax.xaxis.label.set_color(t["ink"])
    ax.yaxis.label.set_color(t["ink"])
    ax.title.set_color(t["ink"])
    ax.grid(True, color=t["rule"], linewidth=0.6)


def figure(t, n, w=11, h=3.4):
    fig, axes = plt.subplots(1, n, figsize=(w, h), squeeze=False)
    fig.patch.set_facecolor(t["bg"])
    for ax in axes[0]:
        style(ax, t)
    return fig, axes[0]


def legend(fig, t, handles):
    lg = fig.legend(handles=handles, loc="lower center", ncol=len(handles), frameon=False, fontsize=9,
                    bbox_to_anchor=(0.5, -0.02))
    for x in lg.get_texts():
        x.set_color(t["ink"])


def save(fig, name, theme, rect=(0, 0.08, 1, 1)):
    os.makedirs(OUT, exist_ok=True)
    fig.tight_layout(rect=rect)
    for ext in ("png", "svg"):
        fig.savefig(os.path.join(OUT, f"{name}-{theme}.{ext}"), dpi=160, facecolor=fig.get_facecolor())
    plt.close(fig)


def line(ax, x, y, system, kind, t):
    return ax.plot(x, y, STYLE[kind], marker=MARK[system], color=t[system], linewidth=1.8, markersize=5,
                   label=f"{NAME[system]}, {KIND[kind]}")[0]


def srv(d, system, kind, start, depth):
    return next((r["server_median_ms"] for r in d["server"] if r["system"] == system and r["kind"] == kind
                 and r["start"] == start and r["depth"] == depth), None)


def charts_v2(data):
    """Raw-relationship traversal, end to end vs server side, and load methods."""
    for theme, t in THEMES.items():
        fig, axes = figure(t, 2, 11, 3.8)
        hs = []
        xs = [data[s]["nodes"] for s in SIZES]
        for ax, kind, title in ((axes[0], "raw", "raw relationships, rules applied while walking"),
                                (axes[1], "reach", "pre-built impact edges (traversal only)")):
            for system in ("falkordb", "postgres"):
                if kind == "raw":
                    rs = [next(r for r in data[s]["raw"] if r["system"] == system and r["start"] == data[s]["array"]
                               and r["depth"] == 10) for s in SIZES]
                    e2e, sv = [r["median_ms"] for r in rs], [r["server_median_ms"] for r in rs]
                else:
                    e2e = [pick(data[s]["depth"], system, "reach", data[s]["array"], 10)["median_ms"] for s in SIZES]
                    sv = [srv(data[s], system, "reach", data[s]["array"], 10) for s in SIZES]
                h1 = ax.plot(xs, e2e, "-", marker=MARK[system], color=t[system], linewidth=1.8, markersize=5,
                             label=f"{NAME[system]}, end to end")[0]
                h2 = ax.plot(xs, sv, ":", marker=MARK[system], color=t[system], linewidth=1.8, markersize=5,
                             mfc="none", label=f"{NAME[system]}, server side")[0]
                if kind == "raw":
                    hs += [h1, h2]
            ax.set_xscale("log")
            ax.set_yscale("log")
            ax.set_title(f"busiest storage array, depth 10: {title}", fontsize=10)
            ax.set_xlabel("nodes (log)")
        axes[0].set_ylabel("median ms (log)")
        legend(fig, t, hs)
        save(fig, "raw-vs-size", theme)

        fig, axes = figure(t, 1, 6.5, 3.8)
        ax = axes[0]
        series = [("falkordb", "--", "o", "FalkorDB, batched Cypher CREATE",
                   [data[s]["load"]["falkordb"]["seconds"]["load_total"] for s in SIZES]),
                  ("falkordb", "-", "o", "FalkorDB, bulk loader",
                   [data[s]["bulk"]["seconds"]["load_total"] for s in SIZES]),
                  ("postgres", "-", "s", "PostgreSQL, COPY", [data[s]["copy"]["seconds"]["load_total"] for s in SIZES])]
        hs = [ax.plot(xs, ys, ls, marker=m, color=t[sysname], linewidth=1.8, markersize=5, label=lab)[0]
              for sysname, ls, m, lab, ys in series]
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("nodes (log)")
        ax.set_ylabel("seconds (log)")
        ax.set_title("load time, including indexes (and the impact layer for FalkorDB)", fontsize=10)
        lg = ax.legend(handles=hs, fontsize=8, frameon=False, loc="upper left")
        for x in lg.get_texts():
            x.set_color(t["ink"])
        save(fig, "load-vs-size", theme, rect=(0, 0, 1, 1))


VARIANTS = [("A_falkordb_full", "A FalkorDB full radius", "falkordb", ""),
            ("B_postgres_full", "B PostgreSQL full radius", "postgres", ""),
            ("C_hybrid", "C FalkorDB ids + PostgreSQL joins", "hybrid", "//"),
            ("D_falkordb_counts", "D FalkorDB counts only", "falkordb", "xx")]


def charts_v3(data):
    """Hybrid experiment: grouped bars per size, end to end and server side, depth 10."""
    sizes = [s for s in SIZES if data[s]["hybrid"]]
    if not sizes:
        return
    for theme, t in THEMES.items():
        colours = {**t, "hybrid": "#2E6B48" if theme == "light" else "#6FBF8E"}
        fig, axes = plt.subplots(2, 2, figsize=(12, 7), squeeze=False)
        fig.patch.set_facecolor(t["bg"])
        hs = []
        for i, start_kind in enumerate(("array", "dc1-host-07")):
            for j, (field, label) in enumerate((("median_ms", "end to end"), ("server_median_ms", "server side"))):
                ax = axes[i][j]
                style(ax, t)
                w = 0.2
                for k, (v, vl, col, hatch) in enumerate(VARIANTS):
                    ys = []
                    for s in sizes:
                        start = data[s]["array"] if start_kind == "array" else "dc1-host-07"
                        r = next((x for x in data[s]["hybrid"] if x["variant"] == v and x["start"] == start
                                  and x["depth"] == 10), None)
                        ys.append(r[field] if r else 0)
                    bars = ax.bar([x + (k - 1.5) * w for x in range(len(sizes))], ys, w, color=colours[col],
                                  hatch=hatch, edgecolor=t["bg"], label=vl)
                    if i == 0 and j == 0:
                        hs.append(bars)
                ax.set_yscale("log")
                ax.set_xticks(range(len(sizes)))
                ax.set_xticklabels([f"{data[s]['nodes'] / 1e6:.2f}M" if data[s]["nodes"] >= 1e6
                                    else f"{data[s]['nodes'] / 1e3:.0f}k" for s in sizes])
                who = "busiest storage array" if start_kind == "array" else "dc1-host-07"
                ax.set_title(f"{who}, depth 10, {label}", fontsize=10)
                ax.set_ylabel("median ms (log)")
        legend(fig, t, hs)
        save(fig, "hybrid", theme, rect=(0, 0.07, 1, 1))


def charts(data):
    for theme, t in THEMES.items():
        # 1. query time vs depth, per size (busiest storage array: the largest radius)
        fig, axes = figure(t, len(SIZES), 12, 3.4)
        hs = []
        for ax, s in zip(axes, SIZES):
            d = data[s]
            for system in ("falkordb", "postgres"):
                for kind in ("radius", "reach"):
                    ys = [pick(d["depth"], system, kind, d["array"], dp)["median_ms"] for dp in C.DEPTHS]
                    h = line(ax, C.DEPTHS, ys, system, kind, t)
                    if s == SIZES[0]:
                        hs.append(h)
            ax.set_yscale("log")
            ax.set_xticks(C.DEPTHS)
            ax.set_title(f"{d['nodes'] / 1e6:.2f}M nodes" if d["nodes"] >= 1e6 else f"{d['nodes'] / 1e3:.0f}k nodes",
                         fontsize=10)
            ax.set_xlabel("depth (hops)")
        axes[0].set_ylabel("median ms (log)")
        legend(fig, t, hs)
        save(fig, "time-vs-depth", theme)

        # 2. query time vs size at depth 6 and 10, for the three start points
        fig, axes = figure(t, 2, 11, 3.8)
        hs = []
        for ax, dp in zip(axes, (6, 10)):
            xs = [data[s]["nodes"] for s in SIZES]
            for system in ("falkordb", "postgres"):
                for kind in ("radius", "reach"):
                    ys = [pick(data[s]["depth"], system, kind, data[s]["array"], dp)["median_ms"] for s in SIZES]
                    h = line(ax, xs, ys, system, kind, t)
                    if dp == 6:
                        hs.append(h)
            ax.set_xscale("log")
            ax.set_yscale("log")
            ax.set_title(f"busiest storage array, depth {dp}", fontsize=10)
            ax.set_xlabel("nodes (log)")
        axes[0].set_ylabel("median ms (log)")
        legend(fig, t, hs)
        save(fig, "time-vs-size", theme)

        # 3. concurrency: p95 vs clients per size
        fig, axes = figure(t, len(SIZES), 12, 3.4)
        hs = []
        for ax, s in zip(axes, SIZES):
            for system in ("falkordb", "postgres"):
                rs = sorted((r for r in data[s]["conc"] if r["system"] == system), key=lambda r: r["clients"])
                h = ax.plot([r["clients"] for r in rs], [r["p95_ms"] for r in rs], "-", marker=MARK[system],
                            color=t[system], linewidth=1.8, markersize=5, label=f"{NAME[system]} p95")[0]
                if s == SIZES[0]:
                    hs.append(h)
            ax.set_yscale("log")
            ax.set_xticks(C.CLIENTS)
            n = data[s]["nodes"]
            ax.set_title(f"{n / 1e6:.2f}M nodes" if n >= 1e6 else f"{n / 1e3:.0f}k nodes", fontsize=10)
            ax.set_xlabel("concurrent clients")
        axes[0].set_ylabel("p95 ms (log)")
        legend(fig, t, hs)
        save(fig, "concurrency-p95", theme)

        # 4. memory and disk vs size
        fig, axes = figure(t, 1, 6.5, 3.8)
        ax = axes[0]
        xs = [data[s]["nodes"] for s in SIZES]
        mb = lambda b: float(b) / 2**20
        series = [
            ("falkordb", "-", "o", "FalkorDB peak memory", [mb(data[s]["load"]["falkordb"]["peak_memory_bytes"]) for s in SIZES]),
            ("falkordb", "--", "o", "FalkorDB on disk (RDB)", [mb(data[s]["res"]["falkordb"]["disk_bytes"]) for s in SIZES]),
            ("postgres", "-", "s", "PostgreSQL peak memory", [mb(max(data[s]["load"]["postgres"]["peak_memory_bytes"],
                                                                     max(r["peak_memory_bytes"] for r in data[s]["conc"]
                                                                         if r["system"] == "postgres"))) for s in SIZES]),
            ("postgres", "--", "s", "PostgreSQL on disk", [mb(data[s]["res"]["postgres"]["disk_bytes"]) for s in SIZES]),
        ]
        hs = [ax.plot(xs, ys, ls, marker=m, color=t[sysname], linewidth=1.8, markersize=5, label=lab)[0]
              for sysname, ls, m, lab, ys in series]
        ax.axhline(4096, color=t["muted"], linewidth=1, linestyle=":")
        ax.text(xs[-1], 4096 * 1.1, "container limit 4 GB", color=t["muted"], fontsize=8, ha="right")
        ax.set_xscale("log")
        ax.set_yscale("log")
        ax.set_xlabel("nodes (log)")
        ax.set_ylabel("MB (log)")
        ax.set_title("memory and disk", fontsize=10)
        lg = ax.legend(handles=hs, fontsize=8, frameon=False, loc="lower right")
        for x in lg.get_texts():
            x.set_color(t["ink"])
        ax.set_ylim(top=4096 * 1.6)
        save(fig, "memory-vs-size", theme, rect=(0, 0, 1, 1))


def bundle(data):
    """Static JSON for the Scale tab: no live computation on the website."""
    top = SIZES[-1]
    d = data[top]
    arr = d["array"]

    def m(system, kind, start, dp=10):
        r = pick(d["depth"], system, kind, start, dp)
        return {"median_ms": r["median_ms"], "p95_ms": r["p95_ms"], "reached": r["reached"],
                **({"server_ms": round(r["server_ms"], 1)} if r.get("server_ms") is not None else {})}
    conc = {f"{r['system']}_{r['clients']}": {k: r[k] for k in ("throughput_qps", "p50_ms", "p95_ms", "p99_ms",
                                                                 "errors")} for r in d["conc"]}
    sizes = [{"nodes": data[s]["nodes"], "edges": data[s]["edges"], "regions": data[s]["regions"],
              "hospitals": data[s]["hospitals"], "gate": data[s]["gate"], "pairs": data[s]["pairs"]} for s in SIZES]
    out = {
        "sizes": sizes, "top": {"nodes": d["nodes"], "edges": d["edges"], "hospitals": d["hospitals"],
                                "storage_array": arr},
        "depth10": {"reach": {s: m(s, "reach", arr) for s in ("falkordb", "postgres")},
                    "radius": {s: m(s, "radius", arr) for s in ("falkordb", "postgres")},
                    "headline_radius": {s: m(s, "radius", "dc1-host-07") for s in ("falkordb", "postgres")}},
        "concurrency": conc,
        "load_s": {s: d["load"][s]["seconds"]["load_total"] for s in ("falkordb", "postgres")},
        "memory_mb": {"falkordb": round(d["load"]["falkordb"]["peak_memory_bytes"] / 2**20),
                      "postgres": round(max(r["peak_memory_bytes"] for r in d["conc"] if r["system"] == "postgres") / 2**20)},
        "disk_mb": {s: round(float(d["res"][s]["disk_bytes"]) / 2**20) for s in ("falkordb", "postgres")},
        "review": {r["system"]: {"median_ms": r["median_ms"], "declared": r["declared"], "real": r["real"],
                                 "hospitals_real": r["hospitals_real"]} for r in d["review"]},
        "hardware": d["hardware"],
        "settings": {"limits": {"memory": C.MEMORY, "cpus": C.CPUS}, "falkordb": C.FALKOR["image"],
                     "postgres": C.PG["image"], "runs": C.RUNS, "concurrency_seconds": C.CONCURRENCY_SECONDS},
        "charts": ["time-vs-depth", "time-vs-size", "raw-vs-size", "concurrency-p95", "memory-vs-size",
                   "load-vs-size"],
        "raw": {s: {k: r[k] for k in ("median_ms", "server_median_ms", "round_trips", "reached")}
                for s in ("falkordb", "postgres")
                for r in [next(x for x in d["raw"] if x["system"] == s and x["start"] == arr and x["depth"] == 10)]},
        "server": {f"{s}_{k}": srv(d, s, k, arr, 10) for s in ("falkordb", "postgres") for k in ("radius", "reach")},
        "load_methods_s": {"falkordb_cypher": d["load"]["falkordb"]["seconds"]["load_total"],
                           "falkordb_bulk": d["bulk"]["seconds"]["load_total"],
                           "falkordb_bulk_insert_only": d["bulk"]["seconds"]["bulk_insert"],
                           "postgres_copy": d["copy"]["seconds"]["load_total"]},
        "raw_gate": [data[s]["raw_gate"] for s in SIZES],
    }
    os.makedirs(SITE, exist_ok=True)
    with open(os.path.join(SITE, "scale.json"), "w") as f:
        json.dump(out, f, indent=1)
    for c in out["charts"]:
        for theme in THEMES:
            shutil.copy(os.path.join(OUT, f"{c}-{theme}.svg"), os.path.join(SITE, f"{c}-{theme}.svg"))
    if os.path.exists(os.path.join(C.ROOT, "RESULTS.md")):
        shutil.copy(os.path.join(C.ROOT, "RESULTS.md"), os.path.join(SITE, "RESULTS.md"))
    return out


if __name__ == "__main__":
    data = load()
    for s in SIZES:
        data[s]["hardware"] = rows(s, "generate")[0]["hardware"]
    import sys
    charts(data)
    charts_v2(data)
    charts_v3(data)
    if "--site" in sys.argv:  # the website bundle is only rebuilt when asked
        print(json.dumps(bundle(data), indent=1)[:3000])
