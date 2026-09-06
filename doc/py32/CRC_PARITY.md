# Parity as the receive integrity check — a fair hearing, and the numbers

**The question.** `CRC_ALTERNATIVES.md` §8.3 disposed of parity in a paragraph
by comparing it to CRC-16 and reporting that it catches none of the 2-bit and
4-bit errors. That is true and it is the wrong comparison. V-USB, Grainuum and
LemcUSB all check **nothing** on receive (`CRC_ALTERNATIVES.md` §9.1), so the
baseline a cheap check has to beat is *no check*, not a full CRC. This file
re-runs the case with that baseline, and it builds the option.

**Result, in one line.** Parity is a genuine partial CRC-16 check and it is
cheaper than free — no bit cell changes (they are 16 cycles by construction at
every setting), the untimed flush gets 5 cycles per wire byte back, and the
turnaround improves by 12 — but on the wire this engine actually reads, it
raises single-glitch detection from **17.1 % to 22.2 %** where CRC-16 gives
**100 %**. It is shipped as a build-time option, `USB_RX_CHECK=1`, and it is
**not** the default.

**Why it is so much weaker than the algebra promises.** Parity catches every
odd-weight error, and the brief for this work said single-bit errors dominate on
a wire. On *this* wire they do not turn into single-bit errors. USB is NRZI, and
this engine samples once per cell with no DPLL, so **one corrupted line sample
becomes two adjacent flipped data bits** — even weight, invisible to parity.
Measured: of the single-sample corruptions that get past the checks the engine
already has, **97.5 % are weight 2** and 2.5 % are weight 1 (§3.2). That single
fact, not the algebra, is what decides this.

## Contents
1. The algebra — verified here, not taken on trust
2. What the engine already detects for free
3. Error detection, measured, in two domains
4. The cost — zero cycles, and why "zero" is exact
5. Ordering and turnaround: the no-false-ACK property
6. Tokens and CRC5: the argument does not transfer, and the reason is clean
7. The build-time knob, and the linked sizes
8. Verdict
9. Reproducing every number

---

## 1. The algebra

### 1.1 The factorisation, checked

```
g16 = x^16 + x^15 + x^2 + 1                      (0x8005 normal form)
    = (x + 1)(x^15 + x + 1)                      [computed: 0x18005 == 0x18005]
x^15 + x + 1 is irreducible                      [no factor of degree <= 7]
```

so the proper divisors of `g16` are exactly `(x+1)` and `(x^15+x+1)`, and there
is nothing in between. A check that is constant on the codeword set must be a
residue modulo a divisor; `(x+1)` is the only cheap one. This is the same
conclusion `CRC_ALTERNATIVES.md` §8.2 reached, re-derived rather than cited.

### 1.2 The consequence, checked

Because `(x+1)` divides `g16`, **every valid CRC-16 codeword has even parity**.
Checking the parity of the received data field *including the two transmitted
CRC bytes* is therefore a correct partial CRC test — not a heuristic bolted on
beside one.

Verified numerically, against a bitwise CRC-16 written from the definition
(self-check `crc16_bitwise(b"123456789") == 0x4B37`):

| check | result |
|---|---|
| parity of `payload \|\| transmitted CRC16`, 20 000 random packets | odd on **0** of them |
| the same, exhaustive over all payloads of length 0, 1, 2 (65 793 cases) | odd on **0** of them |
| CRC-16 residue over `payload \|\| CRC`, 2000 packets | `{0xB001}`, one value |

The residue constant matches the one the engine already tests
(`engine16_merged.S:739`), so the two checks are testing the same codeword set.

### 1.3 What the engine gets to exploit

The engine emits SYNC, then PID, then the data field. Two more facts make the
tail test three instructions instead of ten:

* **SYNC decodes to 0x80**, which has parity **1**.
* **Every legal PID byte has parity 0.** A PID is `(~n<<4) | n`, and
  `popcount(~n) = 4 - popcount(n)`, so its parity is `parity(n) ^ parity(n) = 0`.
  Checked over all 16 legal PID bytes: the set of parities is `{0}`.

