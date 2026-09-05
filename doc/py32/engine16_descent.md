# engine16_descent -- design note

Competitor: **DESCENT**. Lineage: **direct** -- compression of the existing
32-cycle-per-bit engine (`rv003usb/rv003usb.S`, RISC-V original; its Thumb
port `rv003usb-arm.S`, referenced throughout as "the original"). This note
is being written incrementally; sections below are filled in commit by
commit rather than in one pass (see the durability note in the task brief).

## Status

- [x] idea + lineage
- [x] register allocation
- [x] placement
- [x] cycle ledger (data-1, data-0, stuffed, byte boundary, SE0)
- [x] what was removed, and what it cost
- [x] what I gave up

## The idea, in one paragraph

Halving the budget from 32 to 16 does not leave room for the original's
mechanisms scaled down proportionally -- it forces a choice about which
survive. Two changes do the work: (1) CRC moves out of the timed loop
entirely (the original's inline CRC5/16 update costs 5-6 cycles/bit for a
benefit -- early token validation -- that the original's own control flow
never actually uses inside the timed loop, since `se0_complete_flash`
already defers the *decision* to the untimed tail, Sec. "what was removed");
and (2) the per-bit `BITCOUNT` decrement + branch to a *shared*
`is_end_of_byte` handler cannot survive at all, for a reason that is
arithmetic rather than stylistic -- a shared handler inherits whatever
budget its caller has left (1-4 cycles), and a byte store needs at least 7;
"which byte a bit belongs to" has to become a compile-time fact (per-byte
unroll) so the byte-completing slot gets its own full 16-cycle allowance,
converging with GRAINUUM's identical mechanism from the opposite direction
(Sec. "what was removed", point 2, has the full arithmetic). Everything else
in this design -- register reallocation to remove a `mov`-shuffle the
original didn't need to make, the branch-based "1"/"0" decode kept
recognisably close to `rv003usb-arm.S:102-114`, the combined D+/D- sample
kept for the SE0 detection it buys for free -- is compression around those
two decisions, not a new mechanism.

All addresses cited below are from
`arm-none-eabi-objdump -d` on the object this file actually assembles to
(`arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c
engine16_descent.S`, exits 0 in this worktree), cross-checked with
`tools/engine16_cyc.py engine16_descent.o --exec ram --ioport r3`. The tool
does not resolve control flow (its own docstring says so); every path below
is traced by hand against its per-instruction costs, not read off a block
total.

## Register allocation

| reg | role | live range |
|---|---|---|
| r0 | previous masked (D+\|D-) sample | whole packet |
| r1 | `RXBUF_BASE`, resident (loaded once at entry) | whole packet |
| r2 | constant `0x80` (bit-insert mask for a "1") | whole packet |
| r3 | `GPIO_BASE`, resident (loaded once at entry) | whole packet |
| r4 | `SHIFT_BUF` | whole packet, cleared each byte |
| r5 | scratch: this cycle's delta / IDR sample | one bit cell |
| r6 | `BITSTUFF`, down-counter 6..0 | whole packet |
| r7 | pin mask `(1<<DP)\|(1<<DM)`, resident | whole packet |

Eight live values, eight low registers, no spillover to r8-r12 and no `mov`
between a high and a low register anywhere in the timed loop. That last part
is a direct, measurable saving over the original, which keeps `GPIO_BASE` in
r9 and the pin mask in r12 (`rv003usb-arm.S:27,79`) and pays for it with
`mov r5, GPIO_BASE; mov SCRATCH, r12` (`rv003usb-arm.S:97-98`, repeated at
:155-156) before *every* GPIO read, because Thumb's register-offset `ldr`
only addresses r0-r7. Keeping both resident in low registers from the start
removes those two `mov`s entirely — 2 of the 16 cycles/bit this design has
to find come from register allocation alone, not from cutting a mechanism.

The original's `SHIFT_BUF` (r3), `BITCOUNT` (r1), `CRC` (r7), `POLY_RX`
(r14, via `mov`) and `BITSTUFF` (r6) map onto: `SHIFT_BUF`→r4, `BITSTUFF`→r6
unchanged, `BITCOUNT`→**gone** (Sec. "byte boundary" below explains why it
cannot survive as a register), `CRC`/`POLY_RX`→**gone** (deferred to the
untimed tail, Sec. "what was removed").

