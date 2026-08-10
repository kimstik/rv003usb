/* c2tube_dec.c — fixed-point tube decoder for codec2 1300 (see c2tube_dec.h).
 *
 * Ground truth for parameter semantics (reimplemented, not copied), all in
 * the pinned codec2 checkout @310777b:
 *   - bit order / widths:  src/codec2.c codec2_encode_1300 1077-1124,
 *     codec2_decode_1300 1137-1237; Gray coding src/pack.c.
 *   - Wo:      quantise.c decode_Wo 581-593:  Wo = Wo_min + idx*(Wo_max -
 *              Wo_min)/128, Wo_min = 2pi/160, Wo_max = 2pi/20
 *              (sine.c c2const_create 60-72).  In units of 2pi/20480 this is
 *              the EXACT integer 128 + 7*idx.
 *   - energy:  quantise.c decode_energy 934-946: E_dB = -10 + idx*50/32;
 *              kept in log2 domain here (LG2E_Q8 table).
 *   - LSPs:    quantise.c decode_lsps_scalar 740-755 (int-Hz codebooks),
 *              check_lsp_order 266-283 (swap with +/-0.1 rad ~ 127 Hz),
 *              bw_expand_lsps 843-861 (min separation 50 Hz below LSP4,
 *              100 Hz above).
 *   - interpolation: codec2.c 1198-1205: subframes 0..2 get weight
 *              0.25/0.5/0.75 between the previous frame's and this frame's
 *              parameters; LSPs linear (interp.c interpolate_lsp_ver2
 *              302-309), energy log-linear (interp_energy2 288-291), Wo
 *              voicing-gated linear (interp_Wo2 237-258: unvoiced -> Wo_min,
 *              one-sided voicing -> copy the voiced side).
 *
 * Known deliberate deviations from the float reference (parameter domain,
 * each ~sub-Hz / sub-0.1 dB — see REPORT.md "honest gaps"):
 *   - check_lsp_order shift 127 Hz for 0.1 rad (127.32 Hz);
 *   - init prev_lsps at round(i*4000/11) Hz vs i*pi/11 rad (codec2.c:178);
 *   - interpolation truncates to quarter-Hz / Q8-log2 grids.
 */
#include "c2tube_dec.h"
#include "c2tube_tables.h"

uint32_t c2tube_sat_count = 0;

/* ------------------------------------------------------------------ *
 *  small integer helpers (golden.py mirrors these exactly)           *
 * ------------------------------------------------------------------ */

static int32_t sat32(int64_t v) {
  if (v > INT32_MAX) { c2tube_sat_count++; return INT32_MAX; }
  if (v < INT32_MIN) { c2tube_sat_count++; return INT32_MIN; }
  return (int32_t)v;
}

static int16_t sat16(int32_t v) {
  if (v > 32767) return 32767;
  if (v < -32768) return -32768;
  return (int16_t)v;
}

/* arithmetic shift right on int64; negative s shifts left */
static int64_t asr64(int64_t x, int s) {
  if (s >= 0) return x >> s;
  return x << (-s);
}

static int floor_log2_u64(uint64_t v) { /* v > 0 */
  int k = 0;
  while (v >= 2) { v >>= 1; k++; }
  return k;
}

/* log2(v) in Q8 for v > 0 (33-entry LUT + linear interpolation) */
static int32_t log2_q8(uint64_t v) {
  int k = floor_log2_u64(v);
  uint32_t m; /* mantissa in [2^11, 2^12) */
  if (k >= 11) m = (uint32_t)(v >> (k - 11));
  else m = (uint32_t)(v << (11 - k));
  {
    int idx = (int)(m >> 6) - 32; /* 0..31 */
    int r = (int)(m & 63);
    int32_t l = (int32_t)LOG2_Q8[idx] +
                (((int32_t)LOG2_Q8[idx + 1] - (int32_t)LOG2_Q8[idx]) * r >> 6);
    return ((int32_t)k << 8) + l;
  }
}

