# ENGINE16 — catalogue of mechanisms

Six engines were written against `ENGINE16_SPEC.md`, then merged; a transmit
engine followed. This file indexes **mechanisms**, not entrants — a reader in
three months asks "how do I decode NRZI cheaply on this core?", not "what did
GRAINUUM do?". Entrant is an attribution field.

* Chronological record, every claim re-checked against the assembled object:
  `ENGINE16_RESULTS.md`. **Not repeated here.**
* The contract: `ENGINE16_SPEC.md`.

**Why this file exists.** Every rejection in this competition is *conditional*,
and two of the conditions are moving right now:

1. **The 24-vs-48 MHz operating point is open**, and `turnaround.md` §10
   produced evidence for 48. At 24 MHz a bit cell is 16 cycles; at 48 it is 32,
   and every rejection whose arithmetic reads "does not fit 16" is void.
2. **The two engines do not fit in RAM.** RX 1812 B + 32 B buffer, TX 1368 B +
   16 B = **3228 B**, against 3072 on F002Bx5 and **2048 on F003x4**
   (`STATE.md`, "RAM: the two engines do not fit together as written";
   `engine16_tx.md` §7.1). Mechanisms that trade cycles for bytes move from
   losers to candidates.

So each rejected entry carries **the condition under which it wins instead**,
not merely the reason it lost. "Rejected because X" is history; "rejected
because X, reconsider if Y" is a decision tool. Where a condition is not
crisply known, it says so rather than inventing one.

One piece of race history that is engineering knowledge: VUSB's agent died
before writing its design note, so its 128-entry unstuffing table had to be
reverse-engineered from the `.S` and verified 128/128 against an independent
model before the mechanism could be reused (`engine16_merged.md` §2, §12).

**Convention: a table is quoted here as its generator, never as a hex dump.**
This is not a formatting preference. What matters about a table is the relation
between the bits and their origin, and a dump destroys exactly that while being
longer. It is also the standard the project arrived at the hard way: the TX
state-6 row defect (§5.2 D-H) was caught **only** by comparing the `.S` against
its generator, after a bit-exact model had passed 433 packets with zero failures
on a broken object. The generator is the authority (§6 L-1); the `.S` is a build
artifact that must be diffed against it.

**Contents.**
1. Adopted mechanisms — A-1..A-17, what is in the shipping RX and TX engines.
2. Rejected, with the revisit condition **and the transferable idea** — R-1..R-16.
3. Dead ends and negative results — N-1..N-9. The most expensive knowledge here.
4. Convergences — C-1..C-6, where the structure turned out to be forced.
5. Defects: **5.1 in code we did not write** (U-1..U-7, report these upstream),
   5.2 our own slips (D-A..D-I).
6. Lessons about method — L-1..L-9.
7. SWAR and register density — open question, possibly on the critical path.

## 1. Adopted mechanisms

What is in `engine16_merged.S` (RX, 16 cycles/bit exact on every data path) and
`engine16_tx.S` (TX, 16 cycles/bit exact with no range at all). Both
RAM-resident (`.datacode`); the RAM column of `ENGINE16_SPEC.md` §2 applies —
ordinary instruction 1, GPIO access 1 (IOPORT), RAM load/store 2, `bx` 3.

### A-1. Wire-domain unroll with a one-byte-behind software pipeline

**VUSB.** Unroll on **wire** bits, not data bits. A wire bit has no data
dependency at all, so eight cells sample eight levels and pack them — 5 of the
16 cycles. The entire data domain (NRZI, unstuffing, byte assembly, CRC16, the
store) runs one wire byte behind, cut into seven segments that ride in the 11
spare cycles of the eight cells.

**Buys:** the only structure in which the timed cell contains no data-dependent
work whatsoever, so *data 1*, *data 0* and *stuffed bit* are one path rather than
three. Alternatives decode in-cell and pay 7-9 cycles/bit for it.
**Costs:** register pressure is total (below), and the flush after EOP is
expensive because up to two wire bytes are still in the pipeline — which is
exactly the turnaround problem (§2 R-11).

### A-2. Register-offset table lookup as the decoder

**VUSB**, verified 128/128 before reuse (`engine16_merged.md` §2, §12).
`ldrh r1, [r4, r1]` — the one M0+ addressing mode that makes table-driven decode
possible (`M0PLUS_ISA_FACTS.md`). `usb_tables` is indexed `state*32 + nibble*2`,
where `state` (0..6) is the count of consecutive decoded 1s (7 is a sticky
violation row) and the nibble is four NRZI-**decoded** bits. Each halfword is
`m<<11 | n<<8 | newstate<<5` (`engine16_vusb.S:595-616`), with `n` = how many
bits survive unstuffing and `m` = the biased accumulator term (A-3). The whole
128-entry table is this generator, which is the authority
(`engine16_merged.md` §12):

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
```

Row 7 (the sticky violation row) is generated separately and differs from VUSB's
— that difference is a defect fix, not a tuning choice; see §5.2 D-A. The two
CRC tables are one line each, and both residues were verified numerically rather
than inherited (A-9):

```python
def reduce_bits(c, k, poly):
    for _ in range(k): c = (c >> 1) ^ (poly if c & 1 else 0)
    return c
T_CRC16 = [reduce_bits(i, 8, 0xA001) for i in range(256)]   # crc=(crc>>8)^T[..]
T_CRC5  = [reduce_bits(i, 4, 0x0014) for i in range(16)]    # crc=(crc>>4)^T[..]
```

**Buys:** folds NRZI-decoded nibble in, stuffing state, and kept bits out into
**one 2-cycle access**, serving four bits per lookup. That is how a branch
disappears rather than being replaced by mask arithmetic. Against it: 8
cycles/bit for the fused mask, without unstuffing, store or CRC
(§2 R-4). **Costs: 800 B of table in the RX engine** — the reason §2 R-4 and R-6
are live.

### A-3. The biased-add accumulator

**VUSB.** The accumulator holds data in bits `0..r0-1` and a sentinel `1` at bit
`r0`. With `m = kept_bits + 2^n − 1`:

```
acc + (m << r0)  =  data + (bits<<r0) + (1<<(r0+n))
```

One `adds` writes a **variable** number of bits at the sentinel and moves the
sentinel up by exactly that many. With `lsls r1, r1, r0` to align it, appending a
nibble's worth of unstuffed data costs **2 instructions** — no branch, no second
accumulator, no "how many bits do I have" test. The transmit engine uses the same
bias in reverse, and gets its 8/9/10-wire-bit dispatch index free from it:
`acc >> 8` is 1, 2-3 or 4-7 for n = 8, 9, 10 (`engine16_tx.md` §2).

### A-4. Byte-wide NRZI from one shift and one xor

**VUSB** (`SEGA`, `engine16_merged.md` §2):

```
lsrs r1, r5, #1 ; eors r1, r5 ; mvns r1, r1 ; uxtb r1, r1
```

The predecessor of sample *i* is bit *i+1* of the same register, so one shift and
one xor NRZI-decode a whole byte — and bit 8, the last sample of the *previous*
wire byte, supplies the predecessor of this byte's first bit, so **the byte
boundary in the NRZI domain costs nothing**. 8 cycles per wire byte, i.e. 1 per
bit, against 2-3 per bit for a per-bit decode.

Worth noting why this is a Cortex-specific win: an 8-bit machine cannot do this.
It is why the wire-domain unroll pays on this core and not on the AVR the lineage
came from.

### A-5. Combined D+/D− sample-and-mask — SE0 free from the NRZI read

**DESCENT**, found by archaeology in the reference engine (`USB_DMASK`,
`rv003usb.h:128`). One read and one `ands` covering both pins:

```
ldr  r2, [r7, #16]   1   IDR over IOPORT
ands r2, r6          1   Z <=> SE0
beq  rx_eopN         1   not taken on every data path
```

**Buys:** a valid bit always leaves exactly one pin high and only SE0 drives both
low, so the SE0 test falls out of the same instruction that prepares the NRZI
sample — **free**. DESCENT nearly cut it to free a register for mask arithmetic,
traced the alternative by hand, and found it only moves the cost onto the
already-tightest path (either a second GPIO read or a comparison against a stored
idle level, on every ledgered path). That is the archaeology the control
experiment existed to produce: not everything the original does is scaffolding
left over from having 32 cycles.

### A-6. Carry-chain capture

**CLEANSHEET** (VUSB and BALANCE converged on the same two instructions):

```
lsrs r2, r2, #4      1   C = D+
adcs r5, r5          1   shift the raw level into the packer
```

2 cycles, no branch, no mask, and in the merge it captures the **raw wire level**
rather than a decoded bit, which is what makes it value-independent.

### A-7. Per-byte unroll as the granularity

**GRAINUUM** designed it; **DESCENT** proved it forced; **VUSB** arrived from the
AVR school; **BALANCE** quantified it. See §4 — this is the convergence.

**Buys:** "which cell of the byte" becomes a compile-time fact, which is what
lets each cell carry a *different* pipeline segment and its own EOP stub. It is a
statement about code layout, so it composes with any inner loop.
**Costs:** the file is mechanically repeated; lengthening the chain means
regenerating code, not changing a constant.

Upper bound on the idea: **one byte, not a packet** (§3 N-3).

### A-8. Structural buffer bound — power-of-two buffer, masked index

**BALANCE** (`engine16_balance.S:103-107`), closing `DEFECTS_VERIFIED.md` D-2:

```
mov  r2, ip          1   emitted byte count
lsls r2, r2, #27     1   \  index &= 31
lsrs r2, r2, #27     1   /
strb r3, [r1, r2]    2   always inside rxbuf[32]
```

**Buys:** 2 cycles, in a cell that had slack, and **no instruction in the object
can address `rxbuf` out of range** for any input whatever, including a bus held
hostile forever. Unlike a compare-and-branch it cannot be wrong about *which*
addresses are legal. D-2 was a design task rather than a one-liner precisely
because a runtime check is not free inside a timed cell; this solves it
structurally.

**Termination is separate and also needed:** `SEG5` carries `cmp r2,#24 / bhs`,
2 cycles, not taken on any data path. 24 < 32, so the count bound sits inside the
address bound. That check is what exposed VUSB's hang (§5).

On the transmit side the same class of guarantee comes from a single setup-time
clamp (`cmp r1,#TX_MAXPAY / bhi`) outside the timing, because transmit — unlike
receive — is not driven by the bus (`engine16_tx.md` §6.2).

### A-9. CRC16 folded into the cell, verdict ready at EOP

**`PRIOR_ART` S-2, kept.** `SEG4` does the CRC16 byte step and `SEG6` commits it
or not, in cycles that were `nop`. The turnaround budget cannot absorb a deferred
CRC (§2 R-5). Both residues are verified numerically rather than inherited:
**0xB001** over message+CRC16 and **0x06** over the 11 token bits + CRC5
(`engine16_merged.md` §7).

### A-10. Local branch targets, so the assembler proves every range

**The referee's own**, not from any entry. See §3 N-8. Nine relocations on RX,
twelve on TX, **no branch relocations in either**.

### A-11. Assembler-enforced cycle ledger

**The referee's own.** The `CELL` macro pads the cell it closes to exactly 16 and
raises `.error` if it went over; `CELL_END` closes the last cell — which VUSB's
version of the same device never did, leaving its cell 7 unchecked (§5). A
miscount is a build failure, not a review finding. The entry code carries the
same discipline: `.if`/`.error` asserts `usb_rx_chain - .Lprime == 28`, because a
`.balign` that silently added a `nop` would move the sample point and nothing
would complain.

### A-12. Transmit: NRZI as carry arithmetic, five cycles

**`engine16_tx.md` §1**, taking NATIVE's central observation (NRZI *is* "toggle
on 0") into software rather than into a peripheral:

```
lsrs r4, r4, #1        1   C = the next wire bit
sbcs r2, r2            1   r2 = C-1: -1 on a 0, 0 on a 1
ands r2, r6            1   the BSRR toggle word, or nothing
eors r5, r2            1   NRZI: toggle on 0
str  r5, [r7, #0x18]   1   the bit leaves here, cycle 5 of every cell
```

`sbcs r2, r2` computes `r2 − r2 − (1−C) = C−1`, turning the carry into 0 or
0xFFFFFFFF in **one** instruction. Nothing in the cell inspects the value being
emitted, so data 1, data 0 and a stuffed bit are literally the same five
instructions. Five cycles, against the nine a level-computing cell would need.

### A-13. Transmit: ten-cell chain entered at a computed offset

**`engine16_tx.md` §2** — the structural answer to "a stuffed bit *extends* the
output". A byte is 8, 9 or 10 wire bits (n = 10 is reachable: `0xFF` entering at
stuff state 6). Two alternatives die on arithmetic: one pipeline pass per eight
cells backs the queue up and the packet never ends; a per-bit computed branch
costs 5+3+6 = 14, leaving 2 cycles for a pipeline that needs ~66. What fits is
cells `P0`,`P1` carrying no pipeline work plus `S0..S7` always running — enter at
`S0` for n=8, `P1` for n=9, `P0` for n=10. **One `bx` per byte, not per bit**,
and the eight segments always execute.

