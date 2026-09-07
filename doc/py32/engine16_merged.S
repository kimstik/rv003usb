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
 * Placement: selected by USB_ENGINE16_FLASH, and the two configurations are
 * NOT the same object.  ENGINE16_SPEC.md S2 / FLASH_TIMING.md: an access is
 * priced by the memory it touches, not by the addressing mode -
 *
 *   code in flash:  port 1, flash (the tables) 2, RAM (rxbuf) 4
 *   code in RAM:    port 1, RAM (the tables) 2, flash 4
 *
 * so the tables must live in the SAME memory as the code - they do, both
 * engines' block is in engine16_tx.S in this same section - and the only
 * access whose cost differs between the two is the one RAM access in the
 * timed path: SEG3's `strb` into rxbuf, 2 from RAM and 4 from flash.  That
 * is the whole of the difference, it is declared in SEG3_CYC, and CELL pads
 * each configuration to exactly 16 on its own cost model.
 *
 * Flash-resident is the default because it is the configuration the stack
 * is built in (INTEGRATION_BUILD.md: 336 B of RAM against 3600 B) and the
 * one FLASH_TIMING.md says is worth finishing.  Cell 3 measured 18 there
 * until SEG3's commit-mask tail moved into SEG4; see SEG3.
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

/* ---- transmit-side port constants, for the Design B response path -------
 * Identical to engine16_tx.S's, and they must stay identical: the ACK this
 * engine emits by itself and the packets usb_send_data emits drive the same
 * two pins from the same GPIO.  MODER 0x00, BSRR 0x18 (BUILD_FACTS.md S7).
 * Low speed: J = D- high / D+ low, K = D+ high / D- low, SE0 = both low. */
#define USB_MODER_OFS   0x00
#define USB_BSRR_OFS    0x18
#define BSRR_J          ((1 << USB_DM_BIT) | (1 << (USB_DP_BIT + 16)))
#define BSRR_SE0        (USB_PINMASK << 16)
#define BSRR_TOGGLE     (USB_PINMASK | (USB_PINMASK << 16))
#define MODER_MASK      ((3 << (2*USB_DP_BIT)) | (3 << (2*USB_DM_BIT)))
#define MODER_OUT       ((1 << (2*USB_DP_BIT)) | (1 << (2*USB_DM_BIT)))

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

/* ---- the two side-channel bytes Design B needs, and why they are here ---
 * turnaround.md S4 (P4): the dispatch decision has to go to memory, because
 * every low register and r8-r12/r14 is live in the timed chain and there is
 * nowhere to keep it.  It goes in the receive buffer, above the reach of the
 * store: the emitted count is bounded to USB_BYTE_LIMIT = 24 by SEG5, so the
 * masked index the store uses is at most 24 and the highest byte SEG3 can
 * ever write is usb_rxbuf+2+24 = usb_rxbuf+26.  The buffer is
 * USB_RXBUF_LEN+2 = 34 bytes, so usb_rxbuf+28 and +29 are inside the
 * allocation and unreachable from the wire, for any input whatever.
 * Offsets below are from r9, which is usb_rxbuf+2 (F-5).
 *
 *   +26 (usb_rxbuf+28)  TB_OWED  non-zero <=> the last packet this engine
 *                       saw was a SETUP or OUT token addressed to us, so a
 *                       DATA packet is expected next and a handshake is
 *                       owed for it (turnaround.md S7.2).  This is the ONLY
 *                       speculative bit in Design B and it is decided before
 *                       the packet being answered even starts.
 *   +27 (usb_rxbuf+29)  TB_PID   the PID byte that response will carry.  It
 *                       is a PARAMETER, not a constant baked into the
 *                       transmitter (turnaround.md S9, R5): the SYNC goes on
 *                       the wire without it and it is not fetched until the
 *                       eight SYNC bit times are over.
 * Both are written only at the ISR exit (.Lusb_done / .Lusb_done_owed), i.e.
 * never inside a bit cell, and .bss zero-init means TB_OWED is clear at
 * reset. */
#define TB_OWED_OFS     26
#define TB_PID_OFS      27
#define TB_PID_ACK      0xD2		/* ACK, the only PID a DATA is owed  */

/* ---- Design B, the IN direction (design_b_in.md) -----------------------
 * The ACK direction's speculative bit is decided by the PREVIOUS packet.
 * An IN token has no predecessor that identifies it, so the stub reads the
 * received PID out of the buffer as well - a second 4-cycle RAM byte, and
 * design_b_in.md S2 is where the 17 cycles that pay for it come from.
 *
 * One arm record per endpoint, in RAM, written only outside every timed
 * path.  The record holds the packet ALREADY RENDERED TO WIRE BITS: NRZI is
 * "toggle on 0" and so is not in the data, but bit stuffing and the CRC16
 * ARE, so the payload cells have no pipeline at all - five cycles of
 * transmitter and nothing else.  That is what makes the IN direction fit a
 * 16-cycle cell when engine16_tx.S's own cells measure 18 (turnaround.md
 * S11.6): the work is not made cheaper, it is moved off the wire.
 *
 *   +0  u16 tokpat   the ONE legal (byte2,byte3) halfword for "IN, this
 *                    device, this endpoint" - address, endpoint and CRC5
 *                    together (turnaround.md S4.1).  Zero = not armed, and
 *                    zero is not a legal pattern: CRC5 of the all-zero
 *                    field is 0x02, so byte3 is at least 0x10.
 *   +2  u8  pid      0xC3 (DATA0) or 0x4B (DATA1), from e->toggle_in
 *   +3  u8  groups   whole 8-bit groups of rendered wire bits
 *   +4  u32 tail     the code pointer the payload chain jumps to when the
 *                    whole groups are done: usb_ti_T(8-t) for a final
 *                    partial group of t = nbits & 7 bits, or .Lti_eop when
 *                    the stream ends on a group boundary
 *   +8  u8  bits[TI_BITS_BYTES]   the stuffed data-bit stream, LSB first,
 *                    payload then CRC16.  Max is 8 payload + 2 CRC = 80
 *                    bits, plus at most 13 stuff bits = 93 -> 12 bytes, plus
 *                    one pad byte because the chain prefetches one group
 *                    ahead and the last prefetch reads past the end.
 *   +24 u32 src      WHERE the payload chain streams from.  For a payload
 *                    the application computes this is the record's own
 *                    bits[] above; for a static one it is a stream rendered
 *                    at BUILD TIME and sitting in flash (prerender.md), and
 *                    then the ~956 cycles usb_in_render costs are not spent
 *                    at all.  The indirection is what makes the second case
 *                    possible and it is free: cell Q1 loaded the record and
 *                    added 8 to it, and now loads the record and this word.
 */
#ifndef USB_TURNAROUND_B_IN
#define USB_TURNAROUND_B_IN     USB_TURNAROUND_B
#endif
#define TI_PAT_OFS      0
#define TI_PID_OFS      2
#define TI_GROUPS_OFS   3
#define TI_TAIL_OFS     4
#define TI_BITS_OFS     8
#define TI_BITS_BYTES   13
#define TI_SRC_OFS      24
#define TI_REC_SIZE     32		/* power of two: the index is a shift */
#define TI_REC_SHIFT    5
#define TI_PID_IN       0x69		/* the token PID this path answers   */

/* Pre-rendered records (doc/py32/prerender.md).  usb_in_render costs ~956
 * cycles = 40 us and lands on the ISR after the host's ACK; for a static
 * payload every one of them computes something tools/prerender_gen.py
 * already computed at build time.  Set to 0 to render everything at run
 * time - the three table symbols are weak, so a build that simply has no
 * generated table behaves that way without setting anything. */
#ifndef USB_PRERENDER
#define USB_PRERENDER   USB_TURNAROUND_B_IN
#endif

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

/* ------------------------------------------------------- code placement
 * 1: the engine and the shared table block are in .text.engine16, i.e. in
 *    flash, and rxbuf - the only RAM the timed path touches - costs 4.
 * 0: .datacode, copied to RAM at boot, and rxbuf costs 2.
 * engine16_tx.S carries the same switch and must be built with the same
 * value: the tables are in that file and a table read from the wrong memory
 * costs 4 in SEG0, SEG1 and SEG2, which no cell can absorb. */
/* ------------------------------------------ the Design B response path
 * turnaround.md S7: the engine emits the DATA handshake itself, SYNC first
 * and PID gated on the residue, without going through the C layer.  It is
 * on by default because it is what makes the turnaround conformant at
 * 24 MHz (S8.3).  It turns itself off under RV003USB_USER_DATA_HANDLES_TOKEN,
 * where the response to a DATA packet is NOT a foregone conclusion and the
 * user handler owns it (turnaround.md S4.2, rv003usb.c:359); with the option
 * set the arming flag is never written, so the whole path is unreachable and
 * the engine behaves exactly as it did before. */
#ifdef  RV003USB_USER_DATA_HANDLES_TOKEN
#define USB_TURNAROUND_B        0
#endif
#ifndef USB_TURNAROUND_B
#define USB_TURNAROUND_B        1
#endif

#ifndef USB_ENGINE16_FLASH
#define USB_ENGINE16_FLASH      1
#endif
#if USB_ENGINE16_FLASH
#define USB_RAM_ACCESS          4
#define USB_ENGINE16_SECTION    .section .text.engine16, "ax", %progbits
#else
#define USB_RAM_ACCESS          2
#define USB_ENGINE16_SECTION    .section .datacode, "ax", %progbits
#endif

/* Table offsets inside the single base held in r4.  The block itself is in
 * engine16_tx.S - see the note where the tables used to be - and asserts
 * these offsets there.
 *
 * T_CRC16 is at 0 and NOTHING ELSE MAY GO THERE (CRC_ROUND2.md S6 #1).  At
 * offset 0 SEG4's index is the bare `lsls r2,r2,#1`; at 512 it needed
 * `adds #T_CRC16/2-1 / lsls #1 / adds #2`, two cycles more, and those two
 * cycles are paid again in the flush for each of the two wire bytes the
 * turnaround waits on.  T_UT moved above it and pays nothing for the move
 * because its entries became words: its index is state*64 + nibble*4 ORed
 * into the entry's own low halfword, which already carries T_UT itself, so
 * the base is free exactly as it was at 0.  T_CRC5 must stay a whole
 * multiple of 256 for .Lcrc5_byte's index. */
#define T_UT            512		/* 128 words: unstuff + accumulate     */
#define T_CRC16         0		/* 256 halfwords: SHARED with the TX   */
					/* engine, which sees the same table   */
					/* at its own base + 256               */
#define T_CRC5          1024		/*  16 halfwords: CRC5 nibble step     */
	.if     T_CRC16 != 0
	.error  "engine16_merged: SEG4's index arithmetic needs T_CRC16 at 0"
	.endif
	.if     (T_UT & 511) != 0 || (T_UT >> 16) != 0
	.error  "engine16_merged: T_UT must be a multiple of 512 (SEG0's OR)"
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
 *   r8  park: low-nibble index (nibble*4), then the CRC16 table   
 *       value
 *   r9  rxbuf base (32-byte aligned)    r11 T_UT | unstuff state<<6
 *   r10 CRC16, or the parity accumulator, or unused - see USB_RX_CHECK
 *   r12 emitted byte count              r14 chain head (bit0 set, for bx)
 *
 * The table (T_UT) is indexed by  T_UT + state*64 + nibble*4  and returns
 *     entry = m<<27 | n<<24 | (T_UT | newstate<<6),  m = kept_bits + 2^n - 1
 * The low halfword is the NEXT lookup's index base, T_UT included, so the
 * table's own offset costs nothing to carry and T_CRC16 keeps offset 0.
 * n is how many of the nibble's four bits survive unstuffing, kept_bits are
 * those bits with the oldest at bit 0, and the +2^n-1 bias is what makes
 *     acc + (m << r0)
 * write the bits at the sentinel AND move the sentinel up by n, in one ADD,
 * with no branch and no second accumulator.  This is the single mechanism
 * that reconciles unrolling with bit stuffing: the wire is unrolled, the
 * data stream is not, and the table absorbs the difference.
 * ==================================================================== */

	.macro  SEG0			/* 7: high nibble -> table, state     */
	lsls    r1, r1, #2		/* 1  nibble*4                        */
	mov     r2, r11			/* 1  T_UT | state<<6, from the entry */
	orrs    r1, r2			/* 1  index                           */
	ldr     r1, [r4, r1]		/* 2  ONE access = NRZI+stuff+assembly*/
	uxth    r2, r1			/* 1  entry&0xFFFF = T_UT|newstate<<6 */
	mov     r11, r2			/* 1                                  */
	.endm

	.macro  SEG1			/* 11: append it, then low nibble     */
	lsls    r2, r1, #5		/* 1  \  n = (entry>>24)&7            */
	lsrs    r2, r2, #29		/* 1  /                               */
	lsrs    r1, r1, #27		/* 1  m                               */
	lsls    r1, r1, r0		/* 1  align to the sentinel           */
	adds    r3, r3, r1		/* 1  insert n bits, move sentinel    */
	adds    r0, r0, r2		/* 1  bit count += n                  */
	mov     r1, r8			/* 1  low nibble*4                    */
	mov     r2, r11			/* 1                                  */
	orrs    r1, r2			/* 1                                  */
	ldr     r1, [r4, r1]		/* 2                                  */
	.endm

