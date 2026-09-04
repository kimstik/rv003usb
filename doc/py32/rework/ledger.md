# PLAN.md rework — placement, TX cost annotations, paper ledger, cycle walker, bench gates

Owner: ledger editor (one of four parallel editors). Replacement blocks for PLAN.md §2.1, §2.5
(cycle-cost annotations only), Appendix A, Appendix B, plus one NEW block (bench gates for the
cost table). Each block starts with `## REPLACES …` / `## NEW …` so it can be spliced
mechanically. Nothing else in PLAN.md is touched here; cross-effects on sections owned by
others are listed at the end as requests, not edits.

Cites: `XF:<n>` = `doc/py32/CHIP_FACTS_XIAMATSU.md` line; `xm_030.md:<n>` / `xm_002b.md:<n>` =
the Xiamatsu READMEs as cited there; `PLAN:<n>` = `doc/py32/PLAN.md` at 88d1229; `arm.S:<n>` =
`rv003usb/rv003usb-arm.S` at 0ad3c42 (573 lines, re-read for this rework); `LS:<n>` =
`doc/wg015/ledger_static.md` (house ledger format); TRM = DDI0484B Table 3-1.

## 0. The cost model this rework is computed against (read first)

Measured on live F002A/F003/F030 silicon, Flash Latency = 0, i.e. ≤ 24 MHz (XF:12-26,
xm_030.md:464-493). The table depends on where the code executes from, and the prices swap:

| Operation | code in FLASH | code in RAM | PLAN v2 assumed (TRM) |
|---|---|---|---|
| most instructions | 1 | 1 | 1 |
| `b<cc>` taken / not taken | 2 / 1 | **2-3** / 1 | 2 / 1 |
| `b` unconditional | 2-3 | 2-3 | 2 |
| `bx Rm` | 3 | 3 | 2 |
| `bl` | 4 | 4 | 3 |
| `ldr/str` to GPIO (IOPORT) | 1 | «на полной скорости» (xm_030.md:447) | 1 |
| `ldr Rd,[pc,#]` literal, pool in **flash** | 2 | **4** | 2 |
| `ldr/str` to RAM | **4** | **2** | 2 |
| `ldm/stm/push/pop` | 4 first reg, +1 each | 2 first reg, +1 each | 1+N (= 2 first, +1 each) |
| `mov pc, lr` | not measured | not measured | 2 |
| `ldr/str` to flash data via a register base, from RAM code | — | **not measured** | 2 |
| `ldr Rd,[pc,#]` literal, pool in **RAM**, from RAM code | — | **not measured** (inferred = "to RAM" = 2) | 2 |

Direct quotes that fix the direction of the decision: running from RAM «не замедляется, как
ожидалось» (xm_030.md:481, XF:28); a separate test executes from RAM at 55-86 MHz with «нет
тактов ожидания» and «доступ к портам на полной скорости» (xm_030.md:440-457, XF:29-31).

The author's own caveat, which this ledger inherits verbatim: «определить выполнение инструкций
сложно, так как зависит от выравнивания и зависимости от предыдущей инструкции»
(xm_030.md:468-469, XF:45-48). These are typical numbers, not guarantees. Two things are
therefore UNVERIFIED and are bench gates (§5 below): whether the RAM column holds at 48 MHz
with `LATENCY=1` (measurements were at Latency 0, XF:14; Latency 0 ends at 24 MHz on F030 and
30 MHz on F002B, XF:94), and whether F002B — a different die shared with L020 — shares it at
all (XF:50-51).

Parameters used in every formula below (house style LS:8-31):

| Symbol | Meaning | Measured / assumed value |
|---|---|---|
| **B** | cost of a taken `b` / `b<cc>` from RAM | measured **2-3**; PLAN assumed 2; columns given at B=2 and B=3 |
| **L** | cost of `ldr Rd,[pc,#]` from RAM code | **4** if the pool is in flash (measured); **2** if the pool is in RAM (inferred, UNVERIFIED — bench K4) |
| **D** | `ldr/str/ldrb/strb` to RAM data from RAM code | **2** (measured; PLAN assumed 2 — unchanged) |
| **Df** | load of flash *data* via a register base from RAM code (`ldrb SHIFT_BUF,[r0]` when the source is in flash) | not measured; assume 4 (same bus path as the flash literal) — placement rule, not a pad |
| **P** | IOPORT `ldr/str` | 1 (measured "full speed"; PLAN 1 — unchanged) |
| **C** | staircase constant `bl` + return | PLAN 3+2 = **5**; measured `bl` = 4, so 4 + (`mov pc,lr` 2 unmeasured) = **6**, or 4 + (`bx lr` 3) = **7** — bench K10 |

Old PLAN numbers are reproduced exactly at (B, L, D, P, C) = (2, 2, 2, 1, 5). Every number
that moves is shown as `old → new` with the formula that moves it.

---

## REPLACES §2.1 — Placement: what runs from RAM, what from flash, and why it works at all

The branch's placement (table unchanged from v2; addresses from the rebuilt demo_gamepad):

| Region | Lines | Section | Address (PY32F002Bx5) | Why |
|---|---|---|---|---|
| rxbuf | arm.S:30-33 | `.bss.rxbuf`, 3+USB_BUFFER_SIZE = 15 B | 0x20000180 | packet store; PID at +3 so payload at +4 is word-aligned (C uses `__builtin_assume_aligned(data,4)`, c:260) |
| RX ISR core (entry → SE0/keepalive trampolines) | arm.S:36-225 | `.pushsection .datacode,"ax"` … `.popsection` | 0x2000000c-0x20000108 = 252 B of RAM **incl. an 8-word literal pool at 0x200000e8** | bit-critical |
| Dispatch (PID decode, C calls, EXTI ack) | arm.S:227-343 | `.text` (flash) | 0x080001a0…0x08000214 | "not time-critical, continue in flash to conserve RAM" (arm.S:211-212) |
| TX engine (`usb_send_empty`/`usb_send_data` … release) | arm.S:345-569 | `.text` (**flash**) | 0x08000222-0x08000357 | RAM scarcity (3 KB) — the fragile choice, §2.6 |
| `always0` | arm.S:571-573 | `.text` (flash) | 0x08000358 | data source for `usb_send_empty` (read by `ldrb` inside the TX cell, arm.S:463) |

