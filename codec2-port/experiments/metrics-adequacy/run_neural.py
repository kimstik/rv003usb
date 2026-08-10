#!/usr/bin/env python3
"""run_neural.py — reference-free neural judges on the degradation stand.

DNSMOS (P.835): the official Microsoft `speechmos` pip package, which ships
the DNS-Challenge ONNX models bundled (onnxruntime CPU).  Axes:
  SIG  speech signal quality  (proxy for Coloration/distortion)
  BAK  background intrusiveness (proxy for Noisiness)
  OVRL overall               plus P.808 MOS.

NISQA v2 (github.com/gabrielmittag/NISQA, torch CPU): multidimensional model
(weights/nisqa.tar) with per-dimension outputs
  mos_pred, noi_pred (Noisiness), col_pred (Coloration),
  dis_pred (Discontinuity), loud_pred (Loudness)
— exactly the axes the H-principle wants gated asymmetrically.
Shim: torch>=2.6 defaults torch.load(weights_only=True); the NISQA
checkpoint is a pickled dict -> monkeypatch weights_only=False.

Both judges consume the 8 kHz wavs from make_pairs.py (DNSMOS after
resampling to 16 kHz; NISQA loads at its native 48 kHz via librosa).
Absolute values on vocoded narrowband speech are depressed; the stand only
uses per-utterance DELTAS and rankings.

Usage: run_neural.py  -> results/neural.csv (one row per manifest entry)
"""
import csv
import json
import os
import subprocess
import sys
import wave

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
WAVS = os.path.join(HERE, "build", "wavs")
RESULTS = os.path.join(HERE, "results")
NISQA_REPO = os.path.join(HERE, "build", "nisqa_repo")
NISQA_URL = "https://github.com/gabrielmittag/NISQA"


def read_wav(path):
    with wave.open(path, "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return x.astype(np.float32) / 32768.0


def run_dnsmos(names, manifest):
    from scipy.signal import resample_poly
    from speechmos import dnsmos
    out = {}
    for name in names:
        x = read_wav(os.path.join(WAVS, manifest[name]["wav"]))
        # resample_poly can overshoot [-1,1] by a few LSB; speechmos
        # hard-errors on that (same fix as the pyvad shim in warpq_run.py)
        x16 = np.clip(resample_poly(x, 2, 1), -1.0, 1.0)
        r = dnsmos.run(x16.astype(np.float32), sr=16000)
        out[name] = {"dns_sig": float(r["sig_mos"]),
                     "dns_bak": float(r["bak_mos"]),
                     "dns_ovrl": float(r["ovrl_mos"]),
                     "dns_p808": float(r["p808_mos"])}
        print(f"  dnsmos {name:34s} SIG {r['sig_mos']:.2f} "
              f"BAK {r['bak_mos']:.2f} OVRL {r['ovrl_mos']:.2f}")
    return out


def run_nisqa(names, manifest):
    if not os.path.isdir(NISQA_REPO):
        src = "/workspace/gabrielmittag/nisqa"
        if os.path.isdir(src):
            subprocess.run(["cp", "-r", src, NISQA_REPO], check=True)
        else:
            subprocess.run(["git", "clone", "-q", "--depth", "1",
                            NISQA_URL, NISQA_REPO], check=True)
    sys.path.insert(0, NISQA_REPO)
    import torch
    _load = torch.load
    torch.load = lambda *a, **kw: _load(*a, **{**kw, "weights_only": False})
    from nisqa.NISQA_model import nisqaModel

    args = {"mode": "predict_dir",
            "pretrained_model": os.path.join(NISQA_REPO, "weights",
                                             "nisqa.tar"),
            "data_dir": WAVS, "output_dir": None, "csv_file": None,
            "csv_deg": None, "deg": None, "num_workers": 0, "bs": 1,
            "ms_channel": None, "tr_bs_val": 1, "tr_num_workers": 0}
    model = nisqaModel(args)
    df = model.predict()
    out = {}
    by_wav = {os.path.basename(r["deg"]): r for _, r in df.iterrows()}
    for name in names:
        r = by_wav.get(manifest[name]["wav"])
        if r is None:
            continue
        out[name] = {"nisqa_mos": float(r["mos_pred"]),
                     "nisqa_noi": float(r["noi_pred"]),
                     "nisqa_col": float(r["col_pred"]),
                     "nisqa_dis": float(r["dis_pred"]),
                     "nisqa_loud": float(r["loud_pred"])}
    return out


def main():
    with open(os.path.join(RESULTS, "manifest.json")) as fh:
        manifest = json.load(fh)
    names = sorted(k for k in manifest
                   if not manifest[k]["variant"].startswith("ref_for"))

    rows = {n: {"name": n, "utt": manifest[n]["utt"],
                "variant": manifest[n]["variant"],
                "family": manifest[n]["family"],
                "tier": manifest[n].get("tier", "")} for n in names}

    dns = run_dnsmos(names, manifest)
    for n, d in dns.items():
        rows[n].update(d)

    try:
        nis = run_nisqa(names, manifest)
        for n, d in nis.items():
            rows[n].update(d)
        print(f"  nisqa: scored {len(nis)} files")
    except Exception as e:
        print(f"  NISQA FAILED (documented, stand degrades to DNSMOS): {e}")

    keys = ["name", "utt", "variant", "family", "tier",
            "dns_sig", "dns_bak", "dns_ovrl", "dns_p808",
            "nisqa_mos", "nisqa_noi", "nisqa_col", "nisqa_dis", "nisqa_loud"]
    with open(os.path.join(RESULTS, "neural.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows[n] for n in names)
    print(f"wrote results/neural.csv ({len(names)} rows)")


if __name__ == "__main__":
    main()
