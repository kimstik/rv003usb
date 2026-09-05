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
 * Placement: RAM-resident (.datacode), the section this project already
 * copies to SRAM.  ENGINE16_SPEC.md S2 RAM column: ordinary instruction 1,
 * GPIO store 1 (IOPORT), RAM load/store 2, BX 3, B 2-3, taken conditional
 * 2-3.  Load-bearing: from flash-resident code the two table lookups and the
 * two strb would cost 4 each and the pipeline would not fit in 8 x 11.
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

/* Table layout, one base register (r14).  Row 0 of the halfword table is
 * reused as the dispatch table, because the stuff state is stored as
 * (state+1) and therefore never selects row 0. */
#define T_DISPATCH      0		/*  8 words: chain entry, or EOP      */
#define T_TX            32		/*  7 rows x 16 halfwords             */
#define T_CRC16         256		/* 256 halfwords                      */

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
 *   r9  staging+3, the low edge of the CRC-commit window
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

	.macro  TXSEG0			/* 11: fetch the byte, build the mask */
	ldrb    r0, [r3, #0]		/* 2  the byte to prepare             */
	adds    r3, #1			/* 1                                  */
	mov     r1, r9			/* 1  staging+3                       */
	subs    r1, r3, r1		/* 1  >=0 iff at or past the payload  */
	asrs    r1, r1, #31		/* 1  -1 if too early (SYNC, PID)     */
	mov     r2, r8			/* 1  CRC-byte pointer                */
	subs    r2, r2, r3		/* 1  >=0 iff not yet past the payload*/
	asrs    r2, r2, #31		/* 1  -1 if too late (the CRC bytes)  */
	orrs    r1, r2			/* 1                                  */
	mvns    r1, r1			/* 1  commit mask: -1 iff payload byte*/
	.endm

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

	.macro  TXSEG2			/* 11: commit the CRC, publish byte 0 */
	movs    r2, #8			/* 1                                  */
	ands    r2, r1			/* 1  shift by 8 only when committing */
	mov     r1, r10			/* 1                                  */
	lsrs    r1, r1, r2		/* 1  crc>>8, or crc unchanged        */
	eors    r1, r0			/* 1  ^ the gated table value         */
	mov     r10, r1			/* 1  crc'                            */
	mvns    r1, r1			/* 1  the wire form is the complement */
	mov     r2, r8			/* 1                                  */
	strb    r1, [r2, #0]		/* 2  CRC low byte into the stream    */
	nop				/* 1                                  */
	.endm

	.macro  TXSEG3			/* 11: publish byte 1, low nibble idx */
	lsrs    r1, r1, #8		/* 1                                  */
	mov     r2, r8			/* 1                                  */
	strb    r1, [r2, #1]		/* 2  CRC high byte into the stream   */
	mov     r0, r12			/* 1  the byte                        */
	movs    r1, #0x0F		/* 1                                  */
	ands    r1, r0			/* 1  low nibble = first in time      */
	lsls    r1, r1, #1		/* 1  halfword index                  */
	mov     r2, r11			/* 1  (state+1)<<5 selects the row    */
	orrs    r1, r2			/* 1                                  */
	nop				/* 1                                  */
	.endm

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
	mov     r2, r14			/* 1                                  */
	ldrh    r1, [r2, r1]		/* 2                                  */
	nop				/* 1                                  */
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
	lsls    r1, r1, #2		/* 1  word index into T_DISPATCH      */
	mov     r12, r0			/* 1  park the queue                  */
	mov     r2, r8			/* 1                                  */
	subs    r0, r3, r2		/* 1  source - CRC pointer            */
	.endm

	.macro  TXSEG7			/* 11: dispatch, or leave for EOP     */
	subs    r0, #3			/* 1  >=0 iff there is no next byte   */
	asrs    r0, r0, #31		/* 1  -1 while bytes remain, else 0   */
	ands    r1, r0			/* 1  index 0 = T_DISPATCH[0] = EOP   */
	mov     r2, r14			/* 1                                  */
	ldr     r1, [r2, r1]		/* 2  chain entry, or the EOP handler */
	mov     r4, r12			/* 1  the queue, after this cell's bit*/
	nop				/* 1                                  */
	bx      r1			/* 3  flat 3: no 2-vs-3 ambiguity     */
	.endm

/* ========================================================================= */
	.section .datacode, "ax", %progbits	/* RAM-resident: see the header */
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

	cmp     r1, #TX_MAXPAY		/* the only buffer bound, and it is  */
	bhi     .Ltx_reject		/* enforced once, outside the timing */
	cmp     r2, #0
	beq     1f
	cmp     r1, #2			/* a CRC-less packet may carry at    */
	bhi     .Ltx_reject		/* most two bytes - see the .md S6   */
1:
	mov     r8, r2			/* park poly_function                */
	ldr     r6, =usb_txbuf
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
	mov     r2, r8			/* poly_function                     */
	cmp     r2, #0
	bne     2f
	adds    r0, r0, #2		/* CRC present: two more slots       */
2:
	mov     r8, r0

	adds    r0, r6, #3
	mov     r9, r0			/* the low edge of the CRC window    */
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
	TXUSE   11			/* 5 + 11     = 16                   */
	TXCELL  cellS1
	TXSEG1
	TXUSE   11
	TXCELL  cellS2
	TXSEG2
	TXUSE   11
	TXCELL  cellS3
	TXSEG3
	TXUSE   11
	TXCELL  cellS4
	TXSEG4
	TXUSE   11
	TXCELL  cellS5
	TXSEG5
	TXUSE   11
	TXCELL  cellS6
	TXSEG6
	TXUSE   11
	TXCELL  cellS7
	TXSEG7
	TXUSE   11
	TXCELL_END

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
 * every timed lookup is `ldrh rd,[base,rn]` with the base pinned (RX SEG1
 * reads T_UT at r4+0, TXSEG4 reads T_TX at r14+row, and neither engine has a
 * register free for a second base).  So:
 *
 *   usb_tables    + 0     T_UT       256 B  RX  (r4 + T_UT    = 0)
 *   usb_tx_tables         = usb_tables + 256
 *   usb_tx_tables + 0     dispatch    32 B  TX  (r14 + T_DISPATCH)
 *   usb_tx_tables + 32    T_TX       224 B  TX  (r14 + T_TX)
 *   usb_tx_tables + 256   T_CRC16    512 B  BOTH: r14 + 256, and r4 + 512
 *   usb_tables    + 1024  T_CRC5      32 B  RX  (r4 + T_CRC5)
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
 * They live in the same RAM-resident section as the code, so a lookup is a
 * 2-cycle RAM load; from flash it would be 4 and the pipeline would not fit.
 */
	.balign 4
	.global usb_tables
usb_tables:
.Ltab_rx:				/* the assertions below need locals: */
					/* gas will not fold a difference of */
					/* .global symbols                   */
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

	/* No .balign here: T_UT is 256 bytes from a 4-aligned start, so the
	 * dispatch words are word-aligned, and the assertions at the end of
	 * the block prove it.  An alignment directive inside the block would
	 * be a variable-size frag and gas would refuse to fold them. */
	.global usb_tx_tables
usb_tx_tables:
.Ltab_tx:
	/* Row 0 doubles as T_DISPATCH.  TXSEG6 computes acc>>8, which is 1
	 * for n=8, 2..3 for n=9 and 4..7 for n=10; TXSEG7 forces it to 0 when
	 * the packet has run out.  Nothing else can select row 0, because the
	 * stuff state is stored as (state+1) >= 1. */
	.word   usb_tx_eop + 1		/* 0: no byte left -> EOP            */
	.word   usb_tx_cellS0 + 1		/* 1: n = 8                          */
	.word   usb_tx_cellP1 + 1		/* 2: n = 9                          */
	.word   usb_tx_cellP1 + 1		/* 3: n = 9                          */
	.word   usb_tx_cellP0 + 1		/* 4: n = 10                         */
	.word   usb_tx_cellP0 + 1		/* 5: n = 10                         */
	.word   usb_tx_cellP0 + 1		/* 6: n = 10                         */
	.word   usb_tx_cellP0 + 1		/* 7: n = 10                         */

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

.Ltab_crc16:
	/* T_CRC16: crc = (crc>>8) ^ T[(crc^byte)&0xFF], reflected 0xA001.
	 * Read by BOTH engines - TXSEG1 at r14+256, engine16_merged.S SEG4
	 * at r4+512 - and there is one copy.  With reduce_bits(c,k,poly)
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

.Ltab_crc5:
	/* T_CRC5: crc = (crc>>4) ^ T[(crc^nibble)&0xF], reflected 0x14.  Same
	 * reduce_bits as above, four bits at a time:
	 *   T_CRC5 = [reduce_bits(i, 4, 0x0014) for i in range(16)]
	 * Residue over an 11-bit token field plus its FCS is 0x06. */
	.hword	0x0000, 0x0016, 0x0005, 0x0013, 0x000a, 0x001c, 0x000f, 0x0019
	.hword	0x0014, 0x0002, 0x0011, 0x0007, 0x001e, 0x0008, 0x001b, 0x000d

.Ltab_end:
	.if     (.Ltab_tx - .Ltab_rx) != 256
	.error  "engine16 tables: usb_tx_tables is not usb_tables + 256"
	.endif
	.if     (.Ltab_crc16 - .Ltab_tx) != 256
	.error  "engine16 tables: T_CRC16 is not at the TX base + 256"
	.endif
	.if     (.Ltab_crc16 - .Ltab_rx) != 512
	.error  "engine16 tables: T_CRC16 is not at the RX base + 512"
	.endif
	.if     (.Ltab_crc5 - .Ltab_rx) != 1024
	.error  "engine16 tables: T_CRC5 is not at the RX base + 1024"
	.endif
	.if     (.Ltab_end - .Ltab_rx) != 1056
	.error  "engine16 tables: the block is not 1056 bytes"
	.endif

	.section .bss.usb_txbuf, "aw", %nobits
	.balign 4
	.global usb_txbuf
usb_txbuf:
	.space  TXBUF_LEN
