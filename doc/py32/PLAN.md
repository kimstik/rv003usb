# PY32 (Cortex-M0+) port of rv003usb — analysis and fleet plan

Status: **v2, 2026-09-04** — v1 (2026-09-03, sole-analyst deliverable) merged with the prior-art
survey `doc/py32/PRIOR_ART.md` (the evidence appendix; it is not edited by this plan). This file is
the single source of truth the fleet executes; every divergence from v1 is listed in §12.
Every non-obvious claim carries its evidence inline: `arm.S:<n>` = `rv003usb/rv003usb-arm.S` line
in commit 0ad3c42 (`origin/py32`); `S:<n>` = `rv003usb/rv003usb.S` at HEAD 5342825 (identical to
176d357, the PRIOR_ART base, outside `doc/py32/`; v1 mislabelled these as master numbering —
they were branch numbering all along, e.g. `handle_se0_keepalive` is S:740 here and 673 at
80b1893); `c:<n>`/`h:<n>` = `rv003usb/rv003usb.{c,h}` at master 80b1893 (v1 numbering, kept);
`c@HEAD:<n>` = `rv003usb.c` at 5342825 (used for cites imported from PRIOR_ART); `dfu.c:<n>` =
`bootloader_dfu/dfu.c` at HEAD; `vcd:<n>` = `tools/wg015_vcd/wg015vcd.py` at HEAD; `PA S-n / A-n /
D-n / L-n / Q-n / T-n / §n` = the STEAL / AVOID / DIVERGE / LS-trap / open-question / tool rows and
sections of `PRIOR_ART.md`; `RM002B p<n>` = PY32F002B Reference Manual V1.0 page; `RMBC p<n>` =
PY32F002B-C Reference Manual V1.0; `DS002B p<n>` = PY32F002B Datasheet V1.0; `DS030`/`RM030` =
PY32F030 Datasheet V1.8 / RM V1.7; `TRM p<n>` = Arm Cortex-M0+ TRM DDI0484B (all URLs in §1).
"Build log" = my rebuild of the branch with arm-none-eabi-gcc 13.2.1 against py32f0-template
@289ffc8 (the pinned submodule).

## 0. Verdict in one paragraph

