# SYNC lock: what everybody else actually does, and what the specification actually says

Research note.  No engine is modified by this document.

Motivation: commit `b729cff` found that `doc/py32/engine16_merged.S`'s phase lock
had an inverted confirming branch and had never locked at all, and measured, on
`tools/engine16_rx_bus.py`, that the locked sample lands at offset 9.0..15.9 of
the 16-cycle cell — jammed against the end of the cell — with a device-clock
tolerance of -0.20% .. +1.00%.  The obvious next question is whether that shape
is normal.  It is not.  Every implementation read below samples at or before the
middle of the cell, and every one of them detects the SYNC edge with a finer
instrument than our 7-cycle poll loop.

## 0. Provenance

Sources were cloned and read, not summarised from READMEs or articles.  Line
numbers below are into these exact trees:

| Short name | Repository | Commit | Date |
|---|---|---|---|
| `vusb` | https://github.com/obdev/v-usb.git | `801242072fa035e1839e3571df0d9a18e9cec95c` | 2026-08-26 |
| `mn` | https://github.com/micronucleus/micronucleus.git | `4dd1b49f39cd9d6fc736a720777ed62f4564e0e2` | 2026-02-07 |
| `grainuum` | https://github.com/xobs/grainuum.git | `16d0fa6717869332fc43d3b09f4ec850b94fb3a5` | 2017-06-16 |
| `lemc` | https://github.com/lemcu/LemcUSB.git | `f3daa52b9f19969da5b719c575fdf0cc6dfa4f4a` | 2014-03-23 |
| `rv003usb` | this tree | `rv003usb/rv003usb.S` | — |
| `ours` | this tree | `doc/py32/engine16_merged.S` | — |

Specification citations are to *Universal Serial Bus Specification Revision 2.0*
as distributed by USB-IF (`usb_20_20250603.zip` → `usb_20.pdf`, the April 27 2000
core document with errata).  Section numbers, not page numbers, are given.

Measurements attributed to "measured" were produced by running the linked image
on `tools/engine16_rx_bus.py` (the Unicorn harness introduced in `b729cff`) with
the engine source copied to a scratch directory and edited there.  The engines in
this tree are untouched.

Note on the two clones we already had opinions about: `micronucleus`'s copies of
`usbdrvasm12/16/128/165.inc` differ from upstream V-USB **only** in the ISR→plain
function conversion (ArminJo 2020), the removal of the SOF hook, and comment
typos.  The sync path — `waitForJ`, the unrolled `waitForK`, `foundK`, the
two-bit-K confirm and the retry — is byte-identical.  micronucleus's real
contribution to clock handling is `osccal.S` / `osccalASM.S`, treated in §2.5.

## 1. Comparison table

Cells marked "not verified" were not established from a source; they are not
filled in from a README or from inference.

