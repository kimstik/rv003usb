# The arm gate — never drive for a packet nothing is owed to

> **Headline.** `ENUMERATION_SIM.md` §4.1–4.3 found three shapes that made the
> in-ISR response path turn the pin drivers on for a packet the device owed
> nothing to; `usb_bus_fuzz.py` found that the arm actually fires in **five**
> shapes, 25 times in 180 packets of random traffic. All five are gone. The
> engine now decides whether to arm from three things it knows at the EOP stub
> — the emitted byte count, the current packet's PID, and the raw wire packer
> — and the decision is made **before `TBARM`**, so a refusal is silence, not
> a shorter collision.
>
> The cost is 11 cycles on the DATA→ACK deadline path (τ+107 → **τ+118**
> against τ+124) and 16 on the IN→DATA one (τ+94 → **τ+110**). Every timed
> cell is still exactly 16 cycles in all six build configurations, all four
> models are unchanged, the enumeration still verifies 186 descriptor bytes,
> and the phase lock's measured band is untouched.
>
> **The 36 remaining violations are not the engine's** and are left alone; §6
> says what they are.

Reproduce:

```
python3 tools/bus_arm_gate.py                       # the nine shapes, one per line
python3 tools/bus_arm_gate.py --source <old>.S      # and against any other engine
python3 tools/usb_bus_fuzz.py --seqs 40 --len 6     # the invariant over random traffic
python3 tools/usb_enum_sim.py                       # 186 bytes, and the turnaround
```

---

## 1. The invariant, before and after

`BUS_INVARIANT.md` states it once:

> the device may drive the bus only when it owes a response to a packet that
> was addressed to it and that it accepted.

`tools/usb_bus_fuzz.py` checks it over random sequences, with entitlement
computed from the specification and from what the host sent. Both tables below
are `--seqs 40 --len 6` on the same seed; the run stops at 61 recorded
violations, which is why the packet totals differ.

**Before** (`c580444`):

```
packets driven at the device: 180
INVARIANT VIOLATIONS: 61

the device answered             times past tau+124   the rule
a DATA with no token               36       36   S8.5.3: no transfer was armed
a token for another address        10        0   S8.3.2.1 / S9.4.6: not ours
a handshake                         6        0   S8.4.4: never answered
an IN for a dead endpoint           4        0   S8.4.1: endpoint out of range
unclassified                        4        0   -            (OUT a0 e15)
a SOF token                         1        0   S8.4.3: carries no response
```

**After:**

```
packets driven at the device: 240
INVARIANT VIOLATIONS: 51

the device answered             times past tau+124   the rule
a DATA with no token               51       51   S8.5.3: no transfer was armed
```

**Every violation inside the deadline — all 25 of them, in five shapes — is
gone.** What is left is one class, all of it past τ+124, all of it the C
layer's (§6). A wider run agrees: `--seqs 120 --len 8 --seed 7` gives 61
violations in 316 packets, every one of them that same class.

`tools/bus_arm_gate.py` asks the same question of nine specific shapes, with
the arm state set up by hand so each line isolates one reason to refuse. The
count is BSRR writes, which is the only way this engine can put a level on the
wire:

```
packet, and the state it arrives in                     before   after
ACK handshake, ACK arm owed, usb_rxbuf+3 = 0x69 (IN)         9       0
ACK handshake, ACK arm owed, usb_rxbuf+3 = 0x2D (SETUP)      9       0
ACK handshake, ACK arm owed, usb_rxbuf+3 = 0xC3 (DATA0)      9       0
ACK handshake, ACK arm owed, usb_rxbuf+3 = 0xD2 (ACK)        9       0
SOF token, ACK arm owed                                     10       0
OUT token a0 e15, ACK arm owed                              10       0
IN token a17 e1, endpoint 0 armed for a0 e0                 11       0
IN token a0 e2 (out of range), endpoint 0 armed             10       0
IN token a0 e0, endpoint 0 armed for it  (THE CONTROL)      27      27
```

The control is what stops this being a fix that simply stops answering.

---

## 2. Finding 1 — the device answers the host's own ACK

### 2.1 The mechanism

`EOPSTUB` had two arms and neither of them looked at the packet that had just
ended:

