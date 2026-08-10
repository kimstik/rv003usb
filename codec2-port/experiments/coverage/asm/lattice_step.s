# RV32EC inner loop: order-10 Kelly-Lochbaum/IIR lattice step, CSD-3,
# JIT-STRAIGHTLINE (static count for pareto's kl-lattice-csd3 row).
# ---------------------------------------------------------------------------
# Golden model: synth-redteam rt/engines_rt.py synth_kl_lattice(): all-pole
# lattice with reflection coefficients k_i, |k|<1 by construction, CSD-3
# quantised.  Per sample (order M=10):
#     f_M = x
#     for i = M-1 .. 0:   f_i = f_{i+1} - k_i * b_i
#                         b_{i+1}' = b_i + k_i * f_i     (retire b_{i+1})
#     y = f_0 ;  b_0' = y
# TWO CSD multiplies per stage (vs one for G8) — this is where the lattice
# pays for its best-SD-per-term coefficients.  k_i change per subframe, so
# shifts are JIT-emitted into SRAM (same premise as sos_csd_jit.s /
# g8_step.s).  b[] is memory-resident (11 words); f accumulates in a1.
#
# Register plan: a0 x, a1 f accumulator, a2 state base (b[0..10]),
# t0,t1,ra scratch, a5 y (output).
# Costing convention: ALU/shift 1, load 2, store 2, taken branch 2,
# untaken 1 (= count_asm.py).

sample_loop:
#=== region: excitation repeat=1
    lw   ra, 0(sp)          # phase (Q32)
    lw   t1, 4(sp)          # inc
    add  t1, ra, t1
    sltu ra, t1, ra
    sw   t1, 0(sp)
    bnez ra, fire           #notaken
after_fire:
    lw   a1, 8(sp)          # f_M = x = -dc
#=== end
# --- stage i (JIT-emitted, one per reflection coefficient) -----------------
#=== region: stage repeat=10
    lw   t0, 0(a2)          # b_i                                   (2)
    srai ra, t0, 1          # k_i*b_i: CSD term 1                   (1)
    sub  a1, a1, ra         # f -= ...                              (1)
    srai ra, t0, 4          # CSD term 2                            (1)
    add  a1, a1, ra         #                                       (1)
    srai ra, t0, 6          # CSD term 3                            (1)
    sub  a1, a1, ra         #                                       (1)
    srai ra, a1, 1          # k_i*f_i: CSD term 1                   (1)
    add  t0, t0, ra         # b_i + ...                             (1)
    srai ra, a1, 4          # CSD term 2                            (1)
    sub  t0, t0, ra         #                                       (1)
    srai ra, a1, 6          # CSD term 3                            (1)
    add  t0, t0, ra         #                                       (1)
    sw   t0, 4(a2)          # retire b_{i+1}                        (2)
#=== end
#=== region: retire_b0 repeat=1
    sw   a1, 0(a2)          # b_0 = y = f_0
    mv   a5, a1
#=== end
#=== region: output repeat=1
    lw   ra, 24(sp)
    sh   a5, 0(ra)
    addi ra, ra, 2
    sw   ra, 24(sp)
    lw   a0, 28(sp)
    bne  ra, a0, sample_loop  #taken
#=== end

fire:
#=== region: fire repeat=0
    j    after_fire
#=== end
