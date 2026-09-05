# Engine-16 competition — entries as they land, and what survived checking

The captain's record. Every claim an entrant makes about cycles is re-checked
here with `tools/engine16_cyc.py` against the object that actually assembles,
and every hardware claim is checked against a second source where one exists.
An entrant's own number is never taken on trust — not from distrust, but
because a competition judged on unverified self-reports is not a competition.

Two of six have finished. This file grows as the rest land.

## CLEANSHEET (no lineage, software, first principles) — CLAIM VERIFIED

Files: `engine16_cleansheet.S` (348 lines), `engine16_cleansheet.md` (440).
Assembles rc=0. All branches resolve to short `.n` encodings.

**Claimed:** exactly 16 cycles for 7 of every 8 bits; the 8th (byte boundary)
is 16 if a taken branch costs 2, 17 if it costs 3.

**Checked and it holds — but only in the flash column, which is the design's
deliberate choice.** The bit slot is:

```
ldr  r3, [r0, #IDR]   1     (IOPORT, 1 cycle in both columns)
lsrs r5, r3, #4       1
adcs r2, r2           1     carry-chain capture, no branch on data
tst  r3, r1           1
beq  escN             1     not taken on the common path
push {r7}             4     <- RAM access from flash-resident code
pop  {r7}             4     <-
nop x3                3
                     ==16
```

Running the annotator with `--exec ram` gives 12, not 16, because `push`/`pop`
cost 2 from RAM-resident code instead of 4. The design uses the *higher* flash
column cost of a stack access as deterministic padding. That is a legitimate and
rather elegant move — at LAT=0 instruction fetch is single-cycle from flash
anyway, so the engine gives up nothing to buy 8 cycles of exactly-known filler —
but it means the placement decision is load-bearing and not free. Relocating
this engine to RAM silently breaks its timing by 4 cycles per bit.

**Mechanism it offers:** capture/decode split. Sample the raw level into an
`adcs`-chained shift register every bit cell; defer NRZI decode, unstuffing,
byte assembly and CRC entirely to an untimed tail gated only by a per-bit SE0
check. No branch on data value anywhere, no r8-r12 pressure, and the buffer
bound that closes `DEFECTS_VERIFIED.md` D-2 falls out of a counter the loop
needed anyway.

**Negative result it reports, relevant to every entrant:** full straight-line
unrolling of the whole packet does not achieve zero recurring taken branches on
this ISA. The ±256-byte range of a short conditional branch forces a periodic
always-taken transfer regardless, to keep an SE0-escape target in reach. So full
unroll spends kilobytes of flash to pay the identical residual risk a small loop
already pays.

## NATIVE (peripheral-assisted) — MOSTLY NEGATIVE, AND THE NEGATIVES ARE THE VALUE

Files: `engine16_native.S` (266), `engine16_native.c` (215), `.md` (647).
Both assemble/compile rc=0.

**Direction 1, timer-triggered DMA from `GPIO->IDR`: DEAD, and for a reason
worth carrying into every future design.** GPIO on this part lives on a
core-private **IOPORT** bus, not on the bus matrix the DMA masters. Independently
corroborated here from the address map rather than only from the entrant's
reading of the reference manual: `DMA1_BASE` is `AHBPERIPH_BASE` = 0x40020000,
while `IOPORT_BASE` = 0x50000000 is a separate region, and no GPIO or EXTI
appears among the declared DMA request sources.

The elegant part: **the same architectural decision that makes `ldr rd,[gpio,#IDR]`
cost one cycle is what puts GPIO out of the DMA's reach.** Fast GPIO and
DMA-able GPIO are the same trade made in opposite directions. On an STM32F0,
where GPIO sits on AHB, this trick works and the port access is slower. This is
exactly the class of finding that a design transliterated from another platform
would import as a bug.

**Direction 2, timer input capture: WORKS for acquisition, and is the entry's
real contribution.** `CCxNP:CCxP=11` captures both edges on one channel; putting
that channel in slave-reset mode on its own edge makes `CCR1` deliver the
*interval* since the previous transition rather than a timestamp. That removes
timestamp subtraction, 16-bit wrap handling, and lets `MSIZE=8` make the ring one
byte per transition with 0 as a free sentinel.

**Direction 2b, decoding those intervals in software: does not fit at 24 MHz.**
Measured 21-22 cycles per transition, 23-24 with the once-per-byte path, against
16 cycles of wire time worst case. Compounding it, **EOP is a level (SE0), not an
edge**, so the capture stream cannot see it and a backlogged decoder misses the
SE0 window entirely — on an all-zero 8-byte DATA packet the response lands 33 bit
times late. The entrant reports the approach needs ≥36 MHz and works at 48 with
~30 % slack. So the 24 MHz operating point, not the peripherals, is what defeats
it.

**Direction 3, SPI as a shift register: DEAD.** No resynchronisation path; the
±1.5 % low-speed rate tolerance slips ±1.44 bit times over a 96-bit packet.

**Family split confirmed:** `py32f002bx5.h` has neither `DMA1_BASE` nor
`TIM3_BASE`. None of this runs on F002B, so for that part the software engine is
not a fallback — it is the only engine.

**Mechanism it offers:** input capture in slave-reset mode as an *acquisition
front end fitted under one of the software engines*, rather than as a decoder in
its own right. It costs three peripherals and 112 bytes and deletes the preamble
spin, the sample-point choice, the entry-latency budget, the dribble margin and
the phase drift, while the software engine keeps the 16-cycle bit cell where it
is fast enough. The same ring measures the 1 ms keepalive in hardware to one
cycle, which is a far better servo reference than a software counter clocked by
the engine it is meant to be correcting.

**Second idea, for transmit:** output-compare toggle mode plus DMA is a hardware
NRZI transmitter, because NRZI *is* "toggle on 0", and the toggle list is always
precomputable. Its bit cell costs zero CPU cycles and 26 cycles to arm, against
51 for "entry to first preamble store" today. Sketched with its breakages listed
(RAM cost, pin alternate-function conflict, SE0).

## Still running

DESCENT (compression of the existing engine), VUSB (AVR school), GRAINUUM (ARM
school, owns entry/phase/boundaries), BALANCE (own design, may look at the
others but may not repeat them).
