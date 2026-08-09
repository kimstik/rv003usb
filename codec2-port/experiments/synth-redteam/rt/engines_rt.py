"""Red-team engines: best-lawyer rebuilds of round-1 losers, fixed-point
realism for the round-1 winner, and the second-wave candidates G1-G3/G5.

Frame convention identical to round-1 bench (common.py): dict with
Wo (rad/sample), A (harmonic amps), phi (phases), N (samples).
All engines keep state across frames the way the MCU code would.
"""

import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "bench_r1"))

from common import FS  # noqa: E402
from engines import (lpc_from_env, lpc_harmonic_mags, csd_quantize,  # noqa: E402
                     poles_to_sos, _sos_stable)

TWO_PI = 2 * np.pi


# ============================================================================
# Mission 1a. MEANDER, best lawyer: band-limited basis waves
# ============================================================================
# Round-1 killed meander for aliasing of *naive* sampled waves.  A band-limited
# basis (harmonics truncated below Nyquist) removes aliasing entirely, and the
# triangular compensation solve stays exact if it uses the ACTUAL (truncated)
# harmonic content of each basis wave.  Two realizations:
#   mode="exact": every basis truncated at its own qmax  (quality bound;
#                 flash-wise this is one table per basis = wavetable synth)
#   mode="mip":   per-octave mipmap levels qmax in {1,3,7,15,31,63}
#                 (realistic flash; basis may lack harmonics it could carry
#                 -> the solve knows and compensates via higher bases)
# Replay "ideal" = exact Fourier evaluation (infinite-resolution table);
# replay "table" = Ntab-point tables, nearest lookup, int16 quantization
# (the actual MCU mechanism; measures replay noise honestly).

MIP_LEVELS = (1, 3, 7, 15, 31, 63, 127)


def _sq_w(q):
    return (4.0 / np.pi) / q


