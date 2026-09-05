# engine16_merged — the referee's merge

One engine, built from the best mechanism in each of the six entries, against
`ENGINE16_SPEC.md`. Files: `engine16_merged.S` (assembles rc=0 with
`arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c`),
this note.

**Result: every bit cell is exactly 16 cycles, with no taken conditional
branch and no `b` anywhere on the data path.** The only control transfer in
the timed loop is one `bx` per wire byte, which the cost table prices at a
flat 3 (`ENGINE16_SPEC.md` §2), not the 2-3 of a `B`. Three of the five paths
the spec asks about — data 1, data 0, stuffed bit — are not merely equal in
cost, they are *the same instructions*: nothing in a cell looks at the value
of the bit it sampled.

## 1. The idea, in one paragraph

Unroll on **wire** bits, not data bits. A wire bit has no data dependency at
all: eight cells sample eight levels and pack them into a shift register, and
that is all a timed cell does — 5 cycles of the 16. The whole data domain
(NRZI, unstuffing, byte assembly, CRC16, the store) runs one wire byte behind,
cut into seven pieces that ride in the 11 spare cycles of the eight cells. Bit
stuffing, the thing that makes unrolling hard, never appears as control flow:
a table indexed by (unstuff state, decoded nibble) returns the bits that
survive, how many there are, and the next state, in one 2-cycle RAM load. This
is VUSB's chassis, re-cut and repaired.

## 2. VUSB's table, reverse-engineered — the mechanism that buys exactness

VUSB died before writing its note, so `ENGINE16_RESULTS.md` records that the
table's construction had to be recovered from the `.S` before the mechanism
could be reused. That was done first, and it is the reason this merge has a
chassis at all.

`usb_tables` is indexed `state*32 + nibble*2` and each halfword is

```
entry = m<<11 | n<<8 | newstate<<5        (engine16_vusb.S:595-616)
```

* `state` (0..6) is the count of consecutive decoded 1s; 7 is a sticky
  stuffing-violation row.
* the nibble is four NRZI-**decoded** bits, oldest at bit 3.
* `n` (0..4) is how many of them survive unstuffing.
* `m = kept_bits + 2^n - 1`, where `kept_bits` has the oldest bit at bit 0.

The bias is the whole trick. The accumulator holds data in bits `0..r0-1` and
a sentinel `1` at bit `r0`. Then

```
acc + (m << r0)
  = data + (1<<r0) + (bits<<r0) + ((2^n - 1)<<r0)
  = data + (bits<<r0) + (1<<(r0+n))
```

— one `adds` writes a *variable* number of bits at the sentinel and moves the
sentinel up by exactly that number. No branch, no second accumulator, no
"how many bits do I have" test. Combined with `lsls r1, r1, r0` to align it,
appending a nibble's worth of unstuffed data costs 2 instructions.

**Verified, not assumed.** All 128 entries of VUSB's T_UT were regenerated
from an independent model of the rule above and compared: **128/128 match**
(the generator is reproduced in §12). Only after that was the mechanism
reused.

The second half of the answer to "how does it reconcile unrolling with bit
stuffing" is in `SEGA`:

```
lsrs r1, r5, #1 ; eors r1, r5 ; mvns r1, r1 ; uxtb r1, r1
```

The predecessor of sample *i* is bit *i+1* of the same register, so one shift
and one xor NRZI-decode a whole byte — and bit 8, the last sample of the
*previous* wire byte, supplies the predecessor of this byte's first bit, so
the byte boundary in the NRZI domain costs nothing. An 8-bit machine cannot do
this; it is why the wire-domain unroll pays on this core and not on the AVR
the lineage came from.

## 3. Composing the two answers: the fused mask vs. the table

`BALANCE`'s fused mask and `VUSB`'s table are two answers to one question —
how to decode without branching — so they cannot simply both be adopted. The
arithmetic decides it, and it is not close.

BALANCE's core, per **bit** (`engine16_balance.S:70-84`):