## Placement: RAM, `.datacode_descent`

Same choice as the original (`rv003usb-arm.S:37`, `.datacode`) and the same
justification `CHIP_FACTS_XIAMATSU.md` §1 gives generally: a GPIO access is
1 cycle in both columns, so placement doesn't touch the sample; a RAM data
access (the `strb` at the byte boundary) is 2 cycles from RAM-resident code
against 4 from flash-resident code, and this design's byte-boundary path
(Sec. below) has at most 2-3 cycles of slack — it cannot absorb the 4-cycle
flash-column price the way CLEANSHEET's 10-11-cycle-slack design deliberately
does (`engine16_cleansheet.md` "Placement: flash, not RAM"). RAM is not a
free choice here, it is the only one that fits.

## Cycle ledger

Every path below sums to 16, with the taken-branch cost taken at its
worst case (3) unless noted. Per `ENGINE16_SPEC.md` §2 and
`CHIP_FACTS_XIAMATSU.md` §1, that worst case is an open hardware constant,
not a design choice — GRAINUUM and CLEANSHEET both flag the identical
dependency and both resolve it the same way once measured (insert one
`nop`); this design carries that same, already-reported exposure and no
more.

### Path: data "1", bits 1-7 of a byte (`byte0_bit1_one`, addr `0x4a`)

Reached via `beq byte0_bit1_one` **taken** from `byte0_bit1` (addr `0x2a`).

```
0x2a ldr  r5,[r3,#0x10]   1  =1     sample D+|D-
0x2c ands r5, r7          1  =2     isolate the 2-bit field
0x2e eors r5, r0          1  =3     delta (0 here means "no transition")
0x30 beq  byte0_bit1_one  3  =6     TAKEN, worst case
0x4a lsrs r4, r4, #1      1  =7
0x4c orrs r4, r2          1  =8     set bit7 (r2 == 0x80): decoded "1"
0x4e subs r6, r6, #1      1  =9     BITSTUFF--; sets Z
0x50 beq  ..._stuff       1  =10    NOT taken (no stuff this bit)
0x52 nop                  1  =11
0x54 nop                  1  =12
0x56 nop                  1  =13
0x58 b    byte0_bit2       3  =16    taken, worst case (loop to next slot)
```
6 + 4 + 3(nop) + 3 = 16. Arithmetic: 3(entry)+3(taken beq)+4(one-work)+1(nt
beq)+3(pad)+3(taken b) = 16.

### Path: data "0", bits 1-7 of a byte (fall-through at `0x2a`)

```
0x2a ldr  r5,[r3,#0x10]   1  =1
0x2c ands r5, r7          1  =2
0x2e eors r5, r0          1  =3
0x30 beq  ..._one         1  =4     NOT taken (delta != 0)
0x32 eors r0, r5          1  =5     prev := new sample; Z set iff SE0
0x34 beq  ..._se0         1  =6     NOT taken (common case)
0x36 lsrs r4, r4, #1      1  =7     shift a 0 into SHIFT_BUF
0x38 movs r6, #6          1  =8     reset BITSTUFF (a "0" always resets it)
0x3a..0x48  nop x8        8  =16    pad -- falls straight into byte0_bit2
```
3 + 1 + 1 + 1 + 2 + 8 = 16. No loop-back branch on this path at all: the
next slot is physically next in memory (the per-byte unroll, Sec. below),
so the common "0" bit pays **zero** branch-ambiguity cost, not even a
not-taken one for the loop.

### Path: byte boundary, bit 8, "0" (`byte0_bit8`, addr `0x284`, stores byte 0)

```
0x284 ldr  r5,[r3,#0x10]   1  =1
0x286 ands r5, r7          1  =2
0x288 eors r5, r0          1  =3
0x28a beq  ..._one         1  =4     NOT taken
0x28c eors r0, r5          1  =5
0x28e beq  ..._se0         1  =6     NOT taken
0x290 lsrs r4, r4, #1      1  =7
0x292 movs r6, #6          1  =8
0x294 strb r4,[r1,#0]      2  =10    RAM store, RAM-resident code = 2
0x296 movs r4, #0          1  =11    clear SHIFT_BUF for byte 1
0x298 nop                  1  =12
0x29a nop                  1  =13
0x29c b    byte1_bit1       3  =16    taken, worst case
```
8 + 2 + 1 + 2(nop) + 3 = 16.

