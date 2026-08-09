#!/usr/bin/env python3
"""Paired signal-domain metrics for the voicing-swap A/B experiment.

For every corpus file, compares decoded audio (all decoded by the SAME stock
c2dec 1300, sample-aligned by construction):

  swap  vs stock : the only difference in the bitstream is the voicing bits
                   where the FFT-free rule disagrees with the MBE reference
  randN vs stock : voicing bits flipped at the same per-file rate but at
                   random positions (metric-sensitivity calibration)
  o2    vs stock : (one file) stock bitstream decoded by an -O2 build vs the
                   -O3 build -- the float optimization-level noise floor

and, against the ORIGINAL speech: ESTOI(orig, stock/swap/randN) -- does the
swap move end-to-end intelligibility at all?

Metrics:
  segSNR  segmental SNR of pair (ref=stock decode), 20 ms frames / 10 ms hop,
          per-frame clamp [-10, +35] dB, silence-gated 40 dB below utterance
          RMS -- copied from experiments/oracle/metrics_signal.py.
  ESTOI   pystoi extended STOI.
  LSD     log-spectral distortion 100-3400 Hz, Hann-320/hop-160 -- copied
          from experiments/synth-bakeoff/bench/metrics.py lsd_db().
  NMR     envelope-weighted NMR proxy, ADAPTED from synth-bakeoff
          nmr_proxy_db(): that one weights a single long-window error
          spectrum by a known synthetic envelope; here (real speech) it is
          computed per 32 ms Hann frame with the envelope estimated from the
          smoothed reference-decode spectrum (61-bin moving average, floored
          40 dB below frame max), energy-averaged over silence-gated frames.
          Lower (more negative) = error further under the envelope.

Frame attribution (swap vs stock only): per-10 ms-frame error energy, and
the share of total error energy within +-2 frames of (a) a rule-vs-reference
flip, (b) a reference V/UV transition.

Usage: run_metrics.py <raw_dir> <audio_dir> <decisions_dir> <out_json> <seeds...>
  audio_dir must contain <name>.<ver>.raw decodes for ver in
  stock, swap, rand<seed>...; optionally <name>.stock_o2.raw.
"""
import glob
import json
import os
import sys

import numpy as np
from pystoi import stoi

FS = 8000
FLOOR_DB = 40.0
CLAMP_LO, CLAMP_HI = -10.0, 35.0


def read_raw(path):
    return np.fromfile(path, dtype='<i2').astype(np.float64) / 32768.0


# ---- copied from experiments/oracle/metrics_signal.py -----------------------

def seg_snr(ref, test, fs=FS, frame_ms=20.0, hop_ms=10.0):
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
            continue
        ee = np.sum(e ** 2)
        snr = 10.0 * np.log10(es / ee) if ee > 0 else CLAMP_HI
        vals.append(np.clip(snr, CLAMP_LO, CLAMP_HI))
    vals = np.array(vals)
    return {'segsnr_mean_dB': float(vals.mean()),
            'segsnr_median_dB': float(np.median(vals)),
            'segsnr_frames': int(vals.size)}


# ---- copied from experiments/synth-bakeoff/bench/metrics.py -----------------

def lsd_db(x, ref, frame_n=160, lo_hz=100, hi_hz=3400):
    n = min(len(x), len(ref))
    win = np.hanning(2 * frame_n)
    vals = []
    for start in range(0, n - 2 * frame_n, frame_n):
        X = np.abs(np.fft.rfft(x[start:start + 2 * frame_n] * win))
        R = np.abs(np.fft.rfft(ref[start:start + 2 * frame_n] * win))
        f = np.fft.rfftfreq(2 * frame_n, 1 / FS)
        sel = (f >= lo_hz) & (f <= hi_hz)
        if R[sel].max() < 1e-6:
            continue
        lx = 20 * np.log10(np.maximum(X[sel], 1e-9))
        lr = 20 * np.log10(np.maximum(R[sel], 1e-9))
        vals.append(np.sqrt(np.mean((lx - lr) ** 2)))
    return float(np.mean(vals)) if vals else float('nan')


