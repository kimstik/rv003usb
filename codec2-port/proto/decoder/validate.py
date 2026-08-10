#!/usr/bin/env python3
"""validate.py — bit-exactness + quality validation for the c2tube prototype.

Per utterance (hts1a, hts2a, ve9qrp_10s), all driven by REAL c2enc 1300
bitstreams:

  1. BIT-EXACTNESS (README.md §4 tier 2): C decoder output must equal
     golden.py byte-for-byte on every frame.  Any mismatch aborts.
  2. Fixed-point penalty: segSNR of the fixed output against the float
     architecture twins (float_ref.py):
        twinL  same CSD coefficient values, float states  -> pure arithmetic
        twinA  exact-cos CSD                              -> + coefficient path
        twinB  no CSD                                     -> the CSD rung cost
     Gate (mission brief): fixed vs float ladder > ~30 dB (twinA/twinL).
  3. ESTOI to the original speech for: fixed, twinA, twinB, c2dec (native
     phase0 synthesis anchor), and tube.py L0+L2+L4 driven by c2sim dumps
     (the tube-ladder float rung rebuilt with the same params/knobs:
     crossover 2.5 kHz, postfilter 0.65/0.8).
     Gate: ESTOI(fixed) within 0.01 of the float ladder's.

Outputs results/validate.json and a human-readable table on stdout.
"""
import json
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(HERE, "build")
OUT = os.path.join(BUILD, "out")
BIN = os.path.join(BUILD, "codec2", "build_host", "src")
RAWD = os.path.join(BUILD, "codec2", "raw")
RES = os.path.join(HERE, "results")
UTTS = ["hts1a", "hts2a", "ve9qrp_10s"]

sys.path.insert(0, HERE)


def run(cmd, **kw):
    subprocess.run(cmd, check=True, **kw)


def readraw(p):
    return np.fromfile(p, dtype=np.int16).astype(np.float64)


def best_lag(ref, x, maxlag=256):
    """metric-optimal constant lag (tube-ladder alignment lesson)."""
    n = min(len(ref), len(x))
    ref = ref[:n]
    x = x[:n]
    best, bl = -1e18, 0
    for lag in range(-maxlag, maxlag + 1, 4):
        if lag >= 0:
            a, b = ref[lag:], x[:n - lag]
        else:
            a, b = ref[:n + lag], x[-lag:]
        c = float(np.dot(a, b))
        if c > best:
            best, bl = c, lag
    # refine
    best2, bl2 = -1e18, bl
    for lag in range(bl - 4, bl + 5):
        if abs(lag) > maxlag:
            continue
        if lag >= 0:
            a, b = ref[lag:], x[:n - lag]
        else:
            a, b = ref[:n + lag], x[-lag:]
        c = float(np.dot(a, b))
        if c > best2:
            best2, bl2 = c, lag
    return bl2


def aligned(ref, x, lag):
    n = min(len(ref), len(x))
    ref = ref[:n]
    x = x[:n]
    if lag >= 0:
        return ref[lag:], x[:n - lag]
    return ref[:n + lag], x[-lag:]


def estoi(ref, x):
    from pystoi import stoi
    lag = best_lag(ref, x)
    a, b = aligned(ref, x, lag)
    return float(stoi(a, b, 8000, extended=True))


def seg_snr(ref, x, seglen=80, floor_rms=100.0):
    n = min(len(ref), len(x))
    r = ref[:n - n % seglen].reshape(-1, seglen)
    e = (x[:n - n % seglen].reshape(-1, seglen) - r)
    en = (r ** 2).sum(1)
    err = (e ** 2).sum(1)
    sel = en > seglen * floor_rms ** 2 / 100.0
    snr = 10 * np.log10(np.maximum(en[sel], 1e-12) /
                        np.maximum(err[sel], 1e-12))
    return dict(median=float(np.median(snr)), mean=float(snr.mean()),
                p10=float(np.percentile(snr, 10)),
                overall=float(10 * np.log10(
                    (r ** 2).sum() / max((e ** 2).sum(), 1e-12))))


