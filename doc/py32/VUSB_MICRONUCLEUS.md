# V-USB and micronucleus — read, not recalled

## First: I was wrong to refuse

I told the VUSB competitor "do not attempt to fetch V-USB or micronucleus
source (network access is restricted)". That was an assumption, and I had
already disproved it myself by cloning `py32f0-template` from GitHub earlier in
the same session. Both repositories clone in seconds. The competitor was made to
work from recollection for no reason, and its entry — which won on cycles — was
built without the source it was named after.

## How V-USB fits 8 cycles per bit

From `usbdrv/usbdrvasm12.inc` (12 MHz, so 8 cycles per bit — a *harder* budget
than our 16). The receive loop, `rxLoop:`, per bit:

```
in   x2, USBIN      ;1   sample
andi shift, 0xf9    ;1   mask: were the last six bits all ones?
breq unstuff1       ;1   ...the flags say so, no counter needed
eor  x1, x2         ;1   NRZI: differ = 0, same = 1
bst  x1, USBMINUS   ;1   extract that bit into the T flag
bld  shift, 1       ;1   deposit it into the shift register
```

Three things follow, and only one of them is what I assumed:

* **It is unrolled by byte, exactly like ours** — eight inline bit slots, with
  out-of-line `unstuff0..7` handlers. So unrolling is not what makes them small.
  We were right about that and it is not the difference.
* **It uses no tables at all.** The work our table does — NRZI decode, stuffing
  counter, bit deposition — is done by `bst`/`bld`, AVR's single-bit transfer
  through the T flag, in two instructions. **Cortex-M0+ has no equivalent
  primitive**, which is precisely why our engine reached for a lookup. That is a
  real ISA difference, not a design failure on our side.
* **Bit-stuffing needs no counter.** `andi shift, 0xf9` masks the shift register
  so that six consecutive ones make the result zero, and `breq` catches it. The
  mask differs per bit position, which is *why* the loop must be unrolled.

## The size difference is mostly one decision, and it is theirs

`usbdrv.c:580-586`, their own comment:

> *We could check CRC16 here -- but ACK has already been sent anyway. If you
> need data integrity checks with this driver, check the CRC in your app code
> and report errors back to the host. Since the ACK was already sent, retries
> must be handled on application level.*

**V-USB does not verify the receive CRC.** `usbCrc16` appears only as
`usbCrc16Append` on transmit. The 12 MHz receive assembly contains zero
occurrences of "crc".

That single decision accounts for most of the gap. Our two engines carry 1024 B
of `T_CRC16` — a third of the whole 3180 B — to fold CRC16 into the bit cell at
2 cycles. V-USB spends nothing because it checks nothing.

So our size is not waste; it is a **different contract**. We detect corrupted
packets and can refuse to ACK them. V-USB ACKs first and leaves integrity to the
application. Both are defensible. What is not defensible is carrying that cost
*twice* — the duplicated `T_CRC16` is 512 B of pure redundancy and both notes
already identify sharing it as free.

It also reframes the turnaround problem. Our SYNC-first design keeps the ACK PID
downstream of the CRC residue check so a false ACK is structurally impossible.
V-USB's answer to the same deadline is simply to ACK before knowing. If we ever
accept their contract, the 6.5-bit-time problem largely dissolves — and so does
1024 B.

## micronucleus — the real numbers

Compiled bootloaders from `firmware/releases/`, counting only data records:

| target | total |
|---|---|
| t167_default | 1342 B |
| t88_default | 1350 B |
| t45_default | 1514 B |
| Nanite841 | 1548 B |

That is the **entire bootloader** — V-USB driver, USB stack, descriptors, flash
programming, timeout logic — in about 1.4 KB. Against our 3180 B for two engines
and nothing else. The comparison is not like-for-like (they check no CRC, they
target a 5-cycle-per-bit-slot ISA with `bst`/`bld`), but the order of magnitude
is a fair rebuke.

micronucleus reaches it by vendoring V-USB with everything optional switched off
— `usbdrv.c` is 15809 B of source against upstream's 25010 — and all descriptor
properties set to 0 in `usbconfig.h`.

## The polled main loop

