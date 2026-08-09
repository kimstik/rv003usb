#!/usr/bin/env python3
"""metrics_signal.py — signal-domain metrics between two speech files.

Level-2 validation (codec2-port/README.md §4 item 2): perceptual proxies on
paired audio. Inputs are 16-bit signed little-endian mono files, either
headerless .raw (codec2 corpus convention, 8 kHz assumed) or .wav.

Metrics:
  segSNR   segmental SNR, dB: 10*log10(sum s^2 / sum (s-t)^2) per frame
           (default 20 ms frames, 10 ms hop), each frame clamped to
           [-10, +35] dB (classic Quackenbush/Hansen convention), averaged
           over frames whose reference energy is above a silence floor
           (default: 40 dB below utterance RMS). Both mean and median are
           reported.
  ESTOI    extended STOI via pystoi (requires numpy/scipy/pystoi; installed
           with: pip3 install numpy scipy pystoi). Reported only if pystoi
           imports; otherwise metric is 'n/a'.

WARP-Q is NOT implemented here (documented install steps in README.md;
its GitHub-hosted install was not exercised in this experiment).

The two files must be the same sample rate; lengths may differ by a few
samples (trimmed to the shorter). No time alignment is performed: this tool
is meant for oracle-vs-port comparisons where both decoders consume the same
bitstream on the same frame grid (zero lag by construction). Use --lag to
apply a known integer-sample shift of the test signal if needed.

Usage: metrics_signal.py ref.{raw,wav} test.{raw,wav} [--fs 8000] [--lag N]
                         [--json out.json]
"""
import argparse
import json
import os
import sys
import wave

import numpy as np

FLOOR_DB = 40.0     # silence floor below utterance RMS
CLAMP_LO, CLAMP_HI = -10.0, 35.0


def read_speech(path, fs_raw):
    """Return (float array in [-1,1), fs). Headerless .raw assumed fs_raw."""
    if path.lower().endswith(".wav"):
        with wave.open(path, "rb") as w:
            assert w.getsampwidth() == 2, f"{path}: expect 16-bit PCM"
            assert w.getnchannels() == 1, f"{path}: expect mono"
            data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
            return data.astype(np.float64) / 32768.0, w.getframerate()
    data = np.fromfile(path, dtype="<i2")
    return data.astype(np.float64) / 32768.0, fs_raw


def seg_snr(ref, test, fs, frame_ms=20.0, hop_ms=10.0):
    n = int(fs * frame_ms / 1000)
    hop = int(fs * hop_ms / 1000)
    rms = np.sqrt(np.mean(ref ** 2)) + 1e-12
    floor = rms * 10 ** (-FLOOR_DB / 20)
    vals = []
    for start in range(0, len(ref) - n + 1, hop):
        s = ref[start:start + n]
        e = s - test[start:start + n]
        es = np.sum(s ** 2)
        if np.sqrt(es / n) < floor:
            continue  # skip silence
        ee = np.sum(e ** 2)
        snr = 10.0 * np.log10(es / ee) if ee > 0 else CLAMP_HI
        vals.append(np.clip(snr, CLAMP_LO, CLAMP_HI))
    vals = np.array(vals)
    if vals.size == 0:
        return {"segsnr_mean_dB": float("nan"), "segsnr_median_dB":
                float("nan"), "segsnr_frames": 0}
    return {"segsnr_mean_dB": float(vals.mean()),
            "segsnr_median_dB": float(np.median(vals)),
            "segsnr_frames": int(vals.size)}


def estoi(ref, test, fs):
    try:
        from pystoi import stoi
    except ImportError:
        return None
    return float(stoi(ref, test, fs, extended=True))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ref")
    ap.add_argument("test")
    ap.add_argument("--fs", type=int, default=8000,
                    help="sample rate assumed for headerless .raw (def 8000)")
    ap.add_argument("--lag", type=int, default=0,
                    help="shift test by N samples (positive: test is late)")
    ap.add_argument("--json", help="write metrics as JSON")
    args = ap.parse_args()

    ref, fs_r = read_speech(args.ref, args.fs)
    test, fs_t = read_speech(args.test, args.fs)
    if fs_r != fs_t:
        sys.exit(f"sample rates differ: {fs_r} vs {fs_t}")
    if args.lag > 0:
        test = test[args.lag:]
    elif args.lag < 0:
        ref = ref[-args.lag:]
    n = min(len(ref), len(test))
    if n == 0:
        sys.exit("empty input")
    if abs(len(ref) - len(test)) > fs_r:  # > 1 s difference is suspicious
        print(f"WARNING: length mismatch {len(ref)} vs {len(test)} samples",
              file=sys.stderr)
    ref, test = ref[:n], test[:n]

    m = {"file_ref": os.path.basename(args.ref),
         "file_test": os.path.basename(args.test),
         "fs": fs_r, "samples": n}
    m.update(seg_snr(ref, test, fs_r))
    e = estoi(ref, test, fs_r)
    m["estoi"] = e if e is not None else "n/a (pystoi not installed)"

    print(f"--- metrics_signal: {m['file_ref']} vs {m['file_test']} "
          f"({n / fs_r:.2f} s @ {fs_r} Hz)")
    print(f"  segSNR mean {m['segsnr_mean_dB']:.2f} dB  "
          f"median {m['segsnr_median_dB']:.2f} dB  "
          f"({m['segsnr_frames']} active frames)")
    print(f"  ESTOI  {e:.4f}" if e is not None
          else "  ESTOI  n/a (pystoi not installed)")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(m, f, indent=2)


if __name__ == "__main__":
    main()
