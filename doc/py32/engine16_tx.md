# engine16_tx — the transmit half

`engine16_tx.S`, written against `ENGINE16_SPEC.md` and against
`engine16_merged.S`, which is its model. Assembles rc=0 with
`arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c`.

**Result: every bit cell is exactly 16 cycles, and there is exactly one route
through a bit cell.** Data 1, data 0 and a stuffed bit are not merely equal in
cost — they are the same five instructions, and nothing in a cell looks at the
value of the bit it is emitting. There is no conditional branch anywhere in the
timed chain and no `b`; the only control transfer is one `bx` per data byte,
which the cost table prices at a flat 3. The annotator prints `16 cycles` for
all ten cells with **no range at all**, where the receive engine prints
`16..18`.

## 1. The idea, in one paragraph

NRZI encode is "toggle on 0", so a bit cell that consumes one already-stuffed
wire bit is five cycles of carry arithmetic:

```
lsrs r4, r4, #1 ; sbcs r2, r2 ; ands r2, r6 ; eors r5, r2 ; str r5, [r7,#BSRR]
```

`sbcs r2, r2` computes `r2 - r2 - (1-C) = C-1`, i.e. it turns the carry into
`0` or `0xFFFFFFFF` in one instruction, and the whole of NRZI is then one AND
and one XOR. Everything hard — bit stuffing, byte disassembly, CRC16, the byte
source — runs **one byte ahead** in the eleven cycles each cell has left,
exactly as `engine16_merged.S` runs its receive pipeline one wire byte behind.
A register-offset table indexed by (stuff state, data nibble) returns the
stuffed wire bits, how many there are and the next state in one 2-cycle RAM
load. This is the receive engine's chassis run backwards.

## 2. Does the inverse table work? The arithmetic, before the structure

The receive engine's `T_UT` folds NRZI decode, the unstuff counter and byte
assembly into one access. The transmit inverse folds **two** of three:

* **byte disassembly** — yes, the same nibble-at-a-time lookup;
* **stuff insertion** — yes, and it is the same table entry;
* **NRZI encode** — *not in the table, and it does not need to be*. On receive
  the decode is a function of two adjacent samples, so it has to happen before
  the table can be indexed. On transmit it is a function of one bit and the
  current line state, and `sbcs`+`ands`+`eors` does it in three cycles inside
  the cell. Putting it in the table would mean indexing by line state as well,
  doubling the table for nothing.

**The asymmetry the brief asks about is real and it is not in the table — it is
in the cell count.** A stuffed bit *extends* the output. Take a data byte and
the stuff state `s` (0..6, where 6 means "the next wire bit is a forced 0"):

* per nibble, at most one stuff can be inserted. Entering at `s=6` gives
  stuff + 4 bits = 5; entering at `s=5` gives 1 bit, `s` hits 6, stuff, then 3
  bits = 5. A second stuff inside one nibble would need six more 1s than the
  nibble contains. So **n ∈ {4,5} per nibble**, checked exhaustively by the
  generator (`assert n in (4,5)` over all 7×16 entries).
* therefore a byte is **8, 9 or 10 wire bits**, and n=10 is reachable: byte
  `0xFF` entering at `s=6` gives stuff,1,1,1,1 | 1,1,stuff,1,1.

So the receive engine's shape — a fixed eight-cell chain per byte with one
pipeline segment in each — cannot be copied directly. Three ways out, and only
one of them survives arithmetic:

1. *One pipeline pass per eight wire cells.* Dead: a pass produces 8..10 wire
   bits while eight cells consume 8, so the queue backs up and the packet never
   ends.
2. *A stuff cell inserted by a per-bit computed branch.* Every cell then ends
   with `bx` (3) instead of a fall-through (0), and computing the next target
   costs ~6 more. 5 + 3 + 6 = 14 leaves 2 cycles per bit for a byte pipeline
   that needs ~66. Dead.
3. *A ten-cell chain entered at a computed offset.* Cells `P0`,`P1` carry no
   pipeline work; cells `S0..S7` always run and carry the eight segments.
   Enter at `S0` for n=8, `P1` for n=9, `P0` for n=10. One `bx` per **byte**,
   not per bit, and the eight segments always execute. **This is the one that
   fits**, and it is the whole structural answer to "a stuffed bit extends the
   output".

