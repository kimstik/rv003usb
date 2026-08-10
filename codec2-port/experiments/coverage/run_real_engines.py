#!/usr/bin/env python3
"""run_real_engines.py — extend the engine-jurisdiction real-speech axis from
one utterance (synth-redteam real_hts1a_rt.csv: hts1a only) to the full
3-utterance corpus, for the engines that sit on the P2 engine front (pareto
REPORT.md coverage gap: "реальная речь engine-оси — один файл").

Everything is synth-redteam machinery reused verbatim:
  - model dumps: plain `c2sim <utt>.raw --dump` (same recipe as
    synth-redteam/run_all.sh; binary from the tube-ladder pinned oracle build)
  - parsing/segmentation: bench_r1/c2sim_parse.py (voiced runs >= 12 frames,
    zero phases both sides)
  - reference + metrics: bench_r1 synth_reference / lsd_db / click_metric
  - engines: bench_r1/engines.py + rt/engines_rt.py by name

Engines: the P2 engine front (sos-csd-jit == impulse-iir-csd-sos float golden,
lsp-allpass-csd3 (G8), kl-lattice-csd3) + anchors (osc-bank, impulse-iir).

Gate: the hts1a rows must reproduce committed real_hts1a_rt.csv (same code,
same dump recipe) — checked and printed.

Outputs: results/real_engines_3utt.csv (one row per engine x utt + mean row)
"""
import csv
import os
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RT = os.path.abspath(os.path.join(HERE, "..", "synth-redteam"))
ORACLE = os.path.abspath(os.path.join(HERE, "..", "tube-ladder", "build",
                                      "codec2"))
sys.path.insert(0, os.path.join(RT, "rt"))
sys.path.insert(0, os.path.join(RT, "bench_r1"))

from common import synth_reference                    # noqa: E402
from metrics import click_metric, lsd_db              # noqa: E402
import c2sim_parse                                    # noqa: E402
from engines import ENGINES as ENGINES_R1             # noqa: E402
from engines_rt import ENGINES_RT                     # noqa: E402

UTTS = ["hts1a", "hts2a", "ve9qrp_10s"]
ENGINES = ["osc-bank", "impulse-iir", "impulse-iir-csd-sos",
           "lsp-allpass-csd3", "kl-lattice-csd3"]


def get_engine(name):
    return ENGINES_R1[name] if name in ENGINES_R1 else ENGINES_RT[name]


def model_dump(utt):
    """Plain c2sim --dump (no flags) — identical to synth-redteam run_all.sh."""
    d = os.path.join(HERE, "build", "dump")
    os.makedirs(d, exist_ok=True)
    model = os.path.join(d, f"{utt}_model.txt")
    if not os.path.exists(model):
        c2sim = os.path.join(ORACLE, "build_host", "src", "c2sim")
        raw = os.path.join(ORACLE, "raw", f"{utt}.raw")
        subprocess.run([c2sim, raw, "--dump", os.path.join(d, utt)],
                       check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    return model


def main():
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    rows = []
    per_engine = {e: {"lsd": [], "lsd_max": [], "click": []} for e in ENGINES}
    for utt in UTTS:
        model = c2sim_parse.parse_model_dump(model_dump(utt))
        runs = c2sim_parse.voiced_runs(model, min_len=12)
        print(f"[{utt}] {len(runs)} voiced runs, "
              f"{sum(len(r) for r in runs)} frames")
        for name in ENGINES:
            lsds, clicks = [], []
            for run in runs:
                frames = c2sim_parse.to_bench_frames(run)
                ref = synth_reference(frames)
                x = get_engine(name)(frames)
                lsds.append(lsd_db(x, ref, frame_n=80))
                clicks.append(click_metric(x, frames)["click_ratio"])
            r = {"utt": utt, "engine": name,
                 "lsd_db_mean": round(float(np.nanmean(lsds)), 2),
                 "lsd_db_max": round(float(np.nanmax(lsds)), 2),
                 "click_ratio_mean": round(float(np.nanmean(clicks)), 2)}
            rows.append(r)
            per_engine[name]["lsd"].append(r["lsd_db_mean"])
            per_engine[name]["lsd_max"].append(r["lsd_db_max"])
            per_engine[name]["click"].append(r["click_ratio_mean"])
            print(f"  [{utt}] {name:24s} LSD {r['lsd_db_mean']:.2f} "
                  f"(max {r['lsd_db_max']:.2f})  click {r['click_ratio_mean']:.2f}",
                  flush=True)
    for name in ENGINES:
        d = per_engine[name]
        rows.append({"utt": "MEAN-3UTT", "engine": name,
                     "lsd_db_mean": round(float(np.mean(d["lsd"])), 2),
                     "lsd_db_max": round(float(np.max(d["lsd_max"])), 2),
                     "click_ratio_mean": round(float(np.mean(d["click"])), 2)})

    outp = os.path.join(HERE, "results", "real_engines_3utt.csv")
    with open(outp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["utt", "engine", "lsd_db_mean",
                                           "lsd_db_max", "click_ratio_mean"])
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {outp}")

    # ---- reproduction gate vs committed hts1a numbers --------------------
    committed = {r["engine"]: r for r in csv.DictReader(
        open(os.path.join(RT, "results", "real_hts1a_rt.csv")))}
    print("reproduction gate (hts1a vs committed real_hts1a_rt.csv):")
    for r in rows:
        if r["utt"] != "hts1a" or r["engine"] not in committed:
            continue
        c = committed[r["engine"]]
        ok = abs(float(c["lsd_db_mean"]) - r["lsd_db_mean"]) < 0.02
        print(f"  {r['engine']:24s} ours {r['lsd_db_mean']:.2f} "
              f"committed {float(c['lsd_db_mean']):.2f}  "
              f"{'OK' if ok else 'MISMATCH'}")


if __name__ == "__main__":
    main()
