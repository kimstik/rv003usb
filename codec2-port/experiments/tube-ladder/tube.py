"""tube.py — the excitation ladder for the impulse-train->LPC-IIR ("tube") decoder.

Float golden models of the MCU rungs (simplification-map.md D4-d "лестница
возбуждения"), driven by REAL decoded codec2 parameters (c2sim dumps):

  L0  binary excitation: fractional-delay impulse train (zero-mean) on voiced
      frames, white LFSR-style noise on unvoiced -> 10th-order LPC synthesis
      IIR (coefficients straight from the decoded bitstream params), energy
      matched per 10 ms subframe.
  L1  + pulse dispersion filter (MELP-style): each impulse is stamped as a
      fixed spread pulse.  The FIR is DERIVED here by the published
      McCree--Barnwell recipe (spectrally flattened triangle pulse, 65 taps)
      because the exact MELP table is not redistributed in this repo — see
      make_dispersion_filter() docstring.
  L2  + mixed excitation on voiced frames: lowpassed pulse train + highpassed
      noise, power-complementary 2nd-order Butterworth crossover at a fixed
      frequency (swept 1500/2000/2500 Hz by the runner).
  L3  + aperiodic jitter: on weakly-voiced frames (voiced==1 and MBE voicing
      SNR below a threshold) each pitch period is jittered by U(-25%,+25%).
  L4  + adaptive spectral postfilter Hpf(z) = A(z/g1)/A(z/g2) folded into
      coefficients (numerator FIR a_k g1^k, denominator IIR a_k g2^k), with
      G.729-style tilt compensation 1 - mu z^-1 (mu from the first reflection
      coefficient of the truncated postfilter impulse response) and per-frame
      energy renormalisation (codec2's own lpc_post_filter also renormalises).

All filters keep state across frames (as the MCU would).  Parameters consumed
per 10 ms frame: Wo, L, voiced, a[0..10] (decoded LPC), A[1..L] (decoded
harmonic amplitudes after aks_to_M2 + lpc postfilter — used ONLY for the
per-frame energy match, i.e. as the decoded gain; the spectral shape comes
from a[] alone).
"""

import numpy as np
from scipy.signal import lfilter, butter

FS = 8000
N = 80                      # 10 ms subframe
NFREQ = 256                 # grid for filter power-gain estimates


# ---------------------------------------------------------------------------
# L1: pulse dispersion filter
# ---------------------------------------------------------------------------

def make_dispersion_filter(ntaps=65, tri_up=11, tri_down=23):
    """MELP-style pulse dispersion FIR, derived, not copied.

    Recipe from McCree & Barnwell, "A Mixed Excitation LPC Vocoder Model for
    Low Bit Rate Speech Coding" (IEEE Trans. SAP 3(4), 1995), sec. on pulse
    dispersion: take a typical glottal triangle pulse (asymmetric rise/fall),
    flatten its magnitude spectrum to unity while keeping the phase, and use
    the resulting 65-tap FIR as a fixed filter.  The published MELP-2400
    standard ships a fixed 65-tap table (disp_cof) built exactly this way; we
    re-derive it (asymmetric triangle: 11-sample rise, 23-sample fall at
    8 kHz) rather than copy the table, so numbers here are "MELP-style", not
    bit-exact MELP.  |D(w)| == 1 by construction => harmonic magnitudes and
    the frame energy match are untouched; only phases spread, which is the
    whole point (peaky excitation -> dispersed, less buzzy).
    """
    tri = np.concatenate([np.linspace(0.0, 1.0, tri_up, endpoint=False),
                          np.linspace(1.0, 0.0, tri_down)])
    x = np.zeros(ntaps)
    x[:len(tri)] = tri
    X = np.fft.fft(x)
    mag = np.abs(X)
    # unity magnitude, keep phase; guard digital zeros
    Xf = np.where(mag > 1e-12, X / mag, 1.0)
    h = np.fft.ifft(Xf).real
    return h


