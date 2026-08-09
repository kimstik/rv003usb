"""metrics_ladder.py — signal metrics for the tube ladder vs reference synthesis.

Reuses the project's existing metric designs:
  - LSD:       per-frame log-spectral distance, band-limited (default
               100-3700 Hz), silence-gated on the reference — adapted from
               synth-bakeoff/bench/metrics.py lsd_db().
  - NMR-proxy: error spectrum weighted by the inverse of the frame's OWN
               decoded LPC envelope (README §4a "elegant tier"), per frame —
               adapted from synth-bakeoff nmr_proxy_db() to real speech by
               taking the envelope from the decoded a_k of each frame.
  - segSNR:    same convention as oracle/metrics_signal.py (20 ms / 10 ms,
               clamp [-10,35] dB, -40 dB silence gate).  NOTE: the tube and
               the reference use different (free-running vs phase0) phase
               tracks, so segSNR against the reference is a WEAK metric here;
               reported for completeness, magnitude metrics carry the verdict.
  - ESTOI:     pystoi, against reference synthesis AND against the original.

Alignment: both signals sit on the same 10 ms frame grid by construction;
any residual constant offset (reference OLA window centring, IIR group
delay) is removed by a single global lag found by cross-correlation of
smoothed energy envelopes (search +-30 ms), applied to the whole signal.
"""

import numpy as np
from scipy.signal import lfilter

FS = 8000
N = 80


# ---------------------------------------------------------------------------
# alignment
# ---------------------------------------------------------------------------

def envelope(x, fc_hz=60.0):
    """Rectified + one-pole lowpassed energy envelope (phase-insensitive)."""
    alpha = np.exp(-2 * np.pi * fc_hz / FS)
    return lfilter([1 - alpha], [1, -alpha], np.abs(x))


def find_lag(ref, x, max_lag=240):
    """Constant lag of x relative to ref via envelope cross-correlation.
    Positive lag: x is EARLY by `lag` samples (shift x right to align)."""
    n = min(len(ref), len(x))
    er = envelope(ref[:n])
    ex = envelope(x[:n])
    er = er - er.mean()
    ex = ex - ex.mean()
    lags = np.arange(-max_lag, max_lag + 1)
    best, blag = -np.inf, 0
    for lag in lags:
        if lag >= 0:
            c = float(np.dot(er[lag:], ex[:n - lag]))
        else:
            c = float(np.dot(er[:n + lag], ex[-lag:]))
        if c > best:
            best, blag = c, lag
    return blag


def find_lag_lsd(ref, x, max_lag=96, fine=8):
    """Constant lag minimising mean frame LSD (coarse step `fine`, then +-1).

    The envelope-xcorr lag proved too flat/biased on parametric speech (its
    optimum sat ~1 dB LSD away from the true minimum on hts1a), so the
    published metrics use the metric-optimal constant lag per pair — the
    best-case single-delay alignment, applied identically to every rung.
    """
    def lsd_at(lag):
        r, y = apply_lag(ref, x, lag)
        return lsd_stats(y, r)["lsd_mean"]

    coarse = range(-max_lag, max_lag + 1, fine)
    lag0 = min(coarse, key=lsd_at)
    finer = range(max(-max_lag, lag0 - fine + 1),
                  min(max_lag, lag0 + fine - 1) + 1)
    return min(finer, key=lsd_at)


def apply_lag(ref, x, lag):
    """Trim both signals so x delayed by `lag` aligns with ref."""
    if lag >= 0:
        ref, x = ref[lag:], x[:len(x) - lag if lag else len(x)]
        ref, x = ref, x
    else:
        ref, x = ref[:len(ref) + lag], x[-lag:]
    n = min(len(ref), len(x))
    return ref[:n], x[:n]


# ---------------------------------------------------------------------------
# per-frame spectral metrics
# ---------------------------------------------------------------------------

def _frames(x, ref, frame_n=N, win_n=2 * N):
    win = np.hanning(win_n)
    n = min(len(x), len(ref))
    rms = np.sqrt(np.mean(ref ** 2)) + 1e-12
    floor = rms * 10 ** (-40.0 / 20)
    for start in range(0, n - win_n, frame_n):
        r = ref[start:start + win_n]
        if np.sqrt(np.mean(r ** 2)) < floor:
            continue
        yield start, np.abs(np.fft.rfft(x[start:start + win_n] * win)), \
            np.abs(np.fft.rfft(r * win))


def lsd_stats(x, ref, lo_hz=100.0, hi_hz=3700.0):
    """Frame LSD (dB, RMS over band) vs reference; mean/median/p90."""
    f = np.fft.rfftfreq(2 * N, 1 / FS)
    sel = (f >= lo_hz) & (f <= hi_hz)
    vals = []
    for _, X, R in _frames(x, ref):
        lx = 20 * np.log10(np.maximum(X[sel], 1e-6))
        lr = 20 * np.log10(np.maximum(R[sel], 1e-6))
        vals.append(np.sqrt(np.mean((lx - lr) ** 2)))
    v = np.array(vals)
    if v.size == 0:
        return {"lsd_mean": np.nan, "lsd_median": np.nan, "lsd_p90": np.nan}
    return {"lsd_mean": float(v.mean()), "lsd_median": float(np.median(v)),
            "lsd_p90": float(np.percentile(v, 90)), "lsd_frames": int(v.size)}


