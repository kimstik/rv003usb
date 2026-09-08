# The whole stack, executed — a USB enumeration in emulation

> **Headline.** The stack enumerates. 186 descriptor bytes came back
> byte-for-byte correct over ten control reads, the data toggle alternated on
> every transfer, the address filter worked, and the worst DATA→ACK turnaround
> measured over all eight EOP entries is **τ+107 against a τ+124 deadline** —
> `turnaround.md` §11's τ+115 and `audit_discarded.md` §F.6's τ+108 were
> conservative by 8 and 1 cycles.
>
> **And it drives the bus when it must not.** Eight findings, three of them
> bus collisions: the device answers the host's *own ACK*, answers an IN token
> addressed to *another device*, and answers an IN for an endpoint it does not
> have — in every case turning its pin drivers on and holding the line for
> ~20 bit times past the point the host may start its next packet.

Reproduce everything below with:

```
python3 tools/usb_enum_sim.py            # the report
python3 tools/usb_enum_sim.py --trace    # every packet, both directions
python3 tools/usb_enum_sim.py --sweep-latency
```

---

## 1. What runs, and why the check is worth anything

`b729cff` found an inverted branch in the receiver's SYNC phase lock that made
the engine unable to decode a single packet. It survived four bit-exact models
because **every one of them started downstream of it**. The lesson is not "test
more", it is that a checker sharing a source with the artifact proves nothing.

So this harness is built with the two sides deliberately apart.

**The device is the real thing.** `tools/usb_enum_sim.py` assembles
`doc/py32/engine16_merged.S` and `doc/py32/engine16_tx.S` exactly as
`INTEGRATION_BUILD.md` does (`.datacode` → `.text.engine16`, flash-resident)
and links them against **`rv003usb/rv003usb.c` unmodified** and
**`demo_gamepad/demo_gamepad.c` and its `usb_config.h` descriptors
unmodified**. The C layer had never been executed at all in this port; it is
now, 118 times per run through `usb_send_data`, 98 through
`usb_pid_handle_in`, 20 through `usb_pid_handle_data`.

The only stub is `tools/sim_shim/ch32fun.h`, which supplies the CH32V003
vendor register map that `usb_setup()` and the reboot feature-report path
reference. Neither is on any path this exercises. Nothing else is stubbed:

```
  the REAL C layer is in the image: usb_pid_handle_data @ 08001a6a,
  descriptor_list @ 08001ba0, usb_handle_user_in_request @ 08001b6c
```

**The host and the analyser are written from the specification.** Section 1 of
`usb_enum_sim.py` imports nothing from this tree. PID encoding is USB 2.0
§8.3.1, bit order §8.1, SYNC §8.2, NRZI §7.1.7.1, bit stuffing §7.1.9, CRC5 and
CRC16 §8.3.5 from their generator polynomials, EOP §7.1.13.2, the enumeration
sequence §9.1.2. The decoder that says what the device sent is a receiver
written to the same clauses.

**The reference codec is checked against the specification's own numbers**
before anything runs, so that a mistake in the analyser cannot be reported as a
device defect: CRC5 reproduces §8.3.5.1's worked example (address 0x15,
endpoint 0x0E → 0x1D), CRC16 of an empty payload is 0x0000 (§8.3.5.2), and
every PID byte matches Table 8-1. Beyond that, the strongest evidence that the
codec is right is two-sided: the device — which computes CRC5 and CRC16 from
its own tables, written independently — accepted every packet this host built
and rejected the one whose CRC16 the host deliberately broke.

**No verdict is read out of the device's RAM.** The emulator maps the GPIO as
MMIO and records every `BSRR` and `MODER` write with a cycle stamp; the driven
line is reconstructed from those writes and handed to the reference decoder.
The one place the harness looks inside is a single deliberate probe
(§4.7), and it prints the byte it read.

**The clock.** Instruction cost comes from `tools/engine16_cyc.py`'s measured
table, charged per instruction, with a branch charged its taken cost only when
the next address is not the fall-through. Loads and stores are priced **by the
region the access actually touched** — IOPORT 1, flash 2, RAM 4 for
flash-resident code — rather than by guessing what a base register held, which
is a strict improvement on `engine16_rx_bus.py`'s register-name heuristic and
is what makes pricing the C layer meaningful at all.

