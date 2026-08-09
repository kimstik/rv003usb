# RV32EC inner loop: order-10 SOS-CSD cascade, JIT-STRAIGHTLINE variant.
# ---------------------------------------------------------------------------
# Premise: coefficients change per subframe (LSP interpolation!), so shifts
# cannot be assembled into flash code.  This variant assumes a tiny code
# generator emits the section bodies below into SRAM once per subframe
# (~70 instructions; measured emit + re-CSD costs are counted separately in
# rt_cost.py).  This listing is what the GENERATED code looks like for a
# representative coefficient set (3-term CSD per coefficient) plus the fixed
# excitation / output / loop scaffolding around it.
#
# Register plan (RV32E, x0-x15; sp live for IRQs - rv003usb bit-bangs USB,
# gp/tp conservatively reserved):
#   t0,t1  s0_1,s1_1   t2,s0 s0_2,s1_2   s1,a0 s0_3,s1_3
#   a1,a2  s0_4,s1_4   a3,a4 s0_5,s1_5
#   a5     signal x / accumulator v
#   ra     shift scratch
#   stack: phase(Q32), inc, neg_dc, out_ptr, out_end, g_split
#
# Costing convention (= round-1 cost model): ALU/shift 1, load 2, store 2,
# taken branch 2, untaken 1.  Annotations: #notaken = branch usually falls
# through (costed 1), #rare = executed on impulse fire only (amortized
# separately, see rt_cost.py).

sample_loop:
#=== region: excitation repeat=1
    lw   ra, 0(sp)          # phase
    lw   a5, 4(sp)          # inc  (Q32 per-sample phase step)
    add  a5, ra, a5         # phase += inc (mod 2^32 free wrap)
    sltu ra, a5, ra         # carry <=> period boundary crossed
    sw   a5, 0(sp)
    bnez ra, fire           #notaken (taken <= once per pitch period)
after_fire:
    lw   a5, 8(sp)          # x = -dc  (zero-mean excitation)
#=== end
#=== region: section1 repeat=5
    # v = x - b1*s0 - b2*s1 ; 3-term CSD each, representative exponents
    srai ra, t0, 1          # b1 term 1: 2^-1
    sub  a5, a5, ra
    srai ra, t0, 4          # b1 term 2: 2^-4
    add  a5, a5, ra
    srai ra, t0, 7          # b1 term 3: 2^-7
    sub  a5, a5, ra
    srai ra, t1, 1          # b2 term 1
    sub  a5, a5, ra
    srai ra, t1, 3          # b2 term 2
    add  a5, a5, ra
    srai ra, t1, 6          # b2 term 3
    sub  a5, a5, ra
    mv   t1, t0             # s1 = s0
    mv   t0, a5             # s0 = v ; v flows to next section as x
#=== end
#=== region: output repeat=1
    lw   ra, 12(sp)         # out ptr
    sh   a5, 0(ra)
    addi ra, ra, 2
    sw   ra, 12(sp)
    lw   a5, 16(sp)         # out end
    bne  ra, a5, sample_loop  #taken (loop back 159/160)
#=== end

# --- impulse fire path, <= 1 per pitch period (amortized in rt_cost.py) ---
# split 2-tap impulse needs frac = phase_new * (1/inc):
# 1/inc precomputed per frame (1 soft-div/frame), so one soft-mul per PERIOD
# (~130 cyc) + ~12 cyc of adds; at F0<=400 Hz that is <=0.06 MHz.
fire:
#=== region: fire repeat=0
    j    after_fire
#=== end
