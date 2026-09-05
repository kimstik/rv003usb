/*
 * engine16_grainuum.S -- USB low-speed (1.5 Mbit/s) receive engine, exactly
 * 16 cycles per bit cell, Cortex-M0+ @ 24 MHz, RAM-resident.
 *
 * Written for the ENGINE16_SPEC.md competition (doc/py32/ENGINE16_SPEC.md).
 * Lineage: the ARM Cortex-M school (Grainuum / LemcUSB / stm32f030-vusb,
 * surveyed in doc/py32/PRIOR_ART.md) and this repo's own 32-cycle Thumb port
 * (rv003usb-arm.S, referred to below as "arm.S"). Every non-obvious claim is
 * cited by file:line or PRIOR_ART section; see engine16_grainuum.md for the
 * full cycle ledger, the Grainuum load-bearing-vs-artifact analysis, and what
 * this design gives up.
 *
 * Assembles standalone with:
 *   arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c \
 *       engine16_grainuum.S
 * It does not link (extern C-layer symbols named in comments are undefined
 * by design, per ENGINE16_SPEC.md §4: the C layer is shared and out of scope
 * for this file).
 *
 * ---------------------------------------------------------------------------
 * DESIGN IN ONE PARAGRAPH (full version in the .md)
 * ---------------------------------------------------------------------------
 * Grainuum's family (PRIOR_ART.md #1) proved the shape: a cycle-counted
 * Thumb PHY, IRQs masked, executing from RAM (PA S-4/D-2), pad staircases
 * closing every slot to an exact count (PA S-1). At 16 cycles/bit there is
 * no room left for in-slot CRC (arm.S:167-184's Domkeykong trick costs ~6
 * cycles by itself) or for two taken branches per bit (arm.S:162 zero/one
 * split + arm.S:198 loop-back). This design removes both: CRC is deferred
 * whole to the untimed tail, and the byte is fully unrolled at assemble time
 * so a byte boundary is a *place in the code*, not a runtime counter --
 * which also closes DEFECTS_VERIFIED D-2 (unbounded rxbuf store)
 * structurally rather than with a runtime bounds check (ENGINE16_SPEC.md §3
 * point 8). What remains per bit is one Thumb-1 idiom already present in
 * arm.S:160-164 (toggle PREV into NEW with a second EOR instead of a MOV)
 * plus the sample and the insert, and exactly one conditional branch to
 * split 0 from 1 -- no predication exists on this core to remove it
 * (doc/py32/M0PLUS_ISA_FACTS.md).
 */

	.syntax unified
	.thumb
	.cpu cortex-m0plus
	.altmacro

/* ===========================================================================
 * Configuration -- placeholders. Per ENGINE16_SPEC.md §4, "register
 * conventions, buffer layout, internal labels ... are all free"; a real
 * integration substitutes the board's actual pin numbers and GPIO instance
 * (PRIOR_ART.md §5.5: base 0x50000000 + 0x400*port, IDR at +0x10).
 * ========================================================================= */
	.equ	USB_PIN_DM,        0
	.equ	USB_PIN_DP,        1
	.equ	DMASK_VAL,         (1 << USB_PIN_DM) | (1 << USB_PIN_DP)
	.equ	IDR_OFFSET,        0x10
	.equ	GPIO_BASE_ADDR,    0x50000000	/* PRIOR_ART.md §5.5 */
	.equ	EXTI_BASE,         0x40021800	/* PRIOR_ART.md §5.5 */
	.equ	EXTI_PR_OFFSET,    0x0C

/* Structural buffer bound (ENGINE16_SPEC.md §3 point 8 / DEFECTS_VERIFIED
 * D-2): the engine is unrolled for exactly RXBUF_BYTES bytes. No instruction
 * anywhere in this file can address rxbuf16 outside [0, RXBUF_BYTES). This
 * demo unrolls 2 bytes (enough to show the PID byte and the byte-to-byte
 * hand-off, and to keep every branch a comfortable margin inside what a
 * Thumb-1 encoding can reach -- confirmed no wide Bcc exists on this core,
 * M0PLUS_ISA_FACTS.md). A production build repeats the identical pattern up
 * to RXBUF_BYTES=12 (PID + 8 data + 2 CRC16 + 1 margin, USB 2.0
 * §5.6.4/§8.6.5); engine16_grainuum.md says plainly that this is a mechanical
 * extrapolation the .S does not carry out, not a new idea being smuggled in.
 */
	.equ	RXBUF_BYTES,       2

	.section .bss.rxbuf16,"aw",%nobits
	.balign 4
