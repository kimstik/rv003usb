# PLAN.md rework — §0 verdict, §10 risk register, bring-up gates, §12 changelog

Replacement blocks for `doc/py32/PLAN.md` (v2, 2026-09-04). Each block starts with a
`## REPLACES …` or `## NEW SECTION …` line and is spliced in whole; nothing outside the named
sections is touched by this file. Written from `CHIP_FACTS_XIAMATSU.md` (XF), `PRIOR_ART.md`
(PA), the two Xiamatsu READMEs it was built from, and the datasheets — not from the sibling
rework of §3/§6/§11, §2.1/§2.5/App. A/B and §9, so cross-references into those sections use
their v2 names.

Cite conventions added to PLAN §1 by this rework: `xm_030:<n>` = line of the Xiamatsu
`py32f002a_003_030` README, `xm_002b:<n>` = line of the `py32f002b` README, both as copied on
2026-09-04 (XF header); `XF §n` = `doc/py32/CHIP_FACTS_XIAMATSU.md` section; `DS002B T5-14` =
PY32F002B DS V1.0 Table 5-14 (LSI), `DS030 T5-15` = PY32F030 DS V1.8 Table 5-15 (HSI).
Evidence levels used in §10: **measured** (a number read off live silicon, with who/what/how
many units), **datasheet** (Puya DS/RM or Arm TRM), **inferred** (a consequence derived from
the above), **speculation** (no evidence either way). Every row says which.

Risk numbering: v2's R1–R18 keep their numbers (other sections cite them); retired rows stay in
place marked retired; new rows are R19–R26.

---

## REPLACES §0 — Verdict in one paragraph

