#!/usr/bin/env python3
"""warpq_ladder.py — WARP-Q scores for every ladder wav produced by run_ladder.py.

WARP-Q (Jassim/Skoglund/Chen/Hines) is the project's primary perceptual
metric (README §4).  Uses the upstream repo cloned at build/WARP-Q
(github.com/wjassim/WARP-Q, commit recorded in results/warpq.json).

Deviations (documented):
  - pyvad/webrtcvad has no prebuilt wheel in this container, so the module is
    stubbed and WARP-Q runs with apply_vad=False.  Our utterances are dense
    speech (little leading/trailing silence), and every pair is scored the
    same way, so ranking deltas are unaffected.
  - inputs are 8 kHz; WARP-Q's default pipeline resamples to 16 kHz via
    librosa (native_sr=False), which is the metric's standard operating mode.

Score: raw_warpq_score, LOWER = better (DTW distance, not MOS-like).
Writes results/warpq.json and prints a table.
"""
import json
import os
import sys
import types
import warnings

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "build", "WARP-Q"))

# stub pyvad (unused with apply_vad=False; import happens at module level)
sys.modules.setdefault("pyvad", types.SimpleNamespace(
    vad=lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("pyvad stubbed; run with apply_vad=False"))))

# numpy 2.4 removed the np.lib.pad alias that warpq/utils.py cmvnw() uses;
# restore it (np.pad is the same function) instead of patching the clone.
import numpy as np  # noqa: E402
if not hasattr(np.lib, "pad"):
    np.lib.pad = np.pad

warnings.filterwarnings("ignore")
from warpq.core import warpqMetric  # noqa: E402

UTTS = ["hts1a", "hts2a", "ve9qrp_10s"]
CONDS = ["uq", "q1300"]
VARIANTS = ["L0", "L1", "L2-1500", "L2-2000", "L2-2500", "L3",
            "L4-0.50", "L4-0.65", "L4-0.75"]


def main():
    wav = os.path.join(HERE, "build", "wavs")
    metric = warpqMetric(apply_vad=False, n_jobs=1)
    out = {}
    for cond in CONDS:
        for utt in UTTS:
            ref = os.path.join(wav, f"{cond}_{utt}_ref.wav")
            orig = os.path.join(wav, f"{cond}_{utt}_orig.wav")
            key = f"{cond}/{utt}"
            out[key] = {}
            # anchor: reference synthesis scored against the original
            r = metric.evaluate(orig, ref)
            out[key]["REF-vs-ORIG"] = r["raw_warpq_score"]
            for name in VARIANTS:
                deg = os.path.join(wav, f"{cond}_{utt}_{name}.wav")
                if not os.path.exists(deg):
                    continue
                r_ref = metric.evaluate(ref, deg)     # vs reference synthesis
                r_org = metric.evaluate(orig, deg)    # vs original speech
                out[key][name] = {"vs_ref": r_ref["raw_warpq_score"],
                                  "vs_orig": r_org["raw_warpq_score"]}
                print(f"{key:18s} {name:8s} warpq vs_ref "
                      f"{out[key][name]['vs_ref']:.3f}  vs_orig "
                      f"{out[key][name]['vs_orig']:.3f}  "
                      f"[ref-vs-orig {out[key]['REF-vs-ORIG']:.3f}]",
                      flush=True)
    with open(os.path.join(HERE, "results", "warpq.json"), "w") as f:
        json.dump(out, f, indent=1)
    print("wrote results/warpq.json  (raw WARP-Q, lower = better)")


if __name__ == "__main__":
    main()