### A-14. Token validation by pattern match instead of CRC5

**`turnaround.md` §4.1.** The eleven token bits are address (7) + endpoint (4)
and the CRC5 is a pure function of them, so for this device the legal
`(byte2,byte3)` halfword is **one 16-bit value per endpoint** — three or four of
them, built once on SET_ADDRESS, outside any budget. At EOP, two to four `cmp`s
(~8 cycles) validate address, endpoint **and** CRC5 together and yield the
endpoint index, replacing two `bl`s into `.Lcrc5_byte` and four nibble table
steps (`merged:553-559`). It is strictly stronger than a CRC5 check: it rejects a
well-formed token addressed to another device without computing anything. Cost: a
≤8-byte table rebuilt on SET_ADDRESS.

*Status: adopted as a recommendation by `turnaround.md` §10 item 4, present in
`turnaround_sketch.S`, not in `engine16_merged.S`.*

### A-15. The C layer is not in the turnaround path

**`turnaround.md` §4.2**, verified independently against `rv003usb.c`. Every path
of `usb_pid_handle_data` reaches `just_ack` (c:505-509); `usb_pid_handle_in`
never NAKs and its response PID is `e->toggle_in ? 0x4B : 0xC3` (c:203), a
function of the endpoint alone; `usb_pid_handle_out` sends nothing, so **the
turnaround problem does not exist for OUT/SETUP tokens or received handshakes at
all**. There is no NAK or STALL anywhere in the file.

So the engine can emit the response itself and call C afterwards, outside the
budget, with `ENGINE16_SPEC.md` §4's prototypes unchanged. "**The single largest
reduction in the whole document**, and it is not speculative at all — it is the
observation that the C layer was never making a decision the response depended
on."

**The one exception, correctly identified:** under
`RV003USB_USER_DATA_HANDLES_TOKEN` the handler returns early (c:359) and owns the
response, so that option must **disable** the mechanism. No in-tree config
enables it. Second compatibility cost: `usb_send_data(0,0,2,0xD2)` inside
`just_ack` must become a no-op when the engine has already sent the ACK.

### A-16. Pre-staging work into RX slack

**`turnaround.md` §4.** The RX bit cell has 9 idle cycles per wire byte, so an
11-byte DATA packet carries **99 cycles of free budget** and a 4-byte token 36.
Moved out of the post-EOP tail: SYNC compare (4), PID complement check (6), PID
type dispatch (8), "is a response owed and which emitter" (4), and the C-call
marshalling (6).

Two structural points that generalise. **The dispatch target is data, not a
register** — register pressure is total, so it goes to one word at `rxbuf+28`;
the buffer is 32 B and the emitted count is bounded to 24, so offsets 24..31 are
unreachable by the store. And **the offset must stay inside the address bound**:
`rxbuf−4` is outside the region A-8 guarantees, and Thumb-1 has no negative `ldr`
offset anyway — the sketch was written that way first and the assembler rejected
it.

### A-17. Measure turnaround from the specification's zero point

**`turnaround.md` §2.** Not a code mechanism; a correction worth 24 cycles that
every future measurement must repeat. §7.1.18 measures from the **SE0→J
transition**, not from the engine's SE0-detecting sample. SE0 began ~8 cycles
before the sample (cell centre), phase-lock error is ±3.5, and SE0→J is 2 bit
times later, so SE0→J lands in [τ+20, τ+28] and **the budget is [τ+60, τ+124],
64 cycles wide**. The lower bound is not decoration: transmitting before τ+60
violates §7.1.18's *minimum* and would start inside the host's own EOP.

The same section corrects the worst case in the other direction: it is **not**
"SE0 caught in cell 0" as `engine16_merged.md` §10.2 states. At K=0 the current
wire byte holds no sampled bits so the partial-byte path is skipped entirely; the
worst case is **K=1**, six flush segments *plus* a one-bit partial byte that
still costs the full seven segments. 203..229 becomes **208..237**. The problem
was understated, not overstated.

## 2. Rejected — with the condition for revisiting

Each entry: the idea, the mechanism, the number that decided it, the condition
under which it wins — and **what is transferable even if this solution never
returns.** A mechanism can lose on its numbers while the idea underneath it is
reusable somewhere else entirely, so that idea is named rather than left for a
reader to extract. Several of the transferable lines below point at mechanisms
that were adopted (§1) or at §7's open question; that is the point.

### R-1. Deferred decode — capture raw, decode everything after EOP

**CLEANSHEET** (`engine16_cleansheet.md` "The idea"; `.S:129-231`).
Do nothing per bit but sample and pack. The timed slot holds a GPIO read, a
`lsrs`+`adcs` carry-chain capture, an SE0 test and padding; NRZI decode,
unstuffing, byte assembly, CRC and the PID all move to a tail that runs once
per *packet*:

```
ldr  r3, [r0, #IDR]   1
lsrs r5, r3, #4       1
adcs r2, r2           1     carry-chain capture, no branch on data
tst  r3, r1           1
beq  escN             1     not taken on the common path
push {r7}             4     deterministic padding (flash column)
pop  {r7}             4
nop x3                3
                     ==16
```

**Why it lost.** The tail is not free. Decoding ~96 bits after EOP at the
~9.5 cycles/bit the merged engine achieves *pipelined* is **700+ cycles = 45+
bit times**, against a §7.1.18 response window of 2-6.5 bit times
(`engine16_merged.md` §9). Deferral moves the whole problem into the one place
with the least room.

**Condition to revisit.**
* **At 48 MHz, recompute — do not assume it flips.** 700 cycles is 22 bit times
  at 48 MHz against the same 6.5, so it still loses on the *whole-packet* form.
  The arithmetic that changes is the *partial* form: `turnaround.md` §5.2 shows
  the irreducible post-EOP work is the two wire bytes carrying CRC16, 89 cycles
  = 86 % of the 104-cycle budget at 24 MHz and 43 % at 48. A deferred decode of
  the last **one or two** wire bytes only — which is what the merged engine's
  flush already is — is affordable at 48 and not at 24. Full deferral is not
  rescued by the clock; that is the honest statement, and it is stronger than
  "rejected at 24 MHz".
* If the response is ever made **speculative** (SYNC-first, `turnaround.md` §7),
  the tail gets 8 bit times = 128 cycles of SYNC transmission to run in. That
  buys 128 cycles, not 700, so it still does not rescue full deferral — but it
  does rescue *partial* deferral, which is exactly what Design B does with the
  flush.

**Transferable, and this is the important one in the whole section.** The
underlying idea is not "defer" — it is **capture densely, then process a word at
a time instead of a bit at a time.** That is a SWAR approach, and it is
independent of whether the processing happens after EOP or in the next cell's
slack. The merged engine already uses it once, in `SEGA`: NRZI over eight
samples is `x ^ (x>>1)` (A-4), one instruction for eight bits instead of one per
bit. Over 32 bits it is the same two instructions. **The approach outlives the
solution** — see §7, where it is the open question.

**What was kept from it anyway:** the carry-chain capture (§1) and its
branch-range negative result (§3).

### R-2. `push`/`pop` as deterministic padding

**CLEANSHEET** (`engine16_cleansheet.md` "Placement: flash, not RAM").
A `push {r7}` / `pop {r7}` pair costs 4+4 from *flash-resident* code and buys
8 cycles of exactly-known filler in two instructions — cheaper in bytes than
eight `nop`, and with no ambiguity.

**Why it lost.** It buys filler out of the flash column's 4-cycle stack access.
The merged engine is RAM-resident, where the same pair costs 2+2 = 4, so the
padding silently halves. `ENGINE16_RESULTS.md` records it directly: running the
annotator with `--exec ram` gives 12, not 16. And the merged engine has no need
of filler — the software pipeline fills the slack with real work.

**Condition to revisit.** Only for a **flash-resident** timed engine that has
genuine idle cycles. That combination is not currently on any roadmap
(`STATE.md`: RX runs from RAM), and the RAM-overflow pressure of §0 pushes the
*other* way — if 512 B of table had to move to flash the whole pipeline stops
fitting, because a table lookup from flash-resident code is 4 cycles not 2
(`engine16_merged.md` §10.1). Treat this as: **revisit only if the RX engine is
ever forced into flash**, and note that if that happens the padding is the least
of the problems.

**Transferable:** exact-cycle padding can be *bought from the cost model* rather
than counted out in `nop`s — two instructions for 8 cycles instead of eight for
8. Anywhere code size and an exact cycle count are both binding (§2 R-16 is the
same trade from the other side), look for an instruction whose cost the model
states exactly and whose side effects you do not care about.

### R-3. Full-buffer unroll (a code block per buffer byte)

**GRAINUUM** (`engine16_grainuum.S:59-62`, `.md` §6).
Unroll not one byte but `RXBUF_BYTES` of them; every `strb` targets a
compile-time constant offset, and every exit of the *last* block goes to
`too_long_abort` and never back into the engine. The bound on the buffer then
holds because **no instruction exists that could store out of range** — a
strictly stronger property than a masked index, since it bounds the *count* as
well as the *address*, and it costs zero runtime cycles.

**Why it lost.** RAM. ~30 bytes per cell × 8 cells = 240 B per byte-block, so
12 blocks is **2.9 KB on a 3 KB part** (`engine16_merged.md` §6). GRAINUUM's own
`.S` demonstrates it at `RXBUF_BYTES = 2` for that reason.

**Condition to revisit.** Needs a part with RAM to spare — F030x8 has 8 KB
(`BUILD_FACTS.md` §6 as cited in `STATE.md`), where 2.9 KB of RX unroll is
affordable. Concretely: **revisit if the target flip settles on F030x8 alone and
F002B/F003 are dropped.** It does not become cheaper at 48 MHz — this is a byte
cost, not a cycle cost — so it is the one rejection in this section that the
clock question does not touch. Note also that it is *unnecessary* rather than
merely expensive: BALANCE's masked index (§1) already closes D-2, so this buys
only the count bound, which the merged engine gets from a 2-cycle
`cmp #24 / bhs` in slack.