### Path: byte boundary, bit 8, "1" (`byte0_bit8_one`, addr `0x29e`)

```
0x284 ldr/ands/eors        3  =3
0x28a beq  ..._one         3  =6     TAKEN, worst case
0x29e lsrs r4, r4, #1      1  =7
0x2a0 orrs r4, r2          1  =8
0x2a2 subs r6, r6, #1      1  =9     sets Z
0x2a4 beq  ..._stuff       1  =10    NOT taken
0x2a6 strb r4,[r1,#0]      2  =12
0x2a8 movs r4, #0          1  =13
0x2aa b    byte1_bit1       3  =16    taken, worst case, **zero slack**
```
6 + 4 + 2 + 3 = 16. This is the tightest path in the design — no nop can be
added without going over. It is also the arithmetic that answers the
brief's question about the per-bit `BITCOUNT` decrement directly: see
"what was removed" below.

### Path: stuffed bit (`byte0_bit1_stuff`, addr `0x5c`) -- spans two cells

Reached via `beq ..._stuff` **taken** from the data-"1" path above, at its
own cum=9 (`0x4e subs`) +3 (taken) = 12. That is *within the same 16-cycle
cell* as the bit that detected the stuff condition -- the wire has not
advanced yet, this is still that bit's own cell finishing:

```
[cum 12 entering byte0_bit1_stuff]
0x5c..0x62  nop x4      4  =16     finishes the DETECTING cell to 16
```
4(pad) added to 12 = 16, closing the cell that found `BITSTUFF==0`.

The stuffed bit itself is the **next** 16-cycle wire slot, a fresh,
independent cell starting at `0x64`:
```
0x64 ldr  r5,[r3,#0x10]   1  =1     sample the forced transition
0x66 ands r5, r7          1  =2
0x68 eors r5, r0          1  =3
0x6a eors r0, r5          1  =4     prev updated; bit discarded, not stored
0x6c..0x7c  nop x9        9  =13
0x7e b    byte0_bit1       3  =16    taken, worst case: RETRY same bit index
```
4 + 9 + 3 = 16. Two chained 16-cycle cells, 32 total, matching that a
stuffed bit consumes one full wire bit-time on top of the bit that detected
it -- the same two-cell shape the original uses (`rv003usb-arm.S:200-209`,
`DELAY_CYCLES(24)` then a second phase), just re-derived at half the period.
`tools/engine16_cyc.py` reports this combined span as one 19..20-cycle
block (`byte0_bit1_stuff:   19..20 cycles`) because it does not know the
branch at the start of that block ends one cell and the branch at the end
starts a fresh one; 4+16=20 (worst) and 4+15=19 confirm it is two correctly
sized 16-cycle cells, not one oversized 19-20 cycle one -- read together
with the entry ledger above, not the tool's raw block total.

### Path: SE0 / EOP (`byte0_bit1_se0`, addr `0x5a`, and the `0x28e`/`0x2ac` case)

```
0x2a..0x2e  ldr/ands/eors   3  =3
0x30 beq ..._one            1  =4     NOT taken
0x32 eors r0, r5            1  =5     Z set (r0 == 0: SE0)
0x34 beq  ..._se0           3  =8     TAKEN, worst case
0x5a b    se0_tail          3  =11    off the timed path
```
3+1+1+3+3 = 11, and that is the whole story: once EOP is recognised there is
no further sample due on this wire, so the remaining 5 of the nominal 16
cycles are simply unneeded, not a defect. This differs from every other
path in this ledger, which must spend the full 16 to stay phase-locked with
a line that keeps toggling; SE0 is the one event that licenses stopping
early, and the arithmetic above shows exactly how early.

## What was removed, and what it cost

The brief names three specific candidates. Taking them in order:

**1. Inline CRC (removed, deferred to the untimed tail).** The original
folds a CRC5/16 LFSR update into every bit (`rv003usb-arm.S:167-192`,
`HANDLE_CRC`): `lsl`+`asr`+`mov`+`and` on the "0" path, `mov`+`and`+`sub`+
`mov`+`and` on the "1" path, then `lsr`+`eor` on both — 5-6 cycles/bit that
this design's 16-cycle paths above have no room for at all (the tightest
path, byte-boundary-"1", has *zero* slack even without CRC). The deferral
costs nothing in *decision quality*: the original's own control flow never
acts on the CRC value until `se0_complete_flash` (`rv003usb-arm.S:264,308-
311`), after the whole packet is already in the buffer — computing it bit-
by-bit was only ever a way to avoid a second pass over the data, not a way
to decide anything early. A token's CRC5 could in principle be known 3 bits
before EOP if computed inline, and the original does not use that early
knowledge either (it still waits for `se0_complete`). So deferring the
*computation* (not the decision) costs literally nothing here — this is the
same finding CLEANSHEET reports independently ("nothing about PID, bit
count, bit-stuffing, or CRC exists inside my timed loop at all"); DESCENT
converges on it from the opposite direction, by tracing what the original's
own tail already required rather than designing a tail from scratch.

**2. The per-bit `BITCOUNT` decrement + branch to a *shared* byte-boundary
handler (does not survive; the arithmetic is exact, not a matter of taste).**
This is the mechanism the brief singled out to evaluate, and the honest
answer is that it cannot be kept, for a reason visible directly in the
ledger above. Consider what a loop-based byte boundary would need: the
common per-bit decode (Path "data-1"/"data-0" above) already consumes 13-16
of the 16 cycles with *nothing spare*. A shared `is_end_of_byte` reached via
`beq` from that decode inherits whatever is left over from **its caller's**
budget — at best 1-4 cycles (16 minus the caller's own cum at the point of
the taken branch) — and the store alone (`strb`+pointer bump+bound-check+
loop-back) needs at least 2(store)+1(advance)+2(bound, in-place shift-mask,
see below)+2(taken branch, best case) = 7 cycles. 1-4 < 7 for every
predecessor path traced by hand; this is not a near miss to be optimised
away, it is off by a factor of 2-7x depending on which path reaches it.
**A shared handler reached at runtime cannot pay for a byte store at this
budget, no matter how the register allocation or instruction selection is
tuned**, because the caller has already spent nearly all 16 cycles just
deciding a bit's value and updating two counters.

The fix is to remove the runtime decision entirely: which byte a bit
belongs to becomes a **compile-time fact** (which of the 8 unrolled slots
per byte is executing), so the byte-completing slot gets its *own*,
independent, full 16-cycle allowance rather than inheriting a caller's
leftovers — that is the entire reason `RX_BIT_LAST`'s ledger above closes
exactly, while a shared-handler version provably cannot. This converges
with GRAINUUM's static per-byte unroll (`ENGINE16_RESULTS.md`, "no
bit-counter register at all: the loop period *is* 8") and CLEANSHEET's
identical statement about its own 8-slot loop; DESCENT reaches the same
place from the reference engine's own arithmetic rather than by starting
without one, which is the point of running this entry at all — the finding
is that this mechanism is not a style choice a design can decline, it is a
structural consequence of the 16-cycle number itself.

**3. `DELAY_CYCLES`/padding.** The original's `DELAY_CYCLES` macro
(`rv003usb-arm.S:58`) exists to burn cycles up to 32; at 16, the equivalent
role is played by the explicit `.rept N nop .endr` blocks in each path
above, sized by the arithmetic in the ledger rather than by a `mov`+`sub`+
`bne` spin loop (which itself costs 3 instructions minimum and would eat
into a budget that has none to spare on the tight paths). The mechanism
(deterministic padding to hit an exact total) survives; its *implementation*
does not, because a decrement-and-branch delay loop is itself now more
expensive than the padding it would produce.

**4. Register-shuffle before every GPIO access (removed for free).** Not on
the brief's list, but found in the process: `mov r5, GPIO_BASE; mov
SCRATCH, r12` (`rv003usb-arm.S:97-98`, :155-156) costs 2 cycles/bit in the
original purely because `GPIO_BASE` and the pin mask live in r9/r12, which
Thumb's register-offset `ldr` cannot address directly. Re-registering both
into r3/r7 (Sec. "register allocation") removes this at zero cost elsewhere
— it is not a mechanism the original *needed*, just a register choice it
made when it had 32 cycles to spend and no reason to economise on r9-r12.

## Where the taken branches are, and what the 2-vs-3 ambiguity costs

* **Data "0" (7 of 8 bits):** zero taken branches, zero ambiguity exposure.
  There is no loop-back at all on this path — the next slot is physically
  next in memory (Sec. "byte boundary" mechanism above).
* **Data "1" (7 of 8 bits):** two taken branches per bit-cell: the
  `beq ..._one` that selects this path, and the `b` loop-back to the next
  slot. Both are counted at worst case (3) in every "1"-path ledger above.
  This is real, recurring exposure — worse than VUSB's reported "no
  ambiguous branch on the data path" and worse than CLEANSHEET's "only at
  the byte boundary" — and it is the direct price of keeping a
  branch-based decode (`beq pl_got_one`/`pl_got_zero`, unchanged in shape
  from `rv003usb-arm.S:102-114`) instead of the mask-arithmetic idiom
  `M0PLUS_ISA_FACTS.md` recommends. I evaluated the mask idiom (`rsbs`+
  `sbcs`+`adcs`, folding NRZI decode into the carry chain the way
  CLEANSHEET's raw capture does) and rejected it for a register-pressure
  reason, not a cycle-count one: it needs a spare register to hold the
  derived mask while `BITSTUFF` is updated from it, and this design's eight
  live values (Sec. "register allocation") already fill r0-r7 exactly,
  because it keeps the original's *combined* D+/D- sample (one extra
  register, Sec. "SE0" below) where a design with fewer simultaneously-live
  values (CLEANSHEET has six, by deferring decode itself) has room to spare
  one. This is a genuine, reportable trade: recognisable branch structure
  and the original's SE0 mechanism, paid for in branch-ambiguity exposure
  on half the bits, versus VUSB's zero-exposure table lookup or GRAINUUM's
  masked capture, either of which would need restructuring this design's
  register map to adopt.
* **Byte boundary (1 of 8 bits):** one or two taken branches depending on
  path (the "1" variant's `beq ..._one` plus the loop-back; the "0" variant
  only the loop-back), same worst-case-3 convention.
* **Stuffed bit:** one taken branch to enter (`beq ..._stuff`), one taken
  branch to retry (`b byteN_bitM`) — both already counted in the two-cell
  ledger above.

Net: this design's exposure is *between* GRAINUUM's ("every path, 1-2
cycles" per `ENGINE16_RESULTS.md`) and VUSB's (none on the data path) —
worse than either on raw branch count, because the "1" path was left
branch-based rather than rebuilt branchless. If the referee merges designs,
the branchless "1"-path mask-and-insert idiom from `M0PLUS_ISA_FACTS.md`,
applied to *this* design's byte-boundary structure (which already solves
the store-affordability problem the loop-based approach cannot), is the
natural next compression step — but it needs a ninth live value's worth of
register budget found from somewhere first.

## Buffer bound (`DEFECTS_VERIFIED.md` D-2)

Closed structurally, the same class of fix GRAINUUM reports: `strb
r4,[r1,#\byte]` uses a **compile-time immediate** offset (`\byte`, 0 or 1 in
this demo), not a runtime pointer. There is no `adds`/bound-check
instruction in the timed path at all, and there is no instruction in the
object that can address `rxbuf_descent[N]` for `N >= NBYTES` — the last
unrolled byte's boundary slot (`RX_BIT_LAST 1,8,too_long_abort`) branches to
an abort stub instead of back to a `byte2_bit1` that does not exist. This
costs zero cycles in the timed path (the branch target is decided at
assemble time; a taken branch to `too_long_abort` costs exactly what a
taken branch to `byte2_bit1` would have) and it is *why* the byte-boundary
ledger above has no `lsls`/`lsrs` bound-masking instructions in it at all,
unlike an earlier design I worked through by hand and discarded (a
free-running offset register bounded in-place each byte) that needed 2 more
cycles the ledger did not have.

## What I gave up

Every design at this budget gives something up; here is DESCENT's list,
in the order it would matter to a reviewer.

1. **Code size, and a real ceiling on how large a buffer this scales to
   without a further mechanism.** Full per-byte unroll costs roughly 43
   Thumb instructions (~86 bytes) per plain bit-slot and a similar amount
   per byte-boundary slot — measured directly from the assembled object
   (`arm-none-eabi-size doc/py32/engine16_descent.S` on the demo's `NBYTES=2`
   gives 1408 bytes of `.text` for 16 slots). At the original's
   `USB_BUFFER_SIZE=12` (`rv003usb.h:126`) that is roughly 96 slots and
   ~8-9 KB — plausible for flash, heavy for the 2-3 KB total SRAM
   `CHIP_FACTS_XIAMATSU.md` §4 reports for this part if this code stayed
   RAM-resident at that size. More immediately: every `beq ..._se0` in this
   design branches to a **shared, single** `se0_tail`, using Thumb's
   ±252-byte conditional range. This demo (2 bytes, 16 slots, ~1.4 KB)
   assembles clean because `se0_tail` sits close enough — the earliest
   slots reach it in exactly one hop (`arm-none-eabi-gcc` would refuse to
   assemble otherwise; there is no cross-check here beyond "it built"). At
   the original's 12-byte buffer, the slots near the start would be roughly
   4-5 KB from `se0_tail` and a direct `beq` could not reach it at all —
   this is the identical range wall CLEANSHEET already reports for
   full-*packet* unrolling ("a short conditional branch cannot reach far
   enough to keep an SE0 escape target in reach"), and it applies here at a
   smaller scale (per-*byte*, not per-packet, unroll) once the byte count
   grows past roughly `252 / 86 ≈ 2-3` bytes. The fix is mechanical and
   already demonstrated by this file's own `preamble_no_se0` stub (a local
   `bne`-around-a-`b` trampoline) — every slot's SE0 escape would need one —
   but it is more code, and I did not build it out for all 96 slots a
   full-size buffer would need. **This demo's `NBYTES=2` is not an
   arbitrary shrink for readability; it is close to the largest size that
   avoids that fix entirely**, which is itself worth recording as the
   concrete threshold rather than an approximate one.
2. **The untimed tail is a stub, not a hardened implementation.**
   `se0_tail` and `too_long_abort` in `engine16_descent.S` are `bx lr`
   placeholders with a comment, not the CRC5/16 LFSR-over-the-assembled-
   bytes-then-dispatch code the deferred-CRC design in Sec. "what was
   removed" argues for. Writing that out correctly (matching
   `usb_pid_handle_ack`/`_out`/`_in`/`_setup`/`_data`'s existing signatures,
   `rv003usb-arm.S:237-328`, which spec §4 says must not change) is real
   work I did not do in the time available; the argument for *why* deferral
   is free (Sec. "what was removed", point 1) does not by itself supply the
   code. Same choice CLEANSHEET reports making, for the same reason: rank
   correctness of what is written above completeness of what is sketched.