The dispatch index is free. The accumulator uses VUSB's bias
(`m = w + 2^n - 1`), so after both nibbles

```
acc = w_lo + (w_hi << n_lo) + 2^n      -- W with a sentinel 1 at bit n
```

and `acc >> 8` is `1` for n=8, `2..3` for n=9 and `4..7` for n=10 — because W
occupies bits 0..n-1, so the high part of `acc>>8` is the sentinel and the low
part is whatever bits 8..9 of W happen to be. An 8-word dispatch table indexed
by exactly that value costs `lsrs` + `lsls` + `ldr` and needs no comparison, no
subtraction and no extraction of `n` at all. The dispatch table is row 0 of the
halfword table, which is otherwise unreachable because the stuff state is
stored as `(state+1) ≥ 1`.

## 3. The cycle ledger

Placement: **RAM-resident** (`.datacode`). `ENGINE16_SPEC.md` §2 RAM column:
ordinary instruction 1, GPIO store 1 (IOPORT, `CHIP_FACTS_XIAMATSU.md` §1),
RAM load/store 2, `BX` 3, `B` 2-3, taken conditional 2-3. The tables and the
literal pool are in the same section (`.ltorg` inside `.datacode`, verified in
the object at 0x234..0x254), so a table lookup is 2 and a literal load is 2 —
not the 4 the annotator charges, which assumes a flash pool.

### 3.1 The emit, identical in all ten cells (5 cycles)

```
  lsrs r4, r4, #1        1   C = the next wire bit; 1 = data 1
  sbcs r2, r2            1   r2 = C-1: -1 on a 0, 0 on a 1
  ands r2, r6            1   the BSRR toggle word, or nothing
  eors r5, r2            1   NRZI: toggle on 0
  str  r5, [r7, #0x18]   1   the bit leaves here, at cycle 5 of every cell
```

Nothing here inspects the value, so *data 1*, *data 0* and *stuffed bit* are
one path. `r4` is a queue whose sentinel bit is never consumed: the chain emits
exactly `n` bits and the sentinel sits at bit `n`.

### 3.2 The ten cells

| cell | emit | pipeline segment | of which `nop` | total |
|---|---|---|---|---|
| P0 | 5 | — (only reached when n = 10) | 11 | **16** |
| P1 | 5 | — (reached when n ≥ 9) | 11 | **16** |
| S0 | 5 | fetch byte, build the CRC commit mask — 11 | 0 | **16** |
| S1 | 5 | park byte, CRC16 table lookup, gate it — 11 | 0 | **16** |
| S2 | 5 | commit CRC, publish CRC byte 0 — 11 | 1 | **16** |
| S3 | 5 | publish CRC byte 1, low-nibble index — 11 | 1 | **16** |
| S4 | 5 | low nibble → bits, state, n_lo — 11 | 0 | **16** |
| S5 | 5 | high-nibble index and lookup — 11 | 2 | **16** |
| S6 | 5 | biased append, dispatch index, anchor — 11 | 0 | **16** |
| S7 | 5 | dispatch or EOP, load the queue, `bx` — 11 | 1 | **16** |

`10 × 5 (emit) + 8 × 11 (segments) + 2 × 11 (P0, P1 filler) = 50 + 88 + 22
= 160 = 10 × 16.` The `bx` is the last 3 of `S7`'s 11, so the target's first
instruction is cycle 1 of the next cell — the same convention
`engine16_merged.S` uses for its back edge. Of the 88 segment cycles, 5 are
explicit `nop` (S2, S3, S5×2, S7): that is the real slack, 5 cycles per byte,
and it is where a future change has to come from. `P0`/`P1`'s 22 are not
slack — they are the space a stuffed bit occupies.

Cell `S6` in full, from `objdump` (addresses from `.scratch/tx.o`; the same
sequence the annotator prices at exactly 16):

