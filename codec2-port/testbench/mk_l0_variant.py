#!/usr/bin/env python3
"""mk_l0_variant.py — derive a build-switchable copy of the c2tube decoder.

The merged prototype at proto/decoder/ implements the P2 knee L0+L2+L4
(tube-ladder).  The listening bench also wants to hear the BOTTOM of that
ladder — rung L0 alone (bare impulse train / LFSR noise into the G8 tube,
no mixed excitation, no postfilter) — so the tier difference is audible and
not just a number in a CSV.

Rather than fork the decoder, this script rewrites proto/decoder/c2tube_dec.c
into out/build/src/c2tube_dec.c with two `#ifdef C2TUBE_L0_ONLY` guards.
Compiled WITHOUT the define the file must be behaviourally identical to the
pristine source (build_decoders.sh checks the binaries byte-for-byte against
a pristine build); compiled WITH it, the L2 and L4 rungs are bypassed:

  L2 off — voiced excitation is the raw zero-mean fractional-delay impulse
           train; the 2.5 kHz LP/HP crossover biquads and the HP noise
           branch are skipped.  Unvoiced is L0 already (white LFSR noise).
  L4 off — the folded postfilter is forced to the identity transfer
           (num = den = 1, tilt mu = 0), which also makes the log-domain AGC
           settle at exactly 1.0 because e_out == e_in.  No code path is
           removed, so the fixed-point plumbing under test stays the same.

Each replacement must match EXACTLY ONCE or the script fails loudly — the
prototype source is pinned by git, so a silent partial patch is a bug.

Usage: mk_l0_variant.py            -> out/build/src/{c2tube_dec.c,*.h,main}
"""
import os
import shutil
import sys

import paths

L2_FROM = """        int32_t pulse = sat32((int64_t)exc[n] - dc_q);
        int32_t nq = lfsr_step(&d->lfsr);
        int32_t nn = sat32((int64_t)nq * s_n >> 15);
        int32_t lp = biquad(pulse, B_LP_Q14, A_LP_Q14, d->zlp);
        int32_t hp = biquad(nn, B_HP_Q14, A_HP_Q14, d->zhp);
        int32_t x = sat32((int64_t)lp + hp);
"""

L2_TO = """#ifdef C2TUBE_L0_ONLY
        /* testbench: rung L0 — bare impulse train, no mixed excitation */
        int32_t x = sat32((int64_t)exc[n] - dc_q);
        (void)s_n;
#else
        int32_t pulse = sat32((int64_t)exc[n] - dc_q);
        int32_t nq = lfsr_step(&d->lfsr);
        int32_t nn = sat32((int64_t)nq * s_n >> 15);
        int32_t lp = biquad(pulse, B_LP_Q14, A_LP_Q14, d->zlp);
        int32_t hp = biquad(nn, B_HP_Q14, A_HP_Q14, d->zhp);
        int32_t x = sat32((int64_t)lp + hp);
#endif
"""

L4_FROM = """      mu_q15 = (r0 > 0) ? (int32_t)((r1 << 14) / r0) : 0; /* 0.5*r1/r0, Q15 */
    }
"""

L4_TO = """      mu_q15 = (r0 > 0) ? (int32_t)((r1 << 14) / r0) : 0; /* 0.5*r1/r0, Q15 */
    }
#ifdef C2TUBE_L0_ONLY
    /* testbench: rung L0 — postfilter/tilt bypassed.  Identity num/den and
       mu=0 make the L4 block a pass-through, and e_out == e_in makes the
       AGC gain exactly 1.0, so no level rescaling is smuggled in. */
    for (i = 0; i < 11; i++) {
      num_q12[i] = (i == 0) ? 4096 : 0;
      den_q12[i] = (i == 0) ? 4096 : 0;
    }
    mu_q15 = 0;
#endif
"""


def sub_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        sys.exit(f"ERROR: pattern '{label}' matched {n} times, expected 1 — "
                 f"proto/decoder/c2tube_dec.c has drifted; update "
                 f"mk_l0_variant.py")
    return text.replace(old, new)


def main():
    root = paths.c2port_root()
    src = os.path.join(root, "proto", "decoder")
    dst = os.path.join(paths.OUT, "build", "src")
    pristine = os.path.join(paths.OUT, "build", "src_pristine")
    for d in (dst, pristine):
        os.makedirs(d, exist_ok=True)

    for f in ("c2tube_dec.h", "c2tube_tables.h", "c2tube_main.c"):
        shutil.copy2(os.path.join(src, f), os.path.join(dst, f))
        shutil.copy2(os.path.join(src, f), os.path.join(pristine, f))
    shutil.copy2(os.path.join(src, "c2tube_dec.c"),
                 os.path.join(pristine, "c2tube_dec.c"))

    with open(os.path.join(src, "c2tube_dec.c")) as fh:
        t = fh.read()
    t = sub_once(t, L2_FROM, L2_TO, "L2 mixed excitation")
    t = sub_once(t, L4_FROM, L4_TO, "L4 postfilter/tilt")
    with open(os.path.join(dst, "c2tube_dec.c"), "w") as fh:
        fh.write(t)
    print(f"mk_l0_variant: wrote {dst}/c2tube_dec.c (2 guards) and a pristine "
          f"copy for the identity check")


if __name__ == "__main__":
    main()
