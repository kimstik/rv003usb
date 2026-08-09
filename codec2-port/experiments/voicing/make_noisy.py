#!/usr/bin/env python3
"""Generate 20 dB SNR noisy versions of the corpus (white + babble-like).

SNR is measured against the *active-speech* power of the clean file: frames
(10 ms) whose energy is within 30 dB of the loudest frame count as active.
Noise is scaled so that  10*log10(P_active_speech / P_noise) = TARGET_SNR_DB.

Babble-like noise = sum of 6 circularly-shifted copies of the concatenation of
the OTHER corpus files (deterministic seed), i.e. speech-shaped, non-stationary
competing-talker noise without needing any external corpus.

Note on methodology (see task spec): noise MAY change the voicing ground
truth; that is fine, because classify.py always compares our decision against
the reference MBE decision computed on the SAME noisy audio.  We measure
agreement of methods, not noise robustness of the ground truth.
"""
import sys
import os
import glob
import numpy as np

TARGET_SNR_DB = 20.0
FS = 8000
FRAME = 80


def read_raw(path):
    return np.fromfile(path, dtype='<i2').astype(np.float64)


def write_raw(path, x):
    np.clip(x, -32768, 32767).astype('<i2').tofile(path)


def active_power(x):
    n = (len(x) // FRAME) * FRAME
    fr = x[:n].reshape(-1, FRAME)
    e = (fr ** 2).mean(axis=1) + 1e-12
    thresh = e.max() / 10 ** (30 / 10)   # within 30 dB of loudest frame
    act = e[e >= thresh]
    return act.mean() if len(act) else e.mean()


def scale_noise(x, noise):
    ps = active_power(x)
    pn = (noise ** 2).mean() + 1e-12
    g = np.sqrt(ps / pn / 10 ** (TARGET_SNR_DB / 10))
    return noise * g


def main(raw_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    files = sorted(glob.glob(os.path.join(raw_dir, '*.raw')))
    rng = np.random.default_rng(20260809)
    all_audio = {f: read_raw(f) for f in files}
    for f in files:
        name = os.path.basename(f)[:-4]
        x = all_audio[f]
        # -- white --
        white = rng.standard_normal(len(x))
        write_raw(os.path.join(out_dir, f'{name}_white20.raw'),
                  x + scale_noise(x, white))
        # -- babble-like: 6 shifted copies of the other files' concatenation --
        others = np.concatenate([all_audio[g] for g in files if g != f])
        bab = np.zeros(len(x))
        for _ in range(6):
            off = rng.integers(0, len(others))
            seg = np.take(others, np.arange(off, off + len(x)), mode='wrap')
            bab += seg
        write_raw(os.path.join(out_dir, f'{name}_babble20.raw'),
                  x + scale_noise(x, bab))
        print(f'   {name}: white20 + babble20 written')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
