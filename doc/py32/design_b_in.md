# Design B, the IN → DATA0/DATA1 direction

> **Status: built and measured.** The worst-case first response bit is
> **τ+109 = 5.31 bit times** after SE0→J, against a deadline of τ+124 and a
> requirement of 6.5, so the IN → DATA0/DATA1 path is **conformant at
> 24 MHz**. The DATA→ACK path is unchanged at τ+115. §7 has the measurement,
> §7.2 the three budget lines that did not survive, §8 the line of
> `turnaround.md` §7.3 that did not survive at all, §10 the cost this moves
> onto the ISR that follows the host's ACK, and §11 a defect it turned up in
> `engine16_tx.S`.

`turnaround.md` §11 built Design B for DATA→ACK and measured the first wire
edge at **τ+115 = 5.69 bit times**, conformant against τ+124 and 6.5. §11.8
records what was not built: **IN → DATA0/DATA1**. This note is that direction —
its budget first, because the budget survives whether or not the code does.

Conventions are `turnaround.md`'s. `merged:<n>` = `doc/py32/engine16_merged.S`,
`c:<n>` = `rv003usb/rv003usb.c`. τ is the cycle on which the `ldr r2,[r7,#16]`
that sees SE0 executes. The deadline is **τ+124** and the floor **τ+60**
(`turnaround.md` §2); the requirement is 6.5 bit times after SE0→J, and SE0→J
is at τ+24 nominal.

Every cycle figure below is `tools/engine16_cyc.py --exec flash --ioport r7
--flashdata r4 --budget 16` on the **linked** `demo_gamepad.elf` of
`INTEGRATION_BUILD.md`'s recipe (PY32F003x4, calibration on), block by block,
each taken branch priced 3 and each not-taken 1. Where a figure is arithmetic
over those blocks the arithmetic is shown.

---

## 1. Why IN is a different problem from ACK, in one paragraph

For DATA→ACK the speculative commit — "a response is owed" — was made by the
**previous** packet: a SETUP or OUT token to this device means the next packet
is a DATA and the next response is an ACK (`turnaround.md` §7.2). The EOP stub
therefore reads one pre-staged byte and branches, 11..12 cycles, and nothing
about the packet in flight is consulted. For IN there is no such predecessor.
"This packet is an IN token" is a property of **the packet in flight**, and the
only place it is legible before the response must start is `usb_rxbuf+1`, the
received PID byte — which is in RAM and costs 4 cycles to read from
flash-resident code. So the IN stub must make **two** RAM byte reads where the
ACK stub makes one, and that is 9 cycles added to the most expensive part of
the path.

The rest of this note is where those 9 cycles come from, and they come from a
place §8.3 and §11 never looked at: **a token needs no CRC16.**

## 2. The 17 cycles nobody had spent: a token flush has no CRC16

`tb_flush1..6` are the receive engine's own `SEG1..SEG6` and they measure

```
tb_flush1  11   SEG1   unstuff/append + the low-nibble lookup
tb_flush2   9   SEG2   append it; fetch the rxbuf base
tb_flush3  10   SEG3   bounded store + commit mask     (4 of it is a RAM strb)
tb_flush4  10   SEG4   SEG3_TAIL (3) + THE CRC16 TABLE STEP (7)
tb_flush5  11   SEG5   drop, advance, byte bound, CRC gate   (13 if it aborts)
tb_flush6  10   SEG6   COMMIT THE CRC16 STEP (10)
           --
B(1) =     61
```

`SEG4` is three cycles of `SEG3_TAIL` — restoring the bit count after `SEG3`'s
speculative `subs r0,#8`, and structural — followed by seven cycles of CRC16
table step. `SEG6` is ten cycles and is nothing but the CRC16 commit
(`merged:565-580`).

**A token is validated by CRC5 over its two address/endpoint bytes, not by
CRC16.** The receive tail proves it: `.Ltoken` (`merged:1671-1680`) computes
CRC5 from the buffer and never reads `r10`. So on a path that is answering an
IN token, `SEG4`'s CRC16 step and the whole of `SEG6` compute a residue that
nothing will ever look at.

