/*
 * engine16_vusb.S - USB low-speed receive engine, Cortex-M0+, 24 MHz,
 *                   16 cycles per bit cell.  Competition entry "VUSB".
 *
 * Lineage: the AVR school (V-USB / micronucleus) re-derived, not ported.  What
 * is taken is the *principles* - straight-line unrolling instead of a per-bit
 * loop, arithmetic instead of control flow, CRC out of the bit cell, a bounded
 * phase error fixed once at SYNC.  What is deliberately NOT taken is V-USB's
 * shape, which is an 8-bit machine's shape.  See engine16_vusb.md sections
 * "Where the AVR answer is wrong here" and the per-path cycle ledger.
 *
 * Idea in one line: unroll on WIRE bits (which have no data dependency at all),
 * and run the data-domain work - NRZI, unstuffing, byte assembly, CRC16 - as a
 * software pipeline one wire-byte behind, inside the 11 spare cycles of each
 * cell.  Nothing in the timed path branches on data.
 *
 * Cost model: ENGINE16_SPEC.md section 2, RAM column (this code is meant to be
 * copied to and executed from SRAM):
 *   ordinary instruction 1 | GPIO load 1 | RAM load/store 2 | bx 3
 *   conditional branch not taken 1, taken 2-3 | b 2-3
 * Every cell is closed by the CELL macro, which pads with nop to exactly 16
 * and raises .error if the cell is over budget.  The ledger is assembled, not
 * asserted in prose.
 *
 * Assembles with:
 *   arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c
 */

	.syntax unified
	.cpu    cortex-m0plus
	.thumb

/* ------------------------------------------------------------------ port */
#ifndef USB_GPIO_BASE
#define USB_GPIO_BASE   0x48000000	/* GPIOA */
#endif
#ifndef USB_IDR_OFS
#define USB_IDR_OFS     0x10
#endif
#ifndef USB_DP_BIT
#define USB_DP_BIT      2
#endif
#ifndef USB_DM_BIT
#define USB_DM_BIT      1
#endif
#define USB_PINMASK     ((1 << USB_DP_BIT) | (1 << USB_DM_BIT))

/* Longest legal LS packet through this engine, in emitted bytes:
 *   SYNC + PID + 8 data + CRC16 = 12.  The chain is bounded at 16 emitted
 *   bytes and the buffer is 18, so the one speculative store that can run
 *   past the bound still lands inside the buffer.  DEFECTS_VERIFIED D-2. */
#define USB_RXBUF_LEN   18
#define USB_BYTE_BOUND  16

/* Table offsets inside the single table base held in r4. */
#define T_UT            0		/* 128 halfwords: unstuff/accumulate  */
#define T_CRC16         256		/* 256 halfwords: CRC16 byte step     */
#define T_CRC5          768		/*  16 halfwords: CRC5 nibble step    */

/* ------------------------------------------------- cycle-accounting macros */
	.set    CYC, 16			/* "previous cell already closed"     */

/* CELL closes the cell that is running and opens the next one.  \n is the
 * index 0..7 of the cell being opened; it selects that cell's EOP stub so the
 * flush path knows how many wire bits of the current byte are real. */
	.macro  CELL idx
	.if     CYC > 16
	.error  "engine16_vusb: bit cell over 16 cycles"
	.endif
	.rept   16 - CYC
	nop
	.endr
	.set    CYC, 0
	.global usb_rx_cell\idx