```
 184 lsrs r4, r4, #1    1  =1     the emit, cycles 1..5
 186 sbcs r2, r2        1  =2
 188 ands r2, r6        1  =3
 18a eors r5, r2        1  =4
 18c str  r5, [r7,#24]  1  =5     the wire edge
 18e uxtb r2, r1        1  =6     entry_hi & 0xFF = (state'+1)<<5
 190 mov  fp, r2        1  =7
 192 lsrs r1, r1, #9    1  =8     m_hi = w_hi + 2^n_hi - 1
 194 mov  r2, ip        1  =9     n_lo
 196 lsls r1, r2        1  =10    align to the sentinel
 198 adds r0, r0, r1    1  =11    ONE add: append n_hi bits, move the sentinel
 19a lsrs r1, r0, #8    1  =12    1 / 2-3 / 4-7 for n = 8 / 9 / 10
 19c lsls r1, r1, #2    1  =13    word index into the dispatch table
 19e mov  ip, r0        1  =14    park the queue
 1a0 mov  r2, r8        1  =15    the CRC pointer, doubling as the anchor
 1a2 subs r0, r3, r2    1  =16    source - anchor, tested in S7
```

### 3.3 The five paths the spec asks about

| path | cycles | why |
|---|---|---|
| data 1 | 16 | the emit above; the value is not examined |
| data 0 | 16 | *the same five instructions*, byte for byte |
| stuffed bit | 16 | *the same five instructions*. The stuff was inserted a byte earlier by the table, as an extra wire bit in the queue; it reaches the wire as one more ordinary cell — `P1` or `P0` — and those cells are 16 like every other |
| byte boundary | 16 | there is no byte boundary in the timed path. The store, the CRC step and the byte fetch are segments of the same chain, executed unconditionally every byte, and the transition is the `bx` in `S7`, which is inside that cell's 16 |
| SE0 / EOP | 16, 16, 16 | the dispatch word sends `S7`'s `bx` to `usb_tx_eop` instead of a chain entry, at the same flat 3. Three more exact cells follow: SE0, SE0, driven J |

**The maximum is 16 and so is the minimum, on every path.** There is no
worst case distinct from the typical case.

## 4. The branch-ambiguity question

`ENGINE16_SPEC.md` §2 calls the 2-vs-3 taken-branch cost "the single strongest
pressure in the spec". This engine's timed path does not contain a conditional
branch at all — not a taken one, not an untaken one. The receive engine still
carries eight untaken `beq` (its SE0 tests, 1 cycle each and unambiguous);
transmit has nothing to test, because it owns the clock, so even those go away.

The single control transfer in the chain is `bx r1` at the end of `S7`, once
per data byte, priced at a **flat 3** by both `ENGINE16_SPEC.md` §2 and
`CHIP_FACTS_XIAMATSU.md` §1 — the same one assumption `engine16_merged.md` §5
rests on, and the same repair if the bench disagrees: one `nop`, in one place.

Evidence, not assertion:

```
$ python3 tools/engine16_cyc.py tx.o --exec ram --ioport r7 --budget 16
usb_tx_cellP0:   16 cycles
usb_tx_cellP1:   16 cycles
usb_tx_cellS0:   16 cycles
...
usb_tx_cellS7:   16 cycles
```

Ten single values, no ranges. The two blocks the tool flags over budget are
`usb_send_data` (the untimed setup) and `usb_tx_eop` (three timed cells plus
the untimed tail, which the tool cannot separate because they share a label).
Those were counted by hand in §5.

`arm-none-eabi-objdump -r` gives **twelve relocations, all `R_ARM_ABS32`, none
of them branch relocations** — the property `engine16_merged.md` §7 identified
as the one that lets the assembler, not the linker, prove every branch range.
Here it is trivially satisfied: nothing in the object branches to a cell. The
cells are reached only through absolute dispatch words, which is why their
labels can be global without giving anything up.

## 5. Arm-to-first-bit — the number the turnaround work needs

This is the interface to the ACK-first architecture, so it is given as
measured instruction sequences, not as an estimate. Cycle 0 is the first cycle
of `usb_send_data`; "first bit" is the `str r5,[r7,#BSRR]` in cell `S0`, which
is the first driven K.

### 5.1 Cold entry, as written

| block | cycles | note |
|---|---|---|
| `push {r4-r7,lr}` + 4 `mov` + `push {r4-r7}` | 15 | 2+4 and 2+3, RAM column |
| argument checks | 3..4 | one taken `beq` on the CRC path |
| `usb_txbuf` base, SYNC and PID stores | 7 | `ldr` from the `.datacode` pool = 2 |
| payload copy: computed entry + 4 cycles/byte | 7 + 4·L | `bx` = 3, no loop, no taken conditional |
| CRC-pointer / anchor arithmetic | 5 | |
| ten register initialisations | 14 | |
| three pool loads (table, GPIO, toggle) + J word | 8 | 2 each |
| drive J, read-modify-write MODER | 8 | the pins become outputs here |
| `ldr` chain entry + `bx` | 5 | |
| cell `S0`, emit up to the store | 5 | |

