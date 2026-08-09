# RV32EC inner loop: order-10 SOS-CSD cascade, DATA-DRIVEN variant.
# ---------------------------------------------------------------------------
# No JIT: coefficients arrive per subframe as a packed CSD term stream,
# 1 byte per term: bit7 = sign, bits4:0 = right-shift amount.  Fixed layout
# 3 terms per coefficient, 2 coefficients per section, 5 sections =
# 30 bytes per coefficient set.  The inner loop interprets the stream.
# This is what plain C compiles to (+/- scheduling); no SRAM code buffer.
#
# Register plan: same state allocation as the JIT variant
# (t0..a4 = 10 states, a5 = x/v, ra = scratch) PLUS the term-stream pointer
# must live in a register -> one section state pair is evicted to the stack
# (section 5 states: lh/sh per sample, +8 cycles, included below).
#   s1..a2 states 1..4 (8 regs), a3 = term ptr, a4 = term scratch,
#   a5 = x/v, ra = shift scratch, stack: sect5 state + scaffolding.
#
# Per-term interpretation (branchless sign via mask):
#   lbu  a4, 0(a3)      2   term byte
#   addi a3, a3, 1      1
#   andi ra, a4, 31     1   shift amount
#   sra  ra, t0, ra     1   state >> sh   (SIGNED state, arithmetic)
#   srai a4, a4, 7      1   mask = 0 / -1 from sign bit  (byte sign-extended)
#   xor  ra, ra, a4     1
#   sub  ra, ra, a4     1   conditional negate
#   add  a5, a5, ra     1   accumulate
#                       = 9 cycles/term, 6 terms/section

sample_loop:
#=== region: excitation repeat=1
    lw   ra, 0(sp)
    lw   a5, 4(sp)
    add  a5, ra, a5
    sltu ra, a5, ra
    sw   a5, 0(sp)
    bnez ra, fire           #notaken
after_fire:
    lw   a5, 8(sp)          # x = -dc
    lw   a3, 20(sp)         # term stream base (reset each sample)
#=== end
#=== region: term repeat=30
    lbu  a4, 0(a3)
    addi a3, a3, 1
    andi ra, a4, 31
    sra  ra, t0, ra         # (state register varies per section)
    srai a4, a4, 7
    xor  ra, ra, a4
    sub  ra, ra, a4
    add  a5, a5, ra
#=== end
#=== region: state_shuffle repeat=4
    mv   t1, t0             # s1 = s0   (sections 1..4, in-register)
    mv   t0, a5             # s0 = v
#=== end
#=== region: state_sect5_mem repeat=1
    lh   ra, 24(sp)         # section 5 state pair via stack
    sh   ra, 26(sp)
    sh   a5, 24(sp)
#=== end
#=== region: output repeat=1
    lw   ra, 12(sp)
    sh   a5, 0(ra)
    addi ra, ra, 2
    sw   ra, 12(sp)
    lw   a5, 16(sp)
    bne  ra, a5, sample_loop  #taken
#=== end

fire:
#=== region: fire repeat=0
    j    after_fire
#=== end
