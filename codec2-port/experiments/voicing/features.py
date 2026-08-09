#!/usr/bin/env python3
"""Extract reference voicing + FFT-free time-domain candidate features.

Framing (verified in codec2 src @ 310777b, sine.c/c2sim.c, Fs=8000):
  - c2sim consumes n_samp = 80 samples (10 ms) per loop iteration k = 0,1,...
  - analysis buffer Sn[] holds m_pitch = 320 samples; after iteration k it
    spans input samples [80k-240, 80k+80)  (zero-initialized at startup)
  - the nw=279 analysis window is centred on Sn[m_pitch/2], i.e. on input
    sample c_k = 80*(k-1)
  We therefore compute time-domain features on the SAME 320-sample buffer
  [c_k-160, c_k+160) so our features see exactly the audio the reference saw.

Reference voicing: reconstructed per frame k from snr[k] (dumped MBE SNR,
V_THRESH = 6 dB) plus the eratio post-processing of est_voicing_mbe
(sine.c:444), computed from the A[]/Wo dumped on line k.  --selfcheck verifies
this reconstruction equals the (one-frame-stale) voiced column of *_model.txt
shifted by one frame, exactly, for every file.

Candidate features (all FFT-free, MCU-friendly).  Correlation features are
computed on the ~1 kHz-lowpassed signal (2 cascaded one-pole LPs) because
est_voicing_mbe itself only measures harmonicity in 0-1 kHz -- wideband NACF
is destroyed by high-band noise on breathy voiced frames (verified: fixes a
large fraction of misses).  DC is removed first (kristoff.raw has LF rumble).

  nacf_wo   max normalized autocorrelation over lags 0.85P..1.15P, where
            P = round(2pi/Wo) from the DUMPED Wo (decouples from any pitch
            estimator of ours; +-15%% absorbs the frequent mismatch between
            the harmonic-fit Wo and the time-domain periodicity peak)
  nacf_best max NACF over the full pitch range 20..160 samples (on the MCU
            this is a free byproduct of the B1 ASDF pitch search)
  nacf_sub  same but max over two 120-sample sub-windows (robust to pitch
            drift across the 40 ms buffer)
  yin_wo    YIN cumulative-mean-normalized ASDF d'(tau) at tau=P/4 on the
            2 kHz-decimated lowpassed signal (low => voiced)
  yin_best  min d'(tau) over tau = 5..40 (free byproduct of ASDF search)
  r1r0      lag-1 autocorrelation coefficient (spectral tilt; ~1 => voiced)
  zcr       zero-crossing rate (fraction of sign changes)
  lhr_db    low/high band energy ratio, one-pole split at ~1 kHz, dB
  energy_db frame energy, dB (for silence/failure analysis only)
"""
import sys
import os
import glob
import numpy as np

FS = 8000
N_SAMP = 80          # 10 ms
M_PITCH = 320        # analysis buffer
V_THRESH = 6.0       # dB, defines.h:53
ALPHA_1K = 1.0 - np.exp(-2 * np.pi * 1000.0 / FS)   # one-pole LP at ~1 kHz

FEATS = ['nacf_wo', 'nacf_best', 'nacf_sub', 'yin_wo', 'yin_best',
         'r1r0', 'zcr', 'lhr_db', 'energy_db']
COLS = ['frame', 'ref_v', 'snr', 'eratio', 'Wo', 'P', 'L'] + FEATS


def load_dump(prefix):
    d = np.loadtxt(prefix + '_model.txt')
    snr = np.loadtxt(prefix + '_snr.txt')
    assert len(d) == len(snr), prefix
    Wo = d[:, 0]
    L = d[:, 1].astype(int)
    A = d[:, 2:162]                      # A[l] at column l-1
    voiced_col = d[:, 162].astype(int)   # stale by one frame (see build.sh)
    return Wo, L, A, voiced_col, snr


def reconstruct_ref_voicing(Wo, L, A, snr):
    """est_voicing_mbe threshold + eratio post-processing (sine.c:444)."""
    n = len(snr)
    v = np.zeros(n, dtype=int)
    eratio = np.zeros(n)
    sixty = 60.0 * 2 * np.pi / FS
    for k in range(n):
        vk = 1 if snr[k] > V_THRESH else 0
        l2 = int(L[k] * 2000.0 / (FS / 2))
        l4 = int(L[k] * 4000.0 / (FS / 2))
        a = A[k]
        elow = 1e-4 + np.sum(a[0:l2] ** 2)           # l = 1..l_2000
        ehigh = 1e-4 + np.sum(a[max(l2 - 1, 0):l4] ** 2)  # l = l_2000..l_4000
        er = 10 * np.log10(elow / ehigh)
        if vk == 0 and er > 10.0:
            vk = 1
        if vk == 1:
            if er < -10.0:
                vk = 0
            if er < -4.0 and Wo[k] <= sixty:
                vk = 0
        v[k] = vk
        eratio[k] = er
    return v, eratio


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
    """Two cascaded one-pole LPs at ~1 kHz (on MCU: alpha ~ 0.5445, a few
    shift-adds per sample)."""
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
    """Normalized autocorrelation r[q] = sum(w[i]w[i+q]) / sqrt(E0(q)E1(q))
    for all lags q (host-side fast form; per-lag identical to the MCU loop)."""
    n = len(w)
    r = np.correlate(w, w, 'full')[n - 1:]
    c2 = np.concatenate([[0.0], np.cumsum(w * w)])
    q = np.arange(n)
    e0 = c2[n - q] - c2[0]
    e1 = c2[n] - c2[q]
    return r / (np.sqrt(e0 * e1) + 1e-9)