Counted instruction by instruction off the disassembly, with the pool at 2
cycles and the one taken conditional branch at 2-3:

* empty DATA0/DATA1 (CRC path, L = 0): **80..81 cycles ≈ 5.0 bit times**
* handshake (no-CRC path, L = 0): **82..83 cycles ≈ 5.1 bit times**
* 8-byte DATA (L = 8): **112..113 cycles ≈ 7.0 bit times**

The ±1 comes from exactly one taken conditional branch in the argument
checks. It is outside the timed chain, so it moves the whole packet by a
cycle and nothing inside it.

Against `PLAN` Appendix A's 51 cycles for "entry to first preamble store" in
the existing RISC-V engine, which does no payload copy and no CRC staging.

### 5.2 What to pre-stage, and what it buys

Everything above except the two GPIO stores is either constant or computable
before the token arrives. The identified — **not implemented** — pre-staged
entry is a twelve-word control block holding the register image
(`r3,r4,r5,r6,r7,r8,r9,r10,r11,r14`, the MODER output word, the chain entry)
loaded with three `ldm` bursts:

```
ldmia r0!, {r3,r4,r5,r6,r7}   6      2 + 4, RAM column
ldmia r0!, {r1,r2} ; mov r8,r1 ; mov r9,r2     5
ldmia r0!, {r1,r2} ; mov r10,r1 ; mov r11,r2   5
ldmia r0!, {r1,r2} ; mov r14,r1                4
str r5,[r7,#BSRR] ; str r2,[r7,#MODER]         2
bx  <cellS0>                                   3
                                             ==25
```

plus the 5 cycles inside `S0`: **~30 cycles from "decision made" to the first
wire edge**, with no register save (the receive ISR has already saved them) and
no payload copy (a handshake has none). One `strb` (2 cycles) covers a PID that
is chosen at run time rather than having one block per PID.

**So the number to design the turnaround against is 30 cycles ≈ 1.9 bit times
for a handshake, and ~112 cycles ≈ 7 bit times for a cold 8-byte DATA.** For
comparison, `engine16_merged.md` §10.2 measures 203..229 cycles from SE0 to the
C-layer call, so transmit's arm is 13 % of the chain it hangs off. Transmit is
not what is spending the turnaround budget, and shortening it further has
almost no leverage; the pre-staged form exists so that it can be taken off the
table entirely.

What pre-staging costs: one 48-byte control block per response shape, and the
requirement that the C layer fills `usb_txbuf[2..]` and the block before the
token arrives rather than inside the IN handler. That is the same restructuring
the hardware route in §8 needs, which is worth noticing.

## 6. Correctness, checked rather than argued

A bit-exact model of the **instructions in the `.S`** — same registers, same
order, same masks, same 32-bit wrap — is driven by packets and its emitted
line-state sequence compared against an independent reference encoder that
does SYNC, LSB-first bytes, bit stuffing over the whole post-SYNC stream and
NRZI from idle J (§9 reproduces both):

```
433 packets, 0 failures
all-ones DATA0: 108 wire bits
reference      : 108 wire bits
dispatch index histogram: {0: 433, 1: 2801, 2: 102, 3: 148, 5: 1, 6: 6, 7: 12}
CRC16 residue over payload+transmitted CRC: 0 of 430 not 0xB001
```

The 433 include every PID the C layer can send, empty payloads, an all-zero
8-byte payload (every wire bit a toggle), an all-ones payload (maximum
stuffing), `0x7F,0xFF,0xFF,0xFF`, 400 random payloads of 0..8 bytes, and the
three handshakes on the no-CRC path.

The histogram is the coverage argument: **all three chain entry points are
exercised** — 2801 bytes of 8 wire bits (enter at `S0`), 250 of 9 (enter at
`P1`), 19 of 10 (enter at `P0`), and 433 dispatches to EOP. Index 4 never
occurs in this sample; it is the n=10 case whose wire bits 8 and 9 are both
0, and it points at `P0` like 5, 6 and 7.

