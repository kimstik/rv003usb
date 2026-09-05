@ engine16_cleansheet.S -- ENGINE16 competition, clean-sheet entry (no lineage)
@
@ See engine16_cleansheet.md for the design note, the per-path cycle ledger
@ (spec sec 5), and an honest list of what this design gives up.
@
@ Assemble check (this is the whole contract for this file per spec sec 5.1):
@   arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c \
@       engine16_cleansheet.S -o /tmp/e.o
@   arm-none-eabi-objdump -d /tmp/e.o
@
@ Pin/port constants match the assignment already chosen for this port
@ (doc/py32/PLAN.md:1605, "USB_PORT B, DP 0, DM 3, DPU 2"). This file does not
@ include rv003usb.h / usb_config.h (neither exists yet for PY32 in this tree,
@ per PLAN.md's own state) -- it defines the handful of constants it needs
@ locally, the same way the existing rv003usb-arm.S does for its own EXTI/GPIO
@ offsets. Nothing here is a shared file.

	.syntax unified
	.cpu cortex-m0plus
	.thumb

@ ---------------------------------------------------------------------------
@ Hardware constants
@ ---------------------------------------------------------------------------
	.equ GPIOB_BASE,     0x50000400
	.equ EXTI_BASE,      0x40021800
	.equ IDR_OFFSET,     0x10
	.equ EXTI_PR_OFFSET, 0x0C

	.equ USB_PIN_DP,     0
	.equ USB_PIN_DM,     3
	.equ USB_DMASK,      ((1 << USB_PIN_DP) | (1 << USB_PIN_DM))
	.equ DM_SHIFT,       (USB_PIN_DM + 1)   @ lsrs by this puts the D- level in C

@ ---------------------------------------------------------------------------
@ Capture geometry
@
@ Worst case raw bit count for one packet on this wire: PID(8) + payload
@ (USB_BUFFER_SIZE=12 bytes = 96 bits, rv003usb.h:120) + CRC16(16) = 120 coded
@ bits, plus bit-stuff insertions at one stuffed bit per six consecutive ones,
@ worst case ceil(120/6) = 20 -> 140 raw bits -> 18 bytes. RAW_MAXBYTES is a
@ hard structural cap: the capture loop physically cannot write past it (see
@ .md, "buffer bound" -- this closes DEFECTS_VERIFIED.md D-2 by construction,
@ not by a runtime check inside the timed path).
@ ---------------------------------------------------------------------------
	.equ RAW_MAXBYTES,   18
	.equ RXBUF_HDR,      3     @ same alignment trick as rv003usb-arm.S:30-33:
	.equ RXBUF_PAYLOAD,  12    @ PID at HDR, payload at HDR+1 (word-aligned)
	.equ RXBUF_BYTES,    (RXBUF_HDR + 1 + RXBUF_PAYLOAD + 2)

	.pushsection .bss.cleansheet_raw,"aw",%nobits
	.balign 4
raw_capture:
	.space RAW_MAXBYTES
	.popsection

	.pushsection .bss.cleansheet_rx,"aw",%nobits
	.balign 4
rxbuf_cs:
	.space RXBUF_BYTES
	.popsection

@ ===========================================================================
@ Entry: the D- pin-change interrupt handler (spec sec 4).
@
@ Register map for the whole handler, chosen once and held for the entire
@ capture -- see .md "register allocation" for why r8-r12 are never touched.
@   r0  GPIO port base            (resident)
@   r1  USB_DMASK                 (resident)
@   r2  raw-sample accumulator, ADCS-chained, newest bit at bit0
@   r3  per-bit scratch: this cycle's masked-or-unmasked IDR sample
@   r4  raw_capture write pointer
@   r5  per-bit shift scratch (value unused, only its carry-out matters);
@       reused after exit to carry the escape-stub's "valid bits this byte" tag
@   r6  remaining-byte budget (structural RAW_MAXBYTES countdown)
@   r7  the one D- level sampled at sync lock ("bit -1", the NRZI reference);
@       held resident for the WHOLE capture including through the padding
@       push {r7}/pop {r7} sequences, which round-trip it unchanged
@ ===========================================================================
	.global USB_DM_IRQHandler_cleansheet
	.thumb_func
	.type USB_DM_IRQHandler_cleansheet, %function
USB_DM_IRQHandler_cleansheet:
	push {r4, r5, r6, r7, lr}

	ldr  r0, =GPIOB_BASE
	movs r1, #USB_DMASK
	ldr  r3, [r0, #IDR_OFFSET]
	ands r3, r1
	beq  handle_se0_keepalive_cs

@ ---------------------------------------------------------------------------
@ Sync search -- NOT part of the 16-cycle-per-bit ledger. This is a variable-
@ latency search for the reference edge, same role as rv003usb-arm.S's
@ preamble_loop (arm.S:70-77), and it carries the same tolerance argument
@ arm.S:58-62 makes for its DELAY_CYCLES: USB's SYNC field exists precisely so
@ a receiver can lock phase with some slop, so this loop is untimed by design,
@ not merely unverified. See .md "what we gave up" -- the settle constant
@ below has not been bench-tuned.
@ ---------------------------------------------------------------------------
sync_wait_cs:
	ldr  r5, [r0, #IDR_OFFSET]
	ands r5, r1
	cmp  r5, r3
	beq  sync_wait_cs
	cmp  r5, #0
	beq  done_message_cs        @ SE0 during sync search: not a real packet

	lsrs r7, r5, #DM_SHIFT       @ r7 := reference D- level (0 or 1), resident
	ldr  r4, =raw_capture
	movs r6, #RAW_MAXBYTES
	movs r2, #0

	movs r5, #24                 @ coarse phase-alignment settle; see .md --
settle_cs:                       @ not cycle-derived from USB timing, only from
	subs r5, r5, #1               @ this file's own instruction count between
	bne  settle_cs                @ the reference edge and loop_top_cs's first
                                  @ sample (shown in the .md ledger)

@ ===========================================================================
@ The timed capture: exactly 16 cycles per bit, every path, no exceptions.
@
@ One loop body = 8 bit-cells (a byte's worth) + loop control, physically
@ ONE copy in flash, re-entered by the single taken branch at the bottom.
@ See .md sec "why 8 bits, not 1 and not the whole packet" for why this
@ period is the ISA-forced optimum, not a guess.
@ ===========================================================================
	.balign 4
loop_top_cs:

	@ ---- bit 1 --------------------------------------------------------
	ldr  r3, [r0, #IDR_OFFSET]    @ 1
	lsrs r5, r3, #DM_SHIFT        @ 1  (C := this bit's D- level)
	adcs r2, r2                   @ 1  (acc := acc*2 + C)
	tst  r3, r1                   @ 1  (Z := SE0, does not disturb r3)
	beq  esc1_cs                  @ 1  (not taken: running total 5)
	push {r7}                     @ 4
	pop  {r7}                     @ 4
	nop                           @ 1
	nop                           @ 1
	nop                           @ 1  -> 16

	@ ---- bit 2 ---------------------------------------------------------
	ldr  r3, [r0, #IDR_OFFSET]
	lsrs r5, r3, #DM_SHIFT
	adcs r2, r2
	tst  r3, r1
	beq  esc2_cs
	push {r7}
	pop  {r7}
	nop
	nop
	nop

	@ ---- bit 3 ---------------------------------------------------------
	ldr  r3, [r0, #IDR_OFFSET]
	lsrs r5, r3, #DM_SHIFT
	adcs r2, r2
	tst  r3, r1
	beq  esc3_cs
	push {r7}
	pop  {r7}
	nop
	nop
	nop

	@ ---- bit 4 ---------------------------------------------------------
	ldr  r3, [r0, #IDR_OFFSET]
	lsrs r5, r3, #DM_SHIFT
	adcs r2, r2
	tst  r3, r1
	beq  esc4_cs
	push {r7}
	pop  {r7}
	nop
	nop
	nop

	@ ---- bit 5 ---------------------------------------------------------
	ldr  r3, [r0, #IDR_OFFSET]
	lsrs r5, r3, #DM_SHIFT
	adcs r2, r2
	tst  r3, r1
	beq  esc5_cs
	push {r7}
	pop  {r7}
	nop
	nop
	nop

	@ ---- bit 6 ---------------------------------------------------------
	ldr  r3, [r0, #IDR_OFFSET]
	lsrs r5, r3, #DM_SHIFT
	adcs r2, r2
	tst  r3, r1
	beq  esc6_cs
	push {r7}
	pop  {r7}
	nop
	nop
	nop

	@ ---- bit 7 ---------------------------------------------------------
	ldr  r3, [r0, #IDR_OFFSET]
	lsrs r5, r3, #DM_SHIFT
	adcs r2, r2
	tst  r3, r1
	beq  esc7_cs
	push {r7}
	pop  {r7}
	nop
	nop
	nop

	@ ---- bit 8 + loop control ------------------------------------------
	ldr  r3, [r0, #IDR_OFFSET]    @ 1
	lsrs r5, r3, #DM_SHIFT        @ 1
	adcs r2, r2                   @ 1
	tst  r3, r1                   @ 1
	beq  esc8_cs                  @ 1  (not taken: running total 5)
	strb r2, [r4, #0]             @ 4  -> 9   (RAM store, flash-resident code)
	adds r4, r4, #1               @ 1  -> 10
	subs r6, r6, #1               @ 1  -> 11
	beq  escfull_cs                @ 1  (not taken: normal case) -> 12
	nop                            @ 1  -> 13   *** padding BEFORE the branch:
	nop                            @ 1  -> 14   *** it is unconditional, so
	b    loop_top_cs               @ 2-3  -> 16 or 17  anything AFTER it is
                                   @ dead code on the normal path -- see .md
                                   @ "the branch that must come first" and the
                                   @ B=2-vs-3 note in the ledger.

@ ---------------------------------------------------------------------------
@ Dead space: every instruction from here to common_escape_cs is reached ONLY
@ by a taken branch above, NEVER by fall-through -- "b loop_top_cs" just above
@ is unconditional, so nothing after it in program order is ever executed by
@ the normal (SE0-never-happened) path. This is what makes a short (+-256B)
@ beq usable as the SE0 escape from bit-slots that are hundreds of bytes away:
@ see .md "the escape-range problem" for why this had to be solved explicitly
@ and why it is NOT free with full unrolling either.
@
@ r5 := number of valid raw bits captured in THIS byte that never reached
@ raw_capture[] (still live only in r2), i.e. (bit-index-that-saw-SE0) - 1.
@ escfull_cs's byte WAS stored (strb already ran before the check), so it
@ contributes 0 extra pending bits.
@ ---------------------------------------------------------------------------
esc1_cs:     movs r5, #0
             b    common_escape_cs
esc2_cs:     movs r5, #1
             b    common_escape_cs
esc3_cs:     movs r5, #2
             b    common_escape_cs
esc4_cs:     movs r5, #3
             b    common_escape_cs
esc5_cs:     movs r5, #4
             b    common_escape_cs
esc6_cs:     movs r5, #5
             b    common_escape_cs
esc7_cs:     movs r5, #6
             b    common_escape_cs
esc8_cs:     movs r5, #7
             b    common_escape_cs
escfull_cs:  movs r5, #0
common_escape_cs:
	ldr  r6, =rx_tail_entry_cs
	bx   r6

handle_se0_keepalive_cs:
	ldr  r0, =interrupt_complete_cs
	bx   r0

done_message_cs:
	ldr  r0, =interrupt_complete_cs
	bx   r0

@ ===========================================================================
@ Untimed tail from here down: NRZI decode, bit unstuffing, byte assembly,
@ CRC5/CRC16, dispatch. None of this is cycle-budgeted -- see .md for why
@ that is a deliberate, load-bearing design choice and not an omission.
@ Placed in flash (.text); this section makes no timing claim so the
@ flash-vs-RAM cost swap (spec sec 2) does not apply to it.
@ ===========================================================================
	.text
	.thumb_func
rx_tail_entry_cs:
	@ Live-in: r2 = accumulator (up to 7 pending bits, newest at bit0),
	@          r4 = one past the last byte stored in raw_capture[],
	@          r5 = count of pending bits in r2 (0..7),
	@          r7 = NRZI reference bit ("bit -1").
	@ Flush the pending partial byte (if any) so raw_capture[] holds every
	@ captured bit contiguously: shift the r5 valid bits up to be MSB-first
	@ within their byte, matching the convention every full byte already has
	@ (oldest bit at bit7, from 8 ADCS inserts).
	cmp  r5, #0
	beq  tail_no_partial_cs
	lsrs r2, r2, #1                @ discard the SE0 sample itself: the escN
	                               @ that got us here ran its adcs BEFORE its
	                               @ beq, so bit0 is that invalid sample, not
	                               @ data -- see .md, this cost a real bug
	                               @ during bring-up (caught by hand-tracing
	                               @ esc2/esc8 against this code, not by the
	                               @ annotator, which does not model it)
	movs r3, #8
	subs r3, r3, r5               @ r3 := 8 - r5 (left-align amount)
	lsls r2, r2, r3
	strb r2, [r4, #0]
	adds r4, r4, #1
tail_no_partial_cs:
	@ r4 - raw_capture = total complete bytes now in raw_capture[]; total raw
	@ bits = that * 8 (the trailing (8-r5) padding bits in the flushed partial
	@ byte are known garbage and excluded via the bit count kept separately).
	ldr  r3, =raw_capture
	subs r6, r4, r3                @ r6 := byte count
	@ NOTE: exact bit count (for the final byte, only the top r5 bits are
	@ real) would additionally need r5 carried alongside r6 for the decoder
	@ below to stop mid-byte instead of at a byte boundary; omitted here --
	@ see .md "what we gave up" #4 (tail is sketched, not hardened).
	@
	@ From here: walk raw_capture[] with r7 as the running "previous bit",
	@ produce decoded (post-NRZI, post-unstuff) bytes into rxbuf_cs, running
	@ a CRC5/CRC16 LFSR alongside, then dispatch on PID -- structurally the
	@ same shape as rv003usb-arm.S:213-328, minus everything that file does
	@ per-bit inside the timed loop, because none of it needs to be there.
	@ Left as a sketch: this file's contribution is the timed engine and the
	@ capture/decode split, not a byte-for-byte reimplementation of the
	@ existing dispatch table, which is shared and unchanged in spirit.
	b    interrupt_complete_cs

@ ===========================================================================
@ Shared epilogue
@ ===========================================================================
	.thumb_func
interrupt_complete_cs:
	ldr  r0, =EXTI_BASE
	movs r1, #(1 << USB_PIN_DM)
	str  r1, [r0, #EXTI_PR_OFFSET]
	pop  {r4, r5, r6, r7, pc}

@ ===========================================================================
@ TX -- sketched only, per spec sec 3 ("may be sketched rather than
@ finished"). See .md "TX sketch" for the one-paragraph argument that the
@ same capture/decode split does NOT carry over to sending: a transmitter
@ must produce the bus edge on time, so TX has no untimed tail to defer to,
@ and its bit cell is a different, still-open problem this file does not
@ claim to solve.
@ ===========================================================================
	.thumb_func
usb_send_data_cleansheet:
	bx   lr
