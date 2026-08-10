#!/usr/bin/env python3
"""analyze.py — adjudicate the buzzy-vs-smooth pairs, test judge adequacy,
derive the asymmetric taxonomy gates, and emit the G3+noise verdict table.

Inputs: results/classic.csv, results/neural.csv, results/warpq.json
Outputs: results/pairs.csv       one row per matched pair x judge verdicts
         results/adequacy.json   per-judge agreement with the neural consensus
         results/axes.csv        per-variant neural axis deltas vs reference
         results/gates_h1.yaml   proposed asymmetric gates fragment
         stdout summary tables (pasted into REPORT.md)

Conventions.  "Perceptual proxy consensus" = mean z-normalised overall score
of the neural judges (dns_ovrl, dns_p808, nisqa_mos) — the stand's stand-in
for human preference (H1 explicitly calls for NISQA/DNSMOS as the arbiter,
ears later, out of CI).  A judge "agrees" on a pair when it prefers the same
member as the consensus; ties within judge noise (see EPS) are "abstain".
"""
import csv
import json
import os
from collections import defaultdict

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")

# judge -> (column, direction: +1 higher-better / -1 lower-better, epsilon)
JUDGES = {
    "LSD":        ("lsd_mean", -1, 0.30),
    "segSNR":     ("segsnr_mean", +1, 0.5),
    "NMR":        ("nmr_median", -1, 0.5),
    "crest|d|":   ("crest_abs", -1, 0.3),      # |crest delta|: 0 = ref texture
    "ESTOI_ref":  ("estoi_ref", +1, 0.01),
    "ESTOI_orig": ("estoi_orig", +1, 0.01),
    "WARP-Q":     ("warpq", -1, 0.03),
    "DNS_SIG":    ("dns_sig", +1, 0.05),
    "DNS_BAK":    ("dns_bak", +1, 0.05),
    "DNS_OVRL":   ("dns_ovrl", +1, 0.05),
    "DNS_P808":   ("dns_p808", +1, 0.05),
    "NISQA_MOS":  ("nisqa_mos", +1, 0.05),
    "NISQA_Noi":  ("nisqa_noi", +1, 0.05),
    "NISQA_Col":  ("nisqa_col", +1, 0.05),
    "NISQA_Dis":  ("nisqa_dis", +1, 0.05),
}
CONSENSUS = ["dns_ovrl", "dns_p808", "nisqa_mos"]


def load():
    rows = {}
    with open(os.path.join(RESULTS, "classic.csv")) as fh:
        for r in csv.DictReader(fh):
            key = f"{r['utt']}.{r['variant']}"
            rows[key] = {k: (float(v) if k not in
                             ("utt", "variant", "family", "tier", "knob")
                             and v != "" else v) for k, v in r.items()}
    with open(os.path.join(RESULTS, "neural.csv")) as fh:
        for r in csv.DictReader(fh):
            key = r["name"]
            d = rows.setdefault(key, {"utt": r["utt"], "variant": r["variant"],
                                      "family": r["family"],
                                      "tier": r["tier"]})
            for k in ("dns_sig", "dns_bak", "dns_ovrl", "dns_p808",
                      "nisqa_mos", "nisqa_noi", "nisqa_col", "nisqa_dis",
                      "nisqa_loud"):
                if r.get(k):
                    d[k] = float(r[k])
    with open(os.path.join(RESULTS, "warpq.json")) as fh:
        for k, v in json.load(fh).items():
            if k in rows:
                rows[k]["warpq"] = float(v)
    for d in rows.values():
        if "crest_delta_median" in d and d.get("crest_delta_median") != "":
            d["crest_abs"] = abs(d["crest_delta_median"])
    return rows


def consensus_score(rows):
    """z-normalise each neural overall metric across non-anchor variants,
    return mean z per variant (higher = perceptual-proxy better)."""
    keys = [k for k, d in rows.items() if d.get("family") != "anchor"]
    z = {k: [] for k in keys}
    for m in CONSENSUS:
        vals = np.array([rows[k].get(m, np.nan) for k in keys], dtype=float)
        mu, sd = np.nanmean(vals), np.nanstd(vals)
        if not np.isfinite(sd) or sd == 0:
            continue
        for k, v in zip(keys, vals):
            if np.isfinite(v):
                z[k].append((v - mu) / sd)
    return {k: float(np.mean(v)) if v else np.nan for k, v in z.items()}


