# CRC round 2 — the NRZI mechanism, and what the free slots should carry

Second pass on receive integrity, prompted by three observations from the
project owner that the first two passes (`CRC_ALTERNATIVES.md`,
`CRC_PARITY.md`) did not address:

1. NRZI errors are "at least" paired — a persistent disturbance inverts the
   decoded stream from that point on, CRC field included. Does the residue
   still check out?
2. The 16-cycle bit cell is padded with `nop`. Why buy cycles with a 512-byte
   table when seven per bit are already free?
3. Transmit has no timing pressure, so why does it use a table at all?

**Results, in three lines.**

* **NRZI (§1).** A persistent level inversion is a *weight-1* error, not an
  inverted stream: differential decoding cancels the inversion after one bit.
  No physical disturbance inverts the decoded suffix, and even if one did, an
  inverted run of any length below 65 534 bits is never a CRC-16 codeword —
  computed, not argued. **No hole.** The one genuine weak spot found is
  elsewhere: a *sample slip* at the PID/data boundary of a 2-byte-payload
  packet passes the CRC half the time (§1.4). It is a property of USB's CRC
  init/xorout, not of this engine, and it needs a half-cell phase error that
  the lock excludes.
* **Bitwise CRC in the free slots (§2).** The premise is wrong for the merged
  engine: its cells are not 8-9 cycles of work plus `nop`, they are 5 of
  sampling plus ~9.5 of pipeline, and the padding is **9 cycles per wire
  byte**, not 56. A bit-serial fold is 6 cycles per *bit*, 41 per byte gated —
  it does not fit. The closed form (`CRC_ALTERNATIVES.md` §2) *is* the
  bitwise CRC in the free slots: 25 cycles, fits, 3 cycles left. The 512-byte
  table buys 8 of those 9 free cycles and nothing else.
* **Transmit (§4).** The owner is right: TX has no timing pressure and the
  table is unnecessary there. Moving the CRC into the untimed staging copy
  costs ~14 cycles per payload byte on the IN turnaround path unless the C
  layer pre-fills (`ENGINE16_CATALOG.md` C-5); with C-5 it costs nothing.

## Contents
1. The NRZI error model, corrected
2. Bitwise CRC16 in the free slots — verdict
3. What else the free slots can carry
4. Transmit: the table is unnecessary
5. Cheap checks from a fixed polynomial
6. Ranking and recommendation
7. Reproducing every number

---

## 1. The NRZI error model, corrected

### 1.1 Three disturbances that were being conflated

Let `s_i` be the D+ level the engine samples in cell `i`, and
`d_i = NOT(s_i XOR s_{i-1})` the decoded bit (`engine16_merged.S` `SEGA`: no
transition = data 1). Three different things can go wrong with the samples,
and they have three different signatures in `d`:

| disturbance | samples | decoded error | weight |
|---|---|---|---|
| **corrupted sample** `k` | `s_k` flipped, level recovers | `d_k` and `d_{k+1}` flip | **2**, adjacent |
| **persistent inversion** from `k` | `s_j` flipped for all `j >= k` | only `d_k` flips: for `j > k` both operands of the XOR are inverted and the XOR is unchanged | **1** |
| **phase slip** at `k` (sample duplicated or dropped) | stream shifted by one cell from `k` | one bit inserted (a `1`, since `s_k == s_k`) or one bit replaced and the rest shifted | length ±1 |

The second row is the one the owner asked about. NRZI is differential, so
inverting the line from some point on does **not** invert the decoded data —
it flips exactly one bit, the one at the boundary, and everything after it
decodes correctly. `CRC_PARITY.md` §3.2's "level inverted to EOP: weight 1,
100 %" is therefore *consistent* with the differential property, not in
tension with it: the 0.17 detection "without parity" in that table is the
structural checks catching one flipped bit 17 % of the time, and parity
catching it 97 % because weight 1 is odd. There was no contradiction; the
two statements were about the same weight-1 event.

**What would invert the decoded suffix?** For `d'_j = NOT d_j` for all
`j >= k` we need `s'_j XOR s'_{j-1} = NOT(s_j XOR s_{j-1})`, i.e. the sample
error `e_j = s'_j XOR s_j` must satisfy `e_j XOR e_{j-1} = 1` for all `j >= k`:
`e = 1,0,1,0,1,0,...` from `k` on. That is a half-bit-rate square wave added
to the line — every second sample corrupted for the rest of the packet. No
single event produces it: a level or threshold fault gives row 2, a
clock/phase fault gives row 3. It is simulated below anyway, because the
question was whether the CRC survives it, and that is computable regardless
of how physical the input is.

### 1.2 The algebra: is an inverted run ever a codeword?

The residue test is affine over GF(2): with init `I` and xorout `O`,
`residue(f) = L(f) + const`, so a corrupted field `f ^ e` passes iff
`L(e) = 0` iff `g16 | e(x)`. Inverting a contiguous run of `L` bits starting
at position `k` is `e(x) = x^k · ones(L)`, `ones(L) = 1 + x + ... + x^{L-1}`,
and since `gcd(x, g16) = 1` the position does not matter — only `L`.

