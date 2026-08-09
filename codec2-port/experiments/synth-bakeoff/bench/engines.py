"""The four contender synthesis engines (float golden models of MCU designs).

All engines take a list of frames (see common.py) and return a contiguous
signal.  They keep internal state across frames exactly the way the MCU
implementation would (phase accumulators, IIR state, wavetables).
"""

import numpy as np
from numpy.polynomial import polynomial as npoly

TWO_PI = 2 * np.pi


# ============================================================================
# 1. osc-bank: phase-continuous oscillator bank (no OLA)
# ============================================================================

def synth_osc_bank(frames, interp_amp=True):
    """Per-harmonic phase accumulators, sin lookup, linear amplitude ramp.

    Phases free-run across frames (harmonic k advances by k*Wo per sample) ->
    no boundary discontinuity by construction.  Amplitudes ramp linearly over
    the frame from previous to current value (1 add/sample/harmonic on MCU).
    """
    Lmax = max(len(f["A"]) for f in frames)
    phase = None
    A_prev = np.zeros(Lmax)
    out = []
    for f in frames:
        N, Wo = f["N"], f["Wo"]
        L = len(f["A"])
        A_tgt = np.zeros(Lmax)
        A_tgt[:L] = f["A"]
        if phase is None:
            phase = np.mod(np.arange(1, Lmax + 1) * 0.0 + f["phi"][0] * 0, TWO_PI)
            # start phases at the frame's min-phase values for harmonics present
            phase = np.zeros(Lmax)
            phase[:L] = f["phi"]
            A_prev = A_tgt.copy()
        k = np.arange(1, Lmax + 1)
        n = np.arange(N)
        ph = phase[:, None] + k[:, None] * Wo * (n[None, :] + 1)
        if interp_amp:
            amp = A_prev[:, None] + (A_tgt - A_prev)[:, None] * ((n[None, :] + 1) / N)
        else:
            amp = np.repeat(A_tgt[:, None], N, axis=1)
        out.append((amp * np.cos(ph)).sum(axis=0))
        phase = np.mod(phase + k * Wo * N, TWO_PI)
        A_prev = A_tgt
    return np.concatenate(out)


# ============================================================================
# 2. impulse-iir: impulse train -> 10th order LPC all-pole filter
# ============================================================================