| | ISA / clock | cycles per bit | how it finds SYNC | edge-detect granularity | where it samples in the cell | clock tolerance, and where the number comes from |
|---|---|---|---|---|---|---|
| **V-USB 12 MHz** | AVR, 12 MHz | 8 (`vusb usbdrvasm12.inc:16-17`) | `waitForJ` loop, then **unrolled** `waitForK` chain of 5 × (`sbis USBIN,USBMINUS` / `rjmp foundK`) (`:53-77`); one confirming sample one bit later, "we want two bits K", failure pops and re-enters `waitForK` (`:88-91`) | **2 cycles = 1/4 cell**; the source states the consequence: "The following code results in a sampling window of 1/4 bit which meets the spec" (`:58`) | **centre.** `foundK` is reached "{3, 5} after falling D- edge, average delay: 4 cycles [we want 4 for center sampling]" (`:79`) — the detection latency *is* the half-cell offset | none documented; the file requires "a 12 MHz crystal (not a ceramic resonator and not a calibrated RC oscillator)" (`:16-17`) |
| **V-USB 16 MHz** | AVR, 16 MHz | 10.667 (`vusb usbdrvasm16.inc:28`) | same shape (`:46-72`) | 2 cycles (`:51`, "< 1/4 bit") | centre: "{3, 5} … average delay: 4 cycles [we want 5 for center sampling]" (`:74`) | none documented; crystal required (`:16-17`) |
| **V-USB 18 / 20 MHz** | AVR, 18 / 20 MHz | 12 / 13.333 (`usbdrvasm18.inc:34`, `usbdrvasm20.inc:36`) | same shape | 2 cycles (`usbdrvasm20.inc:67`) | centre, stated as an explicit budget: "bit0 should be at 34 for center sampling. Currently at 4 so 30 cycles till bit 0 sample" (`usbdrvasm20.inc:97`) | none documented; crystal required |
| **V-USB 12.8 MHz** | AVR, 12.8 MHz RC | 8.533 | same shape (`usbdrvasm128.inc:113-140`) | 2 cycles (`:118`) | centre: "{3, 5} … [we want 4 for center sampling]" (`:140`) | **claimed ±1 %** (`:19-20`); **derived in the source from the loop's own stretch range**: "min frequency: 67 cycles for 8 bit -> 12.5625 MHz / max frequency: 69.286 cycles for 8 bit -> 12.99 MHz / nominal frequency: 12.77 MHz ( = sqrt(min * max))" (`:44-46`) — i.e. the tolerance is a property of how far the receive loop can be stretched per byte by its PLL, not a spec quantity |
| **V-USB 16.5 MHz** | AVR, 16.5 MHz RC | 11 (`usbdrvasm165.inc:32`) | same shape (`:51-98`); the retry is explicitly bounded: "The entire loop from waitForK until rjmp waitForK above must not exceed two bit times (= 22 cycles)" (`:97-98`) | 2 cycles (`:56`) | centre (`:79`) | **±1.1 %**, stated as an absolute frequency band: "16.3125 MHz < F_CPU < 16.6875 MHz (+/- 1.1%)" (`:33`); mechanism is the per-bit phase sample + once-per-byte 3-cycle insertion described in §2.1 |
| **Grainuum** | Cortex-M0+, 48 MHz | 32 (`grainuum grainuum-phy-ll.s:97-99`) | **unrolled** `.rept 8` of `ldr`/`and`/`cmp`/`bne` waiting for *any* change (`:220-229`), then `.rept 6` of one sample per cell looking for the value to **repeat** (the KK) (`:237-245`) | **4 cycles = 1/8 cell**, stated: "The loop is 4 cycles on a failure. One pulse is 32 cycles. Therefore, loop up to 8 times before giving up" (`:220-222`) | **early**, ≈5-9 of 32 (16-28 %) on Grainuum's own cycle model (`:97-99`): after the edge it calls `usb_phy__wait_5_cycles` — "Move us away from the start of the pulse, to avoid transition errors" (`:232-233`) — and then samples once per 32-cycle cell | not verified — no tolerance statement anywhere in `grainuum-phy-ll.s`, `grainuum-phy.c` or `grainuum.h` |
| **LemcUSB** | Cortex-M0+, **24 MHz** | **16** (`lemc …/usb_internal_bitbangusb.s:201-224`, README:19) | GPIO edge IRQ on D+ (`:82`), then **unrolled** 7 × (`LDR`/`TST`/`BEQ`) with a 1-bit-time timeout (`:139-162`), then **5+1 further samples that must all agree** across the following cell — "check, if bitstate is still 1 (=> if 2 consecutive 1 bits received)" (`:165-187`) | **4 cycles = 1/4 cell** (`LDR`+`TST`+`B<cc>` untaken on M0+) | **centre**: `BL _delay_13` then "here: We are in the middle of the very first bit of this packet" (`:189-191`) | crystal: "Apart from an external 24 MHz crystal nothing more is needed" (README:39).  An RC/DPLL mode is future work: "A synchronization method using an internal RC oscillator is in evaluation phase … the synchronization will most likely not retune the HFRCO, but act like a DPLL" (README:19-23) |
| **rv003usb (RISC-V original)** | RV32EC (QingKe V2A), 48 MHz | 32 | **unrolled** chain of 8 × (`c.lw`/`c.andi`/`bne a0,a1,syncout`) waiting for *any* change from the level latched at entry (`rv003usb.S:133-163`), then a per-bit `preamble_loop` that exits when a bit shows **no** transition — the terminating '1' of SYNC (`:182-204`, exit at `:192`) | one sample per unrolled step (`c.lw`+`c.andi`+`bne`); per-step cycle cost on QingKe V2A not verified, but the chain is sized to one bit cell, the same way Grainuum's `.rept 8` is | not stated in the source; the `preamble_loop` re-times every SYNC bit (§2.2), so the offset is set by the retime, not by the initial catch | not stated in `rv003usb.S` |
| **ours (engine16)** | Cortex-M0+, 24 MHz | **16** (`doc/py32/engine16_merged.S`, chain at `:1072`+) | `.Lwait_j` **loop** for a J, `.Lwait_k` **loop** for the J→K edge (`:999-1011`), then one confirming sample 23 cycles later which must still read K (`:1016-1022`) | **7 cycles = 7/16 cell** (measured: hunt samples at emulated cycles 64, 71, 78, 85 …; `subs`+untaken `beq`+`ldr`+`lsls`+taken `b` = 1+1+1+1+3) | **9.0..15.9 of 16 — the last third of the cell** (measured, `tools/engine16_rx_bus.py`); the source intends ≈8.5 (`:1034-1040`) and is wrong by 4 cycles, see §4.1 | **measured**: -0.20% .. +1.00% at entry latency 16 phase 0; **guaranteed over 4 entry latencies × 4 sub-cycle phases: -0.05% .. +0.85%** |

Two structural facts fall out of the table.

**Everybody else unrolls the edge hunt; we loop it.**  V-USB (2 cycles), Grainuum
(4 cycles), LemcUSB (4 cycles) and rv003usb (unrolled chain) all pay code space to
buy phase resolution, because the granularity of the edge catch *is* the width of
the sample distribution and therefore the clock-tolerance budget.  Ours is the
only one with a loop, and at 7 cycles of 16 it is the coarsest instrument in the
table by a factor of two relative to the cell.

**Nobody else samples in the last third of the cell.**  V-USB and LemcUSB sample
at the centre by construction; Grainuum samples early on purpose.  Our
distribution is where it is because of an arithmetic slip (§4.1), not a decision.

## 2. Mechanisms we do not use

### 2.1 Per-bit phase sampling with a once-per-byte correction (V-USB's PLL)

**What it is.**  In `usbdrvasm165.inc` the receive loop takes *two* samples per
bit: the data sample, and a "phase" sample about half a bit later.  The phase
sample is XORed with the *next* bit's data sample and the disagreement is
accumulated into a register (`in r0, USBIN` … `eor r0, x2` / `or phase, r0`,
e.g. `:270-277`, `:284-290`, repeated for every bit).  Once per byte the
accumulator is tested and, if any disagreement was seen, a 3-cycle `lpm` is
executed as a no-op to stretch that byte:

```
    sbrc    phase, USBMINUS ;[058]
    lpm                     ;[059] optional nop3; modifies r0
    in      phase, USBIN    ;[060] <-- phase
```
(`vusb usbdrvasm165.inc:143-145`)

It is a one-directional bang-bang phase detector: the loop can only be made
longer, never shorter, and only once per byte.  `usbdrvasm128.inc:44-46` states
what that buys as a range of loop lengths (67 .. 69.286 cycles per 8 bits) and
converts it to a frequency band — that is the entire derivation of V-USB's
"±1 %" claim.

**Cost on M0+ at 16 cycles/bit.**  Three instructions per cell:
`ldr r0,[r7,#IDR]` (1 cycle from the IOPORT-based `r7`), `eors r0,r2` against the
cell's own data sample (1), `orrs rPHASE,r0` (1).  Plus, once per byte, a test
and a two-path back edge: `lsls rPHASE,#(31-DM)` / `bpl 1f` / a 1-2 cycle pad —
about 4 more cycles.  Total ≈ 28 cycles per 128-cycle wire byte.