So a running XOR of the per-byte parities over **all** emitted bytes — SYNC and
PID included, no gating needed — must end at **1** on every valid packet.
Verified over 20 000 packets across all 16 PIDs: **0 failures**. The engine
therefore does not have to reproduce the CRC's `count >= 3` gate, and the tail
does not have to fold a byte down to a bit.

---

## 2. The baseline: what the engine already detects for nothing

A cheap check must be measured against what is already there, not against zero.
Before any CRC or parity runs, `engine16_merged.S` rejects a packet on:

* **bit-stuff violation** — seven consecutive 1s, sticky in `r11` row 7, tested
  at `.Lrx_tail`;
* **emitted byte count** — `2..12`, and `>= 4` for a DATAx arm;
* **SYNC byte** — must decode to exactly `0x80`;
* **PID check bits** — high nibble must be the complement of the low nibble.

Those are structural, they cost nothing extra, and on the wire model of §3 they
alone reject **17.1 %** of single-sample corruptions. Any claim about a check's
value has to be stated as what it adds *on top of that*, and the tables below
are written that way.

## 3. Detection, measured

### 3.1 Two domains, and why the second is the real one

**Domain A — decoded data.** Flip bits of the data field directly. This is the
model `CRC_ALTERNATIVES.md` §8.3 used, and the model the brief for this work
assumed when it said single-bit errors dominate.

**Domain B — the wire.** Flip line samples of the NRZI-encoded, bit-stuffed
packet and then decode it the way the engine does. This engine samples once per
bit cell at a fixed phase and has no DPLL, so a glitch corrupts *a sample*, and
what reaches the check is whatever that sample does after NRZI decoding and
unstuffing.

Both are reported. Domain B is the one that describes this hardware.

Method: 40 000 trials per row, payloads 1..8 bytes, seeded RNG. A trial that
leaves the received data field unchanged is discarded, not counted as a success.
The wire model is self-tested first — 5000 packets encode and decode back
byte-identically, stuffing exercised on all-`0xFF` payloads, and every valid
data field has residue `0xB001` and even parity.

### 3.2 The mechanism that decides the case

NRZI decodes `d_i = NOT(s_i XOR s_{i-1})`. Flip one sample `s_k` and **both**
`d_k` and `d_{k+1}` flip. One wire glitch is a **weight-2** data error, and
weight 2 is exactly what parity cannot see.

Measured — Hamming weight of the decoded error, over the single-sample
corruptions that get past the structural checks:

| injected on the wire | weight 1 | weight 2 | weight >= 4 |
|---|---|---|---|
| 1 sample flipped | 0.0246 | **0.9754** | 0 |
| 2 adjacent samples | 0.0249 | 0.9751 | 0 |
| 4 adjacent samples | 0.0263 | 0.9735 | 0.0002 |
| 8 adjacent samples | 0.0311 | 0.9673 | 0.0016 |
| level inverted to EOP | **1.0000** | 0 | 0 |

A contiguous run of flipped samples from `k` to `m` produces flips at `d_k` and
`d_{m+1}` — always two — so burst length does not help parity either. The one
row parity owns outright is the last: a level inversion that runs to EOP flips
only `d_k`, weight 1, and parity catches all of it.

The residual odd-weight cases (2.5 %) are those where the corruption also moved
a stuffed bit, shifting the stream.

### 3.3 Domain A — decoded data (the previous analysis's model)

Detection rate; the structural checks see nothing here, because the injection
does not disturb length or stuffing.

