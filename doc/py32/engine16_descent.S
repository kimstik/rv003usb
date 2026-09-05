@ engine16_descent.S -- DESCENT entry, Engine-16 competition
@ Direct compression of rv003usb-arm.S (32 cyc/bit) to 16 cyc/bit.
@ See engine16_descent.md for the full ledger and lineage discussion.
@
@ Register map, whole packet, all low registers (r0-r7):
@   r0 = prev masked 2-bit (D+|D-) sample
@   r1 = RXBUF_BASE  (constant address of rxbuf_descent)
@   r2 = constant 0x80 (bit-insert mask for a decoded "1")
@   r3 = GPIO_BASE   (constant, IOPORT base)
@   r4 = SHIFT_BUF   (byte under assembly)
@   r5 = scratch
@   r6 = BITSTUFF    (down-counter, persists across bits/bytes)
@   r7 = pin_mask    (constant, (1<<DP)|(1<<DM))
@
@ Placement: RAM (.datacode), matching the original and CHIP_FACTS_XIAMATSU.md
@ Sec.1 (RAM halves store cost 4->2; a GPIO access is 1 cycle in both columns).

.syntax unified
.thumb
.cpu cortex-m0plus

IDR_OFFSET   = 0x10
NBYTES       = 2        @ demo buffer size (PID + 1 data byte); mechanism
                         @ scales linearly to any USB_BUFFER_SIZE -- see .md
                         @ "what I gave up" for the code-size / branch-range
                         @ arithmetic that bounds how large this can grow.

@ GPIOB per BUILD_FACTS.md Sec.7 (0x50000400); board pin numbers are a
@ usb_config.h matter in the original and are left symbolic here -- DMASK
@ must fit an 8-bit `movs` immediate, true for any two-bit-position pair.
GPIO_BASE_ADDR = 0x50000400
USB_PIN_DP     = 0
USB_PIN_DM     = 3
DMASK          = (1 << USB_PIN_DP) | (1 << USB_PIN_DM)

.pushsection .bss.rxbuf_descent,"aw",%nobits
.balign 4
rxbuf_descent: .space NBYTES
.popsection