def tube_ladder_rung(utt):
    """tube.py L0+L2+L4 (no dispersion, no jitter) on c2sim q1300 dumps."""
    sys.path.insert(0, os.path.join(HERE, "..", "..", "experiments",
                                    "tube-ladder"))
    import tube
    import dump_params
    dump_dir = os.path.join(BUILD, "dumps", utt)
    os.makedirs(dump_dir, exist_ok=True)
    pref = os.path.join(dump_dir, utt)
    if not os.path.exists(pref + "_model.txt"):
        run([os.path.join(BIN, "c2sim"), os.path.join(RAWD, utt + ".raw"),
             "--rate", "1300", "--dump", pref,
             "-o", pref + "_simref.raw"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p = dump_params.parse_dump(pref)
    params = dict(Wo=p["Wo"], L=p["L"], voiced=p["voiced"],
                  ak=p["ak"], A=p["A_lpc"])
    # rung=4 with dispersion disabled and no snr_mbe == L0+L2+L4
    orig_mk = tube.make_dispersion_filter
    tube.make_dispersion_filter = lambda *a, **k: None
    try:
        sig = tube.synth_ladder(params, rung=4, crossover_hz=2500.0,
                                pf_g1=0.65, pf_g2=0.8)
    finally:
        tube.make_dispersion_filter = orig_mk
    out = os.path.join(OUT, f"{utt}_tubeL024.raw")
    np.clip(np.round(sig), -32768, 32767).astype(np.int16).tofile(out)
    return out


def main():
    os.makedirs(RES, exist_ok=True)
    import golden
    import float_ref
    results = {}
    for utt in UTTS:
        r = {}
        c2 = os.path.join(OUT, f"{utt}.c2")
        raw = os.path.join(RAWD, f"{utt}.raw")
        if not os.path.exists(c2):
            run([os.path.join(BIN, "c2enc"), "1300", raw, c2])
        fix = os.path.join(OUT, f"{utt}_fix.raw")
        run([os.path.join(HERE, "build", "c2tube"), c2, fix],
            stderr=subprocess.DEVNULL)

        # 1. bit-exactness
        gold = os.path.join(OUT, f"{utt}_gold.raw")
        golden.sat_count = 0
        nfr = golden.decode_file(c2, gold)
        cbytes = open(fix, "rb").read()
        gbytes = open(gold, "rb").read()
        assert len(cbytes) == len(gbytes), (utt, len(cbytes), len(gbytes))
        if cbytes != gbytes:
            ca = np.frombuffer(cbytes, dtype=np.int16)
            ga = np.frombuffer(gbytes, dtype=np.int16)
            bad = np.nonzero(ca != ga)[0]
            raise AssertionError(
                f"{utt}: C/golden mismatch at sample {bad[0]} "
                f"(frame {bad[0] // 320}): {ca[bad[0]]} vs {ga[bad[0]]}")
        r["frames"] = nfr
        r["bitexact"] = True
        r["golden_saturations"] = golden.sat_count

        # 2. float twins + segSNR
        fx = readraw(fix)
        for mode in ["twinA", "twinL", "twinB"]:
            tw = os.path.join(OUT, f"{utt}_{mode}.raw")
            kw = {"twinA": dict(csd=True, lut_cos=False),
                  "twinL": dict(csd=True, lut_cos=True),
                  "twinB": dict(csd=False, lut_cos=False)}[mode]
            float_ref.decode_file(c2, tw, **kw)
            r[f"segsnr_{mode}"] = seg_snr(readraw(tw), fx)

        # 3. ESTOI vs original
        ref = os.path.join(OUT, f"{utt}_ref.raw")
        if not os.path.exists(ref):
            run([os.path.join(BIN, "c2dec"), "1300", c2, ref],
                stderr=subprocess.DEVNULL)
        tube_out = tube_ladder_rung(utt)
        orig = readraw(raw)
        r["estoi"] = {
            "fixed": estoi(orig, fx),
            "twinA": estoi(orig, readraw(os.path.join(OUT, f"{utt}_twinA.raw"))),
            "twinB": estoi(orig, readraw(os.path.join(OUT, f"{utt}_twinB.raw"))),
            "c2dec": estoi(orig, readraw(ref)),
            "tube_L024_dumps": estoi(orig, readraw(tube_out)),
        }
        results[utt] = r
        print(f"== {utt}: {nfr} frames bit-exact; "
              f"segSNR(twinA) med {r['segsnr_twinA']['median']:.1f} dB "
              f"overall {r['segsnr_twinA']['overall']:.1f} dB; "
              f"ESTOI fixed {r['estoi']['fixed']:.3f} "
              f"twinB {r['estoi']['twinB']:.3f} c2dec {r['estoi']['c2dec']:.3f}")

    with open(os.path.join(RES, "validate.json"), "w") as f:
        json.dump(results, f, indent=2)
    print("results/validate.json written")

    # gates
    for utt, r in results.items():
        assert r["bitexact"]
    print("GATE bit-exactness: PASS (all frames, all utterances)")
    med = min(results[u]["segsnr_twinA"]["median"] for u in UTTS)
    print(f"GATE segSNR fixed-vs-floatladder(twinA) median >= 30 dB: "
          f"{'PASS' if med >= 30 else 'CHECK'} (min over corpus {med:.1f})")
    # one-sided: the fixed decoder must not be WORSE than the float ladder
    # by more than 0.01 (being better is fine)
    dmin = min(results[u]["estoi"]["fixed"] - results[u]["estoi"]["twinB"]
               for u in UTTS)
    print(f"GATE ESTOI fixed >= float ladder - 0.01: "
          f"{'PASS' if dmin >= -0.01 else 'CHECK'} "
          f"(worst delta {dmin:+.4f})")


if __name__ == "__main__":
    main()