The residue line closes the loop with the other half of the project: the
CRC16 this engine transmits, fed back through the receive engine's own byte
table, leaves **0xB001** — the constant `engine16_merged.md` §12 verified and
`rv003usb.S:519` compares against. Transmit and receive agree by construction,
not by inspection.

The tables embedded in the `.S` were compared halfword for halfword against
their generator: **368/368 identical**.

### 6.1 A defect this caught, recorded because it was found and not suspected

The state-6 row of `T_TX` was written by hand into the `.S` instead of being
pasted from the generator. **All sixteen entries were wrong.** The model
verified clean at 433/0 while the object was broken, because the model builds
its table from the generator and the object did not. Only the halfword-for-
halfword comparison of the `.S` against the generator found it
(`352/368 identical`). State 6 — a byte ending on the sixth consecutive 1 —
is reachable from ordinary data, so this was a live defect, not a corner.

The lesson is the one `engine16_merged.md` §7 states in the other direction:
a model that shares a source with the thing it is checking proves nothing
about that source. The `.S` and the generator are now compared directly.

### 6.2 What is bounded, and how

* **The staging buffer.** 16 bytes; the walk is `SYNC, PID, ≤8 payload, 2 CRC`
  plus one over-fetch = at most 13 slots. `usb_send_data` clamps the length to
  8 with `cmp r1,#TX_MAXPAY / bhi`, once, outside the timing. No load or store
  in the timed chain can leave the buffer, for any argument whatever — the
  same class of guarantee as BALANCE's masked index, obtained here from a
  single setup-time clamp because transmit, unlike receive, is not driven by
  the bus.
* **Termination.** `S7` compares the source pointer against the anchor in `r8`
  and forces the dispatch index to 0 when the packet is exhausted. The chain
  cannot loop forever: the pointer advances by exactly one every byte and the
  anchor never moves.
* **The precondition on no-CRC packets.** With `poly_function != 0` the anchor
  sits at `staging + length`, so the two CRC bytes are published over slots
  `length` and `length+1`. For `length = 0` (handshakes) those are `SYNC` and
  `PID`, both already consumed when the write happens. For `length = 2` they
  are the two payload bytes, and the value written is `~0xFFFF = 0x0000` — the
  bytes `usb_send_empty` was passing anyway. For `length ≥ 3` it would corrupt
  the payload, so setup refuses it (`cmp r1,#2 / bhi`). Every call site in
  `rv003usb.c` (`:176, :205, :232, :236, :466`) is inside the accepted set.
  `usb_send_empty` is re-expressed here as a zero-length packet *with* CRC,
  which emits the identical two 0x00 bytes because CRC16 over an empty payload
  is 0xFFFF and the wire form is its complement.

### 6.3 EOP width

`usb_tx_eop` is three exact cells: SE0, SE0, driven J, each 16 cycles with the
store at cycle 5, followed by an untimed MODER write that returns the pins to
inputs so the pull-up holds J. SE0 is therefore **32 cycles = 1.333 µs** at
24 MHz. USB 2.0 gives the low-speed EOP SE0 width as 1.25..1.50 µs, i.e.
30..36 cycles, so the margin is 2 cycles low and 4 high. That is why the delay
is straight-line `nop` and not a loop: two iterations of a loop whose back edge
is 2-3 cycles would spend the entire low-side margin on the ambiguity.

## 7. Registers, footprint, and what this design gives up

**Register allocation, honestly.** All fourteen usable registers are live in
the timed chain: r0 and r1 working, r2 the temp the emit clobbers every cell,
r3 source pointer, r4 wire queue, r5 line state, r6 toggle word, r7 GPIO base,
r8 CRC pointer *and* exhaustion anchor, r9 the commit window's low edge, r10
CRC16, r11 stuff state, r12 park (byte, then n_lo, then queue), r14 table base.
The emit pins five low registers, leaving r0 and r1 as the only cross-cell
working registers — which is what drove the cut, and why `r8` had to do two
jobs. There is no free register anywhere; one more piece of per-byte state
would need restructuring, not allocation.