**Transferable, and it was reused twice.** The idea is a safety property proved
by **absence rather than by check**: "can this code address `rxbuf[N]` for
N ≥ len" answered with "no instruction exists that could", not "a check rejects
it". A check can be wrong; an absent instruction cannot. That idea survives in
A-8 (masked index — bounds addresses, not counts) and in TX's single setup-time
clamp (`engine16_tx.md` §6.2 — bounds everything, because transmit is not driven
by the bus). Reach for it whenever a bound sits inside a timed path.

### R-4. The fused transition mask as the decoder

**BALANCE** (`engine16_balance.S:70-84`, `.md` §1; arithmetic in
`engine16_merged.md` §3).
One mask, computed once per bit with `lsls #(31-PIN_DM)` / `asrs #31`, then
consumed three ways — NRZI delta, previous-sample update, and the ones-run
counter — with the carry it leaves behind feeding byte insertion:

```
lsls r6,#(31-PIN_DM) ; asrs r6,#31   2   transition mask
eors r6, r2                          1   NRZI delta
eors r2, r6                          1   prev := new
adds r4,#1 ; bics r4, r6             2   ones-run counter, same mask
lsrs r6, r6, #31 ; adcs r3, r3       2   byte insertion via carry
                                    ==8 cycles/bit
```

Fully branchless on the bit value for **4 extra instructions** over a
branch-based core — which directly refutes GRAINUUM's costing of a comparable
branchless treatment at 9-12 extra instructions (`engine16_grainuum.md` §9). The
disagreement was settled by code.

**Why it lost.** Subsumed, not beaten. Those 8 cycles/bit = 64/byte do **not**
include removing a stuffed bit (BALANCE branches out to `stuff_bit`), the store,
or CRC16. VUSB's table does all three in **76 cycles/wire byte**
(`engine16_merged.md` §3). Where the mask needs one instruction per bit per
consumer, the table serves four bits per lookup and folds three consumers into
one 2-cycle access.

**Condition to revisit — this is the live one.** The table costs **800 B of the
RX engine's 1812 B** (`engine16_merged.md` §10.1); the mask costs registers, not
bytes. If RAM pressure (§0) forces the tables out, the fused mask is the
mechanism that replaces them, and BALANCE's own §8 arithmetic says its core in
an unrolled chassis leaves 3 cycles of slack per slot (verified in
`ENGINE16_RESULTS.md`: 17..18 minus the 3 loop-overhead instructions = 13, and
16−13 = 3). **Concretely: revisit if the pair must fit 2048 B (F003x4), where
sharing the CRC16 table is not enough.** Two unfinished pieces stand in the way,
and BALANCE names both: the byte store must be spread across several slots'
slack rather than paid in one (12 cycles of store work against 21 cycles of
slack per byte — plausible, not built), and unstuffing is still a branch out.

**Transferable:** *pay the cost of going branchless once and consume the result
N ways.* `M0PLUS_ISA_FACTS.md` prices mask arithmetic at "roughly two extra
instructions per decision", which is a losing trade per decision and a winning
one when three decisions share a mask. Whenever a design is about to build the
same predicate twice, the question is whether one mask can serve both — and note
that the mask's *carry* is a fourth consumer for free (`lsrs #31` + `adcs`).

### R-5. In-slot CRC computation

**Rejected by GRAINUUM (§7) and DESCENT (§"What was removed" item 1); adopted by
the merge.** Recorded here because two of six entrants rejected it and their
arithmetic is right for the engines they built.

The reference engine folds an LFSR update into every bit
(`rv003usb-arm.S:167-192`, ~5-6 cycles/bit at a 32-cycle budget). Both entrants
costed it and found no room: GRAINUUM's interior paths have as little as 1 cycle
of slack and its byte-boundary paths have **zero**; DESCENT's tightest path has
zero. DESCENT added the observation that deferring costs nothing in *decision
quality*, because the original never acts on the CRC value until
`se0_complete_flash` (`rv003usb-arm.S:264,308-311`) anyway.

**Why the merge went the other way.** The wire-domain unroll creates 11 spare
cycles in each of eight cells, so `SEG4`/`SEG6` fold the CRC16 byte step into
cycles that were `nop`. The CRC verdict is then ready *at* EOP, which the
turnaround budget requires (`engine16_merged.md` §8, citing `PRIOR_ART` S-2).

**Condition under which deferral is right after all.** If the engine ever loses
the pipelined chassis — i.e. if a future design decodes in-cell rather than one
byte behind — deferral becomes forced again, and then the turnaround cost is
50-60 cycles for a byte-wise table CRC over a 10-byte payload
(`engine16_grainuum.md` §7), which fits the 104-cycle window at 24 MHz *only if
nothing else is in the tail*. `turnaround.md` §5 shows there is 114 cycles of
other work there, so **deferral does not fit at 24 MHz today**; at 48 MHz
(208-cycle window) it does. The dependency is on the chassis, not the clock.

**Transferable, from DESCENT:** separate *deferring the computation* from
*deferring the decision*. The reference engine computed CRC bit-by-bit only to
avoid a second pass over the data; it never acted on the value early. Any
"we compute X eagerly" should be checked against when X is first *read* — often
the eagerness is buying nothing, and sometimes (here) it is buying the only thing
that matters, the verdict being ready at EOP.

### R-6. Nibble CRC16 table instead of the 256-entry byte table

**Identified in `engine16_merged.md` §10.1, not built.** A 16-entry nibble
table saves **480 of the 512 bytes** and costs about 9 more cycles per wire
byte. The engine has exactly 9 cycles of slack per 8 cells
(`engine16_merged.md` §4.2), so arithmetically it fits — with nothing left over.

**Why it is not in the engine.** Not implemented, not verified; the merge
recorded it as a stated option and kept the fast table.