/* 2^(lg/256) * 2^extra_shift, via 33-entry Q14 LUT + lerp; saturating */
static int32_t exp2_shift(int32_t lg_q8, int extra_shift) {
  int32_t n = lg_q8 >> 8; /* floor */
  int32_t f = lg_q8 & 255;
  int idx = f >> 3, r = f & 7;
  int32_t m = (int32_t)EXP2_Q14[idx] +
              (((int32_t)EXP2_Q14[idx + 1] - (int32_t)EXP2_Q14[idx]) * r >> 3);
  int sh = n - 14 + extra_shift;
  if (sh < -40) return 0;
  return sat32(asr64((int64_t)m, -sh));
}

/* ------------------------------------------------------------------ *
 *  bitstream unpack (clean reimplementation of pack.c semantics)      *
 * ------------------------------------------------------------------ */

static uint32_t unpack_gray(const uint8_t *bits, uint32_t *nbit, int width) {
  uint32_t field = 0;
  int i;
  for (i = 0; i < width; i++) {
    uint32_t bi = *nbit + i;
    field = (field << 1) | ((bits[bi >> 3] >> (7 - (bi & 7))) & 1);
  }
  *nbit += width;
  /* Gray -> binary (fields are <= 8 bits wide) */
  field ^= field >> 4;
  field ^= field >> 2;
  field ^= field >> 1;
  return field;
}

/* ------------------------------------------------------------------ *
 *  CSD-3-term quantisation of cos(LSP), Q14                           *
 *  (mirrors synth-redteam bench_r1/engines.py csd_quantize 96-111 and *
 *   rt/engines_rt.py quantize_lsp_csd 662-689 in integer form)        *
 * ------------------------------------------------------------------ */

#define C_CLAMP_Q14 16368 /* 1 - 2^-10 */
#define ORDER_SEP_Q14 32  /* 2^-9      */

static int32_t csd3_q14(int32_t c) {
  int32_t r = c, val = 0;
  int t;
  if (r > C_CLAMP_Q14) r = C_CLAMP_Q14;
  if (r < -C_CLAMP_Q14) r = -C_CLAMP_Q14;
  c = r;
  for (t = 0; t < 3; t++) {
    int32_t av = r < 0 ? -r : r;
    int sgn = r < 0 ? -1 : 1;
    int k0, e;
    int32_t c1, c2, term;
    if (av < 8) break; /* |r| < 2^(exp_lo-1) */
    k0 = floor_log2_u64((uint64_t)av);
    /* geometric rounding: e = k0+1 iff av >= 2^(k0+0.5) <=> av^2 >= 2^(2k0+1) */
    e = ((int64_t)av * av >= ((int64_t)1 << (2 * k0 + 1))) ? k0 + 1 : k0;
    if (e < 4) e = 4;
    if (e > 14) e = 14;
    c1 = (int32_t)1 << e;
    c2 = (int32_t)1 << (e + 1 > 14 ? 14 : e + 1);
    { /* linear nearest of the two candidates, tie -> c1 */
      int32_t d1 = av > c1 ? av - c1 : c1 - av;
      int32_t d2 = av > c2 ? av - c2 : c2 - av;
      term = (d1 <= d2) ? c1 : c2;
    }
    val += sgn * term;
    r -= sgn * term;
  }
  if (val > C_CLAMP_Q14) val = C_CLAMP_Q14;
  if (val < -C_CLAMP_Q14) val = -C_CLAMP_Q14;
  return val;
}

/* interlacing restore, single forward pass over the merged p/q sequence
 * (cos domain: strictly decreasing), redteam quantize_lsp_csd 671-689 */
static void order_fix(int32_t cp[5], int32_t cq[5]) {
  int32_t *seq[10];
  int i;
  for (i = 0; i < 5; i++) { seq[2 * i] = &cp[i]; seq[2 * i + 1] = &cq[i]; }
  for (i = 1; i < 10; i++) {
    if (*seq[i] >= *seq[i - 1] - ORDER_SEP_Q14 / 2)
      *seq[i] = *seq[i - 1] - ORDER_SEP_Q14;
  }
}

