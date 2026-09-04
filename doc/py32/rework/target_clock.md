# PLAN.md rework — target part and clock (§3.1, §3.2, §6, §11)

Replacement blocks for `doc/py32/PLAN.md` at 88d1229. Each block begins with a line
`## REPLACES §…`; splice the block body in place of the named section. Where a subsection
needs no change the block says so in one line.

Cite conventions, in addition to PLAN.md's header: `CF §n` = `doc/py32/CHIP_FACTS_XIAMATSU.md`
section; `xm_030.md:<n>` / `xm_002b.md:<n>` = the line of Xiamatsu's README that CF quotes
(https://github.com/Xiamatsu/py32f002a_003_030, https://github.com/Xiamatsu/py32f002b, fetched
2026-09-04 — I did not re-fetch them; every such number is taken from CF and is only as good as
CF's transcription); `BF §n` = `doc/py32/BUILD_FACTS.md` (facts produced by building the branch
in this container; memory geometry read from `py32f0-template@289ffc8 Libraries/LDScripts/*.ld`);
`PLAN:<n>` = line of PLAN.md at 88d1229. **All Xiamatsu numbers are single units at room
temperature unless CF says otherwise; none of them is a specification.**

No new Р-number is introduced (sibling agents may be adding their own); the part decision goes
into the reworked Р5.

---

## REPLACES §3.1 — Which parts can do 48 MHz

### 3.1 Which parts can do 48 MHz

Two different facts decide the table: whether the part *reaches* 48 MHz, and whether the
frequency it reaches *at reset from the factory constant* is inside the USB tolerance
(±1.5 %, USB 2.0 §7.1.11) and the engine's sampling margin (≈0.44 % with the 14–18/32 sample
band of F5, §2.4.5). The second fact is measured, not specified, and it flips the target order.