How `.datacode` lands in RAM: the vendor script `Libraries/LDScripts/py32f002bx5.ld:111-120`
places `*(.data*)` into `.data >RAM AT> FLASH`; `.datacode` matches the `.data*` glob and
`startup_py32f002b.s:42-57` copies `_sidata→_sdata` at reset. There is no explicit rule — a
linker script without that glob would silently execute the ISR from flash (GNU ld orphan
placement puts an `"ax"` orphan after `.text`). The plan replaces this with an explicit
`.timecrit` output section (T1), as `rv003usb/wg015/wg015_common.ld:52-60` does.

**Decision: the timing engine runs from RAM — CONFIRMED on measured evidence, not on the
v2 argument.** v2 justified RAM by "flash is 1-WS at 48 MHz" (RM002B p38) and by prior-art
unanimity (PA S-4, D-2). Both still stand, but the measured table (§0) gives the reason its
actual shape, and it is favourable rather than merely necessary:

1. RAM-resident code is not slower than flash-resident code: «не замедляется, как ожидалось»
   (xm_030.md:481); ordinary instructions are 1 cycle in both columns (XF:18, :34-35).
2. Data in RAM is **cheaper** from RAM code: `ldr/str` to RAM 4 → **2** (XF:22). The engine's
   RAM data accesses inside timed code are the EOB `strb SHIFT_BUF,[r2]` (arm.S:147) and the
   TX `ldrb SHIFT_BUF,[r0]` (arm.S:463); both stay at the 2 cycles v2 assumed. Had the engine
   been in flash they would be 4 — the EOB cell would be 34, over budget.
3. Stack traffic is cheaper: `push/pop` 4+1 → **2+1** per register (XF:23). The ISR prologue
   `push {r4-r7,r14}; …; push {r7,r14}` (arm.S:44-47) = 6 + 3 = 9 cycles from RAM, exactly the
   TRM 1+N v2 used; from flash it would be 8 + 5 = 13 (+4 in the entry constant of §2.2).
   Consequence for the branch's "TODO: keep code up to here in flash to conserve RAM"
   (arm.S:63): measured cost +4 cycles of entry latency plus two 2-cycle literal loads, plus
   whatever `LATENCY=1` adds to flash fetch at 48 MHz (unmeasured). Not adopted; the ≈60 B is
   not worth an unmeasured entry constant.
4. GPIO stays the cheapest access in both columns — `ldr/str` to the IOPORT «на полной
   скорости» (xm_030.md:447) — which is what the one-sample-per-slot structure needs (P = 1 in
   every slot formula of Appendix A).

The one price that goes **up** from RAM: a PC-relative literal load whose pool is in flash
costs **4** instead of 2 (XF:21, :40-42). This is the trap, and it is the opposite of the trap
the previous run believed in (it applied the flash column to RAM code and concluded RAM data
was expensive — that reading would have put every EOB cell at 34 and the TX byte-load at +2,
both false alarms, while missing the literal-pool cost entirely).

Why the branch's RX works at all with two literal loads per PID slot (arm.S:90, :92): its
literal pool sits inside `.datacode`, i.e. **in RAM** (0x200000e8, table above). A PC-relative
load from a RAM pool is, on the M0+'s single AHB-Lite port, a data load from RAM — expected 2,
the v2 number. This inference is UNVERIFIED (the measured row is "literal from flash"); bench
K4 in §5 measures it directly. If it comes back 4 the hoisting rule below is mandatory rather
than merely recommended, and Appendix A's `L=4` column applies to the PID loop.

**HARD RULE (placement of constants; implementers of T2 are held to this, checked by the
walker of Appendix B and by `nm`):**

> No load from flash inside a timed bit cell. Concretely: (a) every `ldr Rd,[pc,#imm]` executed
> between a cell's sample/store and the next must resolve to an address in SRAM — the engine
> emits its literal pools inside `.timecrit` (`.ltorg` before the section ends, T2 step 2) and
> the walker fails the build if any PC-relative load in a named path resolves outside SRAM;
> (b) every data source of a timed load is in SRAM: `rxbuf`, the `usb_send_data` buffer
> (descriptors → `.rodata.usbdesc` in RAM, Р4/T4), `always0` (→ `.timecrit`); (c) preferred over
> (a): bit-cell constants live in registers, loaded before the cell.

Register cost of the rule, checked against the real file: the engine is Thumb-1 — 16-bit ALU
ops and `ldr [pc,#]` reach r0-r7 only; r8-r12/r14 are reachable through `mov` (arm.S:97-98
pattern, 1 cycle each). The three literal loads that sit inside timed cells today are all
**loop-invariant**: `ldr CRC,=0xffff` and `ldr SCRATCH,=0xa001; mov POLY_RX,SCRATCH`
(arm.S:90-93, "we need to execute them anyway" — executed 8× as filler) and `ldr CRC,=0xffff`
(arm.S:436, executed 15× as filler in the preamble loop). Hoisting them before their loops
(RX: after `mov SHIFT_BUF,#0` arm.S:81, inside the `DELAY_CYCLES(71)` pad; TX: into the
prologue arm.S:362-400, which is turnaround budget, not a cell) costs **zero registers** —
CRC (r7) and POLY_RX (r14) already hold the values — and replaces 2+2 / 2 cycles of filler by
the same number of `nop`s (code size identical: a `ldr =` is 2 B + 4 B pool, two `nop` are
4 B). No other constant is needed inside a cell; r10/r11 remain free (PLAN §2.3) as spill
homes if a future change needs one (1 `mov` to bring it low). Feasible.