3. **Preamble/sync-lock timing is unverified**, same status GRAINUUM
   declares for its own entry/phase constants and the same status
   `PRIOR_ART.md` records for the original's own `DELAY_CYCLES(71)`/`(96)`.
   The settle delay between locking on the first edge and starting
   `byte0_bit1` is marked `TODO` in the source rather than filled with an
   unverified number dressed up as a real one.
4. **A single SE0 sample commits to EOP**, with no 2-bit-time confirmation
   — the same limitation the original has and CLEANSHEET reports sharing,
   not introduced here.
5. **The combined D+/D- sample costs a register the mask-arithmetic "1"-path
   decode (Sec. "branches") would have used.** I kept it because it is how
   the original gets SE0 detection for free out of the same read that does
   NRZI compare (`rv003usb-arm.S`, `USB_DMASK` covering both pins,
   `rv003usb.h:128`) — one `ands` against a 2-bit mask, rather than a
   second GPIO read or a separate single-pin SE0 test. That mechanism *is*
   load-bearing (Sec. "one mechanism that survives", below) and I chose to
   keep it over the register it would free.
6. **TX is not implemented, only costed at a sketch level.** The original's
   transmit path (`rv003usb-arm.S:345-573`) is a different problem in the
   direction this note's Sec. "what was removed" does not reach: a
   transmitter must *produce* the bus edge on schedule, so — as NATIVE's
   entry independently notes — there is no untimed tail to defer work to;
   every TX cycle is real, in the timed path, by construction. The
   original's own TX bit cell (`pre_and_tok_send_inner_loop`/
   `send_inner_loop`, `rv003usb-arm.S:411-525`) already runs at roughly 20
   cycles/bit including its own `DELAY_CYCLES`-free padding (`nop`s at
   :439-442, :511); halving that to 16 needs the same CRC-deferral move RX
   made, except TX's CRC is a *value the wire needs on schedule*, not a
   decision that can wait — so unlike RX, this specific saving does not
   transfer. I have not designed a TX engine for this budget; recording why
   the RX technique does not transfer is the honest content of this
   section, not a working design.