```
lsls r6,#(31-PIN_DM) ; asrs r6,#31   2   transition mask
eors r6, r2                          1   NRZI delta
eors r2, r6                          1   prev := new
adds r4,#1 ; bics r4, r6             2   ones-run counter
lsrs r6, r6, #31 ; adcs r3, r3       2   byte insertion via carry
                                    ==8 cycles/bit, 64 per byte
```

and that 8 does **not** include removing a stuffed bit (BALANCE branches out
to `stuff_bit` for that), does not include the store, and does not include
CRC16.

The merged pipeline, per **wire byte**:

```
SEGA  NRZI for all 8 bits          8
SEG0  nibble -> table, state       7      \  unstuffing, byte assembly and
SEG1  append + second lookup      11      |  the removal of stuffed bits,
SEG2  append                       9      /  two nibbles, no branch
SEG3  bounded store + commit mask 11
SEG4  CRC16 byte step              9
SEG5  drop, advance, bound, gate  11
SEG6  commit CRC or not           10
                                 ==76 cycles per byte, CRC and store included
```

76 with CRC and the store against 64 without either: the table subsumes the
fused mask. Where the mask needs one instruction per bit per consumer, the
table serves four bits per lookup and folds three consumers (NRZI-decoded bits
in, stuffing state, kept bits out) into one 2-cycle access. **The fused mask is
rejected as a decode mechanism**, and BALANCE's real contribution to this merge
is elsewhere (§6).

Two pieces of BALANCE's core do survive, because VUSB and CLEANSHEET
independently arrived at the same instructions: `lsrs`-to-carry + `adcs` as
the capture. In the merge that pair captures the **raw wire level**, not the
decoded bit, which is what makes it free of data dependence.

The unroll composes with either answer, exactly as `ENGINE16_RESULTS.md`
predicted: it is a statement about layout. Here it is load-bearing twice — it
makes "which cell of the byte" a compile-time fact, which is what lets each
cell carry a *different* pipeline segment and its own EOP stub.

## 4. The cycle ledger

Placement: **RAM-resident** (`.datacode`, the section this project already
copies to SRAM). RAM column of `ENGINE16_SPEC.md` §2: ordinary instruction 1,
GPIO load 1 (IOPORT), RAM load/store 2, `bx` 3, conditional branch 1 not taken
/ 2-3 taken, `b` 2-3. The tables are in the same section, so a table lookup is
a 2-cycle RAM load, and `strb` to `rxbuf` is 2. Both are load-bearing: from
flash-resident code they would cost 4 each and the pipeline would not fit.

The ledger is **assembled, not asserted**. The `CELL` macro pads the cell that
is running out to exactly 16 and raises `.error` if it went over; `CELL_END`
closes the last cell, which VUSB's version of the same device never checked.
A miscount is a build failure.

### 4.1 The capture, identical in all eight cells (5 cycles)

```
  108  ldr  r2, [r7, #16]   1   IDR over IOPORT (DESCENT: one read serves both)
  10a  ands r2, r6          1   Z <=> SE0, free: only SE0 drives both pins low
  10c  beq  rx_eop3         1   NOT taken on every data path; 1 cycle, no ambiguity
  10e  lsrs r2, r2, #4      1   C = D+
  110  adcs r5, r5          1   carry-chain capture of the RAW wire level
```

Nothing here inspects the value. That is why *data 1*, *data 0* and *stuffed
bit* are one path, not three.

### 4.2 The eight cells

| cell | capture | pipeline segment | nop | total |
|---|---|---|---|---|
| 0 | 5 | SEG0 high nibble -> table, state — 7 | 4 | **16** |
| 1 | 5 | SEG1 append, second lookup — 11 | 0 | **16** |
| 2 | 5 | SEG2 append, fetch rxbuf base — 9 | 2 | **16** |
| 3 | 5 | SEG3 bounded store, commit mask — 11 | 0 | **16** |
| 4 | 5 | SEG4 CRC16 byte step — 9 | 2 | **16** |
| 5 | 5 | SEG5 drop, advance, bound, gate — 11 | 0 | **16** |
| 6 | 5 | SEG6 commit CRC or not — 10 | 1 | **16** |
| 7 | 5 | SEGA NRZI 8 bits — 8, then `bx r14` — 3 | 0 | **16** |

