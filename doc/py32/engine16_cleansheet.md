# engine16_cleansheet — design note

Competitor: **CLEANSHEET**. Lineage: **none**, by charter. Where this lands
close to the existing engine, I say so; where it doesn't, that's the point of
running four independent designs against one contract.

All cycle numbers below were cross-checked two ways: by hand, against
`ENGINE16_SPEC.md` §2's cost table, and mechanically with
`tools/engine16_cyc.py` against the actual assembled object
(`arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c
engine16_cleansheet.S`, verified to exit 0 in this worktree). Addresses cited
below (e.g. `0x28`) are from that object's `objdump -d`, not from reading the
source.

## The idea, in one paragraph

Don't decode while receiving. A bit cell has 16 cycles; NRZI decode,
unstuffing, byte assembly and CRC together need perhaps 10-14 cycles of real
work if done inline (that's roughly what the existing 32-cycle engine spends,
scaled down) — too much to also leave slack for the one thing that actually
must happen every 16 cycles without fail: sampling the pin at a stable phase.
So don't do the decode inline. Sample the raw line level once per bit cell,
fold it into a shift register with one carry-chained instruction, and do
*nothing else* until either the byte fills or the line goes to SE0. NRZI
decode, bit unstuffing, byte assembly and CRC all move to the untimed tail,
which runs once per **packet**, not once per **bit**, and can be as
unhurried as correctness requires. The per-bit cost drops from "decode a bit"
to "capture a sample," which is small enough that the entire cycle budget is
slack to be spent on making the phase rock-solid instead of on decode logic.

This is not a stylistic choice — see "why not decode inline" below, where I
show inline decode does not fit 16 cycles at all without cutting something
the spec requires (buffer bound or CRC).

## Lineage note

The reference engine (`rv003usb-arm.S`) already knows the shift-register/CRC
pieces are worth doing per-bit — it just also does them *inside the timed
loop*. My decode math for a "1" bit (`lsr`+`orr`) and the general shape of
"sample, compare, branch" are unavoidably similar to any receiver on this
wire — see `rv003usb-arm.S:99-114` — because USB fixes the physics, not the
implementation. Where I diverge is structural: nothing about PID, bit count,
bit-stuffing, or CRC exists inside my timed loop at all. That divergence is
the actual contribution; the similarity is just "receivers samle a pin."

## Register allocation

| reg | role | live range |
|---|---|---|
| r0 | GPIO port base | whole handler |
| r1 | `USB_DMASK` = `(1<<DP)\|(1<<DM)` = `(1<<0)\|(1<<3)` = 9 | whole handler |
| r2 | raw-sample accumulator (ADCS-chained, newest bit at bit0) | whole capture |
| r3 | per-bit scratch: this cycle's IDR sample | one bit cell |
| r4 | `raw_capture` write pointer | whole capture |
| r5 | shift scratch (value unused, only carry-out matters) in-loop; escape-stub "valid bits this byte" tag (0-7) after | one bit cell / after escape |
| r6 | remaining-byte budget, `RAW_MAXBYTES` down to 0 | whole capture |
| r7 | the one D- level sampled at sync lock, the NRZI reference ("bit -1") | whole capture, round-tripped through every `push {r7}`/`pop {r7}` padding pair unchanged |

**r8-r12 are never touched.** The existing engine needs three of them
(`GPIO_BASE`, a spare, and one more via `mov`) because it keeps CRC state,
a bit-stuff counter, a bit counter and a shift buffer all live *simultaneously*
inside the timed loop (`rv003usb-arm.S:22-27`). Once decode moves out of the
timed loop, all of that state disappears with it — eight bit cells need only
the six values above, which fit r0-r7 with a spare (there is no bit-counter
register at all: the loop period **is** 8, so "which bit of the byte is this"
is which of the eight physically-unrolled slots is executing, not a runtime
value). This is register pressure #4 in the judging order, reported honestly:
it isn't close, and it's a direct consequence of the capture/decode split, not
of anything clever with the allocation itself.

## Placement: flash, not RAM

