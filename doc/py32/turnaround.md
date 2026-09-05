# turnaround — getting a response onto the wire inside §7.1.18

The receive engine is finished and exact (`engine16_merged.md`). The one thing
it does not do is answer in time. This note establishes what "in time" is in
cycles, re-derives the measurement against the *specification's* reference
point rather than the engine's, establishes the floor reachable without
speculation, and only then designs the speculative mechanism.

**Result, in one paragraph.** The turnaround deadline at 24 MHz is 124 cycles
from the SE0-detecting sample (§2). The merged engine reaches the C layer at
208..237 and the wire at perhaps 320..350 — outside not only §7.1.18 but
plausibly outside the 16-18 bit-time host timeout as well (§3). Ordinary means
— a stripped flush, dispatch pre-staged into RX slack, token validation by
pattern match instead of CRC5, and the engine emitting the ACK itself instead
of routing the decision through C — bring the first response bit to
**τ+114+A**, measured on an object that assembles
(`turnaround_sketch.S`, §5), where A is the transmitter's arm-to-first-edge
cost. That is `(90+A)/16` bit times after SE0→J: **conformant for A ≤ 10**,
exactly on the line at A = 14, and past even the 7.5 bit-time captive-cable
figure beyond A = 26. The entire
question reduces to one number the TX author is measuring. If A ≤ 10, ACK-first
is unnecessary complexity and the right answer is to say so. If A > 26, §7
gives a speculative design — **SYNC-first, PID-gated**, not ACK-first — that
reaches 5.5 bit times for any A ≤ 62, at the cost of a defined and detectable
deviation from §8.4.5 in the CRC-error case.

Conventions: `merged:<n>` = `doc/py32/engine16_merged.S`, `c:<n>` =
`rv003usb/rv003usb.c` in this repo, `S:<n>` = `rv003usb/rv003usb.S`,
`PA` = `doc/py32/PRIOR_ART.md`, `bn` = `doc/wg015/branch_notes.md` on
`origin/wg015-port`. Cycle costs are the `ENGINE16_SPEC.md` §2 RAM column,
applied by `tools/engine16_cyc.py --exec ram --ioport r7`; every figure below
was produced by that tool on the assembled object unless it says "estimate".

---

## 1. What the specification requires, and what it does not

Claims and their sources. Where I could not verify the exact wording of a
clause I say so rather than paraphrasing it as if I had.

| # | Claim | Clause | Status |
|---|---|---|---|
| L1 | Inter-packet delay is measured from the **SE0-to-J transition at the end of EOP** to the **J-to-K transition that starts the next packet** | USB 2.0 §7.1.18 | wording confirmed against secondary sources; the *reference points* are the load-bearing part and they are unambiguous |
| L2 | That delay must be **≥ 2 and ≤ 6.5 bit times** for a device response; 7.5 is allowed where a captive cable's delay is charged to the device | §7.1.18 | as recorded in `PA` L-1; the 7.5 allowance is cable-dependent and this port has no captive cable, so **6.5 is the number this note holds itself to** |
| L3 | A device or host expecting a response times out **no earlier than 16 and no later than 18 bit times** | §7.1.18 / §7.1.19 | as recorded in `PA` L-1 |
| L4 | EOP is **SE0 for 2 bit times, then driven J for 1 bit time**, then release | §7.1.13.2 | standard, uncontested |
| L5 | Every packet begins with the **same 8-bit SYNC field**, KJKJKJKK on the wire (data 00000001) | §7.1.10, §8.2 | uncontested. **This is the fact §7 is built on: SYNC carries no packet-specific information** |
| L6 | Fields are transmitted **LSB first** | §8.1 | uncontested |
| L7 | A PID is 4 type bits followed by their **ones complement**; if the check bits are not the complement, the receiver must discard the packet | §8.3.1 | paraphrase, not verbatim; the mechanism is uncontested |
| L8 | A transmitter must insert a 0 after **six consecutive 1s**; a receiver seeing seven has a **bit stuff error** | §7.1.9 | uncontested |
| L9 | Bit-stuff violations, PID check failures and CRC failures are the three **packet error categories** a receiver detects | §8.7.1 (subsection number **unverified**) | the categories are uncontested; I could not confirm the subsection number |
| L10 | On a data packet received with a **CRC error the device returns no handshake** — it is silent, and the host times out | §8.4.5 / §8.5 | this is the rule §7 knowingly deviates from, so it is stated as the deviation rather than assumed away |
| L11 | Data toggle (§8.6) makes a retried DATAx unambiguous: a device that did not accept a packet did not toggle, so the retry is processed correctly and cannot be double-delivered | §8.6.3, §8.6.4 | uncontested; this is what makes "host retries" a *correct* outcome and not merely a tolerable one |

**What the specification does not say**, and I will not pretend otherwise: it
does not state that a host receiving a corrupt handshake behaves identically to
a host receiving nothing. It states only that the corrupt handshake is detected
and discarded (L7, L8, L9). The argument in §7.4 is therefore not "the spec
permits this" but "this produces a failure mode every host is already required
to handle correctly".

