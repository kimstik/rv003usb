"""degrade.py — controlled degradations for the metrics-adequacy stand.

Two families, opposite perceptual character, matched on "classic" cost (LSD):

BUZZY family (precise-but-buzzy: tonal / modulated / over-sharpened errors —
the kind the H-principle says must be gated HARD):
  buzz-l0      L0-only tube (impulse train -> LPC IIR, no dispersion, no
               mixed excitation) — resynthesis, reuses tube.py rung 0.
  buzz-sharp   extra formant over-sharpening: A(z/g1)/A(z/0.8) postfilter
               applied ON TOP of the reference synthesis (which already
               contains codec2's own lpc postfilter). Knob: g1 down = sharper.
  buzz-spur    stationary tonal spurs added at fixed non-harmonic frequencies.
               Knob: spur level dB rel active-speech RMS.
  buzz-pump    frame-rate amplitude pumping: (1 + d sin 2*pi*25Hz t) gain.
               Knob: depth d.

SMOOTH-NOISE family (noisier-but-smooth: stationary, speech-shaped, masked —
the kind the H-principle says deserves a soft gate):
  smooth-mix   tube rung 2 (mixed excitation) with GENEROUS noise fraction
               (low crossover) — resynthesis, reuses tube.py rung 2.
  smooth-valley envelope-shaped noise added to the reference: white LFSR noise
               through 1/A(z/gamma) per frame (bandwidth-expanded decoded
               envelope: broadened formants => noise relatively strongest in
               the inter-formant valleys, weakest on peaks — the "valley fill"
               of ideas-doc H1). Knob: noise power dB rel frame power.
  smooth-dither TPDF-dithered requantisation of the reference to a coarse
               step. Knob: step size (continuous "low bit depth").
  par-noise    G3+H1: parallel (residue) SOS form of the decoded LPC filter,
               resonators centred >= fc excited by NOISE even when voiced
               (per-formant aspiration). Float form (no CSD) so the noise
               question is isolated from quantisation. Lightweight
               reimplementation of synth-redteam rt/engines_rt.py
               synth_parallel_sos on real decoded params.
  par-plain    same parallel form, all-pulse (comparator for the G3 verdict:
               same structure & cost, no aspiration).

All functions take/return float signals on the c2sim int16 scale, 8 kHz.
Resynthesis variants consume the decoded-1300 params dict of tube-ladder
(run_ladder.load_params convention: Wo, L, voiced, ak, A, snr_mbe).
"""
import os
import sys

import numpy as np
from scipy.signal import lfilter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, "tube-ladder"))
import tube  # noqa: E402  (the tube-ladder golden models, reused verbatim)

FS = 8000
N = 80


# ---------------------------------------------------------------------------
# resynthesis variants (params-driven)
# ---------------------------------------------------------------------------

def buzz_l0(params):
    return tube.synth_ladder(params, rung=0)


def smooth_mix(params, crossover_hz):
    return tube.synth_ladder(params, rung=2, crossover_hz=crossover_hz)


# --- parallel (residue) SOS form + per-formant aspiration (G3/H1) ----------

def _parallel_sections(a):
    """Partial fractions of 1/A(z) -> [(n0, n1, b1, b2, fc_hz), ...].
    Same math as synth-redteam engines_rt.parallel_sections (G=1, float)."""
    from scipy.signal import residuez
    r, p, _ = residuez(np.array([1.0]), np.asarray(a))
    used = np.zeros(len(p), dtype=bool)
    sec = []
    for i in range(len(p)):
        if used[i]:
            continue
        used[i] = True
        if abs(p[i].imag) > 1e-8:
            j = int(np.argmin(np.abs(p - np.conj(p[i])) + used * 1e9))
            used[j] = True
            n0 = 2 * r[i].real
            n1 = -2 * (r[i] * np.conj(p[i])).real
            b1 = -2 * p[i].real
            b2 = abs(p[i]) ** 2
            fc = abs(np.angle(p[i])) / (2 * np.pi) * FS
        else:
            n0, n1 = r[i].real, 0.0
            b1, b2 = -p[i].real, 0.0
            fc = 0.0 if p[i].real > 0 else FS / 2
        sec.append((n0, n1, b1, b2, fc))
    return sec


