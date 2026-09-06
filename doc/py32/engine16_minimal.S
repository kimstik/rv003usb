/*
 * engine16_minimal.S - what V-USB's receive engine becomes on this core.
 *
 * This file exists to answer one question with an object instead of an
 * argument: if we made V-USB's choices instead of ours, how big would the
 * receive engine be?  It is therefore NOT a proposal.  It is a measurement
 * apparatus, and it deliberately gives up things engine16_merged.S has.
 *
 * V-USB's choices, taken deliberately (usbdrvasm12.inc / usbdrvasm165.inc):
 *   - NO receive CRC.  usbdrv.c:580-586 says so in their own words: "We
 *     could check CRC16 here -- but ACK has already been sent anyway."
 *     So this engine cannot refuse to ACK a corrupted packet.  That is the
 *     contract, not an oversight.
 *   - NO table.  Not one byte of data.
 *   - NRZI by XOR against the previous raw sample, exactly `eor x1,x2`.
 *   - bit stuffing detected from the FLAGS on a shift register, with no
 *     counter and no state variable, exactly `andi shift,0xf9 / breq`.
 *   - unrolled by byte, eight inline bit slots plus eight out-of-line
 *     unstuff escapes, exactly rxLoop / unstuff0..7.
 *   - SE0 tested once per byte (V-USB tests it in the bit-1 slot only), so
 *     the last, partial byte of a packet is garbage and is dropped by the
 *     byte count, exactly as `se0:` in asmcommon.inc does.
 *   - device-address filtering in the dispatch, not in the engine, exactly
 *     `cpse x2,shift / rjmp ignorePacket` (asmcommon.inc:65-70).  V-USB DOES
 *     filter by address; the claim that only we do was wrong and is
 *     corrected in SIZE_COMPARISON.md.
 *
 * What is kept from our side, and why:
 *   - the structural buffer bound (BALANCE / DEFECTS_VERIFIED D-2): the
 *     store is `strb rV,[rBASE,rIDX]` with rIDX masked to 5 bits, so no
 *     input can make it address outside a 32-byte window.  It costs two
 *     instructions per byte (lsls/lsrs) and both land in slack that would
 *     otherwise be nop.  IT IS FREE HERE.  V-USB's equivalent is `st Y+`
 *     plus `subi cnt,1 / brcs overflow` - one instruction for the store,
 *     because AVR has post-increment addressing, and two for a bound that
 *     is a runtime check rather than a property of the address arithmetic.
 *   - the phase lock from engine16_merged.S.  V-USB's sync detector solves
 *     the same problem the same way (catch the LAST SYNC edge); reusing
 *     ours keeps the size delta attributable to the ENGINE and not to the
 *     entry sequence.
 *
 * The bit cell, and the whole point of the file
 * ---------------------------------------------
 * V-USB's per-bit slot is SIX instructions (usbdrvasm12.inc:175-181):
 *     in x1,USBIN / andi shift,MASK / breq unstuffN / eor x2,x1 /
 *     bst x2,USBMINUS / bld shift,n
 * Ours is NINE, and the three extra are named:
 *   +1  no `bst`/`bld`.  AVR moves one bit from any position to any position
 *       in two instructions through the T flag.  ARMv6-M has no single-bit
 *       transfer, so extract-and-deposit is ands + shift + orrs = three.
 *   +2  no AND-immediate.  V-USB's stuffing test costs nothing extra because
 *       `andi shift,0xf9` masks the DATA register in place - the same
 *       register that holds the byte - with a different immediate per bit
 *       position.  Thumb-1 has no AND-immediate, so an in-place rotating
 *       window would need eight mask constants in eight registers we do not
 *       have.  We therefore keep the stuffing history in a SECOND register,
 *       as a plain left-shift register, where the six most recent bits are
 *       always bits 5..0 and the test is one shift with the SAME immediate
 *       at every bit position.  Filling it costs lsrs + adcs and testing it
 *       costs lsls: four instructions where V-USB spends two.
 * The 32-bit register is what makes that second register work at all - a
 * contiguous six-bit window that never wraps exists only because the
 * register is wider than the byte.  It buys the position-independent test,
 * and with it the loss of V-USB's only reason to unroll.  It does not buy
 * back the two instructions.  Net: +3 instructions per bit, +6 bytes per bit
 * slot, 18 bytes per bit against V-USB's 12.
 *
 * Padding is a first-class cost here and is counted separately in
 * SIZE_COMPARISON.md.  A cell is 16 cycles and the work is 9, so seven
 * cycles per cell are nop unless byte work fills them; an unstuff escape
 * must burn a whole 16-cycle bit time and has almost nothing to do in it.
 * V-USB's cells are 8 cycles (12 MHz) or 11 (16.5 MHz), so its padding is a
 * fraction of ours.  That is a consequence of our CLOCK, not of our word
 * width, and the doc says so.
 *
 * Known and deliberate: the unstuff escape is a data-dependent TAKEN
 * conditional branch inside the timed cell.  On AVR `breq` taken is exactly
 * 2 cycles; on this core a taken branch is 2-3 (ENGINE16_SPEC.md S2) and the
 * source does not say which.  The accounting below assumes 2.  Every stuffed
 * bit therefore admits up to one cycle of phase error and a maximal DATA0
 * packet can carry about twelve of them.  This engine is a measurement of
 * SIZE.  It is not timing-safe on this core, and that fact - not the byte
 * count - is the strongest argument for the table engine16_merged.S carries.
 */

	.syntax unified
	.cpu    cortex-m0plus
	.thumb