### 1.1 The numbers in cycles at 24 MHz

Low speed is 1.5 Mb/s (§7.1.11), so one bit time is 24/1.5 = **16 cycles
exactly**. This is the whole reason the problem exists: at 48 MHz the same
budget is 32 cycles per bit time and every figure below doubles.

| quantity | bit times | cycles @24 MHz |
|---|---|---|
| response window, earliest | 2 | 32 |
| response window, latest (§7.1.18) | 6.5 | **104** |
| captive-cable allowance | 7.5 | 120 |
| host timeout | 16..18 | 256..288 |
| EOP SE0 width | 2 | 32 |

---

## 2. The zero point: the engine measures from the wrong place

`engine16_merged.md` §10.2 and `ENGINE16_RESULTS.md` report **203..229 cycles,
12.7..14.3 bit times**, measured from the SE0-detecting sample. The
specification does not measure from there. Correcting the reference point moves
the number in *our* favour, and it is worth doing before anything else.

Let **τ** = the cycle on which the `ldr r2,[r7,#16]` that sees SE0 executes
(`merged:108`, cell 3 shown; identical in all eight cells).

* The engine samples at the nominal centre of a 16-cycle cell, so SE0 began
  about **8 cycles before τ** — the SE0 starts at a cell boundary and the
  first sample that can see it is that cell's centre.
* Phase-lock error is ±3.5 cycles (`merged.md` §4.4), so
  SE0_start ∈ [τ−12, τ−4].
* SE0→J, the specification's zero point (L1, L4), is 2 bit times = 32 cycles
  after SE0_start, so it lands in **[τ+20, τ+28]**.

Therefore, in cycles after τ:

```
  earliest legal first response bit :  SE0->J + 32  in [tau+52, tau+60]  -> use tau+60
  latest   legal first response bit :  SE0->J + 104 in [tau+124, tau+132] -> use tau+124
```

**The budget is [τ+60, τ+124], 64 cycles wide.** The upper figure is the
deadline for the rest of this note. The lower one is not decoration: §7's
design would otherwise be tempted to start transmitting during the host's own
EOP, which is both illegal and a bus fight.

This correction is worth **24 cycles = 1.5 bit times** against the engine's own
accounting, and it is the only free money in this document.

---

## 3. The measurement, re-derived — and it is worse than recorded

Re-run of the tool on the assembled object:

```
arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c \
    doc/py32/engine16_merged.S -o e.o
python3 tools/engine16_cyc.py e.o --exec ram --ioport r7
```

Block costs (min..max; the tool prices a conditional branch 1 not-taken /
2-3 taken and does not resolve control flow, so the path has to be walked by
hand):

| block | cycles | note |
|---|---|---|
| SE0 detect: `ldr`+`ands`+taken `beq` | 4..5 | |
| `rx_eopK` stub | 4..5 | `movs`, `mov r14`, taken `b` |
| `rx_flush0..6` | 7, 11, 9, 11, 9, 11..13, 10 | sum 68..70 |
| partial-byte path (`rx_flush7` head → `.Lrx_tail`) | 83..88 | NRZI of the tail bits + SEG0..SEG6 again |
| tail → first instruction of `usb_pid_handle_data` | 56..76 | includes `bl` at 4 (the tool mis-scores it 1 because `objdump` prints the unrelocated halfword) |

The worst case is **not** "SE0 caught in cell 0" as `merged.md` §10.2 states.
At K=0 the current wire byte holds no sampled bits, so `cmp r2,#8 / beq`
(`merged:465-466`) skips the partial-byte path entirely. The worst case is
**K=1**: six flush segments for the byte in flight *and* a one-bit partial byte
that still costs the full seven segments.

```
K=1:  detect 4..5 + eop 4..5 + flush1..6 (61..63) + partial (83..88) + tail (56..76)
   =  208..237 cycles from tau, to the first instruction of the C handler
K=0:  4..5 + 4..5 + 68..70 + 4..5 + 56..76 = 136..161
```

Against the specification's zero point that is **(208−24)/16 .. (237−24)/16 =
11.5..13.3 bit times to the C call** — and the C call is not the wire. Adding
`usb_pid_handle_data` (§4.3) and the TX arm puts the first response bit at
roughly τ+320..350, i.e. **18.5..20.4 bit times after SE0→J**.

That is past the 16-18 bit-time host timeout (L3). So the recorded position —
"tolerated by real hosts, non-conformant to the specification" — is optimistic:
it omits the two costs that come after the measurement point. The honest
statement is that the merged engine has **no measured position inside the host
timeout either**, and the C and TX costs are what decide it. This does not
change the direction of the work; it changes its urgency.

---

## 4. What can be pre-staged, and where it goes

