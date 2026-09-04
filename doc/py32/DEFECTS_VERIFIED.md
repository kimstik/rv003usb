# PY32 branch — defects verified in the source, not asserted

Each entry below was located in the actual source on the `py32` branch at
0ad3c42 and is quoted with file:line so it can be checked in seconds. The plan
listed several of these as claims; this file is the evidence. Where a defect
turns out to be inherited from mainline rather than introduced by the branch,
that is stated — blaming the branch for an upstream property would be exactly
the kind of unfounded claim this project rejects.

## D-1. Endpoint bound check is off by one — CONFIRMED, branch-introduced

`rv003usb/rv003usb-arm.S:274-277`:

```
	mov r2, #0xf  // endp
	and r2, r3
	cmp r2, #ENDPOINTS
	bhi done_usb_message_in // Make sure < ENDPOINTS
```

`bhi` is unsigned *higher*, so it rejects only `endp > ENDPOINTS`. The value
`endp == ENDPOINTS` falls through and is passed to the token handlers. The
comment on the very same line states the intended semantics — "Make sure <
ENDPOINTS" — so this is a coding slip, not a design choice.

The RISC-V original gets it right, `rv003usb/rv003usb.S:526-528`:

```
	c.andi a2, 0xf    // endp
	li s0, ENDPOINTS
	bgeu a2, s0, done_usb_message // Make sure < ENDPOINTS
```

`bgeu` rejects `endp >= ENDPOINTS`. So the defect was introduced in the Thumb
port, not inherited.

Consequence. `endp` reaches `usb_pid_handle_out/in/setup` unmasked
(`call_token_handler`, arm.S:301-305, passes r2 straight through), and each of
them indexes `ist->eps[endp]` with no further check (`rv003usb.c:165, 416`, and
via `current_endpoint` at `:231, 407`). `eps[ENDPOINTS]` is one element past a
`ENDPOINTS`-element array, and `eps[]` is the **last** member of
`struct rv003usb_internal` (`rv003usb.h:200`), so the access runs off the end of
the struct into whatever the linker placed next. With the demo's `ENDPOINTS 2`
that is `eps[2]`, six bytes past the end, reachable by any host that sends a
token addressed to endpoint 2 — i.e. by an unprivileged device on the bus, not
only by our own driver.

Fix: `bhs` (equivalently `bcs`). One instruction, identical encoding size and
cycle count, and it sits in the flash-resident non-timing-critical region after
`se0_complete_flash`, so it cannot perturb any bit cell.

## D-2. RX byte store has no bound check — CONFIRMED, acknowledged in source

`rv003usb/rv003usb-arm.S:145-148`:

```
is_end_of_byte:
	// TODO: prevent buffer overrun
	mov BITCOUNT, #8          // 19
	strb SHIFT_BUF, [r2]      // 20
	add r2, #1                // 22
```

`r2` starts at `rxbuf + 3` (arm.S:80) and is incremented once per received byte
with no limit. The buffer is `rxbuf: .space 3 + USB_BUFFER_SIZE` (arm.S:32) with
`USB_BUFFER_SIZE 12` (`rv003usb.h:126`) = 15 bytes total, and it lands in its own
section `.bss.rxbuf` (measured size 0xf, see BUILD_FACTS.md §3). A packet longer
than the buffer writes past it into adjacent `.bss`. The author's own TODO
records the gap, so this is a known hole rather than a discovery — but it is
unfixed, and it is reachable from the wire.

The awkward part, which the plan must not gloss: `is_end_of_byte` is inside the
cycle-counted RX path (the trailing comments `// 19`, `// 20`, `// 22` are the
cycle budget), so a bound check is not free. Any fix has to be paid for out of
the bit-cell budget or restructured so the check happens off the hot path. That
makes this a real design task, not a one-line patch, and it should be sized as
one.

## D-3. The per-part `#if` variant is never *selected* — CONFIRMED, and narrower than claimed

Both arms assemble cleanly (BUILD_FACTS.md §2: `-DPY32F002Bx5=1` and
`-DPY32F003x4=1` both rc=0). What never happens is selection: `Makefile.py32`
pins `MCU_TYPE = PY32F002Bx5`, so the branch's own build system has never built
the non-F002B arm of any `#if PY32F002Bx5` in `rv003usb-arm.S` (there are five,
at arm.S:402, 415, 444, 490, 530). The correct statement is "never selected by
the build", and the remedy is a build matrix over the supported parts, not an
assembly repair. This matters more after the target flip, since F003/F030
becomes primary and therefore exercises exactly the arm that has never been
built.

## D-4. The branch cannot link as published — CONFIRMED, environmental

`py32f0-template` is an empty submodule on the branch, and it supplies the
linker scripts, startup files and CMSIS device headers
(`Makefile.py32` references `../py32f0-template/Libraries/LDScripts/$(PYOCD_DEVICE).ld`).
Upstream is reachable and pins cleanly at 289ffc8. See BUILD_FACTS.md §6,
including the open vendor-versus-submodule decision.

## D-5. RAM placement of the RX engine is incidental — CONFIRMED, latent

No `.datacode` rule exists anywhere; the section reaches RAM only because it is
swallowed by the stock script's `*(.data*)` wildcard. A script spelling that
rule `*(.data.*)` would place the RX engine in flash silently, with no
diagnostic, invalidating every timing figure while the build still succeeds.
Full evidence and the link experiment in BUILD_FACTS.md §4. This is the most
dangerous item in this file, because unlike D-1 and D-2 it produces no symptom
at build time and an obscure one at run time.

## Not verified here

The plan also lists a stub keepalive servo. That one is a design gap rather than
a source defect, and after the target flip (F003/F030 needs no servo at reset,
−0.04 % from HSI24xPLL2) its shape changes; it is left to the task owner rather
than recorded here as a bug.