# ---------------------------------------------------------------------------
# excitation helpers
# ---------------------------------------------------------------------------

class Lfsr:
    """16-bit Galois LFSR -> uniform white noise in [-1,1).  Deterministic:
    the MCU twin is ~4 ops/sample; python model matches its statistics."""

    def __init__(self, seed=0xACE1):
        self.s = seed & 0xFFFF

    def block(self, n):
        out = np.empty(n)
        s = self.s
        for i in range(n):
            lsb = s & 1
            s >>= 1
            if lsb:
                s ^= 0xB400
            out[i] = (s / 32768.0) - 1.0
        self.s = s
        return out


def filter_power_gain(a, band=None):
    """Mean power gain of 1/A(z) over the rfft grid (optionally a band)."""
    w = np.linspace(0, np.pi, NFREQ + 1)
    k = np.arange(len(a))
    A = np.exp(-1j * np.outer(w, k)) @ a
    H2 = 1.0 / np.maximum(np.abs(A) ** 2, 1e-12)
    if band is not None:
        lo, hi = band
        f = w * FS / (2 * np.pi)
        sel = (f >= lo) & (f <= hi)
        H2 = H2[sel]
    return float(np.mean(H2))


def lpc_harmonic_mags(a, Wo, L):
    """|1/A| sampled at harmonics k*Wo, k=1..L."""
    k = np.arange(1, L + 1)
    z = np.exp(-1j * np.outer(k * Wo, np.arange(len(a))))
    return 1.0 / np.maximum(np.abs(z @ a), 1e-9)


# ---------------------------------------------------------------------------
# the ladder synthesizer
# ---------------------------------------------------------------------------