```
g16 = (x+1)(x^15+x+1)
(x+1)      | ones(L)  iff  L even                   [ones(L) evaluated at 1 = L mod 2]
(x^15+x+1) | ones(L)  iff  (x^15+x+1) | x^L + 1     [gcd with x+1 is 1]
                      iff  ord(x) | L
ord(x) mod (x^15+x+1) = 32767 = 2^15 - 1            [computed: the factor is primitive]
```

so the smallest inverted run the CRC cannot see is `lcm(2, 32767) = 65 534`
bits. Computed directly: the residue of `ones(L)` for `L = 1, 2, ...` first
vanishes at **L = 65 534**. A USB LS data field is at most 80 bits. **An
inverted suffix, of any length, starting anywhere, including inside the CRC
field, is always detected.** Also verified in the data domain, the way the
owner posed it: 2000 random packets, every suffix length 1..n, 95 712 cases,
residue still valid in **0**.

Parity, by the same arithmetic, misses every *even*-length inverted run —
half of them. That is the 0.50 in `CRC_PARITY.md` §3.3's burst rows.

Two more facts from the same computation, used later:

* an adjacent pair `x^k(1+x)` — the single-glitch signature — is never a
  codeword;
* `gcd(x^8+1, g16) = x+1`, so **the XOR of all bytes carries nothing beyond
  parity**: over 5000 valid packets the byte-XOR takes 128 distinct values —
  every even-parity byte.

### 1.3 Simulation, sample level

Model (§7): payload 1..8 bytes, PID DATA0/DATA1, reference CRC-16, bit-stuff,
NRZI-encode from idle J; corrupt the *samples*; decode as the engine does
(`d = NOT(s ^ s_prev)`, unstuff with a sticky violation on seven 1s, bytes
LSB-first, partial byte dropped at EOP); apply the engine's structural checks
(`.Lrx_tail`: stuff violation, 2..12 bytes, SYNC = 0x80, PID complement,
DATA needs >= 4), then the residue test and the parity test. Disturbances
land at a uniformly random cell from the PID onward. A trial that leaves the
accepted bytes unchanged is discarded. 40 000 counted trials per row,
`random.Random(11)`. Self-test: 3000 clean packets decode byte-identically
and pass every check; 500 all-`0xFF` payloads exercise stuffing.

| disturbance | free checks | + parity | + CRC-16 | decoded error weight |
|---|---|---|---|---|
| 1 sample flipped | 0.174 | 0.223 | **1.000** | 2: 98.1 %, 1: 1.9 % |
| burst of 2 samples | 0.177 | 0.249 | **1.000** | 2: 96 % |
| burst of 8 samples | 0.190 | 0.376 | **1.000** | 2: 85 %, 1: 15 % |
| **level inverted k..EOP** | 0.166 | 0.974 | **1.000** | **1: 100 %** |
| alternating flip k..EOP (decoded stream inverted) | 0.238 | 0.616 | **1.000** | 1..90 |
| **duplicate sample k (slip late)** | 0.139 | 0.597 | **0.995** | length +1 |
| drop sample k (slip early) | 0.123 | 0.585 | **1.000** | length −1 |
| 2 independent samples | 0.312 | 0.384 | 0.998 * | 4: 93 % |

\* the shortfall is trials where the two flips fell within one bit of each
other and the pair errors overlapped into a 4-bit pattern; residual at the
2^-16 chance level plus a structural coincidence.

Row 4 is the owner's persistent inversion and it is weight 1, as §1.1
predicts. Row 5 is the decoded-stream inversion he described: odd weight
half the time (parity 0.62 — the structural checks add a little because an
inverted suffix runs into stuffing violations more often), and the CRC
catches all of it, as §1.2 proves it must. **Conclusion for observation 1:
the mechanism as stated — one inversion gives an inverted stream, CRC
included — does not occur on an NRZI line, and if it did the CRC would still
catch it. No hole.**

### 1.4 The one row that is not 1.000: sample slips

Row 6 is real and was in neither previous pass. Duplicating a sample — the
engine sampling one cell twice, which is what a phase error of more than
half a cell looks like — inserts a decoded `1` (no transition) and shifts
everything after it by one bit. The last bit falls off into the partial byte
and is discarded, so the packet keeps its length and its structure. **0.53 %
of those slips pass the CRC.**

Every undetected case in 60 000 trials (346 cases) but two has one shape: a
**2-byte payload**, the slip lands in the PID's trailing run of 1s (which is
the same as inserting a 1 as the first data bit), and the **last CRC bit is
1**. The two exceptions are at the 2^-16 chance rate.

Why exactly that shape, on paper. Write the transmitted field as `f`, `n`
its length in bits, `I = ones(16)` the init's contribution at positions
0..15 and `x^{n-16} O`, `O = ones(16)`, the xorout at the top. `f` is valid
iff `g | f + I + x^{n-16}O`. The slipped field is `f' = 1 + x·f` with the
top bit `f_{n-1}` dropped: `f' = 1 + x f + x^n f_{n-1}`. Substituting
`f = c + I + x^{n-16}O` with `g | c`:

```
f' + I + x^{n-16}O  =  x c  +  1 + (x+1) I  +  (x+1) x^{n-16} O  +  x^n f_{n-1}
                    =  x c  +  x^16  +  x^{n-16} (x^16 + 1)  +  x^n f_{n-1}     [(x+1)·ones(16) = x^16 + 1]
                    =  x c  +  x^16  +  x^{n-16}  +  x^n (1 + f_{n-1})
```

With `f_{n-1} = 1` this is `x c + x^16 (1 + x^{n-32})`, divisible by `g` iff
`ord(x) | n − 32`, i.e. **iff `n = 32`** for any USB-sized packet — a 2-byte
payload plus its CRC. With `f_{n-1} = 0` the extra `x^n` term makes it fail
at every `n` (a three-term polynomial is never divisible by `x+1`).
Exhaustive check: of the 65 536 two-byte payloads, 32 768 have a CRC ending
in `1`, and `1 || f[:-1]` is a valid field for **all 32 768**; for payload
lengths 0, 1, 3, 4, 8 it is valid for 0 of 3000. The affine `I`/`O` terms
are what make the code shift-invariant at exactly one length; a CRC with
zero init would be shift-invariant at *every* length and a slip would pass
at every length. USB's `0xFFFF` init is doing real work here.

A related wrinkle, found on the way: a slip inside the PID can turn
`DATA0 = 0xC3` into `0x87`, whose nibbles are still complements, and the
engine's PID test (`.Lrx_tail`: high nibble = ~low nibble, type = 3) admits
it as a data packet. 79 of the 346 cases were that. `0x87` is DATA2, a
high-speed PID that cannot legally appear on a low-speed bus; rejecting it
(and `0x0F`, MDATA) costs one compare in the tail and is worth doing
independently of anything else here.

**What this is and is not.** It is a property of USB's CRC-16 as specified,
and every receiver has it. It is not reachable by the failure modes the rest
of this file is about: it needs the sampling phase to be off by more than
half a cell (8 cycles) inside the first two bytes, and the phase lock is
±3.5 cycles at bit 0 with at most 1.5 % of drift per bit after that
(`engine16_merged.md` §4.4). It is recorded because it is the only case in
this study where "CRC passed" and "data wrong" coincide with probability
above 2^-16, and because mid-packet resync (`ENGINE16_CATALOG.md` R-7), if
it is ever built, must not introduce exactly this slip: a resync that steps
the phase by a whole cell is this failure by construction.

### 1.5 Verdict on observation 1

* Corrupted sample: weight 2, adjacent, 98 % measured. Parity blind, CRC 100 %.
* Persistent inversion: weight 1. Parity 97 %, CRC 100 %. `CRC_PARITY.md`
  had this right; its table and the differential property agree.
* Inverted decoded suffix: no physical event produces it; if produced, the
  CRC catches every length below 65 534 bits. **Not a hole.**
* Sample slip: the only structurally weak spot, 0.5 % undetected, entirely
  explained, out of reach of the engine's phase error, and a constraint on
  any future resync.

The previous verdict — CRC-16 stays, parity is a selectable weaker option —
**stands** on observation 1.

---

## 2. Bitwise CRC16 in the free slots — verdict

### 2.1 The premise, checked against the object

"8-9 cycles of real work in a 16-cycle cell, the rest `nop`, roughly seven
free cycles per bit" describes `engine16_minimal.S` — 9 instructions per bit,
39 `nop` in its eight cells and 88 more in its escapes (`SIZE_COMPARISON.md`
§4.2). It does **not** describe `engine16_merged.S`, the engine that is
shipped. Counted on the assembled object (`tools/engine16_cyc.py --exec ram
--ioport r7`, every cell 16):

| cell | sample | pipeline segment | `nop` |
|---|---|---|---|
| 0 | 5 | `SEG0` 7 | 4 |
| 1 | 5 | `SEG1` 11 | 0 |
| 2 | 5 | `SEG2` 9 | 2 |
| 3 | 5 | `SEG3` 11 | 0 |
| 4 | 5 | `SEG4` 9 (CRC: xor, index, `ldrh`, park) | 2 |
| 5 | 5 | `SEG5` 11 | 0 |
| 6 | 5 | `SEG6` 10 (CRC: gate, shift, xor) | 1 |
| 7 | 5 | `SEGA` 8 + `bx` 3 | 0 |
| **per wire byte** | **40** | **79** | **9** |

The padding is 9 cycles in 128 — 7 %, not 44 %. The merged engine already
does what the owner is asking for: the CRC (19 cycles), the unstuffing, the
byte assembly and the bounded store are *the* contents of the slots. What the
512-byte table buys is not "cycles that would be `nop` anyway"; it is the
difference between the table's 19 and whatever replaces it, out of a supply
of `9 + 19 = 28` cycles per wire byte that a CRC may occupy.

### 2.2 What a bit-serial fold costs, assembled

Four blocks, assembled with `arm-none-eabi-as -mcpu=cortex-m0plus` and
priced by the tool (`bx lr` excluded); each checked against the reference
CRC over 100 000 random states (§7):