`5*8 + 76 + 9 nop + 3 (bx) = 40 + 76 + 12 = 128 = 8 x 16.` Every cycle is
accounted; the 9 nops are the slack, and they are real slack, not rounding —
the design fits with 9 cycles to spare out of 128, which is where a future
mid-packet resync or a cheaper CRC would go.

Cell 3 in full, from `objdump` through `tools/engine16_cyc.py --exec ram
--ioport r7` (the tool charges the conditional branch 1..3 because it does not
resolve control flow; on the data path it is 1, and the block minimum of 16 is
the number that matters):

```
  108 ldr  r2,[r7,#16]    1  =1     108 .. 110 is the capture
  10a ands r2, r6         1  =2
  10c beq  rx_eop3        1  =3     not taken
  10e lsrs r2, r2, #4     1  =4
  110 adcs r5, r5         1  =5
  112 mov  r2, ip         1  =6     emitted byte count
  114 lsls r2, r2, #27    1  =7     \  index &= 31 - BALANCE's structural bound
  116 lsrs r2, r2, #27    1  =8     /
  118 strb r3, [r1, r2]   2  =10    speculative store, always inside rxbuf[32]
  11a subs r0, #8         1  =11    <0 <=> byte not finished
  11c asrs r1, r0, #31    1  =12    -1 if not finished
  11e mvns r1, r1         1  =13    commit mask: -1 finished / 0 not
  120 movs r2, #8         1  =14
  122 bics r2, r1         1  =15    8 if not finished, else 0
  124 adds r0, r0, r2     1  =16    bit count restored, or left at -8
```

### 4.3 The five paths the spec asks for

| path | cycles | why |
|---|---|---|
| data 1 | 16 | the cell above; the value is not examined |
| data 0 | 16 | *the same instructions*, byte for byte |
| stuffed bit | 16 | *the same instructions*; the stuffed bit is removed a wire byte later, inside SEG0/SEG1/SEG2, at no extra cost — the table returns `n=3` instead of `n=4` |
| byte boundary | 16 | there is no byte boundary in the timed path. The store is in cell 3 of *every* wire byte's pipeline, executed unconditionally, committed by a mask (`asrs`/`mvns`/`bics`), never by a branch |
| SE0 / EOP | 5, then a taken `beq` (2-3) and out of the timed path forever | the only ambiguity in the engine, and it is on the path that stops timing |

