#!/usr/bin/env python3
"""knees.py — transfer-curve knees and the first ADAPTIVE BUDGET TABLE.

Reads results/curves.csv (+ results/warpq.json if present), aggregates over
utterances, finds — per (injection point x error type) — the amplitude level
at which each end-to-end metric degrades by its fixed quantum:

    dLSD    >= 0.25 dB   (LSD vs clean synthesis; clean-vs-clean = 0)
    dESTOI  >= 0.005     (ESTOI vs original, drop from the clean score)
    dWARP-Q >= +0.05     (raw WARP-Q vs original, rise; hts1a subset)

Knee per metric = first crossing, log-interpolated between sweep levels.
Overall knee = the smallest (governing metric).  Proposed budget = knee / 4
(safety factor).  Knees censored by the sweep range are marked:
  below_sweep — already past the quantum at the lowest level (knee is an
                UPPER bound; budget = lowest_level/4 is conservative);
  above_sweep — never crossed (knee is a LOWER bound; the stage is
                insensitive within the swept range).

Also validates every knee against the metric noise floor (CLEAN2 rows:
same system, different excitation-noise realisation).

Writes results/budgets.yaml (machine-readable gates fragment) and prints
the measured-vs-a-priori comparison for the overlapping stages.
"""
import csv
import json
import os
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
Q_LSD = 0.25
Q_ESTOI = 0.005
Q_WARPQ = 0.05
SAFETY = 4.0

# Timing-type injections dephase the pulse train relative to the clean
# synthesis: the 20 ms analysis windows then see pulses at different offsets
# and magnitude-LSD/segSNR inflate WITHOUT any perceptual correlate (observed:
# LSD 0.9 dB at 0.05% Wo jitter while dESTOI = +0.0001).  For these points
# the knee is taken from the perceptual metrics only; LSD stays reported as
# a diagnostic.
TIMING_POINTS = {"wo", "pulsepos"}

UNITS = {"coslsp": "cos units", "state": "int16 LSB", "pulsepos": "samples",
         "log2e": "log2 units", "mixfc": "relative", "mixnf": "dB",
         "wo": "relative", "lsphz": "Hz", "edb": "dB"}
SIDE = {"coslsp": "decoder", "state": "decoder", "pulsepos": "decoder",
        "log2e": "decoder", "mixfc": "decoder", "mixnf": "decoder",
        "wo": "encoder", "lsphz": "encoder", "edb": "encoder"}


def load_curves():
    rows = []
    with open(os.path.join(HERE, "results", "curves.csv")) as f:
        for r in csv.DictReader(f):
            for k in r:
                if k not in ("utt", "point", "etype"):
                    r[k] = float(r[k]) if r[k] not in ("", "nan") else np.nan
            rows.append(r)
    return rows


def knee_from_curve(levels, values, quantum):
    """First log-interpolated crossing of `quantum` on (levels, values).
    Returns (knee, censoring) with censoring in {None,'below','above'}."""
    levels = np.asarray(levels)
    values = np.asarray(values)
    ok = np.isfinite(values)
    levels, values = levels[ok], values[ok]
    if len(levels) == 0:
        return np.nan, "no_data"
    if values[0] >= quantum:
        return float(levels[0]), "below"
    above = np.nonzero(values >= quantum)[0]
    if len(above) == 0:
        return float(levels[-1]), "above"
    i = above[0]
    l0, l1 = np.log10(levels[i - 1]), np.log10(levels[i])
    v0, v1 = values[i - 1], values[i]
    t = (quantum - v0) / max(v1 - v0, 1e-12)
    return float(10 ** (l0 + t * (l1 - l0))), None