def synth_ladder(params, rung, crossover_hz=2000.0, jitter_frac=0.25,
                 weak_snr_db=10.0, pf_g1=0.5, pf_g2=0.8, pf_tilt=0.5,
                 seed=0xACE1):
    """Synthesize the whole utterance through ladder rung `rung` (0..4).

    params: dict with per-frame arrays (F frames, 10 ms each):
      Wo (F,), L (F,), voiced (F,), ak (F,11), A (F,160), snr_mbe (F,)
    Returns float signal, len F*N, int16-domain scale (same as c2sim -o).
    """
    Wo_all = params["Wo"]
    L_all = params["L"].astype(int)
    voiced = params["voiced"].astype(int)
    ak_all = params["ak"]
    A_all = params["A"]
    snr = params.get("snr_mbe")
    F = len(Wo_all)

    disp = make_dispersion_filter() if rung >= 1 else None
    Tdisp = len(disp) if disp is not None else 2

    use_mix = rung >= 2
    use_jit = rung >= 3
    use_pf = rung >= 4

    if use_mix:
        b_lp, a_lp = butter(2, crossover_hz / (FS / 2), "low")
        b_hp, a_hp = butter(2, crossover_hz / (FS / 2), "high")
        z_lp = np.zeros(2)
        z_hp = np.zeros(2)

    lfsr = Lfsr(seed)
    iir_state = np.zeros(10)
    # excitation tail carried across frames (dispersion stamps overrun)
    tail = np.zeros(Tdisp + 2)
    tau = 0.0                 # samples until next pulse fires
    out = []

    # L4 postfilter state
    if use_pf:
        zi_num = np.zeros(10)
        zi_den = np.zeros(10)
        z_tilt = np.zeros(1)
        g_pow1 = pf_g1 ** np.arange(11)
        g_pow2 = pf_g2 ** np.arange(11)

    for i in range(F):
        Wo, L, v = Wo_all[i], L_all[i], voiced[i]
        a = ak_all[i]
        A = A_all[i, :L]
        P = 2 * np.pi / Wo

        exc = np.zeros(N + Tdisp + 2)
        exc[:Tdisp + 2] += tail

        target_pow = float(np.sum(A ** 2)) / 2.0     # sinusoidal frame power

        if v:
            # impulse gain: line amplitudes of a P-periodic train of height g
            # are 2g/P; match total harmonic energy through the decoded filter
            M = lpc_harmonic_mags(a, Wo, L)
            s = np.sqrt(np.sum(A ** 2) / max(np.sum(M ** 2), 1e-18))
            g = (P / 2.0) * s

            weak = bool(snr is not None and np.isfinite(snr[i])
                        and snr[i] < weak_snr_db)
            jit = jitter_frac if (use_jit and weak) else 0.0

            # place pulses: tau is time-to-next-fire in samples
            while tau < N:
                n0 = int(np.floor(tau))
                frac = tau - n0
                if disp is None:
                    # 2-tap fractional-delay split (constant 1-sample latency)
                    exc[n0] += g * frac
                    exc[n0 + 1] += g * (1.0 - frac)
                else:
                    exc[n0:n0 + Tdisp] += (g * frac) * disp
                    exc[n0 + 1:n0 + 1 + Tdisp] += (g * (1.0 - frac)) * disp
                step = P
                if jit > 0.0:
                    step *= 1.0 + jit * lfsr.block(1)[0]   # U(-jit, +jit)
                tau += max(step, 2.0)
            tau -= N

            pulse = exc[:N] - g / P          # zero-mean excitation (bakeoff #4)

            if use_mix:
                # noise power density must equal the pulse train's (g^2/P
                # total, flat) so the power-complementary crossover leaves
                # the overall spectrum/energy unchanged; uniform noise in
                # [-1,1) has var 1/3 -> scale by sqrt(3) * g/sqrt(P).
                noise = lfsr.block(N) * (np.sqrt(3.0) * g / np.sqrt(P))
                pulse, z_lp = lfilter(b_lp, a_lp, pulse, zi=z_lp)
                hpn, z_hp = lfilter(b_hp, a_hp, noise, zi=z_hp)
                x = pulse + hpn
            else:
                x = pulse
        else:
            # unvoiced: white noise, variance set for the frame energy target
            pg = filter_power_gain(a)
            sigma = np.sqrt(target_pow / max(pg, 1e-12))
            noise = lfsr.block(N)
            x = sigma * np.sqrt(3.0) * noise     # unif [-1,1): var 1/3
            x = x + exc[:N]                      # dispersion tail from V frame
            tau = max(tau - N, 0.0)

        tail = exc[N:].copy()

        # 10th-order all-pole synthesis filter, state across frames
        y, iir_state = lfilter([1.0], a, x, zi=iir_state)

        if use_pf:
            e_in = float(np.sum(y ** 2)) + 1e-12
            num = a * g_pow1
            den = a * g_pow2
            yp, zi_num = lfilter(num, den, y, zi=zi_num)
            # tilt compensation: k1 of truncated impulse response of Hpf
            h = lfilter(num, den, np.r_[1.0, np.zeros(21)])
            r0 = float(np.dot(h, h))
            r1 = float(np.dot(h[:-1], h[1:]))
            mu = pf_tilt * (r1 / r0 if r0 > 0 else 0.0)
            yp, z_tilt = lfilter([1.0, -mu], [1.0], yp, zi=z_tilt)
            e_out = float(np.sum(yp ** 2)) + 1e-12
            gagc = np.sqrt(e_in / e_out)
            y = yp * np.clip(gagc, 0.1, 10.0)

        out.append(y)

    # Scale convention: c2sim's synthesise() (sine.c) writes each harmonic
    # into one bin of an UNNORMALISED kiss inverse rfft, which yields time
    # amplitude 2*A[l] per harmonic (verified empirically: ref frame RMS ~=
    # 1.9x of sqrt(sum A^2/2)).  Multiply by 2 so the tube output lives on
    # the same int16 scale as the c2sim -o reference.
    return 2.0 * np.concatenate(out)
