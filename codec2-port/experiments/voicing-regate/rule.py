#!/usr/bin/env python3
"""Recompute the round-1 winning FFT-free voicing rule (tree2) per frame.

The rule (experiments/voicing/REPORT.md, exact thresholds from
experiments/voicing/results/summary.json tuning.tree2):

    v = (nacf_wo > 0.2638904) ? (r1r0 > -0.29466294) : (r1r0 > 0.845930125)

Feature extraction is COPIED from experiments/voicing/features.py (framing,
DC removal, 1 kHz two-pole lowpass, normalized ACF, +-15% lag window around
P = round(2*pi/Wo) with Wo taken from the c2sim dump).  Divergences from
features.py, all deliberate:
  * only the two features the rule needs (nacf_wo, r1r0) are computed --
    nacf_best/nacf_sub/yin_*/zcr/lhr_db/energy_db dropped;
  * the reference-voicing reconstruction (est_voicing_mbe threshold + eratio
    post-processing) is copied unchanged and re-verified per file against the
    one-frame-stale voiced column of *_model.txt (same --selfcheck gate).

Outputs, per corpus file <name>:
  <out>/<name>.ref.txt    reconstructed reference decisions, one 0/1 per line
                          (must equal what stock c2enc packs; checked later
                          against the actual bitstream by verify_bitstream.py)
  <out>/<name>.rule.txt   tree2 decisions, same format (encoder override input)
  <out>/<name>.csv        frame, ref_v, rule_v, snr, Wo, P, nacf_wo, r1r0

Usage: rule.py <raw_dir> <dump_dir> <out_dir>
"""
import glob
import os
import sys

import numpy as np

FS = 8000
N_SAMP = 80
M_PITCH = 320
V_THRESH = 6.0                                    # dB, defines.h:53
ALPHA_1K = 1.0 - np.exp(-2 * np.pi * 1000.0 / FS)

# exact tuned thresholds (results/summary.json of round 1, not the rounded
# values printed in its REPORT.md)
T_NACF = 0.26389039999999997
T_R1R0_HI = -0.29466294
T_R1R0_LO = 0.845930125


# ---- copied from experiments/voicing/features.py ----------------------------

def load_dump(prefix):
    d = np.loadtxt(prefix + '_model.txt')
    snr = np.loadtxt(prefix + '_snr.txt')
    assert len(d) == len(snr), prefix
    Wo = d[:, 0]
    L = d[:, 1].astype(int)
    A = d[:, 2:162]
    voiced_col = d[:, 162].astype(int)   # stale by one frame
    return Wo, L, A, voiced_col, snr


def reconstruct_ref_voicing(Wo, L, A, snr):
    """est_voicing_mbe threshold + eratio post-processing (sine.c:444)."""
    n = len(snr)
    v = np.zeros(n, dtype=int)
    sixty = 60.0 * 2 * np.pi / FS
    for k in range(n):
        vk = 1 if snr[k] > V_THRESH else 0
        l2 = int(L[k] * 2000.0 / (FS / 2))
        l4 = int(L[k] * 4000.0 / (FS / 2))
        a = A[k]
        elow = 1e-4 + np.sum(a[0:l2] ** 2)
        ehigh = 1e-4 + np.sum(a[max(l2 - 1, 0):l4] ** 2)
        er = 10 * np.log10(elow / ehigh)
        if vk == 0 and er > 10.0:
            vk = 1
        if vk == 1:
            if er < -10.0:
                vk = 0
            if er < -4.0 and Wo[k] <= sixty:
                vk = 0
        v[k] = vk
    return v


def frame_window(x, k):
    """320-sample analysis buffer of frame k: input samples [80k-240, 80k+80)."""
    lo = N_SAMP * k - 240
    hi = N_SAMP * k + 80
    w = np.zeros(M_PITCH)
    a, b = max(lo, 0), min(hi, len(x))
    if b > a:
        w[a - lo:b - lo] = x[a:b]
    return w


