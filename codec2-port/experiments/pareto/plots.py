#!/usr/bin/env python3
"""plots.py — render the volumetric picture from results/pareto.csv +
results/fronts.json.

  plots/front_P1.png  — quality vs MHz at the P1 regime (1-cycle mul),
                        engines (left) + system rungs/variants (right),
                        fronts highlighted, tiers colored
  plots/front_P2.png  — same at the P2 regime (RV32EC no-mul, CH32V003)
  plots/front_P3.png  — same at the P3 regime (hw-mul, CH570/CH32V203)
  plots/quality_vs_flash.png — engine quality vs flash footprint

Colors follow the tier framing (tier 0 blue, tier 1 orange, tier 2 aqua,
killed/no-tier gray); fronts are ringed and step-connected. One axis per
panel; log-x for MHz (the space spans 0.3..234 MHz).
"""
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
(HERE / "plots").mkdir(exist_ok=True)

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
GRID = "#e7e6e2"
TIER = {"0": "#2a78d6", "1": "#eb6834", "2": "#1baf7a", "": "#b0afa8"}
FRONT_EDGE = "#0b0b0b"

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2,
    "text.color": INK, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.7,
    "font.size": 9, "axes.titlesize": 10,
})


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


rows = list(csv.DictReader(open(HERE / "results/pareto.csv")))
fronts = json.load(open(HERE / "results/fronts.json"))

REGIMES = {  # regime -> (mhz column, representative chip for the front,
             #           budget lines {label: MHz})
    "P1": ("mhz_p1", "PY32F003x8",
           {"PY32 half-duplex 12 MHz": 12, "V006 half-duplex 24 MHz": 24}),
    "P2": ("mhz_p2", "CH32V003", {"V003 half-duplex 24 MHz": 24}),
    "P3": ("mhz_p1", "CH570",
           {"CH570 half-duplex 50 MHz": 50}),
}

# manual label offsets (points) to avoid collisions
OFF = {
    "impulse-iir": (7, 5), "impulse-iir-csd-sos (jit)": (-12, -14),
    "impulse-iir-csd-sos (interp)": (5, -9), "lsp-allpass-csd3": (0, 8),
    "kl-lattice-csd3": (6, -11), "osc-bank L=80": (6, -12),
    "cycle-replay-2x L=80": (-10, -15), "cycle-replay L=80": (-98, -3),
    "meander-tri L=80": (4, 4), "meander-sq L=80": (4, -9),
    "tube-L0": (4, -11), "tube-L1": (-6, 7), "tube-L4-0.50": (-20, 8),
    "tube-L2-2500": (6, -3), "smooth-mix-1500": (5, 4),
    "smooth-mix-800": (-16, -13), "par-noise-2000": (5, 3),
    "par-plain": (5, -4), "par-noise-1000": (5, 3), "tube-L3": (-16, -12),
}


def scatter_panel(ax, fam_rows, qcol, mcol, front_cfgs, budgets, floor=None,
                  label_all_front=True, extra_labels=()):
    ax.set_xscale("log")
    front_pts = []
    for r in fam_rows:
        q, m = num(r[qcol]), num(r[mcol])
        if q is None or m is None:
            continue
        killed = r["verdict"] == "killed" or r["stability"].startswith("unstable")
        color = TIER[r["tier"] if r["tier"] in TIER else ""]
        on_front = r["config"] in front_cfgs
        if killed:
            ax.scatter(m, q, marker="x", s=26, color="#b0afa8", linewidths=1.2,
                       zorder=2)
        else:
            ax.scatter(m, q, s=52 if on_front else 30, color=color,
                       edgecolors=FRONT_EDGE if on_front else "none",
                       linewidths=1.1, zorder=4 if on_front else 3)
        if on_front:
            front_pts.append((m, q, r["config"]))
        if (on_front and label_all_front) or r["config"] in extra_labels:
            dx, dy = OFF.get(r["config"], (4, 4))
            ax.annotate(r["config"], (m, q), textcoords="offset points",
                        xytext=(dx, dy), fontsize=7.5, color=INK2, zorder=5)
    front_pts.sort()
    if len(front_pts) > 1:
        xs = [p[0] for p in front_pts]
        ys = [p[1] for p in front_pts]
        ax.step(xs, ys, where="post", color=INK2, lw=1.1, ls="--", zorder=1)
    for lab, b in budgets.items():
        ax.axvline(b, color="#d03b3b", lw=0.9, ls=":", zorder=1)
        ax.annotate(lab, xy=(b, 0.02), xycoords=("data", "axes fraction"),
                    rotation=90, fontsize=6.5, color="#d03b3b",
                    ha="right", va="bottom", textcoords="offset points",
                    xytext=(-3, 0))
    if floor is not None:
        ax.axhline(floor, color=INK2, lw=0.8, ls=":")
        ax.annotate("metric floor (two syntheses of the same bitstream)",
                    xy=(0.02, floor), xycoords=("axes fraction", "data"),
                    fontsize=6.5, color=INK2, va="bottom",
                    textcoords="offset points", xytext=(0, 2))
    ax.set_xlabel("MHz-equivalent (log)")
    ax.invert_yaxis()  # up = better


