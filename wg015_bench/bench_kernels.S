/* bench_kernels.S — position-independent measurement kernels for the WG015
 * P1 calibration benches (PLAN.md §4 P1; redteam FL1/FL2/FL3/FL9/T5/T7).
 *
 * PIC contract (so every kernel can be byte-copied to TCM-A and run there):
 *   - internal branches only (PC-relative, copied together with the code);
 *   - data/MMIO references are register arguments or absolute %hi/%lo pairs
 *     (lui+addi is absolute, unlike la's auipc which would break on copy);
 *   - no calls out, end in ret/mret.
 * rdcycle is read ONCE at kernel start and ONCE at end (FL9: aggregate
 * only — CSR access drains the BM-310 pipeline, a per-op/per-slot rdcycle
 * would measure itself).
 *
 * Common kernel ABI (k_*): a0 = target address, a1 = outer reps (64
 * operations per rep), a2 = store value; returns rdcycle delta in a0.
 */

#include "bench_common.h"

	.text

/*---------------------------------------------------------------------------
 * k_base — 64x addi per rep: 1-cycle-IPC baseline (F3: RV32IMC = 1 cyc/instr)
 * and loop-overhead reference (2 loop instructions per 64 ops = +3%).
 *---------------------------------------------------------------------------*/
	.global k_base, k_base_end
	.balign 64
k_base:
	rdcycle t0
1:	.rept 64
	addi t2, t2, 1
	.endr
	addi a1, a1, -1
	bnez a1, 1b
	rdcycle t1
	sub  a0, t1, t0
	ret
k_base_end:

/*---------------------------------------------------------------------------
 * k_lw — 64x `lw t2, 0(a0)` per rep (bench1 GPIO DATA, bench6 MICC/INTSTATUS,
 * TCM and flash D-code references).
 *---------------------------------------------------------------------------*/
	.global k_lw, k_lw_end
	.balign 64
k_lw:
	rdcycle t0
1:	.rept 64
	lw   t2, 0(a0)
	.endr
	addi a1, a1, -1
	bnez a1, 1b
	rdcycle t1
	sub  a0, t1, t0
	ret
k_lw_end:

/*---------------------------------------------------------------------------
 * k_sw — 64x `sw a2, 0(a0)` per rep (bench1 DATAOUTTGL/MASKLB, bench6 MICC).
 *---------------------------------------------------------------------------*/
	.global k_sw, k_sw_end
	.balign 64
k_sw:
	rdcycle t0
1:	.rept 64
	sw   a2, 0(a0)
	.endr
	addi a1, a1, -1
	bnez a1, 1b
	rdcycle t1
	sub  a0, t1, t0
	ret
k_sw_end:

/*---------------------------------------------------------------------------
 * bench2 branch kernels.  `beq zero, zero, .` is never compressed (rs1=x0 is
 * outside the c.beqz register class), so every unit is a 4-byte branch with
 * an assembler-controlled target alignment:
 *   k_br_taken_al  — taken, target 4-byte aligned
 *   k_br_taken_mis — taken, target at 4n+2 (skipped c.nop before the label).
 *                    NOTE: the .balign filler c.nop AFTER each target IS
 *                    executed, so this kernel runs branch + one c.nop per
 *                    unit — subtract ~1.0 cycle before comparing with the
 *                    aligned row (also stated in the bench2 printout)
 *   k_br_untaken   — never taken, fall-through
 * The closing `bnez a1, 1b` adds one taken BACKWARD branch per 64 units.
 *---------------------------------------------------------------------------*/
	.global k_br_taken_al, k_br_taken_al_end
	.balign 64
k_br_taken_al:
	rdcycle t0
1:	.rept 64
	.balign 4
	beq  zero, zero, 2f
	.balign 4
2:
	.endr
	addi a1, a1, -1
	bnez a1, 1b
	rdcycle t1
	sub  a0, t1, t0
	ret
k_br_taken_al_end:

	.global k_br_taken_mis, k_br_taken_mis_end
	.balign 64
k_br_taken_mis:
	rdcycle t0
1:	.rept 64
	.balign 4
	beq  zero, zero, 2f
	.balign 4
	c.nop                     /* skipped: pushes the target to 4n+2 */
2:
	.endr
	addi a1, a1, -1
	bnez a1, 1b
	rdcycle t1
	sub  a0, t1, t0
	ret