`CHIP_FACTS_XIAMATSU.md` §1 and the existing engine both argue for RAM
placement, because RAM halves the cost of the operations their designs are
tightest on (RAM stores, PUSH/POP). I place this engine in flash instead, and
the reason is exactly the capture/decode split: my slots have 10-11 cycles of
slack (see the ledger below), so the flash-vs-RAM delta on a store (4 vs 2) or
a PUSH/POP pair (8 vs 4) is absorbed by slack that would otherwise be idle
NOPs. Given that, I keep the ~300 bytes of hot-loop code in flash and leave
RAM for buffers, which matters on a part with 2-3 KB of SRAM total
(`CHIP_FACTS_XIAMATSU.md` §2: F002B has no confirmed flash/RAM split beyond
"small"). **This placement choice is only correct because this design has
slack to spend; a tight design (this one's own byte-boundary slot included —
see below) still has to fight for every cycle, and there RAM is worth it.**
That is the interesting generalization: RAM-vs-flash for a bit-cell engine on
this chip is not a fixed answer, it's a question you re-ask every time the
per-slot budget changes.

## The capture geometry

Worst case one packet: PID (8) + payload (`USB_BUFFER_SIZE`=12 bytes = 96
bits, `rv003usb.h:120`) + CRC16 (16) = 120 coded bits. Bit-stuffing worst case
is one stuffed bit per six consecutive ones: `floor(120/6) = 20`. Raw bit
budget: `120 + 20 = 140` bits = 17.5 bytes, rounded up to **18** —
`RAW_MAXBYTES` (`engine16_cleansheet.S:56-64`). The loop's byte countdown
(`r6`, `subs r6, r6, #1` / `beq escfull_cs`, addresses `0xc2`/`0xc4`) makes
this a **hard, unconditional stop**: after exactly 18 byte-periods (144 raw
bits) the engine leaves the timed loop no matter what the bus does. There is
no code path that writes byte 19. This is the D-2 fix
(`DEFECTS_VERIFIED.md`): structural, and it costs nothing extra in the hot
path, because the byte-boundary slot already has to check something at that
point (whether to loop back) — the buffer bound rides on the same check for
free.

## Why 8 bits, not 1 and not "the whole packet" — the escape-range problem

This is the most important structural finding in this entry, and it's a
negative result: **full unrolling of the entire packet does not achieve what
it looks like it should.**

The naive argument for full unroll: make the per-bit "not taken" path the
*only* path with recurring cost, since `spec §6.3` explicitly rewards a design
whose timing "does not depend on the ambiguity at all." A design with zero
backward branches in the hot path would have zero recurring 2-vs-3 events.

That argument breaks on one measured ISA fact
(`M0PLUS_ISA_FACTS.md`, confirmed by assembling): **the short conditional
branch (`beq`, Thumb16 T1) has an eight-bit signed offset — roughly ±256
bytes.** Every plain slot needs a `beq` to escape on SE0 (the escape check
*must* be per-bit — see "why not check SE0 less often" below), and that
escape target must reach the untimed tail. A full 140-bit unroll spans
several kilobytes; a `beq` from bit 3 cannot reach a tail-entry point placed
after bit 140. The standard fix — periodic "trampoline" stubs close enough to
be in range — does not remove the problem, it *relocates* it: something has
to make sure those trampolines aren't executed on the normal (non-SE0) path,
and the only tool available for that is an **unconditional branch that is
always taken**, placed with the same period the trampolines need. That
branch has the identical 2-vs-3 cost and the identical recurrence as a real
loop-back — full unroll just pays several kilobytes of flash for the *same*
residual risk a small loop already has.

Given that, a real loop is not a compromise forced by laziness; it's the
better engineering choice once the branch-range constraint is priced in. The
remaining design question is the loop's *period* — how many bits between one
recurring branch and the next — and that's bounded by the same range
constraint from the other side: the escape stub group must be reachable from
*every* slot in the loop body, including the first. With this file's slot
size (10 instructions / 20 bytes for a plain slot, engineered down from a
naive NOP-padded 16 instructions by using `push {r7}`/`pop {r7}` as compact
4-cycle-per-instruction padding — see below), 8 slots span 160 bytes
(`0x28` to `0xc8`), and the farthest escape (`esc1_cs` from the bit-1 `beq` at
`0x30`, target `0xcc`) measures **156 bytes** — comfortably inside ±256, with
about 100 bytes of margin verified by the assembler actually accepting the
`beq.n` (16-bit) encoding rather than erroring out demanding a form that
doesn't exist on this core. Twelve slots would have been tight; sixteen would
not have fit. Eight was chosen empirically against this constraint, not
picked for being "one byte."

