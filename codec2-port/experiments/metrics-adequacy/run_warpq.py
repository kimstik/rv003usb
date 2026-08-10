#!/usr/bin/env python3
"""run_warpq.py — WARP-Q scores for every stand variant vs its reference.

Wiring recipe proven in experiments/voicing-regate/warpq_run.py, shims reused:
  - np.lib.pad = np.pad                     (pyvad 0.2.0 vs numpy>=2)
  - pyvad.vad clipped to [-1,1]             (librosa resample overshoot)
  - warpq repo cloned into build/warpq_repo (modern package layout)
Metric config: sr=16000, native_sr=False (8 kHz wavs resampled by the metric,
its published mode), VAD on.  raw_warpq_score: LOWER = closer (DTW distance).

Scored per manifest entry: warpq(ref_for_variant, variant); anchor rows
warpq(orig, ref) per utterance.  Output: results/warpq.json
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WAVS = os.path.join(HERE, "build", "wavs")
RESULTS = os.path.join(HERE, "results")
REPO = os.path.join(HERE, "build", "warpq_repo")
REPO_URL = "https://github.com/wjassim/WARP-Q.git"


def main():
    if not os.path.isdir(REPO):
        subprocess.run(["git", "clone", "-q", "--depth", "1", REPO_URL, REPO],
                       check=True)
    sys.path.insert(0, REPO)
    import numpy as np
    if not hasattr(np.lib, "pad"):
        np.lib.pad = np.pad
    import pyvad
    _vad = pyvad.vad
    pyvad.vad = lambda x, *a, **kw: _vad(np.clip(x, -1.0, 1.0), *a, **kw)
    from warpq.core import warpqMetric

    metric = warpqMetric(sr=16000, native_sr=False)

    with open(os.path.join(RESULTS, "manifest.json")) as fh:
        manifest = json.load(fh)

    out = {}
    for name, m in sorted(manifest.items()):
        if m["family"] == "anchor":
            continue
        ref = os.path.join(WAVS, m["ref_wav"])
        deg = os.path.join(WAVS, m["wav"])
        out[name] = metric.evaluate(ref, deg)["raw_warpq_score"]
        print(f"  warpq {name:36s} {out[name]:.3f}")
    # anchors: codec distance per utterance
    for utt in sorted({m["utt"] for m in manifest.values()}):
        out[f"{utt}.ANCHOR_orig_vs_ref"] = metric.evaluate(
            os.path.join(WAVS, f"{utt}.orig.wav"),
            os.path.join(WAVS, f"{utt}.ref.wav"))["raw_warpq_score"]

    with open(os.path.join(RESULTS, "warpq.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print(f"wrote results/warpq.json ({len(out)} scores)")


if __name__ == "__main__":
    main()