**Does it fit?**  No, not in the chain as it stands.  Measured free cycles (`nop`s)
per cell in the linked image, `USB_RX_CHECK=CRC16`, flash-resident:

| cell | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| spare cycles | 4 | 0 | 2 | 2 | 2 | 0 | 1 | 0 |

Eleven spare cycles per byte against the twenty-four a per-cell phase sample
needs.  A **once-per-byte** correction is a different matter: the 3 cycles for one
phase sample fit in cell 0 (4 spare), and the only thing missing is a two-path
back edge in cell 7, which has zero spare and ends in the `bx` that closes the
chain.  So the honest statement is: a per-byte bang-bang PLL costs one cell's
worth of spare cycles plus a re-balance of cell 7; a per-bit one does not fit
without moving segment work out of five cells.

**What it would buy.**  It converts the free-running drift budget from
"total drift over the whole packet" into "drift between corrections".  See §3.4
for the arithmetic; the short version is that it is the difference between
needing ±0.5 % and needing ±4 %, which is the difference between a crystal (or a
trimmed HSI) and an untrimmed RC oscillator.

### 2.2 Re-timing during the SYNC preamble (rv003usb's mechanism, which we dropped)

**What it is.**  The RISC-V original does not lock on one edge and leave.  After
the coarse catch it runs `preamble_loop`, one iteration per bit cell, and inside
each iteration it takes a *second* sample later in the same cell and shortens the
loop by 2 cycles if the level has already changed:

```
	c.lw s0, INDR_OFFSET(a5);
	c.andi s0, USB_DMASK;
	c.xor s0, a1

	// TRICKY: This helps retime the USB sync.
	// If s0 is nonzero, then it's changed (we're going too slow)
	c.bnez s0, 2f;  // This code takes 6 cycles or 8 cycles, depending.
	c.j 1f; 1:
	2:
	j preamble_loop // 4 cycles
```
(`rv003usb.S:194-204`)

That is the same family as V-USB's PLL — a one-directional bang-bang, 2 cycles of
32 per SYNC bit — applied to whatever SYNC bits remain after entry.  The loop
exits on the *absence* of a transition (`c.beqz a0, done_preamble`, `:192`), which
is the last SYNC bit.

**Side-by-side with ours.**  Both engines find the same instant (the end of SYNC),
by different tests:

```
rv003usb.S:182-204              — "no transition between two consecutive
preamble_loop:                     per-cell samples" ⇒ the SYNC hold.
	c.lw a0, INDR(a5)          Polarity-free: only J≠K matters, never
	c.andi a0, USB_DMASK       which of the two it is.
	c.beqz a0, done_usb_message
	c.xor a0, a1
	c.xor a1, a0
	c.beqz a0, done_preamble   <-- exit: this cell did not change
```
```
engine16_merged.S:999-1022      — "a J→K edge whose next cell is still K"
.Lwait_j: ... bpl .Lwait_j         ⇒ the SYNC hold.  Depends on absolute
.Lwait_k: ... bmi .Lwait_k         polarity in three separate places
	.rept 20 / nop / .endr         (bpl, bmi, bmi), which is exactly why
	ldr  r0,[r7,#USB_IDR_OFS]      one of them could be inverted for the
	lsls r0,r0,#(31-USB_DM_BIT)    life of the port without anything
	bmi  .Lsync_hunt               noticing.
```

So: **the RISC-V original does not have the same K-vs-J confirm structure at all.**
It is not a case of a correct original that our ARM transliteration inverted.  The
confirm-by-polarity structure is ours (adopted from V-USB, as the comment at
`:917` says), and it is the structure — three independent polarity decisions, no
model that executes them — that let the inversion survive.  rv003usb's
transition/no-transition test cannot be inverted in that way: there is only one
sense, and getting it wrong fails on the first packet in an obvious way.

**Cost on M0+ at 16 cycles/bit.**  We cannot run rv003usb's version verbatim,
because we do not have a preamble loop at all: `.Lk_edge` falls through 14 cycles
of priming straight into `usb_rx_chain` (`:1046-1064`), so the only SYNC cell
available after the lock is cell 7.  Adopting it means replacing the
"lock on the last edge" design with "lock on the first edge you see, then track
SYNC to its end", i.e. a new ≈10-12 cycle per-cell loop with a 2-path body.  What
it buys is not primarily phase accuracy — it is that the entry-latency window
stops depending on *which* SYNC edge is still ahead of us (§2.4).

### 2.3 An unrolled edge hunt instead of a poll loop — measured

This is the cheapest of the mechanisms and the one with a number attached.

**What it is.**  Replace

```
.Lwait_k:
	subs    r1, #1
	beq     .Lgiveup
	ldr     r0, [r7, #USB_IDR_OFS]
	lsls    r0, r0, #(31 - USB_DM_BIT)
	bmi     .Lwait_k
```
(`engine16_merged.S:1006-1011`, period 7 cycles) with a straight-line chain

```
	.rept   6
	ldr     r0, [r7, #USB_IDR_OFS]	/* 1 (IOPORT base in r7)             */
	lsls    r0, r0, #(31 - USB_DM_BIT)	/* 1                                 */
	bpl     .Lk_edge		/* 1 untaken, 3 taken                */
	.endr
	b       .Lwait_k
```

period **3 cycles**, 6 samples covering 18 cycles ≥ one cell.  This is V-USB's
`waitForK` (`usbdrvasm12.inc:57-70`), Grainuum's `.rept 8`
(`grainuum-phy-ll.s:223-228`) and LemcUSB's 7-deep chain
(`usb_internal_bitbangusb.s:139-161`), transliterated.  The current design's note
that a fall-through exit "costs nothing and its 2-vs-3 never lands in the phase"
(`:919-925`) still holds: the unrolled exit is a *taken* branch, but it is taken
from a known place, so it adds a constant 3, not an ambiguity.

**Measured, by building the variant and running it on the harness.**  Sample-offset
band over all usable entry latencies × 8 sub-cycle packet phases, and the
device-clock tolerance guaranteed over 3 entry latencies × 4 phases:

| variant | sample offset band | band width | decode failures over 256 (entry, phase) pairs | guaranteed clock tolerance |
|---|---|---|---|---|
| as committed (loop, 20 nops) | 9.0 .. 15.9 | 6.9 | **7** | **-0.05 % .. +0.85 %** |
| loop, 16 nops (centred) | 5.0 .. 11.9 | 6.9 | 0 | -0.55 % .. +0.40 % |
| unrolled ×6, 12 nops | 3.0 .. 7.0 | **4.0** | 0 | -0.70 % .. +0.40 % |
| unrolled ×5, 15 nops | 6.0 .. 10.9 | 4.9 | 0 | **-0.45 % .. +0.65 %** |

Reading: the unroll narrows the band from 6.9 cycles to 4.0-4.9 and widens the
guaranteed tolerance span from 0.95 % to 1.10 %; the *centre* of the band is set
independently by the nop count in `.Lk_edge`.  Note the first row: **as committed,
7 of 256 (entry latency, packet phase) combinations do not decode at all**, and
the guaranteed tolerance has essentially no margin on the slow side.

**Integration cost, measured.**  The unroll is +36 bytes (6 samples × 6 bytes) and
it collides with a constraint the source already documents: the EOP stubs sit in
front of the chain "so that a backward `B<cond>` from an early cell reaches them"
within Thumb's ±254 bytes (`:926-931`).  Adding the unrolled chain between the
stubs and the chain pushes cells 0..3 out of range — the assembler says
`Error: branch out of range` at `CELL 0` .. `CELL 3`.  There is about 16 bytes of
headroom, so the unroll only assembles if the `.Lk_edge` delay shrinks by roughly
as much as the unroll grows; that is why the two working variants above have 12
and 15 nops rather than the ~17 that would centre the band.  **Getting both the
narrow band and the centred band requires relocating the EOP stubs**, which is a
layout change, not a timing change.

### 2.4 Locking earlier and hunting with a shorter prologue (V-USB's "push only what is necessary")

**What it is.**  V-USB pushes three registers and enters `waitForJ`
(`usbdrvasm165.inc:39-51`: "push only what is necessary to sync with edge ASAP"),
and does the rest of the register saving *after* the lock — "we have 1 bit time
for setup purposes" (`:80-81`), with the pushes interleaved into the first sampled
bits (`:104-119`).  LemcUSB does the same in a smaller way: ≈28 cycles of prologue
before its hunt (`usb_internal_bitbangusb.s:108-136`; counted off the listing at
ARMv6-M costs, not stated in the source).

**Ours.**  Measured on the harness: with the ISR body entered at emulated cycle 6,
the *first hunt sample* is at cycle 64 — **58 cycles of prologue** (SE0 test,
`push {r2-r7,lr}`, four `mov`s from the high registers, `push {r4-r7}`, five
literal loads, the CRC seed).  That is 3.6 bit times spent before the instrument
is even switched on, and it is the whole explanation of the measured entry-latency
window:

```
G6  entry latency that still decodes: 6..37 cycles = 0.38..2.31 bit times
```

37 + 58 = 95, and SYNC cell 5 — the last J the hunt can catch before the final
J→K edge at cycle 96 — ends at 96.  The window's upper edge is set by the
prologue, one cycle to spare.

**Cost of moving it.**  The four `mov rN, r8..r11` + `push {r4-r7}` block (11
cycles) and the four literal loads that feed the *tail* rather than the chain
(`ldr r4,=usb_tables` is needed by cell 0, but `usb_rxbuf+2` into `r9`, the chain
head into `r14`, and the CRC seed into `r10` are not needed until later cells)
could move after `.Lk_edge`.  There is no room in the 14-cycle priming block, but
there are 11 spare cycles spread over cells 0..6 (§2.1's table) — that is exactly
the "interleave the pushes into the first sampled bits" trick V-USB uses, and it
is the only place the cycles exist.  Every cycle moved is a cycle of entry-latency
budget, which currently stands at 37 against an ARMv6-M exception entry of ~15
cycles plus the interrupted instruction plus EXTI latency.

**What it would buy.**  Directly: headroom on `G6`.  Indirectly: it is the only
one of these mechanisms that also helps the *keepalive* path, where the ISR must
see a 2-bit-time SE0 before it ends.

### 2.5 Trimming the clock instead of tracking it (micronucleus / V-USB `osccal`)

**What it is.**  micronucleus does not improve its receiver's phase tracking; it
moves the problem to the oscillator.  Two generations exist in the tree:

* `mn firmware/osccalASM.S:10-50` (cpldcpu, 2013/2014) — a combined binary +
  neighbourhood search over `OSCCAL`, using "the Start Of Frame signal (a single
  SE0 bit) repeating every millisecond immediately after a USB RESET" as the
  reference, measuring in multiples of 5 cycles, with the neighbourhood search
  justified by "the quasi-monotonic nature of OSCCAL (See Atmel application note
  AVR054)".
* `mn firmware/osccal.S` (Ralph Doncaster, 2020) — "optimized OSCCAL tuning from
  low-speed USB SOF every 1ms" (`:1-2`), a 12-cycle counting loop
  (`countFrame`, `:73-88`) whose accumulator is scaled so that "countH LSbit
  =~ 0.5%" (`:70`), needing "5 consecutive EOF/SOF transitions" (`:45`) and
  applying the correction as a straight subtraction from `OSCCAL` (`:59-63`).

The measurement interval is one 1 ms frame = 24 000 cycles at 24 MHz, so a ±1
cycle count resolution is ±42 ppm — three orders of magnitude better than
anything the 8-bit SYNC field can offer (§2.6).

**Relation to our plan.**  `doc/py32/PRIOR_ART.md:64` (D-5) already proposes a
continuous keepalive servo on `RCC_ICSCR.HSI_TRIM`.  This document adds two things
to that entry: the spec clause that guarantees the reference exists, which
`PRIOR_ART.md:255` (L-9) records as **UNVERIFIED** — it is **§11.8.4.1**, "All hub
ports to which low-speed devices are connected must generate a low-speed
keep-alive strobe, generated at the beginning of the frame, which consists of a
valid low-speed EOP … The strobe must be generated at least once in each frame in
which an SOF is received" (see also §7.1.7.6 and Table 7-2 note 4, "The keep-alive
is a low-speed EOP") — and the observation that Doncaster's `countFrame` measures
the interval between two EOPs with a 12-cycle loop, i.e. it does not need an input
capture peripheral at all.

