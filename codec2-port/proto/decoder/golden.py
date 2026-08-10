#!/usr/bin/env python3
"""golden.py — bit-exact python twin of c2tube_dec.c (codec2-port §4 tier-2).

Every arithmetic operation mirrors the C implementation exactly:
  - int32/int64 helpers with the same saturation points (sat32 counts too);
  - arithmetic right shift == python >> (both floor);
  - C integer division truncates toward zero -> idiv() helper;
  - same LUTs from tables.py (generated together with c2tube_tables.h).

Divergence from the C output on any sample of any frame == bug (assert in
validate.py).  Pure python ints in the signal path; numpy only for I/O.
"""
import sys
import numpy as np

import tables as T

N = 80
GUARD = 8
INT32_MAX = 2**31 - 1
INT32_MIN = -2**31

sat_count = 0


def sat32(v):
    global sat_count
    if v > INT32_MAX:
        sat_count += 1
        return INT32_MAX
    if v < INT32_MIN:
        sat_count += 1
        return INT32_MIN
    return v


def sat16(v):
    return 32767 if v > 32767 else (-32768 if v < -32768 else v)


def asr64(x, s):
    return x >> s if s >= 0 else x << (-s)


def idiv(a, b):
    """C truncating integer division."""
    q = abs(a) // abs(b)
    return q if (a < 0) == (b < 0) else -q


def floor_log2(v):
    return v.bit_length() - 1


def log2_q8(v):
    k = floor_log2(v)
    m = v >> (k - 11) if k >= 11 else v << (11 - k)
    idx = (m >> 6) - 32
    r = m & 63
    l = T.LOG2_Q8[idx] + (((T.LOG2_Q8[idx + 1] - T.LOG2_Q8[idx]) * r) >> 6)
    return (k << 8) + l


def exp2_shift(lg_q8, extra_shift):
    n = lg_q8 >> 8
    f = lg_q8 & 255
    idx, r = f >> 3, f & 7
    m = T.EXP2_Q14[idx] + (((T.EXP2_Q14[idx + 1] - T.EXP2_Q14[idx]) * r) >> 3)
    sh = n - 14 + extra_shift
    if sh < -40:
        return 0
    return sat32(asr64(m, -sh))


# ---------------- bitstream ----------------

LSP_BITS = T.LSP_BITS


def unpack_gray(bits, state, width):
    field = 0
    for i in range(width):
        bi = state[0] + i
        field = (field << 1) | ((bits[bi >> 3] >> (7 - (bi & 7))) & 1)
    state[0] += width
    field ^= field >> 4
    field ^= field >> 2
    field ^= field >> 1
    return field


# ---------------- CSD ----------------

C_CLAMP = 16368
ORDER_SEP = 32


def csd3_q14(c):
    r = max(-C_CLAMP, min(C_CLAMP, c))
    val = 0
    for _ in range(3):
        av = -r if r < 0 else r
        sgn = -1 if r < 0 else 1
        if av < 8:
            break
        k0 = floor_log2(av)
        e = k0 + 1 if av * av >= (1 << (2 * k0 + 1)) else k0
        e = max(4, min(14, e))
        c1 = 1 << e
        c2 = 1 << min(e + 1, 14)
        d1 = abs(av - c1)
        d2 = abs(av - c2)
        term = c1 if d1 <= d2 else c2
        val += sgn * term
        r -= sgn * term
    return max(-C_CLAMP, min(C_CLAMP, val))


def order_fix(cp, cq):
    seq = []
    for i in range(5):
        seq.append(("p", i))
        seq.append(("q", i))
    arr = {"p": cp, "q": cq}
    for j in range(1, 10):
        wj, ij = seq[j]
        wp, ip = seq[j - 1]
        if arr[wj][ij] >= arr[wp][ip] - ORDER_SEP // 2:
            arr[wj][ij] = arr[wp][ip] - ORDER_SEP


