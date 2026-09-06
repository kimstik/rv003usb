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
 * Post-review repairs (ENGINE16_REVIEW.md F-1..F-5).  All of them are in the
 * entry, the dispatch or the ISR tail; no bit cell changed, in either cost
 * model.
 *   F-1  every exit writes EXTI->PR.  Without it the pending flag stayed set,
 *        the NVIC re-entered the handler at once, and the device live-locked
 *        on the first D- edge.
 *   F-2  the PID type dispatch runs BEFORE the length gate, so a handshake -
 *        two emitted bytes, SYNC counted - reaches usb_pid_handle_ack.  Each
 *        arm carries its own length test instead.
 *   F-3  the token endpoint is bounded against ENDPOINTS before it indexes
 *        ist->eps[], which is the last member of the struct.
 *   F-4  the length argument is payload+3, the C layer's convention.
 *   F-7  the token address field is filtered against my_address (address 0,
 *        the unaddressed state, is accepted too).  Latent until F-2 made the
 *        reply paths reachable; now the difference between ignoring another
 *        device's token and colliding with its answer.
 *   F-5  the store base is usb_rxbuf+2, so SYNC/PID land at +2/+3 and the
 *        payload pointer handed to C is 4-aligned; the buffer is 2 bytes
 *        longer so the masked index still cannot leave it.
 *   Size: T_UT, T_CRC16 and T_CRC5 moved into the single table block in
 *        engine16_tx.S - one T_CRC16 now serves both engines, -512 B.
 *
 * The receive integrity check is selectable at build time: USB_RX_CHECK is
 * full CRC-16 (2, the default and bit-for-bit the engine as it was), parity
 * only (1), or nothing (0).  See the block at USB_BYTE_LIMIT for the contract
 * and CRC_PARITY.md for the measured detection rates - which are much less
 * flattering to parity than its algebra suggests, because NRZI turns one
 * corrupted line sample into two adjacent flipped data bits.  No bit cell
 * changes at any setting; every cell is 16 cycles by construction.
 *
 * The HSI calibration seam (py32_hsical.h) is wired in the same two places:
 * a SysTick VAL stamp as the first two instructions of the handler, and one
 * bl py32_hsical_event past both ISR exits.  Both compile away entirely at
 * PY32_HSICAL_ENABLE=0.  No bit cell is involved, in either cost model.
 *
 * Assembles with:
 *   arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c
 */

	.syntax unified
	.cpu    cortex-m0plus
	.thumb

/* ENDPOINTS bounds the token endpoint before it indexes ist->eps[] (F-3).
 * Take it from the project's own config when the build offers one - that is
 * the only place it is authoritative, and the predecessor got it the same way
 * (rv003usb-arm.S includes rv003usb.h) - and fall back to the value every
 * usb_config.h in this tree carries so the file still assembles alone. */
#if defined(__has_include)
#if __has_include("usb_config.h")
#include "usb_config.h"
#endif
#endif
#ifndef ENDPOINTS
#define ENDPOINTS       2
#endif

/* Byte offset of rv003usb_internal.my_address, for the device-address filter
 * (F-7).  Config-dependent: rv003usb.h:124-140 widens every TURBO8TYPE to a
 * word under RV003USB_OPTIMIZE_FLASH, moving my_address from +1 to +4.  The
 * predicate below is a verbatim copy of that header's - #ifdef, NOT #if.
 * That distinction is load-bearing and counter-intuitive: every usb_config.h
 * in this tree writes `#define RV003USB_OPTIMIZE_FLASH 0`, which #ifdef still
 * takes, so the live offset in the gamepad build is 4, not 1.  (Measured:
 * offsetof(struct rv003usb_internal, my_address) == 4 there.)  An #if here
 * would read the wrong byte and the filter would drop every token.
 * usb_config.h is already in scope from the F-3 block above; only that one
 * symbol is needed, so rv003usb.h itself is not pulled in - it defines
 * USB_GPIO_BASE from the RISC-V port name and #errors on a funconfig.h this
 * port does not have. */
#ifndef MY_ADDRESS_OFFSET_BYTES
#ifdef  RV003USB_OPTIMIZE_FLASH
#define MY_ADDRESS_OFFSET_BYTES 4
#else
#define MY_ADDRESS_OFFSET_BYTES 1
#endif
#endif