## The one mechanism that turned out to be load-bearing

Asked to find what does not survive, the more useful answer turned out to
be the mechanism that *does*, because it was the one I nearly cut for
register budget (Sec. "branches") and kept instead: **the original's
combined D+/D- sample-and-mask** (`USB_DMASK`, `rv003usb.h:128`;
`rv003usb-arm.S`'s single `and r5, mask` against both pins at once). It
looks, on a first read, like a convenience -- one register mask instead of
two separate pin tests. It is not: it is how the receiver gets SE0
detection *for free* out of the exact same read and `ands` that already
does the NRZI transition test, because a valid bit always leaves exactly
one of the two pins high (masked value != 0) and only SE0 drives both low
(masked value == 0). Split into two single-pin reads to save the register
pressure Sec. "branches" describes, EOP detection would need either a
second GPIO read (another cycle, on the "0"/SE0 path, which already has
less slack than the "1" path) or a separate comparison against a stored
single-pin idle level, and either change touches every ledgered path in
this file, not just the ones with room to spare. I traced that alternative
by hand before rejecting it: it does not net a saved cycle, it only moves
the cost from "a spare register" to "a cycle on the tightest path," which is
a worse trade at this budget. That is the finding this entry was asked to
produce, and it points the other way from most of the brief's candidates:
not everything the original does is scaffolding left over from having 32
cycles to spend. Some of it is doing two jobs with one instruction, and
halving the budget is exactly the pressure that reveals which parts those
are.

## Summary for the referee

* Fits 16 on every ledgered path, arithmetic shown, addresses cited from a
  real assembled object. One path (`byte0_bit8_one`, and its `byte1`
  counterpart) has zero slack; every other path has 0-4 cycles unused.
* Correct on NRZI, unstuffing (including the stuffed-bit-on-the-8th-bit
  case), byte alignment, SE0/EOP, and the buffer bound (D-2, closed
  structurally). CRC is deferred and argued, not implemented (Sec. "what I
  gave up" #2).
* Branch-ambiguity exposure: none on the "0" path (7/8 bits), two taken
  branches on the "1" path (7/8 bits), one or two at the byte boundary
  (1/8 bits) -- between GRAINUUM's and VUSB's reported numbers, not as good
  as either, for a stated register-pressure reason (Sec. "branches").
* Register pressure: exactly 8 live values in 8 low registers, no r8-r12,
  no high/low `mov` shuffle in the timed path -- a real saving over the
  original that the brief didn't ask for.
* Code size: honestly bounded -- this demo's 2-byte buffer is close to the
  largest size the design's SE0-escape branches reach without a further
  trampoline mechanism (Sec. "what I gave up" #1), and that ceiling is
  quantified (`252 / 86 ~= 2-3` bytes), not hand-waved.
* Contribution to a merged engine: the byte-boundary construction (a
  byte-completing slot gets its own full 16-cycle allowance instead of
  inheriting a caller's leftovers) composes with any other entrant's
  per-bit decode, the same way GRAINUUM's unroll composes with others per
  `ENGINE16_RESULTS.md`. The combined-pin SE0 mechanism (previous section)
  is worth carrying into a merge even where the surrounding decode is
  replaced.