# ---- adapted from synth-bakeoff nmr_proxy_db() (see module docstring) -------

def nmr_proxy(x, ref, frame_n=256, floor_db=-40.0):
    n = min(len(x), len(ref))
    win = np.hanning(frame_n)
    hop = frame_n // 2
    rms = np.sqrt(np.mean(ref ** 2)) + 1e-12
    gate = rms * 10 ** (-FLOOR_DB / 20)
    num = den = 0.0
    kern = np.ones(61) / 61.0
    for start in range(0, n - frame_n, hop):
        r = ref[start:start + frame_n]
        if np.sqrt(np.mean(r ** 2)) < gate:
            continue
        R = np.abs(np.fft.rfft(r * win))
        X = np.abs(np.fft.rfft(x[start:start + frame_n] * win))
        env = np.convolve(R, kern, mode='same')
        env = np.maximum(env, env.max() * 10 ** (floor_db / 20) + 1e-12)
        W = 1.0 / env
        D = X - R
        num += np.sum((W * D) ** 2)
        den += np.sum((W * R) ** 2)
    if den <= 0:
        return float('nan')
    if num <= 0:
        return None   # bit-identical signals: -inf dB, JSON-safe marker
    return 10 * np.log10(num / den)


# ---- frame attribution ------------------------------------------------------
#
# Two views, because codec2 phases are synthetic (perceptually free by
# design) but STATEFUL: one voicing flip permanently decorrelates the
# decoder's phase trajectory, so the waveform error |swap-stock| stays large
# forever after the first flip even where magnitude spectra agree.
#   * waveform view: share of |test-stock|^2 sample energy near flips --
#     expected to be small/uninformative (phase-divergence dominated);
#   * magnitude view: per-frame log-spectral distortion (100-3400 Hz,
#     Hann-160 20 ms window, 10 ms hop = decoder frame grid) between the two
#     decodes -- phase-blind, so concentration of THIS near flips is the
#     perceptually meaningful attribution.

def _mask_near(idx, nfr, halfwidth):
    m = np.zeros(nfr, dtype=bool)
    for i in idx:
        m[max(0, i - halfwidth):min(nfr, i + halfwidth + 1)] = True
    return m


