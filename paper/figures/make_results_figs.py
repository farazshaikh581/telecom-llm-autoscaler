"""Figures 2-4 of the results section, drawn at print size (IEEE compsoc: column 3.45 in, text 7.14 in).
Reads results/runs/*/summary.json, timeline.csv, decisions.jsonl and results/analysis/history_order.csv.
Usage (from the repo root): python paper/figures/make_results_figs.py"""
import json
import pathlib

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Patch
import numpy as np
import pandas as pd

R = pathlib.Path("results/runs")
A = pathlib.Path("results/analysis")
OUT = pathlib.Path("paper/figures")
COL, TEXT = 3.45, 7.14
for _f in pathlib.Path("/usr/share/fonts/truetype/liberation").glob("LiberationSerif-*.ttf"):
    font_manager.fontManager.addfont(str(_f))
plt.rcParams.update({"font.family": "Liberation Serif", "mathtext.fontset": "stix", "font.size": 8,
                     "axes.labelsize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 7,
                     "axes.linewidth": 0.6, "xtick.direction": "in", "ytick.direction": "in",
                     "xtick.top": True, "ytick.right": True, "xtick.major.size": 2.5, "ytick.major.size": 2.5,
                     "xtick.major.width": 0.6, "ytick.major.width": 0.6, "grid.linestyle": "--",
                     "grid.linewidth": 0.4, "grid.color": "#C8C8C8", "legend.frameon": True,
                     "legend.edgecolor": "#888888", "legend.fancybox": False, "legend.framealpha": 1.0,
                     "hatch.linewidth": 0.5, "patch.linewidth": 0.5, "pdf.fonttype": 42})
C = {"rule": "#4C72B0", "rl": "#DD8452", "tgpt": "#55A868", "qwen": "#8172B3", "red": "#C44E52"}
NAMES = {"rule": "Rule-based", "rl": "RL", "tgpt": "TelecomGPT-R1", "qwen": "Qwen3.5-27B"}
CTRL = [("hpa70", "HPA", "rule"), ("keda", "KEDA", "rule"), ("phpa", "P-HPA", "rule"),
        ("aware", "AWARE", "rl"), ("firm", "FIRM", "rl"), ("ours", "LSTM-DDQN", "rl"),
        ("tgpt_joint", "TG-J", "tgpt"), ("tgpt_perservice", "TG-PS", "tgpt"), ("tgpt_oracl", "TG-RF", "tgpt"),
        ("qwen_joint", "QW-J", "qwen"), ("qwen_perservice", "QW-PS", "qwen"), ("qwen_oracl", "QW-RF", "qwen")]
DAYS, REPS, B = range(5), (1, 2), 10_000


def kpis(name):
    s = json.load(open(R / name / "summary.json"))
    return 100 * s["overall"]["violation_rate_k3"], s["cost"]["mean_ready_total"]


def boot_mean(vals, rng):
    """vals: {day: [repeat values]} -> 95% interval of the mean, days then repeats resampled."""
    days = list(vals.values())
    m = np.empty(B)
    for b in range(B):
        pick = [days[i] for i in rng.integers(len(days), size=len(days))]
        m[b] = np.mean(np.concatenate([np.asarray(d)[rng.integers(len(d), size=len(d))] for d in pick]))
    return np.percentile(m, [2.5, 97.5])


def level(prefix, c, rng):
    out = {}
    for k, idx in (("viol", 0), ("copies", 1)):
        vals = {}
        for d in DAYS:
            for r in REPS:
                name = f"{prefix}_{c}_t{d}_r{r}" if prefix == "test2" else f"hl_{c}_t{d}" + ("_r2" if r == 2 else "")
                if (R / name / "summary.json").exists():
                    vals.setdefault(d, []).append(kpis(name)[idx])
        allv = np.concatenate(list(vals.values()))
        out[k] = (allv.mean(), *boot_mean(vals, rng))
    return out


