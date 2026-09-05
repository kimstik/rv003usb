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

## GRAINUUM (ARM Cortex-M school) — CLAIM TRUE AS STATED, BUT READ THE SPREAD

Files: `engine16_grainuum.S` (394 lines), `engine16_grainuum.md` (592).
Assembles rc=0. **RAM-resident** — the opposite placement choice from CLEANSHEET,
which makes the pair a useful controlled comparison.

**Claimed:** 16 cycles worst case on every ledgered path, under a conservative
assumption that every ambiguous taken branch costs 3.

**Checked, and it is true as stated.** The block totals my annotator prints
(18..24 for a slot) do *not* refute it — a bit cell here is a path that branches
out and back, and the tool deliberately does not resolve control flow. Tracing
the paths by hand:

*Data-0 (fall-through):* `ldr`+`ands`+`eors` 3, `beq` not taken 1, `eors` 1,
`beq` not taken 1, `lsrs`+`movs` 2, 5x`nop` 5, `b` taken 2-3 → **15..16**.

*Data-1 (branch out and back):* 3 + `beq` taken 2-3 + `lsrs`+`adds`+`subs` 3 +
`beq` not taken 1 + 3x`nop` 3 + `b` taken 2-3 → **14..16**.

So worst case is 16 on both, as claimed. But **every path here carries the
branch ambiguity**, with a spread of 1 cycle on data-0 and 2 on data-1, whereas
CLEANSHEET's ordinary slot is *exactly* 16 with no ambiguous branch in it at
all, and pays the exposure only once per byte.

On spec §6 criterion 3 — robustness to the 2-vs-3 ambiguity — that is a clear
difference between the two entries, and it is the kind of thing that only shows
up when the paths are traced rather than the claims compared. It does not make
GRAINUUM wrong; it means its exactness is contingent on a number nobody has
measured yet.

**Project-level consequence, common to both entries:** the branch cost is not
run-to-run randomness, it is an unknown constant of the part. Once measured on
silicon, either design is made exact by inserting one `nop`. Both entrants
independently arrived at that same conclusion and both flagged it rather than
hiding it. **This makes "measure the taken-branch cost" the single highest-value
bench item in the whole project** — it is now on the critical path for every
software engine, not a detail.

**Mechanism it offers:** the static per-byte unroll, which turns the receive
buffer bound into a structural property — no instruction in the object can
address the buffer out of range, because every exit of the last unrolled block
goes to an abort handler and never back into the engine. That closes
`DEFECTS_VERIFIED.md` D-2 with no runtime check and therefore no cycles, which
is the difficulty that made D-2 a design task rather than a one-liner. It is a
statement about code layout rather than about how a bit is sampled, so it
composes with any other entrant's inner loop.

**No contradiction with CLEANSHEET's negative result**, despite appearances:
CLEANSHEET showed that unrolling *the whole packet* fails to remove recurring
taken branches, because a short conditional branch cannot reach far enough.
GRAINUUM unrolls *one byte*, eight slots, and keeps a loop around it. Different
claims, both true.

**Bug found and fixed in flight, worth recording:** a stray line-continuation
backslash inside an ASCII-art comment silently swallowed a `nop` through
C-preprocessor line splicing, leaving one path a cycle short. It was caught by
`objdump`, not by reading. Anything in this project that runs a `.S` through the
preprocessor is exposed to this.

**Honest limitation the entrant declares:** the entry and phase-lock delay
constants are first-order estimates, not bench-verified — the same status
`PRIOR_ART.md` records for the reference engine's own constants.

## VUSB (AVR school) — DIED, BUT ITS ENGINE SURVIVED AND VERIFIES

Killed by a model session limit before writing its design note. `engine16_vusb.S`
(659 lines) was committed in its worktree and is intact. No `.md`, so the
reasoning behind it is lost — only the code speaks.