Build configuration: `ENDPOINTS=2`, `USB_RX_CHECK=CRC16`,
`USB_TURNAROUND_B=1`, `USB_TURNAROUND_B_IN=1`, `PY32_HSICAL_ENABLE=1` (with
`py32_hsical_event` a `bx lr`), no pre-rendered descriptor table linked.

---

## 2. It enumerates

The sequence driven is the one a host performs: keep-alive EOP,
`GET_DESCRIPTOR(device, 8)` at address 0, `SET_ADDRESS(3)`,
`GET_DESCRIPTOR(device, 18)`, config at 9 and at its full 34 bytes, the four
string descriptors, the HID report descriptor, `SET_CONFIGURATION(1)`, then
four interrupt INs on endpoint 1.

```
  descriptor bytes verified byte-for-byte against the linked image: 186
  over 10 control reads
  packets sent by the host      139
  responses the device drove    107
  host retries forced           3
```

The comparison is against `descriptor_list` **read out of the linked image**,
not a second transcription of `usb_config.h` — which matters, because two of
the eight entries do not carry their own length (`config_descriptor`'s
`bLength` is 9 against a `wTotalLength` of 34, and the HID report descriptor
has no length byte at all). An earlier iteration of this harness read `bLength`
and passed on a 9-byte prefix of a 34-byte descriptor.

What that verifies, on the wire:

* every `DATA` the device sent had a **correct CRC16** — the reference decoder
  recomputes it from §8.3.5.2 and every packet passed;
* the **data toggle alternated** on every control read (DATA1, DATA0, DATA1…)
  and on the interrupt endpoint (DATA0, DATA1, DATA0, DATA1) — §8.6;
* the **address filter** works: tokens for address 7 are ignored, and after
  `SET_ADDRESS(3)` tokens for address 3 are answered;
* every response **released the pins** and left the line at **J**;
* the device **never began driving while the host was still driving**;
* a **keep-alive EOP** (§7.1.7.6) draws no response, i.e. `usb_rx_keepalive`
  returns without arming anything;
* a **DATA with a wrong CRC16 is never ACKed**.

The three forced retries are structural rather than accidental: Design B's IN
path answers from a record armed by a *previous* transaction, so the **first IN
token carrying a token pattern the device has not seen before is aborted**, and
a new pattern appears once per (address, endpoint) pair. `design_b_in.md` says
one transaction is lost to the host's timeout; measured, it is exactly one, at
address 0/endpoint 0, address 3/endpoint 0 and address 3/endpoint 1.

### 2.1 The entry-latency window, mapped

`--sweep-latency` re-runs the whole enumeration at every ISR entry latency from
4 to 56 cycles.

| entry latency (cycles) | enumeration |
|---|---|
| 4 – 6 | fails completely (0 descriptor bytes) |
| **7 – 8** | **complete, 186 bytes** |
| 9 – 11 | fails completely |
| **12 – 43** | **complete, 186 bytes** |
| 44 – 56 | fails completely |

M0+ exception entry is 15 cycles plus the completion of the interrupted
instruction, so the operating point sits near the middle of a 32-cycle-wide
window with ~4 cycles of margin below and 27 above. The isolated island at 7–8
and the hole at 9–11 are the phase lock's, and are reproducible.

---

## 3. Turnaround, measured

τ is `turnaround.md` §2's reference: the cycle on which the `ldr` that samples
SE0 executes. The window derived there is **[τ+60, τ+124]**.

### 3.1 DATA → ACK, at every K

A real enumeration only ever produces two of the eight EOP entries, because its
packets are all the same lengths and bit stuffing is what moves the wire-byte
boundary. So the harness **searches for payloads by stuff count** — K is the
stuff count mod 8 — and reaches all eight. The flush chain each one entered is
read back out of the execution trace, so the row is labelled by the code that
ran, not by an assumption.

