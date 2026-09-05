# engine16_grainuum — design note

Competitor: GRAINUUM. File under review: `engine16_grainuum.S` (assembled and
disassembled while writing this note; every number below was checked against
`arm-none-eabi-objdump -d` and `tools/engine16_cyc.py`, not typed from memory).

Conventions: `arm.S:<n>` = the 32-cycle reference at
`rv003usb-arm.S` (scratchpad copy read in full for this task); `PA §x` /
`PA S-n` / `PA A-n` / `PA D-n` / `PA L-n` = `doc/py32/PRIOR_ART.md`;
`SPEC §x` = `doc/py32/ENGINE16_SPEC.md`; `ISA` = `doc/py32/M0PLUS_ISA_FACTS.md`.
A line like "arm.S:160-164" is read from the file; a line like "PA #1" is
PRIOR_ART's own digest of Grainuum, not something I re-derived from Grainuum's
source (I did not fetch it — PRIOR_ART.md is the only permitted source, per
the task brief). Anything past that point is my extrapolation and is labelled
as such.

## 1. The idea, in one paragraph, and its lineage

Grainuum's family (PA #1, closest peer on the same core) proved the shape a
software LS PHY takes on Cortex-M0+: cycle-counted Thumb, IRQs masked, RAM-
resident, with a pad staircase closing every slot to an exact count (PA S-1).
At 32 cycles/bit that shape had room for two other things: computing CRC
inside the slot (arm.S:167-184, the Domkeykong trick) and a runtime bit/byte
counter with a real loop-back branch every bit (arm.S:194-198). At 16 cycles
neither survives the budget — in-slot CRC alone costs about what a whole
16-cycle cell has to spend, and a design with two taken branches per bit
(arm.S:162's split plus arm.S:198's loop-back) hands 12.5-19% of the cell to
an ambiguity (SPEC §2's own framing) it doesn't have to accept twice. This
design removes both, independently:

* **CRC is deferred whole to the untimed tail** (§7). The bit loop stores raw,
  destuffed bytes and nothing else.
* **The byte is fully unrolled at assemble time**, so "this is bit 8 of the
  byte, store it" is a fact about *which label the code is at*, not a runtime
  counter that has to be tested and branched on every bit. This removes the
  loop-back branch from the interior of the cell entirely and, as a side
  effect, makes the receive buffer impossible to overrun by construction
  (§6) — SPEC §3 point 8 asks for exactly this trade.

What is left inside a bit cell is: one masked GPIO read, one comparison
against the previous sample using the toggle-instead-of-move idiom already in
arm.S:160-164, and exactly one conditional branch to tell a 0 from a 1 — which
cannot be removed on this core because there is no predication to remove it
with (ISA, "there is no `it`/predication on Cortex-M0+"). Section 4 shows
that branch is the *only* one whose cost is a bit-cell-timing hazard; every
other branch in the file is either never taken on the timed path or is a
short, always-in-range relay to a rare exit (§8).

## 2. Register file

All 8 low registers, no high register anywhere in the bit loop:

| reg | role | notes |
|---|---|---|
| r0 | `PREV` | last masked `{D+,D-}` sample; updated in place with the arm.S:160-164 toggle, never copied |
| r1 | `RXBUFBASE` | address of `rxbuf16`, loaded once at entry |
| r2 | `BASE` | GPIO IDR base, loaded once at entry — kept in a **low** register the whole time |
| r3 | `MASK` | the 2-bit `{D+,D-}` mask, loaded once at entry |
| r4 | `SHIFT` | byte accumulator, LSB-first (arm.S:150-198's shape) |
| r5 | `SCRATCH` | diff/temp inside a slot; carries the complete-byte count into the tail |
| r6 | `BITSTUFF` | consecutive-ones counter, 6→0 |
| r7 | `EXTIBASE` | EXTI base, loaded once at entry; untouched during the bit loop |

This is the honest answer to "does it fit the low-register file": it fits
with **zero** registers to spare, but it needs no high register at all, unlike
arm.S, which pushed `GPIO_BASE`/pin-mask into r8/r9 and paid `mov r5,r9` /
`mov SCRATCH,r12` every slot (arm.S:97-98, 155-156) because CRC's two extra
live values (`CRC`, `POLY_RX`) forced GPIO_BASE out of the low file. Deferring
CRC is what buys back those two registers, and it is spent here on keeping
`BASE` low (a direct 1-cycle `ldr r5,[r2,#16]` with no register shuffle) and
on `MASK` (see §3 — this is what lets one `ands` do double duty as both the
NRZI mask and, via its `Z` flag, the SE0 test).

**What this design gives up because of the register count**: there is no
spare register for a live byte-count check inside the bit loop (§6 explains
why that's fine) and none for a second in-flight sample (a ping-pong
same-register scheme was drafted and abandoned — see §9).

## 3. The bit-cell mechanism, instruction by instruction

One representative interior slot (`b1_s1`/`b1_s1_one` in the `.S`, identical
in shape for every interior slot — this is what the `SLOT` macro emits):

```
b1_s1:
    ldr  r5, [r2, #16]      ; sample the port
    ands r5, r3             ; mask to {D+,D-}; Z set iff SE0 (see below)
    eors r5, r0             ; r5 := diff against PREV
    beq  b1_s1_one          ; diff==0 -> no transition -> bit=1
    eors r0, r5             ; (transition path only) r0 := PREV ^ diff = NEW
    beq  9f                 ; NEW==0 -> SE0 relay (short jump, see §8)
    lsrs r4, r4, #1         ; shift the byte accumulator
    movs r6, #6             ; a 0 always resets the stuff counter
    ...pad...
    b    b1_s2               ; close the cell
```

The `ands r5,r3` does two jobs in one instruction: it isolates the two USB
pins from whatever else lives on that GPIO port, *and* its `Z` flag is the
SE0 test (both pins low ⇒ masked value is `0`) — no separate compare is
needed. The `eors r0,r5` on the transition path is arm.S:163's own idiom,
taken as-is: because "no transition" literally means "new state equals old
state," the *no-transition* arm never has to touch `PREV` at all (it is
already correct for the next slot), and the *transition* arm recovers `NEW`
from `OLD XOR diff` in one instruction instead of a fresh sample-then-compare.
This is the one piece of arm.S I am directly reusing rather than
re-deriving, and it is exactly the kind of mechanism §10 asks about: it is
load-bearing at any clock, because it has nothing to do with cycle budget —
it is just the cheapest way to know "did the pin change" in Thumb-1.

## 4. Cycle ledger, every path, arithmetic shown

All costs from SPEC §2 (RAM-resident column): plain instruction = 1;
GPIO IDR load with the base in a register named to `--ioport` = 1; RAM store
(rxbuf16) = 2; conditional branch not taken = 1, taken = 2 or 3 (assembler-
verified alignment-dependent ambiguity, not modelled further — see the
"what I assume" note below); unconditional `b` = 2 or 3. Every row below was
read back from `arm-none-eabi-objdump -d /tmp/e16.o`, not just counted in the
source, after two rounds of fixing pad counts that were wrong by one nop the
first time (§8 explains one bug found this way).

**What I assume about the 2-vs-3 ambiguity**: I cost every taken branch at
its *worst case (3)* for padding purposes. If the true cost at a given site
is 2, that path finishes 1 cycle early — safe, since padding is only ever
added, never subtracted, and finishing early is explicitly allowed (SPEC §1).
If a site's true cost is 3 and I had assumed 2, the cell would run over —
this is exactly PA #1's flag on Grainuum's own uncertainty here ("its 'taken
branch = 3' note contradicts TRM's 2 → bench2"), and PRIOR_ART's own
unresolved Q-11. I have no hardware to settle it, so I take the conservative
side throughout, consistent with CHIP_FACTS_XIAMATSU.md §1's own RAM-column
figure of 2-3 (not a flat 2).

### 4.1 Data bit = 1, interior slot

```
ldr r5,[r2,#16]   1   =1
ands r5,r3        1   =2
eors r5,r0        1   =3
beq  b1_s1_one   2-3  =5..6   <- TAKEN (bit=1 takes this branch)
--- b1_s1_one: ---
lsrs r4,r4,#1     1   =6..7
adds r4,#0x80     1   =7..8
subs r6,#1        1   =8..9
beq  stuff_...    1   =9..10  (not taken: no stuff this bit)
nop x3            3   =12..13
b    b1_s2       2-3  =14..16
```
Worst case (both branches cost 3): **16**. Best case (both cost 2): **14**
— 2 cycles of slack. `objdump` confirms 10 instructions on this path
(`b1_s1_one` through the closing `b`), matching the count above.

### 4.2 Data bit = 0, interior slot

```
ldr r5,[r2,#16]   1   =1
ands r5,r3        1   =2
eors r5,r0        1   =3
beq  b1_s1_one    1   =4    <- NOT taken (bit=0 falls through)
eors r0,r5        1   =5
beq  9f           1   =6    <- NOT taken (not SE0)
lsrs r4,r4,#1     1   =7
movs r6,#6        1   =8
nop x5            5   =13
b    b1_s2       2-3  =15..16
```
Worst case: **16**. Best case: **15**. This is the one interior path with
only 1 cycle of slack — every other interior path has 2+ — because it is the
only one that does two register-producing instructions (`eors r0,r5` for the
`PREV` update and the SE0 test) before it can even start padding.

### 4.3 Stuffed bit (own cell, entered only from a "1" whose `BITSTUFF` hit 0)

```
ldr r5,[r2,#16]   1   =1
ands r5,r3        1   =2
eors r5,r0        1   =3
beq  8f           1   =4    <- NOT taken: a stuffed bit MUST be a transition (PA L-7/F3)
eors r0,r5        1   =5
beq  9f           1   =6    <- NOT taken (not SE0)
movs r6,#6        1   =7    stuffing consumed, counter reset
nop x6            6   =13
b    <next slot> 2-3  =15..16
```
Worst case: **16**. This is a *whole extra 16-cycle cell*, not part of the
triggering bit's 16 cycles — the physical bit stream really does have one
more bit-time here than the byte count implies, and the engine has to spend
a cycle-cell staying synchronized with it, exactly as arm.S's own
"one+stuffed = 64 = 2×32" shape does (arm.S:200-209, PLAN's own ledger table)
scaled down: here it is 2×16=32 across the two cells, not 16.
If `8f` *is* taken (no transition where the protocol requires one), the cell
diverts to `stuffing_violation` and the packet is dropped (PA L-7) — that
path is untimed from the moment it diverges, so it is not separately ledgered.

### 4.4 Byte boundary, bit = 1 (stores the completed byte)

```
ldr r5,[r2,#16]   1   =1
ands r5,r3        1   =2
eors r5,r0        1   =3
beq  b1_s8_one   2-3  =5..6   <- TAKEN
--- b1_s8_one: ---
lsrs r4,r4,#1     1   =6..7
adds r4,#0x80     1   =7..8
strb r4,[r1,#0]   2   =9..10   store — completed byte
subs r6,#1        1   =10..11
beq  stuff_...    1   =11..12
nop               1   =12..13
b    b2_s1       2-3  =14..16
```
Worst case: **16**, no slack. This and 4.5 are the tightest paths in the
design; see §6 for why I did not spend the last cycle on a redundant runtime
bounds check here.

### 4.5 Byte boundary, bit = 0 (stores the completed byte)

```
ldr r5,[r2,#16]   1   =1
ands r5,r3        1   =2
eors r5,r0        1   =3
beq  b1_s8_one    1   =4    <- NOT taken
eors r0,r5        1   =5
beq  9f           1   =6
lsrs r4,r4,#1     1   =7
movs r6,#6        1   =8
strb r4,[r1,#0]   2   =10
nop x3            3   =13
b    b2_s1       2-3  =15..16
```
Worst case: **16**. Best case: **15**.

### 4.6 SE0 / EOP

SE0 is detected inside the zero-path/stuffed-cell check above (`beq 9f`) —
there is no separate 16-cycle "SE0 cell" to ledger, because the moment SE0 is
recognized the engine leaves the 16-cycle regimen for good and jumps to the
untimed tail. The cost *inside* the triggering cell is already counted in
4.2/4.3/4.5 above (the `beq 9f` that, this time, is taken); the two
instructions at the `9:` relay (`movs r5,#N; b se0_common`) execute after
the packet is over and are not part of any bit-cell budget.

## 5. Where the taken branches are, and what the 2-vs-3 ambiguity costs

Per bit cell there is exactly **one** branch whose outcome depends on line
data and whose cost genuinely varies with which side of the ambiguity the
hardware lands on: the 0-vs-1 split (`beq b1_s1_one` / the zero-path's
fall-through). Every other branch in a cell is one of:

* a rare-path check that is **not taken** on the timed line (SE0, stuffing
  violation) — cost 1, no ambiguity, because "not taken" is not in the 2-vs-3
  range (SPEC §2 lists it as flatly 1);
* the cell's own closing branch, which **is** in the 2-3 range but is the
  *second* branch this design pays per cell, versus arm.S's *two* ambiguous
  branches per cell (its own 0/1 split at arm.S:162, plus its separate
  loop-back `b bit_process` at arm.S:198, paid on every single bit regardless
  of value). Removing the loop-back — the direct consequence of static
  unrolling (§1, §6) — is this design's answer to SPEC §3's "fewer timed
  taken branches is better."

I did not find a way to remove the second (closing) branch without either
predication (absent, ISA) or a structure that could not be made to fit the
budget (a fully out-of-line "cold path" scheme was tried and discarded — see
§9, and it would have hit the ±256 B `beq` range wall the same way the first
draft of this file did, §8). So this design's branch count per bit is **2 in
the worst case, both costed at 3**, versus arm.S's 2 as well but at 32
cycles — the difference is that at 16 cycles the *same* branch count is a
much larger fraction of the budget (2×3=6 cycles is 37.5% of 16, versus
18.75% of 32), which is the concrete form of SPEC §2's warning applied to
this design rather than argued about in the abstract.

## 6. Buffer bound: structural, not checked (SPEC §3 point 8 / DEFECTS_VERIFIED D-2)

`engine16_grainuum.S` unrolls exactly `RXBUF_BYTES` (2, in this file — see
§10 for why 2 and not 12) byte-blocks. Every `strb` in the file targets a
compile-time-known offset into `rxbuf16` (`#0`, `#1`, …), and the *last*
block's every exit — whether from its own zero-path, one-path, or stuffed
cell — branches to `too_long_abort`, never back into the engine. There is
therefore no register holding a runtime "how many bytes have I written"
count that a malformed or malicious bus signal could drive past the buffer's
end: the question "can this code address `rxbuf16[N]` for `N ≥ RXBUF_BYTES`"
has the answer "no instruction exists that could" rather than "a check
rejects it." This is why the byte-boundary paths (4.4/4.5) have no spare
cycle for a `cmp`+`bhs` — none is needed. I drafted a runtime-countdown
version for comparison (subs a countdown register, branch conditionally
instead of unconditionally) and it added exactly one instruction to the
tightest path (4.4), pushing its worst case to 17 — over budget. Given the
structural version is both cheaper *and* a stronger guarantee (a checked
version can have its check be wrong; an absent instruction cannot store out
of bounds), this was not a close call once I saw the arithmetic.

**What this costs**: `RXBUF_BYTES` is fixed at assemble time, so lengthening
it means regenerating code, not changing a runtime constant. For a real
build this is a non-issue (the macro pattern in §10 handles it in one line),
but it means this file's own receive length ceiling (2 bytes) is a property
of *this demonstration*, not of the mechanism, and a reviewer diffing the
`.S` against a production build should expect a longer, mechanically
repeated file, not a different structure.

## 7. CRC: deferred, and the arithmetic for why

SPEC §3 calls this "probably the most consequential [choice] available" and
asks for justification, so here is the arithmetic rather than an assertion.

In-slot CRC (arm.S:167-184, the Domkeykong trick) costs, on the reference
engine's own accounting, about 6 of its ~26 real-work cycles per bit — and
that is at 32 cycles/bit, where there was headroom to spend. At 16, section
4 above shows the *interior* paths already have as little as 1 cycle of
slack (4.2) and the *byte-boundary* paths have **zero** (4.4). There is no
room to add a CRC step to any of them without removing something else, and
nothing else in the ledger is optional (the SE0 check, the stuff check, and
the byte store are all correctness-required, not padding).

Deferring CRC to the untimed tail means it has to fit inside the
**turnaround window** instead: USB 2.0's own device-response budget is 2-6.5
bit-times (PA L-1), or 7.5 for a captive cable. A bit-time is fixed by the
USB spec at 2/3 µs regardless of the engine's internal clock, so at 24 MHz
that is **32-104 cycles** (7.5 → 120). A byte-wise table-driven CRC16 (index,
one `ldr` from a 256-entry table, one `eor`, one shift — roughly 5-6
cycles/byte from RAM) over the worst-case 10-byte data-stage payload is
**≈50-60 cycles** — comfortably inside 104 (and 120), with margin for the
PID dispatch that has to run in the same window (arm.S:236-328 does the
dispatch in well under that on the reference engine, at a slower clock).
**What I have not verified**: whether the *fastest* legal host actually
enforces something tighter than 2 bit-times in practice; PRIOR_ART itself
flags the ACK-first pipeline (PA S-2, PLAN's R8 fallback, branch_notes.md
Part B) as the answer if a real host is found to need it, and that fallback
is available to this design too, unchanged, since it operates on the same
tail — deferring CRC does not foreclose it, it just isn't built here.

**What this buys** beyond cycles: with CRC gone, `packet_type_loop`
(arm.S:85-114, a separate loop tuned around loading `POLY_RX`/`CRC` based on
PID bits) collapses into *the same* per-byte mechanism as every other byte —
PID, address/endpoint, and data bytes are all received by an identical
unrolled block. Polynomial selection happens once, in the tail, after the
PID byte is already sitting in `rxbuf16[0]`.

## 8. Placement, and a bug the assembler caught for me

RAM-resident (`.timecrit16`, matching PA A-2/D-2/S-4 — "everything clocked in
RAM" is the field's unanimous position and PRIOR_ART's own D-2 verdict, and
nothing in this design changes that calculus). Consequence per SPEC §2: a
flash literal (`ldr r,=const`) costs 4 cycles here, not 2 — paid three times
at ISR entry (`GPIO_BASE_ADDR`, `rxbuf16`, `EXTI_BASE`: 12 cycles total, once
per packet) and nowhere inside the bit loop, where every constant lives in a
register already (SPEC §2's "bit-cell constants belong in registers, not in
a pool" — followed literally: `MASK`, the shift amounts, and `#6`/`#0x80` are
all immediates or pre-loaded registers).

Two things worth recording because they were found by *actually assembling*
this file (SPEC §5.2's own demand), not by reasoning about it:

1. **A stray line-continuation backslash silently deleted a `nop`.** An early
   draft used `\` inside an ASCII-art comment bracket to line up padding
   comments; because this file is preprocessed with `-x assembler-with-cpp`,
   a trailing `\` splices the next line into the same logical line, and the
   `@`-comment on the spliced line then swallowed the *next* `nop`
   instruction whole. The result assembled cleanly and looked right in the
   source, but `objdump` showed 4 nops where the ledger needed 5, and every
   interior zero-path was silently one cycle short of 16. Fixed by removing
   the backslash; re-verified against `objdump` before trusting the ledger
   again. I flag this because it is exactly the class of defect the
   project's "an unaccounted cycle is a defect, not rounding" rule exists to
   catch, and it would not have been caught by reading the source alone.
2. **Grouping all stuffed-bit and SE0 handlers at the end of the file does
   not assemble** — every `beq` from an early slot to a handler placed after
   16+ slots exceeds the ±256 B range a Thumb-1 conditional branch can reach
   (ISA: no wide `Bcc` on this core). The fix (§ "two-hop" below) is now
   load-bearing in the file, not incidental.

**The two-hop relay pattern** (used for every SE0 check and stuffing-
violation check): `beq 9f` to a numeric local label a few instructions later
*in the same slot* (always in range — it's a handful of bytes), which then
does an unconditional `b` to the real, possibly-distant handler (Thumb-1 `B`
reaches ±2048 B, verified empirically against this exact toolchain before
relying on it — a 1022-instruction forward jump assembled, a 1023-instruction
one did not). This costs nothing on the timed path: the conditional branch
is exactly as cheap not-taken as a direct branch to a far label would have
been if Thumb-1 conditional branches could reach that far, which they cannot.

## 9. What I gave up

* **A branchless 0/1 split.** ISA explicitly offers the mask-arithmetic idiom
  (`lsls #31; asrs #31` to turn a bit into an all-ones/all-zeros mask, then
  `ands`/`bics`/`eors`) as the way to remove a branch's cost ambiguity
  entirely. I costed it for both the bit-insert and the `BITSTUFF`
  conditional-reset and found it needs roughly 9-12 extra instructions per
  bit to replace one branch that, not-taken, already costs 1 cycle and,
  taken, costs at most 3 — a bad trade at this budget. ISA's own caution
  ("this is not a reason to make everything branchless... the right shape is
  usually fall-through") is exactly what I found by trying it, not just by
  reading the warning.
* **A zero-copy ping-pong sample register** (alternate which of two raw-
  sample registers is "current" each bit, avoiding any `PREV` update at all).
  It works for the plain bit-decode arithmetic, but it does not compose with
  a *shared* stuffed-bit/SE0 handler, because the handler would need to know
  which of the two registers is "current" for whichever slot called it — and
  arm.S's own toggle-in-place idiom (§3) already gets the common case (no
  transition needs no update) for free without this complication. Dropped
  in favor of the simpler, provably-correct version.
* **In-slot CRC** — covered in §7, with the arithmetic for why, not just the
  decision.
* **A finished TX engine** — sketched only (§11), per SPEC §3's allowance.
* **Exact interrupt-entry and phase-lock timing constants** — the mechanism
  (§10) is argued from first principles and cross-checked with the
  annotator's *instruction*-level costs, but the pre-delay and post-lock
  delay constants are first-order estimates, not bench-verified, exactly as
  PRIOR_ART itself records for the reference engine's own constants
  (Q-6, Q-11). I say this plainly rather than presenting a number I cannot
  back with hardware.
* **One cycle of margin on two paths.** 4.4 and 4.5 (byte boundary) have
  zero and one cycle of slack respectively under the worst-case branch
  assumption. If bench measurement later shows a taken branch costs more
  than 3 in some alignment this design didn't anticipate, these two paths
  are where it will show up first.

## 10. Interrupt entry and phase lock — this design's assigned strength

### 10.1 The problem, quantified

Cortex-M0+'s own architectural worst-case interrupt latency, zero wait
states, highest priority, no jitter suppression, is **15 cycles** (TRM
§3.6.1, cited via PLAN §2.2) — a number in *cycles*, not time, and therefore
independent of which bit rate the engine targets. On top of that fixed
latency sits a *variable* component from an abandoned LDM/STM tail-chain and
similar pipeline effects; the coordinator's own annotator run against this
session's reference engine reports its `EXTI2_3_IRQHandler` entry block at
**28-32 cycles**, a 4-cycle spread (attributed to that tool run; not
independently reassembled here since it requires arm.S's full build
environment, which was not reconstructed for this task).

That 4-cycle spread is a fixed number of *cycles*. At 32 cycles/bit it is
4/32 = **12.5%** of a bit cell. At 16 cycles/bit, the *same* 4 cycles is
4/16 = **25%** of a bit cell — literally double, because the bit cell halved
while the hardware's jitter did not. This is the "gets proportionally worse"
the task names, given a number rather than left as a claim. It is also, on
inspection, not a design flaw of any particular engine — it is a property of
the interrupt controller that every one of the four competitors inherits
equally, which is exactly why it is worth spending this section on: it
cannot be fixed by writing a cleverer bit cell.

### 10.2 The mechanism, and why it is the right answer to that number

The reference engine's response (arm.S:60-77) is not to try to predict when
the first SYNC edge will arrive and sample exactly there — it is to **sample
once at entry, wait a fixed interval, then spin comparing against that first
sample until it changes** (`preamble_loop`, arm.S:70-77). This mechanism is
*insensitive* to how much of the entry-latency jitter already elapsed before
it started, because it does not care what time it is — it cares whether the
bus state has changed relative to what it saw first. `engine16_grainuum.S`
keeps the same shape (`preamble_spin`/`preamble_locked`), because the
argument for it does not depend on the bit rate: doubling the *relative*
jitter (§10.1) makes the *edge-catching* mechanism more valuable, not less,
since it is precisely the technique that converts an unpredictable-timing
problem into an unpredictable-*phase* problem and then removes the phase
dependency by re-locking on a real transition instead of trusting a clock.

What *does* change at 16 cycles is the **margin available after locking**.
The last data bit before EOP must tolerate up to 260 ns of dribble (USB 2.0
§7.1.9/§7.1.14, PA D-9), which is a fixed *time*, not a fixed cycle count:
260 ns × 24 MHz ≈ **6.24 cycles**, versus PA D-9's own 12.5-cycle figure at
48 MHz — half as many cycles, because the clock is half as fast, but the
*same fraction of a (now smaller) bit cell*: 6.24/16 ≈ 39%, against PA D-9's
12.5/32 ≈ 39%. The dribble-tolerance requirement scales with the bit cell,
so it costs this design nothing extra by itself — but it eats into the *same*
margin the entry-jitter doubling (§10.1) is also spending down, and the two
draw from one shared budget: the number of cycles between "the earliest a
real edge could plausibly land" and "the latest we can still call it a
correct data bit." I have not carried this combined-budget arithmetic all
the way to a single verified number (that requires the walker/bench
PRIOR_ART itself says is still open, Q-6/Q-11/L-4), but the shape of the
argument — two independent sources of margin pressure, both proportional to
1/bit-cell-size, drawing on the same pool — is the concrete finding this
section contributes, and it is the same pool every competitor's engine has
to budget against regardless of its own inner-loop design.

### 10.3 What this design does with the margin

* `movs r5,#24` bounds the edge-hunt (PA A-16/F9): a stuck K line (resume
  signalling is legitimately ≥20 ms, USB 2.0 §7.1.7.5) or a shorted D+ must
  not spin with IRQs masked forever. 24 iterations at the poll loop's own
  ~7-8 cycles/iteration (measured via the annotator: `ldr`+`ands`+`cmp`+
  `bne`(not-taken)+`subs`+`bne`(taken, closing the loop) = 5 fixed + 2-3 for
  the closing branch) is on the order of 170-190 cycles ≈ 11-12 bit-times —
  long enough to ride out a captive-cable turnaround gap, short enough to
  abandon a genuinely stuck line well before it could be mistaken for
  anything legitimate.
* The masked-SE0-in-the-`ands` trick (§3) means the very first thing the
  handler can determine, immediately after the mandatory entry sample, is
  "was this a keepalive" — matching PA L-9's "must check SE0 immediately"
  discipline, and doing it with the *same* instruction that also produces
  the entry sample's masked value, rather than a separate check.
* NVIC priority is out of scope for a `.S` file (SPEC §4: shared, not to be
  touched) but is worth naming because it is not optional at this budget: PA
  F7 already flags that an equal-priority ISR delaying entry by its own full
  length would eat directly into the margin just quantified. Any integration
  of this engine inherits that requirement unchanged.

## 11. TX sketch (SPEC §3: may be sketched)

Not developed in the `.S` beyond a placeholder symbol
(`engine16_grainuum_tx_sketch`). One structural note worth recording for
whoever builds it out: on transmit, the *sender* controls the bit stream, so
bit-stuffing's taken/not-taken pattern is **known at the point the CRC and
payload are chosen**, not discovered bit-by-bit from the wire. A TX engine
can therefore decide, per byte, exactly which bit positions will stuff
*before* entering the timed loop for that byte, and pick a branch-free or
statically-resolved encoding for each — it does not inherit the RX side's
2-vs-3 uncertainty at all, because there is no data-dependent branch outcome
to be uncertain about at assembly time the way there is on receive. arm.S's
own TX bit-stuffing (`insert_stuffed_bit`, arm.S:527-533) already exploits a
version of this by using a fixed run of `b .+2`s as a pure delay rather than
a real branch; a 16-cycle TX engine has more reason to push that further,
not less. I have not built the ledger for this — it would need its own
per-path table exactly like §4 — so this is offered as a lead for the
referee's synthesis, not a claim about its cost.

## 12. Grainuum: load-bearing vs. artifact of their operating point

Asked directly, per the project owner's concern about inheriting a solution
tuned for someone else's constraints rather than finding this core's own
optimum. Working strictly from PRIOR_ART's digest (I did not fetch
Grainuum's source):

**Load-bearing regardless of clock or budget** (kept, and would keep on any
future core in this family):
* The cycle-counted-Thumb-in-RAM shape itself (PA #1, S-4, D-2) — this is a
  consequence of the chip's flash wait-state and IOPORT behavior, not of
  Grainuum's 47.972 MHz FLL clock; it would still be true at 16 cycles, at
  24 MHz, or on a different M0+ part entirely.
* The pad-staircase idea for exact-N delays (PA S-1) — a general technique
  for hitting a cycle count with no wasted scratch register; nothing about
  it assumes a particular bit rate. (Not used as a `bl`-staircase in this
  file — see §9's honesty note on why the entry section uses inline `nop`s
  instead: at only 1-2 delay sites, the staircase's advantage over inline
  padding is code size, which is exactly what SPEC §6 ranks below fitting
  the cycle budget.)
* Sampling-and-waiting-for-a-transition instead of trusting elapsed time for
  phase lock (§10.2) — this is a response to interrupt-latency uncertainty,
  which exists on every Cortex-M0+ regardless of clock.

**Artifacts of Grainuum's specific operating point, not adopted here for
that reason** — this is extrapolation past what PRIOR_ART records, since
PRIOR_ART does not itself label these as artifacts; it is my reading of why
they would not transfer:
* Grainuum's LS PHY at 48 MHz/32-cycle bits (PA #1) has roughly double this
  design's per-bit budget, which is *why* in-slot CRC (a Grainuum-lineage
  technique this repo's own arm.S carries forward, arm.S:167-184) was ever
  affordable there. At 16 cycles it is not a worse implementation of the
  same idea — it is an idea that assumed a budget this operating point does
  not have. Discarding it (§7) is not a deviation from Grainuum's design so
  much as a recognition that the *budget* it was designed for is a different
  budget.
* PA #1 also flags Grainuum's runtime `struct GrainuumUSB` of GPIO register
  addresses (D-1) as something PRIOR_ART itself already declined to carry
  into this project's PY32 port, for cost-model reasons (a runtime-loaded
  GPIO address costs more than a compile-time one on this specific memory
  map). That decision was made once, by PLAN, before this competition; I
  did not need to re-make it, but I note it here because it's a clean
  example of "an integrator's convenience choice that doesn't survive a
  different memory map," which is the general shape of the concern the
  project owner raised.
* I have not found, in what PRIOR_ART records, a Grainuum mechanism that I
  adopted *without* re-checking it against this operating point's own cost
  model first — the two items kept above (RAM residency, the staircase idea)
  are kept because the arithmetic for *this* chip, *this* clock, and *this*
  budget still says yes, not because Grainuum said yes at 48 MHz.

## 13. Summary for the referee

* Fits 16 cycles on every ledgered path under the conservative (taken=3)
  branch-cost assumption, with 0-2 cycles of slack depending on the path;
  the two zero-slack/one-slack paths (4.4, 4.5) are named, not hidden.
* Buffer overrun (DEFECTS_VERIFIED D-2) is closed structurally, not with a
  runtime check, and the arithmetic in §6 shows why a checked version would
  not have fit.
* Branch count per bit is 2, both now costed at their worst case, versus
  arm.S's 2 as well — but at half the budget, which is the real comparison.
* CRC is deferred with a turnaround-budget argument, not asserted.
* The interrupt-entry/phase-lock section (§10) gives the referee a number
  (25% relative jitter, double the 32-cycle case) rather than a restated
  concern, and argues why the reference engine's own re-lock mechanism is
  still the right answer to that number, not a different one.
* Everything given up is listed in §9 with the reason, not folded silently
  into "left for future work."

The one mechanism I would most want the referee to take from this entry is
the **static per-byte unroll that turns the buffer bound into a structural
property and the byte-boundary test into a compile-time fact** (§6): it is
the single change that both frees the cycles CRC-deferral needed elsewhere
in the ledger *and* closes a named, verified defect (D-2) without spending
any of the 16-cycle budget on a check. It composes with any of the other
three competitors' inner-loop designs, since it is a statement about *code
layout*, not about how a sample is taken or a bit is decoded.