### 2.6 Measuring the clock error from SYNC itself — why it does not work

The task asks whether SYNC gives a lock opportunity we are not using.  Three
candidates, and the arithmetic that kills two of them:

1. **Estimate the clock error from the SYNC period.**  SYNC's guaranteed edges span
   6 bit times = 96 cycles nominal (§7.1.10: "3 KJ pairs followed by 2 K's").
   Timing the first and last edge with our 7-cycle poll gives a ±7-cycle error on
   a 96-cycle interval = ±7 %.  Even a perfect 1-cycle timestamp (a timer input
   capture) gives ±1 %.  The tolerance we need to *resolve* is ±0.5 %.  **SYNC is
   too short to measure the clock error to the accuracy the receiver needs**, by
   about an order of magnitude, on any instrument this part has.  The 1 ms
   keepalive interval (§2.5) is 250× longer and is the only usable reference.
2. **Average over several SYNC edges to reduce the phase error.**  Each of SYNC's
   6 guaranteed edges is nominally 16 cycles apart, so averaging N of them reduces
   the *random* part of the detection error by √N — but our detection error is not
   random, it is a uniform 0..7 quantisation of the same fixed phase, and it is
   perfectly correlated between edges.  Averaging gains nothing.  Sampling
   *finer* (§2.3) is what gains.
3. **Use more than the last edge, by re-timing (§2.2).**  This is the one that
   works, and it is what rv003usb and V-USB 12.8/16.5 do.

One further spec-level warning that bears directly on candidate 1: **§7.1.14.1
says the first SYNC bit is not usable at all** — "Note: Because of this distortion
of the SOP transition relative to the next K-to-J state transition, the first SYNC
field bit should not be used to synchronize the receiver to the data stream."  So
the measurable span is 5 bit times, not 6 or 7, which makes candidate 1 worse
still.

## 3. What the specification actually requires

### 3.1 Source data-rate tolerance — the ±1.5 % in our documents is the wrong direction

**§7.1.11**: "The low-speed data rate is nominally 1.50 Mb/s.  For low-speed
functions, the required data-rate when transmitting (TLDRATE) is 1.50 Mb/s ±1.5 %
(15,000 ppm).  This allows the use of resonators in low cost, low-speed devices."

That number governs **our transmitter**.  The same section says of the traffic we
*receive*: "For hosts, hubs, and high-speed capable functions, the required
data-rate accuracy when transmitting at any speed is ±0.05 % (500 ppm)", and
§7.1.12 repeats it: "the Host Controller and hubs must meet clock accuracy
specification of ±0.05 %".  Table 7-5's *Low-speed Downstream* column charges the
source (the host, signalling at low-speed rate through hubs) 1.7 ns/bit =
0.26 % of a low-speed bit, and charges the ±1.5 % to the **function** as its own
receiver-side term ("Function Frequency Tolerance 10.0 ns/bit, 70.0 ns total").

So a low-speed device's receiver never sees a ±1.5 % source.  ±1.5 % is what the
device is *allowed to be*, and the spec pays for it out of the receiver's own
jitter budget over **seven bit times** (see §3.4).  V-USB understood this exactly
and says so on the *transmit* side: "We don't match the transfer rate exactly
(don't insert leap cycles every third byte) because the spec demands only 1.5 %
precision anyway" (`vusb usbdrvasm16.inc:272-273`, same at `usbdrvasm20.inc:293`,
`usbdrvasm15.inc:348-349`) — a transmit comment, in the transmit routine.

### 3.2 EOP width

**§7.1.13.2.1**: "For low-speed transmissions, the transmitter's SE0 for EOP width
must be between 1.25 µs and 1.50 µs" (1.875 .. 2.25 low-speed bit times = 30 .. 36 cycles at 24 MHz);
"A receiver must accept any valid EOP"; "a low-speed SE0 interval may be as short
as 670 ns (TLEOPR)".  670 ns = **16.1 cycles at 24 MHz**, i.e. **one bit cell**.
A receiver that tests for SE0 once per cell (as ours does, one `ands`/`beq` per
cell, `CELL` macro) will see a 670 ns SE0 in at least one sample; a receiver that
tested less often than once per cell would not.  Our margin here is exactly one
sample, with no slack: this is worth keeping in mind if anyone ever proposes
testing SE0 in only some cells.

**§7.1.14.1** adds that a hub may skew a low-speed SE0's width by up to ±300 ns
(TLHESK) on the cable from the low-speed device, and that the hub's SE0 sense
delay may exceed its differential delay by up to 200 ns at low speed "(to prevent
creating a bit stuff error at the end of the packet)".

### 3.3 Dribble — and our "floor of 7" is *arithmetically* right and *substantively* moot

**§7.1.9.1** is the only place the spec defines dribble:

> "The time interval just before an EOP is a special case.  The last data bit
> before the EOP can become stretched by hub switching skews.  This is known as
> dribble and can lead to the case illustrated in Figure 7-33, which shows where
> dribble introduces a sixth bit that does not require a bit stuff.  Therefore,
> the receiver must accept a packet for which there are up to six full bit times
> at the port with no transitions prior to the EOP."

Three consequences, and none of them is "sample after offset 7 of 16":

1. **The spec states dribble in bit times, not nanoseconds, and it states an
   *acceptance* requirement, not a sampling requirement.**  Figure 7-33 is
   captioned "Illustration of Extra Bit Preceding EOP (Full-/low-speed)" and its
   left margin reads "Acceptable Extra Bit, No Error".  The spec's demand is that
   an extra decoded bit before EOP **must not** cause the packet to be rejected.
   A receiver that avoids *seeing* the extra bit by sampling late is one valid
   implementation; a receiver that sees it and discards it is another, and it is
   the one the spec's own figure describes.