What the measured table does **not** change: the 3 KB RAM budget on 002B that drove the
branch's TX-in-flash choice (R3 fallback: dispatch back to flash — that code is not timed and
reads RAM data at 4 per access from flash, acceptable there and only there).

RAM/flash footprint of the branch's demo_gamepad (build log, `-Os`): RAM 1168 B / FLASH 2696 B
on 002B (RAM: 252 B ISR + 92 B `rv003usb_internal_data` + 16 B rxbuf + libc `impure`/stack).

---

## REPLACES §2.5 — TX path: cycle-cost annotations only

The step table's *mechanism* column is not mine; only its cycle figures change. Three
substitutions inside the table:

| Row | v2 text | Replacement |
|---|---|---|
| Drivers on | "30 cycles from entry to the MODER store (walker)" | "entry → MODER store = 20 + 5L: **30** with the literal pool in RAM (L=2), 40 with it in flash (L=4) — five `ldr =` in arm.S:363-374; Appendix A row C2" |
| NRZI | "`str r5,[GPIO,#BSRR]` (1 cycle, IOPORT)" | "`str r5,[GPIO,#BSRR]` (1 cycle — IOPORT, measured «на полной скорости», xm_030.md:447)" |
| Bit stuffing | "5-6× `b .+2` then `b flip_bus`" | "5-6× `b .+2` then `b flip_bus` — each is a taken branch (B = 2-3 from RAM, §0); the stuffed cell carries 11 of them, Appendix A row B5" |

The paragraph "Walker numbers under the 0-WS model …" (PLAN:229-244) is replaced by:

Walker numbers for these paths executed **from RAM**, at (B, L, D) = (2, 2, 2) — i.e. the
measured RAM column with literal pools in RAM: pre_and_tok zero 20 / one 19 (store index,
0-based from the loop top, 8 / 7); send_inner zero 21, one 21, zero-EOB 21, one-EOB 20;
one+stuffed 40 (target 64), zero-path store index 10, stuffed store index 29; last data bit →
CRC byte 1 → loop top 23 (CRC byte 1 → byte 2: 21, a 2-cycle skew the source admits,
arm.S:521); last CRC bit → SE0 store 31; SE0 width 37; J-park → release 19; entry → first
preamble store 51. These are the v2 figures unchanged, because none of these paths contains a
RAM data access other than the byte load (D = 2 both ways) and their literal loads sit in the
prologue or are hoistable (§2.1 rule). What the measured column adds is an exposure, not a
shift: every `b .+2` and every loop-back is a taken branch at B = 2-3, and the TX paths carry
2…17 of them each (Appendix A gives each path as f(B)); the SE0 pad alone is 17 taken
branches (arm.S:546-548) — 37 cycles at B=2, 54 at B=3. The 0-WS *flash* claim of v2 ("the
loops reach ≈32 only because they execute from 1-wait-state flash", ≈+11 per iteration) is
UNVERIFIED either way: the measured flash column is 1 cycle per ordinary instruction at
Latency 0 (XF:16-18) and nobody has measured Latency 1 at 48 MHz; it is also irrelevant once
TX is in RAM (T2 step 2), so it is dropped from the ledger rather than defended. The per-part
`#if PY32F002Bx5` nop variants (arm.S:402-408, 415-424, 444-446, 490-492, 530-532) and the
alignment assert (arm.S:421-423) remain what §2.6 says they are — never assembled, unknown.
v2's sentence "SRAM is 0-WS and, per the TRM, alignment-free" is **withdrawn**: the measured
RAM column reports taken branches as 2-3 with the explicit note that alignment and the
previous instruction matter (xm_030.md:468-469), so an alignment effect from RAM is possible
and bench K7/K8 (§5) decides it — if it exists, `.balign 4` on loop heads plus walker
re-padding is the fix (R4), not the assert.

---

## REPLACES Appendix A — Paper ledger of the branch engine (RAM-execution column)

Paper ledger, house format (LS:5-6): the value is the f(B, L, D) formulas and the pad map;
absolute numbers are recalibrated by the §5 benches. Costs per §0. "Slot" runs sample → sample
(RX) or loop-top → loop-top (TX). Store index = 0-based cycle, from the loop top, in which the
`str` issues. Old = PLAN v2 (TRM model) = the (2, 2, 2) column by construction; where a
formula's value at the measured column differs from v2 the cell is marked `old → new`.

### A. RX (from RAM)

| # | Path | Lines | f(B,L,D) | (2,2,2) = v2 | (2,4,2) pool in flash | (3,2,2) | Budget |
|---|---|---|---|---|---|---|---|
| A1 | entry → IDR sample done | arm.S:41-42 | 1 + L | 3 | 3 → 5 | 3 | phase const |
| A2 | entry → `DELAY_CYCLES(96)` start (push 6+3 = TRM = RAM column) | arm.S:41-56 | 17 + 2L | 21 | 21 → 25 | 21 | phase const |
| A3 | `DELAY_CYCLES(96)`: 32 iters, 31 taken | arm.S:58,62 | 34 + 31B | 96 | 96 | 96 → **127** | pad |
| A4 | preamble poll, per iteration | arm.S:70-74 | 3 + B | 5 | 5 | 5 → 6 | granularity |
| A5 | detect → `DELAY_CYCLES(71)` start | arm.S:76-81 | 8 + L | 10 | 10 → 12 | 10 | phase const |
| A6 | `DELAY_CYCLES(71)`: 24 iters, 23 taken | arm.S:83 | 26 + 23B | 72 | 72 | 72 → **95** | pad |
| A7 | packet_type top → sample done | arm.S:85-99 | 12 + 3B + 2L | 22 | 22 → 26 | 22 → 25 | phase |
| A8 | packet_type_loop, zero bit (5 taken, 2 literals) | arm.S:85-114 | 18 + 5B + 2L | 32 | 32 → **36** | 32 → **41** | 32 |
| A9 | packet_type_loop, one bit (5 taken, 2 literals) | arm.S:85-114 | 18 + 5B + 2L | 32 | 32 → **36** | 32 → **41** | 32 |
| A10 | packet_type → bit_process transition (`beq .+4` ×2, balanced) | arm.S:128-140 | 4 (+B either way) | 4 | 4 | 5 | const |
| A11 | bit_process zero, mid-byte (4 taken) | arm.S:150-198 | 24 + 4B | 32 | 32 | 32 → **36** | 32 |
| A12 | bit_process one, mid-byte (4 taken) | arm.S:150-198 | 24 + 4B | 32 | 32 | 32 → **36** | 32 |
| A13 | bit_process zero, end-of-byte (3 taken, `strb` D) | arm.S:144-198 | 24 + 3B + D | 32 | 32 | 32 → 35 | 32 |
| A14 | bit_process one, end-of-byte (3 taken, `strb` D) | arm.S:144-198 | 24 + 3B + D | 32 | 32 | 32 → 35 | 32 |
| A15 | one + stuffed (14 taken: DELAY(24) has 7) | arm.S:150-209 | 36 + 14B | 64 | 64 | 64 → **78** | 64 |
| A16 | sample position inside bit_process | arm.S:151-157 | 9 + B (DELAY(6) has 1 taken) | +10 | +10 | +11 | — |
| A17 | bit_process top → `bx` into flash (SE0) | arm.S:150-215 | 14 + B + L + BX | 20 | 20 → 22 | 21 (BX=3 → 21 at B=2) | none |
| A18 | first PID sample = detect + (A5 + A6 + A7) | | 46 + 26B + 3L | 104 | 104 → 110 | 104 → 130 | ≈ 3 bits + 8 (§2.2) |

Reading of the table (plain, no drama):

- **At the measured RAM column with literal pools in RAM (2,2,2) nothing moves.** Every RX
  cell is the v2 figure. The previous run's reading (RAM data = 4) would have made A13/A14 = 34
  — false.
- **Pool in flash (2,4,2):** only the PID loop moves, +4 (A8/A9 = 36) — 4 over budget, and
  A18 shifts the first PID sample by +6. Cured for zero registers by the §2.1 hoist; the
  walker's SRAM check (Appendix B) makes the flash case unreachable in a passing build.
- **B = 3:** every RX cell is over budget (A11/A12 36, A13/A14 35, A15 78) and A3/A6 grow by
  31/23. **This is the only item in the table that flips the "fits in 32" conclusion**, and no
  pad can fix a cell that is over budget: the fix is structural — fewer taken branches per
  cell. Minimum per RX cell is 2 (`b pl_got_zero`, `b bit_process`) if `b .+2` becomes
  `nop; nop` and `DELAY_CYCLES(6)` becomes 6 `nop` (RX in-slot pads must be inline, `r14` =
  POLY_RX, PLAN §7.4). Then A11 = 24 + 4·2 − 2·2 + 2B = 28 + 2B: 32 at B=2, 34 at B=3 — the
  remaining 2 at B=3 come out of the inline `nop` pad, i.e. pad = 32 − (28 + 2B) = 4 − 2B ≥ 0.
  The EOB variants (one fewer taken) then need one `nop` more than mid-byte at B=3, the
  1-cycle skew of the WG015 ledger's A11/A13 pattern (LS:74-76). Whether B is 2 or 3 is
  bench K7-K9's single most important output.
- A1/A2/A5/A7 are phase constants: they shift the §2.2 entry window and the F5 sample offset
  by the same amount and do not consume budget. With pools in RAM they are unchanged; with
  the pool in flash the F5 recomputation (`DELAY(71)` → 78) would have been derived against
  the wrong phase (see requests at the end).

RX pad-site map at (2,2,2): unchanged from v2 — no RX pad is needed, the cells are exact. At
B=3: sites are `DELAY_CYCLES(6)` (arm.S:151) and the `b .+2; nop` tails (arm.S:196-197,
203), formula 4 − 2B per mid-byte cell, 5 − 2B per EOB cell, after the `b .+2` → `nop nop`
rewrite.

### B. TX (from RAM — the T2 step 4 target)

| # | Path | Lines | f(B,L,D) | (2,2,2) = v2 | (2,4,2) | (3,2,2) | Target | Pad = target − f |
|---|---|---|---|---|---|---|---|---|
| B1 | pre_and_tok zero (2 taken, 1 literal); store idx 8 (no B/L before the store) | arm.S:411-447 | 14 + 2B + L | 20 | 22 | 23 | 32 | 18 − 2B − L (12) |
| B2 | pre_and_tok one (3 taken, 1 literal); store idx 5 + B | arm.S:411-451 | 11 + 3B + L | 19 | 21 | 22 | 32 | 21 − 3B − L (13), of which **3 − B** before the store (store-index skew zero−one = 3 − B: 1 at B=2, 0 at B=3) |
| B3 | send_inner zero, mid-byte (2 taken); store idx 10 | arm.S:465-493 | 17 + 2B | 21 | 21 | 23 | 32 | 15 − 2B (11), after the store |
| B4 | send_inner one, mid-byte (3 taken, no store) | arm.S:465-512 | 15 + 3B | 21 | 21 | 24 | 32 | 17 − 3B (11) |
| B5 | one + stuffed (11 taken: `bcs`, `beq insert`, 6× `b .+2`, `b flip_bus`, `b .+2`, `b loop`); stuffed store idx 11 + 9B | arm.S:465-533 | 18 + 11B | 40 | 40 | **51** | 64, store idx 42 | 46 − 11B (24): 31 − 9B (13) before the store, 15 − 2B (11) after |
| B6 | send_inner zero + load_next_byte (1 taken, `ldrb` D) | arm.S:462-489 | 17 + B + D | 21 | 21 (**23** if the buffer is in flash, Df=4 assumed) | 22 | 32 | 15 − B − D (11) |
| B7 | send_inner one + load_next_byte (2 taken, `ldrb` D) | arm.S:462-512 | 14 + 2B + D | 20 | 20 (22 if flash) | 22 | 32 | 18 − 2B − D (12) |
| B8 | last data bit → CRC byte 1 → loop top (2 taken) | arm.S:484-525 | 19 + 2B | 23 | 23 | 25 | 32 | 13 − 2B (9) |
| B9 | CRC byte 1 last bit → CRC byte 2 top (`beq send_inner_loop` taken) | arm.S:484-522 | 17 + 2B | 21 | 21 | 23 | 32 | 15 − 2B (11) — **2 more than B8** (arm.S:521 "TODO … additional delay") |
| B10 | last CRC bit (zero path) → SE0 store issued (6 taken: `beq done`, `beq no_really`, 4× `b .+2`) | arm.S:484-544 | 19 + 6B | 31 | 31 | 37 | ≈32 | 13 − 6B (1) |
| B11 | SE0 width, SE0 store → J store (17 taken) | arm.S:544-552 | 3 + 17B | 37 | 37 | **54** | 64 (60-72, `--gate-se0`) | 61 − 17B (27) |
| B12 | J-park → MODER release (6 taken) | arm.S:552-564 | 7 + 6B | 19 | 19 | 25 | ≥ 16 | 0 |

Turnaround-budget paths (not cells; USB 2.0 §7.1.18-19 allows 64-208 cycles, PA L-1):

| # | Path | Lines | f | (2,2,2) = v2 | (2,4,2) |
|---|---|---|---|---|---|
| C1 | entry → K-preset BSRR store (2 literals) | arm.S:356-365 | 12 + 2L | 16 | 16 → 20 |
| C2 | entry → MODER store, drivers on (5 literals) | arm.S:356-384 | 20 + 5L | 30 | 30 → 40 |
| C3 | entry → first preamble store (6 literals, 0 taken: first SYNC bit is 0 → `bcs` not taken) | arm.S:356-432 | 39 + 6L | 51 | 51 → 63 |
| C4 | `usb_send_empty` prefix (1 literal) | arm.S:347-351 | 3 + L | 5 | 5 → 7 |

Staircase (PLAN §7.4, T2): `bl rv003usb_wait_N` = C + (N − C) `nop`s. v2 assumed C = 5
(`bl` 3 + `mov pc,lr` 2). Measured `bl` = 4 (XF:26); the return is `mov pc,lr` (unmeasured,
TRM 2) or `bx lr` (measured 3) → C ∈ {6, 7}. The `rv003usb_wait_N` label ↔ cycle map must be
generated from the measured C (bench K10), not assumed; the smallest reachable pad becomes 6
or 7, and every TX pad in the table above ≥ 9 is reachable either way. B11 needs 61 > 40 (the
staircase top): two calls (e.g. 30 + 31) or a 64-entry staircase (+48 B RAM) — T2's choice.

**Conclusion of the recompute, stated once:** against the measured RAM column with literal
pools in RAM, the ledger's arithmetic conclusion does **not** change sign — every RX cell
stays exactly 32/64 and every TX cell keeps ≥ 9 cycles of pad room. Two conditions carry
that conclusion and both are bench gates, not assumptions: (i) B = 2 (measured "2-3"; B = 3
puts every RX cell 3-4 over budget — structural rewrite, formulas above); (ii) L = 2 for a
RAM-resident pool (inferred; L = 4 would put only the PID loop over, +4, cured by the
zero-register hoist). Nothing in the measured table makes the RAM placement worse; the
poisoned reading's "RAM data costs 4" is withdrawn in every row it touched (A13, A14, B6, B7,
A2's pushes).

### Pad-site map for TX (where, at (2,2,2); knob = staircase `bl` unless inline)

| Site | Serves | Cycles @ (2,2,2) | f(B,L,D) |
|---|---|---|---|
| after `str` arm.S:432, replacing `ldr CRC,=0xffff; b .+2; nop×4` (arm.S:436-442) | B1 | 12 | 18 − 2B − L |
| between `bcs` target and `sub BITCOUNT` (one path; needs its own stub label — the `#if PY32F002Bx5` `pre_and_tok_delay_one_bit` shape, arm.S:450-451) | B2 store-index skew | 1 | 3 − B |
| after `str` arm.S:481, replacing `b .+2 (; nop)` arm.S:489-492 | B3, B6 | 11 | 15 − 2B (− D + 2 on the EOB path: B6 needs the same 11 only because `beq load_next_byte` taken + `ldrb` D = `b .+2` + `b loop` at B=2, D=2; at other (B,D) the two paths split by (B + D) − (2B) = D − B) |
| after `beq insert_stuffed_bit` NT, arm.S:502-503 (one path only, before `send_end_bit_complete`) | B4, B7 | 0 | (17 − 3B) − (15 − 2B) = 2 − B relative to B3 — pad the one path by 2 − B (arm.S:511's `nop` is this pad at B=1… it is 1 today: v2's structure already equalises B3/B4 at B=2 only if this `nop` stays) |
| `insert_stuffed_bit` arm.S:529-533: replace 6× `b .+2` by one `bl` | B5 before-store | 13 | 31 − 9B |
| after the stuffed `str` — but arm.S:481 is shared with B3; the stuff path needs its own tail (own `str` copy or a stub after `b flip_bus`), the WG015 ledger's B3 remark applies (LS:151-153) | B5 after-store | 11 | 15 − 2B |
| `done_sending_data` arm.S:517-525: `beq send_inner_loop` path | B9 skew vs B8 | 2 | 2 (B-independent) |
| `no_really_done_sending_data` arm.S:537-538: replace 4× `b .+2; nop` | B10 | 1 | 13 − 6B |
| arm.S:546-548: replace 17× `b .+2` by `bl` ×2 | B11 | 27 | 61 − 17B |

Store-index invariants (from LS:230-232, same shape): pre_and_tok store index equal on both
paths (pad the one path *up* by 3 − B, never the zero path down); send_inner zero-path store
index stays 10 — pad only after the store; stuffed store index target 42 = 32 + 10.

---

## REPLACES Appendix B — cycle walker (seed for `tools/py32_cyc.py`), two-column model

Purpose unchanged: static equality gate over the linked image (`== 32/64/≤96/N` per named
path, non-zero exit on mismatch; T2 acceptance, T7 CI). Two things are new: the cost of an
instruction depends on **which section its address lands in** (flash vs RAM), and the cost of
a load depends on **where its target lands** (IOPORT / RAM / flash). The tool therefore reads
two things from the ELF, and nothing else:

1. **Section map**: `arm-none-eabi-readelf -S -W <elf>` → for every section with `A` (alloc)
   flag: name, VMA (`Addr`), size. Classify each VMA range: `RAM` if it lies inside
   `[0x20000000, 0x20000000 + PY32_SRAM_KB·1024)`, `FLASH` if inside `[0x08000000, +FLASH)`,
   else `OTHER`. `PY32_SRAM_KB`/`PY32_FLASH_KB` come from the `-D` set (T1) via
   `--sram-kb/--flash-kb` arguments; defaults 3/24 (002B). A `.timecrit` output section must
   classify as RAM or the tool aborts (placement bug, before any cycle is counted).
2. **Instruction stream**: `arm-none-eabi-objdump -d --no-show-raw-insn <elf>`. Each line gives
   the address (→ region of *execution* via the section map), mnemonic, operands, and — for
   PC-relative loads — the resolved target in the trailing comment (`ldr r7, [pc, #24] ;
   (0x200000e8 <…>)`), which is classified with the same map (→ region of the *pool*). Nothing
   is inferred from symbol names.

Cost table (`--cost-table cost.json`, R4; the defaults below are §0's RAM column with the v2
values where the measurement gives none; a second built-in table `flash` carries the flash
column for symbols that land there — e.g. the dispatch on 002B under R3/OQ14):

```
{ "exec": {
   "RAM":   { "alu":1, "bcc_taken":2, "bcc_nt":1, "b":2, "bx":3, "bl":4, "mov_pc":2,
              "ld_ioport":1, "ld_ram":2, "ld_flash":4, "lit_ram":2, "lit_flash":4,
              "push_first":2, "push_each":1, "pop_pc_extra":2 },
   "FLASH": { "alu":1, "bcc_taken":2, "bcc_nt":1, "b":2, "bx":3, "bl":4, "mov_pc":2,
              "ld_ioport":1, "ld_ram":4, "ld_flash":2, "lit_ram":4, "lit_flash":2,
              "push_first":4, "push_each":1, "pop_pc_extra":2 } },
  "ranges": { "bcc_taken":[2,3], "b":[2,3] } }
```

Classification of a load/store to choose `ld_*`: (i) `[pc,#]` → `lit_<region of target>`;
(ii) `[rX,#imm]` with `imm ∈ {0x00,0x10,0x14,0x18,0x28}` (MODER/IDR/ODR/BSRR/BRR) and the
instruction inside `.timecrit` → `ld_ioport` (the engine's only register-based accesses at
those offsets are GPIO; the walker prints every such site so a reviewer can refute it);
(iii) `ldrb/strb/ldrh` or any other offset → `ld_ram` unless the path list marks the site
`flash` (then `ld_flash`); a site marked `flash` inside a 32/64 path is an error, not a cost
(the §2.1 hard rule). `push/pop`: `first + each·(N−1)`, `pop {…,pc}` adds `pop_pc_extra`.
`b .+2` is a taken `b`. Every `b<cc>` needs a decision from the path list; unlisted → error.

Path list = a table in the engine header (T2), one line per path: `name start_label end_label
budget {branch_addr_or_label: taken|nt, …} [site:flash …]`. Required rows: A8-A15, A17 (≤ the
window), the keepalive path (≤ 96), B1-B12, C1-C4, and the 36 (or 64) staircase entries
(`rv003usb_wait_N == N`). A `bl` into the staircase is followed and its `nop`s and return
counted in the caller's path; any other `bl`/`bx` inside a budgeted path is an error.

Output: one line per path — `name f-at-table [range: min..max over ranges] budget PASS/FAIL`
— computed twice: once at the table's point values (the gate), once with every `ranges` entry
at its max (the exposure, printed, not gated until bench K7-K9 collapse the range; then
`--pin bcc_taken=2` etc. is put in the Makefile). A path whose *range* crosses its budget is
flagged `EXPOSED` so the B = 3 case of Appendix A is visible in CI before hardware.

Seed (≈60 lines, replaces the v2 one-column script):

```python
# tools/py32_cyc.py  -- static cycle walker, flash/RAM two-column model (seed)
import re, subprocess, sys, json
def sections(elf):                       # -> list of (lo, hi, name)
    out = subprocess.check_output(['arm-none-eabi-readelf','-S','-W',elf], text=True)
    secs = []
    for m in re.finditer(r'\]\s+(\S+)\s+\S+\s+([0-9a-f]{8})\s+[0-9a-f]+\s+([0-9a-f]{6})\s+\S+\s+(\S*A\S*)', out):
        lo = int(m.group(2),16); secs.append((lo, lo+int(m.group(3),16), m.group(1)))
    return secs
def region(addr, secs, sram_kb, flash_kb):
    if 0x20000000 <= addr < 0x20000000 + sram_kb*1024: return 'RAM'
    if 0x08000000 <= addr < 0x08000000 + flash_kb*1024: return 'FLASH'
    return 'OTHER'
def disasm(elf):                         # -> {addr: (mnemonic, operands, lit_target|None)}
    out = subprocess.check_output(['arm-none-eabi-objdump','-d','--no-show-raw-insn',elf], text=True)
    ins = {}
    for line in out.splitlines():
        m = re.match(r'\s*([0-9a-f]+):\s+(\S+)\s*(.*)', line)
        if not m: continue
        ops = m.group(3); lit = re.search(r';\s*\(?(0x[0-9a-f]+)', ops)
        ins[int(m.group(1),16)] = (m.group(2).rstrip('.n').rstrip('.w'), ops.split(';')[0].strip(),
                                   int(lit.group(1),16) if lit else None)
    return ins
IOPORT_OFFS = {0x0,0x10,0x14,0x18,0x28}
def cost(mn, ops, lit, exec_reg, tbl, decisions, addr, flash_sites, in_timecrit):
    t = tbl['exec'][exec_reg]
    if mn.startswith('b') and mn not in ('bl','bx','blx','bic'):
        if mn == 'b': return t['b']
        if addr not in decisions: raise SystemExit(f'{addr:#x}: undecided {mn}')
        return t['bcc_taken'] if decisions[addr] else t['bcc_nt']
    if mn == 'bl': return t['bl']
    if mn in ('bx','blx'): return t['bx']
    if mn == 'mov' and ops.startswith('pc'): return t['mov_pc']
    if mn in ('push','pop','stm','ldm','stmia','ldmia'):
        n = ops.count(',') + 1
        return t['push_first'] + t['push_each']*(n-1) + (t['pop_pc_extra'] if 'pc' in ops else 0)
    if mn.startswith(('ldr','str')):
        if lit is not None:              # pc-relative literal: cost by the POOL's region
            r = region(lit, SECS, SRAM_KB, FLASH_KB)
            if in_timecrit and r != 'RAM': raise SystemExit(f'{addr:#x}: literal pool in {r} inside timed code')
            return t['lit_ram'] if r == 'RAM' else t['lit_flash']
        m = re.search(r'#(\d+)\]', ops); off = int(m.group(1)) if m else 0
        if addr in flash_sites: return t['ld_flash']
        if in_timecrit and off in IOPORT_OFFS and mn in ('ldr','str'): return t['ld_ioport']
        return t['ld_ram']
    return t['alu']
# walk(path): follow addresses from start to end, applying `decisions` at branches and
# descending into `bl rv003usb_wait_N` (count callee nops + return, resume after the bl);
# sum cost(); run once with tbl point values (gate) and once with tbl['ranges'] maxima
# (exposure); print `name cycles [min..max] budget PASS|FAIL|EXPOSED`; exit 1 on any FAIL.
```

The tool must never take a cost from a symbol name or a `.req` alias — only from the address
→ section map and the instruction text — so that a mis-placed section (the `.datacode` glob
accident of §2.1) shows up as a cost change, not as a silent pass.

---

## NEW — §5 Bench gates for the cost table (adds to T6 bench1/bench2; T10 runs them first)

Both gates use the same kernel firmware: each kernel is a 1000× unrolled straight-line block
assembled twice, once into `.timecrit` (RAM) and once into `.text` (flash), timed with the
free-running SysTick (`VAL` before/after, HCLK source, Р9), overhead of an empty kernel
subtracted, repeated 16× → report min/max per kernel (the spread is a result: xm_030.md:468-469
says alignment and the previous instruction matter). One kernel (K2) toggles a pin so the LA
can cross-check SysTick against wall time (1 cycle = 20.83 ns at 48 MHz). The same image runs
on every board; only clock init differs.

Kernels and expected values (RAM copy / flash copy), cycles per instruction:

| K | Kernel (×1000) | Expect RAM | Expect FLASH | If it mismatches |
|---|---|---|---|---|
| K1 | `ldr r0,[r1,#0x10]`, r1 = GPIOB base (and GPIOA, GPIOF on F030 — OQ7) | 1 | 1 | ≠1 from RAM: P≠1 → every RX cell +1 with zero slack (A11-A14) → the sample structure must lose a cycle elsewhere; the IOPORT assumption is dead for that port |
| K2 | `str r0,[r1,#0x18]` alternating set/reset (LA-checked) | 1 | 1 | as K1, for TX store index |
| K3 | `ldr r0,[r1,#0]`, r1 = SRAM word | **2** | **4** | RAM ≠ 2: the swap is not on this die → A13/A14/B6/B7 +2 (EOB cells over budget); FLASH ≠ 4: rig does not reproduce the source — stop and find out why before trusting anything else |
| K4 | `ldr r0,[pc,#N]`, pool placed in SRAM (`.ltorg` in `.timecrit`) | **2 (inferred)** | 4 | 4 from RAM: L=4 everywhere → §2.1 hoist mandatory, A8/A9 +4 until hoisted, C1-C4 +2 per literal |
| K5 | `ldr r0,[pc,#N]`, pool in flash | 4 | 2 | the measured row itself; a value ≠ 4 at 48 MHz/LAT1 means the flash-side cost is latency-dependent — record, it only affects paths the hard rule already forbids |
| K6 | `ldr r0,[r1,#0]`, r1 = flash address | 4 (assumed) | 2 | sets Df; any value ≥ 3 confirms the descriptors-in-RAM rule (Р4/T4) is load-bearing, not cosmetic |
| K7 | `b .+2` ×1000, first at a 4-byte-aligned address | 2 or 3 | 2 or 3 | the B question; a run-to-run spread inside one kernel means B is not a constant → the ledger must carry the range (walker `EXPOSED`) |
| K8 | `nop; b .+2` ×1000 (every `b` at the odd halfword) | 2 or 3 | 2 or 3 | K7 ≠ K8 → alignment matters from RAM → `.balign 4` on every loop head and branch target, walker models alignment (R4) |
| K9 | `movs r0,#N; 1: subs r0,#1; bne 1b` (the `DELAY_CYCLES` shape), N = 32 | 3N−1 (B=2) or 4N−1 (B=3) | same | resolves A3/A6 (96 vs 127; 72 vs 95) directly |
| K10 | `bl wait_5` with `mov pc,lr` return; again with `bx lr` | 5 / 6 / 7 | same | sets C for the staircase label map (§7.4); the T6 item "bl rv003usb_wait_N for N = 5…40" stays and now has an expected value: N − 5 + C |
| K11 | `push {r4-r7,lr}; pop {r4-r7}` pairs (pop without pc) | 6 + 5 | 8 + 7 | RAM ≠ TRM → A2 entry constant moves; FLASH ≠ 8+7 → the flash column is not reproduced (see K3) |

**Gate 1 — does the RAM column hold at 48 MHz / LATENCY=1?** Board: PY32F030 (target #1).
Run the full set at 24 MHz, `LATENCY=0` (must reproduce xm_030.md:464-493: K3 = 2/4, K5 = 4/2,
K11 = 6/8 — this is the calibration of the rig against the source), then at 48 MHz
(HSI24 × PLL2, `LATENCY=1`). Pass: K1-K4, K7-K11 **identical** between the two runs for the RAM
copy (cycle counts are per HCLK; a RAM-resident kernel touches no flash, so latency must not
appear). Fail: any RAM-copy kernel that differs between 24/LAT0 and 48/LAT1 → the RAM column is
frequency-dependent on this part (the source's 55-86 MHz "no wait states" test does not
transfer) → re-ledger with the 48 MHz numbers via `--cost-table`; the flash-copy kernels are
allowed to differ (that is what LATENCY does) and their 48 MHz values become the `FLASH`
column for R3/OQ14.

**Gate 2 — does F002B share the table?** Board: PY32F002B (B-C silicon, `DBG_IDCODE`
recorded, R1). Same image, run at 24 MHz `LATENCY=0` (factory HSI24 word) and at
`HSI_FS=101` with `LATENCY=1` (the factory 48 MHz word gives 43.12 MHz, XF:60 — irrelevant
here, counts are per HCLK; LATENCY must be 1 above 30 MHz, xm_002b.md:259 via XF:94). Pass:
every kernel equals Gate 1's F030 RAM-copy value. Fail: F002B gets its own `--cost-table`
(`Makefile.py32` selects it by `MCU`), Appendix A is re-evaluated in that column, and if K3 = 4
from RAM on F002B the "RAM is favourable" conclusion of §2.1 is F030-only — the 002B ledger
then loses 2 on A13/A14 and must find them (the `b .+2` → `nop nop` rewrite frees 0; the
structural option is the branchless EOB of `rx-tx-branchless-ch32v003-rebased`, branch_notes
Part B). If K7/K8 differ from F030, B is per-MCU in the walker.

Neither gate needs USB traffic, a host, or the engine; both run in T10 before the first
enumeration attempt and their numbers go into `doc/py32/calibration.md` next to
`DBG_IDCODE`, HCLK and LATENCY.

---

## Requests to owners of sections I do not own (not edits)

| Section / task | Effect of this rework | Ask |
|---|---|---|
| §2.2 entry window | A2 unchanged at L=2; +4 if the entry literals' pool were in flash | none unless K4 = 4; then window [11,74] → [7,70] |
| §2.4 F5 (`DELAY(71)` → 78) | derived at (2,2,2); A18 = 104 unchanged at L=2, 110 at L=4 | re-derive `USB_RX_SYNC_DELAY` only after K4; state the (B,L) it assumes |
| §7.4 staircase | C = 5 assumed; measured `bl` = 4 → C ∈ {6,7} | label map generated from K10; smallest pad ≥ C |
| T2 step 2 (`.ltorg`) | now a hard rule, walker-enforced | acceptance: walker reports 0 literal targets outside SRAM |
| T2 step 4 | pads as formulas in `usb_port_py32_tune.h` with B as a parameter (`USB_B_TAKEN` default 2), not integers | same shape as LS:196-228 |
| T6 bench1/bench2 | replaced by K1-K11 above (superset) | adopt the kernel list and the two gates |
| R4 / OQ4 | "taken branch 2 (TRM) vs 3 (Grainuum)" is now "2-3 measured from RAM, alignment-dependent per the source" | reword; K7/K8/K9 close it |
| R3 / OQ14 | dispatch in flash reads RAM data at 4/access (flash column) — not timed, acceptable | note only |