```
    ldrb  r2, [r9, #TB_OWED_OFS]   -> Design B's ACK path
    ldrb  r2, [r9, #1]             -> the PID, for Design B's IN path
```

The receive pipeline commits wire byte N during byte **N+1's** cells: `SEG3`
stores it in cell 3 and `SEG5` advances the count in cell 5. A handshake is
SYNC + PID + EOP — there is no byte N+1 — so when the EOP stub runs, `rxbuf+3`
still holds the **previous** packet's PID. After an IN token that byte is
`0x69`, so every ACK that closes an IN transaction entered the IN response
path; and `TB_OWED` says only "a SETUP or OUT token came before", not "the
packet that just ended is the DATA that token promised", so a handshake, a SOF
or another token arriving in between armed the ACK path as well.

The byte *is* committed eventually — from a clean buffer the run ends with
`80 d2` in place, and it still does — so nothing is wrong with the decode. The
decision simply read it too early.

### 2.2 The options

| option | cost on the DATA→ACK path | verdict |
|---|---|---|
| **A. `r12 >= 2`, the emitted byte count** | `mov r2,r12` + `cmp` + untaken `blo` = **3** | chosen |
| B. clear `rxbuf+3` at every ISR exit | 0 on the timed path | rejected |
| C. finish enough of the flush to commit the PID first | ≥ 20 (`SEG0..SEG3` of the byte in flight) | rejected |

**Why A.** A handshake commits exactly one byte — SYNC — and every longer
packet at least two, so `r12 >= 2` *is* the statement "`rxbuf+3` belongs to
this packet", and it is read out of a register the engine maintains rather
than out of the buffer whose timing is the defect. It is exact at every K: a
handshake is 16 wire bits with no stuffing (the longest run of 1s a legal PID
can present is four, and SYNC adds one, against a rule that inserts after
six), so its EOP always lands in cell 0 and the count is always 1.

**Why not B.** It is free, and it is a discipline rather than a structure:
every exit would have to clear the byte, and `usb_rx_keepalive` deliberately
returns early without touching `usb_rxbuf` — a keep-alive EOP between an IN
token and the host's ACK would walk straight back into the defect. A
correctness property that depends on eight exits agreeing is the shape of hole
this whole port keeps finding.

**Why not C.** It buys nothing A does not, and it spends the deadline.

### 2.3 The second half: the ACK arm needs the PID too

`r12 >= 2` makes `rxbuf+3` trustworthy; it does not make `TB_OWED` mean what
the arm needs. The fuzzer's `SOF token` and `OUT a0 e15` rows are exactly that:
a packet that is not the promised DATA, arriving while `TB_OWED` is set.

So the ACK arm now also requires the PID to be DATA0 or DATA1:

```
    adds  r2, r2, #5     1   \  Z <=> (pid & 7) == 3
    lsls  r2, r2, #29    1   /
    bne   4f             1   not taken on the DATA path
```

Three cycles, no taken branch, and it excludes the DATA2/MDATA aliases
(`0x87`, `0x0F`) that share the type field — the same two `CRC_ROUND2.md` §1.4
singles out, and the same test `TBGATE_C` makes eight bit times later.

### 2.4 Measured

`tools/bus_arm_gate.py` rows 1–6, above: nine BSRR writes became zero for every
predecessor byte, and for the SOF and the stray OUT token. `usb_enum_sim.py`'s
own probe, which is written from the other side:

```
  predecessor  rxbuf+3 is     drives?  flush chain
  IN token     0x69 (IN)      no       rx_flush0
  SETUP token  0x2D (SETUP)   no       rx_flush0
  DATA0        0xC3 (DATA0)   no       rx_flush0
  ACK          0xD2 (ACK)     no       rx_flush0
```

Every predecessor takes `rx_flush0`, the ordinary path, and the buffer still
ends with `80 d2` — the handshake was decoded in full, it was just not
answered.

---

## 3. Findings 2 and 3 — an IN token for another device, or a dead endpoint

### 3.1 The mechanism

`TBARM` enables the drivers and the SYNC cells start toggling; `TIG6` (the
endpoint bound) is in cell C6 and `TIG9` (the address, endpoint and CRC5, as
one compare against the armed pattern) is in cell C7 — **eight bit times after
the drivers are already on**. `audit_discarded.md` F-7 fixed this class in the
ACK direction, where the wrong answer was silence; in the IN direction the
wrong answer drives.