def tier_legend(fig):
    import matplotlib.lines as mlines
    h = [mlines.Line2D([], [], marker="o", ls="none", color=TIER["0"],
                       label="tier 0 — etalon"),
         mlines.Line2D([], [], marker="o", ls="none", color=TIER["1"],
                       label="tier 1 — ladder knee"),
         mlines.Line2D([], [], marker="o", ls="none", color=TIER["2"],
                       label="tier 2 — floor"),
         mlines.Line2D([], [], marker="o", ls="none", color=TIER[""],
                       label="no tier"),
         mlines.Line2D([], [], marker="x", ls="none", color="#b0afa8",
                       label="killed / unstable"),
         mlines.Line2D([], [], ls="--", color=INK2, label="Pareto front")]
    fig.legend(handles=h, loc="lower center", ncol=6, frameon=False,
               fontsize=7.5, bbox_to_anchor=(0.5, -0.02))


engines = [r for r in rows if r["family"] == "engine"]
systems = [r for r in rows if r["family"] in ("system-rung", "variant")]

for regime, (mcol, chip, budgets) in REGIMES.items():
    fam = fronts["chips"][chip]["families"]
    efront = {p["config"] for p in fam["engine"]["front"]}
    sfront = {p["config"] for p in fam["system"]["front"]}
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.5, 4.6))
    scatter_panel(ax1, engines, "lsd_engine_db", mcol, efront, budgets,
                  extra_labels=("osc-bank L=80", "cycle-replay-2x L=80",
                                "meander-tri L=80"))
    ax1.set_title(f"Engines @ {regime}: real-speech LSD vs MHz "
                  "(vs sinusoid ref, floor ≈3.5 dB)")
    ax1.set_ylabel("LSD hts1a, dB  (up = better)")
    scatter_panel(ax2, systems, "lsd_sys_db", mcol, sfront, budgets,
                  floor=7.6,
                  extra_labels=("tube-L2-2500", "smooth-mix-1500",
                                "par-noise-2000", "par-plain", "tube-L3",
                                "par-noise-1000", "smooth-mix-800"))
    ax2.set_title(f"Systems @ {regime}: q1300 LSD vs MHz "
                  "(vs codec2 phase0 ref, floor ≈7.6 dB)")
    ax2.set_ylabel("LSD 3 utts, dB  (up = better)")
    tier_legend(fig)
    fig.suptitle(
        f"{regime} tradeoff space — front computed for {chip} "
        f"({fronts['chips'][chip]['clock_mhz']} MHz)", fontsize=11)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(HERE / f"plots/front_{regime}.png", dpi=160,
                bbox_inches="tight")
    plt.close(fig)

# quality vs flash (engines)
OFF2 = {"impulse-iir": (-70, -4), "lsp-allpass-csd3": (10, 2),
        "kl-lattice-csd3": (10, -12), "impulse-iir-csd-sos (jit)": (-34, -15),
        "osc-bank L=80": (8, -12), "cycle-replay-2x L=80": (8, 0),
        "meander-mip-lin L=80": (-50, 10), "meander-tri L=80": (8, 2)}
fig, ax = plt.subplots(figsize=(7.2, 4.6))
for r in engines:
    q, fl = num(r["lsd_engine_db"]), num(r["flash_b"])
    if q is None or fl is None:
        continue
    killed = r["verdict"] == "killed" or r["stability"].startswith("unstable")
    if killed:
        ax.scatter(fl, q, marker="x", s=26, color="#b0afa8", linewidths=1.2)
    else:
        ax.scatter(fl, q, s=34, color=TIER[r["tier"] if r["tier"] in TIER else ""])
    if r["config"] in ("impulse-iir", "impulse-iir-csd-sos (jit)",
                       "lsp-allpass-csd3", "kl-lattice-csd3", "osc-bank L=80",
                       "cycle-replay-2x L=80", "meander-mip-lin L=80",
                       "meander-tri L=80"):
        dx, dy = OFF2.get(r["config"], (4, 4))
        ax.annotate(r["config"], (fl, q), textcoords="offset points",
                    xytext=(dx, dy), fontsize=7.5, color=INK2)
ax.invert_yaxis()
ax.set_xlabel("flash footprint, bytes (tables + code where known)")
ax.set_ylabel("LSD hts1a, dB  (up = better)")
ax.set_title("Engine quality vs flash — flash is nowhere the binding "
             "constraint (all < 3 KB vs 16-240 KB chips)")
tier_legend(fig)
fig.tight_layout(rect=(0, 0.05, 1, 1))
fig.savefig(HERE / "plots/quality_vs_flash.png", dpi=160, bbox_inches="tight")
plt.close(fig)
print("wrote plots/front_P1.png front_P2.png front_P3.png quality_vs_flash.png")