| K | measured | §7 ledger | §F.6 audit | flush entered |
|---|---|---|---|---|
| 0 | **τ+100** | τ+103 | τ+95 | `tb_flush0` |
| 1 | **τ+107** | τ+111 | τ+108 | `tb_flush1` |
| 2 | **τ+96** | τ+100 | τ+97 | `tb_flush2` |
| 3 | **τ+87** | τ+91 | τ+88 | `tb_flush3` |
| 4 | **τ+78** | τ+81 | — | `tb_flush4` |
| 5 | **τ+69** | τ+71 | — | `tb_flush5` |
| 6 | **τ+70** | τ+60 | — | `tb_flush6` |
| 7 | **τ+64** | τ+63 | — | `tb_flush7` |

**Worst case τ+107 against the τ+124 deadline: 17 cycles of margin.**
`turnaround.md` §11's headline of τ+115 and §F.6's revision to τ+108 are both
upper bounds, correct in direction and out by 8 and 1 cycles respectively.
Every value is constant across four payloads per K — there is no jitter in this
path, which is what a chain with no timed taken branches should give.

**Best case τ+64 against the τ+60 floor: 4 cycles.** That is the number to
watch, not the deadline.

Two of the eight rows contradict `engine16_merged.S`'s own ledger comment
(lines 1155–1161), which still says `K=6 60` and *"the earliest tau+60"*. The
per-K floor pad `audit_discarded.md` §SE.1 added — `EOPSTUB 6, 2, 12` — moved
K=6 to τ+70 and the comment was not updated with it. **The file's ledger is
stale; the object is fine.** K=5's τ+69 is now the second-earliest and K=7's
τ+64 the earliest.

### 3.2 IN → DATA

| K | measured | §7 ledger | token | flush entered |
|---|---|---|---|---|
| 0 | **τ+94** | τ+103 | addr 0, endp 0 | `ti_flush0` |
| 1 | **τ+94** | τ+111 | addr 63, endp 0 | `ti_flush1` |

`audit_discarded.md` §F.6 claims τ+95 on this path; **measured τ+94**, one
cycle out, and conservative.

A token is 24 bits after SYNC, so at most three zeros can be stuffed into it.
Over all 128 addresses × 16 endpoints K reaches only 0, 1 and 2, and endpoints
≥ `ENDPOINTS` are rejected — so a token this build **answers** reaches only K =
0 and 1. **`ti_flush2` … `ti_flush7` cannot run on the answering path at all.**
They are reachable only on the abort path, where timing carries no meaning.
That is a structural fact about the IN direction that no per-K ledger states.

### 3.3 The path that misses the deadline

| path | measured | verdict |
|---|---|---|
| DATA→ACK, Design B | τ+100 … τ+107 | conformant |
| **DATA→ACK, Design B not armed** | **τ+515** | **391 cycles past the deadline** |

See finding 6.

---

## 4. What the run found

Eight. Ordered by how much of the bus they take.

### 4.1 The device answers the host's own ACK — and it is a collision

Every IN transaction ends with the host sending ACK. Measured, **the device
drives the bus in reply to that ACK**, every single time: it enables its pin
drivers, emits eight SYNC bit times, fails the gate, spins 120–160 cycles of an
unchanging level (a deliberate stuffing violation) and drives an EOP. It **holds
the line 316 cycles — 19.8 bit times — past the point §7.1.18 lets the host
start its next packet.**

The mechanism, and this is measured rather than reasoned:

```
  predecessor  rxbuf+3 is     drives?  flush chain
  IN token     0x69 (IN)      YES      ti_flush0
  SETUP token  0x2D (SETUP)   YES      tb_flush0
  DATA0        0xC3 (DATA0)   no       rx_flush0
  ACK          0xD2 (ACK)     no       rx_flush0
```

`usb_rxbuf+3` is the byte `EOPSTUB`'s `ldrb r2,[r9,#1]` reads to decide whether
the packet that just ended was an IN token. A handshake is SYNC + PID + EOP;
the receive pipeline commits wire byte N during byte N+1's cells, and a
handshake has no byte N+1, **so at the EOP stub that byte still holds the
PREVIOUS packet's PID.** For the ACK that closes an IN transaction that is the
IN token's `0x69`, and the IN response path is entered for a packet that is not
a token. The gate rejects it — no wrong PID ever reaches the wire, the design's
central claim holds — but only *after* `TBARM` has turned the drivers on.

