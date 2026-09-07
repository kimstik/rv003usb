# Audit: computation that is discarded, and content that carries no information

Two findings were reached by luck in the work that produced `engine16_merged.S`:

* **Find 1** — a computation whose result is discarded. `SEG4`'s table step and
  the whole of `SEG6` maintain a CRC-16 residue that `.Ltoken` never reads, so
  the IN flush drops them: 17 cycles (`design_b_in.md` §2).
* **Find 2** — content that carries no information. SYNC is eight bit times of a
  fixed pattern, so it can go on the wire before the CRC verdict exists. That is
  Design B (`turnaround.md` §7).

This document is the systematic version of that search: every value the engine
maintains against every path that reads it (§A), every fixed pattern on the wire
in both directions (§B), the placement question (§C), and the pipeline depth
(§D). §E ranks what came out of it; §F is what was implemented.

Everything below is priced flash-resident, `USB_RX_CHECK=CRC16`, with
`tools/engine16_cyc.py --exec flash --ioport r7 --flashdata r4`.

---

## 0. The frame: what a cycle is worth, and where

Three claims fix the value of every finding in this document. They are the
reason most of §A and §B produce *nothing*, and they are worth stating before
the tables rather than after.

### 0.1 Only four paths exist, and only two of them touch a wire deadline

| path | entered when | drives the bus | on a deadline |
|---|---|---|---|
| **P-KA** | SE0 already present at ISR entry | no | no |
| **P-TB** | `TB_OWED` set at EOP (host DATA → our ACK) | yes | **yes**, τ+124 |
| **P-TI** | `TB_OWED` clear and `rxbuf[1] == 0x69` (IN token) | yes | **yes**, τ+124 |
| **P-RX** | everything else | no | no |

P-RX is the ordinary 176-cycle flush plus `.Lrx_tail`. Nothing it can handle
requires a reply inside 6.5 bit times: a SETUP or OUT token is answered by the
host's next packet, a handshake from the host is answered by nothing, an
unaddressed token is dropped, SOF is dropped, and a DATA packet that reaches
P-RX is one we did not owe a handshake for. The one apparent exception —
`.Lt_in`'s `bl usb_pid_handle_in`, which calls `usb_send_data` — is not one:
every IN token reaches P-TI, and `.Lti_abort` sets `usb_tx_suppress` before
falling into `.Lrx_tail`, so the C layer's transmit is swallowed.

**Consequence: a cycle removed from the ordinary flush or the tail is worth
nothing.** This is what disposes of the two suspicions the brief listed first
(the handshake's CRC, the keepalive's arithmetic); both are real discards and
both are on P-RX. They are recorded in §A anyway, because the table is the
deliverable.

### 0.2 The first-edge time is a sum of exactly five terms

*(§0.2 and §0.3 describe the engine as this audit found it. §F.6 has the
figures as it now stands.)*

For P-TB (`turnaround.md` §11.1) the first wire edge is

```
  τ + detect(4..5) + stub(11..12) + B(K) + head(35) + TBCELL(2)
```

where `B(K) = SEG_K..SEG6` is the segments of the byte in flight that EOP
interrupted, and `head` is `usb_tb_head` = 4 pad + NRZI 11 + SEG0 7 + TBARM 13.
The worst case is K=1, `B(1) = 61`, giving **τ+115** against a deadline of
τ+124.

`B(1) = (total per-wire-byte pipeline) − SEG0 − SEGA = 76 − 7 − 8 = 61`, and
that identity is invariant under re-cutting the segments: moving work between
cells changes `B(2..5)` but not `B(1)`. **So the ceiling margin on the ACK path
is governed by the total per-wire-byte cost of `SEG1..SEG6`, and by nothing
else.** Any proposal that only moves work between cells is worth zero here.

### 0.3 Until §E.1, a *uniform* saving was worth nothing either

The legal window is `[τ+60, τ+124]`, 64 cycles wide (`turnaround.md` §2). The
measured spread across K is `[τ+62, τ+115]` (min-branch to max-branch), 53 of
those 64. The floor is held by K=6 at τ+62, and the four nops in `usb_tb_head`
that hold it there are on the path of **every** K from 1 to 7.

So before §E.1, subtracting X cycles from a term common to all K moved the
ceiling to τ+115−X *and* the floor to τ+62−X, and the floor had to be bought
back with X nops in the same shared place. Net gain: zero. This is why the
`TB_OWED` register trick, the `SEG0` relocation and the constant-PID work below
are all costed at zero in their own right and only become worth their face value
once §E.1 is in.

---

## A. Every computed value against every consumer

One row per quantity the engine maintains. "Readers" is exhaustive — it is the
result of grepping every use of the register in `engine16_merged.S` and
classifying it by path. A row whose readers are empty on some path is find-1
again; the last column prices it.