1. **RAM, and this is the serious one.** `.datacode` is 1368 bytes: 564 of
   code, 36 of literal pool, 768 of tables (32 dispatch + 224 `T_TX` + 512
   `T_CRC16`), plus 16 bytes of staging buffer. Added to the receive engine's
   1812 + 32 that is **3228 bytes on a part with 3072** (`BUILD_FACTS.md` §6:
   F002Bx5 has 3K). **The two engines as written do not fit together on
   F002B.** The identified fix is that both carry the *same* 512-byte
   `T_CRC16` — sharing it saves 512 bytes and brings the pair to 2716, which
   fits but leaves under 400 bytes for stack, `.data` and `.bss`. It is
   arithmetic, not a design change, but it is **not implemented and not
   verified**, so it is a stated route, not a claim. On F003x6/F030 (BUILD_FACTS
   §6) the question does not arise.
2. **The payload copy is in the arm path.** 4 cycles per byte, up to 32, all
   of it before the first bit. The identified alternative is to copy one byte
   per byte-slot inside the timed slack — the segments use 84 of 88 spare
   cycles, and a clamped `ldrb`/`strb` pair is about 10 — which would take the
   copy out of arm-to-first-bit entirely and make the byte source the caller's
   own buffer. It costs a register the current allocation does not have. Not
   implemented.
3. **The `poly_function != 0, length ≥ 3` case is refused** rather than
   supported (§6.2). It is unreachable from the current C layer, and the
   refusal is explicit rather than silent, but it is a narrowing of the seam.
4. **Five cycles of slack per byte, not nine.** The receive engine has 9 spare
   cycles per 128; this has 5 explicit `nop` in the eight `S` segments, plus
   22 in `P0`/`P1` when they run — and those 22 are not usable, because they
   only exist on bytes that happened to be stuffed. There is no room in the
   `S` cells for anything else without re-cutting the pipeline.
5. **Nothing is measured on silicon.** Three numbers the design leans on come
   from the cost table, not a bench: `str` to BSRR = 1 (IOPORT), `sbcs` = 1,
   `bx` = 3 flat. If `bx` turns out to be 2-3, cell `S7` becomes 15..16 and the
   repair is one `nop` in one macro — the same exposure and the same repair
   `engine16_merged.md` §5 records.
6. **Pin electrical setup is not touched.** Only `MODER` is written; `OTYPER`,
   `OSPEEDR` and `PUPDR` are left at reset (push-pull, slowest slew, no pull).
   For low speed the slowest slew is what is wanted, and the existing RISC-V
   engine deliberately selects 2 MHz output mode for the same reason
   (`rv003usb.S:760-765`), but on PY32 this is an assumption about reset values
   rather than an explicit configuration.
7. **No collision or bus-state check before driving.** The engine drives the
   moment it is called. That is correct for a turnaround response and wrong for
   anything else; it is the caller's contract, and it is not enforced.
8. **The receive engine's own transmit-side needs are untouched.** This engine
   does not know about `rv003usb_internal_data`, toggles or endpoints; it is
   the wire layer only, and the seam is exactly `usb_send_data` /
   `usb_send_empty` as `rv003usb.h:100-101` declares them.

## 8. The hardware transmitter, evaluated rather than dismissed

`engine16_native.md` §7 sketches a timer in output-compare **toggle** mode with
`CCxDE` pulling compare values from RAM by DMA: "NRZI is exactly *toggle on 0*,
so a packet is a list of toggle times", claimed at zero CPU cycles per bit and
26 cycles to arm. It was never built or measured. Decided here on numbers.

**What survives checking, and one point in its favour it did not claim.**

* The central observation is **correct and is taken** — in software. It is why
  this engine's bit cell is `sbcs`/`ands`/`eors`/`str` and five cycles rather
  than the nine a level-computing cell would need. The mechanism is the value;
  the peripheral is one way to spend it.
* **SE0 works, and NATIVE's reasoning was right.** In toggle mode the two pins
  are always complementary, so exactly one is high; putting one extra entry in
  only that channel's list toggles it low and both lines are low = SE0. One
  more entry on D− returns the bus to J. No `MOE`, no `OSSI`, no `OISx`. The
  cost is that the two lists are no longer identical, so both channels need
  their own buffer.
* **Zero cycles per bit is real** and immune to everything `ENGINE16_SPEC.md`
  §2 warns about. It is also worth nothing here: this runs inside a priority-0
  ISR that nothing may preempt (`PRIOR_ART` L-23), so the cycles it frees have
  no other claimant. The metric that matters is latency to the first bit, not
  CPU occupancy, and on that metric the sketch is measured below.