| Part | Max f (DS) | 48 MHz path | Measured at reset (Xiamatsu) | Flash / RAM | Verdict |
|---|---|---|---|---|---|
| **PY32F030x6/x8** | **48 MHz** (DS030 p2, p5) | HSI `HSI_FS=100` (24 MHz, factory trim at `0x1FFF0F10`) × PLL2, or HSE 4–32 MHz × PLL2 with a 24 MHz crystal. PLL input: DS030 Table 5-17 says 12–24 MHz, Xiamatsu 16–24 MHz (xm_030.md:15) and "PLL_IN — only 24 MHz" (:79) — the discrepancy is immaterial because only the 24 MHz input yields 48 MHz; TheYkk's "8 MHz × 6" cannot exist (PA A-19). `PLLON/PLLRDY` RM030 p77, `PLLSRC` p83, tLOCK 15/40 µs DS030 p64 | HSI 24 MHz factory word → **23.99 MHz (−0.04 %)** (xm_030.md:15); × 2 = **47.98 MHz, −0.04 %**, inside the USB tolerance and the sampling margin with **no trim step at all** (CF §2) | 32/64 K, 4/8 K RAM (template README) | **Primary target.** The only part whose 48 MHz is in its datasheet *and* whose factory constant lands inside tolerance. Default `MCU`, reference for every ledger and bench. HSI build needs no calibration before enumeration; the servo question is reduced to drift (Р5) |
| **PY32F003** | 32 MHz (DS003 p1; PLAN v2 listed it as "no PLL") | Same PLL path as F030: "Проверено — PLL запускается на 48 МГц на чипах PY32F002A и PY32F003" (xm_030.md:336) | same HSI family as F030 (CF §2 groups F002A/F003/F030 under one measurement set) | per DS003 — not extracted here; T1 takes the ld numbers from the DS | **Primary family, out-of-spec member.** Reaches 48 MHz by measurement only; its DS says 32 MHz and lists no PLL, so a product on F003 at 48 MHz runs outside its datasheet. Usable as a development/cost-down twin of F030 once T10 shows bench1–6 equal on it (OQ-B). Whether F003/F002A/F030 are one die remains **UNVERIFIED** (PA §5.1); the measurement proves a locking PLL, nothing more |
| **PY32F002B ("B-C" silicon)** | DS002B V1.0: **24 MHz**; RMBC p14: fmax 48 MHz; Xiamatsu: "для F002B не объявлена поддержка HSI 48 MHz" (xm_002b.md:6) | HSI only: `HSI_FS=101` (RMBC p58), factory word at `0x1FFF0104`, 48 MHz flash-timing set at `0x1FFF0130…0x140` (RMBC p24/p30). **No PLL; HSE is a clock *input* only (1–32 MHz)** (CF §2; DS002B p2; `RCC_CR_HSEON` absent from py32f002bx5.h) → no crystal path to 48 MHz either | Factory word `[0x1FFF0104] = 0x0000B3A2` → `HSI_FS=0b101`, `HSI_TRIM=0x13A2`, nominally 49.60 MHz by the author's formula (xm_002b.md:269-270); **measured 43.12 MHz** (MCO/2 = 21.56 MHz, xm_002b.md:172-175; "калибровочная константа установлена неверно", :209-210) = **−10.2 %**. Enumeration from the factory word is impossible in principle (USB ±1.5 %). 48 MHz *is* inside the trim range: at `HSI_FS=101`, `TRIM_L` 0x000…0x1FF spans 21.7–33.4 MHz with `TRIM_H=0` (:249-257) and `TRIM_H` scales that range by +33 % (0b0110) / +41 % (0b0111) / +50 % (0b1000) (:232-246) → 48 MHz lies between `TRIM_H` 0b0111 and 0b1000, "достижимо с запасом, но только собственной калибровкой" (CF §2) | 24 K / 3 K, page 128 B (RM002B p22) | **Second target, with a precondition:** the firmware must trim the HSI itself against an on-chip reference *before* asserting the D− pull-up (Р5). Reference available at reset: LSI, factory-trimmed, measured 32.71 kHz vs 32.768 nominal = **−0.18 %** (xm_002b.md:204-206). Second reference, only after connection: the host keepalive (1 ms). 128 B OTP exists for a per-board constant (CF §3; address and write sequence not extracted — T1/T5 from RM002B) |
| PY32F002A | 24 MHz (DS002A p2), HSE 4–24 MHz | PLL measured to lock at 48 MHz (xm_030.md:336) — same status as F003 | as F003 | as F003 | Not planned. Same standing as F003 (out of DS); nothing in this plan depends on it |

Sanity: the template README's "PY32F0xx up to 48 MHz" is right in the sense that the PLL locks
on all three F0xx parts (xm_030.md:336) and wrong in the sense that only F030's datasheet says
so. Datasheets still win for what a product may claim.

What the Xiamatsu figures do **not** establish (kept honest here, closed in §11): unit-to-unit
spread of the F030 HSI at 48 MHz (DS030 Table 5-15 guarantees only 23.83–24.17 MHz = ±0.7 % at
25 °C; one unit measured −0.04 %), its temperature/voltage drift (DS: ±2 % 0–85 °C, −4/+2 %
−40…85 °C — outside both the USB tolerance and the sampling margin, §2.4.5), the LSI drift on
F002B, and whether the −10.2 % factory constant is universal or one unit's.

---

## REPLACES §3.2 — Core and timing

### 3.2 Core and timing

The TRM table stays the reference for *what the core can do*; the measured table below is the
reference for *what it costs on this silicon*. Where the two disagree, the measurement is the
working figure and the difference is a bench item, not a footnote.