/* --------------------------------------------------- HSI calibration seam
 * py32_hsical.c servos the internal RC oscillator against the host's 1 ms
 * keep-alive; without it PY32F002B cannot reach 24 MHz at all (its factory
 * 48 MHz word measures 43.12 MHz, -10.2%) and this engine's 16-cycle bit
 * cell is meaningless.  It asks the ISR for exactly two things: a SysTick
 * VAL sample taken at a FIXED position at entry, and one call at exit with
 * that sample.  Both are implemented below; see the notes at each site.
 *
 * Defaults track py32_hsical.h, deliberately: the header defaults ENABLE to
 * 1, so this does too.  A port that does not want the calibration sets
 * PY32_HSICAL_ENABLE 0 in usb_config.h - which both sides then see, through
 * the same __has_include block above - and the seam costs zero bytes and
 * zero cycles here, as it does there.  The two defaults are kept identical
 * so that a mismatch is a link error against py32_hsical_event, never a
 * firmware that calibrates silently forever without a reference.
 *
 * PY32_HSICAL_OWN_SYSTICK is NOT set here: the engine only ever reads VAL,
 * and nothing in this port runs SysTick free-running for the calibration to
 * borrow (the sole SysTick user in the integration tree is LL_mDelay, which
 * reloads it for a 1 ms tick).  So the calibration keeps ownership - its
 * default 1 - and py32_hsical_init() must run after the last LL_mDelay.
 * That ordering is the caller's, not the engine's. */
#ifndef PY32_HSICAL_ENABLE
#define PY32_HSICAL_ENABLE 1
#endif
#ifndef PY32_HSICAL_TICK_ADDR
#define PY32_HSICAL_TICK_ADDR 0xE000E018	/* SysTick->VAL, py32_hsical.h */
#endif

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

/* EXTI, for the pending-flag acknowledge every ISR exit owes the NVIC.  The
 * flag is level-latched into the NVIC, so an ISR that returns without it is
 * re-entered immediately and forever.  Base 0x40021800, PR at 0x0C, line =
 * the D- pin: rv003usb-arm.S@0ad3c42:3,17,331-336, the immediate predecessor
 * on this part.  (rv003usb.S:643-646 does the same on the RISC-V.) */
#ifndef USB_EXTI_BASE
#define USB_EXTI_BASE   0x40021800
#endif
#ifndef USB_EXTI_PR_OFS
#define USB_EXTI_PR_OFS 0x0C
#endif

/* Receive buffer: index masked to 5 bits, so the store window is 32 bytes
 * wide wherever the base points.  The longest legal LS packet through this
 * engine is SYNC + PID + 8 data + CRC16 = 12 emitted bytes.  BALANCE's
 * mechanism: the bound is a property of the address arithmetic, not of a
 * runtime check that could be wrong - so the ALLOCATION, not the mask, is
 * what absorbs the +2 payload-alignment bias (F-5).  Base = usb_rxbuf+2 and
 * the masked index is 0..31, so the window is usb_rxbuf+2 .. usb_rxbuf+33
 * and the buffer is USB_RXBUF_LEN + 2 bytes.  No store can leave it for any
 * input whatever, which is the property the mask was chosen for. */
#define USB_RXBUF_LEN   32
#define USB_RXBUF_MASK  27		/* shift pair: 32 - log2(len) */
/* Loop/packet bound: the chain is a loop, so termination needs a runtime
 * limit (see the note on VUSB's hang in the .md).  24 < 32, so the store is
 * bounded twice over: structurally by the mask, and by this. */
#define USB_BYTE_LIMIT  24