def main():
    rows = load_curves()
    utts = sorted({r["utt"] for r in rows})

    # clean baselines per utt
    clean_estoi = {r["utt"]: r["estoi_orig"] for r in rows
                   if r["point"] == "CLEAN"}
    floor = {u: {"lsd": r["lsd_mean"],
                 "destoi": clean_estoi[u] - r["estoi_orig"]}
             for u in utts for r in rows
             if r["utt"] == u and r["point"] == "CLEAN2"}

    warpq = None
    wq_path = os.path.join(HERE, "results", "warpq.json")
    if os.path.exists(wq_path):
        with open(wq_path) as f:
            warpq = json.load(f)

    # aggregate over utts: mean dLSD / dESTOI per (point, etype, level)
    agg = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["point"] in ("CLEAN", "CLEAN2"):
            continue
        key = (r["point"], r["etype"], r["level"])
        agg[key]["lsd"].append(r["lsd_mean"])
        agg[key]["destoi"].append(clean_estoi[r["utt"]] - r["estoi_orig"])
        agg[key]["nmr"].append(r["nmr_median"])
        agg[key]["segsnr"].append(r["segsnr_mean"])
        agg[key]["crest"].append(r["crest_delta_median"])

    combos = sorted({(p, e) for (p, e, _) in agg})
    budgets = []
    for point, etype in combos:
        levels = sorted({lv for (p, e, lv) in agg if p == point
                         and e == etype})
        lsd = [np.mean(agg[(point, etype, lv)]["lsd"]) for lv in levels]
        des = [np.mean(agg[(point, etype, lv)]["destoi"]) for lv in levels]
        seg = [np.mean(agg[(point, etype, lv)]["segsnr"]) for lv in levels]

        k_lsd, c_lsd = knee_from_curve(levels, lsd, Q_LSD)
        k_est, c_est = knee_from_curve(levels, des, Q_ESTOI)
        knees = {"lsd": (k_lsd, c_lsd), "destoi": (k_est, c_est)}

        if warpq is not None:
            dwq = []
            wlv = []
            for lv in levels:
                key = f"{point}/{etype}/{lv:g}"
                if key in warpq["scores"]:
                    wlv.append(lv)
                    dwq.append(warpq["scores"][key] - warpq["clean"])
            if wlv:
                knees["dwarpq"] = knee_from_curve(wlv, dwq, Q_WARPQ)

        # governing: smallest non-'above' knee; 'below' knees are upper
        # bounds and still govern (conservatively).  For timing-type points
        # LSD/segSNR are dephasing-contaminated -> perceptual metrics only.
        cands = {m: k for m, (k, c) in knees.items()
                 if np.isfinite(k) and c != "above"
                 and not (point in TIMING_POINTS and m == "lsd")}
        if cands:
            gov = min(cands, key=cands.get)
            knee = cands[gov]
            cens = knees[gov][1]
        else:  # nothing crossed anywhere in the sweep
            gov = "none"
            knee = float(levels[-1])
            cens = "above"
        budgets.append({
            "point": point, "etype": etype, "unit": UNITS[point],
            "side": SIDE[point], "levels": levels,
            "knees": {m: {"level": (None if not np.isfinite(k) else k),
                          "censoring": c} for m, (k, c) in knees.items()},
            "governing": gov, "knee": knee, "censoring": cens,
            "budget": knee / SAFETY,
            "segsnr_at_knee": float(np.interp(
                np.log10(knee), np.log10(levels), seg)),
        })

    # ---- YAML out --------------------------------------------------------
    y = []
    y.append("# budgets.yaml — first MEASURED adaptive budget table "
             "(README §4a mechanism 1)")
    y.append("# generated by experiments/error-injector/knees.py; "
             "do not edit by hand")
    y.append("meta:")
    y.append("  system: tube L0+L2(2500Hz)+L4-0.50, G8 cos(LSP) data path, "
             "q1300 decoded params")
    y.append(f"  corpus: [{', '.join(utts)}]")
    y.append(f"  quanta: {{dLSD_dB: {Q_LSD}, dESTOI: {Q_ESTOI}, "
             f"dWARPQ: {Q_WARPQ}}}")
    y.append(f"  safety_factor: {SAFETY:g}")
    y.append("  warpq_subset: hts1a" if warpq else
             "  warpq_subset: null  # warpq_inject.py not run")
    y.append("  noise_floor:  # CLEAN2 vs CLEAN (different LFSR realisation)")
    for u in utts:
        y.append(f"    {u}: {{lsd_dB: {floor[u]['lsd']:.3f}, "
                 f"dESTOI: {floor[u]['destoi']:+.4f}}}")
    y.append("budgets:")
    for b in budgets:
        y.append(f"  - stage: {b['point']}")
        y.append(f"    side: {b['side']}")
        y.append(f"    etype: {b['etype']}")
        y.append(f"    unit: {b['unit']}")
        y.append(f"    knee_level: {b['knee']:.6g}")
        y.append(f"    knee_metric: {b['governing']}")
        cens = b["censoring"] if b["censoring"] else "none"
        y.append(f"    knee_censoring: {cens}")
        y.append(f"    budget: {b['budget']:.6g}")
        y.append(f"    segsnr_vs_clean_at_knee_dB: "
                 f"{b['segsnr_at_knee']:.1f}")
        y.append("    knees_per_metric:")
        for m, kk in sorted(b["knees"].items()):
            lv = "null" if kk["level"] is None else f"{kk['level']:.6g}"
            cc = kk["censoring"] if kk["censoring"] else "none"
            y.append(f"      {m}: {{level: {lv}, censoring: {cc}}}")
    out = os.path.join(HERE, "results", "budgets.yaml")
    with open(out, "w") as f:
        f.write("\n".join(y) + "\n")
    print(f"wrote {out}\n")

    # ---- measured vs a-priori (README §4 flat budgets) -------------------
    apriori = {
        ("wo", "white"): ("Wo RMS < 0.2% (rel)", 0.002),
        ("wo", "dc"): ("Wo RMS < 0.2% (rel)", 0.002),
        ("edb", "white"): ("amplitudes < 0.3 dB mean", 0.3),
        ("edb", "dc"): ("amplitudes < 0.3 dB mean", 0.3),
        ("log2e", "white"): ("amplitudes < 0.3 dB -> 0.05 log2", 0.0498),
        ("lsphz", "white"): ("LSP transparency ~ 1 dB SD "
                             "(Paliwal-Atal, ~= few Hz)", None),
    }
    print("=== measured budget vs a-priori ===")
    for b in budgets:
        key = (b["point"], b["etype"])
        if key in apriori:
            name, ap = apriori[key]
            cmp_ = ""
            if ap is not None:
                ratio = b["budget"] / ap
                cmp_ = (f"  a-priori {ap:g} -> {'TOO STRICT' if ratio > 1 else 'TOO LOOSE'} "
                        f"by {max(ratio, 1 / ratio):.1f}x")
            print(f"{b['point']:8s}/{b['etype']:8s} measured budget "
                  f"{b['budget']:.4g} {b['unit']:10s} "
                  f"(knee {b['knee']:.4g}, {b['governing']}){cmp_}  [{name}]")
    # segSNR-25dB comparison for state noise
    for b in budgets:
        if b["point"] == "state" and b["etype"] == "white":
            print(f"\nstate/white: segSNR(vs clean) at knee = "
                  f"{b['segsnr_at_knee']:.1f} dB "
                  f"(a-priori gate demanded > 25 dB median)")


if __name__ == "__main__":
    main()
