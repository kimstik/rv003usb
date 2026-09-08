/*
 * engine16_tx.S - USB low-speed (1.5 Mbit/s) TRANSMIT engine, Cortex-M0+,
 * SYSCLK 24 MHz, EXACTLY 16 cycles per bit cell.  The transmit half of
 * engine16_merged.S; same operating point, same cost model, same standard.
 *
 * The idea, in one paragraph.  NRZI encode is "toggle on 0", so a bit cell
 * that consumes one already-stuffed wire bit costs five cycles and contains
 * no branch and no data-dependent work:
 *
 *     lsrs r4,r4,#1 ; sbcs r2,r2 ; ands r2,r6 ; eors r5,r2 ; str r5,[r7,#BSRR]
 *
 * `sbcs r2,r2` turns the carry into 0 / -1 in one instruction, so the whole
 * of NRZI is one AND and one XOR.  Everything hard - bit stuffing, byte
 * disassembly, CRC16, the byte source - runs ONE BYTE AHEAD in the eleven
 * cycles each cell has left over, exactly as engine16_merged.S runs its
 * receive pipeline one wire byte behind.  A register-offset table indexed by
 * (stuff state, data nibble) returns the stuffed wire bits, their count and
 * the next state in one 2-cycle RAM load - the inverse of the receive
 * engine's T_UT, biased the same way (m = w + 2^n - 1) so appending a
 * nibble's worth of wire bits is one ADD.
 *
 * The asymmetry the receive direction does not have: a stuffed bit EXTENDS
 * the output, so a data byte is 8, 9 or 10 wire bits and the number of cells
 * per byte is not constant.  It is absorbed by entering a TEN-cell chain at a
 * computed offset: cells P0,P1 carry no pipeline work and are skipped when
 * they are not needed; cells S0..S7 always run and carry the eight pipeline
 * segments.  The entry is a `bx` through a dispatch word - 3 cycles flat
 * (ENGINE16_SPEC.md S2), not the 2-3 of a `b` - and the dispatch index falls
 * out of the accumulator's own sentinel bit for free.
 *
 * Mechanisms inherited, with attribution:
 *   engine16_merged.S / VUSB - one-byte-ahead software pipeline; register-
 *     offset table folding stuffing and byte disassembly into one access;
 *     the biased-add accumulator (m = bits + 2^n - 1) so a variable number of
 *     bits is appended by a single ADD and the sentinel moves itself.
 *   engine16_merged.S / CLEANSHEET - the carry chain, here in reverse: the
 *     wire bit leaves the shift register through the carry flag.
 *   engine16_merged.S / GRAINUUM - unroll at byte granularity, so "which
 *     cell" is a compile-time fact and each cell can carry its own segment.
 *   engine16_native.md S7 - "NRZI is toggle on 0, so a packet is a list of
 *     toggles".  Its conclusion (hand the list to a timer + DMA) is NOT taken
 *     - engine16_tx.md S8 says why, with numbers - but the observation is
 *     what makes this bit cell five cycles instead of nine.
 *
 * Placement: USB_ENGINE16_FLASH, exactly as in engine16_merged.S, and the
 * two files MUST be built with the same value - the table block below is
 * read by both engines and a table in the other memory costs 4 per lookup,
 * which neither engine's cells can absorb.  ENGINE16_SPEC.md S2 RAM column:
 * ordinary instruction 1, GPIO store 1 (IOPORT), RAM load/store 2, BX 3,
 * B 2-3, taken conditional 2-3.
 *
 * This engine holds 16 cycles in BOTH residencies.  It did not always: the
 * three accesses to usb_txbuf - TXSEG0's `ldrb` and TXSEG2/TXSEG3's two
 * `strb` - cost 4 from flash-resident code instead of 2, and put S0, S2 and
 * S3 two cycles over.  (An earlier note here said seven cells were over.
 * That was tools/engine16_cyc.py reading `--flashdata r14` and comparing it
 * against objdump's `lr`, so every table lookup was charged as a RAM access;
 * the tool now normalises the aliases and tracks the base through `mov`.)
 *
 * The three were bought back, not redistributed - there was nothing to
 * redistribute, the flash column totalled 94 against 88:
 *   TXSEG0  the commit mask stops being a two-sided pointer comparison
 *           recomputed per byte and becomes one bit of a shift register
 *           built at setup: 8 cycles down to 3.
 *   TXSEG3  the two CRC `strb` become one `strh`, which needs the address
 *           even, which the setup arranges by starting the stream one byte
 *           in when the length is odd.  One RAM access instead of two.
 * The flash column is now 81 and the RAM column 77, and each segment's own
 * count is declared as TXSEG<n>_CYC so the cell padding follows the
 * residency instead of assuming 11.  See engine16_tx.md S6.4.
 *
 * Post-review change (ENGINE16_REVIEW.md, the size item): this file holds the
 * one table block BOTH engines index, because the same 512-byte T_CRC16 was
 * assembled into each of them.  engine16_merged.S has no tables of its own
 * any more; see the note above the block for the layout and why sharing
 * forces it.  No instruction in this engine changed and no cell moved.
 *
 * Assembles with:
 *   arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c
 */

	.syntax unified
	.cpu    cortex-m0plus
	.thumb

/* ------------------------------------------------------------------ port
 * PY32 GPIOB, IOPORT bus, 0x50000400; MODER 0x00, IDR 0x10, BSRR 0x18
 * (BUILD_FACTS.md S7, identical on F002B and F030/F003).  Pin numbers follow
 * bootloader/usb_config.h, the same defaults engine16_merged.S uses. */
#ifndef USB_GPIO_BASE
#define USB_GPIO_BASE   0x50000400
#endif
#define USB_MODER_OFS   0x00
#define USB_BSRR_OFS    0x18
#ifndef USB_DP_BIT
#define USB_DP_BIT      3
#endif
#ifndef USB_DM_BIT
#define USB_DM_BIT      4
#endif
#define USB_PINMASK     ((1 << USB_DP_BIT) | (1 << USB_DM_BIT))