**Its own commit message claimed** "every bit cell measures exactly 16 cycles".
**Checked, and it holds** — in the RAM column. The cell:

```
ldr  r2, [r7, #IDR]    1    IOPORT
ands r2, r6            1
beq  usb_rx_eopN       1    not taken on the common path; taken only to leave
lsrs r2, r2, #3        1
adcs r5, r5            1    carry-chain capture, same idea CLEANSHEET found
lsls r1, r1, #1        1
mov  r2, fp            1
orrs r1, r2            1
ldrh r1, [r4, r1]      2    <- register-offset TABLE LOOKUP, RAM
uxtb r2, r1            1
mov  fp, r2            1
nop x4                 4
                      ==16
```

**This is the strongest result so far on spec §6 criterion 3.** The cell is
*exactly* 16 with no ambiguous branch anywhere on the data path — the only `beq`
is not-taken on the common path and, when taken, exits the cell to EOP handling.
Where CLEANSHEET is exact for 7 bits in 8 and GRAINUUM carries a 1-2 cycle
spread on every path, this carries none at all.

The mechanism doing the work is `ldrh r1, [r4, r1]` — a **register-offset table
lookup**, feeding a state variable held in `fp`. That is the AVR school's
table-driven decode, re-derived on the one M0+ addressing mode that makes it
possible (`M0PLUS_ISA_FACTS.md`). It folds NRZI decode, the stuffing counter and
the byte assembly into one memory access, which is how a branch disappears
rather than being replaced by mask arithmetic. It also explains the unrolling:
eight cells, `usb_rx_cell0..7`, one per bit of the byte.

**Transliteration artifact, and exactly the hazard the owner named:** the file
defaults `USB_GPIO_BASE` to **0x48000000**, which is the STM32 GPIO base. On
PY32 it is 0x50000000 (`BUILD_FACTS.md` §7). It sits behind an `#ifndef` so it
is overridable, but the default is wrong for the part this competition targets,
and it is the kind of constant that would be corrected late and painfully. Worth
recording as evidence that the hazard is real, not hypothetical.

**What is missing and cannot be recovered:** how it reconciles unrolling with
bit stuffing — the hard part of this lineage, and the thing its brief singled
out. The table presumably carries it, but without the note the table's
construction has to be reverse-engineered from `.S` before the mechanism can be
reused. Its last recorded action was "update the model to the shift+sentinel
scheme and re-verify", so the committed file may predate a change it intended.

## DESCENT — TOTAL LOSS, AND THE CAUSE IS INSTRUCTIVE

Nothing on disk, no commits. It died on its **first turn**, not to a rate limit
but to `max_output_tokens`: it exceeded the 64000-token output ceiling in a
single response. It had said only "I'll start by getting the documents".

So this was not bad luck, it was a method failure: the agent attempted to emit a
very large artifact in one response instead of building it incrementally. Every
other entrant wrote its file in pieces and committed as it went, and every other
entrant that died still left something usable. The durability instruction in the
briefs said to commit early and often; it needs to also say to **write in
pieces**, because a single oversized write can fail before the first commit ever
happens.

## BALANCE — TOTAL LOSS

Imported the shared docs and the cycle tool, then died on a model session limit
before writing any engine. One commit, containing only the import.

## Scoreboard so far

| entrant | state | bit cell | branch-ambiguity exposure |
|---|---|---|---|
| VUSB | engine only, no note | **exactly 16** | none on the data path |
| CLEANSHEET | complete | 16 for 7 bits in 8 | only at the byte boundary |
| GRAINUUM | complete | 15..16 / 14..16 | every path, 1-2 cycles |
| NATIVE | complete | n/a — peripheral, mostly negative | n/a |
| DESCENT | lost | — | — |
| BALANCE | lost | — | — |

DESCENT (compression of the existing engine), VUSB (AVR school), GRAINUUM (ARM
school, owns entry/phase/boundaries), BALANCE (own design, may look at the
others but may not repeat them).