| block | what | cycles |
|---|---|---|
| A | one reflected step, state bit-reversed so the input enters at bit 31 and the shift is `lsls`: `lsls r3,r4,#31 / eors r3,r0 / asrs r3,#31 / ands r3,r1 / lsls r0,#1 / eors r0,r3` | **6 / bit** |
| B | A, gated by a commit mask (input *and* shift suppressed) | **9 / bit** |
| C | the classic byte loop unrolled, branch-free (`lsrs / sbcs / mvns / ands / eors` ×8), ungated | **41 / byte** |
| D | the closed form of `CRC_ALTERNATIVES.md` §2.3, gated (re-measured) | **25 / byte** |

A needs the polynomial in a low register (`0x80050000`) — no `movs` can
build it and there is no free low register in either engine, so add a `mov`
from a high register: 7. The owner's estimate of "five or six instructions"
per bit is right; it is the *per byte* figure that kills it.

**Merged engine.** The slots do not see data bits one at a time. `SEG0`/
`SEG1` produce them from the unstuff table as nibble-sized clumps of `n =
0..4` bits, and a byte "finishes" in a data-dependent wire byte, so any fold
has to be byte-granular and gated by the commit mask. That is 8 × B = 72, or
C plus ~5 of gating = 46, against a supply of 28. **Does not fit, by 18
cycles at best.** The closed form at 25 is the bit-parallel form of the same
fold — eight LFSR steps collapsed into a 3-step prefix scan — and it *is* the
"bitwise CRC in the free slots". It fits with 3 cycles to spare. This was
found in the first pass; the brief's framing of it as "how to make CRC cheap
enough to afford" and the owner's "put it in the padding" are the same
design.

**Minimal engine** (for completeness, since it is the one the premise
describes). Its cells have the decoded bit in hand every cell, and the
stuffed-bit escape is a separate path that simply omits the fold — so a
per-cell serial step is structurally natural there. Free cycles per cell
after the byte work: 0, 4, 7, 7, 7, 7, 7, 0 = 39 per byte, against 8 × 7 =
56. **Does not fit either**, and the poly register does not exist. It would
also need the CRC's start gated past SYNC and PID, which per-bit code cannot
do cheaply; the fix is a 9-entry table of per-length residues in the tail,
not gating. Recorded so nobody re-derives it.

### 2.3 The cost the first pass did not count: the flush

`CRC_ALTERNATIVES.md` priced the closed form only inside the cell, where the
cycles are free. The same `SEG4`/`SEG6` code runs again in the **flush** —
untimed, and on the turnaround's critical path — for the byte in flight and
for the partial byte, i.e. twice on the worst-case `K = 1` path
(`turnaround.md` §5). In `turnaround_sketch.S`'s `FEMIT` the table fold is
12 cycles (`mov / eors / uxtb / lsls / adds / adds / ldrh(2) / mov / lsrs /
eors / mov`); the ungated closed form in the same place is 18 (`mov / eors /
lsls #24 / 3×(lsls, eors) / lsrs / eors / lsrs / eors / lsrs #16 / mov /
lsrs #8 / eors / mov`), counted, all single-cycle, not assembled.

| CRC in the flush | per byte | K = 1 path (two folds) | Design A first edge |
|---|---|---|---|
| table at offset 512 (today) | 12 | 24 | τ + 114 + A |
| **table at offset 0** | 10 | 20 | **τ + 110 + A** |
| `v256` byte table of `S` | 14 | 28 | τ + 118 + A |
| closed form | 18 | 36 | **τ + 126 + A** |

The deadline is τ + 124 (`turnaround.md` §2). **The closed form puts Design A
past the deadline for every value of A**, before the transmitter has spent a
cycle. The first pass's ranking was made on the cell budget alone and is
wrong for a 24 MHz build that intends to be conformant by ordinary means;
§6 restates it. (Under Design B, where the flush rides in the TX SYNC cells,
the +12 lands in the `8S − A ≥ 2` condition and is affordable: `A ≤ 50` at
`S = 8`.)

### 2.4 Verdict on observation 2, part 1

* The padding in the shipped engine is 9 cycles per wire byte, not 56. The
  CRC is already in the slots.
* A bit-serial fold is 6-9 cycles per bit; it does not fit either engine.
  The closed form is the bitwise fold, it fits the cell at +6, and it is
  the answer to "can we drop the table at zero cycle cost in the cell" —
  **yes**.
* But the table is not bought with cell cycles. It is bought with **flush
  cycles**, and those are the turnaround. At 24 MHz under Design A the
  512 bytes are worth 12-16 cycles of turnaround, which is the whole
  conformance margin. Keep the table, and move it to offset 0.

---

## 3. What else the free slots can carry

Supply first. Free cycles per wire byte in the timed chain, by check, RAM-
and flash-resident (`FLASH_TIMING.md`: the `strb` in cell 3 costs 2 more
from flash, which the redistribution it describes must take from the same
slack):

