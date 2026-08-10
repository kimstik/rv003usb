"""inject_tube.py — the system under test + calibrated error injection.

System under test (README §4a mechanism 1): the CHOSEN architecture from
simplification-map.md — the G8-class tube decoder modelled by the float
excitation ladder at the recommended P2 rung set **L0+L2(2500 Hz)+L4-0.50**
(no dispersion, no jitter), driven by real decoded 1300 params (c2sim
--rate 1300 dumps).  This file forks tube-ladder/tube.py synth_ladder()
because the injection hooks live *inside* the per-frame loop; the fork keeps
rung semantics identical (dispersion/jitter switchable off independently,
which the cumulative `rung` flag of the original cannot express).

LPC coefficients are ALWAYS rebuilt from the decoded LSPs via cos(LSP)
(validated against the dumps: max |ak_rebuilt - ak_dec| = 8.3e-6 on hts1a
q1300) — this is exactly the G8 two-allpass data path, where the filter
coefficients ARE cos(LSP) straight from the bitstream tables, and it makes
injection point (a) a first-class citizen instead of an approximation.

Injection points (inj = dict(point, etype, level, seed)):

decoder side
  coslsp   error added to c_i = cos(lsp_i) before the polynomial rebuild
           (simulates CSD-quantised coefficient tables / cos LUT error).
           etypes: white (per frame+coeff, sigma=level), framemod (even
           frames only, sigma=2*level — BFP/exponent-switch pumping),
           dc (constant +level on every coefficient), worst (correlated:
           each LSP pair pushed together by level in cos domain — the
           resonance-sharpening direction; pairs clamp at midpoint).
  state    additive noise at the synthesis-filter input, units = int16 LSB
           at the output (simulates guard-bit truncation in the IIR state;
           for a direct-form all-pole, state roundoff enters through the
           same 1/A(z) transfer as input noise).  etypes: white (uniform,
           rms = level LSB/sqrt(12) — i.e. level = quantisation step q),
           framemod (q=2*level on even frames, 0 on odd — pumping floor),
           dc (constant +level/2 LSB — truncation bias).
  pulsepos excitation impulse timing error, units = samples (simulates the
           period accumulator's fractional resolution).  etypes: white
           (emission position jitter U(-level/2, +level/2)), qround (the
           accumulated period step rounded to the level grid), qtrunc (step
           floored to the grid — systematic pitch-sharp drift, the DC case).
  log2e    per-10ms gain multiplied by 2^e (simulates exp2 LUT error at the
           decoder's energy step).  etypes: white (e ~ N(0, level) per
           frame), framemod (e = +level / -level alternating frames — gain
           pumping), dc (e = +level constant).
  mixfc    mixed-excitation crossover frequency error, relative units.
           etypes: dc (fc*(1+level)), white (fc*(1+N(0,level)) per voiced
           frame, filters redesigned per frame).
  mixnf    noise-fraction (HP noise gain) error in dB. etypes: dc, white.

encoder side (perturb clean decoded params, resynthesize)
  wo       Wo relative error. etypes: white (per frame, sigma=level),
           dc (constant bias), framemod (+/-level alternating frames).
           L is recomputed from the perturbed Wo (floor(pi/Wo')).
  lsphz    LSP frequency error in Hz. etypes: white (per frame+coeff,
           sigma=level Hz), dc (all LSPs shifted +level Hz), worst
           (pairs pushed together by level Hz, clamped at midpoint).
  edb      frame energy error in dB, applied per 40 ms parameter block
           (1300's E is quantised once per 40 ms). etypes: white (per
           block, sigma=level dB — quantiser-noise pumping), dc.

Determinism: injection noise uses numpy default_rng(seed) — independent of
the excitation LFSR, which stays fixed across all runs so metric deltas are
attributable to the injection alone.  Output is saturated to +-32767
(int16), as the MCU would.
"""
import os
import sys

import numpy as np
from scipy.signal import lfilter, butter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tube-ladder"))
import tube  # noqa: E402  (Lfsr, lpc_harmonic_mags, filter_power_gain)

FS = 8000
N = 80