The same table shows the ACK path has it too: a handshake arriving while
`TB_OWED` is set (i.e. straight after a SETUP or OUT token) takes `tb_flush0`
and drives.

This is the finding with the largest practical consequence. On a real bus the
host may issue its next token two bit times after its ACK; the device is
driving for twenty.

### 4.2 An IN token addressed to another device is answered

`IN` to address 7 while the device holds address 3: **the device drives**, and
holds the bus **359 cycles = 22.4 bit times** past the earliest legal start of
the host's next packet — on top of whatever the device the token was actually
addressed to is driving at that same moment.

`TBARM` (`engine16_merged.S:2311`) enables the drivers and emits SYNC *before*
`TIG9`, the comparison that settles the address. F-7 fixed exactly this class
for the ACK direction, citing that "an unfiltered token addressed to another
device is a collision, not merely a wasted call". The IN direction has the same
hole, and it is worse: the ACK direction stays silent, this one drives.

### 4.3 An IN for an endpoint the device does not have is answered

`IN` to endpoint 3 with `ENDPOINTS=2`: the endpoint bound `TIG6` is inside the
gate, so again the drivers are already on. **346 cycles = 21.6 bit times** of
bus held.

### 4.4 The device still answers the default address after SET_ADDRESS

After `SET_ADDRESS(3)` completes, a SETUP addressed to **0** is still ACKed.
The filter at `engine16_merged.S:1873` (`beq .Lt_addr_ok`) accepts address 0 unconditionally.
§9.4.6 requires an addressed device to answer only its assigned address; on a
bus where a second, unaddressed device is being enumerated, both answer the
same token. Both RISC-V predecessors do the same thing, so this is inherited
rather than introduced — but nothing had ever demonstrated it.

### 4.5 SET_ADDRESS takes effect one stage early

A token for the new address is answered **before the status stage of
SET_ADDRESS has run**. `rv003usb.c:478` writes `ist->my_address` inside
`usb_pid_handle_data`, i.e. during the DATA stage; §9.2.6.3 requires the old
address to remain in force until the status stage completes. It is benign in
practice *only because of 4.4* — the status IN goes to address 0, which the
filter accepts unconditionally. Fix 4.4 without fixing this and enumeration
breaks.

### 4.6 There is no STALL and no NAK, anywhere

```
  PIDs the device ever put on the wire: DATA1 x23, DATA0 x13, ACK x28
```

A `GET_DESCRIPTOR` for a descriptor that does not exist is answered with a
**zero-length DATA1**, which a host reads as a completed short transfer rather
than as the Request Error §9.4.3 requires a STALL for. An **OUT to endpoint 1**
— which the configuration descriptor declares as `0x81`, IN only — is
**ACKed**. The stack has no path that emits either handshake:
`usb_send_empty` is the only "nothing to send" answer it has.

### 4.7 A DATA with a bad CRC16 is not acknowledged — but is answered

Correct on the part that matters: no ACK PID reaches the wire, the residue
compare sits upstream of the PID as designed. What does happen is
`turnaround.md` §6.4's deliberate corrupt frame, and it **holds the bus 370
cycles = 23.1 bit times** past the point the host may retry. Recorded here as
observed behaviour rather than a defect, because it is the documented design —
but it is the same 20-bit-time occupancy as 4.1 and 4.2, and if those are
fixed by staying silent, this one should be reconsidered with them.

### 4.8 One foreign packet between a SETUP token and its DATA costs the deadline

`TB_OWED` is armed by a SETUP or OUT token and consumed by the **end of the
next packet, whatever that packet is**. Put one packet for another device in
between and the flag is spent; the DATA that follows is not answered by Design
B at all but falls back to `usb_send_data` after the whole C dispatch:

```
  DATA->handshake/slow   0   tau+515   OVER THE DEADLINE
```