The port is believed achievable as a cycle-exact 32-cycles-per-bit Thumb-1 engine executing
entirely from SRAM, **primary target PY32F030** (HSI 24 MHz × PLL2 — the only 48 MHz path in
the family that is inside a datasheet, DS030 p2/p64; PLL lock at 48 MHz measured on the same
die, `xm_030:336`) and **second target PY32F002B**, whose factory 48 MHz trim word runs the chip
at a measured 43.12 MHz (−10.2 %, `xm_002b:172-175`, `:209-210`) and which therefore needs an
HSI self-calibration against its LSI before the D− pull-up is asserted (§10 R19/R21, gate G4).
Two v2 decisions were re-examined against the Xiamatsu measurements: **Р4 (engine in RAM)
survives** — with code in RAM ordinary instructions cost 1, RAM data 2, PUSH/POP 2+1 and the
GPIO port runs at full speed, «не замедляется, как ожидалось» (`xm_030:481-493`, `:447`); the
one new trap is narrow: a PC-relative literal load whose pool sits in flash costs 4 from RAM
code (`xm_030:490`), so every literal pool of `.timecrit` must land in SRAM (R23). The target
order is flipped (v2 had F002B first). Confidence: the cycle discipline itself is high — the
engine is a translation of a working RISC-V engine with a walker-verified ledger — but the
**cost table it is padded against is medium**: one author, one die (F002A/F003/F030), Flash
Latency 0, below 24 MHz (`xm_030:466`), with `BL`/`BX`/taken-branch costs that already disagree
with the TRM (4/3/2-3 vs 3/2/2, `xm_030:472,478-479,488,492-493`), no measurement at 48 MHz and
none on the F002B die. On F030 the servo (Р5) survives but changes role: at room temperature
most units should enumerate from the factory word alone (DS030 T5-15: ±0.7 % @25 °C against
the 0.5 % centred cell margin of §2.4.5 — note the −0.04 % figure in XF §2 was measured on an
**F002B's** 24 MHz constant, `xm_002b:206`, not on an F030), and the servo is needed for
temperature (±2 % over 0–85 °C, same table); on F002B the servo cannot even start from the
factory word (−10.2 % is outside its ±8.3 % sanity window, `S:762-772`), hence the calibration
stage. The single unknown that can still sink the port is **gate G1**: if the per-instruction
costs at 48 MHz / LATENCY 1 do not reproduce the RAM column of the measured table — port and RAM
access no longer 1/2 cycles, or costs that vary with alignment as the author warns
(`xm_030:468-469`) — then every ledger, pad and staircase constant is void and no static tool
can recover them; the only path back is to re-measure the table on the bench and re-pad. For
F002B alone the corresponding unknown is its LSI as a reference: −0.18 % on the one unit
measured (`xm_002b:204`) but ±3 % by datasheet (DS002B T5-14) — enough to bring the servo
into capture, not enough to promise enumeration without it (G4).

---

## REPLACES §10 — Risks

### 10.1 Register

Columns: trigger = the observation that says the risk has materialised; blast = what is lost;
evidence = level and source behind the *risk statement*; retire = the mitigation or the gate
(§10A) that closes it. Only risks specific to this port are listed.

| R | Risk | Trigger | Blast radius | Evidence | Mitigation / gate that retires it |
|---|---|---|---|---|---|
| R1 | *(retired, see 10.2)* Older F002B silicon has no 48 MHz HSI mode | — | — | — | residual (the constant, not the mode) → R19 |
| R2 | HSI drift beyond servo range / hunting over temperature; both parts | `rx.slope_cyc_per_bit` > 0.16 after lock; enumeration drops in the sweep | every HSI build | datasheet: DS030 T5-15 / DS002B T5-13 ±2 % (0–85 °C), −4/+2 % (−40–85 °C); cell margin 0.25/0.5 % (§2.4.5) | G10; slower slow-rate gain (`USB_TRIM_SLOW_SHIFT`), wider saturation; F030: HSE crystal build |
| R3 | F002B SRAM (3 KB) cannot hold RX+TX+dispatch+descriptors+staircase+DFU buffers+stack | ld `ASSERT` in T1/T5 | target #2 only | inferred from v2 §2.1 footprint (1168 B RAM for demo_gamepad with TX still in flash) | dispatch back to flash **priced with the flash column** (RAM data 4, PUSH/POP 4+1, `xm_030:475-476`) — bench4/OQ14 must use it, v2's "not cell-critical" is no longer free; shorter descriptors; HID loader instead of DFU |
| R4 | **Elevated.** The paper ledger (TRM Table 3-1) is wrong on this part: measured `BL` 4 (TRM 3), `BX` 3 (TRM 2), taken branch from RAM 2-3 (TRM 2), `B` 2-3 | G1 kernels ≠ TRM | every 32/64 path, every pad; the staircase `bl rv003usb_wait_N` (§7.4, arithmetic `BL` 3 + `NOP` + `MOV PC` 2) delivers N+1 if `BL` = 4 | measured: `xm_030:472,478-480,488-493` (one author, ≤24 MHz, LAT 0); the same claim Grainuum makes for Kinetis (PA §1 row 1, Q-11) | G1 writes the measured table into `tools/py32_cyc.py --cost-table`; staircase re-derived from the measured `BL`; no pad constant is final before G1 |
| R5 | IRQ entry outside [11,74] cycles (equal-priority ISR, long PRIMASK section, SysTick at prio 0) | bench3 spread; sporadic CRC failures | RX sync on every packet | datasheet/TRM: §2.2 window; measured PUSH from RAM 2+1·(N−1) = TRM's 1+N (`xm_030:491`), so the window is untouched by the measurement | G6; Р7 enforced in `usb_port_hw_setup()`; RAM vector table |
| R6 | Flash timing registers loaded for the wrong clock → mis-programmed pages | DFU readback mismatch | loader on both parts | datasheet: RM002B p33-35; which set applies on F030 when HCLK = 2×HSI via PLL is **not stated** (datasheet gap) | G12 readback; load exactly as `__HAL_FLASH_TIMMING_SEQUENCE_CONFIG` for the HSI mode in use; F002B 48 MHz set exists only on B-C silicon (RMBC p24) |
| R7 | SRAM not retained across SYSRESETREQ → boot flag/counter/double-tap lost | T10 test | DFU fast path, boot counter | datasheet gap (RM002B p56 lists registers only) — speculation either way | T10 test; fallback: STAY via `SFTRSTF` only, counter degrades to "never STAY" |
| R8 | **Elevated.** Turnaround > 7.5 bit-times: C handlers execute from flash, and from flash every RAM data access costs 4 and PUSH/POP 4+1 (`xm_030:475-476`), on top of LATENCY 1 at 48 MHz | `wg015vcd.py tx --gate-turnaround 7.5` fails on SETUP status / DFU GETSTATUS | enumeration, DFU | measured (flash column, ≤24 MHz LAT 0) + datasheet (LATENCY 1 above 24 MHz, RM002B p38) → the cost at 48 MHz is inferred, upward | G11; hot C path (`usb_pid_handle_in/data`) into `.timecrit`; then the ACK-first pipeline (branch_notes Part B, 3735518) |
| R9 | DFU > 4 KB on F002B | `sizecheck` | target #2 loader | inferred (WG015 DFU is 2876 B on RV32IMC, review_findings.md:28; Thumb-1 is denser, startup smaller) | `DFU_ENABLE_BOOTCOUNT/UPLOAD/APPCRC 0`; 8 KB loader, app at +0x2000 |
| R10 | Vendor documents contradict each other and the silicon; no public errata | — | every constant in `py32_min.h`, every clock assumption | measured: F002B DS says 24 MHz max, the chip runs 48 (`xm_002b:172`); factory 48 MHz word off by −10.2 % (R19); "SWD-Delay" known only from a community README (R24) | every number cites a page; every measurement records `DBG_IDCODE` and marking; G1–G5 before any tuning |
| R11 | Shared EXTI vector with user pins (lines 2/3, 4–15) | app enables EXTI on the USB vector | ISR livelock | datasheet: RM002B p97 | F6 hook (T2 step 7) |
| R12 | D± edge rates: lowest OSPEEDR + 33 Ω too slow into a long cable (> 300 ns) or a faster setting rings | scope in T10 outside 75–300 ns / overshoot > VDD | link reliability on long cables | datasheet gap (no tr/tf table, OQ10); Grainuum measured the ringing case (PA S-9) | raise `USB_PORT_OSPEED` one step at a time; never capacitors on D± (PA A-8) |
| R13 | **Elevated, made concrete.** F002B is a different die (shared with L020, `xm_002b:5`) and the cost table was measured only on the F002A/F003/F030 die (`xm_030:464`) | G3/G5 ≠ G1 | every F002B pad; possibly F002B as a target | measured on one die, **unmeasured** on the other (XF §1 "ОТКРЫТО") | G3 (24 MHz/LAT 0, like for like) then G5 (48 MHz); per-MCU `--cost-table` and pad set if they differ |
| R14 | SysTick reconfigured (1 ms reload) → keepalive delta ≠ 48000 → servo silently open-loop | `delta_se0_cyccount` ≈ 0 | every HSI build | inferred (v1 loader had exactly this, Р9) | Р9 rule + `SysTick->LOAD` greps in T1/T4/T5/T6/T9/T11 |
| R15 | Servo lock slower than the host's reset→first-SETUP window (Win10/11 xHCI, USB3 ports) | enumerates on Linux, fails on Windows; keepalive count before first SETUP (OQ9) < lock time | HSI builds; F002B worst (start ≤ ±3 %, R21) | datasheet (±0.7 % start on F030) / inferred (F002B start from G4); V-USB precedent (PA A-6) | G9; `USB_TRIM_FAST_SHIFT`/`LOCK_N`; F030: HSE; F002B: OTP constant |
| R16 | GPL contamination through "translation" of LemcUSB / stm32f030-vusb / V-USB routines | `Provenance:` trailer missing; Р10 grep matches | licence of the repo | — (policy) | Р10 hard rule; revert |
| R17 | Boot-failure counter false STAY (app resets itself > 3× before `usb_setup()`) | loader stays with a healthy app | apps that never call `usb_setup()` | inferred | `py32_app_alive()` early; `DFU_ENABLE_BOOTCOUNT 0`; explicit `DFU_FLAG_APP` always wins |
| R18 | Bounded preamble spin coarsens edge detection (4/4/4/7) → sample band leaves 14–18 | `rx` histogram min < 14 or max > 18 | RX dribble margin (F5) | inferred (§2.2 arithmetic) | G7; re-derive `USB_RX_SYNC_DELAY`; 7/7/7 variant |
| R19 | **New.** F002B factory 48 MHz word (`0x1FFF0104` = 0xB3A2, `xm_002b:269-270`) runs the chip at 43.12 MHz, −10.2 % | loading the word as v2 T1 startup did; MCO/2 ≠ 24 MHz | F002B never enumerates; −10.2 % is outside the servo's ±4000-count (±8.3 %) sanity window (`S:762-772`), so the servo rejects every delta and never engages | measured: `xm_002b:172-175`, `:209-210` — **one unit, MCO/2 on a UT89X handheld** (`xm_002b:203`); the sign and size are far beyond instrument error | never load `0x1FFF0104` blind; HSI self-calibration stage before DPU (R21, G4), or per-board OTP constant |
| R20 | **New.** F002B trim field is non-linear: `TRIM_H` scales the range in coarse steps (+41 % at 0b0111, +50 % at 0b1000), `TRIM_L` 0x000–0x1FF spans it linearly; 48 MHz lies in the 0b1000 band (21.7–33.4 MHz × 1.50 = 32.6–50.1 MHz) | servo step crosses a `TRIM_H` boundary → ≈9 % jump → delta out of window → lock lost | F002B servo | measured: `xm_002b:232-257` (one unit) | servo actuates `TRIM_L` only; `TRIM_H` fixed by G4; `USB_TRIM_SAT` ±64 LSB stays inside the band; bench6 measures LSB weight *in that band* |
| R21 | **New.** LSI as the F002B calibration reference: measured −0.18 % (32.71 kHz, `xm_002b:204`) but datasheet 31.6–33.6 kHz @25 °C = **±3 %**, ±10 % over 0–105 °C (DS002B T5-14) | G4 spread across units > 0.5 % | F002B: enumeration then depends on the servo locking from a ≤ 3 % start inside the host's first-SETUP window (R15); > 3 % or temperature-dependent → LSI unusable | measured (one unit) vs datasheet (worst case) — the two disagree by 15×; which one describes production parts is **unknown** | G4 on ≥ 5 units; fallback: per-board constant in OTP (128 B, XF §3) written at production; last resort: F002B dropped |
| R22 | **New.** The cost table was measured at Flash Latency 0 below 24 MHz (`xm_030:466`); at 48 MHz LATENCY = 1 is mandatory (RM002B p38, vendor BSP) | G1 ≠ XF §1 RAM column | every ledger (flash column certainly changes; the RAM column *should* not — RAM has no wait states) | measured at LAT 0; the 55–86 MHz run-from-RAM test reports "нет тактов ожидания, доступ к портам на полной скорости" (`xm_030:440-452`) but measured no per-instruction costs → 48 MHz validity is **inferred** | G1 is the first hardware step; nothing is padded before it |
| R23 | **New.** Literal-pool load from flash costs 4 cycles when the code runs from RAM (`xm_030:490`); App. B prices `[pc,#…]` at 2 | any `ldr rX,[pc,#n]` in `.timecrit` whose pool is outside SRAM (missing `.ltorg`, assembler placing a pool after `.popsection`) | silent +2 per occurrence per cell; walker (which does not know where the pool landed) reports 32 while the wire shows 34 | measured (`xm_030:490`) | T2 rule `.ltorg` after each block; mechanical gate (add to T2 acceptance / `check-cycles`): every PC-relative load in `.timecrit` resolves to an SRAM address (`objdump -d` + address check); constants of the bit loop in registers |
| R24 | **New.** "SWD-Delay": F002A/F003/F030 need ≈100 ms at start before the probe can attach — «ОБЯЗАТЕЛЬНА ЗАДЕРЖКА — SWD-Delay!», verified with 100 ms, absent from the vendor startup files (`xm_030:376-378`) | probe cannot connect after flashing a build whose startup reconfigures clocks/pins immediately | F030: recoverable via UART ISP at BOOT0 = 1 (`xm_030:374`); F002B has no ROM loader (§3.5) — recovery only by power-on erase with a CMSIS-DAP (`xm_030:379-380`) | measured by the community author, mechanism **undocumented** | T1 startup keeps a 100 ms window before clock/pin reconfiguration on bring-up builds; a known-good recovery procedure is executed once on each part before any USB work (G0 precondition of §10A) |
| R25 | **New.** F003/F002A reach 48 MHz on the same PLL path (`xm_030:336`) but their datasheets say 32/24 MHz max (§3.1) | someone ships a product on F003 at 48 MHz | out-of-spec deployment | measured (works) vs datasheet (unspecified) | the build accepts `MCU=PY32F003*`/`PY32F002A*` only with an explicit `PY32_OUT_OF_SPEC=1`; F030 is the only production part |
| R26 | **New (process).** A conclusion that overturns an engineering decision is adopted from a partial reading of its source | a plan or facts file cites one column of a two-column measurement | one fleet run (this project: d2b4a14 adopted "RAM data 4 cycles → RAM placement wrong", withdrawn in 88d1229 after the RAM column at `xm_030:481-493` was read) | this repo's history (`git show 88d1229`) | rule in §10.3 |

### 10.2 Retired by measurement

| Was | Retired by | Residual |
|---|---|---|
| R1 (v2): "older F002B silicon has no 48 MHz HSI mode (`HSI_FS=101` reserved, RM002B p63)" | `HSI_FS=101` accepted and the core runs from it on a live F002B, `xm_002b:172-175` | the *constant* is wrong (R19); whether pre-B-C dies differ stays a datasheet question — `DBG_IDCODE` is still recorded with every measurement |
| d2b4a14 XF §1: "RAM data costs 4 cycles → every RAM access in a cell is ruinous → Grainuum/LemcUSB/py32-branch RAM placement is the mistake" | the RAM column: RAM data 2, PUSH/POP 2+1, ports full speed, «не замедляется, как ожидалось» — `xm_030:481-493`; run-from-RAM at 55–86 MHz with no wait states, `xm_030:440-452` | the flash column is now the *cost of leaving anything in flash* (R3, R8); the literal-pool trap (R23) |
| OQ7 / R13 (v2 form): "is GPIO on the single-cycle IOPORT at all?" | `LDR/STR` to ports = 1 cycle from flash, "at full speed" from RAM, `xm_030:473`, `:447` | measured on the port the author used, not stated which; GPIOF on F030 still unverified → bench1 per port stays as a cheap check, no longer a risk row |
| v2 §3.1 premise that F002B is the natural first target (HSI-only, no PLL to bring up) | F002B factory 48 MHz word −10.2 % (`xm_002b:172-175`); F030 HSI 24 × PLL2 locks at 48 MHz on the same die as F003/F002A (`xm_030:336`) and is the only in-datasheet 48 MHz path | none — target order flipped (§0, §3) |

### 10.3 Process rule

A conclusion that overturns someone else's engineering decision (a prior-art author's, a
sibling agent's, or v1/v2 of this plan) is not adopted into PLAN.md, CHIP_FACTS or a STATE.md
request until the whole source it rests on has been re-read end to end, and the adopting text
quotes the passage that carries the overturn. The measured-cost reading of d2b4a14 cited one
column of a two-column table and survived a full plan pass before 88d1229 caught it; the rule
exists so that the next such overturn costs a re-read, not a run.

---

## NEW SECTION — §10A Bring-up gates

Ordered, board-scoped, one yes/no question each, one bench measurement each. This is the section
§10.1's "gate that closes it" column points to. Every kernel-level measurement detail (firmware,
timing method, expected values) is `rework/ledger.md` §5's job (kernels K1-K11, its "Gate 1" and
"Gate 2") — this table only fixes **when** each runs and **what happens on failure**; it does not
redefine K1-K11 or their pass values. Gates run in the order listed; none needs USB traffic, a
host, or the engine image except G6 onward.