**Condition to revisit — this one is already due.** §0's RAM arithmetic:
3228 B against 2048 on F003x4. Sharing the identical 512 B CRC16 table between
RX and TX brings the pair to 2716 B (`STATE.md`), which fits F002Bx5 with 356 B
to spare and **still does not fit F003x4**. The nibble table is the next lever
after sharing: RX 512→32 and TX 512→32 would take the shared-table pair to about
2236 B. That is still over 2048, so on F003x4 it is necessary but not sufficient
— something else (R-4, or dropping TX's separate `T_TX`) has to go too. Two
warnings before anyone builds it: the 9 cycles of slack are the *same* 9 cycles
that a mid-packet resync would need (R-7), and it consumes them entirely; and
the TX engine has only **5** cycles of slack per byte, not 9
(`engine16_tx.md` §7.4), so the same substitution does **not** obviously fit on
the transmit side.

**Transferable:** table granularity is a **dial, not a choice** — bits consumed
per lookup traded against bytes of table, at a known cycles-per-byte exchange
rate. The same dial exists on `T_UT` (128 halfwords, 4 wire bits per lookup) and
`T_TX` (224 bytes), and nobody has costed turning either of those down. When RAM
is the binding constraint, enumerate every table's dial position before giving up
a mechanism.

### R-7. Mid-packet resynchronisation

**Not implemented in any entry** (`engine16_merged.md` §9, §10.4).
There are 9 spare cycles per 8 cells, "which is roughly what a phase nudge on a
transition would cost", so it is the natural use of the remaining slack.

**Why it is absent.** Nobody built it; the engine inherits the existing design's
drift assumption and a first-order ±3.5-cycle phase lock.

**Condition to revisit.** If bench measurement of phase lock or of exception
entry latency shows the drift budget does not close. `turnaround.md` §11 flags
that exception entry latency is unmeasured and *not* inside the ±3.5 figure, and
that any run-to-run variation in it lands directly on τ and therefore on every
deadline. **This competes with R-6 for the same 9 cycles.** If both are needed,
the operating point has to move.

**Transferable:** slack is a *budget with named claimants*, not spare room. This
engine's 9 cycles/byte are claimed by mid-packet resync, by a cheaper CRC (R-6),
and by A-16's pre-staging — three proposals that each look free in isolation and
cannot all be taken. Any note proposing to "use the spare cycles" should list who
else wants them.

### R-8. Peripheral acquisition front end (input capture) under a software engine

**NATIVE** (`engine16_native.md` §3, §10). Not a decoder — an *acquisition*
front end. One timer channel in slave-reset mode triggered by its own input,
both edges (`CCxNP:CCxP=11`, `SMS=100`, `TS=101`), so the counter is cleared at
every transition and `CCR1` delivers the **interval since the previous
transition** rather than a timestamp. `MSIZE=8` makes the DMA ring one byte per
transition and byte value 0 is unreachable in-packet (shortest legal interval is
16 counts), so a zero-filled ring is a free "not captured yet" sentinel.

What it deletes from *any* software engine: the bounded preamble spin, the
sample-point choice, the entry-latency budget, `PRIOR_ART` D-9's whole dribble
argument, and the phase-drift term. Cost: three peripherals and 112 bytes.
Intervals are quantised against the *local* interval, so **clock offset never
accumulates** — the structural advantage over every sampling design.

**Why it lost.** `py32f002bx5.h` defines neither `DMA1_BASE` nor `TIM3_BASE`
(`engine16_native.md` H-2), so on F002B the software engine is the only engine
and must stand alone. Taking it would mean a second acquisition path that exists
on half the family and that the timed chain cannot share
(`engine16_merged.md` §9).

**Condition to revisit.** **If F002B is dropped as a target.** That is not
far-fetched: `STATE.md` records the target flip to F030/F003 as primary and
F002B as second, needing HSI self-calibration against LSI before enumeration
because its factory 48 MHz constant measures 43.12 MHz (−10.2 %,
`CHIP_FACTS_XIAMATSU.md` §2). If F002B goes, this becomes the cheapest available
improvement to phase accuracy, and it brings two things the project already
wants: the 1 ms keepalive interval measured **in hardware to one cycle** — a far
better servo reference than a software counter clocked by the engine it is
correcting — and on-device EOP-width/turnaround measurement. One gating unknown
remains: the RM publishes no per-transfer DMA cost, and this needs one transfer
every 16 cycles worst case (`engine16_native.md` §3.4).

**Transferable, two ideas, both cheap and both reusable elsewhere.** First:
**measure each interval against the previous edge, not against a time origin** —
error then never accumulates, which is why slave-reset mode beats timestamping
and is why no wrap arithmetic is needed. Second: **a value that is unreachable
in-band is a free sentinel** — the shortest legal interval is 16 counts, so 0
means "not captured yet" at zero cost. Both apply to any ring or capture buffer
in this project, hardware or software.

### R-9. Hardware NRZI transmitter (output-compare toggle + DMA)

**NATIVE** (`engine16_native.md` §7), **evaluated and rejected by
`engine16_tx.md` §8.**
NRZI *is* "toggle on 0", so a packet is a list of toggle times. A timer in
output-compare toggle mode (`OCxM = 011`) with `CCxDE` pulling compare values
from RAM by DMA is a hardware NRZI transmitter: **zero CPU cycles per bit**, 26
cycles to arm, immune to everything `ENGINE16_SPEC.md` §2 warns about. SE0 works
— in toggle mode the two pins are always complementary, so one extra entry in
one channel's list drives both low.

**Why it lost, on numbers rather than taste.** The toggle list has to be
*built*, and the sketch never costs that. ~90 entries × (2-cycle store +
1 produce) = **270 cycles hard floor**, realistically 500-900 with the time base
and the stuffing/disassembly work — 17 to 55 bit times, which puts a DATA
response past the **host timeout**, not merely past §7.1.18. Its escape ("an IN
response is built when the endpoint buffer is filled") is false for this C
layer: `usb_handle_user_in_request` is called from inside the IN handler
(`rv003usb.c:193`). After the restructuring that would fix it, both routes arm
in ~26-30 cycles and the hardware advantage is gone. Independently: PB3/PB4 are
`TIM1_CH2` and `TIM3_CH1` — **different timers**, so it needs a board change; it
consumes all three DMA channels; F002B has neither DMA nor TIM3.

**Condition to revisit** — stated by `engine16_tx.md` §8 itself and worth
keeping: if the ACK-first work restructures the C layer so responses are
precomputed, then on F030/F003 a **constant-packet** hardware transmitter for
ACK/NAK/STALL is three `.hword` lists in flash and no build at all. Even then it
does not beat 30 cycles by enough to matter, so it is an option, not a
recommendation. The board change (move D+ from PB3 to PB5, giving TIM3_CH1 and
TIM3_CH2 on one counter) is the real gate: **revisit only if a board revision is
happening for other reasons.**

**Transferable, and it was taken — the mechanism outlived the peripheral.**
"NRZI *is* toggle on 0" is the observation, and it is what makes the software TX
cell `sbcs`/`ands`/`eors`/`str` — five cycles instead of the nine a
level-computing cell needs (A-12). NATIVE's SE0 reasoning transferred too: in
toggle mode exactly one line is high, so SE0 is "toggle the currently-high line",
which is also how the software engine does it. **The right place to spend the
sketch's insight was the bit cell.** Second transferable, a review habit: when
evaluating a hardware offload, cost the *construction of its input*, not only its
execution — the sketch's 0 cycles/bit was real and irrelevant next to 270-900
cycles of list building.

### R-10. ACK-first in its recorded form (`PRIOR_ART` R8)

Transmit the ACK's SYNC *and PID* unconditionally, compute the CRC during those
16 bit times, and emit the EOP only if the residue is 0xB001.

**Why it lost** (`turnaround.md` §6.1). It puts a **valid ACK PID on the wire
before the CRC verdict** and gates only the EOP, so a host that accepts a
handshake on PID alone, or that recovers a packet whose EOP was lost to a
glitch, sees an ACK for a packet that failed CRC — the one failure USB has no
recovery for. That design needed the PID's 8 bit times because it had moved CRC
*out* of the receive slot; this engine computes CRC16 in-slot, so **only SYNC's
8 bit times are needed** and the ACK PID stays downstream of the residue check.
`false_acks=0` in a simulator is a measurement of one host model, not a proof.

**What replaced it: SYNC-first, PID-gated** (`turnaround.md` §7) — conformant at
5.5 bit times for any A ≤ 62, with a false ACK **structurally impossible**. That
is an improvement on the project's own recorded plan, and per `STATE.md`'s
cross-check it is **not optional**: the best figure the transmitter can offer is
A = 30 (pre-staged), which gives 7.5 bit times under ordinary means — over the
6.5 §7.1.18 asks.

**Condition under which the whole speculative family goes away.** A ≤ 10, or
48 MHz. At 48 MHz `turnaround.md` §10 gives the requirement as A ≤ 136, and
Design A (ordinary means, no speculation) is conformant with ~90 cycles to
spare. **This is the single strongest argument in the corpus for 48 MHz**: it
deletes §7's entire cost list — the nine-way cut of the flush, the deliberate
§8.4.5 deviation, the abort path, and the RX/TX coupling.

**Transferable, and it is the general form of the fix:** when output must start
before the verdict is known, **commit only to the part of the output that carries
no information, and put the gate at the last field that does.** Every packet
begins with the same SYNC (§7.1.10/§8.2), so transmitting SYNC commits to "a
packet is coming", not to which one. The property that buys is
*structural* — a false ACK becomes impossible rather than improbable — which is a
better kind of argument than `false_acks=0` against one host model. The same
shape applies to any speculative response anywhere: find the information-free
prefix, and gate at the first informative field.

### R-11. Ordinary means alone for turnaround ("Design A")

`turnaround.md` §5 measures the floor reachable with no speculation at all:
**τ+114 worst case against a τ+124 deadline**, i.e. (90+A)/16 bit times after
SE0→J. Its own preferred outcome was "ACK-first is unnecessary if A ≤ 10".

**Why it does not obtain.** `engine16_tx.md` §5.2 reports A = **30** for the
pre-staged arm (identified, not implemented) and A = 80 cold for a handshake.
`STATE.md`'s cross-check does the join: A = 30 → 7.50 bit times, over §7.1.18,
inside the 16-18 host timeout. Neither document could reach that conclusion
alone; each had one of the two numbers.

**Condition to revisit.** 48 MHz (see R-10), or an A ≤ 14 transmitter, which
nobody has a route to. **Adopt §4/§4.1/§4.2 regardless** — pre-staged dispatch,
token pattern-match instead of CRC5, and taking the C layer out of the response
path are ordinary engineering worth ~120 of the ~125 cycles saved
(`turnaround.md` §10 item 4).

**Transferable:** reduce a system question to **one number at an interface** and
say so loudly. `turnaround.md` §5.1 turned the entire conformance question into
"what is A?" and §9 wrote the interface as seven requirements on the transmitter
(R1-R8) rather than as a design. That is what made the later cross-check
possible — and see L-8 for the failure mode it does not prevent: somebody still
has to own the inequality.

### R-12. Sampling SE0 earlier than the cell centre

`turnaround.md` §5.2. The detecting `ldr` sits at the cell centre, ~8 cycles
into the SE0, and those 8 cycles are pure latency on the response deadline. A
second GPIO poll early in each cell recovers ~6 of them and costs
`ldr`+`ands`+`beq` = 3 cycles in *every* cell = **24 cycles per wire byte
against 9 cycles of slack**. Rejected on arithmetic.

**Condition to revisit.** At 48 MHz the cell is 32 cycles and the slack per wire
byte roughly doubles in absolute terms, but so does the latency being recovered
in cycles — the *ratio* is unchanged, so **the clock does not fix this**. It is
rescued only by a cell that has ≥3 spare cycles it does not otherwise need,
which the current pipeline does not have. Record as: structurally rejected, not
budget-rejected.

**Transferable:** distinguish a **ratio-limited** rejection from a
**budget-limited** one. A budget-limited rejection ("does not fit 16") is void at
48 MHz; a ratio-limited one (cost and benefit both scale with the clock) is not.
Every rejection in this section should be classified before anyone re-derives it
at a new operating point — most here are budget-limited, R-3 and R-12 are not.

### R-13. Checking SE0 once per byte instead of once per bit

**CLEANSHEET considered and rejected it** ("Why not check SE0 less often"). The
loop-back branch benefits from being infrequent; the SE0 test does not. If SE0
were tested only every 8 bits, detection could lag the real EOP by up to
**7 bit times**, which alone consumes most of the response budget before the CRC
check or dispatch even starts.

**Condition to revisit.** Only under R-8's hardware acquisition front end — and
even there `engine16_native.md` §5 shows the capture stream cannot see EOP at
all (§3 N-5), so the per-bit `IDR` test survives every design in this corpus.
Treat this as settled.

**Transferable:** a **latency** requirement pins polling frequency independently
of throughput. The loop-back branch benefits from being rare; the SE0 test does
not, because what it costs when late is measured in bit times of response budget,
not in cycles of work. Separate "how often must I *detect*" from "how often must
I *process*" — they are different questions with different answers, and here the
engine ended up with a per-bit test nested inside a per-8-bit loop for exactly
that reason.

### R-14. A shared byte-boundary handler (the reference engine's shape)

**DESCENT proved it impossible, not merely expensive** (`engine16_descent.md`
"What was removed" item 2). A shared `is_end_of_byte` reached by `beq` from the
per-bit decode inherits only what is left of **its caller's** budget — 1-4
cycles by the time any predecessor path reaches it — while the store alone needs
`strb`(2) + advance(1) + bound(2) + taken branch(2) = **7**. 1-4 < 7 on every
predecessor path traced by hand: off by 2-7×, not a near miss.

**Condition to revisit.** At 48 MHz the caller's leftover roughly doubles to
2-8 cycles against the same 7, so it becomes *marginal* rather than impossible —
which is not a reason to build it, because the unrolled chassis costs nothing
and removes the question. Record it as: **the proof is budget-relative and would
need redoing at 32 cycles/bit**, and nobody should quote "shared handlers are
impossible" as an unconditional fact.

**Transferable, and it is the most reusable analysis technique in the corpus:**
**a callee reached by a taken branch inherits its caller's *residue*, not a fresh
budget.** Cost a shared handler as `min over callers of (budget − caller's cum at
the branch)`, never by its own length. That framing is what turned a stylistic
argument into a proof, and it applies to every timed handler in this project.

### R-15. A ping-pong sample register (no `PREV` update at all)

**GRAINUUM, dropped** (`.md` §9). Alternate which of two raw-sample registers is
"current" each bit, so nothing ever has to be moved. The arithmetic works for
plain bit decode but does not compose with a *shared* stuffed-bit/SE0 handler,
which would have to know which register is current for whichever slot called it.

**Condition to revisit.** A design with **per-slot** stuffed-bit and SE0
handlers rather than shared ones — which the merged engine already has (eight
`rx_eopN` stubs, one per cell). Nobody has re-costed it against that chassis.
Low value: the merged engine's capture is value-independent and already 5 cycles,
so there is no `PREV` update to delete. Stated for completeness, not as a lead.

**Transferable:** **composability is part of a mechanism's cost.** This one is
cheap in isolation and unusable with a shared handler; the same is true of
CLEANSHEET's padding (couples to placement) and of Design B's flush (couples RX
to TX cell internals, `turnaround.md` §7.4). Price the coupling, not only the
cycles.

### R-16. `bl`-staircase padding for exact-N delays

**GRAINUUM noted and did not use it** (`.md` §12). The Grainuum-lineage pad
staircase (`PRIOR_ART` S-1) hits an exact cycle count with no scratch register;
its advantage over inline `nop` is code size, which `ENGINE16_SPEC.md` §6 ranks
below fitting the budget, and at 1-2 delay sites it does not pay.

**Condition to revisit.** RAM pressure (§0). If the entry/phase-lock code's
inline `nop` runs become a measurable fraction of the 1812 B, the staircase
trades bytes back for the same exactness. Quantify before adopting: nobody has
measured how many bytes of the RX engine are padding.

**Transferable:** exactness is purchasable in **bytes** (inline `nop`) or in
**cycles-plus-a-branch** (a staircase), and which is right depends on which
resource is scarce. On this project that answer changed between the competition
(cycles scarce) and the merge (bytes scarce, §0). Re-ask it after every footprint
change instead of treating the earlier answer as settled.

## 3. Dead ends and negative results

**These are the most expensive knowledge in this document.** Each cost an
agent-run to establish, and each is trivially re-proposed by somebody who has
not read this. Every one is stated as a claim, with its evidence, and with the
specific condition that would change it — where such a condition exists. Several
have none, and saying so is the point.

### N-1. DMA cannot reach GPIO on this part — the IOPORT bus is core-private

**Claim.** No DMA configuration on PY32 can read `GPIOx->IDR`, so timer-triggered
DMA sampling of the bus is not available.

**Evidence** (`engine16_native.md` §2, corroborated independently in
`ENGINE16_RESULTS.md` from the address map rather than only from the entrant's
reading). RM §3.1 lists two masters (core, DMA) and three slaves (SRAM, flash,
AHB/APB bridge). Figure 3-1 draws GPIOA/B/F on **IOPORT**, which connects to the
core and not to the bus matrix; Table 3-2's bus column reads `I/O PORT` for those
ports and `AHB`/`APB` for everything else. From the address map: `DMA1_BASE` is
`AHBPERIPH_BASE` = 0x40020000 while `IOPORT_BASE` = 0x50000000, and no GPIO or
EXTI appears among the declared DMA request sources.

**The elegant part, and the reason this belongs in every future design note:**
*the same architectural decision that makes `ldr rd,[gpio,#IDR]` cost one cycle
is what puts GPIO out of the DMA's reach.* Fast GPIO and DMA-able GPIO are the
same trade made in opposite directions. On an STM32F0, where GPIO sits on AHB2,
DMA-from-`IDR` is a standard trick **and the port access is slower**. This is
exactly the class of finding a design transliterated from another platform would
import as a bug.

**Condition that would change it.** A different part. Nothing about clock,
budget or software changes a bus topology. One honest caveat the entrant states:
this is a negative claim from three consistent places in the manual, not a
measurement. The settling test is ten lines — `MEM2MEM=1`, `CPAR1=0x50000010`,
`CMAR1=&buf`, `CNDTR1=1`, `EN=1`, then read `TEIF1` — and **it has not been
run.** If someone wants to reopen this, run that, do not re-read the manual.

Two further problems it would still have if the bus were kinder, recorded so
they transfer: one sample per bit is not enough (LS is ±1.5 %, so a fixed
1.5 MHz sampler slips up to ±1.44 bit times over a 96-bit packet — it needs ≥2×
oversampling plus a software resynchroniser, a DMA request every 8 cycles and
~200 B of ring); and `BUILD_FACTS.md` §9 measures 432 B free on F003x4 with the
demo linked.

### N-2. SPI as a receive shift register has no resynchronisation path

**Claim.** No SPI configuration on this part can receive USB, and the miss is not
marginal.

**Evidence** (`engine16_native.md` §4). SPI slave mode needs SCK, and there is
no 1.5 MHz clock in the system phase-locked to the host and no internal route
from a timer output to SPI SCK; the best construction available is a board trace
from `TIM1_CHx` to `SPI1_SCK` started by a hardware trigger on the first SYNC
edge. It dies for the same reason N-1 needed oversampling: **a free-running
receive clock has no resynchronisation path.** LS is ±1.5 % (USB 2.0 §7.1.11),
so over a 96-bit packet the shift register slips up to ±1.44 bit times and is
sampling the wrong bit long before the packet ends.

The structural statement is worth memorising: **NRZI plus bit stuffing exists
precisely so a receiver can re-lock on a transition at least every 7 bits, and a
shift register clocked by a local oscillator is the one receiver architecture
that cannot use that guarantee.**

**Condition that would change it.** Hardware that re-times SCK from the data.
This part has none. Secondary reasons, recorded so nobody reopens it on a
cleverer clock scheme: SPI hands you raw wire symbols, so NRZI decode and
destuffing remain bit-serial software work with the added misery of stuff bits
crossing byte boundaries, and it costs an external wire, a pin and a timer to buy
nothing.

### N-3. Unrolling a whole packet fails on short-branch range

**Claim.** Full straight-line unrolling of an entire packet does **not** achieve
zero recurring taken branches on this ISA, so it spends kilobytes of flash to pay
the identical residual risk a small loop already pays.

**Evidence** (`engine16_cleansheet.md` "Why 8 bits, not 1 and not the whole
packet", confirmed by assembling). The Thumb-16 conditional branch (`beq`, T1)
has an eight-bit signed offset, ~±256 bytes. Every slot needs a `beq` to escape
on SE0, that test **must** be per-bit (N-6 / R-13), and the escape target must
reach the untimed tail. A 140-bit unroll spans several kilobytes, so a `beq`
from bit 3 cannot reach a tail placed after bit 140. Trampolines do not remove
the problem, they relocate it: something must keep the trampolines off the normal
path, and the only tool for that is **an unconditional always-taken branch** with
the same period — identical 2-vs-3 cost, identical recurrence.

**The useful positive form:** the constraint bounds the loop period from both
sides. CLEANSHEET's 8 slots span 160 bytes (`0x28`..`0xc8`) with the farthest
escape measuring 156 bytes, comfortably inside ±256; "twelve would have been
tight; sixteen would not have fit". So **one byte is the right granularity**, and
that is a measured range fact, not a preference.

**Condition that would change it.** Nothing in this project. It is a property of
the instruction encoding, not of the clock or the budget. Note carefully that
this does **not** contradict per-byte unrolling (§4): different claims, both
true, and `ENGINE16_RESULTS.md` records the distinction explicitly because it
looks like a contradiction on a first read.

### N-4. Interval decoding from input capture needs ≥36 MHz

**Claim.** Decoding a hardware capture stream of inter-transition intervals in
software does not keep up with the wire at 24 MHz. Not by a margin that tuning
recovers.

**Evidence** (`engine16_native.md` §6.3-§6.5, numbers from
`tools/engine16_cyc.py --exec ram` on the assembled object). The streaming decode
loop is **21-22 cycles per transition**, 22.75-23.9 including the once-per-byte
path — call it 23-24 against **16 cycles** of wire time worst case. The worst
realistic packet is legal and ordinary (an 8-byte DATA with an all-zero payload,
e.g. a DFU block of zeros): 91 transitions × 23.5 = 2139 cycles of decode against
1536 cycles of wire, so the decoder is **649 cycles behind at the SE0 window**
and the response cannot start before t = 2195 against a 1672 deadline and an
1808 host timeout — **late by 523 cycles = 33 bit times.** An IN token to address
0 endpoint 0 — the enumeration case, which must work — is 588 cycles of decode
against 512 of wire: 15 % slower than the wire from the first packet onward. "A
receiver that works on some packets is not a receiver."

**The crossover, which is the useful form of the result:**

```
cost per transition  <=  cycles per bit time
        23.5         <=  f / 1.5 MHz     ->   f >= 35.3 MHz
```

**Condition to revisit: 48 MHz, and it inverts completely.** The same object
code at 48 MHz catches up with 933 cycles to spare (30 % of the packet), reaches
its idle path *during* the 64-cycle SE0 window, sees EOP 64 cycles before the
EOP ends, and has ~278 cycles of margin. The interval table is unchanged —
intervals become 32n counts, up to 224 for n = 7, still inside a byte, so
`MSIZE=8` and the zero sentinel still work; one shift constant changes,
`n = (interval+16)>>5`. Note the shape: **at 48 MHz the software engines need 32
*exact* cycles per bit and this one needs ≤32 *inexact* cycles per transition.**
The peripheral design is the one that gets easier with clock. One thing not
verified: whether `CHIP_FACTS_XIAMATSU.md` §1's RAM-execution cost table still
holds at 48 MHz, where flash latency is non-zero (`CHIP_FACTS` §3) — the engine
is RAM-resident so it should, but "should" is not a measurement.

### N-5. EOP is a level, not an edge — a capture stream cannot see it

**Claim.** No edge-timestamping front end can detect EOP, ever. This is the trap
in the whole "all the data is in the transitions" idea, and the entrant records
that it had this wrong for most of its design.

**Evidence** (`engine16_native.md` §5). At EOP both lines are driven low for
2 bit times, then return to J. Consider D−: if the last symbol was K (D− low),
D− stays low through SE0 then rises — interval = (1..7 bit times) + 2. If the
last symbol was J (D− high), D− falls at SE0 start and rises at SE0 end —
interval = 2 bit times exactly. **Either way EOP appears in the capture stream as
an ordinary 2..7 bit-time interval, indistinguishable from data.** SE0 is a
coincidence of levels, and no amount of edge timestamping expresses a
coincidence of levels.

So EOP costs a `GPIOx->IDR` read plus a test, and it can only be read when the
CPU gets around to it — **a decoder more than 32 cycles behind misses the SE0
window entirely** (which is exactly what happens in N-4).

**The hardware alternative, evaluated, misses by about four cycles.** TIM1 in
slave reset mode as a retriggerable monostable with `ARR` = idle threshold gives
a "no edge for N cycles" interrupt. `ARR` must exceed the maximum in-packet gap
(7 bit times = 112, +1.7 for rate tolerance, plus jitter ≈ **116**), so the
update fires at end-of-EOP + 116 against a **120-cycle deadline** — before the
ISR has even been entered. **It is a useful watchdog for a lost packet. It is not
a response trigger.** Recorded because it is the first idea everyone has and it
fails by arithmetic rather than by taste.

**Condition to revisit.** At 48 MHz the deadline is 240 cycles and `ARR` is 232,
so the monostable clears it with margin — but by then N-4's decoder is keeping up
and reaches its idle path inside the SE0 window anyway, which is the cheaper
answer. The level-vs-edge claim itself is unconditional: it is physics plus the
USB encoding, and no clock changes it.

### N-6. The 2-vs-3 taken-branch cost is an unmeasured constant of the part

**Claim.** Not run-to-run randomness — an unknown constant. Nobody has measured
it, and it is on the critical path for every software engine in this project.

**Evidence.** `ENGINE16_SPEC.md` §2 prices a taken branch at 2-3 "depending on
alignment and on the preceding instruction"; at a 16-cycle budget an unresolved
±1 is 6 % of a bit cell. Two entrants independently arrived at the same
conclusion — once measured, either design is made exact by inserting one `nop` —
and both flagged it rather than hiding it (`ENGINE16_RESULTS.md`). That makes
**"measure the taken-branch cost" the single highest-value bench item in the
project.**

**How the merge dodged it rather than solving it** (`engine16_merged.md` §5). The
timed chain contains exactly nine control transfers: eight `beq rx_eopN`, one per
cell, **not taken** on every data path (1 cycle, no range), and one `bx r14` per
wire byte. Both `ENGINE16_SPEC.md` §2 and `CHIP_FACTS_XIAMATSU.md` §1 price `BX`
at a flat **3** while giving `B` as a range 2-3 — the asymmetry is the vendor's,
not an assumption. So the engine is exact **on the one assumption that `BX` is
3**; if the bench disagrees, cell 7 becomes 15..16 and the repair is one `nop`.
The transmit engine rests on the same single assumption
(`engine16_tx.md` §4).

**Condition that resolves it.** A bench measurement. Priority order for that
bench, from `engine16_merged.md` §5: (1) is `bx` a flat 3; (2) is a not-taken
conditional branch really 1 — every cell contains one, and if not, all eight
cells shift together; (3) `ldrh` and `strb` from RAM-resident code = 2.

### N-7. Entry-latency jitter gets proportionally worse as the bit cell shrinks

**Claim.** The interrupt controller's jitter is a fixed number of *cycles*, so
halving the bit cell doubles its cost as a fraction of the cell. No engine can
fix it by writing a cleverer bit cell.

**Evidence** (`engine16_grainuum.md` §10.1). M0+ architectural worst-case
interrupt latency at zero wait states is 15 cycles (TRM §3.6.1 via PLAN §2.2);
on top sits a variable component, and the coordinator's annotator run against the
reference engine reports its `EXTI2_3_IRQHandler` entry block at **28-32 cycles**
— a 4-cycle spread. That spread is 4/32 = 12.5 % of a bit cell at 32 cycles and
4/16 = **25 %** at 16.

A second, independent pressure draws on the same pool: dribble. The last data bit
before EOP must tolerate up to 260 ns (USB 2.0 §7.1.9/§7.1.14, `PRIOR_ART` D-9),
which is a fixed *time* — 6.24 cycles at 24 MHz against 12.5 at 48, i.e. the same
39 % of a (now smaller) cell. Dribble therefore costs nothing extra by itself,
but it eats the same margin the entry-jitter doubling is spending down. **The
combined-budget arithmetic has never been carried to a single number**, and
GRAINUUM says so rather than producing one.

**Condition that changes it.** Either 48 MHz (halves the fraction back), or R-8's
hardware capture front end, which deletes the term entirely — "the handler can
arrive 100 cycles late and lose nothing". Note that the phase-lock *mechanism*
is insensitive to entry jitter by construction (sample once at entry, wait a
fixed interval, then spin until the bus state changes relative to that first
sample: `rv003usb-arm.S:70-77`, kept by the merge) — what shrinks is the margin
available *after* locking, not the lock itself.

### N-8. `.global` branch targets defer range-checking to the linker

**Claim.** Making internal labels `.global` means the assembler cannot prove a
branch is in range; the linker finds out later, or the build succeeds and the
encoding is wrong for a reason nobody looked for.

**Evidence** (`engine16_merged.md` §7, §11). The referee made every branch target
local, and `objdump -r` then shows **nine relocations: four `R_ARM_ABS32` for
data and five `R_ARM_THM_CALL` for the C seam — no branch relocations at all.**
The transmit engine shows twelve, all `R_ARM_ABS32`, none of them branch
relocations (`engine16_tx.md` §4). This affects any entry that made internal
labels global — VUSB's eight EOP stubs among them. Given N-3, branch range is a
correctness constraint on this ISA, not a detail, so removing the deferral closes
a class of defect nobody had been checking for.

**Condition.** None — this is free, and it should be a standing rule for every
`.S` in this project. Cheap check: `arm-none-eabi-objdump -r` and look for any
`R_ARM_THM_JUMP*`.

### N-9. Deferring the decode does not remove the timing constraint

**Claim.** "Do it after the packet, untimed" is not an escape from 16 cycles; it
reappears as the same number for a different reason.

**Evidence** (`engine16_native.md` §6.1). The response depends on the packet, so
the decode must finish inside the response window — unless the decoder ran
*during* the packet, which it can, because the ring fills in hardware. The
constraint is then not latency but **whether the decoder keeps up with the wire,
and the wire delivers a transition every 16 cycles in the worst case.** Same
number, different reason. It is a genuinely weaker constraint — an average upper
bound on a free-running loop instead of an exact per-path equality on a
phase-locked one — and it still decides the outcome (N-4).

The same lesson from the other direction: CLEANSHEET's full deferral (R-1) turns
16 cycles/bit of timed work into 700+ cycles of untimed work in the one window
that has 104.

**Condition.** Unconditional as a *warning*: any future "just defer it" proposal
must state where the deferred work lands and what budget applies there.

## 4. Convergences

Where independent entrants arrived at the same thing. This is knowledge of a
different kind from the rest of the catalogue: it says the structure is
**forced, not chosen**. A document organised by entrant would hide it, which is
one reason this one is not.

### C-1. Per-byte unrolling — four directions, three derivations

* **GRAINUUM designed it**, to make the buffer bound a structural property
  (`engine16_grainuum.md` §1, §6).
* **DESCENT proved it**, from the reference engine's own arithmetic: a shared
  byte-boundary handler inherits 1-4 cycles of its caller's budget while a store
  needs 7, so "which byte" must become a compile-time fact
  (`engine16_descent.md`, "What was removed" item 2). **This is a proof, not a
  preference** — see §2 R-14 for the one condition under which it would need
  redoing.
* **VUSB inherited it** from the AVR school, as eight cells `usb_rx_cell0..7`.
* **BALANCE quantified it** without building it: removing `subs r5,#1` +
  `beq byte_boundary` + `b bit_cell` from its 17..18-cycle core gives 13, so an
  unrolled chassis would leave **3 cycles of slack per slot** — more than
  GRAINUUM's own tightest paths have (`engine16_balance.md` §8; arithmetic
  re-checked in `ENGINE16_RESULTS.md`).

And **CLEANSHEET established the limit of the idea** from the branch-range side
(§3 N-3), while `engine16_merged.md` §6 reached the same wall from the footprint
side (2.9 KB on a 3 KB part). So: **one byte is the right granularity**,
established from four directions and bounded from two.

### C-2. Carry-chain capture — three entrants, the same two instructions

`lsrs` to carry then `adcs` was written independently by CLEANSHEET, VUSB and
BALANCE. Three designs with different chassis and different decode strategies
converged on the same 2-cycle idiom for getting a pin level into a shift
register, which is about as strong a signal as this ISA gives that there is no
cheaper way.

### C-3. Deferring the CRC *computation* costs nothing in decision quality

GRAINUUM reached it by costing in-slot CRC against its own ledger (§7);
DESCENT reached it by tracing what the original's tail already required — the
reference engine never acts on the CRC value until `se0_complete_flash`
(`rv003usb-arm.S:264,308-311`), so computing it bit-by-bit was only ever a way to
avoid a second pass, never a way to decide anything early. CLEANSHEET states the
same thing from a third direction ("nothing about PID, bit count, bit-stuffing,
or CRC exists inside my timed loop at all").

The merge nonetheless keeps CRC in-slot — because the *turnaround* budget, not
the decision structure, is what needs the verdict at EOP (§2 R-5). Both
conclusions are correct; they answer different questions, and it is worth being
explicit about that because "three entrants deferred the CRC" reads like a
verdict the merge overrode.

### C-4. Two entrants: the branch cost is unmeasured, and the repair is one `nop`

GRAINUUM and CLEANSHEET both concluded that once the taken-branch cost is
measured on silicon, either design is made exact by inserting one `nop`, and
both flagged it rather than hiding it (`ENGINE16_RESULTS.md`). The transmit
engine and the merge later rest on exactly that repair for `bx`. Convergence here
promoted a design detail into **the highest-value bench item in the project**
(§3 N-6).

### C-5. Both halves reached "the C layer must be restructured" independently

`turnaround.md` §4.2 concluded the C layer should be taken *out* of the response
path because it never makes a decision the response depends on. `engine16_tx.md`
§5.2 and §8 concluded, from the transmit side, that the pre-staged arm and the
hardware transmitter both need the C layer to fill buffers **before the token
arrives** rather than inside the IN handler (`rv003usb.c:193`). Two different
analyses, one restructuring — and `engine16_tx.md` §8 notes that after it, the
hardware and software transmit routes converge to ~30 cycles and the hardware
advantage disappears entirely.

### C-6. Everyone put timed code in RAM, and for the same reason

Every entrant that expressed a placement chose RAM-resident timed code except
CLEANSHEET, which chose flash **deliberately**, to buy 8 cycles of exactly-known
padding from the flash column's 4-cycle stack access (§2 R-2). That makes the
pair a controlled comparison rather than a consensus with one dissenter — and
`ENGINE16_RESULTS.md` records that the placement decision is load-bearing in both
directions: relocating CLEANSHEET to RAM silently breaks its timing by 4 cycles
per bit, and relocating the merged engine to flash costs 4 cycles per table
lookup and the pipeline stops fitting.

`BUILD_FACTS.md` (via `STATE.md`) adds the sharpest form of the same point:
`.datacode` reaches RAM **incidentally**, absorbed by the stock linker script's
`*(.data*)` wildcard. A script spelling that rule `*(.data.*)` would place the RX
engine in flash **silently** — no error, no warning, successful build, all timing
wrong. That is recorded there as the highest-risk item found in the build system,
and it belongs in any reader's head next to this convergence.

## 5. Defects

All verified in source, none suspected. Split, because the two groups are not
worth the same:

**§5.1 — defects in code we did not write** (the py32 branch and the prior art).
These are the valuable ones. They exist in a shipping codebase, they are
reachable from the wire or from the build system, and each is written here with
enough file:line detail to be **reported upstream, which it should be**.

**§5.2 — slips in our own competition artifacts.** Instructive, and each names a
hazard class that will recur — but they are working breakage found and fixed
inside a week, and they should not crowd out §5.1.

### 5.1 Defects in code we did not write

Located on the `py32` branch at 0ad3c42 and in the mainline it was ported from.
Full evidence: `DEFECTS_VERIFIED.md` and `BUILD_FACTS.md`.

#### U-1. Endpoint bound off by one — branch-introduced, reachable from the wire

`rv003usb/rv003usb-arm.S:274-277` (`DEFECTS_VERIFIED.md` D-1):

```
	mov r2, #0xf  // endp
	and r2, r3
	cmp r2, #ENDPOINTS
	bhi done_usb_message_in // Make sure < ENDPOINTS
```

`bhi` is unsigned *higher*, so it rejects only `endp > ENDPOINTS` and lets
`endp == ENDPOINTS` through. **The comment on the same line states the intended
semantics**, so it is a coding slip, not a design choice — and the RISC-V
original gets it right (`rv003usb/rv003usb.S:526-528` uses `bgeu`), so the defect
was **introduced by the Thumb port**, not inherited.

Consequence, traced: `endp` reaches `usb_pid_handle_out/in/setup` unmasked
(`call_token_handler`, arm.S:301-305 passes r2 straight through) and each indexes
`ist->eps[endp]` with no further check (`rv003usb.c:165, 416`, and via
`current_endpoint` at `:231, 407`). `eps[]` is the **last** member of
`struct rv003usb_internal` (`rv003usb.h:200`), so `eps[ENDPOINTS]` runs off the
end of the struct into whatever the linker placed next. With the demo's
`ENDPOINTS 2` that is six bytes past the end, **reachable by any host that sends
a token addressed to endpoint 2** — i.e. by an unprivileged device on the bus,
not only by our own driver.

Fix: `bhs` (equivalently `bcs`). One instruction, identical encoding size and
cycle count, in flash-resident non-timing-critical code after
`se0_complete_flash`, so it cannot perturb any bit cell. **This is the cleanest
upstream report in the set: one instruction, wire-reachable, with the correct
original to compare against.**

#### U-2. RX byte store has no bound check — the author's own TODO, unfixed

`rv003usb/rv003usb-arm.S:145-148` (`DEFECTS_VERIFIED.md` D-2):

```
is_end_of_byte:
	// TODO: prevent buffer overrun
	mov BITCOUNT, #8          // 19
	strb SHIFT_BUF, [r2]      // 20
	add r2, #1                // 22
```

`r2` starts at `rxbuf + 3` (arm.S:80) and is incremented once per received byte
with no limit. The buffer is `rxbuf: .space 3 + USB_BUFFER_SIZE` (arm.S:32) with
`USB_BUFFER_SIZE 12` (`rv003usb.h:126`) = 15 bytes, in its own `.bss.rxbuf`
(measured size 0xf, `BUILD_FACTS.md` §3). A packet longer than the buffer writes
past it into adjacent `.bss`, from the wire.

**The part that makes it interesting rather than routine:** the trailing comments
`// 19 // 20 // 22` are the cycle budget — `is_end_of_byte` is *inside* the
cycle-counted path, so a bound check is not free. That is why this is a design
task with a cycle-budget criterion and not a one-line patch, and it is why the
competition treated it as a first-class design requirement
(`ENGINE16_SPEC.md` §3.8). Two independent structural answers came out of it
(§1 A-8, §2 R-3), both costing 0-2 cycles — which is the fix worth reporting
upstream, not "add a check".

#### U-3. The per-part `#if` variant is never *selected*

`DEFECTS_VERIFIED.md` D-3, narrower than the original claim. Both arms
**assemble** cleanly (`-DPY32F002Bx5=1` and `-DPY32F003x4=1`, both rc=0,
`BUILD_FACTS.md` §2). What never happens is selection: `Makefile.py32` pins
`MCU_TYPE = PY32F002Bx5`, so the non-F002B arm of the five `#if PY32F002Bx5`
sites in `rv003usb-arm.S` (`:402, :415, :444, :490, :530`) has never been built
by the branch's own build system. All five are pure cycle padding; none touches a
register (`BUILD_FACTS.md` §8), and the `#else` arm carries an alignment
assertion that arm's build has never evaluated — assembling the F003 variant
shows it passes.

Remedy is a build matrix over supported parts, not an assembly repair. **This
matters more after the target flip**, since F003/F030 becomes primary and
therefore exercises exactly the arm that has never been built.

#### U-4. `.datacode` reaches RAM by accident — the most dangerous item

`DEFECTS_VERIFIED.md` D-5, `BUILD_FACTS.md` §4 and §12. **No `.datacode` rule
exists anywhere.** The section reaches RAM only because the stock linker script's
`*(.data*)` wildcard swallows it (verified by linking: VMA 0x20000000, LMA
0x08000200). A script spelling that rule `*(.data.*)` would place the RX engine
in **flash, silently** — no error, no warning, successful build, every timing
figure invalid.

Unlike U-1 and U-2 it produces **no symptom at build time and an obscure one at
run time**. `BUILD_FACTS.md` §12 goes further and is worth reading before anyone
writes the guard: it built the obvious guard and showed that it **passes when it
should fail**. Report upstream as "add an explicit output-section rule and a
link-time assertion", not as a bug in the wildcard.

#### U-5. Objects escape the build directory and are not keyed by part

`BUILD_FACTS.md` §10.1. `rules.mk` maps `$(TOP)/<path>.c` to `$(BDIR)/<path>.o`,
and because sources are reached through `../`, the objects land **outside**
`Build/`. Two consequences, both real: `rm -rf Build` does not clean the tree,
and object paths carry no `MCU_TYPE`, so **changing part silently relinks another
part's objects**, compiled with a different `-D<PART>` and a different device
header.

Not theoretical — it produced a wrong result during this project's own
investigation: after an F003x4 build, `rm -rf Build` and a rebuild as PY32F030x8
failed with `undefined reference to BSP_RCC_HSE_PLLConfig`, and the obvious
reading ("F030 does not build") was **wrong**. From a properly clean tree
(`find . -name '*.o' -delete`) F030x8 builds fine. **Any part-to-part rebuild
conclusion on this build system is untrustworthy without that clean.** This one
is worth reporting upstream on its own: it manufactures false negatives.

#### U-6. An F003 build silently configures no clock at all

`BUILD_FACTS.md` §10.3. `demo_gamepad.c:15-23` sets the clock for two parts only:

```
#if PY32F002Bx5
	BSP_RCC_HSI_48MConfig();
#elif PY32F030x8
	BSP_RCC_HSE_PLLConfig();
#endif
```

An F003 build takes **neither** arm. It compiles, links and reports a healthy
image, and runs at whatever `SystemInit()` leaves. For a bit-banged USB stack
whose entire correctness rests on the clock, that is a trap: the port should fail
to compile for any part it has no clock path for, rather than produce a plausible
image.

#### U-7. `RCC_PLL_SUPPORT` is defined only for F030 — open, and it touches the flip

`BUILD_FACTS.md` §10.2, `STATE.md`. The vendor library compiles **no PLL path for
F003**, against Xiamatsu's measured claim that the PLL locks on F003. F003's
`RCC` struct has a reserved word at 0x0C exactly where F030 has `PLLCFGR`, which
raises the prior but does not settle it (F002B has the same hole and its PLL is
reportedly absent). Recorded here because it is unresolved and consequential: if
F003 has no PLL, "the primary family needs no servo" narrows to **F030 alone**,
and the 48 MHz option that §2 R-10 and §3 N-4 both lean on narrows with it.

Also environmental and worth one line: `py32f0-template` is an **empty
submodule**, so the branch cannot link as published (`DEFECTS_VERIFIED.md` D-4);
upstream pins cleanly at 289ffc8.

### 5.2 Slips in our own competition artifacts

Lower value than §5.1 — these were found and repaired inside the competition —
but each names a hazard class that will recur.

#### D-A. VUSB's ISR hangs forever on a bus stuck in a stuffing violation

`engine16_vusb.S:608-616`. Its sticky violation row (row 7) is sixteen identical
entries whose generator rule is `n = 0` — **they emit zero bits**. So during a
violation the emitted-byte counter freezes, its bound
(`engine16_vusb.S:180-181`) **can never fire**, and only SE0 can end the chain.
A bus left in a steady J or K after a spurious edge decodes as an endless run of
1s → violation → **the ISR spins forever, with interrupts masked.**

Found by the merge's termination check, not by reading. The repair is one line of
the generator — row 7 keeps its four bits, so the counter keeps advancing:

```python
def ut_row7(nib):                         # merged; VUSB's row emitted n = 0
    v = 0
    for j, i in enumerate(range(3, -1, -1)): v |= ((nib >> i) & 1) << j
    return ((v + 15) << 11) | (4 << 8) | (7 << 5)     # n = 4, state stays 7
```

The bound then fires after 24 emitted bytes ≈ 128 µs and the sticky state still
rejects the packet in the tail. Simulated:
`stuck bus: overrun=True emitted=24 state=7` (`engine16_merged.md` §6).

**Hazard class:** a bound that is driven by a counter which the error path stops
advancing is not a bound. Any liveness argument in a masked-interrupt engine must
name the quantity that monotonically advances *on every path including the error
paths*.

#### D-B. DESCENT's data-0 path falls through into the handler for the other bit

`engine16_descent.S`, macro `RX_BIT_PLAIN` (used for bits 1-7 of every byte).
Confirmed in raw `objdump`, not only through the annotator: `byte0_bit1` ends at
0x48 with a `nop` and **no branch**, so the data-0 path falls straight through
into `byte0_bit1_one` at 0x4a, executing `lsrs r4, r4, #1` a second time and then
`orrs r4, r2`. **Every received 0 bit would be shifted twice and have bit 7 set**,
and the path does not end at 16 cycles — it runs on into the next handler.

A copy-divergence slip, not a design error: `RX_BIT_LAST` ends its data-0 path
correctly with `b \next`; `RX_BIT_PLAIN` has `.rept 8 / nop / .endr` where the
branch should be. The author wrote the pattern correctly once and dropped the
branch in the other copy.

**The fix costs the claim.** Restoring `b \next` makes the data-0 path 8 real
cycles + 5 nops + a taken branch at 2-3 = **15..16**, i.e. the same
branch-ambiguity exposure GRAINUUM carries, not the exactness DESCENT claimed.
One-line repair; the design survives it; the *ledger* does not.

**Hazard class, and it is subtle:** fall-through is not inherently a defect. The
merged engine's cells 0-6 end with no branch and fall into the next cell, which
is **correct**, because that is what an unrolled sequence is (checked in
`objdump`: cell0 ends at 0xc8, cell1 begins at 0xca — contiguous). The defect is
falling through into the handler for *the other bit value*. Only reading the
disassembled addresses distinguishes the two.

#### D-C. A line-continuation backslash in ASCII art silently ate a `nop`

**GRAINUUM.** A stray `\` inside an ASCII-art comment was spliced by the C
preprocessor, swallowing the following `nop` and leaving one path a cycle short.
Caught by `objdump`, not by reading.

**Hazard class:** anything in this project that runs a `.S` through
`-x assembler-with-cpp` is exposed — which is every file here, because that is
the assemble command `ENGINE16_SPEC.md` §5 fixes. A comment is not inert under
the preprocessor.

#### D-D. VUSB's `USB_GPIO_BASE` defaults to the STM32 base

`engine16_vusb.S:35`: `#define USB_GPIO_BASE 0x48000000 /* GPIOA */`. PY32 GPIOB
is **0x50000400** (`BUILD_FACTS.md` §7). It sits behind an `#ifndef` so it is
overridable, but the default is wrong for the part this competition targets.

DESCENT, working from the same lineage brief, used the correct base — so this is
not "the AVR school's fault", it is one file's transliteration artifact. Recorded
as evidence that the platform-transliteration hazard the project owner named is
**real rather than hypothetical**, and that it hides in the constant nobody
reviews.

#### D-E. VUSB's priming is inconsistent with its own accumulator

`engine16_vusb.S:299-303` loads `r0` — the bit count, used as the shift amount in
`lsls r1,r1,r0` — with **2**, and sets the sentinel at **bit 24**. The biased add
(A-3) only works if bit `r0` holds the sentinel, so the first append lands where
there is no sentinel and the carry is lost. Independently, `r8` — which `SEG1`
reads as the low-nibble index — is saved at entry but **never initialised**, so
SYNC's low nibble comes from whatever the interrupted code left in `r8`.

Both are consistent with the file possibly predating a change it intended (its
last recorded action was "update the model to the shift+sentinel scheme and
re-verify"). Merged priming: `r0=0`, `r3=1` (sentinel at bit 0), `r8=2`.

#### D-F. VUSB's last cell was never budget-checked

Its `CELL` macro checks the cell it *closes*, and nothing closes cell 7. The
merge added `CELL_END`. **Hazard class:** a self-checking macro that validates on
close needs an explicit terminator, or the last instance is silently unverified —
and the last instance is exactly the one carrying the back edge.

#### D-G. The referee's own fall-through into `rx_eop0`

Introduced while moving the EOP stub block, caught in raw `objdump` rather than
by the tool. Recorded because it is the same class as D-B and it happened to the
person who had just written up D-B — which is the argument for checking
disassembly rather than trusting that one understands one's own layout.

#### D-H. The hand-written stuffing row whose sixteen entries were all wrong

**TX.** The state-6 row of `T_TX` was typed into the `.S` by hand instead of
being pasted from the generator. **All sixteen entries were wrong.** State 6 — a
byte ending on the sixth consecutive 1 — is reachable from ordinary data, so this
was a live defect, not a corner.

The bit-exact model reported **433 packets, 0 failures while the object was
broken**, because the model built its table from the generator rather than from
the object. Only a direct halfword-for-halfword comparison of the `.S` against
the generator found it: **352/368 identical**. See §6 L-1 — this is the defect
that produced the most important method lesson in the corpus.

#### D-I. Global branch targets across several entries

Any entry that made internal labels `.global` deferred branch-range checking to
the linker — VUSB's eight EOP stubs among them. Not a defect that fired, but a
class the merge closed by making every branch target local (§3 N-8).

## 6. Lessons about method

Separate from §5 and more valuable, because these will bite again.

### L-1. A model that shares a source with the artifact cannot validate that source

The TX bit-exact model reported **433 packets, 0 failures** while the assembled
object was broken, because the model built its `T_TX` table from the *generator*
and the object's state-6 row had been typed in by hand (D-H). Only a direct
`.S`-versus-generator comparison caught it: **352/368 identical**.

**Rule:** every table embedded in assembly in this project must be **diffed
against its generator**, not merely exercised through a model. The RX merge did
this and reported 400/400; the TX engine now reports 368/368. Exercising a model
tests the *design*; diffing tests the *artifact*, and they are different objects.

Note the shape of the failure — it is not that the model was weak. It was a good
model, run over 433 packets including maximum-stuffing and all-ones cases. The
coverage was irrelevant because the model and the artifact shared an ancestor.

**Corollary, and it is why this document quotes generators and never hex** (see
the convention note in the header): the generator is the authority and the `.S`
is a build artifact. A table written into a document as a dump cannot be checked
against anything, cannot be re-derived, and hides the relation between the bits
and their origin — which is the only part worth recording. `ut_entry` above (A-2)
is 12 lines and fully specifies 128 halfwords; the dump is 128 numbers and
specifies nothing.

### L-2. The cycle tool does not resolve control flow

`tools/engine16_cyc.py` annotates straight-line blocks between labels and reports
min/max; it says so in its own docstring, and deliberately prints a range rather
than one number because the 2-3 taken-branch ambiguity is real hardware
ambiguity, not tool uncertainty.

Consequences, all of which actually happened:

* GRAINUUM's block totals print 18..24 for a "slot" and this does **not** refute
  its 16-cycle claim — a bit cell there is a path that branches out and back, and
  the paths had to be traced by hand to confirm the claim (`ENGINE16_RESULTS.md`).
* The merged engine's cells print 16..18-20, and the maxima are EOP exits that
  leave the cell. The **minimum** is the number that matters on the data path.
* DESCENT's fall-through (D-B) and the referee's own (D-G) were both caught in
  **raw `objdump`**, not by the tool — because a missing branch is a control-flow
  fact and the tool has no opinion about control flow.
* The tool mis-scores a relocated `bl` as 1 cycle, because `objdump` prints the
  unrelocated halfword (`turnaround.md` §3). It is 4.

**Rule:** a branching path is traced by hand in raw `objdump -d`. Use the tool to
price instructions, never to validate a path. And pass `--exec` correctly:
`--exec ram` vs `--exec flash` swaps whole columns, and `--ioport <reg>` matters —
without it the annotator prices GPIO accesses as ordinary RAM.

### L-3. `.global` labels defer branch-range checking to the linker

Making internal labels local lets the **assembler** prove every branch range,
which on this ISA is a correctness constraint (§3 N-3), not a formality. The
check is `arm-none-eabi-objdump -r`: no `R_ARM_THM_JUMP*` relocations means every
branch was resolved and range-checked at assembly time. RX: nine relocations,
none branch. TX: twelve, none branch.

### L-4. Assert the ledger in the assembler

The `CELL`/`CELL_END` macros pad to exactly 16 and `.error` on overflow, so a
miscount is a build failure rather than a review finding. The same discipline
catches silent layout drift: `.if`/`.error` asserting
`usb_rx_chain - .Lprime == 28` exists because a `.balign` that quietly inserted a
`nop` would move the sample point and nothing else would complain (D-F is what
happens when the terminator is missing).

### L-5. Do not overturn someone's engineering decision until the whole source is read

`STATE.md` records an earlier pass that read **one column** of the two-column
cost table as if it were the whole thing and concluded RAM was expensive. It is
not, and the conclusion would have reversed a correct decision. That is now
PLAN.md §10.3 as a standing rule.

The two-column table is the specific trap: the columns **swap**. From
flash-resident code a RAM store is 4 and a flash literal load is 2; from
RAM-resident code they are 2 and **4**. Any statement of the form "X costs N
cycles" in this project is incomplete without its placement.

### L-6. Write in pieces and commit early — the durability record is unambiguous

Eleven agents died mid-run on this project. DESCENT's first attempt died on its
**first turn** by exceeding the 64000-token output ceiling in a single response,
having said only "I'll start by getting the documents" — nothing on disk, no
commits. Every entrant that wrote incrementally and committed as it went left
something usable even when it died: VUSB's engine survived and verifies, BALANCE's
import survived, and a 491-line ledger was salvaged from a worktree where the
agent died before committing at all.

`STATE.md`'s process note: three limit hits across two model families cost **zero**
work, because every agent wrote into an isolated git worktree that outlives it.
Keep that arrangement. And note the specific failure mode — "commit early and
often" is not sufficient advice on its own, because a single oversized write can
fail *before the first commit ever happens*.

### L-7. Re-check every self-reported number against the assembled object

`ENGINE16_RESULTS.md` re-checked every cycle claim with the tool and by hand.
That found DESCENT's defect (D-B), qualified GRAINUUM's claim (true as stated,
but with a 1-2 cycle spread on every path where CLEANSHEET's ordinary slot has
none), and confirmed BALANCE's honest miss exactly. It also found the *positive*
case: VUSB's own commit-message claim of "exactly 16 cycles" held, in the RAM
column.

The corollary that mattered most: **BALANCE declared it did not fit rather than
padding a ledger to hide it, and that was worth more than a borderline pass would
have been.** A competition judged on unverified self-reports is not a competition;
one where honest misses are penalised produces dishonest ledgers.

### L-8. Two documents can each be right and still leave a wrong conclusion between them

`turnaround.md` concluded "ACK-first is unnecessary if A ≤ 10". `engine16_tx.md`
reported A = 30 pre-staged, 80 cold. Neither is wrong; neither could reach the
joint conclusion, because each had one of the two numbers. The cross-check in
`STATE.md` does the join and inverts the recommendation: **SYNC-first is not the
fallback, it is the design**, and the pre-staged arm moves from "optimisation" to
"on the critical path for conformance".

It also surfaced a question **neither document contains**: an IN token is answered
with DATA, and a cold 8-byte DATA response at A = 112 exceeds even SYNC-first's
A ≤ 62. Whether pre-staging brings the DATA path under 62 has not been shown by
either piece of work. That is the open question the two leave between them.

**Rule:** when two workstreams each own one term of an inequality, somebody has to
own the inequality.

### L-9. State what you are unsure of, in the document, as a list


`turnaround.md` §11 is a list of eleven things its author could not verify —
including §8.4.5's exact wording, which is the clause the design knowingly
deviates from. `engine16_native.md` names its own largest unquantified assumption
(per-transfer DMA cost) and declines to present an ST number as a PY32 number.
GRAINUUM declines to produce a combined-margin number it could not carry through.

This is why the corpus is usable a quarter later: the unverified parts are
labelled rather than mixed in. A design note that claims no cost is not finished
(`ENGINE16_SPEC.md` §5).

## 7. SWAR and register density — open, and possibly on the critical path

**Nobody has built this.** It is a section rather than a note because the chain
it sits on ends at the one number the conformance question turns on.

### 7.1 How densely are the registers actually used?

The referee recorded "register pressure is total — all eight low registers plus
r8-r12/r14 are live in the timed chain" as a limitation
(`engine16_merged.md` §10.5), and `turnaround.md` §9 R2 turns that limitation
into a cost: the TX hot entry must fetch its state from `rxbuf+24..31`
**because the receiver has no free register to hand it in**, and "if the
transmitter needs its constants in registers at entry, the receiver cannot supply
them and A grows by two 2-cycle loads".

But *live* is not *full*. From the register contract at
`engine16_merged.S:125-131`:

| reg | role | meaningful bits |
|---|---|---|
| r3 | accumulator, data in bits 0..r0−1, sentinel at bit r0 | up to 17 — **already a SWAR construct** (A-3) |
| r5 | wire packer | 9 (8 samples + the previous byte's last) |
| r10 | CRC16 | 16 |
| r4, r7, r9, r14 | table base, GPIO base, rxbuf base, chain head | 32, legitimately |
| r0 | bits held in the accumulator, 0..15 | **4** |
| r12 | emitted byte count, bounded to 24 | **5** |
| r11 | unstuff state, held pre-shifted as `state<<5` | **3** |
| r6 | pin mask | **2** |
| r1, r2 | working / temp (r2 must be dead at every segment boundary) | — |

So four registers carry **14 meaningful bits between them**, in 128 bits of
register file. r8 is already doing two jobs by hand ("park: low-nibble index,
then the CRC16 table value"), and TX's r12 parks three things at different points
in the chain — so *temporal* packing is already in use; what is untried is
*spatial* packing.

### 7.2 Why it might matter more than it looks

```
pack sparse state  ->  free a register  ->  TX hot entry holds state instead
                       of loading it     ->  A falls  ->  conformance
```

A is the number the entire turnaround question hangs on (§2 R-10, R-11): at
A = 30 the response is 7.5 bit times against §7.1.18's 6.5.

**And here is the honest arithmetic, which does not support the strong form of
that claim.** R2's stated cost for the missing register is *two 2-cycle loads* —
about 4 cycles. A = 30 → ~26 gives (90+26)/16 = **7.25 bit times**: inside the
7.5 captive-cable allowance, still outside 6.5, and this port has no captive
cable (`turnaround.md` L2). Reaching Design A conformance needs A ≤ 14, i.e. 16
cycles off 30, and nobody has a route to that. So the correct statement is:
**freeing registers is worth a few cycles of A and is not by itself sufficient
for conformance** — it improves the position, and the decisive levers remain
48 MHz or SYNC-first.

Where it may matter more is on the *receive* side, where a free register is what
`engine16_balance.md` §8 says is missing to spread the byte store across slots,
and what `engine16_tx.md` §7 says would let the payload copy move out of the arm
path entirely ("it costs a register the current allocation does not have" — and
that copy is 4 cycles/byte, up to 32, **all of it before the first bit**). That
last one is worth more of A than R2's two loads are.

### 7.3 CLEANSHEET's rejected approach is already SWAR

The owner's own observation, and it is the clearest instance in the corpus of an
approach outliving its solution (§2 R-1). CLEANSHEET's deferred decode is
"capture densely, decode a word at a time". The merged engine already does
exactly that for NRZI, in `SEGA`:

```
lsrs r1, r5, #1 ; eors r1, r5 ; mvns r1, r1 ; uxtb r1, r1
```

`x ^ (x>>1)` is NRZI for **eight** bits in one instruction — and it is the same
one instruction for thirty-two. So the SWAR half of CLEANSHEET's idea was adopted
(A-4); what was rejected was only *where* the decode ran (§2 R-1). Nobody has
asked what else in the pipeline widens from 8 bits to 32.

### 7.4 The boundary, and it is sharp: detection is cheap, extraction is not

This is why a SWAR redesign is not simply better, and it should be stated before
anyone starts.

**Detection is cheap.** Finding six consecutive 1s in a word, by doubling rather
than by the naive `x & x>>1 & … & x>>5`:

```
lsrs r1, r0, #1 ; ands r1, r0      runs of >= 2
lsrs r2, r1, #2 ; ands r1, r2      runs of >= 4
lsrs r2, r1, #2 ; ands r1, r2      runs of >= 6
```

Six instructions for a whole word, all 16-bit encodings (assembled and
disassembled to confirm the encodings; the identity itself is arithmetic, not
benched). The naive form is ten or eleven. Either way it is far below one
instruction per bit.

**Extraction is not.** Removing the stuffed bits requires *compaction* — moving
the surviving bits down past the removed ones — and this core has no primitive
for it. `M0PLUS_ISA_FACTS.md` is explicit: **`rbit` is absent** (`rev` reverses
bytes only) and **`clz` is absent**, so there is no cheap "find the first
transition" and no bit-reversal trick to build one from. There is no PEXT, no
predication, and no barrel-shift-by-variable that helps here beyond `lsls rd, rs`.

**That is precisely why unstuffing stayed table-driven** (A-2): the table does
compaction by lookup, four bits at a time, in 2 cycles — which is exactly the
operation SWAR cannot express on this ISA. Detection was never the expensive
half.

### 7.5 The open question

> Is there a decomposition in which SWAR does the **detection and decode** over a
> whole word and the table does only the **compaction**, on a narrower index than
> the current (state, nibble) pair — and does that free enough register bits and
> table bytes to matter?

Unknown. What would have to be shown, in order:

1. That packing r0/r11/r12 into one register frees a register *net* — every use
   then costs an extract and possibly an insert, and the pipeline segments are
   already full (11 cycles each, 9 cycles of slack per 128). **r6 is not a
   candidate**: it is consumed by `ands r2, r6` inside the 5-cycle capture, in
   every cell, so packing it adds a cycle to the tightest path in the engine.
2. That the freed register buys more than it costs — see §7.2's honest arithmetic
   before assuming it does.
3. That a narrower table actually results. The RAM constraint (§0) is what makes
   this worth doing at all, and §2 R-6's dial is the cheaper lever to try first.

Recorded as an open question, not a result. Anyone picking it up should read
§2 R-4 first: BALANCE's fused mask is the other route to the same goal, it is
costed, and it lost to the table by an argument (76 cycles/byte with unstuffing,
store and CRC against 8 cycles/bit without) that a SWAR redesign has to beat too.