The byte-boundary row is the merge's answer to DESCENT's proof that a shared
byte-boundary handler cannot work at this budget: there is no handler, shared
or otherwise, because the boundary is not an event. DESCENT's arithmetic
(a shared handler inherits 1-4 cycles of its caller's budget, a store needs 7)
is what rules out the alternative; the wire-domain unroll makes the question
disappear.

### 4.4 Entry and phase lock (untimed, but the phase is not)

```
poll loop      subs, untaken beq, ldr, lsls, taken branch   6..7 cycles/iteration
edge caught    fall-through, so the exit costs 0 and its 2-vs-3 never lands
               in the phase                                  t0 + 3.5 +- 3.5
20 nops + ldr  confirming sample                             t0 + 23.5
lsls, untaken bpl                                            3
priming        9 setup + 5 nop (asserted: usb_rx_chain - .Lprime == 28)  14
first sample of cell 0                        t0 + 23.5 + 3 + 14 = t0 + 40.5
```

`t0` is the start of SYNC cell 6; the first PID cell runs `[t0+32, t0+48)`, so
its centre is `t0+40`. The nominal sample point is 0.5 cycles late with a
±3.5 cycle spread from the poll granularity — ±3.5 of a ±8 window, leaving
4.5 cycles for entry jitter and drift. The `.if`/`.error` in the source
enforces the 14, because a `.balign` that silently adds a `nop` would move the
sample point and nothing would complain.

VUSB's version of this spent 7..9 cycles per poll and used a *loop* for the
delay, multiplying the 2-vs-3 by four; both were replaced. The constants are
still first-order — the same status GRAINUUM declared for its own — and the
exception entry latency itself is not measured here.

## 5. The branch-ambiguity question, settled

The taken-branch cost is an unknown constant of the part, not run-to-run noise
(`ENGINE16_RESULTS.md`), so the question is not "is it 2 or 3" but "does this
engine's timing depend on the answer".

**It does not, on any timed path.** In the whole eight-cell chain there are
exactly nine control transfers:

* eight `beq rx_eopN`, one per cell, **not taken** on every data path: 1 cycle,
  a single value, no ambiguity. When one is taken the packet is over and the
  engine leaves the timed path for good, so the 2-vs-3 lands where nothing is
  timed any more.
* one `bx r14`, the back edge, once per wire byte. `ENGINE16_SPEC.md` §2 and
  `CHIP_FACTS_XIAMATSU.md` §1 price `BX` at a flat **3**, while `B` is given as
  a range 2-3. That is why the back edge is a `bx` and not a `b`, and it is the
  one place where the merge's exactness rests on a documented number rather
  than on a not-taken branch.

**So: exact without any new measurement, on the one assumption that `BX` is 3
as both sources state.** What needs measuring on the bench, in priority order:

1. **Is `bx` really a flat 3?** The vendor gives it as a single value and gives
   `B` as a range, so the asymmetry is theirs, not an assumption of mine. If
   `bx` is really 2-3, cell 7 becomes 15..16 and the fix is one `nop` once the
   value is known — no other cell is affected and the design does not change.
2. Is a not-taken conditional branch really 1? Every cell contains one; if it
   is not, all eight cells shift together, and the fix is again one `nop` per
   cell.
3. The two data costs the pipeline leans on: `ldrh` from RAM = 2 and `strb` to
   RAM = 2, from RAM-resident code.

Compare: GRAINUUM and DESCENT carry the ambiguity on *every* path, CLEANSHEET
on one bit in eight, BALANCE in its loop overhead. This merge carries it on
none, which is what `ENGINE16_SPEC.md` §6 criterion 3 asks for.

## 6. The buffer bound is structural — and so is termination

`DEFECTS_VERIFIED.md` D-2 is closed twice over, and neither closure is a
runtime check on the data path.

**Addressing (BALANCE's mechanism, `engine16_balance.S:26-27,105-107`).**
`rxbuf` is 32 bytes, 32-byte aligned; the store is `strb r3, [r1, r2]` with
`r1` the base and `r2` the emitted-byte count masked to five bits by
`lsls #27 / lsrs #27`. **No instruction in the object can address `rxbuf` out
of range**, for any input whatever, including a bus held hostile forever. It
costs 2 cycles, in a cell that had slack, and — unlike a compare-and-branch —
it cannot be wrong about *which* addresses are legal.

GRAINUUM's version of the same guarantee is the full-buffer unroll
(`engine16_grainuum.S:59-62`): a block per buffer byte, the last exiting to an
abort. It is a stronger property (it bounds the *count* as well), and it was
**rejected on RAM**: a block here is ~30 bytes per cell x 8 = 240 bytes, so 12
blocks is 2.9 KB of a 3 KB part (F002B), for RAM-resident code. That is the
wall CLEANSHEET hit from the branch-range side, reached again from the
footprint side. What is kept from GRAINUUM is the per-*byte* unroll, which the
whole chassis is built on.

**Termination.** A masked index makes the store safe but does not stop the
loop, and a chain with a back edge must terminate: `SEG5` carries
`cmp r2, #24 / bhs` — 2 cycles, the branch not taken on every data path. 24 <
32, so the count bound sits inside the address bound.

That check exposed a real defect in VUSB, verified rather than suspected. Its
violation row (`engine16_vusb.S:608-616`, sixteen entries of `0x00e0`) emits
**zero** bits, so during a stuffing violation the emitted-byte counter freezes,
its bound (`engine16_vusb.S:180-181`) can never fire, and only SE0 can end the
chain. A bus left in a steady J or K after a spurious edge decodes as an
endless run of 1s → violation → **the ISR spins forever**. The merged table's
row 7 keeps its four bits (`n=4`, state stays 7), so the counter keeps
advancing, the bound fires after 24 emitted bytes ≈ 128 µs, and the sticky
state still rejects the packet in the tail. Simulated:
`stuck bus: overrun=True emitted=24 state=7`.

## 7. Correctness, checked rather than argued

Cycle counts are checked by the assembler and by `tools/engine16_cyc.py`.
Semantics are checked by a bit-exact model of the *instructions in the `.S`* —
same registers, same order, same masks — driven by host-encoded packets (§12):

```
305 packets, 0 failures
stuck bus: overrun=True emitted=24 state=7
```

The 305 include the empty DATA packet, an all-zero 8-byte payload (maximum
stuffing: a stuffed 0 every seven bits), an all-ones payload, DATA0 and DATA1,
and 300 random payloads of 0..8 bytes. For each, the model checks that `rxbuf`
holds `0x80` (SYNC), the PID and the payload byte for byte, that the running
CRC16 ends at the residue **0xB001**, that the emitted-byte count is exact and
that no stuffing violation was raised. Both CRC tables were regenerated from
bitwise references and both residues confirmed numerically (0xB001 over
message+CRC16; 0x06 over the 11 token bits + CRC5), so VUSB's two magic
constants are now verified rather than inherited.

### 7.1 How to read the tool output on this engine

```
arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c \
    doc/py32/engine16_merged.S -o e.o
python3 tools/engine16_cyc.py e.o --exec ram --ioport r7 --budget 16
```

prints, for each cell, `16..18` (cell 5: `16..20`) and flags them over budget.
That is the tool doing its job, not a failure: it does not resolve control
flow, so it prices each `beq rx_eopN` at 1..3 as if it might be taken, and cell
5 also contains the `bhs` bound. **On this engine the minimum is the data
path**, because every conditional branch in a cell is not-taken on every data
path and, when taken, leaves the timed chain for good. All eight minima are
exactly 16, and the maxima are the EOP exits.

`--ioport r7` matters: r7 holds the GPIO base, and without it the tool prices
the IDR read as a 2-cycle RAM load and every cell reads 17.

Control flow was traced in raw `objdump`, not only through the block tool.
Two things that only show up there:

* **A fall-through defect of my own**, in the same class as the one caught in
  DESCENT. Placing the cell 0-3 EOP stubs immediately before `.Lk_edge` put
  them in the fall-through path of the K-detecting `bmi`, so a successful phase
  lock jumped straight into the EOP handler. Found at `0x46 bmi.n` falling into
  `0x48 <rx_eop0>`; fixed by moving the stub block behind the unconditional
  branch in `.Lgiveup`, where nothing can fall into it.
* **Branch ranges are only proved if the target is local.** With
  `.global usb_rx_eopN`, gas emits `R_ARM_THM_JUMP8` relocations and defers the
  ±254-byte range check to the linker; the object then contains placeholder
  encodings and `objdump` shows the symbolic target, which looks fine and
  proves nothing. Making the EOP and flush labels local removed all eight
  relocations, so the assembler now range-checks every conditional branch in
  the engine, and a layout change that pushes a stub out of reach is a build
  error. (Related trap, same family as GRAINUUM's spliced `nop`: an
  *undefined* `.L` label does not fail assembly either — it becomes an
  undefined symbol and fails at link. Two of these were caught here.)

## 8. What came from whom

| mechanism | from | why it beat the alternative |
|---|---|---|
| wire-domain unroll + one-byte-behind software pipeline | **VUSB** | the only structure in which the timed cell contains no data-dependent work at all; alternatives decode in-cell and pay 7-9 cycles/bit for it |
| register-offset table lookup, state in a high register | **VUSB** | folds NRZI-decoded nibble + stuffing state + byte assembly into one 2-cycle access; the fused mask needs ~8 cycles/bit for less (§3) |
| biased-add accumulator (`m = bits + 2^n - 1`) | **VUSB** | appends a variable number of bits with one `adds`, no branch, no second accumulator |
| combined D+/D- sample-and-mask, SE0 free from the sampling read | **DESCENT** | one `ands` gives the SE0 test that a separate check would have to pay for; DESCENT traced the alternative by hand and found it only moves the cost |
| correct PY32 GPIO base 0x50000400 | **DESCENT** | VUSB defaulted to the STM32 base 0x48000000 (`engine16_vusb.S:35`); `BUILD_FACTS.md` §7 |
| carry-chain capture (`lsrs` to carry, `adcs`) | **CLEANSHEET** (VUSB and BALANCE converged on it) | 2 cycles, no branch, no mask; here it captures the raw level, so it is value-independent |
| per-byte unroll as the granularity | **GRAINUUM**, proved forced by **DESCENT**, reached independently by **VUSB** | makes "which cell" a compile-time fact, which is what lets each cell carry a different segment and its own EOP stub; CLEANSHEET's negative result rules out going wider |
| structural buffer bound: power-of-two buffer, masked index, register-offset store | **BALANCE** (`engine16_balance.S:105-107`) | 2 cycles and a guarantee about *addresses*, where GRAINUUM's stronger form costs 2.9 KB of RAM (§6) |
| branch range is a design constraint, not a detail | **CLEANSHEET** | its negative result is why the chain is one byte long and why the EOP stubs are split around it |
| CRC verdict ready at EOP | **PRIOR_ART S-2**, kept | the turnaround budget cannot absorb a deferred CRC (§10) |

## 9. What was rejected, and why

* **BALANCE's fused mask, as a decode mechanism.** Subsumed by the table:
  8 cycles/bit without unstuff-removal, store or CRC, against 76 cycles/byte
  with all three (§3). Its *packaging* finding — that per-bit loop overhead is
  what costs it 3 cycles a slot — is what the unrolled chassis already fixes.
* **CLEANSHEET's deferred decode.** Elegant, and its capture/decode split is
  the reason its ordinary slot is exact. But the tail is not free: decoding
  ~96 bits after EOP at the ~9.5 cycles/bit this engine achieves *pipelined*
  is 700+ cycles, i.e. 45+ bit times at 24 MHz, against a response window of
  2-6.5 bit times (`PRIOR_ART` L-1, §7.1.18). Deferral moves the whole problem
  into the one place with the least room. The pipeline keeps the same
  branchless capture and does the work in cycles that were nops anyway.
* **CLEANSHEET's `push`/`pop`-as-padding.** It buys deterministic filler from
  the *flash* column's 4-cycle stack access; this engine is RAM-resident, where
  the same instructions cost 2, and it has no need of filler because the
  pipeline fills the slack with real work. Its cost — "relocating this engine
  to RAM silently breaks its timing by 4 cycles per bit" — is a coupling this
  merge does not want.
* **GRAINUUM's full-buffer unroll.** Rejected on RAM footprint (§6), not on
  correctness; the per-byte unroll it shares is kept.
* **NATIVE's peripheral front end.** Not taken. `py32f002bx5.h` has neither
  `DMA1_BASE` nor `TIM3_BASE`, so on F002B the software engine is the only
  engine, and the merged engine must stand alone there. Taking it would buy
  the deletion of the phase-lock spin — worth about 20 instructions of untimed
  entry code and ±3.5 cycles of sample-point error — at the price of a second
  acquisition path that only exists on half the family and that the timed
  chain cannot share. The finding it exists to protect *is* carried, in the
  engine's shape: GPIO is on the core-private IOPORT bus, which is exactly why
  `ldr r2,[r7,#IDR]` costs 1 cycle here and why no DMA can reach it.
* **A shared byte-boundary handler** (the reference engine's shape). DESCENT's
  arithmetic rules it out; §4.3.
* **Mid-packet resynchronisation.** Not implemented. There are 9 spare cycles
  per 8 cells, which is roughly what a phase nudge on a transition would cost,
  so it is the natural use of the remaining slack — but it is not in this
  engine and the drift budget is therefore the same one the existing engine
  relies on.

## 10. What this design gives up

1. **RAM.** 1812 bytes of `.datacode` (1012 of code, 800 of tables) plus 32
   bytes of buffer. On a 3 KB F002B that is a real cost, and it is the price of
   the 2-cycle table lookup: from flash the same lookup costs 4 and the
   pipeline does not fit. The identified fallback is a 16-entry nibble CRC16
   table, which saves 480 bytes and costs about 9 more cycles per wire byte —
   arithmetically it fits in the 9 cycles of slack, but it is **not
   implemented and not verified**, so it is a stated option, not a claim.
2. **The turnaround, and this is the honest weak point.** Measured on the
   object with the same cost model: worst case (SE0 caught in cell 0) the
   flush is 68..70 cycles for the byte in flight plus 83..88 for the partial
   byte, and the tail to the `bl` into the C layer is 52..71 — **203..229
   cycles ≈ 12.7..14.3 bit times** at 24 MHz. `PRIOR_ART` L-1 gives the
   response window as 2-6.5 bit times, with host timeout at 16-18. So the
   response lands inside what hosts tolerate but outside what §7.1.18
   specifies, and the C handler's own time is on top of that. This is not
   introduced by the merge — it is the 24 MHz operating point, the same wall
   NATIVE hit from the other side — but it is the binding system constraint,
   and the project-level answer is already on record: ACK-first
   (`PRIOR_ART` R8 / branch_notes Part B), not more assembly here. A cheaper
   hand-written flush (rather than re-running the seven macros) would save
   perhaps 60 cycles and would break the "one source for both copies"
   property; that trade is left open deliberately.
3. **Phase lock is first-order.** ±3.5 cycles of sample-point error from the
   6..7 cycle poll granularity, plus unmeasured exception entry latency. No
   bench data. Same status GRAINUUM declared.
4. **No mid-packet resync**, so the engine inherits the existing design's
   drift assumption.
5. **Register pressure is total.** All eight low registers are live in the
   timed path (r0 bit count, r1 pipeline word, r2 the temp CELL clobbers, r3
   accumulator, r4 table base, r5 wire packer, r6 pin mask, r7 GPIO base) plus
   r8 park, r9 buffer base, r10 CRC, r11 unstuff state, r12 byte count, r14
   chain head. There is no free register anywhere in the chain; every segment
   boundary is constrained by "r2 must be dead", and that constraint drove the
   cut. Adding one more piece of per-byte state would require restructuring,
   not just an allocation.
6. **Transmit is not addressed at all.** The spec allows a sketch; this note
   does not even sketch, because the referee's job was the receive merge.
   NATIVE's output-compare NRZI transmitter remains the most interesting
   unexplored idea in the competition.
7. **The C-layer seam is coded to the existing prototypes but not linked.**
   `usb_pid_handle_{data,setup,in,out,ack}` take five arguments, so the fifth
   (`&rv003usb_internal_data`) is pushed; that is written from
   `rv003usb.h:91-95` and has not been link-tested here.

## 11. Defects found in the entries while merging

Recorded because they were verified, not suspected, and because two of them
are in the entry whose mechanism this merge is built on.

* **VUSB, GPIO base.** `engine16_vusb.S:35` defaults `USB_GPIO_BASE` to
  `0x48000000`, an STM32 base. PY32 GPIOB is `0x50000400` (`BUILD_FACTS.md`
  §7). Already flagged in `ENGINE16_RESULTS.md`; fixed here.
* **VUSB, priming is inconsistent with its own accumulator.**
  `engine16_vusb.S:299-303` loads `r0` (the bit count, used as the shift amount
  in `lsls r1,r1,r0`) with **2**, the low-nibble index, and sets the sentinel at
  **bit 24**. The biased add only works if bit `r0` holds the sentinel, so the
  first append lands where there is no sentinel and the carry is lost.
  Independently, `r8` — which `SEG1` reads as the low-nibble index — is saved
  at entry but **never initialised**, so SYNC's low nibble comes from whatever
  the interrupted code left in `r8`. Both are consistent with the results
  file's note that the committed file may predate the shift+sentinel change it
  intended. The merged priming is `r0=0`, `r3=1` (sentinel at bit 0), `r8=2`.
* **VUSB, the ISR can hang.** Its sticky violation row emits nothing, so the
  bound it does have can never fire on a bus stuck in a violation. §6.
* **VUSB, the last cell is never budget-checked.** Its `CELL` macro checks the
  cell it *closes*, and nothing closes cell 7. `CELL_END` here does.
* **This merge, a fall-through into the EOP handler**, introduced while moving
  the stub block and caught in `objdump`. §7.
* **Global branch targets defer range checking to the linker.** §7. This
  affects any entry that made its internal labels `.global` — VUSB's eight EOP
  stubs among them.

## 12. Reproducing the tables and the verification

The tables in the `.S` are generated and checked by this script. It also
regenerates VUSB's T_UT from an independent model of the rule in §2 and
compares all 128 entries (`128/128 match`), which is what licensed reusing the
mechanism at all.

```python
def ut_entry(state, nib):                 # rows 0..6
    bits, s = [], state
    for i in range(3, -1, -1):            # nibble bit 3 = oldest in time
        b = (nib >> i) & 1
        if s == 6:                        # this bit must be a stuffed 0
            if b == 1:
                return (0 << 11) | (0 << 8) | (7 << 5)     # violation, sticky
            s = 0; continue               # removed from the data stream
        bits.append(b); s = s + 1 if b else 0
    v = 0
    for j, b in enumerate(bits): v |= b << j        # oldest at bit 0
    n = len(bits)
    return ((v + (1 << n) - 1) << 11) | (n << 8) | (s << 5)

def ut_row7(nib):                         # row 7 differs from VUSB: keep the
    v = 0                                 # bits so the byte counter advances
    for j, i in enumerate(range(3, -1, -1)): v |= ((nib >> i) & 1) << j
    return ((v + 15) << 11) | (4 << 8) | (7 << 5)

def reduce_bits(c, k, poly):
    for _ in range(k): c = (c >> 1) ^ (poly if c & 1 else 0)
    return c

T_UT    = [ut_entry(s, n) if s != 7 else ut_row7(n)
           for s in range(8) for n in range(16)]
T_CRC16 = [reduce_bits(i, 8, 0xA001) for i in range(256)]   # crc=(crc>>8)^T[..]
T_CRC5  = [reduce_bits(i, 4, 0x0014) for i in range(16)]    # crc=(crc>>4)^T[..]
```

Checked against bitwise references over 200 random messages: the byte-table
CRC16 equals the bitwise CRC16; the residue over message+CRC16 is **0xB001**;
the nibble-table CRC5 over an 11-bit token field plus its FCS is **0x06** for
every one of the 293 token values tried. Those are the two constants the tail
compares against, and they are now verified rather than inherited from
`rv003usb.S:519`.

The semantic model of §7 transliterates the `.S` one instruction at a time —
for example `SEG3`, whose `.S` form is quoted in §4.2:

```python
def SEG3(self):
    idx = self.r12 & (BUFLEN - 1)          # lsls #27 / lsrs #27
    self.buf[idx] = self.r3 & 0xFF         # strb r3,[r1,r2]
    self.r0 -= 8                           # subs r0,#8
    self.r1 = M32 if self.r0 < 0 else 0    # asrs r1,r0,#31
    self.r1 ^= M32                         # mvns r1,r1  -> commit mask
    self.r0 += 8 & ~self.r1 & M32          # movs/bics/adds
```

The tables embedded in the `.S` were compared halfword for halfword against
the generator's output (`400/400 identical`), so the object that assembles and
the model that was verified use the same tables.

The model drives it with a host-side encoder (SYNC, LSB-first bytes, bit stuffing
after SYNC with the SYNC's trailing 1 counted, NRZI from idle J). The engine's
first sample is the first PID cell, because the phase lock consumes SYNC and
the pipeline is primed with it.

## 13. Summary against the spec's judging order

1. **Fits 16 on every path, arithmetic shown** — §4, and enforced by the
   assembler. With 9 cycles of slack out of 128, not exactly full.
2. **Correct** — NRZI, unstuffing, byte alignment, EOP, CRC16 residue, CRC5
   residue and the buffer bound all checked in §7; 305 packets, 0 failures.
3. **Robust to the 2-vs-3 ambiguity** — no taken conditional branch and no `B`
   on any timed path; one `bx` per byte at a documented flat 3. §5.
4. **Register pressure and footprint** — all 14 usable registers live, 1812 B
   of RAM-resident code and tables, 32 B of buffer. §10.
5. **What it gives up** — RAM, the turnaround at 24 MHz, an unbenched phase
   lock, no resync, no transmit. §10.