### 3.2 The options, and what each costs

The test that settles it is "is this token one this device has a packet
rendered for". The existing form of that test reads `rxbuf+4..5` — the token's
address, endpoint and CRC5 as one halfword. **That is why it could not move:**
`rxbuf+5` is the token's last byte, and at K ≥ 1 it is not in the buffer at
all when the stub runs. Getting it there costs the partial byte's whole front
end.

| option | extra cycles before the first wire edge | first edge | verdict |
|---|---|---|---|
| A. leave it where it is, but release the drivers instead of driving the abort | 0 | τ+94 | rejected: still ~4 bit times of driving |
| B. move `TIG5..TIG9` ahead of `TBARM`, K = 0 only | +20 | τ+114 | rejected: fixes a third of the address space |
| C. same, at every K — needs `SEG0`(7) + `SEG1`(11) + `SEG2`(9) + `SEG3`(9) of the partial byte first, then `mov r1,r9` + `TIG5..TIG9` (20) | +56 in place of the 19 that fits | **≈ τ+148** | **rejected: 24 cycles past τ+124** |
| **D. compare the raw wire packer, `r5`, against a learned key** | +18..19 | **τ+110** | **chosen** |

Option C is the costed refusal the brief asked for, and it is worth stating as
arithmetic rather than as a judgement: the flush cannot be shortened (the two
wire bytes that carry the token are the same irreducible core `turnaround.md`
§5.2 identifies for the CRC16), the window is 64 cycles wide, and 56 does not
fit in it. **A buffer-based address test cannot be moved ahead of the arm at
K ≥ 1 inside the deadline.**

Option A deserves a sentence because it is what a smaller change would have
done. Releasing MODER at the first cell where the flush has committed the
token — cell C3 at the earliest — still drives three SYNC bit times. Those
three bit times are the same J↔K pattern every device on the segment drives
(`turnaround.md` §6.5), so they are not a value conflict; but they are still
the device driving a bus it was not addressed on, and the brief's target is
zero, not small.

### 3.3 Option D: the whole token is already in a register

`CELL`'s capture step is `adcs r5, r5` — a 32-bit shift register of raw D+
samples — and `.Lprime` seeds it with `1`, the last SYNC sample, which is a K.
So at the EOP stub

```
    r5 = sentinel(1) << n  |  the n wire bits of the packet after SYNC
```

with NRZI and bit stuffing *included*, oldest at the top. A token is 24 data
bits after SYNC, so `n = 24 + S` where S is its stuff count. **One 32-bit
compare therefore settles the PID, the address, the endpoint, the CRC5, the
stuffing and the length together**, and it reads nothing from the buffer — so
it is affordable at every K, which is exactly what option C is not.

The gate is `TIGATE`, one copy per K, in front of the IN flush chain:

```
    ldr   r6, =usb_in_arm            2   r6 is dead from SE0 on, and TBARM's
    ldr   r2, [r6, #TI_R5_OFS]       4   anyway
    cmp   r2, r5                     1   THE WHOLE TOKEN
    beq   1f                         1
    adds  r6, r6, #TI_REC_SIZE       1   ... once per endpoint
    ...
1:  ldrh  r2, [r6, #TI_PAT_OFS]      4   and is a packet armed for it?
    cmp   r2, #0                     1
    beq   2f                         1
    b     ti_flush\idx               2-3 answer it
2:  ... suppress, then the ordinary tail, and not one edge on the wire
```

Nothing below `TBARM` runs unless one of those compares matched, and `TBARM` is
downstream of the branch.

### 3.4 Is the key canonical?

An exact compare is only sound if what it compares has exactly one form. Bit
stuffing and NRZI are deterministic functions of the data bits and of the state
the SYNC field leaves behind, and the SYNC field is the same eight bits at the
head of every packet. **So a given (PID, address, endpoint) has exactly one
wire form, and a token that is legal cannot arrive with a different stuffing
pattern than the learned one.** There is no "same token, other encoding" case
to handle.

Measured over all 128 addresses × 16 endpoints, computed from the
specification in `tools/bus_arm_gate.py` rather than from the device:

```
   0 stuffed bits -> 24 samples : 1858 tokens
   1 stuffed bit  -> 25 samples :  187 tokens
   2 stuffed bits -> 26 samples :    3 tokens
   distinct r5 keys: 2048 of 2048 tokens - injective
```

So the sentinel lands at bit 24, 25 or 26 and the key fits `r5` with five bits
to spare; three stuffed bits (bit 27) is the bound the field allows and it is
never reached. The count of samples is *in* the key — the sentinel's position
is the length — so a packet shorter or longer than the token can never compare
equal to it, for any length up to 31 samples after SYNC.

Above 31 the sentinel has been shifted out and `r5` is the last 32 samples
rather than the whole packet, so a long packet is compared as a 32-bit hash.
Three things bound that:

* a DATA packet cannot reach this gate at all — the stub sends type-3 PIDs to
  the ACK arm;
* a collision needs a specific 32-bit value, and the bits above where a token's
  sentinel would sit must all be zero, which is six or more consecutive J
  samples — at the edge of what `§7.1.9` even permits;
* and if one happened, `TIG2`'s `count == 4` in cell C4 still rejects it, five
  bit times before any PID could be emitted. The worst case is the
  SYNC-then-abort behaviour that existed before this change, not a wrong
  response.

### 3.5 Where the key comes from

`r5pat` is **learned from the wire**, in exactly the place and on exactly the
branch where `tokpat` already is — `.Lt_in`, after the CRC5 has been checked,
the endpoint bounded and the address filtered, so the word it installs is a
verified "IN, this device, this endpoint". `design_b_in.md` §6's argument for
learning rather than computing applies unchanged, and doubly: a computed key
would have to be re-derived on every `SET_ADDRESS`.

The word itself is saved at `rx_flush7`, where `r5` is still the packer (`CELL`
was its last writer and no `SEG` touches it) and every ordinary-path K falls
through. The Design B epilogue `.Ltb_sent` **zeroes** it instead, because there
`r5` is a BSRR word: unknown, not stale. Zero is not a value the gate can ever
match, since the sentinel bit is always set, so a record keyed with it is
simply never matched and the next token for that endpoint re-learns.

The gate needs `tokpat` non-zero as well as `r5pat` matching, and that is not
belt-and-braces: `r5pat` is installed on the branch that *disarms*, and the
render that re-arms `tokpat` may not happen. Requiring both is what makes the
gate say "armed for this token" rather than "recognised".

### 3.6 The reject must not answer late either

A token the gate declines still goes up the ordinary tail — that is what
renders the packet the **retry** will be answered with, which is
`design_b_in.md` §6's whole mechanism — but `usb_send_data` must not then
transmit it. Measured, that transmission lands at **τ+873**, 53 bit times after
SE0→J against the 16–18 the host waits: a packet arriving after the host has
already moved on is a worse collision than the abort it replaced. So the gate
sets `usb_tx_suppress`, exactly as the abort path did through `.Ltb_sent`, and
`.Lusb_done` clears the flag if nothing consumed it — a token for another
device reaches no `usb_send_data` at all, and a suppression must never be spent
on somebody else's transmission.

The visible behaviour of the first IN token carrying a pattern the device has
not seen is therefore **silence** where it used to be a deliberately corrupt
frame. The host times out and retries; the retry is answered inside 6.5 bit
times. That is the same one-transaction cost `design_b_in.md` §6 already
records, paid without ~23 bit times of driven abort.

---

## 4. What it costs, measured

### 4.1 The EOP stub

| path | before | after | Δ |
|---|---|---|---|
| DATA → ACK | 11..12 | 21..22 | +10..11 |
| IN → DATA (stub) | 23 | 20..21 | −2..3 |
| IN → DATA (`TIGATE`, endpoint 0) | — | 18..19 | +18..19 |

Flash-resident, `USB_RX_CHECK=CRC16`, each taken branch priced at 3 and each
not-taken at 1, from `tools/engine16_cyc.py` on the linked object. The IN stub
got *cheaper* — the `TB_OWED` byte is no longer loaded on that path — which
pays back three of the gate's cycles.

### 4.2 The turnaround, at every K

