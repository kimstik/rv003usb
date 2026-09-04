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
