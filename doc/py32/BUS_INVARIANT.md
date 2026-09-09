# One invariant, over arbitrary traffic

## Why not more test cases

The scripted enumeration found three bus collisions, each by one hand-injected
error. Finding them one at a time is not a method: it finds the errors
somebody thought to inject. All three are instances of a single property,
and the property is checkable mechanically:

> **The device may drive the bus only when it owes a response to a packet that
> was addressed to it and that it accepted.**

`tools/usb_bus_fuzz.py` generates random packet sequences, runs each through
the assembled stack on `usb_enum_sim.py`'s cycle-clocked emulator, and after
every packet asks whether the device drove and whether it was entitled to.
**Entitlement is computed from the specification and from what the host sent**
— never by asking the engine. A checker that shares a source with the artifact
proves nothing about it, which is how the SYNC defect survived four models.

```
python3 tools/usb_bus_fuzz.py --seqs 40 --len 6
```

The oracle is sanity-checked before it is believed: on a legitimate
SETUP → DATA0 → IN sequence it agrees with the device at every step, and the
one place it disagrees is the known defect — the device answering the host's
own ACK.

## The result

```
packets driven at the device: 180
INVARIANT VIOLATIONS (device drove when it owed nothing): 61

the device answered             times past tau+124   the rule
a DATA with no token               36       36   S8.5.3: no transfer was armed
a token for another address        10        0   S8.3.2.1 / S9.4.6: not ours
a handshake                         6        0   S8.4.4: a handshake is never answered
an IN for a dead endpoint           4        0   S8.4.1: endpoint out of range
unclassified                        4        0
a SOF token                         1        0   S8.4.3: SOF carries no response
```

**A third of arbitrary bus traffic makes this device drive the bus when it
owes nothing.**

## The split is the finding

The `past tau+124` column separates two mechanisms with two different owners.

**Within the deadline — 25 violations — is the engine's in-ISR response arm.**
`TBARM` drives before the address and endpoint are compared, and the EOP
decision reads a PID byte the current packet has not committed. These are the
three collisions the enumeration found, and the fuzzer says they are five
shapes rather than three.

**Past the deadline — 36 violations, all of them — is the C layer.** Probed
directly: a bare DATA0 on a fresh machine, with `TB_OWED` clear, is answered
with ACK (PID 0xD2) at **tau+359**, against a deadline of tau+124. The engine
is not at fault: the packet is well formed, so it is passed up, and
`rv003usb.c` acknowledges a data packet for which no SETUP or OUT token was
ever seen. It is both a protocol violation and, at tau+359, far too late to
be anything but a collision.

That second class was **not** among the three found by hand. It is what the
systematic form buys over the scripted one.

## What this does not check

* One invariant. A device that drives when entitled but drives the *wrong
  thing* passes this check; `usb_enum_sim.py` is what checks content.
* The device's address never changes: the fuzzer sends no SET_ADDRESS, so the
  oracle can stay simple. Address-transition behaviour is unexercised here.
* `ENDPOINTS=2`, one demo, one entry latency, no clock error, no jitter.
* Model cycles, not silicon.
