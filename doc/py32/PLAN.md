# PY32 (Cortex-M0+) port of rv003usb — analysis and fleet plan

Status: v1, 2026-09-03. Sole-analyst deliverable. Every non-obvious claim carries its evidence
inline: `arm.S:<n>` = `rv003usb/rv003usb-arm.S` line in commit 0ad3c42; `c:<n>`/`h:<n>`/`S:<n>` =
`rv003usb/rv003usb.{c,h,S}` at master 80b1893 unless a commit is named; `RM002B p<n>` =
PY32F002B Reference Manual V1.0 page; `RMBC p<n>` = PY32F002B-C Reference Manual V1.0;
`DS002B p<n>` = PY32F002B Datasheet V1.0; `DS030`/`RM030` = PY32F030 Datasheet V1.8 / RM V1.7;
`TRM p<n>` = Arm Cortex-M0+ TRM DDI0484B (all URLs in §1). "Build log" = my rebuild of the
branch with arm-none-eabi-gcc 13.2.1 against py32f0-template @289ffc8 (the pinned submodule).

## 0. Verdict in one paragraph

The PY32 branch (0ad3c42, one commit, Dec 2024, base 9c8a442) is a **hand-translated Thumb-1
copy of the 9c8a442 RISC-V engine** plus `#if __riscv` ladders in shared C, driven by the vendor
py32f0-template (LL library, startup, linker script). What works: the RX ISR is a faithful,
cycle-exact 32-cycle/bit translation that runs from RAM through a linker-script glob trick and
is internally consistent under Cortex-M0+ TRM cycle costs (§2.4, verified by a walker over the
real disassembly). What is broken or fragile: (a) the TX engine executes from 1-wait-state
flash and was tuned by ear with per-part `nop`s; the `#if PY32F002Bx5` variant of it was **never
assembled** because the vendor `rules.mk` passes `-D` only to C (§2.6) — the shipped object is
the "other parts" variant; (b) an endpoint bounds off-by-one (`bhi` vs `bhs`, arm.S:276) that
lets `endp == ENDPOINTS` reach the C handlers; (c) no packet-length bound in RX (arm.S:145);
(d) the keepalive/HSI-trim servo is a stub (arm.S:217) so the device runs open-loop on an RC
oscillator specified at ±0.7 % @25 °C and −4…+2 % over temperature (§3) while the paper
sampling margin tolerates ≈0.25 % (§2.4.5); (e) only PY32F002Bx5 can work at all — the
non-002B C path never configures EXTI (0ad3c42 `rv003usb.c` `#if PY32F002Bx5` around
`LL_EXTI_*`); (f) the 002B is officially a 24 MHz part — 48 MHz HSI exists on the current
("B-C") silicon and in the vendor LL but has no datasheet accuracy figure (§3.1). Distance to
"WG015 standard": engine ≈ 30 % of the work (fix + RAM-TX + servo + contracts), everything else
(own header/startup/ld/Makefile, C seams, demos, DFU chip port, bench, docs, CI) is absent.
Recommended architecture: **separate Thumb engine file** (ISA forbids a shared body) that obeys
the same per-site macro vocabulary as `rv003usb.S`, one `usb_port_<chip>.h` per target for the C
seams (replacing all `#if` ladders with a single include selector), **no vendor submodule**
(self-written minimal header/startup/ld as in `rv003usb/wg015/`), RX+TX+literals+descriptors in
RAM, keepalive servo on the 13-bit HSI trim, DFU loader reusing `bootloader_dfu/dfu.c` unchanged.

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
| This repo | `doc/wg015/*`, `rv003usb/wg015/*`, `bootloader_dfu/*`, `rv003usb/rv003usb.S` | target architecture to mirror |

Tooling used and reproducible: `arm-none-eabi-gcc 13.2.1` (apt), `riscv64-unknown-elf-gcc`
(present), PyMuPDF for PDF text, a 60-line objdump cycle walker (Appendix B) that reproduced
every RX slot at exactly 32/64 cycles.

## 2. The ARM engine (`rv003usb-arm.S`, 573 lines) in detail

### 2.1 Placement: what runs from RAM, what from flash, and why it works at all

| Region | Lines | Section | Address in the rebuilt demo_gamepad (PY32F002Bx5) | Why |
|---|---|---|---|---|
| rxbuf | arm.S:30-33 | `.bss.rxbuf`, 3+USB_BUFFER_SIZE = 15 B | 0x20000180 | packet store; PID at +3 so payload at +4 is word-aligned (C uses `__builtin_assume_aligned(data,4)`, c:260) |
| RX ISR core (entry → SE0/keepalive trampolines) | arm.S:36-225 | `.pushsection .datacode,"ax"` … `.popsection` | 0x2000000c-0x20000108 = **252 B of RAM** incl. an 8-word literal pool at 0x200000e8 | bit-critical; must be 0-wait-state |
| Dispatch (PID decode, C calls, EXTI ack) | arm.S:227-343 | `.text` (flash) | se0_complete_flash=0x080001a0 … interrupt_complete=0x08000214 | "not time-critical, continue in flash to conserve RAM" (arm.S:211-212) |
| TX engine (`usb_send_empty`/`usb_send_data` … release) | arm.S:345-569 | `.text` (**flash**) | 0x08000222-0x08000357 | RAM scarcity (3 KB) — this is the fragile choice, §2.6 |
| `always0` | arm.S:571-573 | `.text` (flash) | 0x08000358 | data source for `usb_send_empty` (read by `ldrb` inside the TX cell, arm.S:463) |

How `.datacode` lands in RAM: the vendor script `Libraries/LDScripts/py32f002bx5.ld:111-120`
places `*(.data*)` into `.data >RAM AT> FLASH`; the section name `.datacode` matches the
`.data*` glob, and `startup_py32f002b.s:42-57` copies `_sidata→_sdata` at reset. There is no
explicit rule — any linker script without that glob would silently execute the ISR from flash
(GNU ld orphan placement puts an `"ax"` orphan after `.text`). The plan replaces this with an
explicit `.timecrit` output section (T1), as `rv003usb/wg015/wg015_common.ld:52-60` does.

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

### 2.3 RX slot structure and register file

Registers (arm.S:22-27, 79, 91, 237, 375, 388): r0 = last masked bus state, r1 BITCOUNT,
r2 write pointer (rxbuf+3), r3 SHIFT_BUF, r4 SCRATCH, r5 sample/temp, r6 BITSTUFF (6→0),
r7 CRC, r9 GPIO_BASE, r12 pin mask (RX) / bit length (TX), r14 POLY_RX (RX only; legal because
EXC_RETURN was pushed at arm.S:44-47 and `pop {…,pc}` at arm.S:342 performs the exception
return), r8 FLIP_MASK (TX only), r10/r11 unused. Thumb-1 pressure: every 16-bit ALU op needs
r0-r7, so each sample costs `mov r5,r9; mov r4,r12` (2 cycles) before `ldr r5,[r5,#IDR]`.
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
`0x14`/`0x1e` CRC5 for tokens) exactly like S:318-329.

### 2.4 Findings on the RX path (numbered F-*, referenced by tasks)

| # | Finding | Evidence | Severity |
|---|---|---|---|
| F1 | Endpoint bound off-by-one: `cmp r2,#ENDPOINTS; bhi done` accepts `endp == ENDPOINTS` → `ist->eps[ENDPOINTS]` written by `usb_pid_handle_setup/out/in` (c:485-494, 241-247) → memory after the struct corrupted. RISC-V uses `bgeu a2,s0` (S:528) | arm.S:276-277; disassembly `cmp r2,#2; bhi.n` | bug |
| F2 | No packet length bound: r2 increments per byte forever (RISC-V bounds by `s1 = USB_BUFFER_SIZE*8`, S:309/408). Long noise/garbage → writes past `rxbuf` (15 B) into .bss/heap/stack | arm.S:145 "TODO: prevent buffer overrun", 146-148 | robustness |
| F3 | Stuffed-bit slot not validated (RISC-V: `c.beqz a0, done_usb_message` S:461) — a stuffing violation is silently accepted (CRC catches most) | arm.S:200-209 | minor |
| F4 | `handle_se0_keepalive` is a two-instruction stub: no SE0→SE0 frame measurement, no HSI trim, no `delta_se0_cyccount` telemetry | arm.S:217-220 vs S:740-806 | design gap, blocks production use on HSI (§2.4.5) |
| F5 | Sample phase ≈ 8-12/32 (early). Fast-clock drift margin ≈ 8-12 cycles, slow-clock ≈ 20 | §2.2 | tune (`DELAY(71)` → 78, verify on LA) |
| F6 | EXTI ack clears only `1<<USB_PIN_DM` in `EXTI_PR` at the end (arm.S:334-336); no equivalent of `RV003_ADD_EXTI_MASK/HANDLER` (S:113-129, 600-614) — another EXTI line on the same vector (EXTI2_3 = lines 2 and 3; EXTI4_15 = 12 lines) would livelock | RM002B p97 vector table | feature gap |
| F7 | NVIC priority never programmed (`NVIC_EnableIRQ` only, 0ad3c42 c:157) → USB IRQ at priority 0 but so is every other IRQ incl. SysTick; an equal-priority ISR is not preempted and delays entry by its full length (window is +55 cycles) | RM002B p97 "4 programmable priority levels" | must fix in port header |
| F8 | `RV003USB_OPTIMIZE_FLASH=1` unsupported: no Thumb `usb_pid_handle_ack/setup`; the .S always `blx`es the C versions, which are compiled out under that flag (c:471-495) → link error | arm.S:257,300 | constraint (DFU configs use the flag) |

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
mechanism the CH32V003 port relies on (S:781-797, 5-bit trim there).