def nmr_proxy_stats(x, ref, ak, lag, lo_hz=100.0, hi_hz=3700.0,
                    floor_db=-40.0):
    """Envelope-weighted NMR proxy per frame (README §4a, bakeoff metric (c)).

    W(f) = 1/env(f), env = |1/A(e^jw)| of the frame's decoded LPC envelope,
    normalised to its max and floored at floor_db.  ak indexed on the 10 ms
    dump grid; `lag` maps signal sample positions back to dump frames.
    """
    f = np.fft.rfftfreq(2 * N, 1 / FS)
    sel = (f >= lo_hz) & (f <= hi_hz)
    w = 2 * np.pi * f[sel] / FS
    k = np.arange(ak.shape[1])
    Ewk = np.exp(-1j * np.outer(w, k))
    vals = []
    for start, X, R in _frames(x, ref):
        fi = min((start + lag + N) // N, len(ak) - 1)  # frame under window
        env = 1.0 / np.maximum(np.abs(Ewk @ ak[fi]), 1e-9)
        env = np.maximum(env / env.max(), 10 ** (floor_db / 20))
        W = 1.0 / env
        D = np.abs(X[sel] - R[sel])
        vals.append(10 * np.log10(
            np.sum((W * D) ** 2) / max(np.sum((W * R[sel]) ** 2), 1e-18)))
    v = np.array(vals)
    if v.size == 0:
        return {"nmr_median": np.nan, "nmr_p90": np.nan}
    return {"nmr_median": float(np.median(v)),
            "nmr_p90": float(np.percentile(v, 90))}


# ---------------------------------------------------------------------------
# time-domain metrics (reused conventions)
# ---------------------------------------------------------------------------

def seg_snr(ref, test, frame_ms=20.0, hop_ms=10.0):
    """Same convention as oracle/metrics_signal.py."""
    n_len = int(FS * frame_ms / 1000)
    hop = int(FS * hop_ms / 1000)
    rms = np.sqrt(np.mean(ref ** 2)) + 1e-12
    floor = rms * 10 ** (-40.0 / 20)
    vals = []
    for start in range(0, len(ref) - n_len + 1, hop):
        s = ref[start:start + n_len]
        if np.sqrt(np.mean(s ** 2)) < floor:
            continue
        e = s - test[start:start + n_len]
        es, ee = np.sum(s ** 2), np.sum(e ** 2)
        snr = 10 * np.log10(es / ee) if ee > 0 else 35.0
        vals.append(np.clip(snr, -10.0, 35.0))
    v = np.array(vals)
    if v.size == 0:
        return {"segsnr_mean": np.nan, "segsnr_median": np.nan}
    return {"segsnr_mean": float(v.mean()),
            "segsnr_median": float(np.median(v))}


def estoi(ref, test):
    from pystoi import stoi
    n = min(len(ref), len(test))
    return float(stoi(ref[:n], test[:n], FS, extended=True))


def crest_stats(x, ref, frame_ms=20.0, hop_ms=10.0):
    """Per-frame crest factor (peak/RMS, dB) delta vs reference.

    LSD/NMR are magnitude-domain and phase-blind, but LPC-10e 'buzz' is
    largely a TIME-domain property: a naked impulse train through a min-phase
    filter is much peakier than the phase0 sinusoidal sum.  The pulse
    dispersion rung exists precisely to fix this, so measure it in its own
    domain: crest_delta = median over active frames of
    (crest_dB(tube frame) - crest_dB(ref frame)).  ~0 = same temporal
    texture as the reference; >0 = peakier (buzzier excitation).
    """
    n_len = int(FS * frame_ms / 1000)
    hop = int(FS * hop_ms / 1000)
    n = min(len(x), len(ref))
    rms_all = np.sqrt(np.mean(ref[:n] ** 2)) + 1e-12
    floor = rms_all * 10 ** (-30.0 / 20)
    deltas = []
    for start in range(0, n - n_len + 1, hop):
        r = ref[start:start + n_len]
        rr = np.sqrt(np.mean(r ** 2))
        if rr < floor:
            continue
        t = x[start:start + n_len]
        tr = np.sqrt(np.mean(t ** 2)) + 1e-12
        c_r = 20 * np.log10(np.max(np.abs(r)) / rr + 1e-12)
        c_t = 20 * np.log10(np.max(np.abs(t)) / tr + 1e-12)
        deltas.append(c_t - c_r)
    v = np.array(deltas)
    if v.size == 0:
        return {"crest_delta_median": np.nan, "crest_delta_p90": np.nan}
    return {"crest_delta_median": float(np.median(v)),
            "crest_delta_p90": float(np.percentile(v, 90))}
