/* turnaround_sketch.S - the post-EOP path, rewritten for latency.
 *
 * In service of doc/py32/turnaround.md.  This is NOT a replacement engine:
 * it is the flush and tail of engine16_merged.S rewritten so that the cycle
 * figures in turnaround.md section 5 are produced by tools/engine16_cyc.py
 * on a real object instead of being asserted.
 *
 * Assembles with
 *   arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c \
 *       doc/py32/turnaround_sketch.S -o t.o
 * Counted with
 *   python3 tools/engine16_cyc.py t.o --exec ram --ioport r7
 *
 * WHAT CHANGES vs engine16_merged.S, and why each is legitimate here:
 *
 *  1. The flush is untimed, so the branchless machinery that exists only to
 *     make a cell exactly 16 cycles is pure overhead.  SEG3..SEG6 (41 cycles:
 *     masked store, commit mask, speculative CRC, gate, commit-or-not) become
 *     one FEMIT with two not-taken branches.
 *  2. The emitted-byte count and the rxbuf base are hoisted out of r12/r9
 *     into r6/r7, which are dead after EOP (r6 was the pin mask, r7 the GPIO
 *     base).  A high register costs a MOV per use; a low one costs nothing.
 *  3. The loop bound (cmp #24 / bhs) is dropped: the flush is straight-line
 *     and processes at most two wire bytes, and the timed chain already
 *     bounded the count before EOP.  The *address* bound (mask to 5 bits)
 *     is kept - it is the structural guarantee of DEFECTS_VERIFIED D-2 and
 *     costs 2 cycles.
 *  4. A partial wire byte of k <= 4 bits skips its low nibble entirely.  The
 *     low nibble is then all padding zeros, and engine16_merged.S:455-462
 *     already establishes that those zeros are inert; feeding them through
 *     the table therefore buys nothing.
 *  5. The tail contains one residue comparison and an indirect branch to a
 *     handler address decided during reception (turnaround.md section 4).
 *     No SYNC check, no PID check, no PID dispatch, no CRC5, no literal load.
 *
 * Register contract at entry, identical to engine16_merged.S:
 *   r0 bit count / sentinel position   r1 pipeline word     r2 scratch
 *   r3 accumulator                     r4 table base        r5 wire packer
 *   r8 park (low nibble*2)             r9 rxbuf base        r10 CRC16
 *   r11 unstuff state<<5               r12 emitted byte count
 *   r14 8-K, the number of unsampled bit slots in the partial byte
 * and after the EOP stub:  r6 = emitted count, r7 = rxbuf base.
 */

	.syntax unified
	.cpu    cortex-m0plus
	.thumb

	.equ    USB_RXBUF_MASK,  27      /* 32-byte buffer: index &= 31   */
	.equ    T_CRC16,        256      /* halfword offset of the table  */

/* ------------------------------------------------------------------ */
	.macro  FSEG0                   /* 7 - unchanged from SEG0        */
	lsls    r1, r1, #1
	mov     r2, r11
	orrs    r1, r2
	ldrh    r1, [r4, r1]
	uxtb    r2, r1
	mov     r11, r2
	.endm

	.macro  FAPPEND                 /* 6 - the append half of SEG1    */
	lsls    r2, r1, #21
	lsrs    r2, r2, #29
	lsrs    r1, r1, #11
	lsls    r1, r1, r0
	adds    r3, r3, r1
	adds    r0, r0, r2
	.endm

	.macro  FLOOK                   /* 5 - the second lookup of SEG1  */
	mov     r1, r8
	mov     r2, r11
	orrs    r1, r2
	ldrh    r1, [r4, r1]
	.endm

	.macro  FSEG2                   /* 8 - SEG2 without the mov r1,r9 */
	uxtb    r2, r1
	mov     r11, r2
	lsls    r2, r1, #21
	lsrs    r2, r2, #29
	lsrs    r1, r1, #11
	lsls    r1, r1, r0
	adds    r3, r3, r1
	adds    r0, r0, r2
	.endm