def features_one(x, k, Wo_k):
    w = frame_window(x, k)
    P = int(np.clip(round(2 * np.pi / Wo_k), 20, 160))
    w = w - w.mean()                       # DC / rumble removal
    lp = lowpass2(w)

    r = norm_acf(lp)
    q0, q1 = max(int(P * 0.85), 20), min(int(P * 1.15), 160)
    nacf_wo = r[q0:q1 + 1].max()
    nacf_best = r[20:161].max()

    nacf_sub = 0.0                          # two 120-sample sub-windows
    for q in range(20, 161, 2):
        n = min(120, M_PITCH - q)
        for lo in (0, M_PITCH - q - n):
            a, b = lp[lo:lo + n], lp[lo + q:lo + q + n]
            den = np.sqrt((a @ a) * (b @ b))
            if den > 0:
                nacf_sub = max(nacf_sub, (a @ b) / den)

    # YIN d' on 2 kHz decimated lowpassed signal
    y = lp[::4]
    n2 = len(y)
    d = np.empty(41)
    for j in range(1, 41):
        diff = y[:n2 - j] - y[j:]
        d[j] = diff @ diff
    dp = d[1:] * np.arange(1, 41) / (np.cumsum(d[1:]) + 1e-9)
    tau = int(np.clip(round(P / 4.0), 5, 40))
    yin_wo = dp[max(tau - 2, 4):min(tau, 39) + 1].min()
    yin_best = dp[4:40].min()

    r0 = w @ w + 1e-9
    r1r0 = (w[:-1] @ w[1:]) / r0
    zcr = np.mean(np.signbit(w[:-1]) != np.signbit(w[1:]))
    hp = w - lp
    lhr_db = 10 * np.log10((lp @ lp + 1e-4) / (hp @ hp + 1e-4))
    energy_db = 10 * np.log10(r0 / M_PITCH + 1e-9)
    return P, [nacf_wo, nacf_best, nacf_sub, yin_wo, yin_best,
               r1r0, zcr, lhr_db, energy_db]


def process(raw_path, prefix, out_csv, selfcheck=False):
    x = np.fromfile(raw_path, dtype='<i2').astype(np.float64)
    Wo, L, A, voiced_col, snr = load_dump(prefix)
    ref_v, eratio = reconstruct_ref_voicing(Wo, L, A, snr)
    if selfcheck:
        m = np.mean(voiced_col[1:] == ref_v[:-1])
        tag = 'OK' if m == 1.0 else f'MISMATCH {m:.4f}'
        print(f'   selfcheck {os.path.basename(prefix)}: '
              f'reconstruction vs shifted model.txt voiced column: {tag}')
        assert m == 1.0, prefix
    rows = []
    for k in range(len(snr)):
        P, f = features_one(x, k, Wo[k])
        rows.append([k, ref_v[k], snr[k], eratio[k], Wo[k], P, L[k]] + f)
    arr = np.array(rows)
    with open(out_csv, 'w') as fh:
        fh.write(','.join(COLS) + '\n')
        np.savetxt(fh, arr, delimiter=',', fmt='%.6g')
    return len(rows), ref_v.mean()


def main():
    raw_dir, dump_dir, out_dir = sys.argv[1:4]
    selfcheck = '--selfcheck' in sys.argv
    os.makedirs(out_dir, exist_ok=True)
    for raw in sorted(glob.glob(os.path.join(raw_dir, '*.raw'))):
        name = os.path.basename(raw)[:-4]
        prefix = os.path.join(dump_dir, name)
        if not os.path.exists(prefix + '_model.txt'):
            print(f'   skip {name} (no dump)')
            continue
        n, vfrac = process(raw, prefix, os.path.join(out_dir, name + '.csv'),
                           selfcheck)
        print(f'   {name}: {n} frames, {100*vfrac:.1f}% voiced')


if __name__ == '__main__':
    main()
