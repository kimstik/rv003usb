#!/usr/bin/env python3
"""neural.py — reference-free neural MOS on the bench wavs (optional column).

Reuses experiments/metrics-adequacy/run_neural.py verbatim in spirit and in
its shims:
  NISQA v2 (github.com/gabrielmittag/NISQA, torch CPU, weights/nisqa.tar) —
    mos_pred plus the noi/col/dis/loud dimensions.  Shim: torch>=2.6 defaults
    torch.load(weights_only=True) and the checkpoint is a pickled dict, so
    torch.load is monkeypatched back to weights_only=False.
  DNSMOS P.835 via the `speechmos` pip package (ONNX, CPU), 16 kHz; the input
    is clipped to [-1,1] after resample_poly because speechmos hard-errors on
    a few-LSB overshoot (same shim as metrics-adequacy warpq_run.py).

Both judges were trained on wideband natural speech; on 8 kHz vocoded speech
their ABSOLUTE values are heavily depressed and must not be read as MOS.  The
metrics-adequacy stand established they are usable only as per-utterance
DELTAS and rankings, so the listening page prints them next to the delta
against condition B and says so.

Skipped gracefully: if a model is unavailable or the run raises, the column is
recorded as absent and the bench still completes.  Writes
out/results/neural.json.

Usage: neural.py [--skip]
"""
import json
import os
import subprocess
import sys
import wave

import numpy as np

import paths

WAVS = os.path.join(paths.OUT, "wavs")
RES = os.path.join(paths.OUT, "results")
NISQA_REPO = os.path.join(paths.OUT, "build", "nisqa_repo")
NISQA_LOCAL = "/workspace/gabrielmittag/nisqa"
NISQA_URL = "https://github.com/gabrielmittag/NISQA"


def read_wav(path):
    with wave.open(path, "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return x.astype(np.float32) / 32768.0


def run_nisqa(names):
    if not os.path.isdir(NISQA_REPO):
        os.makedirs(os.path.dirname(NISQA_REPO), exist_ok=True)
        if os.path.isdir(NISQA_LOCAL):
            subprocess.run(["cp", "-r", NISQA_LOCAL, NISQA_REPO], check=True)
        else:
            subprocess.run(["git", "clone", "-q", "--depth", "1", NISQA_URL,
                            NISQA_REPO], check=True)
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
    df = nisqaModel(args).predict()
    out = {}
    for _, r in df.iterrows():
        out[os.path.basename(r["deg"])] = {
            "nisqa_mos": round(float(r["mos_pred"]), 3),
            "nisqa_noi": round(float(r["noi_pred"]), 3),
            "nisqa_col": round(float(r["col_pred"]), 3),
            "nisqa_dis": round(float(r["dis_pred"]), 3),
            "nisqa_loud": round(float(r["loud_pred"]), 3)}
    return out


def run_dnsmos(names):
    from scipy.signal import resample_poly
    from speechmos import dnsmos
    out = {}
    for n in names:
        x = read_wav(os.path.join(WAVS, n))
        x16 = np.clip(resample_poly(x, 2, 1), -1.0, 1.0).astype(np.float32)
        r = dnsmos.run(x16, sr=16000)
        out[n] = {"dns_sig": round(float(r["sig_mos"]), 3),
                  "dns_bak": round(float(r["bak_mos"]), 3),
                  "dns_ovrl": round(float(r["ovrl_mos"]), 3),
                  "dns_p808": round(float(r["p808_mos"]), 3)}
    return out


def main():
    os.makedirs(RES, exist_ok=True)
    out = {"nisqa": None, "dnsmos": None, "notes": []}
    if "--skip" in sys.argv:
        out["notes"].append("skipped by --skip")
        with open(os.path.join(RES, "neural.json"), "w") as fh:
            json.dump(out, fh, indent=1)
        print("neural: skipped")
        return
    names = sorted(f for f in os.listdir(WAVS) if f.endswith(".wav"))
    for label, fn in (("nisqa", run_nisqa), ("dnsmos", run_dnsmos)):
        try:
            out[label] = fn(names)
            print(f"  {label}: scored {len(out[label])} files")
        except Exception as e:  # documented degradation, never fatal
            out["notes"].append(f"{label} unavailable: {type(e).__name__}: {e}")
            print(f"  {label} FAILED (column omitted): {e}")
    with open(os.path.join(RES, "neural.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print("wrote out/results/neural.json")


if __name__ == "__main__":
    main()
