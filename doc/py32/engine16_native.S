/* engine16_native.S — competitor NATIVE
 *
 * USB low-speed receive on PY32 (Cortex-M0+, 24 MHz) using TIM input capture in
 * slave-reset mode plus DMA as the acquisition front end, and an UNTIMED
 * streaming decoder over inter-transition intervals.
 *
 * There is no bit cell in this file. The hardware timestamps every NRZI
 * transition; the code below turns a stream of intervals into bytes. Its cost
 * is an upper bound, not an equality: see engine16_native.md §6.
 *
 * Peripheral configuration lives in engine16_native.c — it is 40 register
 * writes executed once and nothing about it is timed.
 *
 * Assembles with:
 *   arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c
 *
 * External contract (ENGINE16_SPEC §4) is unchanged: entry from the D- pin
 * change interrupt, output = bytes in a buffer, a length and a PID, then a call
 * into the shared C layer.
 */

	.syntax unified
	.cpu cortex-m0plus
	.thumb

/* ------------------------------------------------------------------ layout */

#define RING_LEN        112     /* one byte per NRZI transition; the longest LS
                                 * packet (8 B data) is 96 bit times, so 96
                                 * transitions is the hard maximum            */
#define RXBUF_LEN       12      /* SYNC+PID+8 data+CRC16 decoded = 11 bytes    */

/* interval table geometry: two rows (normal / stuffed), 17 intervals each */
#define TBL_STRIDE      68      /* 17 entries * 4 bytes                        */

/* GPIO register offsets, identical on F002B and F030/F003
 * (BUILD_FACTS.md §7: MODER 0x00 .. IDR 0x10 .. BSRR 0x18) */
#define IDR_OFFSET      0x10

/* D+/D- bit positions in IDR — a per-site macro in the real port
 * (usb_port_py32_asm.h). Defaults chosen so this file assembles standalone. */
#ifndef USB_DM_BIT
#define USB_DM_BIT      4
#endif
#ifndef USB_DP_BIT
#define USB_DP_BIT      3
#endif
#define SE0_MASK        ((1 << USB_DM_BIT) | (1 << USB_DP_BIT))

/* ------------------------------------------------------------------- data  */

	.section .bss.native, "aw", %nobits
	.align 2
	.global native_ring
native_ring:
	.space  RING_LEN                /* DMA writes here, MSIZE=8. A zero byte
	                                 * means "not captured yet": the shortest
	                                 * legal interval is 16 counts, so 0 is
	                                 * unreachable in-packet.                */
	.global native_rxbuf
native_rxbuf:
	.space  RXBUF_LEN
	.global native_rxlen
native_rxlen:
	.space  4

/* The interval table is built by the assembler and lives with the RAM-resident
 * code, so the load in the hot loop is a 2-cycle RAM read, not a 4-cycle flash
 * literal (CHIP_FACTS_XIAMATSU §1: from RAM-resident code a flash literal-pool
 * load costs 4). */

	.section .datacode, "ax", %progbits
	.align 2

/* entry = (next_stuff_row_offset << 24) | (count << 16) | pattern
 *
 * An interval of n bit times means: one data 0 (the transition itself) followed
 * by n-1 data 1s.  LSB-first, so with the first bit at accumulator position 0:
 *     pattern = ((1 << (n-1)) - 1) << 1        count = n
 * In the stuffed row the leading 0 is the bit-stuffing insert and is removed:
 *     pattern = (1 << (n-1)) - 1               count = n-1
 * Either way the emitted run of 1s is n-1 long, so the next transition's 0 is a
 * stuff bit exactly when n == 7 — that is the only state the decoder carries,
 * and the table carries it instead of a register.
 *
 * n >= 8 cannot occur inside a packet: bit stuffing bounds the gap at 7 bit
 * times (112 counts). Those entries advance the position by 31, which drives
 * the loop out through the byte path and is recognised there as a malformed
 * packet — this is the free stuffed-slot validation of PRIOR_ART D-7.
 */
	.macro TBLROW stuffrow
	.set n, 0
	.rept 17
	  .if (n >= 1) && (n <= 7)
	    .set nx, 0
	    .if n == 7
	      .set nx, TBL_STRIDE       /* six 1s emitted -> next 0 is stuffed */
	    .endif
	    .if \stuffrow
	      .word (nx << 24) | ((n - 1) << 16) | ((1 << (n - 1)) - 1)
	    .else
	      .word (nx << 24) | (n << 16) | ((((1 << (n - 1)) - 1)) << 1)
	    .endif
	  .else
	    .word (31 << 16)            /* n == 0 or n >= 8: malformed */
	  .endif
	  .set n, n + 1
	.endr
	.endm

	.global native_ivtbl
	.align 2
native_ivtbl:
	TBLROW 0
	TBLROW 1

/* ------------------------------------------------------------- the decoder */
/*
 * r0  interval -> n -> table byte index
 * r1  p, bit position inside the byte being assembled (0..7)
 * r2  count / scratch
 * r3  table entry -> pattern
 * r4  stuff row offset, 0 or TBL_STRIDE
 * r5  ring read pointer
 * r6  interval table base
 * r7  bit accumulator, LSB-first, filled upwards from bit 0
 *
 * r8  output pointer      r9  output limit      r10 GPIO base
 * Nothing in the hot loop touches r8-r10; they are only used on the
 * once-per-byte path and at EOP.
 */

	.global native_decode
	.type native_decode, %function
	.align 2
