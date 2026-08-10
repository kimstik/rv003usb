# RV32EC inner loop: L2 mixed-excitation stage — LFSR noise + 2 fixed
# Butterworth biquads (LP on pulses, HP on noise), DF2T, BAKED CSD-3.
# ---------------------------------------------------------------------------
# Audited asm twin of proto/decoder c2tube_dec.c voiced-branch L2 code
# (lfsr_step + 2x biquad + mix), in its TARGET form: the crossover
# coefficients are compile-time constants (fc = 2.5 kHz), so their CSD
# shift-adds live in FLASH (no JIT, no per-subframe re-CSD).  The noise
# scale s_n is per-subframe data -> re-CSD'd once per subframe (booked in
# the param path), 3-term CSD here.
#
# Register plan: a0 pulse excitation in / mixed out, a3 noise sample,
# a4 lfsr state (kept in reg), t0,t1,ra scratch, a2 z-state base.
# Costing: ALU/shift 1, load 2, store 2, taken branch 2, untaken 1.

#=== region: lfsr repeat=1
    andi t0, a4, 1          # lsb
    srli a4, a4, 1
    beqz t0, 1f             #notaken (p=0.5; costed untaken, +1 amortized below)
    xori a4, a4, 0          # placeholder: xor 0xB400 via lui+xor on RV32EC
    lui  t0, 11             # 0xB400 >> 4 class constant build
    xor  a4, a4, t0
1:  addi a3, a4, -2048      # center -> signed Q15 noise (representative)
#=== end
#=== region: noise_scale repeat=1
    srai ra, a3, 2          # s_n CSD term 1 (re-CSD'd per subframe)
    mv   t1, ra
    srai ra, a3, 5          # term 2
    add  t1, t1, ra
    srai ra, a3, 9          # term 3
    sub  a3, t1, ra         # n = s_n * noise
#=== end
# --- LP biquad on pulse path (DF2T, baked CSD-3 per coefficient) -----------
#=== region: biquad_lp repeat=1
    # y = b0*x + z0
    srai ra, a0, 3          # b0 t1                                 (1)
    mv   t0, ra             #                                       (1)
    srai ra, a0, 5          # b0 t2                                 (1)
    add  t0, t0, ra         #                                       (1)
    srai ra, a0, 8          # b0 t3                                 (1)
    add  t0, t0, ra         #                                       (1)
    lw   ra, 0(a2)          # z0                                    (2)
    add  t0, t0, ra         # y                                     (1)
    # z0' = b1*x - a1*y + z1
    srai ra, a0, 2          # b1 t1                                 (1)
    mv   t1, ra             #                                       (1)
    srai ra, a0, 6          # b1 t2                                 (1)
    add  t1, t1, ra         #                                       (1)
    srai ra, a0, 9          # b1 t3                                 (1)
    add  t1, t1, ra         #                                       (1)
    srai ra, t0, 1          # a1 t1                                 (1)
    sub  t1, t1, ra         #                                       (1)
    srai ra, t0, 4          # a1 t2                                 (1)
    add  t1, t1, ra         #                                       (1)
    srai ra, t0, 7          # a1 t3                                 (1)
    sub  t1, t1, ra         #                                       (1)
    lw   ra, 4(a2)          # z1                                    (2)
    add  t1, t1, ra         #                                       (1)
    sw   t1, 0(a2)          # z0'                                   (2)
    # z1' = b2*x - a2*y
    srai ra, a0, 3          # b2 t1                                 (1)
    mv   t1, ra             #                                       (1)
    srai ra, a0, 5          # b2 t2                                 (1)
    add  t1, t1, ra         #                                       (1)
    srai ra, a0, 8          # b2 t3                                 (1)
    add  t1, t1, ra         #                                       (1)
    srai ra, t0, 2          # a2 t1                                 (1)
    sub  t1, t1, ra         #                                       (1)
    srai ra, t0, 5          # a2 t2                                 (1)
    add  t1, t1, ra         #                                       (1)
    srai ra, t0, 9          # a2 t3                                 (1)
    sub  t1, t1, ra         #                                       (1)
    sw   t1, 4(a2)          # z1'                                   (2)
    mv   a0, t0             # lp out                                (1)
#=== end
# --- HP biquad on noise path: identical op structure, z2/z3 state ----------
#=== region: biquad_hp repeat=1
    srai ra, a3, 3
    mv   t0, ra
    srai ra, a3, 5
    add  t0, t0, ra
    srai ra, a3, 8
    add  t0, t0, ra
    lw   ra, 8(a2)
    add  t0, t0, ra
    srai ra, a3, 2
    mv   t1, ra
    srai ra, a3, 6
    add  t1, t1, ra
    srai ra, a3, 9
    add  t1, t1, ra
    srai ra, t0, 1
    sub  t1, t1, ra
    srai ra, t0, 4
    add  t1, t1, ra
    srai ra, t0, 7
    sub  t1, t1, ra
    lw   ra, 12(a2)
    add  t1, t1, ra
    sw   t1, 8(a2)
    srai ra, a3, 3
    mv   t1, ra
    srai ra, a3, 5
    add  t1, t1, ra
    srai ra, a3, 8
    add  t1, t1, ra
    srai ra, t0, 2
    sub  t1, t1, ra
    srai ra, t0, 5
    add  t1, t1, ra
    srai ra, t0, 9
    sub  t1, t1, ra
    sw   t1, 12(a2)
    mv   a3, t0
#=== end
#=== region: mix repeat=1
    add  a0, a0, a3         # x = lp(pulse) + hp(noise)
#=== end