| Fact | Source |
|---|---|
| Cortex-M0+, 2-stage pipeline, single-cycle multiplier on PY32 | DS002B p8, DS030 p17; TRM p1-5 Table 1-1 |
| Interrupt latency 15 cycles (zero WS), LDM/STM abandoned+restarted, late-arrival/tail-chain | TRM p3-10 §3.6.1 |
| TRM instruction costs: MOV/ALU 1; `B<cc>` 1/2; `B` 2; `BL` 3; `BX/BLX` 2; `MOV PC,Rm` 2; `LDR/STR` 2 on AHB, 1 on the single-cycle I/O port; `PUSH` 1+N; `POP{…,PC}` 3+N; `NOP` 1 | TRM p3-4…3-7 Table 3-1 + footnotes b, e |
| **Measured costs depend on where the code executes from, and the access costs swap** (F002A/F003/F030, Flash Latency 0, ≤24 MHz; author's own timing runs): | xm_030.md:464-493 via CF §1 |
| — ordinary instructions: 1 from flash, 1 from RAM | :471 |
| — branch taken / not taken: 2 / 1 from flash, **2–3 / 1 from RAM**; `B` 2–3 either way; `BX Rm` 3; `BL` 4 | CF §1 table (vs TRM 2 / 2 / 3 — see the staircase row) |
| — `LDR/STR` to GPIO: 1 from flash, "на полной скорости" from RAM | :473, :447 |
| — `LDR` of a flash literal via PC: **2 from flash, 4 from RAM** | :474 |
| — `LDR/STR` to SRAM: **4 from flash, 2 from RAM** | :475 |
| — `LDM/STM/PUSH/POP`: 4 + 1·(n−1) from flash, **2 + 1·(n−1) from RAM** (= TRM's 1+N) | CF §1 table |
| "не замедляется, как ожидалось" for RAM execution; separate test executing from RAM at 55–86 MHz "нет тактов ожидания", "доступ к портам на полной скорости" | :481; :440-457 |
| Author's caveat: "определить выполнение инструкций сложно, так как зависит от выравнивания и зависимости от предыдущей инструкции" — the numbers are typical, not guaranteed | :468-469 |
| Consequence 1 — placement: a RAM-resident engine (Р4) is the *cheap* configuration on this part: SRAM data 2, stack 2+1, ports full speed, no wait states at 48 MHz. The RM-derived worry of v2 (flash prefetch making `ldrb` 2-or-3 cycles, PLAN:468) is replaced by a measured one and the conclusion is stronger, not weaker | CF §1 |
| Consequence 2 — the literal-pool trap: from RAM code, a `ldr rX, [pc, #imm]` whose pool sits in flash costs 4. **No such load may appear inside a timed bit cell.** With every engine block in `.timecrit` and `.ltorg` after each block (T2 step 2) the pools are in SRAM and cost 2 like any SRAM load; the branch's RX ISR already has its pool in RAM (PLAN:91). The walker must therefore *check the address* of every `[pc,#…]` load reached from a timed path: SRAM → 2, flash → error (request to T2, `tools/py32_cyc.py`) | CF §1; PLAN:808 |
| Consequence 3 — the flash-code column is the cost model for anything left in flash (R3's "dispatch back to flash" fallback, C handlers): every SRAM access from there is 4, and at 48 MHz with `LATENCY=1` the fetch itself adds wait states the table does not contain (it was taken at Latency 0). Turnaround (R8), not bit cells, is what this hits | CF §1, §3 |
| Consequence 4 — the staircase identity `bl rv003usb_wait_N` = N (§7.4) assumes `BL` 3 + `MOV PC,LR` 2 (TRM). Measured `BL` is 4 and `BX` 3; `MOV PC,LR` was not measured. If `BL` 4 holds from SRAM every entry is N+1 — a relabel, but one that must come from bench2, not from either table (OQ4) | CF §1; PLAN:601-604 |
| Consequence 5 — taken branch 2–3 from RAM is consistent with Grainuum's "taken branch = 3" (PA §1 row 1) and with the author's alignment caveat → `.balign 4` on loop heads (R4) is now the expected outcome of bench2, not a surprise | CF §1; PA Q-11 |
| Measured on F002A/F003/F030 only; F002B is a different die (shared with L020) and "действуют ли те же цены на F002B … не проверено" | CF §1 (ОТКРЫТО) → OQ-B |
| Flash wait states: Latency 0 measured to hold to 24 MHz on F030 (xm_030.md:466) and to 30 MHz on F002B (xm_002b.md:259) → `LATENCY=1` is mandatory at 48 MHz on both (RM002B p38 "two system clock cycles are required for each Flash read"; vendor BSP `LL_FLASH_LATENCY_1`, py32f002b_bsp_clock.c:29-30); CF §3: "на 48 МГц латентность ненулевая, XIP-тайминги плавают". No prefetch buffer / cache documented (RM030 §4.2.2 p26, §4.8.1 p42-43; PA D-2) | CF §3; RM002B p38 |
| Single-cycle I/O port: "accessible both by loads and stores … You cannot execute code from the I/O port"; GPIO A/B/C(/F) on it per the RM memory map (`0x5000_0000` IOPORT). Measured 1-cycle port access (above) confirms the port used by Xiamatsu; port F on F030 and the F002B ports are not separately confirmed (OQ7) | TRM p2-3 §2.2.2; RM002B p15-18, p76; RM030 p18-20, p100; CF §1 |
| Fetch-ahead limited to 32 bits; "Instruction fetch width 16-bit only or mostly 32-bit" is a vendor option (unknown on PY32) | TRM p2-2 §2.2.1, p1-5 Table 1-1 → bench2 |
| SysTick present, `CALIB` 6000 (1 ms @ HCLK/8 → HCLK 48 MHz), `VAL` 24-bit down-counter (wraps every 349.5 ms at 48 MHz with `LOAD=0xFFFFFF`) | RM002B p97 §11.1.2; RMBC p84; py32f002bx5.h:53 |
| VTOR present; vendor `SystemInit` writes `SCB->VTOR` | py32f002bx5.h:51; system_py32f002b.c:132-137 |
| NVIC: 2 priority bits (4 levels), 32 IRQ lines | RM002B p97 §11.1.1 |
| "During a program and erase operations … any attempt to read the Flash memory will stall the bus" → XIP programming is legal, CPU stalls; writing `FLASH_CR` while `BSY` stalls too | RM002B p23-24; RM030 p27-28 |
| CSS: if HSE fails the clock falls back to HSI and an NMI is raised → HSE builds need an `NMI_Handler` (T1); a silent fallback to the untrimmed 24 MHz HSI drops the link | RM030 §8 CSS (PA §5.4) |
| MCO on PA7 tops out at ≈35 MHz (F002B) → 48 MHz cannot be observed on MCO undivided; Xiamatsu used MCO/2 (21.56 MHz reading for 43.12 MHz). Every bench that "measures 48 MHz on the LA" (bench6, T10 clock verification) must use the MCO prescaler and say so. F030 MCO pin/prescaler not extracted here — T6 from RM030 | xm_002b.md:261 via CF §3 |
| NRST needs a "SWD-Delay" of ≈100 ms in the startup files (Xiamatsu errata section) — relevant to the bring-up rig (PA A-12), not to the engine | CF §3 |
| UART ISP loader at BOOT0=1 on the F0xx family (xm_030.md:374) — confirms §3.5 (`puyaisp`); F002B has none | CF §3 |

---

## REPLACES §6 — Architecture decisions (argued both ways)

### 6. Architecture decisions (argued both ways)

Two corrections drive this block, and they hit the *justifications* harder than the conclusions:
(a) the instruction-cost table depends on where the code executes from, and the prices swap
(CF §1) — v2 argued placement from a flash-column reading of the RM; (b) the primary part flips
to F030/F003 (CF §2). Convention used below, so the reader can skip what did not move:

* **unchanged, no edit** — the decision text of PLAN:422-553 stands as written.
* **justification replaced** — the conclusion stands, the stated reason does not. In a plan this
  is a defect and not a cosmetic one: the next person re-derives from the reason, not from the
  answer, and a reason that is wrong will not survive contact with a case v2 never considered.
* **changed** — the decision itself moves.

| Р | Subject | Status |
|---|---|---|
| Р1 | separate `rv003usb-arm.S` | conclusion stands, **justification replaced** (cost numbers) |
| Р2 | per-target `usb_port_<chip>.h` | unchanged, no edit |
| Р3 | no vendor submodule | conclusion stands, **evidence added** (BF §4, §6; DV D-4/D-5) |
| Р4 | code placement | **changed**: split RX/TX, one rule, new gate |
| Р5 | clocking, part order, servo | **changed**: target flip (CF §2, §4.1) |
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

* the struct load is 2 cycles only when the struct *and the code* are in RAM (CF §1: RAM data
  from RAM code = 2); from flash-resident code the same load is **4** (xm_030.md:475). The
  branch's TX path is flash-resident today (BF §3), so a Grainuum-style struct would cost 4
  there, not 2 — the argument is stronger than v2 made it, in the place v2 did not look.
* "compile-time literals cost 1" is not a thing: a literal reaches a register through
  `ldr Rd,[pc,#imm]` at 2 (pool in RAM) or **4** (pool in flash, from RAM code — CF §1,
  xm_030.md:474). Only the *port access itself* is 1. The correct statement of the rule is the
  one Appendix A now uses: the GPIO base is loaded into a register **once, outside every timed
  cell**, and the cell contains only the `ldr/str` at P = 1.

For (against the seam): one file, one ledger discipline, the macro table exists. Against: the
bodies share no instruction, no register file and no exception return; a Thumb "body" inside
`rv003usb.S` is 100 % `#if`. **Decision unchanged.** New supporting evidence, in-family rather
than hypothetical: across F030/F003 and F002B every base address and every register offset the
timed code touches is identical — GPIOA 0x50000000, GPIOB 0x50000400, EXTI 0x40021800,
RCC 0x40021000, `IDR` +0x10, `BSRR` +0x18, matching `arm.S:14-15` (BF §7). The address layer of
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
branch, so the branch cannot link as published** (DV D-4, BF §6) — the strongest possible form
of the "50 MB dependency" objection, since the dependency is not even present; the `Build/../`
object hack, the LL/HAL licence mix and the `-D` bug in `rules.mk` all stand as v2 stated them.

What the build experiment adds, and what makes this decision load-bearing rather than
housekeeping: **the RX engine reaches RAM by accident.** No `.datacode` rule exists anywhere —
not in the branch, not in the template (BF §4). The section lands in RAM only because it matches
the stock script's `*(.data*)` wildcard (`py32f003x4.ld:118`; same spelling at
`py32f030x6.ld:116` and `py32f002bx5.ld:116`, so every stock script hides the problem equally
well). A script that spells the rule `*(.data) *(.data.*)` — with a dot — does **not** match
`.datacode`; the section becomes an orphan, GNU ld places an `"ax"` orphan after `.text`, and the
hard-real-time RX path executes XIP with **no error, no warning, and a successful build**
(DV D-5). That is the argument for owning the linker script: not "fewer megabytes" but "the one
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
(BF §3, from `objdump -h` on the real object, not read from the source):

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
(CF §1): from RAM-resident code, RAM data costs **2** (4 from flash), `push/pop` **2+1** (4+1
from flash), ports run «на полной скорости», and ordinary instructions are 1 cycle either way —
running from RAM «не замедляется, как ожидалось» (xm_030.md:481). RAM placement is therefore the
*cheap* configuration on this part, not a necessary evil, and the architectural reason is visible
in the map: GPIO sits at `IOPORT_BASE` 0x5000_0000, on the M0+ IOPORT bus rather than APB
(BF §7).

For keeping TX in flash (the branch's choice, `arm.S:211-212` "conserve RAM"): 512 B saved, which
on the 2 K F003x4 is a quarter of RAM. Against: every packet byte the TX cell reads is a RAM
access from flash-resident code = **4** cycles (`load_next_byte`, BF §5); the flash literal at
`.text+0xda` sits inside the timed `pre_and_tok_send_one_bit` cell (BF §5), where it is 2 today
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
(Appendix B; the rule exists because from RAM code a flash pool costs 4, CF §1, and today it
holds "by construction, not by enforcement", BF §5). An `nm` check on descriptor symbols is not
sufficient on its own; the pool check is the one that catches the silent case.

Corroboration that the two-column model is real and not an artefact of one author's rig: all five
`#if PY32F002Bx5` sites in the engine (arm.S:402, 415, 444, 490, 530) are **pure cycle padding in
the TX path** — F002B carries two extra `nop`, F003/F030 one extra `b .+2`, exactly the measured
4-byte `.text` difference (BF §8, BF §2). The branch author hit empirically the same per-die cost
difference the Xiamatsu table describes.

---

**Р5. Clocking, part order and the servo — the target flips.**
**Changed.** v2 made F002B the primary target and F030 the alternative; §3.1 reverses that on
measured evidence. The case for F002B is stated first and at full strength, because it was not
silly.

*For keeping F002B primary (v2's case):* 3 K RAM against the 2 K of the cheapest F003
(`py32f003x4.ld:34-35`); 24 K flash against 16 K; **no PLL to bring up** — HSI only, so no lock
wait, no `PLLSRC`/`PLL_IN` constraint, no CSS/NMI handler, one fewer failure mode in the loader;
128 B of OTP for a per-board calibration constant (CF §3); and the branch's own build is already
pinned to it (`Makefile.py32`, `MCU_TYPE = PY32F002Bx5`), so staying put means shipping the only
arm the branch has ever built (DV D-3).

*Against, and this is what settles it:* the F002B factory 48 MHz word
(`[0x1FFF0104] = 0x0000B3A2`, nominally 49.60 MHz by the author's formula) **measures 43.12 MHz
on live silicon — −10.2 %** (xm_002b.md:172-175, :209-210 «калибровочная константа установлена
неверно»). USB LS allows ±1.5 % (USB 2.0 §7.1.11). The part cannot enumerate from its factory
constant at all, so a trim loop there is a precondition, not a refinement. Worse, v2's servo as
specified could not have recovered it: v2 saturates the actuator at **±64 LSB from the factory
value** at ≈0.1 %/LSB (PLAN:497-499), i.e. ±6.4 %, against a −10.2 % starting error — the loop
saturates and never locks. That is a concrete defect in v2's Р5, not a tuning matter. Beyond it:
the trim field is non-linear (`TRIM_H` scales the `TRIM_L` range in coarse steps of +33/+41/+50 %,
xm_002b.md:232-257); there is **no PLL and HSE is an input only** (CF §2), so no crystal escape
hatch exists; there is no ROM ISP loader (CF §3, §3.5), so recovery is SWD-only; and the whole
two-column cost model was measured on the *other* die (CF §1 ОТКРЫТО → OQ-B).

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
the branch's build system has never selected (DV D-3) — answered by experiment rather than by
argument, since it assembles clean (BF §2, rc = 0 with `-DPY32F003x4=1`) and the alignment guard
it carries, `.ifeq (pre_and_tok_send_inner_loop - usb_send_data) & 2 / .error` (arm.S:415,
`#else` arm), **passes**: a correctness constraint its author could not test, which holds, and
which will now fail the build rather than the timing if anyone shifts a halfword ahead of that
label (BF §8). Third objection, portability: **dismissed on evidence, not debated** — every base
address and register offset in the timed code is identical across the two families (BF §7), so
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
headers (BF §7), so `USB_PORT_OSPEED` is a single constant with no per-part arm.

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

---

(§11 block follows.)