The RX bit cell has 9 idle cycles per wire byte (`merged.md` §4.2: nine `nop`
across the eight cells). An 11-byte DATA packet therefore carries **99 cycles
of free budget**, and a 4-byte token 36. Everything below is work that today
sits in the tail — on the critical path — and depends only on bytes that
arrived long before EOP.

| # | moved out of the tail | depends on | cost | fits in |
|---|---|---|---|---|
| P1 | SYNC byte == 0x80 (`merged:508-510`) | wire byte 0 | 4 | slack of byte 2 |
| P2 | PID complement check (`merged:512-518`) | wire byte 1 | 6 | slack of byte 2 |
| P3 | PID type dispatch — DATA / token / handshake / special (`merged:520-529`) | wire byte 1 | 8 | slack of byte 3 |
| P4 | "is a response owed?", and which emitter — one word written to `rxbuf+28` | bytes 0-1 + the preceding token | 4 | slack of byte 3 |
| P5 | the `rv003usb_internal_data` pointer and the payload pointer, marshalled for the C call | nothing | 6 | slack of byte 4 |
| P6 | token validation, §4.1 | bytes 2-3 | 8, and it *removes* the CRC5 | tail |

Two structural points about this list.

**The dispatch target is data, not a register.** `merged.md` §10.5 is right that
register pressure is total — all eight low registers and r8-r12/r14 are live in
the timed chain, so there is nowhere to keep a pre-computed branch target. It
goes to memory: one word at `rxbuf+28`. The buffer is 32 bytes and the emitted
count is bounded to 24 (`merged:522` `cmp r2,#USB_BYTE_LIMIT`), so offsets
24..31 are unreachable by the store and the word is safe there. Writing it
costs a 2-cycle `str` in slack; reading it costs a 2-cycle `ldr` in the tail.

**The buffer offset must stay inside the address bound.** Putting the word at
`rxbuf-4` would be outside the 32-byte aligned region the masked index
guarantees (`DEFECTS_VERIFIED` D-2), and Thumb-1 has no negative `ldr` offset
anyway — the sketch was written that way first and the assembler rejected it.

### 4.1 Tokens: match the pattern, do not compute the CRC5

The merged tail computes CRC5 with two `bl`s into `.Lcrc5_byte`
(`merged:553-559`), four nibble table steps and two calls — expensive, and on a
4-byte token there are only ~18 spare RX cycles after the PID is known, which
is not enough to pre-stage it.

It does not need to be computed. The eleven token bits are address (7) +
endpoint (4), the CRC5 is a pure function of them, and this device implements a
handful of endpoints. So the set of legal `(byte2, byte3)` pairs for this
device is **one 16-bit value per endpoint** — three or four of them. Build them
when the address is set (once per enumeration, outside any budget) and at EOP
compare:

```
    ldrh  r2, [r7, #2]      2      the two token bytes as one halfword
    cmp   r2, r_ep0         1      endpoint 0
    beq   ...               1
    cmp   r2, r_ep1         1
    ...
```

Two to four comparisons, ~8 cycles, and one comparison validates **the address,
the endpoint and the CRC5 together** and yields the endpoint index. It is
strictly stronger than a CRC5 check: it rejects a well-formed token addressed
to another device without computing anything, which is exactly what a device
should do. The cost is a ≤8-byte table rebuilt on SET_ADDRESS.

This removes the only CRC computation that was still in the tail, and it is an
ordinary-means change with no speculation in it.

### 4.2 The C layer is not in the turnaround path, and does not need to be

`ENGINE16_SPEC.md` §4 fixes the external seam: the engine calls
`usb_pid_handle_{data,setup,in,out,ack}` with their existing prototypes. It
does not say *when*. Reading what those functions actually decide
(`rv003usb/rv003usb.c` in this repo) settles whether the decision has to be
inside the budget:

* **`usb_pid_handle_data` (c:291-511) has no handshake decision to make.** Every
  path through it reaches `just_ack` (c:505-509) and calls
  `usb_send_data(0,0,2,0xD2)` — an ACK. The toggle-mismatch path (c:305-308,
  "already received this packet") also ACKs. There is exactly one exception:
  under `RV003USB_USER_DATA_HANDLES_TOKEN` it returns early (c:359) and the
  user handler owns the response.
* **`usb_pid_handle_in` (c:197-279) never NAKs.** It sends data or a
  zero-length DATA, and its response PID is
  `e->toggle_in ? 0x4B : 0xC3` (c:203, DATA1/DATA0) — a function of the
  endpoint alone, not of anything in the packet being received.
* **`usb_pid_handle_out` (c:283-289) sends nothing.** A SETUP or OUT token is
  not answered; the host sends the DATA packet next. **The turnaround problem
  does not exist for OUT/SETUP tokens or for received handshakes.**

So the ACK for a DATA packet is a foregone conclusion the moment the CRC16
residue is right, and the DATA0/DATA1 PID for an IN is known from the endpoint.
Neither needs C. The engine can put the response on the wire and call the C
function afterwards, outside the budget, with the seam unchanged.

