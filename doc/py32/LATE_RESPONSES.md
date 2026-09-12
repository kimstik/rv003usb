# The responses that were both unowed and late

## What this closes

`doc/py32/BUS_INVARIANT.md` states one property and checks it mechanically over
random bus traffic:

> the device may drive the bus only when it owes a response to a packet that
> was addressed to it and that it accepted.

Entitlement is computed from the specification and from what the host sent,
never by asking the engine. After the engine's own arm gate
(`BUS_COLLISIONS.md`) the in-deadline violations were zero and one class
remained — every one of it past the deadline:

```
packets driven at the device: 240
INVARIANT VIOLATIONS: 51
the device answered             times past tau+124   the rule
a DATA with no token               51       51   S8.5.3: no transfer was armed
```

With the gate described here:

```
packets driven at the device: 240
INVARIANT VIOLATIONS: 0
```

Reproduce with `python3 tools/usb_bus_fuzz.py --seqs 40 --len 6`.

## Why a DATA packet needs a gate at all

A DATA packet carries **no address**. USB 2.0 §8.4.1, Figure 8-4: SYNC, PID,
payload, CRC16, and nothing else. So the only thing on the wire that says a
DATA belongs to this device is that a SETUP or OUT token addressed to it came
immediately before.

`TB_OWED` is exactly that statement. It is written at `.Lusb_done_owed`, past
the address filter and the endpoint bound, and cleared by the tail of every
other packet — and the gate reads it *before* this packet's own tail rewrites
it.

Without the gate, `rv003usb.c`'s `usb_pid_handle_data` falls through to
`just_ack` for every DATA it is handed. That is upstream's behaviour unchanged
(`cnlohr/rv003usb@80b1893`, `rv003usb.S:518-534` has no arm test either), which
is why this is a port-level gate and not a bug report against upstream.

## The part that makes it matter: a hub broadcasts

The first reading of this defect — recorded in `BUS_INVARIANT.md` and repeated
in conversation — was that a real host never sends a DATA without a token, so
the shape was unreachable in practice and only a conformance blemish.

**That is wrong, and §11.8.2 is why.** A hub repeats downstream low-speed
traffic to **every enabled low-speed port**. A second low-speed device on the
same hub therefore puts its SETUP data on this device's wire. Before the gate,
that packet was:

* **answered** — an ACK at **tau+367**, three times the §7.1.18 turnaround
  deadline, landing on top of the real recipient's ACK and the host's next
  token;
* and allowed to **flip `e->toggle_out`** for a transfer this device is not
  part of, so the next genuine OUT to this device would have been rejected as
  a retransmission.

The second is the worse of the two and has nothing to do with timing. So this
was never a conformance blemish; it was a device that misbehaves whenever it
shares a hub.

## Where the gate belongs, and what it costs

**In the engine, not in `rv003usb.c`.** The arm state is the engine's: the C
layer's `ist->setup_request` is set by SETUP only, never by OUT, and outlives
the data stage, so it cannot answer the question. The shared C file must not
learn the ISA or grow a field the other port does not have.

Three instructions, **6 cycles**, all in the post-EOP tail:

```
	ldrb    r1, [r6, #TB_OWED_OFS]	/* r6 == r9 == usb_rxbuf+2 */
	cmp     r1, #0
	beq     .Ldone_t		/* nothing armed: drop it, S8.5.3 */
```

Not one cycle inside a bit cell, and not one ahead of a response that is
already on the wire by then on the Design B path — so the turnaround is
untouched.

The flag is written in **every** build, not only under `USB_TURNAROUND_B`. It
now has two readers: the EOP stub's arm gate decides whether to answer *early*,
and this gate decides whether to answer *at all*. A build with Design B off
still has to know which DATA packets are its own; making the write conditional
would have told it "none of them".

## Verified

| | before | after |
|---|---|---|
| `usb_bus_fuzz.py --seqs 40 --len 6` | 51 violations of 240 | **0 of 240** |
| `usb_enum_sim.py` | 186 descriptor bytes, 139 packets, 65 responses | **186 bytes, 139 packets, 64 responses** |
| timed cells, six `USB_RX_CHECK` x `USB_ENGINE16_FLASH` builds | all 16 | **all 16** |
| `engine16_rx_model.py` | 415 packets, 0 failures | **415, 0** |
| `bus_arm_gate.py` | 8 silent, control fires | **8 silent, control fires** |

The response count falling by one is the gate working: the enumeration
sequence contains one DATA that nothing armed, and the descriptor bytes still
verify byte-for-byte, so nothing that was needed was dropped.

## What this does not cover

* One invariant. A device that drives when entitled but drives the *wrong
  thing* passes it; `usb_enum_sim.py` is what checks content.
* The fuzzer never sends SET_ADDRESS, so address-transition behaviour is
  unexercised.
* `ENDPOINTS=2`, one demo, no clock error, no jitter. Model cycles, not
  silicon.