| # | quantity | written by | read by | path with no reader | cycles | on a deadline? |
|---|---|---|---|---|---|---|
| A1 | **r10** CRC-16 residue | `SEG4` (table step, parked in r8), `SEG6` (commit) | `TBGATE_D` (P-TB), `.Ldata` residue test (P-RX/DATA) | **P-TI** (token: `.Ltoken` recomputes CRC5 and never reads r10); **P-RX/token**; **P-RX/handshake** | 17 per wire byte in the flush (`SEG4` 7 + `SEG6` 10) | P-TI: **already taken** (`design_b_in.md` §2 — find 1). P-RX: no |
| A2 | **r10**, on a handshake specifically | as above | nothing | `SEG5`'s `count ≥ 3` gate is never satisfied (a handshake emits 2 bytes), so every `SEG4` lookup and every `SEG6` commit is provably a no-op on this packet, not merely unread | 17 in the flush, 20 per wire byte in the cells | no — P-RX |
| A3 | **r11** unstuff state / next table index | `SEG0`, `SEG1`, `SEG2_A` (each takes the entry's low halfword) | `SEG0`/`SEG1`/`SEG2_A` (index base), `TBGATE_A`, `TIG1`, `.Lrx_tail` | none | — | — |
| A4 | **r12** emitted byte count | `.Lprime` (0), `SEG5_B` | `SEG3_A` (store index), `SEG5_B` (bound + CRC gate), `TBGATE_B`, `TIG2`, `.Lrx_tail` | none | — | — |
| A5 | **r0** bit count | `.Lprime`, `SEG1`, `SEG2_B`, `SEG3_A` (speculative `−8`), `SEG3_TAIL` (restore) | `SEG1`/`SEG2_B` (`lsls r1,r1,r0`), `SEG3_A`, `SEG3_B` | the **last** `SEG3_TAIL` of a packet: r0 is overwritten by `mov r0,r12` in `TBGATE_B` / `TIG2` / `.Lrx_tail` before any read | 3, once per packet | in a timed cell (B2/C2) — no |
| A6 | **r3** accumulator + sentinel | `.Lprime`, `SEG1`, `SEG2_B` (biased add), `SEG5_B` (drop) | `SEG3_A` (`strb`), `SEG4` (CRC/parity input), `SEG5_B` | the **last** `SEG5_B`'s `lsrs r3,r3,r2`: r3 is overwritten by `TBGATE_B`'s `ldrh r3` | 1, once per packet | timed cell — no |
| A7 | **r1** table entry → commit mask | `SEG0`/`SEG1`/`SEG2` (entry), `SEG3_B` (mask), `SEG5_B` (gated mask) | `SEG1`/`SEG2` (fields), `SEG3_TAIL`, `SEG4` (parity), `SEG5`, `SEG6_A` | none | — | — |
| A8 | **r8** park | `SEGA`/head (low nibble×4), `SEG4` (CRC table value) | `SEG1` (nibble), `SEG6_A` (CRC value) | **P-TI**: `SEG4`'s park is gone, so r8 is dead between `SEG1` and the next `SEGA` | 0 (already removed) | — |
| A9 | **r5** wire packer | every `CELL` (`adcs r5,r5`) | `SEGA`, `.Ltb_head`/`.Lti_head`/`rx_flush7` NRZI | none — and it is the *last* reader that frees r5 to become the driven BSRR word | — | — |
| A10 | **r6** D+/D− sample mask | entry (`movs r6,#USB_PINMASK`) | every `CELL`'s `ands r2,r6`; `TBARM` overwrites it with `BSRR_TOGGLE` | **dead from τ until `TBARM`**, i.e. across the whole of piece 0 | — | it is not a discard, it is an **unused resource**: the only free low register during the flush. See §E.6 |
| A11 | **r9** rxbuf base | entry | `SEG2_B`, `EOPSTUB`, `TBGATE_B`, `TIG3`, `TBPIDLOAD`, `.Lrx_tail`, `.Lin_refresh` | none | — | — |
| A12 | **r14** chain head, then 8−K | `.Lprime`, `EOPSTUB` | `bx r14` (cell 7), head NRZI, `rx_flush7`'s `cmp #8` | none. `rx_flush7`'s `cmp r2,#8 / beq` is *not* redundant on P-RX and *is* absent from P-TB/P-TI, where K=0 has its own entry — correctly | — | — |
| A13 | **rxbuf[i]** speculative byte store | `SEG3_A`, every wire byte | `TBGATE_B` (0,1), `TIG3`/`TIG5` (0..3), `.Lrx_tail` (0..3), and bytes 2..count−1 handed to C | the two CRC-16 bytes of a DATA packet are stored and never read as data — but the store is speculative *per wire byte* and unconditional, so there is nothing to skip (`CRC_ROUND2.md` §3.1) | — | — |
| A14 | store **index mask** `lsls/lsrs #USB_RXBUF_MASK` | `SEG3_A` | the `strb`'s register offset | `SEG5_B` already enforces `count < 24` with `cmp #USB_BYTE_LIMIT / bhs .Lovf`, and `.Lprime` starts it at 0, so `r12 ∈ [0,23] ⊂ [0,31]` at every `SEG3` **by control flow** | 2 per wire byte | **yes** — but see §E.5: removing it downgrades DEFECTS_VERIFIED D-2 from structural to argued |
| A15 | `SEG3_A`'s `subs r0,r0,#8` + `SEG3_TAIL`'s restore | `SEG3_A`, `SEG4` head | `SEG3_B` (sign of r0−8) | the subtract-and-restore pair exists only to expose one bit — `r0 ≥ 8` — which is **bit 3 of r0**, because r0 ≤ 15 by induction | 4 of the 6 are avoidable | **yes** — implemented, §F.2 |
| A16 | `SEG6_A`'s `movs r2,#8 / ands r2,r1` | `SEG6_A` | `SEG6_B`'s `lsrs r1,r1,r2` | `SEG5_A` computed the identical value two instructions earlier; in the flush the two are adjacent | 2 per wire byte | yes, but not removable while `SEG5`/`SEG6` share one source with the timed chain — see §E.7 |
| A17 | `.Ltb_head` / `.Lti_head` first `uxtb r1,r1` | head NRZI | nothing: the following `lsls r1,r1,r2` (r2 = 8−K ≤ 7) moves bits 8+ further up and the *second* `uxtb` clears them | — | 1 per packet | **yes** — implemented, §F.3 |
| A18 | **P-KA** keepalive | — | — | computes nothing but the EXTI acknowledge and the HSI stamp it is required to compute (it *is* the calibration reference) | 0 | no |

### A's verdict

Fourteen of the eighteen rows find nothing. The four that find something are
A14, A15, A16 and A17, and only A15 and A17 are both on a deadline and safe;
they are §F.2 and §F.3. A1 is find 1 and is already banked. **The
CRC-is-computed-and-discarded pattern is exhausted**: there is no path that both
drives the bus and maintains a residue nobody reads.

---

## B. Every fixed pattern on the wire, both directions

"Fixed" means: known before the bit exists, either because the protocol fixes it
(SYNC, EOP) or because this device only ever emits one value (the ACK PID), or
because half of it is the complement of the other half (any PID).

| # | pattern | direction | information it carries | what the engine spends | exploited? |
|---|---|---|---|---|---|
| B1 | **SYNC**, 8 bit times, `KJKJKJKK` | TX | none — every packet begins with it | 2 cycles/cell (`eors r5,r6 / str`), no bit queue, no stuff state | **yes** — this is find 2. It is what makes 24 MHz conformant: S = 14 free cycles/cell, 112 over the field, against the 8 the §8.3 feasibility condition assumed |
| B2 | **SYNC**, on receive | RX | none | **zero cells.** The phase lock is a bounded spin on the last J→K edge and `.Lprime` injects SYNC's decoded value (`0x80`, index 2) straight into the pipeline. No SYNC bit is ever decoded | **yes, completely.** The spend is 20 nops + 14 priming *after* the lock, and it sits ~96 cycles before the first PID bit — not on any deadline |
| B3 | **EOP**, SE0 ×2 then J | RX | "the packet ended", 1 bit, and *when* | `ands r2,r6 / beq` — 2 cycles inside a sample the cell had to take anyway (DESCENT). The second SE0 bit time and the return to J are never checked | **yes.** Not checking them is a deliberate non-spend, not an omission: the flush's own stuffing and length gates reject what a malformed EOP could produce |
| B4 | **EOP**, on transmit | TX | none | three `str` on the 16-cycle cadence and 43 nops | **yes** — nothing left |
| B5 | **PID low nibble = ~high nibble** | RX, in `TBGATE_C` | the accept set at this point is **exactly `{0xC3, 0x4B}`** — verified by enumeration over all 256 bytes against the three tests `TBGATE_C` runs | 12 cycles to *derive* that set generically: complement (6), type field (3), DATA2/MDATA alias (3) | **no.** Two `cmp`/branch pairs decide the same set in 4 cycles. §E.3 |
| B6 | same | RX, in `TIG4` | accept set is `{0x69}` | 2 cycles: `cmp r3,#TI_PID_IN / bne` | **yes** |
| B7 | same | RX, in `.Lrx_tail` | genuine 3-way dispatch (token / data / handshake) is needed | 12 cycles | **yes** — and it is on P-RX, so §0.1 applies |
| B8 | **the response PID on P-TB** | TX | **none.** `TB_PID_OFS` is written in exactly one place, `.Lusb_done_owed`, with the constant `TB_PID_ACK = 0xD2`; the only other writer stores 0 on a path that never transmits | `TBPIDLOAD` 5 cycles (a RAM byte from flash-resident code) + 8 × `TBBIT` at 5 cycles, where a constant pattern costs 2 | **no.** 29 cycles of cell budget, 0 of deadline. §E.4 |
| B9 | **PID bits 0..1 of any handshake** | TX | none: the type field is `10`, so LSB-first the first two bits are `0,1` for ACK, NAK and STALL alike. Emitting them commits to "a handshake", and the only alternatives this path has are ACK and a deliberately corrupt packet | currently they sit *downstream* of `TBGATE_D` | **no.** Extending find 2's argument by two cells would move the residue deadline two bit times later and add 28 cycles to the pre-verdict budget. §E.8 uses this |
| B10 | **the response PID on P-TI** | TX | one bit (`e->toggle_in`: `0xC3` vs `0x4B`, which differ in bits 3 and 7) | `TIPIDLOAD` 4 + 8 × `TIBIT` at 5 | **partly.** The eight cells have 11 free cycles each and the payload chain setup uses them, so there is nothing to reclaim |
| B11 | **the IN payload** | TX | all of it | 5 cycles/cell — it was rendered to stuffed, CRC'd, LSB-first wire bits by `usb_in_render` on an *earlier* transaction | **yes** — this is find 2's shape applied to content that does carry information: the work is not cheaper, it is off the wire |
| B12 | **the received PID, read twice** | RX | — | `EOPSTUB`'s `ldrb r2,[r2,#1]` (4) compares it to `0x69`; `TIG4` re-reads and re-compares it (3 of its 6) | **no**, deliberately — but the 3 cycles are in timed cell C5, so §0.2 prices them at zero |

### B's verdict

B5, B8 and B9 are real. None of them is worth anything **on its own**, because
all three land in cells that are already padded to 16 — they buy *budget*, not
*deadline*. Their value is entirely as enablers for §E.4, which converts budget
into deadline by moving `SEG0` out of piece 0. They are ranked there.

---

## C. Work on the deadline that could be off it

Design B moved the flush into the SYNC cells. What is left between EOP and the
first wire edge, and does it have to be there?

| item | cycles | depends on the last received bit? | can it move? |
|---|---|---|---|
| `detect` (`ldr / ands / beq`) | 4..5 | it *is* the last bit | no |
| `EOPSTUB`: `movs / mov r14` | 2 | no | no — r14 must be set before the head reads it, and there is nowhere cheaper |
| `EOPSTUB`: the `TB_OWED` test | 6 | no — the flag was written by the *previous* packet's tail | **it need not be a RAM read.** §E.6 |
| `EOPSTUB`: the IN test (P-TI only) | 9 | yes — it reads `rxbuf[1]`, the PID of the packet in flight | no |
| `usb_tb_head` 4-nop floor pad | 4 | no | **yes — it belongs to K=6 alone.** §E.1 |
| head NRZI | 11 | yes (r5's last sample) | only by parking its two outputs in memory, which costs more than it saves — see §E.8 |
| `SEG0` of the partial byte | 7 | yes, but nothing after the first edge needs its result before cell B0 | **yes, into the SYNC cells** — if 7 cycles of cell budget can be freed. §E.4 |
| `TBARM` | 13 | no | it could be hoisted above the whole flush using r6 as its scratch (A10), leaving only `ldr r5,=BSRR_J` after the head — but the first edge is `TBCELL B0`, which follows everything regardless, so **the total does not change.** Worth 0 |
| `B(K)` = `SEG_K..SEG6` of the byte in flight | 0..61 | yes | not without breaking the register chain: `SEG1` reads r8, the head *writes* r8; `SEG0` reads and writes r11, `SEG4`/`SEG6` read and write r8. The order `SEG_K..SEG6 → head → SEG0 → SEG1..` is forced by register reuse, and there is no free register to break it (A10's r6 is one register, the break needs two) |

**The mirror question — what sits in a timed cell that could move to the
untimed tail — is vacuous on P-TB and P-TI.** Every timed cell is padded to
exactly 16 whether or not it carries work; the free cycles in the SYNC, PID and
payload cells are already off the deadline by construction. The only thing a
timed cell costs the deadline is the *count* of cells before the verdict, and
that is fixed by the protocol at eight (SYNC) — or ten, if B9 is taken.

---

## D. The pipeline depth: is one byte the minimum?

The receive pipeline runs one wire byte behind, and §0.2 shows why that is the
largest single item in the turnaround: `B(1) = 61` is the entire per-byte
pipeline minus its first and last segments.

**Supply.** Eight cells × 11 free cycles after sampling = 88 per wire byte.
**Demand today**, flash-resident, CRC-16:

```
  SEG0 7  SEG1 11  SEG2 9  SEG3 10  SEG4 10  SEG5 11  SEG6 10  SEGA 8   = 76
  + the back edge  bx r14                                          3    = 79
  free                                                                    9
```

### D.1 Nibble-deep — infeasible by 33 cycles

The byte-completion machinery is `SEG3` (store), `SEG4` (CRC step), `SEG5`
(drop, advance, bound, gate) and `SEG6` (CRC commit) = **41 cycles**, and it
runs once per *emitted byte boundary test*. With nibble-granular appends r0 rises
by 0..4 per nibble, so a byte can complete at either nibble and the test must run
**twice per wire byte**: 82 instead of 41. The lookup-and-append work is already
nibble-granular and does not change (27). NRZI splits into two halves that each
need their own shift/xor/invert/mask, ≈ 6 each against 8 for the whole: 12.

```
  27 + 82 + 12 = 121   against 88 free   ->  short by 33
```

**A nibble-deep pipeline does not fit a 16-cycle cell.** The 16-cycle cell is
bought by the table lookup, and the table lookup is not what costs the flush;
the byte-completion machinery is, and halving the depth doubles it.

### D.2 Half-byte, *asymmetric* — feasible, and worth ~20 cycles

The one restructuring that does not double the emit machinery: keep it
byte-granular, and move only the **high nibble's** decode and append forward.
The high nibble of the byte being sampled is complete after cell 3 (its NRZI
predecessors are bits 0..2 plus the last bit of the previous byte, all sampled),
so `SEGA_hi + SEG0 + SEG1` can run in cells 4..7 of the *same* wire byte, while
cells 0..3 finish the previous byte's low nibble and emit.

```
  cost   SEGA split in two, 6 + 6 against 8         +4 / wire byte  -> 80 of 88
  gain   at EOP in cell 1 the pending work is the previous byte's
         low-nibble append and emit, not the whole pipeline:
         B'(1) ~ SEG2..SEG6 + the head's low half  ~ 41 + a few     vs  61
```

**≈ 20 cycles of ceiling margin, which is more than everything in §E combined.**
It is also the largest structural risk in the document: the flush needs two
entry chains instead of one (a K-indexed entry into the low half and a K-indexed
entry into the high half), K=0..7 doubles, the head NRZI splits, and the
`.error` ledger cannot see a fall-through defect — which is exactly the class of
bug `turnaround.md` §11.1 records having shipped once already. **Not
implemented. Costed, and left.**

### D.3 One byte is not load-bearing, but it is well chosen

The depth is not forced by the table (the table is nibble-indexed) nor by NRZI
(which is byte-parallel and *cheaper* the wider it is — D.1's +4). It is forced
by the emit machinery wanting to run once per byte. D.2 is the only cut that
respects that and still shortens the flush.

---

## E. The findings, ranked by cycles of ceiling margin

"Cycles" is cycles off the **worst-case first wire edge** — τ+115 on P-TB,
τ+109 on P-TI — because that is the only number either response path has a
deadline against (§0.1, §0.2). A find that saves cycles anywhere else is
listed at zero and says so.

| rank | find | cycles | status |
|---|---|---|---|
| E.1 | the floor pad is charged to every K | **4** | implemented, §F.1 |
| E.2 | the commit mask is bit 3 of r0 | **2** | implemented, §F.2 |
| E.3 | the head NRZI's pre-shift `uxtb` has no reader | **1** | implemented, §F.3 |
| E.4 | `SEG0` could ride in the SYNC cells | **7** | implemented **on P-TI**, §F.4; P-TB left, see below |
| E.5 | `TB_OWED` need not be a RAM byte | 4 | conditional on a fact I could not verify |
| E.6 | the store index mask is redundant | 2 | rejected: it downgrades D-2 |
| E.7 | `SEG6` recomputes `8 & r1` | 2 | blocked by the shared source |
| E.8 | `TBGATE_C` decodes a two-element set generically | 0 (4 of cell budget) | the enabler P-TB's E.4 needs |
| E.9 | the ACK PID is a compile-time constant | 0 (29 of cell budget) | not needed after all; see E.4 |
| D.2 | half-byte asymmetric pipeline | ~20 | structural, costed and left |
| E.10 | byte-wide unstuff table | ~13 | costs 8 KB of flash |
| E.11 | constant first edge at τ+60 | would make the margin 64 | **infeasible by 1 cycle** |

### E.1 The floor pad is charged to every K — 4 cycles

`turnaround.md` §11.3 put four nops in `usb_tb_head` (and four in
`usb_ti_head`) so that the *earliest* entry, K=6, does not start driving
inside the host's own EOP. But `usb_tb_head` is on the path of every K from 1
to 7, so K=1 — the entry that sets the **ceiling** — paid them too. The two
limits are held by different entries and the pad was a shared instrument.

Moved into `EOPSTUB 6`, which is K=6's own block and costs nothing to enlarge
(the eight stubs are already eight separate blocks), plus K=7's share into
`tb_flush7`. Measured: K=6 and K=7 land exactly where they did, K=1..5 come
down by 4.

**This is the finding that makes the other ten worth anything.** Before it,
any saving common to all K had to be handed straight back as floor padding in
the same shared place (§0.3). After it, the floor is bought per entry and
every uniform cycle is a ceiling cycle.

### E.2 The commit mask is bit 3 of r0 — 2 cycles

`SEG3_A` subtracted 8 from r0, `SEG3_B` took the sign of the result as "no
byte finished", and `SEG3_TAIL` added the 8 back when there was none: six
cycles to expose the predicate `r0 ≥ 8`. r0 ≤ 15 (§F.2 gives the induction),
so that predicate **is bit 3 of r0**:

```
  lsls r2, r0, #28        bit 3 -> the sign bit
  asrs r1, r2, #31        commit mask, -1 or 0        (SEG3_B, still 2)
  ...
  lsls r0, r0, #29        r0 &= 7, which IS r0 - 8 when a byte finished
  lsrs r0, r0, #29        and r0 when it did not      (SEG3_TAIL, 3 -> 2)
```

and `SEG3_A`'s `subs` goes away entirely. Two cycles per wire byte, and both
are inside `SEG1..SEG4`, so they come off `B(K)` for K ≤ 4 — off the ceiling —
and off `B(5)` and `B(6)` not at all — not off the floor. That asymmetry is
worth as much as the two cycles.

### E.3 The head NRZI's pre-shift `uxtb` has no reader — 1 cycle

`.Ltb_head`, `.Lti_head` and `rx_flush7` masked the NRZI result to eight bits,
then left-shifted it by 8−K, then masked to eight bits again. The shift is
**left** and 8−K is 1..7 (K=0 has its own entry on both response paths and
`rx_flush7` tests for it), so everything the first mask removed the shift
would have pushed above bit 7 anyway, where the second mask removes it.

### E.4 `SEG0` rides in the SYNC cells — 7 cycles

Nothing between the first wire edge and the first SYNC cell needs `SEG0`'s
result — `TBARM` writes r2, r5 and r6 only, and `SEG0` needs r1, r4 and r11 —
so its 7 cycles can move out of piece 0 into the SYNC field, at every K. The
question is whether the cells have room, and the two response paths answer it
differently.

**P-TI: yes, with room to spare — implemented (§F.4).** The IN chain's SYNC
cells were carrying **88 of their 112** free cycles, because a token needs no
CRC-16: `SEG4` is `SEG3_TAIL` alone and `SEG6` is gone (find 1). Adding
`SEG0` makes it 95. The re-cut is four cells deep and touches no gate:

```
  C0  SEG0 7 + SEG1_A 6                = 13     (SEG1 split after `adds r0,r0,r2`,
  C1  SEG1_B 5 + SEG2_A 3 + SEG2_B 6   = 14      where only r1 and r2 cross and
  C2  SEG3_A 7 + SEG3_B 2 + TAIL 2     = 11      TICELL writes r5 and the flags)
  C3  SEG5_A 2 + SEG5_B 9              = 11
```

and 5, 14, 9, 11 in the RAM-resident column. C4..C7 are untouched.

**P-TB: yes, but only with E.8 — not implemented.** The budget is exact:

```
  capacity, eight SYNC cells x 14                                 112
  SEG0 7 + SEG1..SEG6 59 + TBGATE A 4 B 14 C 8 D 6
         + PIDLOAD 5 + `movs r0,#1` 1 + exit 5                    109
  spare                                                             3
```

with `TBGATE_C` at 8 (E.8) rather than 12; at 12 it is 113 and does not fit.
Two placement facts make even 109 reachable, and both took finding:
`TBPIDLOAD` may sit *after* the gate — it clobbers r4, the table base, whose
last reader is `SEG4`'s CRC lookup — and `TBGATE_A` (the sticky stuffing test)
depends on nothing after `SEG2`, so it can fill whatever pair of cycles is
left. The gate's tail is what is rigid: `TBGATE_B` clobbers r0 and r1 so it
must follow `SEG6`, `TBGATE_C` consumes the halfword `TBGATE_B` loads, and
`TBGATE_D` and the exit follow that.

Packing 109 into 112 across eight cells, in **both** cost models, at 1-to-4
cycle granularity, is a re-cut of the whole interleave — the exact operation
`turnaround.md` §11.1 records having shipped a fall-through defect once, and
one the assembler's ledger cannot see. Costed, and left. It is worth 7 cycles
on the path that has the least margin, so it is the first thing to do next.

### E.5 `TB_OWED` need not be a RAM byte — 4 cycles, conditional

The `EOPSTUB`'s `mov r2,r9 / ldrb r2,[r2,#TB_OWED_OFS] / cmp / beq` is 6
cycles from flash-resident code and it is on the deadline for both response
paths. The flag is one bit, written by the *previous* packet's tail, and there
is a register that survives the whole packet and is dead from τ onwards: r6,
the D+/D− sample mask (A10).

`r6 = USB_PINMASK | (owed << 31)` leaves `ands r2,r6` unchanged as an SE0 test
**iff IDR bit 31 always reads 0**, and then the stub's test is `movs r2,r6 /
bpl` — 2 cycles instead of 6. **I could not verify from the reference manual
in this tree that GPIOx_IDR's upper half reads as zero on PY32F002B/F003**, and
the whole finding rests on it: if bit 31 can ever be 1, the SE0 test breaks and
the engine stops seeing EOP. Recorded, not implemented, and the datasheet
citation is the work that remains.

### E.6 The store index mask is redundant — 2 cycles, rejected

`SEG3_A`'s `lsls/lsrs #USB_RXBUF_MASK` masks the emitted count to 0..31 before
using it as a store offset. `SEG5_B` already runs `cmp r2,#USB_BYTE_LIMIT /
bhs .Lovf` on the same counter every wire byte and `.Lprime` starts it at 0, so
`r12 ∈ [0,23]` at every `SEG3` — the mask can never change the value.

**Not implemented.** DEFECTS_VERIFIED D-2's claim is that *no instruction in
the object can address `rxbuf` out of range*, which is a property of the
instruction stream. Deleting the mask makes it a property of the control flow
instead, and 2 cycles is not the price of that trade.

### E.7 `SEG6` recomputes what `SEG5` just computed — 2 cycles, blocked

`SEG6_A`'s `movs r2,#8 / ands r2,r1` is character for character `SEG5_A`, and
in the flush the two are adjacent. It cannot be carried in r2 because
`SEG6_A` must gate the parked table value *first* (that needs r1, which
`SEG6_B` then overwrites with the CRC) and every other low register is live —
except r6, which is free during the flush and **not** free in the timed chain,
where it is the sample mask. So the two cycles are removable only by giving
the flush its own copy of `SEG5`/`SEG6`, which is the drift the shared macros
exist to prevent.

### E.8 `TBGATE_C` decodes a two-element set generically — 4 cycles of budget

Twelve cycles derive the accept set the long way round: nibble complement (6),
type field (3), DATA2/MDATA alias (3). Enumeration over all 256 bytes shows
the set is exactly `{0xC3, 0x4B}`.

The obvious replacement — `cmp #0xC3 / beq / cmp #0x4B / bne` — is **wrong for
this engine**, and the reason is worth recording: on the DATA0 arm the `beq`
is *taken*, and a taken conditional branch costs 2 or 3 depending on
alignment. That is a 4-or-5-cycle cell in the middle of the SYNC cadence, and
"no taken conditional branch on any timed path" is a property this engine
holds deliberately (`engine16_merged.md` §13.3). The branch-free form is:

```
  movs r2, #0x77 / ands r2, r3 / cmp r2, #0x43 / bne .Ltb_abort     4
  lsrs r2, r3, #4 / eors r2, r3 / lsls r2, r2, #28 / bpl .Ltb_abort 4
```

The first pair fixes bits 6..4 and 2..0 and leaves bits 7 and 3 free, i.e.
`{0x43, 0x4B, 0xC3, 0xCB}`; the second requires bit 7 ≠ bit 3, which removes
0x43 and 0xCB. Verified by enumeration to accept `{0x4B, 0xC3}` and nothing
else. Eight cycles, both branches not taken on the good path — **exactly
equivalent, 4 cycles cheaper**, and it is what makes P-TB's E.4 fit.

### E.9 The ACK PID is a compile-time constant — 29 cycles of budget

`TB_PID_OFS` is written in one place with one constant, so the eight `TBBIT`
cells (5 cycles each) and `TBPIDLOAD` (5) could be eight constant toggle/hold
cells at 2, like SYNC's. It was costed here as E.4's enabler; it is not needed
for that any more, because `TBPIDLOAD` can simply move past the gate (E.4).
Zero cycles of deadline in its own right, so it stays unimplemented — but it
is what R5's "the response PID is a parameter" costs, and the parameter has
one value.

### E.10 A byte-wide unstuff table — ~13 cycles, 8 KB

The table is indexed by (state, nibble): 8 × 16 words. Indexed by
(state, byte) it is 8 × 256 words = **8 KB**, and one lookup replaces
`SEG0 + SEG1 + SEG2` (27 cycles) with an index, a load and one append (~14).
The entry still fits a word: m needs 9 bits (`m = kept + 2^n − 1`, n ≤ 8),
n needs 4, and the next index needs 14. `r0 ≤ 15` still holds and at most one
byte still completes per wire byte, so nothing downstream changes.

13 cycles is the largest single saving in this document that is not a
restructure. Against 7896 B of flash used of 16 KB in the gamepad build, 8 KB
is not affordable there and would be tight even on a 32 KB part. Costed and
left.

### E.11 A constant first edge at τ+60 — infeasible by one cycle

The tempting endgame: break the ordering that forces `B(K)` in front of the
first edge, by parking the head NRZI's two outputs (r1 and r8) in memory
instead of registers. Then piece 0 is `detect + stub + NRZI + str + TBARM +
TBCELL` — **independent of K** — and can be padded to a constant τ+60, with
the whole flush and gate riding in the cells. The ceiling margin would be the
full 64 cycles, at every K.

It does not close, by a wide margin, and taking E.8 and E.9 as given:

```
  budget after the first edge
    8 SYNC cells x 14                                       112
  + PID bits 0..1, which carry no information (B9)           28
  + the pad from the constant piece 0 (tau+46) to tau+60     14
                                                            ---
                                                            154

  demand, worst case K=1
    B(1)                                                     59
    reload the parked byte and re-split its nibbles           8
    SEG0 7 + SEG1..SEG6 59                                   66
    gate  A 4 + B 14 + C 8 + D 6                             32
    `movs r0,#1` 1 + exit 5                                   6
                                                            ---
                                                            171
```

**171 against 154, short by 17** — and that is before asking whether the
pieces cut at 14-cycle boundaries, which they would not: `TBGATE_B` is a
14-cycle block and the gate's tail is order-constrained (E.4). The parking
itself costs 12 of the 17 (a `str` in piece 0 and a `ldr` plus a re-split
after), so it pays for its own freedom and then some. Recorded so that nobody
costs it again.

---

## F. What was implemented

Four changes, all in `engine16_merged.S`. `engine16_tx.S` is untouched.

### F.1 The floor pad, per entry (E.1)

`EOPSTUB` grew a third parameter and `EOPSTUB 6` carries 5 nops; `tb_flush7`
went from 10 to 13; the four nops at the head of `.Ltb_head` and `.Lti_head`
are gone. K=6 and K=7 keep the first-edge times they had; nothing else pays
for them.

### F.2 The commit mask is bit 3 of r0 (E.2)

```
  SEG3_A   -  subs r0, r0, #8
  SEG3_B      asrs r1,r0,#31 / mvns r1,r1   ->  lsls r2,r0,#28 / asrs r1,r2,#31
  SEG3_TAIL   movs/bics/adds  (3)           ->  lsls r0,#29 / lsrs r0,#29  (2)
```

The invariant the change rests on is `r0 ≤ 15`, and the part of it worth
stating is the flush: entering `rx_flush5` means SE0 was sampled in cell 5, so
cells 0..4 **already ran** `SEG0..SEG4` for the byte in flight. Every wire byte
gets a complete `SEG0..SEG6` — part in the timed cells, part in the flush — so
no byte reaches `SEG3` having skipped a `SEG3_TAIL`, and r0 enters every wire
byte at ≤ 7 and leaves `SEG2` at ≤ 15.

`SEG3_CYC`, `SEG3A_CYC` and the three `SEG4_CYC` all move with it, so the
assembled ledger and the interleaved Design B chains re-pack themselves.

### F.3 The head's dead `uxtb` (E.3)

Removed from `.Ltb_head`, `.Lti_head` and `rx_flush7`. `SEGA`'s `uxtb` stays —
there is no shift after it and `lsrs r1,r1,#4` would otherwise pull garbage
into the high nibble.

### F.4 `SEG0` out of piece 0, on the IN path (E.4)

`.Lti_head` ends at `TBARM` now; `SEG0` is the first thing in cell C0, and
`SEG1` is split at `adds r0,r0,r2` (the one point where only r1 and r2 cross,
and `TICELL` writes r5 and the flags and nothing else) so that C0 can hold
`SEG0` plus its first half. C1..C3 re-cut to match. The gate cells C4..C7 are
untouched, and the K=0 entry never runs C0..C3 so it is unaffected.

`EOPSTUB 6`'s pad grows from 5 to 12 to hold the floor against the same 7
cycles. That pad is shared with P-TB, whose K=6 therefore moves from τ+64 to
τ+71 — harmless, because on P-TB the floor is now held by K=7 at τ+62.

### F.5 Verification

`tools/engine16_rx_model.py` is new and is the instrument for F.2 and F.3: it
transliterates the receive chain instruction for instruction, reads `T_UT` and
`T_CRC16` out of the **assembled object**, and runs the old and new forms of
both changes **in lockstep**, comparing every architectural register, the
buffer and the emitted count at every segment boundary.

```
415 packets, 0 failures  (old and new SEG3/SEG4 and the two head NRZI forms in lockstep)
EOP cell K exercised: [0, 1, 2, 3, 4, 5, 6, 7]
SEG3/SEG3_TAIL identical for every r0 in 0..15 (the invariant)
```

Uniform random payloads only ever reach a few values of K — for a whole-byte
data field K is the *stuffed-bit count* mod 8 — so the case list is topped up
with 1-heavy payloads until all eight are covered. K selects both the flush
entry and the head's shift, so all eight matter.

`tools/design_b_in_model.py`: 7322 cases, 0 mismatches, unchanged.

Every timed cell is exactly 16 in **all six** combinations of `USB_RX_CHECK`
(0/1/2) and `USB_ENGINE16_FLASH` (0/1), measured on the assembled object with
the matching cost model — identical output to the baseline, including the four
block-boundary artifacts the tool reports for `usb_tb_B7`, `usb_tb_P7`,
`usb_ti_C7` and `usb_ti_T7`.

Integration build, `demo_gamepad`, `MCU_TYPE=PY32F003x4`:

| | RAM | flash |
|---|---|---|
| before | 416 B | 7928 B |
| after | 416 B | **7896 B** |
| before, `PY32_HSICAL_ENABLE=0` | 416 B | 7664 B |
| after, `PY32_HSICAL_ENABLE=0` | 416 B | **7632 B** |

### F.6 The turnaround, before and after

Measured on the linked image with `tools/engine16_cyc.py --exec flash --ioport
r7 --flashdata r4`, block by block, and the control flow traced by hand in
`objdump` because the tool does not resolve it. Counting convention: worst =
detect 5 and every taken branch at 3; best = detect 4 and every taken branch
at 2; not-taken conditionals at 1.

**P-TB, DATA → ACK.** Deadline τ+124, floor τ+60.

| K | before (worst) | after (worst) | after (best) |
|---|---|---|---|
| **1** | **τ+115** | **τ+108** | τ+106 |
| 2 | τ+104 | τ+97 | τ+95 |
| 3 | τ+95 | τ+88 | τ+86 |
| 4 | τ+85 | τ+79 | τ+77 |
| 5 | τ+75 | τ+70 | τ+68 |
| 6 | τ+64 | τ+71 | τ+69 |
| 7 | τ+67 | τ+65 | **τ+62** |
| 0 | τ+103 | τ+101 | τ+98 |

Ceiling margin **9 → 16**. Floor margin 2, unchanged: K=6 moved *later*
because it shares `EOPSTUB 6`'s pad with the IN path (F.4), so the floor on
this path is now held by K=7 at τ+62 — the same 2 cycles K=6 held before.

**P-TI, IN token → DATA.** Same limits.

| K | before (worst) | after (worst) | after (best) |
|---|---|---|---|
| **1** | **τ+109** | **τ+95** | τ+93 |
| 2 | τ+98 | τ+84 | τ+82 |
| 3 | τ+89 | τ+75 | τ+73 |
| 4 | τ+79 | τ+66 | τ+64 |
| 5 | τ+76 | τ+64 | **τ+62** |
| 6 | τ+65 | τ+65 | τ+63 |
| 7 | τ+78 | τ+66 | τ+63 |
| 0 | τ+97 | τ+95 | τ+92 |

Ceiling margin **15 → 29**. Floor margin 2, held by K=5 now; it was 3, held by
K=6.

The block-by-block sums the tables are built from, flash-resident,
`USB_RX_CHECK=CRC16`:

```
                       before        after
  tb_flush1..6      11 9 10 10 11 10   11 9 9 9 11 10    B(1): 61 -> 59
  usb_tb_head             35               30
  rx_eop6 (ACK arm)      11..12           23..24          (the pad moved in)
  rx_eop6 (IN arm)          23               35
  tb_flush7              12..13           15..16
  ti_flush1..5      11 9 10 3 11       11 9 9 2 11     B_in(1): 44 -> 42
  ti_flush6 (= the head)  35               23
```

All eight timed SYNC/PID/payload cells in both chains are exactly 16 in all
six combinations of `USB_RX_CHECK` and `USB_ENGINE16_FLASH`, and the stub and
head figures above are read out of the linked `demo_gamepad.elf` and the
control flow traced by hand in `objdump`.

---

## G. What the audit did not find

Recorded because a negative result costs the next reader the same search.

* **No path both drives the bus and maintains a value nobody reads.** A1 was
  find 1 and is banked; A2 (the handshake's provably-idle CRC machinery) and
  the token's CRC on the ordinary flush are real discards on P-RX, and P-RX has
  no deadline.
* **The keepalive computes nothing it does not owe.** It is the HSI
  calibration's only reference; the EXTI acknowledge and the stamp are the
  whole of it.
* **SYNC on receive is already free.** No SYNC bit is decoded at all — the
  phase lock is a spin and `.Lprime` injects the answer. There is nothing left
  to take.
* **A wider byte store saves nothing** (`CRC_ROUND2.md` §3.1, re-checked): the
  store is speculative per *wire* byte because stuffing makes "did a byte
  finish" data-dependent, and a wider store is speculative at the same rate.
* **Folding SYNC and PID into the CRC to delete `SEG5`'s `count ≥ 3` gate does
  not work.** The residue over `message‖crc` is 0xB001 only for init 0xFFFF at
  the start of the covered field; starting from any other register value makes
  the residue a function of the *message length*, and pre-compensating the init
  is impossible because the PID (0xC3 or 0x4B) is not known at `.Lprime`.
* **Reordering the segments cannot help the ceiling.**
  `B(1) = total − SEG0 − SEGA` is invariant under re-cutting; only `B(2..5)`
  move. Every proposal that just shuffles work between cells is worth zero.
* **`TBARM` cannot be made cheaper or moved usefully.** Its three literals are
  2 cycles each and building the two MODER constants with `movs/lsls` is also
  2; hoisting it above the flush (using r6 as scratch, A10) changes no total,
  because the first edge is `TBCELL B0` and that follows everything regardless.
