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
and at init (`usbdrv.c:627`) and on every SETUP (`usbdrv.c:449`). The default
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