def naf_terms(v):
    u = v >> 4
    pos, terms = 4, []
    neg = u < 0
    if neg:
        u = -u
    while u != 0:
        if u & 1:
            dd = 2 - (u & 3)
            terms.append((13 - pos, -dd if neg else dd))
            u -= dd
        u >>= 1
        pos += 1
    return terms


def mul2c(terms, s):
    acc = 0
    for sh, sg in terms:
        t = asr64(s, sh)
        acc += t if sg > 0 else -t
    return acc


# ---------------- decoder state ----------------

class Dec:
    def __init__(self):
        init_lsp = [364, 727, 1091, 1455, 1818, 2182, 2545, 2909, 3273, 3636]
        self.prev_lsp_q2 = [f << 2 for f in init_lsp]
        self.prev_wo_num_q2 = 128 << 2
        self.prev_lg2e_q8 = 0
        self.prev_voiced = 0
        self.tau_q7 = 0
        self.lfsr = 0xACE1
        self.exc_tail = [0, 0, 0, 0]
        self.s1p = [0] * 5
        self.s2p = [0] * 5
        self.s1q = [0] * 5
        self.s2q = [0] * 5
        self.sp_last = 0
        self.sq_last = 0
        self.zlp = [0, 0]
        self.zhp = [0, 0]
        self.ynum_hist = [0] * 10
        self.yden_hist = [0] * 10
        self.tilt_state = 0


def lfsr_step(d):
    v = d.lfsr
    lsb = v & 1
    v >>= 1
    if lsb:
        v ^= 0xB400
    d.lfsr = v
    return v - 32768


def biquad(x, b, a, z):
    y = sat32(((b[0] * x) >> 14) + z[0])
    z[0] = sat32(((b[1] * x) >> 14) - ((a[1] * y) >> 14) + z[1])
    z[1] = sat32(((b[2] * x) >> 14) - ((a[2] * y) >> 14))
    return y


def g8_step(d, ctp, ctq, x):
    p = 0
    q = 0
    inp = [0] * 6
    inq = [0] * 6
    for k in range(5):
        inp[k] = sat32(p)
        p = p - mul2c(ctp[k], d.s1p[k]) + d.s2p[k]
    inp[5] = sat32(p)
    p = p + d.sp_last
    for k in range(5):
        inq[k] = sat32(q)
        q = q - mul2c(ctq[k], d.s1q[k]) + d.s2q[k]
    inq[5] = sat32(q)
    q = q - d.sq_last
    y = sat32(x - ((p + q) >> 1))
    for k in range(5):
        d.s2p[k] = d.s1p[k]
        d.s1p[k] = sat32(inp[k] + y)
        d.s2q[k] = d.s1q[k]
        d.s1q[k] = sat32(inq[k] + y)
    d.sp_last = sat32(inp[5] + y)
    d.sq_last = sat32(inq[5] + y)
    return y