**τ+515 = 30.7 bit times after SE0→J, against the 6.5 §7.1.18 allows.** The
host times out and retries the transfer. On a single-device bus the SETUP token
and its DATA are adjacent and this never fires; on a bus with a hub and a
second device it is a live failure mode, and it is the same premise 4.2 breaks
— that this device is the only one being addressed.

### 4.9 The demo's IN handler runs twice per delivered report

Not a defect, but the wire evidence for `design_b_in.md` §9. Endpoint 1
delivered `010100 030100 050100 070100` — the demo increments byte 0 once per
call and the delivered values **step by two**, so `usb_handle_user_in_request`
runs twice per report: once from the IN token's dispatch and once from
`.Lin_refresh` after the host's ACK (9 calls for 4 delivered reports plus one
retried token). A handler backed by a counter is unharmed. **A handler backed
by a queue would drop every other entry.**

---

## 5. What this run does NOT cover

Read as narrowly as it is written.

**Timing and signal integrity.**
* Host and device share one perfect 24 MHz clock. §7.1.11's ±1.5 % low-speed
  tolerance is not exercised here at all (`engine16_rx_bus.py --sweep` does it
  for a single packet).
* No rise/fall times, no §7.1.9 dribble, no line skew, no reflections. Levels
  change instantaneously at cell boundaries.
* Entry latency is a constant per run, not jittered per packet. The real value
  varies with the instruction the interrupt lands in.
* Cycle costs come from `engine16_cyc.py`'s table. Every absolute number in §3
  inherits that model, including its flash-2 / RAM-4 / IOPORT-1 columns and its
  2-vs-3 taken-branch resolution. They are *model* cycles, not silicon cycles.

**Structure the harness does not model.**
* One ISR entry per host packet. The NVIC's pending-flag behaviour is not
  modelled, the EXTI acknowledge is a no-op write, and what happens when the
  ISR returns *before* the packet ends (the `.Lgiveup` spin timeout) is not
  explored.
* `usb_setup()` is never called: GPIO mode, EXTI configuration, the D− pull-up
  and the interrupt wiring are all unverified, and the shim would absorb a
  register write that was wrong.
* `py32_hsical_event` is a `bx lr`. The HSI calibration servo — without which
  `PY32F002B` cannot hold 24 MHz — is not exercised.
* No second device actually drives the bus, so collisions are *inferred* from
  the device driving outside its window, not observed as contention.

**Code paths not built or not reached.**
* **The pre-rendered descriptor table is not linked.** Every IN response in
  this run took the dynamic `usb_in_render` path. `prerender.md`'s hit path is
  checked by `prerender_check.py` in isolation, but has never run inside an
  enumeration.
* `RV003USB_SUPPORT_CONTROL_OUT`, `RV003USB_HID_FEATURES`,
  `RV003USB_USB_TERMINAL`, `RV003USB_EVENT_DEBUGGING` and the reboot
  feature-report path are all off in `demo_gamepad/usb_config.h`, so
  `usb_pid_handle_data`'s control-OUT arm, the HID get/set report arms and the
  bootloader entry are untouched.
* `USB_RX_CHECK=PARITY` and `USB_RX_CHECK=NONE` are not built here.
* Only `ENDPOINTS=2` and only the gamepad demo. `demo_composite_hid` and the
  bootloader have different descriptor sets and different endpoint counts.
* Only one error was injected — a wrong CRC16. No mid-packet stuffing
  violation, no SE1, no over-long packet, no truncated packet, no wrong-PID
  alias (`DATA2`/`MDATA`), no CRC5 error in a token.
* The suspend/resume, reset and remote-wakeup signalling of §7.1.7.5–7.1.7.7 is
  not touched, and neither is `SET_INTERFACE`, `GET_STATUS`,
  `CLEAR_FEATURE(ENDPOINT_HALT)` or anything else §9.4 lists beyond the four
  requests driven here.

**And the obvious one.** This is an emulator running a cost model. It proves
the *logic* of the stack over a *modelled* wire. Nothing here has been on
silicon.