Two things this project already knows without a bench and therefore does **not** gate here:
the toolchain is installed and both `#if PY32F002Bx5`/`#if PY32F003x4` arms assemble and the
engine links against a pinned `py32f0-template` (BUILD_FACTS.md §1-2, §6 — a build, not a
measurement); and the RAM-placement hazard of D-5 (a linker script spelling `*(.data.*)` would
silently strand `.datacode` in flash) is retired by a build-time `ASSERT` on the section's VMA,
not by anything a bench can fail (BUILD_FACTS.md §4, DEFECTS_VERIFIED.md D-5). That is why the
numbering below has no G2 and no G8 — both slots would have been exactly these two, and putting
either on a bench would be the "generic gate for anything" this section exists to avoid.

| G | Yes/no question | Board(s) | Measurement | Pass | On fail |
|---|---|---|---|---|---|
| G0 | Can a probe still attach after a bring-up build reconfigures clocks/pins at reset? | F030, F002B | Flash a build with immediate clock/pin reconfiguration; attempt SWD attach within the 100 ms `SWD-Delay` window (`xm_030:376-378`, R24); once per part, deliberately exercise the documented recovery path once | Probe attaches inside the window on both parts; the recovery procedure (F030: UART ISP at BOOT0=1, `xm_030:374`; F002B: power-on erase via CMSIS-DAP, `xm_030:379-380`, no ROM loader) actually un-bricks a board | Widen the startup delay before any clock/pin write in bring-up builds; do not touch clocks/pins earlier until this passes (R24) |
| G1 | Does the RAM column of the measured cost table hold at 48 MHz / `LATENCY=1`? | F030 (target #1) | `rework/ledger.md` §5 "Gate 1": K1-K11 at 24 MHz/LAT0 (calibrates the rig against `xm_030:464-493`), then at 48 MHz/LAT1 | RAM-copy kernels K1-K4, K7-K11 identical between the two runs (cycles are per-HCLK; a RAM-resident kernel touches no flash so `LATENCY` must not show up) | Re-ledger with the 48 MHz numbers via `tools/py32_cyc.py --cost-table`; no pad constant in Appendix A/B is final before this gate (R4, R22) |
| *(no G2 — settled without hardware, see above)* | | | | | |
| G3 | Does F002B reproduce the *same* table F030 gave at 24 MHz/LAT0 (like-for-like sanity)? | F002B (B-C silicon, `DBG_IDCODE` recorded) | `rework/ledger.md` §5 "Gate 2", first half: same kernel image, factory HSI24 word, `LATENCY=0` | Every kernel equals G1's 24 MHz/LAT0 F030 value | F002B is a different die for costing purposes (it shares silicon with L020, not with F002A/F003/F030, `xm_030:464`) → own `--cost-table` from here on (R13) |
| G4 | Can F002B trim its own HSI to inside USB tolerance from the on-chip LSI, before the D− pull-up? | F002B, ≥5 units | Run the self-calibration routine (`TRIM_L` swept against an LSI-derived reference count, `TRIM_H` held fixed per R20's band) at 25 °C; read the result via MCO/2 (R21) | Every unit lands within ±1.5 % of 48 MHz after one pass, and the LSI reference itself spreads ≤0.5 % unit-to-unit (`xm_002b:204` measured −0.18 % on one unit vs DS002B T5-14's ±3 % datasheet ceiling — which one describes production parts is what this gate answers) | Spread > 0.5 % or any unit out of tolerance → per-board OTP calibration constant written at production (128 B OTP, XF §3); if that is not viable, F002B is dropped as a target (R19, R21) |
| G5 | Does the RAM column hold on F002B at 48 MHz / `LATENCY=1`, post-calibration? | F002B, same units as G4 | `rework/ledger.md` §5 "Gate 2", second half: same kernels, `HSI_FS=101` post-G4, `LATENCY=1` (mandatory above 30 MHz, `xm_002b:259`) | Every kernel equals G1's 48 MHz F030 RAM-copy value | F002B keeps its own `--cost-table`; if K3 = 4 from RAM there, §2.1's "RAM is favourable" conclusion is F030-only and Appendix A loses cycles on F002B that must be found (branchless EOB restructuring, R13) |
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

---

## REPLACES §12 — Changelog vs the pre-prior-art plan

Items 1-56 are v2's `§12` (v1 → v2, driven by `PRIOR_ART.md`) — **unchanged**, see
PLAN.md at 88d1229 or `git show 88d1229:doc/py32/PLAN.md` for the full text; they are not
reproduced here to avoid re-stating 56 lines that this rework did not touch. This rework
(v2 → this revision) is driven by `CHIP_FACTS_XIAMATSU.md` (measured silicon, marked XF),
`BUILD_FACTS.md` (verified by building in this container, marked BF) and
`DEFECTS_VERIFIED.md` (verified in source, marked DV); it adds:

57. §0: primary target flipped from F002B to F030/F003 — HSI 24 MHz × PLL2 measured at
    47.98 MHz (−0.04 %), inside USB tolerance with **no servo needed at reset**; F002B demoted
    to second target, gated on an HSI-vs-LSI self-calibration stage before the D− pull-up
    (new §10A gate G4) — source: XF §2 (`xm_030:336`; `xm_002b:172-175`, `:209-210`, `:269-270`).
58. §0/§10 (Р4, RAM placement of the engine): **re-examined and survived**, not overturned —
    confirmed twice over. First by Xiamatsu's own-silicon measurement (RAM data access 2 cycles
    not 4, `PUSH`/`POP` 2+1, GPIO ports "at full speed" from RAM, "не замедляется, как
    ожидалось", `xm_030:481-493`, `:447`). Second, independently, by linking the actual engine
    object against the stock `py32f003x4.ld` and observing `.datacode` — the whole hard-real-time
    RX sampling path — actually land at VMA `0x20000000` / LMA `0x08000200` (BF §3-4). A draft of
    this rework (commit d2b4a14) briefly concluded the opposite from one column of the same
    source table ("RAM data 4 cycles → RAM placement is the mistake"), withdrawn in 88d1229
    once the RAM column was read; that mistake is now §10.3's process rule (R26) — source: XF §1;
    BF §3-4; `git show d2b4a14`, `git show 88d1229`.
59. §3.2/Appendix B: the earlier flat assumption "flash execution is the expensive case, RAM is
    the cheap one" does not hold — the costs **swap by location** (flash: RAM-data 4,
    `PUSH`/`POP` 4+1(n−1), literal-pool 2; RAM: RAM-data 2, `PUSH`/`POP` 2+1(n−1), literal-pool
    4). The one direction that gets *worse* in RAM is a PC-relative literal load whose pool is
    still in flash. New rule R23: every literal pool reached from `.timecrit` must resolve to an
    SRAM address, checked mechanically by disassembly, not guaranteed by construction — source:
    XF §1 table; `xm_030:490`.