**Result: this design has exactly one recurring taken branch, once every 8
bits (12.5% of bits) — an 8x reduction against a naive per-bit loop (which is
what the reference engine has: `rv003usb-arm.S`'s `bit_process` loops every
bit, `S:198`).** It is not the theoretical zero the full-unroll argument
promised, and I want to be explicit that I could not get to zero: the ISA's
branch encoding forecloses it for anything longer than about a dozen bits,
which is shorter than almost every real packet.

## Why not check SE0 less often (e.g. only at the byte boundary)?

Considered and rejected on a latency argument, not a cycle-count one. USB
requires the device to answer within a small number of bit times after EOP
(`rework/ledger.md` S-2 cites a 7.5-bit-time gate). If SE0 were only tested
once per 8 bits, detection could lag the real EOP by up to 7 bit times —
which alone consumes most of that budget before the CRC check, dispatch, or
any TX turnaround even starts. So the SE0 test has to be per-bit regardless
of loop period; only the *loop-back* (continue-capturing) event benefits from
being infrequent. This is why the design has a per-bit branch (SE0, cheap
when not taken) nested inside a per-8-bit branch (loop-back, the one with
real ambiguity cost) rather than only the latter.

## The cycle ledger

Every plain bit and the byte-boundary bit are one loop body
(`engine16_cleansheet.S:129-231`), executed once per byte, at most 18 times
per packet (usually far fewer — see "wall-clock" below). SE0 can be sampled
on any of the 8 bits; the ledger covers one representative slot (bit 1) and
notes the others are byte-identical modulo the escape target.

### Path 1 — plain bit, not SE0 (covers data-1, data-0, and a stuffed bit alike)

This is the design's core claim: **the hot loop does not know or care what
the bit's value is, so "data 1", "data 0" and "stuffed bit" are the exact
same instruction sequence with the exact same cost.** There is no branch on
bit value anywhere in this file. Address range `0x28`-`0x3a` (bit 1):

| addr | insn | cost | running |
|---|---|---|---|
| 0x28 | `ldr r3, [r0, #0x10]` (IDR, r0 is the port base) | 1 | 1 |
| 0x2a | `lsrs r5, r3, #4` (C := D- level; r5 is a throwaway dest) | 1 | 2 |
| 0x2c | `adcs r2, r2` (acc := acc\*2 + C) | 1 | 3 |
| 0x2e | `tst r3, r1` (Z := SE0; does not touch r3) | 1 | 4 |
| 0x30 | `beq.n esc1_cs` — **not taken** | 1 | 5 |
| 0x32 | `push {r7}` | 4 | 9 |
| 0x34 | `pop {r7}` | 4 | 13 |
| 0x36 | `nop` | 1 | 14 |
| 0x38 | `nop` | 1 | 15 |
| 0x3a | `nop` | 1 | **16** |

`1+1+1+1+1+4+4+1+1+1 = 16`. Exact, no ambiguity: the one conditional branch
in this path is the *not-taken* case, which `ENGINE16_SPEC.md` §2 gives as a
flat 1 cycle in both flash and RAM — the 2-3 range only applies when a branch
is taken, and this path never takes one. **This is the "timing does not
depend on the ambiguity at all" case from spec §6.3, achieved for 7 of every
8 bits.**

The `tst` must be the *last* flag-setting instruction before the `beq` — an
earlier draft had `lsrs`/`adcs` after the SE0 test, which clobbers the flags
`tst` set (`lsrs`/`adcs` are flag-setting forms too) and made the branch test
the wrong condition. Caught by hand-tracing before assembling anything;
worth naming because it's exactly the kind of bug that "assembles cleanly"
does not catch — the annotator tool doesn't model flag lifetime either, only
per-instruction cost.

`push {r7}`/`pop {r7}` round-trips r7 unchanged (push then pop of the same
register with nothing in between) and costs 4+4=8 cycles for 4 bytes of
code — twice the cycles-per-byte of a `nop` chain (8 cycles would need 8 NOPs
= 16 bytes). This is a genuinely M0+-specific move: it exists because
PUSH/POP cost 4 cycles for the first (only) register even though it is a
single 16-bit opcode (`ENGINE16_SPEC.md` §2, "LDM/STM/PUSH/POP: 4 first reg").
An 8-bit CPU has no equivalent — its stack push/pop is typically 1-2 cycles
because there's no multi-register block-transfer microarchitecture behind it
to amortize (or, here, to spend). Using it purely as timing filler, not for
its data-movement effect, is unusual enough to flag explicitly as "what a
clean-sheet ARM design reaches for that a per-bit V-USB-style AVR loop simply
has no analogue for."