**This is the single largest reduction in the whole document** and it is not
speculative at all — it is the observation that the C layer was never making a
decision the response depended on. It is also the one change with a real
compatibility cost: `usb_send_data(0,0,2,0xD2)` inside `just_ack` must become a
no-op when the engine has already sent the ACK, and `RV003USB_USER_DATA_HANDLES_TOKEN`
must disable the whole mechanism, because under that option the response is not
a foregone conclusion.

---

## 5. The ordinary-means floor, measured

`doc/py32/turnaround_sketch.S` is the flush and tail of `engine16_merged.S`
rewritten with §4 assumed and with the constant-time machinery removed —
nothing speculative in it. It assembles rc=0 and was counted with

```
arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c \
    doc/py32/turnaround_sketch.S -o t.o
python3 tools/engine16_cyc.py t.o --exec ram
```

(no `--ioport`: nothing in the sketch touches GPIO, and `r7` here holds the
rxbuf base, so pricing its accesses at 1 would understate them.)

What was removed and why it is legitimate:

* **SEG3..SEG6 → FEMIT, 41 → 22 cycles.** Those four segments are branchless
  because they must fit a 16-cycle cell: a masked store, a commit mask built
  with `asrs`/`mvns`/`bics`, a speculative CRC lookup parked in r8, a gate, and
  a commit-or-not. The flush is untimed, so two not-taken conditional branches
  do the same job.
* **Count and buffer base hoisted into r6 and r7**, which are dead after EOP
  (pin mask, GPIO base). Each use of a high register costs a `mov`.
* **The loop bound (`cmp #24`/`bhs`) dropped.** The flush is straight-line over
  at most two wire bytes and the timed chain bounded the count before EOP. The
  *address* bound is kept: it is `DEFECTS_VERIFIED` D-2 and costs 2 cycles.
* **A partial byte of k ≤ 4 bits skips its low nibble.** `merged:455-462`
  already proves the padding zeros are inert, so putting them through the table
  buys nothing. 48 instead of 65 cycles.

Measured block costs: `tr_eopK` 6..7, `tr_flush0` 7, `tr_flush1` 11,
`tr_flush2` 8, `FEMIT` 22, partial byte 4 (k=0) / 48..49 (k≤4) / 65 (k≥5),
tail 12.

Total from τ to the `bx` that hands control to the transmitter, by the cell K
in which SE0 was sampled (k = K real bits in the partial byte):

| K | detect | eop | byte in flight | partial byte | tail | **total** |
|---|---|---|---|---|---|---|
| 0 | 4..5 | 6..7 | 48 | 4 | 12 | 74..77 |
| 1 | 4..5 | 6..7 | 41 | 48..49 | 12 | **111..114** |
| 2 | 4..5 | 6..7 | 30 | 48..49 | 12 | 100..103 |
| 3 | 4..5 | 6..7 | 22 | 48..49 | 12 | 92..95 |
| 4 | 4..5 | 6..7 | 22 | 48..49 | 12 | 92..95 |
| 5 | 4..5 | 6..7 | 22 | 65 | 12 | 109..111 |
| 6 | 4..5 | 6..7 | 22 | 65 | 12 | 109..111 |
| 7 | 4..5 | 6..7 | 0 | 65 | 12 | 87..89 |

All K occur: bit stuffing makes the wire-byte boundary land anywhere relative
to the end of the data field, so the worst case is what counts.

**Ordinary means reach τ+114 worst case, against a deadline of τ+124.**
That is 208..237 → 111..114, a factor of **1.9-2.1**, and it is reached without
transmitting a single speculative bit. Against the specification's zero point
the response begins at

```
  (114 + A - 24) / 16  =  (90 + A) / 16   bit times after SE0->J
```

where **A** is the transmitter's arm-to-first-edge cost.

### 5.1 The whole question is now one number

```
  A <= 10   ->  first bit at tau+124        6.25 bit times   CONFORMANT
  A <= 14   ->  first bit at tau+128        6.50 bit times   conformant nominally,
                                                             no jitter margin
  A  = 26   ->  first bit at tau+140        7.25 bit times   inside the 7.5
                                                             captive-cable figure only
  A  = 51   ->  first bit at tau+165        8.8  bit times   not conformant
```

**τ+124 is the deadline with 4 cycles of phase-lock margin (§2); τ+128 is the
nominal deadline with none.** So the honest statement of the floor is:

> **Ordinary means are conformant if and only if the transmitter can put its
> first wire edge out within 10 cycles of being handed control, with all of its
> constants pre-staged in RAM by the receiver. At 14 cycles it is exactly on
> the line with no margin. Beyond ~26 it is out of specification.**

51 is the figure `PLAN` Appendix A records for the RISC-V path
("entry → first preamble store 51", `arm.S:362-389`), but that is measured from
a C call, not from a hot handover — it is an upper bound of the wrong kind and
should not be used to condemn Design A. The number that decides this is the one
the TX author is measuring. **Ask for it before building anything speculative.**