### 2.5 TX path (arm.S:345-569)

| Step | Lines | Mechanism |
|---|---|---|
| Bus turnaround | 362-365 | `BSRR = (1<<DP) | (1<<(DM+16))` **before** enabling drivers (preset K), then |
| Drivers on | 367-372, 384 | read-modify-write of `MODER` to `01` (output) on DP/DM; 30 cycles from entry to the MODER store (walker) |
| NRZI | 387-389, 426, 476-478 | `r5` = absolute BSRR word for the pair; `FLIP_MASK r8 = set+reset bits of both pins`; `eor r5, r8` swaps J/K; `str r5,[GPIO,#BSRR]` (1 cycle, IOPORT). Identical idea to S:871 `t1` |
| Bit stuffing | 412, 428, 482, 501-502, 527-533 | `BITSTUFF r6` 6→0 → `insert_stuffed_bit`: 5-6× `b .+2` then `b flip_bus` |
| CRC16 | 466-474, 496-499, 514-525 | in-slot, sent LSB-first after the payload; `poly_function=2` disables (usb_send_empty = token + two 0x00 bytes = a ZLP with CRC 0x0000, 345-351, same as S:823-828) |
| SE0/EOP | 535-552 | `BSRR = reset both` → 17× `b .+2` → `BSRR = set DM` (J) → 6× `b .+2` |
| Release | 556-564 | `MODER` RMW back to input (`eor` of the `01` bits) |

Walker numbers under the 0-WS model (what these paths would take **from RAM**): pre_and_tok
zero 20 / one 19 (store index 9 / 8); send_inner zero 21, one 21, zero-EOB 21, one-EOB 20,
one+stuffed 40 (target 64), zero-path store index 11, stuffed store index 30; last data bit →
CRC byte-1 → loop top 23; SE0 width 37; J-park → release 19; entry → first preamble store 51.
None of these is 32 — the loops reach ≈32 **only because they execute from 1-wait-state
flash** (RM002B p38: LATENCY=1 → "two system clock cycles are required for each Flash read";
≈11 word fetches per iteration ≈ +11 cycles). That is the whole reason for the per-part
`#if PY32F002Bx5` nop variants (arm.S:402-408, 415-424, 444-446, 490-492, 530-532) and for
the alignment assert `.ifeq (pre_and_tok_send_inner_loop - usb_send_data) & 2; .error` (arm.S:421-423).

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
| "M0+ prefetch makes taken-branch cost depend on target alignment" (49) | Unproven; the assert exists but the effect is a flash-fetch artifact — from 0-WS RAM the TRM gives `B` = 2 regardless (Table 3-1). Bench item |
| "Per-variant cycle deltas `#if PY32F002Bx5`" (50) | Misleading: that variant was never built (§2.6) |
| "Thumb register pressure … r8/r9/r12/r14 as slow spill homes" (54) | True; note `r10/r11` are free and the plan uses them for the debug marker (§7.3) |
| "Startup/vector table/linker: entirely vendor template's" (66) | True; the plan drops the template (§6.3) |
| "USB_DM_IRQ abstracted once" (63) | True; kept, moved into the port header |
| Lessons 1-5 / anti-patterns 1-4 (68-87) | Adopted; this plan is their application |

## 3. Chip facts (verified)

### 3.1 Which parts can do 48 MHz