def make_pairs(rows):
    """Matched pairs: corruption tier (same utt, same T, buzzy x smooth);
    resynthesis tier (same utt, buzzy x smooth, |dLSD| <= 1.2 dB)."""
    by_utt_tier = defaultdict(lambda: {"buzzy": [], "smooth": []})
    for k, d in rows.items():
        if d.get("family") in ("buzzy", "smooth"):
            by_utt_tier[(d["utt"], d["tier"])][d["family"]].append(k)
    pairs = []
    for (utt, tier), fam in sorted(by_utt_tier.items()):
        for b in sorted(fam["buzzy"]):
            for s in sorted(fam["smooth"]):
                dlsd = abs(rows[b]["lsd_mean"] - rows[s]["lsd_mean"])
                if tier == "resynth" and dlsd > 1.2:
                    continue
                pairs.append((utt, tier, b, s, dlsd))
    return pairs


def main():
    rows = load()
    cons = consensus_score(rows)
    pairs = make_pairs(rows)

    out_rows = []
    agree = defaultdict(lambda: [0, 0, 0])   # judge -> [agree, disagree, abst]
    smooth_pref = defaultdict(lambda: [0, 0, 0])  # judge -> smooth/buzzy/abst
    for (utt, tier, b, s, dlsd) in pairs:
        cb, cs_ = cons.get(b, np.nan), cons.get(s, np.nan)
        cons_pref = ("smooth" if cs_ > cb else "buzzy") \
            if np.isfinite(cb) and np.isfinite(cs_) else "n/a"
        rec = {"utt": utt, "tier": tier, "buzzy": rows[b]["variant"],
               "smooth": rows[s]["variant"], "dLSD": round(dlsd, 2),
               "consensus_pref": cons_pref,
               "consensus_delta": round(cs_ - cb, 3)
               if np.isfinite(cb) and np.isfinite(cs_) else ""}
        for jname, (col, sgn, eps) in JUDGES.items():
            vb, vs = rows[b].get(col), rows[s].get(col)
            if vb in (None, "") or vs in (None, ""):
                rec[jname] = ""
                continue
            d = (float(vs) - float(vb)) * sgn
            pref = "smooth" if d > eps else ("buzzy" if d < -eps else "tie")
            rec[jname] = pref
            i = {"smooth": 0, "buzzy": 1, "tie": 2}[pref]
            smooth_pref[jname][i] += 1
            if cons_pref != "n/a":
                if pref == "tie":
                    agree[jname][2] += 1
                elif pref == cons_pref:
                    agree[jname][0] += 1
                else:
                    agree[jname][1] += 1
        out_rows.append(rec)

    with open(os.path.join(RESULTS, "pairs.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)

    # ---- per-judge adequacy summary --------------------------------------
    print("\n=== Judge adequacy: agreement with neural consensus over "
          f"{len(pairs)} matched pairs ===")
    print(f"{'judge':12s} {'agree':>6s} {'against':>8s} {'tie':>5s} "
          f"{'pro-smooth':>11s} {'pro-buzzy':>10s}")
    adequacy = {}
    for j in JUDGES:
        a, d, t = agree[j]
        sm, bz, ti = smooth_pref[j]
        tot = a + d
        adequacy[j] = {"agree": a, "against": d, "tie": t,
                       "agree_rate": round(a / tot, 3) if tot else None,
                       "pro_smooth": sm, "pro_buzzy": bz, "ties": ti}
        print(f"{j:12s} {a:6d} {d:8d} {t:5d} {sm:11d} {bz:10d}")
    with open(os.path.join(RESULTS, "adequacy.json"), "w") as fh:
        json.dump({"n_pairs": len(pairs), "judges": adequacy}, fh, indent=1)

    # ---- neural axis deltas vs reference (gate calibration) --------------
    axes_rows = []
    axis_cols = ["dns_sig", "dns_bak", "dns_ovrl", "dns_p808",
                 "nisqa_mos", "nisqa_noi", "nisqa_col", "nisqa_dis"]
    for k, d in sorted(rows.items()):
        if d.get("family") not in ("buzzy", "smooth"):
            continue
        ref = rows.get(f"{d['utt']}.ref", {})
        rec = {"utt": d["utt"], "variant": d["variant"],
               "family": d["family"], "tier": d["tier"],
               "lsd": d.get("lsd_mean")}
        for c in axis_cols:
            if c in d and c in ref:
                rec["d_" + c] = round(d[c] - ref[c], 3)
        axes_rows.append(rec)
    with open(os.path.join(RESULTS, "axes.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(axes_rows[0].keys()),
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(axes_rows)

    def fam_axis(family, tier_prefix, col):
        v = [r.get("d_" + col) for r in axes_rows
             if r["family"] == family and r["tier"].startswith(tier_prefix)
             and r.get("d_" + col) is not None]
        return (float(np.mean(v)), float(np.min(v))) if v else (np.nan, np.nan)

    print("\n=== Neural axis deltas vs ref (mean / worst over variants) ===")
    print(f"{'axis':10s} {'buzzy@2':>16s} {'smooth@2':>16s} "
          f"{'buzzy@4':>16s} {'smooth@4':>16s}")
    for col in axis_cols:
        cells = []
        for tier in ("corrupt@2", "corrupt@4"):
            for fam in ("buzzy", "smooth"):
                m, w = fam_axis(fam, tier, col)
                cells.append(f"{m:+.2f}/{w:+.2f}")
        print(f"{col:10s} {cells[0]:>16s} {cells[1]:>16s} "
              f"{cells[2]:>16s} {cells[3]:>16s}")

    # ---- G3 verdict table -------------------------------------------------
    print("\n=== G3+noise (par-noise) vs plain parallel / L0 tube ===")
    cols = ["lsd_mean", "crest_delta_median", "estoi_orig", "warpq",
            "dns_sig", "dns_bak", "dns_ovrl", "dns_p808",
            "nisqa_mos", "nisqa_noi", "nisqa_col", "nisqa_dis"]
    hdr = "utt/variant".ljust(26) + " ".join(c.replace("_median", "")
                                             .replace("_mean", "")[:9].rjust(9)
                                             for c in cols)
    print(hdr)
    for utt in sorted({d["utt"] for d in rows.values() if "utt" in d}):
        for v in ("ref", "buzz-l0", "par-plain", "par-noise-1000",
                  "par-noise-2000", "smooth-mix-800", "smooth-mix-1500"):
            d = rows.get(f"{utt}.{v}")
            if not d:
                continue
            cells = []
            for c in cols:
                x = d.get(c)
                cells.append(f"{x:9.2f}" if isinstance(x, float) else " " * 9)
            print(f"{utt + '.' + v:26s}" + " ".join(cells))

    # ---- proposed gates fragment -----------------------------------------
    # calibrated: smooth family @4 dB LSD = "comfortable" operating point the
    # H-principle wants ALLOWED; buzzy family @2 dB LSD already lands beneath
    # the consensus -> its axis signature must be BLOCKED.
    def q(family, tier, col, agg):
        v = [r.get("d_" + col) for r in axes_rows
             if r["family"] == family and r["tier"] == tier
             and r.get("d_" + col) is not None]
        return float(agg(v)) if v else float("nan")

    gates = {
        "h1_asymmetric_gates": {
            "comment": "calibrated on metrics-adequacy stand; deltas vs the "
                       "float reference decode of the same bitstream",
            "soft_noisiness": {
                "nisqa_noi_drop_max": round(
                    max(0.0, -q("smooth", "corrupt@4", "nisqa_noi", np.min))
                    + 0.1, 2),
                "dns_bak_drop_max": round(
                    max(0.0, -q("smooth", "corrupt@4", "dns_bak", np.min))
                    + 0.1, 2),
            },
            "hard_coloration": {
                "nisqa_col_drop_max": round(
                    max(0.05, -q("smooth", "corrupt@4", "nisqa_col", np.min))
                    , 2),
                "dns_sig_drop_max": round(
                    max(0.05, -q("smooth", "corrupt@4", "dns_sig", np.min))
                    , 2),
            },
            "hard_discontinuity": {
                "nisqa_dis_drop_max": round(
                    max(0.05, -q("smooth", "corrupt@4", "nisqa_dis", np.min))
                    , 2),
            },
            "form_invariants": {
                "crest_delta_abs_max_db": 1.0,
                "comment": "crest|d| was the only classic judge tracking the "
                           "consensus; keep as cheap CI-side buzz detector",
            },
        }
    }
    with open(os.path.join(RESULTS, "gates_h1.yaml"), "w") as fh:
        try:
            import yaml
            yaml.safe_dump(gates, fh, sort_keys=False)
        except ImportError:
            json.dump(gates, fh, indent=1)
    print("\nwrote results/pairs.csv, adequacy.json, axes.csv, gates_h1.yaml")


if __name__ == "__main__":
    main()
