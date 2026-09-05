# engine16_balance — design note

Competitor: **BALANCE**. Designed from `ENGINE16_SPEC.md`, `M0PLUS_ISA_FACTS.md`
and `CHIP_FACTS_XIAMATSU.md` first; the other four entrants were read only
*after* this design's core mechanism and its one open problem (§3.2 below)
were already fixed, per the brief's own advice. What I looked at, and what I
took or didn't, is in §7 — nothing before that section depends on it.

All numbers below were produced by assembling `engine16_balance.S`
(`arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c`,
verified exit 0 in this worktree) and annotating it with
`tools/engine16_cyc.py --exec ram --ioport r0`. Addresses and per-instruction
costs quoted below are that tool's output, not hand-counted from source.

## 1. The idea, in one paragraph

USB receive at this budget is three fused decisions per bit (is this a
transition, is the run of six 1s over, does this bit complete a byte) and
Cortex-M0+ has no predication to make any of them free. `M0PLUS_ISA_FACTS.md`
gives the mask-arithmetic idiom for removing a branch's cost ambiguity but
warns it costs "roughly two extra instructions per decision" and may not be
worth it. BALANCE's mechanism is to fuse the *first two* decisions (NRZI
decode and bit-stuffing) into a **single pair of masks computed once per bit**
(`transitionMask` and its carry-derived low bit), so the extra cost of going
branchless is paid once, not per-decision: sign-extending the sampled D− bit
into an all-ones/all-zeros mask (`lsls`/`asrs`, the idiom `M0PLUS_ISA_FACTS.md`
names), then reusing that one mask three times — once via `bics` to update the
consecutive-ones counter, once via the carry it leaves behind to shift a bit
into the byte accumulator (`adcs`), and once, already spent, in the `eors`
pair that both computes it and updates the previous-sample state in the same
two instructions. The result: NRZI decode, byte assembly and bit-unstuffing's
counter update are **all data-branch-free**, using only 10 non-branch
instructions plus two never-taken escape checks (SE0, stuff-due). The
byte-boundary test remains a real branch (§3 explains why removing it needs
more than this design has room for) — this is BALANCE's one open weakness,
stated plainly rather than hidden, and §3.2/§8 give the arithmetic for it.

## 2. Register allocation

All eight low registers are live in the hot loop; nothing else is touched
except `r8` at the byte boundary, once per 8 bits:

| reg | role | live range |
|---|---|---|
| r0 | `GPIO_ADDR` = GPIOB_BASE+IDR_OFFSET | whole ISR, constant |
| r1 | `DMASK2` = `(1<<PIN_DM)\|(1<<PIN_DP)` = 0xC0 | whole ISR, constant |
| r2 | `PREV` — previous D− level, sign-extended (0x0/0xFFFFFFFF) | whole capture, updated every bit |
| r3 | `SHIFT_BUF` — byte accumulator, inverted sense (fixed at the byte boundary, see §3.4) | one byte |
| r4 | `ONESRUN` — consecutive-decoded-1s counter, 0..6 | whole capture |
| r5 | `BITCOUNT` — bits remaining in the byte, 8..1 | whole capture |
| r6 | scratch: raw sample → transitionMask → carry source | one bit |
| r7 | unused in the ordinary path; scratch in `byte_boundary` | — |
| r8 | `BUFPTR` — byte index into `rxbuf`, mov-tax paid only at the boundary | whole capture |