60. New split, not previously stated: the engine is not one execution context for costing
    purposes. The RX path runs from RAM (`.datacode`, 252 B — the entire hard-real-time sampling
    path) and the TX path runs from FLASH (`.text`, 512 B, confirmed by `objdump -h` on the
    actual object, BF §3). Every earlier statement about "the engine"'s cost must be read as two:
    RX costed on the RAM column (§3.2 Consequences 1-2), TX and the C dispatch tail on the flash
    column (§3.2 Consequence 3, R8's turnaround risk). Flags a latent trap in the other
    direction, left open rather than decided: relocating TX to RAM would turn its own
    flash-resident literal load (`.text+0xda`) into a 4-cycle access while its current
    RAM packet-byte reads (4 from flash today) would drop to 2 — an arithmetic question for the
    ledger, not yet answered — source: BF §3, §5.
61. D-1 (endpoint bound check): confirmed by source inspection and **reclassified from
    "possibly inherited" to branch-introduced** — the Thumb port's `bhi` (rejects only
    `endp > ENDPOINTS`) replaces the RISC-V original's correct `bgeu` (rejects
    `endp >= ENDPOINTS`) at the equivalent site; the comment on the very same faulty line still
    states the intended `<` semantics, so this is a coding slip, not a design choice. Consequence
    traced through to `eps[ENDPOINTS]`, one element past the last member of
    `struct rv003usb_internal`, reachable by any host token addressed to the boundary endpoint —
    source: DV D-1 (`rv003usb-arm.S:274-277` vs `rv003usb.S:526-528`).