/* BSRR words.  Low speed: J = D- high, D+ low; K = D+ high, D- low; SE0 =
 * both low.  Consistent with engine16_merged.S, which primes its wire packer
 * with "last SYNC sample was K: D+ = 1". */
#define BSRR_J          ((1 << USB_DM_BIT) | (1 << (USB_DP_BIT + 16)))
/* One XOR of this word swaps set and reset for both pins, i.e. J <-> K. */
#define BSRR_TOGGLE     (USB_PINMASK | (USB_PINMASK << 16))

/* MODER: two bits per pin, 01 = general purpose output, 00 = input. */
#define MODER_MASK      ((3 << (2*USB_DP_BIT)) | (3 << (2*USB_DM_BIT)))
#define MODER_OUT       ((1 << (2*USB_DP_BIT)) | (1 << (2*USB_DM_BIT)))

/* Staging buffer: SYNC, PID, payload, CRC16.  The longest sequence the
 * engine can be asked to walk is SYNC + PID + 8 + CRC16 = 12 slots plus one
 * over-fetch = 13.  The payload length is clamped once, in the untimed
 * setup, so no load or store in the timed chain can leave the buffer. */
#define TXBUF_LEN       16
#define TX_MAXPAY       8

/* Code placement.  See the note in the header, and keep it identical to
 * engine16_merged.S's USB_ENGINE16_FLASH: the table block at the end of this
 * file is shared, so both engines are in the same memory or one of them
 * reads its tables at 4 cycles a lookup. */
#ifndef USB_ENGINE16_FLASH
#define USB_ENGINE16_FLASH      1
#endif
#if USB_ENGINE16_FLASH
#define USB_ENGINE16_SECTION    .section .text.engine16, "ax", %progbits
#define USB_RAM_ACCESS          4
#else
#define USB_ENGINE16_SECTION    .section .datacode, "ax", %progbits
#define USB_RAM_ACCESS          2
#endif

/* Per-segment cycle counts.  The staging buffer is in RAM whatever the engine
 * does, so the two segments that touch it cost 2 cycles more when the engine
 * runs from flash - the same asymmetry engine16_merged.S carries as
 * USB_RAM_ACCESS.  The table block is in the engine's OWN section, so a
 * lookup is 2 in both residencies and the segments that only read tables are
 * the same number twice. */
#define TXSEG0_CYC      (5+USB_RAM_ACCESS)
#define TXSEG1_CYC      11
#define TXSEG2_CYC      7
#define TXSEG3_CYC      (7+USB_RAM_ACCESS)
#define TXSEG4_CYC      11
#define TXSEG5_CYC      11
#define TXSEG6_CYC      11
#define TXSEG7_CYC      11

/* Table layout, one base register (r14).  Row 0 of the halfword table is
 * reused as the dispatch table, because the stuff state is stored as
 * (state+1) and therefore never selects row 0. */
#define T_EXH           0		/*  8 words: the exhausted dispatch   */
#define T_DISPATCH      32		/*  8 words: chain entry, live bytes  */
#define T_TX            64		/*  7 rows x 16 halfwords, row 1 here */
#define T_CRC16         288		/* 256 halfwords                      */

/* ------------------------------------------------- cycle-accounting macros
 * Same device as engine16_merged.S: the ledger is assembled, not asserted.
 * TXCELL pads the cell it closes to exactly 16 and raises .error if it went
 * over; TXCELL_END closes the last one (the hole VUSB's version had). */
	.set    TCYC, 16		/* "the previous cell is already closed" */

	.macro  TXCELL name
	.if     TCYC > 16
	.error  "engine16_tx: bit cell over 16 cycles"
	.endif
	.rept   16 - TCYC
	nop
	.endr
	.set    TCYC, 0
	/* Cell labels are global so tools/engine16_cyc.py can price each cell
	 * on its own.  Unlike engine16_merged.S this costs no range checking:
	 * nothing branches to a cell, they are reached only by `bx` through an
	 * absolute dispatch word, so there is no B<cond> range to prove. */
	.global usb_tx_\name