2. **The "260 ns" in our documents is not a USB 2.0 quantity.**
   `doc/py32/PRIOR_ART.md:131` (D-9) and `:250` (L-4) cite "USB 2.0 §7.1.9/§7.1.14
   via SPRAAT5A LS14" for "up to 260 ns".  §7.1.9 contains no times at all, and
   §7.1.14.1's related numbers are 200 ns (hub EOP-sense delay in excess of the
   differential delay), ±45 ns (TLDHJ1) and ±300 ns (TLHESK).  260 ns is a
   third-party roll-up.  It is not wrong as an engineering estimate — 200 + 45 is
   245 — but it must not be cited as a spec quantity, and 200 ns / 4.8 cycles is
   the number the spec will support.
3. **The rescaling from 32 to 16 cycles is correct, and the concern does not
   transfer to this engine.**  `doc/py32/engine16_grainuum.md:457-462` derives
   6.24 cycles at 24 MHz from the same 260 ns that gives 12.5 cycles at 48 MHz,
   and notes both are 39 % of a bit cell.  That arithmetic is right: it is a fixed
   *time*, both readings agree, and "floor 7 of 16" is **not** a mis-scaled
   32-cycle number.  But the requirement it encodes belongs to a *different
   engine*.  It came from rv003usb, which rejects a packet whose bit count is not
   a multiple of 8:

   ```
   se0_complete:
   	andi a0, s1, 7; // Make sure we received an even number of bytes.
   	c.bnez a0, done_usb_message
   ```
   (`rv003usb.S:473-477`, which is what `PRIOR_ART.md:257` L-8 cites).  Our engine
   has no such test.  It handles the trailing partial wire byte explicitly:
   "The last, partial wire byte: r14 holds 8-k … because a packet's data field is
   a whole number of bytes the accumulator is empty when they arrive, so no
   spurious byte is emitted" (`engine16_merged.S:1680-1703`).  V-USB does the same
   thing by omission — `se0` (`vusb asmcommon.inc:54-63`) converts `cnt` to a byte
   count and never looks at the bit remainder at all.

   **Measured**: holding the last data level for a *full* 16 cycles (666 ns, more
   than twice the 260 ns figure) into the first SE0 cell decodes correctly, for
   every one of 4 entry latencies × 4 sub-cycle phases, both with the committed
   20-nop delay and with the centred 16-nop delay.  The dribble floor does not bind
   on this engine at any sample offset it can produce.

   One residual worry disposed of: could the extra bit trip our sticky
   seven-consecutive-ones check (`engine16_merged.S:1726-1729`)?  No.  §7.1.9
   requires the transmitter to insert a stuffed zero "even if it is the last bit
   before the end-of-packet (EOP) signal", so the last bit on the wire after six
   ones is always a zero, and the dribble-extended level therefore contributes a
   run of one, not a seventh one.

**Verdict on the question asked**: the floor-of-7 reading is arithmetically
correct and is not a mis-scaling — and it is also not a requirement this engine
has to meet.  It should be retired as a *constraint* (`PLAN.md:303` F5 calls it a
"Physical requirement, not a preference"; on engine16 it is neither) and kept only
as a weak preference, well below "centre the band", which is a real requirement
for a different reason (§3.4).

### 3.4 How long a receiver must stay locked, and what sample-window margin that needs

**The lock interval.**  §8.4.4: "The maximum data payload size allowed for
low-speed devices is 8 bytes."  So the longest low-speed packet is
SYNC (8) + PID (8) + 64 data + CRC16 (16) = **96 bit times**, plus stuffed bits
(at most one per six, ≤ ~14 more).  From the last SYNC edge to the last CRC bit is
≈ 90 cells = **1440 cycles at 16 cycles/cell**.

**What the spec asks of a receiver.**  §7.1.15.1: "Data receivers are required to
decode differential data transitions that occur in a window plus and minus a
nominal quarter bit cell from the nominal (centered) data edge position.  (A
simple 4X over-sampling state machine DPLL can be built that satisfies these
requirements.)"  Table 7-5's *Low-speed Downstream* column derives that as
**Function Receiver Jitter Budget = 141.0 ns (next transition) / 184.0 ns
(paired)** — 141 ns is 21 % of a 666.67 ns bit, 3.4 cycles at 24 MHz.

Two things follow.

*First*, the required sample window.  If a data edge may sit anywhere within
±141 ns of nominal, the guaranteed-safe part of a cell is
`[141 ns, 666.67-141 ns]` = **cycles 3.4 .. 12.6 of 16**.  That is the
specification's answer to "what sample-window margin does a receiver actually
need": the sample must be at least 3.4 cycles from either cell boundary, and the
whole distribution of possible sample positions must fit in a 9.2-cycle window.
Our measured band is 9.0..15.9 — 6.9 cycles wide, so it *would* fit, but it is in
the wrong place: 3.3 of its 6.9 cycles lie outside the safe region, past 12.6.
The 16-nop band (5.0..11.9) fits entirely inside it.  V-USB's "sampling window of
1/4 bit which meets the spec" (`usbdrvasm12.inc:58`) is a claim about exactly this
requirement, and it is the same 1/4 cell.

*Second*, and more important: **the spec's receiver model re-synchronises, and
ours does not.**  §7.1.9: bit stuffing "gives the receiver logic a data transition
at least once every seven bit times to guarantee the data and clock lock", and
Table 7-5 charges the function's own ±1.5 % as "10.0 ns/bit, 70.0 ns total" —
7 bits × 10 ns, i.e. the drift is only ever accumulated over the seven bit times
between guaranteed transitions.  §7.1.15.1 spells out the intended implementation:
"A simple 4X over-sampling state machine DPLL".

A receiver that locks once at SYNC and free-runs for 90 cells is outside that
model.  Its clock-error budget is not the spec's ±1.5 % but

```
  ε_max  =  (usable window, in cycles)  /  (cells to end of packet × 16)
         ≈  (16 − band width) / 2  /  1440
```

which for our measured band width of 6.9 gives ±0.31 %, and for the unrolled
band width of 4.0 gives ±0.42 % — against a *measured* guaranteed tolerance of
±0.475 % (16-nop) and ±0.55 % (unrolled ×5, 15 nops).  The model is right to
within the harness's granularity.

