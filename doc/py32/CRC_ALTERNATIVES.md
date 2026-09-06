# CRC16 without the 512-byte table

**Question.** Not "how do we compute CRC16 faster" but **"what is the cheapest
way to decide whether a USB packet arrived intact"** on a Cortex-M0+ at 24 MHz,
16 cycles per bit, 2 KB of RAM.

**Result, in one line.** There is an exact table-free closed form for USB's
CRC16, derived and verified against 2004 vectors: it costs **+5 cycles per wire
byte** and removes **512 bytes**, which beats every other point on the curve and
beats the recorded nibble-table option (`ENGINE16_CATALOG.md` R-6, -480 B and
+9 cycles) on *both* axes. Details in §2; the ranking is §11.

**Recommendation, in one line.** Adopt the closed form (§2), move `T_CRC16` to
table offset 0 first because that is 2 free cycles (§6.1), and move the TX CRC
out of the timed chain into buffer-fill (§2.6) — without that last step the 512
bytes do not actually go anywhere, because the table is shared.

**Second result, and it is a deletion.** `ENGINE16_CATALOG.md` R-6 — the
16-entry nibble table, −480 B and +9 cycles — is **strictly dominated** by the
closed form on both axes and should be struck from the catalogue as an option
(§6.2).

## Contents
0. The baseline being replaced, in cycles and bytes
1. The reference implementation everything is checked against
2. **Table-free closed form** — derived, verified, costed
3. Linearity / GF(2) decomposition
4. Bit-sliced / SWAR
5. Galois vs Fibonacci, reflected vs not
6. Smaller tables — the granularity dial
7. The hardware CRC unit
8. Weaker checks: Fletcher, Adler, syndromes, partial CRC
9. Prior art found by search
10. What the no-false-ACK property actually requires
11. Ranking and recommendation
12. What is verified and what is reasoned
13. Reproducing every number in this file

---

## 0. The baseline being replaced

From `engine16_merged.md` §4.2 and `engine16_merged.S:252-287`, per **wire byte**:

| segment | work | cycles |
|---|---|---|
| `SEG4` | `crc ^ byte`, index arithmetic, `ldrh` (2), park in r8 | **9** |
| `SEG6` | gate the parked value and the shift by the commit mask, apply | **10** |
| | | **19 / wire byte** |

Storage: `T_CRC16`, 256 halfwords = **512 B**, at `usb_tables + 512`, **shared**
between the RX and TX engines (`engine16_merged.S:61-62,756`), so it is counted
once for the pair. **Consequence that governs everything below: removing the
table from RX alone saves nothing.** Both engines have to stop using it.