/* non-adjacent-form decomposition of a Q14 coefficient value (multiple of
 * 2^4 by construction).  Terms are (shift, sign) pairs for the 2*c*s
 * multiply implemented as sum of +/- asr(s, 13 - p).  Max 7 terms. */
typedef struct {
  int8_t n;
  int8_t sh[7];  /* shift amount for asr64 (may be -1 => <<1) */
  int8_t sg[7];
} csd_terms;

static void naf_terms(int32_t v, csd_terms *ct) {
  int32_t u = v >> 4; /* Q10; v is a multiple of 16 */
  int pos = 4, n = 0;
  int neg = 0;
  if (u < 0) { neg = 1; u = -u; }
  while (u != 0) {
    if (u & 1) {
      int d = 2 - (int)(u & 3); /* +1 or -1 */
      ct->sh[n] = (int8_t)(13 - pos);
      ct->sg[n] = (int8_t)(neg ? -d : d);
      n++;
      u -= d;
    }
    u >>= 1;
    pos++;
  }
  ct->n = (int8_t)n;
}

/* 2*c*s via the NAF terms */
static int64_t mul2c(const csd_terms *ct, int32_t s) {
  int64_t acc = 0;
  int i;
  for (i = 0; i < ct->n; i++) {
    int64_t t = asr64((int64_t)s, ct->sh[i]);
    acc += ct->sg[i] > 0 ? t : -t;
  }
  return acc;
}

/* ------------------------------------------------------------------ *
 *  init                                                               *
 * ------------------------------------------------------------------ */

void c2tube_init(c2tube_dec *d) {
  int i;
  /* codec2.c 172-180: prev Wo = Wo_min, unvoiced, lsps = i*pi/11, e = 1 */
  static const int16_t init_lsp[10] = {364, 727, 1091, 1455, 1818,
                                       2182, 2545, 2909, 3273, 3636};
  for (i = 0; i < C2TUBE_ORDER; i++) d->prev_lsp_q2[i] = (int16_t)(init_lsp[i] << 2);
  d->prev_wo_num_q2 = 128 << 2;
  d->prev_lg2e_q8 = 0; /* log2(1) */
  d->prev_voiced = 0;
  d->tau_q7 = 0;
  d->lfsr = 0xACE1;
  for (i = 0; i < 4; i++) d->exc_tail[i] = 0;
  for (i = 0; i < 5; i++) d->s1p[i] = d->s2p[i] = d->s1q[i] = d->s2q[i] = 0;
  d->sp_last = d->sq_last = 0;
  d->zlp[0] = d->zlp[1] = d->zhp[0] = d->zhp[1] = 0;
  for (i = 0; i < C2TUBE_ORDER; i++) d->ynum_hist[i] = d->yden_hist[i] = 0;
  d->tilt_state = 0;
}

/* ------------------------------------------------------------------ *
 *  per-sample kernels                                                 *
 * ------------------------------------------------------------------ */

static int32_t lfsr_step(uint16_t *s) { /* -> uniform Q15 in [-32768,32767] */
  uint16_t v = *s;
  int lsb = v & 1;
  v >>= 1;
  if (lsb) v ^= 0xB400;
  *s = v;
  return (int32_t)v - 32768;
}

/* DF2T biquad, Q14 coefficients */
static int32_t biquad(int32_t x, const int16_t b[3], const int16_t a[3],
                      int32_t z[2]) {
  int32_t y = sat32(((int64_t)b[0] * x >> 14) + z[0]);
  z[0] = sat32(((int64_t)b[1] * x >> 14) - ((int64_t)a[1] * y >> 14) + z[1]);
  z[1] = sat32(((int64_t)b[2] * x >> 14) - ((int64_t)a[2] * y >> 14));
  return y;
}

