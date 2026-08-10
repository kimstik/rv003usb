#!/usr/bin/env python3
"""corpus.py — assemble the listening corpus as 8 kHz mono s16 headerless raw.

Sources, in provenance order:
  repo   pinned codec2 @310777b  build/codec2/raw/*.raw   (8 kHz mono s16)
  ext    originals downloaded by fetch_ext.sh from rowetel.com (wav, 8 kHz)

Every item is trimmed to a whole number of 40 ms codec frames (320 samples)
and capped at MAX_S seconds so the base64-embedded listening page stays small.
No resampling was needed for any reachable source — all are already 8 kHz mono
16-bit; if a future source is not, it is resampled with scipy.resample_poly and
the manifest records `resampled: true`.

Writes out/corpus/<utt>.raw and out/corpus/manifest.json.
"""
import hashlib
import json
import os
import sys
import wave

import numpy as np

import paths

MAX_S = 8.0
FS = 8000
FRAME = 320  # 40 ms, codec2 mode 1300 frame

# utt -> (source kind, file, speaker/description, provenance URL or repo path)
SPEC = [
    ("hts1a", "repo", "hts1a.raw", "male, studio (HTS corpus)",
     "codec2 @310777b raw/hts1a.raw"),
    ("hts2a", "repo", "hts2a.raw", "female, studio (HTS corpus)",
     "codec2 @310777b raw/hts2a.raw"),
    ("kristoff", "repo", "kristoff.raw", "male, accented, close mic",
     "codec2 @310777b raw/kristoff.raw"),
    ("ve9qrp_10s", "repo", "ve9qrp_10s.raw", "male, amateur-radio recording",
     "codec2 @310777b raw/ve9qrp_10s.raw"),
    ("mmt1", "ext", "mmt1.wav", "male over truck/road background noise",
     "https://www.rowetel.com/downloads/codec2/mmt1.wav"),
    ("cq_ref", "ext", "cq_ref.wav", "male, CQ call reference recording",
     "https://www.rowetel.com/downloads/codec2/2200/cq_ref.wav"),
]

# Downloaded but NOT used as a separate utterance: recorded in the manifest as
# a provenance cross-check (see notes in the generated HTML).
CROSSCHECK = ("hts2a_ext.wav",
              "https://www.rowetel.com/downloads/codec2/hts2a.wav",
              "hts2a.raw")


def read_any(path):
    """-> (int16 array, fs, resampled_flag)."""
    if path.lower().endswith(".wav"):
        with wave.open(path, "rb") as w:
            assert w.getsampwidth() == 2, f"{path}: expect 16-bit PCM"
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
            if w.getnchannels() > 1:
                x = x.reshape(-1, w.getnchannels()).mean(1).astype(np.int16)
            fs = w.getframerate()
    else:
        x = np.fromfile(path, dtype="<i2")
        fs = FS
    if fs != FS:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(fs, FS)
        x = resample_poly(x.astype(np.float64), FS // g, fs // g)
        return np.clip(np.round(x), -32768, 32767).astype(np.int16), fs, True
    return x.astype(np.int16), fs, False


def main():
    root = paths.c2port_root()
    raw_dir = os.path.join(paths.codec2_src(), "raw")
    ext_dir = os.path.join(paths.OUT, "ext")
    out_dir = os.path.join(paths.OUT, "corpus")
    os.makedirs(out_dir, exist_ok=True)

    man = {"fs": FS, "max_s": MAX_S, "frame_samples": FRAME,
           "codec2_commit": "310777b1c6f1af0bc7c72f5b32f80f6fd9136962",
           "items": [], "missing": [], "crosscheck": None}

    for utt, kind, fn, who, prov in SPEC:
        src = os.path.join(raw_dir if kind == "repo" else ext_dir, fn)
        if not os.path.exists(src):
            print(f"  MISS {utt}: {src} absent (external fetch failed?)")
            man["missing"].append({"utt": utt, "kind": kind, "file": fn,
                                   "source": prov})
            continue
        x, fs0, resamp = read_any(src)
        n = min(len(x), int(MAX_S * FS))
        n -= n % FRAME
        x = x[:n]
        dst = os.path.join(out_dir, f"{utt}.raw")
        x.tofile(dst)
        sha = hashlib.sha256(x.tobytes()).hexdigest()
        man["items"].append({
            "utt": utt, "kind": kind, "who": who, "source": prov,
            "src_fs": fs0, "resampled": resamp,
            "samples": int(n), "seconds": round(n / FS, 3),
            "frames_40ms": n // FRAME, "sha256_16": sha[:16],
            "peak": int(np.max(np.abs(x.astype(np.int32)))),
            "rms_dbfs": round(float(20 * np.log10(
                (np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-9) / 32768)),
                2),
        })
        print(f"  {utt:11s} {kind:4s} {n/FS:5.2f}s  {n//FRAME:4d} frames  "
              f"{who}")

    # provenance cross-check: is the web copy of hts2a the repo's raw file?
    cfn, curl, crepo = CROSSCHECK
    cpath = os.path.join(ext_dir, cfn)
    if os.path.exists(cpath):
        a, _, _ = read_any(cpath)
        b = np.fromfile(os.path.join(raw_dir, crepo), dtype="<i2")
        same = len(a) == len(b) and bool(np.array_equal(a, b))
        man["crosscheck"] = {"web_file": cfn, "url": curl,
                             "repo_file": f"raw/{crepo}",
                             "identical": same,
                             "sha256_16": hashlib.sha256(
                                 a.tobytes()).hexdigest()[:16]}
        print(f"  crosscheck {cfn} vs raw/{crepo}: "
              f"{'BYTE-IDENTICAL' if same else 'DIFFERENT'}")

    with open(os.path.join(out_dir, "manifest.json"), "w") as fh:
        json.dump(man, fh, indent=1)
    if not man["items"]:
        sys.exit("ERROR: empty corpus")
    print(f"corpus: {len(man['items'])} utterances, "
          f"{sum(i['seconds'] for i in man['items']):.1f} s total")


if __name__ == "__main__":
    main()