#ifndef USB_GPIO_BASE
#define USB_GPIO_BASE   0x50000400
#endif
#ifndef USB_IDR_OFS
#define USB_IDR_OFS     0x10
#endif
#ifndef USB_DP_BIT
#define USB_DP_BIT      3
#endif
#ifndef USB_DM_BIT
#define USB_DM_BIT      4
#endif
#define USB_PINMASK     ((1 << USB_DP_BIT) | (1 << USB_DM_BIT))

#ifndef USB_EXTI_BASE
#define USB_EXTI_BASE   0x40021800
#endif
#ifndef USB_EXTI_PR_OFS
#define USB_EXTI_PR_OFS 0x0C
#endif

#define USB_RXBUF_LEN   32		/* power of two: the bound IS the mask */
#define USB_RXBUF_SHIFT 27		/* 32 - log2(32)                       */
#define USB_BYTE_LIMIT  24		/* longest legal LS packet is 12 bytes */

/* Register contract (V-USB's, transposed)
 *   r0   byte accumulator.  Bit n is 1 if the line CHANGED at bit n, so the
 *        decoded data bit is its complement - the same convention as
 *        V-USB's `shift`, which is why the store does `mvns` first.
 *   r1   \ the two alternating raw port samples.  After a cell, the one
 *   r2   / just loaded is the "previous" for the next cell.  V-USB's x1/x2.
 *   r3   scratch.  Dead across every cell boundary.
 *   r4   stuffing history: plain left-shift register, 1 = changed.  Six
 *        consecutive zeros in bits 5..0 means six consecutive data ONES,
 *        which is where the stuffed bit is.
 *   r5   receive buffer index, masked to 5 bits every byte.
 *   r6   1 << USB_DM_BIT
 *   r7   GPIO base
 *   r8   receive buffer base       r9  remaining byte budget
 *   r14  chain head, bit 0 set, for a 3-cycle-flat BX back edge
 */

/* ------------------------------------------------------- cycle accounting
 * Same discipline as engine16_merged.S: the pad is assembled, not asserted,
 * and a cell that runs over is a build failure.  MCYC counts cycles since
 * the cell's sampling LDR. */
	.set    MCYC, 16

	.macro  MPAD			/* close the open cell at exactly 16 */
	.if     MCYC > 16
	.error  "engine16_minimal: bit cell over 16 cycles"
	.endif
	.rept   16 - MCYC
	nop
	.endr
	.set    MCYC, 16
	.endm

/* One bit slot.  \n is the bit position, \cur the register the sample lands
 * in, \prv the one holding the previous sample, which this cell consumes. */
	.macro  MBIT n, cur, prv, first=0
	MPAD
	.set    MCYC, 0
	.global usb_rx_min_cell\n	/* so tools/engine16_cyc.py can cut  */