k_br_taken_mis_end:

	.global k_br_untaken, k_br_untaken_end
	.balign 64
k_br_untaken:
	rdcycle t0
1:	.rept 64
	.balign 4
	bne  zero, zero, 2f       /* never taken */
2:
	.endr
	addi a1, a1, -1
	bnez a1, 1b
	rdcycle t1
	sub  a0, t1, t0
	ret
k_br_untaken_end:

/*---------------------------------------------------------------------------
 * bench4 fetch-profile kernels (no arguments, single pass, delta in a0).
 *
 * k_straight1k: 1024 compressed instructions (2 KB — the documented
 * prefetcher services this with hit=0ws once streaming; also exactly the
 * size of the undocumented core I-cache, research_bm310.md).
 * k_jump32: 64 jumps each over 32 B — every target leaves the 2x128-bit
 * prefetch window (miss cost per branch); .option norvc keeps each unit at
 * exactly 36 B so all targets are 4-byte aligned.
 *---------------------------------------------------------------------------*/
	.global k_straight1k, k_straight1k_end
	.balign 64
k_straight1k:
	rdcycle t0
	.rept 1024
	addi t2, t2, 1            /* compresses to 2-byte c.addi */
	.endr
	rdcycle t1
	sub  a0, t1, t0
	ret
k_straight1k_end:

	.global k_jump32, k_jump32_end
	.balign 64
k_jump32:
	.option push
	.option norvc
	rdcycle t0
	.rept 64
	.balign 4
	j    1f
	.skip 32
1:
	.endr
	rdcycle t1
	sub  a0, t1, t0
	ret
	.option pop
k_jump32_end:

/*---------------------------------------------------------------------------
 * evictor_4k — FL1 eviction adversary: >4 KB of unrelated straight-line
 * flash code (4416 B forced-uncompressed), overflows both the 2 KB I-cache
 * and the prefetch buffers.  Runs between emulated packets (bench5) and as
 * a cold-state generator elsewhere.  Flash-resident on purpose — never copy.
 *---------------------------------------------------------------------------*/
	.global evictor_4k
	.balign 64
evictor_4k:
	.option push
	.option norvc
	.rept 1104
	addi t0, t0, 1
	.endr
	.option pop
	ret

/*---------------------------------------------------------------------------
 * slot_packet — bench5: slot emulation ISOMORPHIC to the real RX/TX loop
 * (FL2): three slot bodies at real distances (entry-to-entry: A->B 128 B,
 * B->C 160 B, A->C 288 B; the real paths span ~300 B, rv003usb.S:298-424),
 * path chosen per-slot by PRBS9 data, a taken BACKWARD branch inside every
 * slot plus the backward dispatch branch closing each slot, an lbu byte
 * fetch in the TX-like body (the S:890 XW_C_LBU site), and a DATAOUTTGL
 * marker store opening every slot (PLAN Р10: per-slot times come from the
 * LA on B2, NOT from rdcycle — FL9).
 *
 * uint32_t slot_packet(uint32_t nslots      a0,
 *                      uint32_t *prbs_io    a1,   in/out 9-bit PRBS state
 *                      const uint8_t *buf   a2,   64-byte TCM byte buffer
 *                      uint32_t gpiob_base  a3)   marker port base
 * returns aggregate rdcycle delta for the whole packet (one CSR pair).
 *---------------------------------------------------------------------------*/
	.global slot_packet, slot_packet_end
	.balign 64
slot_packet:
	lw   t0, 0(a1)            /* PRBS state */
	li   t5, BENCH_MARKER_MASK
	mv   t6, zero             /* buffer index */
	mv   t4, zero             /* slot counter */
	rdcycle t3
slot_disp:
	/* PRBS9 step: bit = s[8]^s[4]; s = ((s<<1)|bit) & 0x1FF */
	srli t1, t0, 8
	srli t2, t0, 4
	xor  t1, t1, t2
	andi t1, t1, 1
	slli t0, t0, 1
	or   t0, t0, t1
	andi t0, t0, 0x1FF
	/* data-dependent path select: bit -> {A} / {B,C} */
	bnez t1, 1f
	j    slot_a
1:	andi t2, t0, 2
	bnez t2, 2f
	j    slot_b
2:	j    slot_c

	.balign 4