/* G8 two-allpass step: excitation x -> filter output y.
 * P(z) = (1+z^-1) prod(1 - 2 cp_i z^-1 + z^-2),
 * Q(z) = (1-z^-1) prod(1 - 2 cq_i z^-1 + z^-2), A = (P+Q)/2.
 * Placeholder-propagation form: run both FIR chains with current input 0,
 * v = (dP+dQ)/2 = (A-1)y contribution, y = x - v, then update states with
 * the true section inputs (placeholder + y, unity passthrough). */
static int32_t g8_step(c2tube_dec *d, const csd_terms ctp[5],
                       const csd_terms ctq[5], int32_t x) {
  int64_t p = 0, q = 0;
  int32_t inp[6], inq[6];
  int32_t y;
  int k;
  for (k = 0; k < 5; k++) {
    inp[k] = sat32(p);
    p = p - mul2c(&ctp[k], d->s1p[k]) + d->s2p[k];
  }
  inp[5] = sat32(p);
  p = p + d->sp_last; /* (1+z^-1) placeholder output */
  for (k = 0; k < 5; k++) {
    inq[k] = sat32(q);
    q = q - mul2c(&ctq[k], d->s1q[k]) + d->s2q[k];
  }
  inq[5] = sat32(q);
  q = q - d->sq_last; /* (1-z^-1) placeholder output */
  y = sat32((int64_t)x - ((p + q) >> 1));
  for (k = 0; k < 5; k++) {
    d->s2p[k] = d->s1p[k];
    d->s1p[k] = sat32((int64_t)inp[k] + y);
    d->s2q[k] = d->s1q[k];
    d->s1q[k] = sat32((int64_t)inq[k] + y);
  }
  d->sp_last = sat32((int64_t)inp[5] + y);
  d->sq_last = sat32((int64_t)inq[5] + y);
  return y;
}

/* ------------------------------------------------------------------ *
 *  frame decode                                                       *
 * ------------------------------------------------------------------ */

