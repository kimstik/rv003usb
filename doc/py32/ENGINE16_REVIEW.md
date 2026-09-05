# ENGINE16_REVIEW — adversarial review of the merged RX engine and the TX engine

Subjects: `doc/py32/engine16_merged.S` (RX, 727 lines), `doc/py32/engine16_tx.S`
(TX, 576 lines), and their seam with `rv003usb/rv003usb.c`, which must not
change.

Method, as `ENGINE16_CATALOG.md` §6 requires: both files assembled with
`arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c`, then
checked against the object — `objdump -d`, `objdump -r`, `objdump -h`,
`nm -n`, a raw `objcopy -O binary` of `.datacode` for the tables, and
`tools/engine16_cyc.py --exec {ram,flash} --ioport r7 --budget 16` for
instruction pricing only. Control flow was traced by hand in the raw
disassembly; the tool was never used to validate a path (L-2). The reference
for the C seam is the two predecessor engines, `rv003usb/rv003usb.S` (RISC-V,
in tree) and `rv003usb-arm.S` at commit `0ad3c42` (the Thumb port this is a
drop-in replacement for).

Every finding below carries the evidence that produced it. Where I could not
settle something I say so and say what I checked, rather than promoting a
suspicion to a finding.

Both files assemble clean. `objdump -r` shows RX: 9 relocations (5
`R_ARM_THM_CALL`, 4 `R_ARM_ABS32`); TX: 12, all `R_ARM_ABS32`. **No
`R_ARM_THM_JUMP*` in either**, so every branch was range-checked by the
assembler (L-3 satisfied).

---

## Verdict

**Do not run this on hardware yet.** Five defects (F-1..F-5) are of the kind
that stop the stack dead, corrupt memory from the wire or fault the core, and
one more (F-6) makes the only
*linked* build (`INTEGRATION_BUILD.md`) provably miss its own bit cell. The
timed cores of both engines — the bit cells, the tables, the termination
argument, the buffer bound — held up under everything I checked. Every defect
I found is in the untimed seam around them, or in the placement decision, not
in the 16-cycle machinery. That is a repairable position, but it is not a
runnable one.

| # | severity | what | where |
|---|---|---|---|
| F-1 | critical | the EXTI pending flag is never cleared; the ISR re-enters forever | `engine16_merged.S:270-277`, `:639-645` |
| F-2 | critical | handshakes (ACK/NAK/STALL) are rejected by the length gate, so `usb_pid_handle_ack` is dead code and no IN transfer can advance | `engine16_merged.S:501-503` |
| F-3 | critical | token endpoint is unbounded, 0..15, and indexes `ist->eps[endp]` — wire-reachable writes ~240 B past the struct | `engine16_merged.S:561-566` |
| F-4 | high | the `length` argument to `usb_pid_handle_data` is off by 3 against the C layer's convention; a ZLP passes `0xFFFFFFFD` | `engine16_merged.S:536` |
| F-5 | high | the payload pointer handed to C is 2 mod 4, breaking the layer's `__builtin_assume_aligned(...,4)` contract | `engine16_merged.S:537, 569` |
| F-6 | high | the linked build in `INTEGRATION_BUILD.md` puts both engines in flash, where 6 of 8 RX cells and 8 of 10 TX cells are 18 cycles | placement, both files |
| F-7 | medium | the device address is never checked; the engine answers tokens addressed to other devices | `engine16_merged.S:550-590` |
| F-8 | medium | TX accepts `poly_function != 0` with length 1..2 and silently zeroes the payload | `engine16_tx.S:312-315` |
| F-9 | low | the phase-lock comment says 13 cycles of priming; the assembler assertion enforces 14 | `engine16_merged.S:366` vs `:398-401` |
| F-10 | low | no mid-packet resync *and* no SE0 servo: the implied clock requirement is ≈±0.27 %, six times tighter than USB LS allows | `engine16_merged.S:276-277` |

---

## F-1 (critical) — the EXTI pending flag is never cleared

**What.** `engine16_merged.S` contains no access to the EXTI peripheral. Both
of its exits — `usb_rx_keepalive` (`:276-277`, `bx lr`) and `.Lusb_done`
(`:639-645`, `pop {r4-r7,pc}`) — return without writing the pending register. On this part
the pending bit is level-latched into the NVIC, so the handler is re-entered
immediately and forever.

**How verified.**

```
$ grep -n "EXTI\|0x40021800\|0x40010400" doc/py32/engine16_merged.S doc/py32/engine16_tx.S
(no output)
```

Both predecessors do it explicitly, and both name the register:

* `rv003usb-arm.S` @0ad3c42:3,17 `#define EXTI 0x40021800`,
  `#define EXTI_PR_OFFSET 0x0C`; `:332-336`
  ```
  interrupt_complete:
  	ldr r0, =EXTI
  	mov r1, #(1 << USB_PIN_DM)
  	str r1, [r0, #EXTI_PR_OFFSET]
  ```
  That is the immediate predecessor on this exact part, so the address is not
  in question.
* `rv003usb/rv003usb.S:643-646` does the same on the RISC-V (`EXTI_BASE + 20`).

`INTEGRATION_BUILD.md` wires `usb_rx_engine16` straight into the vector slot
(`.thumb_set EXTI2_3_IRQHandler, usb_rx_engine16`), so there is no wrapper
that could be doing it instead.

**Why it matters.** This is not a degradation, it is a live-lock on the first
D− edge. It is also the first thing that happens on any bus at all, including
a keepalive EOP, because `usb_rx_keepalive` returns before anything else runs.

**Suggested fix.** Two instructions plus a literal, in the untimed exit, on
both paths — and the keepalive path needs its own copy because it returns
before the register saves:

```
	ldr     r1, =0x40021800         /* EXTI */
	movs    r0, #(1 << USB_DM_BIT)
	str     r0, [r1, #0x0C]         /* PR, write-1-to-clear */
```

Note this costs `usb_rx_keepalive` its "recognised within a few tens of
cycles" property slightly; that is unavoidable and small.

**Caveat, stated rather than hidden.** I verified the register *address and
offset* from the two predecessor engines in this tree, not from a PY32
reference manual — `py32f0-template` is an empty submodule here
(`DEFECTS_VERIFIED.md` D-4) so I could not read the vendor header. The
*absence* of any EXTI write in the new engines is verified absolutely.

---

## F-2 (critical) — every handshake packet is rejected

**What.** A USB handshake is `SYNC + PID + EOP` and nothing else. This engine
counts SYNC as an emitted byte, so a handshake leaves `r12 = 2`. The tail
rejects anything below 4 *before* it looks at the PID:

`engine16_merged.S:501-503`
```
	mov     r0, r12			/* emitted byte count                */
	cmp     r0, #4			/* SYNC + PID + at least two more    */
	blo     .Lusb_done
	cmp     r0, #12
	bhi     .Lusb_done
```

so `.Lhandshake` (`:596-605`) and the `bl usb_pid_handle_ack` inside it are
**unreachable**.

**How verified.** Traced the emitted-byte count for an ACK through the object.
The priming (`:375-389`) sets `r12 = 0` and enters the chain with SYNC as the
byte in flight. Pass 1 of the chain runs `SEG0..SEG6` for SYNC — `SEG5`
(`:203-215`) does `subs r2, r2, r1` with the commit mask, taking `r12` to 1 —
while sampling the 8 PID wire bits. `SEGA` in cell 7 decodes them. On pass 2,
cell 0 samples the first SE0 bit and takes `beq rx_eop0` (disassembly: `b0:
d0c0 beq.n 34 <rx_eop0>`), so the PID is flushed through `rx_flush0` and
`r12 = 2`. `rx_flush7` sees `r14 == 8` and skips the partial byte
(`:464-466`). The tail then reads `r12 = 2` and takes `blo`.

The predecessor dispatches ACK *first*, with no length test at all —
`rv003usb-arm.S`@0ad3c42:236-260 reads the PID and branches to
`usb_pid_handle_ack` before any CRC or length consideration; `rv003usb.S:505`
does the same (`c.beqz a5, usb_pid_handle_ack`, with the comment "ACK doesn't
need good CRC").

**Why it matters.** `usb_pid_handle_ack` (`rv003usb.c:519-524`) is what flips
`e->toggle_in` and increments `e->count`. Without it the IN data toggle never
advances and `e->count` never moves, so `usb_pid_handle_in` recomputes
`offset = e->count << 3` as 0 forever. Every control-IN transfer longer than
8 bytes — which is every device descriptor read — repeats its first packet
indefinitely. Enumeration cannot complete.

**Suggested fix.** Move the PID validity check and the type dispatch ahead of
the length gate, and apply the length constraints inside the arms that need
them: `.Ltoken` already asserts `r0 == 4` itself (`:551-552`), `.Ldata` needs
`4 <= r0 <= 12`, `.Lhandshake` needs `r0 == 2`. The `cmp r0,#4/blo` and
`cmp r0,#12/bhi` pair then moves into `.Ldata` and costs nothing anywhere
timed.

---

## F-3 (critical) — the token endpoint is unbounded

**What.** `engine16_merged.S:561-566` extracts a full 4-bit endpoint and hands
it to the C layer with no range check:

```
	ldrb    r0, [r6, #2]
	ldrb    r1, [r6, #3]
	lsrs    r2, r0, #7		/* endp bit 0                        */
	lsls    r1, r1, #29
	lsrs    r1, r1, #28		/* endp bits 3:1                     */
	orrs    r2, r1			/* r2 = endpoint                     */
```

`r2` is 0..15 and is passed as the third argument to
`usb_pid_handle_{setup,in,out}` (`:583-591`).

**How verified.** Read the object; there is no `cmp` against `ENDPOINTS`
anywhere between `.Ltoken` (`:550`) and the three `bl`s. Both predecessors
have one: `rv003usb.S:527-528` (`li s0, ENDPOINTS; bgeu a2, s0,
done_usb_message` — correct) and `rv003usb-arm.S`@0ad3c42:252-253
(`cmp r2, #ENDPOINTS; bhi done_usb_message_in` — the known off-by-one, catalog
U-1). The merge dropped the check entirely rather than fixing it.

Consequence traced in the C: `usb_pid_handle_setup` (`rv003usb.c:527-536`)
does `struct usb_endpoint * e = &ist->eps[endp];` and then **writes**
`e->toggle_in`, `e->toggle_out`, `e->count`, `e->opaque`.
`sizeof(struct usb_endpoint)` is 16 (32 under `RV003USB_OPTIMIZE_FLASH`,
asserted at `rv003usb.h:172-176`), and `eps[]` is the last member of
`struct rv003usb_internal` (`rv003usb.h:194`). With the usual `ENDPOINTS 2`,
`endp = 15` writes 208..240 bytes past the end of the struct into adjacent
`.bss`. `usb_pid_handle_ack` compounds it: it uses
`ist->eps[ist->current_endpoint]` with a value an OUT token stored earlier
(`rv003usb.c:285, 521`).

**Why it matters.** This is catalog U-1 made strictly worse: U-1 lets
`endp == ENDPOINTS` through, this lets everything through. It is reachable by
any host, or any other device's traffic on the bus, and it writes rather than
only reads.

**Suggested fix.** Two instructions in the untimed tail, after `orrs r2, r1`:

```
	cmp     r2, #ENDPOINTS
	bhs     .Lusb_done
```

`bhs`, not `bhi` — that is the U-1 repair, applied where the check belongs.

---

## F-4 (high) — the `length` argument is off by three

**What.** `engine16_merged.S:536` computes the fourth argument of
`usb_pid_handle_data` as the payload length:

```
	subs    r3, r0, #4		/* payload length: -SYNC -PID -CRC16 */
```

The C layer's convention is *payload + 3*: `rv003usb.c:300` opens with

```
	length -= 3;
```

**How verified.** Derived the convention from both predecessors, which agree.

* RISC-V, `rv003usb.S:264` sets the store pointer `t2 = sp+DATA_PTR_OFFSET`
  and `:338-339` increments it once per stored byte, the **PID first** (SYNC
  is consumed by the sync detector and never stored). `:484-485` sets the
  `data` argument `a1 = sp+DATA_PTR_OFFSET+1`. `:569-570` then computes
  `sub a3, t2, a1 ; c.addi a3, 1`. With `t2_final - (sp+DPO) = 1 + payload +
  2`, that is `a3 = payload + 3`.
* Thumb port, `rv003usb-arm.S`@0ad3c42:236-238 sets `r1 = rxbuf+3` then
  `add r1,#1`, and `:315-316` computes `sub r3, r2, r1 ; add r3, #1` — the
  same `payload + 3`.

So `length - 3` in C is the payload length, and the merged engine passes the
payload length, which C then reduces by 3 again.

**Why it matters.** Two distinct failures:

1. `usb_handle_user_data(e, epno, data_in, length, ist)` (`rv003usb.c:356`)
   receives `payload - 3`. An 8-byte HID OUT report arrives as 5 bytes.
2. Worse, `length` is `uint32_t`. A zero-length DATA packet — the status stage
   of every control-OUT transfer — gives `0 - 3 = 0xFFFFFFFD`, so
   `rv003usb.c:314`'s `( !ist->setup_request && length > 0 )` is **true** where
   it was false with the old engine, and the user data handler is invoked with
   a length of 4294967293.

**Suggested fix.** `subs r3, r0, #1` in place of `subs r3, r0, #4`
(`payload + 4 - 1 = payload + 3`). One immediate, untimed.

---

## F-5 (high) — the payload pointer is not 4-aligned

**What.** The engine stores SYNC at `rxbuf[0]`, the PID at `rxbuf[1]` and the
payload from `rxbuf[2]`, and hands `rxbuf+2` to the C layer as `data`:

`engine16_merged.S:537` `adds r1, r6, #2 /* payload pointer */`
`engine16_merged.S:569` `adds r1, r6, #2 /* data pointer */`

`usb_rxbuf` is `.balign 32` (`:724`), so `rxbuf+2` is **2 mod 4**.

**How verified.** `objdump -h` on the object: section `.bss.usb_rxbuf`, size
0x20, alignment `2**5`. The pointer arithmetic is a fixed `+2`.

The C layer requires 4-alignment and says so in the code:

* `rv003usb.c:301` `uint8_t * data_in = __builtin_assume_aligned( data, 4 );`
* `rv003usb.c:236` `sendnow = __builtin_assume_aligned( data, 4 );`
* and the word dereferences that alignment licences:
  `rv003usb.c:318-320` `uint32_t * base = ...; base[0] == 0xaa3412fd &&
  (base[1] & 0x00ffffff) == ...`;
  `:334-336` `*DMDATA0 = base[0]; *DMDATA1 = base[1];`
  `:373-378` `dout[0] = din[0]; dout[1] = din[1];`

Both predecessors arrange the alignment deliberately, which is the strongest
evidence that it is a contract and not an accident:

* `rv003usb.S:177` `#define DATA_PTR_OFFSET (59+4)` — 63, so that the data
  pointer at `+64` is word-aligned. The `+4` in `59+4` exists for exactly this.
* `rv003usb-arm.S`@0ad3c42:30-32 `.balign 4 / rxbuf: .space 3 +
  USB_BUFFER_SIZE` with the store base at `rxbuf+3` (`:80`) — the leading 3
  bytes are padding whose only purpose is to put the payload at `rxbuf+4`.

**Why it matters.** ARMv6-M has no unaligned word access: a `ldr`/`str` to an
address that is 2 mod 4 raises a HardFault. `struct usb_urb` is
`__attribute__((packed))` (`rv003usb.h:203-208`) so the setup-packet path is
safe by luck, but the three sites above are plain `uint32_t *` reads and
writes and are compiled under `RV003USB_USE_REBOOT_FEATURE_REPORT`,
`RV003USB_USB_TERMINAL` and `RV003USB_SUPPORT_CONTROL_OUT` respectively. Any
build with one of those on faults on the first matching packet.

**Suggested fix, and it costs nothing in the bit cell.** Move the buffer
origin, not the pointer: bias `r9` by 2 at prime time — `adds r2, #2` before
`mov r9, r2` at `:293` — so SYNC lands at `rxbuf[2]`, the PID at `rxbuf[3]` and
the payload at `rxbuf[4]`, which is 4-aligned. `SEG3` is untouched: it still
masks the count to 0..31 and register-offsets from `r9`, and since the byte
limit is 24 the highest address written becomes `rxbuf+25`, still inside the
32-byte buffer, so the structural bound survives intact. The two `adds r1, r6,
#2` at `:537` and `:569` become `#4`, and the SYNC comparison at `:506-508`
moves from `[r6,#0]`/`[r6,#1]` to `[r6,#2]`/`[r6,#3]` (with the token byte
reads at `:554-562` moving from `#2`/`#3` to `#4`/`#5`). The emitted count in
`r12` does not change, so F-4's repair is still `subs r3, r0, #1`. Do the two
together — they are one decision.

---

## F-6 (high) — the only linked build puts the engines where the cells do not fit

**What.** `INTEGRATION_BUILD.md` builds the 336 B F003x4 image by rewriting
`.section .datacode` to `.section .text.engine16` in both engines, which puts
code and tables in flash. Both engines were ledgered as RAM-resident and both
say so in their own headers (`engine16_merged.S:39-45`,
`engine16_tx.S:44-49`). `INTEGRATION_BUILD.md` presents the resulting timing
as *unverified*, resting on one unknown — the cost of a register-offset table
read from flash. That understates it: the flash configuration is over budget
by the **known** part of the cost model, before that unknown is considered.

**How verified.** `tools/engine16_cyc.py`, spec §2 cost table, both columns:

```
$ tools/engine16_cyc.py rx.o --exec ram   --ioport r7 --budget 16 | grep cell
usb_rx_cell0..7:  16 minimum, every cell
$ tools/engine16_cyc.py rx.o --exec flash --ioport r7 --budget 16 | grep cell
usb_rx_cell0: 18..20   usb_rx_cell1: 18..20   usb_rx_cell2: 16..18
usb_rx_cell3: 18..20   usb_rx_cell4: 18..20   usb_rx_cell5: 16..20
usb_rx_cell6: 16..18   usb_rx_cell7: 16..18
$ tools/engine16_cyc.py tx.o --exec flash --ioport r7 --budget 16 | grep cell
S0,S1,S2,S3,S4,S5,S7: 18 each;  P0,P1,S6: 16
```

(The maxima are EOP exits leaving the cell; the **minima** are the data path,
per L-2. In flash the minima themselves are 18.)

Cell 3 is the clearest case because it contains no table read at all — its
only non-unit instruction is the `rxbuf` store, and the store's cost is in the
measured table:

```
usb_rx_cell3:   18..20 cycles
    118  strb     r3, [r1, r2]                    4   <- 2 from RAM code, 4 from flash
```

That is spec §2's "LDR/STR to RAM: **4** from flash-resident code, 2 from
RAM-resident code" — a column swap, not an unknown. Two cycles over 16, on a
cell that has zero slack, on the data path.

**Why it matters.** `INTEGRATION_BUILD.md`'s own summary — "this image has the
right size and an unverified bit cell" — is too kind. Six of eight RX cells
and eight of ten TX cells are over, and one of them is over for a reason
already measured on silicon. The flash placement is not a pending measurement,
it is a design that does not fit; only the *size* of that miss depends on the
unmeasured flash table read.

**Suggested fix.** Either keep the engines in RAM as written and ledgered
(1812 + 1368 bytes, which `RAM_BUDGET.md` says F003x4 can afford), or take
CLEANSHEET's structure, which has no per-bit table read — but note that even
that does not save cell 3, whose 2-cycle overrun is the `rxbuf` store. If the
engines must live in flash, the store has to leave the cell, which is a
restructuring, not a re-pad. Whichever is chosen, `INTEGRATION_BUILD.md`
should record that the flash configuration is *known* over budget rather than
unmeasured.

---

## F-7 (medium) — no device-address filter

**What.** `.Ltoken` extracts the address (`engine16_merged.S:567-568`) and
passes it to the C layer as argument 0, which every handler ignores. It never
compares it against `ist->my_address`.

**How verified.** No load from `rv003usb_internal_data` occurs anywhere in
`.Ltoken` other than the `ldr r4, =rv003usb_internal_data` that builds the
fifth argument (`:570`). Both predecessors filter:

* `rv003usb.S:529-533`
  ```
  c.beqz a0, yes_check_tokens
  XW_C_LBU(s0, a4, MY_ADDRESS_OFFSET_BYTES)
  bne s0, a0, done_usb_message   // addr != 0 && addr != ours.
  ```
* `rv003usb-arm.S`@0ad3c42:254-258, identical logic.

**Why it matters.** With another device on the same hub, an IN token addressed
to it makes this device drive the bus — a collision, and one that gets worse
the moment F-2 is fixed and the transmit path actually runs. It also means
this device processes SETUP tokens addressed elsewhere.

**Suggested fix.** Restore the predecessor's three-instruction test in the
untimed tail, reading `MY_ADDRESS_OFFSET_BYTES` off the `rv003usb_internal_data`
pointer already in `r4`. Note the offset is config-dependent
(`rv003usb.h:126-143`: 1 normally, 4 under `RV003USB_OPTIMIZE_FLASH`), so use
the macro, not a literal.

---

## F-8 (medium) — TX accepts arguments it silently mishandles

**What.** `engine16_tx.S:312-315` admits `poly_function != 0` with a length of
0, 1 or 2:

```
	cmp     r2, #0
	beq     1f
	cmp     r1, #2			/* a CRC-less packet may carry at    */
	bhi     .Ltx_reject		/* most two bytes - see the .md S6   */
```

For length 1 and 2 the packet is transmitted with the payload replaced by
zeros.

**How verified.** Traced the write/fetch order in the object. On the no-CRC
path the anchor is `r8 = txbuf + length` (`:352-358`, the `adds r0,r0,#2` is
skipped). `TXSEG2`/`TXSEG3` (`:206`, `:213`) unconditionally publish the
running CRC to `[r8+0]` and `[r8+1]` in cells S2 and S3 of *every* byte
window, while `TXSEG0` (`:172`) fetches the next byte in cell S0 of the *next*
window. So a slot written at S3 of window k is read at S0 of window k+1 —
after the write.

* length 0: writes hit slots 0,1 (SYNC, PID). PID is fetched at S0 of the same
  window, before the write. Correct — this is the case `engine16_tx.md` §6.2
  analyses.
* length 1: writes hit slots 1,2. Slot 2 is the payload byte, fetched one
  window later. **Destroyed.**
* length 2: writes hit slots 2,3, both payload bytes, both fetched later.
  **Both destroyed.**

The value written is `~0xFFFF = 0x0000`, because the commit gate never fires on
this path (`TXSEG0`'s mask needs `r3 >= r9 = txbuf+3` and `r3 <= r8`, which is
unsatisfiable when `r8 < txbuf+3`), so the CRC stays at its 0xFFFF init.

`engine16_tx.md` §6.2 states the length-2 case and argues it is harmless
because "the bytes `usb_send_empty` was passing anyway" were zero — but the
same note then re-expresses `usb_send_empty` as a length-0 packet *with* CRC,
so nothing reaches the length-2 arm any more. It does not mention length 1 at
all. Every call site in `rv003usb.c` (`:211, :247, :274, :278, :508`) is
length 0 or CRC-bearing, so nothing currently fires this — it is a latent trap
in the accepted argument domain, not a live failure.

**Why it matters.** The accepted domain is larger than the correct domain, and
the guard is written as though it were exactly the correct domain. A future
caller that sends a 1- or 2-byte CRC-less packet gets silence, not a rejection.

**Suggested fix.** Make the guard match the semantics — on the no-CRC path
require length 0:

```
	cmp     r2, #0
	beq     1f
	cmp     r1, #0
	bne     .Ltx_reject
1:
```

---

## F-9 (low) — the phase-lock comment contradicts the assertion it guards

`engine16_merged.S:359-361`:

> ldr + lsls + untaken bpl = 3, so the priming below must be exactly **13**
> cycles: 9 of setup, **4** of nop.

The code that follows has 9 setup instructions and **5** nops
(`:380-391`), its own inline comment says "9 + 5 = 14 cycles" (`:387-389`), and the
assembler assertion at `:399-401` enforces
`usb_rx_chain - .Lprime == 28`, i.e. 14 halfwords.

**How verified.** `nm -n` gives `usb_rx_chain` at 0xac; the disassembly puts
`.Lprime` at 0x90; 0xac − 0x90 = 28. The assertion is live and passes at 14.

The physical difference is half a cycle either way (13 puts the first sample
0.5 cycles before the cell centre, 14 puts it 0.5 after; the poll granularity
already contributes ±3.5), so this is a documentation defect, not a timing
one. It is worth fixing because the whole discipline of these files is "the
ledger is asserted, not asserted-about", and here the prose and the assertion
disagree by one instruction.

---

## F-10 (low, but name it) — the clock requirement is not stated anywhere

`engine16_merged.md` §10.4 says only "**No mid-packet resync**, so the engine
inherits the existing design's drift assumption." The existing design does not
merely assume: `rv003usb.S:740-800` measures the 1 ms SE0 keepalive interval
into `last_se0_cyccount` / `delta_se0_cyccount` / `se0_windup` and servos the
HSI trim from it. The merged engine's keepalive handler is `bx lr`
(`engine16_merged.S:276-277`) — it maintains none of those fields, so the
inherited assumption is inherited without the mechanism that upheld it.

The arithmetic, which neither note contains: the longest packet the engine
accepts is 12 emitted bytes, of which 11 are sampled after the lock — 88 data
bits plus up to ~14 stuffed = ~102 wire bits = 1632 cycles. The lock already
spends ±3.5 cycles of the ±8-cycle half-cell (`engine16_merged.md` §10.3), so
the drift budget for the whole packet is ±4.5 cycles, i.e. **±0.28 %**
combined device+host error. USB 2.0 §7.1.11 allows the low-speed data rate
±1.5 %; a receiver that tolerates the specified rate needs per-transition
resynchronisation, which §2 R-7 explicitly rejected.

I am **not** claiming this fails: `STATE.md:38` records the project position
that F003/F030 HSI at 24 MHz is inside tolerance with no servo, and that is a
recorded decision with a bench item attached. What is missing is that the
requirement is ±0.28 % rather than ±1.5 %, stated in the engine's own note, so
that the bench measurement has a number to be compared against. Given
`ENGINE16_CATALOG.md` §6 L-9, this belongs on the "what I could not verify"
list rather than being implied by "inherits the drift assumption".

---

# What I checked and did not find

A review that lists only findings cannot be told from one that stopped early.
This half is the negative result, area by area, with the evidence.

## Area 1 — the RX chain against a hostile bus

**Result: no hang and no out-of-bounds access found. The termination argument
holds for every input, including the class that killed VUSB (catalog D-A).**

There are exactly three loops in the receive path. Each has a bound that fires
for every possible input:

**1. The SYNC hunt.** `.Lwait_j` (`:340-345`), `.Lwait_k` (`:346-351`) and the
retry edge from `.Lk_edge` (`:356-362`). One counter, `r1`, initialised to 200
at `:300` and decremented on **every** pass through either wait loop
(`subs r1,#1 / beq .Lgiveup`). The retry at `:362` (`bpl .Lsync_hunt`) jumps to
`.Lwait_j`, which decrements before it can loop again — so no path revisits a
poll without spending a count. Worst case per count is one `wait_k` pass
(6..7 cycles) plus one full `.Lk_edge` confirm (20 nops + ldr + lsls +
taken `bpl`, 23..24), i.e. ≤31 cycles, so the hunt terminates in ≤6200 cycles
(~258 µs at 24 MHz) on any bus whatever — stuck at SE0, at J, at K, or
oscillating.

**2. The bit chain.** The back edge is `bx r14` at cell 7 (`:426`), so the
chain is a loop and needs a runtime bound. It has two, and they are
independent:

* SE0 in any cell → `beq rx_eop\idx`. All eight targets verified distinct and
  correct in the disassembly: `b0→34 rx_eop0`, `ce→3a rx_eop1`,
  `ec→40 rx_eop2`, `10c→46 rx_eop3`, `12a→1a0 rx_eop4`, `148→1a6 rx_eop5`,
  `168→1ac rx_eop6`, `188→1b2 rx_eop7`. No cell branches to another cell's
  stub.
* `SEG5`'s `cmp r2, #USB_BYTE_LIMIT / bhs \ovf` (`:210-211`) on the emitted
  byte count in `r12`.

The catalog's D-A hazard class is "a bound driven by a counter that the error
path stops advancing". I checked that directly, **exhaustively, against the
table in the object** rather than against the generator — reading `T_UT` back
out of `.datacode` with `objcopy -O binary` and enumerating all 8 states × 256
wire bytes:

```
RX  distinct n over all 128 entries: [0, 3, 4]
RX  min data bits emitted per wire byte over all 2048 (state,byte): 3
RX  (state,byte) pairs emitting zero bits: []
RX  row-7 entries that leave state 7 or emit != 4 bits: []
```

So `n = 0` does exist (the violation-*transition* entries, `0x00e0`), but no
wire *byte* emits zero bits: the transition entry moves the state to 7, and row
7 is sticky and always emits 4. `r0` therefore gains ≥3 bits per wire byte and
`SEG3` emits a byte whenever `r0 ≥ 8`, so `r12` advances at least once every
3 wire bytes. The bound of 24 fires within 72 wire bytes = 576 cells = 9216
cycles ≈ **384 µs, guaranteed, for any bus state**. (In the specific stuck-bus
case the .md analyses it is one byte per wire byte, ~128 µs; 384 µs is the
worst case the table permits.) The ISR is not re-entrant and runs with
interrupts effectively masked, so that bound is worth stating as a number, and
neither note does.

**3. The flush.** Straight-line: `rx_flush0..6` fall through consecutively into
`rx_flush7`, which either branches to `.Lrx_tail` or runs one more copy of
`SEG0..SEG6` and then branches. No loop. Verified in the disassembly
(`rx_flush0` 0x1ba → `rx_flush7` 0x23a, contiguous, and the only backward
branch anywhere in the region is `2d4: b.n 2d8`, which is forward).

Specific hostile inputs, traced by hand:

| input | behaviour | where it ends |
|---|---|---|
| SE0 at ISR entry | `usb_rx_keepalive`, `bx lr` before any save | immediate (but see F-1) |
| bus stuck at SE0 after entry | `.Lwait_j` sees D− low forever | `.Lgiveup` after ≤200 counts |
| bus stuck at J | `.Lwait_k` sees D− high forever | `.Lgiveup` |
| bus stuck at K | `.Lwait_j` never satisfied | `.Lgiveup` |
| J/K oscillation that never confirms | `.Lk_edge` retries, each costing a count | `.Lgiveup` |
| SYNC then permanent J or K | all-1s → state 6 → violation → row 7, 8 bits/byte | `SEG5` bound at 24, then `.Lrx_tail` rejects on `r11 == 7<<5` (`:496-497`) |
| stuffing violation held forever | as above; row 7 is sticky *and productive* | same |
| packet that never ends | `SEG5` bound | `.Lusb_overrun` → `.Lusb_done` |
| packet ending mid-nibble | `rx_eopK` for the cell that saw SE0; `r14 = 8−k`; `rx_flush7` left-shifts the k real bits to the top and feeds zeros in below | tail; the garbage bytes are bounded and the CRC rejects |
| SYNC followed by nothing | the confirm fails, the hunt retries | `.Lgiveup` |

**The buffer bound.** Independently of all of the above, no store can leave
`usb_rxbuf`: `SEG3` masks the index with `lsls #27 / lsrs #27` (`:181-182`)
against a `.balign 32` buffer of exactly 32 bytes (`objdump -h`:
`.bss.usb_rxbuf` size 0x20, align `2**5`). I re-derived the accumulator
invariant exhaustively as well — the maximum bit count `r0` can reach before
`SEG3`, over all (state, carried r0, wire byte) triples, is **15**, so
`lsls r1, r1, r0` never shifts out of a word and at most one byte is emitted
per wire byte. Also worth recording: the byte limit is 24 and the mask is 32,
so bytes 24..31 of the buffer are never written — which is exactly the region
`turnaround.md` §9 R2 proposes to use for TX pre-staging. That is currently a
property of the design, not a coincidence waiting to be discovered.

**The tail's reads.** `.Lrx_tail` reads `[r6,#0..3]` and, on the DATA path,
hands out `r6+2` with a length; the count is already constrained to 4..12
(`:501-503`), so every read is inside the 32-byte buffer. Verified.

**What I did not check.** Whether the phase lock actually acquires on silicon —
that needs a bench, and `engine16_merged.md` §10.3 already declares it
unmeasured. And I did not reproduce the .md's 305-packet bit-exact model of a
*legal* packet, because per L-1 re-running a model that shares its tables with
the artifact would not have told me anything the object-versus-generator diff
did not.

## Area 2 — the TX chain's entry conditions

**Result: the dispatch index is provably in range, all eight dispatch words are
reachable and correct, and no index is unreachable-but-live.**

The chain has ten cells and is entered by `bx r1` at the end of `TXSEG7`
(`:271`), where `r1` is a word loaded from `T_DISPATCH` at
`table_base + (acc>>8)*4`, ANDed to 0 when the packet is exhausted.

Index range, proved rather than sampled. `TXSEG4` builds `acc = w_lo + 2^n_lo`
(`:228-229`, `lsrs r0,r1,#9 / adds r0,#1` on `m = w + 2^n − 1`) and `TXSEG6`
adds `m_hi << n_lo` (`:252-255`), so `acc = w + 2^n` with `n = n_lo + n_hi` and
`w < 2^n`. Hence `2^n ≤ acc < 2^(n+1)` and `acc>>8` is 1 for n=8, 2..3 for n=9,
4..7 for n=10. I verified this **exhaustively against `T_TX` read out of the
object**, over all 7 states × 256 bytes:

```
TX  dispatch index histogram over all 1792 (state,byte):
    {1:1272, 2:256, 3:256, 4:3, 5:1, 6:3, 7:1}
TX  n values seen: [8, 9, 10]
TX  defects: [] count 0
```

The checks behind that line: the resulting state is always in 0..6, so `T_TX`
indexing can never select row 0 — which is the dispatch table, and that reuse
is what the header relies on; the sentinel always lands exactly at bit `n`;
`n` is always 8, 9 or 10; and the cell the index selects always gives exactly
`n` cells (1→S0 = 8, 2,3→P1 = 9, 4..7→P0 = 10).

| index | word | cells | reachable? |
|---|---|---|---|
| 0 | `usb_tx_eop` | EOP | only via `TXSEG7`'s `ands r1, r0` exhaustion mask (`:266`) — never from `acc>>8`, because `acc ≥ 2^8` always |
| 1 | `usb_tx_cellS0` | 8 | yes, 1272/1792 |
| 2, 3 | `usb_tx_cellP1` | 9 | yes, 512/1792 |
| 4, 5, 6, 7 | `usb_tx_cellP0` | 10 | yes, 8/1792 |

This is worth stating precisely because `engine16_tx.md` §6 reports "Index 4
never occurs in this sample" from a 433-packet run and then reasons about it by
inspection. It does occur — 3 of the 1792 (state, byte) pairs reach it — and
the exhaustive enumeration confirms its dispatch word is right. The model's
coverage gap is closed, in the direction the model could not close it.

The **initial** entry is separate and also checked: `usb_send_data` primes
`r4 = 0x80` (SYNC, 8 wire bits, LSB first, never stuffed) and `r11 = 2<<5`
(state 1 — SYNC's trailing 1 counts) and enters directly at `usb_tx_cellS0`
with `ldr r0,=(usb_tx_cellS0+1) / bx r0` (`:387-388`), not through the table.
Eight cells for eight bits. Correct.

Termination: `r3` advances by exactly one byte per window and the anchor `r8`
never moves after setup (`:352-358`), so `subs r0,r3,r2 / subs r0,#3` reaches
0 after a fixed number of windows for every argument. Verified by trace for
lengths 0 and 8 with CRC, and length 0 without; the one over-fetch it performs
reads `txbuf[12]` at worst, inside the 16-byte buffer.

Nothing falls into the chain by accident: the arm path ends `8c: bx r0`,
`.Ltx_reject` is at `8e` and is itself a branch, and `usb_tx_chain` begins at
`90` — so the `.balign 4` inserted no fill and nothing reaches `.Ltx_reject`
except the two setup rejections. `usb_tx_cellS7` ends with the unconditional
`bx r1` and `usb_tx_eop` follows at `1be`, so there is no fall-through of the
D-B / D-G class.

I also counted the three EOP cells by hand out of the disassembly rather than
trusting the note: `1be..1dc` = 2 nop + `lsrs` + `lsls` + `str` at cycle 5 + 11
nop = 16; `1de..204` = 4 setup instructions + 16 nops, which is cell B's 12
plus cell C's leading 4; `206` `str` at cycle 5 of cell C + 11 nop = 16. SE0
spans store-to-store = 32 cycles = 1.333 µs, inside USB 2.0's 1.25..1.50 µs.
The note's ledger is correct.

## Area 3 — state shared between RX and TX

**Result: no register or buffer disagreement between the two engines. Every
disagreement I found is between the engines and the C layer, and they are F-2,
F-3, F-4, F-5 and F-7 above.**

* **Register conventions.** The two never run concurrently and share no
  register state: TX is reached from C (`usb_pid_handle_in` →
  `usb_send_data`), i.e. nested inside the RX tail. Both save and restore
  `r4-r7` and `r8-r11` and both return via `pop {r4-r7, pc}`
  (`merged:639-645`, `tx:479-484`), so both are AAPCS-clean callees. The RX
  tail holds nothing across its `bl`s except `r6` (rxbuf base), which it does
  not re-read afterwards. Verified in the disassembly.
* **Buffers.** `objdump -r` confirms disjoint symbol sets: RX relocates against
  `usb_rxbuf`, `usb_tables`, `rv003usb_internal_data` and the five
  `usb_pid_handle_*`; TX against `usb_txbuf`, `usb_tx_tables` and its own cell
  labels. There is no cross-engine buffer coupling today, so `turnaround.md`
  §9 R2's pre-staging is *unimplemented* rather than half-implemented — the
  safer of the two.
* **`rv003usb_internal_data`.** Neither engine reads or writes any field of it;
  RX only takes its address for the fifth argument (`:542, :570, :601`). So
  there is no offset-macro disagreement of the `MY_ADDRESS_OFFSET_BYTES` /
  `RV003USB_OPTIMIZE_FLASH` kind — but that is because the address check is
  *missing* (F-7), not because it was made robust.
* **PID byte convention.** This is the one place the two engines had to agree
  with each other and with C, and they do. The merged RX assembles bytes
  LSB-first, so its PID byte is the wire-order form: `0xC3` DATA0, `0x4B`
  DATA1, `0xD2` ACK, `0xE1` OUT, `0x69` IN, `0x2D` SETUP, `0xA5` SOF. I checked
  each against `merged:518-527` (`&3` type field: 3→data, 1→token,
  2→handshake) and `:573-586` (low nibble 0x0D→setup, 0x09→in, 0x01→out, else
  SOF). All correct. TX writes the caller's token byte straight to `txbuf[1]`
  and emits LSB-first, and the C layer's constants are the same form
  (`rv003usb.c:203` `0b01001011 : 0b11000011`, `:508` `0xD2`). Note this is the
  *opposite* convention to the RISC-V engine, which stores the PID bit-reversed
  (`rv003usb.S:289-291`, "Write header into byte in reverse order") and
  therefore compares `0b01001011` against **ACK**, not DATA1. The new pair is
  self-consistent and consistent with the C; the collision of those two
  constants between the two conventions is a live trap for anyone porting
  comments across, and it deserves a line in the .md that is not there.
* **`which_data`.** `merged:538-540` takes bit 3 of the PID byte: 0 for `0xC3`
  (DATA0), 1 for `0x4B` (DATA1). Matches `e->toggle_out` at `rv003usb.c:304`.
* **CRC16 and CRC5 residues.** Re-derived numerically here rather than taken
  from the notes: the byte table *in the object* gives residue `0xB001` over
  message+CRC for 200 random payloads of 0..8 bytes and for the empty payload
  (whose transmitted CRC is `0x0000, 0x0000`), matching `merged:530-534` and
  `rv003usb.S:568`. CRC5 residue 6 matches `:558`.
* **`this_token`.** RX passes 0 (`:541`); C ignores it, as did both
  predecessors.
* **What RX no longer maintains.** The RISC-V SE0 handler keeps
  `last_se0_cyccount`, `delta_se0_cyccount` and `se0_windup`
  (`rv003usb.S:740-777`) and servos the HSI trim from them.
  `usb_rx_keepalive` is `bx lr`. Nothing in `rv003usb.c` *reads* those fields
  (only `:60` zeroes `se0_windup`), so this breaks no C code — but see F-10 for
  what it costs.

## Area 4 — the embedded tables against their generators

**Result: both tables are correct. 400/400 and 368/368, checked against the
object, not against the source text.**

Per L-1 the comparison has to be against the artifact. I extracted `.datacode`
with `arm-none-eabi-objcopy -O binary --only-section=.datacode`, located
`usb_tables` at 0x3f4 and `usb_tx_tables` at 0x258 from `nm -n`, unpacked the
halfwords straight out of the image, and ran the generators quoted in
`engine16_merged.md` §12 and `engine16_tx.md` §9 verbatim:

```
RX  usb_tables at 0x3f4: 400 halfwords (128 T_UT + 256 T_CRC16 + 16 T_CRC5)
    object-vs-generator mismatches: 0
TX  usb_tx_tables at 0x258: 8 dispatch words + 368 halfwords (112 T_TX + 256 T_CRC16)
    object-vs-generator mismatches: 0
```

I ran the same comparison against the `.S` source text as well (also 0 on
both). Both comparisons matter and they are not the same check: the source
comparison is what D-H needed, and the object comparison additionally rules out
anything the C preprocessor could have done in between.

The eight `T_DISPATCH` words are relocations, not data, so they cannot be
compared as bytes. I checked them from `objdump -r` instead, and the order in
the relocation table is the order in the source:

```
00000258 usb_tx_eop     0000025c usb_tx_cellS0
00000260 usb_tx_cellP1  00000264 usb_tx_cellP1
00000268 usb_tx_cellP0  0000026c usb_tx_cellP0
00000270 usb_tx_cellP0  00000274 usb_tx_cellP0
```

which is `[EOP, S0, P1, P1, P0, P0, P0, P0]` — matching the n = 8 / 9 / 10
mapping proved exhaustively in area 2. The `+1` Thumb bit is in the addend and
is present on every word in the source.

Also checked, because two files carry the same table: RX's `T_CRC16` and TX's
`T_CRC16` are identical to each other as well as to the generator. They are
duplicated rather than shared, which `engine16_tx.md` §7 already declares as a
512-byte cost.

## Area 5 — things that work by accident rather than by construction

**Result: two real instances (F-6, and 5a below), plus four checked and found
to be genuinely by construction.**

### 5a. The `CELL` macro cannot catch an under-declared `USE` — latent

`engine16_merged.S:90-93` claims "the ledger is assembled, not asserted: CELL
pads the cell that is running out to exactly 16 and raises `.error` if it went
over. A miscount is a build failure, not a comment that disagrees with the
code."

That is only half true, and the weaker half is the dangerous one. `CELL` and
`TXCELL` track `CYC` from the **`USE n` declarations**, which are hand-written
numbers, not from the location counter:

```
	.macro  USE n
	.set    CYC, CYC + (\n)
	.endm
```

If a segment's real cost exceeds its declaration — an instruction added to
`SEG3` without bumping `USE 11`, or a 2-cycle `ldrh` where a 1-cycle op was
counted — `CYC` still reaches 16, `CELL` still pads zero, `.error` still does
not fire, and the cell is silently 17. The device catches **over**-declaration
only. The file elsewhere shows the author knows the stronger technique: the
phase-lock assertion at `:398-401` uses real location-counter arithmetic
(`usb_rx_chain - .Lprime == 28`).

**Verified not currently violated.** `tools/engine16_cyc.py --exec ram` prices
every RX cell minimum at exactly 16 and every TX cell at exactly 16, so no
`USE` is presently under-declared. That same check is what rules out the D-C
hazard: a `nop` swallowed by a preprocessor line-continuation would show as a
15-cycle cell, and none is 15. (I separately grepped both files for `\` — seven
occurrences in RX, four in TX, all inside block comments and none immediately
before a newline.)

The exposure is a maintenance one: the only thing between an edit and a broken
bit cell is an external tool somebody has to remember to run. Since bytes are
not cycles, a pure `.` assertion cannot replace `USE`; the cheap mitigation is
to declare the segment's instruction *count* alongside its cost and assert the
count with `.` arithmetic, which catches the realistic case ("an instruction
was added").

### 5b. `.datacode` still reaches RAM only through a wildcard — unaddressed

Both files use `.section .datacode, "ax", %progbits` (`merged:260`, `tx:275`),
which `objdump -h` confirms comes out `READONLY, CODE`. Catalog U-4 established
that **no `.datacode` rule exists in any linker script in this project** and
that the section reaches RAM only because the stock script's `*(.data*)`
wildcard swallows it — and that a script spelling that `*(.data.*)` would place
both engines in flash silently, with every timing number invalidated. Nothing
in either engine, and nothing in `INTEGRATION_BUILD.md`, adds the explicit
output-section rule or the link-time assertion U-4 asks for. The engines are
right to name the section they were ledgered for; what is missing is the guard
— and F-6 is what happens without it. The one integration that was actually
attempted moved them to flash, and nothing complained.

### 5c. Checked and found sound

* **`--gc-sections` cannot half-discard an engine.** Each file puts its entry
  point, chain, untimed tail and tables in one `.datacode` section (`objdump
  -h`: a single `.datacode` of 0x714 / 0x558 bytes, no `.text.*` splitting;
  `nm -n` puts `usb_tables` and `usb_tx_tables` inside it). So the "successful,
  tiny, entirely empty image" failure `INTEGRATION_BUILD.md` records is
  all-or-nothing rather than partial, which makes it detectable by a single
  `nm | grep usb_rx_engine16`.
* **No branch relocations.** `objdump -r`: RX 9 relocations (5
  `R_ARM_THM_CALL`, 4 `R_ARM_ABS32`), TX 12 (all `R_ARM_ABS32`). No
  `R_ARM_THM_JUMP*`, so every branch range was proved by the assembler rather
  than deferred to the linker (N-8 / L-3). This holds even though TX makes its
  cell labels `.global`, because nothing branches to them — they are reached
  only through absolute dispatch words, exactly as `engine16_tx.S:114-119`
  claims.
* **No fall-through into the wrong handler** (the D-B / D-G class). Traced in
  raw `objdump`. RX: cells are contiguous and fall through *by design*
  (`cell0` 0xac–0xc9, `cell1` 0xca, … `cell7` 0x184–0x19f), with `cell7` ending
  in `bx lr` at 0x19e so `rx_eop4` at 0x1a0 cannot be entered by fall-through;
  `rx_eop3` at 0x46 ends in a branch at 0x4a so `.Lwait_j` at 0x4c is not
  entered from it; `.Lovf_a` at 0x1b8 is a branch and `rx_eop7` at 0x1b2 ends
  in one, so `rx_flush0` at 0x1ba is only ever entered deliberately; and the
  entry code at 0x30/0x32 branches over the whole `rx_eop0..3` block. TX:
  `bx r0` at 0x8c, `.Ltx_reject` branch at 0x8e, chain at 0x90; `bx r1` closes
  `cellS7` before `usb_tx_eop` at 0x1be.
* **The `usb_rxbuf` alignment is declared, not inherited.** `objdump -h` gives
  `.bss.usb_rxbuf` size 0x20, alignment `2**5`, from `.balign USB_RXBUF_LEN`
  with a comment naming the dependency; the `lsls #27 / lsrs #27` mask is sound
  against it. (Confirming what the requester had already established.)

### 5d. One tool artifact, recorded so nobody re-derives a wrong number from it

`tools/engine16_cyc.py` prices `ldr rN, [pc, #imm]` at 4 cycles under
`--exec ram`, from spec §2's "LDR of a literal from flash via PC = 4 from
RAM-resident code". In these engines the literal pool is emitted by `.ltorg`
**inside `.datacode`** (RX: after `.size`, before `usb_tables`), i.e. in RAM
alongside the code, so the real cost is a RAM load, not a flash one. This
touches only the untimed entry and arm paths — no literal load is inside any
bit cell, which is what `merged:283-285` explicitly arranges — but it means the
tool's arm-to-first-bit figures are pessimistic by 2 cycles per literal, and
`engine16_tx.md` §5's cold numbers are built on that column. Not a defect in
either engine; recorded so the number is not later re-used as if it were
measured.

---

# What would change the verdict

F-1 through F-5 are each a handful of instructions in untimed code, and none of
them touches a bit cell. F-4 and F-5 must be repaired together, because they
come from one decision — storing SYNC in the buffer, which both shifts the
payload to an odd offset and shortens the count by one. F-6 is the one that is
not a patch: it is a choice between the RAM footprint the engines were ledgered
for and a restructuring that gets the `rxbuf` store out of the cell.

With F-1..F-5 fixed and the engines linked from RAM as written, three things
from a bench would settle it, in this order: that the ISR is entered once per
packet and returns (F-1's repair working); that enumeration completes, which
exercises F-2, F-4 and F-5 in one shot; and the measurement
`INTEGRATION_BUILD.md` already names as the most valuable in the project — the
cost of a register-offset data read from flash, which decides whether the flash
option in F-6 exists at all.

---

# Captain's verification of the review

## F-1 confirmed, and it is the worst thing found

Verified independently. The old engine acknowledges the interrupt:

```
rv003usb-arm.S:3     #define EXTI  0x40021800
rv003usb-arm.S:17    #define EXTI_PR_OFFSET 0x0C
rv003usb-arm.S:331   interrupt_complete:
rv003usb-arm.S:333       // Acknowledge interrupt.
rv003usb-arm.S:334       ldr r0, =EXTI
```

Both new engines contain **zero** occurrences of `EXTI`, `0x40021800` or the
pending register — `grep -c` returns 0 for each file. The ISR returns without
clearing the pending flag, the NVIC re-enters immediately, and the device
live-locks on the first D− edge. It would never enumerate.

**This is a defect in the competition's terms of reference, not only in the
code.** `ENGINE16_SPEC.md` §3 specifies the bit cell and §4 the seam with the C
layer, and says nothing about the interrupt controller. Six independent entrants
and a referee all optimised what the spec measured. Nobody wrote the peripheral
housekeeping because nobody was asked to, and the one place it existed —
the predecessor engine — was offered as a source of *mechanisms*, not as a
checklist of obligations. A specification that scores cycles gets cycles.

## F-6 corrected — the miscount is mine, not the reviewer's

The reviewer reports 6 of 8 RX cells at 18 cycles. That came from
`tools/engine16_cyc.py` as it stood when the review was launched: it charged
every non-port load as a RAM access, including the table read, which in a
flash-resident build reads flash and costs 2 (`xm_030.md:471`). I added
`--flashdata` afterwards.

Measured both ways:

| | cells over 16 |
|---|---|
| without `--flashdata` (table charged as RAM) | 0, 1, 3, 4 — four cells |
| with `--flashdata r4` (table in flash, 2 cycles) | **cell 3 only** |

So the count is one cell, not six. The reviewer's *substance* was right and is
worth keeping: it identified that cell 3's overrun is the `rxbuf` store at 4
versus 2 — "the measured column, not the unknown flash table read" — which is
exactly correct and independent of the tool error.

## What this changes about priority

The size question — 48 % of the engine is tables, a third of it a duplicated
`T_CRC16` — is real and costed, but it is now second. F-1 through F-5 are
correctness defects that stop the stack working at all, and the reviewer's own
summary is the right reading: **every one of them is in the untimed seam or the
placement decision, not in the 16-cycle machinery.** The competition produced a
sound bit cell wrapped in an unfinished driver.