| receive check | in-cell cost | free / wire byte, RAM | free, flash |
|---|---|---|---|
| CRC-16, table at 512 (today) | 19 | 9 | 7 |
| **CRC-16, table at 0** | 17 | **11** | **9** |
| CRC-16, `v256` | 21 | 7 | 5 |
| CRC-16, closed form | 25 | 3 | 1 |
| parity | 14 | 14 | 12 |
| none | 0 | 28 | 26 |

Free cycles are not a lump: they sit in cells 0, 2, 4, 6, 7 in the CRC build,
and any new segment has to be cut at ≤ 11 per cell with `r2` dead at every
boundary. All eight low registers and r8-r12/r14 are live (`merged.md`
§10.5), so anything carried across a cell goes to memory — `rxbuf+24..31`,
unreachable by the masked store (`turnaround.md` §4).

### 3.1 A 32-bit accumulator, storing a word every 32 bits — **no**

The byte store is *speculative*: `SEG3` stores `r3` every wire byte because
the engine does not know, without branching, whether a byte finished this
wire byte — bit stuffing makes that data-dependent. A word store would be
speculative for the same reason, so it would also run **once per wire
byte**. The number of stores does not change; only their width does. No
cycles are saved in the timed path, and from flash the 4-cycle `str` lands
in cell 3 exactly as the `strb` does.

It also does not fit a register. The accumulator holds up to 15 data bits
plus the sentinel (`r0 ≤ 15`); a word boundary needs up to 32 + 4 bits plus
the sentinel — 37 bits — and the biased add `acc + (m << r0)` loses the top
of `m` for `r0 > 27`. Recovering them costs a second register and a
shift-pair, and there is no second register. Bit stuffing does not make it
"unworkable"; it makes it *pointless*, because the reason the store is per
wire byte is stuffing, and a wider store does not touch that.

The flash-residency problem in cell 3 is the store's *cost*, not its
*count*. The fix is the one `FLASH_TIMING.md` names — compute the index a
cell earlier — and it needs 2-3 cycles of slack, which the table build has
and the closed form does not (table: 9 → 7 after the fix; closed form: 3 →
1).

### 3.2 Pre-staging the response during reception — **yes, and it is the best use**

`turnaround.md` §4 lists what can leave the tail: SYNC compare (4), PID
complement (6), PID type dispatch (8), "response owed, which emitter" (4),
C-call marshalling (6) — **28 cycles**, of which the first four depend on
bytes 0-1 and can run from wire byte 2 on (the pipeline is one byte behind,
so byte 1 is decoded in byte 2's cells and usable in byte 3's). They are
worth 44-64 cycles of tail (`turnaround.md` §3: tail 56..76 today, 12 in
the sketch), i.e. **3-4 bit times** of turnaround.

| receive check | free in bytes 2..5 | 28 fits? |
|---|---|---|
| table at 0 | 44 (RAM) / 36 (flash) | **yes** |
| table at 512 | 36 / 28 | yes, no margin in flash |
| `v256` | 28 / 20 | RAM only, no margin |
| closed form | 12 / 4 | **no** |
| parity | 56 / 48 | yes |

For a **token** (4 wire bytes) only byte 3's slack exists after the PID is
usable: 9-11 cycles, which is P1 + P2 and nothing more. Token dispatch
therefore stays in the tail, and `turnaround.md` §4.1's pattern match (8
cycles, validates address + endpoint + CRC5 together) is what makes that
affordable. For a **DATA** packet (≥ 6 wire bytes) all 28 fit in bytes 2-5
with the table at offset 0.

This is the claimant that decides the CRC question: the table's cycles are
worth 3-4 bit times of turnaround via the tail plus 1 bit time via the
flush (§2.3), and the closed form forfeits both.

### 3.3 CRC5 for tokens in the slots — **no**

Same mechanism would work — a closed form for `x^5+x^2+1` at 8 steps is a
prefix scan like §2 — and it would need its own 25 cycles gated by PID type,
in the same slots the CRC-16 uses. It buys nothing: the tail's two
`bl .Lcrc5_byte` (~60 cycles on the IN path) are replaced outright by the
pattern match, which is 8 cycles, needs no table (drops `T_CRC5`, 32 B), and
also performs the address filter. Struck.

### 3.4 Others

* **Mid-packet resync (R-7).** ~9 cycles per byte by the catalogue's
  estimate, i.e. all of today's slack; incompatible with §3.2 at 24 MHz
  regardless of CRC choice. Not needed until the bench says drift does not
  close; and §1.4 adds a design constraint — it must step the phase by less
  than half a cell, never by a whole one, or it manufactures the one slip
  the CRC half-misses.
* **Calibration timestamp arithmetic.** The stamp is 2 instructions at entry
  and the arithmetic runs in `py32_hsical_event` after the reply is on the
  wire (`merged.S` `.Lusb_done`). It is not on the turnaround path. Nothing
  to move.
* **Buffer bound.** Already in cell 3 at 2 cycles, and structural. Nothing
  to move.
* **Address filter.** 4 instructions on the token tail path; subsumed by the
  pattern match, which compares the whole `(addr, endp, crc5)` halfword.