`firmware/main.c:436-470` does not sit in `usbPoll()` waiting for the ISR. It
polls `USBIN & USBMASK` directly in a counted loop to detect SE0, times its own
5 ms window at 15 cycles per iteration, and at `:563` checks whether an
interrupt landed during processing. Reception still uses V-USB's ISR; the
*bootloader's control flow* is polled.

Worth noting against our F-1 defect: their reset detection never depends on an
interrupt firing at all.

## The piece we should take — oscillator calibration

`firmware/osccal.S`, header verbatim:

> *Ralph Doncaster 2020 — optimized OSCCAL tuning from low-speed USB SOF every
> 1ms*

2307 B of source, public domain, and it is exactly the problem F002B has: trim
an internal RC oscillator against the host's 1 ms keepalive, with no crystal.
`STATE.md` lists that as an open task and `turnaround.md` observed that the
keepalive is the only accurate reference available before enumeration.

They solved it in 2020 and published it. We should read it before writing ours.

## What this changes

1. The prior-art brief was wrong to work from recollection. Any future
   competitor gets the sources.
2. Our unrolling is vindicated — V-USB does the same, at a harder budget.
3. Our tables are the cost of checking CRC, which V-USB does not do. Keep the
   check, drop the duplicate: −512 B free.
4. `osccal.S` should be read before the F002B calibration task starts.

---

# Corrections — this file was wrong twice, both in V-USB's favour then against it

Measured after `avr-gcc` was installed and V-USB was actually compiled. See
`SIZE_COMPARISON.md`.

**1. "It uses no tables at all" is false as stated.** It holds only for the
variants that skip the receive CRC. Building with `USB_CFG_CHECK_CRC=1` adds two
256-byte tables and costs **+732 B** — 804 B becomes 1536 B — and that option is
legal only at 18 MHz. So when V-USB does check the CRC it pays *more* than we
do, and has to give up two clock options to afford it. Our 800 B of tables is
not the outlier this file made it look like.

**2. "V-USB does not filter by device address" is false** — I claimed that as
ours alone. It does, at `asmcommon.inc:65-70`: `lds shift, usbDeviceAddr` /
`cpse x2, shift` / `rjmp ignorePacket`. Our F-7 was catching up to it, not
adding something new.

## The number that was missing

The V-USB assembly driver alone, no descriptors, no `usbdrv.c`:

| build | recv CRC | bytes |
|---|:--:|---:|
| micronucleus config, 12 MHz | no | 668 |
| micronucleus config, 16.5 MHz | no | 700 |
| upstream, 16.5 MHz | no | 724 |
| upstream, 18 MHz | no | 804 |
| upstream, 18 MHz, CRC checked | **yes** | **1536** |

## What our own V-USB-shaped engine costs

`doc/py32/engine16_minimal.S` — same choices, our core: no receive CRC, no
tables, masked-compare unstuffing, unrolled byte. **712 B**, zero tables,
verified. Against the 1624 B chargeable to receive today, that is **−56 %**, and
what it gives up is CRC16, CRC5, the branchless cell and per-bit SE0.

## Why we are bigger, decomposed

Assembled: 712 against 520, so 1.37x. **Net of timing filler: 220 against 194,
+13 %.** The 248 B engine gap splits exactly two ways — **+26 B of real
instructions**, which is irreducible, and **+222 B of padding** to fill a
16-cycle cell where theirs fills 8. Almost all of the difference is our clock,
and our clock is forced by those +26 B.

The missing instruction is not the one this file guessed. `bst`/`bld` costs us
+1 per slot; the larger loss is **no AND-immediate** at +2 — `andi shift,0xf9`
masks the data register in place with a per-position constant, and that single
instruction is V-USB's *only* reason to unroll.

32-bit width is a wash. It pays in exactly one place: a six-bit window that
never wraps can live in its own register, so the stuffing test is one shift with
the same immediate everywhere and the escapes need no repair — worth −6 B
against +48 B.

The real handicap is neither width nor a missing instruction: **a taken branch
here is 2-3 cycles rather than a fixed 2**, so V-USB's escape structure would
drift by up to ~12 cycles across a maximal DATA0 packet on this core. That is
what our `T_UT` table actually buys, and it is why the structure could not be
copied even if the bytes were free.