### Path 2 — byte boundary (8th bit of the byte) — the one place ambiguity lives

Address range `0xb4`-`0xca`:

| addr | insn | cost | running |
|---|---|---|---|
| 0xb4 | `ldr r3, [r0, #0x10]` | 1 | 1 |
| 0xb6 | `lsrs r5, r3, #4` | 1 | 2 |
| 0xb8 | `adcs r2, r2` | 1 | 3 |
| 0xba | `tst r3, r1` | 1 | 4 |
| 0xbc | `beq.n esc8_cs` — not taken | 1 | 5 |
| 0xbe | `strb r2, [r4, #0]` (RAM store, flash-resident code: **4**, spec §2) | 4 | 9 |
| 0xc0 | `adds r4, #1` | 1 | 10 |
| 0xc2 | `subs r6, #1` | 1 | 11 |
| 0xc4 | `beq.n escfull_cs` — not taken (normal case) | 1 | 12 |
| 0xc6 | `nop` | 1 | 13 |
| 0xc8 | `nop` | 1 | 14 |
| 0xca | `b.n loop_top_cs` — **taken, unconditional** | **2 or 3** | **16 or 17** |

`12 + 2 = 14 + 2(pad) = 16` if the taken branch costs 2; `17` if it costs 3.
**This is the one path in the whole design whose exactness depends on the
unresolved hardware fact** (`CHIP_FACTS_XIAMATSU.md` §1: "2-3... depends on
alignment and on the preceding instruction" — not resolved in this project
without a bench, `rework/ledger.md` K7/K8). I padded assuming B=2, i.e. I bet
on the lower, architecturally-nominal number. If B=3 in fact, this slot runs
17 cycles instead of 16, and the fix is to delete one of the two NOPs at
`0xc6`/`0xc8` — a one-line change once measured, not a redesign, because the
ambiguity is a *fixed, measurable* property of this exact code layout, not
runtime randomness. I could not verify this without hardware and say so
plainly: **this is where I am least certain of the whole design.**

Note the two NOPs are placed *before* the unconditional branch, not after —
an earlier draft put them after `b loop_top_cs`, where they are dead code
(unreachable, since the branch above them is unconditional) and contribute
nothing to this slot's cycle count. Caught by hand-summing against the
annotator's own per-instruction costs, not by the annotator itself, which
doesn't resolve control flow and so cannot see that a range's tail is
unreachable — see "cross-check" below.

**Consequence for phase drift:** this branch recurs once every 8 bits, for
every packet longer than one byte (i.e. essentially every real packet). If
B is actually 3 rather than the assumed 2, every byte after the first drifts
the sample point 1 cycle later. Over the worst-case 18-byte capture that is
up to 17 cycles of accumulated drift — more than one full bit cell — which
would eventually walk the sample point out of the valid window. This is a
real, not hypothetical, risk *if* the assumption is wrong, and it is the
single most important open item this design has. It is not a "sometimes
works" risk, though: once B is bench-measured for this exact alignment
(`.balign 4` at `loop_top_cs`, verified in the assembled object), the design
becomes exactly correct with a one-NOP edit, for good.

### Path 3 — SE0 (any of the 8 bits reads SE0)

Shown for bit 1; identical shape for bits 2-8 except the strb/adds/subs that
already ran for bits preceding the boundary (bit 8's SE0 case additionally
has `strb`/`adds`/`subs` NOT yet executed — see the escN tags below).

| addr | insn | cost | running |
|---|---|---|---|
| 0x28 | `ldr` | 1 | 1 |
| 0x2a | `lsrs` | 1 | 2 |
| 0x2c | `adcs` | 1 | 3 |
| 0x2e | `tst` | 1 | 4 |
| 0x30 | `beq.n esc1_cs` — **taken** | 2 or 3 | 6 or 7 |

