#!/usr/bin/env python3
"""float_ref.py — float golden model of the SAME architecture as c2tube_dec.c.

This is the "equivalent float rung" of experiments/tube-ladder for the
L0+L2+L4 subset actually implemented by the C prototype (no L1 dispersion,
no L3 jitter — per simplification-map P2 recommendation), driven by the SAME
1300 bitstream instead of c2sim dumps.  The synthesis structure follows
tube.py synth_ladder (experiments/tube-ladder/tube.py) with these deliberate
prototype-architecture substitutions, shared by the fixed-point twin:

  - filter coefficients come from the bitstream LSPs (cos domain), not from
    dumped ak[]; optional CSD-3 quantisation (redteam G8 recipe) to isolate
    the coefficient-quantisation rung;
  - frame gain from the decoded energy E instead of dumped amplitudes:
    voiced pulse height h = sqrt(512*E*P), unvoiced noise rms = sqrt(512*E)
    (derived from aks_to_M2's Am^2 = E*(FFT/2pi)*Wo*|H|^2 convention,
    quantise.c:391-467, folded with tube.py's x2 output scale);
  - pulse timing on the SAME Q7 grid as the fixed decoder (tau/P in 1/128
    sample units) so pulse trains align sample-exactly between float and
    fixed — the comparison then measures arithmetic quantisation, not
    accumulated timing drift;
  - same LFSR (16-bit Galois, taps 0xB400, seed 0xACE1), same draw order.

Float is used ONLY here (host-side reference); the C decoder stays integer.
Modes:
  csd=True,  lut_cos=False  -> "twinA": architecture reference (exact cos,
                               CSD-3 coefficient values, float states)
  csd=True,  lut_cos=True   -> "twinL": same but cos via the fixed decoder's
                               LUT (isolates state-arithmetic penalty)
  csd=False, lut_cos=False  -> "twinB": pure float ladder (no coefficient
                               quantisation) — the quality anchor
"""
import math
import sys

import numpy as np

import tables as T
from golden import (idiv, unpack_gray, order_fix as int_order_fix)  # noqa

LSP_BITS = T.LSP_BITS
N = 80

C_CLAMP = 1.0 - 2.0 ** -10
ORDER_SEP = 2.0 ** -9


def csd3(x):
    """redteam bench_r1/engines.py csd_quantize(x, 3, -10, 0)."""
    r = float(x)
    r = max(-C_CLAMP, min(C_CLAMP, r))
    val = 0.0
    for _ in range(3):
        if abs(r) < 2.0 ** -11:
            break
        e = int(np.clip(np.round(np.log2(abs(r))), -10, 0))
        c1 = math.copysign(2.0 ** e, r)
        c2 = math.copysign(2.0 ** min(e + 1, 0), r)
        term = c1 if abs(r - c1) <= abs(r - c2) else c2
        val += term
        r -= term
    return max(-C_CLAMP, min(C_CLAMP, val))