The same formula, applied to a receiver that re-syncs every seven bit times,
gives ε_max ≈ 4.0 cycles / (7 × 16) = **±3.6 %** — comfortably beyond the ±1.5 %
the spec allows a low-speed function, which is precisely why the spec allows it.
**That factor of eight is what §2.1/§2.2 buy, and it is the whole difference
between "needs a trimmed clock" and "runs on the RC oscillator as it comes."**

### 3.5 Turnaround, for completeness

§7.1.18.1: "A device must provide at least two bit times of inter-packet delay …
the maximum inter-packet delay for a function or hub with a detachable
(TRSPIPD1) cable is 6.5 bit times measured at the Series B receptacle.  If the
device has a captive cable, the inter-packet delay (TRSPIPD2) must be less than
7.5 bit times."  At 16 cycles/bit that is **32 .. 104 cycles** (detachable) or
120 (captive) — the numbers `PRIOR_ART.md:249` (L-1) gives for the 32-cycle
engine, halved.  Nothing in this document changes them; they are quoted here only
because §7.1.18 is one of the sections the task asked to settle and the entry
above cites it correctly.

## 4. Defects and wrong claims in our own documents

### 4.1 The phase lock's delay is four cycles too long, and the source's arithmetic says why

`engine16_merged.S:1034-1040` reasons:

> "Confirmed.  The first PID cell starts at t0 + 32, so its centre is t0 + 40 =
> confirming sample + 16.  ldr + lsls + untaken bpl = 3, so the priming below is
> 14 cycles: 9 of setup, 5 of nop."

and `:1014-1015`:

> "20 nops put the confirming sample at t0 + 23.5 nominal, i.e. the middle of the
> NEXT cell."

Both statements omit a term.  Traced on the harness (entry latency 6, packet
phase 0; emulated cycle in the left column):

```
   97   ldr  r0,[r7,#16]     <- the K-detecting sample.  t0 = 96, so d = 1
   98   lsls r0,r0,#27
   99   bmi  .Lwait_k        (not taken)
  100 ..119   20 × nop
  120   ldr  r0,[r7,#16]     <- the confirming sample: detect + 23, not + 20
  121   lsls r0,r0,#27
  122   bmi  .Lsync_hunt     (not taken)
  123   nop                  <- the .balign 4 fill, uncounted
  124 ..137   14 × priming
  138   ldr  r2,[r7,#16]     <- first chain sample: confirm + 18, not + 16
```

* The 3 cycles of `ldr`/`lsls`/untaken `bmi` that *detect* the edge are charged to
  nothing: the confirming sample is at `t0 + d + 23`, not `t0 + 3.5 + 20`.
* The `.balign 4` immediately before `.Lprime` (`:1046-1048`) emits one `nop`
  here.  Its comment says it is placed there "so no variable-size fill can land
  inside the counted priming block" — correct, and the fill instead lands
  *outside* the block where the `.if (usb_rx_chain - .Lprime) != 28` assertion
  (`:1073-1075`) cannot see it.  It is a real cycle and nobody counts it.

Net: the first chain sample lands at `t0 + d + 41`, i.e. at offset `d + 9` in the
first PID cell, for a detection lag `d` of 0..7 — **offset 9..16**, which is
exactly the measured 9.0..15.9.  The design intends offset ≈ 8.5.  It is four
cycles late, a quarter of a bit cell.