/* FEMIT replaces SEG3+SEG4+SEG5+SEG6 (41 cycles in the timed chain). */
	.macro  FEMIT
	subs    r0, r0, #8              /* 1  <0 -> no byte completed     */
	bmi     99f                     /* 1 not taken                    */
	lsls    r2, r6, #USB_RXBUF_MASK /* 1  \ structural address bound  */
	lsrs    r2, r2, #USB_RXBUF_MASK /* 1  /                           */
	strb    r3, [r7, r2]            /* 2                              */
	adds    r6, r6, #1              /* 1                              */
	cmp     r6, #3                  /* 1  CRC covers from byte 3 on   */
	blo     98f                     /* 1 not taken                    */
	mov     r2, r10                 /* 1                              */
	eors    r2, r3                  /* 1                              */
	uxtb    r2, r2                  /* 1                              */
	lsls    r2, r2, #1              /* 1                              */
	adds    r2, #(T_CRC16 / 2)      /* 1                              */
	adds    r2, #(T_CRC16 / 2)      /* 1                              */
	ldrh    r2, [r4, r2]            /* 2                              */
	mov     r1, r10                 /* 1                              */
	lsrs    r1, r1, #8              /* 1                              */
	eors    r1, r2                  /* 1                              */
	mov     r10, r1                 /* 1                              */
98:	lsrs    r3, r3, #8              /* 1  drop the byte just emitted  */
99:
	.endm

/* ================= EOP stubs: hoist, then enter at segment K ======= */
	.macro  FEOP idx, unsampled
tr_eop\idx:
	movs    r2, #\unsampled         /* 1                              */
	mov     r14, r2                 /* 1                              */
	mov     r6, r12                 /* 1  count  -> low register      */
	mov     r7, r9                  /* 1  rxbuf  -> low register      */
	b       tr_flush\idx            /* 2-3                            */
	.endm

	.text
	.balign 4
	FEOP 0, 8
	FEOP 1, 7
	FEOP 2, 6
	FEOP 3, 5
	FEOP 4, 4
	FEOP 5, 3
	FEOP 6, 2
	FEOP 7, 1

/* ===================== flush of the byte in flight ================= */
tr_flush0:	FSEG0
tr_flush1:	FAPPEND
		FLOOK
tr_flush2:	FSEG2
tr_flush3:
tr_flush4:
tr_flush5:
tr_flush6:	FEMIT
tr_flush7:

/* ===================== the partial wire byte ======================= */
/* r14 = 8-k.  Shift the k real bits to the top of the byte; the zeros that
 * come in below are inert (engine16_merged.S:455-462).  With k <= 4 the low
 * nibble is entirely zeros and is skipped. */
	mov     r2, r14                 /* 1                              */
	cmp     r2, #8                  /* 1                              */
	beq     .Ltr_tail               /* 1 not taken when k > 0         */
	lsrs    r1, r5, #1              /* 1  \                           */
	eors    r1, r5                  /* 1   | NRZI, 8 wire bits at once*/
	mvns    r1, r1                  /* 1   |                          */
	uxtb    r1, r1                  /* 1  /                           */
	lsls    r1, r1, r2              /* 1  real bits to the top        */
	uxtb    r1, r1                  /* 1                              */
	cmp     r2, #4                  /* 1  k <= 4  <=>  8-k >= 4       */
	bhs     .Ltr_hi_only            /* 1 not taken on the k >= 5 path */

	lsls    r2, r1, #28             /* 1  \ low nibble * 2 -> park    */
	lsrs    r2, r2, #27             /* 1  /                           */
	mov     r8, r2                  /* 1                              */
	lsrs    r1, r1, #4              /* 1  high nibble                 */
	FSEG0
	FAPPEND
	FLOOK
	FSEG2
	FEMIT
	b       .Ltr_tail               /* 2-3                            */

.Ltr_hi_only:                           /* k <= 4: low nibble is padding  */
	lsrs    r1, r1, #4              /* 1                              */
	FSEG0
	FAPPEND
	FEMIT
	/* fall through */

/* =========================== the tail ============================== */
/* Everything decidable from bytes 0 and 1 was decided during reception and
 * left at rxbuf+28 as a handler address (the byte count is bounded to 24,
 * so the store can never reach it) (turnaround.md section 4).  What is
 * left here is the CRC16 residue and one indirect branch.  0xB001 is built
 * from immediates, not loaded: a literal load from RAM-resident code costs 4
 * (ENGINE16_SPEC.md section 2). */
.Ltr_tail:
	mov     r12, r6                 /* 1  emitted count back home     */
	movs    r2, #0xB0               /* 1  \                           */
	lsls    r2, r2, #8              /* 1   | r2 = 0xB001              */
	adds    r2, #1                  /* 1  /                           */
	mov     r1, r10                 /* 1                              */
	cmp     r1, r2                  /* 1                              */
	bne     .Ltr_reject             /* 1 not taken                    */
	ldr     r2, [r7, #28]           /* 2  pre-decided handler address */
	bx      r2                      /* 3  -> the TX arm               */

.Ltr_reject:
	bx      lr