def _tri_w(q):
    return (8.0 / np.pi ** 2) * ((-1) ** ((q - 1) // 2)) / (q * q)


def _qmax_exact(m, f0):
    q = int((FS / 2.0 - 1.0) / (m * f0))
    if q < 1:
        return 1
    return q if q % 2 == 1 else q - 1


def _qmax_mip(m, f0):
    qe = _qmax_exact(m, f0)
    lvl = 1
    for q in MIP_LEVELS:
        if q <= qe:
            lvl = q
    return lvl


def meander_solve_bl(A_target, f0, basis="square", mode="exact"):
    """Compensation solve with the actual truncated harmonic content."""
    L = len(A_target)
    w = _sq_w if basis == "square" else _tri_w
    qm = _qmax_exact if mode == "exact" else _qmax_mip
    Q = [0] + [qm(m, f0) for m in range(1, L + 1)]
    B = np.zeros(L + 1)
    for k in range(1, L + 1):
        c = 0.0
        q = 3
        while q <= k:
            if k % q == 0:
                m = k // q
                if q <= Q[m]:
                    c += B[m] * w(q)
            q += 2
        B[k] = (A_target[k - 1] - c) / w(1)
    return B[1:], Q[1:]


def _bl_wave(ph_frac, qmax, basis):
    """Band-limited square/triangle at phase ph_frac (in periods), exact."""
    w = _sq_w if basis == "square" else _tri_w
    qs = np.arange(1, qmax + 1, 2)
    # sine-phase convention like the naive round-1 waves
    return sum(w(q) * np.sin(TWO_PI * q * ph_frac) for q in qs)


def _bl_table(qmax, basis, ntab):
    i = np.arange(ntab)
    t = _bl_wave(i / ntab, qmax, basis)
    # int16 quantization (Q15 against a fixed max |wave| <= 1.5)
    scale = 32767.0 / 1.5
    return np.round(t * scale) / scale


def ntab_for(qmax):
    """Table length: 8 samples per highest harmonic, pow2, capped."""
    n = 8 * (qmax + 1)
    p = 1
    while p < n:
        p *= 2
    return int(min(2048, max(64, p)))


def synth_meander_bl(frames, basis="square", mode="exact", replay="ideal"):
    Lmax = max(len(f["A"]) for f in frames)
    phase = np.zeros(Lmax)
    tables = {}
    out = []
    for f in frames:
        N, Wo = f["N"], f["Wo"]
        f0 = Wo * FS / TWO_PI
        L = len(f["A"])
        B, Q = meander_solve_bl(f["A"], f0, basis=basis, mode=mode)
        n = np.arange(1, N + 1)
        m = np.arange(1, L + 1)
        inc = m * Wo / TWO_PI
        ph = phase[:L, None] + inc[:, None] * n[None, :]
        y = np.zeros(N)
        for mi in range(L):
            if abs(B[mi]) < 1e-12:
                continue
            if replay == "ideal":
                y += B[mi] * _bl_wave(ph[mi] % 1.0, Q[mi], basis)
            else:
                key = (Q[mi], basis)
                if key not in tables:
                    tables[key] = _bl_table(Q[mi], basis, ntab_for(Q[mi]))
                tab = tables[key]
                pos = (ph[mi] % 1.0) * len(tab)
                if replay == "table-lin":
                    i0 = np.floor(pos).astype(int) % len(tab)
                    frc = pos - np.floor(pos)
                    i1 = (i0 + 1) % len(tab)
                    y += B[mi] * (tab[i0] * (1 - frc) + tab[i1] * frc)
                else:
                    idx = np.floor(pos).astype(int) % len(tab)
                    y += B[mi] * tab[idx]
        out.append(y)
        phase[:Lmax] = np.mod(phase[:Lmax]
                              + np.arange(1, Lmax + 1) * Wo / TWO_PI * N, 1.0)
    return np.concatenate(out)


def meander_bl_flash_bytes(f0_min=50.0):
    """Flash of the mipmap tables (worst case: all levels present)."""
    total = 0
    for q in MIP_LEVELS:
        total += 2 * ntab_for(q)
    return total


def _polyblep(t, dt):
    """Standard 2-sample polynomial BLEP residual (vectorized)."""
    out = np.zeros_like(t)
    m1 = t < dt
    tt = t[m1] / max(dt, 1e-9)
    out[m1] = tt + tt - tt * tt - 1.0
    m2 = t > 1.0 - dt
    tt = (t[m2] - 1.0) / max(dt, 1e-9)
    out[m2] = tt * tt + tt + tt + 1.0
    return out


def synth_meander_blep(frames):
    """Naive square bank + polyBLEP edge correction; amplitudes from the
    round-1 ideal-square solve (polyBLEP emulates the ideal square)."""
    from engines import meander_solve
    Lmax = max(len(f["A"]) for f in frames)
    phase = np.zeros(Lmax)
    out = []
    for f in frames:
        N, Wo = f["N"], f["Wo"]
        L = len(f["A"])
        B = meander_solve(f["A"], basis="square")
        n = np.arange(1, N + 1)
        m = np.arange(1, L + 1)
        inc = m * Wo / TWO_PI
        y = np.zeros(N)
        for mi in range(L):
            if abs(B[mi]) < 1e-12:
                continue
            p = (phase[mi] + inc[mi] * n) % 1.0
            v = np.where(p < 0.5, 1.0, -1.0)
            v = v + _polyblep(p, inc[mi])
            v = v - _polyblep((p + 0.5) % 1.0, inc[mi])
            y += B[mi] * v
        out.append(y)
        phase[:Lmax] = np.mod(phase[:Lmax]
                              + np.arange(1, Lmax + 1) * Wo / TWO_PI * N, 1.0)
    return np.concatenate(out)


# ============================================================================
# Mission 1b. CYCLE-REPLAY, best lawyer: fixed-length live table,
# deadband incremental updates, optional decimated update rate.
# ============================================================================
# Round-1 table was length round(P) and rebuilt in full every frame ->
# setup ~ P*L ~= P^2/2 MAC.  Red-team form:
#   * FIXED pow2 table length Nt (bracket by L): harmonic k lives at k/Nt
#     cycles/entry regardless of Wo -> a Wo change costs NOTHING (only the
#     replay increment changes).  This kills the biggest rebuild trigger.
#   * ONE live table, updated in place per harmonic only when the harmonic's
#     complex amplitude moved > eps dB against the LAST-WRITTEN value
#     (deadband vs last-written: bounded error, no drift).
#   * update_every=M evaluates the deadband at 1/M frame rate (decimated).
#   * no double buffer, no crossfade: updates are <= eps by construction.

def _nt_for_L(L):
    if L <= 24:
        return 64
    if L <= 52:
        return 128
    return 256


def synth_cycle_replay_rt(frames, eps_db=0.5, update_every=1, interp="linear",
                          stats=None):
    phase = 0.0
    tab = None
    last = None          # last-written complex amplitude per harmonic
    Nt = None
    out = []
    eps_lin = 10.0 ** (eps_db / 20.0) - 1.0
    for fi, f in enumerate(frames):
        N, Wo = f["N"], f["Wo"]
        L = len(f["A"])
        P = TWO_PI / Wo
        want_nt = _nt_for_L(L)
        rebuilt = False
        if tab is None or want_nt != Nt:
            Nt = want_nt
            tab = np.zeros(Nt)
            last = np.zeros(Nt // 2 + 1, dtype=complex)
            rebuilt = True
        nup = 0
        if rebuilt or fi % update_every == 0:
            c = np.zeros(Nt // 2 + 1, dtype=complex)
            kk = np.arange(1, min(L, Nt // 2) + 1)
            c[kk] = f["A"][:len(kk)] * np.exp(1j * f["phi"][:len(kk)])
            amax = np.abs(c).max() + 1e-12
            i = np.arange(Nt)
            for k in range(1, Nt // 2 + 1):
                d = c[k] - last[k]
                thr = eps_lin * max(abs(c[k]), abs(last[k])) + 1e-4 * amax
                if abs(d) > thr:
                    tab += (d * np.exp(1j * TWO_PI * k * i / Nt)).real
                    last[k] = c[k]
                    nup += 1
        if stats is not None:
            stats.append(nup)
        y = np.zeros(N)
        inc = 1.0 / P
        pos = (phase + inc * np.arange(N)) * Nt
        if interp == "linear":
            i0 = np.floor(pos).astype(int) % Nt
            fr = pos - np.floor(pos)
            i1 = (i0 + 1) % Nt
            y = tab[i0] * (1 - fr) + tab[i1] * fr
        else:
            y = tab[np.round(pos).astype(int) % Nt]
        phase = (phase + inc * N) % 1.0
        out.append(y)
    return np.concatenate(out)


# ============================================================================
# Shared impulse-train excitation (identical to round-1 impulse-iir)
# ============================================================================

class _Exciter:
    def __init__(self):
        self.phase = 0.0
        self.pend = 0.0

    def frame(self, N, P, g, split=True, zero_mean=True):
        exc = np.zeros(N + 1)
        exc[0] = self.pend
        inc = 1.0 / P
        for n in range(N):
            self.phase += inc
            if self.phase >= 1.0:
                self.phase -= 1.0
                frac = self.phase / inc
                if split:
                    exc[n] += g * frac
                    exc[n + 1] += g * (1.0 - frac)
                else:
                    exc[n] += g
        self.pend = exc[N]
        if zero_mean:
            exc[:N] -= g * inc
        return exc[:N]


def _frame_gain(a_use, G, Wo, L, A):
    M = lpc_harmonic_mags(a_use, G, Wo, L)
    s = np.sqrt((A ** 2).sum() / max((M ** 2).sum(), 1e-18))
    P = TWO_PI / Wo
    return (P / 2.0) * s * G


# ============================================================================
# Mission 3 / G1: Kelly-Lochbaum lattice with CSD-quantized reflection coeffs
# ============================================================================

def a_to_k(a):
    """LPC polynomial -> reflection coefficients (backward Levinson)."""
    order = len(a) - 1
    A = np.array(a, dtype=float)
    ks = np.zeros(order + 1)
    for i in range(order, 0, -1):
        ks[i] = A[i]
        if abs(1.0 - ks[i] ** 2) < 1e-12:
            ks[i] = np.sign(ks[i]) * (1 - 1e-6)
        denom = 1.0 - ks[i] ** 2
        Anew = np.zeros(i)
        Anew[0] = 1.0
        for j in range(1, i):
            Anew[j] = (A[j] - ks[i] * A[i - j]) / denom
        A = Anew
    return ks[1:]


def k_to_a(k):
    """Reflection coefficients -> LPC polynomial (forward Levinson)."""
    order = len(k)
    a = np.array([1.0])
    for i in range(1, order + 1):
        anew = np.zeros(i + 1)
        anew[0] = 1.0
        for j in range(1, i):
            anew[j] = a[j] + k[i - 1] * a[i - j]
        anew[i] = k[i - 1]
        a = anew
    return a


K_CLAMP = 1.0 - 2.0 ** -8


def quantize_k_csd(k, terms=3, exp_lo=-10, exp_hi=0):
    kq = np.array([csd_quantize(x, terms, exp_lo, exp_hi) for x in k])
    return np.clip(kq, -K_CLAMP, K_CLAMP)


def synth_kl_lattice(frames, order=10, csd_terms=3):
    """Impulse train -> all-pole lattice with CSD reflection coefficients.
    Stability guaranteed by |k|<1 regardless of quantization."""
    b = np.zeros(order + 1)
    exci = _Exciter()
    out = []
    for f in frames:
        N, Wo = f["N"], f["Wo"]
        L = len(f["A"])
        a, G = lpc_from_env(f["A"], Wo, order=order)
        k = a_to_k(a)
        kq = quantize_k_csd(k, csd_terms)
        a_use = k_to_a(kq)
        g = _frame_gain(a_use, G, Wo, L, f["A"])
        exc = exci.frame(N, TWO_PI / Wo, g)
        y = np.zeros(N)
        for n in range(N):
            fv = exc[n]
            for i in range(order, 0, -1):
                fv = fv - kq[i - 1] * b[i - 1]
                b[i] = b[i - 1] + kq[i - 1] * fv
            b[0] = fv
            y[n] = fv
        out.append(y)
    return np.concatenate(out)


# ============================================================================
# Mission 3 / G2: Chamberlin SVF cascade with CSD f/q coefficients
# ============================================================================
# Per biquad section (1, b1, b2):  f = sqrt(1+b1+b2), q = (1-b2)/f.
# Lowpass output has all-pole numerator (gain f^2, one delay) -> cascade of
# lowpass-out sections realizes 1/A(z) up to gain (absorbed by frame gain).
# Stability of the quantized section iff f>0, q>0, f^2+2qf<4 (clamped).

def sos_to_svf(sections):
    fq = []
    for (_, b1, b2) in sections:
        f2 = max(1.0 + b1 + b2, 1e-9)
        f = np.sqrt(f2)
        q = max((1.0 - b2) / f, 1e-6)
        fq.append((f, q))
    return fq


def quantize_svf_csd(fq, terms=3, exp_lo=-12, exp_hi=1):
    out = []
    for (f, q) in fq:
        fqz = csd_quantize(f, terms, exp_lo, exp_hi)
        qqz = csd_quantize(q, terms, exp_lo, exp_hi)
        fqz = max(fqz, 2.0 ** exp_lo)
        qqz = max(qqz, 2.0 ** exp_lo)
        # stability clamp: f^2 + 2qf < 4
        while fqz * fqz + 2 * qqz * fqz >= 4.0:
            fqz *= 0.5
        out.append((fqz, qqz))
    return out


def svf_equivalent_poly(fq):
    a = np.array([1.0])
    for (f, q) in fq:
        b1 = f * f + q * f - 2.0
        b2 = 1.0 - q * f
        a = np.convolve(a, [1.0, b1, b2])
    return a


def synth_svf(frames, order=10, csd_terms=3):
    exci = _Exciter()
    nsec = order // 2
    low = np.zeros(nsec)
    band = np.zeros(nsec)
    out = []
    for f in frames:
        N, Wo = f["N"], f["Wo"]
        L = len(f["A"])
        a, G = lpc_from_env(f["A"], Wo, order=order)
        sections = poles_to_sos(a)
        fq = quantize_svf_csd(sos_to_svf(sections), csd_terms)
        a_use = svf_equivalent_poly(fq)
        g = _frame_gain(a_use, G, Wo, L, f["A"])
        # remove the per-section f^2 static gain from the drive
        gain_corr = np.prod([ff * ff for (ff, _) in fq])
        exc = exci.frame(N, TWO_PI / Wo, g) / max(gain_corr, 1e-18)
        y = np.zeros(N)
        for n in range(N):
            x = exc[n]
            for si, (ff, qq) in enumerate(fq):
                low[si] += ff * band[si]
                high = x - low[si] - qq * band[si]
                band[si] += ff * high
                x = low[si]
            y[n] = x
        out.append(y)
    return np.concatenate(out)


# ============================================================================
# Mission 3 / G3: parallel SOS (residue form) + per-formant noise mixing
# ============================================================================

def parallel_sections(a, G):
    """Partial fractions of G/A(z) -> [(n0, n1, b1, b2, fc_hz), ...]."""
    from scipy.signal import residuez
    r, p, _ = residuez(np.array([G]), np.array(a))
    used = np.zeros(len(p), dtype=bool)
    sec = []
    for i in range(len(p)):
        if used[i]:
            continue
        used[i] = True
        if abs(p[i].imag) > 1e-8:
            j = np.argmin(np.abs(p - np.conj(p[i])) + used * 1e9)
            used[j] = True
            n0 = 2 * r[i].real
            n1 = -2 * (r[i] * np.conj(p[i])).real
            b1 = -2 * p[i].real
            b2 = abs(p[i]) ** 2
            fc = abs(np.angle(p[i])) / TWO_PI * FS
        else:
            n0 = r[i].real
            n1 = 0.0
            b1 = -p[i].real
            b2 = 0.0
            fc = 0.0 if p[i].real > 0 else FS / 2
        sec.append((n0, n1, b1, b2, fc))
    return sec


def quantize_parallel_csd(sec, terms=3):
    out = []
    for (n0, n1, b1, b2, fc) in sec:
        q = lambda x: csd_quantize(x, terms, -12, 6)
        b1q, b2q = q(b1), q(b2)
        if not (abs(b2q) < 1.0 and abs(b1q) < 1.0 + b2q + 1e-9):
            # bandwidth-expand this section until stable
            gtry = 1.0
            while gtry > 0.85:
                gtry *= 0.97
                b1q, b2q = q(b1 * gtry), q(b2 * gtry * gtry)
                if abs(b2q) < 1.0 and abs(b1q) < 1.0 + b2q + 1e-9:
                    break
        out.append((q(n0), q(n1), b1q, b2q, fc))
    return out


def parallel_response(sec, Wo, L):
    k = np.arange(1, L + 1)
    z1 = np.exp(-1j * k * Wo)
    H = np.zeros(L, dtype=complex)
    for (n0, n1, b1, b2, _) in sec:
        H += (n0 + n1 * z1) / (1.0 + b1 * z1 + b2 * z1 * z1)
    return np.abs(H)


def synth_parallel_sos(frames, order=10, csd_terms=3, noise_above_hz=None,
                       seed=1234):
    exci = _Exciter()
    rng = np.random.default_rng(seed)
    st = None
    out = []
    for f in frames:
        N, Wo = f["N"], f["Wo"]
        L = len(f["A"])
        a, G = lpc_from_env(f["A"], Wo, order=order)
        sec = quantize_parallel_csd(parallel_sections(a, G), csd_terms)
        if st is None or len(st) != len(sec):
            st = [np.zeros(3) for _ in sec]   # x1, y1, y2 per section
        M = parallel_response(sec, Wo, L)
        s = np.sqrt((f["A"] ** 2).sum() / max((M ** 2).sum(), 1e-18))
        P = TWO_PI / Wo
        g = (P / 2.0) * s
        exc = exci.frame(N, P, g)
        noise = rng.standard_normal(N) * (g / np.sqrt(P))
        y = np.zeros(N)
        for si, (n0, n1, b1, b2, fc) in enumerate(sec):
            x = exc
            if noise_above_hz is not None and fc >= noise_above_hz:
                x = noise
            x1, y1, y2 = st[si]
            yy = np.zeros(N)
            for n in range(N):
                v = n0 * x[n] + n1 * x1 - b1 * y1 - b2 * y2
                yy[n] = v
                x1 = x[n]
                y2 = y1
                y1 = v
            st[si][:] = (x1, y1, y2)
            y += yy
        out.append(y)
    return np.concatenate(out)


# ============================================================================
# Mission 3 / G5-G6: Karplus-Strong style recirculation (period-domain IIR)
# ============================================================================
# The only form with a real saving claim: run the order-10 IIR only over ONE
# period per frame (persistent period-domain state) -> table; replay the
# table.  Build cost P*order MAC/frame instead of N*order MAC -> saving
# exists only when P < N (F0 > 50 Hz at 20 ms frames), vanishing exactly
# where cycle-replay needed rescue.  Implemented to get honest quality data.

def synth_ks_period_iir(frames, order=10):
    exci = _Exciter()
    state = np.zeros(order)
    phase = 0.0
    tab_prev = None
    out = []
    for f in frames:
        N, Wo = f["N"], f["Wo"]
        L = len(f["A"])
        a, G = lpc_from_env(f["A"], Wo, order=order)
        g = _frame_gain(a, G, Wo, L, f["A"])
        P = TWO_PI / Wo
        Nt = max(8, int(round(P)))
        # one period of excitation: single split impulse at the period start
        exc = np.zeros(Nt)
        exc[0] = g
        exc -= g / Nt
        tabn = np.zeros(Nt)
        A1 = a[1:]
        st = state
        for n in range(Nt):
            v = exc[n] - np.dot(A1, st)
            tabn[n] = v
            st = np.roll(st, 1)
            st[0] = v
        state = st
        if tab_prev is None:
            tab_prev = tabn
        y = np.zeros(N)
        inc = 1.0 / P
        for n in range(N):
            pos = phase * Nt
            i0 = int(pos) % Nt
            fr = pos - int(pos)
            i1 = (i0 + 1) % Nt
            v_new = tabn[i0] * (1 - fr) + tabn[i1] * fr
            Ntp = len(tab_prev)
            posp = phase * Ntp
            j0 = int(posp) % Ntp
            frp = posp - int(posp)
            j1 = (j0 + 1) % Ntp
            v_old = tab_prev[j0] * (1 - frp) + tab_prev[j1] * frp
            w = min(1.0, (n + 1) / 32.0)
            y[n] = v_old * (1 - w) + v_new * w
            phase += inc
            if phase >= 1.0:
                phase -= 1.0
        tab_prev = tabn
        out.append(y)
    return np.concatenate(out)


# ============================================================================
# Mission 3 / G8: Vaidyanathan two-allpass LSP decomposition, CSD cos(LSP)
# ============================================================================
# A(z) = (P(z)+Q(z))/2;  P = (1+z^-1) prod(1-2*c_i z^-1 + z^-2) over odd LSPs,
# Q = (1-z^-1) prod(...) over even LSPs, c_i = cos(w_i).  Quantizing c_i is a
# pure LSP shift; A stays minimum-phase for ANY c values in (-1,1) that keep
# the interlacing order (box constraint + ordering, guarded by the bitstream's
# own check_lsp_order).  Structure cost ~= 1 CSD-mult per section per sample,
# allpass internals have |H|=1 (no overflow by construction) -- the golden
# model here checks the transfer-function claims (SD, stability); structural
# headroom is a cost-model note.

def a_to_lsp_cos(a):
    """LPC polynomial -> cos(LSP) for P-set (odd) and Q-set (even)."""
    M = len(a) - 1
    ar = a[::-1]
    P = np.concatenate([a, [0.0]]) + np.concatenate([[0.0], ar])
    Q = np.concatenate([a, [0.0]]) - np.concatenate([[0.0], ar])
    # deflate trivial roots: P has z=-1, Q has z=+1
    Pd = np.polydiv(P, np.array([1.0, 1.0]))[0]
    Qd = np.polydiv(Q, np.array([1.0, -1.0]))[0]
    def angles(poly):
        r = np.roots(poly)
        ang = np.angle(r)
        ang = ang[(ang > 1e-9) & (ang < np.pi - 1e-9)]
        return np.sort(ang)
    wp = angles(Pd)
    wq = angles(Qd)
    return np.cos(wp), np.cos(wq)


def lsp_cos_to_a(cp, cq):
    """cos(LSP) sets -> A(z) = (P(z)+Q(z))/2."""
    P = np.array([1.0, 1.0])
    for c in cp:
        P = np.convolve(P, [1.0, -2.0 * c, 1.0])
    Q = np.array([1.0, -1.0])
    for c in cq:
        Q = np.convolve(Q, [1.0, -2.0 * c, 1.0])
    A = 0.5 * (P + Q)
    return A[:-1]      # degree M+1 leading terms cancel; drop the ~0 tail


C_CLAMP = 1.0 - 2.0 ** -10
ORDER_SEP = 2.0 ** -9


def quantize_lsp_csd(cp, cq, terms=3, exp_lo=-10, exp_hi=0):
    """CSD-quantize cos(LSP); restore interlacing if rounding broke it
    (the check_lsp_order move).  Returns (cp_q, cq_q, n_order_fixes)."""
    cpq = np.clip([csd_quantize(c, terms, exp_lo, exp_hi) for c in cp],
                  -C_CLAMP, C_CLAMP)
    cqq = np.clip([csd_quantize(c, terms, exp_lo, exp_hi) for c in cq],
                  -C_CLAMP, C_CLAMP)
    # merged sequence must be strictly decreasing in cos domain, alternating
    # P,Q starting with P (w_1 < w_2 < ... => c_1 > c_2 > ...);
    # cp[i] pairs with w_{2i+1}, cq[i] with w_{2i+2}
    seq = []
    for i in range(max(len(cpq), len(cqq))):
        if i < len(cpq):
            seq.append(("p", i, cpq[i]))
        if i < len(cqq):
            seq.append(("q", i, cqq[i]))
    fixes = 0
    for j in range(1, len(seq)):
        if seq[j][2] >= seq[j - 1][2] - ORDER_SEP / 2:
            seq[j] = (seq[j][0], seq[j][1], seq[j - 1][2] - ORDER_SEP)
            fixes += 1
    for (which, i, val) in seq:
        if which == "p":
            cpq[i] = val
        else:
            cqq[i] = val
    return np.array(cpq), np.array(cqq), fixes


def synth_lsp_allpass(frames, order=10, csd_terms=3):
    """Impulse train through 1/A_q(z) where A_q is rebuilt from CSD-quantized
    cos(LSP).  Transfer-function golden model (float recursion)."""
    state = np.zeros(order)
    exci = _Exciter()
    out = []
    for f in frames:
        N, Wo = f["N"], f["Wo"]
        L = len(f["A"])
        a, G = lpc_from_env(f["A"], Wo, order=order)
        cp, cq = a_to_lsp_cos(a)
        cpq, cqq, _ = quantize_lsp_csd(cp, cq, csd_terms)
        a_use = lsp_cos_to_a(cpq, cqq)
        g = _frame_gain(a_use, G, Wo, L, f["A"])
        exc = exci.frame(N, TWO_PI / Wo, g)
        y = np.zeros(N)
        st = state
        A1 = a_use[1:]
        for n in range(N):
            v = exc[n] - np.dot(A1, st)
            y[n] = v
            st = np.roll(st, 1)
            st[0] = v
        state = st
        out.append(y)
    return np.concatenate(out)


# ============================================================================
# Mission 2. Winner under fixed-point realism: Q15 SOS-CSD cascade
# ============================================================================
# Design modeled: int16 (Q15) section state, CSD coefficient multiply as
# per-term arithmetic shifts of the state, int32 accumulation, state
# requantized to int16 when written.  requant options:
#   "trunc"  - per-term sra only (floor), the cheapest / naive C code
#   "round"  - +0.5 LSB before the final write (1 extra add)
#   "dither" - round + TPDF +-1 LSB dither at the write (2 LFSR adds)

def _csd_terms_of(x, nterms=3, exp_lo=-8, exp_hi=3):
    """Decompose csd_quantize's value into signed power-of-two terms.
    Returns list of (sign, exponent)."""
    terms = []
    r = float(x)
    for _ in range(nterms):
        if abs(r) < 2.0 ** (exp_lo - 1):
            break
        e = int(np.clip(np.round(np.log2(abs(r))), exp_lo, exp_hi))
        c1 = np.sign(r) * 2.0 ** e
        c2 = np.sign(r) * 2.0 ** min(e + 1, exp_hi)
        term = c1 if abs(r - c1) <= abs(r - c2) else c2
        e_used = int(np.round(np.log2(abs(term))))
        terms.append((1 if term > 0 else -1, e_used))
        r -= term
    return terms


def _shift_mul(s, coeff_terms, mode):
    """Fixed-point product of int state s with CSD coeff, per-term shifts.

    Each term +-2^e: e>=0 -> exact shift-left; e<0 -> sra (floor) in trunc
    mode, round-half-up in round/dither modes (costs +1 add on MCU)."""
    acc = 0
    for (sgn, e) in coeff_terms:
        if e >= 0:
            v = s << e
        else:
            k = -e
            if mode == "trunc":
                v = s >> k            # python >> == arithmetic floor: same as sra
            else:
                v = (s + (1 << (k - 1))) >> k
        acc += sgn * v
    return acc


def synth_sos_csd_q15(frames, order=10, csd_terms=3, mode="round",
                      dither_seed=99, level_scale=0.25, tail_frames=0,
                      return_int=False):
    """Q15 golden model of the round-1 winner.  level_scale scales the
    excitation (peak output ~= level_scale of full scale).  tail_frames
    appends zero-excitation frames (idle-channel / limit-cycle probe).
    Returns float signal in full-scale units (1.0 = int16 32767)."""
    exci = _Exciter()
    rng = np.random.default_rng(dither_seed)
    nsec = order // 2
    st = [[0, 0] for _ in range(nsec)]
    out = []
    frames_ext = list(frames)
    if tail_frames:
        last = dict(frames[-1])
        for _ in range(tail_frames):
            frames_ext.append(dict(last, _silent=True))
    for f in frames_ext:
        N, Wo = f["N"], f["Wo"]
        L = len(f["A"])
        a, G = lpc_from_env(f["A"], Wo, order=order)
        sections = poles_to_sos(a)
        qsec = [(csd_quantize(b1, csd_terms), csd_quantize(b2, csd_terms))
                for (_, b1, b2) in sections]
        g_try = 1.0
        while not _sos_stable([(1.0, b1, b2) for (b1, b2) in qsec]) and g_try > 0.85:
            g_try *= 0.97
            qsec = [(csd_quantize(b1 * g_try, csd_terms),
                     csd_quantize(b2 * g_try * g_try, csd_terms))
                    for (_, b1, b2) in sections]
        term_lists = [(
            _csd_terms_of(b1, csd_terms), _csd_terms_of(b2, csd_terms))
            for (b1, b2) in qsec]
        a_use = np.array([1.0])
        for (b1, b2) in qsec:
            a_use = np.convolve(a_use, [1.0, b1, b2])
        g = _frame_gain(a_use, G, Wo, L, f["A"])
        if f.get("_silent"):
            exc = np.zeros(N)
            exci.phase = exci.phase  # keep exciter frozen
        else:
            exc = exci.frame(N, TWO_PI / Wo, g)
        # normalize drive so float output peak ~= level_scale
        exc_i = np.round(exc * level_scale * 32768.0).astype(np.int64)
        y = np.zeros(N)
        for n in range(N):
            x = int(exc_i[n])
            for si, (t1, t2) in enumerate(term_lists):
                s0, s1 = st[si]
                v = x - _shift_mul(s0, t1, mode) - _shift_mul(s1, t2, mode)
                if mode == "dither":
                    v += int(rng.integers(0, 2)) - int(rng.integers(0, 2))
                # saturate to int16
                v = max(-32768, min(32767, v))
                st[si][1] = s0
                st[si][0] = v
                x = v
            y[n] = x
        out.append(y / 32768.0)
    if return_int:
        return np.concatenate(out) * 32768.0
    return np.concatenate(out)


# float golden twin with identical coefficients (for SNR reference)
def synth_sos_csd_float(frames, order=10, csd_terms=3):
    from engines import synth_impulse_iir
    return synth_impulse_iir(frames, order=order, csd=True,
                             csd_terms=csd_terms, csd_form="sos")


ENGINES_RT = {
    "meander-sq-bl-exact": lambda fr: synth_meander_bl(fr, "square", "exact", "ideal"),
    "meander-tri-bl-exact": lambda fr: synth_meander_bl(fr, "triangle", "exact", "ideal"),
    "meander-sq-mip-table": lambda fr: synth_meander_bl(fr, "square", "mip", "table"),
    "meander-tri-mip-table": lambda fr: synth_meander_bl(fr, "triangle", "mip", "table"),
    "meander-sq-mip-lin": lambda fr: synth_meander_bl(fr, "square", "mip", "table-lin"),
    "meander-sq-blep": synth_meander_blep,
    "cr-rt-full": lambda fr: synth_cycle_replay_rt(fr, eps_db=0.0),
    "cr-rt-inc": lambda fr: synth_cycle_replay_rt(fr, eps_db=0.5),
    "cr-rt-inc-1db": lambda fr: synth_cycle_replay_rt(fr, eps_db=1.0),
    "cr-rt-inc-m2": lambda fr: synth_cycle_replay_rt(fr, eps_db=0.5, update_every=2),
    "cr-rt-nn": lambda fr: synth_cycle_replay_rt(fr, eps_db=0.5, interp="nearest"),
    "kl-lattice-csd3": lambda fr: synth_kl_lattice(fr, csd_terms=3),
    "lsp-allpass-csd3": lambda fr: synth_lsp_allpass(fr, csd_terms=3),
    "lsp-allpass-csd2": lambda fr: synth_lsp_allpass(fr, csd_terms=2),
    "svf-csd3": lambda fr: synth_svf(fr, csd_terms=3),
    "svf-csd2": lambda fr: synth_svf(fr, csd_terms=2),
    "parallel-sos-csd3": lambda fr: synth_parallel_sos(fr, csd_terms=3),
    "parallel-sos-noise": lambda fr: synth_parallel_sos(fr, csd_terms=3,
                                                        noise_above_hz=1500.0),
    "ks-period-iir": synth_ks_period_iir,
}