# recommended P2 configuration (tube-ladder REPORT.md verdict #3)
CFG = dict(fc=2500.0, pf_g1=0.50, pf_g2=0.80, pf_tilt=0.5)


# ---------------------------------------------------------------------------
# LSP <-> LPC (G8 data path: coefficients are cos(LSP))
# ---------------------------------------------------------------------------

def cos_to_ak(c):
    """Rebuild A(z) from the 10 cos(LSP) values (codec2 lsp_to_lpc convention:
    even 0-based indices carry the (1+z^-1) factor; validated vs dumps)."""
    p = np.array([1.0])
    q = np.array([1.0])
    for i in range(0, 10, 2):
        p = np.convolve(p, [1.0, -2.0 * c[i], 1.0])
    for i in range(1, 10, 2):
        q = np.convolve(q, [1.0, -2.0 * c[i], 1.0])
    P = np.convolve(p, [1.0, 1.0])
    Q = np.convolve(q, [1.0, -1.0])
    return 0.5 * (P + Q)[:11]


def _pair_narrow(v, lvl, descending):
    """Push each adjacent pair (0,1),(2,3),... together by lvl, clamping at
    the midpoint (eps-separated).  v may be cos values (descending) or LSP
    freqs (ascending)."""
    v = v.copy()
    eps = 1e-4
    for i in range(0, 10, 2):
        hi, lo = (i, i + 1) if descending else (i + 1, i)
        # v[hi] > v[lo]; move together
        a, b = v[hi] - lvl, v[lo] + lvl
        if a - b < eps:
            m = 0.5 * (v[hi] + v[lo])
            a, b = m + eps / 2, m - eps / 2
        v[hi], v[lo] = a, b
    return v


# ---------------------------------------------------------------------------
# parameter-domain perturbations (everything except in-loop hooks)
# ---------------------------------------------------------------------------

def perturb_params(params, inj, rng):
    """Return (lsp (F,10), Wo (F,), L (F,), gain_mult (F,), fc_arr (F,),
    nf_mult (F,)) with the injection applied where it lives in the parameter
    domain.  In-loop points (state, pulsepos) are handled by synth()."""
    lsp = params["lsp"].copy()
    Wo = params["Wo"].copy()
    L = params["L"].astype(int).copy()
    F = len(Wo)
    gain_mult = np.ones(F)
    fc_arr = np.full(F, CFG["fc"])
    nf_mult = np.ones(F)
    if inj is None:
        return lsp, Wo, L, gain_mult, fc_arr, nf_mult

    pt, et, lvl = inj["point"], inj["etype"], inj["level"]
    even = (np.arange(F) % 2 == 0)

    if pt == "coslsp":
        c = np.cos(lsp)
        if et == "white":
            c = c + rng.normal(0.0, lvl, c.shape)
        elif et == "framemod":
            c = c + rng.normal(0.0, 2 * lvl, c.shape) * even[:, None]
        elif et == "dc":
            c = c + lvl
        elif et == "worst":
            c = np.array([_pair_narrow(ci, lvl, descending=True) for ci in c])
        c = np.clip(c, -0.99995, 0.99995)
        lsp = np.arccos(c)
    elif pt == "lsphz":
        d = lvl * 2 * np.pi / FS          # Hz -> rad/sample
        if et == "white":
            lsp = lsp + rng.normal(0.0, d, lsp.shape)
        elif et == "dc":
            lsp = lsp + d
        elif et == "worst":
            lsp = np.array([_pair_narrow(wi, d, descending=False)
                            for wi in lsp])
        lsp = np.clip(lsp, 1e-3, np.pi - 1e-3)
    elif pt == "wo":
        if et == "white":
            Wo = Wo * (1.0 + rng.normal(0.0, lvl, F))
        elif et == "dc":
            Wo = Wo * (1.0 + lvl)
        elif et == "framemod":
            Wo = Wo * (1.0 + lvl * np.where(even, 1.0, -1.0))
        Wo = np.clip(Wo, 2 * np.pi / 160, np.pi / 2)
        L = np.minimum(np.floor(np.pi / Wo).astype(int), 160)
    elif pt == "log2e":
        if et == "white":
            e = rng.normal(0.0, lvl, F)
        elif et == "framemod":
            e = lvl * np.where(even, 1.0, -1.0)
        elif et == "dc":
            e = np.full(F, lvl)
        gain_mult = 2.0 ** e
    elif pt == "edb":
        nblk = (F + 3) // 4
        if et == "white":
            eb = rng.normal(0.0, lvl, nblk)
        elif et == "dc":
            eb = np.full(nblk, lvl)
        e = np.repeat(eb, 4)[:F]
        gain_mult = 10.0 ** (e / 20.0)
    elif pt == "mixfc":
        if et == "dc":
            fc_arr = fc_arr * (1.0 + lvl)
        elif et == "white":
            fc_arr = fc_arr * (1.0 + rng.normal(0.0, lvl, F))
        fc_arr = np.clip(fc_arr, 200.0, 3600.0)
    elif pt == "mixnf":
        if et == "dc":
            nf_mult = np.full(F, 10.0 ** (lvl / 20.0))
        elif et == "white":
            nf_mult = 10.0 ** (rng.normal(0.0, lvl, F) / 20.0)
    elif pt in ("state", "pulsepos"):
        pass                              # in-loop
    else:
        raise ValueError(f"unknown injection point {pt}")
    return lsp, Wo, L, gain_mult, fc_arr, nf_mult