rxbuf16:
	.space	RXBUF_BYTES

/* ===========================================================================
 * Register file for the timed engine -- all 8 low registers, none high.
 * Unlike arm.S, which had to push GPIO_BASE/pin-mask into r8/r9 and pay a
 * "mov r5,r9" shuffle every slot (arm.S:97-98, 155-156) because CRC needed
 * two more low registers (CRC, POLY_RX), deferring CRC frees exactly those,
 * which is what buys the direct 1-cycle IDR read below with no register
 * shuffle at all.
 *
 *   r0  PREV      last masked {D+,D-} sample (arm.S:160-164 update idiom)
 *   r1  RXBUFBASE address of rxbuf16, loaded once at entry
 *   r2  BASE      GPIO IDR base, loaded once at entry (low reg -> 1-cyc LDR)
 *   r3  MASK      DMASK_VAL, loaded once at entry
 *   r4  SHIFT     byte accumulator, built LSB-first (arm.S:150-198's shape)
 *   r5  SCRATCH   diff / temp, dead outside its own slot; also carries the
 *                 complete-byte count into the untimed tail
 *   r6  BITSTUFF  consecutive-ones counter, 6..0
 *   r7  EXTIBASE  EXTI peripheral base, loaded once at entry (untimed use
 *                 only -- spare during the bit loop)
 * ========================================================================= */

	.section .timecrit16,"ax",%progbits	/* RAM-resident: PA A-2/D-2/S-4 */
	.thumb_func
	.global	engine16_grainuum_isr