def fig_levels():
    rng = np.random.default_rng(1)
    nom = {c: level("test2", c, rng) for c, _, _ in CTRL}
    hl = {c: level("hl", c, rng) for c in ("hpa70", "keda", "phpa", "aware", "firm", "ours", "tgpt_joint", "qwen_joint")}
    fig, axes = plt.subplots(1, 2, figsize=(TEXT, 2.2), gridspec_kw={"wspace": 0.16})
    x = np.arange(len(CTRL), dtype=float)
    x[3:] += 0.4
    x[6:] += 0.4
    x[9:] += 0.4
    w = 0.38
    for ax, k, lab, top in ((axes[0], "viol", "SLO violation rate (%)", 25), (axes[1], "copies", "Mean ready copies", 20)):
        for xi, (c, _, f) in zip(x, CTRL):
            m, lo, hi = nom[c][k]
            ax.bar(xi - w / 2, m, w, color=C[f], edgecolor="black", lw=0.5, zorder=2)
            ax.errorbar(xi - w / 2, m, yerr=[[m - lo], [hi - m]], fmt="none", ecolor="black", elinewidth=0.6,
                        capsize=1.6, capthick=0.6, zorder=3)
            if c in hl:
                m2, lo2, hi2 = hl[c][k]
                ax.bar(xi + w / 2, m2, w, color="white", edgecolor=C[f], hatch="//////", lw=0.6, zorder=2)
                ax.bar(xi + w / 2, m2, w, color="none", edgecolor="black", lw=0.5, zorder=2)
                ax.errorbar(xi + w / 2, m2, yerr=[[m2 - lo2], [hi2 - m2]], fmt="none", ecolor="black",
                            elinewidth=0.6, capsize=1.6, capthick=0.6, zorder=3)
        ax.set_xticks(x)
        ax.set_xticklabels([n for _, n, _ in CTRL], rotation=40, ha="right", rotation_mode="anchor")
        ax.set_ylabel(lab)
        ax.set_ylim(0, top)
        ax.set_xlim(x[0] - 0.7, x[-1] + 0.7)
        ax.grid(axis="y")
        ax.set_axisbelow(True)
        ax.tick_params(axis="x", length=0)
    axes[1].axhline(18, color=C["red"], lw=0.9, ls="--", zorder=4)
    axes[1].text(x[4], 18.4, "budget (18 copies)", color=C["red"], fontsize=7, va="bottom", ha="center")
    handles = [Patch(facecolor=C[f], edgecolor="black", lw=0.5, label=NAMES[f]) for f in C if f != "red"]
    handles += [Patch(facecolor="white", edgecolor="black", lw=0.5, label="nominal load (solid)"),
                Patch(facecolor="white", edgecolor="black", hatch="//////", lw=0.5, label="25% more load")]
    handles[4] = Patch(facecolor="#BBBBBB", edgecolor="black", lw=0.5, label="nominal load")
    top = axes[0].get_position().y1
    fig.legend(handles=handles, loc="lower center", ncol=6, bbox_to_anchor=(0.5, top + 0.01), handlelength=1.6,
               columnspacing=1.2, borderpad=0.35)
    for ax, t in zip(axes, ("(a)", "(b)")):
        ax.text(0.018, 0.965, t, transform=ax.transAxes, ha="left", va="top", fontsize=8)
    axes[1].set_yticks(range(0, 21, 3))
    fig.savefig(OUT / "fig2_levels.pdf", bbox_inches="tight", pad_inches=0.02, metadata={"CreationDate": None})
    fig.savefig(OUT / "fig2_levels.png", dpi=300, bbox_inches="tight", pad_inches=0.02)
    return nom, hl


def fig_timeline(day=1, rep=2, svc="graph-bfs", slo=203):
    runs = [(f"test2_hpa70_t{day}_r{rep}", "HPA", C["rule"]),
            (f"test2_qwen_oracl_t{day}_r{rep}", "Qwen3.5-27B, reasoning-first, oldest first", C["qwen"]),
            (f"ho_qwen_oracl_t{day}", "Qwen3.5-27B, reasoning-first, newest first", C["tgpt"])]
    fig, axes = plt.subplots(4, 1, figsize=(COL, 3.0), sharex=True,
                             gridspec_kw={"height_ratios": [0.85, 1, 1, 1], "hspace": 0.12})
    base = pd.read_csv(R / runs[0][0] / "timeline.csv")
    base = base[base.minute >= 15]
    axes[0].fill_between(base.minute - 15 + 0.5, base[f"{svc}_rps"], color="#A6A6A6", lw=0, alpha=0.7)
    axes[0].plot(base.minute - 15 + 0.5, base[f"{svc}_rps"], color="#555555", lw=0.7)
    axes[0].set_ylabel("req/s")
    axes[0].set_ylim(0, 80)
    axes[0].text(0.015, 0.9, "(a) graph BFS function, request rate", transform=axes[0].transAxes, va="top", fontsize=7)
    for ax, (name, lab, col), tag in zip(axes[1:], runs, "bcd"):
        t = pd.read_csv(R / name / "timeline.csv")
        t = t[t.minute >= 15]
        for mnt in t.minute[t[f"{svc}_p95_all_ms"] > slo] - 15:
            ax.axvspan(mnt, mnt + 1, color=C["red"], alpha=0.25, lw=0)
        ticks = [json.loads(l) for l in open(R / name / "decisions.jsonl")]
        ticks = [d for d in ticks if d.get("k") is not None]
        xx = np.array([d["obs"]["t"] / 60 for d in ticks]) - 15
        n = np.array([d["obs"]["services"][svc]["desired"] for d in ticks])
        keep = xx >= -0.5
        ax.step(xx[keep], n[keep], where="post", color=col, lw=1.1)
        ax.set_ylim(0, 12)
        ax.set_yticks([0, 4, 8, 12])
        ax.set_ylabel("copies")
        ax.text(0.015, 0.9, f"({tag}) {lab}", transform=ax.transAxes, va="top", fontsize=7)
        ax.grid(axis="y")
        ax.set_axisbelow(True)
    axes[-1].set_xlabel("Minute of the scored hour")
    axes[-1].set_xlim(0, 60)
    axes[1].legend(handles=[Patch(facecolor=C["red"], alpha=0.25, label="P95 over the SLO")], loc="upper right",
                   handlelength=1.2, borderpad=0.3, fontsize=6.5)
    fig.align_ylabels(axes)
    fig.savefig(OUT / "fig3_timeline.pdf", bbox_inches="tight", pad_inches=0.02, metadata={"CreationDate": None})
    fig.savefig(OUT / "fig3_timeline.png", dpi=300, bbox_inches="tight", pad_inches=0.02)