usb_rx_min_cell\n:			/* the stream into cells             */
.Lbit\n:
	ldr     \cur, [r7, #USB_IDR_OFS] /* 1  sample - IOPORT, 1 cycle      */
	eors    \prv, \cur		/* 1  NRZI: differ = data 0          */
	ands    \prv, r6		/* 1  isolate D- (no bst on ARMv6-M) */
	.if     \n < USB_DM_BIT
	lsrs    r3, \prv, #(USB_DM_BIT - \n)	/* 1  align to bit \n        */
	.elseif \n > USB_DM_BIT
	lsls    r3, \prv, #(\n - USB_DM_BIT)	/* 1  align to bit \n        */
	.else
	movs    r3, \prv		/* 1  already at bit \n              */
	.endif
	.if     \first
	movs    r0, r3			/* 1  bit 0 STARTS the byte: no OR,  */
	.else				/*    which is what pays for the     */
	orrs    r0, r3			/* 1  accumulator clear              */
	.endif
	lsrs    \prv, #(USB_DM_BIT + 1)	/* 1  C = the bit; \prv now dead     */
	adcs    r4, r4			/* 1  push it into the history       */
	lsls    r3, r4, #26		/* 1  six most recent -> Z if all 0  */
	beq     .Lunstuff\n		/* 1 not taken / 2-3 taken           */
.Ldid\n:				/* both paths converge here, at 9    */
	.set    MCYC, 9
	.endm

	.macro  MUSE n			/* declare \n cycles of byte work    */
	.set    MCYC, MCYC + (\n)
	.endm

/* The out-of-line unstuff escape, one per bit position - V-USB's unstuff0..7.
 * A stuffed bit occupies a whole bit cell that carries no data, so the
 * handler samples it (the sample becomes the next cell's "previous", which
 * is the only reason to take it at all), breaks the run of ones in the
 * history register, and rejoins.  The byte accumulator is not touched: with
 * the history in its own register there is nothing to corrupt and nothing to
 * repair, so V-USB's `andi x3,~0x80 / ori shift,0x80` fixup pair and its
 * `eor x3,shift / ser x3` per-byte repair disappear.  That is the one place
 * the second register gives bytes back.
 *
 * Timing, all offsets from this cell's sampling LDR: beq at 8 is taken (2,
 * assumed - see the header), so the handler starts at 10.  The stuffed bit
 * is sampled at 16, and the escape must rejoin the common path at .Ldid\n
 * exactly one bit time after the normal path reaches it, i.e. at 25. */
	.macro  MUNSTUFF n, cur
	.global usb_rx_min_unstuff\n	/* measurable region, see the .md    */
usb_rx_min_unstuff\n:
.Lunstuff\n:
	adds    r4, #1			/* 10  break the run.  Bit 0 of r4   */
					/*     is 0 - that is why we are here */
	.rept   5			/* 11..15                            */
	nop
	.endr
	ldr     \cur, [r7, #USB_IDR_OFS] /* 16  the stuffed bit: sampled     */
					/*     only to become the next NRZI   */
					/*     reference, never decoded       */
	.rept   6			/* 17..22                            */
	nop
	.endr
	b       .Ldid\n			/* 23, +2 -> converge at 25          */
	.endm

/* ======================================================================= */
	.section .datacode, "ax", %progbits
	.balign 4
	.global usb_rx_minimal
	.thumb_func
	.type   usb_rx_minimal, %function

/* Entry.  SE0 first, before any register save, so a keep-alive costs almost
 * nothing.  engine16_merged.S's F-1 discipline holds: EXTI->PR is
 * acknowledged on EVERY exit, or the level-latched flag re-enters the
 * handler forever. */
usb_rx_minimal:
	ldr     r3, =USB_GPIO_BASE
	ldr     r0, [r3, #USB_IDR_OFS]
	movs    r1, #USB_PINMASK
	ands    r0, r1
	bne     1f
	ldr     r0, =USB_EXTI_BASE	/* keep-alive EOP: ack and leave     */
	movs    r1, #(1 << USB_DM_BIT)
	str     r1, [r0, #USB_EXTI_PR_OFS]
	bx      lr
	.ltorg
1:
	push    {r4-r7, lr}
	mov     r4, r8
	mov     r5, r9
	push    {r4, r5}

	mov     r7, r3			/* r7 = GPIO base                    */
	movs    r6, #(1 << USB_DM_BIT)	/* r6 = D- mask                      */
	ldr     r3, =(usb_rxbuf_min + 3) /* PID at +3, payload at +4:        */
	mov     r8, r3			/* 4-aligned for the C layer         */
	movs    r3, #USB_BYTE_LIMIT
	mov     r9, r3
	ldr     r3, =(.Lbit0 + 1)	/* chain head, bit 0 set, for BX     */
	mov     r14, r3
	movs    r1, #200		/* bounded spin (PRIOR_ART A-16)     */
	b       .Lhunt
.Lgiveup:				/* in B<cond> range of the hunt      */
	b       .Ldone

/* Four of the eight unstuff escapes sit ahead of the chain and four behind
 * it, because a Thumb-1 conditional branch reaches +-254 bytes and the chain
 * is longer than that.  engine16_merged.S splits its EOP stubs the same way
 * and for the same reason. */
	MUNSTUFF 0, r1
	MUNSTUFF 1, r2
	MUNSTUFF 2, r1
	MUNSTUFF 3, r2

/* Two trampolines, for the same +-254 reason: the SE0 exit and the packet
 * bound both leave the chain from cells 0 and 1, and their handlers are past
 * the far end of it.  Nothing falls through into these - MUNSTUFF 3 ends in
 * an unconditional branch. */
.Lse0_tramp:
	b       .Lse0
.Lovf_tramp:
	b       .Ldone

/* ---- phase lock (engine16_merged.S, unchanged) --------------------------
 * Lock to the LAST SYNC edge: SYNC is KJKJKJKK, so every K is followed by a
 * J one bit later EXCEPT cell 6, which is followed by another K.  Catch a
 * J->K edge, wait into the middle of the next cell, and if the bus is still
 * K then that edge was cell 6.  V-USB's mechanism (usbdrvasm165.inc
 * waitForJ / waitForK / foundK) and ours agree here, which is exactly why
 * reusing it keeps the size delta attributable to the ENGINE. */
	.global usb_rx_min_sync
usb_rx_min_sync:
.Lhunt:
.Lwait_j:
	subs    r1, #1
	beq     .Lgiveup
	ldr     r0, [r7, #USB_IDR_OFS]
	lsls    r0, r0, #(31 - USB_DM_BIT)
	bpl     .Lwait_j		/* D- low = K: keep waiting          */
.Lwait_k:
	subs    r1, #1
	beq     .Lgiveup
	ldr     r0, [r7, #USB_IDR_OFS]
	lsls    r0, r0, #(31 - USB_DM_BIT)
	bmi     .Lwait_k		/* D- high = J: keep waiting         */
.Lk_edge:
	.rept   20
	nop
	.endr
	ldr     r0, [r7, #USB_IDR_OFS]	/* t0 + 23.5: middle of the NEXT cell */
	lsls    r0, r0, #(31 - USB_DM_BIT)
	bpl     .Lhunt			/* J -> that was not the last edge   */

/* Confirmed.  The first PID cell's centre is 16 cycles after the confirming
 * sample; ldr + lsls + untaken bpl is 3, so the priming below must be 14
 * cycles, exactly as in engine16_merged.S.
 *
 * What is primed, and why these values:
 *   r2 = the last SYNC sample.  SYNC ends KK, so the bus is K: D+ high,
 *        D- low.  This is cell 0's NRZI reference.
 *   r4 = the stuffing history at that instant.  SYNC is KJKJKJKK, whose
 *        change sequence is 1111110 - six ones then a zero - so the low six
 *        bits are 111110 and cannot fire the escape on the first data bit.
 *        Priming it all-zero would fire it immediately.
 *   r5 = 0, the first store index. */
	.balign 4
.Lprime:
	movs    r2, #(1 << USB_DP_BIT)	/* 1  last SYNC sample: K            */
	movs    r4, #0x7e		/* 1  ...1111110                     */
	movs    r5, #(USB_RXBUF_LEN - 1) /* 1  index: cell 0 pre-increments  */
					/*    and masks, so this is -1       */
	movs    r0, #0			/* 1  accumulator (cell 0 assigns)   */
	movs    r3, #0			/* 1                                 */
	.rept   9
	nop				/* 1  phase padding: 5 + 9 = 14      */
	.endr
	.if     (. - .Lprime) != 28
	.error  "engine16_minimal: phase-lock priming is not 14 cycles"
	.endif
	.set    MCYC, 16		/* no cell open; MBIT 0 opens one    */

/* ===================== the timed chain: 8 cells x 16 ====================
 * Nine cycles of bit work per cell (eight in cell 4, where the D- pin and
 * the bit position coincide and the alignment shift disappears), and the
 * byte work is packed into cells 0, 1 and 7 where it exactly fills the
 * slack.  Everything else is nop, and that is the honest picture: 16 cycles
 * per bit is more than V-USB's structure needs even after the ARMv6-M tax.
 *
 * V-USB spends its slack on per-byte jobs too, and in the same places:
 *   bit 0  store            -> ours: cell 7 (we store the byte we just
 *                              finished, not the previous one)
 *   bit 1  SE0 check        -> ours: cell 1, identically
 *   bit 2  overflow check   -> ours: cell 0, with the buffer index masked
 *                              instead of the pointer checked
 *   bit 7  loop control     -> ours: cell 7, BX (3 cycles flat; a B would
 *                              be 2-3 and this edge is taken every byte) */
	MBIT    0, r1, r2, 1
	adds    r5, #1			/* 1  next slot                      */
	lsls    r5, #USB_RXBUF_SHIFT	/* 1  \ THE STRUCTURAL BOUND: index  */
	lsrs    r5, #USB_RXBUF_SHIFT	/* 1  / is 0..31, always.  Free here */
	mov     r3, r9			/* 1  \                              */
	subs    r3, #1			/* 1   | packet bound: the chain is a */
	mov     r9, r3			/* 1   | loop and needs a terminator  */
	beq     .Lovf_tramp		/* 1  /                              */
	MUSE    7

	MBIT    1, r2, r1
	lsls    r3, r2, #(31 - USB_DM_BIT) /* 1  D- -> 31, D+ -> 30          */
	lsrs    r3, #30			/* 1  keep just those two            */
	beq     .Lse0_tramp		/* 1  both low = SE0 = end of packet */
	MUSE    3

	MBIT    2, r1, r2
	MBIT    3, r2, r1
	MBIT    4, r1, r2
	MBIT    5, r2, r1
	MBIT    6, r1, r2

	MBIT    7, r2, r1
	mvns    r0, r0			/* 1  the accumulator holds CHANGED  */
					/*    bits; the data byte is their   */
					/*    complement (V-USB's `ser x3 /  */
					/*    eor x3,shift`, minus the       */
					/*    unstuff repair we do not need) */
	mov     r3, r8			/* 1  buffer base                    */
	strb    r0, [r3, r5]		/* 2  register-offset, bounded store */
	bx      r14			/* 3  flat; a B here would be 2-3    */
	MUSE    7
	MPAD				/* closes cell 7 at 16               */

	MUNSTUFF 4, r1
	MUNSTUFF 5, r2
	MUNSTUFF 6, r1
	MUNSTUFF 7, r2

/* ---- end of packet -----------------------------------------------------
 * V-USB's `se0:` (asmcommon.inc:54-80): recover the length, read the PID,
 * split DATA from tokens, filter tokens by device address, hand off.  No
 * CRC is checked on either side of this comparison - that is the whole
 * point of the file. */
	.global usb_rx_min_dispatch
usb_rx_min_dispatch:
.Lse0:
	movs    r0, #USB_BYTE_LIMIT
	mov     r3, r9
	subs    r0, r3			/* bytes STARTED, including the one  */
	subs    r0, #1			/* the EOP interrupted: drop it, as  */
					/* V-USB drops its own partial byte  */
	bls     .Ldone			/* nothing but a PID, or less        */
	mov     r3, r8
	ldrb    r1, [r3]		/* the PID byte                      */
	movs    r2, #0x0f
	ands    r2, r1
	cmp     r2, #0x03		/* DATA0                             */
	beq     .Lhandoff
	cmp     r2, #0x0b		/* DATA1                             */
	beq     .Lhandoff
/* A token.  V-USB: `lds shift,usbDeviceAddr / ldd x2,y+1 / lsl x2 /
 * cpse x2,shift / rjmp ignorePacket`.  Same test, one more instruction
 * because ARMv6-M cannot compare-and-skip. */
	ldrb    r3, [r3, #1]		/* ADDR is the 7 low bits of buf[1]  */
	lsls    r3, #25
	lsrs    r3, #25
	ldr     r2, =usb_min_addr
	ldrb    r2, [r2]
	cmp     r3, r2
	bne     .Ldone			/* not ours: ignorePacket            */
.Lhandoff:
	mov     r2, r0			/* len                               */
	movs    r0, r1			/* pid                               */
	mov     r1, r8			/* buf                               */
	bl      usb_min_handle
.Ldone:
	ldr     r0, =USB_EXTI_BASE	/* F-1: ack on EVERY exit            */
	movs    r1, #(1 << USB_DM_BIT)
	str     r1, [r0, #USB_EXTI_PR_OFS]
	pop     {r4, r5}
	mov     r8, r4
	mov     r9, r5
	pop     {r4-r7, pc}

	.size   usb_rx_minimal, . - usb_rx_minimal
	.ltorg

/* No tables.  Not one byte.  That is V-USB's defining choice and the reason
 * this file has no data section beyond the receive buffer. */

	.section .bss.usb_rxbuf_min, "aw", %nobits
	.balign USB_RXBUF_LEN		/* the mask bounds the index; the
					 * ALIGNMENT is what makes the mask a
					 * bound on the ADDRESS (F-5) */
	.global usb_rxbuf_min
usb_rxbuf_min:
	.space  USB_RXBUF_LEN + 3	/* +3: PID at +3, payload 4-aligned  */

	.section .bss.usb_min_addr, "aw", %nobits
	.global usb_min_addr
usb_min_addr:
	.space  1