def lowpass2(w):
    """Two cascaded one-pole LPs at ~1 kHz."""
    y = w
    for _ in range(2):
        out = np.empty(len(y))
        s = 0.0
        for i in range(len(y)):
            s += ALPHA_1K * (y[i] - s)
            out[i] = s
        y = out
    return y


def norm_acf(w):
    n = len(w)
    r = np.correlate(w, w, 'full')[n - 1:]
    c2 = np.concatenate([[0.0], np.cumsum(w * w)])
    q = np.arange(n)
    e0 = c2[n - q] - c2[0]
    e1 = c2[n] - c2[q]
    return r / (np.sqrt(e0 * e1) + 1e-9)


def features_one(x, k, Wo_k):
    """nacf_wo and r1r0 only (the two features tree2 uses)."""
    w = frame_window(x, k)
    P = int(np.clip(round(2 * np.pi / Wo_k), 20, 160))
    w = w - w.mean()
    lp = lowpass2(w)

    r = norm_acf(lp)
    q0, q1 = max(int(P * 0.85), 20), min(int(P * 1.15), 160)
    nacf_wo = r[q0:q1 + 1].max()

    r0 = w @ w + 1e-9
    r1r0 = (w[:-1] @ w[1:]) / r0
    return P, nacf_wo, r1r0


# ---- rule + driver ----------------------------------------------------------

def tree2(nacf_wo, r1r0):
    return int(r1r0 > (T_R1R0_HI if nacf_wo > T_NACF else T_R1R0_LO))


def process(raw_path, prefix, out_dir, name):
    x = np.fromfile(raw_path, dtype='<i2').astype(np.float64)
    Wo, L, A, voiced_col, snr = load_dump(prefix)
    ref_v = reconstruct_ref_voicing(Wo, L, A, snr)
    # selfcheck (same gate as round 1): reconstruction == shifted stale column
    m = np.mean(voiced_col[1:] == ref_v[:-1])
    assert m == 1.0, f'{name}: voicing reconstruction selfcheck FAILED ({m:.4f})'

    rows = []
    rule_v = np.zeros(len(snr), dtype=int)
    for k in range(len(snr)):
        P, nacf_wo, r1r0 = features_one(x, k, Wo[k])
        rule_v[k] = tree2(nacf_wo, r1r0)
        rows.append([k, ref_v[k], rule_v[k], snr[k], Wo[k], P, nacf_wo, r1r0])

    np.savetxt(os.path.join(out_dir, name + '.ref.txt'), ref_v, fmt='%d')
    np.savetxt(os.path.join(out_dir, name + '.rule.txt'), rule_v, fmt='%d')
    with open(os.path.join(out_dir, name + '.csv'), 'w') as fh:
        fh.write('frame,ref_v,rule_v,snr,Wo,P,nacf_wo,r1r0\n')
        np.savetxt(fh, np.array(rows), delimiter=',', fmt='%.6g')
    dis = np.mean(ref_v != rule_v)
    print(f'   {name}: {len(snr)} frames, rule-vs-ref disagreement '
          f'{100 * dis:.2f}% ({int((ref_v != rule_v).sum())} frames)')
    return len(snr), dis


def main():
    raw_dir, dump_dir, out_dir = sys.argv[1:4]
    os.makedirs(out_dir, exist_ok=True)
    tot = flips = 0
    for raw in sorted(glob.glob(os.path.join(raw_dir, '*.raw'))):
        name = os.path.basename(raw)[:-4]
        prefix = os.path.join(dump_dir, name)
        if not os.path.exists(prefix + '_model.txt'):
            print(f'   skip {name} (no dump)')
            continue
        n, dis = process(raw, prefix, out_dir, name)
        tot += n
        flips += round(dis * n)
    print(f'   TOTAL: {tot} frames, overall disagreement '
          f'{100 * flips / tot:.2f}% (round-1 reference: 9.93%)')


if __name__ == '__main__':
    main()
