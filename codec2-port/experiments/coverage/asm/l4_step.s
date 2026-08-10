# RV32EC inner loop: L4 adaptive postfilter step — Hpf = A(z/g1)/A(z/g2),
# order 10/10, + tilt (1 - mu z^-1) + energy taps for AGC.  JIT CSD-3, DF2T.
# ---------------------------------------------------------------------------
# Audited asm twin of proto/decoder c2tube_dec.c L4 block in its TARGET form.
# TWO deliberate deviations from the host C prototype, both flagged in the
# coverage report because "state form is part of the spec" (proto/decoder
# methodological lesson):
#   1. DF2T instead of DF1 double-history: z[10] instead of
#      ynum_hist[10]+yden_hist[10]; saves the ~70 cyc/sample history
#      shuffle (the C twin shifts 20 words per sample).  Transfer function
#      identical; transients under 100 Hz coefficient switching differ ->
#      the golden model must adopt the shipped form before tier-2
#      bit-exactness gating.
#   2. AGC energies accumulated as SUM |y| (2 x 4 cyc/sample) instead of
#      sum y^2: y*y is data x data — on P2 that is a soft-mul (~126 cyc)
#      PER SAMPLE PER TAP = ~2 MHz just for energy metering.  The log2-AGC
#      only consumes the RATIO of in/out energies, so the same abs-sum
#      proxy on both sides cancels most of the crest bias; residual error
#      enters the AGC gain (clip [0.1,10]) — needs one golden-model A/B.
# num/den coefficients change per subframe -> JIT-emitted CSD-3 (emit cost
# booked in the param path).  mu is per-subframe data -> re-CSD'd per
# subframe (booked there too), 3 terms here.
#
# Register plan: a0 y_in (from G8), a1 accumulator, a3 y_out, a4 tilt state,
# t0,t1,ra scratch, a2 z-state base.  Costing: ALU/shift 1, load 2, store 2.

#=== region: ein_abs repeat=1
    srai ra, a0, 31         # e_in += |y_in|  (abs-sum AGC proxy)
    xor  t0, a0, ra
    sub  t0, t0, ra
    lw   t1, 32(sp)
    add  t1, t1, t0
    sw   t1, 32(sp)
#=== end
#=== region: head repeat=1
    # yp = num0*y + z0   (num0 CSD-3, JIT)
    srai ra, a0, 2          # num0 t1
    mv   t0, ra
    srai ra, a0, 5          # num0 t2
    add  t0, t0, ra
    srai ra, a0, 9          # num0 t3
    add  t0, t0, ra
    lw   ra, 0(a2)          # z0
    add  t0, t0, ra         # yp
#=== end
# --- sections k=1..9: z_{k-1}' = num_k*x - den_k*yp + z_k  (JIT CSD-3 x2) --
#=== region: sect repeat=9
    srai ra, a0, 2          # num_k t1                              (1)
    mv   t1, ra             #                                       (1)
    srai ra, a0, 5          # num_k t2                              (1)
    add  t1, t1, ra         #                                       (1)
    srai ra, a0, 8          # num_k t3                              (1)
    sub  t1, t1, ra         #                                       (1)
    srai ra, t0, 1          # den_k t1                              (1)
    sub  t1, t1, ra         #                                       (1)
    srai ra, t0, 4          # den_k t2                              (1)
    add  t1, t1, ra         #                                       (1)
    srai ra, t0, 7          # den_k t3                              (1)
    sub  t1, t1, ra         #                                       (1)
    lw   ra, 4(a2)          # z_k                                   (2)
    add  t1, t1, ra         #                                       (1)
    sw   t1, 0(a2)          # z_{k-1}'                              (2)
#=== end
#=== region: sect10 repeat=1
    # z_9' = num10*x - den10*yp (no z_10)
    srai ra, a0, 3
    mv   t1, ra
    srai ra, a0, 6
    add  t1, t1, ra
    srai ra, a0, 9
    sub  t1, t1, ra
    srai ra, t0, 2
    sub  t1, t1, ra
    srai ra, t0, 5
    add  t1, t1, ra
    srai ra, t0, 8
    sub  t1, t1, ra
    sw   t1, 36(a2)
#=== end
#=== region: tilt repeat=1
    # yt = yp - mu*yp_prev  (mu CSD-3, re-CSD'd per subframe; yp_prev in a4)
    srai ra, a4, 2          # mu t1
    mv   t1, ra
    srai ra, a4, 5          # mu t2
    add  t1, t1, ra
    srai ra, a4, 8          # mu t3
    sub  t1, t1, ra
    sub  a3, t0, t1         # yt
    mv   a4, t0             # tilt state = yp
#=== end
#=== region: eout_abs repeat=1
    srai ra, a3, 31         # e_out += |yt|
    xor  t0, a3, ra
    sub  t0, t0, ra
    lw   t1, 36(sp)
    add  t1, t1, t0
    sw   t1, 36(sp)
#=== end
# AGC scale pass (second pass over the 80-sample ybuf once g is known):
# per sample: lw ybuf(2) + g CSD-3 (6, re-CSD'd per subframe) + round/guard
# shift (3) + sh out (2) + ptr (2) = 15 cyc — counted as agc_scale below.
#=== region: agc_scale repeat=1
    lw   a0, 40(sp)
    srai ra, a0, 1
    mv   t1, ra
    srai ra, a0, 4
    add  t1, t1, ra
    srai ra, a0, 7
    add  t1, t1, ra
    addi t1, t1, 128
    srai t1, t1, 8
    sh   t1, 0(a2)
#=== end