def _parallel_mags(sec, Wo, L):
    k = np.arange(1, L + 1)
    z1 = np.exp(-1j * k * Wo)
    H = np.zeros(L, dtype=complex)
    for (n0, n1, b1, b2, _) in sec:
        H += (n0 + n1 * z1) / (1.0 + b1 * z1 + b2 * z1 * z1)
    return np.abs(H)


def synth_parallel(params, noise_above_hz=None, seed=0xACE1):
    """Parallel-SOS synthesis from decoded ak.  noise_above_hz=None -> all
    sections pulse-excited on voiced frames (par-plain); a frequency ->
    sections centred >= it are ALWAYS noise-excited (par-noise, G3+H1).
    Unvoiced frames: noise everywhere (as tube L0).  Fractional-delay
    zero-mean impulse train, LFSR noise, state kept across frames."""
    Wo_all = params["Wo"]
    L_all = params["L"].astype(int)
    voiced = params["voiced"].astype(int)
    ak_all = params["ak"]
    A_all = params["A"]
    F = len(Wo_all)

    lfsr = tube.Lfsr(seed)
    zi = None                      # per-section lfilter state
    tau = 0.0
    out = []
    for i in range(F):
        Wo, L, v = Wo_all[i], L_all[i], voiced[i]
        a = ak_all[i]
        A = A_all[i, :L]
        P = 2 * np.pi / Wo
        sec = _parallel_sections(a)
        if zi is None or len(zi) != len(sec):
            zi = [np.zeros(2) for _ in sec]

        M = _parallel_mags(sec, Wo, L)
        s = np.sqrt(np.sum(A ** 2) / max(np.sum(M ** 2), 1e-18))
        g = (P / 2.0) * s

        if v:
            exc = np.zeros(N + 2)
            while tau < N:
                n0 = int(np.floor(tau))
                frac = tau - n0
                exc[n0] += g * frac
                exc[n0 + 1] += g * (1.0 - frac)
                tau += max(P, 2.0)
            tau -= N
            pulse = exc[:N] - g / P
            # noise with the pulse train's flat PSD (unif var 1/3)
            noise = lfsr.block(N) * (np.sqrt(3.0) * g / np.sqrt(P))
        else:
            # unvoiced: everything noise at the frame's target power
            target = np.sum(A ** 2) / 2.0
            pg = np.mean(_parallel_mags(sec, np.pi / 256, 255) ** 2)
            sigma = np.sqrt(target / max(pg, 1e-12))
            pulse = noise = lfsr.block(N) * (np.sqrt(3.0) * sigma)
            tau = max(tau - N, 0.0)

        y = np.zeros(N)
        for si, (n0, n1, b1, b2, fc) in enumerate(sec):
            x = pulse
            if v and noise_above_hz is not None and fc >= noise_above_hz:
                x = noise
            yy, zi[si] = lfilter([n0, n1], [1.0, b1, b2], x, zi=zi[si])
            y += yy
        out.append(y)
    return 2.0 * np.concatenate(out)


# ---------------------------------------------------------------------------
# signal-domain corruptions of the reference (exact alignment by construction)
# ---------------------------------------------------------------------------

