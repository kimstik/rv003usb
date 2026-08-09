#!/usr/bin/env python3
"""run_ladder.py — synthesize every ladder rung on every utterance/condition,
measure against the c2sim phase0 reference synthesis (and the original), write
results/metrics.csv + results/aggregate.json + wavs for listening/WARP-Q.

Rung variants (the ladder is cumulative; sweeps branch off the main trunk):
  L0                 binary excitation (impulse train / LFSR noise)
  L1                 + pulse dispersion (65-tap MELP-style)
  L2-<fc>            + mixed excitation, crossover fc in {1500,2000,2500} Hz
  L3                 + aperiodic jitter (+-25% period, weakly-voiced frames),
                     on top of L2-2000
  L4-<g1>            + spectral postfilter A(z/g1)/A(z/0.8) + tilt comp,
                     g1 in {0.5,0.65,0.75}, on top of L3 (fc=2000)

Conditions: uq (unquantised 10 ms params) and q1300 (c2sim --rate 1300:
fully quantised + 4x decimated/interpolated params — the real P1/P2 decode).
"""
import json
import os
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import metrics_ladder as M          # noqa: E402
import tube                          # noqa: E402

UTTS = ["hts1a", "hts2a", "ve9qrp_10s"]
CONDS = ["uq", "q1300"]
FS = 8000
N = 80

VARIANTS = [
    ("L0", dict(rung=0)),
    ("L1", dict(rung=1)),
    ("L2-1500", dict(rung=2, crossover_hz=1500.0)),
    ("L2-2000", dict(rung=2, crossover_hz=2000.0)),
    ("L2-2500", dict(rung=2, crossover_hz=2500.0)),
    ("L3", dict(rung=3, crossover_hz=2000.0)),
    ("L4-0.50", dict(rung=4, crossover_hz=2000.0, pf_g1=0.50, pf_g2=0.8)),
    ("L4-0.65", dict(rung=4, crossover_hz=2000.0, pf_g1=0.65, pf_g2=0.8)),
    ("L4-0.75", dict(rung=4, crossover_hz=2000.0, pf_g1=0.75, pf_g2=0.8)),
]


def write_wav(path, x):
    xi = np.clip(np.round(x), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(xi.tobytes())


def load_params(npz_path):
    z = dict(np.load(npz_path))
    return {
        "Wo": z.get("Wo_dec", z["Wo"]),
        "L": z.get("L_dec", z["L"]),
        "voiced": z["voiced"],
        "ak": z["ak_dec"],
        "A": z["A_lpc"],
        "snr_mbe": z.get("snr_mbe"),
    }


def main():
    results_dir = os.path.join(HERE, "results")
    os.makedirs(results_dir, exist_ok=True)
    wav_dir = os.path.join(HERE, "build", "wavs")
    os.makedirs(wav_dir, exist_ok=True)

    rows = []
    for cond in CONDS:
        for utt in UTTS:
            d = os.path.join(HERE, "build", "dumps", cond, utt)
            params = load_params(os.path.join(d, f"{utt}.npz"))
            ref = np.fromfile(os.path.join(d, f"{utt}_ref.raw"),
                              dtype="<i2").astype(float)
            orig = np.fromfile(os.path.join(HERE, "build", "codec2", "raw",
                                            f"{utt}.raw"),
                               dtype="<i2").astype(float)
            write_wav(os.path.join(wav_dir, f"{cond}_{utt}_ref.wav"), ref)
            write_wav(os.path.join(wav_dir, f"{cond}_{utt}_orig.wav"), orig)

            # context row: how far the reference itself is from the original
            e_ref_orig = M.estoi(orig, ref)

            for name, kw in VARIANTS:
                y = tube.synth_ladder(params, **kw)
                lag = M.find_lag(ref, y)          # >0: tube early vs ref
                ref_a, y_a = M.apply_lag(ref, y, lag)
                # dump-frame offset of aligned position p: p + lag_trim
                ref_off = lag if lag > 0 else 0
                row = {"cond": cond, "utt": utt, "variant": name,
                       "lag": lag}
                row.update(M.lsd_stats(y_a, ref_a))
                row.update(M.nmr_proxy_stats(y_a, ref_a, params["ak"],
                                             ref_off))
                row.update(M.seg_snr(ref_a, y_a))
                row["estoi_ref"] = M.estoi(ref_a, y_a)
                n = min(len(orig), len(y))
                row["estoi_orig"] = M.estoi(orig[:n], y[:n])
                row["estoi_ref_vs_orig"] = e_ref_orig
                rows.append(row)
                wavp = os.path.join(wav_dir, f"{cond}_{utt}_{name}.wav")
                write_wav(wavp, y)
                print(f"{cond:6s} {utt:11s} {name:8s} lag {lag:4d} "
                      f"LSD {row['lsd_mean']:.2f} dB  "
                      f"NMR {row['nmr_median']:+.1f} dB  "
                      f"segSNR {row['segsnr_mean']:.1f} dB  "
                      f"ESTOI(ref) {row['estoi_ref']:.3f}  "
                      f"ESTOI(orig) {row['estoi_orig']:.3f} "
                      f"[ref itself: {e_ref_orig:.3f}]", flush=True)

    # ---- write CSV -------------------------------------------------------
    keys = list(rows[0].keys())
    csvp = os.path.join(results_dir, "metrics.csv")
    with open(csvp, "w") as f:
        f.write(",".join(keys) + "\n")
        for r in rows:
            f.write(",".join(f"{r[k]:.4f}" if isinstance(r[k], float)
                             else str(r[k]) for k in keys) + "\n")

    # ---- aggregate over utterances (mean of per-utt values) --------------
    agg = {}
    for cond in CONDS:
        agg[cond] = {}
        for name, _ in VARIANTS:
            sel = [r for r in rows if r["cond"] == cond
                   and r["variant"] == name]
            agg[cond][name] = {
                k: float(np.mean([r[k] for r in sel]))
                for k in ("lsd_mean", "lsd_median", "lsd_p90", "nmr_median",
                          "nmr_p90", "segsnr_mean", "estoi_ref", "estoi_orig",
                          "estoi_ref_vs_orig")}
    with open(os.path.join(results_dir, "aggregate.json"), "w") as f:
        json.dump(agg, f, indent=1)
    print(f"\nwrote {csvp} and aggregate.json; wavs in {wav_dir}")


if __name__ == "__main__":
    main()
