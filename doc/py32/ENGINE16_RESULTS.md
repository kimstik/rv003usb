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

## DESCENT (retry, direct descent) — REAL DEFECT FOUND; CLAIM DOES NOT SURVIVE THE FIX

Files: `engine16_descent.S` (222 lines), `engine16_descent.md` (494). Assembles
rc=0. RAM-resident, and it uses the **correct** PY32 GPIO base 0x50000400 —
unlike VUSB, which defaults to the STM32 base.

**Claimed:** exactly 16 cycles on every ledgered path.

**The data-0 path does not do what the ledger says.** Confirmed in raw
`objdump`, not only through the annotator: `byte0_bit1` ends at 0x48 with a
`nop` and **no branch**, so the data-0 path falls straight through into
`byte0_bit1_one` at 0x4a, which executes `lsrs r4, r4, #1` a second time and
then `orrs r4, r2`. Every received 0 bit would be shifted twice and have bit 7
set. The path also does not end at 16 cycles — it runs on into the next handler.

The cause is visible in the source and is a copy-divergence slip rather than a
design error. There are two cell macros. `RX_BIT_LAST` ends its data-0 path
correctly with `b \next`. `RX_BIT_PLAIN` — used for bits 1-7 of every byte —
does not; it has `.rept 8 / nop / .endr` where the branch should be, and then
runs into the `_one` label. The author wrote the pattern correctly once and
dropped the branch in the other copy.

**The fix costs the claim.** Restoring `b \next` means the data-0 path becomes
8 real cycles + N nops + a taken branch at 2-3. To reach 16 that is 5 nops,
giving **15..16** — the same branch-ambiguity exposure GRAINUUM carries, not the
exactness DESCENT claimed. So corrected, this entry sits with GRAINUUM rather
than with VUSB and CLEANSHEET.

None of that invalidates its analysis, which is where its real value is (below).
It is a one-line repair and the design survives it.

**The structural finding it was asked for, and delivered:** the original's
per-bit `BITCOUNT` decrement plus a branch to a shared `is_end_of_byte` handler
**cannot survive at 16 cycles for an arithmetic reason, not a stylistic one.** A
shared handler inherits only what is left of its caller's budget — 1-4 cycles by
the time any predecessor path reaches it — while a byte store needs at least 7.
The only resolution is to make "which byte" a compile-time fact via per-byte
unroll. That converges independently on GRAINUUM's mechanism, but derived from
the reference engine's own arithmetic rather than designed fresh. Two entrants
reaching the same structure from opposite directions is the strongest evidence
the competition has produced about what the shape of this engine must be.

**The mechanism it found load-bearing in the original:** the combined D+/D-
sample-and-mask (`USB_DMASK`, one `ands` covering both pins). It looks like a
convenience but is how the receiver gets SE0 detection *free* from the same read
that performs the NRZI transition test — a valid bit always leaves exactly one
pin high, and only SE0 drives both low. The entrant nearly cut it to free a
register for mask arithmetic, traced the alternative by hand, and found that
doing so only moves the cost onto the already-tightest path. That is exactly the
archaeology the control experiment existed to produce.

## BALANCE (own design) — HONEST MISS ON CYCLES, GENUINE MECHANISM

Files: `engine16_balance.S` (124 lines), `engine16_balance.md` (486). Assembles
rc=0. RAM-resident, correct PY32 base 0x50000400.

**Self-reported as not fitting: 17-18 cycles on an ordinary bit, 34 at the byte
boundary. Checked, and the self-report is exactly right** — 15 single-cycle
instructions plus a taken loop-back branch at 2-3 gives 17..18. It declared the
miss rather than padding a ledger to hide it, which is worth more than a
borderline pass would have been.

**Its mechanism is real and I verified the arithmetic.** One transition mask in
r6, computed with `lsls #25` / `asrs #31`, then consumed **three** ways:

```
eors r6, r2      NRZI delta against previous sample
eors r2, r6      prev := new
bics r4, r6      bit-stuffing counter update, driven by the same mask
lsrs r6, r6, #31 mask -> carry
adcs r3, r3      byte insertion via the carry chain
```

Full branchlessness on the value decode for **4 extra instructions** over a
minimal branch-based core. That directly contradicts GRAINUUM's own recorded
finding that a comparable branchless treatment costs 9-12 extra instructions and
is not worth it — and the listing above is the evidence, so this is a
disagreement settled by code rather than by opinion.

**Why it misses, and the number that matters for the merge.** The cost is not in
the core, it is in the packaging: `subs r5,#1` + `beq byte_boundary` + `b bit_cell`
is 4-5 cycles of per-bit loop overhead. Those are exactly the instructions that
per-byte unrolling deletes. BALANCE computed, without building it, that its core
placed in an unrolled structure would leave **3 cycles of slack per slot**. I
checked: removing those three instructions from 17..18 gives 13, and 16-13 = 3.
The claim is consistent.

So this entry is a core without a chassis, and it says so.

## The convergent finding — four entrants, three derivations

Per-byte unrolling is not one entrant's idea. It arrived independently from
three directions, and a fourth computed its value without building it:

* GRAINUUM designed it to make the buffer bound structural.
* DESCENT derived it from the reference engine's own arithmetic — a shared
  byte-boundary handler inherits only 1-4 cycles of its caller's remaining
  budget while a byte store needs 7, so "which byte" must become a compile-time
  fact. This is a proof, not a preference.
* VUSB arrived at it from the AVR school, as eight cells `usb_rx_cell0..7`.
* BALANCE quantified what its own core would gain from it.

Meanwhile CLEANSHEET established the limit of the idea: unrolling *a whole
packet* does not help, because a short conditional branch cannot reach far
enough to keep an SE0 escape in range. So the right granularity is one byte, and
that is now established from four directions rather than asserted.

## Scoreboard so far

| entrant | state | bit cell | branch-ambiguity exposure |
|---|---|---|---|
| VUSB | engine only, no note | **exactly 16** | none on the data path |
| CLEANSHEET | complete | 16 for 7 bits in 8 | only at the byte boundary |
| GRAINUUM | complete | 15..16 / 14..16 | every path, 1-2 cycles |
| NATIVE | complete | n/a — peripheral, mostly negative | n/a |
| DESCENT | complete, one-line defect | 15..16 once fixed | every path, after the fix |
| BALANCE | complete, honest miss | 17..18 | 1 branch, in the loop overhead |

DESCENT (compression of the existing engine), VUSB (AVR school), GRAINUUM (ARM
school, owns entry/phase/boundaries), BALANCE (own design, may look at the
others but may not repeat them).
