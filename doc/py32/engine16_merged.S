/*
 * engine16_merged.S - the referee's merge.  USB low-speed (1.5 Mbit/s)
 * receive engine, Cortex-M0+, SYSCLK 24 MHz, EXACTLY 16 cycles per bit cell.
 *
 * This is not a survey of six entries.  It is one engine, and every mechanism
 * in it earned its place against a named alternative.  See engine16_merged.md
 * for the ledger, the arithmetic behind each choice, and what was rejected.
 *
 * Mechanisms, with attribution:
 *   VUSB      - the chassis: unroll on WIRE bits, which have no data
 *               dependency, and run the data-domain work (NRZI, unstuffing,
 *               byte assembly, CRC16) as a software pipeline one wire byte
 *               behind, inside the 11 cycles each cell has left after
 *               sampling.  Register-offset table lookup `ldrh r1,[r4,r1]`
 *               with the unstuff state in r11 folds NRZI-decoded nibble +
 *               stuffing state -> kept bits + new state into ONE memory
 *               access.  Nothing in the timed path branches on data.
 *   VUSB      - the biased-add accumulator: the table returns
 *               m = kept_bits + 2^n - 1, so a single ADD both inserts n bits
 *               at the sentinel and moves the sentinel up by n.  Verified
 *               entry-by-entry against an independent model (128/128).
 *   DESCENT   - the combined D+/D- sample-and-mask: `ands r2,r6` on one IDR
 *               read gives Z <=> SE0 for free, because a valid bit always
 *               leaves exactly one pin high and only SE0 drives both low.
 *               Same read then feeds the capture.  Also the correct PY32
 *               GPIO base; VUSB defaulted to the STM32 one.
 *   CLEANSHEET- carry-chain capture (`lsrs` to carry, `adcs`): the sampled
 *               level enters a shift register with no branch and no mask.
 *               (VUSB and BALANCE reached the same instruction pair.)
 *   GRAINUUM  - per-byte unroll: "which bit of the byte" is a compile-time
 *               fact, which is what lets each cell carry a different
 *               pipeline segment and a different EOP stub.  DESCENT proved
 *               the same structure is forced arithmetically.
 *   BALANCE   - the structural buffer bound: power-of-two buffer, index
 *               masked, register-offset store.  No instruction in this
 *               object can address rxbuf out of range (DEFECTS_VERIFIED D-2)
 *               and it costs 2 cycles inside a cell that had slack.
 *
 * Placement: RAM-resident (.datacode, copied at boot like the rest of this
 * project's hot code).  ENGINE16_SPEC.md S2 RAM column: ordinary insn 1,
 * GPIO load 1 (IOPORT), RAM load/store 2, taken conditional 2-3, B 2-3,
 * BX 3.  The tables are in the same section, so a table lookup is a 2-cycle
 * RAM load.  This is load-bearing: from flash-resident code the rxbuf store
 * and both table lookups would cost 4 each and the pipeline would not fit.
 *
 * Assembles with:
 *   arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c
 */

	.syntax unified
	.cpu    cortex-m0plus
	.thumb

/* ------------------------------------------------------------------ port
 * PY32 GPIOB, IOPORT bus, 0x50000400 - BUILD_FACTS.md S7, identical on
 * F002B and F030/F003.  DESCENT used this; VUSB defaulted to 0x48000000,
 * which is an STM32 base and would have been found late and painfully.
 * Pin numbers follow usb_config.h (bootloader/usb_config.h:20-21). */
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

/* Receive buffer: 32 bytes, 32-byte aligned, index masked to 5 bits.  The
 * longest legal LS packet through this engine is SYNC + PID + 8 data +
 * CRC16 = 12 emitted bytes.  BALANCE's mechanism: the bound is a property of
 * the address arithmetic, not of a runtime check that could be wrong. */
#define USB_RXBUF_LEN   32
#define USB_RXBUF_MASK  27		/* shift pair: 32 - log2(len) */
/* Loop/packet bound: the chain is a loop, so termination needs a runtime
 * limit (see the note on VUSB's hang in the .md).  24 < 32, so the store is
 * bounded twice over: structurally by the mask, and by this. */
#define USB_BYTE_LIMIT  24

/* Table offsets inside the single base held in r4. */
#define T_UT            0		/* 128 halfwords: unstuff + accumulate */
#define T_CRC16         256		/* 256 halfwords: CRC16 byte step      */
#define T_CRC5          768		/*  16 halfwords: CRC5 nibble step     */

/* ------------------------------------------------- cycle-accounting macros
 * The ledger is assembled, not asserted: CELL pads the cell that is running
 * out to exactly 16 and raises .error if it went over.  A miscount is a
 * build failure, not a comment that disagrees with the code. */
	.set    CYC, 16			/* "the previous cell is already closed" */

	.macro  CELL idx
	.if     CYC > 16
	.error  "engine16_merged: bit cell over 16 cycles"
	.endif
	.rept   16 - CYC
	nop
	.endr
	.set    CYC, 0
	.global usb_rx_cell\idx
