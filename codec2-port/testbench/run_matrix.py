#!/usr/bin/env python3
"""run_matrix.py — run the condition matrix and score it.

Conditions (mode 1300, 52 bits / 40 ms), per corpus utterance:

  A  original          the 8 kHz input itself                     (anchor 0)
  B  c2enc -> c2dec    the pinned codec's own float ceiling       (anchor 1)
  C  c2enc -> c2tube   OUR integer decoder, reference bitstream in
  D  our enc -> c2dec  PENDING — no encoder prototype exists yet; the column
                       is emitted as "pending" whenever proto/encoder/ is
                       absent, and filled automatically once it appears.
  E  c2enc -> c2tube_l0  OUR decoder at ladder rung L0 only (Tier-2 grade)

B, C, D and E all consume the SAME .c2 file per utterance, so C/E vs B is a
pure decoder comparison with no encoder variance in it.

Outputs: out/wavs/<utt>.<cond>.wav (8 kHz mono s16) and out/results/bench.json
"""
import json
import os
import subprocess
import sys
import wave

import numpy as np

import metrics
import paths

FS = 8000


def raw_to_wav(raw_path, wav_path):
    x = np.fromfile(raw_path, dtype="<i2")
    with wave.open(wav_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(x.tobytes())
    return x.astype(np.float64) / 32768.0


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ERROR: {' '.join(cmd)}\n{r.stderr[-2000:]}")
    return r.stderr


def main():
    root = paths.c2port_root()
    c2enc = paths.oracle_bin("c2enc")
    c2dec = paths.oracle_bin("c2dec")
    bindir = os.path.join(paths.OUT, "build", "bin")
    c2tube = os.path.join(bindir, "c2tube")
    c2tube_l0 = os.path.join(bindir, "c2tube_l0")
    for p in (c2tube, c2tube_l0):
        if not os.access(p, os.X_OK):
            sys.exit(f"ERROR: {p} missing — run ./build_decoders.sh")

    enc_proto = os.path.join(root, "proto", "encoder")
    have_enc = os.path.isdir(enc_proto)

    corpus = os.path.join(paths.OUT, "corpus")
    with open(os.path.join(corpus, "manifest.json")) as fh:
        man = json.load(fh)

    wavs = os.path.join(paths.OUT, "wavs")
    work = os.path.join(paths.OUT, "build", "work")
    res = os.path.join(paths.OUT, "results")
    for d in (wavs, work, res):
        os.makedirs(d, exist_ok=True)

    conds = [
        ("A", "original", "the 8 kHz input", "anchor 0"),
        ("B", "c2enc -> c2dec (float)", "pinned codec2 @310777b, mode 1300",
         "anchor 1 = codec ceiling"),
        ("C", "c2enc -> c2tube (int)",
         "our fixed-point decoder, L0+L2+L4 (P2 knee)", "decoder under test"),
        ("D", "our enc -> c2dec (float)",
         "our encoder prototype into the reference decoder",
         "encoder under test"),
        ("E", "c2enc -> c2tube_l0 (int)",
         "our fixed-point decoder, rung L0 only", "Tier-2 'second grade'"),
    ]

    out = {"conditions": [{"id": c, "label": l, "detail": d, "role": r}
                          for c, l, d, r in conds],
           "encoder_prototype": bool(have_enc),
           "encoder_note": ("proto/encoder/ present — column D active"
                            if have_enc else
                            "proto/encoder/ absent: condition D (our encoder "
                            "-> stock c2dec) is PENDING round 4"),
           "utterances": []}

    for item in man["items"]:
        utt = item["utt"]
        raw = os.path.join(corpus, f"{utt}.raw")
        c2 = os.path.join(work, f"{utt}.c2")
        run([c2enc, "1300", raw, c2])
        bits = os.path.getsize(c2)

        sig = {}
        sig["A"] = raw_to_wav(raw, os.path.join(wavs, f"{utt}.A.wav"))
        for cid, tool in (("B", c2dec), ("C", c2tube), ("E", c2tube_l0)):
            dst = os.path.join(work, f"{utt}.{cid}.raw")
            log = run([tool, "1300", c2, dst] if tool is c2dec
                      else [tool, c2, dst])
            sig[cid] = raw_to_wav(dst, os.path.join(wavs, f"{utt}.{cid}.wav"))
            if cid in ("C", "E") and "saturations" in log:
                out.setdefault("sat_log", {})[f"{utt}.{cid}"] = log.strip()

        rows = []
        for cid, label, detail, role in conds:
            if cid == "D" and not have_enc:
                rows.append({"cond": "D", "pending": True})
                continue
            if cid == "D":
                sys.exit("proto/encoder/ exists: wire condition D in "
                         "run_matrix.py (encoder CLI is not specified yet)")
            x = sig[cid]
            r = {"cond": cid, "pending": False,
                 "wav": f"{utt}.{cid}.wav",
                 "samples": int(len(x)),
                 "active_rms_dbfs": round(metrics.active_rms_dbfs(x), 2)}
            # --- vs original (A) ---
            if cid == "A":
                r["estoi_vs_A"] = 1.0
                r["lag_A"] = 0
            else:
                lag = metrics.best_lag_estoi(sig["A"], x)
                a, b = metrics.aligned(sig["A"], x, lag)
                r["estoi_vs_A"] = round(metrics.estoi(a, b), 4)
                r["lag_A"] = int(lag)
            # --- vs codec ceiling (B) ---
            if cid == "B":
                r.update({"lsd_vs_B_mean": 0.0, "lsd_vs_B_median": 0.0,
                          "lsd_vs_B_p90": 0.0, "segsnr_vs_B_mean": 35.0,
                          "segsnr_vs_B_median": 35.0, "lag_B": 0,
                          "lsd_frames": 0})
            else:
                lagB = metrics.best_lag_lsd(sig["B"], x)
                a, b = metrics.aligned(sig["B"], x, lagB)
                ls = metrics.lsd_stats(b, a)
                ss = metrics.seg_snr(a, b)
                r.update({"lag_B": int(lagB),
                          "lsd_vs_B_mean": round(ls["lsd_mean"], 3),
                          "lsd_vs_B_median": round(ls["lsd_median"], 3),
                          "lsd_vs_B_p90": round(ls["lsd_p90"], 3),
                          "lsd_frames": ls["lsd_frames"],
                          "segsnr_vs_B_mean": round(ss["segsnr_mean"], 2),
                          "segsnr_vs_B_median": round(ss["segsnr_median"], 2)})
            rows.append(r)

        eB = next(r["estoi_vs_A"] for r in rows if r["cond"] == "B")
        for r in rows:
            if not r["pending"]:
                r["d_estoi_vs_B"] = round(r["estoi_vs_A"] - eB, 4)
                r["rel_estoi_vs_B_pct"] = round(
                    100.0 * (r["estoi_vs_A"] - eB) / eB, 2) if eB else None

        out["utterances"].append({
            "utt": utt, "meta": item, "c2_bytes": bits,
            "frames": item["frames_40ms"],
            "bitrate_bps": round(bits * 8 / item["seconds"], 1),
            "rows": rows})
        print(f"  {utt:11s} .c2 {bits:5d} B  "
              + "  ".join(f"{r['cond']}:ESTOI {r['estoi_vs_A']:.3f}"
                          for r in rows if not r["pending"]))

    with open(os.path.join(res, "bench.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print(f"wrote out/results/bench.json "
          f"({len(out['utterances'])} utterances x {len(conds)} conditions)")


if __name__ == "__main__":
    main()
