"""Metrics for the bake-off.  All magnitude-domain (the codec2 model treats
phase as perceptually free, and engines deliberately use different phases),
plus a time-domain continuity (click) metric on each engine's own output.
"""

import numpy as np
from scipy.signal import get_window

from common import FS


def _analysis_segment(x, frames, settle_frames=5):
    """Steady-state slice: drop settle_frames of warmup, use the rest."""
    n0 = sum(f["N"] for f in frames[:settle_frames])
    return x[n0:]


def harmonic_amps(x, f0_hz, L, fs=FS):
    """Complex harmonic amplitudes by windowed correlation (Blackman-Harris).

    Window is long (whole steady segment) so harmonic spacing >> mainlobe.
    Returns |a_k| for k=1..L (amplitude of cos component, i.e. peak amp).
    """
    N = len(x)
    w = get_window("blackmanharris", N)
    wsum = w.sum()
    n = np.arange(N)
    k = np.arange(1, L + 1)
    E = np.exp(-2j * np.pi * np.outer(k, n) * f0_hz / fs)
    a = (E * (x * w)[None, :]).sum(axis=1) * 2.0 / wsum
    return np.abs(a)


def amp_error_db(meas, target, floor_db=-100.0):
    """Per-harmonic error in dB; returns (err_k_db, gain_db, shape_stats).

    gain_db: amplitude-weighted mean error (a single overall gain an MCU
    implementation would fold into the output scaling).  shape errors are
    gain-removed.  Harmonics below (max target - 60 dB) are excluded from
    the *statistics* (inaudible under any envelope), but reported raw.
    """
    t = np.maximum(target, 1e-12)
    m = np.maximum(meas, 1e-12)
    err = 20 * np.log10(m / t)
    err = np.clip(err, floor_db, -floor_db)
    wgt = t ** 2
    gain = float(np.sum(err * wgt) / np.sum(wgt))
    shape = err - gain
    sig = 20 * np.log10(t / t.max()) > -60.0
    stats = {
        "gain_db": gain,
        "shape_mean_abs_db": float(np.mean(np.abs(shape[sig]))),
        "shape_max_abs_db": float(np.max(np.abs(shape[sig]))),
        "shape_rms_db": float(np.sqrt(np.mean(shape[sig] ** 2))),
    }
    return err, stats


def spectrum(x, zp=4):
    """Blackman-Harris windowed, zero-padded magnitude spectrum + freq axis."""
    N = len(x)
    w = get_window("blackmanharris", N)
    X = np.fft.rfft(x * w, n=zp * N)
    f = np.fft.rfftfreq(zp * N, 1 / FS)
    return f, np.abs(X)


def spur_level_db(x, f0_hz, L, zp=4, guard_bins=8):
    """Worst off-harmonic bin, dB below the strongest harmonic.

    guard_bins is in base-window bins (Blackman-Harris mainlobe ~8 bins);
    all bins within +-guard of any harmonic (and DC / Nyquist edges) are
    excluded; the max of what remains is the spur.
    """
    f, mag = spectrum(x, zp)
    N = len(x)
    guard_hz = guard_bins * FS / N
    mask = np.ones(len(f), dtype=bool)
    for k in range(1, L + 1):
        fk = k * f0_hz
        mask &= np.abs(f - fk) > guard_hz
    mask &= f > guard_hz            # DC region
    mask &= f < FS / 2 - guard_hz   # Nyquist edge
    peak = mag.max()
    if not mask.any() or peak <= 0:
        return np.nan
    spur = mag[mask].max()
    return 20 * np.log10(spur / peak)


def nmr_proxy_db(x, ref, env_fn, zp=4, floor_db=-40.0):
    """Error spectrum weighted by inverse envelope (masking proxy).

    D(f) = ||X|-|R|| on the same window/grid; W(f) = 1/env(f) with the
    envelope floored at floor_db below its max (don't reward the metric
    for silence in deep stopbands).  Returns 10log10(sum(WD)^2/sum(W|R|)^2).
    """
    n = min(len(x), len(ref))
    f, X = spectrum(x[:n], zp)
    _, R = spectrum(ref[:n], zp)
    env = env_fn(np.maximum(f, 1.0))
    env = np.maximum(env / env.max(), 10 ** (floor_db / 20))
    W = 1.0 / env
    D = np.abs(X - R)
    return 10 * np.log10(np.sum((W * D) ** 2) / np.sum((W * R) ** 2))


def click_metric(x, frames, boundary_halfwidth=2, settle_frames=2):
    """Continuity at frame boundaries on the engine's own output.

    ratio = max first-difference in boundary neighborhoods /
            95th-percentile first-difference elsewhere (steady content).
    ~1 means boundaries are indistinguishable from steady signal; >>1 = click.
    Also returns the absolute worst boundary jump normalized by signal RMS.
    """
    d = np.abs(np.diff(x))
    bounds = np.cumsum([f["N"] for f in frames])[:-1]
    bmask = np.zeros(len(d), dtype=bool)
    for b in bounds[settle_frames:]:
        lo = max(0, b - 1 - boundary_halfwidth)
        hi = min(len(d), b - 1 + boundary_halfwidth + 1)
        bmask[lo:hi] = True
    interior = d[~bmask][settle_frames * frames[0]["N"]:]
    if len(interior) == 0 or not bmask.any():
        return {"click_ratio": np.nan, "max_jump_over_rms": np.nan}
    p95 = np.percentile(interior, 95)
    rms = np.sqrt(np.mean(x ** 2)) + 1e-12
    return {
        "click_ratio": float(d[bmask].max() / max(p95, 1e-12)),
        "max_jump_over_rms": float(d[bmask].max() / rms),
    }


def lsd_db(x, ref, frame_n=160, lo_hz=100, hi_hz=3400):
    """Log-spectral distortion vs reference over Hann-320 frames (real data)."""
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
    return float(np.mean(vals)) if vals else np.nan