The port is believed achievable as a cycle-exact 32-cycles-per-bit Thumb-1 engine executing
entirely from SRAM, **primary target PY32F030** (HSI 24 MHz × PLL2 — the only 48 MHz path in
the family that is inside a datasheet, DS030 p2/p64; PLL lock at 48 MHz measured on the same
die, `xm_030.md:336`) and **second target PY32F002B**, whose factory 48 MHz trim word runs the chip
at a measured 43.12 MHz (−10.2 %, `xm_002b.md:172-175`, `:209-210`) and which therefore needs an
HSI self-calibration against its LSI before the D− pull-up is asserted (§10 R19/R21, gate G4).
Two v2 decisions were re-examined against the Xiamatsu measurements: **Р4 (engine in RAM)
survives** — with code in RAM ordinary instructions cost 1, RAM data 2, PUSH/POP 2+1 and the
GPIO port runs at full speed, «не замедляется, как ожидалось» (`xm_030.md:481-493`, `:447`); the
one new trap is narrow: a PC-relative literal load whose pool sits in flash costs 4 from RAM
code (`xm_030.md:490`), so every literal pool of `.timecrit` must land in SRAM (R23). The target
order is flipped (v2 had F002B first). Confidence: the cycle discipline itself is high — the
engine is a translation of a working RISC-V engine with a walker-verified ledger — but the
**cost table it is padded against is medium**: one author, one die (F002A/F003/F030), Flash
Latency 0, below 24 MHz (`xm_030.md:466`), with `BL`/`BX`/taken-branch costs that already disagree
with the TRM (4/3/2-3 vs 3/2/2, `xm_030.md:472,478-479,488,492-493`), no measurement at 48 MHz and
none on the F002B die. On F030 the servo (Р5) survives but changes role: at room temperature
most units should enumerate from the factory word alone (DS030 T5-15: ±0.7 % @25 °C against
the 0.5 % centred cell margin of §2.4.5 — note the −0.04 % figure in CHIP_FACTS_XIAMATSU.md §2 was measured on an
**F002B's** 24 MHz constant, `xm_002b.md:206`, not on an F030), and the servo is needed for
temperature (±2 % over 0–85 °C, same table); on F002B the servo cannot even start from the
factory word (−10.2 % is outside its ±8.3 % sanity window, `S:762-772`), hence the calibration
stage. The single unknown that can still sink the port is **gate G1**: if the per-instruction
costs at 48 MHz / LATENCY 1 do not reproduce the RAM column of the measured table — port and RAM
access no longer 1/2 cycles, or costs that vary with alignment as the author warns
(`xm_030.md:468-469`) — then every ledger, pad and staircase constant is void and no static tool
can recover them; the only path back is to re-measure the table on the bench and re-pad. For
F002B alone the corresponding unknown is its LSI as a reference: −0.18 % on the one unit
measured (`xm_002b.md:204`) but ±3 % by datasheet (DS002B T5-14) — enough to bring the servo
into capture, not enough to promise enumeration without it (G4).

## 1. Evidence base

| Source | Where | Used for |
|---|---|---|
| PY32 branch | `origin/py32` = 0ad3c42, read-only checkout; rebuilt in scratch with the pinned submodule | engine, build hack, dead `#if` |
| PY32F002B DS V1.0 | https://www.puyasemi.com/download_path/数据手册/MCU%20微处理器/PY32F002B_Datasheet_V1.0.pdf | 24 MHz max, HSI table 5-13, memory table 5-15 |
| PY32F002B RM V1.0 | https://www.puyasemi.com/download_path/用户手册/MCU%20微处理器/PY32F002B_Reference_Manual_V1.0.pdf | flash sequences, GPIO/EXTI/RCC regs, boot modes, Load Flash |
| PY32F002B-C RM V1.0 | https://www.puyasemi.com/download_path/用户手册/MCU%20微处理器/PY32F002B-C_Reference_Manual_V1.0.pdf | fmax 48 MHz, HSI_FS=101, 48 MHz trim/flash-timing addresses |
| PY32F030 DS V1.8 / RM V1.7 | https://download.py32.org/Datasheet/en/PY32F030_Datasheet_V1.8.pdf ; https://www.puyasemi.com/download_path/用户手册/MCU%20微处理器/PY32F030_Reference_Manual_V1.7.pdf | 48 MHz, PLL×2, HSI table 5-15, single-cycle multiplier, flash |
| PY32F002A DS V0.2, PY32F003 DS R1.2 | download.py32.org (see §3.3) | exclusion (24 / 32 MHz max) |
| Arm Cortex-M0+ TRM r0p0 DDI0484B | https://www.keil.com/dd/docs/datashts/arm/cortex_m0p/r0p0/ddi0484b_cortex_m0p_r0p0_trm.pdf | Table 3-1 cycle counts, §2.2.2 single-cycle I/O port, §3.6.1 15-cycle latency |
| py32f0-template @289ffc8 | https://github.com/IOsetting/py32f0-template (cloned) | CMSIS device headers, LL/HAL flash+clock code, rules.mk, ld, startup |
| This repo | `doc/wg015/*`, `rv003usb/wg015/*`, `bootloader_dfu/*`, `rv003usb/rv003usb.S`, `wg015_bench/*` | target architecture to mirror |
| `doc/py32/CHIP_FACTS_XIAMATSU.md` (2026-09-04) | measurements transcribed from the Xiamatsu READMEs `py32f002a_003_030` and `py32f002b`, fetched 2026-09-04 | every `CHIP_FACTS_XIAMATSU.md §n` cite; `xm_030.md:<n>` / `xm_002b.md:<n>` = the README line it quotes. Single units at room temperature; none of it is a specification |
| `doc/py32/BUILD_FACTS.md` (2026-09-04) | produced by building this branch in this container, `arm-none-eabi-gcc` 13.2.1 | every `BUILD_FACTS.md §n` cite — build/link/section facts, cited and never re-derived |
| `doc/py32/DEFECTS_VERIFIED.md` (2026-09-04) | defects verified against the engine source | every `DEFECTS_VERIFIED.md D-n` cite; §9.5 maps each defect to its owning task |
| `doc/py32/PRIOR_ART.md` (v1, 2026-09-04) | six prior-art sweeps merged; Grainuum/TheYkk line numbers fetched 2026-09-04 | every `PA` cite in this file; the licence class of each source (Р10) |
| Grainuum (xobs) `grainuum-phy-ll.s` | https://github.com/xobs/grainuum — **MIT** | pad staircase (PA S-1), slew finding (PA S-9), loopback idea (PA S-6) |
| joyboot (xobs) `bootloader.c`, `flash.c` | https://github.com/xobs/joyboot — **MIT** | boot-failure counter (PA S-7), jump sanity (PA S-8) |
| Pico-PIO-USB @5a37a66 | https://github.com/sekigon-gonnoc/Pico-PIO-USB — **MIT** | timer-quantization slack (PA S-3), "nothing timing-adjacent in flash" (PA S-4), `test_ll.c` loopback |
| uf2-samdx1 | https://github.com/adafruit/uf2-samdx1 — **MIT** | double-tap idiom (already in `dfu_015.h:44-76`) |
| LemcUSB | https://github.com/lemcu/LemcUSB — **GPLv3** (+emlib exception) | ideas only (RAM execution, single-cycle I/O port remark) — no code (Р10) |
| stm32f030-vusb (ads830e) | https://github.com/ads830e/stm32f030-vusb — **GPL-3.0** | ideas only (2-cycle GPIO is enough at 32 cyc/bit) — no code (Р10) |
| V-USB / micronucleus | obdev — **GPLv2**/commercial | lessons only (PA A-1, A-6, A-7, S-10) — no code (Р10) |
| TheYkk/py32f030-bitbang-usb | https://github.com/TheYkk/py32f030-bitbang-usb — no licence stated | AVOID list only (PA A-8, A-9, A-15, A-19); nothing taken |

Tooling used and reproducible: `arm-none-eabi-gcc 13.2.1` (apt), `riscv64-unknown-elf-gcc`
(present), PyMuPDF for PDF text, a 60-line objdump cycle walker (Appendix B) that reproduced
every RX slot at exactly 32/64 cycles. No simulator gives Cortex-M0+ cycle truth (PA §6, T-8…T-11:
QEMU/Renode disclaim it) — the walker plus the LA loop through `tools/wg015_vcd` is the gate.

Two cite forms carried in from the rework blocks: `PLAN:<n>` is a line of **this file at
88d1229** — the v2 text a block overturns or quotes, never a line of the current text; `LS:<n>`
is a line of `doc/wg015/ledger_static.md`, the house ledger format this plan's Appendix A
follows.

## 2. The ARM engine (`rv003usb-arm.S`, 573 lines) in detail

### 2.0 The cost model this rework is computed against (read first)

Measured on live F002A/F003/F030 silicon, Flash Latency = 0, i.e. ≤ 24 MHz (CHIP_FACTS_XIAMATSU.md:12-26,
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
ожидалось» (xm_030.md:481, CHIP_FACTS_XIAMATSU.md:28); a separate test executes from RAM at 55-86 MHz with «нет
тактов ожидания» and «доступ к портам на полной скорости» (xm_030.md:440-457, CHIP_FACTS_XIAMATSU.md:29-31).

The author's own caveat, which this ledger inherits verbatim: «определить выполнение инструкций
сложно, так как зависит от выравнивания и зависимости от предыдущей инструкции»
(xm_030.md:468-469, CHIP_FACTS_XIAMATSU.md:45-48). These are typical numbers, not guarantees. Two things are
therefore UNVERIFIED and are bench gates (Appendix D): whether the RAM column holds at 48 MHz
with `LATENCY=1` (measurements were at Latency 0, CHIP_FACTS_XIAMATSU.md:14; Latency 0 ends at 24 MHz on F030 and
30 MHz on F002B, CHIP_FACTS_XIAMATSU.md:94), and whether F002B — a different die shared with L020 — shares it at
all (CHIP_FACTS_XIAMATSU.md:50-51).

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

### 2.1 Placement: what runs from RAM, what from flash, and why it works at all

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
unanimity (PA S-4, D-2). Both still stand, but the measured table (§2.0) gives the reason its
actual shape, and it is favourable rather than merely necessary:

1. RAM-resident code is not slower than flash-resident code: «не замедляется, как ожидалось»
   (xm_030.md:481); ordinary instructions are 1 cycle in both columns (CHIP_FACTS_XIAMATSU.md:18, :34-35).
2. Data in RAM is **cheaper** from RAM code: `ldr/str` to RAM 4 → **2** (CHIP_FACTS_XIAMATSU.md:22). The engine's
   RAM data accesses inside timed code are the EOB `strb SHIFT_BUF,[r2]` (arm.S:147) and the
   TX `ldrb SHIFT_BUF,[r0]` (arm.S:463); both stay at the 2 cycles v2 assumed. Had the engine
   been in flash they would be 4 — the EOB cell would be 34, over budget.
3. Stack traffic is cheaper: `push/pop` 4+1 → **2+1** per register (CHIP_FACTS_XIAMATSU.md:23). The ISR prologue
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
costs **4** instead of 2 (CHIP_FACTS_XIAMATSU.md:21, :40-42). This is the trap, and it is the opposite of the trap
the previous run believed in (it applied the flash column to RAM code and concluded RAM data
was expensive — that reading would have put every EOB cell at 34 and the TX byte-load at +2,
both false alarms, while missing the literal-pool cost entirely).

Why the branch's RX works at all with two literal loads per PID slot (arm.S:90, :92): its
literal pool sits inside `.datacode`, i.e. **in RAM** (0x200000e8, table above). A PC-relative
load from a RAM pool is, on the M0+'s single AHB-Lite port, a data load from RAM — expected 2,
the v2 number. This inference is UNVERIFIED (the measured row is "literal from flash"); bench
K4 in Appendix D measures it directly. If it comes back 4 the hoisting rule below is mandatory rather
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

### 2.2 Interrupt entry and latency compensation (arm.S:40-62)

```
40 EXTIx_y_IRQHandler:          ; symbol built as LOCAL_EXP(USB_DM_IRQ, Handler)  (h:26-35 of 0ad3c42)
41   ldr r3, =GPIOx              ; 2 cyc (literal, RAM)
42   ldr r0, [r3, #IDR]          ; 1 cyc (IOPORT)  -> first sample 3 cycles after the first instruction
44-47 push {r4-r7,lr}; mov lr,r9; mov r7,r8; push {r7,lr}   ; 6+1+1+3 = 11 cyc, saves r8/r9 + EXC_RETURN
49-51 ldr r2,=USB_DMASK; ands r0,r2; beq handle_se0_keepalive ; SE0 => keepalive path
53-56 mov r9,r3; movs r1,#8; movs r6,#6                      ; GPIO_BASE, BITCOUNT, BITSTUFF
58 #define DELAY_CYCLES(c) mov SCRATCH,#((c+1)/3); sub SCRATCH,#1; bne .-1   ; = exactly 3*((c+1)/3) cycles
62   DELAY_CYCLES(96)            ; "90 to 117 would work ... use less than the mean so it'll work with a delayed interrupt"
70-77 preamble_loop: ldr r5,[r3,#IDR]; ands r5,r2; cmp r5,r0; beq preamble_loop  ; 5 cyc/poll; exits on the first state change; cmp r5,#0 => SE0 => done
83   DELAY_CYCLES(71)            ; = 72 cycles
```

Mechanism: sample the bus once at entry (state K if entry is <32 cycles late, J if 32-64), wait
`96+21` cycles, then spin until the bus state differs from the entry sample, i.e. lock onto a
SYNC edge, then `72` cycles later start the 32-cycle PID slots. Paper window (TRM costs, entry
sample in SYNC bit 0): `preamble_loop` must start inside SYNC bit 4 (K, 128-160 cycles after
the D− edge) → first-instruction time ∈ [11, 42]; if the entry sample fell into bit 1 (J), it
must start inside bit 5 → [43, 74]. Union = **first instruction 11…74 cycles after the D−
edge is tolerated**; nominal Cortex-M0+ entry is 15 cycles (TRM p3-10 §3.6.1: "worst case
interrupt latency, for the highest priority active interrupt in a zero-waitstate system not
using jitter suppression, is 15 cycles") + 1-2 for the 1-WS flash vector fetch + 0…N for an
abandoned LDM/STM. Margin is therefore ≈ −5 / +55 cycles: safe against late entry, essentially
none against early entry (impossible below 15). The author's "90 to 117" bracket is consistent
with this (window width 27 ≈ the 32-cycle bit minus poll granularity).

Sample phase: from the `ldr` that detects the edge to the first PID sample = 10 + 72 + 22 =
**104 cycles = 3 bits + 8 cycles** (walker, Appendix A). Adding the 0…4-cycle poll granularity,
samples sit at **cell offset ≈ 8…12 of 32** (25-37 %), i.e. early. Consequence in §2.4.5.

The preamble spin (arm.S:70-74, and S:182-205 on RISC-V) has no exit other than a state change
or SE0: resume signalling is K for ≥ 20 ms (USB 2.0 §7.1.7.5, PA L-13) and a shorted D+ is K
forever, both spent with IRQs masked → F9.

`DELAY_CYCLES(c)` reaches only multiples of 3 and burns `SCRATCH`; the plan's pads use an
exact-N staircase instead (§7.4, PA S-1).

### 2.3 RX slot structure and register file

Registers (arm.S:22-27, 79, 91, 237, 375, 388): r0 = last masked bus state, r1 BITCOUNT,
r2 write pointer (rxbuf+3), r3 SHIFT_BUF, r4 SCRATCH, r5 sample/temp, r6 BITSTUFF (6→0),
r7 CRC, r9 GPIO_BASE, r12 pin mask (RX) / bit length (TX), r14 POLY_RX (RX only; legal because
EXC_RETURN was pushed at arm.S:44-47 and `pop {…,pc}` at arm.S:342 performs the exception
return; r14 is *free* from entry until `mov POLY_RX, r5` in the PID decode, arm.S:128-140 —
the two entry pads may therefore use `bl`, §7.4), r8 FLIP_MASK (TX only), r10/r11 unused.
In TX, `usb_send_data` pushes `lr` in its prologue (`push {r4-r7,r14}` arm.S:357) and never uses
r14 afterwards → every TX pad may use `bl`. Thumb-1 pressure: every 16-bit ALU op needs r0-r7,
so each sample costs `mov r5,r9; mov r4,r12` (2 cycles) before `ldr r5,[r5,#IDR]`.
AAPCS check: the ISR pushes 28 B then `push {r5}` (arm.S:256,302,325) before `blx` → SP is
8-byte aligned at every C call; 5th argument `ist` on the stack matches the C prototypes.

Slot ledger (Appendix A, all measured on the real object with TRM costs: ALU 1, `B<cc>` 2 taken
/ 1 not, `B` 2, `BX/BLX` 2, IOPORT `LDR/STR` 1, other `LDR/STR/LDRB/STRB` 2, `PUSH` 1+N):

| Path | Lines | Cycles | Note |
|---|---|---|---|
| packet_type_loop, zero or one bit | arm.S:85-114 | 32 / 32 | sample done at +22 from the loop top |
| packet_type → bit_process transition, token vs DATA | arm.S:128-148 | 4 = 4 | `beq .+4` skips balanced ("constant amount of time either way", arm.S:134) |
| bit_process zero, mid-byte / end-of-byte | arm.S:150-198 | 32 / 32 | EOB path: `beq is_end_of_byte`(2)+`movs`+`strb`(2)+`add` replaces `b .+2; nop; b`(5) |
| bit_process one, mid-byte / end-of-byte | arm.S:175-198 | 32 / 32 | |
| one + stuffed bit (arm.S:200-209) | | 64 | stuffed cell consumed blind (no transition check, "TODO" arm.S:201) |
| sample position inside a bit_process slot | | +10 from top | `DELAY(6)+lsr+mov+mov+ldr` |
| SE0 detected → `bx` into flash | arm.S:213-215 | 20 | |

The CRC is computed in-slot in both bit paths (arm.S:167-171, 179-184, Domkeykong trick), the
polynomial is chosen from PID bit 2 (arm.S:128-140: `0xa001`/`0xffff` for DATA vs
`0x14`/`0x1e` CRC5 for tokens) exactly like S:318-329. The dispatch checks the residual
before any C call (`cmp CRC,#0` for tokens, `0xb001` for DATA, arm.S:262-314) — the same
"verdict ready at EOP" property PA S-2 credits Pico-PIO-USB with; only the *response* latency
remains to be gated (`wg015vcd.py tx --gate-turnaround 7.5`, vcd:879-884).

### 2.4 Findings on the RX path (numbered F-*, referenced by tasks)

| # | Finding | Evidence | Severity |
|---|---|---|---|
| F1 | Endpoint bound off-by-one: `cmp r2,#ENDPOINTS; bhi done` accepts `endp == ENDPOINTS` → `ist->eps[ENDPOINTS]` written by `usb_pid_handle_setup/out/in` (c:485-494, 241-247) → memory after the struct corrupted. RISC-V uses `bgeu a2,s0` (S:528) | arm.S:276-277; disassembly `cmp r2,#2; bhi.n` | bug |
| F2 | No packet length bound: r2 increments per byte forever (RISC-V bounds by `s1 = USB_BUFFER_SIZE*8`, S:309/408). Long noise/garbage → writes past `rxbuf` (15 B) into .bss/heap/stack | arm.S:145 "TODO: prevent buffer overrun", 146-148 | robustness |
| F3 | Stuffed-bit slot not validated (RISC-V: `c.beqz a0, done_usb_message` S:461) — a stuffing violation is silently accepted (CRC catches most); hosts may deliberately abort a packet with one (PA L-7, OpenTitan gap) | arm.S:200-209 | minor → required (PA D-7) |
| F4 | `handle_se0_keepalive` is a two-instruction stub: no SE0→SE0 frame measurement, no HSI trim, no `delta_se0_cyccount` telemetry | arm.S:217-220 vs S:740-806 | design gap, blocks production use on HSI (§2.4.5) |
| F5 | Sample phase ≈ 8-12/32 (early). Fast-clock drift margin ≈ 8-12 cycles, slow-clock ≈ 20. **Physical requirement, not a preference** (PA D-9, L-4): a receiver must accept a last bit lengthened by up to 260 ns of dribble = 12.5 cycles (USB 2.0 §7.1.9/§7.1.14) — a sample earlier than offset 14 in the slot after the last data bit reads the dribble as a spurious 1 → non-byte-aligned frame → aborted (`S:475-477`) | §2.2 | **must**: `DELAY(71)` → 78, `rx` offset histogram min ≥ 14 (T10 gate) |
| F6 | EXTI ack clears only `1<<USB_PIN_DM` in `EXTI_PR` at the end (arm.S:334-336); no equivalent of `RV003_ADD_EXTI_MASK/HANDLER` (S:113-129, 600-614) — another EXTI line on the same vector (EXTI2_3 = lines 2 and 3; EXTI4_15 = 12 lines) would livelock | RM002B p97 vector table | feature gap |
| F7 | NVIC priority never programmed (`NVIC_EnableIRQ` only, 0ad3c42 c:157) → USB IRQ at priority 0 but so is every other IRQ incl. SysTick; an equal-priority ISR is not preempted and delays entry by its full length (window is +55 cycles) | RM002B p97 "4 programmable priority levels" | must fix in port header |
| F8 | `RV003USB_OPTIMIZE_FLASH=1` unsupported: no Thumb `usb_pid_handle_ack/setup`; the .S always `blx`es the C versions, which are compiled out under that flag (c:471-495) → link error | arm.S:257,300 | constraint (DFU configs use the flag) |
| F9 | Unbounded preamble spin: `preamble_loop` (arm.S:70-74) exits only on a bus-state change or SE0; resume signalling (K ≥ 20 ms, USB 2.0 §7.1.7.5) or a stuck line is spent inside the ISR with IRQs masked (RISC-V S:182-205 has the same shape) | PA A-16, L-13 | robustness: bound at ≈16 bit-times (T2 step 3b) |

#### 2.4.5 Why the servo is not optional

Bit cell = 32 cycles; a relative clock error ε shifts the sample by 32·ε per bit. A maximum LS
packet (11 bytes = 88 bits + ≤14 stuff bits ≈ 100 cells) accumulates 3200·ε cycles. With the
early sample (F5) the fast-side margin is ≈ 8 cycles → ε < 0.25 %; centered it is 16 → 0.5 %.
DS002B p38 Table 5-13 (24 MHz row): `fHSI 23.83…24.17 MHz @25 °C` (±0.7 %), temperature
drift `−2…+2 %` (0-85 °C), `−4…+2 %` (−40-85 °C), `fTRIM fine-tuning accuracy 0.1 %`; DS030
p61-63 Table 5-15 identical numbers. No row exists for the 48 MHz mode. USB LS itself requires
±1.5 % (USB 2.0 §7.1.11). Conclusion: without a keepalive-driven trim loop the device works only
on units/temperatures where the factory trim happens to land within ≈0.25 %; with the 13-bit
trim (`RCC_ICSCR.HSI_TRIM[12:0]`, RM002B p62-63; F030 header `RCC_ICSCR_HSI_TRIM_Msk 0x1FFF`)
and a 1 ms keepalive reference the residual is bounded by one trim LSB (≈0.1 %) — the same
mechanism the CH32V003 port relies on (S:781-797, 5-bit trim there). Field corroboration (PA
§5.1): Grainuum runs LS at 47.972 MHz (−0.058 %) from a 32.768 kHz FLL without trouble — a fixed
offset well inside the margin is harmless, drift beyond ≈0.25 % without a servo is what kills a
device (PA A-5: LemcUSB never shipped RC operation; TheYkk has no trim at all).

Lock budget (PA S-12, A-6, L-14): keepalives reach the device only from port-enable onward and a
host may issue GET_DESCRIPTOR as soon as 10 ms after reset (USB 2.0 §7.1.7.3); Windows 10
shortened that gap versus Windows 7 and broke V-USB's one-shot OSCCAL (obdev thread t=9959).
Starting from the factory word (±0.7 % @25 °C = ≈7 trim LSB) the loop must land inside ≈0.25 %
within ≤ 8 keepalives and must then stop hunting → two-rate law (Р5, T2 step 5), N measured in
T10 (OQ9).

### 2.5 TX path (arm.S:345-569)

| Step | Lines | Mechanism |
|---|---|---|
| Bus turnaround | 362-365 | `BSRR = (1<<DP) \| (1<<(DM+16))` **before** enabling drivers (preset K), then |
| Drivers on | 367-372, 384 | read-modify-write of `MODER` to `01` (output) on DP/DM; entry → MODER store = 20 + 5L: **30** with the literal pool in RAM (L=2), 40 with it in flash (L=4) — five `ldr =` in arm.S:363-374; Appendix A row C2 |
| NRZI | 387-389, 426, 476-478 | `r5` = absolute BSRR word for the pair; `FLIP_MASK r8 = set+reset bits of both pins`; `eor r5, r8` swaps J/K; `str r5,[GPIO,#BSRR]` (1 cycle — IOPORT, measured «на полной скорости», xm_030.md:447). Identical idea to S:871 `t1` |
| Bit stuffing | 412, 428, 482, 501-502, 527-533 | `BITSTUFF r6` 6→0 → `insert_stuffed_bit`: 5-6× `b .+2` then `b flip_bus` — each is a taken branch (B = 2-3 from RAM, §2.0); the stuffed cell carries 11 of them, Appendix A row B5; the `subs BITSTUFF; beq insert_stuffed_bit` (arm.S:501-502) precedes `send_end_bit_complete`'s bit-count test (arm.S:505) exactly as S:1023-1025 precedes S:1058-1062 → the trailing stuff bit after a six-ones CRC tail *should* be emitted (PA L-6, OQ11 — walker path "one+stuffed at the last CRC bit" must show 64, T2) |
| CRC16 | 466-474, 496-499, 514-525 | in-slot, sent LSB-first after the payload; `poly_function=2` disables (usb_send_empty = token + two 0x00 bytes = a ZLP with CRC 0x0000, 345-351, same as S:823-828) |
| SE0/EOP | 535-552 | `BSRR = reset both` → 17× `b .+2` → `BSRR = set DM` (J) → 6× `b .+2` |
| Release | 556-564 | `MODER` RMW back to input (`eor` of the `01` bits) |

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
Latency 0 (CHIP_FACTS_XIAMATSU.md:16-18) and nobody has measured Latency 1 at 48 MHz; it is also irrelevant once
TX is in RAM (T2 step 2), so it is dropped from the ledger rather than defended. The per-part
`#if PY32F002Bx5` nop variants (arm.S:402-408, 415-424, 444-446, 490-492, 530-532) and the
alignment assert (arm.S:421-423) remain what §2.6 says they are — never assembled, unknown.
v2's sentence "SRAM is 0-WS and, per the TRM, alignment-free" is **withdrawn**: the measured
RAM column reports taken branches as 2-3 with the explicit note that alignment and the
previous instruction matter (xm_030.md:468-469), so an alignment effect from RAM is possible
and bench K7/K8 (Appendix D) decides it — if it exists, `.balign 4` on loop heads plus walker
re-padding is the fix (R4), not the assert.

Carried forward from v2 and untouched by the cost rework (both are flash-side facts, and
the block above only re-prices the RAM side): PY32 flash is a flat LATENCY=1 with no
prefetch buffer and no cache anywhere in the RM (PA D-2, 517-page RM030 grep), but the core
fetches 32 bits ahead over 16-bit instructions (TRM §2.2.1), so a branch target's half-word
alignment changes its *flash* cost — the artefact the `.ifeq … .error` assert guards. The
same failure is on record elsewhere: Grainuum issue #1 ("Running deterministic from Flash",
open since 2016) and its comment that jumps > 48 B from flash "cause random amounts of
jitter" (PA A-2).

### 2.6 The dead variant and the build hack

`Makefile.py32:42` lists the engine as `AFILES := ./Build/../rv003usb/rv003usb-arm.o` so that
`rules.mk:107-110`'s `$(BDIR)/%.o: %.s` pattern is bypassed by the private rule
`Makefile.py32:110-114` (`$(BDIR)/%.o: %.S`), which uses `$(TGT_ASFLAGS)` =
`-mcpu=cortex-m0plus -gdwarf-3 -Os -Wa,--warn` (rules.mk:53) — **without `$(addprefix -D,
$(LIB_FLAGS))`**, which rules.mk:49 adds only to `TGT_CFLAGS`. Build log line for the engine:
`arm-none-eabi-gcc -mcpu=cortex-m0plus -gdwarf-3 -Os -Wa,--warn -I … -c ../rv003usb/rv003usb-arm.S`
— no `-DPY32F002Bx5`. Proof in the object: `bcs.n <pre_and_tok_send_one_bit>` (the `#else`
form), six `b.n .+2` in `insert_stuffed_bit` (the `#if !PY32F002Bx5` extra one present).
Assembling with `-DPY32F002Bx5=1` by hand produces a different object (760 vs 764 B `.text`).
So every PY32 board the author tested ran the "non-002B" TX padding; the 002B-specific tuning
was never exercised and must be treated as unknown. Also: the `.o` is written into the source
tree (`rv003usb/rv003usb-arm.o`, hence `*.o` in the branch's .gitignore).

Guard (PA S-11, A-3; hard acceptance in T1 and T2): the engine's first lines are
`#ifndef RV003USB_PY32 / #error "rv003usb-arm.S assembled without the target defines" / #endif`
plus `#if !defined(PY32F002B) && !defined(PY32F030) / #error / #endif`, and `Makefile.py32`
feeds the **same** `$(DEFS)` variable to its `.S` and `.c` rules. Mechanical proofs: the `.S`
compile line in `make -n` output contains `-DRV003USB_PY32=1` (T1); assembling the engine
without `-DRV003USB_PY32` exits non-zero (T2); with the unmodified branch engine, the
`MCU=PY32F002Bx5` and `MCU=PY32F030x8` objects differ (`cmp` exits 1) because `-D$(MCU)=1` now
reaches the assembler (T1).

Other hard-coded numbers and their part/clock binding:

| Constant | Where | Bound to |
|---|---|---|
| `DELAY_CYCLES(96/71/12/6/24)` | arm.S:62,83,86,151,202 | 48 MHz, 32 cyc/bit, M0+ costs; 96 also encodes the ≈15-20 cycle entry latency |
| GPIO A/B/C/F bases, MODER 0/IDR 0x10/BSRR 0x18 | arm.S:3-15 | PY32F0 IOPORT map (§3.4) — both families |
| `EXTI 0x40021800`, `EXTI_PR 0x0C` | arm.S:3,17 | both families (§3.4) |
| `USB_DM_IRQ` = EXTI0_1/2_3/4_15 by pin | h:26-35 (0ad3c42) | both families |
| TX nop counts | arm.S:402-533 | flash fetch timing of the non-002B parts at 48 MHz LATENCY=1 |
| `MY_ADDRESS_OFFSET_BYTES`, `ENDPOINTS`, `USB_BUFFER_SIZE` | via rv003usb.h | shared |
| Clock bring-up | demo_gamepad.c (0ad3c42): `BSP_RCC_HSI_48MConfig()` (002B) / `BSP_RCC_HSE_PLLConfig()` (F030x8, needs a 24 MHz crystal) | vendor LL |

### 2.7 branch_notes.md Part A — verified / refuted

| Claim (doc/wg015/branch_notes.md) | Verdict |
|---|---|
| "Hot code runs from RAM: `.pushsection .datacode`" (line 45) | True for RX only; TX and dispatch run from flash (§2.1). The RAM placement is a glob side effect, not a designed section |
| "IRQ-entry skew handled by `DELAY_CYCLES(96)`" (41) | True; the window derivation in §2.2 is new |
| "M0+ prefetch makes taken-branch cost depend on target alignment" (49) | Unproven from RAM; the assert exists but the effect is a flash-fetch artifact — from 0-WS RAM the TRM gives `B` = 2 regardless (Table 3-1). Grainuum's source comment claims "taken branch = 3" on Kinetis (PA §1 row 1, Q-11) → bench2 item, both alignments, RAM and flash |
| "Per-variant cycle deltas `#if PY32F002Bx5`" (50) | Misleading: that variant was never built (§2.6) |
| "Thumb register pressure … r8/r9/r12/r14 as slow spill homes" (54) | True; note `r10/r11` are free and the plan uses them for the debug marker (§7.3) |
| "Startup/vector table/linker: entirely vendor template's" (66) | True; the plan drops the template (§6.3) |
| "USB_DM_IRQ abstracted once" (63) | True; kept, moved into the port header |
| Lessons 1-5 / anti-patterns 1-4 (68-87) | Adopted; this plan is their application (anti-pattern 2 = §2.6 guard) |

## 3. Chip facts (verified)

### 3.1 Which parts can do 48 MHz

Two different facts decide the table: whether the part *reaches* 48 MHz, and whether the
frequency it reaches *at reset from the factory constant* is inside the USB tolerance
(±1.5 %, USB 2.0 §7.1.11) and the engine's sampling margin (≈0.44 % with the 14–18/32 sample
band of F5, §2.4.5). The second fact is measured, not specified, and it flips the target order.

| Part | Max f (DS) | 48 MHz path | Measured at reset (Xiamatsu) | Flash / RAM | Verdict |
|---|---|---|---|---|---|
| **PY32F030x6/x8** | **48 MHz** (DS030 p2, p5) | HSI `HSI_FS=100` (24 MHz, factory trim at `0x1FFF0F10`) × PLL2, or HSE 4–32 MHz × PLL2 with a 24 MHz crystal. PLL input: DS030 Table 5-17 says 12–24 MHz, Xiamatsu 16–24 MHz (xm_030.md:15) and "PLL_IN — only 24 MHz" (:79) — the discrepancy is immaterial because only the 24 MHz input yields 48 MHz; TheYkk's "8 MHz × 6" cannot exist (PA A-19). `PLLON/PLLRDY` RM030 p77, `PLLSRC` p83, tLOCK 15/40 µs DS030 p64 | HSI 24 MHz factory word → **23.99 MHz (−0.04 %)** (xm_030.md:15); × 2 = **47.98 MHz, −0.04 %**, inside the USB tolerance and the sampling margin with **no trim step at all** (CHIP_FACTS_XIAMATSU.md §2) | 32/64 K, 4/8 K RAM (template README) | **Primary target.** The only part whose 48 MHz is in its datasheet *and* whose factory constant lands inside tolerance. Default `MCU`, reference for every ledger and bench. HSI build needs no calibration before enumeration; the servo question is reduced to drift (Р5) |
| **PY32F003** | 32 MHz (DS003 p1; PLAN v2 listed it as "no PLL") | Same PLL path as F030: "Проверено — PLL запускается на 48 МГц на чипах PY32F002A и PY32F003" (xm_030.md:336) | same HSI family as F030 (CHIP_FACTS_XIAMATSU.md §2 groups F002A/F003/F030 under one measurement set) | per DS003 — not extracted here; T1 takes the ld numbers from the DS | **Primary family, out-of-spec member.** Reaches 48 MHz by measurement only; its DS says 32 MHz and lists no PLL, so a product on F003 at 48 MHz runs outside its datasheet. Usable as a development/cost-down twin of F030 once T10 shows bench1–6 equal on it (OQ-B). Whether F003/F002A/F030 are one die remains **UNVERIFIED** (PA §5.1); the measurement proves a locking PLL, nothing more |
| **PY32F002B ("B-C" silicon)** | DS002B V1.0: **24 MHz**; RMBC p14: fmax 48 MHz; Xiamatsu: "для F002B не объявлена поддержка HSI 48 MHz" (xm_002b.md:6) | HSI only: `HSI_FS=101` (RMBC p58), factory word at `0x1FFF0104`, 48 MHz flash-timing set at `0x1FFF0130…0x140` (RMBC p24/p30). **No PLL; HSE is a clock *input* only (1–32 MHz)** (CHIP_FACTS_XIAMATSU.md §2; DS002B p2; `RCC_CR_HSEON` absent from py32f002bx5.h) → no crystal path to 48 MHz either | Factory word `[0x1FFF0104] = 0x0000B3A2` → `HSI_FS=0b101`, `HSI_TRIM=0x13A2`, nominally 49.60 MHz by the author's formula (xm_002b.md:269-270); **measured 43.12 MHz** (MCO/2 = 21.56 MHz, xm_002b.md:172-175; "калибровочная константа установлена неверно", :209-210) = **−10.2 %**. Enumeration from the factory word is impossible in principle (USB ±1.5 %). 48 MHz *is* inside the trim range: at `HSI_FS=101`, `TRIM_L` 0x000…0x1FF spans 21.7–33.4 MHz with `TRIM_H=0` (:249-257) and `TRIM_H` scales that range by +33 % (0b0110) / +41 % (0b0111) / +50 % (0b1000) (:232-246) → 48 MHz lies between `TRIM_H` 0b0111 and 0b1000, "достижимо с запасом, но только собственной калибровкой" (CHIP_FACTS_XIAMATSU.md §2) | 24 K / 3 K, page 128 B (RM002B p22) | **Second target, with a precondition:** the firmware must trim the HSI itself against an on-chip reference *before* asserting the D− pull-up (Р5). Reference available at reset: LSI, factory-trimmed, measured 32.71 kHz vs 32.768 nominal = **−0.18 %** (xm_002b.md:204-206). Second reference, only after connection: the host keepalive (1 ms). 128 B OTP exists for a per-board constant (CHIP_FACTS_XIAMATSU.md §3; address and write sequence not extracted — T1/T5 from RM002B) |
| PY32F002A | 24 MHz (DS002A p2), HSE 4–24 MHz | PLL measured to lock at 48 MHz (xm_030.md:336) — same status as F003 | as F003 | as F003 | Not planned. Same standing as F003 (out of DS); nothing in this plan depends on it |

Sanity: the template README's "PY32F0xx up to 48 MHz" is right in the sense that the PLL locks
on all three F0xx parts (xm_030.md:336) and wrong in the sense that only F030's datasheet says
so. Datasheets still win for what a product may claim.

What the Xiamatsu figures do **not** establish (kept honest here, closed in §11): unit-to-unit
spread of the F030 HSI at 48 MHz (DS030 Table 5-15 guarantees only 23.83–24.17 MHz = ±0.7 % at
25 °C; one unit measured −0.04 %), its temperature/voltage drift (DS: ±2 % 0–85 °C, −4/+2 %
−40…85 °C — outside both the USB tolerance and the sampling margin, §2.4.5), the LSI drift on
F002B, and whether the −10.2 % factory constant is universal or one unit's.

### 3.2 Core and timing

The TRM table stays the reference for *what the core can do*; the measured table below is the
reference for *what it costs on this silicon*. Where the two disagree, the measurement is the
working figure and the difference is a bench item, not a footnote.

| Fact | Source |
|---|---|
| Cortex-M0+, 2-stage pipeline, single-cycle multiplier on PY32 | DS002B p8, DS030 p17; TRM p1-5 Table 1-1 |
| Interrupt latency 15 cycles (zero WS), LDM/STM abandoned+restarted, late-arrival/tail-chain | TRM p3-10 §3.6.1 |
| TRM instruction costs: MOV/ALU 1; `B<cc>` 1/2; `B` 2; `BL` 3; `BX/BLX` 2; `MOV PC,Rm` 2; `LDR/STR` 2 on AHB, 1 on the single-cycle I/O port; `PUSH` 1+N; `POP{…,PC}` 3+N; `NOP` 1 | TRM p3-4…3-7 Table 3-1 + footnotes b, e |
| **Measured costs depend on where the code executes from, and the access costs swap** (F002A/F003/F030, Flash Latency 0, ≤24 MHz; author's own timing runs): | xm_030.md:464-493 via CHIP_FACTS_XIAMATSU.md §1 |
| — ordinary instructions: 1 from flash, 1 from RAM | :471 |
| — branch taken / not taken: 2 / 1 from flash, **2–3 / 1 from RAM**; `B` 2–3 either way; `BX Rm` 3; `BL` 4 | CHIP_FACTS_XIAMATSU.md §1 table (vs TRM 2 / 2 / 3 — see the staircase row) |
| — `LDR/STR` to GPIO: 1 from flash, "на полной скорости" from RAM | :473, :447 |
| — `LDR` of a flash literal via PC: **2 from flash, 4 from RAM** | :474 |
| — `LDR/STR` to SRAM: **4 from flash, 2 from RAM** | :475 |
| — `LDM/STM/PUSH/POP`: 4 + 1·(n−1) from flash, **2 + 1·(n−1) from RAM** (= TRM's 1+N) | CHIP_FACTS_XIAMATSU.md §1 table |
| "не замедляется, как ожидалось" for RAM execution; separate test executing from RAM at 55–86 MHz "нет тактов ожидания", "доступ к портам на полной скорости" | :481; :440-457 |
| Author's caveat: "определить выполнение инструкций сложно, так как зависит от выравнивания и зависимости от предыдущей инструкции" — the numbers are typical, not guaranteed | :468-469 |
| Consequence 1 — placement: a RAM-resident engine (Р4) is the *cheap* configuration on this part: SRAM data 2, stack 2+1, ports full speed, no wait states at 48 MHz. The RM-derived worry of v2 (flash prefetch making `ldrb` 2-or-3 cycles, PLAN:468) is replaced by a measured one and the conclusion is stronger, not weaker | CHIP_FACTS_XIAMATSU.md §1 |
| Consequence 2 — the literal-pool trap: from RAM code, a `ldr rX, [pc, #imm]` whose pool sits in flash costs 4. **No such load may appear inside a timed bit cell.** With every engine block in `.timecrit` and `.ltorg` after each block (T2 step 2) the pools are in SRAM and cost 2 like any SRAM load; the branch's RX ISR already has its pool in RAM (PLAN:91). The walker must therefore *check the address* of every `[pc,#…]` load reached from a timed path: SRAM → 2, flash → error (request to T2, `tools/py32_cyc.py`) | CHIP_FACTS_XIAMATSU.md §1; PLAN:808 |
| Consequence 3 — the flash-code column is the cost model for anything left in flash (R3's "dispatch back to flash" fallback, C handlers): every SRAM access from there is 4, and at 48 MHz with `LATENCY=1` the fetch itself adds wait states the table does not contain (it was taken at Latency 0). Turnaround (R8), not bit cells, is what this hits | CHIP_FACTS_XIAMATSU.md §1, §3 |
| Consequence 4 — the staircase identity `bl rv003usb_wait_N` = N (§7.4) assumes `BL` 3 + `MOV PC,LR` 2 (TRM). Measured `BL` is 4 and `BX` 3; `MOV PC,LR` was not measured. If `BL` 4 holds from SRAM every entry is N+1 — a relabel, but one that must come from bench2, not from either table (OQ4) | CHIP_FACTS_XIAMATSU.md §1; PLAN:601-604 |
| Consequence 5 — taken branch 2–3 from RAM is consistent with Grainuum's "taken branch = 3" (PA §1 row 1) and with the author's alignment caveat → `.balign 4` on loop heads (R4) is now the expected outcome of bench2, not a surprise | CHIP_FACTS_XIAMATSU.md §1; PA Q-11 |
| Measured on F002A/F003/F030 only; F002B is a different die (shared with L020) and "действуют ли те же цены на F002B … не проверено" | CHIP_FACTS_XIAMATSU.md §1 (ОТКРЫТО) → OQ-B |
| Flash wait states: Latency 0 measured to hold to 24 MHz on F030 (xm_030.md:466) and to 30 MHz on F002B (xm_002b.md:259) → `LATENCY=1` is mandatory at 48 MHz on both (RM002B p38 "two system clock cycles are required for each Flash read"; vendor BSP `LL_FLASH_LATENCY_1`, py32f002b_bsp_clock.c:29-30); CHIP_FACTS_XIAMATSU.md §3: "на 48 МГц латентность ненулевая, XIP-тайминги плавают". No prefetch buffer / cache documented (RM030 §4.2.2 p26, §4.8.1 p42-43; PA D-2) | CHIP_FACTS_XIAMATSU.md §3; RM002B p38 |
| Single-cycle I/O port: "accessible both by loads and stores … You cannot execute code from the I/O port"; GPIO A/B/C(/F) on it per the RM memory map (`0x5000_0000` IOPORT). Measured 1-cycle port access (above) confirms the port used by Xiamatsu; port F on F030 and the F002B ports are not separately confirmed (OQ7) | TRM p2-3 §2.2.2; RM002B p15-18, p76; RM030 p18-20, p100; CHIP_FACTS_XIAMATSU.md §1 |
| Fetch-ahead limited to 32 bits; "Instruction fetch width 16-bit only or mostly 32-bit" is a vendor option (unknown on PY32) | TRM p2-2 §2.2.1, p1-5 Table 1-1 → bench2 |
| SysTick present, `CALIB` 6000 (1 ms @ HCLK/8 → HCLK 48 MHz), `VAL` 24-bit down-counter (wraps every 349.5 ms at 48 MHz with `LOAD=0xFFFFFF`) | RM002B p97 §11.1.2; RMBC p84; py32f002bx5.h:53 |
| VTOR present; vendor `SystemInit` writes `SCB->VTOR` | py32f002bx5.h:51; system_py32f002b.c:132-137 |
| NVIC: 2 priority bits (4 levels), 32 IRQ lines | RM002B p97 §11.1.1 |
| "During a program and erase operations … any attempt to read the Flash memory will stall the bus" → XIP programming is legal, CPU stalls; writing `FLASH_CR` while `BSY` stalls too | RM002B p23-24; RM030 p27-28 |
| CSS: if HSE fails the clock falls back to HSI and an NMI is raised → HSE builds need an `NMI_Handler` (T1); a silent fallback to the untrimmed 24 MHz HSI drops the link | RM030 §8 CSS (PA §5.4) |
| MCO on PA7 tops out at ≈35 MHz (F002B) → 48 MHz cannot be observed on MCO undivided; Xiamatsu used MCO/2 (21.56 MHz reading for 43.12 MHz). Every bench that "measures 48 MHz on the LA" (bench6, T10 clock verification) must use the MCO prescaler and say so. F030 MCO pin/prescaler not extracted here — T6 from RM030 | xm_002b.md:261 via CHIP_FACTS_XIAMATSU.md §3 |
| NRST needs a "SWD-Delay" of ≈100 ms in the startup files (Xiamatsu errata section) — relevant to the bring-up rig (PA A-12), not to the engine | CHIP_FACTS_XIAMATSU.md §3 |
| UART ISP loader at BOOT0=1 on the F0xx family (xm_030.md:374) — confirms §3.5 (`puyaisp`); F002B has none | CHIP_FACTS_XIAMATSU.md §3 |

### 3.3 Memory and flash geometry / sequences

| Item | PY32F002B(-C) | PY32F030 | Source |
|---|---|---|---|
| Flash | 24 K @0x08000000-0x08005FFF, aliased at 0 | 16-64 K @0x08000000 | RM002B p18 Table 3-1; py32f030x8.h:471 |
| SRAM | 3 K @0x20000000-0x20000BFF | 2-8 K | RM002B p18; py32f030x8.h:478 |
| Page / sector | 128 B / 4 KB | 128 B / 4 KB | RM002B p22 §4.1; py32f030x8.h:473-475 |
| Program | whole page, 32 words, "only accept 32 bit program", HardFault on half-word/byte: unlock KEYR (0x45670123, 0xCDEF89AB) → set `PG`+`EOPIE` → write words 1-31 → set `PGSTRT` (CR bit 19) → write word 32 → poll `SR.BSY` (bit 16) → check/clear `EOP` → clear `PG` | same | RM002B p24-25 steps 1-10; RM030 p28; py32f002b_hal_flash.c:281-304 (`FLASH_Program_Page`) |
| Page erase | unlock → set `PER`+`EOPIE` → "Write arbitrary data (32-bit) to the page" → poll BSY | same | RM002B p25; HAL `FLASH_PageErase`: `SET_BIT(CR,PER); *(vu32*)addr = 0xFF` |
| Sector erase | `SER` + write to sector | same | RM002B p26 |
| Timing registers | `TS0,TS1,TS2P,TPS3,TS3,PERTPE,SMERTPE,PRGTPE,PRETPE` (FLASH_R_BASE+0x100…0x120) must hold the factory values for the **current HSI frequency**: 24 MHz set at `0x1FFF011C…0x1FFF012C`, 48 MHz set at `0x1FFF0130…0x1FFF0140` (B-C) | per HSI_FS: 24 MHz set at `0x1FFF0F6C…` (`_FlashTimmingParam[]`) | RM002B p33-35, p44-46; RMBC p24 Table 4-2, p30; py32f0xx_hal_flash.c:61, py32f002b_hal_flash.c:61 (`{…,0x1FFF0130,…}` at index 5 = HSI_FS 101); macro `__HAL_FLASH_TIMMING_SEQUENCE_CONFIG` (py32f002b_hal_flash.h:462-472) |
| Times | tprog 1.0/1.5 ms, tERASE 3.5/5.0 ms (typ/max) | 1.0/1.5, 3.0/4.5 | DS002B p39 Table 5-15; DS030 p64 Table 5-18 |
| Endurance | 100 K cycles (−40…85 °C) | | DS002B p39 Table 5-16 |
| Factory trim | HSI_TRIMMING_FOR_USER: word = `HSI_FS[15:13] \| HSI_TRIM[12:0]`, "read … then write to HSI_FS and HSI_TRIM in RCC_ICSCR": 24 MHz @0x1FFF0100, **48 MHz @0x1FFF0104** | 24 MHz @0x1FFF0F10 (`(0x4<<13)\|(*0x1FFF0F10 & 0x1FFF)`) | RM002B p33; RMBC p31, p59; py32f002b_ll_rcc.h:384-386; py32f0xx_ll_rcc.h:455-462 |
| Boot modes | `nBOOT1`/`nBOOT0` (option bytes): main flash / SRAM / **Load Flash** (1-4 KB at the top of main flash, `0x08005000-0x08005FFF` for 4 K, aliased at 0; pages configured as Load Flash "will not be erased" by page erase); `FLASH_BTCR` boot control register; **no ROM loader** → SWD is the only recovery (PA A-12) | BOOT0 pin + nBOOT1: main flash / system memory (3.5 KB ROM UART loader, `puyaisp`) / SRAM | RM002B p20-21 §3.6, p25, p42 §4.8.8; RM030 p21, p24-25 |
| Reset cause | `RCC_CSR` @+0x60: `IWDGRSTF`29 `SFTRSTF`28 `PWRRSTF`27 `PINRSTF`26 `OBLRSTF`25, `RMVF`23 (write 1 clears); software reset = SYSRESETREQ (`SCB->AIRCR = 0x05FA0004`) | same offsets + `WWDGRSTF`30 | RM002B p56-57 §7.1, p73; py32f030x8.h:3389-3408 |
| SRAM across system reset | RM says a system reset "sets all registers to their reset values except … the reset flag register" (RM002B p56); SRAM is not in that list and stop mode explicitly keeps SRAM (p51/p53). Retention through SYSRESETREQ is the STM32-family norm but **not stated** → OQ2, verified in T10 | same | RM002B p56, p51/p53 |
| Option bytes / RDP | never written by our firmware: RDP level 1 on a PY32F003 could not be undone with J-Link (py32f0-template #36, PA A-11) | | rule in T5 (`! grep -rn 'OPTR\|RDP' bootloader_dfu/dfu_py32.h bootloader_dfu/py32/`) |

### 3.4 Peripherals the engine touches

| Block | Layout | Source |
|---|---|---|
| GPIO (`x=A,B,C` on 002B; `A,B,F` on F0xx) | `MODER 0x00, OTYPER 0x04, OSPEEDR 0x08, PUPDR 0x0C, IDR 0x10, ODR 0x14, BSRR 0x18, LCKR 0x1C, AFR[2] 0x20/0x24, BRR 0x28`; bases `0x50000000 + 0x400·{A=0,B=1,C=2,F=5}`; BSRR: "Write any bit to 0 … does not have any effect", set wins over reset; MODER 2 bits/pin: 00 input, 01 output; OSPEEDR 2 bits/pin, 4 settings, `00` = lowest (T3 confirms the encoding against RM002B p78 when writing `py32_min.h`) | py32f002bx5.h:239-251, 443-445; py32f030x8.h:265-277, 525-527; RM002B p78-79, p85 |
| EXTI | base `0x40021800` (AHBPERIPH+0x1800, both families): `RTSR 0x00, FTSR 0x04, SWIER 0x08, PR 0x0C (write-1-clear), EXTICR[n] 0x60+4n (port select per line, 8 bits/line: mask 3 for lines 0-4, 1 for 5-7 on 002B), IMR 0x80, EMR 0x84` | py32f002bx5.h EXTI_TypeDef; py32f002b_ll_exti.h:143-160, 649-654; RM002B p100 §11.2.4 |
| EXTI IRQ numbers | `EXTI0_1_IRQn=5, EXTI2_3_IRQn=6, EXTI4_15_IRQn=7` (vector 0x54/0x58/0x5C) | RM002B p97; py32f002bx5.h enum; startup_py32f002b.s:133-135; F030 identical (py32f030x8.h) |
| RCC | `CR 0x00 (HSION bit8, HSIRDY 10, HSIDIV[13:11]; F030: PLLON 24, PLLRDY 25)`, `ICSCR 0x04 (HSI_TRIM[12:0], HSI_FS[15:13])`, `CFGR 0x08 (SW[2:0], SWS[5:3])`, F030 `PLLCFGR 0x0C (PLLSRC bit0)`, `IOPENR 0x34 (GPIOxEN)`, `CSR 0x60` | py32f002bx5.h RCC_TypeDef; py32f030x8.h:338-359; RM030 p83 |
| SysTick | core, `0xE000E010`: CTRL/LOAD/VAL(24-bit down)/CALIB; **PY32 rule (Р9): LOAD is always 0xFFFFFF (free-running)** — the engine's keepalive delta assumes it | TRM/CMSIS; RM002B p97 |
| NVIC/SCB | `ISER 0xE000E100`, `IPR 0xE000E400` (2 bits/priority in bits 7:6), `VTOR 0xE000ED08`, `AIRCR 0xE000ED0C`, `ICSR 0xE000ED04` (`PENDSTSET` bit 26, used by `dfu_port_cycles()`) | ARMv6-M ARM / TRM; no CMSIS file is copied (register maps re-derived, Р10) |
| 5 V tolerance | not specified in DS002B/DS030 pin tables (no "FT"/"5 V tolerant" anywhere) → assume **not** tolerant; run VDD = 3.3 V (USB LS signalling is 3.3 V, VBUS→LDO) | grep of both datasheets |
| Electrical | VDD 1.7-5.5 V; −40…85/105 °C; GPIO OSPEEDR exists — D± driven at the **lowest** setting plus 33 Ω series (Р8; v1 said "high"); no tr/tf table in either DS (OQ10) | DS002B p2,p5; RM002B p78; README.md:31 (33/47 Ω) |

### 3.5 Toolchain, probes and recovery (PA §5.4, A-12)

| Fact | Consequence for the plan |
|---|---|
| No PY32 support in mainline OpenOCD (only experimental forks) | `Makefile.py32` `flash` target uses pyOCD (with Puya's `PY32F0xx_DFP` imported by hand) or J-Link; no OpenOCD in CI or docs (T1, T7) |
| pyOCD on PY32F002x5 fails on most DAPLink clones, ST-LINK v2 and J-Link ("Unexpected ACK", pyOCD #1523, open) | bring-up rig = J-Link + Puya DFP or a known-good DAPLink, recorded in `calibration.md` (T10) |
| F030 has a ROM UART loader (`pip install puyaisp`, RX on PA2/PA9/PA14); **002B has none** | the DFU address guard (`dfu.c:143-149`, refuses `addr < DFU_APP_BASE`) is load-bearing on 002B — T5 never weakens it |
| Community template (`IOsetting/py32f0-template`) passes `-D` to C only | §2.6 guard |
| No public errata sheet for any PY32 | every RM number in `py32_min.h` cites a page (T1); silicon revision (`DBG_IDCODE`) recorded next to every measurement (T10) |

## 4. Drift: the 29 master commits 9c8a442..80b1893 touching `rv003usb/`

Classification of what the ARM side never learned. "Engine" = must be mirrored in
`rv003usb-arm.S`; "C" = arrives for free once the shared C compiles for PY32; "n/a" = WCH-only.

| Commit | Change | ARM impact |
|---|---|---|
| 25c5946 Add USB terminal | `RV003USB_USB_TERMINAL` (DM-register debug over HID, WCH-only), `swio.h` | C: flag must be 0 / `#error` on PY32 (pulls `lib/swio_self.h`) |
| 1f791fa Upgrade to ch32fun | `#include "ch32fun.h"` in .S/.c; submodule dir `ch32fun/` | C/build: shim must be named `ch32fun.h` (as `rv003usb/wg015/ch32fun.h`); `.gitmodules` rename (branch still says `ch32v003fun`) |
| 9763396, 3556981, 5963978, 8627d27, 5e37a84, f3e3f40, 15a22e2, 0d364aa | terminal fixes/formatting; `if( tosend <= 0 \|\| !tsend )` (c:230) | C (free) |
| ed6fab0 GP→0x200003fc (tiny boot) | S tiny-boot only | n/a |
| 9f5835e, b2028e1 | terminal example; reboot-feature fix | C (free) |
| 23fbbd4, 0c2a92e, 0843bc6 | `RV003_ADD_EXTI_MASK/HANDLER` shared-EXTI hook in the ISR (S:113-129, 600-614, 645-650) | **Engine**: F6 — provide the same hook (T2, optional level) |
| f5d5543 | merge | — |
| dac9d71, 1b334c0 | HSITRIM servo cleanup (always trim, no readback) | Engine reference for the servo (T2) |
| 46f1461, d7d0d4e | SYSTICK base register; `ENDP_OFFSET` folding | n/a (ARM has no asm ack/setup) |
| b76eb87 Add CH32V00x | RAM execution (`.srodata,"ax"`), `VOOXDELAY`, far dispatch (`lui/addi + c.jr`), 2 MHz→speed nibble; .c: DEBUG_TIMING V00x, `GPIO_CFGLR_*` names | Engine: conceptually the ARM already runs RX from RAM; **C**: DEBUG_TIMING block is WCH-only → excluded on PY32 |
| 7329c1d Fixes requested by cnlohr | S restructure; c: `GPIO_CNF_IN_FLOATING` for D± (c:139-140) | C: PY32 D± must be floating inputs (PUPDR=00) |
| edf0b63 v006 bootloader | h: `struct usb_endpoint` re-laid out (`count` now 16-bit at +4, `EP_*_OFFSET` renumbered, h:133-138, 160-170); c: `RV003USB_BOOTLOADER` hooks `runwordpad/runwordpadready/reset_timeout` (c:156-160, 221-228, 251-254), `RCC->RSTSCKR \|= 0x1000000` in the reboot path (c:183) | Engine: unaffected (only `MY_ADDRESS_OFFSET_BYTES` is used, arm.S:281); C: hooks arrive free — enables a PY32 HID-blob loader on the shared C layer (T9); reboot seam #4 needs a PY32 body |
| aa2d591, 2c19ea5, 4143b98, 11bfb94 | timing nudges / tiny-boot RAM clear / keepalive jump refactor | n/a |
| eeba5cf | `length > 3` → `length > 0` for small user-data packets (c:272) | C (free) |

Net C/H drift the ARM branch conflicts with (dry-run cherry-pick of 0ad3c42 onto WG015+80b1893,
6 conflicts: `.gitignore`, `.gitmodules`, `Makefile`, `demo_gamepad/demo_gamepad.c`,
`demo_gamepad/usb_config.h`, `rv003usb/rv003usb.c`; `rv003usb/rv003usb.h` auto-merges). The
branch's `#if __riscv / #elif PY32F002Bx5 / #else` includes (0ad3c42 c:10-20) and its
`usb_setup` forks (c:62-66, 108-159) collide with the WG015 restructuring
(`#if defined(WG015) && WG015` at c@HEAD:62 / c:64-89 of v1's HEAD) — resolution rules in T0.
The two commits after 176d357 (this file and PRIOR_ART.md) touch only `doc/py32/`, so the
dry-run result stands.

Feature flags / behaviours the ARM side never saw and must be decided per target:
`RV003USB_USB_TERMINAL` (→0/#error), `RV003USB_BOOTLOADER` hooks (usable), `RV003USB_DEBUG_TIMING`
(→#error), `RV003USB_USE_REBOOT_FEATURE_REPORT` default 1 (h:46-53) → needs the PY32 seam
(branch dodged it by setting 0 in usb_config.h), `RV003USB_OPTIMIZE_FLASH` (F8),
`RV003_ADD_EXTI_MASK` (F6), `RV003USB_SUPPORT_CONTROL_OUT` (needed by DFU, C-only, free).

## 5. Gaps versus the WG015 target

| WG015 has | PY32 branch has | Plan |
|---|---|---|
| `rv003usb/wg015/`: shim `ch32fun.h`, `K1921VG015_min.h` (self-written, license-clean), `startup_wg015.S`, `wg015_common.ld` + 2 variants, `Makefile.wg015`, stdio stub | vendor submodule (Apache/PUYA mixed), vendor startup/ld, top-level `Makefile.py32` with the object-path hack | T1: `rv003usb/py32/` with the same shape, no submodule |
| Per-site macro contracts in one `rv003usb.S` | forked Thumb file with `#if` ladders | T2: separate file **by necessity**, same macro vocabulary, zero `#if <part>` inside, `#error` guard against the §2.6 hole |
| C seams `#if WG015` in rv003usb.c + reboot seam #4 | `#if __riscv` ladders (older base) | T3: `usb_port_<chip>.h` per target, one selector |
| demo_hidapi conditioned | demo_gamepad only | T4: both demos |
| `bootloader_dfu/{dfu.c, dfu_rv003usb.h, dfu_015.h, dfu_v003.h, wg015/, v003/}` | none | T5: `dfu_py32.h` + `py32/` (+ boot-failure counter, PA S-7) |
| `bootloader_wg015` HID-blob loader + 5 blobs + hidapi CLI with bcdDevice gate | none | T9 (optional, after DFU) |
| `wg015_bench/` P1 calibration set (bench1-6, UART menu) | none | T6: `py32_bench/` bench1-6 |
| — (no writer→reader loopback anywhere in the repo) | none | T11: `py32_bench/bench7_loopback.c` + vector generator (PA S-6) |
| `tools/wg015_vcd` (chip-agnostic VCD analyzer; gates entry/excursion/turnaround, EOP width reported only, vcd:448, 678-701, 861-893), `tools/wg015mkdfu.py` | none | reuse; T5 adds `--bcddevice/--pid/--vid` to mkdfu; T6 adds `--marker-edge` and the `--gate-se0` EOP-width gate (PA T-2, A-14) |
| `doc/wg015/{PLAN,STATE,TODO,chip_info,ledger_static,review_findings}` | none | this file + PRIOR_ART.md + T8 (STATE.md carries the provenance ledger, Р10) |
| CI builds everything (`make all`) | `build_py32` hook that needs the submodule | T7 (+ `make check-cycles`, PA T-1) |
| Size gates in loader Makefiles (`sizecheck`, bootloader_dfu/wg015/Makefile:14-22) | none | T5/T9 |
| Timing verification method (ledger + LA + VCD) | hand annotations only | Appendix A/B + T6/T10/T11 |

## 6. Architecture decisions (argued both ways)

Two corrections drive this block, and they hit the *justifications* harder than the conclusions:
(a) the instruction-cost table depends on where the code executes from, and the prices swap
(CHIP_FACTS_XIAMATSU.md §1) — v2 argued placement from a flash-column reading of the RM; (b) the primary part flips
to F030/F003 (CHIP_FACTS_XIAMATSU.md §2). Convention used below, so the reader can skip what did not move:

* **unchanged, no edit** — the decision text of PLAN:422-553 stands as written.
* **justification replaced** — the conclusion stands, the stated reason does not. In a plan this
  is a defect and not a cosmetic one: the next person re-derives from the reason, not from the
  answer, and a reason that is wrong will not survive contact with a case v2 never considered.
* **changed** — the decision itself moves.

| Р | Subject | Status |
|---|---|---|
| Р1 | separate `rv003usb-arm.S` | conclusion stands, **justification replaced** (cost numbers) |
| Р2 | per-target `usb_port_<chip>.h` | unchanged, no edit |
| Р3 | no vendor submodule | conclusion stands, **evidence added** (BUILD_FACTS.md §4, §6; DEFECTS_VERIFIED.md D-4/D-5) |
| Р4 | code placement | **changed**: split RX/TX, one rule, new gate |
| Р5 | clocking, part order, servo | **changed**: target flip (CHIP_FACTS_XIAMATSU.md §2, §4.1) |
| Р6 | bootloader layout | mechanism unchanged, **per-part numbers move** |
| Р7 | interrupt policy | conclusion stands, **justification replaced** (vector-fetch cost) |
| Р8 | D± drive strength | unchanged, no edit |
| Р9 | SysTick free-running | mechanism unchanged, **one constant corrected** |
| Р10 | licence and provenance | unchanged, no edit |

---

**Р1. Engine seam — sibling inside `rv003usb.S` vs separate `rv003usb-arm.S`.**
Conclusion unchanged (separate Thumb file, per-site macro contract §7.1, no `#if <part>` in the
engine). **Justification replaced.** v2 closed the argument against Grainuum's runtime
`struct GrainuumUSB` of register addresses (PA D-1) with "every address in such a struct is a
2-cycle AHB load inside a 32-cycle slot; compile-time literals from an IOPORT base register cost
1" (PLAN:434-436). Both halves are wrong under the measured table:

* the struct load is 2 cycles only when the struct *and the code* are in RAM (CHIP_FACTS_XIAMATSU.md §1: RAM data
  from RAM code = 2); from flash-resident code the same load is **4** (xm_030.md:475). The
  branch's TX path is flash-resident today (BUILD_FACTS.md §3), so a Grainuum-style struct would cost 4
  there, not 2 — the argument is stronger than v2 made it, in the place v2 did not look.
* "compile-time literals cost 1" is not a thing: a literal reaches a register through
  `ldr Rd,[pc,#imm]` at 2 (pool in RAM) or **4** (pool in flash, from RAM code — CHIP_FACTS_XIAMATSU.md §1,
  xm_030.md:474). Only the *port access itself* is 1. The correct statement of the rule is the
  one Appendix A now uses: the GPIO base is loaded into a register **once, outside every timed
  cell**, and the cell contains only the `ldr/str` at P = 1.

For (against the seam): one file, one ledger discipline, the macro table exists. Against: the
bodies share no instruction, no register file and no exception return; a Thumb "body" inside
`rv003usb.S` is 100 % `#if`. **Decision unchanged.** New supporting evidence, in-family rather
than hypothetical: across F030/F003 and F002B every base address and every register offset the
timed code touches is identical — GPIOA 0x50000000, GPIOB 0x50000400, EXTI 0x40021800,
RCC 0x40021000, `IDR` +0x10, `BSRR` +0x18, matching `arm.S:14-15` (BUILD_FACTS.md §7). The address layer of
the engine survived the target flip with zero edits, which is exactly the property the
`usb_port_<chip>_asm.h` seam was designed to have.

---

**Р2. C seam — keep the `#if` ladders vs per-target `usb_port_<chip>.h`.**
Unchanged, no edit. Neither correction touches it: the selector is per-*chip*, not per-part, and
the F030/F003 and F002B builds share one `py32/usb_port_py32.h`. (The default `MCU` value moves
with Р5; that is a `Makefile.py32`/T1 constant, not a seam change.)

---

**Р3. Vendor submodule vs self-written minimal header/startup/ld.**
Conclusion unchanged (no submodule; `py32_min.h`, `startup_py32.S`, own linker scripts,
`Makefile.py32`). **Evidence added, and it is now first-hand rather than argued:**

For the submodule: tested clock/flash code, all parts covered, and it supplies the linker
scripts and startup files the branch actually links against. Against: it is **empty on the
branch, so the branch cannot link as published** (DEFECTS_VERIFIED.md D-4, BUILD_FACTS.md §6) — the strongest possible form
of the "50 MB dependency" objection, since the dependency is not even present; the `Build/../`
object hack, the LL/HAL licence mix and the `-D` bug in `rules.mk` all stand as v2 stated them.

What the build experiment adds, and what makes this decision load-bearing rather than
housekeeping: **the RX engine reaches RAM by accident.** No `.datacode` rule exists anywhere —
not in the branch, not in the template (BUILD_FACTS.md §4). The section lands in RAM only because it matches
the stock script's `*(.data*)` wildcard (`py32f003x4.ld:118`; same spelling at
`py32f030x6.ld:116` and `py32f002bx5.ld:116`, so every stock script hides the problem equally
well). A script that spells the rule `*(.data) *(.data.*)` — with a dot — does **not** match
`.datacode`; the section becomes an orphan, GNU ld places an `"ax"` orphan after `.text`, and the
hard-real-time RX path executes XIP with **no error, no warning, and a successful build**
(DEFECTS_VERIFIED.md D-5). That is the argument for owning the linker script: not "fewer megabytes" but "the one
property every timing figure in this plan depends on is currently invisible to the build".

**Decision (unchanged in direction, sharpened): our own `py32_common.ld` + per-part scripts, with
an explicitly named RAM-code output section (`.timecrit`) and an `ASSERT` that its VMA lies in
the RAM region.** Scripts to ship, geometry read from the pinned template (`py32f0-template`
@289ffc8, `Libraries/LDScripts/<part>.ld:32-33`, and `py32f003x4.ld:34-35`) rather than from a
datasheet:

| part | RAM | FLASH | role after Р5 |
|---|---|---|---|
| PY32F030x6 | 4K | 32K | **reference part** (default `MCU`) |
| PY32F030x8 | 8K | 64K | headroom variant |
| PY32F003x6 / x8 | 4K / 8K | 32K / 64K | cost-down twins, out-of-DS at 48 MHz (Р5) |
| PY32F003x4 | **2K** | **16K** | tightest supported part; build-matrix member, not the reference |
| PY32F002Bx5 | 3K | 24K | target #2 |
| PY32F002Ax5 | 3K | 20K | not planned (§3.1); script shipped only if T1 needs it |

---

**Р4. Code placement.**
**Changed.** v2 wrote one rule over "the engine" and gave a flash-column reason for it. Both
halves need work.

*The factual correction first.* "The engine" is two things with opposite placement today
(BUILD_FACTS.md §3, from `objdump -h` on the real object, not read from the source):

| path | section | size | resident in |
|---|---|---|---|
| RX sampling path (`EXTI2_3_IRQHandler` … `done_usb_message`) | `.datacode` | 252 B | **RAM** |
| dispatch tail + **the entire TX path** (`usb_send_empty` … `no_really_done_sending_data`) | `.text` | 512 B | **flash** |

So v2's Р4 ("RX ISR, TX engine, dispatch trampolines … → RAM", PLAN:465-467) described a state
that does not exist, and Appendix A costed both halves with one column. Every decision phrased
over "the engine" splits here.

*The reason correction.* v2's stated reason was "flash reads are 1-WS and the prefetch state
makes `ldrb` inside a TX cell 2-or-3 cycles (RM002B p38; TRM §2.2.1)" (PLAN:468-469). That reason
is RM-derived guesswork and it is not what the silicon does. The measured reason is a swap
(CHIP_FACTS_XIAMATSU.md §1): from RAM-resident code, RAM data costs **2** (4 from flash), `push/pop` **2+1** (4+1
from flash), ports run «на полной скорости», and ordinary instructions are 1 cycle either way —
running from RAM «не замедляется, как ожидалось» (xm_030.md:481). RAM placement is therefore the
*cheap* configuration on this part, not a necessary evil, and the architectural reason is visible
in the map: GPIO sits at `IOPORT_BASE` 0x5000_0000, on the M0+ IOPORT bus rather than APB
(BUILD_FACTS.md §7).

For keeping TX in flash (the branch's choice, `arm.S:211-212` "conserve RAM"): 512 B saved, which
on the 2 K F003x4 is a quarter of RAM. Against: every packet byte the TX cell reads is a RAM
access from flash-resident code = **4** cycles (`load_next_byte`, BUILD_FACTS.md §5); the flash literal at
`.text+0xda` sits inside the timed `pre_and_tok_send_one_bit` cell (BUILD_FACTS.md §5), where it is 2 today
only because the pool happens to be in flash *with* the code; and at 48 MHz `LATENCY=1` adds
fetch wait states that the cost table — taken at Latency 0, ≤ 24 MHz (xm_030.md:466) — does not
contain at all (OQ15). A TX cell whose cost is unmeasured is not a cell one can pad.

Arithmetic, so the RAM objection is answered with a number rather than a preference: the
branch's own `-Os` demo_gamepad uses 1168 B of RAM with TX in flash (ledger §2.1, build log);
moving the 512 B TX path into RAM gives ≈1680 B, which fits the 2 K F003x4 with ≈370 B of margin
and is comfortable on the 4 K reference part. One rule, no per-part placement matrix.

**Decision: one placement rule for both halves — RX and TX both in `.timecrit` (RAM) on every
part**, together with `always0`, the pad staircase (§7.4), the dispatch trampolines, the literal
pools (`.ltorg` per block, T2 step 2) and every `usb_send_data` source (descriptors →
`.rodata.usbdesc` → RAM, T4). The non-timed dispatch tail may stay in flash as the R3 fallback,
and when it does it is costed with the flash column (RAM data 4 per access) — which v2's "not
cell-critical, so free" did not say. **Gates, both mechanical and both at build time:** (a) the
linker `ASSERT` of Р3 — `.timecrit` VMA inside the RAM region; (b) the walker resolves every
`ldr Rd,[pc,#imm]` reachable from a timed path and fails the build if the target is outside SRAM
(Appendix B; the rule exists because from RAM code a flash pool costs 4, CHIP_FACTS_XIAMATSU.md §1, and today it
holds "by construction, not by enforcement", BUILD_FACTS.md §5). An `nm` check on descriptor symbols is not
sufficient on its own; the pool check is the one that catches the silent case.

Corroboration that the two-column model is real and not an artefact of one author's rig: all five
`#if PY32F002Bx5` sites in the engine (arm.S:402, 415, 444, 490, 530) are **pure cycle padding in
the TX path** — F002B carries two extra `nop`, F003/F030 one extra `b .+2`, exactly the measured
4-byte `.text` difference (BUILD_FACTS.md §8, BUILD_FACTS.md §2). The branch author hit empirically the same per-die cost
difference the Xiamatsu table describes.

---

**Р5. Clocking, part order and the servo — the target flips.**
**Changed.** v2 made F002B the primary target and F030 the alternative; §3.1 reverses that on
measured evidence. The case for F002B is stated first and at full strength, because it was not
silly.

*For keeping F002B primary (v2's case):* 3 K RAM against the 2 K of the cheapest F003
(`py32f003x4.ld:34-35`); 24 K flash against 16 K; **no PLL to bring up** — HSI only, so no lock
wait, no `PLLSRC`/`PLL_IN` constraint, no CSS/NMI handler, one fewer failure mode in the loader;
128 B of OTP for a per-board calibration constant (CHIP_FACTS_XIAMATSU.md §3); and the branch's own build is already
pinned to it (`Makefile.py32`, `MCU_TYPE = PY32F002Bx5`), so staying put means shipping the only
arm the branch has ever built (DEFECTS_VERIFIED.md D-3).

*Against, and this is what settles it:* the F002B factory 48 MHz word
(`[0x1FFF0104] = 0x0000B3A2`, nominally 49.60 MHz by the author's formula) **measures 43.12 MHz
on live silicon — −10.2 %** (xm_002b.md:172-175, :209-210 «калибровочная константа установлена
неверно»). USB LS allows ±1.5 % (USB 2.0 §7.1.11). The part cannot enumerate from its factory
constant at all, so a trim loop there is a precondition, not a refinement. Worse, v2's servo as
specified could not have recovered it: v2 saturates the actuator at **±64 LSB from the factory
value** at ≈0.1 %/LSB (PLAN:497-499), i.e. ±6.4 %, against a −10.2 % starting error — the loop
saturates and never locks. That is a concrete defect in v2's Р5, not a tuning matter. Beyond it:
the trim field is non-linear (`TRIM_H` scales the `TRIM_L` range in coarse steps of +33/+41/+50 %,
xm_002b.md:232-257); there is **no PLL and HSE is an input only** (CHIP_FACTS_XIAMATSU.md §2), so no crystal escape
hatch exists; there is no ROM ISP loader (CHIP_FACTS_XIAMATSU.md §3, §3.5), so recovery is SWD-only; and the whole
two-column cost model was measured on the *other* die (CHIP_FACTS_XIAMATSU.md §1 ОТКРЫТО → OQ-B).

*For F030/F003 primary:* the factory HSI 24 MHz word measures 23.99 MHz, −0.04 %
(xm_030.md:15); ×PLL2 = **47.98 MHz, −0.04 %**, inside the USB tolerance and inside the ≈0.44 %
sampling margin of §2.4.5 **with no calibration step of any kind before enumeration**. It is the
only 48 MHz path that appears in a datasheet (DS030 p2, p5). A crystal path exists as a build
option (HSE 24 MHz × PLL2). UART ISP at BOOT0 = 1 gives a recovery route without a probe
(xm_030.md:374). The cost table was measured on this die.

*Against the flip, argued honestly:* the new primary family's smallest member has **less RAM than
the part being demoted** — F003x4 2 K/16 K vs F002B 3 K/24 K. The trade is made knowingly, it is
bounded by Р4's arithmetic (≈1680 B of 2048 with both engine halves in RAM), and the reference
part is F030x6 (4 K/32 K), not F003x4. Second objection: the arm that becomes primary is the one
the branch's build system has never selected (DEFECTS_VERIFIED.md D-3) — answered by experiment rather than by
argument, since it assembles clean (BUILD_FACTS.md §2, rc = 0 with `-DPY32F003x4=1`) and the alignment guard
it carries, `.ifeq (pre_and_tok_send_inner_loop - usb_send_data) & 2 / .error` (arm.S:415,
`#else` arm), **passes**: a correctness constraint its author could not test, which holds, and
which will now fail the build rather than the timing if anyone shifts a halfword ahead of that
label (BUILD_FACTS.md §8). Third objection, portability: **dismissed on evidence, not debated** — every base
address and register offset in the timed code is identical across the two families (BUILD_FACTS.md §7), so
the flip changes not one address in the engine.

**Decision.**
1. **Primary target: PY32F030x6**, default `MCU` in `Makefile.py32`. Clock: HSI 24 MHz factory
   word (`0x1FFF0F10`) × PLL2 → 47.98 MHz, `FLASH_ACR.LATENCY = 1`, **no calibration before the
   D− pull-up**. `USB_TRIM_ACTUATE` exists, but the enumeration path does not depend on it.
2. **HSE 24 MHz × PLL2 stays a build option** for a drift-free build; it requires `NMI_Handler`
   for CSS, since a silent fallback to the untrimmed HSI drops the link (PA §5.4).
3. **The servo is not retired — it is demoted from precondition to drift loop.** DS030
   Table 5-15 gives ±2 % over 0–85 °C and −4/+2 % over −40…85 °C, both far outside the ≈0.44 %
   sampling margin, so an HSI build without a keepalive loop is a room-temperature device
   (§2.4.5 stands as written). What the flip removes on F030 is the *lock-time* coupling: the
   device enumerates before the loop has done anything, so R15/OQ9 (the Windows
   reset→first-SETUP window) stop being blocking there. They remain blocking on F002B.
4. **Target #2: PY32F002B, with a precondition** — the firmware trims the HSI against the LSI
   (measured 32.71 kHz vs 32.768 kHz nominal, −0.18 %, xm_002b.md:204-206) to inside the USB
   tolerance *before* asserting the pull-up (T12), and only then hands over to the keepalive
   loop. **The factory word is never used as-is on this part.** Actuator saturation is specified
   relative to the *calibrated* value; v2's ±64 LSB from the factory word is removed.
5. **F003/F002A at 48 MHz are outside their datasheets** (DS003 32 MHz, DS002A 24 MHz) and reach
   it by measurement only (xm_030.md:336). The build accepts them at 48 MHz only under an
   explicit `PY32_OUT_OF_SPEC=1` (R25). F030 is the only production part.
6. The two-rate actuator law (fast proportional step for the first `USB_TRIM_LOCK_N` in-window
   keepalives, then the decimated integrator) is unchanged in shape; `USB_TRIM_SIGN` and the LSB
   weight still come from bench6 (OQ3) — now for the F030 24 MHz trim rather than the F002B
   48 MHz one, which is a different measurement of a different field.
7. The keepalive path's cycle budget (ack `EXTI_PR` first, complete within 96 cycles) is
   unchanged.

---

**Р6. Bootloader layout.**
Mechanism unchanged — loader in flash pages 0-31 (4 KB) at 0x08000000, app at 0x08001000 with
VTOR, boot words in the top 16 B of SRAM `PROVIDE`d by `py32_common.ld` and never touched by
startup, flag qualified by `RCC_CSR.SFTRSTF`. What moves with Р5 is the arithmetic, in the
direction that matters for R9: on the tightest supported part the 4 KB loader is **25 % of the
16 KB flash** (`py32f003x4.ld:35`), against 17 % of F002B's 24 K and 12.5 % of the reference
F030x6's 32 K (`py32f030x6.ld:33`). The 16 B of reserved SRAM is 0.8 % of 2 K. The F002B-only
"Load Flash" zone stays a follow-up (OQ6), now on the *second* target rather than the first,
which lowers its priority without changing its content.

---

**Р7. Interrupt policy.**
Conclusion unchanged (USB EXTI at priority 0, everything else ≥ 1, SysTick 3, PRIMASK sections
≤ 40 cycles, vector table in flash, RAM vector table as a documented 192 B option).
**Justification replaced.** v2 justified leaving the table in flash with "1-WS vector fetch =
+1…2 constant cycles" and with PA D-11's "deterministic because LATENCY is flat" (PLAN:523-526).
Neither number is measured: the cost table has no vector-fetch row, it was taken at Latency 0
below 24 MHz (xm_030.md:466), and the flash column shows that flash-side accesses are not the
1-cycle events that "+1…2" assumes. The honest form of the argument does not need the number:
whatever the vector fetch costs, it is the same on every entry, and §2.2's compensation absorbs a
constant; what would break the engine is *spread*, and spread is measured directly by bench3
rather than derived. The 192 B RAM table is also 9 % of the 2 K part's RAM (Р3 table) — the real
reason to keep it an option rather than a default.

---

**Р8. D± drive strength — lowest OSPEEDR + 33 Ω series.**
Unchanged, no edit; neither correction touches it (the argument is USB 2.0 §7.1.2.1 Table 7-9
plus Grainuum's measured ringing, PA S-9, not a cost model and not a part choice). One fact that
makes it cheaper to implement than v2 assumed: `OSPEEDR` is at offset 0x08 in both families'
headers (BUILD_FACTS.md §7), so `USB_PORT_OSPEED` is a single constant with no per-part arm.

---

**Р9. Timebase rule — SysTick free-running, always.**
Mechanism unchanged (`LOAD = 0xFFFFFF`, CLKSOURCE = HCLK, the shim is the only writer,
`dfu_port_cycles()` a 32-bit HCLK count, the `SysTick->LOAD` greps as acceptance). One constant
is corrected by the flip: at 47.98 MHz the expected keepalive delta is **47980**, not 48000
(xm_030.md:15 → §3.1). It is well inside v2's ±4000 sanity window, so no code changes; it matters
because the servo's error term is computed against that expectation, and a plan that writes 48000
as if it were exact invites someone to tighten the window around the wrong centre.
`DFU_CYCLES_PER_MS = 48000` stays (0.04 % on a millisecond delay is irrelevant, and the DFU
timebase is not a servo reference).

---

**Р10. Licence and provenance rule.**
Unchanged, no edit.

## 7. Contracts

### 7.1 Per-site macro contract of the Thumb engine (mirrors doc/wg015/PLAN.md Р2 table)

Defined in `rv003usb/py32/usb_port_py32_asm.h` (T2 owns) from `usb_config.h` pins + `py32_min.h`;
the header `#error`s if `USB_PORT`/`USB_PIN_DP`/`USB_PIN_DM` are undefined:

| Macro | Semantics | PY32 value |
|---|---|---|
| `USB_GPIO_BASE` | port base for `USB_PORT` | `0x50000000 + 0x400*idx(USB_PORT)` (A0,B1,C2,F5) |
| `USB_GPIO_IDR`, `USB_GPIO_BSRR`, `USB_GPIO_MODER` | sample / absolute pair write / direction | `0x10`, `0x18`, `0x00` |
| `USB_SAMPLE(rd, rbase)` | one 1-cycle load of D± | `ldr rd,[rbase,#USB_GPIO_IDR]` |
| `USB_TX_PRESET_WORD` | absolute K before acquire | `(1<<DP)\|(1<<(DM+16))` |
| `USB_TX_ACQUIRE_MASK/VALUE`, `USB_TX_RELEASE` | MODER bits 01 for DP/DM; back to 00 | `3<<(2*DP)\|3<<(2*DM)`, `1<<(2*DP)\|1<<(2*DM)` |
| `USB_TX_FLIP_WORD` | NRZI flip | `(1<<DP)\|(1<<DM)\|(1<<(DP+16))\|(1<<(DM+16))` |
| `USB_TX_SE0_WORD`, `USB_TX_PARK_J_WORD` | both low; J | `(1<<(DP+16))\|(1<<(DM+16))`; `1<<DM` (LS: J = D− high) |
| `USB_ISR_ACK_ADDR/VALUE` | clear pending | `EXTI_BASE+0x0C`, `1<<DM` |
| `USB_DM_IRQ_HANDLER` | vector symbol | `EXTI0_1/2_3/4_15_IRQHandler` by `USB_PIN_DM` (h:26-35 of 0ad3c42) |
| `USB_TICK_ADDR` | free-running HCLK counter | SysTick `VAL` `0xE000E018` (24-bit **down**, `LOAD` always 0xFFFFFF — Р9; delta = `(last-now)&0xFFFFFF`) |
| `USB_TRIM_ACTUATE` | servo plug-in | writes `RCC_ICSCR` (empty for HSE builds); two-rate law per Р5 |
| `USB_DBG_MARK_SET/CLR` | zero-intrusion marker (§7.3) | `str r10-derived,[dbgport,#BSRR/#BRR]` |
| `USB_RX_ENTRY_DELAY`, `USB_RX_SYNC_DELAY`, `USB_TX_*_PAD` | pads (cycles, never µs — PA S-3) | `usb_port_py32_tune.h` (defaults 96, 71→78, and the T2 TX pads incl. `USB_TX_SE0_PAD` → 64-cycle EOP) |
| `USB_RX_PREAMBLE_LIMIT` | bounded preamble spin (F9) | `usb_port_py32_tune.h`, default ≈512 cycles (16 bit-times) |
| `USB_TRIM_LOCK_N`, `USB_TRIM_FAST_SHIFT`, `USB_TRIM_SLOW_SHIFT`, `USB_TRIM_SAT`, `USB_TRIM_SIGN` | servo law (Р5) | `usb_port_py32_tune.h`: 8, 6, 9, 64, +1 (bench6/T10 set the final values) |
| `rv003usb_wait_<N>` (N = 5…40) | exact-N-cycle pad entry points (§7.4) | staircase in `.timecrit`, emitted by the engine, referenced by the pad macros |

### 7.2 Engine ↔ C ABI (unchanged from RISC-V; the C layer must not know the ISA)

Exports: `usb_send_data(const void*, uint32_t len, uint32_t poly_function, uint32_t token)`,
`usb_send_empty(token)`, `always0`, the vector handler. Calls (all 5-arg, `ist` on the stack):
`usb_pid_handle_ack/in/out/setup/data` (h:91-95). Reads `rv003usb_internal_data.my_address` at
`MY_ADDRESS_OFFSET_BYTES` (1 or 4 with OPTIMIZE_FLASH, h:125/140 — `ldrb` at either is fine on
little-endian). Uses `ENDPOINTS`, `USB_BUFFER_SIZE`, `USB_DMASK` (h:120-122). Only CRC-valid
packets reach the C handlers (arm.S:262-314) — the property T11's loopback counter relies on.

### 7.3 Zero-intrusion debug marker (port of Р10)

BSRR write of 0 is architecturally a no-op ("Write any bit to 0 in GPIOx_BSRR does not have
any effect", RM002B p79) — exact analog of WG015's MASKLB[0]. Marker = `mov r4, r10; str r4,
[r5, #BSRR]` right after the sample (r5 = port base, r4 scratch, r10 = mask loaded from a RAM
word at ISR entry, 0 in production) and `… [r5, #BRR]` (0x28) at the slot tail = a pulse per
slot, 4 cycles taken from existing padding, instruction stream identical in TUNE and
production. `tools/wg015_vcd` needs a `--marker-edge rise` option (T6) because it assumes one
toggle per sample. (Pico-PIO-USB's debug side-set is the same idea, PA S-5 — nothing to add.)

### 7.4 Pad staircase and the `lr` rule (PA S-1, Grainuum MIT)

A run of `nop`s with one label per entry and `mov pc, lr` at the bottom
(`rv003usb_wait_40: nop` … `rv003usb_wait_6: nop`, `rv003usb_wait_5: mov pc, lr`) gives, via
`bl rv003usb_wait_N`, exactly N cycles for any N ≥ 5 (`BL` 3 + `NOP`·(N−5) + `MOV PC` 2, TRM
Table 3-1) with no scratch register and a 4-byte call site; the branch's `DELAY_CYCLES(c)`
reaches only multiples of 3 and burns `SCRATCH`, and inline `nop`/`b .+2` padding at ≈8 TX
sites × ≈12 cycles × 2 B costs ≈200 B on a 3 KB part versus ≈70 B once + 4 B per site. Rules:
(1) the staircase lives in `.timecrit` (never fetched from 1-WS flash; `bl` range ±16 MB is
irrelevant); (2) it may be called only where `lr` is dead: every TX pad (`usb_send_data`
pushes `lr`, arm.S:357, and never reads r14 after) and the two RX entry pads
(`USB_RX_ENTRY_DELAY`, `USB_RX_SYNC_DELAY`, before `mov POLY_RX, r5` at arm.S:128-140); in-slot RX
pads (`DELAY(6/12/24)`) stay inline because r14 = POLY_RX there; (3) `tools/py32_cyc.py` models
`bl`=3, `nop`=1, `mov pc,lr`=2 and walks *through* the staircase so every named path still
reports its total; bench2 (T6) measures `bl rv003usb_wait_N` for N = 5…40 from SRAM against
SysTick to confirm the 3/1/2 costs on PY32 (Grainuum's "taken branch = 3" note, OQ4); (4) copied
Grainuum code (`grainuum-phy-ll.s` L433-461) carries its copyright line and the MIT notice in the
engine's header comment — `grep -q 'xobs/grainuum' rv003usb/rv003usb-arm.S` (T2 acceptance,
[MIT-attrib]).

## 8. DFU: the chip-port contract and the PY32 sketch

Contract extracted from `bootloader_dfu/dfu.c` (every symbol a `dfu_chip.h` must provide):

| Symbol | Used at | PY32 implementation (`bootloader_dfu/dfu_py32.h`) |
|---|---|---|
| `DFU_APP_BASE` | dfu.c:78,101,104,142,168 | `0x08001000` (Р6); never lowered — the address guard dfu.c:143-149 is the only thing between a bad image and an unrecoverable 002B (§3.5) |
| `DFU_FLASH_END` | :102,144,170-171 | `0x08000000 + FLASH_SIZE` (24 K / 32 K / 64 K from the Makefile MCU) |
| `DFU_PAGE_SIZE` | :124 (erase when block starts a page) | `128` |
| `DFU_XFER_SIZE` | :66,142-144,168-171,227; transport buffer dfu_rv003usb.h:22 | **`128`** — the RM allows whole-page programming only (RM002B p24 "programmed the entire page"), so one DFU block = one page (erase+program every block; `DFU_POLL_ERASE_MS` always applies). Also `wTransferSize` in `usb_config.h` = 128 |
| `DFU_CYCLES_PER_MS` | :223 (3 ms quiet), :245 (25 ms manifest) | **`48000`** — `dfu_port_cycles()` returns HCLK cycles (Р9; v1 said `1`/ms-tick) |
| `DFU_POLL_ERASE_MS`, `DFU_POLL_PROG_MS` | :124-125 | **`12`, `12`** (v1: 8/8). Arithmetic, worst case, 002B: t=0 GETSTATUS SETUP handled in the ISR, `arm_cycles` captured (dfu.c:127); the host's `bwPollTimeout` countdown starts when its control transfer completes, ≥ t≈0; the main loop's quiet wait ends at t = 3.0 ms (cycle-exact under Р9; with v1's 1 ms tick it was 2.0–3.0 ms, PA S-3); IRQs masked (dfu.c:231-233); page erase ≤ 5.0 ms + program ≤ 1.5 ms (DS002B p39 Table 5-15; F030 4.5 + 1.5, DS030 p64 Table 5-18) → IRQs back at t ≤ 9.5 ms (F030 9.0). A GETSTATUS arriving inside the masked window is lost on a bus with no hardware to answer it (LS: 3 retries within the frame, then the host stack errors out). Requirement: `bwPollTimeout` ≥ 9.5 + 1.0 (host timer resolution / device-vs-host clock phase) = 10.5 → **12** (1.5 ms margin; 8 would sit 1.5 ms *inside* the window). Throughput: 192 blocks × (12 + ≈3 ms transfer) ≈ 2.9 s per 24 KB. `DFU_POLL_PROG_MS` is never selected on PY32 (every block starts a page) but is set equal so the choice at dfu.c:124-125 cannot matter |
| `DFU_FLAG_APP`, `DFU_FLAG_STAY` | :204,207 | `0x0AFF10AD`, `0xB00710AD` (same values as WG015 for tooling parity) |
| `dfu_port_cycles()` | :127,161,223,245 | 32-bit HCLK cycle count from the free-running SysTick (Р9): `do { w = dfu_wraps; v = SysTick->VAL; } while (w != dfu_wraps); if (SCB->ICSR & PENDSTSET) { w++; v = SysTick->VAL; } return (w << 24) \| (0xFFFFFF − v);` — the pending check covers a wrap that occurred while the USB ISR (priority 0) held off the SysTick handler (called from dfu.c:127 inside the ISR); `SysTick_Handler` (priority 3, ≈12 cycles) does `dfu_wraps++`. (Raw 24-bit VAL under the core's 32-bit subtraction would end waits early on wrap — PA L-21) |
| `dfu_port_irq_disable/enable()` | :231,233 | `cpsid i` / `cpsie i` |
| `dfu_port_flag_read_and_clear()` | :202 | `r = RCC->CSR; RCC->CSR \|= RMVF; f = py32_boot_flag; py32_boot_flag = 0; if (!(r & SFTRSTF)) f = 0; if (r & PWRRSTF) { py32_boot_count = 0; py32_dbltap = 0; } if (f) { py32_boot_count = 0; return f; }` then **boot-failure counter (PA S-7, joyboot `bootloader.c` L64-90, MIT)** under `#if DFU_ENABLE_BOOTCOUNT`: `if (++py32_boot_count > 3) { py32_boot_count = 0; return DFU_FLAG_STAY; }` — covers the case the CRC gate cannot: a CRC-valid app that crashes (HardFault, IWDG) before it can request DFU; the app clears the word when alive (`py32_app_alive()` from `usb_port_hw_setup()`, T3) — then the optional double-tap (500 ms, samd11 idiom, `dfu_015.h:44-76` pattern) on `py32_dbltap`, POR-qualified. Words are the ld-fixed top-of-RAM block (Р6), never `.bss` |
| `dfu_port_reboot_to_app()` | :248 | `py32_boot_flag = DFU_FLAG_APP; SCB->AIRCR = 0x05FA0004; while(1);` (SYSRESETREQ, RM002B p57 §7.1.5) |
| `dfu_port_jump_app()` | :205,208 | validate `sp ∈ SRAM`, `pc ∈ [APP_BASE,FLASH_END)` with Thumb bit (PA S-8); `SCB->VTOR = DFU_APP_BASE; __set_MSP(app[0]); ((void(*)(void))app[1])();` (VTOR present, §3.2; runs before `usb_setup`, near-reset state) |
| `dfu_port_flash_timebase_init()` | :212 | `PY32_systick_freerun()` + `TICKINT` (Р9); load the flash timing registers `TS0…PRETPE` from the factory set for the running HSI mode (§3.3) — mandatory before any program/erase |
| `dfu_port_flash_write_block(addr, src)` | :232 (IRQs masked around it) | XIP is legal (bus stalls, RM002B p23; the V003 port `dfu_v003.h:84-112` is the model, not the WG015 RAM routine): `KEYR=KEY1,KEY2` if `CR.LOCK`; `CR\|=PER; *(vu32*)addr=0xFF; wait !BSY; CR&=~PER; CR\|=PG; for i<31: dst[i]=src[i]; CR\|=PGSTRT; dst[31]=src[31]; wait !BSY; CR&=~PG; CR\|=LOCK` (RM002B p24-25; HAL `FLASH_Program_Page`/`FLASH_PageErase`). joyboot's cooperative `idle_func()` wait is *not* adopted (PA D-3): every C handler the ISR needs is in flash, which stalls during the op anyway |
| `DFU_ENABLE_UPLOAD`, `DFU_ENABLE_APPCRC`, `DFU_ENABLE_BOOTCOUNT` | :30-35 (`#ifndef` defaults; chip port overrides) | UPLOAD/APPCRC both 1 on F030; measure on 002B (4 KB budget); BOOTCOUNT 1 on F030, 0 on 002B until the budget is known (R9), ≈24 B |
| option bytes | — | never written (`FLASH_OPTR`, RDP — PA A-11); `! grep -rn 'OPTR\|RDP' bootloader_dfu/dfu_py32.h bootloader_dfu/py32/` |

Transport (`dfu_rv003usb.h`, unchanged) needs `usb_config.h` with `RV003USB_OTHER_CONTROL 1`,
`RV003USB_SUPPORT_CONTROL_OUT 1`, `ENDPOINTS 1`, descriptors in `.rodata.usbdesc` (RAM), and
`RV003USB_OPTIMIZE_FLASH 0` until F8 is closed. The reply pointers handed to `e->opaque`
(dfu_rv003usb.h:47-49) point at `dfu_status`/`dfu_upload_buf` (RAM, dfu.c:58-67) and must stay
there (PA S-4: `nm` gate in T5). Image convention: length word at app+0x10 = M0+ vector slot 4
(reserved, `.word 0` in every startup) → `wg015mkdfu.py`'s "0 or 0xFFFFFFFF" check passes;
CRC32 covers `[base, len-4)`; loader gates on it (dfu.c:98-106). Host side stays stock
`dfu-util` (PA D-4); interop at 8-byte EP0 with `wTransferSize` 128 / 12 ms is OQ13.

## 9. Work breakdown (parallel fleet; file ownership is disjoint; waves are the dependency order)

### 9.0 What changed against v2 §9

Six structural changes; everything else in v2 §9 that is not restated below stands.

1. **Primary part flipped** (CHIP_FACTS_XIAMATSU.md §2, §3.1). `MCU ?= PY32F030x8`. F002B clock work is off the
   critical path on its own track (T12, T13's `acquire` mode, T16). No task in waves 0–3 waits
   on an F002B decision.
2. **The linker script is a first-class task, not a line in T1's content list** (BUILD_FACTS.md §4, DEFECTS_VERIFIED.md D-5).
   The branch's RAM-resident RX engine reaches RAM by accident — it is swallowed by the stock
   script's `*(.data*)` wildcard. A script spelling that rule `*(.data.*)` drops the engine into
   flash with no error and no warning. The remedy is our own script with a named RAM-code
   section, `ASSERT`s, and `--orphan-handling=error`.
3. **"The engine runs from RAM" is retired as a phrase.** BUILD_FACTS.md §3 measured the split:
   `.datacode` 252 B = the whole real-time RX path, RAM-resident; `.text` 512 B = the
   token-dispatch tail **and the entire TX path**, flash-resident. Every task says which side it
   is on.
4. **No task is gated on hardware.** arm-none-eabi-gcc 13.2.1 is installed and the engine
   assembles and links (BUILD_FACTS.md §1, §2). Every acceptance criterion below is a command that runs in
   this container. T10/T16 exist to *record* silicon, and nothing in waves 0–3 depends on them.
5. **v2's T2 was too big for one session.** Split into T2 (RX path, correctness, placement) and
   T2T (TX path, per-part `#if` review, re-pad), serialised on `rv003usb-arm.S`. The walker and
   the trim actuator came out of T2 as T14 and T13 and moved to wave 1, because T2's own
   acceptance needs them.
6. **Every task carries a model marking** (Sonnet / Opus) with its reason. The executing fleet is
   mixed; the marking is a routing instruction, not a compliment.

### 9.1 Conventions

v2's conventions stand, amended:

* Branch = the T0 result. Builds run from the repo root. `ARMCC=arm-none-eabi-` (gcc ≥ 13, BUILD_FACTS.md §1);
  RISC-V via the `ch32fun` submodule.
* One Makefile variable `DEFS` is passed **identically to the `.c` and `.S` rules** (§2.6):
  `-DRV003USB_PY32=1`, exactly one family (`-DPY32F030=1` / `-DPY32F002B=1`), `-D$(MCU)=1`
  (part), `-DPY32_FLASH_KB= -DPY32_SRAM_KB=`.
* Timing constants are **cycles**, from `usb_port_py32_tune.h`, never microseconds (PA S-3).
* "walker" = `tools/py32_cyc.py` (T14; Appendix B is its seed).
* No task edits a file it does not own. A task that needs a change elsewhere appends to the
  `requests` section of `doc/py32/STATE.md` (T8's file — the single shared exception, append-only).
* Every commit touching the Р10 files carries a `Provenance:` trailer; `[MIT-attrib]` /
  `[GPL-ideas-only]` tags below mark the tasks concerned.
* Acceptance criteria are mechanical and **hardware-free**: a command that must exit 0, a size
  limit, a symbol at an address, a grep that must or must not match.

**Vendor-versus-submodule — decided here, not deferred.** `py32f0-template` is removed and the
handful of files we need are **written**, not copied: linker scripts, startup, and a minimal
device header, exactly as the WG015 port writes `K1921VG015_min.h`. Reasons, both from BUILD_FACTS.md §6:
the submodule is empty on the branch so the branch cannot link as published, and the upstream
files carry their own licence into a repo that would rather not inherit one. Consequence for the
fleet: T0 removes the submodule; T1 writes the replacements from RM/DS page cites; nothing in
the tree ever refers to `../py32f0-template/…`. Upstream at 289ffc8 stays a *reference* for
memory geometry and register offsets (that is where BUILD_FACTS.md §6's RAM/flash table came from), not a
build input.

### 9.2 Ownership matrix — strictly disjoint at file granularity

`Serialised after` means the two tasks share a file and must never run concurrently; every other
pair is free to run in parallel. Nothing else in this table shares a path.

| Task | Model | Wave | Owns (exact paths) | Serialised after |
|---|---|---|---|---|
| T0 | Sonnet | 0 | the merge — every file it touches, alone | — |
| T1 | **Opus** | 1 | `rv003usb/py32/{py32_min.h, ch32fun.h, startup_py32.S, py32_common.ld, py32f003x6.ld, py32f030x6.ld, py32f030x8.ld, py32f002bx5.ld, Makefile.py32, py32_stdio_stub.c, selftest_main.c, README.md}` | — |
| T3 | Sonnet | 1 | `rv003usb/rv003usb.c`, `rv003usb/rv003usb.h`, `rv003usb/usb_port_ch32.h`, `rv003usb/wg015/usb_port_wg015.h`, `rv003usb/py32/usb_port_py32.h` | — |
| T8 | Sonnet | 1 | `doc/py32/{chip_info.md, ledger_arm.md, STATE.md, TODO.md}` | — |
| T13 | **Opus** | 1 | `rv003usb/py32/usb_port_py32_trim.h`, `rv003usb/py32/selftest_trim.S` | — |
| T14 | **Opus** | 1 | `tools/py32_cyc.py`, `tools/py32_cyc_costs.json`, `tools/py32_cyc_selftest/*` | — |
| T2 | **Opus** | 2 | `rv003usb/rv003usb-arm.S`, `rv003usb/py32/usb_port_py32_asm.h`, `rv003usb/py32/usb_port_py32_tune.h` | — |
| T4 | Sonnet | 2 | `demo_gamepad/{usb_config.h, funconfig.h, demo_gamepad.c, README.md}`, `demo_hidapi/{usb_config.h, funconfig.h, demo_hidapi.c, README.md}` | — |
| T6 | Sonnet | 2 | `py32_bench/{Makefile, main.c, bench_common.c, bench_common.h, bench_kernels.S, bench1_ioport.c, bench2_branch.c, bench3_irq.c, bench4_flash.c, bench5_slot.c, bench6_trim.c}`, `tools/wg015_vcd/*` | — |
| T12 | **Opus** | 2 | `rv003usb/py32/py32_hsical.c`, `rv003usb/py32/py32_hsical.h`, `py32_bench/bench8_hsical.c` | — |
| T2T | **Opus** | 3 | `rv003usb/rv003usb-arm.S`, `rv003usb/py32/usb_port_py32_asm.h`, `rv003usb/py32/usb_port_py32_tune.h` | **T2** |
| T5 | **Opus** | 3 | `bootloader_dfu/dfu_py32.h`, `bootloader_dfu/py32/*`, `bootloader_dfu/README.md`, `tools/wg015mkdfu.py` | — |
| T7 | Sonnet | 3 | `Makefile` (top), `.github/workflows/build.yml`, `.gitignore`, `README.md` (top) | — |
| T9 | **Opus** | 3 | `bootloader_py32/*`, `bootloader_wg015/wg015hostcli/*` | — |
| T11 | Sonnet | 3 | `py32_bench/{bench7_loopback.c, loopback_vectors.h, gen_loopback_vectors.py}` | — |
| T15 | Sonnet | 3 | `rv003usb/py32/engine_pid_handlers.S` | — |
| T10 | **Opus** | 4 | `doc/py32/calibration.md`, `rv003usb/py32/py32_cal_f030.mk` | — |
| T16 | **Opus** | 4 | `doc/py32/calibration_f002b.md`, `rv003usb/py32/py32_cal_f002b.mk` | — |

**Verification — every file the plan touches, listed once, with its single owner.** Read this as
the proof obligation, not as decoration: if a path appears twice the fleet is not parallel-safe.

| Path | Owner |
|---|---|
| `rv003usb/rv003usb-arm.S` | T2, then T2T (serialised — never concurrent) |
| `rv003usb/py32/usb_port_py32_asm.h`, `usb_port_py32_tune.h` | T2, then T2T (same serialisation) |
| `rv003usb/rv003usb.c`, `rv003usb.h`, `usb_port_ch32.h`, `wg015/usb_port_wg015.h`, `py32/usb_port_py32.h` | T3 |
| `rv003usb/py32/py32_min.h`, `ch32fun.h`, `startup_py32.S`, `*.ld`, `Makefile.py32`, `py32_stdio_stub.c`, `selftest_main.c`, `README.md` | T1 |
| `rv003usb/py32/usb_port_py32_trim.h`, `selftest_trim.S` | T13 |
| `rv003usb/py32/py32_hsical.{c,h}` | T12 |
| `rv003usb/py32/engine_pid_handlers.S` | T15 |
| `rv003usb/py32/py32_cal_f030.mk` | T10 |
| `rv003usb/py32/py32_cal_f002b.mk` | T16 |
| `demo_gamepad/*`, `demo_hidapi/*` | T4 |
| `py32_bench/{Makefile, main.c, bench_common.*, bench_kernels.S, bench1..bench6}` | T6 |
| `py32_bench/bench7_loopback.c`, `loopback_vectors.h`, `gen_loopback_vectors.py` | T11 |
| `py32_bench/bench8_hsical.c` | T12 |
| `tools/py32_cyc.py`, `py32_cyc_costs.json`, `py32_cyc_selftest/*` | T14 |
| `tools/wg015_vcd/*` | T6 |
| `tools/wg015mkdfu.py` | T5 |
| `bootloader_dfu/*` | T5 |
| `bootloader_py32/*`, `bootloader_wg015/wg015hostcli/*` | T9 |
| `Makefile` (top), `.github/workflows/build.yml`, `.gitignore`, `README.md` (top) | T7 |
| `doc/py32/{chip_info.md, ledger_arm.md, STATE.md, TODO.md}` | T8 |
| `doc/py32/calibration.md` | T10 |
| `doc/py32/calibration_f002b.md` | T16 |
| `doc/py32/PLAN.md`, `doc/py32/rework/*` | **no task** — the rework blocks were spliced into this file before T0; `doc/py32/rework/` is kept unedited as the provenance record |

**Four seams that make the above disjoint** (each replaces a "task X edits task Y's file"
edge that v2 had; the pattern is v2's own, from T6's weak-symbol bench menu):

| Seam | Owner of the mechanism | Who drops in without touching it |
|---|---|---|
| `SOURCES += $(wildcard $(PY32_DIR)/engine_*.S)` in `Makefile.py32` | T1 | T15 (`engine_pid_handlers.S`) |
| `SOURCES += $(wildcard $(PY32_DIR)/py32_*.c)` | T1 | T12 (`py32_hsical.c`); T1's own `py32_stdio_stub.c` also matches. `selftest_main.c` deliberately does not match — it belongs to one target only, and `selftest_trim.S` (T13) does not match `engine_*.S` |
| `-include $(PY32_DIR)/py32_cal_*.mk` (a glob that matches nothing is silent in make) | T1 | T10 (`py32_cal_f030.mk`), T16 (`py32_cal_f002b.mk`) — measured constants arrive as `DEFS +=` overrides, so no hardware task ever edits a header |
| `py32_bench/Makefile` globs `bench[0-9]_*.c`; `main.c`'s menu binds keys `1`…`9` to weak `benchN_run` | T6 | T11 (`bench7_loopback.c`), T12 (`bench8_hsical.c`) |

Consequence for T10/T16: they write **no** header and no source. v2 had them writing "values only"
into `usb_port_py32_tune.h`, which is T2's file — that is the one ownership violation v2 shipped,
and the `.mk` seam removes it.

### 9.3 Dependency edges and waves

Edges, each with the reason it exists. An edge is a real artefact one task consumes from another,
not a courtesy.

| Edge | Why |
|---|---|
| everything ← T0 | the branch does not exist before it |
| T1 ← T0 | — |
| T3 ← T0; T3 ← T1 (soft) | T3's PY32 compile check needs T1's `Makefile.py32` and `py32_min.h`; its V003/WG015 bit-identity gates need nothing. Wave-1 exit item |
| T8 ← T0 | — |
| T13 ← T0; T13 ← T14 (soft) | the actuator's cycle cost is a walker path. Wave-1 exit item |
| T14 ← T0 | its fixtures are self-contained `.S` files it writes and assembles itself |
| T2 ← T1 | linker script with the named RAM-code section, `py32_min.h`, build |
| T2 ← T3 | `USB_DM_IRQ` block moved out of `rv003usb.h` into the PY32 port header |
| T2 ← T13 | `USB_TRIM_ACTUATE` is called from the SE0 branch |
| T2 ← T14 | T2's acceptance *is* a walker run |
| T4 ← T1, T3 | build + seam headers |
| T6 ← T1 | build |
| T12 ← T1 | RCC/LSI/ICSCR offsets from `py32_min.h` |
| T2T ← T2 | same file |
| T5 ← T1, T2, T3 | links the engine, uses the shared boot words and the port header |
| T7 ← T1, T2, T4, T5, T14 | the top `Makefile` builds all of them and runs `check-cycles` |
| T9 ← T2, T5 | the engine, and T5's proof that the transport works |
| T11 ← T2, T6 | the engine to time, T6's menu and glob |
| T15 ← T2, T3 | `usb_port_py32_asm.h` macros; `EP_*_OFFSET` from `rv003usb.h` |
| T10 ← waves 0–3 | it flashes what they built |
| T16 ← T12, T13, T10 | it runs T10's rig on the second part with T12's calibration in the image |

| Wave | Tasks | Exit gate (run once, by whoever lands last) |
|---|---|---|
| 0 | T0 | `git submodule status` names only `ch32fun`; the four RISC-V/WG015 builds green |
| 1 | T1, T3, T8, T13, T14 | T3's PY32 compile item; T13's actuator costed by T14's walker; `tools/py32_cyc.py --selftest` exits 0 |
| 2 | T2, T4, T6, T12 | `make check-cycles` green on both demos for `MCU=PY32F030x8` and `PY32F002Bx5` |
| 3 | T2T, T5, T7, T9, T11, T15 | `make all` (= `build build_py32 check-cycles`) green; CI yaml parses |
| 4 | T10 (F030, primary), T16 (F002B track) | §10A G1–G12; nothing in waves 0–3 waits on them |

T10 and T16 are **parallel and independent** — different boards, different `.mk` files, different
docs. The F002B track (T12 → T13 `acquire` mode → T16) never blocks the primary path.

### 9.4 Tasks

#### Wave 0

**T0 — Starting state (ONE agent, alone, before anyone else) — Sonnet**
*Sonnet: the conflict set is enumerated in advance and every resolution is named; nothing is
judged, only applied.*

Owns: everything the merge touches — exclusive because nobody else runs yet. This is also why
T0 may edit `.gitignore`, the top `Makefile` and `demo_gamepad/*`, which T7 and T4 own later.

Entry: none.

Does — dry-run verified at 1db45fd; the later commits touch only `doc/py32/`:
1. `git checkout -b py32-port claude/wg015-bitbang-usb-port-bxuu7w && git merge --no-edit 80b1893`
   (clean: `bootloader/usb_config.h`, 2 lines).
2. `git fetch origin py32 && git cherry-pick -x 0ad3c42` → conflicts: `.gitignore`, `.gitmodules`,
   `Makefile`, `demo_gamepad/demo_gamepad.c`, `demo_gamepad/usb_config.h`, `rv003usb/rv003usb.c`.
3. Resolve: `.gitignore` = HEAD + `*.o`, `*.d`, `Build/`; `.gitmodules` = HEAD (ch32fun only) and
   `git rm --cached py32f0-template && rm -rf py32f0-template` (§9.1: vendored, not carried —
   DEFECTS_VERIFIED.md D-4); `Makefile` = HEAD (T7 adds the PY32 hook); `demo_gamepad.c` = HEAD (`#include
   "ch32fun.h"`, no BSP calls — clocks belong to startup); `demo_gamepad/usb_config.h` = HEAD's
   flag block, no pin ladder (T4 adds it); `rv003usb.c` = HEAD entirely (drop the LL includes and
   the `#if __riscv` forks); `rv003usb.h` auto-merges and keeps the `USB_DM_IRQ` block (T3 moves
   it). `git rm -r .vscode Makefile.py32` — the branch's `Makefile.py32` is deleted, not amended:
   it pins `MCU_TYPE = PY32F002Bx5` (DEFECTS_VERIFIED.md D-3) and T1 writes its replacement with the flipped
   default.
4. Keep `rv003usb/rv003usb-arm.S` byte-identical. Commit listing the resolutions. Do not push.

Accept (static): `git submodule status` shows only `ch32fun`; `git status` clean;
`git diff origin/py32 -- rv003usb/rv003usb-arm.S` empty; `! grep -rn 'py32f0-template' . --exclude-dir=.git`;
`make -C demo_gamepad`, `make -C demo_hidapi`, `make -C bootloader`, `make -C bootloader_dfu/v003`,
`make -C bootloader_dfu/wg015 PREFIX=riscv64-unknown-elf-`, and
`make -C demo_hidapi -f ../rv003usb/wg015/Makefile.wg015 PREFIX=riscv64-unknown-elf-` all succeed.

Size: one session.

#### Wave 1

**T1 — Target skeleton, linker scripts, RAM budget — Opus — closes DEFECTS_VERIFIED.md D-4, DEFECTS_VERIFIED.md D-5**
*Opus: DEFECTS_VERIFIED.md D-5 is the only defect in the set that produces no diagnostic at build time and an
obscure one at run time; and the RAM arithmetic below decides which parts the port supports.*

Owns: `rv003usb/py32/{py32_min.h, ch32fun.h, startup_py32.S, py32_common.ld, py32f003x6.ld,
py32f030x6.ld, py32f030x8.ld, py32f002bx5.ld, Makefile.py32, py32_stdio_stub.c, selftest_main.c,
README.md}`.

Entry: T0 landed.

Does:

*(a) The linker script — the real content of this task (BUILD_FACTS.md §4, DEFECTS_VERIFIED.md D-5).* There is **no
`.datacode` rule anywhere** — not in the branch, not in `py32f0-template@289ffc8`. The branch's
RX engine lands in RAM only because the stock script's rule is `*(.data*)`, a wildcard with no
dot, which absorbs it; verified by linking (VMA `0x20000000`, LMA `0x08000200`, BUILD_FACTS.md §4). A script
spelling that rule `*(.data.*)` places the RX engine in flash: no error, no warning, build
succeeds, every timing figure void. Therefore:
* one named output section `.timecrit` (RAM, `AT>` flash), input rule
  `KEEP(*(.timecrit)) KEEP(*(.timecrit.*)) KEEP(*(.datacode))` — the third pattern carries the
  unmodified branch engine until T2 renames its section, and is **not** removed when T2 lands
  (an inherited object could still emit it);
* `ASSERT(SIZEOF(.timecrit) > 0, "…")` — this is the D-5 killer: if the input rule ever stops
  matching, the output section is empty and the build fails instead of running from flash;
* `ASSERT(ADDR(.timecrit) >= ORIGIN(RAM) && ADDR(.timecrit) + SIZEOF(.timecrit) <= ORIGIN(RAM) + LENGTH(RAM), "…")`;
* `-Wl,--orphan-handling=error` in `Makefile.py32`, with `.ARM.attributes`, `.ARM.exidx`,
  `.comment` and `.debug*` explicitly placed or discarded. This generalises the fix: **any**
  input section the script does not name becomes a link error rather than a silent placement.
  That is what turns D-5 from a class of accidents into an impossibility.

The remaining half of the rule — "no flash literal-pool load inside a timed bit cell" (CHIP_FACTS_XIAMATSU.md §1,
§10 R23) — cannot be expressed in `ld`, which does not resolve `[pc,#imm]` targets. It is T14's
walker. T1's part is to make it *fail the build*: `Makefile.py32` declares `all: … check-cycles`
so the walker runs on every build, not on request. `--orphan-handling=error` covers the case
where a pool is emitted into a section nobody placed.

*(b) Memory map, and a real RAM budget.* Geometry from BUILD_FACTS.md §6 / `py32f0-template@289ffc8`
LDScripts, one script per supported part:

| part | RAM | FLASH | script |
|---|---|---|---|
| PY32F003x4 | 2 K | 16 K | — see below, out of scope |
| PY32F003x6 / PY32F030x6 | 4 K | 32 K | `py32f003x6.ld` / `py32f030x6.ld` |
| PY32F003x8 / PY32F030x8 | 8 K | 64 K | `py32f030x8.ld` |
| PY32F002Bx5 | 3 K | 24 K | `py32f002bx5.ld` |
| PY32F002Ax5 | 3 K | 20 K | out of scope (§10 R25) |

The flip makes this a live constraint: the new primary family's smallest member has **less** RAM
than the demoted F002B. Fixed RAM floor for a PY32 app, all terms sourced:

| term | bytes | source |
|---|---|---|
| `.timecrit` ceiling | 960 | T2 acceptance (branch today: 252 RX + 512 TX = 764 measured, BUILD_FACTS.md §3) |
| `rxbuf` | 20 | `4 + USB_BUFFER_SIZE(12) + 4`, T2 step 2 |
| fixed top-of-RAM `.noinit` block | 16 | Р6 |
| stack `ASSERT` floor | 768 | T2 acceptance |
| **floor** | **1764** | |

2048 − 1764 = **284 B** left on an F003x4 for descriptors, `.data`, `.bss` and
`rv003usb_internal_data`. `demo_hidapi` alone carries eight descriptor-list entries plus device,
config, HID report and three strings (`demo_hidapi/usb_config.h:28-152`), all of which Р4 puts in
RAM. **Plan decision: PY32F003x4 is out of scope; the minimum primary part is x6 (4 K).** T1
does not assert this by hand — the ld `ASSERT` in (c) fails on x4 and passes on x6, and T4's
`--print-memory-usage` prints the number that closes the question.

*(c)* `ASSERT(__noinit_top - __bss_end >= PY32_STACK_MIN, "stack below PY32_STACK_MIN")`,
`PY32_STACK_MIN = 768`. Section order: `.isr_vector` (flash) → `.data` + `.rodata.usbdesc`
(RAM `AT>` flash) → `.timecrit` (as (a)) → `.text`/`.rodata` (flash) → `.bss` → `.noinit`
(NOLOAD) → fixed block `__noinit_top = ORIGIN(RAM)+LENGTH(RAM)-16` with
`PROVIDE(py32_boot_flag = __noinit_top); PROVIDE(py32_boot_count = __noinit_top+4);
PROVIDE(py32_dbltap = __noinit_top+8); PROVIDE(py32_noinit_spare = __noinit_top+12);` → stack top
= `__noinit_top`. `PROVIDE(__timecrit_lma/_start/_end, __data_*, __bss_*)`.

*(d)* `py32_min.h` — structs, offsets and bit masks from §3.3-3.4, one `_Static_assert` per
struct size and offset (`offsetof(GPIO_TypeDef,BSRR)==0x18`, `RCC.CSR==0x60`, `EXTI.PR==0x0C`,
`EXTICR[0]==0x60`, `IMR==0x80`, FLASH `CR==0x14`, `SR==0x10`, `TS0==0x100`,
`SCB.ICSR==0xE000ED04`…); family switches (`PY32F030`: ports A/B/F, PLL, `HSI_FS=100` @24 MHz;
`PY32F002B`: ports A/B/C, no PLL, `HSI_FS=101`); OSPEEDR encoding with its RM002B p78 cite (Р8);
LSI and `RCC->CSR` bits, needed by T12; every block cites its RM page; assembler-clean (no `UL`).
Written from RM/DS, not copied from CMSIS or the LL drivers (§9.1).

*(e)* `ch32fun.h` shim, mirroring `rv003usb/wg015/ch32fun.h`: `NVIC_EnableIRQ`/`SetPriority`
(2-bit `IPR`)/`SystemReset`, `__disable_irq`/`__enable_irq`, SysTick struct +
`PY32_systick_freerun()` (`LOAD=0xFFFFFF`, `CLKSOURCE=HCLK`, `ENABLE`, no IRQ — **the only writer
of `LOAD` in the tree**, Р9), wrap-safe `Delay_Ms`/`Delay_Us` (`Delay_Ms` chunked ≤ 100 ms),
`SystemInit()` no-op, `#error` on `RV003USB_USB_TERMINAL`/`RV003USB_DEBUG_TIMING`,
`extern uint32_t py32_boot_flag, py32_boot_count, py32_dbltap;` and
`static inline void py32_app_alive(void){ py32_boot_count = 0; }`.

*(f)* `startup_py32.S`: 48-word vector table, weak `Default_Handler`, EXTI symbols exactly as
`startup_py32f002b.s:133-135`; `NMI_Handler` = `NVIC_SystemReset` when `PY32_HSE=1` (a silent CSS
fallback to the untrimmed HSI drops the link, §3.2) else weak default; `Reset_Handler`: SP =
`__noinit_top`, copy `.data` (incl. `.rodata.usbdesc`), copy `.timecrit` (LMA→VMA), zero `.bss`
(never `.noinit`), then clock init per family —
**F030/F003 (primary):** `HSI_FS=100` trim word from `0x1FFF0F10`, `HSION`, `PLLCFGR.PLLSRC=HSI`,
`PLLON`, wait `PLLRDY`, `ACR=LATENCY_1`, `CFGR.SW=PLL`, wait `SWS` (RM030 p77, p83). 24 × 2 =
**47.98 MHz, −0.04 %** (CHIP_FACTS_XIAMATSU.md §2, `xm_030.md:15`, `:336`) — inside the USB ±1.5 % tolerance and the
sampling margin **with no trim step and no servo at reset** (§3.1).
**F002B (second track):** `ACR=LATENCY_1`, `HSION`, wait `HSIRDY` — and **never** load
`[0x1FFF0104]` blind: the factory 48 MHz word runs the chip at a measured 43.12 MHz, −10.2 %
(`xm_002b.md:172-175`, `:209-210`, §10 R19). The clock is brought to 48 MHz by T12's calibration,
called from `main()` before `usb_setup()`, not from startup.
Then `VTOR = __vector_table`, `bl main`. Bring-up builds (`PY32_SWD_DELAY=1`) hold ≈100 ms before
reconfiguring clocks or pins (§10 R24, `xm_030.md:376-378`) — without it a probe cannot re-attach
and F002B has no ROM loader to recover through.

*(g)* `Makefile.py32`, mirroring `Makefile.wg015`: **`MCU ?= PY32F030x8`** (the flip; also
`PY32F030x6`, `PY32F003x6`, `PY32F002Bx5`); `SOURCES := $(TARGET).c rv003usb/rv003usb-arm.S
rv003usb/rv003usb.c startup stub $(wildcard $(PY32_DIR)/engine_*.S) $(wildcard $(PY32_DIR)/py32_*.c)`;
`-include $(PY32_DIR)/py32_cal_*.mk`; `DEFS` per §9.1 on **both** the `%.o: %.c` and `%.o: %.S`
rules (`-x assembler-with-cpp`); `-mcpu=cortex-m0plus -mthumb -Os -ffunction-sections
-fdata-sections -nostartfiles -specs=nano.specs`, `-Wl,--gc-sections
-Wl,--orphan-handling=error -Wl,--print-memory-usage`; targets
`all size lst bin flash clean check-cycles`, `all: … check-cycles`; `flash` = `pyocd load
--target py32f030x8 …` (Puya DFP, §3.5) with a `JLINK=1` alternative, **no OpenOCD**.

*(h)* `README.md`: pins (D+=PB0, D−=PB3, DPU=PB2, as the branch, same on F030), clock options,
the RAM budget table above, IRQ policy (Р7), **D± drive: lowest OSPEEDR + 33 Ω series, why (Р8),
and "22 pF on D± is not a fix" (PA A-8)**, `USB_DPU_DELAY_MS` (PA A-10), the SysTick rule (Р9),
probes (§3.5), and the one-line statement that the primary part needs no clock servo at reset.

Accept (static, all in this container):
1. `make -C demo_gamepad -f ../rv003usb/py32/Makefile.py32 MCU=PY32F030x8` and `MCU=PY32F002Bx5`
   link `selftest_main.c` against the **unmodified branch** `rv003usb-arm.S` and master
   `rv003usb.c` (a T1-local weak `usb_port_hw_setup` stub is allowed until T3 lands).
2. `arm-none-eabi-objdump -h selftest.elf`: `.timecrit` VMA in SRAM, LMA in flash, size > 0;
   `.noinit` outside `.bss`; `.isr_vector` at `0x08000000` with word0 = `__noinit_top`,
   word1 = `Reset_Handler|1`.
3. **D-5 regression, both directions.** Temporarily narrowing the `.timecrit` input rule to
   `*(.data.*)` must make the link **fail** on the `SIZEOF(.timecrit) > 0` assert (record the
   command and the message in the commit); restoring it must make it pass. This is the single
   check that distinguishes this port from the branch.
4. `nm selftest.elf | grep py32_boot_flag` = `0x20001FF0` on F030x8 (8 K), `0x20000FF0` on x6
   (4 K), `0x20000BF0` on F002Bx5 (3 K).
5. `MCU=PY32F003x4` fails to link on the stack `ASSERT` with `demo_hidapi` — the budget decision
   of (b) is enforced, not asserted in prose.
6. `make -n … | grep rv003usb-arm.S` contains `-DRV003USB_PY32=1 -DPY32F030=1 -DPY32F030x8=1`;
   the engine objects of the two `MCU` builds differ (`cmp` exits 1) — the `#if PY32F002Bx5`
   arms are finally both selected by the build system (DEFECTS_VERIFIED.md D-3, half closed here, half in T7).
7. `grep -c 'SysTick->LOAD' rv003usb/py32/ch32fun.h` = 1 and `grep -rn 'SysTick->LOAD'
   rv003usb/py32/*.S rv003usb/py32/py32_*.c` empty (Р9).
8. `_Static_assert`s compile; `! grep -rn 'py32f0-template' rv003usb/py32/`.

Size: one session. The linker script and `py32_min.h` are the bulk; (e)–(h) are transcription.

**T3 — C-layer seams: per-target `usb_port_<chip>.h` — Sonnet**
*Sonnet: a mechanical code move whose correctness is proved by two byte-identity gates, not by
judgement.*

Owns: `rv003usb/rv003usb.c`, `rv003usb/rv003usb.h`, `rv003usb/usb_port_ch32.h` (new),
`rv003usb/wg015/usb_port_wg015.h` (new), `rv003usb/py32/usb_port_py32.h` (new).

Entry: T0 landed. (Item 3 of Accept additionally needs T1 — wave-1 exit item.)

Does: Р2 exactly. `rv003usb.h` gets the single selector and declares the seam API. `usb_setup()`
in `rv003usb.c` becomes `rv003usb_internal_data.se0_windup = 0; usb_port_hw_setup();`, with the
V003/V00x body (`c:59-153`, incl. `DEBUG_TIMING`) moved **verbatim** into `usb_port_ch32.h` and
the WG015 body into `usb_port_wg015.h`; the reboot block (`c:173-186` and the WG015 variant)
becomes `USB_PORT_REBOOT_TO_BOOTLOADER()`; the `USB_DM_IRQ` block from 0ad3c42 moves into the
PY32 header (this is the edge T2 depends on); `RV003USB_USB_TERMINAL` and `RV003USB_DEBUG_TIMING`
become `#error` under `RV003USB_PY32`.
PY32 `usb_port_hw_setup()`: `RCC->IOPENR |= GPIOxEN`; DP/DM `MODER=00, PUPDR=00,
OSPEEDR=USB_PORT_OSPEED` (**default 0 = lowest**, Р8; overridable from `usb_config.h`); DPU
`MODER=01` then `BSRR` high after `Delay_Ms(USB_DPU_DELAY_MS)` (default 0; charger-detect ICs
sharing D± need ≈2 s, rv003usb #137, PA A-10); `EXTI->EXTICR[DM>>2]` port select (field widths as
`py32f002b_ll_exti.h:153-160` — lines 0-4 are 3-bit, 5-7 are 1-bit); `EXTI->IMR |= 1<<DM;
EXTI->FTSR |= 1<<DM; EXTI->PR = 1<<DM`; `NVIC_SetPriority(USB_DM_IRQn, 0)`; `NVIC_EnableIRQ`;
`py32_app_alive()`.
`USB_PORT_REBOOT_TO_BOOTLOADER()`: `py32_boot_flag = 0xB00710AD; NVIC_SystemReset()` —
`py32_boot_flag` is T1's ld-provided top-of-RAM word, **not** a `.noinit` variable.

Accept (static): (1) `make -C demo_gamepad` (CH32V003) `.bin` **byte-identical** to the T0 build
(`cmp`); (2) WG015 `demo_hidapi` and `bootloader_dfu/wg015` build and their `.bin` is identical or
the diff is explained in the commit message; (3) `rv003usb.c` compiles for `MCU=PY32F030x8` and
`PY32F002Bx5` with `-Wall -Werror` (wave-1 exit item — needs T1); (4) `grep -n 'OSPEEDR'
rv003usb/py32/usb_port_py32.h` shows only the `USB_PORT_OSPEED` use, default `0`;
(5) `grep -c 'py32_app_alive' rv003usb/py32/usb_port_py32.h` ≥ 1; (6) `grep -c 'USB_DM_IRQ'
rv003usb/rv003usb.h` = 0.

Size: one session.

**T8 — Documentation set — Sonnet**
*Sonnet: transcription against a source-per-fact rule; no design decisions are taken here.*

Owns: `doc/py32/{chip_info.md, ledger_arm.md, STATE.md, TODO.md}`.

Entry: T0 landed.

Does: `chip_info.md` = §3 expanded with page refs, incl. §3.5 probes; `ledger_arm.md` = Appendix A
(RAM-execution column) + the TX ledger + the staircase costs, carried over verbatim
with Appendix A's cites, **not re-derived**; `STATE.md` = fleet progress table + the append-only
`requests` section (§9.1's single shared exception) + the provenance table (one row per task:
source, licence class, `Provenance:` trailers seen, Р10); `TODO.md` in the house format of
`doc/wg015/TODO.md`.

Accept (static): every fact carries a source; `STATE.md` lists **every** task T0–T16 with its
owner, model and wave; `grep -c '^| T' doc/py32/STATE.md` ≥ 18; the provenance table names
Grainuum (MIT), joyboot (MIT), LemcUSB (GPLv3, ideas only), stm32f030-vusb (GPL-3.0, ideas only),
V-USB (GPLv2, ideas only); `grep -c 'RAM-execution' doc/py32/ledger_arm.md` ≥ 1 (the ledger must
state which column it is in — §2.0).

Size: one session.

**T13 — Keepalive trim actuator: what the servo becomes after the flip — Opus**
*Opus: this task decides the default behaviour of the only closed loop in the firmware; a wrong
default either hunts on the primary part or silently disables drift correction on both.*

Owns: `rv003usb/py32/usb_port_py32_trim.h`, `rv003usb/py32/selftest_trim.S`.

Entry: T0 landed. (The cost item of Accept needs T14 — wave-1 exit item.)

Does. The branch ships a **stub**: `handle_se0_keepalive:` at `arm.S:217` is `// TODO` followed by
`ldr r0, =interrupt_complete; bx r0` — it acknowledges nothing and measures nothing. DEFECTS_VERIFIED.md's "Not
verified here" is right that this is a design gap, not a source defect. After the flip its shape
changes, and the honest statement is what it **becomes**, in three modes selected at compile time
by `USB_TRIM_MODE`:

| mode | for | what runs on every keepalive |
|---|---|---|
| `off` | HSE builds on F030 | nothing — `USB_TRIM_ACTUATE` expands to zero instructions |
| `drift` | **default; F030/F003 HSI, the primary path** | ack `EXTI->PR` first, measure the `USB_TICK` delta `(last − now) & 0xFFFFFF`, sanity ±4000 (as `S:762-772`; an out-of-window delta also resets the lock counter), store `last_se0`/`delta_se0`/`se0_windup` (`h:190-192`), then a single saturating slow term `trim = trim0 − USB_TRIM_SIGN · sat(windup >> USB_TRIM_SLOW_SHIFT, ±USB_TRIM_SAT)` |
| `acquire` | F002B, after T12 has already brought the clock into range | `drift` plus the two-rate acquisition arm: while `lock < USB_TRIM_LOCK_N`, `trim −= USB_TRIM_SIGN · (dev >> USB_TRIM_FAST_SHIFT)`, `lock++`; `trim0` captured at the first keepalive |

**What the flip deletes from the primary path.** HSI 24 × PLL2 = 47.98 MHz, −0.04 % (CHIP_FACTS_XIAMATSU.md §2,
`xm_030.md:15`, `:336`) is inside the USB ±1.5 % tolerance and inside the engine's ≈0.44 %
sampling margin (§2.4.5) at reset, so `drift` is the default and the following v2 code is **not
compiled on the primary path**: the fast-acquisition arm, the `lock` counter, `USB_TRIM_LOCK_N`,
`USB_TRIM_FAST_SHIFT`, and the capture of `trim0` at the first keepalive. With them goes the
constraint that set their values — the servo no longer has to reach 0.25 % inside the host's
reset→first-SETUP window (§10 R15, OQ9), because it starts inside it. Concretely, the keepalive
path shrinks from acquisition + drift to: ack, delta, sanity, one shift, one saturate, one
`ICSCR` write.
What does **not** go away: temperature. DS030 T5-15 gives ±2 % over 0–85 °C and −4/+2 % over
−40…85 °C — both outside the sampling margin (§10 R2). So the servo is not deleted, it is demoted
from *enumeration precondition* to *drift compensator*, and it stops being on the critical path
of bring-up.
Actuation constraints, both from measurement: on F002B the servo moves `TRIM_L` **only** —
`TRIM_H` scales the range in coarse steps (+41 % at 0b0111, +50 % at 0b1000, `xm_002b.md:232-246`)
and a step across a band is ≈9 %, which throws the delta out of the sanity window and loses lock
(§10 R20). `USB_TRIM_SAT` (default ±64 LSB) must keep the excursion inside the band T12 selected.
`USB_TRIM_SIGN` is a build constant, measured by bench6/T16 — the sign of the HSI trim LSB is not
documented.
The header expands to Thumb-1 with **no** literal-pool load: the register base and the masks
arrive in registers from the caller's frame, because the actuator runs in `.timecrit` and a
flash-resident pool costs 4 cycles there (CHIP_FACTS_XIAMATSU.md §1, §10 R23).
`selftest_trim.S` instantiates `USB_TRIM_ACTUATE` alone in `.timecrit` between two global labels,
so the walker can cost the actuator in isolation without linking the engine.

Accept (static):
1. Each of `USB_TRIM_MODE = off|drift|acquire` assembles `selftest_trim.S` for both `MCU`s;
   an unset or unknown `USB_TRIM_MODE` is `#error`; `acquire` on a `PY32F030` build is `#error`
   ("the primary path does not acquire at reset — see §9.4 T13").
2. `off` produces a zero-byte body: `arm-none-eabi-nm --print-size` shows
   `__trim_actuate_end - __trim_actuate_begin == 0`.
3. `arm-none-eabi-objdump -d selftest_trim.o | grep -c 'ldr.*\[pc'` = 0.
4. Wave-1 exit item: `tools/py32_cyc.py --path trim` reports the `drift` body ≤ 40 cycles and the
   whole keepalive path (first instruction → exception return) ≤ 96 cycles, the budget Appendix A/B sets
   because a token may follow a keepalive EOP after 2 bit-times of idle (USB 2.0 §7.1.18-19).
5. `grep -c 'TRIM_H' rv003usb/py32/usb_port_py32_trim.h` ≥ 1 **and** no write to `TRIM_H` in the
   actuator body (`objdump` of the `acquire` build): §10 R20 enforced, not documented.

Size: one session.

**T14 — Cycle walker, cost table, literal-pool check — Opus**
*Opus: this file encodes the cost model the entire ledger is padded against; a walker that passes
for the wrong reason is worse than no walker.*

Owns: `tools/py32_cyc.py`, `tools/py32_cyc_costs.json`, `tools/py32_cyc_selftest/*`.

Entry: T0 landed. Self-contained otherwise — it writes its own fixtures and assembles them with
the installed gcc (BUILD_FACTS.md §1).

Does: Appendix B's walker seed, finished. Two-column model, keyed on **where the instruction
executes**, from `py32_cyc_costs.json`:

* `exec: {RAM, FLASH}` with the CHIP_FACTS_XIAMATSU.md §1 rows — RAM: ordinary 1, `b<cc>` taken 2-3 / not-taken 1,
  `bl` 4, `bx` 3, GPIO `ldr/str` 1, RAM data **2**, flash literal pool **4**, `push/pop` **2 + 1·(n−1)**;
  FLASH: RAM data **4**, flash literal pool 2, `push/pop` 4 + 1·(n−1).
* The region of every address comes from the **section map of the ELF**, never from a symbol name
  or a `.req` alias (Appendix B's rule) — so a mis-placed section shows up as a cost change, not a silent
  pass.
* `--cost-table FILE` override (§10 R4/G1: no pad constant is final before the bench).
* Point values give the gate; `ranges` maxima give the exposure; report
  `name cycles [min..max] budget PASS|FAIL|EXPOSED`, exit 1 on any FAIL.
* **The literal-pool rule as a hard error, not a report** (CHIP_FACTS_XIAMATSU.md §1, §10 R23, BUILD_FACTS.md §5): every
  `ldr rN,[pc,#imm]` reached from a path marked time-critical has its pool address resolved and
  its region looked up; a pool outside RAM is a fatal error. BUILD_FACTS.md §5 measured that today the rule
  holds **by construction, not by enforcement** — gcc happened to emit `.datacode`'s pool inside
  `.datacode` (`.word`s at 0xdc..0xf8 of a 0xfc section), with three of the eight loads inside
  timed loops. That is a property of one compiler invocation, not of the source. T1 makes
  `all: … check-cycles`, so this check fails the build.
* `--selftest`: assemble the `tools/py32_cyc_selftest/*.S` fixtures with the installed
  arm-none-eabi-gcc, link them at known VMAs with a fixture linker script, and compare against
  the counts written in each fixture's header comment. Fixtures must cover, at minimum: a
  straight-line block in each region; a `push`/`pop` pair in each region; a `[pc,#imm]` load with
  the pool in RAM and the same load with the pool in flash (they must differ by 2 in the RAM
  column); a taken and a not-taken `b<cc>`; a `bl`/`mov pc,lr` staircase entry; and one fixture
  that **must be rejected** — a flash pool inside a time-critical path (`--selftest` fails if it
  is accepted).

Accept (static): `python3 tools/py32_cyc.py --selftest` exits 0; the negative fixture makes a
normal run exit non-zero with a message naming the address and the region;
`python3 -c "import json; json.load(open('tools/py32_cyc_costs.json'))"` exits 0 and the file
carries both `exec` columns and a `source` field per row citing `xm_030.md:<n>`;
`grep -c 'ranges' tools/py32_cyc.py` ≥ 1; `! grep -n 'startswith(.rv003usb_wait' tools/py32_cyc.py`
(no cost may be taken from a symbol name).

Size: one session. The walk function and the fixtures are the bulk; the cost table is transcription
from §2.0.

#### Wave 2

**T2 — Engine: RX path, correctness, placement — Opus — closes DEFECTS_VERIFIED.md D-1, DEFECTS_VERIFIED.md D-2**
*Opus: this is the engine everything else in waves 2-4 builds on; a wrong cycle count here is
wrong everywhere downstream, and nobody catches it by inspection.*

Owns: `rv003usb/rv003usb-arm.S`, `rv003usb/py32/usb_port_py32_asm.h`, `rv003usb/py32/usb_port_py32_tune.h`.

Entry: T1 landed (`.timecrit` section, `py32_min.h`, build). T3 landed (`USB_DM_IRQ` moved into
the PY32 port header — wave-1 exit item). T13 landed (`USB_TRIM_ACTUATE` callable from the SE0
branch — wave-1 exit item). T14 landed (`tools/py32_cyc.py --selftest` exits 0 — wave-1 exit item;
T2's own acceptance is a walker run).

Does — RX path and correctness only; the TX re-pad and the per-part `#if` review are T2T's job
(§9.0 point 5), serialised on this file:

0. **Guard** (v2 §2.6, PA S-11): first lines `#ifndef RV003USB_PY32 #error … #endif` and
   `#if !defined(PY32F030) && !defined(PY32F002B) #error … #endif`. File header carries the MIT
   notice for Grainuum's staircase idea (Р10, `[MIT-attrib]`) and the [11,74]-cycle entry-window
   citation. `usb_port_py32_asm.h` `#error`s on an unset `USB_PORT`.
1. Replace every hand-written literal in the RX region with the §7.1-style macros from
   `usb_port_py32_asm.h` (GPIO base, `IDR_OFFSET`/`BSRR_OFFSET` — `0x10`/`0x18` in either family,
   BUILD_FACTS.md §7, so the macro carries no per-part arm).
2. **Placement (Р4, half of it — RX only).** `.pushsection .datacode,"ax"` becomes
   `.section .timecrit,"ax"`; `.ltorg` after each RX block so every literal pool it emits resolves
   inside `.timecrit` (RAM) — **not a preference, a hard rule now enforced by T14's walker**
   (§10 R23: a pool that lands in flash costs 4 from RAM code, `xm_030.md:490`, and today it holds
   only "by construction", BUILD_FACTS.md §5). `rxbuf: .space 4 + USB_BUFFER_SIZE + 4` (20 B, T1's floor
   table) — the leading 4 is the limit word D-2's bound check reads, the trailing 4 keeps the
   struct word-aligned. TX stays in `.text` (flash) until T2T moves it; the linker's third
   `KEEP(*(.datacode))` pattern (T1) stays live for exactly this reason. **Consequence for the
   wave-2 exit gate:** the walker's path list carries the TX paths against the flash column
   (their pre-existing, unchanged budgets) until T2T retargets them to the RAM column in wave 3
   — `make check-cycles` is scored against mixed columns at the end of wave 2 by design, not by
   oversight. Placement is corroborated a second time, in a fully linked image rather than a
   synthetic object: BUILD_FACTS.md §9 shows `EXTI2_3_IRQHandler` at `0x200000c8`, `preamble_loop` at
   `0x200000e6`, `bit_process` at `0x20000142`, `rxbuf` at `0x2000023c` (all RAM) against
   `usb_send_data` at `0x0800022c` (flash) — the vendor-toolchain build BUILD_FACTS.md §9-§10 exercised, not
   this port's own linker script, but the same mechanism D-5 describes.
3. **D-1 — endpoint bound, `bhi`→`bhs`** (`arm.S:277`, DEFECTS_VERIFIED.md D-1). One-instruction fix in the
   dispatch tail, which stays in flash and is not cycle-counted (Appendix A; §10 R3/OQ14 —
   "dispatch in flash reads RAM data at 4/access — not timed, acceptable" — the §9.6 request, noted,
   no action needed beyond this fix). Same encoding size, same cycle count, zero effect on any
   RX or TX cell.
4. **D-2 — RX overrun, bound the store** (`arm.S:145-148`, DEFECTS_VERIFIED.md D-2). At `is_end_of_byte`:
   `cmp r2, r8; bhs done_usb_message` before the `strb`, with r8 = `rxbuf + 4 + USB_BUFFER_SIZE`
   loaded once at ISR entry (r8 is free through the RX path). This sits **inside the
   cycle-counted path**, so it is not free: `cmp lo,hi` costs 1 cycle (RAM, ordinary instruction,
   §2.0) and must be paid for out of the existing 32/32/32/32/64 budget, not added on top. Pay
   for it by removing 1 cycle from `DELAY_CYCLES(6)` (arm.S:151) on the mid-byte path and by
   shortening the EOB tail's `nop` pad by 1; the walker's path list carries both variants and
   must still report exactly 32 — a bound check that changes the budget is a bug, not a feature.
5. **F9 — bounded preamble spin** (PA A-16): `preamble_loop` gives up after
   `USB_RX_PREAMBLE_LIMIT` (≈512 cycles = 16 bit-times, `usb_port_py32_tune.h`) instead of
   spinning forever on a stuck or shorted line; counter in `SCRATCH` (r4, free there), 4×-unrolled
   poll so sample spacing is 4/4/4/7 (worst-case detect jitter 0…6 instead of 0…4 — §10 R18; the
   walker reports the wider spacing as an `EXPOSED` range, not a silent pass). `USB_RX_SYNC_DELAY`
   (F5) is re-derived on paper against the wider jitter so the 14–18/32 sample band still holds;
   §10A gate G7 is the hardware check that it actually does on silicon, out of scope here.
6. **F3 — early exit on `rx_stuffed`.** Sample the delay once and `beq done_usb_message` when no
   bus transition has occurred, instead of spinning the full `DELAY_CYCLES(24)`; the 4-cycle test
   comes out of that delay's own budget so the slot stays 32/64.
7. **F6 — `RV003_ADD_EXTI_MASK`/`HANDLER` hook**: on ISR entry, if `EXTI->PR & USB_DMASK` is
   zero, jump to a user hook in flash before touching any RX state; ack the extra mask bits at
   exit (mirrors the RISC-V `S:113-129/645-650` pattern) — how an app that also needs EXTI on
   lines 4-15 shares the vector (§10 R11) without T2 knowing about it.
8. **Marker** (§7.3, zero-intrusion debug pulse): r10 = mask loaded from the RAM word
   `usb_dbg_mask`, one `str`/pulse per RX slot boundary; production mask is `0`, so the pulse
   compiles to nothing measurable when disabled — ported once here because `usb_port_py32_asm.h`
   is this task's file.
9. **RX-side staircase entries.** The two RX entry pads (after `DELAY_CYCLES(96)` and
   `DELAY_CYCLES(71)`) move off inline `nop` padding onto `bl rv003usb_wait_N` (§7.4) for N ≥ 9
   (below that, inline `nop` stays cheaper than a `bl`+return). The label table itself
   (`rv003usb_wait_5`…`rv003usb_wait_N`, `N − C` `nop`s then a return) is generated in this file
   because RX needs it first; T2T extends the same table for TX's larger N (up to 64) in wave 3.
   `C` is **not assumed**: it comes from `usb_port_py32_tune.h`'s `USB_STAIRCASE_C`, wired to
   T14's default cost table (`bl`:4 + `mov_pc`:2 = 6, the seed values in `py32_cyc_costs.json`),
   overridable from a `py32_cal_*.mk` (T10/T16's seam, §9.2) once bench K10 measures whether the
   return is `mov pc,lr` (C=6) or `bx lr` (C=7) — no header edit, ever, for a measured constant.
10. **In-slot RX pads stay inline** (Appendix A's structural finding): the loop-invariant literal loads
    `ldr CRC,=0xffff` / `ldr SCRATCH,=0xa001; mov POLY_RX,SCRATCH` hoist out of the packet-type
    loop into the `DELAY_CYCLES(71)` pad (zero extra registers — CRC/POLY_RX already hold the
    values, §2.1) — this is what keeps that loop's literal load out of the timed cell
    entirely, rather than merely making it cheap.
11. Every pad above is a **formula in `usb_port_py32_tune.h`, parameterised on `USB_B_TAKEN`**
    (default 2 — a taken branch/`b<cc>` from RAM, §2.0), not a baked integer: e.g. the mid-byte
    `bit_process` pad is `4 − 2·USB_B_TAKEN` `nop`s (≥0 only at `USB_B_TAKEN`≤2; at 3 the fix is
    structural — fewer taken branches per cell, the Appendix A reading — out of scope here since
    B=2 is what T14's default cost table and every criterion below assume; a `USB_B_TAKEN=3`
    build is a research build for T10/T16, not a wave-2 deliverable). Same shape as the WG015
    house ledger (the §9.6 request, honoured; T2T applies the same style to the TX pads).

Accept (static):
1. `arm-none-eabi-gcc -x assembler-with-cpp -c rv003usb/rv003usb-arm.S -o /dev/null` with no `-D`
   exits non-zero, stderr matches `#error` (item 0's guard).
2. Assembles clean (`-Wa,--fatal-warnings`) for `MCU=PY32F030x8` and `PY32F002Bx5`.
3. `python3 tools/py32_cyc.py <elf>` exits 0: every RX path reports exactly 32 and the stuffed
   path 64; the keepalive path (T13) ≤ 96.
4. `arm-none-eabi-objdump -d rv003usb-arm.o | grep -c 'bhi.*done_usb_message'` = 0 and the
   disassembly at the old D-1 site shows `bhs`/`bcs` instead.
5. `arm-none-eabi-nm rv003usb-arm.o`: the RX-ISR-through-`done_usb_message` symbols resolve to
   `.timecrit` (SRAM); the TX symbols are still in `.text` (T2T has not moved them yet).
6. `arm-none-eabi-objdump -d --no-show-raw-insn rv003usb-arm.o` shows every `ldr rN,[pc,#imm]`
   reached from a `.timecrit` label resolving inside `[0x20000000, 0x20000000+PY32_SRAM_KB·1024)`
   (T14's literal-pool rule, §10 R23) — zero exceptions.
7. `grep -c 'USB_B_TAKEN' rv003usb/py32/usb_port_py32_tune.h` ≥ 1; the assembled `.text`/`.timecrit`
   byte count changes if `USB_B_TAKEN` is overridden to 1 at build time (the formulas actually
   drive the pad count, not a comment beside a fixed integer).
8. `grep -q 'xobs/grainuum' rv003usb/rv003usb-arm.S` (Р10 attribution present).

Size: one session; hard. The D-2 rebalance and the RX staircase migration are the parts that need
care — everything else is mechanical against Appendix A/B's tables.

**T4 — Demos conditioned for PY32 — Sonnet**
*Sonnet: a conditional-compile pass against gates T1/T3 already fixed; no engineering judgement
left to exercise.*

Owns: `demo_gamepad/{usb_config.h, funconfig.h, demo_gamepad.c, README.md}`,
`demo_hidapi/{usb_config.h, funconfig.h, demo_hidapi.c, README.md}`.

Entry: T1 landed (`Makefile.py32`, `py32_min.h`). T3 landed (`usb_port_py32.h`, `USB_PORT_OSPEED`).

Does: pins under `#if defined(RV003USB_PY32)` — `USB_PORT B, DP 0, DM 3, DPU 2` (the branch's own
numbering, unchanged on F030). `funconfig.h` untouched for V003 (T4 must not break the
byte-identity gate — new flags only inside `#if RV003USB_PY32`). Descriptors carry the `USBDESC`
section attribute exactly as `bootloader_dfu/wg015/usb_config.h:39` when `RV003USB_PY32` (Р4's
RAM-placement rule — descriptors are a `usb_send_data` source and must resolve inside SRAM, the
same rule T2's literal-pool check enforces for code). `demo_hidapi.c`'s WS2812/GPIOD block guarded
`#if !defined(RV003USB_PY32) && !(defined(WG015)&&WG015)`. `Delay_Ms(1)` before `usb_setup()` kept
(h TDDIS note, unaffected by the flip). PY32 builds never call a clock-configuration function from
either demo — T1's `startup_py32.S` brings the clock up before `main()` runs, unlike the vendor
branch's `demo_gamepad.c:15-23`, which calls `BSP_RCC_HSI_48MConfig()`/`BSP_RCC_HSE_PLLConfig()`
per part and silently configures **no clock at all** for any part neither `#if` arm names — a
build-system defect this port's architecture avoids structurally rather than by adding a third
arm (BUILD_FACTS.md §10.3). README build lines updated to the flipped default:
`make -f ../rv003usb/py32/Makefile.py32 MCU=PY32F030x8` first, `MCU=PY32F002Bx5` second.

Accept (static): both demos build for `MCU=PY32F030x8`, `PY32F030x6`, `PY32F002Bx5`;
`arm-none-eabi-size --print-memory-usage` prints RAM ≤ 1900 B on F030x6 and ≤ 2200 B on F002Bx5
(T1's floor table, §9.4 T1(b) — the vendor-toolchain build of the same demo lands at 2128 B/8K on
F030x8 and 1168 B/3K on F002Bx5, BUILD_FACTS.md §9, a different header stack from ours but the right order of
magnitude to sanity-check against); V003 `demo_gamepad.bin` unchanged vs the T0 build (`cmp`);
descriptor placement gate (PA S-4): `arm-none-eabi-nm --numeric-sort demo_hidapi.elf | grep -iE
'descriptor|string|report' | awk '$1 !~ /^2000/' | wc -l` = 0; `grep -c 'BSP_RCC\|SystemClock_Config'
demo_gamepad/demo_gamepad.c demo_hidapi/demo_hidapi.c` = 0 under `RV003USB_PY32` (clock init is
T1's job, never the demo's); `grep -rn 'SysTick->LOAD' demo_gamepad demo_hidapi` empty (Р9).

Size: one session.

**T6 — Calibration bench firmware: K1-K11 kernel superset + VCD gates — Sonnet**
*Sonnet: the kernel shapes and the two gates are fully specified by Appendix D; this
task assembles what is already designed.*

Owns: `py32_bench/{Makefile, main.c, bench_common.c, bench_common.h, bench_kernels.S,
bench1_ioport.c, bench2_branch.c, bench3_irq.c, bench4_flash.c, bench5_slot.c, bench6_trim.c}`,
`tools/wg015_vcd/*`.

Entry: T1 landed (build).

Does: **v2's bench1/bench2 are retired and replaced by the K1-K11 kernel superset of
Appendix D** (the §9.6 request, honoured in full):
1. `bench_kernels.S`: each kernel is a 1000×-unrolled straight-line block assembled twice — once
   into `.timecrit` (RAM copy), once into `.text` (flash copy) — timed with the free-running
   SysTick (`VAL` before/after, HCLK source, Р9), empty-kernel overhead subtracted, 16 repeats
   reported as min/max (Appendix D: "the spread is a result", per the source's own alignment caveat).
2. `bench1_ioport.c` now covers K1 (`ldr [r1,#0x10]`, GPIOA/B/F on F030 — closes OQ7), K2
   (`str [r1,#0x18]` alternating, LA-checked pin toggle), K3 (SRAM load, expect 2 RAM / 4 flash),
   K6 (flash-data load via register base, sets `Df` for R3/OQ14's "dispatch reads RAM data at
   4/access — not timed, acceptable" note).
3. `bench2_branch.c` now covers K4/K5 (literal load, pool in SRAM vs flash — resolves whether
   `L`=2 is real or T2's hoist is mandatory), K7/K8 (taken `b .+2`, aligned vs the odd-halfword
   case — **the direct test of R4/OQ4**: "taken branch 2 (TRM) vs 3 (Grainuum)" becomes a measured
   2-or-3 with an explicit alignment split, not a guess), K9 (`DELAY_CYCLES` shape, resolves
   A3/A6's 96-vs-127 question), K10 (`bl`/`mov pc,lr` vs `bl`/`bx lr` — sets `C` for T2/T2T's
   staircase), K11 (push/pop pairs without `pc`, sets the A2 entry constant).
4. `bench5_slot.c`, `bench4_flash.c` carried over from v2 unchanged in shape (isomorphic RX slot
   with PRBS+evictor; flash fetch profile for OQ14/R8's turnaround question).
5. `bench6_trim.c` unchanged in shape (HSI_TRIM LSB weight and **sign**, OQ3) — needed by T13's
   `USB_TRIM_SIGN` and, on the F002B track, by T12/T16.
6. `main.c`: menu binds keys `1`-`9` to weak `benchN_run` symbols (`__attribute__((weak))`,
   "n/a" printed when null); `Makefile` globs `bench[0-9]_*.c` — the seam T11's
   `bench7_loopback.c` and T12's `bench8_hsical.c` drop into without touching this task's files
   (§9.2's seam table).
7. `tools/wg015_vcd/wg015vcd.py`: `--marker-edge rise|both` (§7.3); **`--gate-se0 LO:HI`**
   (default `60:72` cycles = 1.25-1.5 µs) applied to the already-computed `eop_se0_cyc` inside
   `eval_gates` — the EOP width was reported but never gated before (PA T-2, A-14, L-2).

Accept (static): `make -C py32_bench -f ../rv003usb/py32/Makefile.py32 MCU=PY32F030x8` and
`MCU=PY32F002Bx5` build; `nm bench.elf | grep -cE 'bench[1-6]_run'` = 6;
`tools/wg015_vcd/selftest/run_selftest.sh` reports `0 failed` **and** contains a new case
exercising `--gate-se0` on a too-short EOP capture (must FAIL) and a 64-cycle one (must PASS);
`grep -c 'gate_se0' tools/wg015_vcd/wg015vcd.py` ≥ 3; `grep -rn 'SysTick->LOAD' py32_bench` empty
(Р9); `grep -c 'K1[01]\?' py32_bench/bench2_branch.c` ≥ 1 (K10/K11 present, not silently dropped).

Size: one session.

**T12 — F002B HSI self-calibration against LSI — Opus — the F002B track's precondition**
*Opus: §10 R19-R21's non-linear trim field and the ±3 % LSI datasheet-vs-measurement gap mean a
naive linear search either saturates or crosses a `TRIM_H` band; getting the search shape wrong
strands the whole F002B track behind an untunable servo.*

Owns: `rv003usb/py32/{py32_hsical.c, py32_hsical.h}`, `py32_bench/bench8_hsical.c`.

Entry: T1 landed (RCC/ICSCR/LSI/CSR offsets from `py32_min.h`).

Does: Р5.4 / §10 R19-R21 / §10A gate G4. The factory 48 MHz word (`[0x1FFF0104]`) is **never
loaded** on this part (R19 — it measures 43.12 MHz, −10.2 %, outside the servo's ±8.3 % sanity
window, so the servo could never engage from it):
1. Enable LSI (`RCC->CSR.LSION`, wait `LSIRDY`) — the reference available at reset, measured
   −0.18 % on one unit (`xm_002b.md:204-206`) but ±3 % by DS002B T5-14 (§10 R21's disagreement,
   closed only by §10A gate G4 on ≥5 units — out of scope here).
2. Count HSI cycles per LSI reference period (SysTick or a free-running counter clocked by HSI,
   gated by an LSI-derived edge; exact register sequence from `py32_min.h`'s RCC/LSI bits, T1).
3. Search `TRIM_L` (0x000-0x1FF, linear across 21.7-33.4 MHz at `TRIM_H=0`, `xm_002b.md:249-257`)
   with `TRIM_H` **fixed for the whole search** at the band containing 48 MHz (`0b0111` or
   `0b1000`, §10 R20 — a step across a `TRIM_H` boundary is a ≈9 % jump the search must never
   take). Binary or proportional search — a pure function, no MMIO, lives in `py32_hsical.h` so
   it is unit-testable in isolation.
4. Write `ICSCR.HSI_TRIM` with the winning `TRIM_L`, leave `HSI_FS=101`, `TRIM_H` untouched from
   step 3's fixed value; hand control to `main()`'s clock-ready point before `usb_setup()` (T1's
   clock-init spec: T12 runs from `main()`, not `startup_py32.S`).
5. Runs entirely before `USB_PORT_OSPEED`/DPU are asserted (Р5.4) — no bus activity, no timed
   cell, so none of T2's cycle-budget rules apply; this code lives in `.text` (flash).
6. `bench8_hsical.c`: on-target harness that runs the same search and reports the winning
   `TRIM_L`, the LSI reference count, and the resulting HSI frequency via MCO/2 (R21 — "MCO tops
   out at ≈35 MHz, must divide", `xm_002b.md:261`) — a **measurement** tool, run in T16 (Wave 4),
   not this task.

Accept (static, hardware-free — the search's correctness, not the silicon's, is what this task
can prove without a board):
1. `rv003usb/py32/py32_hsical.c`/`.h` compile for `MCU=PY32F002Bx5` with `-Wall -Werror`;
   **compiling for `MCU=PY32F030x8` is a build error** (`#error "F002B-only — F030 needs no
   calibration, §3.1"`), so the F030 wave-4 rig never links this file.
2. `py32_hsical.h`'s search function has no register access
   (`grep -c 'RCC->\|ICSCR' rv003usb/py32/py32_hsical.h` = 0) — MMIO stays in the `.c` wrapper
   only, so the algorithm is host-testable.
3. **Native unit test** (host `gcc`, not `arm-none-eabi-gcc`, compiled and run in this container):
   feed the search a synthetic LSI-vs-HSI ratio corresponding to (a) the measured −0.18 % LSI /
   −10.2 % factory HSI case and (b) the DS002B T5-14 ±3 % LSI extremes; assert the search
   converges to a `TRIM_L` whose synthetic HSI lands within ±1.5 % of 48 MHz in every case, in
   ≤ `USB_HSICAL_MAX_STEPS` iterations, and never selects a `TRIM_L`/`TRIM_H` pair outside the
   fixed band chosen in step 3. Exit code 0 is the gate.
4. `grep -c 'TRIM_H' rv003usb/py32/py32_hsical.c` shows exactly one write to `TRIM_H` (the fixed
   value from step 3) and zero writes to it from inside the search loop (§10 R20 enforced the same
   way T13's item 5 enforces it on the actuator side).
5. `grep -c '0x1FFF0104' rv003usb/py32/py32_hsical.c` = 0 (the factory word is never read, R19).
6. `py32_bench/bench8_hsical.c` builds for `MCU=PY32F002Bx5` and links against `py32_hsical.h`'s
   pure search (no duplicated logic).

Size: one session. The search-shape unit test is the real content; the MMIO wrapper is
transcription from `py32_min.h`.

#### Wave 3

**T2T — Engine: TX path onto RAM, re-pad, per-part `#if` review — Opus — closes the RAM-placement
half of Р4**
*Opus: moving 512 B of flash-resident TX code into the same `.timecrit` region T2 already
budgeted changes literal-pool costs in both directions at once (§12 note 60) — the arithmetic has
to be redone, not copied from T2's RX numbers.*

Owns: `rv003usb/rv003usb-arm.S`, `rv003usb/py32/usb_port_py32_asm.h`,
`rv003usb/py32/usb_port_py32_tune.h`. Serialised after T2 (same files, never concurrent).

Entry: T2 landed.

Does — TX only; RX is untouched:
1. **Placement, the other half of Р4.** Move `usb_send_empty` … `no_really_done_sending_data`
   (512 B, BUILD_FACTS.md §3) from `.text` into `.timecrit`. `.ltorg` after each TX block so its literal pool
   — today at `.text+0x1b8..0x1fc`, in flash, costing 2 from the flash-resident code that reads it
   (BUILD_FACTS.md §5) — resolves in SRAM instead. The one load inside a timed cell today, `.text+0xda` inside
   `pre_and_tok_send_one_bit`, must be **hoisted into a register before the cell** (T2's §2.1
   hard-rule pattern, reused verbatim) — otherwise it becomes a 4-cycle RAM-code-reads-flash-pool
   fault the moment the section moves and a stale `.ltorg` is missed.
2. **The trade the move buys** (§12 note 60, BUILD_FACTS.md §5): `load_next_byte`'s packet-byte read,
   `ldrb SHIFT_BUF,[r0]`, was 4 cycles (RAM data from flash-resident code); from RAM-resident code
   it drops to 2. This is the arithmetic v2 never ran; it is why Р4's "one rule for both halves"
   nets out favourably rather than merely being simpler.
3. **Re-pad every TX cell to the Appendix A/B targets (32/64)**, using the staircase T2 seeded,
   extended to N up to 64 for the SE0 pad (B11's target, `--gate-se0 60:72`). **Every pad is a
   formula in `usb_port_py32_tune.h` parameterised on `USB_B_TAKEN` and `USB_L_LITERAL`** (the §9.6
   request for "T2 step 4", honoured — the TX re-pad is where that request actually lands, since
   T2's RX pads at `USB_B_TAKEN=2` already fit with zero pad room to spare): e.g. B1's pad is
   `18 − 2·USB_B_TAKEN − USB_L_LITERAL` `nop`s or a staircase entry, not the integer 12.
   Store-index invariants from Appendix A's pad-site map (pre_and_tok zero/one equal store index;
   send_inner zero-path store index stays 10; stuffed store index = 32 + 10) are asserted by the
   walker's path list, not by eyeballing the table.
4. **Per-part `#if` review, corrected from v2.** All five `#if PY32F002Bx5` sites
   (`arm.S:402, 415, 444, 490, 530`) are, by BUILD_FACTS.md §8, pure cycle padding with no register or address
   difference — re-derive their pad counts under the moved-to-RAM cost model (they were tuned
   against the flash column; RAM changes `push/pop` and RAM-data costs under them).
   **The `.ifeq (pre_and_tok_send_inner_loop - usb_send_data) & 2 / .error` alignment assert at
   the `#else` (F003/F030) arm is preserved, not deleted** — reversing v2's step-4 instruction to
   delete it. BUILD_FACTS.md §8 found it has never been evaluated by the branch's own build and passes today;
   deleting a correctness guard just because bench2 (T6, K7/K8) might show RAM alignment doesn't
   matter would convert a silent timing break into a silent build success — exactly backwards
   from D-5's lesson. If K7/K8 show alignment does matter, `.balign 4` on loop heads is the fix
   (§10 R4); the assert stays regardless, mirrored onto whichever arm needs it once both are
   re-padded.
5. Extend the walker's path list (`usb_port_py32_asm.h`) with the TX paths B1-B12, C1-C4, and the
   trailing-stuff-bit case (OQ11) so `tools/py32_cyc.py` covers RX and TX in one run.

Accept (static):
1. Assembles for both `MCU`s; `arm-none-eabi-objdump -h` shows `.timecrit` now containing every
   RX **and** TX symbol, `.text` reduced to the dispatch tail only (D-1's fix site).
2. `python3 tools/py32_cyc.py <elf>` exits 0: every TX path (B1-B12) = 32 or 64, C1-C4
   (turnaround) reported, keepalive still ≤ 96, no regression on any RX path from T2.
3. Every `ldr rN,[pc,#imm]` reached from `.timecrit` resolves inside SRAM (T14's rule), including
   the relocated `.text+0xda` site.
4. `grep -c '.ifeq' rv003usb/rv003usb-arm.S` ≥ 1 **and** the F003/F030 arm still assembles clean —
   the assert is present and still passes, not removed (reversal of v2, noted in the commit
   message).
5. `grep -c '#if PY32F002Bx5' rv003usb/rv003usb-arm.S` = 5 (all five sites retained, re-padded,
   not deleted); the two `MCU` engine objects still differ (`cmp` exits 1, D-3's selection stays
   exercised).
6. `grep -c 'USB_L_LITERAL\|USB_B_TAKEN' rv003usb/py32/usb_port_py32_tune.h` ≥ 2 (both pad
   parameters present).
7. `.timecrit` total size ≤ 960 B (T1's ceiling; RX 252 + TX 512 measured today, ≈196 B of
   staircase/pad headroom).

Size: one session, hard — the literal-pool bookkeeping in both directions (step 1/2) is the part
that must not be rushed.

**T5 — DFU bootloader for PY32 — Opus — [MIT-attrib: joyboot boot counter] [GPL-ideas-only:
micronucleus write-sleep idea only]**
*Opus: the fixed boot-word contract (Р6) and the flash-timing-register set (§10 R6/G12) both have
to match T1's ld exactly or DFU bricks a board that the app side never would.*

Owns: `bootloader_dfu/dfu_py32.h`, `bootloader_dfu/py32/{Makefile, bootloader.c, dfu_chip.h,
dfu_transport.h, usb_config.h, funconfig.h, py32-dfu-bootloader.ld}`, `bootloader_dfu/README.md`,
`tools/wg015mkdfu.py`.

Entry: T1, T2, T3 landed. (The transport's turnaround budget is only exact once TX is also in
`.timecrit`, i.e. after T2T — both are wave 3, so T2T lands before this task's acceptance is
scored regardless; no new dependency edge, since T2T is serialised on T2's files, not a separate
input.)

Does: Р6 (mechanism unchanged, per-part numbers move with the flip). `usb_config.h` = copy of
`bootloader_dfu/wg015/usb_config.h` with PY32 pins (T1's README pinout), `wTransferSize 0x80`,
`bcdDevice 0x0210`, serial `"P32D"`, `USBDESC` to RAM (same rule as T4). `Makefile` wraps
`Makefile.py32` (`TARGET=bootloader`, `LDSCRIPT=py32-dfu-bootloader.ld`, hard `SIZE_BUDGET` via ld
`FLASH LENGTH`: 4096 on F030x6/F003x6, 4096 on F002Bx5, soft-warn at 3800, printed like
`bootloader_dfu/wg015/Makefile:14-22`). `py32-dfu-bootloader.ld` includes `py32_common.ld` with
`FLASH ORIGIN 0x08000000 LENGTH 4096`, RAM per-part unchanged from the app scripts (T1) so
`py32_boot_flag` etc. resolve to the **same address** in loader and app.
`dfu_port_flash_timebase_init()` writes the flash-timing register set matching the HSI mode
actually in use (§10 R6/G12 — F030 at `LATENCY=1`/HCLK=2×HSI is a set the datasheet does not
explicitly enumerate for that path, flagged not guessed) and enables `TICKINT` on the
free-running SysTick; `SysTick_Handler` (priority 3, `dfu_wraps++`) in `bootloader.c`.
`DFU_ENABLE_BOOTCOUNT 1` on F030 / `0` on F002B in `dfu_chip.h` (R17's false-STAY risk stays
smaller on the primary part, whose apps call `py32_app_alive()` from T3's default hook).
`tools/wg015mkdfu.py` gets `--bcddevice`, `--pid`, `--vid` options, defaults unchanged (V003/WG015
output is bit-identical unless a flag is passed).

Accept (static): builds for `MCU=PY32F030x8` and `PY32F002Bx5`; `sizecheck` passes both; `nm
bootloader.elf | grep ' py32_boot_flag$'` prints the **same address** as T1's `selftest.elf` for
the same `MCU` (shared boot words, Р6 — the whole point of the ld-`PROVIDE` scheme); `nm` shows
`dfu_status`/`dfu_upload_buf` at SRAM addresses; `grep -n 'DFU_POLL_ERASE_MS\|DFU_POLL_PROG_MS'
bootloader_dfu/dfu_py32.h` shows `12` for both, `DFU_CYCLES_PER_MS` shows `47980` on F030 (Р9's
corrected constant, §3.2 — "not 48000") and `48000` on F002B (post-T12 calibration target);
`! grep -rn 'OPTR\|RDP' bootloader_dfu/dfu_py32.h bootloader_dfu/py32/`; `grep -rn
'SysTick->LOAD' bootloader_dfu/dfu_py32.h bootloader_dfu/py32/` empty; `python3
tools/wg015mkdfu.py --selfcheck` and `--bcddevice 0x0210` produce a suffix with the new value;
V003/WG015 DFU builds unchanged.

Size: one session, hard.

**T7 — Build integration, CI, top-level docs — Sonnet — closes the build-system half of DEFECTS_VERIFIED.md D-3
and BUILD_FACTS.md §10.1**
*Sonnet: wiring existing targets together — except item 3 below, which exists because a sibling
build already got bitten by exactly the failure mode it guards against.*

Owns: `Makefile` (top), `.github/workflows/build.yml`, `.gitignore`, `README.md` (top).

Entry: T1, T2, T4, T5, T14 landed (§9.3's edge table — `check-cycles` needs T14's walker, which is
a wave-1 artefact; T2T is not a separate input, see T5's note).

Does:
1. `PROJECTS_PY32 := demo_gamepad demo_hidapi bootloader_dfu/py32`; `build_py32:` loops
   `$(MAKE) -C $$d -f $(abspath rv003usb/py32/Makefile.py32) MCU=$$mcu` for `MCU in PY32F030x8
   PY32F002Bx5`; `check-cycles:` runs `tools/py32_cyc.py` on every PY32 ELF; `all: build
   build_py32 check-cycles`.
2. **Per-part build hygiene (BUILD_FACTS.md §10.1).** The build this port replaces (vendor `rules.mk`, via
   `py32f0-template`) reaches sources through `../`, so objects land outside `Build/`, carry no
   `MCU_TYPE` in their path, and `rm -rf Build` does not clean them — a part switch can silently
   relink objects compiled for a different part. That build is gone (T0 removes the submodule,
   T1 writes `Makefile.py32` from scratch), but `build_py32`'s own MCU loop is the one place in
   this port's build where the same failure mode could still reappear if `Makefile.py32`'s object
   directory is not keyed by part. T7 does not own `Makefile.py32` (T1's file, already landed), so
   the fix at this layer is defensive rather than structural: `build_py32` runs
   `$(MAKE) -C $$d -f … clean` immediately before each per-`MCU` build in the loop, guaranteeing
   no cross-`MCU` object survives from a previous iteration regardless of how `Makefile.py32`
   names its build directory. T7 also appends a request to `doc/py32/STATE.md` (T8's file, the
   one shared append-only exception, §9.1) asking `Makefile.py32` to key `BDIR` by `$(MCU)`
   directly, since a clean-before-build in the CI loop is a mitigation, not the permanent fix.
3. CI installs `gcc-arm-none-eabi` (no OpenOCD/pyOCD — nothing here needs hardware) and runs
   `make build_py32 check-cycles`. README gets a "PY32 / Cortex-M0+" section (targets, pins,
   clock options incl. the no-servo-at-reset F030 default, D± drive per Р8, loader, limits, IRQ
   policy, SysTick rule, the RAM budget table from T1).

Accept (static): `make all` green locally; CI yaml parses (`python3 -c "import yaml,sys;
yaml.safe_load(open('.github/workflows/build.yml'))"`); `grep -c 'check-cycles' Makefile
.github/workflows/build.yml` ≥ 2; `grep -c 'PY32F030x8' .github/workflows/build.yml Makefile` ≥ 1
each (the flipped default is exercised in CI, not only locally); **stale-object regression**:
`make build_py32` for `MCU=PY32F030x8` alone, record the RAM/flash totals reported by
`--print-memory-usage`, then run the full `build_py32` loop (both MCUs) without an intervening
manual `clean` — the F030x8 totals in the second run must be byte-identical to the first (proves
the loop's own `clean` step, not luck, produced correct per-part objects); `grep -c 'requests' -A5
doc/py32/STATE.md` shows the `Makefile.py32` `BDIR` request with a citation to `BUILD_FACTS.md §10.1`.

Size: one session.

**T9 — HID blob loader for PY32 — Opus**
*Opus: the blob table and the address guard are the one place a bug bricks a board with no
recovery path faster than a full re-flash.*

Owns: `bootloader_py32/{bootloader.c, Makefile, usb_config.h, funconfig.h,
py32-usb-bootloader.ld, blobs/Makefile, blobs/blob_erase_page.S, blobs/blob_program_page.S,
blobs/blob_read_chunk.S, blobs/blob_boot_app.S, blobs/blob_rescale_timings.S}`; modify
`bootloader_wg015/wg015hostcli/wg015bflash.c` + its `README.md` (accept `bcdDevice 0x0210`, a
Thumb blob table, page = 128 B, unit = page).

Entry: T5, T2 landed (the engine, and T5's proof that the DFU transport works over it).

Does: port of `bootloader_wg015/bootloader.c` with RV32-specific mechanisms swapped for their
PY32 equivalents — `RTC_REG` boot words → the ld-`PROVIDE`d top-of-RAM block (Р6, same scheme as
T5), `rdcycle` → free-running SysTick (Р9), PLIC teardown → `NVIC_DisableIRQ` + `EXTI->PR` ack —
with the shared-C `RV003USB_BOOTLOADER` hooks unchanged; scratchpad at `0x20000000`, sized to the
smallest supported part's headroom (T1's floor table) rather than WG015's flat 1152 B. Blobs are
hand-written PIC Thumb (no `-fPIC`, entry at +4, address guard `< APP_BASE` refused before any
flash-controller register is touched).

Accept (static): builds for both `MCU`s; blobs ≤ 284 B each (`arm-none-eabi-size`); the CLI
refuses a loader image whose `bcdDevice` is not `0x0210`; `grep -rn 'SysTick->LOAD'
bootloader_py32` empty.

Size: one session, hard.

**T11 — Writer→reader loopback bench — Sonnet — [MIT-attrib if Pico `test_ll.c` vector logic is
copied]**
*Sonnet: the vector set and the protocol are fully specified by v2/PA; this is assembly against
an existing design, not new design.*

Owns: `py32_bench/{bench7_loopback.c, loopback_vectors.h, gen_loopback_vectors.py}`.

Entry: T2 landed (the engine — effectively after T2T too, same note as T5/T9). T6 landed (bench
framework, weak-symbol menu, Makefile glob — the seam this task drops into without touching T6's
files).

Does: two-board setup (D+↔D+, D−↔D−, common ground; board B's DPU asserted, board A's D± as
inputs with internal pull-downs so idle state is J); writer/reader roles from the UART menu.
Writer: `usb_send_data` every 1 ms over the vector set — all-0x00, all-0xFF, 0x7E/0xFE runs
(PA A-7), payloads whose CRC16 tail ends in six ones (trailing stuff bit, OQ11 — the generator
asserts ≥4 such vectors exist), one deliberate seven-ones violation (must be rejected), one
8-cycle SE0 glitch (optional), LFSR random. Reader: links the engine with its own PID handler
stubs, counts CRC-valid packets per vector id, answers ACK so the LA also measures turnaround.
`gen_loopback_vectors.py --check` regenerates and diffs the header — CI-safe, no hardware.

Accept (static): `make -C py32_bench MCU=PY32F002Bx5` and `MCU=PY32F030x8` build with `nm
bench.elf | grep -q bench7_run`; `python3 py32_bench/gen_loopback_vectors.py --check` exits 0,
reports ≥4 six-ones-tail vectors and exactly 1 stuffing-violation vector; `grep -rn
'SysTick->LOAD' py32_bench` empty. The hardware run itself and its numbers land in T10's
`calibration.md` (Wave 4).

Size: one session.

**T15 — Thumb PID handlers under `RV003USB_OPTIMIZE_FLASH` — Sonnet — closes v2's F8**
*Sonnet: a mechanical translation of two existing C handlers into hand-written Thumb using an
offset table T3 already fixed; no new design.*

Owns: `rv003usb/py32/engine_pid_handlers.S`.

Entry: T2 landed (`usb_port_py32_asm.h` macros). T3 landed (`EP_*_OFFSET` constants in
`rv003usb.h`).

Does: v2's F8 (`rv003usb-arm.S` always `blx`es the C `usb_pid_handle_ack`/`usb_pid_handle_setup`,
which are compiled out under `RV003USB_OPTIMIZE_FLASH=1` — a link error the branch never hit
because it never builds with that flag). Thumb reimplementations of both handlers, ≈40 B each,
reading `EP_*_OFFSET` (T3, `h:133-138`) directly instead of through the C struct layout, guarded
`#ifdef RV003USB_OPTIMIZE_FLASH`. Lands in `.text` (flash) — dispatch-tail code, not a timed cell
(same classification as D-1's fix site), so none of T2/T2T's cycle rules apply. Picked up
automatically by T1's `engine_*.S` Makefile glob — no `Makefile.py32` edit needed (§9.2's seam
table).

Accept (static): `make -C demo_gamepad -f ../rv003usb/py32/Makefile.py32 MCU=PY32F030x8
RV003USB_OPTIMIZE_FLASH=1` links (the link error F8 named no longer occurs); the same build with
`RV003USB_OPTIMIZE_FLASH=0` (default) shows neither handler in `nm` (the C versions are used
instead, unchanged behaviour); `arm-none-eabi-size` on the object shows ≤ 45 B per handler; DFU
configs (T5, T9 — both already build with the flag) are unaffected in address layout
(`EP_*_OFFSET` sourced from the one place, T3).

Size: one session.

#### Wave 4

**T10 — F030 bring-up rig, procedure, and calibration record — Opus**
*Opus: getting the gate order or the fallback wrong here wastes bench time on real hardware,
which nothing else in this fleet can undo.*

Owns: `doc/py32/calibration.md`, `rv003usb/py32/py32_cal_f030.mk`.

Entry: waves 0-3 landed (everything it flashes and measures). Runs before T16 in practice (T16
reuses this rig's document format) but does not block it — they are independent per §9.3.

Does — **the deliverable is the procedure and the rig, not a measurement** (per the governing
rule: no task in this fleet is gated on hardware, and this wave is no exception to that, only to
the "hardware-free acceptance" part of it):
1. `calibration.md` is a filled-in template, one section per RV/G gate in order — G0, G1, G6, G7,
   the F030 leg of G9, G10, G11, the F030 leg of G12 — each section quoting the exact yes/no
   question, board, measurement method and pass/fail line from §10A and
   the K1-K11 procedure from Appendix D **verbatim** (both are already-verified
   documents this fleet cites, not re-derives), with a blank result field and an explicit `TBD —
   requires hardware, out of scope for this task's own static acceptance` marker next to every
   field until the rig actually runs.
2. `py32_cal_f030.mk` is the seam target (T1's `-include $(PY32_DIR)/py32_cal_*.mk`, §9.2): today
   it contains only commented-out `DEFS += -DUSB_RX_SYNC_DELAY=… # from G7`-style placeholders,
   one per constant this gate sequence is expected to produce (`USB_RX_SYNC_DELAY`,
   `USB_TRIM_LOCK_N`, `USB_TRIM_FAST_SHIFT`, `USB_TX_SE0_PAD`, a `USB_STAIRCASE_C` override) —
   every one commented out, so the file changes no build until a real measurement fills it in.
3. Notes, not gates: BUILD_FACTS.md §10.2 found the vendor CMSIS library builds no PLL path at all for F003
   (`RCC_PLL_SUPPORT` undefined for that part), which directly disagrees with the measured claim
   in `CHIP_FACTS_XIAMATSU.md` §2 that the PLL locks at 48 MHz on F003 silicon. This port never
   calls the vendor library (T1 writes the PLL bring-up as direct register writes, identically for
   the whole PY32F030 family — BUILD_FACTS.md §10.3's "no owning task", "which parts get benched" gap does not
   reopen this port's clock-init design), so the vendor header's `#ifdef` does not constrain us
   either way; what remains unresolved is whether the **registers themselves** lock on F003
   silicon, which is unmeasured and out of this document's scope — F030 is the only part T10
   benches (§10 R25/§3.1 already restrict F003/F002A to an explicit
   `PY32_OUT_OF_SPEC=1` opt-in), and an F003 register-level PLL measurement is recorded as an open
   item in `calibration.md` rather than invented as a new gate this rework does not own.

Accept (static): `calibration.md` names every one of G0, G1, G6, G7, G9, G10, G11, G12 by ID and
quotes its pass condition (cross-checked against §10A's `| G` heading rows);
`rv003usb/py32/py32_cal_f030.mk` exists and, entirely commented out, changes nothing:
`make -C demo_gamepad -f ../rv003usb/py32/Makefile.py32 MCU=PY32F030x8` succeeds identically with
and without the file present (`cmp` on the two `.bin`s); `grep -c '^#' rv003usb/py32/py32_cal_f030.mk`
equals its non-blank line count (no live override ships before a real gate runs); every
unmeasured field in `calibration.md` says `TBD`, not a fabricated number (`grep -c 'TBD'
doc/py32/calibration.md` ≥ the gate count); `grep -q 'RCC_PLL_SUPPORT\|BUILD_FACTS.md §10' doc/py32/calibration.md`
(the F003-PLL open item is recorded, not silently dropped).

Size: one session for the document and the seam file; the gates themselves are hardware work,
explicitly out of this task's scope.

**T16 — F002B bring-up rig, procedure, and calibration record — Opus**
*Opus: the F002B leg carries the one open question (§10A gate G4, the 15× LSI
datasheet-vs-measurement gap) that decides whether the second target ships at all.*

Owns: `doc/py32/calibration_f002b.md`, `rv003usb/py32/py32_cal_f002b.mk`.

Entry: T12 landed (self-calibration routine to flash). T13 landed (`acquire` mode built). T10
landed (the rig's format and procedure to mirror on the second board — a documentation
dependency, not a build one; T16 does not read T10's files, it follows the same template).

Does — same discipline as T10, on the F002B-specific gate subset: G3 (like-for-like sanity at
24 MHz/LAT0 against T10's G1 result), **G4** (the self-calibration spread across ≥5 units — the
gate that decides whether R19/R21's disagreement is survivable, or whether F002B needs per-board
OTP, or is dropped), G5 (RAM column post-calibration at 48 MHz/LAT1), the F002B leg of G9 (servo
lock from up to ±3 % start, using T13's `acquire` mode) and of G12 (48 MHz flash-timing set,
B-C silicon only, RMBC p24). `calibration_f002b.md` additionally records `DBG_IDCODE` per unit
(R1's retired-risk residual) and T12's `bench8_hsical` output (winning `TRIM_L`, LSI reference
count) per unit — the artefact G4 is actually scored against. `py32_cal_f002b.mk` mirrors T10's
seam shape: commented-out `DEFS +=` placeholders for the F002B-specific constants
(`USB_TRIM_SIGN`, F002B's own `USB_RX_SYNC_DELAY` if G3/G5 diverge from F030's, a
`PY32_HSICAL_TRIM_H` override if G4 needs a different band than T12's compile-time default).

Accept (static): `calibration_f002b.md` names G3, G4, G5, G9(F002B), G12(F002B) and quotes each
pass condition from §10A; explicitly states G4's ≥5-unit requirement
and its two fallbacks (per-board OTP constant; F002B dropped as a target) so a reader cannot
mistake "ran once" for "gate passed"; `rv003usb/py32/py32_cal_f002b.mk` exists, fully commented
out, and `make -C demo_gamepad -f ../rv003usb/py32/Makefile.py32 MCU=PY32F002Bx5` is
byte-identical with and without it present; every unmeasured field says `TBD`.

Size: one session for the document and seam file; the gates are hardware work, explicitly out of
scope here, same as T10.

### 9.5 Defect map — every verified defect has an owning task

None of DEFECTS_VERIFIED.md's five defects, and none of the design gaps the plan tracks
alongside them, is left without a task that closes it. Three (D-3, D-4, D-5, and the stub servo)
are already closed by Waves 0-1's finished prose; this rework's job was the remaining two (D-1,
D-2) plus one build-system defect surfaced mid-rework by BUILD_FACTS.md §10.1.

| Defect | Source | Owning task(s) | Wave | How it is closed |
|---|---|---|---|---|
| D-1 endpoint bound off-by-one (`bhi` accepts `endp == ENDPOINTS`) | DEFECTS_VERIFIED.md D-1 | **T2** | 2 | `bhi`→`bhs`, one instruction in the untimed dispatch tail; Accept item 4 |
| D-2 RX byte store has no bound check | DEFECTS_VERIFIED.md D-2 | **T2** | 2 | bound check inside the cycle-counted path, paid for out of the existing budget, not added on top; Accept items via the walker |
| D-3 per-part `#if` variant never *selected* by the build | DEFECTS_VERIFIED.md D-3 | T1 (build matrix, already landed) + **T7** (CI matrix, stale-object regression) | 1, 3 | T1 Accept item 6 shows the two `MCU` objects differ; T7 closes the build-hygiene half BUILD_FACTS.md §10.1 exposed (per-`MCU` `clean`, STATE.md request for a permanent `BDIR` keying fix) |
| D-4 branch cannot link as published (empty `py32f0-template`) | DEFECTS_VERIFIED.md D-4 | T0 (removal) + T1 (replacement files, already landed) | 0, 1 | submodule dropped; own linker/startup/header written from RM/DS cites, not vendored |
| D-5 RAM placement of the RX engine is incidental | DEFECTS_VERIFIED.md D-5 | T1 (already landed) | 1 | named `.timecrit` section + `ASSERT(SIZEOF>0)` + `ASSERT` on its VMA + `--orphan-handling=error` (the VMA `ASSERT` alone passes vacuously on an empty section, BUILD_FACTS.md §12); regression-tested in both directions (T1 Accept item 3); corroborated a second time in a fully linked image, BUILD_FACTS.md §9 |
| Stub keepalive servo (`// TODO`, acks and measures nothing) | DEFECTS_VERIFIED.md "Not verified here" | T13 (already landed) | 1 | three-mode actuator (`off`/`drift`/`acquire`) replacing the stub, sized to what the target flip actually needs |
| New: build objects escape `Build/`, not keyed by part (a build-system near-D-3) | BUILD_FACTS.md §10.1 | **T7** | 3 | per-`MCU` `clean` inside `build_py32`'s loop, plus a request routed to T1's file via STATE.md for the permanent fix |

No defect is orphaned: every row names a task, and every task named above appears in this
rework's Wave 2-4 prose or in Waves 0-1's already-finished prose.

### 9.6 Cross-section requests raised by the cost-model rework — disposition

The cost-model rework (now §2.0, §2.1, §2.5 and Appendices A, B and D) closed with a list of
five items addressed to §9's tasks. That list was process residue between editors and is not
reproduced in this plan; what it asked for is. All five are honoured explicitly, not by silent
coincidence:

| Request | Honoured in | How |
|---|---|---|
| T2 step 2 (`.ltorg`): "now a hard rule, walker-enforced" | T2 step 2, Accept item 6 | every literal pool reached from `.timecrit` must resolve inside SRAM or the build fails (T14's rule, applied); not a style note |
| T2 step 4: "pads as formulas in `usb_port_py32_tune.h` with B as a parameter (`USB_B_TAKEN` default 2), not integers" | T2 step 11 (RX pads) and **T2T step 3** (TX pads, where "step 4" actually landed after the RX/TX split) | `USB_B_TAKEN`/`USB_L_LITERAL`-parameterised formulas in both tasks' Accept criteria (T2 item 7, T2T item 6); a build with a different `USB_B_TAKEN` measurably changes the assembled pad, proving the formula drives the number rather than documenting it |
| T6 bench1/bench2: "replaced by K1-K11 above (superset); adopt the kernel list and the two gates" | T6 (Wave 2), entirely rewritten around it | bench1/bench2 no longer exist as separate concepts — K1-K11 are distributed across `bench1_ioport.c` (K1-K3, K6) and `bench2_branch.c` (K4-K5, K7-K11); Gate 1/Gate 2 themselves are T10/T16's job (Wave 4), consistent with Appendix D's own "T10 runs them first" |
| R4/OQ4: "'taken branch 2 (TRM) vs 3 (Grainuum)' is now '2-3 measured from RAM, alignment-dependent per the source'; K7/K8/K9 close it" | T6 step 3 | K7/K8 explicitly named as "the direct test of R4/OQ4"; the reworded risk text itself lives in §10 (which the cost-model rework did not own), but the evidence that closes it is produced here |
| R3/OQ14: "dispatch in flash reads RAM data at 4/access — not timed, acceptable; note only" | T2 step 3 (D-1's fix site) | stated as a note at the one place in this rework where it is directly relevant (the dispatch tail staying in flash while RX/TX move to RAM), with no task action beyond what D-1 already required |

Size: n/a — this section records disposition, not work.

## 10. Risks

### 10.1 Register

Columns: trigger = the observation that says the risk has materialised; blast = what is lost;
evidence = level and source behind the *risk statement*; retire = the mitigation or the gate
(§10A) that closes it. Only risks specific to this port are listed.

| R | Risk | Trigger | Blast radius | Evidence | Mitigation / gate that retires it |
|---|---|---|---|---|---|
| R1 | *(retired, see 10.2)* Older F002B silicon has no 48 MHz HSI mode | — | — | — | residual (the constant, not the mode) → R19 |
| R2 | HSI drift beyond servo range / hunting over temperature; both parts | `rx.slope_cyc_per_bit` > 0.16 after lock; enumeration drops in the sweep | every HSI build | datasheet: DS030 T5-15 / DS002B T5-13 ±2 % (0–85 °C), −4/+2 % (−40–85 °C); cell margin 0.25/0.5 % (§2.4.5) | G10; slower slow-rate gain (`USB_TRIM_SLOW_SHIFT`), wider saturation; F030: HSE crystal build |
| R3 | F002B SRAM (3 KB) cannot hold RX+TX+dispatch+descriptors+staircase+DFU buffers+stack | ld `ASSERT` in T1/T5 | target #2 only | inferred from v2 §2.1 footprint (1168 B RAM for demo_gamepad with TX still in flash) | dispatch back to flash **priced with the flash column** (RAM data 4, PUSH/POP 4+1, `xm_030.md:475-476`) — bench4/OQ14 must use it, v2's "not cell-critical" is no longer free; shorter descriptors; HID loader instead of DFU |
| R4 | **Elevated.** The paper ledger (TRM Table 3-1) is wrong on this part: measured `BL` 4 (TRM 3), `BX` 3 (TRM 2), taken branch from RAM 2-3 (TRM 2), `B` 2-3 | G1 kernels ≠ TRM | every 32/64 path, every pad; the staircase `bl rv003usb_wait_N` (§7.4, arithmetic `BL` 3 + `NOP` + `MOV PC` 2) delivers N+1 if `BL` = 4 | measured: `xm_030.md:472,478-480,488-493` (one author, ≤24 MHz, LAT 0); the same claim Grainuum makes for Kinetis (PA §1 row 1, Q-11) | G1 writes the measured table into `tools/py32_cyc.py --cost-table`; staircase re-derived from the measured `BL`; no pad constant is final before G1 |
| R5 | IRQ entry outside [11,74] cycles (equal-priority ISR, long PRIMASK section, SysTick at prio 0) | bench3 spread; sporadic CRC failures | RX sync on every packet | datasheet/TRM: §2.2 window; measured PUSH from RAM 2+1·(N−1) = TRM's 1+N (`xm_030.md:491`), so the window is untouched by the measurement | G6; Р7 enforced in `usb_port_hw_setup()`; RAM vector table |
| R6 | Flash timing registers loaded for the wrong clock → mis-programmed pages | DFU readback mismatch | loader on both parts | datasheet: RM002B p33-35; which set applies on F030 when HCLK = 2×HSI via PLL is **not stated** (datasheet gap) | G12 readback; load exactly as `__HAL_FLASH_TIMMING_SEQUENCE_CONFIG` for the HSI mode in use; F002B 48 MHz set exists only on B-C silicon (RMBC p24) |
| R7 | SRAM not retained across SYSRESETREQ → boot flag/counter/double-tap lost | T10 test | DFU fast path, boot counter | datasheet gap (RM002B p56 lists registers only) — speculation either way | T10 test; fallback: STAY via `SFTRSTF` only, counter degrades to "never STAY" |
| R8 | **Elevated.** Turnaround > 7.5 bit-times: C handlers execute from flash, and from flash every RAM data access costs 4 and PUSH/POP 4+1 (`xm_030.md:475-476`), on top of LATENCY 1 at 48 MHz | `wg015vcd.py tx --gate-turnaround 7.5` fails on SETUP status / DFU GETSTATUS | enumeration, DFU | measured (flash column, ≤24 MHz LAT 0) + datasheet (LATENCY 1 above 24 MHz, RM002B p38) → the cost at 48 MHz is inferred, upward | G11; hot C path (`usb_pid_handle_in/data`) into `.timecrit`; then the ACK-first pipeline (branch_notes Part B, 3735518) |
| R9 | DFU > 4 KB on F002B | `sizecheck` | target #2 loader | inferred (WG015 DFU is 2876 B on RV32IMC, review_findings.md:28; Thumb-1 is denser, startup smaller) | `DFU_ENABLE_BOOTCOUNT/UPLOAD/APPCRC 0`; 8 KB loader, app at +0x2000 |
| R10 | Vendor documents contradict each other and the silicon; no public errata | — | every constant in `py32_min.h`, every clock assumption | measured: F002B DS says 24 MHz max, the chip runs 48 (`xm_002b.md:172`); factory 48 MHz word off by −10.2 % (R19); "SWD-Delay" known only from a community README (R24) | every number cites a page; every measurement records `DBG_IDCODE` and marking; G1–G5 before any tuning |
| R11 | Shared EXTI vector with user pins (lines 2/3, 4–15) | app enables EXTI on the USB vector | ISR livelock | datasheet: RM002B p97 | F6 hook (T2 step 7) |
| R12 | D± edge rates: lowest OSPEEDR + 33 Ω too slow into a long cable (> 300 ns) or a faster setting rings | scope in T10 outside 75–300 ns / overshoot > VDD | link reliability on long cables | datasheet gap (no tr/tf table, OQ10); Grainuum measured the ringing case (PA S-9) | raise `USB_PORT_OSPEED` one step at a time; never capacitors on D± (PA A-8) |
| R13 | **Elevated, made concrete.** F002B is a different die (shared with L020, `xm_002b.md:5`) and the cost table was measured only on the F002A/F003/F030 die (`xm_030.md:464`) | G3/G5 ≠ G1 | every F002B pad; possibly F002B as a target | measured on one die, **unmeasured** on the other (CHIP_FACTS_XIAMATSU.md §1 "ОТКРЫТО") | G3 (24 MHz/LAT 0, like for like) then G5 (48 MHz); per-MCU `--cost-table` and pad set if they differ |
| R14 | SysTick reconfigured (1 ms reload) → keepalive delta ≠ 48000 → servo silently open-loop | `delta_se0_cyccount` ≈ 0 | every HSI build | inferred (v1 loader had exactly this, Р9) | Р9 rule + `SysTick->LOAD` greps in T1/T4/T5/T6/T9/T11 |
| R15 | Servo lock slower than the host's reset→first-SETUP window (Win10/11 xHCI, USB3 ports) | enumerates on Linux, fails on Windows; keepalive count before first SETUP (OQ9) < lock time | HSI builds; F002B worst (start ≤ ±3 %, R21) | datasheet (±0.7 % start on F030) / inferred (F002B start from G4); V-USB precedent (PA A-6) | G9; `USB_TRIM_FAST_SHIFT`/`LOCK_N`; F030: HSE; F002B: OTP constant |
| R16 | GPL contamination through "translation" of LemcUSB / stm32f030-vusb / V-USB routines | `Provenance:` trailer missing; Р10 grep matches | licence of the repo | — (policy) | Р10 hard rule; revert |
| R17 | Boot-failure counter false STAY (app resets itself > 3× before `usb_setup()`) | loader stays with a healthy app | apps that never call `usb_setup()` | inferred | `py32_app_alive()` early; `DFU_ENABLE_BOOTCOUNT 0`; explicit `DFU_FLAG_APP` always wins |
| R18 | Bounded preamble spin coarsens edge detection (4/4/4/7) → sample band leaves 14–18 | `rx` histogram min < 14 or max > 18 | RX dribble margin (F5) | inferred (§2.2 arithmetic) | G7; re-derive `USB_RX_SYNC_DELAY`; 7/7/7 variant |
| R19 | **New.** F002B factory 48 MHz word (`0x1FFF0104` = 0xB3A2, `xm_002b.md:269-270`) runs the chip at 43.12 MHz, −10.2 % | loading the word as v2 T1 startup did; MCO/2 ≠ 24 MHz | F002B never enumerates; −10.2 % is outside the servo's ±4000-count (±8.3 %) sanity window (`S:762-772`), so the servo rejects every delta and never engages | measured: `xm_002b.md:172-175`, `:209-210` — **one unit, MCO/2 on a UT89X handheld** (`xm_002b.md:203`); the sign and size are far beyond instrument error | never load `0x1FFF0104` blind; HSI self-calibration stage before DPU (R21, G4), or per-board OTP constant |
| R20 | **New.** F002B trim field is non-linear: `TRIM_H` scales the range in coarse steps (+41 % at 0b0111, +50 % at 0b1000), `TRIM_L` 0x000–0x1FF spans it linearly; 48 MHz lies in the 0b1000 band (21.7–33.4 MHz × 1.50 = 32.6–50.1 MHz) | servo step crosses a `TRIM_H` boundary → ≈9 % jump → delta out of window → lock lost | F002B servo | measured: `xm_002b.md:232-257` (one unit) | servo actuates `TRIM_L` only; `TRIM_H` fixed by G4; `USB_TRIM_SAT` ±64 LSB stays inside the band; bench6 measures LSB weight *in that band* |
| R21 | **New.** LSI as the F002B calibration reference: measured −0.18 % (32.71 kHz, `xm_002b.md:204`) but datasheet 31.6–33.6 kHz @25 °C = **±3 %**, ±10 % over 0–105 °C (DS002B T5-14) | G4 spread across units > 0.5 % | F002B: enumeration then depends on the servo locking from a ≤ 3 % start inside the host's first-SETUP window (R15); > 3 % or temperature-dependent → LSI unusable | measured (one unit) vs datasheet (worst case) — the two disagree by 15×; which one describes production parts is **unknown** | G4 on ≥ 5 units; fallback: per-board constant in OTP (128 B, CHIP_FACTS_XIAMATSU.md §3) written at production; last resort: F002B dropped |
| R22 | **New.** The cost table was measured at Flash Latency 0 below 24 MHz (`xm_030.md:466`); at 48 MHz LATENCY = 1 is mandatory (RM002B p38, vendor BSP) | G1 ≠ CHIP_FACTS_XIAMATSU.md §1 RAM column | every ledger (flash column certainly changes; the RAM column *should* not — RAM has no wait states) | measured at LAT 0; the 55–86 MHz run-from-RAM test reports "нет тактов ожидания, доступ к портам на полной скорости" (`xm_030.md:440-452`) but measured no per-instruction costs → 48 MHz validity is **inferred** | G1 is the first hardware step; nothing is padded before it |
| R23 | **New.** Literal-pool load from flash costs 4 cycles when the code runs from RAM (`xm_030.md:490`); App. B prices `[pc,#…]` at 2 | any `ldr rX,[pc,#n]` in `.timecrit` whose pool is outside SRAM (missing `.ltorg`, assembler placing a pool after `.popsection`) | silent +2 per occurrence per cell; walker (which does not know where the pool landed) reports 32 while the wire shows 34 | measured (`xm_030.md:490`) | T2 rule `.ltorg` after each block; mechanical gate (add to T2 acceptance / `check-cycles`): every PC-relative load in `.timecrit` resolves to an SRAM address (`objdump -d` + address check); constants of the bit loop in registers |
| R24 | **New.** "SWD-Delay": F002A/F003/F030 need ≈100 ms at start before the probe can attach — «ОБЯЗАТЕЛЬНА ЗАДЕРЖКА — SWD-Delay!», verified with 100 ms, absent from the vendor startup files (`xm_030.md:376-378`) | probe cannot connect after flashing a build whose startup reconfigures clocks/pins immediately | F030: recoverable via UART ISP at BOOT0 = 1 (`xm_030.md:374`); F002B has no ROM loader (§3.5) — recovery only by power-on erase with a CMSIS-DAP (`xm_030.md:379-380`) | measured by the community author, mechanism **undocumented** | T1 startup keeps a 100 ms window before clock/pin reconfiguration on bring-up builds; a known-good recovery procedure is executed once on each part before any USB work (G0 precondition of §10A) |
| R25 | **New.** F003/F002A reach 48 MHz on the same PLL path (`xm_030.md:336`) but their datasheets say 32/24 MHz max (§3.1) | someone ships a product on F003 at 48 MHz | out-of-spec deployment | measured (works) vs datasheet (unspecified) | the build accepts `MCU=PY32F003*`/`PY32F002A*` only with an explicit `PY32_OUT_OF_SPEC=1`; F030 is the only production part |
| R26 | **New (process).** A conclusion that overturns an engineering decision is adopted from a partial reading of its source | a plan or facts file cites one column of a two-column measurement | one fleet run (this project: d2b4a14 adopted "RAM data 4 cycles → RAM placement wrong", withdrawn in 88d1229 after the RAM column at `xm_030.md:481-493` was read) | this repo's history (`git show 88d1229`) | rule in §10.3 |

### 10.2 Retired by measurement

| Was | Retired by | Residual |
|---|---|---|
| R1 (v2): "older F002B silicon has no 48 MHz HSI mode (`HSI_FS=101` reserved, RM002B p63)" | `HSI_FS=101` accepted and the core runs from it on a live F002B, `xm_002b.md:172-175` | the *constant* is wrong (R19); whether pre-B-C dies differ stays a datasheet question — `DBG_IDCODE` is still recorded with every measurement |
| d2b4a14 CHIP_FACTS_XIAMATSU.md §1: "RAM data costs 4 cycles → every RAM access in a cell is ruinous → Grainuum/LemcUSB/py32-branch RAM placement is the mistake" | the RAM column: RAM data 2, PUSH/POP 2+1, ports full speed, «не замедляется, как ожидалось» — `xm_030.md:481-493`; run-from-RAM at 55–86 MHz with no wait states, `xm_030.md:440-452` | the flash column is now the *cost of leaving anything in flash* (R3, R8); the literal-pool trap (R23) |
| OQ7 / R13 (v2 form): "is GPIO on the single-cycle IOPORT at all?" | `LDR/STR` to ports = 1 cycle from flash, "at full speed" from RAM, `xm_030.md:473`, `:447` | measured on the port the author used, not stated which; GPIOF on F030 still unverified → bench1 per port stays as a cheap check, no longer a risk row |
| v2 §3.1 premise that F002B is the natural first target (HSI-only, no PLL to bring up) | F002B factory 48 MHz word −10.2 % (`xm_002b.md:172-175`); F030 HSI 24 × PLL2 locks at 48 MHz on the same die as F003/F002A (`xm_030.md:336`) and is the only in-datasheet 48 MHz path | none — target order flipped (§0, §3) |

### 10.3 Process rule

A conclusion that overturns someone else's engineering decision (a prior-art author's, a
sibling agent's, or v1/v2 of this plan) is not adopted into PLAN.md, CHIP_FACTS_XIAMATSU.md or a STATE.md
request until the whole source it rests on has been re-read end to end, and the adopting text
quotes the passage that carries the overturn. The measured-cost reading of d2b4a14 cited one
column of a two-column table and survived a full plan pass before 88d1229 caught it; the rule
exists so that the next such overturn costs a re-read, not a run.

## 10A. Bring-up gates

Ordered, board-scoped, one yes/no question each, one bench measurement each. This is the section
§10.1's "gate that closes it" column points to. Every kernel-level measurement detail (firmware,
timing method, expected values) is Appendix D's job (kernels K1-K11, its "Gate 1" and
"Gate 2") — this table only fixes **when** each runs and **what happens on failure**; it does not
redefine K1-K11 or their pass values. Gates run in the order listed; none needs USB traffic, a
host, or the engine image except G6 onward.

Two things this project already knows without a bench and therefore does **not** gate here:
the toolchain is installed and both `#if PY32F002Bx5`/`#if PY32F003x4` arms assemble and the
engine links against a pinned `py32f0-template` (BUILD_FACTS.md §1-2, §6 — a build, not a
measurement); and the RAM-placement hazard of D-5 (a linker script spelling `*(.data.*)` would
silently strand `.datacode` in flash) is retired by the three-layer build-time guard of §9 T1
(non-empty `ASSERT`, VMA `ASSERT`, `--orphan-handling=error`; a VMA `ASSERT` alone passes
vacuously on an empty section — BUILD_FACTS.md §12),
not by anything a bench can fail (BUILD_FACTS.md §4, DEFECTS_VERIFIED.md D-5). That is why the
numbering below has no G2 and no G8 — both slots would have been exactly these two, and putting
either on a bench would be the "generic gate for anything" this section exists to avoid.

| G | Yes/no question | Board(s) | Measurement | Pass | On fail |
|---|---|---|---|---|---|
| G0 | Can a probe still attach after a bring-up build reconfigures clocks/pins at reset? | F030, F002B | Flash a build with immediate clock/pin reconfiguration; attempt SWD attach within the 100 ms `SWD-Delay` window (`xm_030.md:376-378`, R24); once per part, deliberately exercise the documented recovery path once | Probe attaches inside the window on both parts; the recovery procedure (F030: UART ISP at BOOT0=1, `xm_030.md:374`; F002B: power-on erase via CMSIS-DAP, `xm_030.md:379-380`, no ROM loader) actually un-bricks a board | Widen the startup delay before any clock/pin write in bring-up builds; do not touch clocks/pins earlier until this passes (R24) |
| G1 | Does the RAM column of the measured cost table hold at 48 MHz / `LATENCY=1`? | F030 (target #1) | Appendix D "Gate 1": K1-K11 at 24 MHz/LAT0 (calibrates the rig against `xm_030.md:464-493`), then at 48 MHz/LAT1 | RAM-copy kernels K1-K4, K7-K11 identical between the two runs (cycles are per-HCLK; a RAM-resident kernel touches no flash so `LATENCY` must not show up) | Re-ledger with the 48 MHz numbers via `tools/py32_cyc.py --cost-table`; no pad constant in Appendix A/B is final before this gate (R4, R22) |
| *(no G2 — settled without hardware, see above)* | | | | | |
| G3 | Does F002B reproduce the *same* table F030 gave at 24 MHz/LAT0 (like-for-like sanity)? | F002B (B-C silicon, `DBG_IDCODE` recorded) | Appendix D "Gate 2", first half: same kernel image, factory HSI24 word, `LATENCY=0` | Every kernel equals G1's 24 MHz/LAT0 F030 value | F002B is a different die for costing purposes (it shares silicon with L020, not with F002A/F003/F030, `xm_030.md:464`) → own `--cost-table` from here on (R13) |
| G4 | Can F002B trim its own HSI to inside USB tolerance from the on-chip LSI, before the D− pull-up? | F002B, ≥5 units | Run the self-calibration routine (`TRIM_L` swept against an LSI-derived reference count, `TRIM_H` held fixed per R20's band) at 25 °C; read the result via MCO/2 (R21) | Every unit lands within ±1.5 % of 48 MHz after one pass, and the LSI reference itself spreads ≤0.5 % unit-to-unit (`xm_002b.md:204` measured −0.18 % on one unit vs DS002B T5-14's ±3 % datasheet ceiling — which one describes production parts is what this gate answers) | Spread > 0.5 % or any unit out of tolerance → per-board OTP calibration constant written at production (128 B OTP, CHIP_FACTS_XIAMATSU.md §3); if that is not viable, F002B is dropped as a target (R19, R21) |
| G5 | Does the RAM column hold on F002B at 48 MHz / `LATENCY=1`, post-calibration? | F002B, same units as G4 | Appendix D "Gate 2", second half: same kernels, `HSI_FS=101` post-G4, `LATENCY=1` (mandatory above 30 MHz, `xm_002b.md:259`) | Every kernel equals G1's 48 MHz F030 RAM-copy value | F002B keeps its own `--cost-table`; if K3 = 4 from RAM there, §2.1's "RAM is favourable" conclusion is F030-only and Appendix A loses cycles on F002B that must be found (branchless EOB restructuring, R13) |
| G6 | Is IRQ entry inside the [11,74]-cycle window on real silicon? | F030, F002B (post-G3/G5 for F002B's own costs) | Bench3-style: EXTI edge → first-instruction toggle, median + spread, equal-priority ISR and SysTick-at-prio-0 deliberately excluded | Median + spread inside [11,74] on both parts, using each part's own measured `PUSH`/entry cost (G1/G5) | Enforce Р7 (RAM vector table); forbid equal-priority ISR / SysTick at prio 0 in `usb_port_hw_setup()` (R5) |
| G7 | Does the bounded preamble spin leave the dribble sample band intact? | F030, F002B | VCD `rx` sample-offset histogram under the 4/4/4/7 bounded counter, re-derived from G1/G5's measured `BL` | Histogram min ≥ 14, max ≤ 18 (of 32, F5's 260 ns dribble allowance) | Re-derive `USB_RX_SYNC_DELAY`; fall back to a 7/7/7 unrolled counter shape (R18) |
| *(no G8 — settled without hardware, see above)* | | | | | |
| G9 | Does the servo lock before the host's first SETUP? | F030 (drift only, no cold-start trim needed), F002B (post-G4, locking from up to ±3 % start, R21) | Count keepalives between reset end and first SETUP on Win10/11 xHCI, direct and behind a TT hub (OQ9); compare against measured lock time with `USB_TRIM_LOCK_N`/`_FAST_SHIFT` | Lock completes (within the ≈0.44 % sampling margin, §2.4.5) before the counted keepalive budget on every host path tested | Tune `USB_TRIM_FAST_SHIFT`/`LOCK_N`; F030 fallback: HSE crystal build; F002B fallback: per-board OTP constant, same as G4 (R15) |
| G10 | Does the servo hold lock across temperature? | F030, F002B (HSI builds only) | Hair-dryer/freezer sweep with the servo active, `rx.slope_cyc_per_bit` sampled continuously | `rx.slope_cyc_per_bit` ≤ 0.16 throughout, enumeration never drops | Slower slow-rate gain (`USB_TRIM_SLOW_SHIFT`), wider saturation window; F030 fallback: HSE crystal build (R2) |
| G11 | Is turnaround inside 7.5 bit-times with C handlers running from flash? | F030, F002B | `wg015vcd.py tx --gate-turnaround 7.5` on SETUP status and DFU GETSTATUS, at 48 MHz/`LATENCY=1`, flash-column costs (G1/G5's flash-copy kernels, R8's inferred-upward figure) | ≤ 7.5 bit-times on every capture | Move the hot C path (`usb_pid_handle_in/data`) into `.timecrit` (RAM); then the ACK-first pipeline restructuring (R8) |
| G12 | Do flash timing registers, loaded for the active clock, read back correctly? | F030 (HCLK = 2×HSI via PLL, R6's datasheet gap), F002B (48 MHz timing set exists only on B-C silicon, RMBC p24) | DFU program/erase/readback, reusing T10's existing 100-cycle `dfu-util` interop run (OQ13) rather than a new cycle count | Readback matches on every page across that run, on both parts | Load exactly the `__HAL_FLASH_TIMMING_SEQUENCE_CONFIG` set for the HSI mode in use; restrict the 48 MHz DFU path to confirmed B-C silicon on F002B (R6) |

Neither G1, G3 nor G5 needs USB traffic, a host, or the engine — they and G0 can run before any
of G6-G12. G4 gates G5 and the F002B leg of G9 (both need the calibrated HSI in hand); G1 gates
every pad in Appendix A/B (R22) before G6 can even be scored, since G6's window depends on G1's
`PUSH`/entry costs.

## 11. Open questions

v2 listed OQ1-OQ14 as "could not be verified from documents." Two documents didn't exist when
v2 was written — measurements off real chips (CHIP_FACTS_XIAMATSU.md) and a build of the
actual port (BUILD_FACTS.md) — and between them they close several of v2's questions
outright. What follows is only what survives that reading: clock- and cost-model questions those
sources cannot settle, each paired with the bench that would. Non-clock questions
(SRAM retention, EXTI latency, protocol correctness, host quirks) are not this section's
territory and are handed off at the end rather than restated.

**Closed by measurement on live silicon (CHIP_FACTS_XIAMATSU.md) — not by argument:**
* The instruction cost model, v2 OQ4's core question — measured directly on F002A/F003/F030,
  Flash Latency 0, ≤24 MHz (CHIP_FACTS_XIAMATSU.md §1, xm_030.md:464-493). What it does **not** close — the same
  table at 48 MHz/`LATENCY=1`, and on F002B's die — is restated below, not reopened as OQ4.
* Whether F002B's factory 48 MHz constant is usable, v2 OQ1 — it is not: measured 43.12 MHz
  against the 48 MHz the word encodes, −10.2 % (CHIP_FACTS_XIAMATSU.md §2, xm_002b.md:172-175, :209-210).
* Whether **F030** reaches 48 MHz cleanly — yes: HSI 24 MHz × PLL2 = 47.98 MHz measured,
  −0.04 % (CHIP_FACTS_XIAMATSU.md §2, xm_030.md:15). **F003 is not included in this closure** — see the PLL-support
  question below (BUILD_FACTS.md §10.2), which contradicts the same measurement's F003 claim.
* Whether running code from RAM is slower than from flash — it is not: «не замедляется, как
  ожидалось» (CHIP_FACTS_XIAMATSU.md §1, xm_030.md:481); ordinary instructions cost 1 cycle either way.

**Closed by experiment in this container (BUILD_FACTS.md) — not by measurement on silicon:**
* Whether the toolchain can build the port — yes, `arm-none-eabi-gcc` 13.2.1, both per-part
  `#if` arms assemble, rc = 0 (BUILD_FACTS.md §1-2).
* Where the engine actually executes from — RX from RAM in `.datacode` (252 B), TX from flash
  in `.text` (512 B) (BUILD_FACTS.md §3).
* How `.datacode` reaches RAM at all — absorbed by the stock script's `*(.data*)` wildcard,
  confirmed by linking: VMA 0x20000000 / LMA 0x08000200 (BUILD_FACTS.md §4).
* Whether the register map is portable across the families — byte-identical: GPIOB
  0x50000400, EXTI 0x40021800, `IDR` +0x10, `BSRR` +0x18, same `GPIO_TypeDef` field order
  (BUILD_FACTS.md §7).
* What the five `#if PY32F002Bx5` sites in the engine differ over — pure cycle padding, none
  touches a register (BUILD_FACTS.md §8).
* Whether `demo_gamepad` builds end to end, from a clean tree, for every candidate part — yes,
  all three link: F030x8 2128/8K RAM, 2908/64K flash; F003x4 1616/2K RAM (**78.91 %**), 2132/16K
  flash; F002Bx5 1168/3K RAM, 2696/24K flash (BUILD_FACTS.md §9).
* Whether 2 K of RAM is enough on F003x4, the smallest member of the newly-primary family — not
  an assumption any more: ≈432 B free after the demo, including a tunable 192 B RAM vector table
  and a 1028 B heap/stack reservation, before this port's own additions (BUILD_FACTS.md §9). What remains
  open is only whether *this port's* footprint fits in that 432 B, a budgeting task for T1/T5,
  not a question this section poses.
* Whether the RX engine really executes from RAM in a fully linked image, not just a synthetic
  one — yes: `EXTI2_3_IRQHandler` at 0x200000c8, `preamble_loop` at 0x200000e6, `bit_process` at
  0x20000142, `rxbuf` at 0x2000023c, all RAM; `usb_send_data` at 0x0800022c, flash (BUILD_FACTS.md §9).

These two groups are not the same kind of evidence and do not substitute for each other: the
first is a number read off a running chip, the second is a compiler and linker doing their job
in a container. Neither substitutes for the bench gates below, which run on this port's own
image, on this port's own silicon.

**Genuinely open — cannot be settled from documents in hand:**

| OQ | Question | Bench | Expected | A mismatch means |
|---|---|---|---|---|
| — | **Does F003 actually have a usable PLL at all?** The vendor CMSIS headers and the measured source flatly disagree, and this bears directly on §6 Р5's flip. `RCC_PLL_SUPPORT` is defined for `py32f030x6`/`x8` only, not for `py32f003x4/x6/x8` or either F002 part; `BSP_RCC_HSI_PLL48MConfig()` — exactly the HSI24×PLL2 path the flip depends on — lives inside `#if defined(RCC_PLL_SUPPORT)` (`py32f0xx_bsp_clock.c:57-89`), so **the vendor library will not compile a PLL path for F003 at all** (BUILD_FACTS.md §10.2). CHIP_FACTS_XIAMATSU.md §2 cites the opposite as a measurement: «Проверено — PLL запускается на 48 МГц на чипах PY32F002A и PY32F003» (xm_030.md:336). Entangled with a second fact: `demo_gamepad.c:15-23` configures the clock only `#if PY32F002Bx5` / `#elif PY32F030x8` — an F003 build takes neither arm, links, and runs at whatever `SystemInit()` leaves, not 48 MHz (BUILD_FACTS.md §10.3) — so it is also open whether F003 at 48 MHz has ever actually been *run*, as opposed to measured by someone else's rig | On an F003 part, write the PLL registers by hand (bypass the LL library) and measure the resulting clock; MCO/PA7 tops out at ≈35 MHz (CHIP_FACTS_XIAMATSU.md §3) so measure through a divider | The PLL locks at 48 MHz on F003 by direct register access, matching CHIP_FACTS_XIAMATSU.md §2's claim | If it does **not** lock: the header is right, CHIP_FACTS_XIAMATSU.md §2's F003 claim does not hold on this die/marking, and §6 Р5's flip loses F003 as a "primary family" member — F030 becomes the only in-scope 48 MHz-by-PLL part and F003 drops to F002B's tier (out-of-datasheet, no clean path). If it **does** lock: the port must bring the PLL up against registers directly on F003, since the vendor library never will, and R25's `PY32_OUT_OF_SPEC=1` gate needs a register-level clock init, not the BSP call §6 Р5 assumed |
| OQ4 | Does the measured cost table — including `BL`=4 and the (unmeasured) return — hold at 47.98 MHz with `LATENCY=1`? At ≤24 MHz/`LATENCY=0` it is measured (CHIP_FACTS_XIAMATSU.md §1); at the target clock it is not | Appendix D "Gate 1" = §10A G1: kernels K1-K11 on F030, run at 24 MHz/LAT0 (must reproduce CHIP_FACTS_XIAMATSU.md §1's numbers — calibrates the rig) then at 48 MHz/LAT1; K10 fixes the staircase constant `C` | RAM-copy kernels K1-K4, K7-K11 identical between the two runs (cost is per-HCLK; a RAM-resident kernel touches no flash) | The RAM column is frequency/latency-dependent on this part → every pad, staircase entry and budget in §3.2/Appendix A-B is void until re-measured; flash-copy kernels *are* allowed to differ (that is what `LATENCY` buys) and their 48 MHz values become the flash cost model for the non-timed dispatch tail (R3/OQ14) |
| OQ-B | Was the cost table measured only on F002A/F003/F030's die? F002B is a different die, shared with L020 (CHIP_FACTS_XIAMATSU.md §1 "ОТКРЫТО") | Appendix D "Gate 2" = §10A G3 (24 MHz/LAT0, like-for-like) then G5 (48 MHz/LAT1, post-calibration) | Every F002B kernel equals F030's value at the same clock/latency | F002B needs its own `--cost-table`; if `ldr/str` to RAM (K3) comes back 4 instead of 2 there, §2.1's "RAM is favourable" conclusion is F030-only, and Appendix A loses cycles on F002B that must be found (branchless EOB restructuring, §10 R13) |
| — | Is the "2-3 cycles" taken-branch cost a fixed constant, or does it depend on alignment / the previous instruction, as the author warns — «зависит от выравнивания и зависимости от предыдущей инструкции» (xm_030.md:468-469)? This also stands in for the fetch-width question (16 vs 32-bit, unknown on PY32, TRM p2-2 §2.2.1) that v2's OQ4 asked and no single kernel answers directly | K7 (`b .+2` at a 4-byte-aligned address) vs K8 (same, at an odd halfword) — part of Gate 1 above | K7 == K8, and each is stable across the 16 repeats | K7 ≠ K8 → alignment matters from RAM → `.balign 4` on every loop head and branch target, and the walker must carry `B` as a range, not a constant (R4; already anticipated in §3.2 Consequence 5) |
| — | Can F002B's HSI actually be trimmed to inside USB tolerance from the on-chip LSI before the D− pull-up, given the LSI itself is characterized on one unit only (−0.18 %, xm_002b.md:204-206) against a ±3 % datasheet ceiling (DS002B T5-14) — a 15× spread between the two numbers with no way from the documents to say which describes production parts | §10A G4: ≥5 units, `TRIM_L` swept against an LSI-derived reference at 25 °C, `TRIM_H` held fixed per the R20 band | Every unit lands within ±1.5 % of 48 MHz after one pass; the LSI reference itself spreads ≤0.5 % unit-to-unit | Spread > 0.5 % or any unit out of tolerance → a per-board OTP calibration constant is required (128 B OTP, CHIP_FACTS_XIAMATSU.md §3); if that is not viable in production, F002B is dropped as a target (Р5 decision 4) |
| OQ7 | Are all GPIO ports on the single-cycle IOPORT, or only the one Xiamatsu happened to use? Port F on F030 and the F002B ports are not separately confirmed | K1 (`ldr r0,[r1,#0x10]` against GPIOA/GPIOB/GPIOF) — part of Gate 1/2, per-port | K1 = 1 cycle on every port tested, both parts | A port ≠ 1 cycle → the one-sample-per-slot structure loses its P = 1 assumption on that port; the sample structure must find the lost cycle elsewhere before that port is used for D± |
| — | Does crystal-less HSI drift stay inside the sampling margin (≈0.44 %, §2.4.5) over the *whole* operating envelope on F030/F003, not just at room temperature — and specifically, over supply voltage, which neither datasheet tables (temperature only) nor any gate currently sweeps | §10A G10 sweeps temperature (`rx.slope_cyc_per_bit` under a hair-dryer/freezer run); **it does not sweep VDD** | `rx.slope_cyc_per_bit` ≤ 0.16 across temperature *and* the datasheet's VDD range | G10 as written can pass while a VDD-dependent drift still exists in the field, because it never varies VDD — this is flagged here as an ungated gap, not answered; closing it means adding a VDD sweep to G10 (or a G10b), which this section cannot do on its own authority |
| OQ3 | `HSI_TRIM` LSB weight, sign and monotonicity of the 13-bit field, inside the `TRIM_H` band 48 MHz falls in (R20) | bench6, run as part of G4's sweep, read via MCO/2 | Monotonic, sign consistent, LSB weight small enough that the servo can capture ±1.5 % without crossing a `TRIM_H` boundary (R20) | Wrong-sign or non-monotonic → the servo's actuator law (Р5 decision 6) needs the sign/gain reworked before it is trusted on F002B |
| OQ6 | 002B "Load Flash" boot zone: option-byte programming flow, erase-protection reliability, RDP lock-out risk (PA A-11) — a brick-proof loader alternative to Р6, not clock-critical but only relevant on the part this flip demoted to target #2 | Read RM002B p20-21/p42; try on hardware once DFU already works, using G0's recovery procedure as the safety net if it bricks | A programming/erase sequence exists that cannot lock the part out under any observed fault | If RDP lock-out is reachable from a partial write, this stays a documented "do not implement" rather than a shipped feature |

**Caution for whoever runs the benches above, not a question of its own:** this build system
places objects outside `Build/` and does not key them by part, so switching `MCU_TYPE` and
rebuilding without a full `find . -name '*.o' -delete` silently reuses objects compiled for the
previous part (BUILD_FACTS.md §10.1) — it produced a false "F030 does not build" during this very
investigation. G1/G3/G5's K1-K11 kernels are exactly the kind of per-part rebuild this bites;
clean fully between parts or the gate result is worthless, not merely wrong.

**Mooted, not closed, by the Р4 decision:** v2's argument against TX-in-flash raised "a TX cell
whose cost is unmeasured is not a cell one can pad" (an earlier draft of that rework called it OQ15).
Р4's final decision moves TX into RAM alongside RX, so the unmeasured flash column no longer
prices a timed bit cell — the question evaporates, it is not answered. The flash column still
prices the *non-timed* dispatch tail that Р4 leaves in flash by design (§3.2 Consequence 3); that
is R3/OQ14's residual, tracked in Appendices A/B and §10, not restated
here.

**Superseded, not restated:** v2 OQ9 (keepalive count before the host's first SETUP) is now
§10 R15, gated by G9; both parts are in scope there, F002B starting from
the post-G4 calibrated value rather than the factory word. Nothing in this section adds to it.

**Not this section's territory, and said once so nothing is silently dropped:** v2 OQ2 (SRAM
retention across `SYSRESETREQ`) is §10 R7; OQ5 (EXTI entry latency) is R5,
gated by G6; OQ10 (D± edge rates) is R12; OQ13 (`dfu-util` interop) keeps its label and is the
measurement inside G12; OQ14 (dispatch-in-flash cost) keeps its label as R3's residual. OQ8 (5 V
I/O tolerance) was already dismissed by v2 itself as irrelevant at this plan's 3.3 V-only design
and needs no gate. OQ11 (trailing stuff bit after a six-ones CRC) and OQ12 (macOS `GET_STATUS`
quirk) are protocol-correctness questions, not clock or cost-model ones; they were not picked up
by any part of this rework and stand exactly as v2 left them, unowned.

## Appendix A — Paper ledger of the branch engine (RAM-execution column)

Paper ledger, house format (LS:5-6): the value is the f(B, L, D) formulas and the pad map;
absolute numbers are recalibrated by the Appendix D benches. Costs per §2.0. "Slot" runs sample → sample
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
(`bl` 3 + `mov pc,lr` 2). Measured `bl` = 4 (CHIP_FACTS_XIAMATSU.md:26); the return is `mov pc,lr` (unmeasured,
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

## Appendix B — cycle walker (seed for `tools/py32_cyc.py`), two-column model

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

Cost table (`--cost-table cost.json`, R4; the defaults below are §2.0's RAM column with the v2
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

## Appendix C — T0 conflict resolution table

| File | HEAD side | 0ad3c42 side | Resolution |
|---|---|---|---|
| `.gitignore` | `.vscode .pio *.elf *.map *.lst bench.bin demo_*.bin bootloader.bin bootloader_wg015/blobs/*` | `*.bin *.d *.elf *.hex *.lst *.map *.o Build/` | HEAD + `*.o *.d Build/` |
| `.gitmodules` | `ch32fun` | `ch32v003fun` + `py32f0-template` | HEAD; remove gitlink |
| `Makefile` | PROJECTS incl. `testing/demo_xinput`, `$(MAKE)` | `PROJECTS_PY32`, `make -f ../Makefile.py32` | HEAD |
| `demo_gamepad/demo_gamepad.c` | `#include "ch32fun.h"` | BSP includes + `BSP_RCC_*Config()` | HEAD |
| `demo_gamepad/usb_config.h` | flag block (HEAD) | flags + `RV003USB_USE_REBOOT_FEATURE_REPORT 0` + pin ladder | HEAD (T4 re-adds pins) |
| `rv003usb/rv003usb.c` | `ch32fun.h` + terminal guard; WG015 `usb_setup` | LL includes; `#if __riscv` forks | HEAD |
| `rv003usb/rv003usb.h` | terminal default | `USB_DM_IRQ` block | auto-merged; keep both |
| new files | — | `rv003usb-arm.S`, `Makefile.py32`, `.vscode/*`, `py32f0-template` | keep `rv003usb-arm.S` only |

## Appendix D — Bench gates for the cost table (adds to T6 bench1/bench2; T10 runs them first)

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
`HSI_FS=101` with `LATENCY=1` (the factory 48 MHz word gives 43.12 MHz, CHIP_FACTS_XIAMATSU.md:60 — irrelevant
here, counts are per HCLK; LATENCY must be 1 above 30 MHz, xm_002b.md:259 via CHIP_FACTS_XIAMATSU.md:94). Pass:
every kernel equals Gate 1's F030 RAM-copy value. Fail: F002B gets its own `--cost-table`
(`Makefile.py32` selects it by `MCU`), Appendix A is re-evaluated in that column, and if K3 = 4
from RAM on F002B the "RAM is favourable" conclusion of §2.1 is F030-only — the 002B ledger
then loses 2 on A13/A14 and must find them (the `b .+2` → `nop nop` rewrite frees 0; the
structural option is the branchless EOB of `rx-tx-branchless-ch32v003-rebased`, branch_notes
Part B). If K7/K8 differ from F030, B is per-MCU in the walker.

Neither gate needs USB traffic, a host, or the engine; both run in T10 before the first
enumeration attempt and their numbers go into `doc/py32/calibration.md` next to
`DBG_IDCODE`, HCLK and LATENCY.

## 12. Changelog vs the pre-prior-art plan

Items 1-56 are v2's `§12` (v1 → v2, driven by `PRIOR_ART.md`) — **unchanged**, see
PLAN.md at 88d1229 or `git show 88d1229:doc/py32/PLAN.md` for the full text; they are not
reproduced here to avoid re-stating 56 lines that this rework did not touch. This rework
(v2 → this revision) is driven by `CHIP_FACTS_XIAMATSU.md` (measured silicon),
`BUILD_FACTS.md` (verified by building in this container) and
`DEFECTS_VERIFIED.md` (verified in source); it adds:

57. §0: primary target flipped from F002B to F030/F003 — HSI 24 MHz × PLL2 measured at
    47.98 MHz (−0.04 %), inside USB tolerance with **no servo needed at reset**; F002B demoted
    to second target, gated on an HSI-vs-LSI self-calibration stage before the D− pull-up
    (new §10A gate G4) — source: CHIP_FACTS_XIAMATSU.md §2 (`xm_030.md:336`; `xm_002b.md:172-175`, `:209-210`, `:269-270`).
58. §0/§10 (Р4, RAM placement of the engine): **re-examined and survived**, not overturned —
    confirmed twice over. First by Xiamatsu's own-silicon measurement (RAM data access 2 cycles
    not 4, `PUSH`/`POP` 2+1, GPIO ports "at full speed" from RAM, "не замедляется, как
    ожидалось", `xm_030.md:481-493`, `:447`). Second, independently, by linking the actual engine
    object against the stock `py32f003x4.ld` and observing `.datacode` — the whole hard-real-time
    RX sampling path — actually land at VMA `0x20000000` / LMA `0x08000200` (BUILD_FACTS.md §3-4). A draft of
    this rework (commit d2b4a14) briefly concluded the opposite from one column of the same
    source table ("RAM data 4 cycles → RAM placement is the mistake"), withdrawn in 88d1229
    once the RAM column was read; that mistake is now §10.3's process rule (R26) — source: CHIP_FACTS_XIAMATSU.md §1;
    BUILD_FACTS.md §3-4; `git show d2b4a14`, `git show 88d1229`.
59. §3.2/Appendix B: the earlier flat assumption "flash execution is the expensive case, RAM is
    the cheap one" does not hold — the costs **swap by location** (flash: RAM-data 4,
    `PUSH`/`POP` 4+1(n−1), literal-pool 2; RAM: RAM-data 2, `PUSH`/`POP` 2+1(n−1), literal-pool
    4). The one direction that gets *worse* in RAM is a PC-relative literal load whose pool is
    still in flash. New rule R23: every literal pool reached from `.timecrit` must resolve to an
    SRAM address, checked mechanically by disassembly, not guaranteed by construction — source:
    CHIP_FACTS_XIAMATSU.md §1 table; `xm_030.md:490`.
60. New split, not previously stated: the engine is not one execution context for costing
    purposes. The RX path runs from RAM (`.datacode`, 252 B — the entire hard-real-time sampling
    path) and the TX path runs from FLASH (`.text`, 512 B, confirmed by `objdump -h` on the
    actual object, BUILD_FACTS.md §3). Every earlier statement about "the engine"'s cost must be read as two:
    RX costed on the RAM column (§3.2 Consequences 1-2), TX and the C dispatch tail on the flash
    column (§3.2 Consequence 3, R8's turnaround risk). Flags a latent trap in the other
    direction, left open rather than decided: relocating TX to RAM would turn its own
    flash-resident literal load (`.text+0xda`) into a 4-cycle access while its current
    RAM packet-byte reads (4 from flash today) would drop to 2 — an arithmetic question for the
    ledger, not yet answered — source: BUILD_FACTS.md §3, §5.
61. D-1 (endpoint bound check): confirmed by source inspection and **reclassified from
    "possibly inherited" to branch-introduced** — the Thumb port's `bhi` (rejects only
    `endp > ENDPOINTS`) replaces the RISC-V original's correct `bgeu` (rejects
    `endp >= ENDPOINTS`) at the equivalent site; the comment on the very same faulty line still
    states the intended `<` semantics, so this is a coding slip, not a design choice. Consequence
    traced through to `eps[ENDPOINTS]`, one element past the last member of
    `struct rv003usb_internal`, reachable by any host token addressed to the boundary endpoint —
    source: DEFECTS_VERIFIED.md D-1 (`rv003usb-arm.S:274-277` vs `rv003usb.S:526-528`).
62. D-2 (RX overrun): confirmed real, already flagged by the author's own `// TODO`, but
    **reclassified from an implicit one-line fix to a design task** — `is_end_of_byte`'s
    unchecked `strb`/`add` sit inside the cycle-counted RX path (cycle-budget comments in place
    on the same lines), so any bound check must be paid for out of the bit-cell budget or moved
    off the hot path — source: DEFECTS_VERIFIED.md D-2 (`rv003usb-arm.S:145-148`).
63. "The per-part `#if` variant is never assembled" (a claim in earlier drafts) is **too strong,
    corrected**: both `#if PY32F002Bx5` arms assemble cleanly (`-DPY32F002Bx5=1` and
    `-DPY32F003x4=1` both rc=0, objects differ by 4 bytes). What is actually missing is the
    build system's *selection* of the non-default arm — `Makefile.py32` pins
    `MCU_TYPE = PY32F002Bx5` unconditionally. This matters more after item 57's target flip,
    since F003/F030 now exercises exactly the arm the branch's own build has never run — source:
    BUILD_FACTS.md §2; DEFECTS_VERIFIED.md D-3.
64. D-4 (link failure) and D-5 (incidental RAM placement) added as verified findings: the
    branch's `py32f0-template` submodule is empty so it cannot link as published (pins cleanly
    at upstream `289ffc8`); and the `.datacode`→RAM placement that item 58 confirms is not the
    product of any explicit rule anywhere in the branch or its template — it is swallowed by the
    stock linker script's `*(.data*)` wildcard by accident. A script spelling the more common
    `*(.data.*)` form would place the RX engine in flash silently, no build error, no
    diagnostic. This port's own linker script must therefore carry an explicit named RAM-code
    section guarded on three layers — `ASSERT(SIZEOF(.timecrit) > 0)`, an `ASSERT` on its VMA,
    and `--orphan-handling=error` (the full rule is in §9 T1). A VMA `ASSERT` **alone is not
    sufficient**: if the input rule ever stops matching, the output section is empty, its start
    and end are both nominally inside RAM, the assertion passes vacuously and the engine runs
    from flash anyway. Both failure modes and the three-layer guard were reproduced and
    regression-tested by linking — BUILD_FACTS.md §12. A build-time check, deliberately **not**
    one of the §10A bring-up gates — source: BUILD_FACTS.md §4, §6, §12;
    DEFECTS_VERIFIED.md D-4, D-5.
65. New §10A "Bring-up gates": turns §10.1's "gate that closes it" column into an
    ordered, citable sequence of yes/no hardware measurements (G0-G12, skipping G2 and G8 —
    items 66 and 64 respectively); defers the cost-table measurement detail to
    Appendix D (kernels K1-K11,
    its Gate 1/Gate 2) instead of inventing a parallel scheme — source: §10.1 of this plan
    and Appendix D.
66. Toolchain/link viability (`arm-none-eabi-gcc` 13.2.1 present; both MCU arms assemble; the
    engine links once `py32f0-template` is vendored/pinned) is recorded as a build fact and
    explicitly **excluded** from §10A — it needed no hardware and was already settled by
    building in this container — source: BUILD_FACTS.md §1-2, §6.
67. Splice bookkeeping (T0). The four rework fragments in `doc/py32/rework/` were merged into
    this file and are kept there, unedited, as the provenance record. Where they landed:
    `ledger.md` → new §2.0 (the cost model, cited by the other blocks as their "§0"), §2.1, the
    cycle-cost annotations of §2.5, Appendix A, Appendix B, and Appendix D (its own "§5", renamed
    because that number is taken by §5 Gaps versus WG015); `risks_verdict.md` → §0, §10, the new
    §10A, §12; `target_clock.md` → §3.1, §3.2, §6, §11; `tasks_waves.md` → §9 in full, including
    §9.0-§9.6. Two things did **not** come across: the fragments' own preambles (splice
    scaffolding), and `ledger.md`'s closing "Requests to owners of sections I do not own", which
    is process residue between editors — its five items are dispositioned in §9.6, which records
    where each landed. Cross-references were rewritten to this file's numbering; references to
    documents that stay standalone now name the file (`BUILD_FACTS.md §n`,
    `CHIP_FACTS_XIAMATSU.md §n`, `DEFECTS_VERIFIED.md D-n`) instead of the fragments' two-letter
    shorthands, and §1 gained a row for each of those three documents plus the `PLAN:<n>` /
    `LS:<n>` cite forms the blocks use. Section numbering is unchanged: §2.0, §10A and Appendix D
    are suffixed additions, so every existing citation into §1-§8 and Appendices A-C still
    resolves. No renumbering — source: this splice.