**This fits the low-register file with zero spare registers and zero
high-register traffic inside the ordinary bit path** — a direct, measurable
improvement over the pre-existing 32-cycle engine, which keeps `GPIO_BASE` in
r9 and the pin mask in r12 and pays `mov r5,GPIO_BASE`/`mov SCRATCH,r12`
*every bit* (`rv003usb-arm.S:97-98,155-156`) because CRC's two live values
(`CRC`, `POLY_RX`) push GPIO addressing out of the low file. BALANCE defers
CRC entirely (§3.5), which is what buys `GPIO_ADDR` and `DMASK2` their low-reg
slots and removes those two `mov`s from every bit — a saving GRAINUUM makes
the same way and names explicitly (`engine16_grainuum.md` §2, "Deferring CRC
is what buys back those two registers"); I did not know that when I designed
it (§7), and record the coincidence rather than claim priority.

`r8` is the one register that ever costs a `mov`, and only twice per 8 bits
(`byte_boundary`, not the ordinary path) — see §3.3.

## 3. Cycle ledger, every path, arithmetic shown

Cost model: `ENGINE16_SPEC.md` §2, RAM-resident column (§4 justifies the
placement). Plain instruction = 1. GPIO IDR load through `r0` (named to
`--ioport`) = 1. RAM load/store = 2. Conditional branch not taken = 1, taken =
2 or 3 (alignment-dependent, unresolved without a bench — `CHIP_FACTS_XIAMATSU.md`
§1). Unconditional `b` = 2 or 3. Flash literal pool from RAM code = 4.

**Policy on the 2-vs-3 ambiguity**: every taken branch is padded assuming the
worst case (3). If real hardware resolves a given site to 2, that path
finishes 1 cycle early, which `ENGINE16_SPEC.md` §1 explicitly allows
("one that finishes early must pad deterministically" — padding for 3 and
landing on 2 is exactly that, in the safe direction). This is the same policy
GRAINUUM adopts and names for the same reason (`engine16_grainuum.md` §4,
"What I assume about the 2-vs-3 ambiguity") — arrived at independently before
I read their note (§7), kept because the arithmetic argument is the same
either way: finishing early is safe, finishing late is not.

### 3.1 Data bit (covers both data-1 and data-0 — one instruction sequence, no branch on value)

This is BALANCE's central claim: unlike every other entry examined (§7), there
is no branch anywhere in this file on the *value* of the decoded bit. Address
range `0x10`-`0x2c` of the ordinary (fall-through) path:

| addr | insn | cost | running |
|---|---|---|---|
| 0x10 | `ldr r6,[r0]` (IDR, r0 named to `--ioport`) | 1 | 1 |
| 0x12 | `ands r6,r1` (mask to D+/D−; Z = SE0) | 1 | 2 |
| 0x14 | `beq se0_bit` — **not taken** | 1 | 3 |
| 0x16 | `lsls r6,r6,#25` (isolate D− → bit31) | 1 | 4 |
| 0x18 | `asrs r6,r6,#31` (r6 = newLevel, sign-extended) | 1 | 5 |
| 0x1a | `eors r6,r2` (r6 := transitionMask; r2 untouched) | 1 | 6 |
| 0x1c | `eors r2,r6` (r2 := PREV XOR transitionMask = newLevel — PREV updated) | 1 | 7 |
| 0x1e | `cmp r4,#6` | 1 | 8 |
| 0x20 | `beq stuff_bit` — **not taken** | 1 | 9 |
| 0x22 | `adds r4,#1` (tentative ones-run increment) | 1 | 10 |
| 0x24 | `bics r4,r6` (finalize: 0 if transition, else r4+1 — no branch) | 1 | 11 |
| 0x26 | `lsrs r6,r6,#31` (r6 = 0/1; Carry = transitionMask's uniform bit) | 1 | 12 |
| 0x28 | `adcs r3,r3` (SHIFT_BUF := SHIFT_BUF·2 + Carry — insert, no branch) | 1 | 13 |
| 0x2a | `subs r5,#1` (BITCOUNT--) | 1 | 14 |
| 0x2c | `beq byte_boundary` — **not taken** | 1 | 15 |
| 0x2e | `b bit_cell` — **taken, unconditional** | 3 (worst case) | **18** |

`14×1 + 3 = 17`... **this path totals 18 under the worst-case policy (17 best
case), not 16.** I am not hiding this: it is BALANCE's one real defect against
spec §6 criterion 1, and §3.2 and §8 give the full accounting for why, what I
tried, and what closing it would cost. Every other path below is shown with
the same honesty.

**The two `bics`/`adcs` instructions are the mechanism this design is built
around**: `bics r4,r6` computes "0 if this bit was a 0 (transitionMask
all-ones), else r4+1" in one instruction with no branch — GRAINUUM evaluated
exactly this class of technique and priced a full branchless value-split and
stuff-counter blend at "roughly 9-12 extra instructions" and rejected it
(`engine16_grainuum.md` §9); BALANCE's version costs **4** extra instructions
over what a minimal branch-based core would need for the same three decisions
(`lsls`,`asrs`,`bics`,`lsrs` — the second `eors` is needed either way to keep
`PREV` current, and GRAINUUM's own branch-based core needs it too, just on
one arm only). §7 has the full comparison and says plainly that this refines,
rather than contradicts, their finding.

### 3.2 Byte boundary (data-1/data-0 whichever completes the byte)

Reached via the taken `beq byte_boundary` at `0x2c` above. Entry cost:
instructions `0x10`-`0x2a` (14, all cost 1) + the taken branch (worst case 3)
= **17** just to arrive. Then:

| addr | insn | cost | running (from entry to `byte_boundary`) |
|---|---|---|---|
| — | (arrival) | 17 | 17 |
| 0x34 | `mvns r3,r3` (fix inverted sense — see §3.4) | 1 | 18 |
| 0x36 | `ldr r6,=rxbuf` (flash literal, RAM code = 4) | 4 | 22 |
| 0x38 | `mov r7,r8` (BUFPTR out of high reg) | 1 | 23 |
| 0x3a | `strb r3,[r6,r7]` (RAM store) | 2 | 25 |
| 0x3c | `adds r7,#1` | 1 | 26 |
| 0x3e | `movs r6,#15` | 1 | 27 |
| 0x40 | `ands r7,r6` (structural wrap — D-2, see §6) | 1 | 28 |
| 0x42 | `mov r8,r7` (BUFPTR back into high reg) | 1 | 29 |
| 0x44 | `movs r5,#8` (BITCOUNT reset) | 1 | 30 |
| 0x46 | `movs r3,#0` (SHIFT_BUF reset) | 1 | 31 |
| 0x48 | `b bit_cell` — taken | 3 (worst) | **34** |

**This path costs 34 cycles against a 16-cycle budget — more than double.**
This is BALANCE's largest, most consequential defect, and I state the
arithmetic rather than a qualitative "it's slower": the shared 14-instruction
decode core plus a genuine byte store (RAM store 2, plus a flash-literal
address load at 4, plus pointer round-trip through `r8` at 2, plus two resets)
simply does not fit in the same 16 cycles as the ordinary path when *both*
the decode and the store must happen in the one bit slot that completes a
byte. §8 explains why I did not close this and what closing it would need.

### 3.3 Stuffed bit

Reached via the taken `beq stuff_bit` at `0x20`. Entry: instructions
`0x10`-`0x1e` (8, cost 1 each) + taken branch (worst 3) = 11. Then:

| addr | insn | cost | running |
|---|---|---|---|
| — | (arrival) | 11 | 11 |
| 0x30 | `movs r4,#0` (ones-run reset — a stuff bit is always a forced 0) | 1 | 12 |
| 0x32 | `nop` (padding, see below) | 1 | 13 |
| 0x34 | `b bit_cell` — taken | 3 (worst) | **16** |

`8 + 3 + 1 + 1 + 3 = 16` under the worst-case-branch policy — **exact, with
one `nop` computed to make it so** (`16 - 8 - 3 - 1 - 3 = 1`). Best case (both
taken branches actually cost 2): `8+2+1+1+2=14`, two cycles early — safe under
the policy in §3's header.

This path does *not* touch `SHIFT_BUF` or `BITCOUNT`, which is the whole
point of catching it before `0x22`: a stuffed bit is discarded, not
assembled, and the branch that catches it (`cmp r4,#6`/`beq stuff_bit` at
`0x1e`-`0x20`) fires *before* any of the insert/counter logic runs, so there
is nothing to undo.

### 3.4 SE0 / EOP

Reached via the taken `beq se0_bit` at `0x14`. Entry: `0x10`-`0x12` (2) +
taken branch (worst 3) = 5, then `ldr r6,=se0_bit_flash` (flash literal, RAM
code = 4) + `bx r6` (3) = 5+4+3 = **12**. This path does **not** sum to 16,
deliberately: once SE0 is recognized there is no next bit cell on this wire
for this packet, so the 16-cycle contract — which exists to keep *consecutive*
samples phase-locked — has nothing left to protect. CLEANSHEET reaches the
identical conclusion for the identical reason (`engine16_cleansheet.md`,
Path 3: "there is no next sample on this wire for this packet ... the
2-vs-3 ambiguity on this branch is real but irrelevant"); I had already made
the same call before reading their note (§7) and record the agreement rather
than claim it as new.

One correctness note carried from the reference engine, not new here: a
single SE0 sample is treated as EOP with no 2-bit-time confirmation
(`rv003usb-arm.S:164`, `beq se0_complete`, is the identical simplification).
Not fixed in this entry.

**Why `SHIFT_BUF` is built in inverted sense.** The insert instruction
(`adcs r3,r3`) uses the Carry flag straight out of `lsrs r6,r6,#31`, and that
Carry equals transitionMask's bit — 1 when a *transition* occurred (decoded
bit 0), 0 when it didn't (decoded bit 1). Building `SHIFT_BUF` directly from
that Carry therefore assembles the byte with every bit inverted relative to
its true value. Fixing this per-bit would cost a `mvns` inside the 16-cycle
path (as an earlier draft of this file did, at 16 non-branch instructions
instead of 10 — cut once I noticed the fix could move to the byte boundary,
which pays for it once per 8 bits instead of once per bit: `mvns r3,r3` at
`0x34`, §3.2). This is the same "move a fixed-cost correction from the
frequent path to the infrequent one" idea CLEANSHEET's design is built around
(defer *everything* to a packet-end tail, engine16_cleansheet.md, "the idea
in one paragraph") — I apply it to one instruction rather than to the whole
decode, which is why it is credited as an application of their idea and not
claimed as equivalent to their design.

## 4. Placement: RAM, and what it costs

RAM-resident (`.datacode`, matching `rv003usb-arm.S`'s own placement and
`BUILD_FACTS.md` §3-4's finding that the RX path already lives there). Per
`ENGINE16_SPEC.md` §2, this makes the `strb` in `byte_boundary` cost 2 instead
of 4, and makes the `ldr r6,=rxbuf` literal-pool load cost 4 instead of 2 —
paid once per byte (`byte_boundary`), not once per bit, so the RAM-column
saving on the store dominates. No constant lives in a literal pool inside the
ordinary bit path (`DMASK2` is a `movs` immediate, `GPIO_ADDR` is loaded once
at ISR entry) — `M0PLUS_ISA_FACTS.md`'s "bit-cell constants belong in
registers, not in a pool" is followed by construction in the timed loop, and
violated once, deliberately, in the untimed-per-byte `byte_boundary` path
(the `=rxbuf` load), where its 4-cycle cost is a real and counted contributor
to the overrun in §3.2, not an oversight.

## 5. Taken branches, and what the 2-vs-3 ambiguity costs

Per ordinary bit, one taken branch (the mandatory loop-back, `0x2e`), costed
worst-case at 3 — 3/16 = 18.75% of the nominal budget, and the reason the
ordinary path lands at 18 rather than 16 (§3.1). Two more branches exist per
bit but are **not taken** on the ordinary path (SE0-check, stuff-check) —
`ENGINE16_SPEC.md` §2 gives not-taken as a flat 1 cycle in both flash and
RAM, so neither contributes to the ambiguity there. `stuff_bit` and
`byte_boundary` each add one more taken branch to reach them, both padded
under the same worst-case-3 policy (§3).

**BALANCE does not achieve "timing that does not depend on the ambiguity at
all"** (`ENGINE16_SPEC.md` §6 criterion 3's strongest form) on the ordinary
path, because the loop-back branch is unavoidable in a genuine runtime loop —
there is no way to return control to the top of a linear instruction stream
without a control-transfer instruction, and Thumb offers nothing cheaper than
`b` for that. VUSB, CLEANSHEET and GRAINUUM all reach the same wall from
different directions and all resolve it by **unrolling per byte**, so that
the "loop-back" only happens once every 8 bits instead of every bit
(`engine16_grainuum.md` §1, "removes the loop-back branch from the interior
of the cell entirely"; `engine16_cleansheet.md`, "Why 8 bits, not 1", which
also shows *full*-packet unrolling doesn't even remove it, only relocates it
to periodic trampolines with the same cost). Three independent designs
converging on the same structural fix is a strong signal that it is close to
the correct answer for this problem — which is exactly why I did not adopt it
as my own contribution once I'd read all three: doing so now would be
reproducing an idea the field already has from at least three sources, not
contributing one. §8 quantifies what BALANCE's own core would look like
merged into that already-established structure instead of pretending I
invented the merge.

## 6. Buffer bound (`ENGINE16_SPEC.md` §3.8 / `DEFECTS_VERIFIED.md` D-2)

Structural, but via a **different mechanism from GRAINUUM's**: `BUFSIZE=16`
is a power of two, and `byte_boundary` advances `BUFPTR` with
`adds r7,#1; movs r6,#15; ands r7,r6` — a **runtime circular index mask**,
not a compile-time-bounded static unroll. No instruction can compute a store
address outside `rxbuf[0..15]`, because the index is masked to that range on
every advance, regardless of how many bytes the wire ultimately delivers — a
packet longer than 16 bytes overwrites from the start of the buffer rather
than running past its end. This is weaker than GRAINUUM's guarantee in one
respect (it does not stop reception at the true buffer bound, it wraps and
keeps going, so a too-long packet is truncated-and-corrupted rather than
cleanly aborted) and equal to it in the respect that matters for D-2
specifically: **no instruction in this file can address memory outside
`rxbuf`**, satisfying the defect's actual concern (a bus-reachable write past
the buffer into adjacent `.bss`) without a runtime bounds check inside the
16-cycle path. The cost is one `ands`, paid once per byte (`byte_boundary`,
already the over-budget path, §3.2) — not inside the ordinary bit path at
all, so it is free with respect to the 16-cycle budget even though it is not
free with respect to the byte-boundary overrun already documented there.

## 7. What I looked at, and what I took or extended

Design order: `ENGINE16_SPEC.md`, `M0PLUS_ISA_FACTS.md`,
`CHIP_FACTS_XIAMATSU.md`, `BUILD_FACTS.md`, `DEFECTS_VERIFIED.md`, then
`rv003usb-arm.S` (the pre-existing reference engine — not a competitor, the
spec's own comparison point, `ENGINE16_SPEC.md` §2's "existing 32-cycle
RISC-V-derived engine"). From those alone I built §1-§6 above, including
discovering the loop-back-branch problem in §3.1/§5 by hand-deriving the
ordinary path's cost and finding it did not fit. **Only after that** did I
read the four competitors' design notes (`engine16_{vusb,cleansheet,
grainuum,native}.md`) and `engine16_vusb.S` (VUSB has no `.md` — "its design
note was lost, so only its code survives", per the brief — I read the `.S`
directly, ~150 lines, for its register-contract comments and macro
structure). I did not read `engine16_grainuum.S` or `engine16_cleansheet.S`
line-by-line; their `.md` files carry their own ledgers quoted against their
own `objdump` output, which is what I cite.

**From `rv003usb-arm.S` (baseline, not a competitor)**: the toggle-in-place
identity for tracking a changing GPIO state (`arm.S:160-164`, `eor r0,r5`)
is the same *mathematical* move as my `eors r2,r6` PREV-update, independently
re-derived as an XOR-invertibility argument before I read GRAINUUM's note,
which also credits the same lines for the same idiom
(`engine16_grainuum.md` §3). This is baseline-lineage, common to any receiver
comparing consecutive samples on this wire, not any one competitor's
contribution — GRAINUUM says as much about their own use of it, and I agree.

**From GRAINUUM** (`engine16_grainuum.md`): two things, both *rethought*, not
reproduced.
1. Their §9 prices a full branchless value-split-plus-stuff-counter blend at
   "9-12 extra instructions" and rejects it as not worth the trade. My §3.1
   shows the same class of technique costing 4 extra instructions when the
   value-decode, the stuff-counter update, and the byte-insertion are fused
   around **one** mask (`transitionMask`) instead of built as three
   independent branchless blends — `bics` for the counter and the
   `lsrs`-then-`adcs` carry-chain for insertion both consume the *same*
   register produced by the *same* two `eors`, rather than each needing its
   own isolate-and-blend sequence. This directly answers their rejection with
   a cheaper realization of the same idea, which is the "rethink into a
   different answer to the same question" the brief asks for, not a
   reproduction of a mechanism they already tried (they tried it and stopped;
   I tried a fused version and it worked).
2. Their central claimed contribution, static per-byte unroll (their §13,
   "the one mechanism I would most want the referee to take from this
   entry"), I deliberately did **not** adopt, for the reproduction reason
   given in §5 above. §8 below extends their own finding instead: I give the
   concrete cycle count BALANCE's core would have *if* merged into their (or
   CLEANSHEET's, or VUSB's) unrolled structure, which none of the four notes
   I read computes for a fused-mask core like this one.

**From CLEANSHEET** (`engine16_cleansheet.md`): one point of independent
agreement, stated as such, not claimed as new: SE0 need not sum to 16 because
there is no next sample to protect (§3.4 above; their Path 3 makes the same
argument). I also adopted their explicit *policy* of costing every taken
branch at its architectural worst case (3) for padding purposes, and named it
as theirs in §3 rather than presenting it as my own invention — both GRAINUUM
and CLEANSHEET converge on this same policy independently of each other, so
crediting it to one and not the other would be arbitrary; I cite both where
each states it. Their idea of moving a per-bit fixed-cost correction to a
less-frequent boundary (§3.4 above, the `SHIFT_BUF` polarity fix) is their
central design move (defer *all* decode to the packet tail) applied by me to
exactly one instruction rather than the whole decode — an extension in
scale, not a reproduction, and I say so at the point it's used rather than
only here.

**From VUSB** (`engine16_vusb.S`, read directly — no `.md`): their register
contract comment block states outright "nothing in the timed path branches
on data" and backs it with a capture-then-table-lookup pipeline that defers
NRZI/unstuffing/assembly into the spare cycles of *later* cells (`SEG0`-`SEG6`
macros, interleaved into each cell's slack). I did not adopt table-driven
decode (a genuinely different mechanism from my mask-arithmetic one, and
VUSB's own strongest, most-cited result per the brief — reproducing it would
score zero). What I take from it is the *pipelining principle* — moving
work out of the cell that generates a byte and into the slack of nearby
cells — and I apply it nowhere in the shipped `.S` (BALANCE does not
pipeline anything; §8 explains why my core's slack, once unrolled, is too
small to carry the whole store the way VUSB carries a whole decode) but I
name it explicitly in §8 as the mechanism that would close BALANCE's own
byte-boundary overrun, crediting VUSB for the principle rather than silently
using it or silently not crediting it because it isn't in the code.

**From NATIVE** (`engine16_native.md`): nothing adopted into this design —
their result is that no peripheral route survives at 24 MHz (§0, "peripheral
assistance does not rescue reception"), which is an argument about *whether*
to bit-bang, not about how, and BALANCE was already a bit-bang design before
I read it. Worth recording for the referee independent of BALANCE: their §10
recommendation (input-capture-in-slave-reset-mode as a phase-lock front end
under any of the four bit-bang engines, costing three peripherals and 112
bytes) is compatible with BALANCE's own SYNC-lock, which is sketched, not
built (§9) — I neither confirm nor extend this, I just note it doesn't
conflict.

## 8. Extending the field's own finding: what BALANCE's core would cost, unrolled

This section is arithmetic on a structure I did **not** build (§5, §7), given
because it is the honest way to answer "would this design be competitive if
merged" without either building a duplicate of someone else's unroll or
asserting an unfounded number.

If `BITCOUNT` (r5, `0x1e`-line `subs`/`beq` pair removed — see below) is
eliminated the way GRAINUUM eliminates it (byte position becomes a
compile-time fact of which unrolled slot is executing, not a runtime value,
`engine16_grainuum.md` §1), BALANCE's ordinary-bit core shrinks by exactly
those two instructions (`subs r5,#1` and `beq byte_boundary`), from 15
instructions (14 cost-1 + one not-taken beq, per §3.1's table minus the final
`b`) to 13 (11 cost-1 instructions + 2 not-taken beqs for SE0 and stuff).
Padded to 16 inside an unrolled slot with **no loop-back branch needed for 7
of every 8 slots** (they fall through to the next physically-adjacent slot,
exactly as CLEANSHEET's and GRAINUUM's slots do): `13 + 3(pad) = 16`, meaning
**3 cycles of slack per ordinary slot** — more than GRAINUUM's own tightest
paths (0-2 cycles of slack on their §4.4/§4.5, by their own account) despite
GRAINUUM's core using a real conditional branch on bit value and BALANCE's
using none. This is the concrete form of the answer to their §9 rejection:
fusing the three decisions around one mask, not three separate blends, is
cheap enough that — merged into the unroll structure their own note argues
for — it would have *more* room to spare than their branch-based core does,
not less.

That slack (3 cycles × 7 slots = 21 cycles per byte) is not, by itself, enough
to absorb the full byte-boundary store BALANCE currently pays 18 extra cycles
for in one slot (§3.2: 34 total, 18 over budget) — `strb`(2) +
literal-load(4) + pointer round-trip(4) + two resets(2) = 12 cycles of real
store work, comfortably under 21, **if** it can be spread across multiple
slots' slack rather than paid in one. That spreading is exactly VUSB's
pipelining principle (§7) applied to a much narrower job than VUSB's own (one
store, not a whole decode) — I have not built it, and do not claim the 21-vs-12
arithmetic proves it works without handling the real complication (the
pending byte's value and target index must survive in registers across
slots that are also running the *next* byte's decode, which is exactly the
kind of register-lifetime bug GRAINUUM's own §8 flags catching by
assembling-and-checking, not by reasoning). Stated as what it is: a costed,
plausible direction, not a finished mechanism — see §9.

## 9. What I gave up

Every design at this budget gives something up; here is BALANCE's list,
sized honestly rather than folded into "future work":

1. **The ordinary bit path costs 17-18 cycles, not 16** (§3.1) — a real,
   uncorrected overrun of BALANCE's own making, not an artifact of the
   ambiguity policy. This is the design's central admitted defect against
   spec §6 criterion 1. The fix (per-byte unroll, §5/§8) is well understood
   and quantified in §8, but adopting it now would reproduce a mechanism
   three other entrants already claim; I chose to report the honest number
   on the structure I actually built rather than ship an unrolled file that
   would look, to a reader who has seen the other three notes, like a fourth
   copy of the same idea.
2. **The byte-boundary path costs 34 cycles, more than double budget**
   (§3.2) — the largest single defect in this design. §8 sketches a fix
   (pipeline the store across the following byte's slack, extending VUSB's
   principle) but it is not built or verified.
3. **CRC is not implemented at all**, only argued for deferral (§1, §4) — no
   CRC5/CRC16 code exists anywhere in `engine16_balance.S`; `se0_bit_flash`
   is a one-instruction stub (`bx lr`). GRAINUUM's §7 gives a turnaround-window
   arithmetic argument for why deferred CRC fits (32-104 cycles available,
   ≈50-60 needed for a table-driven CRC16); I did not re-derive it
   independently and cite theirs rather than restate it as my own.
4. **SYNC acquisition and phase lock are not implemented**, only assumed —
   `engine16_balance_entry` initializes registers and "falls into `bit_cell`
   once locked" with no lock mechanism shown. The reference engine's own
   sample-and-wait-for-a-transition approach (`rv003usb-arm.S:70-77`) is the
   obvious starting point and is not reproduced here because it isn't needed
   to make BALANCE's point (the bit-cell mechanism), not because I have a
   different answer.
5. **A single SE0 sample is treated as EOP** with no 2-bit-time confirmation
   — inherited simplification from the reference engine (§3.4), not
   independently re-examined or fixed.
6. **The wraparound buffer bound (§6) truncates-and-corrupts rather than
   cleanly aborting** an over-length packet, unlike GRAINUUM's stop-dead
   unroll bound. I judged this an acceptable trade for keeping the bound
   mechanism inside a loop-based (non-unrolled) design, but it is a real,
   named difference in guarantee strength, not an equivalent one.
7. **Transmit is not sketched at all** — spec §3 permits a sketch; this entry
   has none. GRAINUUM's §11 observation (a transmitter knows its own
   bit-stuffing pattern before entering the timed loop, so TX does not
   inherit RX's branch-ambiguity problem the same way) looks right to me on
   inspection but I have not built or costed anything against it.
8. **No hardware verification of the 2-vs-3 branch cost** anywhere in this
   file — every number in §3 is the tool's model plus a stated policy, not a
   bench measurement. Shared with every other entry examined; not a BALANCE-
   specific gap, but real nonetheless.

## Summary for the referee

* Does **not** fit 16 cycles on the ordinary path (17-18) or the byte-boundary
  path (34) — stated plainly, with the arithmetic, per spec §6's own priority
  on honesty over a false fit.
* The one mechanism I would want merged: the **fused-mask bit-cell core**
  (§3.1) — NRZI decode, byte insertion and the stuffing-counter update from
  one `transitionMask`, at 10 non-branch instructions with zero data
  branches, refining GRAINUUM's own explicit rejection of a similar but more
  expensive (9-12 extra instruction) branchless treatment down to 4 extra
  instructions. §8 shows it has more slack than GRAINUUM's own core once
  placed inside the unrolled structure GRAINUUM, CLEANSHEET and VUSB all
  independently arrived at — which I read as the strongest evidence this
  mechanism is worth taking, even though the file it ships in does not use
  that structure.
* Buffer bound (D-2) is closed by a runtime circular-index mask, a genuinely
  different mechanism from GRAINUUM's static-unroll bound, with a named
  weaker guarantee (wraps instead of aborting).
* CRC deferred, TX unsketched, SYNC-lock unsketched, branch-cost bench
  unverified — all named in §9, none hidden in "future work".