Deleting them gives a token-only flush chain — call it `ti_flush1..6` —

```
ti_flush1  11   SEG1
ti_flush2   9   SEG2
ti_flush3  10   SEG3
ti_flush4   3   SEG3_TAIL only
ti_flush5  11   SEG5
ti_flush6   0   -
           --
B_in(1) =  44        against B(1) = 61
```

**17 cycles, and they are on the critical path twice** — once for the byte in
flight and once for the partial byte, though only the first is in front of the
first wire edge. `r8` is safe: `SEGA` writes it and `SEG1` reads it every wire
byte, and `SEG4`/`SEG6` only borrow it in between.

Per-K, `B_in(K) = sum of ti_flushK..6`:

| K | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 0 |
|---|---|---|---|---|---|---|---|---|
| `B(K)` (ACK) | 61 | 50 | 41 | 31 | 21 | 10 | 0 | 70 |
| `B_in(K)` | **44** | **33** | **24** | **14** | **11** | **0** | 0 | 51 |

## 3. The stub, and what the two reads cost

The ACK stub, unchanged and measured at 11..12 on its taken arm:

```
rx_eopK:  movs r2,#\unsampled        1
          mov  r14,r2                1
          mov  r2,r9                 1
          ldrb r2,[r2,#TB_OWED_OFS]  4     <- RAM byte, flash-resident code
          cmp  r2,#0                 1
          beq  1f                    1     NOT taken when armed
          b    tb_flushK             2-3
```

The IN arm has to be **downstream** of that test, or the ACK path pays for it
and `turnaround.md` §11's τ+115 regresses. So the IN path takes the `beq`
(3 taken) and then:

```
1:        mov  r2,r9                 1
          ldrb r2,[r2,#1]            4     <- the received PID byte
          cmp  r2,#0x69              1     IN
          bne  2f                    1     NOT taken on the IN path
          b    ti_flushK             2-3
2:        b    rx_flushK             2-3
```

IN stub, worst = `1+1+1+4+1+3 + 1+4+1+1+3` = **21**; ACK stub unchanged at 12.

## 4. The budget, worst case (K=1)

```
                                    ACK (built, S11)   IN (this note)
  detect  ldr + ands + taken beq          5                 5
  EOPSTUB                                12                21
  flush of the byte in flight        B(1) 61       B_in(1) 44
  head: 4 pad + NRZI 11 + SEG0 7
        + TBARM 13                       35                35
  first cell: eors + str                   2                 2
                                        ----              ----
  FIRST WIRE EDGE                    tau+115           tau+107
```

**τ+107 = (107−24)/16 = 5.19 bit times after SE0→J**, against a deadline of
τ+124 and a requirement of 6.5. **17 cycles of margin, 8 more than the ACK
path has.** The 9 cycles the second RAM read costs are paid for twice over by
the 17 the absent CRC16 returns, and that is the whole of why this direction
fits at all.

The floor needs checking too, and it is the tighter end. Best branch
resolution, K=6, `B_in(6) = 0`:

```
  4 + (1+1+1+4+1+2 + 1+4+1+1+2) + 0 + 35 + 2  =  tau+60
```

exactly on `turnaround.md` §2's `[τ+60, τ+124]` floor, with nothing to spare.
The ACK build padded `usb_tb_head` with 4 nops for the same reason (§11.3);
this path needs padding of its own in `ti_flush6`, and §7 below says how much
was actually assembled.

## 5. What the SYNC cells have to hold, and what is left over

