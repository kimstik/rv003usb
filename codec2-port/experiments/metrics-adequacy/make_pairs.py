#!/usr/bin/env python3
"""make_pairs.py — build the buzzy-vs-smooth degradation stand.

For every utterance (decoded-1300 params + c2sim phase0 reference synthesis):

1. RESYNTHESIS TIER (character fixed by construction, LSD lands where it
   lands — verified matched within ~1 dB post hoc):
     buzz-l0, smooth-mix-800, smooth-mix-1500,
     par-plain, par-noise-1000, par-noise-2000
   Aligned to the reference by the metric-optimal constant lag
   (tube-ladder lesson: envelope xcorr is biased on parametric speech).

2. CORRUPTION TIER (character orthogonal to level; level auto-tuned so every
   member hits the SAME classic cost): for each LSD target T in {2, 4} dB,
   bisect each corruption's knob until |LSD - T| <= 0.15 dB:
     buzz-spur@T, buzz-pump@T, buzz-sharp@T   (buzzy family)
     smooth-valley@T, smooth-dither@T          (smooth family)
   -> matched pairs by construction (any buzzy x smooth combo at same T).

Classic judges are computed here (LSD, segSNR, NMR-proxy, crest delta,
ESTOI vs ref and vs original).  WARP-Q and neural judges run separately
(run_warpq.py, run_neural.py) on the wavs this script writes.

Outputs: build/wavs/<utt>.<variant>.wav (aligned, trimmed), results/classic.csv,
results/manifest.json (variant -> family, knob, wav path, classic metrics).
"""
import csv
import json
import os
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, os.pardir, "tube-ladder"))
import degrade                     # noqa: E402
import metrics_ladder as M         # noqa: E402

FS = 8000
N = 80
UTTS = ["hts1a", "hts2a", "ve9qrp_10s"]
LSD_TARGETS = [2.0, 4.0]
TOL = 0.15

DUMPS = os.path.join(HERE, "build", "dumps", "q1300")
RAWDIR = os.path.join(HERE, "build", "codec2", "raw")
WAVS = os.path.join(HERE, "build", "wavs")
RESULTS = os.path.join(HERE, "results")


def load_params(npz_path):
    z = dict(np.load(npz_path))
    return {"Wo": z.get("Wo_dec", z["Wo"]), "L": z.get("L_dec", z["L"]),
            "voiced": z["voiced"], "ak": z["ak_dec"], "A": z["A_lpc"],
            "snr_mbe": z.get("snr_mbe")}


