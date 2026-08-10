#!/usr/bin/env python3
"""warpq_inject.py — WARP-Q vs the ORIGINAL speech for every injected wav.

Timebox per the mission brief: WARP-Q runs on the hts1a subset only (the
sweep writes wavs for that utterance).  Same operating mode as
tube-ladder/warpq_ladder.py: upstream repo pinned in build/WARP-Q, pyvad
stubbed, apply_vad=False, 8 kHz inputs resampled to 16 kHz by the metric.

Score: raw_warpq_score, LOWER = better.  Deltas are taken against the CLEAN
tube synthesis's score in knees.py.  Writes results/warpq.json.
"""
import glob
import json
import os
import subprocess
import sys
import types
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
WARPQ_DIR = os.path.join(HERE, "build", "WARP-Q")
WARPQ_REPO = "https://github.com/wjassim/WARP-Q.git"
WARPQ_COMMIT = "bdf8616dc21dc4d7e8ae504bb162cc7f04b188a2"


def ensure_repo():
    if not os.path.isdir(WARPQ_DIR):
        subprocess.run(["git", "clone", "-q", WARPQ_REPO, WARPQ_DIR],
                       check=True)
        subprocess.run(["git", "-C", WARPQ_DIR, "checkout", "-q",
                        WARPQ_COMMIT], check=False)


def main():
    ensure_repo()
    sys.path.insert(0, WARPQ_DIR)
    sys.modules.setdefault("pyvad", types.SimpleNamespace(
        vad=lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("pyvad stubbed; apply_vad=False"))))
    import numpy as np
    if not hasattr(np.lib, "pad"):
        np.lib.pad = np.pad
    warnings.filterwarnings("ignore")
    from warpq.core import warpqMetric

    metric = warpqMetric(apply_vad=False, n_jobs=1)
    wav = os.path.join(HERE, "build", "wavs")
    orig = os.path.join(wav, "hts1a_orig.wav")

    out = {"commit": WARPQ_COMMIT, "utt": "hts1a", "scores": {}}
    clean = metric.evaluate(orig, os.path.join(wav, "hts1a_CLEAN.wav"))
    out["clean"] = clean["raw_warpq_score"]
    print(f"CLEAN vs orig: {out['clean']:.3f}", flush=True)

    for p in sorted(glob.glob(os.path.join(wav, "hts1a_*_*_*.wav"))):
        name = os.path.basename(p)[len("hts1a_"):-len(".wav")]
        point, etype, lvl = name.rsplit("_", 2)
        r = metric.evaluate(orig, p)
        key = f"{point}/{etype}/{lvl}"
        out["scores"][key] = r["raw_warpq_score"]
        print(f"{key:28s} warpq {r['raw_warpq_score']:.3f} "
              f"(d {r['raw_warpq_score'] - out['clean']:+.3f})", flush=True)

    with open(os.path.join(HERE, "results", "warpq.json"), "w") as f:
        json.dump(out, f, indent=1)
    print("wrote results/warpq.json")


if __name__ == "__main__":
    main()