* **Reject DATA2/MDATA PIDs (0x87, 0x0F).** One `cmp` in `.Ldata`; not a
  slot item but found in §1.4 and worth one line.

---

## 4. Transmit: the table is unnecessary — with one condition

`engine16_tx.S` computes the CRC in its timed chain: `TXSEG1` (11: xor,
index, `ldrh`, gate) and the first six of `TXSEG2` (shift, xor, commit,
complement) — ~17 of the 88 pipeline cycles per byte, against 5 of `nop`.
`usb_send_data` copies the payload into `usb_txbuf` first, untimed, at 4
cycles per byte by computed entry (`engine16_tx.S:330-355`), so the CRC has a
natural home in that copy, or in the C layer before the copy, as LemcUSB does
(`CRC_ALTERNATIVES.md` §9.1).

**The owner is right that the table is unnecessary there** — the wire is not
waiting on a table lookup during transmission, the chain would have 21 free
cycles per byte instead of 5, `r8`'s double duty goes away, and the CRC can
be ~40 bytes of C with no table. **He is wrong that the chain has no timing
pressure**, in one specific sense: the chain *starts* on the turnaround
deadline, and the in-chain CRC is overlapped with transmission — it costs
zero cycles before the first edge. Moving it in front of the chain costs, per
payload byte, ~14 cycles for the closed form or ~45 for the bitwise loop, on
the cold-entry path that `engine16_tx.md` §5.1 measures at 112 cycles for 8
bytes already:

| where the TX CRC is computed | cost before the first edge, 8-byte DATA |
|---|---|
| in the chain (today) | 0 |
| in the staging copy, closed form in asm | +112 |
| in the staging copy, bitwise C | +360 |
| **in the C layer before the token (C-5)** | **0** |

So the move is free exactly when `ENGINE16_CATALOG.md` C-5 lands — the C
layer fills the staging buffer, CRC included, in the main loop before the
IN token arrives — and is a regression of 7-22 bit times otherwise. C-5 is
already wanted by `turnaround.md` §4.2 for its own reasons, so the
recommendation is unchanged from the first pass: **do it, after C-5.** What
it costs to move: the `poly_function` argument changes meaning (the caller
supplies the two CRC bytes and `length` includes them), `usb_send_empty`
becomes a 2-byte send of `0x00 0x00`, `TXSEG1`/`TXSEG2` lose ~30 bytes, and
the C side gains ~40. The 512-byte table stays as long as the receive engine
uses it (§2.4 says it should), so the saving is TX chain cycles and
simplicity, not bytes.

---

## 5. Cheap checks from a fixed polynomial

The brief asks for candidates in parity's cost class beyond the factor
structure. The space is smaller than it looks, and it can be closed with one
argument and one exhaustive check.

### 5.1 The argument

The received field is `f = c + a`, `c` in the shortened cyclic code `C_n`
(`g16 | c`, `n ≤ 80`), `a` the fixed init/xorout offset. A check is a
function that is constant on `C_n + a` and not constant off it.

* **Linear checks.** A linear functional constant on `C_n` vanishes on it,
  so it is in the dual, which has dimension 16 and is spanned by the 16
  syndrome bits. So *every* linear check is a linear combination of the CRC's
  own bits — there are 65 535 of them and nothing else. Each has a fixed
  0/1 mask over the bit positions. The check is "cheap" — constant cost per
  byte, no table, no position counter — only if the mask is **periodic** in
  the position: the same byte-mask applied to every byte. A functional
  `u` has period `p` iff `g16 | u·(x^p + 1)`, and since `(x^15+x+1)` is
  irreducible and `deg u < 16`, that forces `(x^15+x+1) | x^p + 1`, i.e.
  `32767 | p`. **No period below 32 767 exists except through the `(x+1)`
  factor, which is parity.** Checked exhaustively: over all 65 535 nonzero
  syndrome combinations at length 48, exactly one has a byte-periodic mask —
  `u = 0xFFFF`, mask `11111111`, parity.
* **Residues modulo another polynomial.** Constant on the code iff the
  polynomial divides `g16`. Exhaustive over every polynomial of degree 1..8
  against 400 valid fields: the residue is constant for exactly one, `0x3 =
  x+1`. Byte-XOR (`x^8+1`), nibble-XOR (`x^4+1`), any checksum with a
  power-of-two period — all reduce to parity (§1.2: byte-XOR takes 128
  values on valid packets, every even-parity byte).
* **Evaluation at a point.** Over GF(2) the only point is 1 — parity. At
  `α ∈ GF(2^15)`, a root of `x^15+x+1`, evaluation *is* the 15-bit residue,
  the full CRC minus its parity bit; not cheaper (same LFSR, one bit
  shorter).
* **Non-linear checks.** A function constant on an affine subspace and not
  on its cosets is a function of the coset label, which is the syndrome.
  Computing it means computing the syndrome. No escape.
* **Checking the received CRC field against a partial computation.** Every
  CRC bit depends on every message bit; to predict any one bit of the field
  the full state is needed. No escape.
