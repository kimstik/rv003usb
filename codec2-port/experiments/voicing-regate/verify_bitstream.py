#!/usr/bin/env python3
"""Verify the encoder voicing override actually landed in the bitstreams.

Mode 1300 packs 52 bits per 40 ms frame = 7 bytes; the first 4 bits (MSB
first, pack.c) are the four 10 ms voicing decisions (1-bit gray = identity).
Bitstreams are written headerless (.bit extension -- c2enc only prepends the
7-byte c2_header for .c2 filenames).

Checks per corpus file:
  1. stock .bit voicing == reconstructed reference decisions (proves the
     c2sim-derived frame grid/reconstruction matches what stock c2enc packs,
     AND that the patched binary without C2_VOICING_OVERRIDE is stock);
  2. swap .bit voicing == rule decisions;
  3. each rand<seed> .bit voicing == its decision file;
  4. all non-voicing bits are byte-identical across every version (the
     override must touch nothing but the 4 voicing bits/frame).

Usage: verify_bitstream.py <bits_dir> <decisions_dir> <raw_dir> <seed> ...
Exits non-zero on any mismatch.
"""
import glob
import os
import sys

import numpy as np


def load_bits(path):
    b = np.fromfile(path, dtype=np.uint8)
    assert len(b) % 7 == 0, f'{path}: {len(b)} bytes not a multiple of 7'
    return b.reshape(-1, 7)


def voicing_bits(frames):
    out = np.empty(4 * len(frames), dtype=int)
    for j, byte in enumerate(frames[:, 0]):
        for i, k in enumerate((7, 6, 5, 4)):
            out[4 * j + i] = (int(byte) >> k) & 1
    return out


def nonvoicing_bits(frames):
    f = frames.copy()
    f[:, 0] &= 0x0F
    return f


def main():
    bits_dir, dec_dir, raw_dir = sys.argv[1:4]
    seeds = sys.argv[4:]
    fail = 0
    for raw in sorted(glob.glob(os.path.join(raw_dir, '*.raw'))):
        name = os.path.basename(raw)[:-4]
        stock_path = os.path.join(bits_dir, name + '.stock.bit')
        if not os.path.exists(stock_path):
            continue
        nsamp = os.path.getsize(raw) // 2
        n_enc = 4 * (nsamp // 320)
        versions = {'stock': 'ref', 'swap': 'rule'}
        for s in seeds:
            versions[f'rand{s}'] = f'rand{s}'
        stock = load_bits(stock_path)
        assert 4 * len(stock) == n_enc, (name, len(stock), n_enc)
        v = {}
        for ver, dec in versions.items():
            frames = load_bits(os.path.join(bits_dir, f'{name}.{ver}.bit'))
            v[ver] = voicing_bits(frames)
            want = np.loadtxt(os.path.join(dec_dir, f'{name}.{dec}.txt'),
                              dtype=int)[:n_enc]
            ok_v = np.array_equal(v[ver], want)
            ok_rest = np.array_equal(nonvoicing_bits(frames),
                                     nonvoicing_bits(stock))
            if not (ok_v and ok_rest):
                print(f'   FAIL {name}.{ver}: voicing_match={ok_v} '
                      f'nonvoicing_match={ok_rest}')
                fail += 1
        rates = {ver: 100.0 * np.mean(v[ver] != v['stock'])
                 for ver in versions if ver != 'stock'}
        print(f'   OK {name}: {n_enc} frames; flip% vs stock: ' +
              ', '.join(f'{k}={r:.2f}' for k, r in rates.items()))
    if fail:
        sys.exit(f'{fail} bitstream verification failures')
    print('   all bitstreams verified: overrides landed, only voicing bits differ')


if __name__ == '__main__':
    main()