| error | corrupt trials | CRC-16 | parity | no check |
|---|---|---|---|---|
| 1 bit | 40000 | 1.0000 | **1.0000** | 0 |
| 2 bits | 40000 | 1.0000 | **0.0000** | 0 |
| 3 bits | 40000 | 1.0000 | **1.0000** | 0 |
| 4 bits | 40000 | 0.9994 | **0.0000** | 0 |
| 5 bits | 40000 | 1.0000 | **1.0000** | 0 |
| 8 bits | 40000 | 1.0000 | **0.0000** | 0 |
| burst 2 | 40000 | 1.0000 | 0.0000 | 0 |
| burst 3 | 40000 | 1.0000 | 0.4989 | 0 |
| burst 4 | 40000 | 1.0000 | 0.5014 | 0 |
| burst 5 | 40000 | 1.0000 | 0.5031 | 0 |
| burst 6 | 40000 | 1.0000 | 0.5044 | 0 |
| burst 7 | 40000 | 1.0000 | 0.5000 | 0 |
| burst 8 | 40000 | 1.0000 | 0.4996 | 0 |
| whole byte replaced | 39837 | 1.0000 | 0.4994 | 0 |

Read on its own this is the case *for* parity: it catches every odd-weight
error, half of everything random, and the 2⁻¹ figure `CRC_ALTERNATIVES.md`
quoted is the last row, not the first. The CRC-16 shortfall at 4 bits is the
trials where two of the four positions collided.

### 3.4 Domain B — the wire (this engine's actual failure modes)

Same trials, injected on the line samples. "free only" is the four structural
checks of §2 with no CRC and no parity — i.e. what shipping V-USB's answer
would give. "parity" and "CRC-16" are the total including those checks.

| error injected on the wire | corrupt trials | free only | CRC-16 | parity | parity adds |
|---|---|---|---|---|---|
| 1 sample | 40000 | 0.1714 | **1.0000** | 0.2220 | **+0.0506** |
| 2 samples | 40000 | 0.3173 | 1.0000 | 0.3943 | +0.0770 |
| 3 samples | 40000 | 0.4289 | 0.9983 | 0.5132 | +0.0843 |
| 4 samples | 40000 | 0.5203 | 0.9998 | 0.6069 | +0.0866 |
| 8 samples | 40000 | 0.7667 | 1.0000 | 0.8304 | +0.0637 |
| contiguous burst 2 | 40000 | 0.1764 | 1.0000 | 0.2313 | +0.0549 |
| contiguous burst 4 | 40000 | 0.1945 | 1.0000 | 0.2558 | +0.0613 |
| contiguous burst 8 | 40000 | 0.2224 | 1.0000 | 0.2891 | +0.0667 |
| level inverted to EOP | 40000 | 0.1722 | 1.0000 | **0.9719** | **+0.7997** |

**This is the table that decides it.** On the dominant mode — one corrupted
sample — parity takes detection from 17.1 % to 22.2 %. CRC-16 takes it to
100 %. Undetected-corruption probability per single-glitch event:

| check | undetected |
|---|---|
| none (V-USB, Grainuum, LemcUSB) | 0.8286 |
| **parity** | **0.7780** |
| CRC-16 | **0.0000** |

Parity removes 6 % of the residual. The CRC removes all of it. The one place
parity is worth having outright is a sustained level inversion running to EOP,
where it goes from 17 % to 97 % — a receiver-threshold or common-mode fault
rather than transient noise.

---

## 4. The cost

### 4.1 In the bit cell: zero, and the word is exact

Every cell is padded to **exactly 16 cycles** by the `CELL` macro, whatever the
pipeline segment inside it costs, and the macro raises `.error` if a segment
goes over. So a cheaper check does not make a cell shorter — it makes the cell
carry more `nop`. "Zero cycles in the bit cell" is therefore true by
construction for all three settings, and the thing actually worth reporting is
**which cells changed**, which is none.

Measured on the assembled object, `--exec flash --ioport r7 --flashdata r4
--budget 16`, identical numbers at all three settings:

| cell | cycles | | cell | cycles |
|---|---|---|---|---|
| `usb_rx_cell0` | 16..18 | | `usb_rx_cell4` | 16..18 |
| `usb_rx_cell1` | 16..18 | | `usb_rx_cell5` | 16..20 |
| `usb_rx_cell2` | 16..18 | | `usb_rx_cell6` | 16..18 |
| `usb_rx_cell3` | 18..20 | | `usb_rx_cell7` | 16..18 |