/* ------------------------------------------- the receive integrity check
 * USB_RX_CHECK selects WHAT this engine verifies before the control flow is
 * allowed to reach the ACK PID.  The ordering property (turnaround.md, A-9)
 * holds for every setting; only the strength behind it changes.  The
 * measured detection rates for all three are in CRC_PARITY.md §3 - read
 * that before moving this knob, because the honest numbers are much less
 * flattering to parity than the algebra suggests.
 *
 *   2  USB_RX_CHECK_CRC16   full CRC-16 residue test against 0xB001.  The
 *                           default, and the only setting that detects
 *                           every single- and double-bit wire error.
 *   1  USB_RX_CHECK_PARITY  the (x+1) factor of g16 alone.  A genuine
 *                           partial CRC-16 check - g16 = (x+1)(x^15+x+1),
 *                           so every valid codeword has even parity - and
 *                           it is exactly free: SEG4 shrinks by 5 cycles,
 *                           SEG6 is unchanged, no cell moves, the tail
 *                           test is 3 instructions against the CRC's 4.
 *                           It catches all odd-weight errors, which on an
 *                           NRZI wire is far fewer than it sounds.
 *   0  USB_RX_CHECK_NONE    no check at all.  This is what V-USB, Grainuum
 *                           and LemcUSB ship (CRC_ALTERNATIVES.md §9.1).
 *                           Present so the cost of the other two can be
 *                           measured against it, not as a recommendation.
 *
 * The 512-byte T_CRC16 does NOT go away at settings 1 and 0: it lives in
 * engine16_tx.S and the transmit engine still needs it, because the host
 * checks the CRC we send.  Only the receive-side code shrinks. */
#define USB_RX_CHECK_NONE       0
#define USB_RX_CHECK_PARITY     1
#define USB_RX_CHECK_CRC16      2
#ifndef USB_RX_CHECK
#define USB_RX_CHECK    USB_RX_CHECK_CRC16
#endif
#if USB_RX_CHECK != USB_RX_CHECK_NONE && \
    USB_RX_CHECK != USB_RX_CHECK_PARITY && \
    USB_RX_CHECK != USB_RX_CHECK_CRC16
#error "engine16_merged: USB_RX_CHECK must be 0, 1 or 2"
#endif

/* Table offsets inside the single base held in r4.  The block itself is in
 * engine16_tx.S - see the note where the tables used to be - and asserts
 * these three offsets there.  T_CRC16 must stay <= 512 for SEG4's index
 * arithmetic and T_CRC5 a whole multiple of 256 for .Lcrc5_byte's. */
#define T_UT            0		/* 128 halfwords: unstuff + accumulate */
#define T_CRC16         512		/* 256 halfwords: SHARED with the TX   */
					/* engine, which sees the same table   */
					/* at its own base + 256               */
#define T_CRC5          1024		/*  16 halfwords: CRC5 nibble step     */
	.if     (T_CRC16 / 2 - 1) > 255 || (T_CRC16 & 1)
	.error  "engine16_merged: T_CRC16 does not fit SEG4's index arithmetic"
	.endif
	.if     ((T_CRC5 >> 8) << 8) != T_CRC5 || (T_CRC5 >> 8) > 255
	.error  "engine16_merged: T_CRC5 does not fit .Lcrc5_byte's index"
	.endif

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
 *   r9  rxbuf base (32-byte aligned)    r11 unstuff state<<5
 *   r10 CRC16, or the parity accumulator, or unused - see USB_RX_CHECK
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

#if USB_RX_CHECK == USB_RX_CHECK_CRC16
#define SEG4_CYC 9
	.macro  SEG4			/* 9: CRC16 byte step, speculative    */
	mov     r2, r10			/* 1  running CRC                     */
	eors    r2, r3			/* 1  crc ^ candidate byte            */
	uxtb    r2, r2			/* 1                                  */
	adds    r2, #(T_CRC16 / 2 - 1)	/* 1  \ index*2 + T_CRC16, in three  */
	lsls    r2, r2, #1		/* 1   | instructions for any even     */
	adds    r2, #2			/* 1  /  T_CRC16 <= 512 (imm8 is 255) */
	ldrh    r2, [r4, r2]		/* 2                                  */
	mov     r8, r2			/* 1  park; the commit is decided late*/
	.endm
#elif USB_RX_CHECK == USB_RX_CHECK_PARITY
#define SEG4_CYC 4
	/* Park the byte, gated.  r1 here is SEG3's "a byte finished" mask,
	 * NOT the CRC's later count>=3 mask - parity needs no such gate,
	 * because SYNC and PID contribute a known constant (CRC_PARITY.md
	 * §1.3) and the tail absorbs it.  r8 is free between SEG1 and SEG6,
	 * which is exactly the window the CRC16 build parks its table value
	 * in.  The byte must be taken HERE and not in SEG6: SEG5 shifts it
	 * out of r3. */
	.macro  SEG4			/* 4: park the emitted byte           */
	mov     r2, r3			/* 1  accumulator; byte in bits 0..7  */
	uxtb    r2, r2			/* 1  drop the sentinel and leftovers */
	ands    r2, r1			/* 1  zero unless a byte finished     */
	mov     r8, r2			/* 1  park                            */
	.endm