def _active_rms(x, gate_db=-40.0):
    rms = np.sqrt(np.mean(x ** 2)) + 1e-12
    floor = rms * 10 ** (gate_db / 20)
    fr = x[:len(x) // N * N].reshape(-1, N)
    act = fr[np.sqrt(np.mean(fr ** 2, axis=1)) >= floor]
    return float(np.sqrt(np.mean(act ** 2))) if len(act) else float(rms)


def buzz_spur(ref, level_db, freqs_hz=(1130.0, 2470.0)):
    """Stationary tonal spurs, level dB rel active-speech RMS (per spur)."""
    n = np.arange(len(ref))
    amp = _active_rms(ref) * 10 ** (level_db / 20) * np.sqrt(2.0)
    y = ref.copy()
    for k, f in enumerate(freqs_hz):
        y = y + amp * np.sin(2 * np.pi * f * n / FS + 0.7 * k)
    return y


def buzz_pump(ref, depth, rate_hz=25.0):
    """Frame-rate amplitude pumping (gain wobble at the 1300 frame rate)."""
    n = np.arange(len(ref))
    return ref * (1.0 + depth * np.sin(2 * np.pi * rate_hz * n / FS))


def buzz_sharp(ref, params, g1, g2=0.8):
    """Extra per-frame formant sharpening A(z/g1)/A(z/g2) + frame AGC."""
    ak = params["ak"]
    F = min(len(ak), len(ref) // N)
    k = np.arange(ak.shape[1])
    zi_num = np.zeros(ak.shape[1] - 1)
    zi_den = np.zeros(ak.shape[1] - 1)
    out = np.array(ref, dtype=float).copy()
    for i in range(F):
        seg = out[i * N:(i + 1) * N]
        num = ak[i] * g1 ** k
        den = ak[i] * g2 ** k
        e_in = np.sum(seg ** 2) + 1e-12
        y, zi_num = lfilter(num, [1.0], seg, zi=zi_num)
        y, zi_den = lfilter([1.0], den, y, zi=zi_den)
        gagc = np.sqrt(e_in / (np.sum(y ** 2) + 1e-12))
        out[i * N:(i + 1) * N] = y * np.clip(gagc, 0.1, 10.0)
    return out


def smooth_valley(ref, params, level_db, gamma=0.6, seed=0xBEEF):
    """Envelope-shaped 'valley fill' noise: white LFSR noise through
    1/A(z/gamma) (bandwidth-expanded decoded envelope), power level_db below
    the frame's power, added to the reference.  Silence stays silent."""
    ak = params["ak"]
    F = min(len(ak), len(ref) // N)
    k = np.arange(ak.shape[1])
    lfsr = tube.Lfsr(seed)
    zi = np.zeros(ak.shape[1] - 1)
    out = np.array(ref, dtype=float).copy()
    gl = 10 ** (level_db / 10)
    for i in range(F):
        seg = ref[i * N:(i + 1) * N]
        p_frame = np.mean(seg ** 2)
        den = ak[i] * gamma ** k
        w = lfsr.block(N)
        y, zi = lfilter([1.0], den, w, zi=zi)
        p_n = np.mean(y ** 2) + 1e-12
        out[i * N:(i + 1) * N] = seg + y * np.sqrt(gl * p_frame / p_n)
    return out


def smooth_dither(ref, step, seed=0xF00D):
    """TPDF-dithered requantisation with step size `step` (int16 domain).
    step = 2^(16-b) models b-bit audio; continuous step is the LSD knob."""
    rng = np.random.default_rng(seed)
    tpdf = (rng.random(len(ref)) - rng.random(len(ref)))  # triangular +-1 LSB
    return np.round(ref / step + tpdf) * step


# knob registry for the auto-tuner (monotone LSD knobs; lo..hi search range)
CORRUPTIONS = {
    "buzz-spur":     dict(fn=lambda r, p, k: buzz_spur(r, k),
                          knob="level_db", lo=-70.0, hi=-15.0, family="buzzy"),
    "buzz-pump":     dict(fn=lambda r, p, k: buzz_pump(r, k),
                          knob="depth", lo=0.01, hi=0.95, family="buzzy"),
    "buzz-sharp":    dict(fn=lambda r, p, k: buzz_sharp(r, p, k),
                          knob="g1", lo=0.75, hi=0.15, family="buzzy"),
    "smooth-valley": dict(fn=lambda r, p, k: smooth_valley(r, p, k),
                          knob="level_db", lo=-40.0, hi=-2.0, family="smooth"),
    "smooth-dither": dict(fn=lambda r, p, k: smooth_dither(r, k),
                          knob="step", lo=2.0, hi=4096.0, family="smooth"),
}