| Part | Max f (DS) | 48 MHz path | Flash / RAM | Verdict |
|---|---|---|---|---|
| PY32F002B (branch's target) | **24 MHz** (DS002B p2 "Up to 24 MHz as a maximum frequency"; p5 "Max. CPU frequency 24 MHz"; puyasemi product page "Max CLK 24") | HSI 48 MHz mode: DS002B p10 clock figure "HSI RC 24/48MHz"; RM002B p59 figure "HSI RC 48MHz" but p63 `HSI_FS` "100:24 MHz others: reserved"; **RMBC** (PY32F002B-C RM V1.0) p14 "CPU CORTEX-M0+ fmax= 48MHz", p58 `HSI_FS: 000:4 MHz 001:8 MHz 100:24 MHz 101: 48 MHz`, p29/p31 factory trim word for 48 MHz at `0x1FFF0104`, p24/p30 flash timing set for HSI 48 MHz at `0x1FFF0130…0x1FFF0140`; vendor LL `LL_RCC_HSICALIBRATION_48MHz = *(0x1FFF0104)&0xFFFF` (py32f002b_ll_rcc.h:386), `HSIFreqTable[5]=48000000` under `RCC_HSI48M_SUPPORT` (system_py32f002b.c:67-68, py32f002bx5.h:2221). No PLL, no HSE (DS002B p2 lists HSI/LSI/LSE/external clock input only; `RCC_CR_HSEON` absent from py32f002bx5.h) | 24 K / 3 K, page 128 B, sector 4 K (RM002B p22) | **Target #2 (cost-down)**, HSI-only, servo mandatory, treat 48 MHz as "documented in RM B-C, unspecified in DS" (open question OQ1) |
| PY32F030x6/x8 | **48 MHz** (DS030 p2 "Up to 48 MHz"; p5) | PLL ×2 from HSI 24 MHz or HSE 4-32 MHz (DS030 p2 "PLL (supports 2 octaves for HSI or HSE)", p18 figure "HSI RC 24MHz X2 PLL"; DS030 p64 PLL table: output 48 MHz, `tLOCK 15…40 µs @ fPLL_IN=24MHz`; RM030 p74 §8.1.5, p77 `PLLON/PLLRDY`, p83 `PLLSRC 0:HSI 1:HSE`) | 32/64 K, 4/8 K RAM (template README) | **Target #1 (development/reference)**: crystal option (24 MHz HSE → servo off) or HSI (servo on); single-cycle multiplier (DS030 p17) |
| PY32F003 | 32 MHz (DS003 p1) | none | | excluded (33.3 cyc/bit impossible) |
| PY32F002A | 24 MHz (DS002A p2), HSE 4-24 MHz, no PLL | none | | excluded |

Sanity: the template README's "PY32F0xx up to 48 MHz" is wrong for 003/002A; datasheets win.

### 3.2 Core and timing

| Fact | Source |
|---|---|
| Cortex-M0+, 2-stage pipeline, single-cycle multiplier on PY32 | DS002B p8, DS030 p17 ("single-cycle multipliers"); TRM p1-5 Table 1-1 (multiplier "Fast or small") |
| Interrupt latency 15 cycles (zero WS), LDM/STM abandoned+restarted, late-arrival/tail-chain | TRM p3-10 §3.6.1 |
| Instruction costs: MOV/ALU 1; `B<cc>` 1/2; `B` 2; `BL` 3; `BX/BLX` 2; `LDR/STR/LDRB/STRB` "2 or 1 — 2 if to AHB interface or SCS, 1 if to single-cycle I/O port"; `PUSH` 1+N; `POP{…,PC}` 3+N; `NOP` 1; `MULS` 1 or 32 | TRM p3-4…3-7 Table 3-1 + footnotes b, e |
| Single-cycle I/O port: "accessible both by loads and stores … You cannot execute code from the I/O port"; optional | TRM p2-3 §2.2.2, p1-5 |
| GPIO is on that port: memory map row "0xE000 0000… M0+ IOPORT 0x5000 …" and the system diagrams show "IOPORT" between the core and PORT A/B/C(/F); GPIO feature list "Fast toggle capable of changing every single cycle" | RM002B p15-18, p76; RM030 p18-20, p100; DS030 p16, p54 |
| Fetch-ahead limited to 32 bits; configurable "Instruction fetch width 16-bit only or mostly 32-bit" (vendor choice unknown) | TRM p2-2 §2.2.1 note, p1-5 Table 1-1 → bench item (T6) |
| SysTick present, calibration 6000 (=1 ms @ HCLK/8 = 6 MHz → HCLK 48 MHz), `__Vendor_SysTickConfig 0` | RM002B p97 §11.1.2; RMBC p84; py32f002bx5.h:53 |
| VTOR present; vendor SystemInit writes `SCB->VTOR = FLASH_BASE|offset` (or SRAM) | py32f002bx5.h:51 `__VTOR_PRESENT 1`; system_py32f002b.c:132-137 |
| NVIC: 2 priority bits (4 levels), 32 IRQ lines | RM002B p97 §11.1.1 |
| Flash: LATENCY=1 → "two system clock cycles are required for each Flash read"; required above 24 MHz (vendor BSP sets `LL_FLASH_LATENCY_1` for 48 MHz, py32f002b_bsp_clock.c:29-30) | RM002B p38 |
| "During a program and erase operations … any attempt to read the Flash memory will stall the bus" → XIP programming is legal, CPU simply stalls; writing FLASH_CR while BSY stalls too | RM002B p23-24; RM030 p27-28 |

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
| Factory trim | HSI_TRIMMING_FOR_USER: word = `HSI_FS[15:13] | HSI_TRIM[12:0]`, "read … then write to HSI_FS and HSI_TRIM in RCC_ICSCR": 24 MHz @0x1FFF0100, **48 MHz @0x1FFF0104** | 24 MHz @0x1FFF0F10 (`(0x4<<13)|(*0x1FFF0F10 & 0x1FFF)`) | RM002B p33; RMBC p31, p59; py32f002b_ll_rcc.h:384-386; py32f0xx_ll_rcc.h:455-462 |
| Boot modes | `nBOOT1`/`nBOOT0` (option bytes): main flash / SRAM / **Load Flash** (1-4 KB at the top of main flash, `0x08005000-0x08005FFF` for 4 K, aliased at 0; pages configured as Load Flash "will not be erased" by page erase); `FLASH_BTCR` boot control register | BOOT0 pin + nBOOT1: main flash / system memory (3.5 KB ROM loader) / SRAM | RM002B p20-21 §3.6, p25, p42 §4.8.8; RM030 p21, p24-25 |
| Reset cause | `RCC_CSR` @+0x60: `IWDGRSTF`29 `SFTRSTF`28 `PWRRSTF`27 `PINRSTF`26 `OBLRSTF`25, `RMVF`23 (write 1 clears); software reset = SYSRESETREQ (`SCB->AIRCR = 0x05FA0004`) | same offsets + `WWDGRSTF`30 | RM002B p56-57 §7.1, p73; py32f030x8.h:3389-3408 |
| SRAM across system reset | RM says a system reset "sets all registers to their reset values except … the reset flag register" (RM002B p56); SRAM is not in that list and stop mode explicitly keeps SRAM (p51/p53). Retention through SYSRESETREQ is the STM32-family norm but **not stated** → OQ2, verified in T10 | |

### 3.4 Peripherals the engine touches

| Block | Layout | Source |
|---|---|---|
| GPIO (`x=A,B,C` on 002B; `A,B,F` on F0xx) | `MODER 0x00, OTYPER 0x04, OSPEEDR 0x08, PUPDR 0x0C, IDR 0x10, ODR 0x14, BSRR 0x18, LCKR 0x1C, AFR[2] 0x20/0x24, BRR 0x28`; bases `0x50000000 + 0x400·{A=0,B=1,C=2,F=5}`; BSRR: "Write any bit to 0 … does not have any effect", set wins over reset; MODER 2 bits/pin: 00 input, 01 output | py32f002bx5.h:239-251, 443-445; py32f030x8.h:265-277, 525-527; RM002B p79, p85 |
| EXTI | base `0x40021800` (AHBPERIPH+0x1800, both families): `RTSR 0x00, FTSR 0x04, SWIER 0x08, PR 0x0C (write-1-clear), EXTICR[n] 0x60+4n (port select per line, 8 bits/line: mask 3 for lines 0-4, 1 for 5-7 on 002B), IMR 0x80, EMR 0x84` | py32f002bx5.h EXTI_TypeDef; py32f002b_ll_exti.h:143-160, 649-654; RM002B p100 §11.2.4 |
| EXTI IRQ numbers | `EXTI0_1_IRQn=5, EXTI2_3_IRQn=6, EXTI4_15_IRQn=7` (vector 0x54/0x58/0x5C) | RM002B p97; py32f002bx5.h enum; startup_py32f002b.s:133-135; F030 identical (py32f030x8.h) |
| RCC | `CR 0x00 (HSION bit8, HSIRDY 10, HSIDIV[13:11]; F030: PLLON 24, PLLRDY 25)`, `ICSCR 0x04 (HSI_TRIM[12:0], HSI_FS[15:13])`, `CFGR 0x08 (SW[2:0], SWS[5:3])`, F030 `PLLCFGR 0x0C (PLLSRC bit0)`, `IOPENR 0x34 (GPIOxEN)`, `CSR 0x60` | py32f002bx5.h RCC_TypeDef; py32f030x8.h:338-359; RM030 p83 |
| SysTick | core, `0xE000E010`: CTRL/LOAD/VAL(24-bit down)/CALIB | TRM/CMSIS; RM002B p97 |
| NVIC/SCB | `ISER 0xE000E100`, `IPR 0xE000E400` (2 bits/priority in bits 7:6), `VTOR 0xE000ED08`, `AIRCR 0xE000ED0C` | CMSIS core_cm0plus.h (Apache-2.0) |
| 5 V tolerance | not specified in DS002B/DS030 pin tables (no "FT"/"5 V tolerant" anywhere) → assume **not** tolerant; run VDD = 3.3 V (USB LS signalling is 3.3 V, VBUS→LDO) | grep of both datasheets |
| Electrical | VDD 1.7-5.5 V; −40…85/105 °C; GPIO OSPEEDR exists (set high speed on D±) | DS002B p2,p5; RM002B p78 |

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
(`#if defined(WG015) && WG015` at c:64-89 of HEAD) — resolution rules in T0.

Feature flags / behaviours the ARM side never saw and must be decided per target:
`RV003USB_USB_TERMINAL` (→0/#error), `RV003USB_BOOTLOADER` hooks (usable), `RV003USB_DEBUG_TIMING`
(→#error), `RV003USB_USE_REBOOT_FEATURE_REPORT` default 1 (h:46-53) → needs the PY32 seam
(branch dodged it by setting 0 in usb_config.h), `RV003USB_OPTIMIZE_FLASH` (F8),
`RV003_ADD_EXTI_MASK` (F6), `RV003USB_SUPPORT_CONTROL_OUT` (needed by DFU, C-only, free).

## 5. Gaps versus the WG015 target

| WG015 has | PY32 branch has | Plan |
|---|---|---|
| `rv003usb/wg015/`: shim `ch32fun.h`, `K1921VG015_min.h` (self-written, license-clean), `startup_wg015.S`, `wg015_common.ld` + 2 variants, `Makefile.wg015`, stdio stub | vendor submodule (Apache/PUYA mixed), vendor startup/ld, top-level `Makefile.py32` with the object-path hack | T1: `rv003usb/py32/` with the same shape, no submodule |
| Per-site macro contracts in one `rv003usb.S` | forked Thumb file with `#if` ladders | T2: separate file **by necessity**, same macro vocabulary, zero `#if <part>` inside |
| C seams `#if WG015` in rv003usb.c + reboot seam #4 | `#if __riscv` ladders (older base) | T3: `usb_port_<chip>.h` per target, one selector |
| demo_hidapi conditioned | demo_gamepad only | T4: both demos |
| `bootloader_dfu/{dfu.c, dfu_rv003usb.h, dfu_015.h, dfu_v003.h, wg015/, v003/}` | none | T5: `dfu_py32.h` + `py32/` |
| `bootloader_wg015` HID-blob loader + 5 blobs + hidapi CLI with bcdDevice gate | none | T9 (optional, after DFU) |
| `wg015_bench/` P1 calibration set | none | T6: `py32_bench/` |
| `tools/wg015_vcd` (chip-agnostic VCD analyzer), `tools/wg015mkdfu.py` | none | reuse; T5 adds `--bcddevice/--pid` to mkdfu; T6 adds a pulse-marker option to the VCD tool |
| `doc/wg015/{PLAN,STATE,TODO,chip_info,ledger_static,review_findings}` | none | this file + T8 |
| CI builds everything (`make all`) | `build_py32` hook that needs the submodule | T7 |
| Size gates in loader Makefiles (`sizecheck`, bootloader_dfu/wg015/Makefile:14-22) | none | T5/T9 |
| Timing verification method (ledger + LA + VCD) | hand annotations only | Appendix A/B + T6/T10 |

## 6. Architecture decisions (argued both ways)

**Р1. Engine seam — sibling inside `rv003usb.S` vs separate `rv003usb-arm.S`.**
For: one file, one ledger discipline, the macro table already exists. Against: the body is
RISC-V (`c.lw`, `c.beqz`, `XW_C_LBU`, `mret`, 16 registers, `nx6p3delay`); a Thumb "body" would be
100 % `#if`, i.e. two files interleaved — worse than two files. Register allocation, stack
frames, exception return, literal pools, CRC bit tricks all differ. **Decision: separate file
`rv003usb/rv003usb-arm.S`, but it must obey the identical per-site contract vocabulary
(§7.1) with every hardware address and every pad constant coming from
`rv003usb/py32/usb_port_py32_asm.h` / `usb_port_py32_tune.h`; the file contains no
`#if <part>`.** A second Cortex-M0+ chip with a single-cycle IOPORT (e.g. STM32G0/L0) then is a
new `usb_port_<chip>_asm.h`, not a fork. The build picks exactly one engine per target
(`rv003usb.S` for RISC-V targets, `rv003usb-arm.S` for PY32); they are never linked together.

**Р2. C seam — keep the `#if` ladders vs per-target `usb_port_<chip>.h`.**
For ladders: no refactor risk to V003 bit-identity, WG015 already inlined its blocks. Against:
a third arm makes `usb_setup()` a 3-column ladder, the reboot seam a second one, and the
DEBUG_TIMING/terminal blocks a third — exactly the anti-pattern documented in branch_notes.md:86.
**Decision: per-target header with a single include selector in `rv003usb.h`:**
`#if defined(WG015)&&WG015 → "wg015/usb_port_wg015.h"; #elif defined(RV003USB_PY32) →
"py32/usb_port_py32.h"; #else → "usb_port_ch32.h"` (the V003/V00x code moved verbatim). Seams
(static inline, all `__ASSEMBLER__`-guarded): `usb_port_hw_setup()` (clock enable, pins, EXTI,
NVIC priority, DPU), `USB_PORT_REBOOT_TO_BOOTLOADER()`, `USB_PORT_DEBUG_TIMING_SETUP()` (or
`#error`), `USB_DM_IRQ`/handler symbol, `USB_PORT_TERMINAL_SUPPORTED`. Gate: V003 `demo_gamepad`
`.bin` bit-identical before/after (the WG015 branch already enforces this discipline,
STATE.md:32-35); WG015 `demo_hidapi` builds in both ld variants.

**Р3. Vendor submodule vs self-written minimal header/startup/ld.** For the submodule: tested
clock/flash code, all parts covered. Against: a 50 MB dependency for ≈300 lines actually used,
the `Build/../` object hack, LL/HAL license mix, `rules.mk` semantics (the `-D` bug), CI cost,
and inconsistency with `rv003usb/wg015/`. **Decision: no submodule.** `py32_min.h` (registers
for GPIO/RCC/EXTI/FLASH/SYSCFG/SCB/NVIC/SysTick, both families, `__ASSEMBLER__`-clean,
facts cited to RM pages), `startup_py32.S`, `py32_common.ld`+per-part ld, `Makefile.py32`.
Apps that want the vendor LL include it themselves; our header must not be included together
with a vendor device header (documented).

**Р4. Code placement.** RX ISR, TX engine, dispatch trampolines, literal pools, `always0`,
`descriptor` bytes, any `usb_send_data` source → RAM (`.timecrit` + `.rodata.usbdesc`→`.data`,
the WG015 rule "clocked-path data in RAM", PLAN Р3). Reason: flash reads are 1-WS and the
prefetch state makes `ldrb` inside a TX cell 2-or-3 cycles (RM002B p38; TRM §2.2.1 fetch-ahead).
Cost on 002B: ≈600 B code + descriptors (≈250 B demo_hidapi) out of 3 KB — budget table in T1.

**Р5. Clocking and the servo.** PY32F030: HSE 24 MHz ×2 (crystal, servo off = `USB_TRIM_ACTUATE`
empty) or HSI 24 MHz ×2 (servo on). PY32F002B: HSI 48 MHz (`HSI_FS=101` + factory word
0x1FFF0104), servo on. Keepalive path measures SysTick deltas (expect 48000/frame) and steers
`RCC_ICSCR.HSI_TRIM` (13-bit) with saturation ±64 LSB from the factory value; one LSB ≈ 0.1 %
(DS `fTRIM`), so the loop must be gentle (see T2). The frame measurement itself is unconditional
(telemetry `delta_se0_cyccount`), the actuator is a port macro — same layering as
`rv003usb.S:778-797`.

**Р6. Bootloader layout.** Uniform for both parts: loader = flash pages 0-31 (4 KB) at
0x08000000, app at `0x08001000` (VTOR-relocated Cortex-M image with its own vector table). Boot
flag = word in `.noinit` RAM qualified by `RCC_CSR.SFTRSTF` (analog of WG015 `RTC_REG[0]` +
`RSTSTAT.SYSRST`); fast-path `dfu_port_jump_app()` = VTOR + MSP + jump. The 002B-only "Load
Flash" zone (hardware-protected 4 KB at 0x08005000, app unmodified at 0x08000000) is
attractive (brick-proof) but needs option-byte provisioning and differs per family → recorded
as OQ6 / a follow-up, not the default.

**Р7. Interrupt policy.** USB EXTI IRQ priority 0, every other IRQ ≥ 1, SysTick 3; PRIMASK
critical sections ≤ 40 cycles (README.md:94 rule); no other IRQ may run at priority 0. Vector
table stays in flash (1-WS vector fetch = +1…2 constant cycles, inside the +55 window); RAM
vector table is an optional 192 B trade documented in T1.

## 7. Contracts

### 7.1 Per-site macro contract of the Thumb engine (mirrors doc/wg015/PLAN.md Р2 table)

Defined in `rv003usb/py32/usb_port_py32_asm.h` (T2 owns) from `usb_config.h` pins + `py32_min.h`:

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
| `USB_TICK_ADDR` | free-running HCLK counter | SysTick `VAL` `0xE000E018` (24-bit **down**; delta = `(last-now)&0xFFFFFF`) |
| `USB_TRIM_ACTUATE` | servo plug-in | writes `RCC_ICSCR` (empty for HSE builds) |
| `USB_DBG_MARK_SET/CLR` | zero-intrusion marker (§7.3) | `str r10-derived,[dbgport,#BSRR/#BRR]` |
| `USB_RX_ENTRY_DELAY`, `USB_RX_SYNC_DELAY`, `USB_TX_*_PAD` | pads | `usb_port_py32_tune.h` (defaults 96, 71→78, and the T2 TX pads) |

### 7.2 Engine ↔ C ABI (unchanged from RISC-V; the C layer must not know the ISA)

Exports: `usb_send_data(const void*, uint32_t len, uint32_t poly_function, uint32_t token)`,
`usb_send_empty(token)`, `always0`, the vector handler. Calls (all 5-arg, `ist` on the stack):
`usb_pid_handle_ack/in/out/setup/data` (h:91-95). Reads `rv003usb_internal_data.my_address` at
`MY_ADDRESS_OFFSET_BYTES` (1 or 4 with OPTIMIZE_FLASH, h:125/140 — `ldrb` at either is fine on
little-endian). Uses `ENDPOINTS`, `USB_BUFFER_SIZE`, `USB_DMASK` (h:120-122).

### 7.3 Zero-intrusion debug marker (port of Р10)

BSRR write of 0 is architecturally a no-op ("Write any bit to 0 in GPIOx_BSRR does not have
any effect", RM002B p79) — exact analog of WG015's MASKLB[0]. Marker = `mov r4, r10; str r4,
[r5, #BSRR]` right after the sample (r5 = port base, r4 scratch, r10 = mask loaded from a RAM
word at ISR entry, 0 in production) and `… [r5, #BRR]` (0x28) at the slot tail = a pulse per
slot, 4 cycles taken from existing padding, instruction stream identical in TUNE and
production. `tools/wg015_vcd` needs a `--marker-edge rise` option (T6) because it assumes one
toggle per sample.

## 8. DFU: the chip-port contract and the PY32 sketch

Contract extracted from `bootloader_dfu/dfu.c` (every symbol a `dfu_chip.h` must provide):

| Symbol | Used at | PY32 implementation (`bootloader_dfu/dfu_py32.h`) |
|---|---|---|
| `DFU_APP_BASE` | dfu.c:78,101,104,142,168 | `0x08001000` (Р6) |
| `DFU_FLASH_END` | :102,144,170-171 | `0x08000000 + FLASH_SIZE` (24 K / 32 K / 64 K from the Makefile MCU) |
| `DFU_PAGE_SIZE` | :124 (erase when block starts a page) | `128` |
| `DFU_XFER_SIZE` | :66,142-144,168-171,227; transport buffer dfu_rv003usb.h:22 | **`128`** — the RM allows whole-page programming only (RM002B p24 "programmed the entire page"), so one DFU block = one page (erase+program every block; `DFU_POLL_ERASE_MS` always applies). Also `wTransferSize` in `usb_config.h` = 128 |
| `DFU_CYCLES_PER_MS` | :223 (3 ms quiet), :245 (25 ms manifest) | `1` — `dfu_port_cycles()` returns milliseconds (see cycles) |
| `DFU_POLL_ERASE_MS`, `DFU_POLL_PROG_MS` | :124-125 | `8`, `8` (tERASE max 5 ms + tprog max 1.5 ms, DS002B p39; one block = both) |
| `DFU_FLAG_APP`, `DFU_FLAG_STAY` | :204,207 | `0x0AFF10AD`, `0xB00710AD` (same values as WG015 for tooling parity) |
| `dfu_port_cycles()` | :127,161,223,245 | SysTick IRQ at priority 3 increments `volatile uint32_t dfu_ms`; returns it. (SysTick VAL is 24-bit → 349 ms wrap; the core subtracts unsigned 32-bit, so a raw counter would terminate waits early on wrap.) |
| `dfu_port_irq_disable/enable()` | :231,233 | `cpsid i` / `cpsie i` |
| `dfu_port_flag_read_and_clear()` | :202 | `r = RCC->CSR; RCC->CSR \|= RMVF; f = boot_flag; boot_flag = 0; return (r & SFTRSTF) ? f : 0;` with `uint32_t boot_flag __attribute__((section(".noinit")))` placed by the ld after `.bss` (startup never touches it). Optional double-tap (500 ms, samd11 idiom) on a second `.noinit` word, also qualified by `!PWRRSTF` |
| `dfu_port_reboot_to_app()` | :248 | `boot_flag = DFU_FLAG_APP; SCB->AIRCR = 0x05FA0004; while(1);` (SYSRESETREQ, RM002B p57 §7.1.5) |
| `dfu_port_jump_app()` | :205,208 | validate `sp ∈ SRAM`, `pc ∈ [APP_BASE,FLASH_END)` with Thumb bit; `SCB->VTOR = DFU_APP_BASE; __set_MSP(app[0]); ((void(*)(void))app[1])();` (VTOR present, §3.2; runs before `usb_setup`, near-reset state) |
| `dfu_port_flash_timebase_init()` | :212 | start the SysTick ms tick; load the flash timing registers `TS0…PRETPE` from the factory set for the running HSI mode (§3.3) — mandatory before any program/erase |
| `dfu_port_flash_write_block(addr, src)` | :232 (IRQs masked around it) | XIP is legal (bus stalls, RM002B p23): `KEYR=KEY1,KEY2` if `CR.LOCK`; `CR\|=PER; *(vu32*)addr=0xFF; wait !BSY; CR&=~PER; CR\|=PG; for i<31: dst[i]=src[i]; CR\|=PGSTRT; dst[31]=src[31]; wait !BSY; CR&=~PG; CR\|=LOCK` (RM002B p24-25; HAL `FLASH_Program_Page`/`FLASH_PageErase`) |
| `DFU_ENABLE_UPLOAD`, `DFU_ENABLE_APPCRC` | :30-35 | both 1 on F030; measure on 002B (4 KB budget) |

Transport (`dfu_rv003usb.h`, unchanged) needs `usb_config.h` with `RV003USB_OTHER_CONTROL 1`,
`RV003USB_SUPPORT_CONTROL_OUT 1`, `ENDPOINTS 1`, descriptors in `.rodata.usbdesc` (RAM), and
`RV003USB_OPTIMIZE_FLASH 0` until F8 is closed. Image convention: length word at app+0x10 = M0+
vector slot 4 (reserved, `.word 0` in every startup) → `wg015mkdfu.py`'s "0 or 0xFFFFFFFF"
check passes; CRC32 covers `[base, len-4)`; loader gates on it (dfu.c:98-106).

## 9. Work breakdown (parallel fleet; file ownership is disjoint; waves are the dependency order)

Conventions for all tasks: branch = the T0 result; build commands run from the repo root;
`ARMCC=arm-none-eabi-` (gcc ≥ 13), RISC-V builds via `ch32fun` submodule (`git submodule update
--init`); size numbers from `arm-none-eabi-size`; "walker" = `tools/py32_cyc.py` (Appendix B
until T2 lands it). Macro conventions: `-DRV003USB_PY32=1` on every PY32 compile/assemble line,
plus exactly one of `-DPY32F002B=1` / `-DPY32F030=1` (family), plus `-DPY32_FLASH_KB=<n>
-DPY32_SRAM_KB=<n>` (from the Makefile `MCU=` value). No task edits a file it does not own; if
a task needs a change elsewhere it writes the exact request into `doc/py32/STATE.md` (T8's
file — append-only section "requests", the one shared exception).

### Wave 0

**T0 — Starting state (ONE agent, alone, before anyone else) — tier: medium**
Goal: a branch containing WG015 work + master 80b1893 + the PY32 branch content, building
green for RISC-V and WG015, with the vendor scaffolding removed.
Files: everything the merge touches (exclusive because nobody else runs yet).
Procedure (dry-run verified):
1. `git checkout -b py32-port claude/wg015-bitbang-usb-port-bxuu7w && git merge --no-edit 80b1893`
   (clean: `bootloader/usb_config.h` 2 lines).
2. `git fetch origin py32 && git cherry-pick -x 0ad3c42` → conflicts: `.gitignore`, `.gitmodules`,
   `Makefile`, `demo_gamepad/demo_gamepad.c`, `demo_gamepad/usb_config.h`, `rv003usb/rv003usb.c`.
3. Resolve: `.gitignore` = HEAD + append `*.o`, `*.d`, `Build/`; `.gitmodules` = HEAD (ch32fun
   only) and `git rm --cached py32f0-template && rm -rf py32f0-template`; `Makefile` = HEAD
   (T7 adds the PY32 hook later); `demo_gamepad/demo_gamepad.c` = HEAD (`#include "ch32fun.h"`,
   no BSP calls — clocks belong to startup); `demo_gamepad/usb_config.h` = HEAD's flag block, no
   pin ladder (T4 adds it); `rv003usb/rv003usb.c` = HEAD entirely (drop all LL includes and
   `#if __riscv` forks); `rv003usb/rv003usb.h` auto-merged (keeps the `USB_DM_IRQ` block, T3
   moves it). `git rm -r .vscode Makefile.py32`. Keep `rv003usb/rv003usb-arm.S` byte-identical.
4. Commit with message listing the resolutions; push.
Acceptance: `git submodule status` shows only `ch32fun`; `make -C demo_gamepad`,
`make -C demo_hidapi`, `make -C bootloader`, `make -C bootloader_dfu/v003`,
`make -C bootloader_dfu/wg015 PREFIX=riscv64-unknown-elf-`, `make -C demo_hidapi -f
../rv003usb/wg015/Makefile.wg015 PREFIX=riscv64-unknown-elf-` all succeed; `git status` clean;
`rv003usb/rv003usb-arm.S` present.

### Wave 1

**T1 — `rv003usb/py32/` target skeleton and build — tier: hard**
Files (create): `rv003usb/py32/py32_min.h`, `rv003usb/py32/ch32fun.h`,
`rv003usb/py32/startup_py32.S`, `rv003usb/py32/py32_common.ld`, `rv003usb/py32/py32f002b.ld`,
`rv003usb/py32/py32f030x6.ld`, `rv003usb/py32/py32f030x8.ld`, `rv003usb/py32/Makefile.py32`,
`rv003usb/py32/py32_stdio_stub.c`, `rv003usb/py32/README.md`.
Depends on: T0.
Content: `py32_min.h` — structs/offsets/bit masks from §3.3-3.4 with a `_Static_assert` per
struct size and offset (`offsetof(GPIO_TypeDef,BSRR)==0x18`, `RCC.CSR==0x60`, `EXTI.PR==0x0C`,
`EXTICR[0]==0x60`, `IMR==0x80`, FLASH `CR==0x14`, `SR==0x10`, `TS0==0x100`…), family switches
(`PY32F002B`: ports A/B/C, no PLL, HSI_FS 48; `PY32F030`: ports A/B/F, PLL, HSI_FS 24; trim and
flash-timing addresses per §3.3); every block cites its RM page; assembler-clean (no `UL`).
`ch32fun.h` shim (mirror `rv003usb/wg015/ch32fun.h`): includes `py32_min.h`; `NVIC_EnableIRQ`
(`ISER`), `NVIC_SetPriority` (2-bit `IPR`), `NVIC_SystemReset`, `__disable_irq/__enable_irq`,
`SysTick` struct + `PY32_systick_freerun()` (LOAD=0xFFFFFF, CLKSOURCE=HCLK, ENABLE, no IRQ),
`Delay_Ms/Delay_Us` (SysTick polling, wrap-safe), `SystemInit()` no-op (clocks are in startup),
`FUNCONF_SYSTICK_USE_HCLK` satisfied by construction, `#error` on `RV003USB_USB_TERMINAL`/
`RV003USB_DEBUG_TIMING`. `startup_py32.S`: vector table (48 words, weak `Default_Handler`, EXTI
symbols exactly as `startup_py32f002b.s:133-135`), `Reset_Handler`: SP, copy `.data` (incl.
`.rodata.usbdesc`), copy `.timecrit` (LMA flash → VMA RAM), zero `.bss` (never `.noinit`),
clock init per family — 002B: `FLASH->ACR = LATENCY_1` then `RCC->ICSCR = (ICSCR & ~0xFFFF) |
(*(uint32_t*)0x1FFF0104 & 0xFFFF)`, HSION, wait HSIRDY (order as py32f002b_bsp_clock.c:27-46);
030: `HSI_FS=100` trim from `0x1FFF0F10`, HSION, `PLLCFGR.PLLSRC=HSI` (or HSE: HSEON, wait
HSERDY, `PLLSRC=HSE`, `HSE_VALUE` must be 24000000), `PLLON`, wait `PLLRDY`, `ACR=LATENCY_1`,
`CFGR.SW=PLL`, wait `SWS` (RM030 p77,p83; LL `UTILS_EnablePLLAndSwitchSystem`) — then `VTOR =
__vector_table`, `bl main`. Linker: `.isr_vector`(flash) → `.data` + `.rodata.usbdesc` (RAM AT
flash) → `.timecrit` (RAM AT flash, `KEEP`; also catch `*(.datacode)` so the unmodified branch engine
lands in RAM until T2 renames its section) → `.text/.rodata` (flash) → `.bss` → `.noinit
(NOLOAD)` → stack top = end of RAM; `ASSERT(stack ≥ 512)`; `PROVIDE(__timecrit_lma/start/end,
__data_*, __bss_*)`. `Makefile.py32` (mirror `Makefile.wg015`): `MCU ?= PY32F030x8`
(also `PY32F002Bx5`, `PY32F030x6`), `SOURCES := $(TARGET).c rv003usb/rv003usb-arm.S
rv003usb/rv003usb.c startup stub`, flags `-mcpu=cortex-m0plus -mthumb -Os -ffunction-sections
-fdata-sections -nostartfiles -specs=nano.specs -DRV003USB_PY32=1 -DPY32F0xx… -I<py32 dir
first> -I../rv003usb -I../lib`, targets `all/size/lst/bin/flash (pyocd, openocd -f target/py32f0xx?)/clean`,
`--print-memory-usage`, `sizecheck` hook variable. `README.md`: pin defaults (D+=PB0, D−=PB3,
DPU=PB2 as the branch; F030 same), clock options, RAM budget, IRQ policy (Р7).
Acceptance: `make -C demo_gamepad -f ../rv003usb/py32/Makefile.py32 MCU=PY32F002Bx5` and
`MCU=PY32F030x8` link a stub `main` (T1 may add a throwaway `rv003usb/py32/selftest_main.c`
it owns) with the **branch's** `rv003usb-arm.S` (unmodified) and master `rv003usb.c` compiled
with `-DRV003USB_PY32` stubs for the two seams (`usb_port_hw_setup` may be a T1-local weak stub
until T3 lands); map shows `.timecrit` VMA in SRAM / LMA in flash and `.isr_vector` at
0x08000000 with word0 = stack top, word1 = `Reset_Handler|1`; `arm-none-eabi-objdump -h`
shows `.noinit` outside `.bss`; static asserts compile.

**T3 — C-layer seams: per-target `usb_port_<chip>.h` — tier: medium**
Files: `rv003usb/rv003usb.c`, `rv003usb/rv003usb.h`, `rv003usb/usb_port_ch32.h` (new),
`rv003usb/wg015/usb_port_wg015.h` (new), `rv003usb/py32/usb_port_py32.h` (new).
Depends on: T0 (T1 for the PY32 compile check; T3's PY32 header includes `py32_min.h` by name).
Content: implement Р2 exactly: `rv003usb.h` gets the single selector and declares the seam
API; `rv003usb.c` `usb_setup()` becomes `rv003usb_internal_data.se0_windup=0; usb_port_hw_setup();`
with the V003/V00x body (c:59-153 incl. DEBUG_TIMING) moved verbatim into `usb_port_ch32.h`
and the WG015 body (HEAD c:64-89) into `usb_port_wg015.h`; the reboot block (c:173-186 and the
WG015 variant) becomes `USB_PORT_REBOOT_TO_BOOTLOADER()`; `USB_DM_IRQ` block (from 0ad3c42) moves
into the PY32 header; `RV003USB_USB_TERMINAL` and `RV003USB_DEBUG_TIMING` are `#error` when
`RV003USB_PY32`. PY32 `usb_port_hw_setup()`: `RCC->IOPENR |= GPIOxEN`; DP/DM `MODER=00,
PUPDR=00, OSPEEDR=11`; DPU `MODER=01`, `BSRR` high; `EXTI->EXTICR[DM>>2]` port select (mask
per line as py32f002b_ll_exti.h:153-160 — lines 0-4 are 3-bit fields, 5-7 1-bit fields);
`EXTI->IMR |= 1<<DM; EXTI->FTSR |= 1<<DM; EXTI->PR = 1<<DM`; `NVIC_SetPriority(USB_DM_IRQn,0)`;
`NVIC_EnableIRQ`. `USB_PORT_REBOOT_TO_BOOTLOADER()`: `py32_boot_flag = 0xB00710AD;
NVIC_SystemReset()` (`py32_boot_flag` declared `extern` in `.noinit`, defined in the shim).
Acceptance: (1) `make -C demo_gamepad` (CH32V003) `.bin` byte-identical to the T0 build (`cmp`);
(2) WG015: `make -C demo_hidapi -f ../rv003usb/wg015/Makefile.wg015` and `bootloader_dfu/wg015`
build, `.bin` identical or the diff explained in the commit message; (3) PY32:
`rv003usb.c` compiles for both `MCU`s with `-Wall -Werror`.

**T8 — Documentation set — tier: mechanical**
Files (create): `doc/py32/chip_info.md` (§3 expanded with page refs), `doc/py32/ledger_arm.md`
(Appendix A + the target TX ledger), `doc/py32/STATE.md` (fleet progress + "requests" section),
`doc/py32/TODO.md`.
Depends on: T0. Acceptance: every fact carries a source; `STATE.md` lists every task with owner.

### Wave 2

**T2 — Engine: contracts, RAM TX, fixes, servo, marker — tier: hard**
Files: `rv003usb/rv003usb-arm.S`, `rv003usb/py32/usb_port_py32_asm.h` (new),
`rv003usb/py32/usb_port_py32_tune.h` (new), `tools/py32_cyc.py` (new, from Appendix B).
Depends on: T1 (build, `py32_min.h`), T3 (`USB_DM_IRQ` moved).
Content, in order:
1. Replace arm.S:3-17 and every literal with §7.1 macros; delete all `#if PY32F002Bx5` (§2.6);
   `#include "usb_port_py32_asm.h"` only.
2. Put the whole file's code (ISR, dispatch, TX, `always0`) in `.section .timecrit,"ax"`;
   `.ltorg` after each block; `rxbuf` → 4+USB_BUFFER_SIZE+4 bytes.
3. F1: `bhi` → `bhs` (arm.S:277). F2: at `is_end_of_byte` add `cmp r2, r8; bhs done_usb_message`
   with r8 = `rxbuf+4+USB_BUFFER_SIZE` loaded at entry (r8 is free in RX; `cmp lo,hi` is 1 cycle);
   rebalance: EOB tail becomes 5 → keep 32 by removing 3 cycles from `DELAY_CYCLES(6)` for all
   paths and adding 3 `nop` to the two non-EOB tails (walker must show 32/32/32/32/64). F3:
   after `rx_stuffed`'s delay sample once and `beq done_usb_message` if no transition (costs 4
   cycles inside the 24-cycle delay — shorten `DELAY(24)` accordingly).
4. TX re-pad to the Appendix A targets: every `pre_and_tok`/`send_inner` path = 32, stuffed = 64,
   store index equal on zero/one paths (pad the one-path before its store), stuffed store at
   32+11, SE0 width 64 (2 bit-times; V003 ships ≈48 — tunable `USB_TX_SE0_PAD`), J-park hold ≥ 16
   before release; the `.ifeq` alignment assert is deleted (bench T6 decides if RAM alignment
   matters — if it does, `.balign 4` on loop heads is the fix, not the assert).
5. Keepalive: on the SE0 branch measure `USB_TICK` delta, store `last_se0/delta_se0/se0_windup`
   (h:190-192), sanity ±4000 like S:762-772, then `USB_TRIM_ACTUATE`: HSI build → `ICSCR.HSI_TRIM
   = trim0 − sat(windup>>K, ±64)` with `trim0` captured at first keepalive; HSE build → nothing.
   Runs in `.timecrit` too (it is short) — must return within ~1 bit-time.
6. Marker (§7.3): r10 = mask from `usb_dbg_mask` (RAM word), pulse per slot; production mask 0.
7. F6: `RV003_ADD_EXTI_MASK/HANDLER` port: on entry check `EXTI->PR & USB_DMASK`; if zero, jump
   to the user hook (flash), ack `RV003_ADD_EXTI_MASK` at exit (mirror S:113-129/645-650).
8. F8: Thumb `usb_pid_handle_ack`/`usb_pid_handle_setup` under `RV003USB_OPTIMIZE_FLASH`
   using `EP_*_OFFSET` (h:133-138) — ≈40 B each; keeps DFU configs unchanged.
9. NVIC priority is T3's job; document in the file header the [11,74]-cycle entry window.
Acceptance: assembles for both `MCU`s with `-Wa,--fatal-warnings`; `tools/py32_cyc.py
<elf>` prints all RX paths = 32 (stuffed 64), all TX paths = 32/64 with the store indices
stated in the file header; `nm` shows every engine symbol inside `.timecrit` (SRAM); `.timecrit`
≤ 900 B; demo_gamepad links on 002B with ≥ 768 B stack (ld ASSERT); V003/WG015 builds untouched
(files not owned). Hardware validation is T10.

**T4 — Demos conditioned for PY32 — tier: mechanical**
Files: `demo_gamepad/usb_config.h`, `demo_gamepad/funconfig.h`, `demo_gamepad/demo_gamepad.c`,
`demo_gamepad/README.md`, `demo_hidapi/usb_config.h`, `demo_hidapi/funconfig.h`,
`demo_hidapi/demo_hidapi.c`, `demo_hidapi/README.md`.
Depends on: T1, T3.
Content: pins under `#if defined(RV003USB_PY32)` (`USB_PORT B, DP 0, DM 3, DPU 2`) else the
WG015/V003 blocks already present; `funconfig.h` unchanged for V003 (T4 must not break the
bit-identity gate — flags only under `RV003USB_PY32`); descriptors get the `USBDESC` section
attribute exactly as `bootloader_dfu/wg015/usb_config.h:39` when `RV003USB_PY32` (RAM
placement, Р4); `demo_hidapi.c` WS2812/GPIOD block guarded `#if !defined(RV003USB_PY32) &&
!(defined(WG015)&&WG015)`; `Delay_Ms(1)` before `usb_setup()` kept (h TDDIS note); README build
lines: `make -f ../rv003usb/py32/Makefile.py32 MCU=PY32F002Bx5`.
Acceptance: both demos build for both `MCU`s; RAM ≤ 2200 B on 002B (`--print-memory-usage`);
V003 `demo_gamepad.bin` unchanged vs T0.

**T6 — Calibration bench firmware + walker/VCD extension — tier: medium**
Files (create): `py32_bench/Makefile`, `py32_bench/main.c`, `py32_bench/bench_common.{c,h}`,
`py32_bench/bench_kernels.S`, `py32_bench/bench1_ioport.c` (LDR/STR IOPORT vs AHB vs literal,
via SysTick deltas over 1000× unrolled kernels), `bench2_branch.c` (taken/untaken, aligned/
unaligned targets, RAM vs flash, 16- vs 32-bit fetch signature), `bench3_irq.c` (EXTI entry
latency: marker on first ISR instruction, LA measures; vector table flash vs RAM),
`bench4_flash.c` (straight-line and branchy fetch profile from flash), `bench5_slot.c`
(isomorphic RX slot from RAM with PRBS + evictor, cumulative excursion), `bench6_trim.c`
(HSI_TRIM LSB weight: step ±1 and count SysTick vs an external 1 kHz reference or MCO on LA);
modify `tools/wg015_vcd/wg015vcd.py` + `tools/wg015_vcd/README.md` (`--marker-edge rise|both`).
Depends on: T1. Acceptance: builds for both `MCU`s via `Makefile.py32`; UART menu on
USART1 (PA2/PA3 on 002B? — pick and document); VCD selftest still `0 failed`.

### Wave 3

**T5 — DFU bootloader for PY32 — tier: hard**
Files: `bootloader_dfu/dfu_py32.h` (new), `bootloader_dfu/py32/{Makefile, bootloader.c,
dfu_chip.h, dfu_transport.h, usb_config.h, funconfig.h, py32-dfu-bootloader.ld}` (new),
`bootloader_dfu/README.md` (add a PY32 section), `tools/wg015mkdfu.py` (add `--bcddevice`,
`--pid`, `--vid` options; defaults unchanged).
Depends on: T1, T2, T3.
Content: §8 verbatim; `usb_config.h` = copy of `bootloader_dfu/wg015/usb_config.h` with PY32
pins, `wTransferSize 0x80`, `bcdDevice 0x0210` (PY32 DFU), serial `"P32D"`, `USBDESC` to RAM;
`Makefile` wraps `Makefile.py32` (`TARGET=bootloader`, `LDSCRIPT=py32-dfu-bootloader.ld`,
`SIZE_BUDGET 4096` hard via ld `FLASH LENGTH=4096`, soft 3800 printed like
`bootloader_dfu/wg015/Makefile:14-22`); ld = `py32_common.ld` include with `FLASH ORIGIN
0x08000000 LENGTH 4096`, RAM per MCU; `dfu_port_flash_timebase_init()` writes the 9 timing
registers from the family's factory set; SysTick ISR `SysTick_Handler` (priority 3) in
`bootloader.c`; `.noinit` flag; UPLOAD/APPCRC on for F030, decided by size on 002B.
Acceptance: builds for `MCU=PY32F002Bx5` and `PY32F030x8`; `sizecheck` passes; `nm` shows
`dfu_boot_flag` in `.noinit` (not `.bss`), `dfu_port_flash_write_block` anywhere (XIP ok);
`python3 tools/wg015mkdfu.py --selfcheck` and `--bcddevice 0x0210` produce a suffix with the
new value; the V003/WG015 DFU builds unchanged.

**T7 — Build integration, CI, top-level docs — tier: mechanical**
Files: `Makefile` (top), `.github/workflows/build.yml`, `.gitignore`, `README.md`.
Depends on: T1, T4, T5.
Content: `PROJECTS_PY32 := demo_gamepad demo_hidapi bootloader_dfu/py32`, `build_py32:` loops
`$(MAKE) -C $$d -f $(abspath rv003usb/py32/Makefile.py32) MCU=$$mcu` for `MCU in PY32F002Bx5
PY32F030x8`; `all: build build_py32`; CI installs `gcc-arm-none-eabi` and runs `make build_py32`;
README gets a "PY32 / Cortex-M0+" section (targets, pins, clocks, loader, limits: no terminal,
no DEBUG_TIMING, IRQ policy). Acceptance: `make all` green locally; CI yaml validated by a
dry parse (`python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/build.yml'))"`).

**T9 — HID blob loader for PY32 (optional, after T5 proves the transport) — tier: hard**
Files (create): `bootloader_py32/{bootloader.c, Makefile, usb_config.h, funconfig.h,
py32-usb-bootloader.ld, blobs/Makefile, blobs/blob_erase_page.S, blobs/blob_program_page.S,
blobs/blob_read_chunk.S, blobs/blob_boot_app.S, blobs/blob_rescale_timings.S}`; modify
`bootloader_wg015/wg015hostcli/wg015bflash.c` + its `README.md` (accept `bcdDevice 0x0210`, a
Thumb blob table, page = 128 B, unit = page).
Depends on: T5, T2. Content: port of `bootloader_wg015/bootloader.c` (RTC_REG → `.noinit`
flag, rdcycle → SysTick ms, PLIC teardown → `NVIC_DisableIRQ`+`EXTI->PR`, scratchpad at
`0x20000000` 1152 B, `runwordpad` after it) with the shared-C `RV003USB_BOOTLOADER` hooks;
blobs are PIC Thumb (`-mthumb -fPIC`-free hand asm, entry at +4, address guard `< APP_BASE`
refused). Acceptance: builds; blobs ≤ 284 B; CLI refuses loaders with other bcdDevice.

### Wave 4 (hardware, sequential)

**T10 — Bring-up, calibration, hardware validation — tier: hard, needs a board + LA**
Files: `doc/py32/calibration.md` (new), `rv003usb/py32/usb_port_py32_tune.h` (values only,
after T2 is merged). Depends on: all above.
Steps: blink@0x08000000 cold start; bench1-6 → fill the table (IOPORT cost, branch/alignment,
entry latency median+spread, 16/32-bit fetch, HSI_TRIM LSB, SRAM-across-SYSRESETREQ = OQ2);
set `USB_RX_SYNC_DELAY` from the VCD `rx` sample-offset histogram (target 14-18/32); verify
every TX cell period 32±0 and SE0 with `wg015vcd.py tx --gate-turnaround 7.5`; enumerate
demo_hidapi on Linux/Windows ≥100 replugs, 1 h soak; `dfu-util -D` 100 cycles; temperature
sweep (hair-dryer/freezer spray) with the servo on HSI builds — must stay enumerated.

### Ownership matrix (must stay disjoint)

| Task | Owns |
|---|---|
| T0 | merge only (runs alone) |
| T1 | `rv003usb/py32/{py32_min.h, ch32fun.h, startup_py32.S, py32_common.ld, py32f002b.ld, py32f030x6.ld, py32f030x8.ld, Makefile.py32, py32_stdio_stub.c, README.md, selftest_main.c}` |
| T2 | `rv003usb/rv003usb-arm.S`, `rv003usb/py32/usb_port_py32_asm.h`, `rv003usb/py32/usb_port_py32_tune.h`, `tools/py32_cyc.py` |
| T3 | `rv003usb/rv003usb.c`, `rv003usb/rv003usb.h`, `rv003usb/usb_port_ch32.h`, `rv003usb/wg015/usb_port_wg015.h`, `rv003usb/py32/usb_port_py32.h` |
| T4 | `demo_gamepad/*`, `demo_hidapi/{usb_config.h,funconfig.h,demo_hidapi.c,README.md}` |
| T5 | `bootloader_dfu/dfu_py32.h`, `bootloader_dfu/py32/*`, `bootloader_dfu/README.md`, `tools/wg015mkdfu.py` |
| T6 | `py32_bench/*`, `tools/wg015_vcd/*` |
| T7 | `Makefile`, `.github/workflows/build.yml`, `.gitignore`, `README.md` |
| T8 | `doc/py32/{chip_info.md, ledger_arm.md, STATE.md, TODO.md}` |
| T9 | `bootloader_py32/*`, `bootloader_wg015/wg015hostcli/*` |
| T10 | `doc/py32/calibration.md`, values in `usb_port_py32_tune.h` (after T2) |

## 10. Risks

| R | Risk | Trigger | Fallback |
|---|---|---|---|
| R1 | Older PY32F002B silicon has no 48 MHz HSI (`HSI_FS=101` reserved in RM002B p63) | bench6 / clock output ≠ 48 MHz, or `HSIRDY` never | require "B-C" silicon (DBG_IDCODE at 0x40015800, RMBC p265/p269 reset value 0x20200061) or use PY32F030 only |
| R2 | HSI accuracy/drift beyond servo range; servo hunting | LA sample-offset slope > 0.16 cyc/bit after lock; enumeration drops with temperature | reduce K (gentler), widen saturation; HSE crystal on F030; 002B: only with servo proven |
| R3 | 002B RAM (3 KB) too small for RAM-resident RX+TX+descriptors+DFU buffers+stack | ld ASSERT in T1/T5 | move dispatch (`se0_complete_flash…interrupt_complete`) back to flash (it is not cell-critical, ≈120 B); shorter descriptors; HID loader instead of DFU on 002B |
| R4 | RAM branch/alignment penalties or 16-bit-only fetch make the paper ledger wrong | bench2 shows `B`≠2 or alignment deltas | `.balign 4` loop heads + re-pad via walker with measured costs (the walker's cost table is a parameter) |
| R5 | IRQ entry outside [11,74] (long PRIMASK sections, equal-priority ISRs, SysTick at prio 0) | bench3 spread; sporadic CRC failures | enforce Р7; RAM vector table; assert no ISR at priority 0 in `usb_port_hw_setup` |
| R6 | Flash timing registers wrong for the running HSI mode → mis-programmed pages | verify readback after DFU download fails | load the set matching `ICSCR.HSI_FS` exactly as `__HAL_FLASH_TIMMING_SEQUENCE_CONFIG`; program at 24 MHz? no — keep 48, but the 002B non-C set has no 48 MHz entry (R1) |
| R7 | SRAM not retained across SYSRESETREQ (OQ2) → boot flag lost | T10 test | drop the APP fast-path (CRC path still boots), STAY via `SFTRSTF` only; or park the flag in the `.noinit`-like top of SRAM AND require double-tap |
| R8 | Turnaround > 7.5 bit-times (flash-resident C handlers with 1-WS) | `wg015vcd.py tx` gate | move `usb_pid_handle_in/data` hot path into `.timecrit` (attribute), or tier-b ACK-first idea from branch_notes.md |
| R9 | DFU > 4 KB on 002B | sizecheck | `DFU_ENABLE_UPLOAD 0`, `APPCRC 0`, strings trimmed (v003 precedent in TODO.md 19b); or 8 KB loader (app at +0x2000) |
| R10 | Vendor documents contradict each other (24 vs 48 MHz, HSI_FS table) | — | every number in `py32_min.h` cites a page; T10 measures |
| R11 | Shared EXTI vector with user pins | app needs EXTI on lines 2/3 (or 4-15) | F6 hook (T2 step 7) |
| R12 | D± edge rates / no series resistors; 3.3 V only | LA/scope in T10 | OSPEEDR high, 22-33 Ω series, VDD 3.3 V |
| R13 | Per-part behaviour differences between 002B and 030 (flash controller, IOPORT on port F) | bench1 on both | keep all timing in RAM (Р4) so only clock init differs |

## 11. Open questions (could not be verified from documents)

| OQ | Question | Why it matters | How to close |
|---|---|---|---|
| OQ1 | Is 48 MHz HSI officially supported on PY32F002B? DS V1.0 says 24 MHz max, RM B-C says fmax 48 and defines HSI_FS=101; DS has no 48 MHz accuracy row | production viability of target #2 | ask PUYA / check DS ≥ V1.8 listed on puyasemi.com product page; bench6 |
| OQ2 | SRAM content retained across SYSRESETREQ? (RM only lists registers) | boot-flag scheme (§8) | T10 test; fallback R7 |
| OQ3 | HSI_TRIM LSB weight at 48 MHz and monotonicity of the 13-bit field | servo gain | bench6 |
| OQ4 | Cortex-M0+ configuration on PY32: fetch width (16 vs 32), multiplier (DS says single-cycle), alignment penalties from SRAM | ledger validity | bench2 |
| OQ5 | Real EXTI entry latency incl. 1-WS vector fetch and GPIO input synchronizer delay (2 cycles assumed) | window §2.2, sample phase | bench3 + VCD `rx` entry stats |
| OQ6 | 002B "Load Flash" boot zone: option-byte programming flow, erase protection reliability | brick-proof loader alternative to Р6 | RM002B p20-21/p42; try on hardware after DFU works |
| OQ7 | Are all GPIO ports (incl. GPIOF on F030) on the single-cycle IOPORT? (memory map says the whole 0x5000_0000 region) | 1-cycle sample assumption | bench1 per port |
| OQ8 | 5 V tolerance of PY32 I/O (not in DS) | hardware design | irrelevant at VDD 3.3 V; document |

## Appendix A — Paper ledger of the branch engine (walker over the real object, TRM costs)

RX (RAM): entry→IDR sample 3; entry→DELAY start 21; DELAY(96) = 96; preamble poll 5/iter;
detect→DELAY(71) start 10; DELAY(71) = 72; packet_type top→sample done 22; packet_type
iteration 32/32; bit_process zero/one × mid/EOB = 32/32/32/32; one+stuffed 64; sample at +10
in bit_process; SE0 → `bx` 20. First PID sample = detect + 104 cycles.

TX (0-WS model = target for T2 step 4; today runs from flash and reaches ≈32 by wait states):

| Path | now | target | pad |
|---|---|---|---|
| entry → turnaround BSRR store | 16 | — | — |
| entry → MODER (drivers on) | 30 | — | — |
| entry → first preamble store | 51 | measure (turnaround budget) | — |
| pre_and_tok zero / one (store idx 9 / 8) | 20 / 19 | 32 / 32, idx 9 / 9 | +12 / +1 before store, +12 after |
| pretok last bit → send_inner top | 12 | 32-relative: keep the first data store on the grid | check with walker |
| send_inner zero / one, mid-byte | 21 / 21 | 32 / 32 | +11 / +11 (after the store) |
| send_inner zero / one, end-of-byte | 21 / 20 | 32 / 32 | +11 / +12 |
| one + stuffed (store idx 30) | 40 | 64, idx 43 | +13 before, +11 after |
| last data bit → CRC byte 1 → top | 23 | 32 | +9 |
| last CRC bit → SE0 store | 31 | ≈32 | ≈+1 |
| SE0 width | 37 | 64 (2 bit-times; V003 ≈48) | +27 (tunable) |
| J-park → MODER release | 19 | ≥16 | 0 |

## Appendix B — cycle walker (seed for `tools/py32_cyc.py`)

Cost model: `ldr/str … [rX,#0|16|24]` with an IOPORT base register = 1; `[pc,#…]` = 2; other
loads/stores = 2; `b<cc>` 2 taken / 1 not; `b`, `bx`, `blx` = 2; `push` 1+N; `pop {…pc}` 3+N;
everything else 1. Input: `arm-none-eabi-objdump -d --no-show-raw-insn <elf>`; paths are
segments `(start, end, {branch_addr: taken})`. The engine header must list the label names of
every path so the tool can check `== 32/64` mechanically (T2 acceptance). The 60-line script
used for Appendix A is reproduced in `doc/py32/ledger_arm.md` by T8 (source: this analysis).

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