* **The shortened code.** Length ≤ 80 ≪ 32 767 gives minimum distance 4 —
  all 1-, 2-, 3-bit errors and all bursts ≤ 16 caught — but that is a
  strength of the full check, not a source of a cheaper one.

**So the design space of checks costing O(1) per byte with a constant mask
is exactly {parity}, and the next point on the curve is the full 16-bit
residue at 17 cycles per wire byte.** Reasoned from the dual-code dimension;
the two exhaustive checks are the measurement.

### 5.2 Three things that are *not* codes and are worth a line

* **Single-glitch correction.** The dominant error is `x^k(1+x)`; its
  syndrome identifies `k` uniquely, so the tail could *correct* it. Rejected:
  USB's recovery is silence and a retry (`turnaround.md` L10-L11), a
  miscorrection would ACK wrong data, and the discrete log is 88 × 5 cycles
  or a 64 KB table.
* **Free wire-state parity** (`CRC_PARITY.md` §4.3): 0 cycles, weaker than
  byte-XOR parity by 3 points. Already recorded there.
* **PID range**: reject `0x87`/`0x0F` (§1.4). One compare, closes a real if
  remote acceptance.

### 5.3 Summary table

| check | cycles / wire byte | bytes | detects, 1 wire glitch (§1.3) | ordering preserved |
|---|---|---|---|---|
| structural only | 0 | 0 | 0.174 | yes |
| parity, byte-XOR | 14 (in `SEG4`/`SEG6`'s place) | 0 | 0.223 | yes |
| parity, wire identity | 0 | 0 | 0.19 | yes |
| **CRC-16, table at 0** | **17** | 512 (shared) | **1.000** | yes |
| CRC-16, `v256` | 21 | 256 | 1.000 | yes |
| CRC-16, closed form | 25 | 0 | 1.000 | yes, but §2.3 |
| anything else linear and cheap | — | — | — | does not exist (§5.1) |

---

## 6. Ranking and recommendation

Ranked by what each does for the two numbers that matter at 24 MHz — the
turnaround (`turnaround.md`: deadline τ+124, Design A at τ+114+A today) and
the undetected-corruption rate — with bytes third, because in the
flash-resident configuration `FLASH_TIMING.md` says is the one to finish,
the table costs flash on a part with 14 KB of it spare.

| # | item | turnaround | detection | bytes | verdict |
|---|---|---|---|---|---|
| 1 | **Keep CRC-16; move `T_CRC16` to offset 0** | −4 (flush) | unchanged | 0 | **do first, free** |
| 2 | **Spend the slack on A-16 pre-staging** (P1-P5, 28 cycles in bytes 2-5) | −44..−64 (tail) | — | +~30 code | **do; needs ≥ 9 free/byte, which 1 gives** |
| 3 | TX CRC to the C layer at pre-fill | 0 with C-5, +7..22 bit times without | — | −30 asm, +40 C | **do, after C-5** |
| 4 | Reject DATA2/MDATA PIDs in `.Ldata` | +1 | closes §1.4's PID alias | +4 | do |
| 5 | Pattern-match tokens instead of CRC5 (`turnaround.md` §4.1) | −50 on IN path | stronger (address included) | −32 (`T_CRC5`) | do; already recommended there |
| 6 | Parity (`USB_RX_CHECK=1`) | −12 | 0.22 vs 1.00 | −24 | unchanged: selectable, not default |
| 7 | Closed-form CRC-16 | **+12..+16** (flush) | unchanged | −512 | **not at 24 MHz under Design A**; right answer for Design B, for a RAM-resident F002B build, and at 48 MHz |
| 8 | `v256` | +4..+8 | unchanged | −256 | dominated by 7 where 7 applies, by 1 where it does not |
| 9 | Bit-serial CRC in the slots | n/a | — | 0 | does not fit either engine (§2.2) |
| 10 | 32-bit accumulator | 0 | — | 0 | rejected: the store is per wire byte regardless (§3.1) |
| 11 | CRC5 in the slots | 0 | — | 0 | dominated by 5 |
| 12 | Mid-packet resync | −9 free/byte | — | — | not until the bench asks; must step < ½ cell (§1.4) |
| 13 | Other cheap checks | — | — | — | none exist (§5.1) |

**What changed from the previous passes.**

* `CRC_ALTERNATIVES.md` §11's #1 — adopt the closed form — is **reversed for
  the 24 MHz Design A build.** It was costed in the cell, where cycles are
  free, and not in the flush, where they are the turnaround; there it is
  worth +12..16 cycles, i.e. the entire conformance margin. The 512 bytes
  buy one bit time of turnaround and the slack that pre-staging needs. That
  is a good price in flash and a bad one only in RAM.
* `CRC_PARITY.md`'s verdict **stands**, and observation 1 strengthens it:
  the persistent-inversion class is weight 1, parity's one good case, and
  the CRC catches every inverted run below 65 534 bits anyway.
* Observation 2's premise — 7 free cycles per bit — is true of the minimal
  engine and false of the merged one (9 per byte). The merged engine already
  fills its slots with the CRC; the question was never "afford the CRC" but
  "who else gets the 9", and the answer is the turnaround.
* Observation 3 is right about the table and needs C-5 to be right about
  the cost; unchanged from `CRC_ALTERNATIVES.md` §2.6, now with the number
  (+112 cycles before the first edge without C-5).
* New: the sample-slip hole (§1.4), the DATA2 alias, and the proof that
  parity is the only cheap linear check (§5.1).

**The recommendation in one line.** At 24 MHz keep the table, put it at
offset 0, spend the slack on pre-staging the dispatch, move the TX CRC to
the C layer once C-5 lands, and reject `0x87`/`0x0F`; take the closed form
only where the flush is not on the critical path or where 512 bytes of RAM
decide whether the part fits.

## 7. Reproducing every number

Scripts: `tools/crc_round2_nrzi.py` (§1.3, §1.4) and `tools/crc_round2_algebra.py`
(§1.2, §5.1), plain python3, no dependencies; quoted here in the only form that
matters — what they compute.

**§1.3 / §1.4 — `crc_round2_nrzi.py`.** Reference `crc16_bitwise` (self-check
`0x4B37` on `"123456789"`), `usb_data_field` = payload + complemented CRC;
`stuff` (a 0 after six 1s), `nrzi_encode` from idle J (data 1 = no
transition), `decode` (`d = 1 if s == prev`), `unstuff` (drop the bit after
six 1s; a seventh 1 sets a sticky violation), `to_bytes` LSB-first dropping
the partial byte; `receive` applies violation, `2 ≤ len ≤ 12`, `SYNC ==
0x80`, PID nibble complement, `DATA needs ≥ 4`, then residue `0xB001` and
even parity. Disturbances: `flip_one`, `invert_from` (all later samples
inverted), `alternate_from` (every second later sample), `dup_sample`
(`s[:k+1] + s[k:]`), `drop_sample`, `burst`. 40 000 counted trials per row,
payload 1..8 bytes, `random.Random(11)`, `k` uniform over cells ≥ 8. The
slip analysis: 60 000 trials with `random.Random(9)`, undetected cases
binned by `(payload length, last CRC bit, field == 1||field[:-1], PID
intact)`; the exhaustive 2-byte check enumerates all 65 536 payloads.

**§1.2 / §5.1 — `crc_round2_algebra.py`.** GF(2) `pmod`/`pmul`/`pgcd` on ints;
`(x+1)(x^15+x+1) == 0x18005`; order of `x` mod `x^15+x+1` by repeated
doubling (32 767); `ones(L) mod g16` incrementally (`r = (r<<1 | 1) mod g`),
first zero at 65 534; inverted suffixes of every length over 2000 packets;
`(x^15+x+1) | x^p+1` for `p = 8, 16, 32, 64` (all false); `gcd(x^8+1, g16)
= 0x3`; byte-XOR value count. The exhaustive functional check builds the 48
syndrome columns of single-bit fields with init 0, forms all 65 535
combinations, and tests byte-periodicity of the mask; the polynomial check
tests every `h` of degree 1..8 for a constant residue over 400 valid fields.

**§2.1 — cells.**
```
arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c doc/py32/engine16_merged.S -o m.o
python3 tools/engine16_cyc.py m.o --exec ram --ioport r7 --budget 16
```
(`--exec flash --flashdata r4` for the flash column; cell 3 reports 18.)

**§2.2 — `variants.s`.** Blocks `serial_bit`, `serial_bit_gated`,
`serial_byte`, `closed_form`, each ending in `bx lr`; `arm-none-eabi-as
-mcpu=cortex-m0plus -mthumb`, then `engine16_cyc.py --exec ram`: 9 / 12 / 44
/ 28 including the 3-cycle `bx`. A and C were transcribed instruction by
instruction into Python and checked against the reference over 100 000
random `(crc, bit)` and `(crc, byte)` pairs: 0 mismatches each.

**§2.3 — flush.** Counted by hand from `turnaround_sketch.S`'s `FEMIT`
(12 cycles of CRC fold with the table at 512) and the ungated closed form
(18); every instruction single-cycle except `ldrh` at 2. Not assembled;
labelled as counted throughout.

**§3 / §4 — cycle budgets** are arithmetic on figures already measured in
`engine16_merged.md` §4.2, `turnaround.md` §3-§5, `engine16_tx.md` §3-§5,
`FLASH_TIMING.md`; each is cited where used.

**Measured vs reasoned, in one place.** Measured: every detection rate in
§1.3-§1.4, every algebraic fact in §1.2 and §5.1, the cell table in §2.1,
the four block costs in §2.2. Reasoned: the flush deltas in §2.3 (counted),
the pre-staging fit in §3.2 (arithmetic on `turnaround.md`'s figures), the
TX cold-entry deltas in §4 (14 cycles per byte for the closed form in the
copy is counted, not assembled; 45 for bitwise C is an estimate), and the
statement that the phase lock cannot produce a half-cell slip inside the
first two bytes (from `merged.md` §4.4's ±3.5 cycles, with the caveat that
exception-entry jitter is unmeasured — `turnaround.md` §11).