usb_tx_\name:
	lsrs    r4, r4, #1		/* 1  C = wire bit; 1 = data 1        */
	sbcs    r2, r2			/* 1  r2 = C-1: -1 on a 0, 0 on a 1   */
	ands    r2, r6			/* 1  the toggle word, or nothing     */
	eors    r5, r2			/* 1  NRZI: toggle on 0               */
	str     r5, [r7, #USB_BSRR_OFS]	/* 1  the bit leaves here             */
	.set    TCYC, 5
	.endm

	.macro  TXUSE n
	.set    TCYC, TCYC + (\n)
	.endm

	.macro  TXCELL_END
	.if     TCYC > 16
	.error  "engine16_tx: last bit cell over 16 cycles"
	.endif
	.rept   16 - TCYC
	nop
	.endr
	.set    TCYC, 16
	.endm

/* ====================================================================
 * The pipeline for ONE data byte, cut into eight pieces of at most 11
 * cycles.  While the chain emits byte k these prepare byte k+1.
 *
 * Register contract
 *   r0  working: gated CRC table value, then the accumulator, then the
 *       exhaustion test
 *   r1  working: commit mask, table entry, dispatch offset
 *   r2  temp.  MUST be dead at every segment boundary: TXCELL clobbers it.
 *   r3  source pointer, walking the staging buffer
 *   r4  wire-bit queue: LSB = next bit in time, sentinel above the last bit
 *   r5  BSRR word currently driven     r6  BSRR toggle word
 *   r7  GPIO base
 *   r8  pointer to the two CRC bytes in the staging buffer; doubles as the
 *       exhaustion anchor (TXSEG6/TXSEG7)
 *   r9  the commit shift register: bit k = 1 iff the k-th fetched byte is
 *       NOT a payload byte.  Consumed one bit per TXSEG0.
 *   r10 running CRC16                  r11 (stuff state + 1) << 5
 *   r12 park: the data byte, then n_lo, then the queue
 *   r14 table base
 *
 * T_TX is indexed  (state+1)*32 + nibble*2  and returns
 *     entry = m<<9 | (n-4)<<8 | (state'+1)<<5,   m = w + 2^n - 1
 * where w is the four data bits with any stuffed 0 inserted, oldest at bit 0,
 * and n (4 or 5) is how many wire bits that is.  The bias is what lets one
 * ADD append a variable number of bits and move the sentinel by exactly n:
 * with acc = data + 2^f,   acc + (m<<f) = data + (w<<f) + 2^(f+n).
 * ==================================================================== */

	.macro  TXSEG0			/* fetch the byte, take the mask      */
	ldrb    r0, [r3, #0]		/* A  the byte to prepare             */
	adds    r3, #1			/* 1                                  */
	mov     r1, r9			/* 1  the commit SHIFT REGISTER       */
	lsrs    r1, r1, #1		/* 1  C = 1 iff this byte is NOT a    */
	mov     r9, r1			/* 1     payload byte (MOV to a high  */
					/*       register does not touch C)   */
	sbcs    r1, r1			/* 1  0 if C, -1 if not: the mask     */
	.endm				/*    = 5 + A                         */

	.macro  TXSEG1			/* 11: CRC16 table step, gated        */
	mov     r12, r0			/* 1  park the byte before r0 is lost */
	mov     r2, r10			/* 1  running CRC                     */
	eors    r2, r0			/* 1  crc ^ byte                      */
	uxtb    r2, r2			/* 1                                  */
	lsls    r2, r2, #1		/* 1  halfword index                  */
	adds    r2, #(T_CRC16 / 2)	/* 1  \ ADDS takes imm8; T_CRC16=256  */
	adds    r2, #(T_CRC16 / 2)	/* 1  /                               */
	mov     r0, r14			/* 1  table base                      */
	ldrh    r0, [r0, r2]		/* 2                                  */
	ands    r0, r1			/* 1  zero unless this byte counts    */
	.endm

	.macro  TXSEG2			/* 7: commit the CRC                  */
	movs    r2, #8			/* 1                                  */
	ands    r2, r1			/* 1  shift by 8 only when committing */
	mov     r1, r10			/* 1                                  */
	lsrs    r1, r1, r2		/* 1  crc>>8, or crc unchanged        */
	eors    r1, r0			/* 1  ^ the gated table value         */
	mov     r10, r1			/* 1  crc'                            */
	mvns    r1, r1			/* 1  the wire form is the complement */
	.endm				/*    r1 carries it into TXSEG3       */

	.macro  TXSEG3			/* publish the CRC, low nibble index  */
	mov     r2, r8			/* 1                                  */
	strh    r1, [r2, #0]		/* A  BOTH CRC bytes in ONE store -    */
					/*    the setup shifts the stream by  */
					/*    one byte on odd lengths so this */
					/*    address is always even          */
	mov     r0, r12			/* 1  the byte                        */
	lsls    r1, r0, #28		/* 1  \ (byte & 0x0F) * 2 in two,     */
	lsrs    r1, r1, #27		/* 1  / not movs/ands/lsls in three   */
	mov     r2, r11			/* 1  (state+1)<<5 selects the row    */
	orrs    r1, r2			/* 1                                  */
	adds    r1, #(T_TX - 32)	/* 1  r11 already carries 32 for row  */
	.endm				/*    1; = 7 + A                      */

	.macro  TXSEG4			/* 11: low nibble -> bits, state, n   */
	mov     r2, r14			/* 1                                  */
	ldrh    r1, [r2, r1]		/* 2  ONE access = stuff + disassembly*/
	uxtb    r2, r1			/* 1  (state'+1)<<5                   */
	mov     r11, r2			/* 1                                  */
	lsrs    r0, r1, #9		/* 1  m_lo                            */
	adds    r0, r0, #1		/* 1  acc = w_lo + 2^n_lo (sentinel)  */
	lsls    r2, r1, #23		/* 1  \                               */
	lsrs    r2, r2, #31		/* 1   | n_lo = 4 + bit8              */
	adds    r2, #4			/* 1  /                               */
	mov     r1, r2			/* 1                                  */
	.endm

	.macro  TXSEG5			/* 11: high nibble -> table           */
	mov     r2, r12			/* 1  the byte                        */
	lsrs    r2, r2, #4		/* 1  high nibble = second in time    */
	lsls    r2, r2, #1		/* 1                                  */
	mov     r12, r1			/* 1  park n_lo                       */
	mov     r1, r11			/* 1  the state the low nibble left   */
	orrs    r1, r2			/* 1                                  */
	adds    r1, #(T_TX - 32)	/* 1  the same row bias as TXSEG3     */
	mov     r2, r14			/* 1                                  */
	ldrh    r1, [r2, r1]		/* 2                                  */
	nop				/* 1                                  */
	.endm

	.macro  TXSEG6			/* 11: append, and read n off free    */
	uxtb    r2, r1			/* 1                                  */
	mov     r11, r2			/* 1  state for the next byte         */
	lsrs    r1, r1, #9		/* 1  m_hi                            */
	mov     r2, r12			/* 1  n_lo                            */
	lsls    r1, r1, r2		/* 1  align to the sentinel           */
	adds    r0, r0, r1		/* 1  ONE add: append, and move it    */
	lsrs    r1, r0, #8		/* 1  1 / 2-3 / 4-7 for n = 8 / 9 / 10*/
	mov     r12, r0			/* 1  park the queue                  */
	mov     r2, r8			/* 1                                  */
	subs    r0, r3, r2		/* 1  source - CRC pointer            */
	subs    r0, #3			/* 1  ZERO iff there is no next byte, */
	.endm				/*    negative while bytes remain     */

	.macro  TXSEG7			/* 11: dispatch, or leave for EOP     */
	lsrs    r2, r0, #31		/* 1  1 while bytes remain, 0 at the  */
					/*    end of the packet               */
	lsls    r2, r2, #3		/* 1  8 -> T_DISPATCH, 0 -> T_EXH     */
	adds    r1, r1, r2		/* 1  the SAME n picks the row in     */
					/*    whichever of the two it is      */
	lsls    r1, r1, #2		/* 1  word index                      */
	mov     r2, r14			/* 1                                  */
	ldr     r1, [r2, r1]		/* 2  chain entry, or EOP, or the     */
					/*    trailing stuffed zero           */
	mov     r4, r12			/* 1  the queue, after this cell's bit*/
	bx      r1			/* 3  flat 3: no 2-vs-3 ambiguity     */
	.endm

/* ========================================================================= */
	USB_ENGINE16_SECTION		/* .text.engine16, or .datacode:     */
					/* see the placement note above      */
	.balign 4

/* ================= entry: usb_send_data / usb_send_empty =================
 * Untimed.  The rv003usb C layer's prototypes, unchanged (rv003usb.h:100-101):
 *   void usb_send_data( const void *data, uint32_t length,
 *                       uint32_t poly_function, uint32_t token );
 *   void usb_send_empty( uint32_t token );
 * poly_function == 0 means "append CRC16"; non-zero means "do not", which the
 * C layer uses only for handshakes of length 0 (rv003usb.c:466) and for
 * usb_send_empty.  usb_send_empty is re-expressed here as a zero-length
 * packet WITH CRC, which emits exactly the two 0x00 bytes the RISC-V engine
 * fabricates by hand (rv003usb.S:740-744), because CRC16 over an empty
 * payload is 0xFFFF and the wire form is its complement.
 * ======================================================================== */
	.global usb_send_empty
	.thumb_func
	.type   usb_send_empty, %function
usb_send_empty:
	mov     r3, r0			/* token                             */
	movs    r1, #0			/* no payload                        */
	movs    r2, #0			/* ...and let the CRC supply 0x0000  */
	/* falls into usb_send_data */

	.global usb_send_data
	.thumb_func
	.type   usb_send_data, %function
usb_send_data:
	push    {r4-r7, lr}
	mov     r4, r8
	mov     r5, r9
	mov     r6, r10
	mov     r7, r11
	push    {r4-r7}

	/* Design B (turnaround.md S7, engine16_merged.S) puts the DATA
	 * handshake on the wire itself, from inside the receive ISR and
	 * before the C layer is called at all.  The C layer is then called
	 * anyway, with its source and its prototypes unchanged
	 * (ENGINE16_SPEC.md S4), and rv003usb.c's just_ack ends every path
	 * through usb_pid_handle_data with usb_send_data(0,0,2,0xD2).  That
	 * call must not put a SECOND ACK on the bus, so the receive engine
	 * leaves a one-shot flag here and this consumes it.  It is the whole
	 * of turnaround.md S4.2's "compatibility cost", and it is paid on
	 * this side of the seam rather than in rv003usb.c.
	 *
	 * One-shot, and cleared as it fires, so a lost or reordered call
	 * cannot wedge the transmitter.  A user data handler that transmits
	 * on its own must set RV003USB_USER_DATA_HANDLES_TOKEN, which
	 * disables Design B outright - the same condition turnaround.md S4.2
	 * states. */
	/* Design B's IN direction (design_b_in.md): if an IN response is what
	 * is being produced, render it to wire bits into the endpoint's arm
	 * record before anything else happens to the arguments.  The call is
	 * unconditional and the decision is inside usb_in_render, which is a
	 * `bx lr` when USB_TURNAROUND_B_IN is off, so this file needs to know
	 * nothing about the receive engine's configuration.  It is placed
	 * ahead of the suppression test on purpose: the refresh path suppresses
	 * the transmission and the rendering is the whole point of the call. */
	bl      usb_in_render

	ldr     r4, =usb_tx_suppress
	ldrb    r5, [r4, #0]
	cmp     r5, #0
	beq     2f
	movs    r5, #0
	strb    r5, [r4, #0]
	b       .Ltx_reject		/* nothing driven; pop and return    */
2:

	cmp     r1, #TX_MAXPAY		/* the only buffer bound, and it is  */
	bhi     .Ltx_reject		/* enforced once, outside the timing */
	cmp     r2, #0
	beq     1f
	cmp     r1, #2			/* a CRC-less packet may carry at    */
	bhi     .Ltx_reject		/* most two bytes - see the .md S6   */
1:
	mov     r8, r2			/* park poly_function                */
	ldr     r6, =usb_txbuf
	movs    r4, #1
	ands    r4, r1			/* length & 1: start the stream one  */
	adds    r6, r6, r4		/* byte in on odd lengths, so that   */
					/* the two CRC bytes always land on  */
					/* an even address and TXSEG3 can    */
					/* publish them with one STRH        */
	movs    r4, #0x80		/* SYNC = 00000001, first bit first  */
	strb    r4, [r6, #0]
	strb    r3, [r6, #1]		/* the PID byte                      */

	/* Copy the payload into the staging buffer.  Unrolled backwards so
	 * the ENTRY is computed and the exit is a fall-through: 4 cycles per
	 * byte, 0..8 bytes, no loop, no taken conditional branch. */
	adds    r5, r6, #2		/* destination                       */
	lsls    r2, r1, #2		/* 4 bytes of code per payload byte  */
	ldr     r3, =(.Ltx_copy_end + 1)
	subs    r3, r3, r2
	bx      r3
	ldrb    r2, [r0, #7]
	strb    r2, [r5, #7]
	ldrb    r2, [r0, #6]
	strb    r2, [r5, #6]
	ldrb    r2, [r0, #5]
	strb    r2, [r5, #5]
	ldrb    r2, [r0, #4]
	strb    r2, [r5, #4]
	ldrb    r2, [r0, #3]
	strb    r2, [r5, #3]
	ldrb    r2, [r0, #2]
	strb    r2, [r5, #2]
	ldrb    r2, [r0, #1]
	strb    r2, [r5, #1]
	ldrb    r2, [r0, #0]
	strb    r2, [r5, #0]
.Ltx_copy_end:

	/* r8 = staging + total - 2, where total = 2 + length + (CRC ? 2 : 0).
	 * It is both the address the CRC bytes are published to and the
	 * anchor the exhaustion test in TXSEG7 compares against. */
	adds    r0, r6, r1		/* staging + length                  */
	/* The commit mask, built once, as a SHIFT REGISTER: bit k - the bit
	 * the k-th TXSEG0 consumes - is 1 iff the byte that TXSEG0 fetches is
	 * NOT a payload byte.  Bit 0 is the PID.  The old form recomputed a
	 * two-sided window comparison every byte, eight cycles, in the one
	 * segment that also pays for the RAM fetch.  On a CRC-less packet no
	 * byte is committed at all, which is what the untaken branch leaves. */
	movs    r2, #0
	mvns    r2, r2			/* -1                                */
	lsls    r2, r2, #1		/* ...1110                           */
	mov     r5, r8			/* poly_function                     */
	cmp     r5, #0
	bne     2f
	adds    r0, r0, #2		/* CRC present: two more slots       */
	lsls    r2, r2, r1		/* and `length` payload bytes are    */
					/* committed to it                   */
2:
	adds    r2, #1			/* bit 0: the PID, never committed   */
	mov     r9, r2
	mov     r8, r0

	/* Zero the slot the pipeline over-fetches one byte past the end.
	 * From ANY stuff state 0..5 a 0x00 byte inserts no stuffed bit, so
	 * the chain's own n comes out 8; from state 6 the table's forced
	 * zero makes it 9.  That is what lets TXSEG7 read "a trailing
	 * stuffed zero is owed" off the dispatch index it already has,
	 * without a register, a compare or a cycle.  See S6.3. */
	movs    r2, #0
	strb    r2, [r0, #2]

	adds    r3, r6, #1		/* source pointer: the PID is next   */
	movs    r4, #0x80		/* the queue holds SYNC, n = 8       */
	movs    r0, #0
	mvns    r0, r0
	lsrs    r0, r0, #16
	mov     r10, r0			/* CRC16 init 0xFFFF                 */
	movs    r0, #(2 << 5)		/* SYNC leaves one consecutive 1     */
	mov     r11, r0
	ldr     r0, =usb_tx_tables
	mov     r14, r0

	ldr     r7, =USB_GPIO_BASE
	ldr     r6, =BSRR_TOGGLE
	ldr     r5, =BSRR_J

	/* Turn the bus around: drive J first, then make the pins outputs, so
	 * the line never sees an undriven instant it did not already have. */
	str     r5, [r7, #USB_BSRR_OFS]
	ldr     r0, [r7, #USB_MODER_OFS]
	ldr     r1, =MODER_MASK
	bics    r0, r1
	ldr     r1, =MODER_OUT
	orrs    r0, r1
	str     r0, [r7, #USB_MODER_OFS]

	ldr     r0, =(usb_tx_cellS0 + 1)	/* SYNC is 8 wire bits: enter at S0  */
	bx      r0			/* 3, flat                           */

/* A Thumb conditional branch reaches +-254 bytes and the chain is longer
 * than that, so the two setup rejections trampoline through here.  Placed
 * behind the bx, where nothing can fall into it - the fall-through defect
 * engine16_merged.md S7 records finding in its own EOP stubs. */
.Ltx_reject:
	b       .Ltx_done

/* ======================= the timed chain: 16 cycles a cell ================
 * Ten cells.  P0 and P1 carry no pipeline work and exist only to lengthen
 * the byte when bit stuffing did; S0..S7 always run and carry the eight
 * pipeline segments for the NEXT byte.  Entry is by the dispatch word:
 *   n = 8  -> S0,  n = 9  -> P1,  n = 10 -> P0.
 * The only control transfer in the whole chain is the single `bx` at the end
 * of S7, priced at a flat 3.  There is no conditional branch anywhere in it,
 * so no data path in this engine touches the 2-vs-3 ambiguity at all. */
	.balign 4
	.global usb_tx_chain
usb_tx_chain:
	TXCELL  cellP0
	TXUSE   0			/* 5 + 11 nop = 16                   */
	TXCELL  cellP1
	TXUSE   0			/* 5 + 11 nop = 16                   */
	TXCELL  cellS0
	TXSEG0
	TXUSE   TXSEG0_CYC		/* 5 + the segment, TXCELL pads      */
	TXCELL  cellS1			/* the rest to 16                    */
	TXSEG1
	TXUSE   TXSEG1_CYC
	TXCELL  cellS2
	TXSEG2
	TXUSE   TXSEG2_CYC
	TXCELL  cellS3
	TXSEG3
	TXUSE   TXSEG3_CYC
	TXCELL  cellS4
	TXSEG4
	TXUSE   TXSEG4_CYC
	TXCELL  cellS5
	TXSEG5
	TXUSE   TXSEG5_CYC
	TXCELL  cellS6
	TXSEG6
	TXUSE   TXSEG6_CYC
	TXCELL  cellS7
	TXSEG7
	TXUSE   TXSEG7_CYC
	TXCELL_END

/* ===================== the trailing stuffed zero, timed ==================
 * USB 2.0 S7.1.9: "If required by the bit stuffing rules, a zero bit will be
 * inserted even if it is the last bit before the end-of-packet signal."
 * T_TX DEFERS a stuffed zero to the head of the next nibble - that is what
 * its seventh row is for - and at the end of a packet there is no next
 * nibble, so this cell is where the deferred bit goes.
 *
 * Reached only from T_EXH[2] and T_EXH[3], i.e. only when the over-fetched
 * 0x00 came out nine wire bits long, i.e. only from stuff state 6.  Nothing
 * falls into it: cellS7 ends with `bx`.  It emits the bit and falls through
 * into EOP, so the whole packet is exactly one bit time longer.
 *
 * The bit is a hard toggle, not a queue shift: a stuffed bit is a 0 by
 * definition and nothing here should depend on what the over-fetch left in
 * the queue.  The store is at cycle 5, where every data bit's store is. */
	.global usb_tx_stuff0
usb_tx_stuff0:
	eors    r5, r6			/* 1  NRZI: toggle = a 0 bit          */
	nop				/* 1                                  */
	nop				/* 1                                  */
	nop				/* 1                                  */
	str     r5, [r7, #USB_BSRR_OFS]	/* 1  =5  on the bit boundary         */
	.rept   11
	nop
	.endr				/* =16, then fall into EOP            */

/* ============================ EOP, still timed ===========================
 * Reached by the dispatch word, i.e. by the `bx` that closes the last data
 * cell, so the first instruction here is cycle 1 of a fresh bit cell and the
 * SE0 store lands at cycle 5 - exactly where every data bit landed.
 * USB 2.0: EOP is SE0 for two bit times, then a driven J for one, then the
 * bus is released to the pull-up. */
	.global usb_tx_eop
usb_tx_eop:
	nop				/* 1                                 */
	nop				/* 1                                 */
	lsrs    r2, r6, #16		/* 1  \ the toggle word's reset half */
	lsls    r2, r2, #16		/* 1  / = both pins low = SE0        */
	str     r2, [r7, #USB_BSRR_OFS]	/* 1  =5  SE0 starts on the boundary */
	.rept   11
	nop
	.endr				/* =16                               */

	movs    r0, #(1 << USB_DM_BIT)	/* 1  \                              */
	movs    r1, #(1 << USB_DP_BIT)	/* 1   | build the J word in the      */
	lsls    r1, r1, #16		/* 1   | spare cycles of the second   */
	orrs    r0, r1			/* 1  /  SE0 bit time                */
	.rept   12
	nop
	.endr				/* =16, the second SE0 bit time      */

	nop				/* 1                                 */
	nop				/* 1                                 */
	nop				/* 1                                 */
	nop				/* 1                                 */
	str     r0, [r7, #USB_BSRR_OFS]	/* 1  =5  driven J for one bit time  */
	.rept   11
	nop
	.endr				/* =16                               */

	/* Untimed from here: release the pins, restore, return. */
	ldr     r0, [r7, #USB_MODER_OFS]
	ldr     r1, =MODER_MASK
	bics    r0, r1			/* 00 = input; the pull-up holds J   */
	str     r0, [r7, #USB_MODER_OFS]

.Ltx_done:
	pop     {r4-r7}
	mov     r8, r4
	mov     r9, r5
	mov     r10, r6
	mov     r11, r7
	pop     {r4-r7, pc}

	.size   usb_send_data, . - usb_send_data
	.ltorg

/* ====================== tables, for BOTH engines =========================
 * One block, two bases.  engine16_merged.S has no tables of its own: the
 * CRC16 byte table is the same 512 bytes in both directions, and the only
 * way to share one copy is to lay both engines' tables out together, because
 * every timed lookup is `ldr(h) rd,[base,rn]` with the base pinned (RX SEG4
 * reads T_CRC16 at r4+0, TXSEG4 reads T_TX at r14+row, and neither engine
 * has a register free for a second base).  So:
 *
 *   usb_tx_tables + 0     dispatch    32 B  TX  (r14 + T_DISPATCH)
 *   usb_tx_tables + 32    T_TX       224 B  TX  (r14 + T_TX)
 *   usb_tables            = usb_tx_tables + 256
 *   usb_tables    + 0     T_CRC16    512 B  BOTH: r4 + 0, and r14 + 256
 *   usb_tables    + 512   T_UT       512 B  RX  (r4 + T_UT   = 512)
 *   usb_tables    + 1024  T_CRC5      32 B  RX  (r4 + T_CRC5)
 *
 * CRC_ROUND2.md's #1 ("move T_CRC16 to offset 0") is what put T_CRC16 at the
 * RX base and T_UT above it.  T_UT could not simply swap places with it: its
 * index is  state*32 + nibble*2  ORed together from r11 and the nibble, with
 * no room for a non-zero table offset and no register to hold one, so at any
 * base but 0 it costs a cycle in SEG0, SEG1 and SEG2 - more than SEG4 saves.
 * The way out is to make T_UT's entries WORDS: the index becomes
 * state*64 + nibble*4, the biased base 512 rides in the entry's own low
 * halfword (uxth, one cycle, exactly where uxtb was), and m and n move to
 * bits 27..31 and 24..26 where a single shift still extracts each.  Every
 * receive segment keeps its cycle count; the table costs 256 bytes more.
 *
 * The offsets both engines assume are asserted at the end of the block, so a
 * table that grows or moves is a build failure, not a wrong packet.
 *
 * Generated and checked by the scripts in engine16_tx.md S9 (T_TX and
 * T_CRC16 against an independent encoder, and the wire stream of 300+
 * packets against a reference NRZI/bit-stuffing encoder) and
 * engine16_merged.md S9 (T_UT re-derived from an independent model, 128/128;
 * both CRC residues confirmed numerically).  Each table carries the
 * generator that produces it.
 *
 * They live in the same section as both engines' code, so a lookup costs 2
 * whichever memory that section lands in.  It is the SAME memory for both
 * engines by construction - one block, one section - which is what makes the
 * sharing possible and what forces the two engines to the same residency.
 */
	.balign 4
	.global usb_tx_tables
usb_tx_tables:
.Ltab_tx:
.Ltab_exh:
	/* T_EXH - where the chain goes when the packet has run out.  TXSEG6
	 * computes acc>>8 = 1 for n=8, 2..3 for n=9, 4..7 for n=10, and it
	 * computed it from the OVER-FETCHED byte, which the setup zeroed.
	 * A 0x00 byte inserts no stuffed bit from stuff state 0..5 (n=8) and
	 * exactly one from state 6 (n=9), so this row's index IS the answer
	 * to "is a trailing stuffed zero owed" - already in r1, for free.
	 * n=10 is unreachable from 0x00; those slots hold EOP so that a
	 * corrupted state cannot fall out of the table. */
	.word   usb_tx_eop + 1		/* 0: unreachable, acc>>8 >= 1       */
	.word   usb_tx_eop + 1		/* 1: n = 8  -> nothing owed         */
	.word   usb_tx_stuff0 + 1	/* 2: n = 9  -> the deferred zero    */
	.word   usb_tx_stuff0 + 1	/* 3: n = 9                          */
	.word   usb_tx_eop + 1		/* 4: n = 10, unreachable            */
	.word   usb_tx_eop + 1		/* 5: n = 10, unreachable            */
	.word   usb_tx_eop + 1		/* 6: n = 10, unreachable            */
	.word   usb_tx_eop + 1		/* 7: n = 10, unreachable            */

.Ltab_disp:
	/* T_DISPATCH - the live chain entry, selected by the same n.  TXSEG7
	 * adds 8 to the index while bytes remain, which is what picks this
	 * table over T_EXH; the row within it is unchanged. */
	.word   usb_tx_eop + 1		/* 0: unreachable, acc>>8 >= 1       */
	.word   usb_tx_cellS0 + 1		/* 1: n = 8                          */
	.word   usb_tx_cellP1 + 1		/* 2: n = 9                          */
	.word   usb_tx_cellP1 + 1		/* 3: n = 9                          */
	.word   usb_tx_cellP0 + 1		/* 4: n = 10                         */
	.word   usb_tx_cellP0 + 1		/* 5: n = 10                         */
	.word   usb_tx_cellP0 + 1		/* 6: n = 10                         */
	.word   usb_tx_cellP0 + 1		/* 7: n = 10                         */

.Ltab_txrows:

	/* T_TX: 7 stuff states x 16 data nibbles, at row (state+1).
	 * halfword = m<<9 | (n-4)<<8 | (state'+1)<<5,  m = w + 2^n - 1. */
	/* state 0 */
	.hword	0x1e20, 0x2020, 0x2220, 0x2420, 0x2620, 0x2820, 0x2a20, 0x2c20
	.hword	0x2e40, 0x3040, 0x3240, 0x3440, 0x3660, 0x3860, 0x3a80, 0x3ca0
	/* state 1 */
	.hword	0x1e20, 0x2020, 0x2220, 0x2420, 0x2620, 0x2820, 0x2a20, 0x2c20
	.hword	0x2e40, 0x3040, 0x3240, 0x3440, 0x3660, 0x3860, 0x3a80, 0x3cc0
	/* state 2 */
	.hword	0x1e20, 0x2020, 0x2220, 0x2420, 0x2620, 0x2820, 0x2a20, 0x2c20
	.hword	0x2e40, 0x3040, 0x3240, 0x3440, 0x3660, 0x3860, 0x3a80, 0x3ce0
	/* state 3 */
	.hword	0x1e20, 0x2020, 0x2220, 0x2420, 0x2620, 0x2820, 0x2a20, 0x4d20
	.hword	0x2e40, 0x3040, 0x3240, 0x3440, 0x3660, 0x3860, 0x3a80, 0x6d40
	/* state 4 */
	.hword	0x1e20, 0x2020, 0x2220, 0x4520, 0x2620, 0x2820, 0x2a20, 0x5520
	.hword	0x2e40, 0x3040, 0x3240, 0x6540, 0x3660, 0x3860, 0x3a80, 0x7560
	/* state 5 */
	.hword	0x1e20, 0x4120, 0x2220, 0x4920, 0x2620, 0x5120, 0x2a20, 0x5920
	.hword	0x2e40, 0x6140, 0x3240, 0x6940, 0x3660, 0x7160, 0x3a80, 0x7980
	/* state 6 */
	.hword	0x3f20, 0x4320, 0x4720, 0x4b20, 0x4f20, 0x5320, 0x5720, 0x5b20
	.hword	0x5f40, 0x6340, 0x6740, 0x6b40, 0x6f60, 0x7360, 0x7780, 0x7ba0

	.global usb_tables
usb_tables:
.Ltab_rx:				/* the assertions below need locals: */
					/* gas will not fold a difference of */
					/* .global symbols                   */
.Ltab_crc16:
	/* T_CRC16: crc = (crc>>8) ^ T[(crc^byte)&0xFF], reflected 0xA001.
	 * Read by BOTH engines - TXSEG1 at r14+256, engine16_merged.S SEG4
	 * at r4+0 - and there is one copy.  With reduce_bits(c,k,poly)
	 * reducing c by k bits (engine16_tx.md S9, engine16_merged.md S9):
	 *   T_CRC16 = [reduce_bits(i, 8, 0xA001) for i in range(256)]
	 * The residue over message+CRC16 is 0xB001, which is what the receive
	 * engine's tail compares against. */
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

.Ltab_ut:
	/* T_UT: 8 unstuff states x 16 wire nibbles, WORDS (see the layout note
	 * at the head of this block for why they are not halfwords).
	 *   word = m<<27 | n<<24 | (T_UT | newstate<<6),  m = kept_bits + 2^n-1
	 * The low halfword is the NEXT lookup's index base, already carrying
	 * T_UT, so SEG0/SEG2 park it with one `uxth` and SEG0/SEG1 OR the
	 * nibble*4 straight into it: 512 is bit 9, state*64 is bits 6..8 and
	 * nibble*4 is bits 2..5, so the OR is an add that cannot carry.
	 * Derived from the halfword table this replaces, entry for entry:
	 *   word = (h>>11)<<27 | ((h>>8)&7)<<24 | 512 | ((h>>5)&7)<<6
	 * Row 7 is the sticky stuffing-violation row.  It differs from VUSB's:
	 * it KEEPS its four bits (n=4) instead of emitting nothing, so that the
	 * emitted-byte counter keeps advancing and SEG5's bound terminates the
	 * chain.  With VUSB's n=0 row the counter freezes and a bus stuck in a
	 * violation spins the ISR forever - see the .md, "the hang". */
	.word	0x7c000200, 0xbc000240, 0x9c000200, 0xdc000280, 0x8c000200, 0xcc000240, 0xac000200, 0xec0002c0
	.word	0x84000200, 0xc4000240, 0xa4000200, 0xe4000280, 0x94000200, 0xd4000240, 0xb4000200, 0xf4000300
	.word	0x7c000200, 0xbc000240, 0x9c000200, 0xdc000280, 0x8c000200, 0xcc000240, 0xac000200, 0xec0002c0
	.word	0x84000200, 0xc4000240, 0xa4000200, 0xe4000280, 0x94000200, 0xd4000240, 0xb4000200, 0xf4000340
	.word	0x7c000200, 0xbc000240, 0x9c000200, 0xdc000280, 0x8c000200, 0xcc000240, 0xac000200, 0xec0002c0
	.word	0x84000200, 0xc4000240, 0xa4000200, 0xe4000280, 0x94000200, 0xd4000240, 0xb4000200, 0xf4000380
	.word	0x7c000200, 0xbc000240, 0x9c000200, 0xdc000280, 0x8c000200, 0xcc000240, 0xac000200, 0xec0002c0
	.word	0x84000200, 0xc4000240, 0xa4000200, 0xe4000280, 0x94000200, 0xd4000240, 0x73000200, 0x000003c0
	.word	0x7c000200, 0xbc000240, 0x9c000200, 0xdc000280, 0x8c000200, 0xcc000240, 0xac000200, 0xec0002c0
	.word	0x84000200, 0xc4000240, 0xa4000200, 0xe4000280, 0x53000200, 0x73000240, 0x000003c0, 0x000003c0
	.word	0x7c000200, 0xbc000240, 0x9c000200, 0xdc000280, 0x8c000200, 0xcc000240, 0xac000200, 0xec0002c0
	.word	0x43000200, 0x63000240, 0x53000200, 0x73000280, 0x000003c0, 0x000003c0, 0x000003c0, 0x000003c0
	.word	0x3b000200, 0x5b000240, 0x4b000200, 0x6b000280, 0x43000200, 0x63000240, 0x53000200, 0x730002c0
	.word	0x000003c0, 0x000003c0, 0x000003c0, 0x000003c0, 0x000003c0, 0x000003c0, 0x000003c0, 0x000003c0
	.word	0x7c0003c0, 0xbc0003c0, 0x9c0003c0, 0xdc0003c0, 0x8c0003c0, 0xcc0003c0, 0xac0003c0, 0xec0003c0
	.word	0x840003c0, 0xc40003c0, 0xa40003c0, 0xe40003c0, 0x940003c0, 0xd40003c0, 0xb40003c0, 0xf40003c0

.Ltab_crc5:
	/* T_CRC5: crc = (crc>>4) ^ T[(crc^nibble)&0xF], reflected 0x14.  Same
	 * reduce_bits as above, four bits at a time:
	 *   T_CRC5 = [reduce_bits(i, 4, 0x0014) for i in range(16)]
	 * Residue over an 11-bit token field plus its FCS is 0x06. */
	.hword	0x0000, 0x0016, 0x0005, 0x0013, 0x000a, 0x001c, 0x000f, 0x0019
	.hword	0x0014, 0x0002, 0x0011, 0x0007, 0x001e, 0x0008, 0x001b, 0x000d

.Ltab_end:
	.if     (.Ltab_exh - .Ltab_tx) != T_EXH
	.error  "engine16 tables: T_EXH is not at the TX base + 0"
	.endif

	.if     (.Ltab_disp - .Ltab_tx) != T_DISPATCH
	.error  "engine16 tables: T_DISPATCH is not at the TX base + 32"
	.endif

	.if     (.Ltab_txrows - .Ltab_tx) != T_TX
	.error  "engine16 tables: T_TX row 1 is not at the TX base + 64"
	.endif

	.if     (.Ltab_rx - .Ltab_tx) != 288
	.error  "engine16 tables: usb_tables is not usb_tx_tables + 288"
	.endif
	.if     (.Ltab_crc16 - .Ltab_tx) != 288
	.error  "engine16 tables: T_CRC16 is not at the TX base + 288"
	.endif
	.if     (.Ltab_crc16 - .Ltab_rx) != 0
	.error  "engine16 tables: T_CRC16 is not at the RX base + 0"
	.endif
	.if     (.Ltab_ut - .Ltab_rx) != 512
	.error  "engine16 tables: T_UT is not at the RX base + 512"
	.endif
	.if     (.Ltab_crc5 - .Ltab_rx) != 1024
	.error  "engine16 tables: T_CRC5 is not at the RX base + 1024"
	.endif
	.if     (.Ltab_end - .Ltab_rx) != 1056
	.error  "engine16 tables: the RX half of the block is not 1056 bytes"
	.endif
	.if     (.Ltab_end - .Ltab_tx) != 1344
	.error  "engine16 tables: the block is not 1344 bytes"
	.endif

	.section .bss.usb_txbuf, "aw", %nobits
	.balign 4
	.global usb_txbuf
usb_txbuf:
	.space  TXBUF_LEN