Eight SYNC cells at 14 free cycles each = **112** (`turnaround.md` §11.2: a
SYNC bit is `eors r5,r6 / str r5,[r7,#BSRR]`, two cycles, not the transmit
engine's five). Into them go the rest of the partial byte's flush and the
gate:

```
  partial byte, SEG1..SEG6 without CRC16     44
  gate                                       45   (S6)
                                            ---
                                             89   against 112 -> 23 spare
```

against the ACK path's 61 + 36 = 97 of the same 112. Both fit; IN fits with
more room, again because of §2.

## 6. The IN gate

The ACK gate is 36 cycles and ends at the CRC16 residue, which is the
structural reason a false ACK is impossible (`turnaround.md` §11.5). The IN
gate has the same shape and a different last question. What it must establish
before a DATA0/DATA1 PID may be emitted:

| # | test | cycles | why |
|---|---|---|---|
| G1 | no sticky stuffing violation | 4 | `TBGATE_A`, unchanged |
| G2 | emitted count == 4, `rxbuf[0] == 0x80` | 12 | a token is exactly SYNC+PID+2; the `ldrh` is a RAM access, 4 |
| G3 | `rxbuf[1] == 0x69` | 2 | already true — the stub branched on it — but re-tested from the buffer rather than trusted |
| G4 | endpoint from bytes 2-3, bounded | 11 | `endp = (b2>>7) \| (b3&7)<<1`, `cmp #ENDPOINTS / bhs abort` |
| G5 | the armed record's pattern == the received `(b2,b3)` halfword | 12 | **address, CRC5, endpoint and "this endpoint is armed" in ONE compare** |
| G6 | load the response PID from the record | 4 | R5: nothing about which packet this is was decided at arm time |
| | | **45** | |

G5 is `turnaround.md` §4.1 taken literally and it is the load-bearing step.
The eleven token bits are address(7) + endpoint(4) and the CRC5 is a pure
function of them, so for a given device address and endpoint there is exactly
**one** legal 16-bit `(byte2,byte3)` value. Comparing the received halfword
against the one stored in that endpoint's arm record validates the CRC5, the
address and the endpoint together, in one `cmp`, and simultaneously answers
"is there a rendered packet waiting for this endpoint" — because the record's
pattern is written only when a packet is rendered into it.

It is strictly stronger than computing the CRC5 would be: a well-formed IN
token addressed to another device is rejected without any arithmetic, which is
`turnaround.md` §6.5's aliasing hazard closed rather than argued.

## 7. As built, and measured

`engine16_merged.S` and `engine16_tx.S`. Everything below is what the build
says. Where it disagrees with §1-6 the build wins and the line is named.

### 7.1 The measurement

**First wire edge, from τ, worst branch resolution, flash-resident,
`USB_RX_CHECK=CRC16`, on the linked `demo_gamepad.elf`:**

> **Superseded.** `audit_discarded.md` §F.6 measures this path at **τ+95**, 29 cycles of margin instead of 15. The table below is the build this section described.

| K (cell SE0 was sampled in) | first edge | bit times after SE0→J |
|---|---|---|
| 1 | **τ+109** | **5.31** |
| 2 | τ+98 | 4.63 |
| 3 | τ+89 | 4.06 |
| 4 | τ+79 | 3.44 |
| 5 | τ+76 | 3.25 |
| 6 | τ+65 | 2.56 |
| 7 | τ+78 | 3.38 |
| 0 | τ+97 | 4.56 |

**Against §2's two limits: the deadline is τ+124 and the worst case τ+109, so
there are 15 cycles of margin; the floor is τ+60 and the earliest edge, at K=6
with every branch resolving at its minimum, is τ+63, so there are 3. Against
the 6.5 bit-time requirement the worst case is 5.31. The IN → DATA0/DATA1
direction is conformant to §7.1.18 at 24 MHz.**

For comparison, the DATA→ACK path in the same image is **unchanged at τ+115**:
its stub still measures 12 and `tb_flush1..6` still 61. The IN direction is
**6 cycles faster than the ACK direction** it was supposed to be harder than,
and §2 is the whole reason.

The arithmetic, block by block, every figure printed by
`tools/engine16_cyc.py --exec flash --ioport r7 --flashdata r4` on the linked
image:

```
  detect      ldr + ands + taken beq                     5
  rx_eopK     movs 1, mov 1, mov 1, ldrb 4, cmp 1,
              TAKEN beq 3 (the ACK arm declines),
              mov 1, ldrb 4, cmp 1, not-taken bne 1,
              ldr-literal 2, bx 3                       23   -> tau+28
  ti_flushK   SEG1 11, SEG2 9, SEG3 10, SEG3_TAIL 3,
              SEG5 11, (SEG6 gone)             B_in(1) = 44  -> tau+72
  usb_ti_head 4 pad + NRZI 11 + SEG0 7 + TBARM 13        35  -> tau+107
  usb_ti_C0   eors + str                                  2  -> FIRST WIRE EDGE
                                                       ----
                                                    tau+109
```

### 7.2 Where §1-6 were wrong

| line | as budgeted | as built | why |
|---|---|---|---|
| §3 stub | 21 | **23** | the branch to `ti_flushK` does not fit a Thumb `B`. The IN chain sits past ±2046 bytes from the EOP stubs, so it is `ldr`-literal + `bx` — 5 cycles rather than 2-3 — and it needed a literal pool of its own after the stubs, because the entry's is 0x444 back and an `LDR`-literal reaches ±1020 |
| §4 first edge | τ+107 | **τ+109** | the same 2 cycles |
| §6 gate | 45 | **41** | `endp = (b2>>7) \| (b3&7)<<1` is bits 10..7 of the halfword the two bytes already occupy, so it is one shift and a mask, not eight instructions |
| §5 cells | 89 of 112 | **90 of 112** | 44 flush + 41 gate + 5 for the `bx` out of cell C7 |

Nothing in §2 changed. The 17 cycles a token's absent CRC16 returns are the
whole of why this fits, and they were measured before anything was written.

### 7.3 The gate, as assembled

Cells C4..C7 carry it, four pieces, every branch **not taken** on the good
path and every taken one leaving for `.Lti_abort`:

```
  C4   TIG1 stuffing 4 | TIG2 count == 4 3 | TIG3 ldrh SYNC,PID 5      12
  C5   TIG4 SYNC == 0x80, PID == 0x69 6 | TIG5 ldrh the token 4        10
  C6   TIG6 endpoint, bounded 5 | TIG7 the record 4 | TIG8 ldrh 4      13
  C7   TIG9 THE COMPARISON 2 | TIPIDLOAD 4 | ldr + bx 5                11
```

and the property §7.3 of `turnaround.md` claims for the ACK path holds here
in the same shape:

```
usb_ti_C7:  nop ; str r5,[r7,#BSRR]        SYNC bit 7
            cmp  r3, r2                        the ARMED PATTERN vs the
            bne  .Lti_abt                      RECEIVED TOKEN HALFWORD
            ldrb r4, [r1, #TI_PID_OFS]     <-- the PID, downstream of it
            ldr  r2, =(.Lti_pid+1) ; bx r2
```

There is one path from this engine to a DATA0/DATA1 PID and it passes through
that `cmp`. The PID byte is not even fetched until after it, and it is fetched
from the record rather than being an immediate, which is `turnaround.md` §9's
R5: at arm time nothing about which packet this will be has been decided.

What the single comparison settles, because the pattern is the one 16-bit
value that (device address, endpoint) maps to under CRC5:

* the token's CRC5 — without computing one;
* the device address — a token for another device cannot match;
* the endpoint — the record is indexed by it and holds only its own pattern;
* that a packet has been **rendered** for this endpoint, because the pattern
  is written by the last store of a completed render and by nothing else.

### 7.4 The pattern is learned, not computed

`turnaround.md` §4.1 proposes building the legal `(byte2,byte3)` values when
the address is set. This build does not: `.Lt_in` stores the halfword the
ordinary tail has just validated — CRC5 checked, address filtered, endpoint
bounded — into the record. A computed pattern would be the same number arrived
at the long way and would have to be rebuilt on every SET_ADDRESS; a learned
one re-learns itself, at the cost of one aborted transaction the first time
each (address, endpoint) pair is seen.

## 8. The payload, and why it is not produced in the PID's bit times

`turnaround.md` §7.3 gives the IN token "the PID's 8 bit times (128 cycles) to
be produced by `usb_pid_handle_in`, which is the first point at which C is on
the critical path at all — and 128 cycles is a different order of problem from
10."

**That line does not survive contact.** The PID cells have 11 free cycles
each, not 16 — the cell is the transmit engine's five-cycle bit — so the
window is 88, not 128. And `usb_pid_handle_in` does not return a payload: it
*calls `usb_send_data`*, i.e. it ends in a transmitter. Producing a payload
inside the PID field would mean running the C layer, the CRC16 and the bit
stuffer in 88 cycles, against ~950 measured for the stuffer alone (§10). It is
not a different order of the same problem; it is a different problem.

So the payload is **pre-rendered**, which is what the hardware model does and
what `CRC_ALTERNATIVES.md` §2.6 recommends independently. The record holds the
packet already stuffed and CRC'd, LSB first, and NRZI stays in the cell
because it is one `sbcs`/`ands`/`eors`. A payload cell is therefore:

```
usb_ti_D0..D7:  lsrs r4,r4,#1 ; sbcs r2,r2 ; ands r2,r6 ; eors r5,r2
                str  r5,[r7,#BSRR]                              5 cycles
```

five cycles and eleven free, and **it holds 16 where `engine16_tx.S`'s own
cells measure 18** (`turnaround.md` §11.6). Not because the work is cheaper —
it is the same work — but because it is no longer on the wire. That is the
answer to the brief's "if that blocks you, say so": the 18-cycle transmit
cells do not block this design, and this design does not fix them either.

### 8.1 The chain

Groups of eight wire bits. `D0` prefetches the next group; `D7` installs it
and dispatches. The dispatch is branch-free and touches no memory:

```
usb_ti_D7:  <the bit>                                            5
            mov  r4, r8          the prefetched group
            subs r0, r0, #1      groups remaining
            rsbs r2, r0, #0
            asrs r2, r2, #31     m = 0 if this was the last, else -1
            mov  r3, r10         loop ^ tail, built once in cell Q2
            ands r2, r3
            mov  r3, r14         tail
            eors r2, r3          = loop when m = -1, tail when m = 0
            bx   r2                                             11
                                                                --
                                                                16
```

`BX` is a flat 3 (`ENGINE16_SPEC.md` §2), so the cell is exactly 16 whichever
way it goes — which a taken conditional branch here would destroy. The final
partial group of `t = nbits & 7` bits is entered at `usb_ti_T(8-t)`, a code
pointer the renderer stores in the record, and `t = 0` points straight at the
EOP.

The eight PID cells set the chain up in their spare cycles: `Q0` the group
count and the tail target, `Q1` the stream pointer and group 0, `Q2` the
dispatch constant, `Q7` the queue. `r9`, `r11` and `r12` are not touched:
`.Lrx_tail` runs afterwards and needs the buffer base, the stuffing state and
the emitted count exactly as they are.

## 9. What the C layer sees

The seam does not change. `usb_pid_handle_{in,out,data,setup,ack}` keep their
prototypes and `rv003usb.c` is not edited. Two things are added, both on the
engine's side of the seam:

1. **`usb_send_data` renders.** It calls `usb_in_render` before anything else.
   Whether a render happens is decided inside, by a one-shot `usb_in_pend`
   that only the token dispatch and the refresh hook write, so no other caller
   can arm anything by accident.
2. **`.Lin_refresh` calls `usb_pid_handle_in` again**, with the transmitter
   suppressed, after each handler that changes what the next IN returns —
   `ack`, `data`, `setup`. This is the additional call `ENGINE16_SPEC.md` §4
   allows, not a replacement.

The refresh is safe to call early because `usb_pid_handle_in` mutates nothing
the next IN depends on: `e->count` and `e->toggle_in` move only in
`usb_pid_handle_ack` and `usb_pid_handle_setup` (`rv003usb.c:519-535`), and
`ist->current_endpoint` is set to the value it already holds. There is no
refresh after an IN token, because a token changes no endpoint state.

**What it does change, and this is a behaviour difference, not a nuance:** the
payload is *evaluated* one transaction early. Three places where that is
visible:

* `usb_handle_user_in_request` (`rv003usb.c:230`) is called from the ACK of
  the previous transaction rather than from the token, so a handler that reads
  live state — a gamepad report, say — reports state from one poll earlier.
* `RV003USB_BOOTLOADER`'s `runwordpad` (`rv003usb.c:262-267`) is armed one
  transaction early.
* `RV003USB_USE_REBOOT_FEATURE_REPORT`'s reset (`rv003usb.c:208`) fires one
  transaction early.

None is a correctness failure of the USB protocol; all three are visible to
the application. A build that cannot accept them sets `USB_TURNAROUND_B_IN 0`
and keeps the DATA→ACK path.

### 9.1 Failing safe

The record is disarmed by whoever *asks* for a render, not by the render, so
every path that does not reach the render's last store leaves the endpoint
unarmed rather than armed with a packet built from state that has since moved.
A user IN handler that transmits nothing takes exactly that path.

An unarmed endpoint costs one transaction: the gate misses, the abort puts a
detectably corrupt packet on the wire (`turnaround.md` §6.4 and §11.4, whose
argument is inherited unchanged), the ordinary tail runs, the record is
rendered, the host times out and retries, and the retry is answered inside 6.5
bit times. It does not wedge, and it does not livelock, because the abort path
ends in the tail rather than in a drop.

## 10. The cost, which is real and is on the ACK

The render is the transmit engine's own per-nibble pipeline, moved off the
wire, and it is not free. Measured on the linked image, worst case (8-byte
payload, DATA0), by walking the loops:

```
  prologue and the five checks                       53
  copy + CRC16, one pass, 8 bytes at 21, + exit     172
  CRC publish and stuffer setup                      43
  20 nibbles: 11 that flush a byte at 26,
              9 that do not at 19                   457
  byte-loop overhead, 10 bytes at 17, + exit        175
  tail, the record's four stores, epilogue           56
                                                    ---
                                                    956  = 40 us at 24 MHz
```

A zero-length response is about 250. A bit-at-a-time stuffer was written and
measured first at roughly 2400; T_TX is 2.5× better than that and the
difference is why this section reads as it does rather than worse.

**Where it lands.** Per IN transaction, comparing the ISR work that follows
each EOP:

| | today (reactive) | Design B |
|---|---|---|
| IN token | ~2030, of which 1728 is the response on the wire | ~2080, of which ~1780 is the response on the wire |
| host's ACK | ~50 | **~1050** |

So Design B moves the response out of the ACK's ISR and puts the render into
it: ~44 µs of work where there was ~2 µs. That is the honest price and it is
not hidden by the turnaround number.

Whether it matters is a question about the host's inter-transaction gap. At
low speed one bit is 667 ns, so an 8-byte DATA packet occupies the wire for
~73 µs and a token for ~24 µs; the stack already spends ~85 µs in the IN
token's ISR today and works. A 44 µs ISR after a 3-byte ACK is the same order
as one packet time, and if a host does issue the next token inside it the
token is missed, the host times out (16-18 bit times) and retries — correct,
but slower. **No host has been measured. This is the item most likely to need
work next**, and the obvious next cut is the 175 cycles of byte-loop overhead
and the 172 of copy+CRC, neither of which is inherent.

## 11. A defect in `engine16_tx.S`, found by the model and not fixed

USB 2.0 §7.1.9: *"If required by the bit stuffing rules, a zero bit will be
inserted even if it is the last bit before the end-of-packet signal."*

`T_TX` **defers** a stuffed zero to the head of the next nibble — that is what
its seventh row (state 6) means, and `tools/design_b_in_model.py` generates
the table from that definition and reproduces all 112 assembled halfwords
exactly. At the end of a packet there is no next nibble to carry it.
`engine16_tx.S`'s chain leaves for `usb_tx_eop` the moment the source is
exhausted and has no state-6 test anywhere, so **a packet whose last six data
bits are 1s goes out one bit short of what the specification requires.**

Measured, over the model's 7322 packets: **0.61%** — about one packet in 165,
not an edge nobody meets. `engine16_tx.md` §6's verification against an
independent encoder did not catch it, which is consistent with that encoder
having been written from the same table.

`usb_in_render` handles it explicitly, at `.Lir_tail`. The transmit engine is
not fixed here: the fix is an extra bit cell conditional on the stuff state at
the end of the stream, which is a change to a timed chain and belongs with the
redistribution that chain already needs.

## 12. Footprint, and what is not built

`INTEGRATION_BUILD.md`'s recipe, PY32F003x4, gamepad demo, calibration on, all
objects deleted first:

| | RAM | FLASH |
|---|---|---|
| DATA→ACK only (`turnaround.md` §11) | 344 B | 5844 B |
| **with IN → DATA0/DATA1** | **416 B** | **7928 B** |

**+72 B of RAM, +2084 B of flash.** The RAM is `usb_in_arm` — one 32-byte
record per endpoint, 64 B at `ENDPOINTS = 2` — plus `usb_in_pend`'s 4 and
alignment. Each further endpoint is 32 B. The flash is the second
instantiation of `SEG0..SEG5` for the token flush, the eight SYNC cells and
K=0's four, the eight PID cells, the payload chain's eight plus the tail's
seven, the EOP, and the renderer.

The record's 24 bytes of stream space are bounded, not hoped for: the longest
rendered stream the model finds over 7322 packets is **92 wire bits = 12
bytes**, 13 with the payload chain's one-group prefetch overrun.

`usb_rx_engine16`, `ti_flush0..7`, `usb_ti_C0..C7`, `usb_ti_E0..E3`,
`usb_ti_Q0..Q7`, `usb_ti_D0..D7`, `usb_ti_T1..T7`, `usb_in_render` and
`usb_in_arm` are all present in the linked ELF — the `--gc-sections` trap
`INTEGRATION_BUILD.md` records was checked for.

Every timed cell is **exactly 16 cycles** on the linked image and in all six
combinations of `USB_RX_CHECK` (0/1/2) and `USB_ENGINE16_FLASH` (0/1). The
RAM-resident column matters here for the same reason `turnaround.md` §11.1
records: cell C7 ends in a PC-relative literal, which is 2 cycles from
flash-resident code and 4 from RAM-resident, and the sizes in the ledger are
expressions in `USB_RAM_ACCESS` and `LIT_CYC` for that reason.

### What is not built

* **Anything on hardware.** No part of this has been on a bus.
* **A bit-exact model of the emitted instruction stream.**
  `tools/design_b_in_model.py` checks the *algorithm* — the render and the
  cell chain's bit order and group split — against a reference encoder over
  7322 packets. It does not execute the assembly. The two transcription errors
  it caught while it was written are the argument for having it; they are not
  an argument that it is sufficient.
* **The 18-cycle transmit cells** (`turnaround.md` §11.6). Still outstanding.
  This design routes around them; `usb_send_data` is still the path for the
  first IN after any state change and for a build with `USB_TURNAROUND_B_IN`
  off.
* **§11's trailing stuff bit in `engine16_tx.S`.**
* **A response to an IN token while `TB_OWED` is set.** The stub tests the ACK
  arm first, so an IN token arriving where the protocol says a DATA must
  arrive takes the ACK path and aborts. The protocol does not do that; it is
  the same residual `turnaround.md` §11.8 records for the other direction.
* **Reducing §10's 956.** The copy+CRC pass and the byte-loop overhead are
  347 of it and neither is inherent.