# ---------------------------------------------------------------------------
# the injectable synthesizer (fork of tube.synth_ladder, rungs L0+L2+L4)
# ---------------------------------------------------------------------------

def synth(params, inj=None, seed=0xACE1):
    """Synthesize the utterance through the L0+L2+L4 tube with injection
    `inj` = dict(point, etype, level, seed) or None (clean).  Returns float
    signal on the int16 scale, saturated to +-32767."""
    rng = np.random.default_rng(inj["seed"] if inj else 0)
    lsp_all, Wo_all, L_all, gain_mult, fc_arr, nf_mult = \
        perturb_params(params, inj, rng)
    voiced = params["voiced"].astype(int)
    A_all = params["A"]
    F = len(Wo_all)

    pt = inj["point"] if inj else None
    et = inj["etype"] if inj else None
    lvl = inj["level"] if inj else 0.0

    # state-noise schedule (int16 LSB at output => /2 in tube units, since
    # the final signal is 2 * filter output)
    state_q = np.zeros(F)
    state_dc = 0.0
    if pt == "state":
        if et == "white":
            state_q[:] = lvl
        elif et == "framemod":
            state_q[np.arange(F) % 2 == 0] = 2 * lvl
        elif et == "dc":
            state_dc = 0.5 * lvl
    pos_white = lvl if (pt == "pulsepos" and et == "white") else 0.0
    pos_grid = lvl if (pt == "pulsepos" and et in ("qround", "qtrunc")) else 0.0
    pos_trunc = (et == "qtrunc")

    # crossover filters: cache designs per rounded fc (white mixfc redesigns)
    filt_cache = {}

    def xover(fc):
        key = round(fc, 1)
        if key not in filt_cache:
            b_lp, a_lp = butter(2, fc / (FS / 2), "low")
            b_hp, a_hp = butter(2, fc / (FS / 2), "high")
            filt_cache[key] = (b_lp, a_lp, b_hp, a_hp)
        return filt_cache[key]

    z_lp = np.zeros(2)
    z_hp = np.zeros(2)
    lfsr = tube.Lfsr(seed)
    iir_state = np.zeros(10)
    tail = np.zeros(2)                    # 2-tap frac-delay overrun (no disp)
    tau = 0.0
    out = []

    g1p = CFG["pf_g1"] ** np.arange(11)
    g2p = CFG["pf_g2"] ** np.arange(11)
    zi_num = np.zeros(10)
    zi_den = np.zeros(10)
    z_tilt = np.zeros(1)

    for i in range(F):
        Wo, L, v = Wo_all[i], L_all[i], voiced[i]
        a = cos_to_ak(np.cos(lsp_all[i]))
        A = A_all[i, :min(L, A_all.shape[1])]
        P = 2 * np.pi / Wo

        exc = np.zeros(N + 2)
        exc[:2] += tail

        if v:
            M = tube.lpc_harmonic_mags(a, Wo, L)
            s = np.sqrt(np.sum(A ** 2) / max(np.sum(M ** 2), 1e-18))
            g = (P / 2.0) * s * gain_mult[i]

            while tau < N:
                t_emit = tau
                if pos_white > 0.0:
                    t_emit = tau + rng.uniform(-pos_white / 2, pos_white / 2)
                    t_emit = min(max(t_emit, 0.0), N - 1e-6)
                n0 = int(np.floor(t_emit))
                frac = t_emit - n0
                exc[n0] += g * frac
                exc[n0 + 1] += g * (1.0 - frac)
                step = P
                if pos_grid > 0.0:
                    # quantised period accumulator: the *step* loses
                    # fractional resolution below the grid
                    step = (np.floor(step / pos_grid) if pos_trunc
                            else np.round(step / pos_grid)) * pos_grid
                tau += max(step, 2.0)
            tau -= N

            pulse = exc[:N] - g / P       # zero-mean excitation

            noise = lfsr.block(N) * (np.sqrt(3.0) * g / np.sqrt(P)) \
                * nf_mult[i]
            b_lp, a_lp, b_hp, a_hp = xover(fc_arr[i])
            pulse, z_lp = lfilter(b_lp, a_lp, pulse, zi=z_lp)
            hpn, z_hp = lfilter(b_hp, a_hp, noise, zi=z_hp)
            x = pulse + hpn
        else:
            pg = tube.filter_power_gain(a)
            target_pow = float(np.sum(A ** 2)) / 2.0
            sigma = np.sqrt(target_pow / max(pg, 1e-12)) * gain_mult[i]
            x = sigma * np.sqrt(3.0) * lfsr.block(N) + exc[:N]
            tau = max(tau - N, 0.0)

        tail = exc[N:].copy()

        # guard-bit truncation noise at the synthesis filter input
        if state_q[i] > 0.0:
            x = x + rng.uniform(-state_q[i] / 2, state_q[i] / 2, N) * 0.5
        if state_dc != 0.0:
            x = x + state_dc * 0.5

        y, iir_state = lfilter([1.0], a, x, zi=iir_state)
        # runaway guard: an unstable perturbed filter must not take the
        # whole run down with inf/nan — saturate the state like an MCU would
        if not np.all(np.isfinite(y)) or np.max(np.abs(y)) > 1e9:
            y = np.nan_to_num(y, nan=0.0, posinf=1e9, neginf=-1e9)
            y = np.clip(y, -1e9, 1e9)
            iir_state = np.clip(np.nan_to_num(iir_state), -1e9, 1e9)

        # L4 postfilter
        e_in = float(np.sum(y ** 2)) + 1e-12
        num = a * g1p
        den = a * g2p
        yp, zi_num = lfilter(num, den, y, zi=zi_num)
        h = lfilter(num, den, np.r_[1.0, np.zeros(21)])
        r0 = float(np.dot(h, h))
        r1 = float(np.dot(h[:-1], h[1:]))
        mu = CFG["pf_tilt"] * (r1 / r0 if r0 > 0 else 0.0)
        yp, z_tilt = lfilter([1.0, -mu], [1.0], yp, zi=z_tilt)
        e_out = float(np.sum(yp ** 2)) + 1e-12
        gagc = np.sqrt(e_in / e_out)
        y = yp * np.clip(gagc, 0.1, 10.0)

        out.append(y)

    y = 2.0 * np.concatenate(out)
    return np.clip(np.nan_to_num(y), -32767.0, 32767.0)


def load_params(npz_path):
    """Decoded q1300 params on the 10 ms grid (same keys run_ladder uses,
    plus the decoded LSP track that the G8 path consumes)."""
    z = dict(np.load(npz_path))
    return {
        "Wo": z.get("Wo_dec", z["Wo"]),
        "L": z.get("L_dec", z["L"]),
        "voiced": z["voiced"],
        "lsp": z["lsp_dec"],
        "A": z["A_lpc"],
        "snr_mbe": z.get("snr_mbe"),
    }