usb_rx_cell\idx:
	ldr     r2, [r7, #USB_IDR_OFS]	/* 1  raw D+/D-, IOPORT              */
	ands    r2, r6			/* 1  DESCENT: Z <=> SE0, free       */
	beq     rx_eop\idx		/* 1  not taken except at EOP        */
	lsrs    r2, r2, #(USB_DP_BIT + 1) /* 1  C = D+                       */
	adcs    r5, r5			/* 1  CLEANSHEET: carry-chain capture */
	.set    CYC, 5
	.endm

	.macro  USE n			/* declare \n cycles of pipeline work */
	.set    CYC, CYC + (\n)
	.endm

/* ====================================================================
 * The data-domain pipeline for ONE wire byte, cut into seven pieces that
 * each fit the 11 cycles a cell has left after sampling.  Emitted twice from
 * this one source - interleaved between the eight CELLs (timed) and back to
 * back in the flush path (untimed) - so the two copies cannot drift apart.
 * VUSB's structure; the cut points and SEG2/SEG3/SEG5 are the referee's.
 *
 * Register contract
 *   r0  bits held in the accumulator (0..15)
 *   r1  table index -> table entry -> kept-bits word -> commit mask
 *   r2  temp.  MUST be dead at every segment boundary: CELL clobbers it.
 *   r3  accumulator: bits 0..r0-1 are data (oldest at bit 0), bit r0 = 1
 *   r4  table base    r5 wire packer    r6 pin mask    r7 GPIO base
 *   r8  park: low-nibble index, then the CRC16 table value
 *   r9  rxbuf base (32-byte aligned)    r10 CRC16    r11 unstuff state<<5
 *   r12 emitted byte count              r14 chain head (bit0 set, for bx)
 *
 * The table (T_UT) is indexed by  state*32 + nibble*2  and returns
 *     entry = m<<11 | n<<8 | newstate<<5,   m = kept_bits + 2^n - 1
 * n is how many of the nibble's four bits survive unstuffing, kept_bits are
 * those bits with the oldest at bit 0, and the +2^n-1 bias is what makes
 *     acc + (m << r0)
 * write the bits at the sentinel AND move the sentinel up by n, in one ADD,
 * with no branch and no second accumulator.  This is the single mechanism
 * that reconciles unrolling with bit stuffing: the wire is unrolled, the
 * data stream is not, and the table absorbs the difference.
 * ==================================================================== */

	.macro  SEG0			/* 7: high nibble -> table, state     */
	lsls    r1, r1, #1		/* 1  nibble*2                        */
	mov     r2, r11			/* 1  state, pre-shifted <<5          */
	orrs    r1, r2			/* 1  index                           */
	ldrh    r1, [r4, r1]		/* 2  ONE access = NRZI+stuff+assembly*/
	uxtb    r2, r1			/* 1  entry&0xFF = newstate<<5        */
	mov     r11, r2			/* 1                                  */
	.endm

	.macro  SEG1			/* 11: append it, then low nibble     */
	lsls    r2, r1, #21		/* 1  \  n = (entry>>8)&7             */
	lsrs    r2, r2, #29		/* 1  /                               */
	lsrs    r1, r1, #11		/* 1  m                               */
	lsls    r1, r1, r0		/* 1  align to the sentinel           */
	adds    r3, r3, r1		/* 1  insert n bits, move sentinel    */
	adds    r0, r0, r2		/* 1  bit count += n                  */
	mov     r1, r8			/* 1  low nibble*2                    */
	mov     r2, r11			/* 1                                  */
	orrs    r1, r2			/* 1                                  */
	ldrh    r1, [r4, r1]		/* 2                                  */
	.endm

	.macro  SEG2			/* 9: append it; fetch the rxbuf base */
	uxtb    r2, r1			/* 1                                  */
	mov     r11, r2			/* 1                                  */
	lsls    r2, r1, #21		/* 1                                  */
	lsrs    r2, r2, #29		/* 1                                  */
	lsrs    r1, r1, #11		/* 1                                  */
	lsls    r1, r1, r0		/* 1                                  */
	adds    r3, r3, r1		/* 1                                  */
	adds    r0, r0, r2		/* 1                                  */
	mov     r1, r9			/* 1  buffer base, 32-byte aligned    */
	.endm

	.macro  SEG3			/* 11: bounded store, commit decision */
	mov     r2, r12			/* 1  emitted byte count              */
	lsls    r2, r2, #USB_RXBUF_MASK	/* 1  \ BALANCE: index &= 31, so no   */
	lsrs    r2, r2, #USB_RXBUF_MASK	/* 1  / store can leave the buffer    */
	strb    r3, [r1, r2]		/* 2  speculative store of the byte   */
	subs    r0, r0, #8		/* 1  negative <=> byte not finished  */
	asrs    r1, r0, #31		/* 1  -1 if NOT finished              */
	mvns    r1, r1			/* 1  commit mask: -1 finished, 0 not */
	movs    r2, #8			/* 1                                  */
	bics    r2, r1			/* 1  8 if not finished, else 0       */
	adds    r0, r0, r2		/* 1  bit count restored, or -8       */
	.endm

	.macro  SEG4			/* 9: CRC16 byte step, speculative    */
	mov     r2, r10			/* 1  running CRC                     */
	eors    r2, r3			/* 1  crc ^ candidate byte            */
	uxtb    r2, r2			/* 1                                  */
	lsls    r2, r2, #1		/* 1  halfword index                  */
	adds    r2, #(T_CRC16 / 2)	/* 1  \ ADDS takes imm8; T_CRC16=256  */
	adds    r2, #(T_CRC16 / 2)	/* 1  /                               */
	ldrh    r2, [r4, r2]		/* 2                                  */
	mov     r8, r2			/* 1  park; the commit is decided late*/
	.endm

	.macro  SEG5 ovf		/* 11: drop, advance, bound, gate     */
	movs    r2, #8			/* 1                                  */
	ands    r2, r1			/* 1  8 if a byte finished, else 0    */
	lsrs    r3, r3, r2		/* 1  drop the byte just emitted      */
	mov     r2, r12			/* 1                                  */
	subs    r2, r2, r1		/* 1  count += 1 only on a real byte  */
	mov     r12, r2			/* 1                                  */
	cmp     r2, #USB_BYTE_LIMIT	/* 1  \ loop bound: the chain is a    */
	bhs     \ovf			/* 1  / loop and must terminate       */
	subs    r2, r2, #3		/* 1  \ CRC covers the data field:    */
	asrs    r2, r2, #31		/* 1   | -1 while SYNC/PID not past   */
	bics    r1, r2			/* 1  / commit mask &= (count >= 3)   */
	.endm

	.macro  SEG6			/* 10: commit the CRC step, or not    */
	mov     r2, r8			/* 1  parked table value              */
	ands    r2, r1			/* 1  zero unless committing          */
	mov     r8, r2			/* 1                                  */
	movs    r2, #8			/* 1                                  */
	ands    r2, r1			/* 1  shift 8 if committing, else 0   */
	mov     r1, r10			/* 1                                  */
	lsrs    r1, r1, r2		/* 1  crc>>8, or crc>>0               */
	mov     r2, r8			/* 1                                  */
	eors    r1, r2			/* 1  crc = (crc>>8) ^ T[..], or crc  */
	mov     r10, r1			/* 1                                  */
	.endm

/* NRZI for eight wire bits at once.  The predecessor of sample i is bit i+1
 * of the same register, so one shift and one xor decode a whole byte - and
 * bit 8, the last sample of the PREVIOUS byte, supplies the predecessor of
 * this byte's first bit, so the byte boundary costs nothing.  This is what
 * an 8-bit machine cannot do and is why the wire-domain unroll pays. */
	.macro  SEGA			/* 8                                  */
	lsrs    r1, r5, #1		/* 1  every sample's predecessor      */
	eors    r1, r5			/* 1  1 = transition                  */
	mvns    r1, r1			/* 1  NRZI: no transition = data 1    */
	uxtb    r1, r1			/* 1  bit7 = first bit in time        */
	lsls    r2, r1, #28		/* 1  \                               */
	lsrs    r2, r2, #27		/* 1  / low nibble * 2                */
	mov     r8, r2			/* 1  park for SEG1                   */
	lsrs    r1, r1, #4		/* 1  high nibble                     */
	.endm

/* CELL only checks the cell it *closes*, so the last cell of the chain would
 * never be checked.  CELL_END closes it.  (VUSB's accounting device has this
 * hole; the fix costs nothing.) */
	.macro  CELL_END
	.if     CYC > 16
	.error  "engine16_merged: last bit cell over 16 cycles"
	.endif
	.rept   16 - CYC
	nop
	.endr
	.set    CYC, 16
	.endm

/* ========================================================================= */
	.section .datacode, "ax", %progbits	/* RAM-resident: see the header */
	.balign 4
	.global usb_rx_engine16
	.thumb_func
	.type   usb_rx_engine16, %function

/* Entry: the D- pin-change interrupt, priority 0, nothing may preempt it
 * (PRIOR_ART L-23).  The SE0 test comes before the register saves because a
 * keepalive EOP must be recognised within a few tens of cycles (L-9). */
usb_rx_engine16:
	ldr     r3, =USB_GPIO_BASE
	ldr     r0, [r3, #USB_IDR_OFS]
	movs    r1, #USB_PINMASK
	ands    r0, r1
	bne     1f
	.global usb_rx_keepalive
usb_rx_keepalive:			/* SE0 already at entry: keepalive   */
	bx      lr
1:
	push    {r4-r7, lr}
	mov     r4, r8
	mov     r5, r9
	mov     r6, r10
	mov     r7, r11
	push    {r4-r7}

/* Everything that needs a literal is loaded HERE, before the phase lock, so
 * that no load whose cost depends on where the literal pool ended up can sit
 * between the lock edge and the first sample. */
	mov     r7, r3			/* r7 = GPIO base                    */
	movs    r6, #USB_PINMASK	/* r6 = D+|D- mask (DESCENT)         */
	ldr     r4, =usb_tables		/* r4 = table base                   */
	ldr     r2, =usb_rxbuf
	mov     r9, r2
	ldr     r2, =(usb_rx_chain + 1)	/* chain head, bit0 set for bx       */
	mov     r14, r2
	movs    r2, #0
	mvns    r2, r2
	lsrs    r2, r2, #16		/* 0xFFFF                            */
	mov     r10, r2			/* CRC16 init                        */
	movs    r1, #200		/* bounded spin (PRIOR_ART A-16)     */
	b       .Lsync_hunt
.Lgiveup:				/* in range of the hunt's B<cond>    */
	b       .Lusb_done

/* ---- phase lock ---------------------------------------------------------
 * Exception entry on M0+ is ~15 cycles plus the instruction it interrupted,
 * so the first SYNC edge is long gone by the time we run.  Lock to the LAST
 * SYNC edge instead, which is unambiguous: SYNC is K J K J K J K K, so every
 * K cell is followed by a J one bit later EXCEPT cell 6, which is followed
 * by another K.  Catch a J->K edge, wait into the middle of the next cell,
 * and if the bus is still K then that edge was cell 6.  (VUSB's mechanism.)
 *
 * Both poll loops are the tightest bounded form: the wait is the TAKEN
 * branch and the exit is a FALL-THROUGH, so the exit costs nothing and its
 * 2-vs-3 never lands in the phase.  Period 6..7 cycles (subs, untaken beq,
 * ldr, lsls, taken branch), so the edge is seen 0..7 cycles late, nominally
 * 3.5 +-3.5.  VUSB's loops cost 7..9 with a taken B on the way out, and its
 * delay was itself a loop, which multiplied the ambiguity again; here the
 * delay is straight-line nops. */
/* EOP stubs for cells 0..3 sit in front of the chain so that a backward
 * B<cond> from an early cell reaches them; the chain is longer than the
 * +-254 byte range of a Thumb conditional branch (CLEANSHEET's finding about
 * branch range, applied at the granularity where it still works).  r14 -
 * dead as the chain head the moment we leave the chain - carries the number
 * of wire-bit slots of the current byte that were never sampled. */
rx_eop0:	movs    r2, #8
	mov     r14, r2
	b       rx_flush0
rx_eop1:	movs    r2, #7
	mov     r14, r2
	b       rx_flush1
rx_eop2:	movs    r2, #6
	mov     r14, r2
	b       rx_flush2
rx_eop3:	movs    r2, #5
	mov     r14, r2
	b       rx_flush3

.Lsync_hunt:
.Lwait_j:				/* first get to a J cell             */
	subs    r1, #1
	beq     .Lgiveup
	ldr     r0, [r7, #USB_IDR_OFS]
	lsls    r0, r0, #(31 - USB_DM_BIT)
	bpl     .Lwait_j		/* D- low = K: keep waiting          */
.Lwait_k:				/* then catch the J->K edge          */
	subs    r1, #1
	beq     .Lgiveup
	ldr     r0, [r7, #USB_IDR_OFS]
	lsls    r0, r0, #(31 - USB_DM_BIT)
	bmi     .Lwait_k		/* D- high = J: keep waiting         */
	/* falls through: t0 = the start of this cell, nominally 3.5 cycles ago */

/* 20 nops put the confirming sample at t0 + 23.5 nominal, i.e. the middle of
 * the NEXT cell.  SYNC cell 7 is K; an ordinary K cell would be J by now. */
.Lk_edge:
	.rept   20
	nop
	.endr
	ldr     r0, [r7, #USB_IDR_OFS]	/* t0 + 23.5                         */
	lsls    r0, r0, #(31 - USB_DM_BIT)
	bpl     .Lsync_hunt		/* J -> that was not the last edge   */

/* Confirmed.  The first PID cell starts at t0 + 32, so its centre is
 * t0 + 40 = confirming sample + 16.  ldr + lsls + untaken bpl = 3, so the
 * priming below must be exactly 13 cycles: 9 of setup, 4 of nop.
 *
 * The pipeline is primed with the one wire byte we already know - SYNC
 * itself.  SYNC decodes to 0,0,0,0,0,0,0,1 oldest-first, i.e. 0x80 as a USB
 * byte, so it enters the chain as if cell 7 had just decoded it: high
 * nibble 0, low nibble 0b0001 -> index 2. */
	.balign 4			/* align HERE, not at the chain head,
					 * so no variable-size fill can land
					 * inside the counted priming block */
.Lprime:
	movs    r0, #0			/* 1  accumulator holds no bits      */
	movs    r3, #1			/* 1  sentinel at bit 0              */
	movs    r5, #1			/* 1  last SYNC sample was K: D+ = 1 */
	movs    r1, #0			/* 1  SYNC high nibble               */
	movs    r2, #2			/* 1  SYNC low nibble * 2            */
	mov     r8, r2			/* 1                                 */
	movs    r2, #0			/* 1                                 */
	mov     r11, r2			/* 1  unstuff state 0                */
	mov     r12, r2			/* 1  no bytes emitted yet           */
	nop				/* 1  \                              */
	nop				/* 1   | phase padding: 9 + 5 = 14   */
	nop				/* 1   | cycles; an odd nop count also*/
	nop				/* 1   | keeps usb_rx_chain 4-aligned */
	nop				/* 1  /                              */
	/* falls straight into usb_rx_cell0 - no branch, no ambiguity */

/* ======================= the timed chain: 8 cells x 16 ===================
 * Cell 7 closes the wire byte, decodes it (SEGA) and takes the back edge.
 * Cells 0..6 carry the pipeline for the byte cell 7 decoded one iteration
 * ago.  The only branches in the chain are the eight SE0 tests, not taken
 * until the packet ends (1 cycle, no ambiguity), and the single back edge,
 * which is a BX: 3 cycles flat, not the 2-3 of a B. */
usb_rx_chain:
	.if     (usb_rx_chain - .Lprime) != 28	/* 14 insns x 2 bytes */
	.error  "engine16_merged: phase-lock priming is not 14 cycles"
	.endif
	CELL    0
	SEG0
	USE     7			/* 5 + 7  + 4 nop = 16               */
	CELL    1
	SEG1
	USE     11			/* 5 + 11 + 0     = 16               */
	CELL    2
	SEG2
	USE     9			/* 5 + 9  + 2 nop = 16               */
	CELL    3
	SEG3
	USE     11			/* 5 + 11 + 0     = 16               */
	CELL    4
	SEG4
	USE     9			/* 5 + 9  + 2 nop = 16               */
	CELL    5
	SEG5    .Lovf_a
	USE     11			/* 5 + 11 + 0     = 16               */
	CELL    6
	SEG6
	USE     10			/* 5 + 10 + 1 nop = 16               */
	CELL    7
	SEGA
	USE     8
	bx      r14			/* 3  deterministic back edge        */
	USE     3			/* 5 + 8 + 3      = 16               */
	CELL_END

/* EOP stubs for cells 4..7, and the overflow escape for SEG5 in cell 5. */
rx_eop4:	movs    r2, #4
	mov     r14, r2
	b       rx_flush4
rx_eop5:	movs    r2, #3
	mov     r14, r2
	b       rx_flush5
rx_eop6:	movs    r2, #2
	mov     r14, r2
	b       rx_flush6
rx_eop7:	movs    r2, #1
	mov     r14, r2
	b       rx_flush7
.Lovf_a:
	b       .Lusb_overrun

/* ===================== flush: finish the pipeline, untimed ================
 * Entering at rx_flushK means "SE0 was sampled in cell K, so segments
 * K..6 of the byte in flight still have to run".  Same macros as the timed
 * chain, so the two copies cannot disagree. */
rx_flush0:	SEG0
rx_flush1:	SEG1
rx_flush2:	SEG2
rx_flush3:	SEG3
rx_flush4:	SEG4
rx_flush5:	SEG5    .Lovf_b
rx_flush6:	SEG6
rx_flush7:
	/* The last, partial wire byte: r14 holds 8-k, the number of bit slots
	 * never sampled.  Left-shifting the decoded byte by that amount moves
	 * the k real bits to the top and feeds zeros in below.  Those zeros
	 * decode as data 0s: they cannot trip the stuffing rule, and because a
	 * packet's data field is a whole number of bytes the accumulator is
	 * empty when they arrive, so no spurious byte is emitted. */
	mov     r2, r14
	cmp     r2, #8
	beq     .Lrx_tail		/* nothing real in this byte         */
	lsrs    r1, r5, #1
	eors    r1, r5
	mvns    r1, r1
	uxtb    r1, r1
	lsls    r1, r1, r2
	uxtb    r1, r1
	lsls    r2, r1, #28
	lsrs    r2, r2, #27
	mov     r8, r2
	lsrs    r1, r1, #4
	SEG0
	SEG1
	SEG2
	SEG3
	SEG4
	SEG5    .Lovf_b
	SEG6
	b       .Lrx_tail
.Lovf_b:
	b       .Lusb_overrun

/* =========================== tail: validate and dispatch ==================
 * Untimed, but not unbudgeted: the device must answer 2-6.5 bit times after
 * EOP (USB 2.0 S7.1.18, PRIOR_ART L-1 = 64..208 cycles at 24 MHz).  The CRC
 * is already folded in as the bytes arrived, so what is left here is one
 * comparison - which is exactly why CRC stays in the bit cell and does not
 * move to the tail (PRIOR_ART S-2). */
.Lrx_tail:
	mov     r2, r11
	cmp     r2, #(7 << 5)		/* sticky stuffing violation         */
	beq     .Lusb_done

	mov     r6, r9			/* rxbuf base                        */
	mov     r0, r12			/* emitted byte count                */
	cmp     r0, #4			/* SYNC + PID + at least two more    */
	blo     .Lusb_done
	cmp     r0, #12			/* longer than any legal LS packet   */
	bhi     .Lusb_done

	ldrb    r1, [r6, #0]
	cmp     r1, #0x80		/* SYNC decodes to 0x80, oldest-first*/
	bne     .Lusb_done

	ldrb    r5, [r6, #1]		/* PID byte                          */
	lsrs    r1, r5, #4
	movs    r2, #0x0F
	ands    r2, r5
	eors    r1, r2			/* high nibble must be ~low nibble   */
	cmp     r1, #0x0F
	bne     .Lusb_done

	movs    r1, #3
	ands    r1, r5			/* PID type field                    */
	cmp     r1, #3
	beq     .Ldata
	cmp     r1, #1
	beq     .Ltoken
	cmp     r1, #2
	beq     .Lhandshake
	b       .Lusb_done		/* special (PRE/ERR): not supported  */

/* DATA0/DATA1: the CRC16 was folded in as the bytes arrived, so only the
 * residue is left.  0xB001 is the reflected-CRC16 residue over message+CRC
 * (verified numerically here, and the same constant rv003usb.S:519 uses). */
.Ldata:
	mov     r1, r10
	ldr     r2, =0x0000B001
	cmp     r1, r2
	bne     .Lusb_done
	subs    r3, r0, #4		/* payload length: -SYNC -PID -CRC16 */
	adds    r1, r6, #2		/* payload pointer                   */
	lsrs    r2, r5, #3
	movs    r0, #1
	ands    r2, r0			/* DATA1 -> which_data = 1           */
	movs    r0, #0			/* this_token: filled by the C layer */
	ldr     r4, =rv003usb_internal_data
	push    {r4}			/* 5th argument is on the stack      */
	bl      usb_pid_handle_data
	add     sp, #4
	b       .Lusb_done

/* Token: CRC5 over the eleven address/endpoint bits plus the five CRC bits
 * leaves the constant residue 0x06 (verified numerically here). */
.Ltoken:
	cmp     r0, #4
	bne     .Lusb_done
	movs    r3, #0x1F		/* CRC5 init                         */
	ldrb    r0, [r6, #2]
	bl      .Lcrc5_byte
	ldrb    r0, [r6, #3]
	bl      .Lcrc5_byte
	cmp     r3, #6
	bne     .Lusb_done

	ldrb    r0, [r6, #2]
	ldrb    r1, [r6, #3]
	lsrs    r2, r0, #7		/* endp bit 0                        */
	lsls    r1, r1, #29
	lsrs    r1, r1, #28		/* endp bits 3:1                     */
	orrs    r2, r1			/* r2 = endpoint                     */
	movs    r1, #0x7F
	ands    r0, r1			/* r0 = address                      */
	adds    r1, r6, #2		/* data pointer                      */
	ldr     r4, =rv003usb_internal_data
	push    {r4}
	movs    r3, #0			/* unused                            */
	movs    r4, #0x0F
	ands    r4, r5
	cmp     r4, #0x0D
	beq     .Lt_setup
	cmp     r4, #0x09
	beq     .Lt_in
	cmp     r4, #0x01
	beq     .Lt_out
	add     sp, #4
	b       .Lusb_done		/* SOF: nothing to do here           */
.Lt_setup:
	bl      usb_pid_handle_setup
	add     sp, #4
	b       .Lusb_done
.Lt_in:
	bl      usb_pid_handle_in
	add     sp, #4
	b       .Lusb_done
.Lt_out:
	bl      usb_pid_handle_out
	add     sp, #4
	b       .Lusb_done

.Lhandshake:
	adds    r1, r6, #2
	movs    r0, #0
	movs    r2, #0
	movs    r3, #0
	ldr     r4, =rv003usb_internal_data
	push    {r4}
	bl      usb_pid_handle_ack
	add     sp, #4
	b       .Lusb_done

/* CRC5, one byte, two nibble steps.  r3 = crc, r0 = byte, r4 = table base. */
.Lcrc5_byte:
	mov     r12, lr
	mov     r1, r3
	eors    r1, r0
	lsls    r1, r1, #28
	lsrs    r1, r1, #27
	movs    r2, #(T_CRC5 >> 8)
	lsls    r2, r2, #8
	adds    r1, r1, r2
	ldrh    r1, [r4, r1]
	lsrs    r3, r3, #4
	eors    r3, r1
	lsrs    r0, r0, #4
	mov     r1, r3
	eors    r1, r0
	lsls    r1, r1, #28
	lsrs    r1, r1, #27
	movs    r2, #(T_CRC5 >> 8)
	lsls    r2, r2, #8
	adds    r1, r1, r2
	ldrh    r1, [r4, r1]
	lsrs    r3, r3, #4
	eors    r3, r1
	mov     pc, r12

.Lusb_overrun:
	/* A packet longer than any legal LS packet, or a bus stuck in a
	 * stuffing violation.  No store went out of bounds to get here - the
	 * index is masked - so this is a liveness escape, not a memory fix. */
	b       .Lusb_done

.Lusb_done:
	pop     {r4-r7}
	mov     r8, r4
	mov     r9, r5
	mov     r10, r6
	mov     r11, r7
	pop     {r4-r7, pc}

	.size   usb_rx_engine16, . - usb_rx_engine16
	.ltorg

/* ============================== tables ===================================
 * Generated and verified by the script in engine16_merged.md, which also
 * re-derives VUSB's T_UT independently (128/128 entries matched) and
 * confirms both CRC residues numerically.  They live in the same
 * RAM-resident section as the code, so `ldrh rd,[r4,rn]` is a 2-cycle RAM
 * load; from flash they would cost 4 and the pipeline would not fit.
 */
	.balign 4
	.global usb_tables
usb_tables:
	/* T_UT: 8 unstuff states x 16 wire nibbles.
	 * halfword = m<<11 | n<<8 | newstate<<5, m = kept_bits + 2^n - 1.
	 * Row 7 is the sticky stuffing-violation row.  It differs from VUSB's:
	 * it KEEPS its four bits (n=4) instead of emitting nothing, so that the
	 * emitted-byte counter keeps advancing and SEG5's bound terminates the
	 * chain.  With VUSB's n=0 row the counter freezes and a bus stuck in a
	 * violation spins the ISR forever - see the .md, "the hang". */
	.hword	0x7c00, 0xbc20, 0x9c00, 0xdc40, 0x8c00, 0xcc20, 0xac00, 0xec60
	.hword	0x8400, 0xc420, 0xa400, 0xe440, 0x9400, 0xd420, 0xb400, 0xf480
	.hword	0x7c00, 0xbc20, 0x9c00, 0xdc40, 0x8c00, 0xcc20, 0xac00, 0xec60
	.hword	0x8400, 0xc420, 0xa400, 0xe440, 0x9400, 0xd420, 0xb400, 0xf4a0
	.hword	0x7c00, 0xbc20, 0x9c00, 0xdc40, 0x8c00, 0xcc20, 0xac00, 0xec60
	.hword	0x8400, 0xc420, 0xa400, 0xe440, 0x9400, 0xd420, 0xb400, 0xf4c0
	.hword	0x7c00, 0xbc20, 0x9c00, 0xdc40, 0x8c00, 0xcc20, 0xac00, 0xec60
	.hword	0x8400, 0xc420, 0xa400, 0xe440, 0x9400, 0xd420, 0x7300, 0x00e0
	.hword	0x7c00, 0xbc20, 0x9c00, 0xdc40, 0x8c00, 0xcc20, 0xac00, 0xec60
	.hword	0x8400, 0xc420, 0xa400, 0xe440, 0x5300, 0x7320, 0x00e0, 0x00e0
	.hword	0x7c00, 0xbc20, 0x9c00, 0xdc40, 0x8c00, 0xcc20, 0xac00, 0xec60
	.hword	0x4300, 0x6320, 0x5300, 0x7340, 0x00e0, 0x00e0, 0x00e0, 0x00e0
	.hword	0x3b00, 0x5b20, 0x4b00, 0x6b40, 0x4300, 0x6320, 0x5300, 0x7360
	.hword	0x00e0, 0x00e0, 0x00e0, 0x00e0, 0x00e0, 0x00e0, 0x00e0, 0x00e0
	.hword	0x7ce0, 0xbce0, 0x9ce0, 0xdce0, 0x8ce0, 0xcce0, 0xace0, 0xece0
	.hword	0x84e0, 0xc4e0, 0xa4e0, 0xe4e0, 0x94e0, 0xd4e0, 0xb4e0, 0xf4e0

	/* T_CRC16: crc = (crc>>8) ^ T[(crc^byte)&0xFF], reflected 0xA001 */
	.hword	0x0000, 0xc0c1, 0xc181, 0x0140, 0xc301, 0x03c0, 0x0280, 0xc241
	.hword	0xc601, 0x06c0, 0x0780, 0xc741, 0x0500, 0xc5c1, 0xc481, 0x0440
	.hword	0xcc01, 0x0cc0, 0x0d80, 0xcd41, 0x0f00, 0xcfc1, 0xce81, 0x0e40
	.hword	0x0a00, 0xcac1, 0xcb81, 0x0b40, 0xc901, 0x09c0, 0x0880, 0xc841
	.hword	0xd801, 0x18c0, 0x1980, 0xd941, 0x1b00, 0xdbc1, 0xda81, 0x1a40
	.hword	0x1e00, 0xdec1, 0xdf81, 0x1f40, 0xdd01, 0x1dc0, 0x1c80, 0xdc41
	.hword	0x1400, 0xd4c1, 0xd581, 0x1540, 0xd701, 0x17c0, 0x1680, 0xd641
	.hword	0xd201, 0x12c0, 0x1380, 0xd341, 0x1100, 0xd1c1, 0xd081, 0x1040
	.hword	0xf001, 0x30c0, 0x3180, 0xf141, 0x3300, 0xf3c1, 0xf281, 0x3240
	.hword	0x3600, 0xf6c1, 0xf781, 0x3740, 0xf501, 0x35c0, 0x3480, 0xf441
	.hword	0x3c00, 0xfcc1, 0xfd81, 0x3d40, 0xff01, 0x3fc0, 0x3e80, 0xfe41
	.hword	0xfa01, 0x3ac0, 0x3b80, 0xfb41, 0x3900, 0xf9c1, 0xf881, 0x3840
	.hword	0x2800, 0xe8c1, 0xe981, 0x2940, 0xeb01, 0x2bc0, 0x2a80, 0xea41
	.hword	0xee01, 0x2ec0, 0x2f80, 0xef41, 0x2d00, 0xedc1, 0xec81, 0x2c40
	.hword	0xe401, 0x24c0, 0x2580, 0xe541, 0x2700, 0xe7c1, 0xe681, 0x2640
	.hword	0x2200, 0xe2c1, 0xe381, 0x2340, 0xe101, 0x21c0, 0x2080, 0xe041
	.hword	0xa001, 0x60c0, 0x6180, 0xa141, 0x6300, 0xa3c1, 0xa281, 0x6240
	.hword	0x6600, 0xa6c1, 0xa781, 0x6740, 0xa501, 0x65c0, 0x6480, 0xa441
	.hword	0x6c00, 0xacc1, 0xad81, 0x6d40, 0xaf01, 0x6fc0, 0x6e80, 0xae41
	.hword	0xaa01, 0x6ac0, 0x6b80, 0xab41, 0x6900, 0xa9c1, 0xa881, 0x6840
	.hword	0x7800, 0xb8c1, 0xb981, 0x7940, 0xbb01, 0x7bc0, 0x7a80, 0xba41
	.hword	0xbe01, 0x7ec0, 0x7f80, 0xbf41, 0x7d00, 0xbdc1, 0xbc81, 0x7c40
	.hword	0xb401, 0x74c0, 0x7580, 0xb541, 0x7700, 0xb7c1, 0xb681, 0x7640
	.hword	0x7200, 0xb2c1, 0xb381, 0x7340, 0xb101, 0x71c0, 0x7080, 0xb041
	.hword	0x5000, 0x90c1, 0x9181, 0x5140, 0x9301, 0x53c0, 0x5280, 0x9241
	.hword	0x9601, 0x56c0, 0x5780, 0x9741, 0x5500, 0x95c1, 0x9481, 0x5440
	.hword	0x9c01, 0x5cc0, 0x5d80, 0x9d41, 0x5f00, 0x9fc1, 0x9e81, 0x5e40
	.hword	0x5a00, 0x9ac1, 0x9b81, 0x5b40, 0x9901, 0x59c0, 0x5880, 0x9841
	.hword	0x8801, 0x48c0, 0x4980, 0x8941, 0x4b00, 0x8bc1, 0x8a81, 0x4a40
	.hword	0x4e00, 0x8ec1, 0x8f81, 0x4f40, 0x8d01, 0x4dc0, 0x4c80, 0x8c41
	.hword	0x4400, 0x84c1, 0x8581, 0x4540, 0x8701, 0x47c0, 0x4680, 0x8641
	.hword	0x8201, 0x42c0, 0x4380, 0x8341, 0x4100, 0x81c1, 0x8081, 0x4040

	/* T_CRC5: crc = (crc>>4) ^ T[(crc^nibble)&0xF], reflected 0x14 */
	.hword	0x0000, 0x0016, 0x0005, 0x0013, 0x000a, 0x001c, 0x000f, 0x0019
	.hword	0x0014, 0x0002, 0x0011, 0x0007, 0x001e, 0x0008, 0x001b, 0x000d


	.section .bss.usb_rxbuf, "aw", %nobits
	.balign USB_RXBUF_LEN		/* the mask in SEG3 assumes this */
	.global usb_rxbuf
usb_rxbuf:
	.space  USB_RXBUF_LEN