(The 18 on cell 3 and the 16..18 spread are the flash cost model's existing
figures, recorded in `engine16_merged.md`; nothing here moved them.)

Stronger, and checked rather than argued: at `USB_RX_CHECK=2` the file
assembles **bit-for-bit identically** to the engine before this change —
`objdump -d` of the two objects differs only in the filename line. The default
build is not a re-derivation of the old engine, it *is* the old engine.

### 4.2 What the segments cost

Per wire byte, from the same tool, reading the untimed flush copies where the
segments are not padded:

| segment | CRC-16 | parity | none |
|---|---|---|---|
| `SEG4` | 9 | **4** | 0 |
| `SEG6` | 10 | **10** | 0 |
| total | **19** | **14** | 0 |

So parity returns **5 cycles per wire byte of slack** to the cell that carries
`SEG4`. It does not shorten the cell; it shortens the *flush*, which is where
the turnaround budget lives (§5).

The parity segments, in full:

```asm
	SEG4:  mov r2,r3 / uxtb r2,r2 / ands r2,r1 / mov r8,r2        (4)
	SEG6:  mov r1,r8 / lsrs r2,r1,#4 / eors r1,r2
	       lsrs r2,r1,#2 / eors r1,r2 / lsrs r2,r1,#1 / eors r1,r2
	       mov r2,r10 / eors r2,r1 / mov r10,r2                   (10)
```

Two things make it fit with no new registers and no new gating. The byte has to
be taken in `SEG4`, because `SEG5` shifts it out of `r3`; `r8` is free in
exactly that window, which is where the CRC build parks its table value. And
`r1` — the commit mask — is dead after `SEG5`, so `SEG6` has two scratch
registers and the parity fold is a plain halve-and-XOR chain: no table, no
branch, no memory access. A zeroed park folds to 0, so the step needs no commit
mask of its own, and the `count >= 3` gate the CRC needs is not reproduced at
all, because §1.3 makes SYNC and PID a known constant.

### 4.3 The free-er variant that was found, measured, and rejected

The brief asked for parity that falls out of state the engine already has.
There is one, and it costs **nothing at all**, not even 14 cycles.

Over any run of `C` sampled cells, `sum(d_i) = C - T` where `T` is the number of
line transitions, and `T mod 2 = s_start XOR s_end` because each transition
flips the level. Stuff bits are 0s and add nothing. So

```
parity(all decoded bits) = (C + s_start XOR s_end) mod 2
```

and the engine has both terms at EOP for free: cells run 8 per chain iteration,
so `C mod 2` is just the index of the cell that sampled SE0 (which `rx_eopK`
already encodes in `r14`), and `s_end` is bit 0 of the wire packer `r5`.
Verified: the identity holds on 20 000 packets, 0 failures, and the resulting
tail verdict is 0 on every valid packet.

It was rejected on measurement, not on elegance. It computes the parity of the
whole decoded bit stream including any trailing bits that never became a byte,
where the byte-XOR form computes the parity of the bytes actually accepted, and
the byte-XOR form detects strictly more:

| error on the wire | free only | parity, tail identity (0 cyc) | parity, byte XOR (14 cyc) |
|---|---|---|---|
| 1 sample | 0.1714 | 0.1905 | **0.2220** |
| 2 samples | 0.3173 | 0.3492 | **0.3943** |
| contiguous burst 4 | 0.1945 | 0.2139 | **0.2558** |
| level inverted to EOP | 0.1722 | **1.0000** | 0.9719 |