### 5.2 What is not reducible

Two costs were examined and rejected as irreducible:

* **Sampling SE0 earlier.** The detecting `ldr` is at the cell centre, ~8
  cycles into the SE0, and those 8 cycles are pure latency. A second GPIO poll
  early in each cell would recover ~6 of them, but it costs `ldr`+`ands`+`beq`
  = 3 cycles in *every* cell = 24 cycles per wire byte, against 9 cycles of
  slack. Rejected on arithmetic.
* **The two wire bytes of flush.** The last two message bytes of a DATA packet
  are the CRC16 itself and the residue needs every bit of them, so they cannot
  be dropped, deferred or pre-staged. They are the irreducible core of the
  post-EOP path, and at 41+48 = 89 of the 114 they are 78% of it.

---

## 6. Is ACK-first sound?

The recorded idea (`PA` R8, `bn` Part B commit 3735518) is: transmit the ACK's
SYNC and PID unconditionally, compute the CRC during those 16 bit times, and
**emit the EOP only if the residue is 0xB001** — on a bad CRC "the line just
returns to J, host sees no EOP and retries". Reported: turnaround 15.7 → 5.3
bit times, `false_acks=0`, `wedged=0`.

Three things are wrong with importing that verbatim.

**6.1 It puts a valid ACK PID on the wire before the CRC is known.** In that
design the only conditional thing is the EOP. A host that accepts a handshake
on PID alone — or that recovers a packet whose EOP was lost to a glitch — sees
a valid ACK for a packet that failed CRC. That is the one failure USB has no
recovery for: the host believes the data was accepted, advances the toggle, and
the transfer silently loses a packet. `false_acks=0` in a simulator is not a
proof that no host does this; it is a measurement of one host model.

That design needed 16 bit times of transmission because it had moved the CRC
*out* of the receive slot (`bn`, commit f46ed67). **Our engine computes CRC16
inside the bit cell** (`merged` SEG4/SEG6, `PA` S-2), so we do not need the
PID's 8 bit times to finish the CRC — 8 bit times of SYNC are enough. That
buys the strictly stronger property in §7: **no ACK PID is ever transmitted
without a passing residue.**

**6.2 The SYNC is not speculation.** Every packet begins with the same 8-bit
SYNC field (L5). Transmitting it commits to "a packet is coming", not to
*which* packet. The only genuinely speculative bit in the whole scheme is
"a response is owed at all", and §7.2 shows that is known before EOP in every
case — it is not a guess about the packet in flight.

**6.3 Aborting without an EOP is worse than aborting with one.** "The line just
returns to J" leaves the host's receiver in the middle of a packet with no
terminating event. §7.1.13.2 makes EOP the thing that ends a packet; a packet
that never ends is not a case the specification describes, and the host's
behaviour is then implementation-defined. Emitting a proper EOP after the
corrupt field costs nothing (the transmitter is already running) and leaves the
bus in a state the specification does describe.

### 6.4 What the host does when the abort is on the wire

The abort in §7.3 is: after SYNC, **stop toggling** — hold the line at its
current level for ≥7 bit times, then EOP, then release. Two independent
detections, both mandatory for a receiver:

1. **Bit stuff error (L8, §7.1.9).** A transmitter must insert a 0 after six
   consecutive 1s. Holding the level *is* transmitting consecutive 1s in NRZI.
   Seven of them is a bit stuff violation, which is one of the packet error
   categories a receiver must detect (L9).
2. **PID check failure (L7, §8.3.1).** Even if a receiver's stuff detector did
   not fire, the eight bits it decodes in the PID position are all 1s = 0xFF,
   whose upper nibble 0xF is not the complement of its lower nibble 0xF. The
   PID check fails and the packet is discarded.

So the host discards the packet. It received no valid handshake for the OUT or
SETUP data stage. It retries the transaction, and the retry is correct because
the device did not accept the data and did not toggle its sequence bit
(L11, §8.6): the same DATAx arrives again with the same toggle and is processed
once.

**The honest limit of this argument.** The specification says the device must
be *silent* on a CRC error (L10, §8.4.5). This design is not silent: it emits a
detectably corrupt packet. I cannot cite a clause saying that is equivalent.
What I can say is narrower and, I think, sufficient:

> The corrupt packet is indistinguishable at the host's receiver from a
> handshake that the *bus* corrupted. Handling that correctly is not optional
> for a host — it is exactly what §8.7's error detection exists for. The design
> therefore produces a failure mode every host is already required to handle,
> rather than a novel one.

The difference that remains is timing: a silent device makes the host wait out
its 16-18 bit-time timeout (L3), while an aborting device gives it an answer
early. That is a difference in the host's favour.

### 6.5 The failure probability of committing early — stated plainly

