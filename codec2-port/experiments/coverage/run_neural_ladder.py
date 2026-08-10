#!/usr/bin/env python3
"""run_neural_ladder.py — NISQA + DNSMOS for every tube-ladder rung and the two
exact recommended knee subsets (pareto REPORT.md coverage gap #1: the tier-1
knee was recommended without a single neural score).

Judges and shims are metrics-adequacy/run_neural.py IMPORTED VERBATIM (module
functions run_dnsmos / run_nisqa, incl. the torch weights_only monkeypatch and
the resample clip shim); only the wav manifest differs.  Same caveat applies:
absolute MOS on 8 kHz vocoded speech is depressed — per-utterance deltas and
rankings are the only meaningful reading.

Scored set (q1300 condition, 3 utterances):
  anchors : orig (clean), ref (codec2 phase0 decode = the codec's ceiling)
  trunk   : L0 L1 L2-1500 L2-2000 L2-2500 L3 L4-0.50 L4-0.65 L4-0.75
  knees   : P1-knee (L0+L1+L2.5k+L4), P2-knee (L0+L2.5k+L4)  [run_knees.py]

Inputs : tube-ladder/build/wavs/q1300_*  (run_ladder.py)
         build/wavs/q1300_*_P{1,2}-knee.wav (run_knees.py)
Outputs: results/neural_ladder.csv
"""
import csv
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LADDER_WAVS = os.path.abspath(os.path.join(HERE, "..", "tube-ladder",
                                           "build", "wavs"))
ADEQ = os.path.abspath(os.path.join(HERE, "..", "metrics-adequacy"))
KNEE_WAVS = os.path.join(HERE, "build", "wavs")
STAGE = os.path.join(HERE, "build", "neural_wavs")
RESULTS = os.path.join(HERE, "results")

UTTS = ["hts1a", "hts2a", "ve9qrp_10s"]
COND = "q1300"
TRUNK = ["L0", "L1", "L2-1500", "L2-2000", "L2-2500", "L3",
         "L4-0.50", "L4-0.65", "L4-0.75"]
KNEES = ["P1-knee", "P2-knee"]
TIER = {"L0": 2, "L1": 2, "L2-1500": 2, "L2-2000": 2, "L2-2500": 2, "L3": 2,
        "L4-0.50": 1, "L4-0.65": 1, "L4-0.75": 1,
        "P1-knee": 1, "P2-knee": 1, "ref": 0, "orig": ""}


def main():
    os.makedirs(STAGE, exist_ok=True)
    os.makedirs(RESULTS, exist_ok=True)

    manifest = {}
    for utt in UTTS:
        for var in ["ref", "orig"] + TRUNK + KNEES:
            src_dir = KNEE_WAVS if var in KNEES else LADDER_WAVS
            wav = f"{COND}_{utt}_{var}.wav"
            src = os.path.join(src_dir, wav)
            if not os.path.exists(src):
                sys.exit(f"missing wav {src} — run run_ladder.py / "
                         f"run_knees.py first")
            shutil.copyfile(src, os.path.join(STAGE, wav))
            manifest[f"{COND}_{utt}_{var}"] = {
                "wav": wav, "utt": utt, "variant": var,
                "family": "ladder", "tier": TIER[var]}

    # ---- judges: metrics-adequacy module, repointed at our staging dir ----
    sys.path.insert(0, ADEQ)
    import run_neural as RN
    RN.WAVS = STAGE
    RN.NISQA_REPO = os.path.join(HERE, "build", "nisqa_repo")

    names = sorted(manifest)
    rows = {n: {"name": n, "utt": manifest[n]["utt"],
                "variant": manifest[n]["variant"],
                "family": manifest[n]["family"],
                "tier": manifest[n]["tier"]} for n in names}

    dns = RN.run_dnsmos(names, manifest)
    for n, d in dns.items():
        rows[n].update(d)

    try:
        nis = RN.run_nisqa(names, manifest)
        for n, d in nis.items():
            rows[n].update(d)
        print(f"  nisqa: scored {len(nis)} files")
    except Exception as e:
        print(f"  NISQA FAILED (documented, stand degrades to DNSMOS): {e}")

    keys = ["name", "utt", "variant", "family", "tier",
            "dns_sig", "dns_bak", "dns_ovrl", "dns_p808",
            "nisqa_mos", "nisqa_noi", "nisqa_col", "nisqa_dis", "nisqa_loud"]
    outp = os.path.join(RESULTS, "neural_ladder.csv")
    with open(outp, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows[n] for n in names)
    print(f"wrote {outp} ({len(names)} rows)")


if __name__ == "__main__":
    main()