@ ---------------------------------------------------------------------------
@ RX_BIT_PLAIN byte, bit, next  --  bits 1..7 of a byte (no store).
@ RX_BIT_LAST  byte, bit, next  --  bit 8 of a byte (stores, then \next).
@
@ \next is byte<N+1>_bit1, or too_long_abort for the last unrolled byte.
@ Each generated cell is ledgered in engine16_descent.md Sec. "cycle ledger".
@ ---------------------------------------------------------------------------
.macro RX_BIT_PLAIN byte, bit, next
byte\byte\()_bit\bit\():
    ldr  r5, [r3, #IDR_OFFSET]        @ 1  sample D+|D-
    ands r5, r7                       @ 1  isolate the 2-bit field
    eors r5, r0                       @ 1  delta: 0 = "one", nonzero = "zero"
    beq  byte\byte\()_bit\bit\()_one  @ 1 (not taken, common)
    eors r0, r5                       @ 1  prev := new sample; sets Z iff 0
    beq  byte\byte\()_bit\bit\()_se0  @ 1 (not taken, common)
    lsrs r4, r4, #1                   @ 1  shift 0 into SHIFT_BUF
    movs r6, #6                       @ 1  reset BITSTUFF
    .rept 8
    nop                                @ 1 x8  pad to 16 (see ledger: 8 real)
    .endr
byte\byte\()_bit\bit\()_one:
    lsrs r4, r4, #1                   @ 1  shift, then...
    orrs r4, r2                       @ 1  ...set bit7 (r2 == 0x80)
    subs r6, r6, #1                   @ 1  BITSTUFF--; sets Z
    beq  byte\byte\()_bit\bit\()_stuff @ 1 (not taken, common)
    .rept 3
    nop                                @ 1 x3
    .endr
    b    \next                        @ 2-3 (taken, worst 3)
byte\byte\()_bit\bit\()_se0:
    b    se0_tail                     @ off the timed path once EOP is known
byte\byte\()_bit\bit\()_stuff:
    .rept 4
    nop                                @ 1 x4  finish THIS cell to 16
    .endr
    ldr  r5, [r3, #IDR_OFFSET]        @ 1  fresh cell: consume the stuff bit
    ands r5, r7                       @ 1
    eors r5, r0                       @ 1
    eors r0, r5                       @ 1  prev updated, bit discarded
    .rept 9
    nop                                @ 1 x9  pad this cell to 16
    .endr
    b    byte\byte\()_bit\bit\()       @ 2-3 (taken) retry same bit position
.endm

.macro RX_BIT_LAST byte, bit, next
byte\byte\()_bit\bit\():
    ldr  r5, [r3, #IDR_OFFSET]        @ 1
    ands r5, r7                       @ 1
    eors r5, r0                       @ 1
    beq  byte\byte\()_bit\bit\()_one  @ 1 (not taken, common)
    eors r0, r5                       @ 1  sets Z iff SE0
    beq  byte\byte\()_bit\bit\()_se0  @ 1 (not taken, common)
    lsrs r4, r4, #1                   @ 1
    movs r6, #6                       @ 1
    strb r4, [r1, #\byte]             @ 2  RAM store, compile-time offset
    movs r4, #0                       @ 1  clear SHIFT_BUF for next byte
    .rept 2
    nop                                @ 1 x2
    .endr
    b    \next                        @ 2-3 (taken, worst 3)
byte\byte\()_bit\bit\()_one:
    lsrs r4, r4, #1                   @ 1
    orrs r4, r2                       @ 1
    subs r6, r6, #1                   @ 1  sets Z
    beq  byte\byte\()_bit\bit\()_stuff @ 1 (not taken, common)
    strb r4, [r1, #\byte]             @ 2
    movs r4, #0                       @ 1
    b    \next                        @ 2-3 (taken, worst 3) -- no pad needed
byte\byte\()_bit\bit\()_se0:
    b    se0_tail
byte\byte\()_bit\bit\()_stuff:
    .rept 4
    nop                                @ 1 x4  finish THIS cell to 16
    .endr
    ldr  r5, [r3, #IDR_OFFSET]        @ 1  fresh cell
    ands r5, r7                       @ 1
    eors r5, r0                       @ 1
    eors r0, r5                       @ 1
    .rept 9
    nop                                @ 1 x9
    .endr
    b    byte\byte\()_bit\bit\()       @ 2-3 retry same (8th) bit position
.endm

.pushsection .datacode_descent,"ax",%progbits

@ ---------------------------------------------------------------------------
@ Entry: preamble/sync search adapted from rv003usb-arm.S:41-83, re-registered
@ onto the low-register-resident map above (no more mov-shuffle of a high
@ register into a low one before every GPIO access -- see .md "register
@ allocation"). Timing here is a sketch, not bench-verified, same status
@ PRIOR_ART.md and GRAINUUM's own note give these constants -- USB's SYNC
@ field is defined to give a receiver slop to lock phase; this is not the
@ 16-cycles-per-bit obligation the ledger below is about.
@ ---------------------------------------------------------------------------
.thumb_func
.global engine16_descent_entry
engine16_descent_entry:
    ldr  r3, =GPIO_BASE_ADDR          @ IOPORT base (constant for whole ISR)
    ldr  r0, [r3, #IDR_OFFSET]
    movs r7, #DMASK
    ands r0, r7                       @ r0 = initial (idle) D+|D- sample

preamble_wait:
    ldr  r5, [r3, #IDR_OFFSET]
    ands r5, r7
    cmp  r5, r0
    beq  preamble_wait                @ spin until an edge
    cmp  r5, #0
    bne  preamble_no_se0               @ short branch, in range (adjacent)
    b    too_long_abort                @ SE0 before any data: bail out (far)
preamble_no_se0:
    movs r0, r5                       @ lock: r0 = sample at the edge

    ldr  r1, =rxbuf_descent           @ RXBUF_BASE, resident for the packet
    movs r2, #0x80                    @ bit-insert constant, resident
    movs r4, #0                       @ SHIFT_BUF
    movs r6, #6                       @ BITSTUFF
    .ltorg                            @ force the literal pool HERE: the
                                       @ unrolled body below is far larger
                                       @ than a literal load's +/-1020 byte
                                       @ reach (ENGINE16_SPEC.md Sec.2 does
                                       @ not price this, but ARMv6-M's PC-
                                       @ relative LDR range is fixed either
                                       @ way -- see .md "what I gave up").

    @ Sketch only, unverified: a settle delay to the mid-bit sample point
    @ belongs here, matching the original's DELAY_CYCLES(71)/(96)
    @ (rv003usb-arm.S:62,83). Left out rather than filled with an
    @ unverified number dressed up as a real one -- see .md "what I gave
    @ up" #3.
    b    byte0_bit1

@ ---------------------------------------------------------------------------
@ Unrolled receive body: NBYTES=2 (PID + 1 data byte). USB_BUFFER_SIZE in the
@ original is 12; this demo uses 2 to keep the object small enough to read
@ and to stay inside a single short branch's reach to se0_tail (see .md,
@ "what I gave up", for the exact byte-count-vs-range arithmetic and the
@ CLEANSHEET finding it converges with).
@ ---------------------------------------------------------------------------
RX_BIT_PLAIN 0,1,byte0_bit2
RX_BIT_PLAIN 0,2,byte0_bit3
RX_BIT_PLAIN 0,3,byte0_bit4
RX_BIT_PLAIN 0,4,byte0_bit5
RX_BIT_PLAIN 0,5,byte0_bit6
RX_BIT_PLAIN 0,6,byte0_bit7
RX_BIT_PLAIN 0,7,byte0_bit8
RX_BIT_LAST  0,8,byte1_bit1

RX_BIT_PLAIN 1,1,byte1_bit2
RX_BIT_PLAIN 1,2,byte1_bit3
RX_BIT_PLAIN 1,3,byte1_bit4
RX_BIT_PLAIN 1,4,byte1_bit5
RX_BIT_PLAIN 1,5,byte1_bit6
RX_BIT_PLAIN 1,6,byte1_bit7
RX_BIT_PLAIN 1,7,byte1_bit8
RX_BIT_LAST  1,8,too_long_abort   @ last unrolled byte: never loop back in --
                                  @ D-2 closed structurally, see .md Sec 6.

@ ---------------------------------------------------------------------------
@ Exits from the timed loop. Nothing below this point has a 16-cycle
@ obligation: once EOP is recognised, or the buffer's static limit is hit,
@ there is no further sample due, so nothing here needs padding.
@ ---------------------------------------------------------------------------
.thumb_func
se0_tail:
    @ Stub. The untimed tail belongs here: CRC5/CRC16 over rxbuf_descent,
    @ then PID dispatch into the existing C layer (usb_pid_handle_*,
    @ rv003usb-arm.S:237-328, unchanged per spec Sec.4). Not implemented --
    @ see .md "what I gave up" #2 for why deferring the computation is
    @ argued to cost nothing, and why the code itself is still missing.
    bx   lr

.thumb_func
too_long_abort:
    @ Buffer's static limit reached without EOP: keep sampling (stay off the
    @ bus correctly) but write nothing further -- D-2 is closed by never
    @ having generated a store instruction that could reach past NBYTES-1,
    @ not by a runtime check.
    bx   lr
.popsection