#else
#define SEG4_CYC 0
	.macro  SEG4			/* 0: no receive check                */
	.endm
#endif

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

#if USB_RX_CHECK == USB_RX_CHECK_CRC16
#define SEG6_CYC 10
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
#elif USB_RX_CHECK == USB_RX_CHECK_PARITY
#define SEG6_CYC 10
	/* Fold the parked byte to one bit and XOR it into r10.  r1 is dead
	 * from here (the CRC build clobbers it in the same place), so this
	 * segment has TWO scratch registers and the fold is a plain
	 * halve-and-xor chain - no table, no branch, no memory.  A zeroed
	 * park (no byte finished) folds to 0, so the step is idempotent and
	 * needs no commit mask of its own.
	 * Only bit 0 of r10 is ever read; bits 1..7 collect the junk the
	 * partial folds leave, and XOR is bitwise, so they cannot reach it. */
	.macro  SEG6			/* 10: parity of the byte, into r10   */
	mov     r1, r8			/* 1  the gated byte                  */
	lsrs    r2, r1, #4		/* 1  \                               */
	eors    r1, r2			/* 1   |                              */
	lsrs    r2, r1, #2		/* 1   | bit 0 = parity of the byte   */
	eors    r1, r2			/* 1   |                              */
	lsrs    r2, r1, #1		/* 1   |                              */
	eors    r1, r2			/* 1  /                               */
	mov     r2, r10			/* 1  running parity                  */
	eors    r2, r1			/* 1                                  */
	mov     r10, r2			/* 1                                  */
	.endm
#else
#define SEG6_CYC 0
	.macro  SEG6			/* 0: no receive check                */
	.endm
#endif

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
#if PY32_HSICAL_ENABLE
	/* THE CALIBRATION STAMP.  py32_hsical.h asks for this "immediately
	 * after the EXTI_PR ack"; here it is the first thing in the handler
	 * instead, and that is not a compromise but the only correct reading.
	 * The ack cannot come first on this port - F-1 puts it LAST, because
	 * our own reply toggles D- and re-sets the flag, and acking early
	 * would make every packet we answer re-enter the ISR.  What the
	 * calibration actually needs is not adjacency to the ack; it is a
	 * constant delay from the D- edge to this load.  First-in-handler is
	 * the smallest such delay and the only one that is the same on every
	 * path: the SE0 test below forks the code, so any position after it
	 * would give the keep-alive EOP and a packet start two different
	 * delays - and the keep-alive EOP is the reference being measured.
	 * Cost here is 2 instructions of constant length; exception entry on
	 * M0+ is fixed, so the residual jitter is the interrupted
	 * instruction's completion, a few cycles.  One cycle is 0.004% of
	 * frequency and the dead band absorbs 16 (py32_hsical.h:36). */
	ldr     r2, =PY32_HSICAL_TICK_ADDR
	ldr     r2, [r2]		/* stamp; carried in r2 to both exits */