def decode_frame(d, bits, trace=None):
    state = [0]
    v = [unpack_gray(bits, state, 1) for _ in range(4)]
    wo_idx = unpack_gray(bits, state, 7)
    e_idx = unpack_gray(bits, state, 5)
    lsp_idx = [unpack_gray(bits, state, LSP_BITS[i]) for i in range(10)]

    lsp_hz = [T.LSP_CB[i][lsp_idx[i]] for i in range(10)]

    # check_lsp_order, mirrors the C control flow (for-loop with i=1 reset)
    i = 1
    while i < 10:
        if lsp_hz[i] < lsp_hz[i - 1]:
            tmp = lsp_hz[i - 1]
            lsp_hz[i - 1] = lsp_hz[i] - 127
            lsp_hz[i] = tmp + 127
            i = 1  # C: i=1 then i++ -> 2
        i += 1
    for i in range(1, 4):
        if lsp_hz[i] - lsp_hz[i - 1] < 50:
            lsp_hz[i] = lsp_hz[i - 1] + 50
    for i in range(4, 10):
        if lsp_hz[i] - lsp_hz[i - 1] < 100:
            lsp_hz[i] = lsp_hz[i - 1] + 100

    cur_lsp_q2 = [f << 2 for f in lsp_hz]
    cur_wo_num_q2 = (128 + 7 * wo_idx) << 2
    cur_lg2e_q8 = T.LG2E_Q8[e_idx]

    speech = []
    for isub in range(4):
        w = isub + 1
        voiced = v[isub]
        f_q2 = [(d.prev_lsp_q2[i] * (4 - w) + cur_lsp_q2[i] * w) >> 2
                for i in range(10)]
        lg2e_sub = (d.prev_lg2e_q8 * (4 - w) + cur_lg2e_q8 * w) >> 2

        if isub == 3:
            wo_num = cur_wo_num_q2
        else:
            if voiced and not d.prev_voiced and not v[3]:
                voiced = 0
            if voiced:
                if d.prev_voiced and v[3]:
                    wo_num = (d.prev_wo_num_q2 * (4 - w) +
                              cur_wo_num_q2 * w) >> 2
                elif not d.prev_voiced and v[3]:
                    wo_num = cur_wo_num_q2
                else:
                    wo_num = d.prev_wo_num_q2
            else:
                wo_num = 128 << 2
        P_q7 = 0
        lg2P = 0
        if voiced:
            P_q7 = idiv(10485760, wo_num)
            lg2P = log2_q8(P_q7) - (7 << 8)

        # cos LUT
        c_q14 = []
        for i in range(10):
            f = max(4, min(15996, f_q2[i]))
            idx, r = f >> 6, f & 63
            c_q14.append(T.COS_Q14[idx] +
                         (((T.COS_Q14[idx + 1] - T.COS_Q14[idx]) * r) >> 6))
        cp = [csd3_q14(c_q14[2 * i]) for i in range(5)]
        cq = [csd3_q14(c_q14[2 * i + 1]) for i in range(5)]
        order_fix(cp, cq)
        ctp = [naf_terms(x) for x in cp]
        ctq = [naf_terms(x) for x in cq]

        # rebuild A(z) in Q14
        pP = [0] * 12
        pQ = [0] * 12
        pP[0] = 16384
        pP[1] = 16384
        pQ[0] = 16384
        pQ[1] = -16384
        for k in range(5):
            deg = 1 + 2 * k
            for i in range(deg + 2, -1, -1):
                t = (pP[i] if i <= deg else 0) + (pP[i - 2] if i >= 2 else 0)
                m = ((pP[i - 1] * (-2 * cp[k])) >> 14) \
                    if (1 <= i and i - 1 <= deg) else 0
                pP[i] = t + m
            for i in range(deg + 2, -1, -1):
                t = (pQ[i] if i <= deg else 0) + (pQ[i - 2] if i >= 2 else 0)
                m = ((pQ[i - 1] * (-2 * cq[k])) >> 14) \
                    if (1 <= i and i - 1 <= deg) else 0
                pQ[i] = t + m
        a_q12 = [sat32(((pP[i] + pQ[i]) >> 1) >> 2) for i in range(11)]

        num_q12 = [sat32((a_q12[i] * T.G1POW_Q14[i]) >> 14) for i in range(11)]
        den_q12 = [sat32((a_q12[i] * T.G2POW_Q14[i]) >> 14) for i in range(11)]

        # tilt mu
        h = [0] * 22
        for j in range(22):
            acc = (num_q12[0] << 12) if j == 0 else 0
            for k in range(1, 11):
                if j - k < 0:
                    break
                if j == k:
                    acc += num_q12[k] << 12
                acc -= den_q12[k] * h[j - k]
            h[j] = acc >> 12
        r0 = sum(x * x for x in h)
        r1 = sum(h[j] * h[j + 1] for j in range(21))
        mu_q15 = idiv(r1 << 14, r0) if r0 > 0 else 0

        # excitation
        exc = [0] * (N + 4)
        for n in range(4):
            exc[n] = d.exc_tail[n]
        ybuf = [0] * N
        e_in = 0

        if voiced:
            lg2h = (lg2e_sub + lg2P + (9 << 8)) >> 1
            h_q = exp2_shift(lg2h, GUARD)
            dc_q = idiv(h_q << 7, P_q7)
            tau = d.tau_q7
            while tau < (N << 7):
                n0 = tau >> 7
                frac = tau & 127
                exc[n0] = sat32(exc[n0] + ((h_q * frac) >> 7))
                exc[n0 + 1] = sat32(exc[n0 + 1] + ((h_q * (128 - frac)) >> 7))
                tau += P_q7 if P_q7 > 256 else 256
            tau -= N << 7
            d.tau_q7 = tau
            s_n = exp2_shift(lg2h - (lg2P >> 1) + 203, GUARD)
            if trace is not None:
                trace.append(("V", lg2e_sub, P_q7, h_q, s_n))
            for n in range(N):
                pulse = sat32(exc[n] - dc_q)
                nq = lfsr_step(d)
                nn = sat32((nq * s_n) >> 15)
                lp = biquad(pulse, T.B_LP_Q14, T.A_LP_Q14, d.zlp)
                hp = biquad(nn, T.B_HP_Q14, T.A_HP_Q14, d.zhp)
                x = sat32(lp + hp)
                y = g8_step(d, ctp, ctq, x)
                e_in += y * y
                ybuf[n] = y
        else:
            s_uv = exp2_shift((lg2e_sub + 2710) >> 1, GUARD)
            if trace is not None:
                trace.append(("U", lg2e_sub, 0, s_uv, 0))
            for n in range(N):
                nq = lfsr_step(d)
                x = sat32(((nq * s_uv) >> 15) + exc[n])
                y = g8_step(d, ctp, ctq, x)
                e_in += y * y
                ybuf[n] = y
            d.tau_q7 = d.tau_q7 - (N << 7) if d.tau_q7 > (N << 7) else 0

        for n in range(4):
            d.exc_tail[n] = exc[N + n]

        # postfilter
        e_out = 0
        for n in range(N):
            acc = num_q12[0] * ybuf[n]
            for k in range(1, 11):
                acc += num_q12[k] * d.ynum_hist[k - 1]
            for k in range(1, 11):
                acc -= den_q12[k] * d.yden_hist[k - 1]
            yp = sat32(acc >> 12)
            for k in range(9, 0, -1):
                d.ynum_hist[k] = d.ynum_hist[k - 1]
                d.yden_hist[k] = d.yden_hist[k - 1]
            d.ynum_hist[0] = ybuf[n]
            d.yden_hist[0] = yp
            yt = sat32(yp - ((mu_q15 * d.tilt_state) >> 15))
            d.tilt_state = yp
            e_out += yt * yt
            ybuf[n] = yt

        if e_in > 0 and e_out > 0:
            dlg = (log2_q8(e_in) - log2_q8(e_out)) >> 1
            g_q14 = exp2_shift(dlg, 14)  # Q14 gain
            g_q14 = max(1638, min(163840, g_q14))
        else:
            g_q14 = 16384

        for n in range(N):
            t = (ybuf[n] * g_q14) >> 14
            t = (t + (1 << (GUARD - 1))) >> GUARD
            speech.append(sat16(sat32(t)))

    d.prev_lsp_q2 = cur_lsp_q2
    d.prev_wo_num_q2 = cur_wo_num_q2
    d.prev_lg2e_q8 = cur_lg2e_q8
    d.prev_voiced = v[3]
    return speech


def decode_file(c2_path, raw_path, trace=None):
    data = open(c2_path, "rb").read()
    if data[:3] == b"\xc0\xde\xc2":
        data = data[7:]
    d = Dec()
    out = []
    nfr = len(data) // 7
    for f in range(nfr):
        out.extend(decode_frame(d, data[f * 7:(f + 1) * 7], trace))
    np.asarray(out, dtype=np.int16).tofile(raw_path)
    return nfr


if __name__ == "__main__":
    nfr = decode_file(sys.argv[1], sys.argv[2])
    print(f"golden: {nfr} frames, {sat_count} saturations", file=sys.stderr)