`tools/usb_enum_sim.py`, which searches for payloads by stuff count so that all
eight EOP entries are reached, and labels each row with the flush chain read
out of the execution trace.

**DATA → ACK**

| K | before | after | flush |
|---|---|---|---|
| 0 | τ+100 | **τ+111** | `tb_flush0` |
| 1 | τ+107 | **τ+118** | `tb_flush1` |
| 2 | τ+96 | **τ+107** | `tb_flush2` |
| 3 | τ+87 | **τ+98** | `tb_flush3` |
| 4 | τ+78 | **τ+89** | `tb_flush4` |
| 5 | τ+69 | **τ+80** | `tb_flush5` |
| 6 | τ+70 | **τ+81** | `tb_flush6` |
| 7 | τ+64 | **τ+75** | `tb_flush7` |

Uniformly +11, which is what §4.1 predicts — the stub is the only thing that
moved and it is common to every K. **Worst τ+118 against the τ+124 deadline:
6 cycles of margin** (was 17). **Best τ+75 against the τ+60 floor: 15** (was
4) — the change spends the ceiling and buys the floor, and the floor was the
tighter of the two before. Every value is constant across four payloads per K;
there is still no jitter in this path.

**IN → DATA**

| K | before | after | token | flush |
|---|---|---|---|---|
| 0 | τ+94 | **τ+110** | addr 0, endp 0 | `ti_flush0` |
| 1 | τ+94 | **τ+110** | addr 63, endp 0 | `ti_flush1` |

**Worst τ+110 against τ+124: 14 cycles.** K reaches only 0, 1 and 2 over all
128 addresses × 16 endpoints, and only 0 and 1 on the answering path, so those
two rows are the whole of it.

### 4.3 Everything that had to not move

| check | result |
|---|---|
| assembles, all six `-DUSB_RX_CHECK=0/1/2` × `-DUSB_ENGINE16_FLASH=0/1` | rc = 0 |
| every timed cell exactly 16 cycles, all six | **56 cells, minimum 16 in every one**, with `usb_tb_B7`, `usb_tb_P7`, `usb_ti_C7` and `usb_ti_T7` the only blocks reporting otherwise — the four known artifacts of a control-flow-blind tool, and no others |
| `tools/engine16_rx_model.py` | 415 packets, **0 failures**, all eight K |
| `tools/engine16_tx_model.py` | 6030 cases, **0 mismatches** |
| `tools/design_b_in_model.py` | 7322 cases, **0 mismatches** |
| `tools/prerender_check.py` | phase 1 **PASS**, phase 2 **PASS**, 0 problems |
| `tools/usb_enum_sim.py` | **186 descriptor bytes** over 10 control reads, 139 host packets, 3 retries — and **65** driven responses instead of 107 |
| `tools/engine16_rx_sweep.py --verify --jitter 0.6` | entry 0..37, sample band **9.00..11.88 of 16**, symmetric tolerance 0.155 %, **0 fails** — identical to `SAMPLE_POINT.md` §3.2 |
| footprint | text **+404 B** (`USB_RX_CHECK=1`) / **+408 B** (`=2`), bss **+4 B** (`usb_rx_wire`) |

The four cells that report other than 16 are the same four
`turnaround.md` §11.1 names. `usb_tb_P7`'s block grew from 84 to 91 because the
tool folds the untimed epilogue after the EOP into it and `.Ltb_sent` gained
three instructions there; the cadence inside the cell is unchanged.

One cell needed a fix that is worth recording, because it is the tool's failure
mode rather than the engine's. `.Lovf_a` — cell 5's overflow escape — had to
move in front of the EOP stubs, whose growth put it out of a `bhs`'s 254-byte
reach. A `.L` name is invisible to `engine16_cyc.py`, so its block merged into
`usb_rx_cell7`'s and the cell read 18..21 instead of 16..18 for no reason but
the label's name. It is `usb_rx_ovf_a` now. **An assembled ledger checks the
ledger; a symbol table is part of the instrument.**

### 4.4 Two measurements that changed for reasons that are not the engine's

**Coverage went down**, 52.6 % → 41.8 % (`tools/engine16_coverage.py`). That
battery stubs the C layer, so no arm record is ever filled; the gate correctly
declines every IN token and the whole IN chain goes cold. Before the gate the
stub armed on the PID alone and the chain ran as far as `TIG9` on its way to an
abort — warm code on a path that was the defect. `usb_enum_sim.py` and
`usb_bus_fuzz.py`, which run the real C layer, are what exercise that chain
now.