native_decode:
	push	{r4, r5, r6, r7, lr}

	ldr	r5, =native_ring
	ldr	r6, =native_ivtbl
	ldr	r0, =native_rxbuf
	mov	r8, r0
	adds	r0, #RXBUF_LEN
	mov	r9, r0                  /* hard output limit — the buffer bound is
	                                 * a register comparison on the untimed
	                                 * byte path, never inside the loop
	                                 * (ENGINE16_SPEC §3.8)                 */
	ldr	r0, =USB_GPIO_BASE
	mov	r10, r0

	movs	r1, #0                  /* p   = 0     */
	movs	r4, #0                  /* row = normal */
	movs	r7, #0                  /* acc = 0     */

/* --- hot loop.  Ledger in engine16_native.md §6.3.  22 cycles, RAM column. */
.Lloop:
	ldrb	r0, [r5, #0]            /* 2  interval, 0 = not captured yet     */
	cmp	r0, #0                  /* 1                                     */
	beq	.Lwait                  /* 1  not taken on the hot path          */
	adds	r5, #1                  /* 1                                     */
	adds	r0, #8                  /* 1  round to nearest bit time          */
	lsrs	r0, r0, #4              /* 1  n = (interval + 8) / 16, 0..16     */
	lsls	r0, r0, #2              /* 1  n * 4                              */
	adds	r0, r0, r4              /* 1  + stuff row                        */
	ldr	r3, [r6, r0]            /* 2  table entry                        */
	lsrs	r4, r3, #24             /* 1  next stuff row offset              */
	lsls	r2, r3, #8              /* 1  drop the row byte                  */
	lsrs	r2, r2, #24             /* 1  count                              */
	uxth	r3, r3                  /* 1  pattern                            */
	lsls	r3, r1                  /* 1  pattern << p                       */
	orrs	r7, r3                  /* 1  acc |= pattern << p                */
	adds	r1, r1, r2              /* 1  p += count                         */
	cmp	r1, #8                  /* 1                                     */
	blt	.Lloop                  /* 2-3 taken on the hot path             */
	                                /* ------------------------------------- */
	                                /* 22 (branch 3) / 21 (branch 2)         */

/* --- once per byte: store, bound, renormalise.  14 cycles, untimed. */
.Lbyte:
	cmp	r1, #16                 /* count 31 from a malformed interval    */
	bge	.Lmalformed
	mov	r0, r8
	mov	r2, r9
	cmp	r0, r2
	bhs	.Loverrun               /* structural buffer bound               */
	strb	r7, [r0, #0]
	adds	r0, #1
	mov	r8, r0
	lsrs	r7, r7, #8              /* keep the spill bits                   */
	subs	r1, #8
	b	.Lloop

/* --- ring empty.  The only place SE0 is looked for.
 *
 * This is the correction that matters: SE0 is a LEVEL (both lines low), and
 * the capture stream cannot express it — the EOP shows up on D- only as an
 * ordinary 2..7 bit-time interval (engine16_native.md §5). So EOP is a GPIO
 * read, and it is seen only when the decoder has drained the ring. A backlogged
 * decoder sees EOP late by exactly its backlog. That is the whole timing
 * argument of this design, and it is why it does not fit at 24 MHz.
 *
 * The spin is bounded (PRIOR_ART A-16: resume signalling holds K for >= 20 ms;
 * an unbounded wait for a bus transition is a hang).
 */
	.align 2
.Lwait:
	ldr	r2, =0x00000400         /* ~= 1024 polls, well over one packet   */
.Lwait_loop:
	mov	r0, r10
	ldr	r0, [r0, #IDR_OFFSET]   /* 1 cycle: GPIO is on the IOPORT        */
	movs	r3, #SE0_MASK
	tst	r0, r3
	beq	.Leop                   /* both lines low = SE0 = end of packet  */
	ldrb	r0, [r5, #0]
	cmp	r0, #0
	bne	.Lloop                  /* a new interval landed                 */
	subs	r2, #1
	bne	.Lwait_loop
	b	.Lgiveup

/* --- tail.  Everything below is outside any deadline except the USB
 * turnaround, and is counted as a block in engine16_native.md §6.4. */
.Leop:
	mov	r0, r8
	ldr	r2, =native_rxbuf
	subs	r0, r0, r2
	ldr	r2, =native_rxlen
	str	r0, [r2, #0]
	movs	r0, #0                  /* 0 = packet delivered                  */
	b	.Ldone

.Lmalformed:
	movs	r0, #1
	b	.Ldone
.Loverrun:
	movs	r0, #2
	b	.Ldone
.Lgiveup:
	movs	r0, #3
.Ldone:
	pop	{r4, r5, r6, r7, pc}
	.size native_decode, . - native_decode

/* --- ring re-arm.  Untimed tail work between transactions: the DMA is in
 * circular mode and never stops, so the sentinel has to be rewritten over the
 * entries just consumed.  ~2 cycles per entry from RAM-resident code. */
	.global native_rearm
	.type native_rearm, %function
	.align 2
native_rearm:
	ldr	r1, =native_ring
	movs	r2, #0
	movs	r3, #RING_LEN
1:	strb	r2, [r1, #0]
	adds	r1, #1
	subs	r3, #1
	bne	1b
	bx	lr
	.size native_rearm, . - native_rearm

	.align 2
	.ltorg

/* USB_GPIO_BASE is a per-site constant in the real port; GPIOB on the current
 * py32 branch (BUILD_FACTS.md §7). */
	.equ USB_GPIO_BASE, 0x50000400