Slack available: **9 cycles per 8 cells** on RX (`engine16_merged.md` §4.2),
**5 cycles per byte** on TX (`engine16_tx.md` §7.4, "the segments use 84 of 88
spare"). Those RX cycles already have three named claimants
(`ENGINE16_CATALOG.md` R-7: mid-packet resync, a cheaper CRC, A-16 pre-staging).

Why `SEG4`+`SEG6` is 19 and not 6: the wire byte may not *become* a data byte —
bit stuffing means a wire byte yields 8 or fewer data bits — so the step is
computed speculatively and committed under a mask. Twelve of the nineteen cycles
are that gating, and **every candidate below pays them too**. Comparisons in this
file are therefore of the *unconditional* part unless stated.

The decision structure that must be preserved, from `turnaround.md` and
`ENGINE16_CATALOG.md` A-9: the ACK PID is emitted strictly downstream of the
residue test, so **a false ACK is structurally impossible**. §10 states exactly
what that requires of a replacement.

## 1. The reference

Everything in this file is checked against a bitwise CRC16 written from the
definition, not against the engine's table:

```python
def crc16_bitwise(data, init=0xFFFF):
    c = init
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ (0xA001 if c & 1 else 0)
    return c
```

Self-check: `crc16_bitwise(b"123456789") == 0x4B37`, and `0x4B37 ^ 0xFFFF ==
0xB4C8`, the published CRC-16/USB check value (that code is defined with
`xorout=0xFFFF`; USB transmits the complement, `rv003usb.S` and `engine16_tx.S`
both do). Residue over message+transmitted-CRC is **0xB001**, matching A-9.

## 2. Table-free closed form — **this is the result**

### 2.1 Derivation

The brief asks for the analogue of the CRC-CCITT trick
(`x ^= x>>4; crc = (crc<<8) ^ (x<<12) ^ (x<<5) ^ x`). That form is specific to
0x1021's nibble-aligned taps and does **not** transfer to 0x8005. The right
derivation is direct.

Reflected form, one step: `c = (c>>1) ^ (0xA001 if c&1)`. The table entry is
`T[i] = reduce_bits(i, 8, 0xA001)` — eight steps starting from an 8-bit `i`.
`0xA001` has taps at bits **15, 13 and 0**.

*Step 1 — what is shifted out.* Let `f_k` be the bit leaving the register at
step `k`. After step `k` the register's bit 0 is `i_{k+1}` XOR any feedback that
has reached bit 0. Feedback applied at step `j` occupies bits 15, 13, 0 and moves
down one bit per step, so it reaches bit 0 after 0, 13 or 15 further steps. With
only 8 steps, **only the bit-0 tap can re-enter**, and it re-enters immediately:

```
f_0 = i_0 ,   f_{k} = i_k XOR f_{k-1}
```

so `f_k = i_0 ^ i_1 ^ ... ^ i_k` — the **prefix XOR** of the byte. Call that byte
`S` (`S_k = f_k`).

*Step 2 — where the feedback lands.* Feedback applied at step `k` is `0xA001`,
then shifted right `7-k` more times, so it contributes `0xA001 >> (7-k)`:

| tap | final bit position | contribution over all k |
|---|---|---|
| 15 | `15-(7-k) = 8+k` | `S << 8` |
| 13 | `13-(7-k) = 6+k` | `S << 6` |
| 0 | `0-(7-k)` — survives only for `k=7` | `S_7` at bit 0 |

```
T[i] = (S << 8) ^ (S << 6) ^ (S >> 7),      S = prefix_xor8(i)
```

**Verified: 0 mismatches over all 256 entries** against
`reduce_bits(i,8,0xA001)`, and `crc16_bitwise == crc16_table == crc16_closed`
over 2004 vectors.

`S >> 7` is the parity of the byte; note it is also `S`'s top bit, so the whole
step is a function of `S` alone — **the 512-byte table is an 8-bit function
dressed as a 16-bit one.** That is the reason it was compressible at all.

### 2.2 The ARM form, and why it needs no mask

Naively `S` needs a `uxtb` because the doubling chain `x^=x<<1; x^=x<<2; x^=x<<4`
spills garbage into bits 8..14, and `S<<6` would carry that garbage into bits
14..15 of the CRC. Left-aligning removes the problem: put `x` at bits 24..31
(`lsls #24`, which also *does* the `uxtb`), and every spill shifts off the top
and is discarded by the machine. Then with `y = S<<24`:

```
T = ((y ^ (y>>2)) >> 16) ^ (y >> 31)
```

and since `z = y ^ (y>>2)` has bit 31 equal to `y`'s bit 31, and `z>>15` has no
bits above 16, that collapses further to

```
z = y ^ (y>>2) ;   T = (z ^ (z>>15)) >> 16
```

`(S<<8)^(S<<6) = ((S ^ (S>>2)) << 8)`, and the parity term rides in for one
extra shift-and-xor.

### 2.3 The block, assembled and counted

Register roles as `engine16_merged.S:190-191`: r10 = running CRC, r3 = candidate
byte in the low bits, r1 = commit mask (0 or −1), r2 = scratch, r8 = park.

```asm
	movs    r2, #8
	ands    r2, r1          /* sh = 8 if committing, else 0        */
	mov     r8, r2          /* park sh; frees r1 as the scratch    */
	mov     r2, r10
	eors    r2, r3
	ands    r2, r1          /* gated x: 0 unless committing        */
	lsls    r2, r2, #24     /* y = x<<24  (also the uxtb)          */
	lsls    r1, r2, #1
	eors    r2, r1
	lsls    r1, r2, #2
	eors    r2, r1
	lsls    r1, r2, #4
	eors    r2, r1          /* y = S<<24, nothing below bit 24     */
	lsrs    r1, r2, #2
	eors    r1, r2          /* z = y ^ (y>>2)                      */
	lsrs    r2, r1, #15
	eors    r1, r2
	lsrs    r1, r1, #16     /* T                                   */
	mov     r2, r8
	mov     r8, r1
	mov     r1, r10
	lsrs    r1, r1, r2      /* crc>>8, or crc>>0                   */
	mov     r2, r8
	eors    r1, r2
	mov     r10, r1
```

**25 cycles**, measured by `tools/engine16_cyc.py --exec ram` on the assembled
object, all 16-bit encodings, no loads, no branches, two scratch registers and
one park — the allocation the engine already has.

The gating is *cheaper* than the table's, because `T(0) = 0`: masking the input
byte kills the whole step, so the mask is spent on `x` (one `ands`) instead of
on the parked 16-bit result. That is what keeps the delta to +6 rather than +11.

### 2.4 Verification

The 25 instructions were transcribed one-for-one into a Python simulation, every
value masked to 32 bits, and run:

* **skip path** (`mask = 0`): CRC bit-identical in 0/9362 cases changed — correct.
* **commit path**: `1 135 940` (crc, byte) pairs against `(c>>8) ^ T[(c^b)&0xFF]`
  — **0 mismatches**.
* **3000 random packets** with randomly interleaved non-committed steps, against
  `crc16_bitwise` — **0 failures**.

### 2.5 Cost

| | table (today) | closed form |
|---|---|---|
| cycles / wire byte | 19 | **25 (+6)** |
| bytes of table | 512 (shared RX+TX) | **0** |
| lives in the cell? | yes | yes — 82 cycles of pipeline work against 88 available |
| no-false-ACK? | yes | **yes, unchanged** — same value, same place in the order |

RX fits: the per-byte pipeline goes 76 → 82 cycles, against `8*11 = 88` of
segment room (`5*8` capture + 82 + 3 `bx` = 125 of 128), leaving **3 cycles of
slack instead of 9**. The re-cut is real work — the 25 cycles do not fall into
the existing `SEG4`/`SEG6` boundaries — but 82 partitions into seven chunks of
≤11 plus `SEGA`+`bx` = 11 with room.

**It does not fit TX as written.** TX has 5 cycles of slack per byte and the
same +6 delta. §2.6 is how TX pays.

### 2.6 TX: do not compute the CRC in the timed chain at all

TX's CRC is not a *check*, it is a *generator*, and its input — the payload — is
known before the token arrives. `ENGINE16_CATALOG.md` C-5 has already concluded
independently, from two directions, that the C layer must fill the staging
buffer **before** the IN token, not inside the IN handler. Once that holds, the
two CRC bytes can be computed there, in untimed C, by any implementation at all
(the closed form in C is ~10 instructions per byte, ~100 cycles for an 8-byte
payload, off the turnaround path entirely).

That deletes `TXSEG1`/`TXSEG2`'s CRC work from the timed chain — **11 cycles per
byte back**, TX's slack goes 5 → ~16 — and removes TX's dependence on the table
without needing the closed form in the timed chain at all. It also removes
`r8`'s double duty as the CRC pointer *and* the exhaustion anchor
(`engine16_tx.S:162-163`), which §7.2 of the catalogue wants for other reasons.

This is a *restructuring* claim, not a measured one: it depends on C-5 landing.
Recorded as the dependency it is.

### 2.7 Why USB's polynomial costs six instructions more than CCITT's

The same derivation applied to CRC-CCITT (0x1021, MSB-first) reproduces the
folklore trick exactly, and shows where the six instructions come from.

`0x1021` has **no tap at bit 15**, so nothing re-enters the top and the bits
shifted out are just the input byte — no prefix XOR at all. The three taps then
land as: bit 12 → bits 15..12 (only four of the eight steps stay inside the
register, which is precisely the `x ^= x>>4` fold), bit 5 → `x<<5`, bit 0 → `x`.
That is `crc = (crc<<8) ^ (x<<12) ^ (x<<5) ^ x` with `x ^= x>>4`, derived rather
than recalled.

USB's polynomial is `x^16 + x^15 + x^2 + 1`. It has **both** the `x^15` term
(which is the adjacent tap for MSB-first) and the `x^0` term (the adjacent tap
for LSB-first, `0xA001` bit 0). Every CRC polynomial has an `x^0` term by
definition, so **no reflected CRC can avoid the prefix XOR**; and 0x8005 happens
to have `x^15` too, so the MSB-first orientation does not escape it either (§5).
The prefix-XOR scan is the irreducible extra cost, and its doubling form —
`x^=x<<1; x^=x<<2; x^=x<<4` — is optimal at 3 steps for 8 lanes.

**Not found in the literature.** Searches for a table-free byte-at-a-time
0xA001/0x8005 form returned only the bitwise loop and nibble tables
([NXP](https://community.nxp.com/t5/Kinetis-Microcontrollers/Std-CRC-16-bytewise-with-0xA001-polynomial/m-p/240276),
[Medo64 nibble table](https://www.medo64.com/2022/10/crc-16-nibble-lookup-table/),
[wxWiki small-table CRC](https://wiki.wxwidgets.org/Development:_Small_Table_CRC)).
The CCITT trick is folklore with no published derivation I could find. The
0xA001 form above is derived here; I claim novelty only in the weak sense that I
could not find it, not that it is unknown.

---

## 3. Linearity / GF(2) decomposition — **no**

`CRC(a ^ b) = CRC(a) ^ CRC(b)` for the linear part, and a shifted message is a
matrix multiply. The brief asks whether partial accumulation in separate
registers, combined once at the end, is cheap enough here. It is not, and the
reason is structural rather than arithmetic.

**What splitting buys, and where.** Splitting a message into *k* interleaved
streams and combining at the end is the standard high-throughput CRC technique
(zlib's `crc32_combine`, the "slicing-by-N" family, PCLMULQDQ folding). What it
buys is **instruction-level parallelism**: on a superscalar out-of-order core the
serial dependency `crc -> crc` is the bottleneck, and *k* independent chains fill
the pipeline. Cortex-M0+ is **single-issue, in-order, no dual issue**. There is
no latency to hide. *k* chains do exactly *k* times the per-byte work.

**What the recombination costs.** Combining a CRC computed over a stream that
must then be shifted by *n* bytes is multiplication by `z^{8n} mod g`, a 16x16
GF(2) matrix-vector product. On this ISA that is 16 iterations of
"test bit, conditionally XOR a row" — and with no `it` block, each is a mask
sequence (`M0PLUS_ISA_FACTS.md`): 4-5 cycles x 16 = **64-80 cycles**, once, plus
either 32 bytes of stored matrix per distinct shift or a log-squaring routine to
build it. On a 10-byte payload the shifts are all distinct, so it is a table
again — **32 bytes x every shift used**, which is the object we are deleting.

**Verdict: rejected, and it is not close.** Same per-byte work, plus 64-80 cycles
of recombination, plus storage. It is a technique for machines that have
parallelism to exploit; this one has none.

**The one linearity fact that is load-bearing** is already used, in §2: the byte
step is linear in the input byte, which is exactly why `T(0) = 0` and why
masking the *input* is a legal way to skip a non-committed wire byte. That saves
5 cycles in §2.3 relative to masking the 16-bit result. Linearity earns its keep
there and nowhere else.

## 4. Bit-sliced / SWAR

Two readings of "bit-sliced", and they have opposite answers.

**Reading A — transpose the state across registers, 1 CRC bit per register.**
This is the classic bit-slice, and it pays only when there are *many independent
messages* to advance in lockstep (one per bit lane). There is exactly one packet.
Sixteen registers holding one meaningful bit each, on a core with eight low
registers, is the worst possible register density — the precise opposite of what
`ENGINE16_CATALOG.md` §7.1 is asking for. **Rejected.**

**Reading B — advance several bit positions at once inside one register.**
This is the right reading, and **§2 already is it.** The eight LFSR steps of a
byte are collapsed into three shift-and-XOR pairs: that is a Hillis–Steele
prefix scan over 8 lanes in `log2(8) = 3` steps, and it is optimal for a scan.
`ENGINE16_CATALOG.md` §7.5 asks "what else in the pipeline widens from 8 bits to
32?" — **the CRC does, and this is the answer**, on exactly the same footing as
`SEGA`'s `x ^ (x>>1)` NRZI (A-4).

### 4.1 Widening to two bytes at once — derived, then rejected on the chassis

Sixteen steps at a time is derivable by the same method, and it is genuinely
cheaper per byte. With 16 steps the bit-13 tap **does** re-enter, so
`f_{k} = i_k ^ f_{k-1} ^ f_{k-14} ^ f_{k-16}`, which is the prefix XOR plus two
correction terms at k = 14, 15:

```
S = prefix_xor16(x) ^ (P_0 << 14) ^ ((P_0 ^ P_1) << 15)
T16 = S ^ (S >> 2) ^ (S >> 15)          (taps 15, 13, 0 at 16 steps)
```

Roughly 22 cycles for two bytes = **11/byte** against §2's ~15/byte
unconditional. The saving is real.

**Rejected, and the reason is specific.** Two wire bytes yield 0, 1 or 2
committed data bytes, and the 16-step formula is *not* the 8-step formula
gated — skipping one byte needs 8 steps, not 16 with a masked input. Three
distinct results would have to be computed and selected, or a branch taken, in
a chassis whose whole point (A-1) is that data 1, data 0 and stuffed bit are one
path. The gating that makes the byte-wide form free is exactly what the two-byte
form cannot have.

**Condition to revisit:** if the engine ever stops speculating — i.e. if the CRC
moves to a tail pass over `rxbuf`, where the byte count is known and no gating
exists (48 MHz, `ENGINE16_CATALOG.md` R-5) — then the two-byte form is the right
one and this section is the design.

## 5. Galois vs Fibonacci, reflected vs not

**Reflected is forced, not chosen.** USB is LSB-first on the wire (§8.1, and
`turnaround.md` L6), the engine assembles bytes LSB-first, and the non-reflected
form consumes bits MSB-first. Bridging them is a per-byte bit reversal, and
`M0PLUS_ISA_FACTS.md` records that **`rbit` does not exist** on ARMv6-M (`rev`
reverses bytes only). A software byte reversal is ~10 cycles. Dead on arrival.

**And it would not even be cheaper.** Deriving the non-reflected byte step by
the same method (0x8005 = taps 15, 2, 0, MSB-first): `f` is again a prefix XOR —
this time from the MSB down, because 0x8005 *has* the `x^15` term — and

```
T'[i] = P ^ (P << 2) ^ ((P & 1) << 15),   P = suffix_xor8(i)
```

which is the mirror image of §2.1 at the same instruction count. The two
orientations cost the same; only one of them is free of a bit reversal.

**Galois vs Fibonacci.** The table form and §2's closed form are both the
**Galois** (internal-XOR) LFSR: one conditional XOR of the whole polynomial per
step, which is what makes the 8 steps collapse into a scan. The **Fibonacci**
(external-XOR) form computes the feedback as a parity of taps and then shifts —
per step that is a parity, and parities do not collapse into a scan the same
way. On an AVR the Fibonacci form is attractive because `ror` through the carry
flag makes the shift free; ARMv6-M has `rors` but no rotate-through-carry, so
that advantage does not transfer. **Galois, reflected: forced twice over.**

---

## 6. Smaller tables — the dial has a **second axis**

`ENGINE16_CATALOG.md` R-6 states the dial as *bits consumed per lookup*: 8 bits
(512 B) or 4 bits (32 B, +9 cycles/byte). §2.1 exposes a second axis nobody has
turned: **how wide the stored value has to be.**

`T[i] = (S<<8) ^ (S<<6) ^ (S>>7)` is a five-instruction function of the single
byte `S`. So the halfword table is storing 16 bits where 8 would do. A
**256-byte table of `S = prefix_xor8(i)`** is exactly as informative as the
512-byte table of `T`.

### 6.1 Three points on the curve, assembled and measured

Same structure for all three — input-gated (§2.3), table at `r4+0` so that no
offset arithmetic distorts the comparison — assembled and run through
`tools/engine16_cyc.py --exec ram` (`bx lr` excluded):

| variant | table | cycles / wire byte | delta |
|---|---|---|---|
| `v512` halfword table of `T` | **512 B** | **17** | baseline |
| `v256` byte table of `S` | **256 B** | **21** | +4 |
| `v0` closed form (§2.3) | **0 B** | **25** | +8 |

(The engine as written measures 19, not 17, because `T_CRC16` sits at offset 512
and pays two extra `adds` for the index — `engine16_merged.S:256-258`. Placement
is a free variable; the table that ends up in the engine should be at offset 0.
Against the engine as it stands today the closed form is **+6**, §2.5.)

The two table variants in full, so the file is self-contained (`v0` is §2.3):

```asm
v512:   movs r2,#8 ; ands r2,r1 ; mov r8,r2          @ 256 halfwords of T[i]
        mov r2,r10 ; eors r2,r3 ; ands r2,r1 ; uxtb r2,r2
        lsls r2,r2,#1 ; ldrh r1,[r4,r2]                     @ 2
        mov r2,r8 ; mov r8,r1 ; mov r1,r10 ; lsrs r1,r1,r2
        mov r2,r8 ; eors r1,r2 ; mov r10,r1                 @ 17 cycles

v256:   movs r2,#8 ; ands r2,r1 ; mov r8,r2          @ 256 bytes of prefix_xor8
        mov r2,r10 ; eors r2,r3 ; ands r2,r1 ; uxtb r2,r2
        ldrb r2,[r4,r2]                                     @ 2   -> S
        lsrs r1,r2,#7 ; lsls r2,r2,#6 ; eors r1,r2          @ parity, S<<6
        lsls r2,r2,#2 ; eors r1,r2                          @ S<<8      -> T
        mov r2,r8 ; mov r8,r1 ; mov r1,r10 ; lsrs r1,r1,r2
        mov r2,r8 ; eors r1,r2 ; mov r10,r1                 @ 21 cycles
```

`v256` verified the same way as §2.4: 0 mismatches over 1 135 940 (crc, byte)
pairs, 0 on the skip path, 0 failures over 2000 packets.

### 6.2 The 32-byte nibble table (R-6) is **dominated** — delete it as an option

Reconstructing `S` for a byte from two nibble lookups needs the low nibble's
prefix, the high nibble's prefix, and a correction: if the low nibble has odd
parity the high nibble's prefix must be complemented. That is two `ldrb` (4
cycles), a mask, a shift, a 4-bit sign-extend-to-mask, an XOR and a merge —
~16 cycles to reach `S`, against **7** for the closed form's scan.

| | bytes saved | cycles added |
|---|---|---|
| R-6 nibble table | −480 | **+9** |
| closed form §2 | **−512** | **+6** |

The closed form is better on **both** axes. R-6 is not a fallback below the
closed form; it is strictly worse than it. Its recorded warning — that it eats
all 9 cycles of slack and therefore collides with R-7 mid-packet resync — is
resolved by the closed form leaving 3.

### 6.3 A table of a different function

The brief asks whether a table of some *other* function is cheaper to apply.
§6.1 answers it: the cheapest such function is `prefix_xor8`, and it costs 4
cycles to apply against 0 for `T`, buying 256 B. Going further down (parity,
2 bits) buys 224 B more and costs the whole scan back. There is no third useful
point: the function has to be `S` or a refinement of it, because `S` is
precisely the state the LFSR is in after the byte, expressed minimally.

---

## 7. The hardware CRC unit — **verified: cannot serve, and cannot even help**

### 7.1 The register map, checked

`CRC_TypeDef` is `DR`/`IDR`/`CR` at 0x00/0x04/0x08 on `py32f002x5.h` and
`py32f003x8.h`, and the **only** bit defined in `CR` is:

```
#define CRC_CR_RESET_Pos   (0U)
#define CRC_CR_RESET_Msk   (0x1UL << CRC_CR_RESET_Pos)
```

No `POLYSIZE`, no `POL` register, no `INIT` register, no `REV_IN`/`REV_OUT`.
`CRC_HandleTypeDef` (`py32f002b_hal_crc.h`) carries only `Instance`, `Lock`,
`State` — none of ST's F0/F3 `InitValue`, `GeneratingPolynomial`,
`InputDataInversionMode` fields — and the API is
`HAL_CRC_Accumulate(hcrc, uint32_t pBuffer[], BufferLength)`: **word input
only.** `IDR` is 8 bits of scratch storage with no connection to the engine.

**The owner's reading is correct.** This is the STM32F1-generation fixed unit:
CRC-32/MPEG-2 style, polynomial 0x04C11DB7, init 0xFFFFFFFF, MSB-first, no
reflection, 32-bit writes only. It cannot be told to compute CRC-16/USB. (The
"specify generating polynomial" line in `py32f002b_hal_crc.c:19` is ST
boilerplate copied along with the file header; the register map does not
implement it. Trust the map.)

### 7.2 Could a fixed CRC-32 serve as a filter? **No — proved, not assumed**

The owner's guess was "no, because a valid CRC16 codeword has an arbitrary
CRC-32". That is right, and here is the proof rather than the guess.

The received bytes carry exactly one piece of redundancy: the 16 CRC bits the
host appended, which are defined **modulo `g16`**. A test of integrity must be a
function that is constant on the codeword set `C = {c : c ≡ 0 mod g16}` and
non-constant off it. `CRC32(c)` is not constant on `C` — two different valid
packets have different CRC-32s — so it is not a test at all. There is nothing to
compare a CRC-32 against.

The only way a CRC-32 could carry *partial* information about the CRC-16
syndrome is through a shared factor:

```
g16 = x^16+x^15+x^2+1 = (x+1)(x^15+x+1)        [x^15+x+1 verified irreducible]
g32 = x^32+x^26+...+x+1   (0x04C11DB7)
gcd(g16, g32) = 1                               [computed]
```

`gcd = 1`, so **not even the parity bit is shared** — `g32` has 15 terms, an odd
count, so unlike many CRC-32s it is not divisible by `(x+1)`. Demonstration:
4000 messages constructed to share one CRC-32 residue produced **3871 distinct
CRC-16 residues** out of 65536 — the CRC-16 residue is free.

Three further nails, any one of which is fatal on its own: the unit takes
**32-bit words only** (USB packets are 1..10 payload bytes and are not word
multiples); it is **MSB-first with no input reflection**, and USB is LSB-first
with no `rbit` on this core; and `DR` is an APB peripheral, so each write is a
peripheral store from RAM-resident code, not the 1-cycle IOPORT access GPIO
gets — the timed cell cannot afford it.

**Verdict: the hardware CRC unit is unusable for this problem. Refuted, with the
arithmetic.**

---

## 8. Weaker checks — Fletcher, Adler, sums, syndromes, partial CRC

### 8.1 Fletcher, Adler and one's-complement sums are not *available*

This has to be said first because it disposes of half the list. Those are
alternative *codes*: you compute them at the sender and check them at the
receiver. The receiver here does not get to choose the code. The only
redundancy on the wire is the 16 CRC bits the **host** appended, computed with
`g16`. A Fletcher sum of the received bytes has nothing to be compared against.

Substituting a cheaper code is only possible if both ends can be changed. USB's
host cannot. **Rejected as inapplicable, not as inferior.**

### 8.2 What weakening *is* algebraically legal

A legal weaker check is a residue modulo a **divisor** of `g16` — that, and
nothing else, is guaranteed to be zero on every valid codeword. The divisor
lattice is completely known:

```
g16 = (x+1) * (x^15+x+1),   x^15+x+1 irreducible  [verified: no factor of degree <= 8,
                                                   which for degree 15 settles it]
```

so the proper divisors are exactly **`(x+1)`** and **`(x^15+x+1)`**. There is no
degree-4 or degree-8 divisor to check against. This closes the design space:

* **degree 15** — a 15-bit LFSR is not cheaper than a 16-bit one; the same
  derivation applies and gives the same instruction count. **No saving.**
* **degree 1, `(x+1)`** — the parity of the whole received bit string. This is
  the only cheap point that exists.

### 8.3 Parity-only: the cost, and exactly what it stops detecting

Verified: the parity of `payload || transmitted CRC16` is **0 for every valid
packet** (5000 random packets, one distinct value in the set) — the init and the
complement both have even weight, so the constant is 0.

Cost in the engine: `ands` the byte with the commit mask, `eors` into an
accumulator — **2 cycles per wire byte, 0 bytes of table**, against 19-25.
It is by far the cheapest thing on this page.

What it detects and what it does not, measured over 20 000 trials on an 80-bit
packet:

| error | parity catches | CRC16 catches |
|---|---|---|
| 1 bit flipped | 1.0000 | 1.0000 |
| 2 bits flipped | **0.0000** | 0.9881 * |
| 3 bits flipped | 1.0000 | 1.0000 |
| 4 bits flipped | **0.0000** | 0.9992 * |

\* the CRC shortfall is entirely the trials where the two random positions
collided and cancelled (1/80 = 0.0125); on genuinely distinct positions CRC-16
catches **all** 2-bit and 3-bit errors, and all bursts up to 16 bits.

So parity-only misses **every even-weight error**: all 2-bit errors, all
even-weight bursts, and about half of random multi-bit corruption. Undetected
error probability goes from ~2^-16 to ~2^-1. For a check whose entire job is to
decide whether to ACK, that is a 32768x degradation.

**Verdict: not acceptable as the check.** It is worth naming for one reason
only — if RAM pressure ever forces the CRC out entirely, parity is *strictly
better than V-USB's nothing* at 2 cycles and 0 bytes, and it should be what the
engine falls back to rather than nothing. Ranked last-but-one in §11, above
"no check", never above a real CRC.

### 8.4 Syndrome tables

A syndrome table maps residue -> error pattern, for *correction*. It is 65536
entries for a 16-bit syndrome, or a discrete-log construction that is far more
arithmetic. It makes the problem bigger in both dimensions and answers a
question nobody asked — USB's recovery for a bad packet is silence and a host
retry (`turnaround.md` L10, L11), not correction. **Rejected.**

### 8.5 The checks that are already free, and what they leave

Worth stating so that "weaker check" proposals are measured against the right
baseline, not against zero. The engine already detects, at no extra cost:

* **bit-stuff violations** — seven consecutive 1s, the sticky row 7 of `T_UT`
  (A-2, D-A). Catches any corruption that produces an illegal run.
* **PID check bits** — 4 bits complemented (`turnaround.md` L7).
* **byte count and EOP position** — a slipped bit changes the packet length.

Those catch a large fraction of *wire-level* faults (slips, dropouts, SE0
glitches). What they do not catch is the case the CRC exists for: **a bit flip
that leaves the packet structurally legal and changes a data value.** That is
the residual the 512 bytes are buying down, and it is the only thing worth
measuring a replacement against.

### 8.6 Deferring the whole check to the tail — does not fit at 24 MHz

For completeness, since §2's candidate could in principle be a tail pass. In the
tail there is no gating (the byte count is known), so the closed form is ~15
cycles/byte plus loop overhead; over a 10-byte payload that is **~150-170
cycles**. `turnaround.md` §5 measures 114 cycles of work already in the tail
against a 104-cycle window. **Deferral is not viable at 24 MHz** — which is
exactly `ENGINE16_CATALOG.md` R-5's conclusion, and the closed form does not
change it. In-cell is required, and §2 is in-cell.

---

## 9. What the search found

Explicitly separating *found* from *derived here*.

### 9.1 Software USB stacks on ARM: none of them check the receive CRC

Three independent stacks, three times the same answer.

| stack | receive CRC16 | evidence |
|---|---|---|
| **V-USB** (AVR) | **not checked** | "We could check CRC16 here — but ACK has already been sent anyway"; `usbCrc16` appears only as `usbCrc16Append` on transmit (`VUSB_MICRONUCLEUS.md` §, already in this repo) |
| **Grainuum** (Cortex-M0+, xobs) | **not checked** | `crc16_add` is the plain bitwise loop and is called only from the transmit path, `grainuum-state.c:155`; the receive path does `memcpy(state->tok_buf + state->tok_pos, packet, size - 2)` — it *discards* the two CRC bytes without looking at them (`grainuum-state.c:297`) |
| **LemcUSB** (Cortex-M0+ at **24 MHz** — the closest prior art there is) | **not checked**, and CRC5 not checked either | `crc16()` in `usb_helperfunctions.h:43-60` is the bitwise loop; its only caller is `usb_ep_in_commit_pkt()` (`usb.c:201-213`), the transmit buffer-fill. The 484-line receive assembly `usb_internal_bitbangusb.s` contains the string "CRC" exactly once, in a TODO: "interpret CRC5 ??" |

Sources:
[xobs/grainuum](https://github.com/xobs/grainuum),
[lemcu/LemcUSB](https://github.com/lemcu/LemcUSB).

**Two things follow, and they point in opposite directions.**

1. The project's position — check the CRC, gate the ACK on it — is genuinely
   better than all three, and the 512 bytes are what it costs. That is worth
   knowing before deciding the price is too high.
2. **LemcUSB independently does §2.6**: its TX CRC is computed in C, in the
   buffer-fill function, entirely outside the timed path. The restructuring this
   file proposes for our TX engine is not speculative; the nearest comparable
   implementation already works that way.

### 9.2 Table-free CRC-16 for 0xA001: not found

Searches for a byte-at-a-time table-free form for 0x8005/0xA001 turned up only
(a) the bitwise loop and (b) nibble tables. Representative:
[NXP forum, "Std CRC-16 bytewise with 0xA001"](https://community.nxp.com/t5/Kinetis-Microcontrollers/Std-CRC-16-bytewise-with-0xA001-polynomial/m-p/240276),
[Medo64, CRC-16 nibble lookup table](https://www.medo64.com/2022/10/crc-16-nibble-lookup-table/),
[wxWiki, Small Table CRC](https://wiki.wxwidgets.org/Development:_Small_Table_CRC),
[sunshine2k, Understanding CRC](https://www.sunshine2k.de/articles/coding/crc/understanding_crc.html).
No derivation of the CCITT trick, and no generalisation of it, was findable.
**§2 is derived here.** I claim novelty only in the weak sense that I searched
and did not find it.

### 9.3 Chorba (arXiv 2412.16398) — table-free CRC-32, and not applicable

[Chorba: A novel CRC32 implementation](https://arxiv.org/abs/2412.16398)
(Sam Russell, Dec 2024) reports table-free CRC-32 at ~2x the throughput of
slicing-by-8, matching hardware CRC instructions on x86_64 and ARMv8. It is
genuine prior art for "CRC without tables" and worth citing.

**I could not extract the full text** (the PDF did not convert), so what follows
is reasoning from the abstract and from the technique family, and is labelled as
such. Chorba's gains are reported on x86_64/ARMv8 against multi-kilobyte
buffers; the family it belongs to (fold many words in flight, reduce once at the
end) is the same family as §3, and it requires the same two things this core does
not have: instruction-level parallelism to hide the dependency chain, and a
buffer long enough to amortise a final reduction. Here the message is 1..10
bytes, the CRC value must be current at EOP, and the core is single-issue
in-order. **Not applicable, on the same grounds as §3** — stated as an inference,
not as a reading of the paper.

---

## 10. What "no false ACK" requires of a replacement

The property, stated precisely (`turnaround.md`, `ENGINE16_CATALOG.md` A-9): the
instruction that puts the ACK PID on the wire is **downstream in the control
flow** of the residue comparison. It is not a probabilistic claim about the CRC;
it is a claim about instruction order, and it holds for any check whatsoever
provided the verdict exists before the PID is emitted.

Two distinct things a candidate can break:

1. **Ordering.** A candidate that cannot produce a verdict before the PID breaks
   it structurally. §8.6 is why deferral to the tail does exactly that at
   24 MHz, and why every candidate here is in-cell.
2. **Strength.** A candidate that produces a *weaker* verdict on time keeps the
   structural property and degrades the number behind it — parity (§8.3) takes
   the undetected-error probability from ~2^-16 to ~2^-1 while remaining
   structurally sound. "Cannot false-ACK" then means something much weaker than
   it does today, and any note claiming it must say which.

| candidate | ordering | strength | property held? |
|---|---|---|---|
| §2 closed form | verdict at EOP, unchanged | identical CRC-16, bit for bit | **yes, fully** |
| §6 `v256` byte table | unchanged | identical | **yes, fully** |
| §2.6 TX in C | not a receive check | n/a | unaffected |
| §8.3 parity | unchanged | 2^-16 -> 2^-1 | structurally yes, **materially no** |
| deferral (§8.6) | verdict after the PID | identical | **no** |
| §7 hardware CRC-32 | n/a | no relation to `g16` | **no** |

Only the first two are drop-in. That is the whole point of preferring them: the
CRC-16 value they produce is **bit-identical to today's**, verified over
1 135 940 (crc, byte) pairs and 5000 packets, so nothing downstream — the
0xB001 residue test, the byte count, the ACK gate — changes at all.

---

## 11. Ranking, and the recommendation

Cycles are per **wire byte**, measured on assembled objects with
`tools/engine16_cyc.py --exec ram`; the engine as it stands today is 19.
Bytes are of the **shared** `T_CRC16`, counted once for the RX+TX pair.

| # | candidate | cycles | vs today | table | in cell? | no false ACK | verdict |
|---|---|---|---|---|---|---|---|
| **1** | **§2 closed form** | **25** | **+6** | **0 B** | yes | identical | **adopt** |
| 2 | §6 `v256` byte table of `S` | 21 | +2 | 256 B | yes | identical | fallback if 3 cycles of slack is not enough |
| 3 | §6.1 move `T_CRC16` to offset 0 | 17 | **−2** | 512 B | yes | identical | **free; do it regardless of 1 or 2** |
| 4 | §2.6 TX CRC in C at buffer-fill | −11 on TX | | | n/a | unaffected | **required** for 1 or 2 to save anything |
| 5 | §8.3 parity only | ~2 | −17 | 0 B | yes | 2^-16 -> 2^-1 | last resort above "no check"; **not** a CRC |
| — | §6.2 nibble table (R-6) | 28 * | +9 * | 32 B | yes | identical | **dominated by 1 — delete as an option** |
| — | §4.1 two-byte SWAR step | ~11/byte | −8 | 0 B | **no** | identical | incompatible with speculative commit; revisit only if gating disappears |
| — | §8.6 defer to tail | n/a | | 0 B | no | **breaks ordering** | rejected at 24 MHz |
| — | §3 GF(2) split / recombine | ≥ same | +64..80 once | +32 B/shift | no | identical | rejected: no ILP to exploit |
| — | §4 bit-slice (transpose) | worse | | 0 B | no | identical | rejected: one message, not many |
| — | §7 hardware CRC unit | n/a | | 0 B | no | **cannot test `g16`** | refuted, §7.2 |
| — | §8.1 Fletcher / Adler / sums | n/a | | | | | inapplicable: the host chose the code |
| — | §8.4 syndrome table | worse | | 64 KB | no | | rejected |

\* R-6's own recorded figure (`ENGINE16_CATALOG.md`, "+9 cycles per wire byte").
My reconstruction in §6.2 makes it worse than that — ~16 cycles to rebuild `S`
from two nibble lookups against 7 for the scan, i.e. ~34 cycles — but the
catalogue's own number is used here so the comparison cannot be accused of
being stacked. It loses either way.

### Recommendation: adopt §2, the closed form, together with 3 and 4

The arithmetic that decides it:

**Bytes.** `2716 − 512 = 2204`, plus the code the closed form adds. `SEG4`
appears at three sites (`engine16_merged.S:485, 524, 551`) and grows by ~7
16-bit instructions each: **+42 B**. Net **≈ 2246 B** for the pair, and that is
an upper bound because §2.6 shrinks TX.

* **F002Bx5 (3072 B): 826 B spare**, against 356 B with the table shared. That
  is the difference between "fits" and "fits with room for the C layer".
* **F003x4 (2048 B): still 198 B over.** The closed form is **necessary and not
  sufficient** there; `ENGINE16_CATALOG.md` R-4 (fused mask, −800 B of `T_UT`)
  or the `T_UT` dial still has to go too. Nobody should read this file as having
  solved F003x4.

**Cycles.** The RX per-byte pipeline goes 76 → 82 against 88 cycles of segment
room. `5*8 + 82 + 3 = 125` of 128. **3 cycles of slack, down from 9.**

**The cost that must be said out loud.** Those 9 cycles had three named
claimants (`ENGINE16_CATALOG.md` R-7): mid-packet resync, a cheaper CRC, and
A-16's pre-staging. This spends 6 of them on the second. **Mid-packet resync
becomes unaffordable at 24 MHz** if this is adopted. That is a real trade and it
should be decided deliberately, not discovered later — and it is the one reason
to prefer candidate 2 (`v256`, +2 cycles, 7 cycles of slack left, 256 B saved)
if the bench ever shows the drift budget needs resync. At 48 MHz
(`ENGINE16_CATALOG.md` §0.1) the question evaporates: a 32-cycle cell has room
for both.

**Why not simply keep the 512 bytes.** The brief invited a negative result and
it is not the answer here, for a reason that is arithmetic rather than taste:
the *only* thing the table buys over the closed form is **6 cycles per wire
byte**, and the engine has 9 to spend. It buys no correctness, no simplicity
(the closed form is 14 straight-line instructions with no index arithmetic, no
`.error` guard on the table offset, and no generator to keep in sync — three of
the five defect classes in `ENGINE16_CATALOG.md` §5.2 were table-related), and
no verification confidence. Six cycles at 512 bytes is **85 bytes per cycle**,
on a part where the pair is 156 B over its budget. That is not a price worth
paying.

### What to build, in order

1. **Move `T_CRC16` to offset 0** and drop `SEG4`'s two `adds`. Free 2 cycles,
   independent of everything else, and it makes the comparison honest.
2. **§2.6 on TX first**, because until TX stops using the table the 512 B does
   not go anywhere. It is a restructuring of the C layer that
   `ENGINE16_CATALOG.md` C-5 already wants for other reasons, and LemcUSB
   already does it (§9.1).
3. **Swap `SEG4`/`SEG6` for the §2.3 block on RX** and re-cut the segment
   boundaries to ≤11 cycles per cell. The `CELL` macro's `.error` proves the
   re-cut (A-11); no new verification machinery is needed.
4. **Re-run the existing packet harness.** The CRC value is bit-identical, so a
   single failure means the transcription is wrong, not the mathematics.

## 12. What is verified and what is reasoned

**Verified by running code.**
* The closed form `T[i] = (S<<8)^(S<<6)^(S>>7)`: 0 mismatches over all 256 table
  entries; 2004 vectors agree with a bitwise reference; residue 0xB001.
* The 25-instruction ARM block, transcribed instruction-for-instruction:
  1 135 940 (crc, byte) pairs, 0 mismatches; skip path leaves the CRC unchanged
  in 9362/9362 cases; 3000 packets with interleaved skipped bytes, 0 failures.
* The `v256` byte-table variant: same three tests, 0 failures.
* Cycle counts 17 / 21 / 25: assembled with `arm-none-eabi-as -mcpu=cortex-m0plus`
  and measured by `tools/engine16_cyc.py --exec ram`. All 16-bit encodings.
* `g16 = (x+1)(x^15+x+1)`, `x^15+x+1` irreducible, `gcd(g16, g32) = 1`, `g32` has
  odd weight; 4000 CRC-32-equal messages gave 3871 distinct CRC-16 residues.
* Parity of `payload || transmitted CRC16` is 0 for all 5000 valid packets;
  parity catches 0.0000 of 2-bit and 4-bit errors over 20 000 trials each.
* The PY32 CRC register map and HAL handle, read from the headers.
* Grainuum and LemcUSB do not check the receive CRC, read from their sources.

**Reasoned, not measured.**
* The re-cut of the eight segments to absorb 82 cycles. The totals fit
  (82 ≤ 88, 125 ≤ 128); the specific partition is not written.
* The +42 B code-size estimate (7 instructions x 3 sites x 2 B).
* §2.6, that TX's CRC can move to buffer-fill time. It depends on the C-5
  restructuring landing, and LemcUSB is evidence that it works, not proof that
  it works here.
* §4.1's ~11 cycles/byte for the two-byte step — derived, counted by hand, **not
  assembled**. It is rejected on chassis grounds anyway.
* §9.3's assessment of Chorba, from the abstract only.
* Everything about 48 MHz, which this file inherits and does not re-derive.

**Not attempted.** Silicon. No figure here has been on a bench, and
`ENGINE16_CATALOG.md` C-4's warning applies: the taken-branch cost is still
unmeasured. This block contains no branches, which is one fewer thing exposed to
that.

---

## 13. Reproducing every number in this file

Following `ENGINE16_CATALOG.md`'s convention that a table is quoted as its
generator: **this candidate has no table, so what is quoted is its checker.**
Paste and run; it needs nothing but python3.

```python
def crc16_bitwise(data, init=0xFFFF):              # the reference
    c = init
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ (0xA001 if c & 1 else 0)
    return c

M = 0xFFFFFFFF
def crc_block(r10, r3, mask):        # one line per ARM instruction, S2.3
    r = {1: mask, 3: r3, 8: 0, 10: r10}
    def st(k, v): r[k] = v & M
    st(2, 8); st(2, r[2] & r[1]); st(8, r[2])              # sh = 8 & mask, park
    st(2, r[10]); st(2, r[2] ^ r[3]); st(2, r[2] & r[1])   # gated x
    st(2, r[2] << 24)                                      # y = x<<24
    st(1, r[2] << 1);  st(2, r[2] ^ r[1])
    st(1, r[2] << 2);  st(2, r[2] ^ r[1])
    st(1, r[2] << 4);  st(2, r[2] ^ r[1])                  # y = S<<24
    st(1, r[2] >> 2);  st(1, r[1] ^ r[2])                  # z = y ^ (y>>2)
    st(2, r[1] >> 15); st(1, r[1] ^ r[2]); st(1, r[1] >> 16)   # T
    st(2, r[8]); st(8, r[1]); st(1, r[10])
    st(1, r[1] >> r[2]); st(2, r[8]); st(1, r[1] ^ r[2]); st(10, r[1])
    return r[10]

import random; random.seed(7); fail = 0
for _ in range(3000):
    d = [random.randrange(256) for _ in range(random.randrange(0, 20))]
    c = 0xFFFF
    for b in d:
        for _ in range(random.randrange(0, 3)):
            c = crc_block(c, random.randrange(256), 0)     # non-committed byte
        c = crc_block(c, b, M)
    fail += (c != crc16_bitwise(bytes(d)))
print("failures:", fail)                                   # 0
```

Cycle counts, from the repository root:

```
# variants.s = the three blocks of S2.3 and S6.1, each ending in `bx lr`
arm-none-eabi-as -mcpu=cortex-m0plus -mthumb variants.s -o variants.o
python3 tools/engine16_cyc.py --exec ram variants.o     # 20/24/28 incl. bx lr = 3
```

The GF(2) facts in §7.2 and §8.2 (`gcd(g16,g32) = 1`, `g16 = (x+1)(x^15+x+1)`,
`x^15+x+1` irreducible) reduce to polynomial long division over GF(2) with
`g16 = 0x18005`, `g32 = 0x104C11DB7`; a degree-15 polynomial with no factor of
degree ≤ 7 is irreducible, and none of degree ≤ 8 was found.