`4 + (2 or 3) = 6 or 7`. **This path does not sum to 16, deliberately.** The
spec's per-path ledger asks for a running count "summing to 16" for every
path; I read that as applying to paths where a *next* bit cell's phase
depends on this one's exact duration. Once a bit reads SE0, there is no next
sample on this wire for this packet — the 16-cycle contract exists to keep
consecutive samples phase-locked, and there are no more samples to lock. The
2-vs-3 ambiguity on *this* branch is real but irrelevant, which is exactly
the outcome spec §6.3 asks for on the rare path ("pay 2-3 only on the rare
path" — `M0PLUS_ISA_FACTS.md`'s own closing guidance). I flag this
explicitly as an interpretive choice rather than assume it's obviously
right.

One correctness caveat shared with the reference engine, not new to this
design: a single SE0 sample is treated as EOP immediately, with no
confirmation that it persists the 2 bit times USB 2.0 actually requires
before a J. `rv003usb-arm.S:164` (`beq se0_complete`) makes the identical
simplification. Not a regression; also not fixed here.

### Escape stubs and the "dead space" trick (untimed, but load-bearing)

`esc1_cs`..`esc8_cs`, `escfull_cs` (`engine16_cleansheet.S:246-262`) are
placed immediately after `b loop_top_cs` — i.e. in code that is **never
reached by fall-through**, because the branch immediately before it is
unconditional. This is what makes a ±256-byte-range `beq` usable as the SE0
escape from a slot up to 156 bytes away: the escape doesn't jump to the far
untimed tail directly, it jumps to a same-neighborhood 1-2 instruction stub
that tags which bit saw SE0 (`movs r5, #N`) and then does the far jump
(`b common_escape_cs`, itself within range of the stub group; `common_escape`
does the actual long jump via `ldr r6, =rx_tail_entry_cs; bx r6`, unranged
since it's register-indirect). None of this costs hot-path cycles — it only
executes once, when the packet is over.

## Cross-check against `tools/engine16_cyc.py`

Running it (`--exec flash --ioport r0 --budget 16`) reproduces every
per-instruction cost used above exactly (1 for the ALU/shift/GPIO ops via
`r0`, 4 for `strb`/PUSH/POP-single-reg, 1-3 for conditional branches). It
also flags `loop_top_cs` as one block, "128..147 cycles, OVER BUDGET" — this
is the tool's own documented limitation (`tools/engine16_cyc.py`'s docstring
and the coordinator's note: "does NOT resolve control flow... your hand
ledger remains the authority"): it sums straight through 8 embedded
not-taken branches as if they always fall through, because a label-to-label
span is its unit, not a bit cell. Summed against the *actual* control flow
(8 independent 16-cycle not-taken exits, or one 6-7-cycle taken exit, per
slot) by hand above, using the tool's own verified per-instruction numbers.
I want to be explicit that the tool did not confirm "16 cycles per bit" by
itself — it confirmed the instruction-level costs I built the hand ledger
from, and one honest "OVER BUDGET" flag that is correct about the block as
the tool defines it and not informative about the bit-cell claim this design
actually makes.

## Wall-clock note (why the loop almost never loops)

`RAW_MAXBYTES=18` bounds the *worst case*. A real token packet (PID + 2 bytes
+ CRC5, ~3-4 bytes raw after stuffing) never reaches the loop-back branch at
all — it escapes via an `escN` inside the *first* pass through the loop body.
The 8x-reduced, ambiguity-carrying loop-back only executes for packets longer
than one byte, and only `floor(raw_bits/8) - 1` times even then. For the
common case (tokens, short data), this design's timing is fully
branch-ambiguity-free in practice, not just in the theoretical best case.

## Answers to the first-principles questions

* **Loop or straight line?** Neither purely. A real loop, because full
  unroll doesn't avoid the recurring-branch cost on this ISA (see above) —
  it just spends kilobytes of flash to keep paying it. Loop period is 8 bits,
  forced by the ±256-byte `beq` range at this slot size, not chosen for
  round-ness.
* **Decode now or in the untimed tail?** Untimed tail, unconditionally. This
  is the design's central move; see "the idea" above. Cost: 36 bytes of BSS
  (`raw_capture[18]` + `rxbuf_cs[18]`) and a tail that has to redo NRZI/
  unstuff/byte-assembly/CRC serially instead of getting them "for free" as a
  side effect of per-bit decode — a real complexity transfer, not a free
  lunch, but one that happens entirely outside the 16-cycle contract.
* **Whole-word bit unstuffing?** Considered, rejected, honestly a non-result:
  removing a stuffed bit shifts everything after it, so a word-parallel
  version needs a variable-shift compaction step per stuffed bit found in the
  word (this core has no `rbit`/`clz` to even find that bit cheaply —
  `M0PLUS_ISA_FACTS.md`). Since the tail is untimed, a simple serial bit scan
  removing stuffed bits one at a time is no slower in any way that matters
  and is far easier to verify correct. **Not implemented as word-parallel;
  the tail sketch is serial.**
* **Where does CRC belong?** Entirely in the untimed tail, computed over the
  fully NRZI-decoded, unstuffed byte stream — it cannot go anywhere else once
  decode itself is deferred, and once it's deferred anyway there is no
  argument left for computing it incrementally, unlike a design that already
  decodes inline and gets CRC nearly free as a byproduct (`rv003usb-arm.S`'s
  Domkeykong trick, `S:167-184` — a genuinely good mechanism for *that*
  design, not applicable to this one).
* **Byte-awareness while receiving?** None. The hot loop has no concept of a
  byte boundary beyond "which of the 8 physically-unrolled slots is this,"
  which is a compile-time fact, not a runtime one.

## What this design gives up (every design at this budget gives something up)

1. **The one open hardware fact** (branch cost 2 vs 3) determines whether the
   byte-boundary slot is exactly 16 or 17 cycles, recurring every 8 bits.
   Unverified without a bench. Worst case ~17 cycles of drift over a
   maximum-length packet if I bet wrong. See Path 2 above for the full
   argument and why it's a one-NOP fix once measured, not a redesign.
2. **The tail is a sketch, not a hardened implementation.** `rx_tail_entry_cs`
   shows the partial-byte flush (the one piece I could get fully right and
   trace by hand — see the esc2/esc8 worked examples in the source comments)
   but stops short of a complete NRZI/unstuff/CRC/dispatch — that's
   acknowledged directly in the source (`engine16_cleansheet.S`, comment
   above `b interrupt_complete_cs`) rather than papered over with plausible-
   looking but unverified code. Spec ranks correctness (#2) above code size
   (#4); I chose to be honestly incomplete on a secondary, untimed path
   rather than confidently wrong on it.
3. **Sync-search timing is coarse and unverified.** The settle delay
   (`settle_cs`, `movs r5, #24`) uses the same "USB's own SYNC field gives a
   receiver slop to lock phase" argument the reference engine's
   `DELAY_CYCLES` comment makes (`rv003usb-arm.S:60-62`), scaled by eye,
   not derived against real sync-field timing at this bit width. Needs a
   bench, same as item 1.
4. **A single SE0 sample commits to EOP**, with no 2-bit-time confirmation
   (shared limitation with the reference engine, not introduced here — see
   Path 3).
5. **TX is sketched only** (spec §3 permits this). The capture/decode split
   that makes RX cheap does not transfer to TX: a transmitter has to *produce*
   the bus edge on schedule, so there is no untimed tail to defer work to —
   every TX cycle is real work, in the timed path, by construction. That's a
   different, still-open problem; `usb_send_data_cleansheet` in the `.S` is a
   placeholder (`bx lr`) and claims nothing.
6. **Code size for the escape fan-out**: 9 small stubs (`esc1`..`esc8`,
   `escfull`) add ~40 bytes of dead-space flash the hot loop itself doesn't
   need. Total assembled `.text` is 304 bytes (`arm-none-eabi-size`) — still
   small in absolute terms, but not zero, and it's the price of the
   escape-range fix, paid once per file rather than once per bit.

## What I'd want the referee to take from this entry

**The capture/decode split** (sample raw levels into a shift register with
one `adcs`-chained instruction, defer NRZI/unstuff/CRC entirely to an untimed
tail gated only by a per-bit SE0 check) is the one mechanism I'd most want
merged. It is what makes every other property of this design possible: no
r8-r12 pressure, no per-bit branch, a byte-boundary loop-back that's the
*only* place ambiguity matters, and a buffer bound that falls out of a
counter the design needed anyway rather than costing a dedicated check. It is
also the mechanism most likely to combine cleanly with another competitor's
CRC or dispatch code, since it produces an ordinary byte buffer at the end —
the interface to "the rest of the packet processing" is exactly as generic
as the reference engine's already is.

The negative result I'd most want on record for the other three entries and
the referee alike: **full unrolling does not remove the recurring-branch-
ambiguity risk on this ISA** — the ±256-byte conditional branch range forces
some periodic always-taken control transfer regardless, so the real question
every design here answers, explicitly or by accident, is "how large a period
can I afford," not "can I get to zero."
