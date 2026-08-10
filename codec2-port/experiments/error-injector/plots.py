#!/usr/bin/env python3
"""plots.py — per-stage transfer curves (error level -> end-to-end metrics).

One PNG per injection point: 4 panels (dLSD vs clean, dESTOI vs original,
dWARP-Q vs original [hts1a subset], envelope-NMR median), one line per error
type, log-x, quantum thresholds as dashed hlines.  Metric values are means
over the 3-utterance corpus (WARP-Q: hts1a).
"""
import csv
import json
import os
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PLOTS = os.path.join(HERE, "plots")

Q = {"lsd": 0.25, "destoi": 0.005, "dwarpq": 0.05}
COLORS = {"white": "C0", "framemod": "C1", "dc": "C2", "worst": "C3",
          "qround": "C4", "qtrunc": "C5"}


def main():
    os.makedirs(PLOTS, exist_ok=True)
    rows = []
    with open(os.path.join(HERE, "results", "curves.csv")) as f:
        for r in csv.DictReader(f):
            rows.append(r)
    clean_estoi = {r["utt"]: float(r["estoi_orig"]) for r in rows
                   if r["point"] == "CLEAN"}

    warpq = None
    wq = os.path.join(HERE, "results", "warpq.json")
    if os.path.exists(wq):
        with open(wq) as f:
            warpq = json.load(f)

    agg = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["point"] in ("CLEAN", "CLEAN2"):
            continue
        key = (r["point"], r["etype"], float(r["level"]))
        agg[key]["lsd"].append(float(r["lsd_mean"]))
        agg[key]["destoi"].append(
            clean_estoi[r["utt"]] - float(r["estoi_orig"]))
        agg[key]["nmr"].append(float(r["nmr_median"]))

    points = sorted({p for (p, _, _) in agg})
    for point in points:
        etypes = sorted({e for (p, e, _) in agg if p == point})
        fig, axes = plt.subplots(2, 2, figsize=(10, 7))
        panels = [("lsd", "dLSD vs clean [dB]", axes[0, 0]),
                  ("destoi", "dESTOI vs original", axes[0, 1]),
                  ("dwarpq", "dWARP-Q vs original (hts1a)", axes[1, 0]),
                  ("nmr", "envelope-NMR median [dB]", axes[1, 1])]
        for et in etypes:
            levels = sorted({lv for (p, e, lv) in agg
                             if p == point and e == et})
            c = COLORS.get(et, "k")
            for m, _, ax in panels:
                if m == "dwarpq":
                    if warpq is None:
                        continue
                    xs, ys = [], []
                    for lv in levels:
                        k = f"{point}/{et}/{lv:g}"
                        if k in warpq["scores"]:
                            xs.append(lv)
                            ys.append(warpq["scores"][k] - warpq["clean"])
                    if xs:
                        ax.plot(xs, ys, "o-", color=c, label=et)
                else:
                    ys = [np.mean(agg[(point, et, lv)][m]) for lv in levels]
                    ax.plot(levels, ys, "o-", color=c, label=et)
        for m, title, ax in panels:
            ax.set_xscale("log")
            ax.set_title(title, fontsize=10)
            ax.grid(True, which="both", alpha=0.3)
            if m in Q:
                ax.axhline(Q[m], ls="--", color="gray", lw=1)
            ax.legend(fontsize=8)
        fig.suptitle(f"injection point: {point}  "
                     "(x = injected error level, log scale)")
        fig.tight_layout()
        out = os.path.join(PLOTS, f"transfer_{point}.png")
        fig.savefig(out, dpi=110)
        plt.close(fig)
        print("wrote", out)


if __name__ == "__main__":
    main()