def fig_history():
    h = pd.read_csv(A / "history_order.csv")
    get = lambda meas, m, mode, col: float(h[(h.measure == meas) & (h.model == m) & (h["mode"] == mode)][col].iloc[0])
    fig, (a, b) = plt.subplots(1, 2, figsize=(COL, 1.75), gridspec_kw={"width_ratios": [1, 1.35], "wspace": 0.42})
    xs = [0, 0.75, 1.9, 2.65]
    rows = [("tgpt", "oldest"), ("tgpt", "newest"), ("qwen", "oldest"), ("qwen", "newest")]
    for xi, (m, o) in zip(xs, rows):
        lat, st = 100 * get("latest", m, "oracl", o), 100 * get("stale", m, "oracl", o)
        a.bar(xi, lat, 0.62, color=C[m], edgecolor="black", lw=0.5)
        a.bar(xi, st, 0.62, bottom=lat, color=C["red"], edgecolor="black", lw=0.5, hatch="xxxx")
        a.bar(xi, 100 - lat - st, 0.62, bottom=lat + st, color="white", edgecolor="black", lw=0.5)
    a.set_xticks(xs)
    a.set_xticklabels(["TG\nold", "TG\nnew", "QW\nold", "QW\nnew"])
    a.set_ylim(0, 100)
    a.set_xlim(-0.5, 3.15)
    a.set_ylabel("Present-tense numbers (%)")
    a.tick_params(axis="x", length=0)
    a.legend(handles=[Patch(facecolor="#CCCCCC", edgecolor="black", lw=0.5, label="latest"),
                      Patch(facecolor=C["red"], edgecolor="black", lw=0.5, hatch="xxxx", label="stale"),
                      Patch(facecolor="white", edgecolor="black", lw=0.5, label="other")],
             loc="upper center", bbox_to_anchor=(0.5, 1.27), ncol=3, handlelength=1.0, columnspacing=0.6,
             handletextpad=0.3, borderpad=0.3, fontsize=6.5)
    sets = [("tgpt", "joint", "TG-J"), ("tgpt", "oracl", "TG-RF"), ("qwen", "joint", "QW-J"), ("qwen", "oracl", "QW-RF")]
    w = 0.36
    for i, (m, mode, _) in enumerate(sets):
        o, n = get("reversals_h", m, mode, "oldest"), get("reversals_h", m, mode, "newest")
        b.bar(i - w / 2, o, w, color="white", edgecolor=C[m], hatch="//////", lw=0.6)
        b.bar(i - w / 2, o, w, color="none", edgecolor="black", lw=0.5)
        b.bar(i + w / 2, n, w, color=C[m], edgecolor="black", lw=0.5)
        b.text(i - w / 2, o + 1.5, f"{o:.0f}", ha="center", va="bottom", fontsize=6)
        b.text(i + w / 2, n + 1.5, f"{n:.1f}" if n < 10 else f"{n:.0f}", ha="center", va="bottom", fontsize=6)
    b.set_xticks(range(4))
    b.set_xticklabels([s[2] for s in sets])
    b.set_ylabel("Reversals per hour")
    b.set_ylim(0, 90)
    b.grid(axis="y")
    b.set_axisbelow(True)
    b.tick_params(axis="x", length=0)
    b.legend(handles=[Patch(facecolor="white", edgecolor="black", hatch="//////", lw=0.5, label="oldest first"),
                      Patch(facecolor="#CCCCCC", edgecolor="black", lw=0.5, label="newest first")],
             loc="upper center", bbox_to_anchor=(0.5, 1.27), ncol=2, handlelength=1.2, columnspacing=0.8,
             handletextpad=0.3, borderpad=0.3, fontsize=6.5)
    for ax, t, xo in ((a, "(a)", -0.42), (b, "(b)", -0.1)):
        ax.text(xo, 1.2, t, transform=ax.transAxes, ha="left", va="center", fontsize=8)
    fig.savefig(OUT / "fig4_history.pdf", bbox_inches="tight", pad_inches=0.02, metadata={"CreationDate": None})
    fig.savefig(OUT / "fig4_history.png", dpi=300, bbox_inches="tight", pad_inches=0.02)


if __name__ == "__main__":
    fig_levels()
    fig_timeline()
    fig_history()