#endif
	ldr     r3, =USB_GPIO_BASE
	ldr     r0, [r3, #USB_IDR_OFS]
	movs    r1, #USB_PINMASK
	ands    r0, r1
	bne     1f
	.global usb_rx_keepalive
usb_rx_keepalive:			/* SE0 already at entry: keepalive   */
#if PY32_HSICAL_ENABLE
	push    {r1, lr}		/* r1 is dead here; the PAIR is what  */
					/* keeps SP 8-aligned for the AAPCS   */
					/* call below (entry SP is 8-aligned  */
					/* by ARMv6-M exception entry).       */
#endif
	ldr     r0, =USB_EXTI_BASE	/* acknowledge, or the NVIC re-enters*/
	movs    r1, #(1 << USB_DM_BIT)
	str     r1, [r0, #USB_EXTI_PR_OFS]
#if PY32_HSICAL_ENABLE
	/* This path IS the reference.  A low-speed keep-alive is an EOP and
	 * nothing else, so it lands here, once per frame - it is the only
	 * event the calibration can actually measure, which is why the
	 * contract says call on EVERY interrupt and do not filter. */
	mov     r0, r2
	bl      py32_hsical_event
	pop     {r1, pc}
#else
	bx      lr
#endif
	.ltorg				/* Entry literal pool, and it is load-
					 * bearing at BOTH settings of
					 * PY32_HSICAL_ENABLE: the entry's
					 * LDR-literals already sat 1016 of
					 * their 1020-byte reach from the pool
					 * at the end of the function, so the
					 * ten bytes F-7 adds ahead of that
					 * pool are on their own enough to put
					 * them out of range and make the
					 * assembler refuse the build.  Costs
					 * one duplicated USB_EXTI_BASE word.
					 * Nothing falls through into it - the
					 * BX/POP above is unconditional. */
1:
#if PY32_HSICAL_ENABLE
	push    {r2, r3, r4-r7, lr}	/* r2 = the stamp, parked for the tail.
					 * r3 is PADDING, not data: 7 words
					 * keeps this frame's residue mod 8 the
					 * same as the original 5, so the C
					 * dispatch calls below still make
					 * their `push {r4}` land on an
					 * 8-aligned SP - while leaving the
					 * tail able to pop ONE word and reach
					 * alignment for its own call. */
#else
	push    {r4-r7, lr}
#endif
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
	ldr     r2, =(usb_rxbuf + 2)	/* F-5: SYNC at rxbuf+2, PID at +3,  */
	mov     r9, r2			/* payload at +4, i.e. 4-ALIGNED.    */
					/* The C layer does plain uint32_t   */
					/* loads through this pointer and    */
					/* ARMv6-M has no unaligned access.  */
	ldr     r2, =(usb_rx_chain + 1)	/* chain head, bit0 set for bx       */
	mov     r14, r2
#if USB_RX_CHECK == USB_RX_CHECK_CRC16
	movs    r2, #0
	mvns    r2, r2
	lsrs    r2, r2, #16		/* 0xFFFF                            */
	mov     r10, r2			/* CRC16 init                        */
#elif USB_RX_CHECK == USB_RX_CHECK_PARITY
	movs    r2, #0
	mov     r10, r2			/* parity accumulator init           */
#endif
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
 * priming below is 14 cycles: 9 of setup, 5 of nop - which is what the code
 * does and what the assertion at usb_rx_chain enforces (F-9: this comment
 * used to say 13/4 and contradict both).  14 puts the first sample half a
 * cycle after the cell centre instead of half a cycle before; the poll
 * granularity already contributes +-3.5.
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
	USE     SEG4_CYC		/* CRC16 5+9+2nop; parity 5+4+7nop;  */
					/* none 5+0+11nop.  Always 16.       */
	CELL    5
	SEG5    .Lovf_a
	USE     11			/* 5 + 11 + 0     = 16               */
	CELL    6
	SEG6
	USE     SEG6_CYC		/* CRC16 and parity 5+10+1nop; none  */
					/* 5+0+11nop.  Always 16.            */
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

/* Trampoline.  .Lusb_done is at the far end of the tail and a Thumb
 * conditional branch reaches +-254 bytes, which the validate block below
 * already sat inside by only a few instructions - F-7's four extra ones in
 * .Ltoken pushed it over, and the assembler said so.  Every reject in the
 * validate block goes through here instead; the extra B is 2-3 cycles on a
 * path that is about to do nothing anyway.  The arms past .Ldata are left
 * pointing straight at .Lusb_done: they are nearer to it than to this. */
.Ldone_t:
	b       .Lusb_done

/* =========================== tail: validate and dispatch ==================
 * Untimed, but not unbudgeted: the device must answer 2-6.5 bit times after
 * EOP (USB 2.0 S7.1.18, PRIOR_ART L-1 = 64..208 cycles at 24 MHz).  The CRC
 * is already folded in as the bytes arrived, so what is left here is one
 * comparison - which is exactly why CRC stays in the bit cell and does not
 * move to the tail (PRIOR_ART S-2). */
.Lrx_tail:
	mov     r2, r11
	cmp     r2, #(7 << 5)		/* sticky stuffing violation         */
	beq     .Ldone_t

	mov     r6, r9			/* rxbuf base                        */
	mov     r0, r12			/* emitted byte count                */
	cmp     r0, #2			/* SYNC + PID: a handshake is exactly*/
	blo     .Ldone_t		/* this and must reach the dispatch  */
	cmp     r0, #12			/* longer than any legal LS packet   */
	bhi     .Ldone_t

	ldrb    r1, [r6, #0]
	cmp     r1, #0x80		/* SYNC decodes to 0x80, oldest-first*/
	bne     .Ldone_t

	ldrb    r5, [r6, #1]		/* PID byte                          */
	lsrs    r1, r5, #4
	movs    r2, #0x0F
	ands    r2, r5
	eors    r1, r2			/* high nibble must be ~low nibble   */
	cmp     r1, #0x0F
	bne     .Ldone_t

	/* Dispatch on the PID type BEFORE any length constraint: a handshake
	 * is SYNC + PID + EOP, i.e. two emitted bytes, and the old
	 * "cmp r0,#4 / blo" rejected it here, which made .Lhandshake and
	 * usb_pid_handle_ack dead code and froze the IN data toggle.  Each arm
	 * now carries the length it needs: token == 4, data 4..12, handshake
	 * == 2. */
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
	cmp     r0, #4			/* SYNC + PID + CRC16 at least       */
	blo     .Lusb_done
#if USB_RX_CHECK == USB_RX_CHECK_CRC16
	mov     r1, r10
	ldr     r2, =0x0000B001
	cmp     r1, r2
	bne     .Lusb_done
#elif USB_RX_CHECK == USB_RX_CHECK_PARITY
	/* r10 bit 0 is the XOR of the per-byte parities of every emitted
	 * byte - SYNC, PID and the data field.  parity(SYNC=0x80) = 1 and
	 * every legal PID byte has parity 0 (it is (~n<<4)|n, and
	 * popcount(~n) = 4 - popcount(n)), and the PID's own complement test
	 * three instructions up has already rejected anything else.  A valid
	 * data field has parity 0.  So bit 0 must be 1, and the whole test is
	 * a shift into carry.  CRC_PARITY.md §1.3 verifies all three legs.
	 * This sits in exactly the same place in the control flow as the
	 * residue test it replaces, so the ACK PID stays downstream of the
	 * verdict (turnaround.md, A-9) - and it is one instruction shorter,
	 * so the 2..6.5 bit-time turnaround does not get worse. */
	mov     r1, r10
	lsrs    r1, r1, #1		/* C = accumulated parity            */
	bcc     .Lusb_done
#endif
	subs    r3, r0, #1		/* F-4: the C layer's convention is  */
					/* payload + 3 (rv003usb.c:300 does  */
					/* length -= 3), and the count here  */
					/* is payload + 4.  Passing the bare */
					/* payload length made a ZLP arrive  */
					/* as 0xFFFFFFFD.                    */
	adds    r1, r6, #2		/* payload pointer, 4-aligned (F-5)  */
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
	cmp     r2, #ENDPOINTS		/* eps[] is the LAST member of the   */
	bhs     .Lusb_done		/* struct: an unchecked 0..15 from   */
					/* the wire writes past it.  BHS,    */
					/* not BHI - that is catalogue U-1,  */
					/* repaired (rv003usb.S:528 is the   */
					/* correct predecessor).             */
	movs    r1, #0x7F
	ands    r0, r1			/* r0 = address; ANDS sets Z on 0    */
	ldr     r4, =rv003usb_internal_data
	/* F-7: the device-address filter.  Accept address 0 - the unaddressed
	 * state every device answers to before SET_ADDRESS - or a match
	 * against the address the host assigned us; drop anything else.
	 * rv003usb.S:524-531 and rv003usb-arm.S:275-283 both do exactly this,
	 * for the same reason: on a bus with more than one device every token
	 * is seen by all of them.  It stopped being latent when F-2 made the
	 * handshake and token arms reachable - the engine now DRIVES the bus
	 * in reply, so an unfiltered token addressed to another device is a
	 * collision, not merely a wasted call.
	 * LDR-literal does not touch the flags, so the Z from the ANDS above
	 * still stands at the BEQ: the filter is 4 instructions and needs no
	 * extra register.  LDRB is correct for both offsets - my_address is a
	 * little-endian uint32_t at +4 under RV003USB_OPTIMIZE_FLASH and a
	 * uint8_t at +1 without, and only its low byte is ever nonzero (a USB
	 * address is 7 bits).
	 * SOF is filtered too, since its 11-bit frame number sits where the
	 * address does.  Both predecessors do the same and it changes nothing:
	 * the SOF arm below falls through to .Lusb_done anyway.
	 * Placed before the PUSH so the reject path needs no SP fixup, and
	 * entirely inside the post-EOP tail - not one cycle in a bit cell. */
	beq     .Lt_addr_ok		/* addr 0: unaddressed, always ours  */
	ldrb    r1, [r4, #MY_ADDRESS_OFFSET_BYTES]
	cmp     r0, r1
	bne     .Lusb_done		/* addr != 0 && addr != ours         */
.Lt_addr_ok:
	adds    r1, r6, #2		/* data pointer                      */
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
	cmp     r0, #2			/* SYNC + PID + EOP and nothing else */
	bne     .Lusb_done
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
	/* Acknowledge last, not first: our own transmission has toggled D- by
	 * now and set the flag again, so clearing here is what makes the ISR
	 * run once per packet - the predecessor's interrupt_complete does the
	 * same.  Every exit from this engine is either here or the keepalive
	 * bx lr above; both clear it. */
	ldr     r0, =USB_EXTI_BASE
	movs    r1, #(1 << USB_DM_BIT)
	str     r1, [r0, #USB_EXTI_PR_OFS]
	pop     {r4-r7}
	mov     r8, r4
	mov     r9, r5
	mov     r10, r6
	mov     r11, r7
#if PY32_HSICAL_ENABLE
	/* THE CALIBRATION CALL, at the one place the contract allows: after
	 * the packet is handled and nowhere near SYNC..EOP.  The device's
	 * reply is already on the wire - usb_send_data transmits inline from
	 * the C handler above - so this does not touch the 2..6.5 bit-time
	 * turnaround (PRIOR_ART L-1).  It is also after the EXTI ack, so an
	 * edge arriving during the call re-pends and is served on return
	 * rather than being lost.
	 * Frame here is 7 words; popping the stamp leaves 6, i.e. an 8-
	 * aligned SP for an AAPCS call, and leaves the padding word to be
	 * dropped afterwards.  r4-r11 are already restored and the callee
	 * preserves them, so the call is transparent to the caller.
	 * Cost: py32_hsical.c counts 12 instructions on the reject path and
	 * ~40 on the once-per-frame path that writes ICSCR.  Worst case that
	 * is well under the 128 cycles of a SYNC field, and the phase lock
	 * keys off the LAST SYNC edge, so a packet that starts during the
	 * call still locks on re-entry. */
	pop     {r0}			/* the entry stamp                   */
	bl      py32_hsical_event
	add     sp, #4			/* drop the alignment padding word   */
#endif
	pop     {r4-r7, pc}

	.size   usb_rx_engine16, . - usb_rx_engine16
	.ltorg

/* ============================== tables ===================================
 * The tables are NOT here.  Both engines need a CRC16 byte table and it is
 * the same 512-byte table, so there is one copy, in engine16_tx.S, inside a
 * single block that both engines index off their own base:
 *
 *   usb_tables + 0     T_UT        256 B   receive: unstuff + accumulate
 *   usb_tables + 256   usb_tx_tables       transmit: dispatch + T_TX
 *   usb_tables + 512   T_CRC16     512 B   SHARED, and +256 from usb_tx_tables
 *   usb_tables + 1024  T_CRC5       32 B   receive: token CRC5
 *
 * Neither engine can hold the shared copy at a link-time-unknown offset -
 * every timed table access is `ldrh rd,[base,rn]` with the base pinned by
 * SEG1 (r4+0 for T_UT) and by TXSEG4, and no register is free for a second
 * base - so the two 256-byte prefixes have to be one block, and the block
 * lives in one file.  The offsets above are asserted there, at assembly
 * time.  The generators that produce every entry are in the same place, with
 * the data.
 */
	.section .bss.usb_rxbuf, "aw", %nobits
	.balign USB_RXBUF_LEN		/* +2 is then 2 mod 4 and +4 is word-
					 * aligned, which is what the C layer
					 * requires of the payload pointer */
	.global usb_rxbuf
usb_rxbuf:
	.space  USB_RXBUF_LEN + 2	/* +2: see the note at USB_RXBUF_LEN */
