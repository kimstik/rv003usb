#!/usr/bin/env python3
"""run_knees.py — synthesize and measure the two EXACT recommended ladder
subsets that round 2/3 recommended but never measured (pareto REPORT.md
coverage matrix, gap #2):

  P1-knee = L0+L1+L2(2.5k)+L4(g1=0.50)   tube-ladder P1 recommendation
  P2-knee = L0+L2(2.5k)+L4(g1=0.50)      tube-ladder P2 recommendation (no L1)

Both differ from the measured trunk rung L4-0.50 (= L0+L1+L2-2000+L3+L4) in
crossover (2500 vs 2000), absence of jitter (L3), and — for P2 — absence of
the dispersion FIR (L1).

Reuse policy (no re-derivation): synthesis is tube-ladder/tube.py verbatim.
  - jitter off: synth_ladder(jitter_frac=0.0) — with jit==0 the code takes the
    exact L2 path (no extra LFSR draws), so rung=4 + jitter_frac=0 IS
    L0+L1+L2+L4 sample-exactly.
  - dispersion off (P2-knee): tube.make_dispersion_filter monkeypatched to
    return None; synth_ladder already handles disp=None (2-tap split), which
    is exactly the L0 impulse path.
Metrics are metrics_ladder.py verbatim, same row schema as run_ladder.py
(metric-optimal constant lag per pair, q1300 condition, 3 utterances).

Inputs : tube-ladder/build/dumps/q1300/<utt>/  (make_dumps.sh)
Outputs: results/knees_metrics.csv, build/wavs/q1300_<utt>_<knee>.wav
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
LADDER = os.path.abspath(os.path.join(HERE, "..", "tube-ladder"))
sys.path.insert(0, LADDER)

import metrics_ladder as M           # noqa: E402
import tube                          # noqa: E402
from run_ladder import load_params, write_wav   # noqa: E402

UTTS = ["hts1a", "hts2a", "ve9qrp_10s"]
COND = "q1300"

KNEES = [
    # name, kwargs, disable_dispersion
    ("P1-knee", dict(rung=4, crossover_hz=2500.0, jitter_frac=0.0,
                     pf_g1=0.50, pf_g2=0.8), False),
    ("P2-knee", dict(rung=4, crossover_hz=2500.0, jitter_frac=0.0,
                     pf_g1=0.50, pf_g2=0.8), True),
]


def synth(params, kw, no_disp):
    if not no_disp:
        return tube.synth_ladder(params, **kw)
    orig_fn = tube.make_dispersion_filter
    tube.make_dispersion_filter = lambda *a, **k: None
    try:
        return tube.synth_ladder(params, **kw)
    finally:
        tube.make_dispersion_filter = orig_fn


def main():
    results_dir = os.path.join(HERE, "results")
    wav_dir = os.path.join(HERE, "build", "wavs")
    os.makedirs(results_dir, exist_ok=True)
    os.makedirs(wav_dir, exist_ok=True)

    rows = []
    for utt in UTTS:
        d = os.path.join(LADDER, "build", "dumps", COND, utt)
        params = load_params(os.path.join(d, f"{utt}.npz"))
        ref = np.fromfile(os.path.join(d, f"{utt}_ref.raw"),
                          dtype="<i2").astype(float)
        orig = np.fromfile(os.path.join(LADDER, "build", "codec2", "raw",
                                        f"{utt}.raw"),
                           dtype="<i2").astype(float)
        e_ref_orig = M.estoi(orig, ref)

        for name, kw, no_disp in KNEES:
            y = synth(params, kw, no_disp)
            lag = M.find_lag_lsd(ref, y)
            ref_a, y_a = M.apply_lag(ref, y, lag)
            ref_off = lag if lag > 0 else 0
            row = {"cond": COND, "utt": utt, "variant": name, "lag": lag}
            row.update(M.lsd_stats(y_a, ref_a))
            row.update(M.nmr_proxy_stats(y_a, ref_a, params["ak"], ref_off))
            row.update(M.seg_snr(ref_a, y_a))
            row.update(M.crest_stats(y_a, ref_a))
            row["estoi_ref"] = M.estoi(ref_a, y_a)
            n = min(len(orig), len(y))
            row["estoi_orig"] = M.estoi(orig[:n], y[:n])
            row["estoi_ref_vs_orig"] = e_ref_orig
            rows.append(row)
            write_wav(os.path.join(wav_dir, f"{COND}_{utt}_{name}.wav"), y)
            print(f"{COND:6s} {utt:11s} {name:8s} lag {lag:4d} "
                  f"LSD {row['lsd_mean']:.2f} dB  "
                  f"NMR {row['nmr_median']:+.1f} dB  "
                  f"crest {row['crest_delta_median']:+.1f} dB  "
                  f"ESTOI(ref) {row['estoi_ref']:.3f}  "
                  f"ESTOI(orig) {row['estoi_orig']:.3f} "
                  f"[ref itself: {e_ref_orig:.3f}]", flush=True)

    keys = list(rows[0].keys())
    csvp = os.path.join(results_dir, "knees_metrics.csv")
    with open(csvp, "w") as f:
        f.write(",".join(keys) + "\n")
        for r in rows:
            f.write(",".join(f"{r[k]:.4f}" if isinstance(r[k], float)
                             else str(r[k]) for k in keys) + "\n")

    # aggregate (mean over utterances), same keys as run_ladder aggregate
    import json
    agg = {}
    for name, _, _ in KNEES:
        sel = [r for r in rows if r["variant"] == name]
        agg[name] = {k: float(np.mean([r[k] for r in sel]))
                     for k in ("lsd_mean", "lsd_median", "lsd_p90",
                               "nmr_median", "nmr_p90", "segsnr_mean",
                               "crest_delta_median", "estoi_ref",
                               "estoi_orig", "estoi_ref_vs_orig")}
    with open(os.path.join(results_dir, "knees_aggregate.json"), "w") as f:
        json.dump(agg, f, indent=1)
    print(f"wrote {csvp} and knees_aggregate.json; wavs in {wav_dir}")


if __name__ == "__main__":
    main()