For a DATA packet, the commit at τ+D uses **no bit of the packet in flight
except its PID**. So the commit is wrong exactly when the packet fails CRC16 —
which is to say, at the bus's ordinary packet error rate. ACK-first does not
make bad outcomes rarer; **it converts "silence" into "an aborted packet", and
its soundness rests entirely on those being equivalent from the host's side.**
Anyone who wants to argue this design is safe must argue §6.4, not
probabilities.

Two second-order commit errors, for completeness:

* The received PID was corrupt but passed its complement check: requires ≥2
  specific bit errors in one byte, and is dominated by the CRC-error case.
* **Token address aliasing.** For an IN token the commit also uses the address
  field, whose CRC5 has not been checked yet. A bit error in the address of a
  token addressed to device B can make device A start a SYNC while B also
  starts one. During SYNC both drive the *same* pattern, so there is no value
  conflict, only two drivers agreeing; A then fails its check and must
  **release its drivers immediately rather than drive an abort pattern**, or it
  will fight B's real response. This is a real residual hazard, it needs a
  second LS device on the segment plus a specific single-bit error, and the
  mitigation is one line of the abort path. It is stated, not dismissed.

---

## 7. The design: SYNC-first, PID-gated

Used **only if the transmitter's A exceeds 10 cycles** (§5.1). It is strictly
more machinery than the ordinary path and buys margin, not capability.

### 7.1 Shape

```
  tau+0     SE0 sampled
  tau+4..5  branch to the EOP stub
  tau+11    stub done; state hoisted; TX registers loaded (still not driving)
  tau+40    earliest safe drive-enable: the host stops driving J one bit time
            after SE0->J (7.1.13.2), so driving before this fights it
  tau+112   FIRST WIRE EDGE, J->K, SYNC bit 0        <- 5.5 bit times, conformant
  ...       SYNC bits 1..7; the flush runs in the free cycles of each TX cell
  tau+240   SYNC done.  PID COMMIT POINT.
              residue == 0xB001  ->  emit ACK PID (0xD2), then EOP
              residue != 0xB001  ->  hold the level (>=7 bit times), then EOP
  tau+368   PID done; EOP until tau+400; release; only now call the C handler
```

The flush is the same code as §5, cut into nine pieces: one that runs in
`[tau+11+A, tau+112)` and eight that ride in the free cycles of the SYNC bit
cells. This is the identical discipline the RX engine already uses — a
software pipeline whose segments are sized to the slack of a timed cell — so
the engineering is known, not novel. `FEMIT` at 22 cycles is the largest piece
and has to be split across three cells.

### 7.2 Commit 1 — "a response is owed" — is not a guess

Set during reception, never after EOP, one bit at `rxbuf+28` (§4, P4):

| received | response owed? | known from | when |
|---|---|---|---|
| DATA0/DATA1 | yes, an ACK | the preceding SETUP/OUT token to our address | before this packet started |
| IN token | yes, a DATA packet | PID (byte 1) + address (byte 2) | ≥8 bit times before EOP |
| SETUP / OUT token | **no** | PID (byte 1) | ~9 bit times into the packet |
| SOF | no | PID | same |
| ACK / NAK from host | no | PID | same |
| address not ours | no | byte 2 | ≥8 bit times before EOP |

So the transmitter is armed only for the two cases that owe a response, and the
arming decision never depends on anything that arrives in the last bit times of
the packet. The OUT/SETUP token case matters: those are the majority of tokens
and **they must not arm**, or the device transmits into the host's own DATA
packet.

### 7.3 Commit 2 — the PID — is where the CRC gate sits

At τ+240 the flush has finished and the residue is known.

* **DATA + residue 0xB001** → PID = ACK (0xD2). This is the only path that puts
  an ACK on the wire, and it is downstream of the residue check. **A false ACK
  is structurally impossible**, which is the property §6.1 says the recorded
  design does not have.
* **IN token + pattern match (§4.1)** → PID = DATA0/DATA1 from `e->toggle_in`
  (c:203). The payload then has the PID's 8 bit times (128 cycles) to be
  produced by `usb_pid_handle_in`, which is the first point at which C is on
  the critical path at all — and 128 cycles is a different order of problem
  from 10.
* **anything else** → abort: stop toggling for ≥7 bit times (bit stuff
  violation, §6.4), emit EOP, release. On the token-aliasing path of §6.5,
  release the drivers instead of holding.

### 7.4 What it costs

1. The flush must be cut into nine timed pieces and stay correct in both the
   ordinary and the SYNC-interleaved layout — two copies of the same logic, or
   one macro set instantiated twice, which is the trap `merged.md` §7 already
   documents (a cell chain and its flush drifting apart).
2. A deliberate deviation from §8.4.5 in the CRC-error case (§6.4).
3. The abort path and the driver-release rule of §6.5.
4. TX and RX are coupled: the transmitter's SYNC cells must expose their free
   cycles to receiver code. That is a much more intrusive interface than "call
   me when you have a packet".