def lpc_from_env(A_harm, Wo, order=10, nfft=512, gamma=1.0):
    """Fit an all-pole model to the harmonic amplitude set.

    Power spectrum is built by interpolating |A_k| over the rfft grid
    (flat extrapolation at the edges), then autocorrelation + Levinson.
    Returns (a[0..order] with a[0]=1, G).
    """
    L = len(A_harm)
    fbin = np.arange(nfft // 2 + 1) * np.pi / (nfft // 2)  # rad/sample
    fk = np.arange(1, L + 1) * Wo
    mag = np.interp(fbin, fk, A_harm, left=A_harm[0], right=A_harm[-1])
    P = mag ** 2
    full = np.concatenate([P, P[-2:0:-1]])
    r = np.fft.ifft(full).real[: order + 1]
    r[0] *= 1.0 + 1e-9
    # Levinson-Durbin
    a = np.zeros(order + 1)
    a[0] = 1.0
    err = r[0]
    for i in range(1, order + 1):
        acc = r[i] + np.dot(a[1:i], r[i - 1 : 0 : -1])
        k = -acc / err
        a[1:i] = a[1:i] + k * a[i - 1 : 0 : -1]
        a[i] = k
        err *= 1 - k * k
        if err <= 0:
            err = 1e-12
    if gamma != 1.0:  # bandwidth expansion (stability headroom for CSD)
        a = a * gamma ** np.arange(order + 1)
    G = np.sqrt(max(err, 1e-12))
    return a, G


def lpc_harmonic_mags(a, G, Wo, L):
    k = np.arange(1, L + 1)
    z = np.exp(-1j * np.outer(k * Wo, np.arange(len(a))))
    return G / np.maximum(np.abs(z @ a), 1e-9)


def csd_quantize(x, nterms=3, exp_lo=-8, exp_hi=3):
    """Greedy CSD: approximate x by a sum of <=nterms signed powers of two."""
    r = float(x)
    val = 0.0
    for _ in range(nterms):
        if abs(r) < 2.0 ** (exp_lo - 1):
            break
        e = int(np.clip(np.round(np.log2(abs(r))), exp_lo, exp_hi))
        # nearest of 2^e / 2^(e) with rounding of log2 gives within sqrt2;
        # check neighbor exponent for a better fit
        c1 = np.sign(r) * 2.0 ** e
        c2 = np.sign(r) * 2.0 ** min(e + 1, exp_hi)
        term = c1 if abs(r - c1) <= abs(r - c2) else c2
        val += term
        r -= term
    return val


def poles_to_sos(a):
    """Pair the roots of A(z) into biquad sections [(1, b1, b2), ...]."""
    rts = np.roots(a)
    used = np.zeros(len(rts), dtype=bool)
    sections = []
    order = np.argsort(-np.abs(rts.imag))
    for idx in order:
        if used[idx]:
            continue
        p = rts[idx]
        used[idx] = True
        if abs(p.imag) > 1e-8:
            # find its conjugate
            cand = np.where(~used & (np.abs(rts - np.conj(p)) < 1e-6))[0]
            used[cand[0]] = True
            sections.append((1.0, -2 * p.real, abs(p) ** 2))
        else:
            # pair real poles greedily
            reals = np.where(~used & (np.abs(rts.imag) < 1e-8))[0]
            if len(reals):
                q = rts[reals[0]].real
                used[reals[0]] = True
                sections.append((1.0, -(p.real + q), p.real * q))
            else:
                sections.append((1.0, -p.real, 0.0))
    return sections


def _sos_stable(sections):
    return all(abs(b2) < 1.0 and abs(b1) < 1.0 + b2 + 1e-9
               for (_, b1, b2) in sections)


def synth_impulse_iir(frames, order=10, csd=False, csd_terms=3, csd_form="direct",
                      gamma=1.0, split_impulse=True):
    """Impulse train at the pitch period through an LPC all-pole IIR.

    Min-phase is implicit (Levinson yields a min-phase A(z)).  Impulse height
    g = P/2 makes harmonic amplitudes ~= G/|A(e^jkWo)| (impulse-train Fourier
    series).  A scalar per frame matches total harmonic energy to the target.
    split_impulse: fractional pitch period via a 2-tap linear split of the
    impulse (one constant sample of latency, zero period jitter).
    csd_form: "direct" quantizes direct-form a[1..10]; "sos" pairs poles into
    5 biquads and quantizes the biquad coefficients (better conditioned).
    """
    state = np.zeros(order)
    sos_state = None
    exc_phase = 0.0        # in periods (wraps at 1.0)
    exc_pend = 0.0         # split-impulse tail carried into next frame
    out = []
    for f in frames:
        N, Wo = f["N"], f["Wo"]
        L = len(f["A"])
        a, G = lpc_from_env(f["A"], Wo, order=order)
        if gamma != 1.0:
            a = a * gamma ** np.arange(order + 1)
        sections = None
        if csd and csd_form == "sos":
            sections = poles_to_sos(a)
            qsec = [(1.0, csd_quantize(b1, csd_terms), csd_quantize(b2, csd_terms))
                    for (_, b1, b2) in sections]
            g_try = 1.0
            while not _sos_stable(qsec) and g_try > 0.85:
                g_try *= 0.97
                qsec = [(1.0, csd_quantize(b1 * g_try, csd_terms),
                         csd_quantize(b2 * g_try * g_try, csd_terms))
                        for (_, b1, b2) in sections]
            sections = qsec
            # equivalent polynomial for gain matching
            a_use = np.array([1.0])
            for (_, b1, b2) in sections:
                a_use = np.convolve(a_use, [1.0, b1, b2])
        elif csd:
            aq = np.array([1.0] + [csd_quantize(c, csd_terms) for c in a[1:]])

            def stable(av):
                return np.all(np.abs(np.roots(av)) < 1.0)

            g_try = 1.0
            while not stable(aq) and g_try > 0.85:
                g_try *= 0.97
                aexp = a * g_try ** np.arange(order + 1)
                aq = np.array([1.0] + [csd_quantize(c, csd_terms) for c in aexp[1:]])
            a_use = aq
        else:
            a_use = a
        # per-frame gain match (energy over harmonics, using the *used* coeffs)
        M = lpc_harmonic_mags(a_use, G, Wo, L)
        s = np.sqrt((f["A"] ** 2).sum() / max((M ** 2).sum(), 1e-18))
        P = TWO_PI / Wo
        g = (P / 2.0) * s * G
        # excitation: 2-tap split -> impulse at exact (t*+1), constant latency
        exc = np.zeros(N + 1)
        exc[0] = exc_pend
        inc = 1.0 / P
        for n in range(N):
            exc_phase += inc
            if exc_phase >= 1.0:
                exc_phase -= 1.0
                frac = exc_phase / inc  # samples since the true crossing
                if split_impulse:
                    exc[n] += g * frac
                    exc[n + 1] += g * (1.0 - frac)
                else:
                    exc[n] += g
        exc_pend = exc[N]
        # zero-mean excitation: an impulse train carries a DC pedestal g/P
        # which the all-pole filter amplifies by G/A(1) -- with a low F1 the
        # DC gain is large and CSD perturbation of A(1) makes it explode
        # (caught by the NMR-proxy metric, invisible to the spur metric).
        # One extra add/sample on the MCU.
        exc[:N] -= g * inc
        # all-pole IIR, state carried across frames
        y = np.zeros(N)
        if sections is not None:
            if sos_state is None:
                sos_state = [np.zeros(2) for _ in sections]
            x = exc[:N].copy()
            for si, (_, b1, b2) in enumerate(sections):
                st = sos_state[si]
                yy = np.zeros(N)
                for n in range(N):
                    v = x[n] - b1 * st[0] - b2 * st[1]
                    yy[n] = v
                    st[1] = st[0]
                    st[0] = v
                x = yy
            y = x
        else:
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
# 3. meander: square/triangle-wave basis + triangular compensation solve
# ============================================================================

def meander_solve(A_target, basis="square"):
    """Solve basis amplitudes B[m], m=1..L by forward substitution.

    Square wave at m*Wo contributes to harmonic q*m (odd q) with weight
    (4/pi)/q; triangle with (8/pi^2)*(-1)^((q-1)/2)/q^2.  The matrix is
    triangular under the divisibility order with constant diagonal -> exactly
    solvable, conditioning is NOT an issue.  The issue is what q*m*Wo beyond
    Nyquist does after sampling (aliasing) -- measured, not solved.
    """
    L = len(A_target)
    B = np.zeros(L + 1)  # 1-indexed
    if basis == "square":
        diag = 4 / np.pi
        weight = lambda q: (4 / np.pi) / q
    else:
        diag = 8 / np.pi ** 2
        weight = lambda q: (8 / np.pi ** 2) * ((-1) ** ((q - 1) // 2)) / (q * q)
    for k in range(1, L + 1):
        c = 0.0
        q = 3
        while q * 1 <= k:
            if k % q == 0:
                c += B[k // q] * weight(q)
            q += 2
        B[k] = (A_target[k - 1] - c) / diag
    return B[1:]


def _square_wave(phase_frac):
    return np.where(np.mod(phase_frac, 1.0) < 0.5, 1.0, -1.0)


def _triangle_wave(phase_frac):
    p = np.mod(phase_frac, 1.0)
    return 4.0 * np.abs(p - 0.5) - 1.0   # peak +1 at p=0, -1 at p=0.5


def synth_meander(frames, basis="square"):
    """Bank of naive (sampled-ideal) square/triangle waves, amplitudes from the
    triangular solve.  Phase accumulators free-run across frames; basis
    amplitudes step at frame boundaries (adds are cheap, ramps would need
    mul or extra state -- measured as-is)."""
    Lmax = max(len(f["A"]) for f in frames)
    phase = np.zeros(Lmax)     # in periods of each basis wave
    wave = _square_wave if basis == "square" else _triangle_wave
    out = []
    for f in frames:
        N, Wo = f["N"], f["Wo"]
        L = len(f["A"])
        B = np.zeros(Lmax)
        B[:L] = meander_solve(f["A"], basis=basis)
        m = np.arange(1, Lmax + 1)
        inc = m * Wo / TWO_PI
        n = np.arange(1, N + 1)
        ph = phase[:, None] + inc[:, None] * n[None, :]
        out.append((B[:, None] * wave(ph)).sum(axis=0))
        phase = np.mod(phase + inc * N, 1.0)
        # note: phases where m*Wo > pi (above Nyquist) never used (L covers it)
    return np.concatenate(out)


# ============================================================================
# 4. cycle-replay: one pitch-period wavetable per frame + crossfade
# ============================================================================

def synth_cycle_replay(frames, interp="linear", xfade=32, oversample=1):
    """Compute one pitch-period waveform per frame (direct sum of L harmonics
    once), replay via phase accumulator; crossfade old->new table over the
    first `xfade` samples of each frame.

    Table length Nt = round(period); replay increment Nt/P_true table-samples
    per output sample keeps the exact Wo.  The shared phase accumulator (in
    period fractions) carries across frames so the fundamental stays
    phase-continuous; higher harmonics jump by their table difference ->
    crossfade eats the click.
    """
    out = []
    phase = 0.0          # position in period, [0,1)
    table_prev = None
    for f in frames:
        N, Wo = f["N"], f["Wo"]
        L = len(f["A"])
        P = TWO_PI / Wo
        Nt = max(8, int(round(oversample * P)))
        i = np.arange(Nt)
        k = np.arange(1, L + 1)[:, None]
        # build one period; harmonic k at table freq k/Nt -> replayed at k*Wo
        tab = (f["A"][:, None] * np.cos(k * TWO_PI * i[None, :] / Nt + f["phi"][:, None])).sum(axis=0)
        if table_prev is None:
            table_prev = tab
        y = np.zeros(N)
        inc = 1.0 / P
        for n in range(N):
            pos = phase * Nt
            i0 = int(pos) % Nt
            if interp == "linear":
                fr = pos - int(pos)
                i1 = (i0 + 1) % Nt
                v_new = tab[i0] * (1 - fr) + tab[i1] * fr
                Ntp = len(table_prev)
                posp = phase * Ntp
                j0 = int(posp) % Ntp
                frp = posp - int(posp)
                j1 = (j0 + 1) % Ntp
                v_old = table_prev[j0] * (1 - frp) + table_prev[j1] * frp
            else:
                v_new = tab[i0]
                Ntp = len(table_prev)
                v_old = table_prev[int(phase * Ntp) % Ntp]
            if n < xfade:
                w = (n + 1) / (xfade + 1)
                y[n] = v_old * (1 - w) + v_new * w
            else:
                y[n] = v_new
            phase += inc
            if phase >= 1.0:
                phase -= 1.0
        table_prev = tab
        out.append(y)
    return np.concatenate(out)


ENGINES = {
    "osc-bank": synth_osc_bank,
    "impulse-iir": synth_impulse_iir,
    "impulse-iir-csd": lambda fr: synth_impulse_iir(fr, csd=True, csd_terms=3),
    "impulse-iir-csd-sos": lambda fr: synth_impulse_iir(fr, csd=True,
                                                        csd_terms=3,
                                                        csd_form="sos"),
    "meander-sq": lambda fr: synth_meander(fr, basis="square"),
    "meander-tri": lambda fr: synth_meander(fr, basis="triangle"),
    "cycle-replay": synth_cycle_replay,
    "cycle-replay-2x": lambda fr: synth_cycle_replay(fr, oversample=2),
    "cycle-replay-nn": lambda fr: synth_cycle_replay(fr, interp="nearest"),
}