**The entry-latency window widened**, from `{7,8} ∪ [12,43]` to `[4,43]`
contiguous — and this is the *harness*, not the engine. `in_transaction`
treated "the device drove nothing" as a dead end, contradicting its own
docstring: a host that gets no response waits out §7.1.18's timeout and retries,
three attempts (§5.5.5). Before the arm gate that branch was unreachable — the
device always drove something, if only an abort — so nothing had exercised it.
The old engine, run under the patched harness, also completes at every latency
from 4 to 43. The holes were the harness abandoning a transfer a real host
would have retried, and they were reported in `ENUMERATION_SIM.md` §2.1 as the
phase lock's.

---

## 5. What the gate is, in one place

At `rx_eopK`, before anything drives:

```
    r12 >= 2                      the emitted byte count: this packet HAS a PID
                                  at rxbuf+3, because a handshake commits one
                                  byte and everything longer at least two
    (rxbuf[1] & 7) == 3           it is DATA0 or DATA1 - not a SOF, not a
       and TB_OWED                token, not a handshake, not DATA2/MDATA
                            ->    tb_flushK, the ACK

    otherwise, r5 == r5pat[e]     the whole token in one word: PID, address,
       and tokpat[e] != 0         endpoint, CRC5, stuffing, length
                            ->    ti_flushK, the DATA

    otherwise                ->   rx_flushK, and not one edge on the wire
```

Three registers and two RAM bytes, no buffer read that the packet has not
written, and every branch that leads to `TBARM` downstream of all of it.

---

## 6. What is left, and whose it is

**36 of the 61 violations in the "before" table, and all 51 in the "after"
one, are past τ+124 and belong to the C layer.** Probed directly: a bare DATA0
on a fresh machine with `TB_OWED` clear is answered with ACK at τ+359.
`rv003usb.c` acknowledges a data packet for which no SETUP or OUT token was ever
seen (§8.5.3). The engine is not at fault — the packet is well formed, so it is
passed up — and `rv003usb.c` is shared with the RISC-V original, so it is not
changed here. It is stated as out of scope, not as fixed.

`usb_enum_sim.py`'s remaining four findings are the same kind and are all
pre-existing:

* the device still answers the default address after `SET_ADDRESS`
  (§9.4.6) — `ENUMERATION_SIM.md` §4.4, and §4.5 explains why fixing it alone
  breaks enumeration;
* there is no STALL and no NAK anywhere (§9.4.3, §8.4.6) — §4.6;
* an OUT to an IN-only endpoint is ACKed (§8.4.5) — §4.6;
* `TB_OWED` spent by an intervening packet puts the ACK at τ+523 — §4.8.

And one behaviour this change deliberately did not touch: a DATA whose CRC16
fails is still answered with the deliberately corrupt frame `turnaround.md`
§6.4 designs, which holds the bus ~23 bit times. It is not a violation of the
invariant — a handshake **is** owed for that packet, the device simply declines
to give a valid one — and it is the documented design rather than an accident.
`ENUMERATION_SIM.md` §4.7 is right that it should be reconsidered alongside
these three; the argument for changing it is a different one (it is about
§8.4.5, not about entitlement) and it is not made here.

---

## 7. What this does not establish

* Model cycles, not silicon cycles. Every figure inherits
  `engine16_cyc.py`'s cost table.
* One entry latency per run, one clock, no jitter, no second device actually
  driving. Collisions are still *inferred* from the device driving outside its
  window, not observed as contention.
* `ENDPOINTS=2`, the gamepad demo. The `TIGATE` scan costs about 6 cycles per
  endpoint, so the IN→DATA first edge is τ+110 at two endpoints and would be
  about τ+128 at five — past the deadline. A build with more endpoints than
  four needs this measured again, and the natural fix if it binds is to key the
  scan rather than walk it.
* The gate refuses; it does not verify what is sent afterwards. `TIG1..TIG9`
  still run in the SYNC cells and still abort, and `usb_enum_sim.py` is what
  checks that the bytes on the wire are the right ones.