engine16_grainuum_isr:
/* -------------------------------------------------------------------------
 * Entry. PA L-9: the very first thing done must be capable of resolving to
 * "this was SE0" (a keepalive), matching arm.S's own discipline. Every load
 * below is a one-time cost, not part of the per-bit ledger -- accounted
 * separately in the .md's entry-latency section, which is where this
 * engine's distinctive contribution lives.
 * ---------------------------------------------------------------------- */
	ldr	r2, =GPIO_BASE_ADDR		@ one-time: 4 cyc (RAM-resident literal, ENGINE16_SPEC.md §2)
	ldr	r0, [r2, #IDR_OFFSET]		@ one-time: 1 cyc -- entry sample, raw
	push	{r4, r5, r6, r7, lr}		@ one-time: 2+4=6 cyc (RAM PUSH, 5 regs)
	movs	r3, #DMASK_VAL			@ one-time: 1
	ands	r0, r3				@ one-time: 1 -- masked entry sample
	ldr	r1, =rxbuf16			@ one-time: 4
	ldr	r7, =EXTI_BASE			@ one-time: 4 (untimed uses only)
	.ltorg					@ pin the three literals above here --
						@ this function is long, and Thumb-1
						@ PC-relative LDR only reaches +-1020 B
	movs	r6, #6				@ one-time: 1 -- BITSTUFF init
	movs	r4, #0				@ one-time: 1 -- SHIFT init

/* -------------------------------------------------------------------------
 * Phase lock. Mechanism is arm.S:60-77 (PA S-1's staircase for the fixed
 * delay, PA A-16/F9's bound on the edge-hunt): wait a fixed interval, then
 * spin sampling until the bus state differs from the entry sample -- this
 * locks onto the first real SYNC edge and is *insensitive* to how much
 * interrupt-entry jitter preceded it, which is the whole point (.md's
 * interrupt-latency section explains why that insensitivity now matters
 * twice as much as it did at 32 cycles/bit). The pre-delay constant below is
 * a first-order, NOT bench-verified, estimate; flagged rather than asserted,
 * per PRIOR_ART.md's own treatment of this exact number for the reference
 * engine (Q-6, Q-11).
 * ---------------------------------------------------------------------- */
	nop					@ pre-delay staircase, PA S-1 style,
	nop					@ inlined here (few call sites --
	nop					@ see .md "what we gave up" for why
	nop					@ this isn't the bl-staircase S-1
	nop					@ actually describes).
	nop
	nop
	nop
	nop
	nop
	nop
	nop
	nop
	nop
	nop
	nop
	movs	r5, #24				@ bounded spin budget, PA A-16/F9
preamble_spin:
	ldr	r6, [r2, #IDR_OFFSET]
	ands	r6, r3
	cmp	r6, r0
	bne	preamble_locked
	subs	r5, #1
	bne	preamble_spin
	b	too_long_abort			@ PA A-16: bounded, not infinite
preamble_locked:
	cmp	r6, #0
	bne	1f				@ not SE0 (common): skip the far jump
	b	too_long_abort			@ SE0 at the lock edge: not a packet
1:
	movs	r0, r6				@ r0 := PREV, the edge state
	movs	r6, #6				@ restore BITSTUFF (r6 was borrowed above)
	nop					@ post-lock staircase to the sample
	nop					@ offset -- see .md D-9-style analysis:
	nop					@ offset must be >= 7/16 of a cell
	nop					@ (dribble tolerance 260 ns = 6.24 cyc
	nop					@ @ 24 MHz, USB 2.0 §7.1.9/§7.1.14,
	nop					@ scaled from PA D-9's 14/32 @ 48 MHz).
	b	b1_s1

/* ===========================================================================
 * The bit-cell macros. Every distinct path in the required ledger (data 1,
 * data 0, stuffed bit, byte boundary, SE0) is generated by exactly one of
 * these three, invoked once per slot below -- so this source shows the
 * *pattern* once and the object file (checked with objdump/engine16_cyc.py)
 * shows every concrete instance the ledger claims.
 *
 * Long-range note: a conditional branch (beq/bne) on this core reaches only
 * +-256 B (Thumb-1 T1 encoding; no wide Bcc exists here, M0PLUS_ISA_FACTS.md)
 * but the rare SE0/violation exits can be many slots away from the shared
 * handler. Each such check therefore branches *conditionally* to a label a
 * few instructions further down in the SAME slot (always in range), which
 * then does an *unconditional* B to the shared handler (Thumb-1 B reaches
 * +-2048 B, comfortably covering this file). This costs nothing on the
 * common path: the conditional branch is exactly as cheap not-taken as a
 * direct branch to a far label would have been if it could reach.
 * ========================================================================= */

	.macro SLOT bidx, s, nextlbl
b\bidx\()_s\s:
	ldr	r5, [r2, #IDR_OFFSET]		@ 1
	ands	r5, r3				@ 1   =2
	eors	r5, r0				@ 1   =3   r5 := diff
	beq	b\bidx\()_s\s\()_one		@ 1/2-3 =4 (not taken: bit=0)
	eors	r0, r5				@ 1       =5   r0 := new masked state
	beq	9f				@ 1        =6  (not taken; SE0 relay)
	lsrs	r4, r4, #1			@ 1        =7
	movs	r6, #6				@ 1        =8
	nop					@ 1        =9   (pad to 16: .md row
	nop					@ 1        =10   "data 0, interior";
	nop					@ 1        =11   13 + 2-3 taken close
	nop					@ 1        =12   = 15..16)
	nop					@ 1        =13
	b	\nextlbl			@ 2-3      =15..16 (taken, skip-over)
9:
	movs	r5, #(\bidx - 1)		@ untimed from here: SE0, byte \bidx incomplete
	b	se0_common
b\bidx\()_s\s\()_one:
	lsrs	r4, r4, #1			@ 1        =7  (from beq-taken=2-3, worst 4+3=7)
	adds	r4, #0x80			@ 1        =8
	subs	r6, #1				@ 1        =9
	beq	stuff_b\bidx\()_s\s		@ 1/2-3    =10 (not taken; stuff cell
						@            is interleaved right after
						@            this slot, always in range)
	nop					@ 1        =11 \ pad to 16: .md row
	nop					@ 1        =12 | "data 1, interior"
	nop					@ 1        =13 |
	b	\nextlbl			@ 2-3      =15..16 (taken, closes the cell;
						@ see .md for why this differs from the
						@ first draft's free fall-through)
	.endm

	.macro SLOT_LAST bidx, s, nextlbl, off
b\bidx\()_s\s:
	ldr	r5, [r2, #IDR_OFFSET]		@ 1
	ands	r5, r3				@ 1   =2
	eors	r5, r0				@ 1   =3
	beq	b\bidx\()_s\s\()_one		@ 1   =4  (not taken)
	eors	r0, r5				@ 1   =5
	beq	9f				@ 1   =6  (not taken; SE0 relay)
	lsrs	r4, r4, #1			@ 1   =7
	movs	r6, #6				@ 1   =8
	strb	r4, [r1, #\off]			@ 2   =10  store byte \off
	nop					@ 1   =11
	nop					@ 1   =12
	nop					@ 1   =13
	b	\nextlbl			@ 2-3 =15..16
9:
	movs	r5, #(\bidx - 1)		@ untimed: SE0, byte \bidx incomplete
	b	se0_common
b\bidx\()_s\s\()_one:
	lsrs	r4, r4, #1			@ 1   =7 (from beq-taken=2-3, worst 4+3=7)
	adds	r4, #0x80			@ 1   =8
	strb	r4, [r1, #\off]			@ 2   =10  store byte \off
	subs	r6, #1				@ 1   =11
	beq	stuff_b\bidx\()_s\s		@ 1   =12  (not taken)
	nop					@ 1   =13
	b	\nextlbl			@ 2-3 =15..16
	.endm

	.macro STUFF_CELL bidx, s, nextlbl, bytecount
stuff_b\bidx\()_s\s:
	ldr	r5, [r2, #IDR_OFFSET]		@ 1  sample the stuff-bit cell
	ands	r5, r3				@ 1   =2
	eors	r5, r0				@ 1   =3
	beq	8f				@ 1   =4  (not taken: a stuffed bit
						@          MUST be a transition,
						@          PA L-7/F3; taken = abort)
	eors	r0, r5				@ 1   =5
	beq	9f				@ 1   =6  (not taken; SE0 relay)
	movs	r6, #6				@ 1   =7  stuffing consumed, reset
	nop					@ 1   =8
	nop					@ 1   =9
	nop					@ 1   =10
	nop					@ 1   =11
	nop					@ 1   =12
	nop					@ 1   =13
	b	\nextlbl			@ 2-3 =15..16
9:
	movs	r5, #\bytecount
	b	se0_common
8:
	b	stuffing_violation
	.endm

/* ===========================================================================
 * BYTE 1 (PID). Bits 1-7 interior; bit 8 stores and hands off to byte 2.
 * ========================================================================= */
	@ Each slot's stuff cell is interleaved immediately after it (not
	@ grouped at the end): a stuffed bit is detected by a *conditional*
	@ branch (beq, +-256 B range, ENGINE16_SPEC.md §2 / M0PLUS_ISA_FACTS.md)
	@ and this keeps that branch's target always a few instructions away,
	@ regardless of which byte or slot it is in -- discovered by actually
	@ assembling the first draft (which grouped all stuff cells at the file
	@ end) and having the assembler refuse it, exactly the check
	@ ENGINE16_SPEC.md §5.2 asks for.
	SLOT       1, 1, b1_s2
	STUFF_CELL 1, 1, b1_s2, 0
	SLOT       1, 2, b1_s3
	STUFF_CELL 1, 2, b1_s3, 0
	SLOT       1, 3, b1_s4
	STUFF_CELL 1, 3, b1_s4, 0
	SLOT       1, 4, b1_s5
	STUFF_CELL 1, 4, b1_s5, 0
	SLOT       1, 5, b1_s6
	STUFF_CELL 1, 5, b1_s6, 0
	SLOT       1, 6, b1_s7
	STUFF_CELL 1, 6, b1_s7, 0
	SLOT       1, 7, b1_s8
	STUFF_CELL 1, 7, b1_s8, 0
	SLOT_LAST  1, 8, b2_s1, 0
	STUFF_CELL 1, 8, b2_s1, 1		@ byte 1 already stored -> count=1

/* ===========================================================================
 * BYTE 2. Identical shape; only the store offset (#1) and the hand-off
 * target (too_long_abort, since RXBUF_BYTES=2 in this demo) differ. A
 * production build repeats this block up to byte RXBUF_BYTES, changing only
 * those two things -- see .md for why that repetition is mechanical.
 * ========================================================================= */
	SLOT       2, 1, b2_s2
	STUFF_CELL 2, 1, b2_s2, 1
	SLOT       2, 2, b2_s3
	STUFF_CELL 2, 2, b2_s3, 1
	SLOT       2, 3, b2_s4
	STUFF_CELL 2, 3, b2_s4, 1
	SLOT       2, 4, b2_s5
	STUFF_CELL 2, 4, b2_s5, 1
	SLOT       2, 5, b2_s6
	STUFF_CELL 2, 5, b2_s6, 1
	SLOT       2, 6, b2_s7
	STUFF_CELL 2, 6, b2_s7, 1
	SLOT       2, 7, b2_s8
	STUFF_CELL 2, 7, b2_s8, 1
	SLOT_LAST  2, 8, too_long_abort, 1
	STUFF_CELL 2, 8, too_long_abort, 2	@ byte 2 already stored -> count=2

/* ===========================================================================
 * Untimed tail. r5 holds the count of complete bytes in rxbuf16. Kept in the
 * same RAM section as the timed engine (PA A-2 forbids timing-critical code
 * in flash, not *all* code; a real build would move this to flash to save
 * RAM once it is more than a sketch -- see .md). CRC is computed here, not
 * in the bit loop -- see the .md's "CRC: deferred" section for the
 * turnaround-budget arithmetic this choice has to meet (PA S-2,
 * ENGINE16_SPEC.md §3 point 7, PRIOR_ART L-1).
 * ========================================================================= */
	.thumb_func
se0_common:
	@ Acknowledge the EXTI line (mirrors arm.S:334-336). r7 = EXTI_BASE,
	@ loaded once at entry -- no literal load needed this deep in the file.
	movs	r6, #(1 << USB_PIN_DM)
	str	r6, [r7, #EXTI_PR_OFFSET]
	@ A too-short packet (r5 < 1, i.e. no PID byte) is dropped here, not
	@ corrected -- matching arm.S's "CRC is the backstop" philosophy but
	@ with an explicit length gate first (structural, like the write-side
	@ bound; PA A-4/F2's point applied to length).
	cmp	r5, #1
	blo	rx_done
	@ CRC5 (tokens) / CRC16 (data) over rxbuf16[1 .. r5-1], polynomial
	@ chosen from the PID byte exactly as arm.S:128-140 / rv003usb.S:318-329
	@ do it; sketched, not reproduced, because it is untimed and this
	@ file's job is the timed engine (ENGINE16_SPEC.md §3: TX/other may be
	@ sketched). See .md for the byte-count budget this sketch must fit.
	@   ... crc = crc_over_buffer(rxbuf16, r5) ...
	@ Dispatch on rxbuf16[0] (PID), call the shared C layer exactly as
	@ arm.S:236-328 does -- ENGINE16_SPEC.md §4: that layer does not change.
	@   ... usb_pid_handle_{setup,out,in,data,ack}(..., &rv003usb_internal_data) ...
	.extern	usb_pid_handle_data
	.extern	usb_pid_handle_setup
	.extern	usb_pid_handle_out
	.extern	usb_pid_handle_in
	.extern	usb_pid_handle_ack
	.extern	rv003usb_internal_data
rx_done:
	pop	{r4, r5, r6, r7, pc}

	.thumb_func
too_long_abort:
	@ PA A-16/F9: bounded preamble spin, or the structural byte-count
	@ ceiling, expired without a legal EOP. Drop the frame; the host will
	@ retry (USB 2.0's own error-recovery model; PA §4 D-6's reasoning:
	@ nothing device-side needs to happen here beyond staying alive).
	movs	r6, #(1 << USB_PIN_DM)
	str	r6, [r7, #EXTI_PR_OFFSET]
	pop	{r4, r5, r6, r7, pc}

	.thumb_func
stuffing_violation:
	@ PA L-7: a receiver must reject a stuffing violation. Same drop path.
	b	too_long_abort

/* ===========================================================================
 * TX sketch (ENGINE16_SPEC.md §3: "may be sketched rather than finished").
 * Not costed in detail here -- see the .md's TX section for why this design
 * spends its budget on RX, and what a 16-cycle TX bit would need to borrow
 * from arm.S:411-533 (bit stuffing on send is a *known* count, since the
 * sender controls it, so a taken branch there is fully static at assemble
 * time and does not carry the RX side's 2-vs-3 uncertainty at all).
 * ========================================================================= */
	.thumb_func
	.global	engine16_grainuum_tx_sketch
engine16_grainuum_tx_sketch:
	bx	lr
