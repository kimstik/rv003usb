# NAK and STALL: is their absence a defect?

The enumeration simulator (`doc/py32/ENUMERATION_SIM.md`) reported that no STALL
and no NAK exist anywhere in this stack: an unsatisfiable `GET_DESCRIPTOR`
answers a zero-length DATA1, and an OUT to the IN-only endpoint 1 is ACKed.

This note settles whether that is a defect. Short answer, established below:

* It is **deliberate**, it is **inherited from upstream unchanged**, and the
  same policy on *unsupported control requests* is what V-USB does too.
* The PHY **can already emit NAK and STALL at zero incremental cost** — it does
  emit NAK, today, in `rvswdio_programmer` — so this is a policy decision in the
  C layer, not a capability gap in the engine.
* Exactly **one** of the three findings is a real, host-visible defect with a
  plausible trigger, and it is not the one the simulator led with.

Every claim below carries a file:line or a specification section. Where I could
not verify something I say "not verified".

Sources read (not recalled):

| what | where |
|---|---|
| this fork | `/home/user/rv003usb` @ `fe1f382` |
| upstream | `github.com/cnlohr/rv003usb` @ `80b1893` (cloned) |
| V-USB | `github.com/obdev/v-usb` @ HEAD (cloned) |
| micronucleus | `github.com/micronucleus/micronucleus` @ HEAD (cloned) |
| USB 2.0 spec | plain-text of the 2000-04-27 spec + 2025-06-03 errata bundle |

---

## 1. What the RISC-V original actually does

### 1.1 The three answers the stack can give, and the one it never gives

The engine has exactly one transmit primitive, in assembly:

```
rv003usb/rv003usb.S:823   //void usb_send_empty( uint32_t token );
rv003usb/rv003usb.S:824   usb_send_empty:
rv003usb/rv003usb.S:825       c.mv a3, a0        // token -> a3
rv003usb/rv003usb.S:826       la   a0, always0   // data  -> the constant 0
rv003usb/rv003usb.S:827       li   a1, 2         // length 2
rv003usb/rv003usb.S:828       c.mv a2, a1        // poly_function = 2 (no CRC)
rv003usb/rv003usb.S:830   usb_send_data:
```

`usb_send_empty` is a *tail-call into* `usb_send_data` with a two-byte payload
of zeroes and CRC generation disabled. Those two zero bytes are the CRC16 of an
empty payload, so what goes on the wire is a well-formed **zero-length DATA0/1
packet**. It is not a special packet type; it is the general sender with a
hard-coded residue.

The same primitive emits handshakes. ACK is:

```
rv003usb/rv003usb.c:508       usb_send_data( 0, 0, 2, 0xD2 ); // Send ACK
```

length 0, no CRC, PID byte `0xD2`. A handshake packet is SYNC + PID + EOP and
nothing else (USB 2.0 §8.4.5), so "length 0, no CRC" is exactly right, and the
**only** thing that distinguishes ACK from NAK or STALL here is the PID byte
argument: ACK `0xD2`, NAK `0x5A`, STALL `0x1E` (USB 2.0 Table 8-1).

That matters for costing later: the engine has no missing capability. It has a
missing *call site*.

### 1.2 The zero-length answer is deliberate, and upstream calls it "NAK"

Not an accident, and not undocumented — it is documented in the wrong word.
Four separate upstream demos put this comment directly above the call:

```
demo_gamepad/demo_gamepad.c:25          // If it's a control transfer, nak it.
demo_gamepad/demo_gamepad.c:26          usb_send_empty( sendtok );
testing/demo_touchpad/demo_touchpad.c:112-113   (identical)
testing/demo_xinput/demo_xinput.c:75-76         (identical)
testing/sandbox/sandbox.c:25                    (identical)
```

So in upstream's own vocabulary, **"nak it" means `usb_send_empty()`**. The
author is aware there is a situation that wants a refusal and has chosen the
zero-length DATA packet as the way to express it. This is the central fact of
the whole investigation: the behaviour is intentional, it is named, and the name
is wrong — a zero-length DATA is not a NAK and the host does not read it as one
(§3 below).

The API contract makes the same choice explicit — there is no third answer:

```
rv003usb/rv003usb.h:68   // usb_handle_interrupt_in is OBLIGATED to call usb_send_data or usb_send_empty.
```

The bootloader carries a TODO acknowledging the gap in passing, again using
"nak" to mean something it does not implement:

```
bootloader/bootloader.c:339   if( endp ) //XXX TODO: This can be reworked - if it's anything
                              //   other than "is_descriptor" then send nak.
```
(upstream path; the fork's bootloader is restructured.)

### 1.3 Upstream's *other* meaning of NAK: answer nothing at all

There is a second, genuinely different mechanism, added on purpose:

```
commit 3f611bd  cnlohr, 2024-01-01  "Make it possible to NAK from within handlers."
  rv003usb/rv003usb.c  +3   #if RV003USB_USER_DATA_HANDLES_TOKEN
                            	return;
                            #endif
  rv003usb/rv003usb.h  +2   #define RV003USB_USER_DATA_HANDLES_TOKEN 0
```

The commit adds an early `return` that skips the unconditional `just_ack:` at
`rv003usb/rv003usb.c:508`, so the device answers a DATA packet with **silence**.
The host then times out and retries (§8.4.6.3, §8.6.4 — see §3). Upstream calls
this "NAK" too. It is a third distinct behaviour wearing the same name.

Present in this fork at `rv003usb/rv003usb.c:358`, enabled by
`bootloader_v006/usb_config.h:29` and `rvswdio_programmer/usb_config.h:20`.

### 1.4 A real NAK PID *is* emitted — by an application, not by the stack

This is the finding that costs the "we cannot do handshakes" argument its footing:

```
rvswdio_programmer/rvswdio_programmer.c:597    // Send NACK (can't accept any more data right now)
rvswdio_programmer/rvswdio_programmer.c:598    usb_send_data( 0, 0, 2, 0x5A );
rvswdio_programmer/rvswdio_programmer.c:627-628 (again, in usb_handle_user_data)
```

Byte-identical in upstream at the same line numbers. `0x5A` is the NAK PID. So:

* the engine emits NAK correctly on real hardware today, in a shipping tool;
* it costs one call, identical in every respect to the ACK call;
* the shared C layer's silence on NAK/STALL is a **policy** choice, and one that
  the author has already overridden by hand where it mattered to him.

### 1.5 It is not discussed anywhere else

* `grep -rni 'nak|stall'` over upstream's `rv003usb/`, `README.md` and `doc/`:
  **zero hits** other than those quoted above.
* Upstream `README.md:100-130` ("It's still in beta") is a 24-item status
  checklist. Every item is PHY-level — sync sled, CRC, bit stuffing, HSI trim,
  retiming. **No item mentions protocol completeness, NAK, STALL, or
  Windows/host compatibility.** The gap is not on the author's TODO list.
* Upstream commit-message search for `nak|stall`: exactly one hit, 3f611bd
  above.
* Upstream issue tracker (30 issues, searched for NAK / STALL / enumerate /
  timeout / windows). The two candidates are **not** handshake bugs:
  * **#76 "Not working on Windows, problem with descriptor failure"** — Code 43,
    "USB device descriptor request failed", `demo_gamepad` on CH32V003. The
    reporter states **no DPU pull-up resistor is installed**. That is a physical-
    layer configuration fault, not a protocol fault. No comments, unresolved.
  * **#137 "bootloader: compatibility with other devices on the USB data lines"**
    — a BQ25611D charger IC needs 2 s on D+/D- before the bootloader grabs them.
    Timing/GPIO, not handshakes.
  * No issue anywhere reports a symptom attributable to a missing NAK or STALL.
    **Not verified**: whether such reports exist and were closed as invalid, or
    exist in the CH32V003 community outside this tracker.

**Verdict for §1.** The zero-length answer is deliberate, is upstream-identical,
is called "NAK" in four places by an author who elsewhere emits a real NAK PID
by hand, and has never been reported as a field failure in the upstream tracker.

---

## 2. What V-USB and micronucleus do

### 2.1 V-USB implements both, and the encoding is the whole trick

```
vusb/usbdrv/usbdrv.h:672     #define USBPID_NAK      0x5a
vusb/usbdrv/usbdrv.h:673     #define USBPID_STALL    0x1e
vusb/usbdrv/usbdrv.c:30      volatile uchar usbTxLen = USBPID_NAK;
                             /* number of bytes to transmit with next IN token or handshake token */
```

One byte, `usbTxLen`, is **either** a transmit length **or** a handshake PID.
Valid low-speed lengths are 0..11 (payload + sync + CRC), so bit 4 is clear;
both `0x5a` and `0x1e` have bit 4 set. The whole dispatch is therefore two
instructions on the IN path:

```
vusb/usbdrv/asmcommon.inc:145     lds  cnt, usbTxLen        ;[37]
vusb/usbdrv/asmcommon.inc:146     sbrc cnt, 4               ;[39] all handshake tokens have bit 4 set
vusb/usbdrv/asmcommon.inc:147     rjmp sendCntAndReti       ;[40] 42 + 16 = 58 until SOP
```

**Cost of having NAK and STALL at all, measured in their source:**

* dispatch on IN: `sbrc` + `rjmp`, 2 instructions / 4 bytes, 3 cycles taken
  (`asmcommon.inc:146-147`); a second copy for endpoint 1 at `:170-171` and a
  third for endpoint 3 at `:180-181`.
* emitting a handshake: it is the **same transmit path as ACK**, differing only
  in the PID loaded —

```
vusb/usbdrv/usbdrvasm12.inc:282   sendNakAndReti:  ldi x3, USBPID_NAK ; rjmp usbSendX3
vusb/usbdrv/usbdrvasm12.inc:285   sendAckAndReti:  ldi x3, USBPID_ACK ; rjmp usbSendX3
vusb/usbdrv/usbdrvasm12.inc:288   sendCntAndReti:  mov x3, cnt        ; (fallthrough)
```

2 instructions / 4 bytes per handshake flavour. At 12 MHz — 8 cycles/bit, a
*harder* budget than this port's 16 — V-USB budgets 19 cycles from EOP to the
start of its reply (`usbdrvasm12.inc:282` comment, against the spec's 7.5 bit
times ≈ 60 cycles, `usbdrvasm12.inc:300-302`).

So: NAK and STALL cost V-USB roughly a dozen bytes and 3 cycles of ISR
dispatch. They are not expensive. Nothing about the bit-banged setting makes
them expensive.

### 2.2 What V-USB actually uses NAK for — and it is *not* control requests

`usbTxLen` is reset to `USBPID_NAK` immediately after every transmit
(`asmcommon.inc:148`, with the reasoning spelled out at `asmcommon.inc:153-160`)
at init (`usbdrv.c:30` for endpoint 0, `usbdrv.c:627` for endpoint 1) and on
every SETUP (`usbdrv.c:449`). The default
answer to an IN is therefore NAK, and the application converts it to data by
filling the buffer. Two flow-control uses:

* **input backlog** — `asmcommon.inc:133-135`: if `usbRxLen` still holds an
  unprocessed received packet, answer the IN with NAK.
* **no data ready on the interrupt endpoint** — `asmcommon.inc:169-172`: if
  `usbTxLen1` still reads NAK because the app has not called
  `usbSetInterrupt()`, NAK the IN.

This is the substantive difference from rv003usb, and it is structural rather
than protocol-theoretical: V-USB's application produces IN data *outside* the
ISR into a buffer, so the ISR routinely finds nothing and must say so. rv003usb
calls the application handler *from inside* the ISR and obliges it to produce a
packet synchronously (`rv003usb/rv003usb.h:68`), so the "not ready" state is
designed out rather than answered.

### 2.3 What V-USB uses STALL for — a much shorter list than expected

Only two situations:

* **explicit endpoint HALT** — `SET_FEATURE`/`CLEAR_FEATURE`(ENDPOINT_HALT) on
  ep1 sets `usbTxLen1` to `USBPID_STALL`/`USBPID_NAK` (`usbdrv.c:388-391`), and
  `GET_STATUS` reports it back (`usbdrv.c:383`). Compiled out unless
  `USB_CFG_IMPLEMENT_HALT`.
* **application error mid-control-transfer** — `usbFunctionWrite()` returning
  `0xff` (`usbdrv.c:483`, documented `usbdrv.h:288-294`) or `usbFunctionRead()`
  returning >8 (`usbdrv.c:548`, documented `usbdrv.h:302-307`).

### 2.4 The decisive comparison: V-USB does *not* STALL unsupported requests

This is the part that most changes the verdict. V-USB's fallthroughs for
requests it does not implement leave `len = 0`:

```
vusb/usbdrv/usbdrv.c:334-341  SWITCH_DEFAULT   // unknown STRING descriptor index
vusb/usbdrv/usbdrv.c:349-355  SWITCH_DEFAULT   // unknown descriptor type
vusb/usbdrv/usbdrv.c:413-415  SWITCH_DEFAULT   // 7=SET_DESCRIPTOR, 12=SYNC_FRAME
                                               //   "Should we add an optional hook here?"
```

With `USB_CFG_DESCR_PROPS_UNKNOWN` at its default 0, all three yield `len = 0`,
`usbMsgLen = 0`, and `usbBuildTxBlock` (`usbdrv.c:535-546`) then sends a
**zero-length DATA packet**. Likewise the default `usbFunctionSetup()` in every
example returns 0 for anything it does not recognise — same result.

**V-USB answers an unsupported control request with a zero-length data packet,
exactly as rv003usb does.** That policy has shipped on millions of AVR devices
against every host stack in existence. It is therefore not, on its own, a
device-breaking defect — and finding (a) from the simulator report is largely
disposed of by this one observation.

### 2.5 micronucleus: kept NAK, deleted STALL

micronucleus V2 forked V-USB into an interrupt-less driver (`Modified to an
interrupt-less driver for micronucleus V2. (c) 2014 T. Bo"scke`,
`micronucleus/firmware/usbdrv/asmcommon.inc:8-10`) for a bootloader that fits in
~1.3–1.5 kB total (`doc/py32/VUSB_MICRONUCLEUS.md`, from
`firmware/releases/*.hex`). Under that pressure it deleted double-buffering,
CRC checking, the reset hook, interrupt endpoints — and:

```
$ grep -n 'NAK\|STALL' micronucleus/firmware/usbdrv/usbdrv.c
265:    usbTxLen = USBPID_NAK;  /* abort pending transmit */
394:    usbTxLen = USBPID_NAK;
```

**Zero occurrences of STALL.** `usbResetStall()` is commented out
(`usbdrv.c:386`). But the NAK dispatch survived verbatim in the ISR:

```
micronucleus/firmware/usbdrv/asmcommon.inc:138-141   lds x1, usbRxLen / cpi / brge sendNakAndReti
                                                     ldi x1, USBPID_NAK
micronucleus/firmware/usbdrv/asmcommon.inc:142-145   lds cnt, usbTxLen / sbrc cnt,4
                                                     rjmp sendCntAndReti / sts usbTxLen, x1
```

The most byte-starved deployed bit-banged USB stack in the world, cutting
everything it could cut, **kept NAK and threw away STALL**. That is as clear a
statement of relative importance as the prior art can give.

**Verdict for §2.** Ranked by what the field-proven stacks actually consider
load-bearing: NAK for IN flow control is kept even at 1.3 kB; STALL is optional
enough to delete outright; and STALL-on-unsupported-request is not implemented
by V-USB either.

---

## 3. What the specification actually requires

All citations are to *Universal Serial Bus Specification Revision 2.0*
(2000-04-27), read from the plain text, plus one Microsoft document where the
behaviour is Windows-specific.

### 3.0 Groundwork

A handshake packet is SYNC + PID + EOP and nothing else — §8.4.5, "Handshake
packets ... consist of only a PID ... delimited by an EOP after one byte of
packet field." That is exactly what `usb_send_data(ptr, 0, 2, PID)` produces, so
the shape of a NAK or a STALL is already available.

PID values, Table 8-1: ACK `0b0010` → wire byte `0xD2`, NAK `0b1010` → `0x5A`,
STALL `0b1110` → `0x1E`. All three are stuff-free after SYNC: taken LSB-first
after SYNC's single trailing one, `0xD2` runs to two consecutive ones, `0x5A` to
two, `0x1E` to four — under the six that would force a stuffed zero (§7.1.9).

### 3.1 (a) May a device answer an IN it cannot satisfy with a zero-length DATA?

**No.** §8.4.6.1, Table 8-4, is exhaustive for a function receiving an IN:

| Token corrupted | Tx Endpoint Halt | Can Transmit Data | Action |
|---|---|---|---|
| Yes | — | — | Return no response |
| No | Set | — | **Issue STALL handshake** |
| No | Not set | No | **Issue NAK handshake** |
| No | Not set | Yes | Issue data packet |

"If the function is unable to send data, due to a halt or a flow control
condition, it issues a STALL or NAK handshake, respectively" (§8.4.6.1 prose).
A zero-length DATA packet is the fourth row — "Can Transmit Data: Yes". The
device is asserting that it *could* transmit and that what it had to transmit
was nothing. There is no row for "answer an unsatisfiable IN with empty data".

§8.5.4 says the same thing for interrupt endpoints in normative prose: "If the
endpoint has no new interrupt information to return (i.e., no interrupt is
pending), the function returns a NAK handshake during the data phase."

**What the host concludes.** A zero-length DATA is a *short packet* and short
packets end a transfer: §8.5.3.2, "the function should indicate that the Data
stage is ended by returning a packet that is shorter than the MaxPacketSize for
the pipe"; §9.4.3, "If the descriptor is shorter than the wLength field, the
device indicates the end of the control transfer by sending a short packet ...
A short packet is defined as a packet shorter than the maximum payload size or
a zero length data packet." So the host reads it as **the transfer completed
successfully with zero bytes**, which is a different outcome from both "not
ready, ask again" (NAK) and "cannot" (STALL).

### 3.2 (b) A control request the device does not support

Two clauses, and they are unambiguous:

* **§9.2.7 Request Error** — "When a request is received by a device that is
  not defined for the device, is inappropriate for the current setting of the
  device, or has values that are not compatible with the request, then a Request
  Error exists. The device deals with the Request Error by returning a STALL PID
  in response to the next Data stage transaction or in the Status stage of the
  message. It is preferred that the STALL PID be returned at the next Data stage
  transaction, as this avoids unnecessary bus activity."
* **§9.4.3 Get Descriptor** — "If a device does not support a requested
  descriptor, it responds with a Request Error."

and §8.5.3.4: "protocol stall indicates that the request or its parameters are
not understood by the device and thus provides a mechanism for extending USB
requests." Protocol STALL is self-clearing — it "lasts until the receipt of the
next SETUP transaction" — so it costs no recovery handshake from the host.

Note the direction of the requirement. §9.4 states plainly that "USB devices
must respond to standard device requests" and Table 9-3 lists GET_STATUS,
GET_CONFIGURATION, GET_INTERFACE and the rest as standard. **The spec's first
demand is that the device answer the request; STALL is what it owes only for
requests it genuinely does not support.** That distinction turns out to decide
§4.

**What a host does with a short packet instead of a STALL.** It reports success
with a short count, and the caller's own length check — not the USB stack —
becomes the error detector. Verified against Linux v6.12:

* `drivers/usb/core/message.c:791-798` (`usb_get_descriptor`) — the retry loop
  is written for exactly this device: `/* retry on length 0 or error; some
  devices are flakey */ ... if (result <= 0 && result != -ETIMEDOUT) continue;`.
  Three attempts. A STALL surfaces as `-EPIPE`, which is also `<= 0`, so it is
  retried the same three times. **On this path Linux cannot tell a zero-length
  answer from a STALL**, and the cost of each is identical.
* `message.c:887-905` (`usb_string_sub`) — `if (rc < 2)` retries at length 2 and
  then at `buf[0]`, then `if (rc < 2) rc = (rc < 0 ? rc : -EINVAL)`. Again
  zero-length and STALL converge.
* `message.c:1153-1173` (`usb_get_status`) — a `switch (ret)` accepting only
  exactly 2 or 4 bytes, `default: ret = -EIO`. A zero-length answer is
  **`-EIO`**, and so is a STALL's `-EPIPE` at the caller's `if (status)`.

Where the two *do* diverge is a host that treats STALL as a documented,
cacheable "not supported" and a short packet as a malformed answer. §4.2 has the
one deployed instance of that.

### 3.3 (c) Is ACKing an OUT to an IN-only endpoint a violation?

Endpoint identity in §5.3.1 includes direction: "Each endpoint on a device is
given at design time a unique device-determined identifier called the endpoint
number. Each endpoint has a device-determined direction of data flow. The
combination of the device address, endpoint number, and direction allows each
endpoint to be uniquely referenced. Each endpoint is a simplex connection that
supports data flow in one direction." So EP1-IN and EP1-OUT are two endpoints,
and `demo_gamepad/usb_config.h:102` declares only `0x81` — EP1-OUT does not
exist on this device.

This is a real structural gap in the stack — `struct usb_endpoint`
(`rv003usb/rv003usb.h:160-170`) is indexed by endpoint *number* only
(`ist->eps[endp]`) and holds `toggle_in` and `toggle_out` in one record, so the
stack has no representation of direction and cannot distinguish the two. The
endpoint *number* is bounds-checked and out-of-range tokens get no response at
all, which is correct:

```
rv003usb/rv003usb.S:527   li   s0, ENDPOINTS
rv003usb/rv003usb.S:528   bgeu a2, s0, done_usb_message   // Make sure < ENDPOINTS
```

But **no table in §8.4.6 covers "the addressed endpoint does not exist"**. Table
8-6 (function response to OUT) presumes an endpoint that is there; §8.4.6.4
covers only the SETUP case ("If a non-control endpoint receives a SETUP token,
it must ignore the transaction and return no response"); the §8.4.6 preamble
covers only token corruption. The spec addresses this from the *host* side
instead — §5.3.1: "Endpoints other than those with endpoint number zero are in
an unknown state before being configured and may not be accessed by the host
before being configured." An endpoint absent from the configuration descriptor
is never configured, so a compliant host never issues the transaction.

**Honest verdict on (c): a model violation with no cited clause and no
compliant-host trigger.** It is wrong — the device claims to have accepted data
into an endpoint that does not exist — and it is unreachable except from a
deliberately non-compliant host or a compliance test. Note that V-USB has the
same hole: `vusb/usbdrv/asmcommon.inc:87-94` routes any OUT to
`storeTokenAndReturn` and `:104-127` ACKs the data that follows, with no
direction check either.

---

## 4. What actually breaks, and when

Ranked by how likely an ordinary host is to hit it. The ranking does not come
out where the simulator report pointed.

### 4.1 Rank 1 — GET_STATUS is unanswered, and Linux disconnects the device on every resume

**This is the one real defect, and it is not a missing handshake.** It is a
missing *request*, and the zero-length answer is what hides it.

`GET_STATUS` is implemented in the shared C layer and switched off:

```
rv003usb/rv003usb.c:480   #if 0
rv003usb/rv003usb.c:481   		// These are optional for the most part.
rv003usb/rv003usb.c:482   		else if( reqShl == (0x0080>>1) ) // GET_STATUS = 0x00 ,always reply with { 0x00, 0x00 }
rv003usb/rv003usb.c:483   		{
rv003usb/rv003usb.c:484   			e->opaque = (uint8_t*)always0;
rv003usb/rv003usb.c:485   			e->max_len = wLength;
rv003usb/rv003usb.c:486   		}
...
rv003usb/rv003usb.c:492   #endif
```

The comment on line 481 is wrong. §9.4 Table 9-3 lists GET_STATUS as a standard
device request and §9.4 states "USB devices must respond to standard device
requests, even if the device has not yet been assigned an address or has not
been configured." With the block disabled, `GET_STATUS` falls through to the
final `else` and the device answers the Data stage with a zero-length DATA1.

The chain, verified against Linux v6.12:

1. `drivers/usb/core/hub.c:3628` — `finish_port_resume()` issues
   `usb_get_std_status(udev, USB_RECIP_DEVICE, 0, &devstatus)` on **every**
   resume, with the comment "10.5.4.5 says be sure devices in the tree are still
   there." This is the only routine GET_STATUS to a non-hub device in the whole
   of `drivers/usb/core` — the other three call sites are hub-only
   (`hub.c:1627`, `hub.c:5471`) or fire on a failed suspend (`driver.c:1462`).
2. `drivers/usb/core/message.c:1153-1173` — `usb_get_status()` accepts only a
   reply of exactly 2 or 4 bytes; `default: ret = -EIO`. A zero-length answer is
   **`-EIO`**.
3. `hub.c:3631-3636` — `if (status && !udev->reset_resume && udev->persist_enabled)`
   sets `reset_resume = 1` and `goto retry_reset_resume`, which is
   `usb_reset_and_verify_device()` at `hub.c:3615-3619` — **a full USB reset and
   re-enumeration.** Then GET_STATUS runs again, fails again, and the retry is
   not repeated.
4. `hub.c:3819-3822` — `if (status < 0) { dev_dbg("can't resume, status %d");
   hub_port_logical_disconnect(hub, port1); }`.

**Observable effect:** every system suspend/resume costs the device a bus reset
and a logical disconnect, after which it is rediscovered from scratch. Open
handles break, the `hidraw`/`input` node is renumbered, and drivers see a
disconnect/reconnect. The device does come back — which is precisely why this
has never been filed as a bug against a device that "works".

**A STALL would not fix it.** A protocol STALL surfaces as `-EPIPE`, which is
also non-zero at `hub.c:3631` and `< 0` at `hub.c:3820`. The fix is to *answer
the request*, which is §9.4's actual demand.

Not verified: Windows and macOS resume behaviour; whether a given system
suspends a low-speed HID port at all (system suspend does; HID autosuspend is
usually off).

### 4.2 Rank 2 — the Windows 0xEE probe wants a STALL and says so

Microsoft, *Microsoft OS Descriptors for USB Devices*
(learn.microsoft.com/windows-hardware/drivers/usbcon/microsoft-defined-usb-descriptors),
verbatim:

> "The operating system queries for the string descriptor at index 0xEE during
> device enumeration--before the driver for the device loads... If a device
> doesn't contain a valid string descriptor at index 0xEE, **it must respond
> with a stall packet** (in other words, a packet that contains a packet
> identifier of type STALL), which is described in the 'Request Errors' section
> of the Universal Serial Bus Specification. If the device doesn't respond with
> a stall packet, the system issues a single-ended zero reset packet to the
> device, to help it recover from its stalled state **(Windows XP only)**."

and

> "If the device doesn't provide a valid response the first time that the
> operating system queries it for a Microsoft OS String Descriptor, the
> operating system makes no further requests for that descriptor."

with the result cached in `osvc` under
`HKLM\SYSTEM\CurrentControlSet\Control\UsbFlags\vvvvpppprrrr`.

The device does hit this. `demo_gamepad`'s descriptor table
(`demo_gamepad/usb_config.h:154-165`) is keyed `(wIndex<<16)|wValue`; the probe
is `wValue = 0x03EE, wIndex = 0`, key `0x000003EE`, no entry, so
`rv003usb.c:457-471` leaves `e->opaque = 0` and the IN is answered with a
zero-length DATA1 (`rv003usb.c:272-275`).

**Damage:** one extra bus reset per device per machine, on Windows XP only. On
Vista and later Microsoft documents no recovery action and the probe is not
repeated, so the practical consequence there is **not verified** and is probably
nil. This is the case where a STALL is *explicitly required by name* and where
it is cheapest to give — and its payoff is nonetheless small.

### 4.3 Rank 3 — a SET_* request the device ignored is reported as success

For a control request with no data stage, the host's Status stage is an IN, and
`usb_pid_handle_in` answers it with a zero-length DATA (`rv003usb.c:272-275`).
Table 8-7 defines exactly that as **"Function completes"**. So the device
reports success for every SET_* it silently dropped.

The plausible instance is `CLEAR_FEATURE(ENDPOINT_HALT)`. Linux
`usb_clear_halt()` (`message.c`, `usb_control_msg_send` then
`/* don't un-halt or force to DATA0 except on success */ if (result) return
result; ... usb_reset_endpoint(dev, endp);`) resets the **host's** data toggle
to DATA0 on a success it was handed for nothing, while the device's
`e->toggle_in` (`rv003usb.h:162`) is untouched.

**But it self-heals.** §8.6.3/§8.6.4: the host receives the next report with the
unexpected toggle, discards it as a retransmission, and ACKs anyway (Table 8-5);
`usb_pid_handle_ack` (`rv003usb.c:519-524`) flips `e->toggle_in` on that ACK and
the two are back in step. Cost: **one dropped report**, not a wedged endpoint.

The other instances are inert on the demos as configured:
`SET_FEATURE(DEVICE_REMOTE_WAKEUP)` is never sent because
`demo_gamepad/usb_config.h:78` sets `bmAttributes = 0x80` (bus-powered, no
remote wakeup); `SET_INTERFACE` to a non-existent alternate setting is not
something a compliant host issues.

Note what this rank implies for any fix: **"no handler matched" cannot be used
to mean "unsupported".** `SET_CONFIGURATION` and `SET_INTERFACE` are mandatory
and the stack deliberately no-ops them, relying on this very zero-length status
to report success (`rv003usb.c:493-497` says so in comments). A blanket STALL on
the unmatched branch would break enumeration outright. §5 respects that.

### 4.4 Rank 4 — no NAK on an interrupt IN with nothing to send (§8.5.4)

§8.5.4 is explicit: "If the endpoint has no new interrupt information to return
(i.e., no interrupt is pending), the function returns a NAK handshake during the
data phase." The stack cannot, and instead requires the handler to produce a
packet synchronously inside the ISR (`rv003usb.h:68`). `demo_pikokey_hid`
(`demo_pikokey_hid/pikokey.c:44-67`) sends a full 8-byte report on **every** poll
whether or not anything changed.

**What breaks: nothing.** The host receives a valid report each interval; a
duplicate report is not an error. The costs are bus bandwidth (8 bytes per
interval instead of a 1-byte NAK — irrelevant on a low-speed bus with one
device) and the loss of the option to have nothing ready, which the design has
already foreclosed by calling the handler from the ISR.

This is where V-USB genuinely differs (§2.2) and where the difference is
**structural, not a defect**: V-USB's application fills a buffer outside the
ISR, so its ISR routinely finds nothing and must say so. A handler that
returns without transmitting is the one case that does misbehave — on the
RISC-V it produces silence and a host timeout, which is `RV003USB_USER_DATA_HANDLES_TOKEN`'s
documented mechanism (§1.3) rather than an accident.

### 4.5 Rank 5 — the OUT to the IN-only endpoint

Covered in §3.3. No compliant host issues it (§5.3.1), no §8.4.6 table covers
it, and V-USB has the identical hole. Reachable only by a compliance tool or a
deliberately misbehaving host. **Not worth fixing** — see §5.4.

### 4.6 What the enumeration simulator saw, re-read

| simulator finding | verdict |
|---|---|
| unsatisfiable GET_DESCRIPTOR returns a zero-length DATA1 | real, spec-contrary (§9.4.3, §9.2.7), but the same policy V-USB ships (§2.4); on Linux indistinguishable from a STALL (§3.2); matters on Windows only for 0xEE (rank 2) |
| an OUT to the IN-only endpoint 1 is ACKed | real, unreachable from a compliant host, V-USB does the same (rank 5) |
| `usb_send_empty` answers "nothing to send" with a zero-length DATA rather than NAK | correct as a description; as a *defect* it is rank 4, and the design has foreclosed the situation |
| *(not seen by the simulator)* GET_STATUS unanswered | **rank 1, the only routine real-host failure** |

The simulator was reading the IN path and could not see rank 1 because it never
suspended the bus. That is worth recording as a gap in `ENUMERATION_SIM.md`, not
just here.

---

## 5. The minimal change

Nothing below has been applied. Sizes are **measured**, not estimated:
`rv003usb/rv003usb.c` compiled alone at `-Os` against `demo_gamepad`'s
`usb_config.h` and `tools/sim_shim/ch32fun.h` (the enumeration simulator's own
recipe, `tools/usb_enum_sim.py:341-355`), for both ISAs:

```
arm-none-eabi-gcc  -Os -mcpu=cortex-m0plus -mthumb
riscv64-unknown-elf-gcc -Os -march=rv32ec_zicsr -mabi=ilp32e
```

| variant | M0+ `.text` | Δ | RV32EC `.text` | Δ | `.bss` |
|---|---|---|---|---|---|
| baseline (as committed) | 788 | — | 874 | — | 92 |
| **A1** GET_STATUS + GET_INTERFACE, upstream's block enabled verbatim | 816 | **+28** | 906 | **+32** | 92 |
| **A2** GET_STATUS only, `wLength` clamped to 2 | 812 | **+24** | 906 | **+32** | 92 |
| **B** STALL an unknown descriptor | 812 | **+24** | 900 | **+26** | 92 |
| **A2 + B** | 836 | **+48** | 932 | **+58** | 92 |

RAM cost of every variant: **zero.**

### 5.1 Change A — answer GET_STATUS (recommended)

**Touches `rv003usb/rv003usb.c` only.** Delete the `#if 0` / `#endif` at
`rv003usb.c:480` and `:492`.

Two corrections belong with it, and they are why A2 is preferred to A1:

* `e->max_len = wLength` (`rv003usb.c:485`) is unclamped, and `always0` is
  **four bytes** (`rv003usb.S:1195-1196`, `.byte 0x00` "Automatically expands
  out to 4 bytes"; `rv003usb.S:1204`, `.word 0x00`). A host asking for more than
  4 bytes would read past it. §9.4.5 fixes the reply at two bytes, so clamp:
  `e->max_len = (wLength > 2) ? 2 : wLength;`
* the reply is hard-coded `0x0000`, i.e. bus-powered, remote wakeup off. Correct
  for `demo_gamepad` (`usb_config.h:78`, `bmAttributes = 0x80`) and wrong for a
  self-powered build. Worth a `#if` on the build's own `bmAttributes` if anyone
  ships one; not worth code today.

**ISA knowledge required: none.** No new primitive; `always0` and the existing
descriptor-reply machinery do the work.

**Upstream-divergence cost: the smallest possible.** The code being enabled is
upstream's own, at upstream's own line numbers, and the diff is two
preprocessor lines plus one clamp. It is a clean upstream PR with a Linux
`hub.c` line number as its justification, and the fork carries at most a
three-line delta until it lands.

### 5.2 Change B — STALL an unknown descriptor (optional, cheap, correct)

**Touches `rv003usb/rv003usb.c` only.** Three edits, using the already-unused
`reserved1` byte of `struct usb_endpoint` (`rv003usb.h:165`; grep confirms no
reader or writer anywhere in the tree), so the `_Static_assert` at
`rv003usb.h:174` and the RAM footprint are untouched:

1. `rv003usb.c:396-400` — add `e->reserved1 = 0;` beside `e->custom = 0;`, so
   the flag is cleared for every SETUP.
2. after the GET_DESCRIPTOR search loop (`rv003usb.c:457-471`) — add
   `if( !e->opaque ) e->reserved1 = 1;`
3. `rv003usb.c:272` — ahead of the empty/data decision in `usb_pid_handle_in`:
   `if( e->reserved1 ) { usb_send_data( 0, 0, 2, 0x1E ); return; }`

Scope is deliberately narrow: **only** GET_DESCRIPTOR-not-found, which is the
one case §9.4.3 names by itself and the one Windows names by itself. It must not
be widened to "no branch matched" — §4.3 explains that SET_CONFIGURATION and
SET_INTERFACE live in that branch on purpose and a STALL there breaks
enumeration.

No status-stage handling is needed: §8.5.3.1, "If a control pipe returns STALL
during the Data stage, there will be no Status stage for that control transfer."
The single IN-side STALL is the whole of it.

**ISA knowledge required: none.** `usb_send_data(0, 0, 2, 0x1E)` uses only the
prototype at `rv003usb.h:100`, in exactly the form the C layer already uses for
ACK at `rv003usb.c:508` and that `rvswdio_programmer.c:598` already uses for NAK.
Nothing is asked of `rv003usb.S`.

**Cost on the RISC-V: zero beyond the +24/+26 B above.** `usb_send_data` is
PID-agnostic and stuffing-general, and `0x1E` needs no stuffed zero anyway
(§3.0).

**Cost on the M0+ port: one untimed routine, and this is the part that must not
be skipped.** Today a C-layer STALL on an IN would come out as *silence*, not a
STALL, because Design B's IN path refuses to render it:

```
doc/py32/engine16_merged.S:2729   cmp r2, #0        /* poly_function != 0 means no CRC, */
doc/py32/engine16_merged.S:2730   bne .Lir_bail     /*   i.e. a handshake, not a DATA   */
doc/py32/engine16_merged.S:2733   cmp r3, #0xC3     /* DATA0                            */
doc/py32/engine16_merged.S:2735   cmp r3, #0x4B     /* DATA1, and nothing else          */
doc/py32/engine16_merged.S:2736   bne .Lir_bail
```

A bail leaves the endpoint **unarmed**, the G5 pattern compare
(`doc/py32/design_b_in.md` §6) then fails, and the engine emits nothing — a host
timeout instead of a handshake, which is a *worse* answer than the zero-length
DATA it replaced. So Change B on the port requires `usb_in_render` to render a
handshake record: relax the two guards for the handshake PIDs, and write
`pid = 0x1E`, `groups = 0`, `tail = usb_ti_tails[0]`. That tail already is
`usb_ti_eop + 1` (`engine16_merged.S:2997-2998`) — a zero-bit record goes
straight to the EOP — so the downstream emitter needs no change at all.

**No timed path moves and no cycle budget changes.** `usb_in_render` is
explicitly untimed (`engine16_merged.S:2719-2722`, "One taken branch on the good
path, in an untimed routine"), and the arm record's `pid` is read at emit time
by G6 (`design_b_in.md` §6, 4 cycles) regardless of what it holds. Estimated
~10 Thumb instructions; **not measured**, because it needs the record-layout
work and the `prerender.py` cross-check (`doc/py32/prerender.md` §6) re-run
against it.

### 5.3 The DATA-direction handshake is already free

For completeness, and because it changes what any future NAK proposal costs:
the port's handshake emitter already reads its PID from RAM.

```
doc/py32/engine16_merged.S:256    #define TB_PID_OFS      27
doc/py32/engine16_merged.S:257    #define TB_PID_ACK      0xD2   /* ACK, the only PID a DATA is owed */
doc/py32/engine16_merged.S:1328-1331   TBPIDLOAD:  mov r2,r9 ; ldrb r4,[r2,#TB_PID_OFS]
doc/py32/engine16_merged.S:2133,2140   movs r1,#TB_PID_ACK ; strb r1,[r2,#TB_PID_OFS]
```

Emitting NAK or STALL there instead of ACK is a **different constant in a store
that already happens: 0 cycles, 0 bytes.** The store is at `.Lusb_done_owed`,
past every timed path (`engine16_merged.S:2122-2125`). If a reason ever appears
to NAK an OUT, the engine side of it is already paid for.

### 5.4 What not to do

* **Do not add direction tracking to `struct usb_endpoint`** to stop ACKing the
  OUT to EP1 (rank 5). No compliant host issues the transaction (§5.3.1), no
  §8.4.6 table covers it, V-USB has the same hole, and the struct is
  size-asserted at 32 B (`rv003usb.h:174`) with the index used as a shift.
* **Do not add NAK to the interrupt IN path** (rank 4). It contradicts
  `rv003usb.h:68`, which is the contract that lets the stack have no transmit
  buffer at all. micronucleus is not a counter-example: its application runs
  outside the ISR, so it needs the NAK that this design has engineered away.
* **Do not STALL the unmatched-request branch.** §4.3.

### 5.5 Recommendation

Do **A2**. It is 24/32 bytes, no RAM, no ISA knowledge, an upstream diff of
three lines, and it is the only change here that fixes something an ordinary
host hits on an ordinary day.

**B** is correct, cheap on the C side, and buys one avoided bus reset on Windows
XP. Do it when `usb_in_render` is next open, not before — and never ship the C
half without the `usb_in_render` half, or the port answers with silence.

Leave the rest. The zero-length answer to an unsupported control request is an
accepted simplification with 20 years of V-USB deployment behind it, and this
note now says exactly where it bites.