usb_rx_cell\idx:
	ldr     r2, [r7, #USB_IDR_OFS]	/* 1  raw D+/D-, IOPORT             */
	ands    r2, r6			/* 1  keep only D+ and D-; Z = SE0  */
	beq     usb_rx_eop\idx		/* 1  not taken except at EOP       */
	lsrs    r2, r2, #(USB_DP_BIT + 1) /* 1  C = D+                      */
	adcs    r5, r5			/* 1  pack raw D+ into the wire reg */
	.set    CYC, 5
	.endm

	.macro  USE n			/* declare \n cycles of pipeline work */
	.set    CYC, CYC + (\n)
	.endm

/* ------------------------------------------------------ pipeline segments
 * SEG0..SEG6 are the whole data-domain pipeline for ONE wire byte, cut into
 * seven pieces that each fit in the eleven cycles a cell has left over after
 * sampling.  They are emitted twice - interleaved between the eight CELLs
 * (the timed copy) and back to back in the flush path (the untimed copy) -
 * from this one source, so the two cannot drift apart.
 *
 * Register contract:
 *   r0  bit count held in the accumulator (0..15)
 *   r1  wire nibble index -> table entry -> kept bits -> commit mask
 *   r2  short-lived temp.  MUST be dead at every segment boundary, because
 *       the CELL macro clobbers it with the sampled port word.
 *   r3  data accumulator: bits 0..r0-1 are data, bit r0 is a sentinel 1
 *   r4  table base   r5 wire packer   r6 pin mask   r7 GPIO base
 *   r8  scratch park: low-nibble index, then the CRC table value
 *   r9  rx write pointer   r10 CRC16   r11 unstuff state<<5   r12 skip/gate
 *   r14 chain head for the deterministic back edge
 *
 * The accumulator is the one trick worth naming.  The table hands back
 *     m = kept_bits + 2^n - 1
 * so that a single ADD both writes the kept bits at the sentinel and moves
 * the sentinel up by n:  acc + (m << pos) == acc_with_bits_and_new_sentinel.
 * That turns "append a variable number of bits" into two instructions with no
 * branch and no second accumulator, and it keeps the oldest bit at bit 0, so
 * the completed byte is always just the low byte of the accumulator even when
 * a wire byte over-delivers and eleven bits are pending.
 */

	.macro  SEG0			/* 7 cycles                          */
	lsls    r1, r1, #1		/* 1  hi nibble * 2 = halfword index */
	mov     r2, r11			/* 1  unstuff state, pre-shifted <<5 */
	orrs    r1, r2			/* 1  index = state*32 + nibble*2    */
	ldrh    r1, [r4, r1]		/* 2  table entry E                  */
	uxtb    r2, r1			/* 1  E&0xFF = new state<<5          */
	mov     r11, r2			/* 1                                 */
	.endm

	.macro  SEG1			/* 11 cycles                         */
	lsls    r2, r1, #21		/* 1  \                              */
	lsrs    r2, r2, #29		/* 1  / n = bits kept from the nibble*/
	lsrs    r1, r1, #11		/* 1  m = bits + 2^n - 1             */
	lsls    r1, r1, r0		/* 1  align to the sentinel          */
	adds    r3, r3, r1		/* 1  append, sentinel moves by n    */
	adds    r0, r0, r2		/* 1  bit count += n                 */
	mov     r1, r8			/* 1  low nibble index * 2           */
	mov     r2, r11			/* 1                                 */
	orrs    r1, r2			/* 1                                 */
	ldrh    r1, [r4, r1]		/* 2                                 */
	.endm

	.macro  SEG2			/* 8 cycles                          */
	uxtb    r2, r1			/* 1                                 */
	mov     r11, r2			/* 1                                 */
	lsls    r2, r1, #21		/* 1                                 */
	lsrs    r2, r2, #29		/* 1                                 */
	lsrs    r1, r1, #11		/* 1                                 */
	lsls    r1, r1, r0		/* 1                                 */
	adds    r3, r3, r1		/* 1                                 */
	adds    r0, r0, r2		/* 1                                 */
	.endm

	.macro  SEG3			/* 9 cycles: CRC16 table step        */
	mov     r2, r10			/* 1  running CRC                    */
	eors    r2, r3			/* 1  crc ^ candidate byte (acc lsb) */
	uxtb    r2, r2			/* 1                                 */
	lsls    r2, r2, #1		/* 1  halfword index                 */
	adds    r2, #128		/* 1  \  + T_CRC16; ADDS takes imm8  */
	adds    r2, #128		/* 1  /                              */
	ldrh    r2, [r4, r2]		/* 2                                 */
	mov     r8, r2			/* 1  park it; commit is decided late*/
	.endm

	.macro  SEG4			/* 11 cycles: store and re-normalise */
	mov     r2, r9			/* 1  rx write pointer               */
	strb    r3, [r2]		/* 2  speculative store of the byte  */
	subs    r0, r0, #8		/* 1  negative <=> byte not finished */
	asrs    r1, r0, #31		/* 1  -1 if NOT finished             */
	mvns    r1, r1			/* 1  r1 = commit mask, -1 or 0      */
	subs    r2, r2, r1		/* 1  advance only on a real byte    */
	mov     r9, r2			/* 1                                 */
	movs    r2, #8			/* 1                                 */
	bics    r2, r1			/* 1  8 if not finished else 0       */
	adds    r0, r0, r2		/* 1  bit count restored or -8       */
	.endm

	.macro  SEG5 ovf		/* 11 cycles: drop byte, gate, bound */
	movs    r2, #8			/* 1                                 */
	ands    r2, r1			/* 1  8 if finished else 0           */
	lsrs    r3, r3, r2		/* 1  drop the byte just emitted     */
	mov     r2, r12			/* 1  skip counter, starts at 2      */
	adds    r2, r2, r1		/* 1  one fewer byte to skip         */
	mov     r12, r2			/* 1                                 */
	adds    r2, #USB_BYTE_BOUND	/* 1  \  structural buffer bound     */
	bmi     \ovf			/* 1  /  not taken                   */
	subs    r2, #USB_BYTE_BOUND	/* 1                                 */
	asrs    r2, r2, #31		/* 1  -1 once SYNC and PID are past  */
	ands    r1, r2			/* 1  final CRC commit mask          */
	.endm

	.macro  SEG6			/* 10 cycles: commit the CRC or not  */
	mov     r2, r8			/* 1  parked table value             */
	ands    r2, r1			/* 1  zero unless we are committing  */
	mov     r8, r2			/* 1                                 */
	movs    r2, #8			/* 1                                 */
	ands    r2, r1			/* 1  shift by 8 if committing, 0 if */
	mov     r1, r10			/* 1                                 */
	lsrs    r1, r1, r2		/* 1  crc >> 8  or  crc >> 0         */
	mov     r2, r8			/* 1                                 */
	eors    r1, r2			/* 1  crc = (crc>>8) ^ T[...]  or crc*/
	mov     r10, r1			/* 1                                 */
	.endm

/* NRZI for eight wire bits at once, and the split into two table indices.
 * This is the piece that does not exist on an 8-bit machine: one shift and
 * one xor decode a whole byte of NRZI, because the predecessor of sample i is
 * simply bit i+1 of the same register. */
	.macro  SEGA			/* 8 cycles                          */
	lsrs    r1, r5, #1		/* 1  every sample's predecessor     */
	eors    r1, r5			/* 1  1 = transition                 */
	mvns    r1, r1			/* 1  NRZI: no transition = data 1   */
	uxtb    r1, r1			/* 1  decoded byte, bit7 = first bit */
	lsls    r2, r1, #28		/* 1  \                              */
	lsrs    r2, r2, #27		/* 1  / low nibble * 2               */
	mov     r8, r2			/* 1  park it for SEG1               */
	lsrs    r1, r1, #4		/* 1  high nibble                    */
	.endm

/* ========================================================================= */
	.section .text.usb_rx, "ax", %progbits
	.balign 4
	.global usb_rx_engine16
	.thumb_func
	.type   usb_rx_engine16, %function

/* Entry: the D- pin-change interrupt.  Nothing may preempt this (priority 0,
 * PRIOR_ART L-23).  r0-r3 and r12 are already ours by AAPCS; r4-r7 and r8-r11
 * are saved before we touch them, but only AFTER the SE0 test, because a
 * keepalive EOP must be seen within a few tens of cycles (PRIOR_ART L-9). */
usb_rx_engine16:
	ldr     r3, =USB_GPIO_BASE
	ldr     r0, [r3, #USB_IDR_OFS]
	movs    r1, #USB_PINMASK
	ands    r0, r1
	bne     1f
	.global usb_rx_keepalive
usb_rx_keepalive:			/* SE0 at entry: keepalive / reset    */
	bx      lr
1:

	push    {r4-r7, lr}
	mov     r4, r8
	mov     r5, r9
	mov     r6, r10
	mov     r7, r11
	push    {r4-r7}

	mov     r7, r3			/* r7 = GPIO base                     */
	movs    r6, #USB_PINMASK	/* r6 = pin mask                      */

/* ---- SYNC catcher -------------------------------------------------------
 * We cannot lock to the first SYNC edge: exception entry on M0+ is ~15 cycles
 * plus whatever instruction was executing, so by the time we run we may be
 * anywhere inside SYNC cells 1..4.  Instead lock to the LAST SYNC edge, which
 * is unambiguous: SYNC is K J K J K J K K, so every K cell is followed by a J
 * one bit later EXCEPT the seventh, which is followed by another K.
 *
 * Poll granularity is 3 cycles (ldr + lsls + untaken bmi), so the phase error
 * is +-1.5 cycles plus the +-0.5 of the taken exit branch: about +-2 cycles of
 * a +-8 cycle window.  The spin is bounded (PRIOR_ART A-16) so that resume
 * signalling cannot wedge the ISR. */
	movs    r3, #200		/* bounded spin                       */
.Lsync_hunt:
	subs    r3, #1
	beq     .Lgiveup
.Lwait_j:				/* first get to a J cell              */
	ldr     r0, [r7, #USB_IDR_OFS]
	lsls    r0, r0, #(31 - USB_DM_BIT)
	bpl     .Lwait_j2
	b       .Lwait_k
.Lwait_j2:
	subs    r3, #1
	bne     .Lwait_j
	b       .Lgiveup
.Lwait_k:				/* then catch the J->K edge           */
	ldr     r0, [r7, #USB_IDR_OFS]
	lsls    r0, r0, #(31 - USB_DM_BIT)
	bmi     .Lwait_k2
	b       .Lk_edge
.Lwait_k2:
	subs    r3, #1
	bne     .Lwait_k
	b       .Lgiveup

.Lk_edge:				/* t0 = this point, +-2 cycles        */
	movs    r0, #4			/* 4*3+2 = 14 cycles of delay ...     */
.Lk_delay:
	subs    r0, #1			/* 1                                  */
	bne     .Lk_delay		/* 2-3 taken, 1 not taken             */
	nop
	nop
	ldr     r0, [r7, #USB_IDR_OFS]	/* t0 + ~22: cell 7 is still K here,  */
	lsls    r0, r0, #(31 - USB_DM_BIT) /* an ordinary K cell is J by now   */
	bpl     .Lsync_hunt		/* J -> that was not the last edge    */

/* We are at t0 + ~24, t0 being the start of SYNC cell 7.  The first sample
 * must land in the middle of the first PID cell = t0 + 2*16 + 8 = t0 + 40.
 * The set-up below is exactly that remaining delay, and it also primes the
 * pipeline with the one wire byte we already know: SYNC itself.  SYNC decodes
 * to 0x01 in first-bit-at-bit7 form (verified in the note), so the pipeline
 * enters cell 0 as if it had just finished sampling it. */
	ldr     r4, =usb_tables		/* 4  table base (entry only)         */
	movs    r0, #2			/* 1  lo nibble of 0x01, times two    */
	movs    r1, #0			/* 1  hi nibble of 0x01, times two    */
	movs    r5, #1			/* 1  last SYNC sample was K, D+ = 1  */
	movs    r2, #1			/* 1                                  */
	lsls    r3, r2, #24		/* 1  accumulator: empty, sentinel b24*/
	movs    r2, #0			/* 1  unstuff state 0 entering SYNC   */
	mov     r11, r2			/* 1                                  */
	movs    r2, #2			/* 1  skip SYNC and PID from the CRC  */
	mov     r12, r2			/* 1                                  */
	ldr     r2, =0x0000FFFF		/* 2  CRC16 init                      */
	mov     r10, r2			/* 1                                  */
	ldr     r2, =usb_rxbuf		/* 4                                  */
	mov     r9, r2			/* 1                                  */
	ldr     r2, =(usb_rx_chain + 1)	/* 4  \ loop head; bit0 set for bx  */
	mov     r14, r2			/* 1  /                               */
	b       usb_rx_chain

.Lgiveup:
	b       .Lusb_done
.Lovf_a:
	b       .Lusb_overrun
	.ltorg

/* EOP stubs for cells 0..3.  They live in front of the chain so that the
 * conditional branch from an early cell reaches them (Thumb B<cond> is a
 * +-254 byte offset and the chain is longer than that).  r8 carries the count
 * of wire bits of the current byte that are NOT real. */
	.global usb_rx_eop0
usb_rx_eop0:	movs    r2, #8
	mov     r14, r2
	b       usb_rx_flush0
	.global usb_rx_eop1
usb_rx_eop1:	movs    r2, #7
	mov     r14, r2
	b       usb_rx_flush1
	.global usb_rx_eop2
usb_rx_eop2:	movs    r2, #6
	mov     r14, r2
	b       usb_rx_flush2
	.global usb_rx_eop3
usb_rx_eop3:	movs    r2, #5
	mov     r14, r2
	b       usb_rx_flush3

/* ======================= the timed chain: 8 cells x 16 cycles =============
 * Cell 7 closes the wire byte, decodes it (SEGA) and takes the back edge.
 * Cells 0..6 carry the pipeline for the byte that cell 7 decoded.
 * No cell contains a taken branch.  The only branches are the eight SE0
 * tests, which are not taken until the packet ends: 1 cycle, no ambiguity. */
	.balign 4
	.global usb_rx_cell0
usb_rx_chain:
	CELL    0
	SEG0
	USE     7			/* 5 + 7 + 4 nop = 16                 */
	CELL    1
	SEG1
	USE     11			/* 5 + 11      = 16                   */
	CELL    2
	SEG2
	USE     8			/* 5 + 8 + 3 nop = 16                 */
	CELL    3
	SEG3
	USE     9			/* 5 + 9 + 2 nop = 16                 */
	CELL    4
	SEG4
	USE     11			/* 5 + 11      = 16                   */
	CELL    5
	SEG5    .Lovf_a
	USE     11			/* 5 + 11      = 16                   */
	CELL    6
	SEG6
	USE     10			/* 5 + 10 + 1 nop = 16                */
	CELL    7
	SEGA
	USE     8
	bx      r14			/* 3  deterministic back edge         */
	USE     3			/* 5 + 8 + 3 = 16 exactly             */

/* EOP stubs for cells 4..7. */
	.global usb_rx_eop4
usb_rx_eop4:	movs    r2, #4
	mov     r14, r2
	b       usb_rx_flush4
	.global usb_rx_eop5
usb_rx_eop5:	movs    r2, #3
	mov     r14, r2
	b       usb_rx_flush5
	.global usb_rx_eop6
usb_rx_eop6:	movs    r2, #2
	mov     r14, r2
	b       usb_rx_flush6
	.global usb_rx_eop7
usb_rx_eop7:	movs    r2, #1
	mov     r14, r2
	b       usb_rx_flush7

/* ===================== flush: finish the pipeline, untimed ================
 * Entry .LflushK means "the SE0 was sampled in cell K, so segments K..6 of the
 * current byte still have to run".  Same macros as the timed chain, so the
 * two copies cannot disagree. */
	.global usb_rx_flush0
usb_rx_flush0:	SEG0
	.global usb_rx_flush1
usb_rx_flush1:	SEG1
	.global usb_rx_flush2
usb_rx_flush2:	SEG2
	.global usb_rx_flush3
usb_rx_flush3:	SEG3
	.global usb_rx_flush4
usb_rx_flush4:	SEG4
	.global usb_rx_flush5
usb_rx_flush5:	SEG5    .Lovf_b
	.global usb_rx_flush6
usb_rx_flush6:	SEG6
	.global usb_rx_flush7
usb_rx_flush7:
	/* The final, partial wire byte: r8 holds 8-k, the number of bit slots
	 * that were never sampled.  Left-shifting the decoded byte by that
	 * amount moves the k real bits to the top and feeds zeros in below.
	 * Those zeros are decoded data 0s: they can never trip the stuffing
	 * rule, and because the packet's data length is always a whole number
	 * of bytes the accumulator is empty when they arrive, so at most seven
	 * of them accumulate and no spurious byte is ever emitted. */
	mov     r2, r14
	cmp     r2, #8
	beq     .Lrx_tail		/* nothing real in this byte          */
	lsrs    r1, r5, #1
	eors    r1, r5
	mvns    r1, r1
	uxtb    r1, r1
	lsls    r1, r1, r2		/* real bits to the top, zeros below  */
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

/* =========================== tail: validate and dispatch ================== */
.Lovf_b:
	b       .Lusb_overrun

.Lrx_tail:
	mov     r2, r11			/* final unstuff state                */
	cmp     r2, #(7 << 5)		/* sticky stuffing violation          */
	beq     .Lusb_done

	ldr     r6, =usb_rxbuf
	mov     r0, r9
	subs    r0, r0, r6		/* r0 = emitted byte count            */
	cmp     r0, #4			/* SYNC + PID + at least two more     */
	blo     .Lcheck_short

	ldrb    r1, [r6, #0]
	cmp     r1, #0x80		/* SYNC must decode to 0x80           */
	bne     .Lusb_done
	ldrb    r5, [r6, #1]		/* PID byte                           */
	lsrs    r1, r5, #4
	mvns    r1, r1
	uxtb    r1, r1
	lsls    r1, r1, #4
	lsrs    r2, r5, #4
	lsls    r2, r2, #4
	eors    r1, r2
	lsls    r1, r1, #24		/* PID check: high nibble = ~low      */
	bne     .Lusb_done

	movs    r1, #2
	tst     r5, r1
	beq     .Ltoken_or_hs

	/* DATA0 / DATA1: the CRC16 was folded in as the bytes arrived, so all
	 * that is left is the residue.  0xB001 is the reflected-CRC16 residue
	 * over message+CRC (verified numerically; same constant the existing
	 * RISC-V engine uses at rv003usb.S:519). */
	mov     r1, r10
	ldr     r2, =0x0000B001
	cmp     r1, r2
	bne     .Lusb_done
	subs    r3, r0, #2		/* length, PID and SYNC removed       */
	adds    r1, r6, #2		/* payload pointer                    */
	movs    r2, #0
	lsrs    r5, r5, #3
	movs    r2, #1
	ands    r2, r5			/* DATA1 -> which_data = 1            */
	movs    r0, #0
	b       .Lcall_data

.Lcheck_short:
	b       .Lusb_done

.Ltoken_or_hs:
	movs    r1, #1
	tst     r5, r1
	beq     .Lhandshake

	/* Token: CRC5 over the eleven address/endpoint bits plus the five CRC
	 * bits leaves the constant residue 0x06 (verified numerically). */
	cmp     r0, #4
	bne     .Lusb_done
	movs    r3, #0x1F		/* CRC5 init                          */
	ldrb    r0, [r6, #2]
	bl      .Lcrc5_byte
	ldrb    r0, [r6, #3]
	bl      .Lcrc5_byte
	cmp     r3, #6
	bne     .Lusb_done
	b       .Lcall_token

.Lhandshake:
	b       .Lcall_ack

/* CRC5, one byte, two nibble steps.  r3 = crc, r0 = byte, r4 = tables. */
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

/* -------- hand-off to the shared C layer.  Register wiring mirrors the
 * RISC-V engine (rv003usb.S:440-530); the exact prototypes are a merge seam,
 * not part of the timed design. */
.Lcall_data:
	bl      usb_pid_handle_data
	b       .Lusb_done
.Lcall_token:
	movs    r1, #0x0F
	ands    r1, r5
	cmp     r1, #0x01
	beq     .Lt_out
	cmp     r1, #0x09
	beq     .Lt_in
	cmp     r1, #0x0D
	bne     .Lusb_done
	bl      usb_pid_handle_setup
	b       .Lusb_done
.Lt_out:
	bl      usb_pid_handle_out
	b       .Lusb_done
.Lt_in:
	bl      usb_pid_handle_in
	b       .Lusb_done
.Lcall_ack:
	bl      usb_pid_handle_ack
	b       .Lusb_done

.Lusb_overrun:
	/* A packet longer than any legal LS packet.  The bound is checked
	 * before the pointer that the next speculative store would use is
	 * committed, so the buffer cannot be walked off the end from the bus. */
	b       .Lusb_done

.Lusb_done:
	pop     {r4-r7}
	mov     r8, r4
	mov     r9, r5
	mov     r10, r6
	mov     r11, r7
	pop     {r4-r7, pc}

	.size   usb_rx_engine16, . - usb_rx_engine16

/* ============================== tables ==================================
 * Generated and verified by the script reproduced in engine16_vusb.md.
 * They sit in the same section as the code so that one `adr` reaches them
 * after the whole blob has been copied to SRAM (position independent).
 */
	.balign 4
	.global usb_tables
usb_tables:
	/* T_UT: 8 unstuff states x 16 wire nibbles.
	 * halfword = bits<<11 | n<<8 | newstate<<5, where `bits` is already
	 * biased so that a single ADD both inserts the kept bits and moves the
	 * accumulator sentinel.  Row 7 is the sticky stuffing-violation row:
	 * it emits nothing and stays in row 7, so a violation costs zero
	 * cycles in the timed path and is read out once at the end. */
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
	.hword	0x00e0, 0x00e0, 0x00e0, 0x00e0, 0x00e0, 0x00e0, 0x00e0, 0x00e0
	.hword	0x00e0, 0x00e0, 0x00e0, 0x00e0, 0x00e0, 0x00e0, 0x00e0, 0x00e0
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
	.balign 4
	.global usb_rxbuf
usb_rxbuf:
	.space  USB_RXBUF_LEN