def order_fix_f(cp, cq):
    seq = [("p", i // 2) if i % 2 == 0 else ("q", i // 2) for i in range(10)]
    arr = {"p": cp, "q": cq}
    for j in range(1, 10):
        wj, ij = seq[j]
        wp, ip = seq[j - 1]
        if arr[wj][ij] >= arr[wp][ip] - ORDER_SEP / 2:
            arr[wj][ij] = arr[wp][ip] - ORDER_SEP


def lsp_cos_to_a(cp, cq):
    """cos(LSP) -> A(z) = (P+Q)/2 (redteam engines_rt.py lsp_cos_to_a)."""
    P = np.array([1.0, 1.0])
    for c in cp:
        P = np.convolve(P, [1.0, -2.0 * c, 1.0])
    Q = np.array([1.0, -1.0])
    for c in cq:
        Q = np.convolve(Q, [1.0, -2.0 * c, 1.0])
    A = 0.5 * (P + Q)
    return A[:-1]


def lut_cos_q14(f_q2):
    f = max(4, min(15996, f_q2))
    idx, r = f >> 6, f & 63
    return (T.COS_Q14[idx] +
            (((T.COS_Q14[idx + 1] - T.COS_Q14[idx]) * r) >> 6)) / 16384.0


class FloatDec:
    def __init__(self, csd=True, lut_cos=False):
        self.csd = csd
        self.lut_cos = lut_cos
        init_lsp = [364, 727, 1091, 1455, 1818, 2182, 2545, 2909, 3273, 3636]
        self.prev_lsp = [float(x) for x in init_lsp]
        self.prev_wo_num_q2 = 128 << 2  # EXACT int, as the fixed decoder
        self.prev_lg2e = 0.0
        self.prev_voiced = 0
        self.tau_q7 = 0
        self.lfsr = 0xACE1
        self.exc_tail = np.zeros(4)
        # G8 two-allpass structure state (same topology as the C decoder --
        # state COORDINATES matter: coefficient switches at subframe rate
        # produce different transients in different realisations)
        self.s1p = np.zeros(5)
        self.s2p = np.zeros(5)
        self.s1q = np.zeros(5)
        self.s2q = np.zeros(5)
        self.spl = 0.0
        self.sql = 0.0
        self.zlp = np.zeros(2)
        self.zhp = np.zeros(2)
        self.ynum_hist = np.zeros(10)
        self.yden_hist = np.zeros(10)
        self.tilt_state = 0.0
        # Q14-quantised crossover biquads == the C tables
        self.blp = np.array(T.B_LP_Q14) / 16384.0
        self.alp = np.array(T.A_LP_Q14) / 16384.0
        self.bhp = np.array(T.B_HP_Q14) / 16384.0
        self.ahp = np.array(T.A_HP_Q14) / 16384.0

    def lfsr_block(self, n):
        out = np.empty(n)
        s = self.lfsr
        for i in range(n):
            lsb = s & 1
            s >>= 1
            if lsb:
                s ^= 0xB400
            out[i] = (s - 32768) / 32768.0
        self.lfsr = s
        return out

    def decode_frame(self, bits):
        from scipy.signal import lfilter
        state = [0]
        v = [unpack_gray(bits, state, 1) for _ in range(4)]
        wo_idx = unpack_gray(bits, state, 7)
        e_idx = unpack_gray(bits, state, 5)
        lsp_idx = [unpack_gray(bits, state, LSP_BITS[i]) for i in range(10)]

        lsp = [float(T.LSP_CB[i][lsp_idx[i]]) for i in range(10)]
        i = 1
        while i < 10:
            if lsp[i] < lsp[i - 1]:
                tmp = lsp[i - 1]
                lsp[i - 1] = lsp[i] - 127.0
                lsp[i] = tmp + 127.0
                i = 1
            i += 1
        for i in range(1, 4):
            if lsp[i] - lsp[i - 1] < 50.0:
                lsp[i] = lsp[i - 1] + 50.0
        for i in range(4, 10):
            if lsp[i] - lsp[i - 1] < 100.0:
                lsp[i] = lsp[i - 1] + 100.0

        cur_lsp = lsp
        cur_wo_num_q2 = (128 + 7 * wo_idx) << 2
        cur_lg2e = (-10.0 + (50.0 / 32) * e_idx) * math.log2(10) / 10.0

        out = []
        for isub in range(4):
            w = (isub + 1) / 4.0
            voiced = v[isub]
            f_hz = [(1 - w) * self.prev_lsp[i] + w * cur_lsp[i]
                    for i in range(10)]
            lg2e = (1 - w) * self.prev_lg2e + w * cur_lg2e
            E = 2.0 ** lg2e

            wi = isub + 1
            if isub == 3:
                wo_num = cur_wo_num_q2
            else:
                if voiced and not self.prev_voiced and not v[3]:
                    voiced = 0
                if voiced:
                    if self.prev_voiced and v[3]:
                        wo_num = (self.prev_wo_num_q2 * (4 - wi) +
                                  cur_wo_num_q2 * wi) >> 2
                    elif not self.prev_voiced and v[3]:
                        wo_num = cur_wo_num_q2
                    else:
                        wo_num = self.prev_wo_num_q2
                else:
                    wo_num = 128 << 2
            # same Q7 period grid as the fixed decoder (pulse-time exact)
            P_q7 = idiv(10485760, wo_num)
            P = P_q7 / 128.0

            if self.lut_cos:
                c = [lut_cos_q14(int(round(f * 4))) for f in f_hz]
            else:
                c = [math.cos(math.pi * f / 4000.0) for f in f_hz]
            cp = [c[0], c[2], c[4], c[6], c[8]]
            cq = [c[1], c[3], c[5], c[7], c[9]]
            if self.csd:
                cp = [csd3(x) for x in cp]
                cq = [csd3(x) for x in cq]
                order_fix_f(cp, cq)
            a = lsp_cos_to_a(cp, cq)

            num = a * (0.65 ** np.arange(11))
            den = a * (0.80 ** np.arange(11))
            h_imp = lfilter(num, den, np.r_[1.0, np.zeros(21)])
            r0 = float(np.dot(h_imp, h_imp))
            r1 = float(np.dot(h_imp[:-1], h_imp[1:]))
            mu = 0.5 * (r1 / r0 if r0 > 0 else 0.0)

            exc = np.zeros(N + 4)
            exc[:4] = self.exc_tail

            if voiced:
                h = math.sqrt(512.0 * E * P)
                tau = self.tau_q7
                while tau < (N << 7):
                    n0 = tau >> 7
                    frac = (tau & 127) / 128.0
                    exc[n0] += h * frac
                    exc[n0 + 1] += h * (1.0 - frac)
                    tau += P_q7 if P_q7 > 256 else 256
                self.tau_q7 = tau - (N << 7)
                pulse = exc[:N] - h / P
                noise = self.lfsr_block(N) * (math.sqrt(3.0) * h / math.sqrt(P))
                pulse, self.zlp = lfilter(self.blp, self.alp, pulse,
                                          zi=self.zlp)
                hpn, self.zhp = lfilter(self.bhp, self.ahp, noise, zi=self.zhp)
                x = pulse + hpn
            else:
                x = (self.lfsr_block(N) * math.sqrt(3.0 * 512.0 * E)
                     + exc[:N])
                self.tau_q7 = max(self.tau_q7 - (N << 7), 0)

            self.exc_tail = exc[N:].copy()

            y = np.empty(N)
            for n in range(N):
                p = 0.0
                inp = [0.0] * 6
                for k in range(5):
                    inp[k] = p
                    p = p - 2.0 * cp[k] * self.s1p[k] + self.s2p[k]
                inp[5] = p
                dP = p + self.spl
                qv = 0.0
                inq = [0.0] * 6
                for k in range(5):
                    inq[k] = qv
                    qv = qv - 2.0 * cq[k] * self.s1q[k] + self.s2q[k]
                inq[5] = qv
                dQ = qv - self.sql
                yn = x[n] - 0.5 * (dP + dQ)
                y[n] = yn
                for k in range(5):
                    self.s2p[k] = self.s1p[k]
                    self.s1p[k] = inp[k] + yn
                    self.s2q[k] = self.s1q[k]
                    self.s1q[k] = inq[k] + yn
                self.spl = inp[5] + yn
                self.sql = inq[5] + yn
            e_in = float(np.sum(y * y))

            # L4: folded postfilter with persistent DF1 histories (same
            # topology as the fixed decoder)
            yp = np.empty(N)
            for n in range(N):
                acc = num[0] * y[n]
                acc += float(np.dot(num[1:], self.ynum_hist))
                acc -= float(np.dot(den[1:], self.yden_hist))
                self.ynum_hist[1:] = self.ynum_hist[:-1]
                self.yden_hist[1:] = self.yden_hist[:-1]
                self.ynum_hist[0] = y[n]
                self.yden_hist[0] = acc
                yt = acc - mu * self.tilt_state
                self.tilt_state = acc
                yp[n] = yt
            e_out = float(np.sum(yp * yp))
            g = math.sqrt(e_in / e_out) if (e_in > 0 and e_out > 0) else 1.0
            g = max(0.1, min(10.0, g))
            out.append(yp * g)

        self.prev_lsp = cur_lsp
        self.prev_wo_num_q2 = cur_wo_num_q2
        self.prev_lg2e = cur_lg2e
        self.prev_voiced = v[3]
        return np.concatenate(out)


def decode_file(c2_path, raw_path, csd=True, lut_cos=False):
    data = open(c2_path, "rb").read()
    if data[:3] == b"\xc0\xde\xc2":
        data = data[7:]
    d = FloatDec(csd=csd, lut_cos=lut_cos)
    out = []
    nfr = len(data) // 7
    for f in range(nfr):
        out.append(d.decode_frame(data[f * 7:(f + 1) * 7]))
    sig = np.concatenate(out)
    np.clip(np.round(sig), -32768, 32767).astype(np.int16).tofile(raw_path)
    return nfr


if __name__ == "__main__":
    mode = sys.argv[3] if len(sys.argv) > 3 else "twinA"
    kw = {"twinA": dict(csd=True, lut_cos=False),
          "twinL": dict(csd=True, lut_cos=True),
          "twinB": dict(csd=False, lut_cos=False)}[mode]
    nfr = decode_file(sys.argv[1], sys.argv[2], **kw)
    print(f"float_ref[{mode}]: {nfr} frames", file=sys.stderr)