**What does not survive: the toggle list has to be built, and the sketch never
costs it.**

An 8-byte `DATA0` is SYNC + PID + 8 + CRC16 = 96 data bits, 96..108 on the wire
after stuffing. The list needs one 16-bit absolute compare value per *toggle*,
i.e. per 0 in the stuffed stream — worst case (an all-zero payload) about 90
entries. The floor is one 16-bit RAM store per entry at 2 cycles from
RAM-resident code plus at least one instruction to produce each value:

```
  90 entries x (2 store + 1 produce)                  = 270 cycles   hard floor
  + advance the time base once per bit, ~100 bits     = 100
  + stuffing and byte disassembly, the same work
    this engine's segments do (~45 cycles/byte x 12)  = 540
                                                     ------
  realistic                                          ~500-900 cycles
```

Even the impossible floor of 270 cycles is **17 bit times** at 24 MHz. The
realistic figure is 30-55 bit times. The receive chain already spends 203..229
cycles (12.7..14.3 bit times) reaching the C layer
(`engine16_merged.md` §10.2); USB 2.0 §7.1.18 asks for 6.5 and the host times
out at 16-18. **Building the list inside the turnaround puts the response past
the host timeout, not merely past the specification.**

So the sketch's own escape — "an IN response is built when the endpoint buffer
is filled, long before the IN token arrives" — is load-bearing, and it is not
true of this C layer as written: `usb_handle_user_in_request` is called from
inside the IN-token handler (`rv003usb.c:193`), i.e. after the token has
arrived. Precomputing the list needs the same restructuring the pre-staged
software arm in §5.2 needs. Against a restructured C layer both routes arm in
~26-30 cycles, and the hardware route's advantage disappears entirely.

**The pin conflict is real, and it is more specific than "unchecked".**
`engine16_native.md` H-12 lists, for F030: `PA2/PA6 → TIM3_CH1`,
`PA3 → TIM1_CH1`, `PB3 → TIM1_CH2`, `PB4 → TIM3_CH1`, `PB5 → TIM3_CH2`. This
project's pins are `PB3` (D+) and `PB4` (D−) — `TIM1_CH2` and `TIM3_CH1`,
**two different timers**, which cannot carry one synchronised toggle pair. The
nearest fix is to move D+ from `PB3` to `PB5`, giving `TIM3_CH1` (D−, `PB4`)
and `TIM3_CH2` (D+, `PB5`) on one counter, with `TIM3_CH1` also serving as the
receive capture channel. That is a **board change**, not a software one. Not
fatal, but it is a cost the sketch does not carry.

**Budget.** DMA1 has three channels (`engine16_native.md` H-1). Receive capture
takes one, the two transmit channels take the rest: zero spare for anything
else the product might ever want. RAM for the lists is ~360 bytes worst case
(two channels, ~90 entries each), on top of the receive side's needs.

**And it does not exist where it is most needed.** `py32f002bx5.h` defines
neither `DMA1_BASE` nor `TIM3_BASE` (H-2), so on F002B this software engine is
not a fallback — it is the only transmitter that part will ever have.

### Verdict

**Not taken.** Not because the mechanism is wrong — it is right, and it is
taken in software — but because on the only metric that binds, latency from
decision to first bit, it is not better: ~30 cycles pre-staged for either
route on a handshake, and for a DATA response it is worse by 270-900 cycles
unless the C layer is restructured, at which point the software route is
already at ~30 too. Add a board change, the whole DMA budget, and a second
acquisition path that only half the family has. The right place to spend the
sketch's insight was the bit cell, and that is where it went.

The one thing worth revisiting: if the ACK-first work restructures the C layer
so responses are precomputed, then on F030/F003 a **constant-packet** hardware
transmitter for ACK/NAK/STALL costs three `.hword` lists in flash and no build
at all. It would still not beat 30 cycles by enough to matter, which is why it
is recorded here as an option and not as a recommendation.

## 9. Reproducing

Assemble and price:

```
arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c \
    doc/py32/engine16_tx.S -o tx.o
python3 tools/engine16_cyc.py tx.o --exec ram --ioport r7 --budget 16
arm-none-eabi-objdump -d tx.o     # control flow traced here, not in the tool
arm-none-eabi-objdump -r tx.o     # 12 relocations, all R_ARM_ABS32
```

