#!/usr/bin/env python3
"""Build the random-control voicing decision files.

For each corpus file the control starts from the reference (= stock encoder)
decisions and flips exactly as many frames as the FFT-free rule disagrees on,
at uniformly random positions -- i.e. the SAME overall flip rate as the rule,
but NOT concentrated at the reference's own V/UV uncertainty boundary.  If
boundary-concentrated flips are perceptually cheaper than random ones (the
round-2 hypothesis), the random control must degrade the decoded audio MORE
than the rule swap does.

Rate matching is done over the frames the encoder actually consumes
(n_enc = 4 * floor(n_samples/320); the c2sim dump can have up to 3 more 10 ms
frames than c2enc encodes when the file is not a whole number of 40 ms
frames).  Several seeds are generated so the control is not hostage to one
draw.

Usage: make_controls.py <raw_dir> <decisions_dir> <seed> [<seed> ...]
Writes <decisions_dir>/<name>.rand<seed>.txt
"""
import glob
import os
import sys

import numpy as np


def main():
    raw_dir, dec_dir = sys.argv[1], sys.argv[2]
    seeds = [int(s) for s in sys.argv[3:]] or [1, 2, 3]
    for raw in sorted(glob.glob(os.path.join(raw_dir, '*.raw'))):
        name = os.path.basename(raw)[:-4]
        ref_path = os.path.join(dec_dir, name + '.ref.txt')
        if not os.path.exists(ref_path):
            continue
        ref = np.loadtxt(ref_path, dtype=int)
        rule = np.loadtxt(os.path.join(dec_dir, name + '.rule.txt'), dtype=int)
        nsamp = os.path.getsize(raw) // 2
        n_enc = 4 * (nsamp // 320)
        assert n_enc <= len(ref), (name, n_enc, len(ref))
        n_flip = int(np.sum(ref[:n_enc] != rule[:n_enc]))
        for seed in seeds:
            rng = np.random.default_rng(1300 * 1000 + seed * 100 + len(ref))
            rand = ref.copy()
            idx = rng.choice(n_enc, size=n_flip, replace=False)
            rand[idx] = 1 - rand[idx]
            np.savetxt(os.path.join(dec_dir, f'{name}.rand{seed}.txt'),
                       rand, fmt='%d')
        print(f'   {name}: {n_flip}/{n_enc} flips '
              f'({100 * n_flip / n_enc:.2f}%) x {len(seeds)} seeds')


if __name__ == '__main__':
    main()