/* Design B needs the flush cut finer than one segment per piece: eight TX
 * cells of 14 free cycles each have to hold SEG1..SEG6 (61) plus the gate
 * (36), and whole-segment packing strands 23 of the 112 (turnaround.md
 * S7.4's "nine timed pieces").  So SEG2, SEG3, SEG5 and SEG6 are each
 * expressed as two halves and the segment itself as their concatenation:
 * the timed chain and the ordinary flush emit exactly the instruction
 * stream they did before - the _A/_B pair is a source cut, not a code
 * change, and the objdump before and after is identical - and the
 * interleaved chain places the halves in different cells.  The cut points
 * are where only r2 (or r1) crosses the boundary, because TBCELL's two
 * instructions write r5 and the flags and nothing else; no cut separates a
 * CMP from its branch. */
	.macro  SEG2_A			/* 3                                  */
	uxth    r2, r1			/* 1                                  */
	mov     r11, r2			/* 1                                  */
	lsls    r2, r1, #5		/* 1                                  */
	.endm
	.macro  SEG2_B			/* 6                                  */
	lsrs    r2, r2, #29		/* 1                                  */
	lsrs    r1, r1, #27		/* 1                                  */
	lsls    r1, r1, r0		/* 1                                  */
	adds    r3, r3, r1		/* 1                                  */
	adds    r0, r0, r2		/* 1                                  */
	mov     r1, r9			/* 1  buffer base, 32-byte aligned    */
	.endm
	.macro  SEG2			/* 9: append it; fetch the rxbuf base */
	SEG2_A
	SEG2_B
	.endm

/* SEG3 is the cell FLASH_TIMING.md measured at 18: its `strb` into rxbuf is
 * the one RAM access in the timed path and costs 4 from flash-resident code.
 * The three cycles it is over by leave here rather than the index
 * computation FLASH_TIMING.md proposed moving.  Moving the index needs a
 * register to carry it across the cell boundary and there is none free
 * between cells 2 and 3 (r8 still holds SEGA's low-nibble park until SEG1
 * reads it, and computing the address in SEG2 instead costs SEG2 five
 * cycles against its two of slack).  The commit-mask tail needs no carrier:
 * r1 already survives the boundary - CELL clobbers only r2 - and nothing
 * between the two uses r0 or r2.  So `movs/bics/adds` moved to the head of
 * SEG4, which change 1 left with the room.  The flush emits SEG3 then SEG4
 * back to back, so the untimed copy is unchanged instruction for
 * instruction. */
#define SEG3_CYC        (6 + USB_RAM_ACCESS)
	.macro  SEG3_A			/* 6/8                                */
	mov     r2, r12			/* 1  emitted byte count              */
	lsls    r2, r2, #USB_RXBUF_MASK	/* 1  \ BALANCE: index &= 31, so no   */
	lsrs    r2, r2, #USB_RXBUF_MASK	/* 1  / store can leave the buffer    */
	strb    r3, [r1, r2]		/* 2/4  speculative store of the byte */
	subs    r0, r0, #8		/* 1  negative <=> byte not finished  */
	.endm
	.macro  SEG3_B			/* 2                                  */
	asrs    r1, r0, #31		/* 1  -1 if NOT finished              */
	mvns    r1, r1			/* 1  commit mask: -1 finished, 0 not */
	.endm
	.macro  SEG3			/* 9/11: bounded store, commit mask   */
	SEG3_A
	SEG3_B
	.endm

/* The three cycles SEG3 handed over.  They run at the head of every SEG4,
 * at every USB_RX_CHECK setting, because they are not part of the check:
 * they restore r0 (the bit count) after SEG3's speculative `subs #8`. */
	.macro  SEG3_TAIL		/* 3: restore the bit count           */
	movs    r2, #8			/* 1                                  */
	bics    r2, r1			/* 1  8 if not finished, else 0       */
	adds    r0, r0, r2		/* 1  bit count restored, or -8       */
	.endm

#if USB_RX_CHECK == USB_RX_CHECK_CRC16
#define SEG4_CYC 10
	.macro  SEG4			/* 10: 3 of SEG3, then the CRC16 step */
	SEG3_TAIL			/* 3                                  */
	mov     r2, r10			/* 1  running CRC                     */
	eors    r2, r3			/* 1  crc ^ candidate byte            */
	uxtb    r2, r2			/* 1                                  */
	lsls    r2, r2, #1		/* 1  index*2: T_CRC16 is 0, so that  */
					/*    is the whole address arithmetic */
	ldrh    r2, [r4, r2]		/* 2                                  */
	mov     r8, r2			/* 1  park; the commit is decided late*/
	.endm
#elif USB_RX_CHECK == USB_RX_CHECK_PARITY
#define SEG4_CYC 7
	/* Park the byte, gated.  r1 here is SEG3's "a byte finished" mask,
	 * NOT the CRC's later count>=3 mask - parity needs no such gate,
	 * because SYNC and PID contribute a known constant (CRC_PARITY.md
	 * §1.3) and the tail absorbs it.  r8 is free between SEG1 and SEG6,
	 * which is exactly the window the CRC16 build parks its table value
	 * in.  The byte must be taken HERE and not in SEG6: SEG5 shifts it
	 * out of r3. */
	.macro  SEG4			/* 7: 3 of SEG3, then park the byte   */
	SEG3_TAIL			/* 3                                  */
	mov     r2, r3			/* 1  accumulator; byte in bits 0..7  */
	uxtb    r2, r2			/* 1  drop the sentinel and leftovers */
	ands    r2, r1			/* 1  zero unless a byte finished     */
	mov     r8, r2			/* 1  park                            */
	.endm
#else
#define SEG4_CYC 3
	.macro  SEG4			/* 3: SEG3's tail, and no check       */
	SEG3_TAIL			/* 3                                  */
	.endm
#endif

	.macro  SEG5_A			/* 2                                  */
	movs    r2, #8			/* 1                                  */
	ands    r2, r1			/* 1  8 if a byte finished, else 0    */
	.endm
	.macro  SEG5_B ovf		/* 9                                  */
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
	.macro  SEG5 ovf		/* 11: drop, advance, bound, gate     */
	SEG5_A
	SEG5_B  \ovf
	.endm

#if USB_RX_CHECK == USB_RX_CHECK_CRC16
#define SEG6_CYC 10
	.macro  SEG6_A			/* 5                                  */
	mov     r2, r8			/* 1  parked table value              */
	ands    r2, r1			/* 1  zero unless committing          */
	mov     r8, r2			/* 1                                  */
	movs    r2, #8			/* 1                                  */
	ands    r2, r1			/* 1  shift 8 if committing, else 0   */
	.endm
	.macro  SEG6_B			/* 5                                  */
	mov     r1, r10			/* 1                                  */
	lsrs    r1, r1, r2		/* 1  crc>>8, or crc>>0               */
	mov     r2, r8			/* 1                                  */
	eors    r1, r2			/* 1  crc = (crc>>8) ^ T[..], or crc  */
	mov     r10, r1			/* 1                                  */
	.endm
	.macro  SEG6			/* 10: commit the CRC step, or not    */
	SEG6_A
	SEG6_B
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
	.macro  SEG6_A			/* 5                                  */
	mov     r1, r8			/* 1  the gated byte                  */
	lsrs    r2, r1, #4		/* 1  \                               */
	eors    r1, r2			/* 1   |                              */
	lsrs    r2, r1, #2		/* 1   | bit 0 = parity of the byte   */
	eors    r1, r2			/* 1   |                              */
	.endm
	.macro  SEG6_B			/* 5                                  */
	lsrs    r2, r1, #1		/* 1   |                              */
	eors    r1, r2			/* 1  /                               */
	mov     r2, r10			/* 1  running parity                  */
	eors    r2, r1			/* 1                                  */
	mov     r10, r2			/* 1                                  */
	.endm
	.macro  SEG6			/* 10: parity of the byte, into r10   */
	SEG6_A
	SEG6_B
	.endm
#else
#define SEG6_CYC 0
	.macro  SEG6_A			/* 0                                  */
	.endm
	.macro  SEG6_B			/* 0                                  */
	.endm
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
	lsrs    r2, r2, #26		/* 1  / low nibble * 4                */
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

/* ================= the free cycles, and what will not fit ================
 * Measured on the assembled object, flash-resident, USB_RX_CHECK=CRC16 and
 * T_CRC16 at offset 0 - the nops CELL emits, per wire byte:
 *
 *   cell   0  1  2  3  4  5  6  7      total
 *   free   4  0  2  1  1  0  1  0        9
 *
 * Nine, which is what CRC_ROUND2.md S3 predicts for this configuration.
 * CRC_ROUND2.md S3.2 then spends 28 of them - across wire bytes 2..5 - on
 * pre-staging the tail's SYNC compare, PID complement, PID type dispatch,
 * "is a response owed" and the C-call marshalling.  THAT DOES NOT FIT HERE,
 * and the reason is placement rather than budget:
 *
 * 1. There is no branch-free way to run a fragment on ONE wire byte.  The
 *    chain is unrolled on wire BITS and every wire byte runs the same eight
 *    cells; nothing distinguishes byte 2 from byte 7 without a test, and a
 *    taken branch inside a cell is 2-3 cycles that the cell does not have.
 *    So pre-staging costs its price on EVERY wire byte, against 9 free -
 *    not 28 spread over four bytes' 36.
 * 2. Within a cell the only register that may be written is r2: CELL
 *    clobbers it at the next boundary, and every other register is live
 *    across it (r0 count, r1 entry/mask, r3 accumulator, r4 tables, r5
 *    packer, r6 mask, r7 GPIO, r9-r12, r14).  r8 is free only between
 *    SEG1's `mov r1,r8` in cell 1 and SEG4's `mov r8,r2` in cell 4, i.e.
 *    in cells 2 and 3, which hold 2 and 1 of the free cycles.
 * 3. Flash-resident, a byte of rxbuf costs 4 to read (FLASH_TIMING.md), so
 *    the cheapest possible first step - `mov r2,r9 / ldrb r2,[r2,#1]` to
 *    fetch the PID - is 5 cycles, and it has to sit in a cell where r8 is
 *    free to carry the result to the store.  Cells 2 and 3 have 2 and 1.
 *    It does not fit before any of the computation, the dispatch table
 *    lookup or the 4-cycle `str` of the staged word is counted; the whole
 *    of P1 alone is ~14 per wire byte against 9.
 *
 * The 28 cycles are real and the 9 free cycles are real; what is not real
 * is the assumption that a cycle free in cell 0 can be spent by work that
 * needs a carrier register in cell 3.  Pre-staging needs either a spare
 * register across the cell boundary or a cheap per-byte gate, and this
 * engine has neither.  turnaround_sketch.S's 12-cycle tail assumes the work
 * has already moved; it has not, and the tail below is still 55 cycles from
 * the stuffing-violation test to the `bl` (measured, flash, DATA path).
 */

/* ========================================================================= */
	USB_ENGINE16_SECTION		/* .text.engine16, or .datacode:     */
					/* see the placement note above      */
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
	ldr     r0, =(.Lusb_done + 1)	/* B no longer reaches: the Design B  */
	bx      r0			/* block sits between here and there  */
	.ltorg				/* Second entry pool.  The stubs below
					 * grew when Design B's arming test
					 * went into them (EOPSTUB), and the
					 * entry's LDR-literals - which the
					 * comment at the first .ltorg records
					 * as already sitting 1016 of their
					 * 1020-byte reach from it - went out
					 * of range.  Nothing falls into this
					 * one either: the B above is
					 * unconditional. */

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
/* The stub also makes Design B's only speculative decision (turnaround.md
 * S7.2): if TB_OWED is set, the last packet was a SETUP or OUT token to this
 * device, a DATA packet is what just ended, a handshake is owed for it, and
 * the response path is taken.  If it is clear, nothing is owed and the
 * ordinary flush runs exactly as before.  The test is 6 cycles (a RAM byte
 * costs 4 from flash-resident code) and it is paid on both paths; on the
 * ordinary one it lands in a tail that is already 200+ cycles long, and on
 * the response one it is inside piece 0's slack.  r2 is the only free
 * register here - r1 already carries the table entry SEG\idx will consume -
 * which is why the base has to be re-fetched from r9 each time. */
	.macro  EOPSTUB idx, unsampled