def write_wav(path, x):
    xi = np.clip(np.round(x), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(xi.tobytes())


def classic_metrics(ref_a, y_a, orig, params, lag):
    row = {}
    row.update(M.lsd_stats(y_a, ref_a))
    row.update(M.seg_snr(ref_a, y_a))
    row.update(M.nmr_proxy_stats(y_a, ref_a, params["ak"], lag))
    row.update(M.crest_stats(y_a, ref_a))
    row["estoi_ref"] = M.estoi(ref_a, y_a)
    n = min(len(orig), len(y_a))
    row["estoi_orig"] = M.estoi(orig[:n], y_a[:n])
    return row


def tune_knob(name, spec, ref, params, target):
    """Bisect the knob (monotone in LSD) to hit `target` dB LSD +- TOL."""
    lo, hi = spec["lo"], spec["hi"]

    def lsd_of(k):
        y = spec["fn"](ref, params, k)
        return M.lsd_stats(y, ref)["lsd_mean"], y

    llo, _ = lsd_of(lo)
    lhi, _ = lsd_of(hi)
    if not (min(llo, lhi) - 0.3 <= target <= max(llo, lhi) + 0.3):
        raise RuntimeError(f"{name}: target {target} outside "
                           f"[{llo:.2f},{lhi:.2f}] for knob [{lo},{hi}]")
    a, b, la, lb = lo, hi, llo, lhi
    k = None
    for _ in range(24):
        # interpolate in log-domain for wide-range knobs, linear otherwise
        frac = (target - la) / (lb - la) if lb != la else 0.5
        frac = min(max(frac, 0.15), 0.85)
        k = a + frac * (b - a)
        lk, y = lsd_of(k)
        if abs(lk - target) <= TOL:
            return k, y, lk
        if (lk < target) == (la < target):
            a, la = k, lk
        else:
            b, lb = k, lk
    return k, y, lk   # best effort after 24 iters (report actual LSD)


def main():
    os.makedirs(WAVS, exist_ok=True)
    os.makedirs(RESULTS, exist_ok=True)
    manifest = {}
    rows = []

    for utt in UTTS:
        d = os.path.join(DUMPS, utt)
        params = load_params(os.path.join(d, f"{utt}.npz"))
        ref = np.fromfile(os.path.join(d, f"{utt}_ref.raw"),
                          dtype="<i2").astype(float)
        orig = np.fromfile(os.path.join(RAWDIR, f"{utt}.raw"),
                           dtype="<i2").astype(float)

        write_wav(os.path.join(WAVS, f"{utt}.ref.wav"), ref)
        write_wav(os.path.join(WAVS, f"{utt}.orig.wav"), orig)
        manifest[f"{utt}.ref"] = {"utt": utt, "variant": "ref",
                                  "family": "anchor", "tier": "anchor",
                                  "wav": f"{utt}.ref.wav"}
        manifest[f"{utt}.orig"] = {"utt": utt, "variant": "orig",
                                   "family": "anchor", "tier": "anchor",
                                   "wav": f"{utt}.orig.wav"}

        # ---------------- resynthesis tier ----------------
        resynth = {
            "buzz-l0": ("buzzy", lambda: degrade.buzz_l0(params)),
            "smooth-mix-800": ("smooth",
                               lambda: degrade.smooth_mix(params, 800.0)),
            "smooth-mix-1500": ("smooth",
                                lambda: degrade.smooth_mix(params, 1500.0)),
            "par-plain": ("buzzy", lambda: degrade.synth_parallel(params)),
            "par-noise-1000": ("smooth", lambda: degrade.synth_parallel(
                params, noise_above_hz=1000.0)),
            "par-noise-2000": ("smooth", lambda: degrade.synth_parallel(
                params, noise_above_hz=2000.0)),
        }
        for vname, (family, fn) in resynth.items():
            y = fn()
            lag = M.find_lag_lsd(ref, y)
            ref_a, y_a = M.apply_lag(ref, y, lag)
            row = {"utt": utt, "variant": vname, "family": family,
                   "tier": "resynth", "knob": "", "lag": lag}
            row.update(classic_metrics(ref_a, y_a, orig, params, lag))
            rows.append(row)
            wav = f"{utt}.{vname}.wav"
            write_wav(os.path.join(WAVS, wav), y_a)
            # aligned reference for full-reference judges of this variant
            write_wav(os.path.join(WAVS, f"{utt}.ref_for.{vname}.wav"), ref_a)
            manifest[f"{utt}.{vname}"] = dict(row, wav=wav,
                                              ref_wav=f"{utt}.ref_for.{vname}.wav")
            print(f"  {utt} {vname:16s} LSD {row['lsd_mean']:.2f} "
                  f"crestD {row['crest_delta_median']:+.2f} lag {lag}")

        # ---------------- corruption tier -----------------
        for T in LSD_TARGETS:
            for cname, spec in degrade.CORRUPTIONS.items():
                vname = f"{cname}@{T:g}"
                try:
                    k, y, lk = tune_knob(cname, spec, ref, params, T)
                except RuntimeError as e:
                    print(f"  {utt} {vname}: SKIP ({e})")
                    continue
                row = {"utt": utt, "variant": vname, "family": spec["family"],
                       "tier": f"corrupt@{T:g}",
                       "knob": f"{spec['knob']}={k:.4g}", "lag": 0}
                row.update(classic_metrics(ref, y, orig, params, 0))
                rows.append(row)
                wav = f"{utt}.{vname}.wav"
                write_wav(os.path.join(WAVS, wav), y)
                manifest[f"{utt}.{vname}"] = dict(row, wav=wav,
                                                  ref_wav=f"{utt}.ref.wav")
                print(f"  {utt} {vname:22s} knob {row['knob']:16s} "
                      f"LSD {row['lsd_mean']:.2f}")

    keys = ["utt", "variant", "family", "tier", "knob", "lag", "lsd_mean",
            "lsd_median", "lsd_p90", "segsnr_mean", "segsnr_median",
            "nmr_median", "nmr_p90", "crest_delta_median", "crest_delta_p90",
            "estoi_ref", "estoi_orig"]
    with open(os.path.join(RESULTS, "classic.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(RESULTS, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)
    print(f"wrote {len(rows)} variants -> results/classic.csv, manifest.json")


if __name__ == "__main__":
    main()