Since the 14 cycles cost nothing in the cell and nothing in the turnaround (they
are less than the CRC's 19 they replace), buying 3 detection points with them is
free. **`SEG4`/`SEG6` byte-XOR is what is implemented.** The identity is
recorded here because it is the cheapest parity that exists on this engine and
someone will re-derive it otherwise.

---

## 5. Ordering, and the turnaround

### 5.1 The no-false-ACK property is preserved, structurally

`turnaround.md` and `ENGINE16_CATALOG.md` A-9 state the property as a claim
about instruction order, not about probability: the instruction that puts the
ACK PID on the wire is downstream in the control flow of the integrity verdict.
The parity test occupies **the same position in `.Ldata`** the residue test
occupied, branching to the same `.Lusb_done` on failure:

```asm
	cmp     r0, #4
	blo     .Lusb_done
	mov     r1, r10		/* CRC16: mov / ldr =0xB001 / cmp / bne */
	lsrs    r1, r1, #1	/* parity: C = accumulated parity       */
	bcc     .Lusb_done
	...                     /* only this path reaches usb_pid_handle_data */
```

So the ordering is unchanged and a false ACK remains structurally impossible.
What changes is the strength behind it, and §3.4 is that number: on a single
wire glitch the verdict is right 22 % of the time instead of 100 %. Any note
that repeats "cannot false-ACK" for a parity build must carry that sentence
with it.

### 5.2 The turnaround gets *better*, by 12 cycles

Parity does not merely avoid worsening the 2..6.5 bit-time reply window — it
improves it, because the flush path is where the CRC's per-byte work is paid
untimed. Measured deltas, same tool, same flash cost model:

| | CRC-16 | parity | delta |
|---|---|---|---|
| `rx_flush4` (`SEG4` in the byte in flight) | 9 | 4 | **−5** |
| `rx_flush7` block (partial byte + tail) | 289..342 | 282..335 | **−7** |

The −7 is −5 for the second `SEG4` copy in the partial-byte path and −2 for the
tail test, which is 3 instructions (`mov` / `lsrs` / `bcc`) against the CRC's 4
(`mov` / `ldr` literal / `cmp` / `bne`).

Applied to `turnaround.md` §5's worst case (SE0 in cell K=1), which is τ+114
against a τ+124 deadline:

| | worst case | bit times after SE0→J | conformant for |
|---|---|---|---|
| CRC-16 | τ+114 | (90+A)/16 | A ≤ 14 |
| **parity** | **τ+102** | **(78+A)/16** | **A ≤ 26** |
| none | τ+71 | (47+A)/16 | A ≤ 57 |

`A` is the transmitter's arm-to-first-edge cost, and `turnaround.md` §5.1 calls
it "the whole question". Parity nearly doubles the budget for it. That is a real
benefit and it is the one argument for parity that does not depend on error
detection at all.

## 6. Tokens and CRC5 — the argument does not transfer

Tokens carry CRC5 over eleven address/endpoint bits, and the engine checks them
separately, in the untimed tail, against residue `0x06` (`.Ltoken`). The
question is whether the `(x+1)` trick applies there too. It does not, and the
reason is one line of arithmetic:

```
g5 = x^5 + x^2 + 1     — three terms, an odd count, so g5(1) = 1
(x+1) does not divide g5                              [computed]
```

Consequence, verified exhaustively over all 2048 legal token fields: the set of
parities of `11 bits || CRC5` is `{0, 1}` — **both values occur**. Parity is not
constant on the token codeword set, so it is not a check there at all. It is not
"weaker"; it is meaningless.

Nor is there anything to save. `T_CRC5` is 16 halfwords — **32 bytes** — and the
two `bl .Lcrc5_byte` calls run in the untimed tail, not in a bit cell. The token
path is already the cheap one. **Leave it alone.** `USB_RX_CHECK` deliberately
does not touch it: at every setting, including `USB_RX_CHECK_NONE`, tokens are
still CRC5-checked, because a token accepted with a corrupted endpoint field
addresses the wrong endpoint and `F-3`'s bound is the only thing standing behind
it.

---

## 7. The knob, and the linked sizes

### 7.1 `USB_RX_CHECK`

Defined in `engine16_merged.S`, overridable from `usb_config.h` — which the
engine already includes through its `__has_include` block, the same route
`ENDPOINTS` and `PY32_HSICAL_ENABLE` take. An out-of-range value is an
assembly-time `#error`.

| value | name | what is checked | detection, 1 wire glitch |
|---|---|---|---|
| **2** | `USB_RX_CHECK_CRC16` | full CRC-16 residue against `0xB001` | **1.0000** |
| 1 | `USB_RX_CHECK_PARITY` | the `(x+1)` factor of `g16` | 0.2220 |
| 0 | `USB_RX_CHECK_NONE` | nothing (what V-USB, Grainuum, LemcUSB ship) | 0.1714 |

**2 is the default**, and stays the default. Setting it in a build:

```c
/* usb_config.h */
#define USB_RX_CHECK 1          /* parity only */
```

Tokens are CRC5-checked at every setting (§6). The structural checks of §2 run
at every setting.

### 7.2 Linked sizes

`demo_gamepad`, PY32F003x4, built by `INTEGRATION_BUILD.md`'s recipe. Both rows
of the table are real builds, not estimates; the `PY32_HSICAL_ENABLE=0` column
reproduces the recorded baseline of 344 B RAM / 4052 B flash exactly.

| `USB_RX_CHECK` | RAM | FLASH (cal on) | FLASH (cal off) | `usb_rx_engine16` |
|---|---|---|---|---|
| 2 — CRC-16 | 344 B | 4316 B | **4052 B** | 1030 B |
| 1 — parity | 344 B | 4292 B (−24) | 4028 B (**−24**) | 1010 B (−20) |
| 0 — none | 344 B | 4224 B (−92) | 3960 B (**−92**) | 944 B (−86) |

### 7.3 The 512 bytes do not go away, and this is the important caveat

The obvious reason to want a parity build is to delete `T_CRC16`. **It is not
deleted, and it cannot be from here.** The table lives in `engine16_tx.S`
(`.Ltab_crc16`, asserted at RX base + 512 and TX base + 256) and the
**transmit** engine still needs it, because the host verifies the CRC-16 the
device sends. A receive-side check is a choice; a transmit-side CRC is not.

So the honest saving from `USB_RX_CHECK=1` is **24 bytes**, not 536. The 512
only becomes available if the TX engine also stops using the table — which is
`CRC_ALTERNATIVES.md` §2.6 (move the TX CRC into buffer-fill, as LemcUSB
already does) plus §2 (the closed form), and neither is in this file's scope.

A build that wants both is `USB_RX_CHECK=1` *and* that work, and then the
arithmetic is −536 B for a receive check worth 22 % against 100 %. That trade
should be made deliberately, with §3.4 in front of the person making it.

---

## 8. Verdict

**The owner is right that parity deserved a candidacy, and the previous
dismissal used the wrong baseline.** Parity is a correct partial CRC-16 check
(§1), it is free in the timed path and cheaper than the CRC in the untimed one
(§4), and it improves the turnaround by 12 cycles (§5.2). Against no check at
all it is strictly better on every row of every table. It is implemented, it is
selectable, and it is documented.

**And it should not be the default, for a reason that is about this wire and
not about parity.** USB is NRZI and this engine samples once per cell, so a
single line glitch arrives as two adjacent flipped data bits — measured 97.5 %
of the time (§3.2) — and weight 2 is precisely parity's blind spot. The
argument that made parity attractive was "single-bit errors dominate, and
parity catches all of them". The first half is true of the wire and the second
of the data domain, and NRZI sits between them and breaks the syllogism. What
survives is +5 detection points on the dominant mode, against the CRC's +83.

**What it is good for**, concretely:

* **A bootloader or size-floor build** where `T_CRC16` has already gone with the
  TX rework — `USB_RX_CHECK=1` there is 536 B cheaper than the CRC and strictly
  better than the `USB_RX_CHECK=0` that such a build would otherwise take. This
  is the case the owner made and it stands.
* **A turnaround-limited build.** If `A` (the transmitter's arm cost) lands
  between 15 and 26, parity is the difference between conformant and not
  (§5.2), and that has nothing to do with error detection.
* **Bad-cable / common-mode faults**, where the failure is a sustained level
  inversion rather than transient noise: parity catches 97 % of those against
  the structural checks' 17 %.

**What it is not good for:** being the receive check on a general build. At
24 MHz the CRC-16 fits, `engine16_merged.md` ledgers it at 19 cycles inside
cells that have the slack, and it is the difference between a device that
cannot accept a corrupted packet and one that accepts four out of five.

**One line for `CRC_ALTERNATIVES.md` §11**, replacing the "last resort above
no check" entry: *parity is a legal, free, selectable partial check that adds
5 detection points over the structural checks on this NRZI wire and 80 on a
sustained inversion; it is `USB_RX_CHECK=1`, it is not the default, and the
reason it is weak here is NRZI error doubling, not the algebra.*

## 9. Reproducing every number

Everything above comes from four scripts and three builds. Nothing is asserted
from memory.

**The model.** A bit-accurate encoder/decoder — `stuff` / `nrzi_encode` /
`nrzi_decode` / `unstuff` / byte assembly — self-tested before use: 5000
packets round-trip byte-identically, all-`0xFF` payloads exercise stuffing on
2000/2000 trials, and every valid data field has CRC-16 residue `0xB001` and
even parity.

```python
def crc16_bitwise(data, init=0xFFFF):
    c = init
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ (0xA001 if c & 1 else 0)
    return c                       # crc16_bitwise(b"123456789") == 0x4B37

def usb_data_field(payload):       # the CRC-covered field, as transmitted
    c = crc16_bitwise(payload) ^ 0xFFFF
    return bytes(payload) + bytes([c & 0xFF, (c >> 8) & 0xFF])

def nrzi_decode(samples, start=1): # data 1 == no transition
    prev, out = start, []
    for s in samples:
        out.append(1 if s == prev else 0); prev = s
    return out
```

**§1 algebra** — GF(2) polynomial multiply/divide on ints:
`(x+1)*(x^15+x+1) == 0x18005 == g16`; `x^15+x+1` has no factor of degree ≤ 7;
`g5 = 0x25` has an odd term count so `(x+1) ∤ g5`; parities of all 2048 token
fields are `{0,1}`; parities of all 16 legal PID bytes are `{0}`.

**§3 detection** — 40 000 trials per row, payloads 1..8 bytes,
`random.Random(11)`. Domain A injects on `usb_data_field(p)`; domain B injects
on `nrzi_encode(stuff(bits(PID||field)))` and decodes. Free checks modelled as
the four in §2. A trial whose received field is unchanged is discarded.

**§4 cycles** —

```
arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb \
    -DUSB_RX_CHECK=<0|1|2> -c doc/py32/engine16_merged.S -o /tmp/x.o
python3 tools/engine16_cyc.py /tmp/x.o --exec flash --ioport r7 \
    --flashdata r4 --budget 16
```

and, for the bit-identity claim at setting 2, `objdump -d` of that object
against `objdump -d` of the engine before this change: the only differing line
is the filename.

**§7 sizes** — `INTEGRATION_BUILD.md`'s recipe: `demo_gamepad`,
`make -f ../Makefile.py32 MCU_TYPE=PY32F003x4`, with `.section .datacode`
changed to `.section .text.engine16` and the `EXTI2_3_IRQHandler` alias added.
`USB_RX_CHECK` is set in `usb_config.h`. **The engine objects are not built
under `Build/`** and `make clean` does not remove them, and `usb_config.h` is
not a listed prerequisite of the `%.o: %.S` rule — so
`rm -f demo_gamepad/rv003usb/*.o` between settings, or every configuration
links the same stale object and reports the same size. That trap cost a
measurement here and is recorded so it does not cost another.