62. D-2 (RX overrun): confirmed real, already flagged by the author's own `// TODO`, but
    **reclassified from an implicit one-line fix to a design task** — `is_end_of_byte`'s
    unchecked `strb`/`add` sit inside the cycle-counted RX path (cycle-budget comments in place
    on the same lines), so any bound check must be paid for out of the bit-cell budget or moved
    off the hot path — source: DV D-2 (`rv003usb-arm.S:145-148`).
63. "The per-part `#if` variant is never assembled" (a claim in earlier drafts) is **too strong,
    corrected**: both `#if PY32F002Bx5` arms assemble cleanly (`-DPY32F002Bx5=1` and
    `-DPY32F003x4=1` both rc=0, objects differ by 4 bytes). What is actually missing is the
    build system's *selection* of the non-default arm — `Makefile.py32` pins
    `MCU_TYPE = PY32F002Bx5` unconditionally. This matters more after item 57's target flip,
    since F003/F030 now exercises exactly the arm the branch's own build has never run — source:
    BF §2; DV D-3.
64. D-4 (link failure) and D-5 (incidental RAM placement) added as verified findings: the
    branch's `py32f0-template` submodule is empty so it cannot link as published (pins cleanly
    at upstream `289ffc8`); and the `.datacode`→RAM placement that item 58 confirms is not the
    product of any explicit rule anywhere in the branch or its template — it is swallowed by the
    stock linker script's `*(.data*)` wildcard by accident. A script spelling the more common
    `*(.data.*)` form would place the RX engine in flash silently, no build error, no
    diagnostic. This port's own linker script must therefore carry an explicit named RAM-code
    section and an `ASSERT` on its VMA — a build-time check, deliberately **not** one of the
    §10A bring-up gates — source: BF §4, §6; DV D-4, D-5.
65. New §10A "Bring-up gates" (this file): turns §10.1's "gate that closes it" column into an
    ordered, citable sequence of yes/no hardware measurements (G0-G12, skipping G2 and G8 —
    items 66 and 64 respectively); defers the cost-table measurement detail to
    `rework/ledger.md` §5 (kernels K1-K11,
    its Gate 1/Gate 2) instead of inventing a parallel scheme — source: this file;
    `rework/ledger.md` §5.
66. Toolchain/link viability (`arm-none-eabi-gcc` 13.2.1 present; both MCU arms assemble; the
    engine links once `py32f0-template` is vendored/pinned) is recorded as a build fact and
    explicitly **excluded** from §10A — it needed no hardware and was already settled by
    building in this container — source: BF §1-2, §6.