**Design A (§5) has none of these.** That is why §5.1's question — what is A? —
has to be answered before this section is built.

---

## 8. Cycle budget, SE0 to first response bit

Everything below is from τ, the SE0-detecting `ldr`. SE0→J, the
specification's zero point, is at τ+20..τ+28 (§2); bit times are quoted against
τ+24 and the deadline against τ+124.

### 8.1 Today (`engine16_merged.S`, measured §3)

| | cycles | running |
|---|---|---|
| SE0 detect (`ldr`, `ands`, taken `beq`) | 4..5 | 5 |
| `rx_eop1` stub | 4..5 | 10 |
| `rx_flush1..6` (SEG1..SEG6) | 61..63 | 73 |
| partial byte: NRZI + SEG0..SEG6 | 83..88 | 161 |
| tail: violation, bounds, SYNC, PID, dispatch, residue, marshal, `bl` | 56..76 | 237 |
| **first instruction of `usb_pid_handle_data`** | | **208..237** |
| C handler to `usb_send_data` | not measured, est. 40..70 | |
| TX arm to first edge | not measured, est. 51 (`PLAN` App. A) | |
| **first wire edge, estimated** | | **~300..360** |

= **11.5..13.3 bit times to the C call**, ~17..21 to the wire. Deadline 6.5;
host timeout 16..18.

### 8.2 Design A — ordinary means (`turnaround_sketch.S`, measured §5)

Worst case K=1:

| | cycles | running |
|---|---|---|
| SE0 detect | 4..5 | 5 |
| `tr_eop1`: set r14, hoist count→r6 and rxbuf→r7, branch | 6..7 | 12 |
| `tr_flush1`: FAPPEND 6 + FLOOK 5 | 11 | 23 |
| `tr_flush2`: FSEG2 | 8 | 31 |
| `tr_flush3`: FEMIT (store + CRC16 fold) | 22 | 53 |
| partial byte, k=1: NRZI, shift, one nibble, FEMIT | 48..49 | 102 |
| tail: writeback, build 0xB001, compare, `ldr` target, `bx` | 12 | **114** |
| TX arm to first edge | **A** | 114+A |
| **first wire edge** | | **τ + 114 + A** |

Bit times after SE0→J: **(90 + A)/16**. Conformant for **A ≤ 10** with jitter
margin, **A ≤ 14** nominal.

Where the 114 goes: 78% of it (89 cycles) is the two wire bytes that carry the
CRC16 and cannot be pre-staged (§5.2); 12 is the tail; 12 is detection and
entry; and 1 cycle is the address bound, kept deliberately.

### 8.3 Design B — SYNC-first (§7)

| | cycles | running |
|---|---|---|
| SE0 detect + `tr_eop` stub | 10..12 | 12 |
| TX register load (no drive yet) | A | 12+A |
| flush piece 0, in `[12+A, 112)` | 100−A | 112 |
| **first wire edge — SYNC bit 0** | | **τ+112 = 5.5 bit times** |
| SYNC bits 0..7, 8 cells × 16 | 128 | 240 |
| — flush pieces 1..8 ride in the free S cycles of each cell | 8·S | |
| PID commit: residue compare | in-cell | 240 |
| ACK PID, 8 cells | 128 | 368 |
| EOP | 32 | 400 |

Feasibility condition: the flush (102 cycles after detect+stub) must fit in
`(112 − 12 − A) + 8S`:

```
  100 - A + 8S >= 102     ->     8S - A >= 2
  S = 8  (an 8-cycle TX cell)  ->  A <= 62
  S = 10 (a 6-cycle TX cell)   ->  A <= 78
```

**Design B is conformant at 5.5 bit times for any A ≤ 62**, and the response
does not depend on the C layer at all.

---

## 9. What this requires of the transmitter

Stated as an interface, not as a design. The TX bit cell is not mine.

**R1 — the number to report.** "Arm to first bit on the wire" must be measured
**from a hot handover**, not from a C call: control arrives by `bx r2` with all
constants already in RAM, so the figure is the cost of the pin-mode store, the
J/K preset store and entry into the timed loop — not of `usb_send_data`'s
prologue. Call it **A**. Everything in §5.1 turns on it.

**R2 — a hot entry must exist.** The receiver hands over with `r7` = rxbuf base
and nothing else guaranteed. The transmitter must be able to fetch its state
from a fixed offset in that 32-byte buffer (offsets 24..31 are unreachable by
the receive store, §4) rather than from a register the receiver does not have
free (`merged.md` §10.5). If the transmitter needs its constants in registers
at entry, the receiver cannot supply them and A grows by two 2-cycle loads.

**R3 — do not drive before τ+40.** The host drives J for one bit time after the
SE0→J transition (§7.1.13.2). Enabling the output drivers earlier fights it.
`τ+40` is `SE0→J (τ+20 worst case) + 16 + 4` of margin.