slot_a:                           /* RX zero-bit-like body */
	sw   t5, GPIO_DATAOUTTGL_OFFSET(a3)   /* slot marker (LA) */
	lw   t1, GPIO_DATA_OFFSET(a3)         /* USB_SAMPLE-like GPIO load */
	xor  t1, t1, t0
	li   t2, 2
3:	addi t2, t2, -1
	bnez t2, 3b                            /* taken backward branch */
	j    slot_next

	.skip slot_a + 128 - .        /* assembler-checked slot distance A->B */
slot_b:                           /* RX one-bit/stuff-like body */
	sw   t5, GPIO_DATAOUTTGL_OFFSET(a3)
	lw   t1, GPIO_DATA_OFFSET(a3)
	srli t1, t1, 1
	add  t1, t1, t0
	li   t2, 3
3:	addi t2, t2, -1
	bnez t2, 3b                            /* taken backward branch */
	j    slot_next

	.skip slot_a + 288 - .        /* slot distance A->C (B->C = 160) */
slot_c:                           /* TX-like body: descriptor byte fetch */
	sw   t5, GPIO_DATAOUTTGL_OFFSET(a3)
	add  t1, a2, t6
	lbu  t1, 0(t1)                         /* the S:890 lbu site */
	addi t6, t6, 1
	andi t6, t6, 63
	xor  t1, t1, t0
	li   t2, 2
3:	addi t2, t2, -1
	bnez t2, 3b                            /* taken backward branch */
	j    slot_next

	.skip slot_a + 352 - .
slot_next:
	addi t4, t4, 1
	bltu t4, a0, slot_disp        /* backward branch across the whole cluster */
	rdcycle t2
	sub  a0, t2, t3
	sw   t0, 0(a1)                /* PRBS state back to caller */
	ret
slot_packet_end:

/*---------------------------------------------------------------------------
 * bench3 IRQ-entry handlers (T5 gate).  mtvec (direct) points straight at
 * these; no dispatch layer so entry cost is undiluted.
 *
 * irq3_handler — self-contained ISR, absolute addressing throughout, hence
 * runnable in place (flash) or byte-copied to TCM-A:
 *   instr 1-3   stash a0/a1 on the stack
 *   instr 4-6   B2 marker toggle (LA sees the entry edge)   <- 6th instr
 *   instr 7     rdcycle capture -> bench_irq_cycle
 * then INTSTATUS W1C -> PLIC claim -> complete (USB_ISR_ACK order, Р2),
 * completion flag, restore, mret.
 *---------------------------------------------------------------------------*/
	.global irq3_handler, irq3_handler_end
	.balign 64
irq3_handler:
	addi sp, sp, -16
	sw   a0, 0(sp)
	sw   a1, 4(sp)
	lui  a0, %hi(GPIOB_BASE)
	li   a1, BENCH_MARKER_MASK
	sw   a1, GPIO_DATAOUTTGL_OFFSET(a0)   /* marker: 6th instruction */
	rdcycle a1                            /* 7th instruction */
	lui  a0, %hi(bench_irq_cycle)
	sw   a1, %lo(bench_irq_cycle)(a0)
	lui  a0, %hi(GPIOB_BASE)
	li   a1, BENCH_TRIG_MASK
	sw   a1, GPIO_INTSTATUS_OFFSET(a0)    /* W1C our pin first ... */
	lui  a0, %hi(PLIC_MICC_ADDR)
	lw   a1, %lo(PLIC_MICC_ADDR)(a0)      /* ... then claim         */
	sw   a1, %lo(PLIC_MICC_ADDR)(a0)      /* ... then complete      */
	lui  a0, %hi(bench_irq_flag)
	li   a1, 1
	sw   a1, %lo(bench_irq_flag)(a0)
	lw   a0, 0(sp)
	lw   a1, 4(sp)
	addi sp, sp, 16
	mret
irq3_handler_end:

/*---------------------------------------------------------------------------
 * irq3_stub — Р7/R2 fallback shape: head stub (stash, marker, rdcycle) meant
 * to live in TCM-A, then an ABSOLUTE far jump (lui+jr — flash and TCM are
 * 1 GB apart, PLAN z8) into the flash-resident tail.  Measures stub cost
 * plus the TCM->flash fetch transition.
 *---------------------------------------------------------------------------*/
	.global irq3_stub, irq3_stub_end
	.balign 64