Consequences, all measured (§2.3's table): 7 of 256 (entry, phase) combinations
fail to decode; the guaranteed device-clock tolerance is **-0.05 % .. +0.85 %**,
i.e. a device clock 0.1 % *slow* breaks the longest packet, while the same engine
would survive 0.85 % fast.  Changing `.rept 20` to `.rept 16` in `.Lk_edge` moves
the band to 5.0..11.9, removes all 7 failures, and makes the tolerance
-0.55 % .. +0.40 %.  (Stated as a finding, not applied — this document changes no
engine.)

### 4.2 "±1.5 % is what the receiver must tolerate" — repeated, and wrong

`doc/py32/ENGINE16_CATALOG.md:894` ("one sample per bit is not enough (LS is
±1.5 %, so a fixed…"), `:910` ("receive clock has no resynchronisation path.  LS
is ±1.5 % (USB 2.0 §7.1.11)"), `ENGINE16_RESULTS.md:93` ("±1.5 % low-speed rate
tolerance slips ±1.44 bit times over a 96-bit packet"), `engine16_native.md:109`
("One sample per bit is not enough.  LS is 1.5 Mbit/s ±1.5 % (USB 2.0 §7.1.11)")
and `ENGINE16_REVIEW.md:504` all treat §7.1.11's ±1.5 % as a property of the
incoming stream.  §3.1 above shows it is a property of *our transmitter*; the
incoming stream is specified at ±0.05 % (host/hub transmit accuracy) and is
budgeted at 0.26 %/bit in Table 7-5.

The conclusions those passages draw are mostly still right, but for a different
reason and with a different number.  "One sample per bit is not enough" is true
because *our own* clock drifts over 90 unresynchronised cells, not because the
host's does; and the drift that matters is ours at ±0.5 %, not a combined ±1.5 %.
`ENGINE16_REVIEW.md:510` already reaches the right conclusion by another route
("requirement is ±0.28 % rather than ±1.5 %, stated in the engine's own note") —
that is the sentence the rest should be reconciled to, and §3.4 gives it a
derivation and a measurement.

### 4.3 The sign of the clock-error sweep is documented backwards

`tools/engine16_rx_bus.py:212-213`:

> "Positive ppm = the device's cell is longer than the host's bit, i.e. the device
> clock is SLOW."

`Bus.period` is *device cycles per bus bit* (`level_at` divides an emulated device
cycle count by it, `:139-148`).  More device cycles per bus bit means the device
clock is **fast**.  Positive ppm is a fast device clock, and a fast device clock
makes the sample offset drift *down* through the cell (the chain advances 16
cycles per cell while the cell is 16(1+ε) cycles long).

That matters for reading the result.  With the band jammed against the *top* of
the cell (9.0..15.9) there is almost no room to drift *up* and plenty to drift
down — which is why the measured tolerance is `-0.20 % .. +1.00 %`, asymmetric in
favour of positive ppm.  The commit message of `b729cff` therefore also has it
backwards where it says "A device clock that is FAST pushes the sample past the
cell boundary almost immediately"; it is the **slow** clock (negative ppm) that
has only 0.20 % of room, for exactly the reason the same paragraph correctly
identifies — the distribution is not centred, it is jammed against the end of the
cell.  The remedy named there (centre the distribution) is the right one and is
confirmed by measurement in §2.3.

### 4.4 `PRIOR_ART.md` D-9 / L-4: a third-party number cited as a spec number

Covered in §3.3(2).  `PRIOR_ART.md:131` and `:250` attribute "260 ns" to
"USB 2.0 §7.1.9/§7.1.14"; neither section contains it.  The citation should read
"SPRAAT5A LS14, a roll-up of §7.1.14.1's 200 ns EOP-sense allowance and ±45 ns
TLDHJ1" — and, per §3.3(3), the derived requirement ("offset ≥ 14/32", `PLAN.md:303`
F5's "must") does not apply to `engine16_merged.S` at all.

### 4.5 `PRIOR_ART.md` L-9's UNVERIFIED clause is now verified

`PRIOR_ART.md:255` records the low-speed keepalive as "§7.1.7 area (exact clause
**UNVERIFIED**, Sweep 6 §4)".  It is **§11.8.4.1** ("Low-speed Keep-alive"),
supported by §7.1.7.6 and Table 7-2 note 4.  The clause also gives the servo of
`PRIOR_ART.md:64` (D-5) its guarantee: at least one low-speed EOP per frame in
which an SOF is received, generated at the beginning of the frame.

### 4.6 What is right, and worth saying so

* The 260 ns → 6.24 cycles rescaling in `engine16_grainuum.md:457-462` is correct
  arithmetic, and the "same fraction of the cell" observation is correct.
* `PLAN.md:433`'s engine sampling margin of "≈0.44 %" is very close to the
  measured guaranteed tolerance of the *centred* engine (±0.475 %) — the plan's
  number was right, and the engine as committed does not achieve it (§4.1).
* `engine16_merged.S:919-925`'s reasoning about why the poll loop's exit must be a
  fall-through, and about V-USB's 7..9-cycle loops being worse, is sound as far as
  it goes.  What it misses is that V-USB does not use a loop at all for the
  phase-defining edge (`usbdrvasm12.inc:57-70`) — the comparison was made against
  the wrong V-USB code.
* `PRIOR_ART.md:64` (D-5)'s keepalive servo is the mechanism micronucleus arrived
  at independently twice (§2.5), and it is the only instrument on this part with
  enough resolution to trim to ±0.5 % (§2.6).

## 5. If only one thing is done

In measured order of value per cycle spent:

1. **Retune `.Lk_edge`'s delay** (20 → 16 nops, zero bytes, zero cycles in the
   timed path): band 9.0..15.9 → 5.0..11.9, 7 decode failures → 0, guaranteed
   clock tolerance -0.05 %/+0.85 % → -0.55 %/+0.40 %.  Fixes §4.1.
2. **Unroll `.Lwait_k`** (+36 bytes, and it needs the EOP stubs relocated to keep
   the ±254-byte backward reach of `CELL 0..3`): band width 6.9 → 4.0 cycles,
   guaranteed tolerance span 0.95 % → 1.10 %.  §2.3.
3. **Move prologue work after the lock** (V-USB's trick, using the 11 spare cycles
   in cells 0..6): buys entry-latency headroom on `G6`, which currently has one
   cycle of margin at its upper edge.  §2.4.
4. **A per-byte bang-bang re-time** (§2.1/§2.2): the only mechanism that changes
   the *class* of the problem, from ±0.5 % to a few percent.  Needs cell 7
   re-balanced; do not start it before 1-3.

## Appendix. Reproducing the measurements

Everything measured here comes from `tools/engine16_rx_bus.py` (Unicorn, GPIO
mapped as MMIO, IDR driven from a synthesized low-speed waveform indexed by an
emulated cycle count).  The baseline numbers are what the tool prints unmodified:

```
$ python3 tools/engine16_rx_bus.py
G6  entry latency that still decodes: 6..37 cycles = 0.38..2.31 bit times
G7  locked sample offset in the 16-cycle cell, ...
      min 9.0  max 15.9
clock error that still decodes an 8-byte DATA0: -0.20% .. +1.00%
```

The variant numbers were produced by copying `doc/py32/engine16_merged.S` to a
scratch directory, editing the copy (`.Lk_edge`'s `.rept 20`, and for the unroll
the `.Lwait_k` block), and calling `prerender_check.build()` on the copy —
`engine16_rx_bus.make()` with a different source path.  The three quantities are:

* **band** — `engine16_rx_bus.sweep_offset(elf, syms, range(lo, hi+1), 8)` over
  every usable entry latency × 8 sub-cycle packet phases, taking `min`/`max` of
  the histogram and its failure count;
* **guaranteed clock tolerance** — the ppm range for which *every* one of
  {3 entry latencies} × {4 sub-cycle phases} decodes the 8-byte DATA0 byte for
  byte, stepped at 500 ppm.  This is stricter than the tool's own `sweep_ppm`,
  which fixes one entry latency and one phase and therefore reports a wider,
  phase-lucky number (`-0.20 % .. +1.00 %` at entry 16, phase 0);
* **dribble** — `Bus(..., dribble=N)` holds the last driven level for N device
  cycles into the first SE0 cell (`engine16_rx_bus.py:143-147`).

The instruction trace in §4.1 is a `UC_HOOK_CODE` hook on the same `Bus` object
printing `(b.cyc, address)` against the disassembly from
`prerender_check.disassemble()`; the per-cell spare-cycle table in §2.1 counts
`nop` mnemonics between consecutive `usb_rx_cellN` symbols in the linked image.