void c2tube_decode_frame(c2tube_dec *d, const uint8_t bits[C2TUBE_FRAME_BYTES],
                         int16_t speech[4 * C2TUBE_N]) {
  uint32_t nbit = 0;
  int v[4], wo_idx, e_idx, lsp_idx[10];
  int32_t lsp_hz[10];
  int i, isub, n, k;
  int16_t cur_lsp_q2[10];
  int16_t cur_wo_num_q2;
  int16_t cur_lg2e_q8;

  /* ---- unpack ---- */
  for (i = 0; i < 4; i++) v[i] = (int)unpack_gray(bits, &nbit, 1);
  wo_idx = (int)unpack_gray(bits, &nbit, 7);
  e_idx = (int)unpack_gray(bits, &nbit, 5);
  for (i = 0; i < 10; i++)
    lsp_idx[i] = (int)unpack_gray(bits, &nbit, LSP_BITS_TAB[i]);

  /* ---- parameter decode (40 ms rate) ---- */
  for (i = 0; i < 10; i++)
    lsp_hz[i] = LSP_CB_FLAT[LSP_CB_OFF[i] + lsp_idx[i]];

  /* check_lsp_order (quantise.c 266-283), 0.1 rad ~ 127 Hz */
  for (i = 1; i < 10; i++) {
    if (lsp_hz[i] < lsp_hz[i - 1]) {
      int32_t tmp = lsp_hz[i - 1];
      lsp_hz[i - 1] = lsp_hz[i] - 127;
      lsp_hz[i] = tmp + 127;
      i = 1;
    }
  }
  /* bw_expand_lsps(lsps, 10, 50, 100) (quantise.c 843-861) */
  for (i = 1; i < 4; i++)
    if (lsp_hz[i] - lsp_hz[i - 1] < 50) lsp_hz[i] = lsp_hz[i - 1] + 50;
  for (i = 4; i < 10; i++)
    if (lsp_hz[i] - lsp_hz[i - 1] < 100) lsp_hz[i] = lsp_hz[i - 1] + 100;

  for (i = 0; i < 10; i++) cur_lsp_q2[i] = (int16_t)(lsp_hz[i] << 2);
  cur_wo_num_q2 = (int16_t)((128 + 7 * wo_idx) << 2);
  cur_lg2e_q8 = LG2E_Q8[e_idx];

  /* ---- 4 x 10 ms subframes ---- */
  for (isub = 0; isub < 4; isub++) {
    int w = isub + 1; /* interpolation weight w/4 (codec2.c 1198-1205) */
    int voiced = v[isub];
    int32_t f_q2[10], c_q14[10];
    int32_t cp[5], cq[5];
    csd_terms ctp[5], ctq[5];
    int32_t lg2e_sub_q8, wo_num_q2 = 0, P_q7 = 0, lg2P_q8 = 0;
    int64_t a_q14[12], pP[12], pQ[12];
    int32_t a_q12[11], num_q12[11], den_q12[11];
    int32_t mu_q15;
    int32_t exc[C2TUBE_N + 4];
    int32_t ybuf[C2TUBE_N];
    uint64_t e_in = 0, e_out = 0;
    int32_t g_q14;

    /* -- interpolate LSPs (quarter-Hz), energy (log2 Q8) -- */
    for (i = 0; i < 10; i++)
      f_q2[i] = ((int32_t)d->prev_lsp_q2[i] * (4 - w) +
                 (int32_t)cur_lsp_q2[i] * w) >> 2;
    lg2e_sub_q8 = ((int32_t)d->prev_lg2e_q8 * (4 - w) +
                   (int32_t)cur_lg2e_q8 * w) >> 2;

    /* -- Wo per interp_Wo2 (interp.c 237-258) -- */
    if (isub == 3) {
      wo_num_q2 = cur_wo_num_q2;
    } else {
      if (voiced && !d->prev_voiced && !v[3]) voiced = 0;
      if (voiced) {
        if (d->prev_voiced && v[3])
          wo_num_q2 = ((int32_t)d->prev_wo_num_q2 * (4 - w) +
                       (int32_t)cur_wo_num_q2 * w) >> 2;
        else if (!d->prev_voiced && v[3])
          wo_num_q2 = cur_wo_num_q2;
        else /* prev voiced, next unvoiced */
          wo_num_q2 = d->prev_wo_num_q2;
      } else {
        wo_num_q2 = 128 << 2; /* Wo_min */
      }
    }
    if (voiced) {
      P_q7 = 10485760 / wo_num_q2; /* 2pi/Wo in samples, Q7; soft div @100Hz */
      lg2P_q8 = log2_q8((uint64_t)P_q7) - (7 << 8);
    }

    /* -- cos(LSP) via LUT, CSD-3 quantise, order fix, NAF -- */
    for (i = 0; i < 10; i++) {
      int32_t f = f_q2[i];
      int idx, r;
      if (f < 4) f = 4;
      if (f > 15996) f = 15996;
      idx = (int)(f >> 6);
      r = (int)(f & 63);
      c_q14[i] = (int32_t)COS_Q14[idx] +
                 (((int32_t)COS_Q14[idx + 1] - (int32_t)COS_Q14[idx]) * r >> 6);
    }
    for (i = 0; i < 5; i++) {
      cp[i] = csd3_q14(c_q14[2 * i]);     /* odd LSPs w1,w3,..  */
      cq[i] = csd3_q14(c_q14[2 * i + 1]); /* even LSPs w2,w4,.. */
    }
    order_fix(cp, cq);
    for (i = 0; i < 5; i++) {
      naf_terms(cp[i], &ctp[i]);
      naf_terms(cq[i], &ctq[i]);
    }

    /* -- rebuild A(z) = (P+Q)/2 in Q14 for the L4 folded postfilter -- */
    for (i = 0; i < 12; i++) { pP[i] = 0; pQ[i] = 0; }
    pP[0] = 16384; pP[1] = 16384;  /* (1 + z^-1) */
    pQ[0] = 16384; pQ[1] = -16384; /* (1 - z^-1) */
    for (k = 0; k < 5; k++) {
      int deg = 1 + 2 * k; /* current degree */
      for (i = deg + 2; i >= 0; i--) {
        int64_t t = (i <= deg ? pP[i] : 0) + (i >= 2 ? pP[i - 2] : 0);
        int64_t m = (i >= 1 && i - 1 <= deg)
                        ? ((int64_t)pP[i - 1] * (-2 * cp[k]) >> 14) : 0;
        pP[i] = t + m;
      }
      for (i = deg + 2; i >= 0; i--) {
        int64_t t = (i <= deg ? pQ[i] : 0) + (i >= 2 ? pQ[i - 2] : 0);
        int64_t m = (i >= 1 && i - 1 <= deg)
                        ? ((int64_t)pQ[i - 1] * (-2 * cq[k]) >> 14) : 0;
        pQ[i] = t + m;
      }
    }
    for (i = 0; i < 11; i++) a_q14[i] = (pP[i] + pQ[i]) >> 1;
    for (i = 0; i < 11; i++) a_q12[i] = sat32(a_q14[i] >> 2);

    /* -- L4 postfilter coefficients + tilt mu (param rate) -- */
    for (i = 0; i < 11; i++) {
      num_q12[i] = sat32((int64_t)a_q12[i] * G1POW_Q14[i] >> 14);
      den_q12[i] = sat32((int64_t)a_q12[i] * G2POW_Q14[i] >> 14);
    }
    {
      /* truncated impulse response of Hpf = num/den, 22 samples (G.729-style
         tilt; tube.py synth_ladder L4) */
      int64_t h[22], r0 = 0, r1 = 0;
      int j;
      for (j = 0; j < 22; j++) {
        int64_t acc = (j == 0) ? (int64_t)num_q12[0] << 12 : 0;
        for (k = 1; k <= 10; k++) {
          if (j - k < 0) break;
          if (j == k) acc += (int64_t)num_q12[k] << 12;
          acc -= (int64_t)den_q12[k] * h[j - k];
        }
        h[j] = acc >> 12;
      }
      for (j = 0; j < 22; j++) r0 += h[j] * h[j];
      for (j = 0; j < 21; j++) r1 += h[j] * h[j + 1];
      mu_q15 = (r0 > 0) ? (int32_t)((r1 << 14) / r0) : 0; /* 0.5*r1/r0, Q15 */
    }

    /* -- excitation (L0 + L2) -- */
    for (n = 0; n < C2TUBE_N + 4; n++) exc[n] = 0;
    for (n = 0; n < 4; n++) exc[n] = d->exc_tail[n];

    if (voiced) {
      /* pulse height h = sqrt(512*E*P) (energy convention derived from
         aks_to_M2, quantise.c 391-467: Am^2 = E*Wo*(FFT/2pi)*|H|^2, folded
         with tube.py's x2 output scale); +8 guard bits */
      int32_t lg2h = (lg2e_sub_q8 + lg2P_q8 + (9 << 8)) >> 1;
      int32_t h_q = exp2_shift(lg2h, C2TUBE_GUARD);
      int32_t dc_q = (int32_t)(((int64_t)h_q << 7) / P_q7);
      int32_t s_n; /* mixed-excitation noise scale sqrt(3)*h/sqrt(P) */
      uint32_t tau = d->tau_q7;
      while (tau < (C2TUBE_N << 7)) {
        int n0 = (int)(tau >> 7);
        int32_t frac = (int32_t)(tau & 127);
        exc[n0] = sat32((int64_t)exc[n0] + ((int64_t)h_q * frac >> 7));
        exc[n0 + 1] =
            sat32((int64_t)exc[n0 + 1] + ((int64_t)h_q * (128 - frac) >> 7));
        tau += (P_q7 > 256) ? (uint32_t)P_q7 : 256;
      }
      tau -= C2TUBE_N << 7;
      d->tau_q7 = (uint16_t)tau;

      s_n = exp2_shift(lg2h - (lg2P_q8 >> 1) + 203, C2TUBE_GUARD); /* +log2(sqrt3) */
      for (n = 0; n < C2TUBE_N; n++) {
        int32_t pulse = sat32((int64_t)exc[n] - dc_q);
        int32_t nq = lfsr_step(&d->lfsr);
        int32_t nn = sat32((int64_t)nq * s_n >> 15);
        int32_t lp = biquad(pulse, B_LP_Q14, A_LP_Q14, d->zlp);
        int32_t hp = biquad(nn, B_HP_Q14, A_HP_Q14, d->zhp);
        int32_t x = sat32((int64_t)lp + hp);
        int32_t y = g8_step(d, ctp, ctq, x);
        e_in += (uint64_t)((int64_t)y * y);
        ybuf[n] = y;
      }
    } else {
      /* unvoiced: white LFSR noise, rms^2 = 512*E  (sqrt(1536*E) scale on
         uniform Q15 noise); log2(1536)*256 = 2710 */
      int32_t s_uv = exp2_shift((lg2e_sub_q8 + 2710) >> 1, C2TUBE_GUARD);
      for (n = 0; n < C2TUBE_N; n++) {
        int32_t nq = lfsr_step(&d->lfsr);
        int32_t x = sat32(((int64_t)nq * s_uv >> 15) + exc[n]);
        int32_t y = g8_step(d, ctp, ctq, x);
        e_in += (uint64_t)((int64_t)y * y);
        ybuf[n] = y;
      }
      d->tau_q7 = (uint16_t)(d->tau_q7 > (C2TUBE_N << 7)
                                 ? d->tau_q7 - (C2TUBE_N << 7)
                                 : 0);
    }
    for (n = 0; n < 4; n++) d->exc_tail[n] = exc[C2TUBE_N + n];

    /* -- L4 postfilter: num/den + tilt, in place over ybuf -- */
    for (n = 0; n < C2TUBE_N; n++) {
      int64_t acc = (int64_t)num_q12[0] * ybuf[n];
      int32_t yp, yt;
      for (k = 1; k <= 10; k++) acc += (int64_t)num_q12[k] * d->ynum_hist[k - 1];
      for (k = 1; k <= 10; k++) acc -= (int64_t)den_q12[k] * d->yden_hist[k - 1];
      yp = sat32(acc >> 12);
      for (k = 9; k >= 1; k--) {
        d->ynum_hist[k] = d->ynum_hist[k - 1];
        d->yden_hist[k] = d->yden_hist[k - 1];
      }
      d->ynum_hist[0] = ybuf[n];
      d->yden_hist[0] = yp;
      yt = sat32((int64_t)yp - ((int64_t)mu_q15 * d->tilt_state >> 15));
      d->tilt_state = yp;
      e_out += (uint64_t)((int64_t)yt * yt);
      ybuf[n] = yt;
    }

    /* -- AGC: g = sqrt(e_in/e_out), log2 domain, clip [0.1, 10] -- */
    if (e_in > 0 && e_out > 0) {
      int32_t dlg = (log2_q8(e_in) - log2_q8(e_out)) >> 1;
      g_q14 = exp2_shift(dlg, 14); /* Q14 gain */
      if (g_q14 < 1638) g_q14 = 1638;
      if (g_q14 > 163840) g_q14 = 163840;
    } else {
      g_q14 = 16384;
    }

    /* -- guard-bit removal with rounding, output int16 -- */
    for (n = 0; n < C2TUBE_N; n++) {
      int64_t t = (int64_t)ybuf[n] * g_q14 >> 14;
      t = (t + (1 << (C2TUBE_GUARD - 1))) >> C2TUBE_GUARD;
      speech[isub * C2TUBE_N + n] = sat16(sat32(t));
    }
  }

  /* ---- update 40 ms memories (codec2.c 1230-1233) ---- */
  for (i = 0; i < 10; i++) d->prev_lsp_q2[i] = cur_lsp_q2[i];
  d->prev_wo_num_q2 = cur_wo_num_q2;
  d->prev_lg2e_q8 = cur_lg2e_q8;
  d->prev_voiced = (uint8_t)v[3];
}