`--ioport r7` matters: r7 holds the GPIO base and without it the annotator
prices the BSRR store as a 2-cycle RAM store and every cell reads 17. The tool
does not resolve control flow, so the chain was also read straight out of
`objdump`: cells are contiguous (`P0` ends 0xae / `P1` begins 0xb0; `P1` ends
0xce / `S0` begins 0xd0; … `S6` ends 0x1a2 / `S7` begins 0x1a4), `S7` ends with
an unconditional `bx` at 0x1bc so nothing falls into `usb_tx_eop` at 0x1be, and
the three EOP cells were counted instruction by instruction (5 + 11, 4 + 12,
5 + 11).

The table generator, the instruction-level model and the reference encoder:

```python
def tx_entry(s, nib):                    # T_TX, rows 0..6 at table row s+1
    bits, st = [], s
    for i in range(4):
        b = (nib >> i) & 1
        if st == 6:                      # this wire bit is a forced 0
            bits.append(0); st = 0
        bits.append(b)
        st = st + 1 if b else 0
    n = len(bits)                         # asserted to be 4 or 5, all 112 rows
    w = 0
    for j, b in enumerate(bits): w |= b << j
    return ((w + (1 << n) - 1) << 9) | ((n - 4) << 8) | ((st + 1) << 5)

def reduce_bits(c, k, poly):
    for _ in range(k): c = (c >> 1) ^ (poly if c & 1 else 0)
    return c
T_CRC16 = [reduce_bits(i, 8, 0xA001) for i in range(256)]
```

The model transliterates the `.S` one instruction at a time — for example
`TXSEG6`, whose assembled form is quoted in §3.2:

```python
def SEG6(self):
    self.r2  = self.r1 & 0xFF               # uxtb r2,r1
    self.r11 = self.r2                      # mov  fp,r2
    self.r1 >>= 9                           # lsrs r1,r1,#9   -> m_hi
    self.r2  = self.r12                     # mov  r2,ip      -> n_lo
    self.r1  = (self.r1 << self.r2) & M32   # lsls r1,r2
    self.r0  = (self.r0 + self.r1) & M32    # adds r0,r0,r1   -> the biased add
    self.r1  = self.r0 >> 8                 # lsrs r1,r0,#8   -> 1 / 2-3 / 4-7
    self.r1 <<= 2                           # lsls r1,r1,#2
    self.r12 = self.r0                      # mov  ip,r0
    self.r2  = self.r8                      # mov  r2,r8
    self.r0  = (self.r3 - self.r2) & M32    # subs r0,r3,r2
```

and the reference encoder is written from the specification, not from the
engine: SYNC `00000001` first-bit-first, body bytes LSB-first, a stuffed 0
after every six consecutive 1s counted across the whole post-SYNC stream
including SYNC's own trailing 1, then NRZI from idle J.

## 10. Summary against the spec's judging order

1. **Fits 16 on every path, arithmetic shown** — §3, and enforced by the
   assembler: `TXCELL` pads to 16 and `.error`s if a cell went over. Ten cells,
   ten single values from the annotator, no ranges.
2. **Correct** — NRZI, stuffing, byte order, CRC16, EOP width and the buffer
   bound in §6; 433 packets against an independent encoder, 0 failures; CRC
   residue 0xB001 through the receive engine's own table; tables 368/368
   against their generator, after that comparison found a live defect.
3. **Robust to the 2-vs-3 ambiguity** — §4. No conditional branch and no `B`
   anywhere in the timed chain; one `bx` per byte at a documented flat 3.
4. **Register pressure and footprint** — §7. All fourteen registers live;
   1368 B of RAM-resident code and tables plus 16 B of buffer, which does
   **not** fit alongside the receive engine on F002B without sharing
   `T_CRC16`.
5. **What it gives up** — §7: the F002B footprint, the payload copy in the arm
   path, a narrowed no-CRC seam, four cycles of slack, nothing measured on
   silicon, and pin electrical setup left at reset values.
6. **Arm-to-first-bit** — §5: 80..81 cycles cold for an empty DATA, 112..113
   for eight bytes, **~30 cycles pre-staged**, which is the number the
   turnaround work should design against.