**R4 — do not transmit before τ+60.** §7.1.18's *minimum* inter-packet delay of
2 bit times is a requirement, not advice (§2). A transmitter that starts the
moment it is armed will violate it on the short flush paths (K=0 finishes at
τ+77).

**R5 — the response PID must be a parameter, not a constant.** Both designs
select the PID after the transmitter has been armed: ACK/DATA0/DATA1 in
Design A (chosen at τ+114 by the pre-staged dispatch word), and in Design B not
until the SYNC has been on the wire for 8 bit times. A transmitter that bakes
the packet before it starts cannot be used by either.

**R6 — Design B only: publish the per-cell slack.** The SYNC cells must leave
S ≥ 8 free cycles each and allow receiver code to be placed in them, exactly as
`merged` places SEG0..SEG6 in the RX cells. If the TX cell has less than 8 free
cycles, Design B does not fit either and §10's conclusion applies.

**R7 — the abort must be expressible.** "Hold the current level for ≥7 bit
times, then EOP, then release" and "release the drivers immediately" (§6.4,
§6.5) must both be reachable from inside the transmitter's timed loop.

**R8 — both packet directions.** DATA→ACK and IN→DATA0/DATA1 use the same path.
SETUP/OUT tokens and received handshakes must **not** arm the transmitter
(§7.2); if arming is unconditional, the device transmits into the host's own
DATA packet.

---

## 10. Verdict, and the 24-vs-48 MHz question

**24 MHz is not disqualified, but it has no margin.**

At 24 MHz one bit time is 16 cycles and the response must start within 104 of
them from SE0→J. The irreducible post-EOP work — two wire bytes carrying the
CRC16, which cannot be pre-staged, deferred or skipped — is 89 cycles measured
(§5.2). That is **86% of the entire budget spent on arithmetic that only
becomes possible after the last bit arrives**. Everything else — detection,
entry, the residue compare, the dispatch and the transmitter's arm — must fit
in the remaining 25 cycles, of which 12 are already spent on detection and
entry.

At 48 MHz the same 89 cycles of work occupy 89 of 208, i.e. **43%**, and A ≤ 10
becomes A ≤ 136. The problem simply does not exist there; `PA` L-1's cycle
figures (64-208) were written for 48 MHz for that reason. This is the evidence
the 24-vs-48 question was waiting for, and it says: **at 48 MHz the turnaround
is a non-issue and Design A is trivially conformant with ~90 cycles to spare;
at 24 MHz conformance is achievable but hangs on a single-digit cycle count in
someone else's module.**

The recommendation, in order:

1. **Get A.** Everything is downstream of it. If A ≤ 10, build Design A, delete
   §7, and record ACK-first as considered and unnecessary — which is the
   outcome the brief asked for if ordinary means reach 6.5.
2. **If 10 < A ≤ 26**, Design A is inside the 7.5 bit-time captive-cable figure
   but not inside 6.5. That is *better than the status quo and still
   non-conformant*, and it is a bad place to stop: it is the position the
   engine is already in, only less so.
3. **If A > 26**, build Design B. It is conformant at 5.5 bit times for any
   A ≤ 62, at the price of §7.4 — including a deliberate, argued deviation
   from §8.4.5 in the CRC-error case that this document does not pretend is
   sanctioned by the specification.
4. **Independently of all three**, adopt §4 and §4.1 and §4.2. Pre-staging the
   dispatch, matching tokens against precomputed patterns instead of computing
   CRC5, and taking the C layer out of the response path are ordinary
   engineering with no speculative content, and together they are worth ~120 of
   the ~125 cycles saved.

## 11. What I am not sure of

Stated rather than buried.

* **§8.4.5's exact wording.** I could not verify the clause text for "no
  handshake is returned on a CRC error", only that the rule exists. The design
  in §7 deviates from it knowingly, so the wording matters and should be
  checked against the actual document before §7 is built.
* **§8.7.1's subsection number** for the packet error categories. The
  categories are certain; the number is not.
* **Host behaviour on a deliberately corrupt handshake.** §6.4 argues by
  indistinguishability, not by citation. No host was tested. `bn` reports
  `false_acks=0` for the weaker CH32V003 form against a simulator, which is
  evidence but not proof, and it is evidence about a different design.
* **The 22-cycle `FEMIT` and the 48-cycle partial byte are counted, not
  executed.** `turnaround_sketch.S` assembles and was priced by the same tool
  as the merged engine, but nothing has verified that it decodes packets
  correctly. `merged.md` §7's bit-exact model is the instrument for that and
  was not run against the sketch.
* **`A` itself**, obviously.
* **The C handler's cost** (§8.1's "40..70") is a guess from reading c:291-511.
  It stops mattering under both designs, which is why it was not measured.
* **Phase-lock jitter is ±3.5 cycles from the poll granularity alone**
  (`merged.md` §4.4); exception entry latency is unmeasured and is *not* in
  that figure. If entry latency has run-to-run variation it lands directly on
  τ and therefore on every deadline in this document.
