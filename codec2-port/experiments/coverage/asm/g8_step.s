# RV32EC inner loop: G8 two-allpass LSP synthesis step, CSD-3, JIT-STRAIGHTLINE.
# ---------------------------------------------------------------------------
# Audited asm twin of proto/decoder c2tube_dec.c:g8_step() in its TARGET form:
#   - int32 state with guard bits, no explicit saturation (0 saturations
#     measured on the corpus; guard bits carry the headroom — red-team
#     CHANGED-1a prescription), matching the sos_csd_jit.s convention.
#   - coefficient shift-adds JIT-emitted into SRAM once per subframe
#     (2 branches x 5 sections x 3-term CSD), like sos_csd_jit.s.
#   - FUSED-UPDATE form: the C prototype runs two passes per sample
#     (placeholder chains, then the +y state update).  Here the update of
#     sample n is folded into the chain pass of sample n+1: memory cell
#     A[k] holds inp[k] (placeholder value), B[k] holds s1p[k]; section k
#     rebuilds s1p[k] = A[k] + y_prev in one add and retires it into B[k]
#     (which then serves as next sample's s2p[k]).  Saves ~60 cyc/sample
#     over the literal two-pass C structure.
#
# Register plan (RV32E):
#   a0 x (excitation in), a1 chain accumulator p/q, a2 state base,
#   a4 saved p, a5 y_prev (previous output, feeds the fused update),
#   t0,t1,ra scratch.  sp frame: A[0..5]/Aq[0..5] cells + out ptr/end.
#
# Costing convention (= round-1 model / count_asm.py): ALU/shift 1, load 2,
# store 2, taken branch 2, untaken 1.

sample_loop:
#=== region: excitation repeat=1
    lw   ra, 0(sp)          # phase accumulator (Q32)
    lw   t1, 4(sp)          # inc
    add  t1, ra, t1
    sltu ra, t1, ra         # period boundary carry
    sw   t1, 0(sp)
    bnez ra, fire           #notaken (<= once per pitch period)
after_fire:
    lw   a0, 8(sp)          # x = -dc (zero-mean excitation base)
    li   a1, 0              # p accumulator
#=== end
# --- P branch: 5 sections (1 - 2 cp_k z^-1 + z^-2), placeholder chain ------
# y_prev lives in a5 across the whole sample (written by `combine` below)
#=== region: p_sect repeat=5
    lw   t0, 16(a2)         # A[k] = inp_prev[k]                    (2)
    add  t0, t0, a5         # s1p[k] = inp_prev[k] + y_prev         (1)
    lw   t1, 56(a2)         # B[k] = s2p[k] (= s1p one sample ago)  (2)
    sw   t0, 56(a2)         # retire s1p[k] -> becomes next s2p[k]  (2)
    sw   a1, 16(a2)         # A[k] = inp[k] = p (placeholder)       (2)
    srai ra, t0, 1          # -2*cp_k*s1p: CSD term 1               (1)
    sub  a1, a1, ra         #                                       (1)
    srai ra, t0, 4          # CSD term 2                            (1)
    add  a1, a1, ra         #                                       (1)
    srai ra, t0, 7          # CSD term 3                            (1)
    sub  a1, a1, ra         #                                       (1)
    add  a1, a1, t1         # + s2p[k]                              (1)
#=== end
#=== region: p_tail repeat=1
    lw   t0, 36(a2)         # A[5] = inp_prev[5]
    add  t0, t0, a5         # sp_last = inp_prev[5] + y_prev
    sw   a1, 36(a2)         # A[5] = inp[5] = p
    add  a1, a1, t0         # p += sp_last   (1+z^-1 tap)
    mv   a4, a1             # save p
    li   a1, 0              # q accumulator
#=== end
# --- Q branch: mirror with cq_k and (1-z^-1) tap ---------------------------
#=== region: q_sect repeat=5
    lw   t0, 40(a2)
    add  t0, t0, a5
    lw   t1, 76(a2)
    sw   t0, 76(a2)
    sw   a1, 40(a2)
    srai ra, t0, 1
    sub  a1, a1, ra
    srai ra, t0, 3
    add  a1, a1, ra
    srai ra, t0, 6
    sub  a1, a1, ra
    add  a1, a1, t1
#=== end
#=== region: q_tail repeat=1
    lw   t0, 60(a2)         # Aq[5]
    add  t0, t0, a5         # sq_last
    sw   a1, 60(a2)         # Aq[5] = inq[5] = q
    sub  a1, a1, t0         # q -= sq_last   (1-z^-1 tap)
#=== end
#=== region: combine repeat=1
    add  a1, a1, a4         # p + q
    srai a1, a1, 1          # (p+q)/2 = (A(z)-1) contribution
    sub  a5, a0, a1         # y = x - v ; y_prev for next sample
#=== end
#=== region: output repeat=1
    lw   ra, 24(sp)         # out ptr
    sh   a5, 0(ra)
    addi ra, ra, 2
    sw   ra, 24(sp)
    lw   a0, 28(sp)         # out end
    bne  ra, a0, sample_loop  #taken (159/160)
#=== end

# --- impulse fire path, <= 1/pitch period: identical to sos_csd_jit.s -----
# (1 soft-mul for the 2-tap fractional split, amortized per period)
fire:
#=== region: fire repeat=0
    j    after_fire
#=== end
