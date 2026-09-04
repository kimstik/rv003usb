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

(§6 and §11 blocks follow.)