irq3_stub:
	addi sp, sp, -16
	sw   a0, 0(sp)
	sw   a1, 4(sp)
	lui  a0, %hi(GPIOB_BASE)
	li   a1, BENCH_MARKER_MASK
	sw   a1, GPIO_DATAOUTTGL_OFFSET(a0)
	rdcycle a1
	lui  a0, %hi(bench_irq_cycle)
	sw   a1, %lo(bench_irq_cycle)(a0)
	lui  a0, %hi(irq3_tail)               /* z8-style far branch */
	addi a0, a0, %lo(irq3_tail)
	jr   a0
irq3_stub_end:

	.balign 4
irq3_tail:                                /* flash-resident tail */
	lui  a0, %hi(GPIOB_BASE)
	li   a1, BENCH_TRIG_MASK
	sw   a1, GPIO_INTSTATUS_OFFSET(a0)
	lui  a0, %hi(PLIC_MICC_ADDR)
	lw   a1, %lo(PLIC_MICC_ADDR)(a0)
	sw   a1, %lo(PLIC_MICC_ADDR)(a0)
	lui  a0, %hi(bench_irq_flag)
	li   a1, 1
	sw   a1, %lo(bench_irq_flag)(a0)
	lw   a0, 0(sp)
	lw   a1, 4(sp)
	addi sp, sp, 16
	mret

/*---------------------------------------------------------------------------
 * irq6_handler — bench6 back-to-back ISR (T7): stamps rdcycle at every
 * entry into b6_stamps[], and on every EVEN entry re-arms a fresh falling
 * edge on B3 *after* the W1C so a new request is pending while the full
 * exit tail (W1C -> claim -> complete -> restore -> mret) executes; the
 * core then re-enters immediately.  stamps[odd]-stamps[even] = post-rdcycle
 * handler remainder + full tail + trap re-entry (the ACK re-entry window).
 * B2 marker toggles at each entry so the LA can measure the same gap.
 *---------------------------------------------------------------------------*/
	.global irq6_handler
	.balign 64
irq6_handler:
	addi sp, sp, -32
	sw   a0, 0(sp)
	sw   a1, 4(sp)
	sw   a2, 8(sp)
	sw   a3, 12(sp)
	sw   a4, 16(sp)
	lui  a0, %hi(GPIOB_BASE)
	li   a1, BENCH_MARKER_MASK
	sw   a1, GPIO_DATAOUTTGL_OFFSET(a0)   /* entry marker (LA gap) */
	rdcycle a1
	lui  a2, %hi(b6_count)
	lw   a3, %lo(b6_count)(a2)            /* old count */
	lui  a4, %hi(b6_stamps)
	addi a4, a4, %lo(b6_stamps)
	slli a0, a3, 2
	add  a4, a4, a0
	sw   a1, 0(a4)                        /* b6_stamps[count] = rdcycle */
	addi a4, a3, 1
	sw   a4, %lo(b6_count)(a2)
	/* ---- full ISR tail starts here (USB_ISR_ACK order) ---- */
	lui  a0, %hi(GPIOB_BASE)
	li   a1, BENCH_TRIG_MASK
	sw   a1, GPIO_INTSTATUS_OFFSET(a0)    /* W1C the served request */
	li   a2, B6_MAX_EVENTS
	bgeu a4, a2, 3f                       /* cap reached -> mask pin  */
	andi a2, a3, 1
	bnez a2, 4f                           /* odd entry -> no re-arm   */
	sw   a1, GPIO_DATAOUTSET_OFFSET(a0)   /* B3 high again...         */
	nop                                   /* let the 2-clk input sync */
	nop                                   /* sample the high level    */
	nop
	nop
	nop
	nop
	sw   a1, GPIO_DATAOUTCLR_OFFSET(a0)   /* ...fresh falling edge:   */
	j    4f                               /* pending during our exit  */
3:	sw   a1, GPIO_INTENCLR_OFFSET(a0)
4:	lui  a0, %hi(PLIC_MICC_ADDR)
	lw   a1, %lo(PLIC_MICC_ADDR)(a0)      /* claim    */
	sw   a1, %lo(PLIC_MICC_ADDR)(a0)      /* complete */
	lw   a0, 0(sp)
	lw   a1, 4(sp)
	lw   a2, 8(sp)
	lw   a3, 12(sp)
	lw   a4, 16(sp)
	addi sp, sp, 32
	mret
