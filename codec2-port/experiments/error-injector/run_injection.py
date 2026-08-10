#!/usr/bin/env python3
"""run_injection.py — the sensitivity-analysis sweep (README §4a mechanism 1).

For every (injection point x error type x amplitude level) on every utterance:
synthesize through the L0+L2+L4 tube with the calibrated error injected,
measure end-to-end against (a) the CLEAN tube synthesis of the same params
(LSD / envelope-NMR / crest delta / segSNR — pure injection effect, zero lag
by construction: same time base, same LFSR) and (b) the ORIGINAL speech
(ESTOI; WARP-Q later on the hts1a subset via warpq_inject.py).

Writes results/curves.csv (one row per utt x point x etype x level, plus
CLEAN and CLEAN2 baseline rows) and build/wavs/ for hts1a (WARP-Q subset).

Baselines:
  CLEAN   inj=None                       -> reference for all deltas
  CLEAN2  inj=None, different LFSR seed  -> metric noise floor (different
          noise realisation, identical system): knees must sit above this.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "tube-ladder"))
import inject_tube as it            # noqa: E402
import metrics_ladder as M          # noqa: E402

DUMPS = os.path.join(HERE, "..", "tube-ladder", "build", "dumps", "q1300")
RAWD = os.path.join(HERE, "..", "tube-ladder", "build", "codec2", "raw")
UTTS = ["hts1a", "hts2a", "ve9qrp_10s"]
WAV_UTT = "hts1a"                   # WARP-Q subset (timebox)

# level sweeps: 4-6 log-spaced (x4) points per injection point
SWEEP = {
    # point: (unit, etypes, levels)
    "coslsp": ("cos units", ["white", "framemod", "dc", "worst"],
               [1e-4, 4e-4, 1.6e-3, 6.4e-3, 2.56e-2, 0.1024]),
    "state": ("int16 LSB", ["white", "framemod", "dc"],
              [0.25, 1.0, 4.0, 16.0, 64.0, 256.0]),
    "pulsepos": ("samples", ["white", "qround", "qtrunc"],
                 [1.0 / 32, 1.0 / 8, 0.5, 2.0, 8.0]),
    "log2e": ("log2 units", ["white", "framemod", "dc"],
              [0.002, 0.008, 0.032, 0.128, 0.512]),
    "mixfc": ("relative", ["dc", "white"],
              [0.0025, 0.01, 0.04, 0.16, 0.64]),
    "mixnf": ("dB", ["dc", "white"],
              [0.25, 1.0, 4.0, 16.0]),
    "wo": ("relative", ["white", "dc", "framemod"],
           [5e-4, 2e-3, 8e-3, 3.2e-2, 0.128]),
    "lsphz": ("Hz", ["white", "dc", "worst"],
              [0.5, 2.0, 8.0, 32.0, 128.0]),
    "edb": ("dB", ["white", "dc"],
            [0.05, 0.2, 0.8, 3.2, 12.8]),
}


def write_wav(path, x):
    import wave
    xi = np.clip(np.round(x), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(xi.tobytes())


def measure(y, clean, orig, ak):
    """All end-to-end metrics for one injected synthesis."""
    row = {}
    row.update(M.lsd_stats(y, clean))
    row.update(M.nmr_proxy_stats(y, clean, ak, 0))
    row.update(M.seg_snr(clean, y))
    row.update(M.crest_stats(y, clean))
    n = min(len(orig), len(y))
    row["estoi_orig"] = M.estoi(orig[:n], y[:n])
    return row


def main():
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    wav_dir = os.path.join(HERE, "build", "wavs")
    os.makedirs(wav_dir, exist_ok=True)

    rows = []
    for utt in UTTS:
        params = it.load_params(os.path.join(DUMPS, utt, f"{utt}.npz"))
        ak = np.array([it.cos_to_ak(np.cos(w)) for w in params["lsp"]])
        orig = np.fromfile(os.path.join(RAWD, f"{utt}.raw"),
                           dtype="<i2").astype(float)

        clean = it.synth(params, None)
        clean2 = it.synth(params, None, seed=0x1234)
        if utt == WAV_UTT:
            write_wav(os.path.join(wav_dir, f"{utt}_orig.wav"), orig)
            write_wav(os.path.join(wav_dir, f"{utt}_CLEAN.wav"), clean)

        base = {"utt": utt, "point": "CLEAN", "etype": "-", "level": 0.0}
        r = measure(clean, clean, orig, ak)
        r["lsd_mean"] = 0.0                 # identical signals
        rows.append({**base, **r})
        e_clean = r["estoi_orig"]
        rows.append({**base, "point": "CLEAN2",
                     **measure(clean2, clean, orig, ak)})
        print(f"{utt}: CLEAN estoi(orig)={e_clean:.3f}  "
              f"CLEAN2 floor: lsd={rows[-1]['lsd_mean']:.2f} "
              f"dEstoi={e_clean - rows[-1]['estoi_orig']:+.4f}", flush=True)

        for point, (unit, etypes, levels) in SWEEP.items():
            for et in etypes:
                for lvl in levels:
                    inj = {"point": point, "etype": et, "level": lvl,
                           "seed": 0xC0DEC2}
                    y = it.synth(params, inj)
                    r = measure(y, clean, orig, ak)
                    rows.append({"utt": utt, "point": point, "etype": et,
                                 "level": lvl, **r})
                    print(f"{utt:11s} {point:8s} {et:8s} {lvl:<9.4g} "
                          f"LSD {r['lsd_mean']:6.2f}  NMR {r['nmr_median']:+6.1f}  "
                          f"crest {r['crest_delta_median']:+5.2f}  "
                          f"segSNR {r['segsnr_mean']:5.1f}  "
                          f"dESTOI {e_clean - r['estoi_orig']:+.4f}", flush=True)
                    if utt == WAV_UTT:
                        write_wav(os.path.join(
                            wav_dir, f"{utt}_{point}_{et}_{lvl:g}.wav"), y)

    keys = ["utt", "point", "etype", "level", "lsd_mean", "lsd_median",
            "lsd_p90", "lsd_frames", "nmr_median", "nmr_p90", "segsnr_mean",
            "segsnr_median", "crest_delta_median", "crest_delta_p90",
            "estoi_orig"]
    csvp = os.path.join(HERE, "results", "curves.csv")
    with open(csvp, "w") as f:
        f.write(",".join(keys) + "\n")
        for r in rows:
            f.write(",".join(
                f"{r.get(k, float('nan')):.5g}"
                if isinstance(r.get(k), (int, float)) and k != "utt"
                and not isinstance(r.get(k), str) else str(r.get(k, ""))
                for k in keys) + "\n")
    print(f"wrote {csvp} ({len(rows)} rows); wavs in {wav_dir}")


if __name__ == "__main__":
    main()