rx_eop\idx:
	movs    r2, #\unsampled	/* 1  wire-bit slots never sampled   */
	mov     r14, r2			/* 1                                 */
#if USB_TURNAROUND_B
	mov     r2, r9			/* 1  rxbuf base                     */
	ldrb    r2, [r2, #TB_OWED_OFS]	/* 4  is a response owed?            */
	cmp     r2, #0			/* 1                                 */
	beq     1f			/* 1 NOT TAKEN on the response path  */
	b       tb_flush\idx		/* 2-3  yes: Design B                */
1:
#endif
#if USB_TURNAROUND_B_IN
	/* The IN arm, and it is DOWNSTREAM of the ACK test on purpose: an IN
	 * token has no predecessor that identifies it, so this path has to
	 * read the PID out of the buffer - a second 4-cycle RAM byte - and
	 * putting it first would charge those cycles to the DATA->ACK path
	 * that turnaround.md S11 already measured at tau+115.  Here the IN
	 * stub costs 21 against the ACK stub's 12, and design_b_in.md S2's
	 * 17 absent CRC16 cycles pay for it twice over. */
	mov     r2, r9			/* 1                                 */
	ldrb    r2, [r2, #1]		/* 4  the RECEIVED PID byte          */
	cmp     r2, #TI_PID_IN		/* 1                                 */
	bne     2f			/* 1 NOT TAKEN on the IN path        */
	ldr     r2, =(ti_flush\idx + 1)	/* 2/4  the IN chain is past a B's   */
	bx      r2			/* 3    reach from here              */
2:
#endif
	b       rx_flush\idx		/* 2-3  no: the ordinary path        */
	.endm

	EOPSTUB 0, 8
	EOPSTUB 1, 7
	EOPSTUB 2, 6
	EOPSTUB 3, 5
	/* A literal pool for the four stubs above: the IN arm reaches its
	 * flush chain through LDR-literal + BX (the chain is past a B's
	 * range) and the entry's pool is 0x444 back, outside the +-1020 an
	 * LDR-literal has.  Nothing falls into it - every stub ends in an
	 * unconditional B. */
	.ltorg

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
	movs    r2, #4			/* 1  SYNC low nibble * 4            */
	mov     r8, r2			/* 1                                 */
	movs    r2, #0			/* 1                                 */
	mov     r12, r2			/* 1  no bytes emitted yet           */
	movs    r2, #(T_UT >> 8)	/* 1  \ unstuff state 0, biased with */
	lsls    r2, r2, #8		/* 1   | T_UT: r11 IS the next index */
	mov     r11, r2			/* 1  /  base, table offset included */
	nop				/* 1  \  phase padding: 11 + 3 = 14  */
	nop				/* 1   | cycles, and 14 halfwords is  */
	nop				/* 1  /  4-aligned at usb_rx_chain    */
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
	USE     SEG3_CYC		/* flash 5+10+1nop; RAM 5+8+3nop     */
	CELL    4
	SEG4
	USE     SEG4_CYC		/* CRC16 5+10+1nop; parity 5+7+4nop; */
					/* none 5+3+8nop.  Always 16.        */
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
	EOPSTUB 4, 4
	EOPSTUB 5, 3
	EOPSTUB 6, 2
	EOPSTUB 7, 1
.Lovf_a:
	b       .Lusb_overrun

/* ================= Design B: SYNC-first, PID-gated =======================
 * turnaround.md S7.  SE0 is detected, the transmitter is armed, and eight
 * bit times of SYNC go on the wire BEFORE the CRC verdict exists.  That is
 * sound because SYNC carries no information: it is the same eight bits at
 * the head of every packet, so emitting it commits to "a packet is coming",
 * not to which packet (turnaround.md S6.2).  The PID - which is what
 * actually says ACK - stays downstream of the residue compare, so a false
 * ACK is structurally impossible.  Do not move the residue compare after
 * the PID; that is the whole claim of this design.
 *
 * The receive flush rides in the free cycles of the SYNC cells, exactly as
 * SEG0..SEG6 ride in the free cycles of the receive cells.  A SYNC bit
 * costs TWO cycles here, not the transmit engine's five, because during
 * SYNC the wire pattern is a compile-time constant - seven J<->K toggles
 * and one hold - so there is no bit queue to shift and no stuff state to
 * consult.  That leaves S = 14 free cycles per cell, 112 over the eight,
 * against turnaround.md S8.3's feasibility condition of S >= 8.
 *
 * Register map while this runs.  r7 (GPIO base) is shared with the receive
 * side unchanged, r6 (the D+|D- sample mask) is dead the moment SE0 is
 * detected and is reused for the BSRR toggle word, and r5 (the wire packer)
 * is dead the moment .Ltb_head has consumed it and is reused for the BSRR
 * word currently driven.  Nothing else moves: r0-r4 stay the flush's, r8-r12
 * stay the pipeline's, and the C-layer seam is untouched.
 *
 *   r5  the BSRR word on the wire    r6  BSRR_TOGGLE      r7  GPIO base
 *   r4  table base, then the PID bit queue
 *   r0,r1,r2,r3  the flush and the gate
 *   r8,r9,r10,r11,r12  the receive pipeline, exactly as in the flush
 *
 * The cycle ledger, from tau (the SE0-detecting LDR), for the worst entry
 * (every taken branch at 3):
 *
 *   detect ldr+ands+beq                  4..5
 *   EOPSTUB incl. the TB_OWED test         12   ->  17
 *   SEG_K..SEG6 of the byte in flight    B(K)   ->  17+B(K)
 *   .Ltb_head: NRZI of the partial byte    11
 *   SEG0 of the partial byte                7   ->  "flush piece 0"
 *   TBARM (drive J, pins to output)        13
 *   TBCELL 0: eors + str                    2   ->  FIRST WIRE EDGE
 *
 *   B(K):  K=1 61, K=2 50, K=3 41, K=4 31, K=5 21, K=6 10, K=7 0(+10 pad)
 *   first edge:  K=1 tau+111   K=2 100   K=3 91   K=4 81
 *                K=5 tau+71    K=6  60   K=7 63   K=0 103
 *
 * so the LATEST first response bit is tau+111 and the earliest tau+60,
 * inside turnaround.md S2's [tau+60, tau+124] window at every K.  tau+111
 * is (111-24)/16 = 5.44 bit times after SE0->J.
 * ======================================================================== */
#if USB_TURNAROUND_B

/* A SYNC cell: two cycles of transmitter, fourteen of receiver.  Same
 * accounting device as CELL - the ledger is assembled, not asserted. */
	.set    BCYC, 16

	.macro  TBCELL nm
	.if     BCYC > 16
	.error  "engine16_merged: Design B SYNC cell over 16 cycles"
	.endif
	.rept   16 - BCYC
	nop
	.endr
	.set    BCYC, 0
	.global usb_tb_\nm
usb_tb_\nm:
	eors    r5, r6			/* 1  J <-> K                        */
	str     r5, [r7, #USB_BSRR_OFS]	/* 1  the SYNC bit leaves here       */
	.set    BCYC, 2
	.endm

/* SYNC is 00000001 LSB-first, i.e. seven NRZI transitions and then a hold.
 * The last cell re-drives the level it is already at, so the store cadence
 * stays exactly 16 cycles and the pin is asserted, not merely left. */
	.macro  TBHOLD nm
	.if     BCYC > 16
	.error  "engine16_merged: Design B SYNC cell over 16 cycles"
	.endif
	.rept   16 - BCYC
	nop
	.endr
	.set    BCYC, 0
	.global usb_tb_\nm
usb_tb_\nm:
	nop				/* 1  data 1 = no transition         */
	str     r5, [r7, #USB_BSRR_OFS]	/* 1                                 */
	.set    BCYC, 2
	.endm

	.macro  TBUSE n
	.set    BCYC, BCYC + (\n)
	.endm

	.macro  TBPAD			/* close a cell that ends in a BX    */
	.if     BCYC > 16
	.error  "engine16_merged: Design B cell over 16 cycles"
	.endif
	.rept   16 - BCYC
	nop
	.endr
	.set    BCYC, 16
	.endm

/* One PID/EOP bit cell.  Here the pattern is NOT a constant - R5 of
 * turnaround.md S9 requires the response PID to be a parameter, and it is
 * not fetched until the eight SYNC bit times are already on the wire - so
 * this is the transmit engine's own five-cycle cell: shift the queue,
 * toggle on a 0, store.  r4 is the queue, LSB first. */
	.macro  TBBIT nm
	.if     BCYC > 16
	.error  "engine16_merged: Design B PID cell over 16 cycles"
	.endif
	.rept   16 - BCYC
	nop
	.endr
	.set    BCYC, 0
	.global usb_tb_\nm
usb_tb_\nm:
	lsrs    r4, r4, #1		/* 1  C = the data bit               */
	sbcs    r2, r2			/* 1  0 on a 1, -1 on a 0            */
	ands    r2, r6			/* 1  the toggle word, or nothing    */
	eors    r5, r2			/* 1  NRZI: toggle on 0              */
	str     r5, [r7, #USB_BSRR_OFS]	/* 1                                 */
	.set    BCYC, 5
	.endm

/* Arm the transmitter: 13 cycles, and it drives nothing until its eighth.
 * ODR is written BEFORE MODER because the reset ODR is 0 on both pins,
 * which is SE0 - making the pins outputs first would put an SE0 on the bus.
 * turnaround.md S9 R3: do not drive before tau+40.  The MODER store is the
 * 11th of these 13 cycles and the first wire edge is 4 cycles after it, so
 * on the earliest path (K=6, first edge tau+60) it lands at tau+56. */
	.macro  TBARM
	ldr     r2, [r7, #USB_MODER_OFS]	/* 1                         */
	ldr     r5, =MODER_MASK			/* 2                         */
	bics    r2, r5				/* 1                         */
	ldr     r5, =MODER_OUT			/* 2                         */
	orrs    r2, r5				/* 1                         */
	ldr     r5, =BSRR_J			/* 2                         */
	str     r5, [r7, #USB_BSRR_OFS]		/* 1  ODR = J                */
	str     r2, [r7, #USB_MODER_OFS]	/* 1  now the pins drive J   */
	ldr     r6, =BSRR_TOGGLE		/* 2                         */
	.endm					/* = 13                      */

/* The response PID.  Five cycles, and they are the whole of R5: nothing
 * about which packet this is has been decided before here. */
	.macro  TBPIDLOAD
	mov     r2, r9			/* 1  through r2, not r4: r4 is the  */
	ldrb    r4, [r2, #TB_PID_OFS]	/* 4  TABLE base until this very     */
	.endm				/* = 5   instruction, and pricing the*/
					/*    load as a flash-table access   */
					/*    would understate it by 2       */

/* ---- the gate: everything that must be true before a PID may be emitted -
 * 36 cycles at USB_RX_CHECK=CRC16, cut into four pieces that ride in cells
 * 4, 5, 6 and 7.  It is the tail's validation with the token and handshake
 * arms removed - the only PID this path can answer is DATA - plus the alias
 * test .Ldata does.  Every branch is NOT TAKEN on the good path and costs 1;
 * a taken one leaves the timed chain for .Ltb_abort, where timing no longer
 * means anything because what is being emitted is deliberately a corrupt
 * packet.  The ordinary tail still runs afterwards and re-validates from the
 * same bytes before it calls C, so this gate is not the only check - but it
 * IS the only thing between the wire and an ACK PID.
 *
 * The cut is four pieces rather than three because cell 7 also carries the
 * BX into the PID chain, and a flash literal costs 2 from flash-resident
 * code and 4 from RAM-resident code.  Three pieces fitted the flash column
 * and overran the RAM one by a cycle - a hole the .error cannot see, because
 * the ledger is what it checks.  Four pieces fit both columns and all three
 * USB_RX_CHECK settings, which is why the sizes below are expressions and
 * not constants. */
	.macro  TBGATE_A		/* 4:  the stuffing violation        */
	mov     r2, r11			/* 1  T_UT | state<<6                */
	lsrs    r2, r2, #6		/* 1                                 */
	cmp     r2, #((T_UT >> 6) + 7)	/* 1  sticky stuffing violation      */
	beq     .Ltb_abort		/* 1                                 */
	.endm

	.macro  TBGATE_B		/* 10 + a RAM access: length, SYNC   */
	mov     r0, r12			/* 1  emitted byte count             */
	cmp     r0, #4			/* 1  SYNC + PID + CRC16 at least    */
	blo     .Ltb_abort		/* 1                                 */
	cmp     r0, #12			/* 1  longer than any legal LS packet*/
	bhi     .Ltb_abort		/* 1                                 */
	mov     r1, r9			/* 1                                 */
	ldrh    r3, [r1, #0]		/* 2/4  SYNC and PID in ONE access - */
					/*    rxbuf+2 is 2-aligned (F-5)     */
	uxtb    r2, r3			/* 1  the SYNC byte                  */
	cmp     r2, #0x80		/* 1  decodes to 0x80, oldest first  */
	bne     .Ltb_abort		/* 1                                 */
	lsrs    r3, r3, #8		/* 1  r3 = the PID byte              */
	.endm

	.macro  TBGATE_C		/* 12: the PID, three ways           */
	lsrs    r2, r3, #4		/* 1                                 */
	movs    r1, #0x0F		/* 1                                 */
	ands    r1, r3			/* 1                                 */
	eors    r2, r1			/* 1  high nibble must be ~low       */
	cmp     r2, #0x0F		/* 1                                 */
	bne     .Ltb_abort		/* 1                                 */
	movs    r2, #3			/* 1                                 */
	ands    r2, r3			/* 1  PID type field                 */
	cmp     r2, #3			/* 1  DATA0/DATA1 and nothing else   */
	bne     .Ltb_abort		/* 1                                 */
	lsls    r2, r3, #29		/* 1  PID bit 2 -> sign              */
	bmi     .Ltb_abort		/* 1  DATA2/MDATA: not on an LS bus  */
	.endm

#if USB_RX_CHECK == USB_RX_CHECK_CRC16
	.macro  TBGATE_D		/* 6:  THE RESIDUE                   */
	mov     r1, r10			/* 1  the running CRC16              */
	movs    r2, #0xB0		/* 1  \ 0xB001, built rather than    */
	lsls    r2, r2, #8		/* 1   | loaded: no literal pool in   */
	adds    r2, #1			/* 1  / range matters inside a cell  */
	cmp     r1, r2			/* 1  Everything below this line is  */
	bne     .Ltb_abort		/* 1  downstream of it.              */
	.endm
#elif USB_RX_CHECK == USB_RX_CHECK_PARITY
	.macro  TBGATE_D		/* 3                                 */
	mov     r1, r10			/* 1                                 */
	lsrs    r1, r1, #1		/* 1  C = accumulated parity         */
	bcc     .Ltb_abort		/* 1                                 */
	.endm
#else
	/* USB_RX_CHECK_NONE: there is no residue to gate on, so the ACK is
	 * downstream of nothing but the PID tests.  That is the documented
	 * meaning of this setting - it is what V-USB, Grainuum and LemcUSB
	 * ship (CRC_ALTERNATIVES.md S9.1) - and it degrades Design B's
	 * no-false-ACK property exactly as far as it degrades the ordinary
	 * tail's, which is all the way.  Do not select it and then quote
	 * turnaround.md S7.3. */
	.macro  TBGATE_D		/* 0                                 */
	.endm
#endif

	.macro  TBPADTO n		/* pad this cell up to \n cycles     */
	.if     BCYC > (\n)
	.error  "engine16_merged: Design B cell over budget"
	.endif
	.rept   (\n) - BCYC
	nop
	.endr
	.set    BCYC, (\n)
	.endm

/* Declared costs of the segment halves, so the cell ledger below is
 * assembled rather than asserted.  They track the macros above. */
#define SEG0_CYC        7
#define SEG1_CYC        11
#define SEG2A_CYC       3
#define SEG2B_CYC       6
#define SEG3A_CYC       (4 + USB_RAM_ACCESS)
#define SEG3B_CYC       2
#define SEG5A_CYC       2
#define SEG5B_CYC       9
#if USB_RX_CHECK == USB_RX_CHECK_NONE
#define SEG6A_CYC       0
#define SEG6B_CYC       0
#else
#define SEG6A_CYC       5
#define SEG6B_CYC       5
#endif
/* GATE1 ends with an LDRH out of rxbuf and TBPIDLOAD with an LDRB out of
 * it, and rxbuf is RAM: 4 cycles from flash-resident code, 2 from RAM-
 * resident.  Declaring them as constants made cells B4 and B5 come out at
 * 14 real cycles in the RAM-resident build while the assembler's ledger
 * still read 16 - a two-cycle hole in the SYNC cadence that the .error
 * cannot see, because the ledger is what it checks. */
#define GATEA_CYC       4
#define GATEB_CYC       (10 + USB_RAM_ACCESS)
#define GATEC_CYC       12
#if USB_RX_CHECK == USB_RX_CHECK_CRC16
#define GATED_CYC       6
#elif USB_RX_CHECK == USB_RX_CHECK_PARITY
#define GATED_CYC       3
#else
#define GATED_CYC       0
#endif
#define PIDLOAD_CYC     (1 + USB_RAM_ACCESS)
/* A PC-relative literal is 2 cycles from flash-resident code and 4 from
 * RAM-resident code (ENGINE16_SPEC.md S2).  Cells B7 and A4 end in
 * LDR-literal + BX and the difference lands inside them. */
#if USB_ENGINE16_FLASH
#define LIT_CYC         2
#else
#define LIT_CYC         4
#endif

/* ---- piece 0: from the EOP stub to the first wire edge ------------------
 * The byte in flight is finished here, untimed, exactly as the ordinary
 * flush finishes it - same macros, so the two copies cannot drift - and so
 * is the NRZI decode of the partial byte and its SEG0.  Everything after
 * SEG0 rides in the SYNC cells.
 *
 * turnaround.md S8.3 pads piece 0 to a constant 100-A cycles so the first
 * edge is always at tau+112.  That is not done here and must not be: the
 * work in piece 0 varies by 61 cycles between K=1 and K=7, the legal window
 * for the first response bit is [tau+60, tau+124] and is only 64 cycles
 * wide, and padding every K up to the worst one buys nothing - a response
 * that starts at tau+71 is as conformant as one that starts at tau+111, and
 * cheaper.  Only K=7, which would otherwise start at tau+50 and violate the
 * 2-bit-time MINIMUM of S7.1.18 (turnaround.md S9 R4), is padded, and it is
 * padded to the floor and not to the ceiling. */
.Lovf_c:
	b       .Ltb_dropped		/* overflow in piece 0: nothing has   */
					/* been driven yet, so this is silence*/
	.global tb_flush7
tb_flush7:
	.rept   10			/* R4: without this the first edge    */
	nop				/* would be tau+50, inside the host's */
	.endr				/* own EOP.  See the ledger above.    */
	b       .Ltb_head

/* K = 0 is the one entry with no partial byte at all: the byte in flight
 * completed on the cell boundary, and r14 = 8 says none of the next byte's
 * bit slots were sampled.  Feeding the eight unsampled slots through the
 * pipeline anyway is not an option - they decode as eight data 0s, which
 * completes a spurious byte, advances the emitted count and folds a wrong
 * byte into the CRC - so K=0 gets its own entry and its own first five SYNC
 * cells, and rejoins the shared chain at cell 5 where the gate begins. */
	.global tb_flush0
tb_flush0:
	SEG0
	SEG1
	SEG2
	SEG3
	SEG4
	SEG5    .Lovf_c
	SEG6
	b       .Ltb_arm_k0
	.global tb_flush1
tb_flush1:	SEG1
	.global tb_flush2
tb_flush2:	SEG2
	.global tb_flush3
tb_flush3:	SEG3
	.global tb_flush4
tb_flush4:	SEG4
	.global tb_flush5
tb_flush5:	SEG5    .Lovf_c
	.global tb_flush6
tb_flush6:	SEG6

/* The partial byte's NRZI decode.  This is the last reader of r5, which is
 * what frees it to become the BSRR word the SYNC cells drive.  Unlike the
 * ordinary flush there is no "r14 == 8" test here: K=0 never reaches this
 * code. */
	.global usb_tb_head
usb_tb_head:
.Ltb_head:
	.rept   4			/* R4 again, at the other end of the */
	nop				/* spread: without these the earliest */
	.endr				/* path (K=6, every branch at its     */
					/* minimum) starts at tau+58, two     */
					/* cycles inside the 2-bit-time floor.*/
	mov     r2, r14			/* 1  8-k, the unsampled slots       */
	lsrs    r1, r5, #1		/* 1  \                              */
	eors    r1, r5			/* 1   |                             */
	mvns    r1, r1			/* 1   | NRZI over the whole byte    */
	uxtb    r1, r1			/* 1   |                             */
	lsls    r1, r1, r2		/* 1   | k real bits to the top      */
	uxtb    r1, r1			/* 1  /                              */
	lsls    r2, r1, #28		/* 1  \                              */
	lsrs    r2, r2, #26		/* 1   | low nibble * 4              */
	mov     r8, r2			/* 1  /  parked for SEG1             */
	lsrs    r1, r1, #4		/* 1  high nibble; r5 is now DEAD    */
	SEG0				/* 7  the last of piece 0            */
	TBARM				/* 13                                */

/* ---- the eight SYNC cells: 2 cycles of wire, 14 of receive engine ------- */
	TBCELL  B0
	SEG1
	TBUSE   SEG1_CYC
	SEG2_A
	TBUSE   SEG2A_CYC

	TBCELL  B1
	SEG2_B
	TBUSE   SEG2B_CYC
	SEG3_A
	TBUSE   SEG3A_CYC

	TBCELL  B2
	SEG3_B
	TBUSE   SEG3B_CYC
	SEG4
	TBUSE   SEG4_CYC
	SEG5_A
	TBUSE   SEG5A_CYC

	TBCELL  B3
	SEG5_B  .Lovf_d			/* the bound is still enforced, and   */
	TBUSE   SEG5B_CYC		/* now it aborts a packet in flight   */
	SEG6_A
	TBUSE   SEG6A_CYC

	TBCELL  B4
	SEG6_B
	TBUSE   SEG6B_CYC
	TBPIDLOAD			/* the flush is finished; r4 is free  */
	TBUSE   PIDLOAD_CYC
	TBGATE_A
	TBUSE   GATEA_CYC

	TBCELL  B5			/* <- K=0 joins here                  */
	TBGATE_B
	TBUSE   GATEB_CYC

	TBCELL  B6
	TBGATE_C
	TBUSE   GATEC_CYC

	TBHOLD  B7
	TBGATE_D			/* THE RESIDUE.  Fall through = ACK.  */
	TBUSE   GATED_CYC
	movs    r0, #1			/* 1  "a PID went out" - read by the  */
	TBUSE   1			/*    EOP, which is shared with abort */
	TBPADTO (16 - LIT_CYC - 3)
	ldr     r2, =(.Ltb_pid + 1)	/* 2/4                                */
	bx      r2			/* 3  flat 3: no 2-vs-3 in a cell     */
	TBUSE   (LIT_CYC + 3)
	.if     BCYC != 16
	.error  "engine16_merged: Design B SYNC cell 7 is not 16 cycles"
	.endif
.Lovf_d:
	b       .Ltb_abort
	.ltorg

/* ---- the abort ---------------------------------------------------------
 * turnaround.md S6.4 and S7.3.  Stop toggling: with no store the pins keep
 * driving the level the last SYNC cell left, and an unchanging level is a
 * run of consecutive 1s in NRZI.  Seven of them is a bit stuff violation,
 * which every receiver must detect (S7.1.9), and the eight bits decoded in
 * the PID position are 0xFF, whose nibbles are not complements, so the PID
 * check fails too (S8.3.1).  Then a proper EOP, because a packet that never
 * ends is not a case the specification describes (S6.3).
 *
 * What the host does with it: it discards the packet, sees no valid
 * handshake for the data stage, and retries - which is correct, because the
 * device did not accept the data and did not toggle its sequence bit.  The
 * honest limit of the argument is in turnaround.md S6.4: S8.4.5 says be
 * silent on a CRC error and this is not silent.  What it is, is
 * indistinguishable at the host's receiver from a handshake the BUS
 * corrupted - a failure mode every host is already required to handle. */
.Ltb_abort:
	movs    r1, #40			/* 40 * (subs + taken bne) = 120..160 */
1:	subs    r1, #1			/* cycles = 7.5..10 bit times of an   */
	bne     1b			/* unchanging level.  >= 7 is needed.  */
	movs    r0, #0			/* no PID went out                    */
	b       .Ltb_eop

/* ---- K = 0: arm, then five SYNC cells of its own ----------------------- */
.Ltb_arm_k0:
	TBARM
	TBCELL  A0
	TBPADTO 16
	TBCELL  A1
	TBPADTO 16
	TBCELL  A2
	TBPADTO 16
	TBCELL  A3
	TBPADTO 16
	TBCELL  A4
	TBPIDLOAD
	TBUSE   PIDLOAD_CYC
	TBGATE_A			/* K=0 skips cell B4, so it carries   */
	TBUSE   GATEA_CYC		/* B4's piece of the gate itself      */
	TBPADTO (16 - LIT_CYC - 3)
	ldr     r2, =(usb_tb_B5 + 1)	/* 2/4  join the shared chain at the  */
	bx      r2			/* 3    cell where the gate resumes   */
	TBUSE   (LIT_CYC + 3)
	.if     BCYC != 16
	.error  "engine16_merged: Design B cell A4 is not 16 cycles"
	.endif

/* ---- the PID: eight cells, and only now is it known ------------------- */
	.set    BCYC, 16
.Ltb_pid:
	TBBIT   P0
	TBBIT   P1
	TBBIT   P2
	TBBIT   P3
	TBBIT   P4
	TBBIT   P5
	TBBIT   P6
	TBBIT   P7
	TBPADTO 16
	/* No bit stuffing is needed and none is done.  A legal PID is
	 * (~n<<4)|n, so its longest run of 1s is four; SYNC ends with one
	 * data 1, so the longest run this packet can present is five, and
	 * the rule inserts a 0 only after six.  ACK (0xD2) runs to two. */

/* ---- EOP: SE0 for two bit times, driven J for one, then release -------- */
.Ltb_eop:
	nop				/* 1                                 */
	nop				/* 1                                 */
	lsrs    r2, r6, #16		/* 1  \ the toggle word's reset half */
	lsls    r2, r2, #16		/* 1  / = both pins low = SE0        */
	str     r2, [r7, #USB_BSRR_OFS]	/* 1  =5, on the same cadence        */
	.rept   11
	nop
	.endr				/* =16                               */
	ldr     r2, =BSRR_J		/* 2  built in the spare cycles of   */
	.rept   14			/*    the second SE0 bit time        */
	nop
	.endr				/* =16                               */
	nop
	nop
	nop
	nop
	str     r2, [r7, #USB_BSRR_OFS]	/* 1  =5  driven J for one bit time  */
	.rept   11
	nop
	.endr				/* =16                               */

	/* Untimed from here.  Release the pins; the pull-up holds J. */
	ldr     r1, =MODER_MASK
	ldr     r2, [r7, #USB_MODER_OFS]
	bics    r2, r1
	str     r2, [r7, #USB_MODER_OFS]

	cmp     r0, #0
	beq     .Ltb_dropped
.Ltb_sent:
	/* The ACK is on the wire.  The C layer is called AFTERWARDS, through
	 * the ordinary tail and with the seam unchanged (ENGINE16_SPEC.md
	 * S4): .Lrx_tail re-validates from the same buffer bytes and reaches
	 * usb_pid_handle_data with the same prototype it always had.  The one
	 * thing that must not happen twice is the handshake, and
	 * rv003usb.c's just_ack unconditionally calls usb_send_data - so the
	 * transmitter is told to swallow exactly one call.  The C source does
	 * not change; usb_send_data is ours. */
	ldr     r4, =usb_tables		/* the table base, clobbered by the  */
					/* PID load, and .Lrx_tail's token   */
					/* arm would want it                 */
	movs    r1, #1
	ldr     r2, =usb_tx_suppress
	strb    r1, [r2]
	b       .Lrx_tail
.Ltb_dropped:
	b       .Lusb_done
	.ltorg


#endif	/* USB_TURNAROUND_B */

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
	lsrs    r2, r2, #26
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
	mov     r2, r11			/* T_UT | state<<6 - the biased form */
	lsrs    r2, r2, #6		/* the state, T_UT>>6 above it       */
	cmp     r2, #((T_UT >> 6) + 7)	/* sticky stuffing violation         */
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
	blo     .Ldone_t
	/* CRC_ROUND2.md S1.4: the complement test above admits four PIDs of
	 * type 3, and only two of them are legal on a low-speed bus - DATA0
	 * (0xC3) and DATA1 (0x4B).  The other two are DATA2 (0x87) and MDATA
	 * (0x0F), high-speed PIDs, and 0x87 is exactly what a one-cell sample
	 * slip inside the PID turns DATA0 into: its nibbles are still
	 * complements, so nothing upstream rejects it, and 79 of the 346
	 * undetected slips in that study were this alias.  The four differ in
	 * bit 2 - low nibble 3, B legal; 7, F not - so one shifted test
	 * separates them, and a packet that reached here with 0x87 or 0x0F is
	 * either corrupt or not addressed to a low-speed device. */
	lsls    r1, r5, #29		/* PID bit 2 -> sign: DATA2 or MDATA */
	bmi     .Ldone_t
#if USB_RX_CHECK == USB_RX_CHECK_CRC16
	mov     r1, r10
	ldr     r2, =0x0000B001
	cmp     r1, r2
	bne     .Ldone_t
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
	bcc     .Ldone_t
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
#if USB_TURNAROUND_B && USB_TURNAROUND_B_IN
	bl      .Lin_refresh
#endif
	b       .Lusb_done

/* Token: CRC5 over the eleven address/endpoint bits plus the five CRC bits
 * leaves the constant residue 0x06 (verified numerically here). */
.Ltoken:
	cmp     r0, #4
	bne     .Ldone_t
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
#if USB_TURNAROUND_B && USB_TURNAROUND_B_IN
	bl      .Lin_refresh
#endif
	b       .Lusb_done_owed		/* a DATA packet follows; we owe it  */
					/* an ACK - turnaround.md S7.2       */
.Lt_in:
#if USB_TURNAROUND_B && USB_TURNAROUND_B_IN
	/* Learn the arm pattern rather than compute it.  CRC5 has just been
	 * checked, the endpoint bounded and the address filtered, so
	 * rxbuf[2..3] is a VERIFIED "IN, this device, this endpoint" halfword
	 * - exactly the one value the gate compares against (design_b_in.md
	 * S6).  A CRC5 computed here would be the same number arrived at the
	 * long way, and it would have to be recomputed whenever the device
	 * address changes; a learned pattern re-learns itself.
	 * r2 is the endpoint and r6 the rxbuf base, both still live; r0-r3
	 * are the call's arguments and are not touched. */
	ldrh    r6, [r6, #2]		/* the token halfword                */
	lsls    r5, r2, #TI_REC_SHIFT
	ldr     r7, =usb_in_arm
	adds    r7, r7, r5
	ldrh    r5, [r7, #TI_PAT_OFS]
	cmp     r5, r6			/* already armed for exactly this    */
	beq     1f			/* token?  Then the response was     */
					/* the pre-rendered one, no endpoint */
					/* state has moved, and re-rendering */
					/* it would only call the user's IN  */
					/* handler a second time.            */
	movs    r5, #0
	strh    r5, [r7, #TI_PAT_OFS]	/* disarm NOW: only a completed      */
					/* render re-arms, so a handler that */
					/* sends nothing leaves nothing armed*/
	ldr     r7, =usb_in_pend
	strh    r6, [r7, #0]
	strb    r2, [r7, #3]
	movs    r6, #1
	strb    r6, [r7, #2]		/* one-shot: usb_send_data renders   */
1:
#endif
	bl      usb_pid_handle_in
	add     sp, #4
	/* No refresh here.  An IN token changes no endpoint state - e->count
	 * and e->toggle_in move on the host's ACK, not on the token - so the
	 * record the call above just rendered is the right one until that ACK
	 * arrives, and calling the user's IN handler a second time per token
	 * would be visible to it for nothing. */
	b       .Lusb_done
.Lt_out:
	bl      usb_pid_handle_out
	add     sp, #4
	b       .Lusb_done_owed

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
#if USB_TURNAROUND_B && USB_TURNAROUND_B_IN
	bl      .Lin_refresh
#endif
	b       .Lusb_done

#if USB_TURNAROUND_B && USB_TURNAROUND_B_IN
/* ---- re-render the armed IN response ----------------------------------
 * The arm record holds a packet, not a promise, so it goes stale the moment
 * the state that produced it moves: usb_pid_handle_ack flips e->toggle_in
 * and advances e->count, usb_pid_handle_data fills in what the data stage
 * will return, and usb_pid_handle_setup resets both.  After each of them
 * this calls usb_pid_handle_in for the endpoint that changed, with the
 * transmitter suppressed, so the only effect is that usb_send_data renders
 * the NEXT response into the record.
 *
 * This is the additional call ENGINE16_SPEC.md S4 allows and it is not a
 * replacement: usb_pid_handle_in keeps its prototype and the C source is
 * unchanged.  It is safe to call early because it mutates nothing the next
 * IN depends on - e->count and e->toggle_in move only in
 * usb_pid_handle_ack and usb_pid_handle_setup, and ist->current_endpoint is
 * set to the value it already has.  What it DOES do is evaluate the payload
 * one transaction early, and design_b_in.md S9 lists the three places where
 * that is visible.
 *
 * usb_in_pend's pattern is left at zero, meaning "keep the one already
 * learned": a refresh knows the endpoint but has no token in front of it. */
	.thumb_func
.Lin_refresh:
	/* Four words, not five: every other call in this tail is reached with
	 * SP at the alignment `push {r4}` then makes 8-aligned for the AAPCS,
	 * and an odd-sized frame here would invert that for the call below.
	 * lr rides in r7 and comes back in it. */
	mov     r7, lr
	push    {r4-r7}
	ldr     r4, =rv003usb_internal_data
	ldrb    r2, [r4, #0]		/* ist->current_endpoint, offset 0 at */
	cmp     r2, #ENDPOINTS		/* both settings of TURBO8TYPE        */
	bhs     1f
	ldr     r7, =usb_in_pend
	lsls    r5, r2, #TI_REC_SHIFT
	ldr     r6, =usb_in_arm
	adds    r6, r6, r5
	ldrh    r5, [r6, #TI_PAT_OFS]	/* carry the learned pattern across   */
	strh    r5, [r7, #0]		/* the rebuild...                     */
	movs    r5, #0
	strh    r5, [r6, #TI_PAT_OFS]	/* ...and disarm until it completes   */
	strb    r2, [r7, #3]
	movs    r6, #1
	strb    r6, [r7, #2]
	ldr     r7, =usb_tx_suppress	/* render, do not transmit            */
	strb    r6, [r7, #0]
	mov     r1, r9			/* a 4-aligned scratchpad: rxbuf+4,   */
	adds    r1, r1, #2		/* whose packet has been handled      */
	movs    r0, #0
	movs    r3, #0
	push    {r4}
	bl      usb_pid_handle_in
	add     sp, #4
1:	pop     {r4-r7}
	bx      r7
#endif

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

/* The two exits differ in one thing: whether the packet just handled was a
 * SETUP or OUT token addressed to this device, in which case the host will
 * send a DATA packet next and this engine owes it a handshake.  That, and
 * the PID the handshake will carry, are the whole of Design B's pre-staged
 * state (see TB_OWED_OFS).  Writing them HERE - after the C dispatch, past
 * every timed path - is what makes turnaround.md S7.2's claim true: the
 * arming decision never depends on anything that arrives in the last bit
 * times of the packet being answered.
 * Every path out of the tail passes through one of these two labels, so the
 * flag is one-shot: it is cleared by the very packet it armed.  The
 * keepalive exit does NOT pass through here, deliberately - a keep-alive EOP
 * between the token and its data must not disarm us. */
.Lusb_done_owed:
	movs    r0, #USB_TURNAROUND_B
	movs    r1, #TB_PID_ACK
	b       1f
.Lusb_done:
	movs    r0, #0
	movs    r1, #0
1:	mov     r2, r9
	strb    r0, [r2, #TB_OWED_OFS]
	strb    r1, [r2, #TB_PID_OFS]
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

/* ======================= Design B, the IN direction =======================
 * design_b_in.md.  Same shape as the DATA->ACK path above and three things
 * different:
 *
 *   1. The arming decision is made from the packet IN FLIGHT, not from its
 *      predecessor, so the EOP stub reads usb_rxbuf+1 - a second RAM byte,
 *      9 cycles - and it is placed downstream of the ACK test so the ACK
 *      path measured at tau+115 pays none of it.
 *   2. A TOKEN NEEDS NO CRC16.  SEG4's table step and the whole of SEG6
 *      compute a residue that .Ltoken never reads, so this flush drops them:
 *      17 cycles, twice what the second RAM read cost (design_b_in.md S2).
 *   3. The response carries a PAYLOAD, and the payload is not produced here.
 *      It was rendered to wire bits - stuffed, CRC16 appended, LSB first -
 *      by usb_send_data on an earlier transaction, into the endpoint's arm
 *      record.  So the payload cell is five cycles of transmitter and no
 *      pipeline at all, which is why it holds 16 where engine16_tx.S's own
 *      cells measure 18 (turnaround.md S11.6).  The work is not cheaper; it
 *      is off the wire.
 *
 * The gate ends at ONE comparison - the received (byte2,byte3) halfword
 * against the arm record's pattern - and that comparison is the address,
 * the endpoint, the CRC5 and "is anything rendered for this endpoint"
 * simultaneously (turnaround.md S4.1).  A DATA0/DATA1 PID is downstream of
 * it, exactly as the ACK PID is downstream of the residue.
 * ======================================================================== */
#if USB_TURNAROUND_B && USB_TURNAROUND_B_IN

/* SYNC and payload cells.  Identical machinery to TBCELL/TBHOLD/TBBIT, own
 * labels so the two chains are separable in a disassembly and in the tool's
 * per-block output. */
	.macro  TICELL nm
	.if     BCYC > 16
	.error  "engine16_merged: Design B IN cell over 16 cycles"
	.endif
	.rept   16 - BCYC
	nop
	.endr
	.set    BCYC, 0
	.global usb_ti_\nm
usb_ti_\nm:
	eors    r5, r6			/* 1  J <-> K                        */
	str     r5, [r7, #USB_BSRR_OFS]	/* 1                                 */
	.set    BCYC, 2
	.endm

	.macro  TIHOLD nm
	.if     BCYC > 16
	.error  "engine16_merged: Design B IN cell over 16 cycles"
	.endif
	.rept   16 - BCYC
	nop
	.endr
	.set    BCYC, 0
	.global usb_ti_\nm
usb_ti_\nm:
	nop				/* 1  data 1 = no transition         */
	str     r5, [r7, #USB_BSRR_OFS]	/* 1                                 */
	.set    BCYC, 2
	.endm

	.macro  TIBIT nm
	.if     BCYC > 16
	.error  "engine16_merged: Design B IN cell over 16 cycles"
	.endif
	.rept   16 - BCYC
	nop
	.endr
	.set    BCYC, 0
	.global usb_ti_\nm
usb_ti_\nm:
	lsrs    r4, r4, #1		/* 1  C = the data bit               */
	sbcs    r2, r2			/* 1  0 on a 1, -1 on a 0            */
	ands    r2, r6			/* 1                                 */
	eors    r5, r2			/* 1  NRZI: toggle on 0              */
	str     r5, [r7, #USB_BSRR_OFS]	/* 1                                 */
	.set    BCYC, 5
	.endm

/* ---- the gate, in the pieces the cells can hold ------------------------ */
	.macro  TIG1			/* 4:  the stuffing violation        */
	mov     r2, r11			/* 1                                 */
	lsrs    r2, r2, #6		/* 1                                 */
	cmp     r2, #((T_UT >> 6) + 7)	/* 1                                 */
	beq     .Lti_abt		/* 1                                 */
	.endm
#define TIG1_CYC        4

	.macro  TIG2			/* 3:  a token is exactly 4 bytes    */
	mov     r0, r12			/* 1  emitted byte count             */
	cmp     r0, #4			/* 1  SYNC + PID + 2, and no other   */
	bne     .Lti_abt		/* 1  length is a token              */
	.endm
#define TIG2_CYC        3

	.macro  TIG3			/* 1 + a RAM access                  */
	mov     r1, r9			/* 1  rxbuf base                     */
	ldrh    r3, [r1, #0]		/* 2/4  SYNC and PID in one access   */
	.endm
#define TIG3_CYC        (1 + USB_RAM_ACCESS)

	.macro  TIG4			/* 6:  SYNC byte, then the PID       */
	uxtb    r2, r3			/* 1                                 */
	cmp     r2, #0x80		/* 1  SYNC decodes to 0x80           */
	bne     .Lti_abt		/* 1                                 */
	lsrs    r3, r3, #8		/* 1  the PID byte                   */
	cmp     r3, #TI_PID_IN		/* 1  and it must be IN.  The stub   */
	bne     .Lti_abt		/* 1  read the same byte; this reads */
	.endm				/*    it again rather than trust it  */
#define TIG4_CYC        6

	.macro  TIG5			/* a RAM access: the token halfword  */
	ldrh    r2, [r1, #2]		/* 2/4  rxbuf+2 is 2-aligned (F-5)   */
	.endm
#define TIG5_CYC        USB_RAM_ACCESS

/* endp = (b2>>7) | ((b3 & 7) << 1), and with the two bytes already in one
 * register that is bits 10..7 of the halfword: one shift and a mask. */
	.macro  TIG6			/* 5:  the endpoint, bounded         */
	lsrs    r0, r2, #7		/* 1                                 */
	lsls    r0, r0, #28		/* 1  \ & 0x0F                       */
	lsrs    r0, r0, #28		/* 1  /                              */
	cmp     r0, #ENDPOINTS		/* 1  eps[] is the last member of    */
	bhs     .Lti_abt		/* 1  the struct (F-3)               */
	.endm
#define TIG6_CYC        5

	.macro  TIG7			/* 2 + a literal: the arm record     */
	lsls    r0, r0, #TI_REC_SHIFT	/* 1                                 */
	ldr     r1, =usb_in_arm		/* 2/4                               */
	adds    r1, r1, r0		/* 1                                 */
	.endm
#define TIG7_CYC        (2 + LIT_CYC)

	.macro  TIG8			/* a RAM access: the armed pattern   */
	ldrh    r3, [r1, #TI_PAT_OFS]	/* 2/4                               */
	.endm
#define TIG8_CYC        USB_RAM_ACCESS

/* THE COMPARISON.  One CMP settles the device address, the endpoint, the
 * CRC5 over both of them, and whether a packet has been rendered for this
 * endpoint at all - because the pattern is written only when one is
 * (turnaround.md S4.1).  Everything below this line is downstream of it, so
 * a DATA0/DATA1 PID for a token this device was not asked for, or was not
 * ready for, is structurally impossible. */
	.macro  TIG9			/* 2                                 */
	cmp     r3, r2			/* 1                                 */
	bne     .Lti_abt		/* 1                                 */
	.endm
#define TIG9_CYC        2

	.macro  TIPIDLOAD		/* a RAM access: DATA0 or DATA1      */
	ldrb    r4, [r1, #TI_PID_OFS]	/* 2/4  from e->toggle_in, decided   */
	.endm				/*    at render time, not here       */
#define TIPIDLOAD_CYC   USB_RAM_ACCESS

/* ---- flush piece 0: no CRC16 anywhere in it ---------------------------- */
.Lti_ovf0:				/* overflow before anything is driven */
	ldr     r2, =(.Lusb_done + 1)	/* so this is silence, and the tail   */
	bx      r2			/* is out of a B's reach from here    */

	.global ti_flush7
ti_flush7:
	.rept   10			/* R4, as tb_flush7: without it the   */
	nop				/* first edge is inside the host's EOP*/
	.endr
	b       .Lti_head

	.global ti_flush0
ti_flush0:
	SEG0
	SEG1
	SEG2
	SEG3
	SEG3_TAIL
	SEG5    .Lti_ovf0
	b       .Lti_arm_k0
	.global ti_flush1
ti_flush1:	SEG1
	.global ti_flush2
ti_flush2:	SEG2
	.global ti_flush3
ti_flush3:	SEG3
	.global ti_flush4
ti_flush4:	SEG3_TAIL		/* SEG4 without its CRC16 step       */
	.global ti_flush5
ti_flush5:	SEG5    .Lti_ovf0
	.global ti_flush6		/* SEG6 is gone entirely             */
ti_flush6:

	.global usb_ti_head
usb_ti_head:
.Lti_head:
	.rept   4
	nop
	.endr
	mov     r2, r14			/* 1  8-k, the unsampled slots       */
	lsrs    r1, r5, #1		/* 1  \                              */
	eors    r1, r5			/* 1   |                             */
	mvns    r1, r1			/* 1   | NRZI over the whole byte    */
	uxtb    r1, r1			/* 1   |                             */
	lsls    r1, r1, r2		/* 1   | k real bits to the top      */
	uxtb    r1, r1			/* 1  /                              */
	lsls    r2, r1, #28		/* 1  \                              */
	lsrs    r2, r2, #26		/* 1   | low nibble * 4              */
	mov     r8, r2			/* 1  /  parked for SEG1             */
	lsrs    r1, r1, #4		/* 1  high nibble; r5 is now DEAD    */
	SEG0				/* 7                                 */
	TBARM				/* 13                                */

/* ---- eight SYNC cells: 2 of wire, 14 of receiver ----------------------- */
	.set    BCYC, 16
	TICELL  C0
	SEG1
	TBUSE   SEG1_CYC
	SEG2_A
	TBUSE   SEG2A_CYC

	TICELL  C1
	SEG2_B
	TBUSE   SEG2B_CYC
	SEG3_A
	TBUSE   SEG3A_CYC

	TICELL  C2
	SEG3_B
	TBUSE   SEG3B_CYC
	SEG3_TAIL
	TBUSE   3
	SEG5_A
	TBUSE   SEG5A_CYC

	TICELL  C3
	SEG5_B  .Lti_ovf
	TBUSE   SEG5B_CYC

	TICELL  C4			/* <- K=0 joins here: the flush is    */
	TIG1				/*    over and the gate begins        */
	TBUSE   TIG1_CYC
	TIG2
	TBUSE   TIG2_CYC
	TIG3
	TBUSE   TIG3_CYC

	TICELL  C5
	TIG4
	TBUSE   TIG4_CYC
	TIG5
	TBUSE   TIG5_CYC

	TICELL  C6
	TIG6
	TBUSE   TIG6_CYC
	TIG7
	TBUSE   TIG7_CYC
	TIG8
	TBUSE   TIG8_CYC

	TIHOLD  C7			/* SYNC bit 7: data 1, no transition */
	TIG9				/* THE COMPARISON                    */
	TBUSE   TIG9_CYC
	TIPIDLOAD			/* and only now the PID              */
	TBUSE   TIPIDLOAD_CYC
	TBPADTO (16 - LIT_CYC - 3)
	ldr     r2, =(.Lti_pid + 1)	/* 2/4                               */
	bx      r2			/* 3                                 */
	TBUSE   (LIT_CYC + 3)
	.if     BCYC != 16
	.error  "engine16_merged: Design B IN cell C7 is not 16 cycles"
	.endif

/* The gate's rejects and SEG5's byte bound both land here.  It is a
 * trampoline because a Thumb conditional branch reaches +-254 bytes and the
 * abort is a chain of cells away; nothing falls into it - cell C7 ends in a
 * BX - and it costs 2-3 cycles on a path that is about to spend 160 doing
 * nothing. */
.Lti_ovf:
.Lti_abt:
	b       .Lti_abort

/* ---- K = 0: no partial byte, so no flush at all - four cells of its own,
 * then the shared chain from the cell where the gate begins. ------------- */
.Lti_arm_k0:
	TBARM
	.set    BCYC, 16
	TICELL  E0
	TBPADTO 16
	TICELL  E1
	TBPADTO 16
	TICELL  E2
	TBPADTO 16
	TICELL  E3
	TBPADTO (16 - LIT_CYC - 3)
	ldr     r2, =(usb_ti_C4 + 1)	/* 2/4                               */
	bx      r2			/* 3                                 */
	TBUSE   (LIT_CYC + 3)
	.if     BCYC != 16
	.error  "engine16_merged: Design B IN cell E3 is not 16 cycles"
	.endif
	.ltorg
#endif	/* USB_TURNAROUND_B && USB_TURNAROUND_B_IN */

#if USB_TURNAROUND_B && USB_TURNAROUND_B_IN
/* ---- the PID: eight cells, and the payload chain set up in their slack --
 * The PID byte reached r4 in cell C7, downstream of the one comparison that
 * validates the token (TIG9).  These eight cells put it on the wire and, in
 * the eleven cycles each has left, load everything the payload chain needs:
 *
 *   r0  whole 8-bit groups of rendered wire bits still to send
 *   r1  the byte pointer into the record's rendered stream
 *   r4  the bit queue (the PID until cell Q7, then group 0)
 *   r8  the group prefetched one group ahead
 *   r10 (loop target) XOR (tail target) - the branch-free dispatch below
 *   r14 the tail target, a code pointer stored in the record at render time
 *
 * r9, r11 and r12 are NOT touched: .Lrx_tail runs afterwards and needs the
 * rxbuf base, the stuffing state and the emitted count exactly as they are.
 */
	.set    BCYC, 16
.Lti_pid:
	TIBIT   Q0
	ldrb    r0, [r1, #TI_GROUPS_OFS]	/* 2/4                       */
	ldr     r2, [r1, #TI_TAIL_OFS]		/* 2/4  the tail target      */
	mov     r14, r2				/* 1                         */
	TBUSE   (1 + 2 * USB_RAM_ACCESS)

	TIBIT   Q1
	ldr     r1, [r1, #TI_SRC_OFS]		/* 2/4  where the stream is  */
	ldrb    r2, [r1, #0]			/* 2/4  group 0.  Priced at  */
	adds    r1, r1, #1			/* 1    the RAM cost: a       */
	mov     r8, r2				/* 1    dynamic record streams*/
	TBUSE   (2 + 2 * USB_RAM_ACCESS)	/*      from its own bits[]  */

	TIBIT   Q2
	ldr     r2, =(usb_ti_D0 + 1)		/* 2/4  the loop target      */
	mov     r3, r14				/* 1                         */
	eors    r2, r3				/* 1  loop ^ tail            */
	mov     r10, r2				/* 1  the dispatch constant  */
	TBUSE   (3 + LIT_CYC)

	TIBIT   Q3
	TBPADTO 16
	TIBIT   Q4
	TBPADTO 16
	TIBIT   Q5
	TBPADTO 16
	TIBIT   Q6
	TBPADTO 16

	TIBIT   Q7
	mov     r4, r8				/* 1  group 0 is the queue   */
	TBUSE   1
	TBPADTO (16 - LIT_CYC - 3)
	ldr     r2, =(usb_ti_D0 + 1)		/* 2/4                       */
	bx      r2				/* 3                         */
	TBUSE   (LIT_CYC + 3)
	.if     BCYC != 16
	.error  "engine16_merged: Design B IN cell Q7 is not 16 cycles"
	.endif

/* ---- the payload: eight cells a group, five cycles of transmitter -------
 * There is no pipeline here because there is nothing left to compute: bit
 * stuffing and the CRC16 are already in the stream, so a cell is the NRZI
 * toggle and the store, and the remaining eleven cycles carry a one-group
 * prefetch and, in the last cell, the dispatch.
 *
 * The dispatch is branch-free and touches no memory.  m = 0 when this was
 * the last full group and -1 otherwise, and
 *     target = tail ^ ((loop ^ tail) & m)
 * is the loop target when m = -1 and the tail target when m = 0.  A BX is a
 * flat 3 cycles (ENGINE16_SPEC.md S2), so the cell is exactly 16 whichever
 * way it goes - which is the property a taken conditional branch here would
 * destroy. */
	.set    BCYC, 16
	TIBIT   D0
	ldrb    r2, [r1, #0]			/* 2/4  one group ahead      */
	adds    r1, r1, #1			/* 1                         */
	mov     r8, r2				/* 1                         */
	TBUSE   (2 + USB_RAM_ACCESS)
	TBPADTO 16

	TIBIT   D1
	TBPADTO 16
	TIBIT   D2
	TBPADTO 16
	TIBIT   D3
	TBPADTO 16
	TIBIT   D4
	TBPADTO 16
	TIBIT   D5
	TBPADTO 16
	TIBIT   D6
	TBPADTO 16

	TIBIT   D7
	mov     r4, r8				/* 1  the prefetched group   */
	subs    r0, r0, #1			/* 1  groups remaining       */
	rsbs    r2, r0, #0			/* 1  \                      */
	asrs    r2, r2, #31			/* 1  / m: 0 last, -1 more   */
	mov     r3, r10				/* 1  loop ^ tail            */
	ands    r2, r3				/* 1                         */
	mov     r3, r14				/* 1  tail                   */
	eors    r2, r3				/* 1                         */
	bx      r2				/* 3                         */
	TBUSE   11
	.if     BCYC != 16
	.error  "engine16_merged: Design B IN cell D7 is not 16 cycles"
	.endif

/* The final, partial group: t = (wire bits) & 7 cells, entered at T(8-t) so
 * the queue's low bits are consumed oldest first exactly as in a full group.
 * The record carries T(8-t) as its tail target, or .Lti_eop when t = 0. */
	.set    BCYC, 16
	TIBIT   T1
	TBPADTO 16
	TIBIT   T2
	TBPADTO 16
	TIBIT   T3
	TBPADTO 16
	TIBIT   T4
	TBPADTO 16
	TIBIT   T5
	TBPADTO 16
	TIBIT   T6
	TBPADTO 16
	TIBIT   T7
	TBPADTO (16 - LIT_CYC - 3)
	ldr     r2, =(.Lti_eop + 1)		/* 2/4                       */
	bx      r2				/* 3                         */
	TBUSE   (LIT_CYC + 3)
	.if     BCYC != 16
	.error  "engine16_merged: Design B IN cell T7 is not 16 cycles"
	.endif

/* ---- abort: turnaround.md S6.4, and it ends in the C layer -------------
 * The IN abort differs from the DATA one in where it goes afterwards.  A
 * DATA whose CRC failed is dropped and the host retries the DATA.  An IN
 * token that failed the gate did so because this endpoint has nothing
 * rendered for it - the first IN after a state change, or a token for
 * another device - and dropping it would leave nothing rendered for the
 * retry either.  So the abort falls into the ordinary tail, which calls
 * usb_pid_handle_in, whose usb_send_data is swallowed by the suppression
 * flag and renders the arm record instead.  One transaction is lost to the
 * host's timeout; the retry is answered inside 6.5 bit times. */
.Lti_abort:
	movs    r1, #40			/* 120..160 cycles = 7.5..10 bit     */
1:	subs    r1, #1			/* times of an unchanging level: a   */
	bne     1b			/* stuff violation, and a PID of 0xFF*/

/* ---- EOP: SE0 for two bit times, driven J for one, then release -------- */
	.global usb_ti_eop
usb_ti_eop:
.Lti_eop:
	nop				/* 1                                 */
	nop				/* 1                                 */
	lsrs    r2, r6, #16		/* 1  \ the toggle word's reset half */
	lsls    r2, r2, #16		/* 1  / = both pins low = SE0        */
	str     r2, [r7, #USB_BSRR_OFS]	/* 1  =5, on the same cadence        */
	.rept   11
	nop
	.endr				/* =16                               */
	ldr     r2, =BSRR_J		/* 2                                 */
	.rept   14
	nop
	.endr				/* =16                               */
	nop
	nop
	nop
	nop
	str     r2, [r7, #USB_BSRR_OFS]	/* 1  =5  driven J for one bit time  */
	.rept   11
	nop
	.endr				/* =16                               */

	/* Untimed.  Release the pins; the pull-up holds J. */
	ldr     r1, =MODER_MASK
	ldr     r2, [r7, #USB_MODER_OFS]
	bics    r2, r1
	str     r2, [r7, #USB_MODER_OFS]
	ldr     r2, =(.Ltb_sent + 1)	/* the shared epilogue: set the       */
	bx      r2			/* suppression flag, then .Lrx_tail   */
	.ltorg
#endif	/* USB_TURNAROUND_B && USB_TURNAROUND_B_IN */

	.size   usb_rx_engine16, . - usb_rx_engine16
	.ltorg


/* ===================== rendering an IN response ==========================
 * Called by usb_send_data (engine16_tx.S) with its four arguments intact,
 * from the untimed tail of the transaction BEFORE the one this packet will
 * answer.  It turns (payload, length, PID) into the wire-bit stream the
 * payload cells shift out: CRC16 appended, bit stuffing applied, LSB first.
 * NRZI is NOT applied - that is the cell's own `sbcs/ands/eors`.
 *
 * This is not new work.  It is exactly the pipeline engine16_tx.S runs in
 * the eleven spare cycles of each of its cells; doing it here costs the
 * same arithmetic and takes it off the wire, which is what lets the payload
 * cell hold 16 cycles when the transmit engine's own measures 18
 * (turnaround.md S11.6).  The ISR gets SHORTER, not longer: a transmission
 * it no longer performs was ~100 cells of 16 cycles.
 *
 * Arming is one-shot and explicit.  usb_in_pend is written only by the two
 * places that know an IN response is what is being produced - the token
 * dispatch and the refresh hook - and is consumed and cleared here, so no
 * other caller of usb_send_data can arm anything by accident.
 *
 *   usb_in_pend +0  u16 pat    the validated token halfword to install, or
 *                              zero meaning "keep the one already there"
 *               +2  u8  valid  one-shot
 *               +3  u8  endp
 * ======================================================================== */
#if USB_TURNAROUND_B && USB_TURNAROUND_B_IN && USB_PRERENDER
/* Generated by tools/prerender_gen.py into the translation unit that
 * instantiates the descriptors, so the linker resolves the keys against the
 * descriptor symbols themselves and no address is baked in anywhere.  WEAK:
 * absent, they link at zero and usb_in_render renders every packet. */
	.weak   usb_pr_keys
	.weak   usb_pr_recs
	.weak   usb_pr_count
#endif
	.global usb_in_render
	.thumb_func
	.type   usb_in_render, %function
usb_in_render:
#if USB_TURNAROUND_B && USB_TURNAROUND_B_IN
	push    {r0-r7, lr}		/* sp+0 r0, +4 r1, +8 r2, +12 r3     */
	ldr     r6, =usb_in_pend
	ldrb    r5, [r6, #2]
	cmp     r5, #0
	bne     .Lir_go
	/* Every bail-out returns HERE rather than at the far end: a Thumb
	 * conditional branch reaches +-254 bytes and the stuffing loop below
	 * is longer than that.  One taken branch on the good path, in an
	 * untimed routine. */
.Lir_bail:
	pop     {r0-r7, pc}
.Lir_go:
	movs    r5, #0
	strb    r5, [r6, #2]		/* one-shot, cleared as it fires     */

	cmp     r2, #0			/* poly_function != 0 means no CRC,  */
	bne     .Lir_bail		/* i.e. a handshake, not a DATA      */
	cmp     r1, #8			/* the LS maximum, and the bound the */
	bhi     .Lir_bail		/* record's 24 spare bytes rest on   */
	cmp     r3, #0xC3		/* DATA0                             */
	beq     .Lir_pid_ok
	cmp     r3, #0x4B		/* DATA1, and nothing else           */
	bne     .Lir_bail
.Lir_pid_ok:
	ldrb    r5, [r6, #3]		/* endpoint                          */
	cmp     r5, #ENDPOINTS
	bhs     .Lir_bail
	lsls    r5, r5, #TI_REC_SHIFT
	ldr     r4, =usb_in_arm
	adds    r4, r4, r5		/* r4 = the record                   */

	/* The pattern to install.  The record was disarmed by whoever set
	 * usb_in_pend, so every path that returns without reaching the last
	 * store below leaves the endpoint UNARMED rather than armed with a
	 * packet that no longer matches the state - which is what a bail-out
	 * here means.  A user IN handler that transmits nothing at all takes
	 * exactly that path. */
	ldrh    r5, [r6, #0]
	push    {r4, r5}		/* sp+0 record, +4 pattern; every     */
					/* saved argument moves up by 8       */

#if USB_PRERENDER
/* ---- the packet may already have been rendered, at build time ----------
 * Everything below this block - the CRC16 pass, T_TX, the byte loop, the
 * tail - is a pure function of (pid, payload bytes), and for a descriptor
 * those bytes are a const array in flash that was known to the compiler.
 * tools/prerender_gen.py renders them on the host into usb_pr_bits[] and
 * this table; doc/py32/prerender.md has the ledger.  A hit is four loads
 * and five stores instead of ~956 cycles (design_b_in.md S10).
 *
 * The table is keyed by the SOURCE POINTER, because that and the length are
 * all usb_send_data's seam carries - rv003usb.c is not edited for this.
 * usb_pr_keys is sorted, so this is a binary search: five probes over the
 * gamepad demo's 25 packets, and five on a miss too, which is what a
 * dynamic HID report on another endpoint pays for the table existing.
 *
 * The three symbols are WEAK.  A build with no generated table links with
 * them at address zero and the first compare sends every packet down the
 * dynamic path, which is exactly the behaviour before this block existed. */
	ldr     r6, =usb_pr_count
	cmp     r6, #0
	beq     .Lir_dyn
	ldrb    r6, [r6, #0]		/* hi                                */
	movs    r7, #0			/* lo                                */
	ldr     r2, =usb_pr_keys
.Lir_bs:
	cmp     r7, r6
	bhs     .Lir_dyn		/* lo == hi: not a pre-rendered one  */
	adds    r3, r7, r6
	lsrs    r3, r3, #1		/* mid                               */
	lsls    r4, r3, #2
	ldr     r4, [r2, r4]		/* usb_pr_keys[mid]                  */
	cmp     r4, r0
	beq     .Lir_hit
	bhi     1f
	adds    r7, r3, #1		/* below the target: lo = mid + 1    */
	b       .Lir_bs
1:	mov     r6, r3			/* above it: hi = mid                */
	b       .Lir_bs

.Lir_hit:
	ldr     r2, =usb_pr_recs
	lsls    r3, r3, #3		/* 8 bytes an entry                  */
	adds    r2, r2, r3
	ldrb    r3, [r2, #4]		/* the length it was rendered for.   */
	cmp     r3, r1			/* A host that asks for a truncated  */
	bne     .Lir_dyn		/* read gets a PREFIX of these bytes */
					/* and a different CRC16, so it is   */
					/* a different packet: fall through  */
	ldrb    r3, [r2, #5]		/* and the PID it was rendered for   */
	ldr     r4, [sp, #20]		/* against the PID argument, which   */
	cmp     r3, r4			/* the toggle decides at runtime     */
	bne     .Lir_dyn

	ldr     r4, [sp, #0]		/* the arm record                    */
	ldr     r3, [r2, #0]		/* the stream, in flash              */
	str     r3, [r4, #TI_SRC_OFS]
	ldrb    r3, [r2, #6]		/* groups                            */
	strb    r3, [r4, #TI_GROUPS_OFS]
	ldrb    r3, [r2, #7]		/* t = the rendered bits & 7         */
	lsls    r3, r3, #2
	ldr     r7, =usb_ti_tails
	ldr     r3, [r7, r3]
	str     r3, [r4, #TI_TAIL_OFS]
	ldrb    r3, [r2, #5]
	strb    r3, [r4, #TI_PID_OFS]
	ldr     r5, [sp, #4]		/* the pattern                       */
	strh    r5, [r4, #TI_PAT_OFS]	/* ARMED - the last store, as ever   */
	add     sp, #8
	pop     {r0-r7, pc}
	.ltorg				/* unreachable: the literals for the */
					/* block above, in range of it       */
.Lir_dyn:
#endif	/* USB_PRERENDER */

/* ---- payload and CRC16 into the staging buffer ------------------------
 * One pass, not two: the copy and the CRC16 step share the byte that has
 * just been loaded.  usb_txbuf is free here - the transmit path refills it
 * after this returns and the suppressed path never touches it. */
	ldr     r6, =usb_txbuf
	ldr     r7, =usb_tables		/* T_CRC16 is at offset 0            */
	movs    r2, #0
	mvns    r2, r2
	lsrs    r2, r2, #16		/* CRC16 init 0xFFFF                 */
	movs    r5, #0
.Lir_crc:
	cmp     r5, r1
	bhs     .Lir_crce
	ldrb    r3, [r0, r5]
	strb    r3, [r6, r5]
	eors    r3, r2
	uxtb    r3, r3
	lsls    r3, r3, #1
	ldrh    r3, [r7, r3]
	lsrs    r2, r2, #8
	eors    r2, r3			/* crc = (crc>>8) ^ T[(crc^b)&0xFF]  */
	adds    r5, r5, #1
	b       .Lir_crc
.Lir_crce:
	mvns    r2, r2			/* the wire form is the complement,  */
	strb    r2, [r6, r1]		/* low byte first - TXSEG2/TXSEG3    */
	lsrs    r3, r2, #8
	adds    r5, r1, #1
	strb    r3, [r6, r5]
	adds    r1, r1, #2		/* source bytes = payload + CRC16    */

/* ---- bit stuffing, LSB first, into the record -------------------------
 * A nibble at a time, through engine16_tx.S's own T_TX - the table that
 * engine's TXSEG4/TXSEG5 read once per nibble in the eleven spare cycles of
 * a bit cell.  Indexed (state+1)*32 + nibble*2, it returns
 *     entry = m<<9 | (n-4)<<8 | (state'+1)<<5,   m = w + 2^n - 1
 * where w is the nibble's four data bits with any stuffed 0 inserted,
 * oldest at bit 0, and n is 4 or 5.  The bias is what lets ONE add append a
 * variable number of bits and move the accumulator's sentinel by exactly n:
 * with acc = data + 2^f,  acc + (m<<f) = data + (w<<f) + 2^(f+n).
 *
 * A bit-at-a-time loop was written first and measured; this is about five
 * times cheaper, and the difference lands directly on the ISR that follows
 * the host's ACK (design_b_in.md S10). */
	.macro  TISTUFF
	lsls    r7, r7, #1
	orrs    r7, r5			/* (state+1)*32 + nibble*2           */
	ldrh    r1, [r6, r7]
	uxtb    r5, r1			/* (state'+1)<<5                     */
	lsrs    r2, r1, #9		/* m                                 */
	lsls    r2, r2, r4
	adds    r3, r3, r2		/* append n bits AND move the sentinel*/
	lsls    r1, r1, #23		/* \                                 */
	lsrs    r1, r1, #31		/*  | n = 4 + bit 8                  */
	adds    r1, r1, #4		/* /                                 */
	adds    r4, r4, r1
	mov     r2, r10
	adds    r2, r2, r1
	mov     r10, r2			/* nbits += n                        */
	cmp     r4, #8			/* f <= 7 on entry and n <= 5, so at */
	blo     1f			/* most one byte can come out here   */
	mov     r2, r9
	strb    r3, [r2, #0]
	adds    r2, r2, #1
	mov     r9, r2
	lsrs    r3, r3, #8		/* the sentinel comes with it        */
	subs    r4, r4, #8
1:
	.endm

	mov     r8, r6			/* source                            */
	mov     r11, r1			/* source bytes                      */
	ldr     r6, [sp, #0]		/* the record                        */
	adds    r6, r6, #TI_BITS_OFS
	mov     r9, r6			/* output byte pointer               */
	movs    r6, #0
	mov     r10, r6			/* nbits                             */
	/* The run of consecutive 1s SYNC and the PID leave behind.  SYNC is
	 * 00000001 and contributes one; DATA0 = 0xC3 is 1,1,0,0,0,0,1,1 LSB
	 * first and ends on two; DATA1 = 0x4B is 1,1,0,1,0,0,1,0 and ends on
	 * none.  Neither field can stuff on its own - a legal PID's longest
	 * run is four and SYNC adds one, against a rule that inserts after
	 * six - so the payload is the first place a 0 can be needed. */
	ldr     r3, [sp, #20]		/* the PID argument, as pushed       */
	movs    r5, #(1 << 5)		/* state 0, biased: DATA1            */
	cmp     r3, #0xC3
	bne     .Lir_run
	movs    r5, #(3 << 5)		/* state 2, biased: DATA0            */
.Lir_run:
	ldr     r6, =usb_tx_tables	/* T_TX's base                       */
	movs    r3, #1			/* accumulator: the sentinel at bit 0*/
	movs    r4, #0			/* f, the bits under it              */
	movs    r0, #0			/* source byte index                 */
.Lir_byte:
	mov     r2, r11
	cmp     r0, r2
	bhs     .Lir_tail
	mov     r2, r8
	ldrb    r2, [r2, r0]
	mov     r12, r2
	movs    r7, #0x0F
	ands    r7, r2			/* low nibble = first in time        */
	TISTUFF
	mov     r7, r12
	lsrs    r7, r7, #4		/* high nibble                       */
	TISTUFF
	adds    r0, r0, #1
	b       .Lir_byte

.Lir_tail:
	/* USB 2.0 S7.1.9: "a zero bit will be inserted even if it is the last
	 * bit before the end-of-packet signal".  T_TX DEFERS a stuffed zero
	 * to the head of the next nibble - that is what state 6 means, and it
	 * is why the table has a seventh row - so at the end of the stream
	 * there is no next nibble to carry it and it has to be emitted here.
	 * Measured on 20000 random packets, 0.69% of them end this way: about
	 * one packet in 150, not an edge nobody meets.  design_b_in.md S11
	 * records what that means for engine16_tx.S, which has no equivalent. */
	cmp     r5, #(7 << 5)		/* state 6: six consecutive 1s       */
	bne     .Lir_flush
	movs    r2, #1
	lsls    r2, r2, r4
	adds    r3, r3, r2		/* a 0 bit: only the sentinel moves  */
	adds    r4, r4, #1
	mov     r2, r10
	adds    r2, r2, #1
	mov     r10, r2
	cmp     r4, #8
	blo     .Lir_flush
	mov     r2, r9
	strb    r3, [r2, #0]
	adds    r2, r2, #1
	mov     r9, r2
	lsrs    r3, r3, #8
	subs    r4, r4, #8

.Lir_flush:
	cmp     r4, #0			/* the final, partial group.  Its    */
	beq     .Lir_fin		/* high bits carry the sentinel and  */
	mov     r7, r9			/* whatever is above it, and are     */
	strb    r3, [r7, #0]		/* never shifted out: the tail chain */
					/* is entered at T(8-t), t cells.    */
.Lir_fin:
	pop     {r4, r5}		/* record, pattern                   */
	movs    r3, #TI_BITS_OFS	/* a dynamic payload streams from    */
	adds    r3, r3, r4		/* the record's own bits[]; a static */
	str     r3, [r4, #TI_SRC_OFS]	/* one from flash (see .Lir_hit)     */
	mov     r7, r10			/* nbits                             */
	lsrs    r3, r7, #3
	strb    r3, [r4, #TI_GROUPS_OFS]
	movs    r3, #7
	ands    r7, r3			/* t = nbits & 7                     */
	lsls    r7, r7, #2
	ldr     r3, =usb_ti_tails
	ldr     r3, [r3, r7]
	str     r3, [r4, #TI_TAIL_OFS]
	ldr     r3, [sp, #12]		/* the PID argument                  */
	strb    r3, [r4, #TI_PID_OFS]
	strh    r5, [r4, #TI_PAT_OFS]	/* ARMED - and this is the LAST store*/
.Lir_out:
	pop     {r0-r7, pc}

/* Where the payload chain goes when the whole groups are done: T(8-t) for a
 * final partial group of t bits, and straight to the EOP when t = 0. */
	.balign 4
usb_ti_tails:
	.word   usb_ti_eop + 1
	.word   usb_ti_T7 + 1
	.word   usb_ti_T6 + 1
	.word   usb_ti_T5 + 1
	.word   usb_ti_T4 + 1
	.word   usb_ti_T3 + 1
	.word   usb_ti_T2 + 1
	.word   usb_ti_T1 + 1
	.ltorg
#else
	bx      lr			/* B_IN off: nothing to render, and  */
					/* engine16_tx.S still calls this    */
#endif
	.size   usb_in_render, . - usb_in_render

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

/* Set by the Design B path when it has already put the handshake on the
 * wire; usb_send_data (engine16_tx.S) consumes it and returns without
 * transmitting.  This is what lets the C layer keep calling
 * usb_send_data(0,0,2,0xD2) from just_ack with its source unchanged
 * (turnaround.md S4.2).  One byte, written only outside every timed path. */
#if USB_TURNAROUND_B && USB_TURNAROUND_B_IN
/* The one-shot handshake between "an IN response is being produced" and
 * usb_send_data, which is the only place that sees the payload.  Written by
 * .Lt_in and by the refresh hook, consumed and cleared by usb_in_render. */
	.section .bss.usb_in_pend, "aw", %nobits
	.balign 4
	.global usb_in_pend
usb_in_pend:
	.space  4

/* One pre-rendered IN response per endpoint.  Written only by
 * usb_send_data (engine16_tx.S), i.e. from the untimed tail of a previous
 * transaction; read only by the gate and the payload chain above.  A record
 * whose pattern is zero is not armed, and zero is not a legal token
 * halfword (design_b_in.md S6), so .bss zero-init disarms everything at
 * reset without a line of startup code.
 * TI_REC_SIZE is a power of two because the endpoint indexes it with a
 * shift - there is no room in cell C6 for a multiply. */
	.section .bss.usb_in_arm, "aw", %nobits
	.balign 4
	.global usb_in_arm
usb_in_arm:
	.space  TI_REC_SIZE * ENDPOINTS
	/* The longest stream this can be asked to hold: 8 payload + 2 CRC16
	 * bytes = 80 data bits, plus at most one stuffed zero per six 1s = 13,
	 * = 93 wire bits = 12 bytes, plus one because the payload chain
	 * prefetches a group ahead and the last prefetch reads past the end.
	 * tools/design_b_in_model.py measures the real worst case at 92 bits
	 * over 7322 packets; this is the bound, not the measurement. */
	.if     (TI_BITS_OFS + TI_BITS_BYTES) > TI_SRC_OFS
	.error  "engine16_merged: the IN arm record cannot hold its stream"
	.endif
	.if     (TI_SRC_OFS + 4) > TI_REC_SIZE
	.error  "engine16_merged: the IN arm record cannot hold its src word"
	.endif
#endif

	.section .bss.usb_tx_suppress, "aw", %nobits
	.balign 4
	.global usb_tx_suppress
usb_tx_suppress:
	.space  1
