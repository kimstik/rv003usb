# Execution coverage: what has never run

## Why this file exists

On 2026-09-08 the receive engine's SYNC phase lock was found to have an
inverted branch. It could not decode a single packet. It had assembled
cleanly, every timed cell measured exactly 16 cycles, and four independent
bit-exact models passed — because **every one of them starts downstream of the
lock**. The lock had never been executed by anything, in any form, at any
point in the port.

That is a class, not an accident. The useful question is not "what else might
be wrong" but **"what has never run"**, and that is a measurable quantity, not
a matter of opinion.

`tools/engine16_coverage.py` links the two engines, drives them through
`tools/engine16_rx_bus.py`'s emulator with a battery of stimuli, records every
instruction address that actually executes, and reports the remainder grouped
by owning symbol. Reproduce with:

```
python3 tools/engine16_coverage.py
```

## The number

```
battery: 201 runs, 67 distinct stimuli
instructions in the linked image: 2484   executed: 1307 (52.6%)
```

67 stimuli: every PID the stack can meet (SETUP/IN/OUT/SOF/DATA0/DATA1/
ACK/NAK/STALL), payload lengths 0..8 in both data toggles, tokens at three
addresses and three endpoints, a corrupted CRC16, a bit-stuffing violation,
SE0 at entry, an aliased PID, and entry latencies across the whole usable
window at two sub-cycle packet phases — plus a second pass with `TB_OWED` set
at `usb_rxbuf+28` so the Design B ACK path arms.

**Half the linked image has never executed.**

## What is cold, and what that means

| region | state | who could warm it |
|---|---|---|
| `usb_ti_*`, `ti_flush*`, `usb_ti_head` — the **Design B IN response**: the path that emits a DATA packet from inside the ISR | never executed by anything | needs an IN token to a matching address with an endpoint armed, i.e. the real C layer |
| `usb_tx_*`, `usb_send_data`, `usb_send_empty` — the **whole transmit engine** | never executed by anything | `tools/engine16_tx_model.py` transliterates it in Python; nothing runs the object |
| `rx_eop2`, `rx_eop4`, `rx_eop6`, `rx_eop7` — EOP in those cells | cold | EOP cell K is the stuffed-bit count mod 8; needs payloads chosen for K, as `engine16_rx_model.py` does |
| `usb_in_render` | cold **in this battery only** | `tools/prerender_check.py` executes it — 4132 cases, phase 1 |
| `tb_flush7`, `ti_flush*` | cold | flush entries are selected by K, same as above |

Two of these deserve to be stated plainly rather than left in a table.

**The Design B response path is the most analysed code in this project and it
has never run.** `doc/py32/turnaround.md`, `doc/py32/design_b_in.md` and
`doc/py32/audit_discarded.md` between them derive its first-edge time to the
cycle — tau+108 on DATA→ACK, tau+95 on IN→DATA against a deadline of tau+124 —
by summing block costs out of a control-flow-blind tool and tracing the
branches by hand. Every one of those figures describes code no processor,
real or emulated, has ever executed. The ACK half is warm as of this file; the
IN half is not.

**The transmit engine is in exactly the position the receive engine was in
this morning.** Its model is a hand transliteration into Python that reads the
tables out of the assembled object — better than nothing, and it did catch the
trailing-stuffed-zero defect — but it is still a model that starts where the
engine's setup ends, which is the shape of hole the SYNC defect lived in.

## What this measurement does not cover

* The C layer. It is stubbed in this harness; `rv003usb.c`'s control-transfer
  state machine has never been executed in this port at all.
* Anything the emulator cannot model: real EXTI latency, the flash
  controller's timing, GPIO electrical behaviour, the host.
* Coverage is per-instruction, not per-path: a warm instruction reached on one
  arm of a branch says nothing about the other arm.

## The rule this file exists to enforce

A cycle budget, a bit-exact model and a clean assembly are all statements
about code that may never have run. Before believing any of them, ask what
has executed the code — and if the answer is "nothing", that is where to
look first.