def error_attribution(stock, test, ref_v, rule_v, n_enc, halfwidth=2):
    n = min(len(stock), len(test))
    err = test[:n] - stock[:n]
    nfr = n // 80
    e = np.array([np.sum(err[80 * k:80 * (k + 1)] ** 2) for k in range(nfr)])
    tot = e.sum() + 1e-30

    # first sample where the decodes diverge (samples before the first flip's
    # superframe must be bit-identical -- decoder is deterministic)
    div = np.nonzero(err)[0]
    first_div_frame = int(div[0] // 80) if len(div) else None

    # per-frame log-spectral distortion, 20 ms window centred on frame k
    win = np.hanning(160)
    f = np.fft.rfftfreq(160, 1 / FS)
    sel = (f >= 100) & (f <= 3400)
    rms = np.sqrt(np.mean(stock[:n] ** 2)) + 1e-12
    gate = rms * 10 ** (-FLOOR_DB / 20)
    d = np.full(nfr, np.nan)
    for k in range(nfr - 1):
        s = stock[80 * k:80 * k + 160]
        t = test[80 * k:80 * k + 160]
        if np.sqrt(np.mean(s ** 2)) < gate:
            continue
        S = np.abs(np.fft.rfft(s * win))[sel]
        T = np.abs(np.fft.rfft(t * win))[sel]
        ls = 20 * np.log10(np.maximum(S, 1e-9))
        lt = 20 * np.log10(np.maximum(T, 1e-9))
        d[k] = np.sqrt(np.mean((lt - ls) ** 2))

    flips = np.where(ref_v[:n_enc] != rule_v[:n_enc])[0]
    trans = np.where(np.diff(ref_v[:n_enc]) != 0)[0]
    m_f = _mask_near(flips, nfr, halfwidth)
    m_t = _mask_near(trans, nfr, halfwidth)
    act = ~np.isnan(d)
    big = act & (d > 3.0)

    def frac(mask_sel, mask_all):
        tot_ = mask_all.sum()
        return float(mask_sel.sum() / tot_) if tot_ else None

    return {
        'first_divergent_frame': first_div_frame,
        'first_flip_frame': int(flips[0]) if len(flips) else None,
        'err_mass_near_flips': float(e[m_f].sum() / tot),
        'err_mass_near_transitions': float(e[m_t].sum() / tot),
        'n_flips': int(len(flips)), 'n_transitions': int(len(trans)),
        'frames_near_flips': int(m_f.sum()), 'n_frames': int(nfr),
        'sd_mean_near_flips_dB': float(np.nanmean(d[m_f & act]))
            if (m_f & act).any() else None,
        'sd_mean_elsewhere_dB': float(np.nanmean(d[~m_f & act]))
            if (~m_f & act).any() else None,
        'sd_gt3dB_frames': int(big.sum()),
        'sd_gt3dB_near_flips': frac(big & m_f, big),
        'sd_gt3dB_near_flips_or_transitions': frac(big & (m_f | m_t), big),
    }


def pair_metrics(stock, test):
    m = seg_snr(stock, test)
    n = min(len(stock), len(test))
    m['estoi_vs_stock'] = float(stoi(stock[:n], test[:n], FS, extended=True))
    m['lsd_dB'] = lsd_db(test, stock)
    m['nmr_dB'] = nmr_proxy(test, stock)
    return m


def main():
    raw_dir, audio_dir, dec_dir, out_json = sys.argv[1:5]
    seeds = sys.argv[5:]
    results = {}
    for raw in sorted(glob.glob(os.path.join(raw_dir, '*.raw'))):
        name = os.path.basename(raw)[:-4]
        stock_path = os.path.join(audio_dir, name + '.stock.raw')
        if not os.path.exists(stock_path):
            continue
        orig = read_raw(raw)
        stock = read_raw(stock_path)
        ref_v = np.loadtxt(os.path.join(dec_dir, name + '.ref.txt'), dtype=int)
        rule_v = np.loadtxt(os.path.join(dec_dir, name + '.rule.txt'), dtype=int)
        n_enc = 4 * ((os.path.getsize(raw) // 2) // 320)

        r = {'n_frames_enc': n_enc,
             'flip_rate_pct': round(100.0 * np.mean(
                 ref_v[:n_enc] != rule_v[:n_enc]), 3)}
        n0 = min(len(orig), len(stock))
        r['estoi_orig_stock'] = float(stoi(orig[:n0], stock[:n0], FS,
                                           extended=True))
        versions = ['swap'] + [f'rand{s}' for s in seeds]
        if os.path.exists(os.path.join(audio_dir, name + '.stock_o2.raw')):
            versions.append('stock_o2')
        for ver in versions:
            test = read_raw(os.path.join(audio_dir, f'{name}.{ver}.raw'))
            m = pair_metrics(stock, test)
            if ver != 'stock_o2':
                n1 = min(len(orig), len(test))
                m['estoi_orig'] = float(stoi(orig[:n1], test[:n1], FS,
                                             extended=True))
                m['destoi_orig'] = m['estoi_orig'] - r['estoi_orig_stock']
            if ver == 'swap':
                m['attribution'] = error_attribution(stock, test, ref_v,
                                                     rule_v, n_enc)
            elif ver.startswith('rand'):
                rv = np.loadtxt(os.path.join(dec_dir, f'{name}.{ver}.txt'),
                                dtype=int)
                m['attribution'] = error_attribution(stock, test, ref_v,
                                                     rv, n_enc)
            r[ver] = m
        results[name] = r
        print(f'   {name}: swap segSNR {r["swap"]["segsnr_mean_dB"]:.1f} dB '
              f'ESTOI(vs stock) {r["swap"]["estoi_vs_stock"]:.4f} '
              f'dESTOI(orig) {r["swap"]["destoi_orig"]:+.4f}')

    with open(out_json, 'w') as fh:
        json.dump(results, fh, indent=1)
    print(f'   wrote {out_json}')


if __name__ == '__main__':
    main()
