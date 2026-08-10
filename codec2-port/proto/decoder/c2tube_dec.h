/* c2tube_dec.h — fixed-point "tube" decoder for codec2 mode 1300.
 *
 * Architecture (codec2-port round-3 C prototype):
 *   bitstream 1300 unpack  ->  int parameter decode (LSP int-Hz tables,
 *   log2-domain energy, exact-rational Wo)  ->  per-subframe interpolation
 *   ->  G8 two-allpass LSP synthesis filter, CSD-3-term cos coefficients,
 *   int32 state with 8 guard bits (synth-redteam CHANGED-1/2 prescriptions)
 *   ->  excitation ladder L0 (zero-mean fractional-delay impulse train /
 *   LFSR noise) + L2 (mixed excitation, 2.5 kHz crossover) + L4 (folded
 *   postfilter A(z/0.65)/A(z/0.8) + tilt + AGC)  per tube-ladder REPORT.md.
 *
 * Integer-only in the frame/sample path: no float anywhere in this module.
 * (The "FLOAT_PARAM_OK" concession of the differential-porting principle is
 * not used — parameter-rate math fit in integers with two soft divisions.)
 *
 * Bit layout of one 52-bit frame (7 bytes), reimplemented from the layout
 * documented in codec2.c codec2_encode_1300() (pinned @310777b,
 * src/codec2.c:1077-1124) — clean-room from the documented order:
 *   1+1+1+1 voicing bits (10 ms subframes 0..3), 7-bit Wo index,
 *   5-bit energy index, 10 scalar LSP indices of 4,4,4,4,4,4,4,3,3,2 bits.
 * All fields Gray-coded (codec2.c:206 c2->gray=1), MSB-first packing
 * (pack.c unpack_natural_or_gray).
 */
#ifndef C2TUBE_DEC_H
#define C2TUBE_DEC_H

#include <stdint.h>

#define C2TUBE_N 80          /* 10 ms subframe at 8 kHz  */
#define C2TUBE_NSUB 4        /* subframes per 40 ms frame */
#define C2TUBE_FRAME_BYTES 7 /* 52 bits */
#define C2TUBE_ORDER 10      /* LPC/LSP order            */
#define C2TUBE_GUARD 8       /* guard bits on filter state (redteam presc. a) */

typedef struct {
  /* previous 40 ms frame decoded params (interpolation memory) */
  int16_t prev_lsp_q2[C2TUBE_ORDER]; /* LSP, quarter-Hz            */
  int16_t prev_wo_num_q2;            /* Wo as (128+7*idx)<<2, unit 2*pi/20480 */
  int16_t prev_lg2e_q8;              /* log2(E), Q8                */
  uint8_t prev_voiced;

  /* excitation state */
  uint16_t tau_q7; /* time to next pulse, samples Q7 (uint16 accumulator) */
  uint16_t lfsr;   /* 16-bit Galois LFSR, taps 0xB400              */
  int32_t exc_tail[4];

  /* G8 two-allpass synthesis filter state (int32, 8 guard bits) */
  int32_t s1p[5], s2p[5]; /* P-branch section delays  */
  int32_t s1q[5], s2q[5]; /* Q-branch section delays  */
  int32_t sp_last, sq_last;

  /* L2 mixed-excitation crossover biquads (DF2T) */
  int32_t zlp[2], zhp[2];

  /* L4 postfilter state */
  int32_t ynum_hist[C2TUBE_ORDER]; /* past filter outputs y  */
  int32_t yden_hist[C2TUBE_ORDER]; /* past postfilter outputs yp */
  int32_t tilt_state;
} c2tube_dec;

void c2tube_init(c2tube_dec *d);

/* decode one 52-bit frame -> 320 samples (40 ms) */
void c2tube_decode_frame(c2tube_dec *d, const uint8_t bits[C2TUBE_FRAME_BYTES],
                         int16_t speech[4 * C2TUBE_N]);

/* FIXED_DEBUG-style saturation census (host diagnostics) */
extern uint32_t c2tube_sat_count;

#endif
