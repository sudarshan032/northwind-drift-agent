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
                   "res": res, "hardware": gen.get("hardware")}
    return data


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
        "charts": ["time-vs-depth", "time-vs-size", "concurrency-p95", "memory-vs-size"],
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
    charts(data)
    print(json.dumps(bundle(data), indent=1)[:3000])
