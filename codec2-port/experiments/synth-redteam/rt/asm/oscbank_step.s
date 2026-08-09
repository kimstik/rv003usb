# RV32EC inner loop: osc-bank, per-harmonic step (soft-mul core, CH32V003).
# ---------------------------------------------------------------------------
# Arrays (all int16): phase[], inc[], amp[], damp[] indexed by harmonic;
# 256-entry int16 sin LUT.  Per harmonic per sample:
#   phase[i] += inc[i]  (u16 wrap free)
#   s = sinlut[phase[i] >> 8]
#   amp[i] += damp[i]   (linear amplitude ramp, Q15)
#   acc += (amp[i] * s) >> 15      <- __mulsi3 soft multiply
#
# Register plan: a0/a1 mul args+result (clobbered by __mulsi3), a2 acc,
# a3 array cursor, a4 lut base, a5 scratch, t0-t2 scratch/end.
# __mulsi3 on this repo's core: ~120 cycles measured (demo_pikoball_hid
# calibration, round-1 model), + jal/ret + arg staging counted inline.

harm_loop:
#=== region: per_harmonic repeat=1
    lhu  a5, 0(a3)          # phase
    lhu  t0, 2(a3)          # inc
    add  a5, a5, t0
    sh   a5, 0(a3)          # u16 store wraps for free
    srli a5, a5, 8          # LUT index
    slli a5, a5, 1
    add  a5, a5, a4
    lh   a0, 0(a5)          # sin sample
    lh   a1, 4(a3)          # amp
    lh   t0, 6(a3)          # damp
    add  a1, a1, t0
    sh   a1, 4(a3)
    jal  __mulsi3           #call=__mulsi3   amp * sin
    srai a0, a0, 15
    add  a2, a2, a0         # acc
    addi a3, a3, 8          # next harmonic record
    bne  a3, t2, harm_loop  #taken
#=== end
