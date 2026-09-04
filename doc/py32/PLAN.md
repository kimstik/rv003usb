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
("B-C") silicon and in the vendor LL but has no datasheet accuracy figure (§3.1); (g) the
preamble spin is unbounded (F9, PA A-16). Distance to "WG015 standard": engine ≈ 30 % of the
work (fix + RAM-TX + servo + contracts), everything else (own header/startup/ld/Makefile, C
seams, demos, DFU chip port, bench, docs, CI) is absent. Recommended architecture: **separate
Thumb engine file** (ISA forbids a shared body) that obeys the same per-site macro vocabulary
as `rv003usb.S`, one `usb_port_<chip>.h` per target for the C seams (replacing all `#if`
ladders with a single include selector), **no vendor submodule** (self-written minimal
header/startup/ld as in `rv003usb/wg015/`), RX+TX+literals+descriptors in RAM, keepalive
servo on the 13-bit HSI trim (two-rate: fast lock, then gentle — PA S-12), DFU loader reusing
`bootloader_dfu/dfu.c` unchanged. Prior art (PA §0) confirms Р1–Р7 unanimously and flips two v1
defaults on measured evidence: D± drive strength **lowest, not highest** (Р8) and DFU
`bwPollTimeout` **12 ms, not 8** (§8). It also adds work v1 lacked — an exact-N-cycle pad
staircase (PA S-1), a bounded preamble spin (PA A-16), a writer→reader loopback bench (PA S-6,
new task T11), a boot-failure counter in the DFU chip port (PA S-7), the two-rate servo (PA
S-12), an assembler-define guard against the §2.6 build hole (PA S-11), and an EOP-width gate
in the VCD tool (PA T-2). Two v1-internal inconsistencies surfaced while folding these in and
are fixed here: the boot flag lived in `.noinit` of two different images (§8, Р6) and the
loader's 1 ms SysTick reload would have silently disabled the engine's keepalive servo (Р9).

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
| Drivers on | 367-372, 384 | read-modify-write of `MODER` to `01` (output) on DP/DM; 30 cycles from entry to the MODER store (walker) |
| NRZI | 387-389, 426, 476-478 | `r5` = absolute BSRR word for the pair; `FLIP_MASK r8 = set+reset bits of both pins`; `eor r5, r8` swaps J/K; `str r5,[GPIO,#BSRR]` (1 cycle, IOPORT). Identical idea to S:871 `t1` |
| Bit stuffing | 412, 428, 482, 501-502, 527-533 | `BITSTUFF r6` 6→0 → `insert_stuffed_bit`: 5-6× `b .+2` then `b flip_bus`; the `subs BITSTUFF; beq insert_stuffed_bit` (arm.S:501-502) precedes `send_end_bit_complete`'s bit-count test (arm.S:505) exactly as S:1023-1025 precedes S:1058-1062 → the trailing stuff bit after a six-ones CRC tail *should* be emitted (PA L-6, OQ11 — walker path "one+stuffed at the last CRC bit" must show 64, T2) |
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
PY32 flash is a flat LATENCY=1 with no prefetch buffer and no cache anywhere in the RM (PA D-2,
517-page RM030 grep), but the core fetches 32 bits ahead over 16-bit instructions (TRM §2.2.1),
so a branch target's half-word alignment changes its flash cost — the artefact that assert
guards. SRAM is 0-WS and, per the TRM, alignment-free; bench2 (T6) confirms or refutes
(OQ4/R4). The same failure is on record elsewhere: Grainuum issue #1 ("Running deterministic
from Flash", open since 2016) and its comment that jumps > 48 B from flash "cause random
amounts of jitter" (PA A-2).

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

| Part | Max f (DS) | 48 MHz path | Flash / RAM | Verdict |
|---|---|---|---|---|
| PY32F002B (branch's target) | **24 MHz** (DS002B p2 "Up to 24 MHz as a maximum frequency"; p5 "Max. CPU frequency 24 MHz"; puyasemi product page "Max CLK 24") | HSI 48 MHz mode: DS002B p10 clock figure "HSI RC 24/48MHz"; RM002B p59 figure "HSI RC 48MHz" but p63 `HSI_FS` "100:24 MHz others: reserved"; **RMBC** (PY32F002B-C RM V1.0) p14 "CPU CORTEX-M0+ fmax= 48MHz", p58 `HSI_FS: 000:4 MHz 001:8 MHz 100:24 MHz 101: 48 MHz`, p29/p31 factory trim word for 48 MHz at `0x1FFF0104`, p24/p30 flash timing set for HSI 48 MHz at `0x1FFF0130…0x1FFF0140`; vendor LL `LL_RCC_HSICALIBRATION_48MHz = *(0x1FFF0104)&0xFFFF` (py32f002b_ll_rcc.h:386), `HSIFreqTable[5]=48000000` under `RCC_HSI48M_SUPPORT` (system_py32f002b.c:67-68, py32f002bx5.h:2221). No PLL, no HSE (DS002B p2 lists HSI/LSI/LSE/external clock input only; `RCC_CR_HSEON` absent from py32f002bx5.h) | 24 K / 3 K, page 128 B, sector 4 K (RM002B p22) | **Target #2 (cost-down)**, HSI-only, servo mandatory, treat 48 MHz as "documented in RM B-C, unspecified in DS" (open question OQ1). No public evidence of a working 48 MHz bit-bang on any PY32 exists — the only attempt (TheYkk) never configured 48 MHz (PA §1 row 6, A-19) |
| PY32F030x6/x8 | **48 MHz** (DS030 p2 "Up to 48 MHz"; p5) | PLL ×2 from HSI 24 MHz or HSE 4-32 MHz (DS030 p2 "PLL (supports 2 octaves for HSI or HSE)", p18 figure "HSI RC 24MHz X2 PLL"; DS030 p64 PLL table: output 48 MHz, `tLOCK 15…40 µs @ fPLL_IN=24MHz`; PLL input floor 12 MHz, DS030 Table 5-17 — TheYkk's "8 MHz × 6" cannot lock; RM030 p74 §8.1.5, p77 `PLLON/PLLRDY`, p83 `PLLSRC 0:HSI 1:HSE`) | 32/64 K, 4/8 K RAM (template README) | **Target #1 (development/reference)**: crystal option (24 MHz HSE → servo off) or HSI (servo on); single-cycle multiplier (DS030 p17) |
| PY32F003 | 32 MHz (DS003 p1) | none | | excluded (33.3 cyc/bit impossible) |
| PY32F002A | 24 MHz (DS002A p2), HSE 4-24 MHz, no PLL | none | | excluded (the "F002A is an F030 die" claim is UNVERIFIED per unit, PA §5.1) |

Sanity: the template README's "PY32F0xx up to 48 MHz" is wrong for 003/002A; datasheets win.

### 3.2 Core and timing

| Fact | Source |
|---|---|
| Cortex-M0+, 2-stage pipeline, single-cycle multiplier on PY32 | DS002B p8, DS030 p17 ("single-cycle multipliers"); TRM p1-5 Table 1-1 (multiplier "Fast or small") |
| Interrupt latency 15 cycles (zero WS), LDM/STM abandoned+restarted, late-arrival/tail-chain | TRM p3-10 §3.6.1 |
| Instruction costs: MOV/ALU 1; `B<cc>` 1/2; `B` 2; `BL` 3; `BX/BLX` 2; `MOV PC,Rm` 2; `LDR/STR/LDRB/STRB` "2 or 1 — 2 if to AHB interface or SCS, 1 if to single-cycle I/O port"; `PUSH` 1+N; `POP{…,PC}` 3+N; `NOP` 1; `MULS` 1 or 32 | TRM p3-4…3-7 Table 3-1 + footnotes b, e; staircase arithmetic in §7.4 uses `BL` 3 + `NOP` 1·k + `MOV PC,LR` 2 |
| Single-cycle I/O port: "accessible both by loads and stores … You cannot execute code from the I/O port"; optional | TRM p2-3 §2.2.2, p1-5 |
| GPIO is on that port: memory map row "0xE000 0000… M0+ IOPORT 0x5000 …" and the system diagrams show "IOPORT" between the core and PORT A/B/C(/F); GPIO feature list "Fast toggle capable of changing every single cycle". (PA §5.2: one sweep inferred "plain AHB" from `#if` structure — contradicted by the RM map; bench1 settles it per port, OQ7) | RM002B p15-18, p76; RM030 p18-20, p100; DS030 p16, p54 |
| Fetch-ahead limited to 32 bits; configurable "Instruction fetch width 16-bit only or mostly 32-bit" (vendor choice unknown) | TRM p2-2 §2.2.1 note, p1-5 Table 1-1 → bench item (T6) |
| SysTick present, calibration 6000 (=1 ms @ HCLK/8 = 6 MHz → HCLK 48 MHz), `__Vendor_SysTickConfig 0`; VAL is a 24-bit down-counter (wraps every 349.5 ms at 48 MHz with LOAD=0xFFFFFF) | RM002B p97 §11.1.2; RMBC p84; py32f002bx5.h:53 |
| VTOR present; vendor SystemInit writes `SCB->VTOR = FLASH_BASE\|offset` (or SRAM) | py32f002bx5.h:51 `__VTOR_PRESENT 1`; system_py32f002b.c:132-137 |
| NVIC: 2 priority bits (4 levels), 32 IRQ lines | RM002B p97 §11.1.1 |
| Flash: LATENCY=1 → "two system clock cycles are required for each Flash read"; required above 24 MHz (vendor BSP sets `LL_FLASH_LATENCY_1` for 48 MHz, py32f002b_bsp_clock.c:29-30); no prefetch buffer / cache documented | RM002B p38; RM030 §4.2.2 p26, §4.8.1 p42-43 (PA D-2) |
| "During a program and erase operations … any attempt to read the Flash memory will stall the bus" → XIP programming is legal, CPU simply stalls; writing FLASH_CR while BSY stalls too | RM002B p23-24; RM030 p27-28 |
| CSS: if HSE fails the clock falls back to HSI and an NMI is raised | RM030 §8 CSS (PA §5.4) → HSE builds need an `NMI_Handler` (T1) — a silent fallback to untrimmed HSI drops the link |

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
Prior art agrees against Grainuum's runtime `struct GrainuumUSB` of register addresses (PA D-1):
every address in such a struct is a 2-cycle AHB load inside a 32-cycle slot; compile-time
literals from an IOPORT base register cost 1.

**Р2. C seam — keep the `#if` ladders vs per-target `usb_port_<chip>.h`.**
For ladders: no refactor risk to V003 bit-identity, WG015 already inlined its blocks. Against:
a third arm makes `usb_setup()` a 3-column ladder, the reboot seam a second one, and the
DEBUG_TIMING/terminal blocks a third — exactly the anti-pattern documented in branch_notes.md:86.
**Decision: per-target header with a single include selector in `rv003usb.h`:**
`#if defined(WG015)&&WG015 → "wg015/usb_port_wg015.h"; #elif defined(RV003USB_PY32) →
"py32/usb_port_py32.h"; #else → "usb_port_ch32.h"` (the V003/V00x code moved verbatim). Seams
(static inline, all `__ASSEMBLER__`-guarded): `usb_port_hw_setup()` (clock enable, pins, EXTI,
NVIC priority, DPU after `USB_DPU_DELAY_MS`, boot-counter clear), `USB_PORT_REBOOT_TO_BOOTLOADER()`,
`USB_PORT_DEBUG_TIMING_SETUP()` (or `#error`), `USB_DM_IRQ`/handler symbol,
`USB_PORT_TERMINAL_SUPPORTED`. Gate: V003 `demo_gamepad` `.bin` bit-identical before/after (the
WG015 branch already enforces this discipline, STATE.md:32-35); WG015 `demo_hidapi` builds in
both ld variants.

**Р3. Vendor submodule vs self-written minimal header/startup/ld.** For the submodule: tested
clock/flash code, all parts covered. Against: a 50 MB dependency for ≈300 lines actually used,
the `Build/../` object hack, LL/HAL license mix, `rules.mk` semantics (the `-D` bug), CI cost,
and inconsistency with `rv003usb/wg015/`. **Decision: no submodule.** `py32_min.h` (registers
for GPIO/RCC/EXTI/FLASH/SYSCFG/SCB/NVIC/SysTick, both families, `__ASSEMBLER__`-clean,
facts cited to RM pages), `startup_py32.S`, `py32_common.ld`+per-part ld, `Makefile.py32`.
Apps that want the vendor LL include it themselves; our header must not be included together
with a vendor device header (documented). TheYkk's port is the cautionary tale (PA A-19): a
wrong GPIO base (`0x48000000`, the STM32 value) and a PLL fed from an 8 MHz HSI that cannot lock
went unnoticed because nothing checked the numbers — `py32_min.h` carries a `_Static_assert` per
offset and a page cite per block.

**Р4. Code placement.** RX ISR, TX engine, dispatch trampolines, literal pools, `always0`,
the pad staircase (§7.4), `descriptor` bytes, any `usb_send_data` source → RAM (`.timecrit` +
`.rodata.usbdesc`→`.data`, the WG015 rule "clocked-path data in RAM", PLAN Р3). Reason: flash
reads are 1-WS and the prefetch state makes `ldrb` inside a TX cell 2-or-3 cycles (RM002B p38;
TRM §2.2.1 fetch-ahead). Cost on 002B: ≈600 B code + descriptors (≈250 B demo_hidapi) out of
3 KB — budget table in T1. Prior art is unanimous (PA S-4, D-2: Grainuum `.ramtext`, LemcUSB
`.functioninRAM`, joyboot `.ramtext` + RAM vectors, Pico-PIO-USB `__not_in_flash` for CRC tables
and delay loops); the WG015 track's "flash first" directive does not transfer because that chip
has a cache and prefetch buffer and PY32 has neither. Corollary gate: no DFU reply pointer and
no descriptor symbol may resolve into flash (T4/T5 `nm` checks).

**Р5. Clocking and the servo.** PY32F030: HSE 24 MHz ×2 (crystal, servo off = `USB_TRIM_ACTUATE`
empty; `NMI_Handler` handles CSS) or HSI 24 MHz ×2 (servo on). PY32F002B: HSI 48 MHz (`HSI_FS=101`
+ factory word 0x1FFF0104), servo on. Keepalive path measures SysTick deltas (expect 48000/frame)
and steers `RCC_ICSCR.HSI_TRIM` (13-bit) with saturation ±64 LSB from the factory value; one LSB
≈ 0.1 % (DS `fTRIM`). **Two-rate law (PA S-12, D-5):** for the first `USB_TRIM_LOCK_N` (default 8)
in-window keepalives after a reset or after any out-of-window delta (missed keepalives: flash
op, suspend, re-enumeration) the actuator applies a proportional step `dev >> USB_TRIM_FAST_SHIFT`
(default 6: a 0.7 % error = 336 cycles/ms → ≈5 LSB per keepalive → inside 0.25 % in ≤ 4
keepalives); afterwards the V003-style decimated integrator `windup >> USB_TRIM_SLOW_SHIFT`
(default 9, S:788-796). A one-shot trim is known to break when the host shortens the post-reset
window (V-USB on Windows 10, PA A-6); a single-rate gentle integrator cannot meet the ≤ 10 ms
first-request budget from a 0.7 % start (§2.4.5). Sign (`USB_TRIM_SIGN`) and LSB weight come
from bench6 (OQ3); N from the Windows/xHCI keepalive count (OQ9). The frame measurement itself
is unconditional (telemetry `delta_se0_cyccount`), the actuator is a port macro — same layering
as `rv003usb.S:778-797`. The keepalive path acks `EXTI_PR` first and must complete within 96
cycles (walker path, T2) because a token may follow a keepalive EOP after 2 bit-times of idle.

**Р6. Bootloader layout.** Uniform for both parts: loader = flash pages 0-31 (4 KB) at
0x08000000, app at `0x08001000` (VTOR-relocated Cortex-M image with its own vector table).
**Boot words live at fixed addresses shared by both images**: `py32_common.ld` (T1) reserves the
top 16 B of SRAM (`__noinit_top = ORIGIN(RAM)+LENGTH(RAM)-16`, stack top = `__noinit_top`) and
`PROVIDE`s `py32_boot_flag` (+0), `py32_boot_count` (+4), `py32_dbltap` (+8), `py32_noinit_spare`
(+12); startup never touches them. v1 had the flag in `.noinit` of *each* image — the loader's
`.noinit` follows its own `.bss`, the app's follows a different `.bss`, so the app's write
(`USB_PORT_REBOOT_TO_BOOTLOADER()`) would not have landed on the word the loader reads (analog of
WG015's `RTC_REG[0]`, which is a hardware register and has no such problem). Flag qualified by
`RCC_CSR.SFTRSTF`; fast-path `dfu_port_jump_app()` = VTOR + MSP + jump with SP/PC sanity (PA S-8);
boot-failure counter in `py32_boot_count` (PA S-7, §8). The 002B-only "Load Flash" zone
(hardware-protected 4 KB at 0x08005000, app unmodified at 0x08000000) is attractive (brick-proof)
but needs option-byte provisioning (PA A-11) and differs per family → recorded as OQ6 / a
follow-up, not the default.

**Р7. Interrupt policy.** USB EXTI IRQ priority 0, every other IRQ ≥ 1, SysTick 3; PRIMASK
critical sections ≤ 40 cycles (README.md:94 rule); no other IRQ may run at priority 0. Vector
table stays in flash (1-WS vector fetch = +1…2 constant cycles, inside the +55 window; PA D-11:
deterministic because LATENCY is flat); RAM vector table is an optional 192 B trade documented
in T1, switched on only if bench3's measured entry spread eats the window.

**Р8. D± drive strength — v1 said "OSPEEDR high", prior art says low.** For high: sharper edges
look like more timing margin. Against: Grainuum *measured* overshoot and failures on longer
traces with fast slew and fixed them with the slow setting (PA S-9, D-10, A-8); the CH32V003
engine we are porting drives D± at its slowest CFGLR speed (2 MHz, S:857-861) and ships that
way; USB 2.0 §7.1.2.1 Table 7-9 requires LS edges of 75–300 ns into 200–450 pF, so a slow edge
is the *specified* edge — nothing in the 32-cycle cell depends on edges faster than that, the
LA measures periods at threshold crossings (symmetric slew does not move them) and D+/D− use
the same setting (matching `tLRFM`); a fast edge into an unterminated 1–2 m cable is ringing
plus EMI; TheYkk's "add 22 pF" advice is the symptom of the same mistake. **Decision: OSPEEDR =
lowest setting (`USB_PORT_OSPEED` default 0 in `usb_port_py32.h`, T3) + 33 Ω series (README.md:31)**;
T10 scopes tr/tf into the real load and raises the setting only if 300 ns is exceeded (OQ10).
Every place v1 assumed "high" is changed: §3.4, T1 README, T3, R12.

**Р9. Timebase rule — SysTick is free-running, always.** The engine's `USB_TICK_ADDR` reads
SysTick `VAL` and computes `(last − now) & 0xFFFFFF`, expecting ≈48000 per keepalive (§7.1).
v1's DFU port instead configured SysTick as a 1 ms IRQ tick (`LOAD = 47999`), under which the
same read yields the drift only (≈0), fails the ±4000 sanity window and silently disables the
servo — failure mode PA A-5 in the loader itself. **Decision: `LOAD = 0xFFFFFF`, CLKSOURCE =
HCLK in every PY32 image (`PY32_systick_freerun()` in the shim is the only writer of `LOAD`);
the loader adds `TICKINT` and counts wraps (349.5 ms) at priority 3, and `dfu_port_cycles()`
returns a 32-bit HCLK cycle count with `DFU_CYCLES_PER_MS = 48000` (§8)**; 32-bit unsigned
subtraction is then modular and wrap-safe for any wait < 89 s (PA L-21 solved without touching
`dfu.c`). `Delay_Ms()` in the shim loops in ≤ 100 ms chunks. Mechanical guard: `grep -rn
'SysTick->LOAD\|SYST_RVR' bootloader_dfu/dfu_py32.h bootloader_dfu/py32/ demo_*/ py32_bench/`
returns nothing (T5/T4/T6 acceptance); the shim is the single writer.

**Р10. Licence and provenance rule.** Sources are classed in §1. **MIT** (Grainuum, joyboot,
Pico-PIO-USB, uf2-samdx1): techniques *and code* may be adopted; copied code carries the
original copyright line and the MIT permission notice in the header comment of the file that
contains it (the repo stays MIT). **GPL** (LemcUSB GPLv3, stm32f030-vusb GPL-3.0, V-USB and
micronucleus GPLv2/commercial): *ideas only* — no code, no derived or "translated" files, no
line-by-line paraphrase; reading them to understand a failure mode is fine. Unlicensed (TheYkk):
nothing taken. Enforcement: every commit that adds or rewrites asm/C in `rv003usb/rv003usb-arm.S`,
`rv003usb/py32/`, `bootloader_dfu/dfu_py32.h`, `bootloader_dfu/py32/`, `py32_bench/` carries a
`Provenance:` trailer (`own` / `rv003usb.S:<lines>` / `Grainuum MIT <file:lines>` / `idea: <source>`);
T8's `STATE.md` keeps the ledger; `grep -rIl -e 'lemcu' -e 'LemcUSB' -e 'ads830e' -e 'usbdrvasm' -e
'osccal' rv003usb/ bootloader_dfu/ py32_bench/ tools/` must return nothing (docs excluded). Tasks
that touch GPL sources for ideas are tagged **[GPL-ideas-only]** in §9; tasks that copy MIT code
are tagged **[MIT-attrib]**.

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

Conventions for all tasks: branch = the T0 result; build commands run from the repo root;
`ARMCC=arm-none-eabi-` (gcc ≥ 13), RISC-V builds via `ch32fun` submodule (`git submodule update
--init`); size numbers from `arm-none-eabi-size`; "walker" = `tools/py32_cyc.py` (Appendix B
until T2 lands it). Macro conventions: one Makefile variable `DEFS` holds the whole set and is
passed **identically to the `.c` and `.S` rules** (§2.6): `-DRV003USB_PY32=1` on every PY32
compile/assemble line, plus exactly one of `-DPY32F002B=1` / `-DPY32F030=1` (family), plus
`-D$(MCU)=1` (part, e.g. `-DPY32F002Bx5=1` — mirrors the vendor `LIB_FLAGS` so the branch's
`#if PY32F002Bx5` is finally exercised in T1's interim build), plus `-DPY32_FLASH_KB=<n>
-DPY32_SRAM_KB=<n>` (from the Makefile `MCU=` value). Timing constants are cycles from
`usb_port_py32_tune.h`, never microseconds (PA S-3). No task edits a file it does not own; if
a task needs a change elsewhere it writes the exact request into `doc/py32/STATE.md` (T8's
file — append-only section "requests", the one shared exception). Every commit touching the
files named in Р10 carries a `Provenance:` trailer; tags **[MIT-attrib]** / **[GPL-ideas-only]**
below mark the tasks concerned. Acceptance criteria are mechanical: a command that must
succeed, a size limit, a symbol at an address, or a grep that must (not) match.

### Wave 0

**T0 — Starting state (ONE agent, alone, before anyone else) — tier: medium**
Goal: a branch containing WG015 work + master 80b1893 + the PY32 branch content, building
green for RISC-V and WG015, with the vendor scaffolding removed.
Files: everything the merge touches (exclusive because nobody else runs yet).
Procedure (dry-run verified at 1db45fd; the later commits touch only `doc/py32/`):
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
`rv003usb/rv003usb-arm.S` present and `git diff origin/py32 -- rv003usb/rv003usb-arm.S` empty.

### Wave 1

**T1 — `rv003usb/py32/` target skeleton and build — tier: hard**
Files (create): `rv003usb/py32/py32_min.h`, `rv003usb/py32/ch32fun.h`,
`rv003usb/py32/startup_py32.S`, `rv003usb/py32/py32_common.ld`, `rv003usb/py32/py32f002b.ld`,
`rv003usb/py32/py32f030x6.ld`, `rv003usb/py32/py32f030x8.ld`, `rv003usb/py32/Makefile.py32`,
`rv003usb/py32/py32_stdio_stub.c`, `rv003usb/py32/README.md`, `rv003usb/py32/selftest_main.c`.
Depends on: T0.
Content: `py32_min.h` — structs/offsets/bit masks from §3.3-3.4 with a `_Static_assert` per
struct size and offset (`offsetof(GPIO_TypeDef,BSRR)==0x18`, `RCC.CSR==0x60`, `EXTI.PR==0x0C`,
`EXTICR[0]==0x60`, `IMR==0x80`, FLASH `CR==0x14`, `SR==0x10`, `TS0==0x100`, `SCB.ICSR==0xE000ED04`…),
family switches (`PY32F002B`: ports A/B/C, no PLL, HSI_FS 48; `PY32F030`: ports A/B/F, PLL,
HSI_FS 24; trim and flash-timing addresses per §3.3); OSPEEDR encoding stated with its RM002B
p78 cite (Р8); every block cites its RM page; assembler-clean (no `UL`). `ch32fun.h` shim
(mirror `rv003usb/wg015/ch32fun.h`): includes `py32_min.h`; `NVIC_EnableIRQ` (`ISER`),
`NVIC_SetPriority` (2-bit `IPR`), `NVIC_SystemReset`, `__disable_irq/__enable_irq`, `SysTick`
struct + `PY32_systick_freerun()` (LOAD=0xFFFFFF, CLKSOURCE=HCLK, ENABLE, no IRQ — **the only
writer of `LOAD` in the tree**, Р9), `Delay_Ms/Delay_Us` (SysTick polling, wrap-safe, `Delay_Ms`
chunked ≤ 100 ms), `SystemInit()` no-op (clocks are in startup), `FUNCONF_SYSTICK_USE_HCLK`
satisfied by construction, `#error` on `RV003USB_USB_TERMINAL`/`RV003USB_DEBUG_TIMING`;
`extern uint32_t py32_boot_flag, py32_boot_count, py32_dbltap;` (ld-provided, Р6) and
`static inline void py32_app_alive(void){ py32_boot_count = 0; }`. `startup_py32.S`: vector
table (48 words, weak `Default_Handler`, EXTI symbols exactly as `startup_py32f002b.s:133-135`),
`NMI_Handler` = `NVIC_SystemReset` when built with `PY32_HSE=1` (CSS fallback to untrimmed HSI
must not run silently, §3.2) else weak default; `Reset_Handler`: SP = `__noinit_top`, copy `.data`
(incl. `.rodata.usbdesc`), copy `.timecrit` (LMA flash → VMA RAM), zero `.bss` (never the
`.noinit` block), clock init per family — 002B: `FLASH->ACR = LATENCY_1` then `RCC->ICSCR =
(ICSCR & ~0xFFFF) | (*(uint32_t*)0x1FFF0104 & 0xFFFF)`, HSION, wait HSIRDY (order as
py32f002b_bsp_clock.c:27-46); 030: `HSI_FS=100` trim from `0x1FFF0F10`, HSION,
`PLLCFGR.PLLSRC=HSI` (or HSE: HSEON, wait HSERDY, `PLLSRC=HSE`, `HSE_VALUE` must be 24000000,
CSS on), `PLLON`, wait `PLLRDY`, `ACR=LATENCY_1`, `CFGR.SW=PLL`, wait `SWS` (RM030 p77,p83; LL
`UTILS_EnablePLLAndSwitchSystem`) — then `VTOR = __vector_table`, `bl main`. Linker:
`.isr_vector`(flash) → `.data` + `.rodata.usbdesc` (RAM AT flash) → `.timecrit` (RAM AT flash,
`KEEP`; also catch `*(.datacode)` so the unmodified branch engine lands in RAM until T2 renames
its section) → `.text/.rodata` (flash) → `.bss` → `.noinit (NOLOAD)` → **fixed block**
`__noinit_top = ORIGIN(RAM)+LENGTH(RAM)-16` with `PROVIDE(py32_boot_flag = __noinit_top);
PROVIDE(py32_boot_count = __noinit_top+4); PROVIDE(py32_dbltap = __noinit_top+8);
PROVIDE(py32_noinit_spare = __noinit_top+12);` → stack top = `__noinit_top`; `ASSERT(stack ≥
512)`; `PROVIDE(__timecrit_lma/start/end, __data_*, __bss_*)`. `Makefile.py32` (mirror
`Makefile.wg015`): `MCU ?= PY32F030x8` (also `PY32F002Bx5`, `PY32F030x6`), `SOURCES :=
$(TARGET).c rv003usb/rv003usb-arm.S rv003usb/rv003usb.c startup stub`, `DEFS :=
-DRV003USB_PY32=1 -DPY32F0xx… -D$(MCU)=1 -DPY32_FLASH_KB= -DPY32_SRAM_KB=` used by **both** the
`%.o: %.c` and `%.o: %.S` rules (`-x assembler-with-cpp`), flags `-mcpu=cortex-m0plus -mthumb
-Os -ffunction-sections -fdata-sections -nostartfiles -specs=nano.specs -I<py32 dir first>
-I../rv003usb -I../lib`, targets `all/size/lst/bin/flash/clean/check-cycles`; `flash` = `pyocd
load --target py32f030x8 …` (Puya DFP, §3.5) with a `JLINK=1` alternative, **no OpenOCD**;
`check-cycles` = `python3 $(PY32_DIR)/../../tools/py32_cyc.py $(TARGET).elf` (the tool arrives
with T2); `--print-memory-usage`, `sizecheck` hook variable. `README.md`: pin defaults
(D+=PB0, D−=PB3, DPU=PB2 as the branch; F030 same), clock options, RAM budget, IRQ policy (Р7),
**D± drive: OSPEEDR lowest + 33 Ω series, why (Р8), and "22 pF on D± is not a fix" (PA A-8)**,
`USB_DPU_DELAY_MS` (PA A-10), the SysTick rule (Р9), probes (§3.5).
Acceptance: `make -C demo_gamepad -f ../rv003usb/py32/Makefile.py32 MCU=PY32F002Bx5` and
`MCU=PY32F030x8` link a stub `main` (`selftest_main.c`) with the **branch's** `rv003usb-arm.S`
(unmodified) and master `rv003usb.c` compiled with `-DRV003USB_PY32` stubs for the two seams
(`usb_port_hw_setup` may be a T1-local weak stub until T3 lands); map shows `.timecrit` VMA in
SRAM / LMA in flash and `.isr_vector` at 0x08000000 with word0 = `__noinit_top` (RAM end − 16),
word1 = `Reset_Handler|1`; `arm-none-eabi-objdump -h` shows `.noinit` outside `.bss`; `nm
selftest.elf | grep py32_boot_flag` = `0x20000BF0` on 002B (3 K) / `0x20001FF0` on F030x8 (8 K);
static asserts compile; **build-hole guard**: `make -n … | grep rv003usb-arm.S` contains
`-DRV003USB_PY32=1 -DPY32F002B=1 -DPY32F002Bx5=1`, and the engine objects of the two `MCU` builds
differ (`cmp` exits 1 — the `#if PY32F002Bx5` variant now assembles, §2.6); `grep -c
'SysTick->LOAD' rv003usb/py32/ch32fun.h` = 1 and `grep -rn 'SysTick->LOAD' rv003usb/py32/*.S
rv003usb/py32/*.c` empty.

**T3 — C-layer seams: per-target `usb_port_<chip>.h` — tier: medium**
Files: `rv003usb/rv003usb.c`, `rv003usb/rv003usb.h`, `rv003usb/usb_port_ch32.h` (new),
`rv003usb/wg015/usb_port_wg015.h` (new), `rv003usb/py32/usb_port_py32.h` (new).
Depends on: T0 (T1 for the PY32 compile check; T3's PY32 header includes `py32_min.h` by name).
Content: implement Р2 exactly: `rv003usb.h` gets the single selector and declares the seam
API; `rv003usb.c` `usb_setup()` becomes `rv003usb_internal_data.se0_windup=0; usb_port_hw_setup();`
with the V003/V00x body (c:59-153 incl. DEBUG_TIMING) moved verbatim into `usb_port_ch32.h`
and the WG015 body (c@HEAD:62-…) into `usb_port_wg015.h`; the reboot block (c:173-186 and the
WG015 variant) becomes `USB_PORT_REBOOT_TO_BOOTLOADER()`; `USB_DM_IRQ` block (from 0ad3c42) moves
into the PY32 header; `RV003USB_USB_TERMINAL` and `RV003USB_DEBUG_TIMING` are `#error` when
`RV003USB_PY32`. PY32 `usb_port_hw_setup()`: `RCC->IOPENR |= GPIOxEN`; DP/DM `MODER=00,
PUPDR=00, OSPEEDR=USB_PORT_OSPEED` (**default 0 = lowest, Р8**; overridable from `usb_config.h`);
DPU `MODER=01`, then `BSRR` high after `Delay_Ms(USB_DPU_DELAY_MS)` (default 0; charger-detect
ICs sharing D± need ≈2 s, rv003usb #137, PA A-10); `EXTI->EXTICR[DM>>2]` port select (mask
per line as py32f002b_ll_exti.h:153-160 — lines 0-4 are 3-bit fields, 5-7 1-bit fields);
`EXTI->IMR |= 1<<DM; EXTI->FTSR |= 1<<DM; EXTI->PR = 1<<DM`; `NVIC_SetPriority(USB_DM_IRQn,0)`;
`NVIC_EnableIRQ`; `py32_app_alive()` (clears `py32_boot_count`, Р6/§8 — apps that want a later
"alive" point define `USB_PORT_APP_ALIVE_MANUAL` and call it themselves).
`USB_PORT_REBOOT_TO_BOOTLOADER()`: `py32_boot_flag = 0xB00710AD; NVIC_SystemReset()`
(`py32_boot_flag` = the ld-provided top-of-RAM word from T1, **not** a `.noinit` variable).
Acceptance: (1) `make -C demo_gamepad` (CH32V003) `.bin` byte-identical to the T0 build (`cmp`);
(2) WG015: `make -C demo_hidapi -f ../rv003usb/wg015/Makefile.wg015` and `bootloader_dfu/wg015`
build, `.bin` identical or the diff explained in the commit message; (3) PY32:
`rv003usb.c` compiles for both `MCU`s with `-Wall -Werror`; (4) `grep -n 'OSPEEDR' rv003usb/py32/usb_port_py32.h`
shows only the `USB_PORT_OSPEED` use with default `0`; `grep -c 'py32_app_alive' rv003usb/py32/usb_port_py32.h` ≥ 1.

**T8 — Documentation set — tier: mechanical**
Files (create): `doc/py32/chip_info.md` (§3 expanded with page refs, incl. §3.5 probes),
`doc/py32/ledger_arm.md` (Appendix A + the target TX ledger + the staircase costs),
`doc/py32/STATE.md` (fleet progress + "requests" section + **"provenance" table**: one row per
task with source, licence class and the `Provenance:` trailers seen, Р10), `doc/py32/TODO.md`.
Depends on: T0. Acceptance: every fact carries a source; `STATE.md` lists every task T0–T11
with owner; `grep -c '^| T' doc/py32/STATE.md` ≥ 12; the provenance table names Grainuum (MIT),
joyboot (MIT), LemcUSB (GPLv3, ideas only), stm32f030-vusb (GPL-3.0, ideas only), V-USB
(GPLv2, ideas only).

### Wave 2

**T2 — Engine: contracts, RAM TX, fixes, servo, marker, staircase — tier: hard — [MIT-attrib: Grainuum staircase] [GPL-ideas-only: V-USB/LemcUSB/stm32f030-vusb read for failure modes only]**
Files: `rv003usb/rv003usb-arm.S`, `rv003usb/py32/usb_port_py32_asm.h` (new),
`rv003usb/py32/usb_port_py32_tune.h` (new), `tools/py32_cyc.py` (new, from Appendix B).
Depends on: T1 (build, `py32_min.h`), T3 (`USB_DM_IRQ` moved).
Content, in order:
0. **Guard (§2.6, PA S-11)**: first lines `#ifndef RV003USB_PY32 #error …` and
   `#if !defined(PY32F002B) && !defined(PY32F030) #error …`; the file header carries the MIT
   notice and copyright line of `xobs/grainuum` for the staircase (Р10) and the [11,74]-cycle
   entry window (§2.2); `usb_port_py32_asm.h` `#error`s on missing pins.
1. Replace arm.S:3-17 and every literal with §7.1 macros; delete all `#if PY32F002Bx5` (§2.6);
   `#include "usb_port_py32_asm.h"` only.
2. Put the whole file's code (ISR, dispatch, TX, `always0`, staircase) in `.section .timecrit,"ax"`;
   `.ltorg` after each block; `rxbuf` → 4+USB_BUFFER_SIZE+4 bytes.
3. F1: `bhi` → `bhs` (arm.S:277). F2: at `is_end_of_byte` add `cmp r2, r8; bhs done_usb_message`
   with r8 = `rxbuf+4+USB_BUFFER_SIZE` loaded at entry (r8 is free in RX; `cmp lo,hi` is 1 cycle);
   rebalance: EOB tail becomes 5 → keep 32 by removing 3 cycles from `DELAY_CYCLES(6)` for all
   paths and adding 3 `nop` to the two non-EOB tails (walker must show 32/32/32/32/64). F3:
   after `rx_stuffed`'s delay sample once and `beq done_usb_message` if no transition (costs 4
   cycles inside the 24-cycle delay — shorten `DELAY(24)` accordingly).
   3b. **F9 bounded spin (PA A-16)**: `preamble_loop` gives up after `USB_RX_PREAMBLE_LIMIT`
   (≈512 cycles = 16 bit-times) → `done_usb_message`; use `SCRATCH` (r4, free there) as the
   counter; implement as a 4× unrolled poll with one `subs/bne` per 4 polls so the sample
   spacing is 4/4/4/7 cycles (worst-case detect jitter 0…6 instead of 0…4; the walker reports
   the max spacing) and re-derive `USB_RX_SYNC_DELAY` so the histogram band 14–18 (F5) still
   holds. Resume signalling (K ≥ 20 ms) and a shorted line then cost ≈11 µs of ISR, not 20 ms
   or forever.
4. TX re-pad to the Appendix A targets using the **staircase (§7.4)** at every TX pad and at the
   two RX entry pads (`bl rv003usb_wait_N`, N from `usb_port_py32_tune.h`); in-slot RX pads stay
   inline (`lr` = POLY_RX): every `pre_and_tok`/`send_inner` path = 32, stuffed = 64,
   store index equal on zero/one paths (pad the one-path before its store), stuffed store at
   32+11, SE0 width 64 (2 bit-times = 1.33 µs, inside the 1.25–1.5 µs transmitter spec, USB 2.0
   §7.1.13.2; V003 ships ≈48 = below spec, PA A-14 — tunable `USB_TX_SE0_PAD`), J-park hold ≥ 16
   before release; the `.ifeq` alignment assert is deleted (bench T6 decides if RAM alignment
   matters — if it does, `.balign 4` on loop heads is the fix, not the assert). The path list
   must include "one + stuffed at the last CRC bit" (trailing stuff bit, PA L-6/OQ11) = 64.
5. Keepalive (Р5): on the SE0 branch **ack `EXTI_PR` first**, measure `USB_TICK` delta
   `(last − now) & 0xFFFFFF`, store `last_se0/delta_se0/se0_windup` (h:190-192), sanity ±4000 like
   S:762-772 (an out-of-window delta also resets the lock counter), then `USB_TRIM_ACTUATE`: HSI
   build → **two-rate law**: `lock < USB_TRIM_LOCK_N` → `trim −= USB_TRIM_SIGN·(dev >> USB_TRIM_FAST_SHIFT)`,
   `lock++`; else `trim = trim0 − USB_TRIM_SIGN·sat(windup >> USB_TRIM_SLOW_SHIFT, ±USB_TRIM_SAT)`
   with `trim0` captured at first keepalive; write `ICSCR.HSI_TRIM`; HSE build → nothing.
   Runs in `.timecrit`; **walker path "keepalive: first instruction → exception return" ≤ 96
   cycles** (a token may follow a keepalive EOP after 2 bit-times of idle, USB 2.0 §7.1.18-19).
   Written from `rv003usb.S:740-806` only (V-USB's `osccalASM.s` is GPL: lesson A-6, no code).
6. Marker (§7.3): r10 = mask from `usb_dbg_mask` (RAM word), pulse per slot; production mask 0.
7. F6: `RV003_ADD_EXTI_MASK/HANDLER` port: on entry check `EXTI->PR & USB_DMASK`; if zero, jump
   to the user hook (flash), ack `RV003_ADD_EXTI_MASK` at exit (mirror S:113-129/645-650).
8. F8: Thumb `usb_pid_handle_ack`/`usb_pid_handle_setup` under `RV003USB_OPTIMIZE_FLASH`
   using `EP_*_OFFSET` (h:133-138) — ≈40 B each; keeps DFU configs unchanged.
9. `tools/py32_cyc.py`: Appendix B cost table (incl. `bl` 3, `nop` 1, `mov pc,lr` 2, staircase
   walk-through), `--cost-table` override (R4), reads the path list from the engine header,
   non-zero exit on any mismatch (CI use, T7).
Acceptance: assembles for both `MCU`s with `-Wa,--fatal-warnings`; **`arm-none-eabi-gcc -x
assembler-with-cpp -c rv003usb/rv003usb-arm.S -o /dev/null` (no `-D`) exits non-zero and its
stderr matches `#error`** (§2.6 guard); `tools/py32_cyc.py <elf>` exits 0 and prints all RX
paths = 32 (stuffed 64), all TX paths = 32/64 incl. the trailing-stuff path, keepalive ≤ 96,
staircase entries `rv003usb_wait_5…40` = N, with the store indices stated in the file header;
`nm` shows every engine symbol incl. `rv003usb_wait_5` inside `.timecrit` (SRAM); `.timecrit`
≤ 960 B (v1: 900 + ≈60 B staircase); demo_gamepad links on 002B with ≥ 768 B stack (ld ASSERT);
`grep -c '#if PY32F002Bx5' rv003usb/rv003usb-arm.S` = 0; `grep -q 'xobs/grainuum'
rv003usb/rv003usb-arm.S`; V003/WG015 builds untouched (files not owned). Hardware validation is T10.

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
V003 `demo_gamepad.bin` unchanged vs T0; **descriptor placement gate (PA S-4)**:
`arm-none-eabi-nm --numeric-sort demo_hidapi.elf | grep -iE 'descriptor|string|report' | awk
'$1 !~ /^2000/' | wc -l` = 0 (every descriptor symbol at an SRAM address); `grep -rn
'SysTick->LOAD' demo_gamepad demo_hidapi` empty (Р9).

**T6 — Calibration bench firmware + walker/VCD extension — tier: medium**
Files (create): `py32_bench/Makefile`, `py32_bench/main.c`, `py32_bench/bench_common.{c,h}`,
`py32_bench/bench_kernels.S`, `py32_bench/bench1_ioport.c` (LDR/STR IOPORT vs AHB vs literal,
via SysTick deltas over 1000× unrolled kernels, per port incl. GPIOF on F030 — OQ7),
`bench2_branch.c` (taken/untaken, aligned/unaligned targets, RAM vs flash, 16- vs 32-bit fetch
signature; **explicitly refute or confirm Grainuum's "taken branch = 3 cycles" from SRAM, and
time `bl rv003usb_wait_N`-shaped staircase calls for N = 5…40** — OQ4), `bench3_irq.c` (EXTI entry
latency: marker on first ISR instruction, LA measures; vector table flash vs RAM; pattern
`wg015_bench/bench3_irq.c`), `bench4_flash.c` (straight-line and branchy fetch profile from
flash; feeds OQ12 "can dispatch stay in flash on 002B"), `bench5_slot.c` (isomorphic RX slot
from RAM with PRBS + evictor, cumulative excursion), `bench6_trim.c` (HSI_TRIM LSB weight
**and sign**: step ±1 and count SysTick vs an external 1 kHz reference or MCO on LA — OQ3);
modify `tools/wg015_vcd/wg015vcd.py` + `tools/wg015_vcd/README.md` + `tools/wg015_vcd/selftest/*`:
`--marker-edge rise|both` (§7.3) and **`--gate-se0 LO:HI` (default `60:72` cycles = 1.25–1.5 µs)
on the already-computed `eop_se0_cyc` (vcd:448, 678-701) inside `eval_gates` (vcd:861-893)** — the
EOP width was reported but never gated (PA T-2, A-14, L-2). Bench framework rule: `main.c`'s menu
binds keys `1`…`9` to weak symbols `bench1_run`…`bench9_run` declared in `bench_common.h`
(`__attribute__((weak))`, "n/a" when NULL) and the Makefile globs `bench[0-9]_*.c`, so T11's
`bench7_loopback.c` drops in without touching T6 files.
Depends on: T1. Acceptance: builds for both `MCU`s via `Makefile.py32`; UART menu on
USART1 (PA2/PA3 on 002B? — pick and document); `tools/wg015_vcd/selftest/run_selftest.sh`
still reports `0 failed` **and contains a new case exercising `--gate-se0` on a capture with a
too-short EOP that must FAIL and a 64-cycle one that must PASS**; `grep -c 'gate_se0'
tools/wg015_vcd/wg015vcd.py` ≥ 3; `grep -rn 'SysTick->LOAD' py32_bench` empty (Р9).

### Wave 3

**T5 — DFU bootloader for PY32 — tier: hard — [MIT-attrib: joyboot boot counter] [GPL-ideas-only: micronucleus write-sleep read for the idea behind S-10 only]**
Files: `bootloader_dfu/dfu_py32.h` (new), `bootloader_dfu/py32/{Makefile, bootloader.c,
dfu_chip.h, dfu_transport.h, usb_config.h, funconfig.h, py32-dfu-bootloader.ld}` (new),
`bootloader_dfu/README.md` (add a PY32 section), `tools/wg015mkdfu.py` (add `--bcddevice`,
`--pid`, `--vid` options; defaults unchanged).
Depends on: T1, T2, T3.
Content: §8 verbatim (12 ms polls, cycle timebase, fixed boot words, boot-failure counter,
no option bytes); `usb_config.h` = copy of `bootloader_dfu/wg015/usb_config.h` with PY32
pins, `wTransferSize 0x80`, `bcdDevice 0x0210` (PY32 DFU; pid.codes 1209 kept, PA A-9), serial
`"P32D"`, `USBDESC` to RAM; `Makefile` wraps `Makefile.py32` (`TARGET=bootloader`,
`LDSCRIPT=py32-dfu-bootloader.ld`, `SIZE_BUDGET 4096` hard via ld `FLASH LENGTH=4096`, soft 3800
printed like `bootloader_dfu/wg015/Makefile:14-22`); ld = `py32_common.ld` include with `FLASH
ORIGIN 0x08000000 LENGTH 4096`, RAM per MCU (so the top-of-RAM block resolves to the same
addresses as in the app); `dfu_port_flash_timebase_init()` writes the 9 timing registers from
the family's factory set and enables `TICKINT` on the free-running SysTick; `SysTick_Handler`
(priority 3, `dfu_wraps++`) in `bootloader.c`; UPLOAD/APPCRC on for F030, decided by size on
002B; `DFU_ENABLE_BOOTCOUNT` 1 on F030 / 0 on 002B in `dfu_chip.h`.
Acceptance: builds for `MCU=PY32F002Bx5` and `PY32F030x8`; `sizecheck` passes; **`nm
bootloader.elf | grep ' py32_boot_flag$'` prints the same address as the T1 selftest ELF for
the same MCU** (shared boot words, Р6); `nm` shows `dfu_status` and `dfu_upload_buf` at SRAM
addresses (PA S-4) and `dfu_port_flash_write_block` anywhere (XIP ok); `grep -n 'DFU_POLL_ERASE_MS\|DFU_POLL_PROG_MS'
bootloader_dfu/dfu_py32.h` shows `12` for both and `grep -n 'DFU_CYCLES_PER_MS'` shows `48000`;
`! grep -rn 'OPTR\|RDP' bootloader_dfu/dfu_py32.h bootloader_dfu/py32/`; `grep -rn
'SysTick->LOAD' bootloader_dfu/dfu_py32.h bootloader_dfu/py32/` empty (Р9); `grep -c
'DFU_ENABLE_BOOTCOUNT' bootloader_dfu/dfu_py32.h` ≥ 2; `python3 tools/wg015mkdfu.py --selfcheck`
and `--bcddevice 0x0210` produce a suffix with the new value; the V003/WG015 DFU builds unchanged.

**T7 — Build integration, CI, top-level docs — tier: mechanical**
Files: `Makefile` (top), `.github/workflows/build.yml`, `.gitignore`, `README.md`.
Depends on: T1, T4, T5, **T2** (new: `check-cycles` needs `tools/py32_cyc.py`).
Content: `PROJECTS_PY32 := demo_gamepad demo_hidapi bootloader_dfu/py32`, `build_py32:` loops
`$(MAKE) -C $$d -f $(abspath rv003usb/py32/Makefile.py32) MCU=$$mcu` for `MCU in PY32F002Bx5
PY32F030x8`; **`check-cycles:` runs `tools/py32_cyc.py` on every PY32 ELF (PA T-1)**; `all:
build build_py32 check-cycles`; CI installs `gcc-arm-none-eabi` (no OpenOCD, no pyOCD needed
for building) and runs `make build_py32 check-cycles`; README gets a "PY32 / Cortex-M0+" section
(targets, pins, clocks, D± drive per Р8, loader, limits: no terminal, no DEBUG_TIMING, IRQ policy,
SysTick rule). Acceptance: `make all` green locally; CI yaml validated by a dry parse (`python3 -c
"import yaml,sys; yaml.safe_load(open('.github/workflows/build.yml'))"`); `grep -c 'check-cycles'
Makefile .github/workflows/build.yml` ≥ 2.

**T9 — HID blob loader for PY32 (optional, after T5 proves the transport) — tier: hard**
Files (create): `bootloader_py32/{bootloader.c, Makefile, usb_config.h, funconfig.h,
py32-usb-bootloader.ld, blobs/Makefile, blobs/blob_erase_page.S, blobs/blob_program_page.S,
blobs/blob_read_chunk.S, blobs/blob_boot_app.S, blobs/blob_rescale_timings.S}`; modify
`bootloader_wg015/wg015hostcli/wg015bflash.c` + its `README.md` (accept `bcdDevice 0x0210`, a
Thumb blob table, page = 128 B, unit = page).
Depends on: T5, T2. Content: port of `bootloader_wg015/bootloader.c` (RTC_REG → the ld-fixed
boot words of Р6, rdcycle → `dfu_port_cycles()`-style SysTick cycles, PLIC teardown →
`NVIC_DisableIRQ`+`EXTI->PR`, scratchpad at `0x20000000` 1152 B, `runwordpad` after it) with the
shared-C `RV003USB_BOOTLOADER` hooks; blobs are PIC Thumb (`-mthumb -fPIC`-free hand asm, entry
at +4, address guard `< APP_BASE` refused). Acceptance: builds; blobs ≤ 284 B; CLI refuses
loaders with other bcdDevice; `grep -rn 'SysTick->LOAD' bootloader_py32` empty.

**T11 — Writer→reader loopback bench (PA S-6) — tier: medium — [MIT-attrib if Pico `test_ll.c` vector logic is copied]**
Files (create): `py32_bench/bench7_loopback.c`, `py32_bench/loopback_vectors.h` (generated),
`py32_bench/gen_loopback_vectors.py`.
Depends on: T2 (engine), T6 (bench framework, weak-symbol menu, Makefile glob).
Content: two boards D+↔D+, D−↔D−, common ground, board B enables its DPU (1.5 kΩ D− pull-up),
board A keeps D± as inputs with internal pull-downs (`PUPDR=10`) so the idle state is J. Role
chosen from the UART menu (`7` then `w`/`r`). Writer (A): never enables EXTI; every 1 ms calls
`usb_send_data(vec, len, 0, 0xC3 /*DATA0*/)` over the vector set from `loopback_vectors.h` —
{all-0x00, all-0xFF, 0x7E/0xFE runs (V-USB's bit-6 unstuff bug pattern, PA A-7), payloads whose
CRC16 tail sent LSB-first ends in six ones (trailing stuff bit, PA L-6/OQ11 — the generator
searches for them and asserts at least 4 exist), one with a deliberate seven-ones stuffing
violation (must be rejected, PA L-7), one with an 8-cycle SE0 glitch (PA L-3, optional), LFSR
random} — and counts packets sent. Reader (B): links the engine and *its own*
`usb_pid_handle_ack/in/out/setup/data` stubs (no `rv003usb.c`); `usb_pid_handle_data` counts
CRC-valid packets per vector id (only CRC-valid packets reach C, §7.2) and answers with
`usb_send_empty(ACK)` so the LA also measures turnaround; prints sent/received/missing per
vector on UART every second. The LA on the wire feeds `wg015vcd.py decode/rx/tx` (`--gate-se0`,
`--gate-turnaround 7.5`, offset histogram). `gen_loopback_vectors.py --check` regenerates the
header and diffs it (CI-safe, no hardware).
Acceptance: `make -C py32_bench MCU=PY32F002Bx5` and `MCU=PY32F030x8` build with bench7
present (`nm bench.elf | grep -q bench7_run`); `python3 py32_bench/gen_loopback_vectors.py
--check` exits 0 and reports ≥ 4 six-ones-tail vectors and 1 stuffing-violation vector;
`grep -rn 'SysTick->LOAD' py32_bench` empty. Hardware run and numbers land in T10's
`calibration.md`.

### Wave 4 (hardware, sequential)

**T10 — Bring-up, calibration, hardware validation — tier: hard, needs two boards + LA + scope**
Files: `doc/py32/calibration.md` (new), `rv003usb/py32/usb_port_py32_tune.h` (values only,
after T2 is merged). Depends on: all above (T11 for the loopback run).
Rig: J-Link + Puya DFP or a known-good DAPLink (PA A-12); record `DBG_IDCODE` and chip marking
with every result. Steps: blink@0x08000000 cold start; bench1-6 → fill the table (IOPORT cost
per port, branch/alignment incl. the "taken = 3?" question and staircase costs, entry latency
median+spread, 16/32-bit fetch, HSI_TRIM LSB weight **and sign**, SRAM-across-SYSRESETREQ = OQ2);
set `USB_RX_SYNC_DELAY` from the VCD `rx` sample-offset histogram (target 14-18/32, **min ≥ 14** —
F5/dribble); `wg015vcd.py rx --gate-entry 55` (keepalive SE0 must be sampled in time, PA L-9);
verify every TX cell period 32±0, `wg015vcd.py tx --gate-turnaround 7.5 --gate-se0 60:72`;
**scope D± tr/tf into the real cable/load per OSPEEDR setting** (USB 2.0 Table 7-9: 75–300 ns;
Р8/OQ10) — raise `USB_PORT_OSPEED` only if 300 ns is exceeded; **count keepalives between reset
end and the first SETUP on Windows 10/11 xHCI, direct and behind a TT hub (OQ9)** and set
`USB_TRIM_LOCK_N`/`_FAST_SHIFT` so the servo is inside 0.25 % before it; loopback run (T11)
with all vectors, 0 missing except the two deliberately invalid ones; enumerate demo_hidapi on
Linux/Windows/macOS ≥100 replugs each, direct xHCI + USB2 hub (TT) + USB3 hub + Raspberry Pi
(PA L-19), 1 h soak; warm re-enumeration ×100 and HID-out first-packet check (non-EP0 OUT
toggles are not reset by a bus reset, c@HEAD:305-308, PA L-11); Windows selective suspend →
resume (bounded spin, PA L-13); macOS GET_STATUS ZLP tolerance (OQ12); `dfu-util -D` 100 cycles
with stock dfu-util (OQ13); **boot-failure counter**: flash a deliberately crashing app on F030
→ loader stays after the 4th reset, `dfu-util -l` sees it; temperature sweep (hair-dryer/freezer
spray) with the servo on HSI builds — must stay enumerated, `rx.slope_cyc_per_bit` ≤ 0.16 after
lock (PA L-10).

### Ownership matrix (must stay disjoint)

| Task | Owns | Licence tag |
|---|---|---|
| T0 | merge only (runs alone) | — |
| T1 | `rv003usb/py32/{py32_min.h, ch32fun.h, startup_py32.S, py32_common.ld, py32f002b.ld, py32f030x6.ld, py32f030x8.ld, Makefile.py32, py32_stdio_stub.c, README.md, selftest_main.c}` | own (register maps from RM/TRM, no CMSIS/LL copy) |
| T2 | `rv003usb/rv003usb-arm.S`, `rv003usb/py32/usb_port_py32_asm.h`, `rv003usb/py32/usb_port_py32_tune.h`, `tools/py32_cyc.py` | [MIT-attrib] Grainuum staircase; [GPL-ideas-only] |
| T3 | `rv003usb/rv003usb.c`, `rv003usb/rv003usb.h`, `rv003usb/usb_port_ch32.h`, `rv003usb/wg015/usb_port_wg015.h`, `rv003usb/py32/usb_port_py32.h` | own |
| T4 | `demo_gamepad/*`, `demo_hidapi/{usb_config.h,funconfig.h,demo_hidapi.c,README.md}` | own |
| T5 | `bootloader_dfu/dfu_py32.h`, `bootloader_dfu/py32/*`, `bootloader_dfu/README.md`, `tools/wg015mkdfu.py` | [MIT-attrib] joyboot counter; [GPL-ideas-only] |
| T6 | `py32_bench/{Makefile, main.c, bench_common.c, bench_common.h, bench_kernels.S, bench1_ioport.c, bench2_branch.c, bench3_irq.c, bench4_flash.c, bench5_slot.c, bench6_trim.c}`, `tools/wg015_vcd/*` | own |
| T7 | `Makefile`, `.github/workflows/build.yml`, `.gitignore`, `README.md` | — |
| T8 | `doc/py32/{chip_info.md, ledger_arm.md, STATE.md, TODO.md}` | — |
| T9 | `bootloader_py32/*`, `bootloader_wg015/wg015hostcli/*` | own |
| T11 | `py32_bench/{bench7_loopback.c, loopback_vectors.h, gen_loopback_vectors.py}` | [MIT-attrib] if Pico `test_ll.c` logic is copied |
| T10 | `doc/py32/calibration.md`, values in `usb_port_py32_tune.h` (after T2) | — |

### Wave order and dependency edges (re-checked after the v2 edits)

| Wave | Tasks | Edges |
|---|---|---|
| 0 | T0 | — |
| 1 | T1, T3, T8 | all ← T0; T3's PY32 compile check ← T1 |
| 2 | T2, T4, T6 | T2 ← T1, T3; T4 ← T1, T3; T6 ← T1 |
| 3 | T5, T7, T9, T11 | T5 ← T1, T2, T3; T7 ← T1, T2 (new), T4, T5; T9 ← T2, T5; T11 ← T2, T6 (new task) |
| 4 | T10 | ← all, incl. T11 |

New edges versus v1: T7 → T2 (`check-cycles` needs the walker; T7 already sat behind T5 → T2,
so its wave is unchanged) and T11 → T2, T6 (bench7 uses the merged engine and T6's menu/glob;
T6 itself stays in wave 2 because bench1-6 do not need the engine). The shared boot words are
`PROVIDE`d by T1's ld and only *referenced* by T3/T5 → no new edge. No task gained a file
another task owns.

## 10. Risks

| R | Risk | Trigger | Fallback |
|---|---|---|---|
| R1 | Older PY32F002B silicon has no 48 MHz HSI (`HSI_FS=101` reserved in RM002B p63) | bench6 / clock output ≠ 48 MHz, or `HSIRDY` never | require "B-C" silicon (DBG_IDCODE at 0x40015800, RMBC p265/p269 reset value 0x20200061) or use PY32F030 only |
| R2 | HSI accuracy/drift beyond servo range; servo hunting | LA sample-offset slope > 0.16 cyc/bit after lock; enumeration drops with temperature | reduce the slow-rate gain (larger `USB_TRIM_SLOW_SHIFT`), widen saturation; HSE crystal on F030; 002B: only with servo proven |
| R3 | 002B RAM (3 KB) too small for RAM-resident RX+TX+descriptors+staircase+DFU buffers+stack | ld ASSERT in T1/T5 | move dispatch (`se0_complete_flash…interrupt_complete`) back to flash (it is not cell-critical, ≈120 B; bench4/OQ12 says whether flat LATENCY=1 keeps it deterministic); shorter descriptors; HID loader instead of DFU on 002B |
| R4 | RAM branch/alignment penalties or 16-bit-only fetch make the paper ledger wrong (Grainuum's "taken = 3" note) | bench2 shows `B`≠2, `BL`≠3 or alignment deltas | `.balign 4` loop heads + re-pad via walker with measured costs (`--cost-table`) |
| R5 | IRQ entry outside [11,74] (long PRIMASK sections, equal-priority ISRs, SysTick at prio 0) | bench3 spread; sporadic CRC failures | enforce Р7; RAM vector table; assert no ISR at priority 0 in `usb_port_hw_setup` |
| R6 | Flash timing registers wrong for the running HSI mode → mis-programmed pages | verify readback after DFU download fails | load the set matching `ICSCR.HSI_FS` exactly as `__HAL_FLASH_TIMMING_SEQUENCE_CONFIG`; program at 24 MHz? no — keep 48, but the 002B non-C set has no 48 MHz entry (R1) |
| R7 | SRAM not retained across SYSRESETREQ (OQ2) → boot flag / boot counter / double-tap lost | T10 test | drop the APP fast-path (CRC path still boots), STAY via `SFTRSTF` only; the counter degrades to "never STAY" (safe direction) |
| R8 | Turnaround > 7.5 bit-times (flash-resident C handlers with 1-WS; `dfu_class_request()` runs inside the ISR on a SETUP's status path, PA D-13) | `wg015vcd.py tx` gate | move `usb_pid_handle_in/data` hot path into `.timecrit` (attribute), or the ACK-first CRC pipeline from `origin/rx-tx-branchless-ch32v003-rebased` (branch_notes.md Part B, commit 3735518; PA S-2) — not more asm |
| R9 | DFU > 4 KB on 002B | sizecheck | `DFU_ENABLE_BOOTCOUNT 0` (already default there), `DFU_ENABLE_UPLOAD 0`, `APPCRC 0`, strings trimmed (v003 precedent in TODO.md 19b); or 8 KB loader (app at +0x2000) |
| R10 | Vendor documents contradict each other (24 vs 48 MHz, HSI_FS table); no public errata | — | every number in `py32_min.h` cites a page; T10 measures and records the silicon revision |
| R11 | Shared EXTI vector with user pins | app needs EXTI on lines 2/3 (or 4-15) | F6 hook (T2 step 7) |
| R12 | D± edge rates: **lowest OSPEEDR + 33 Ω (Р8) may be too slow into a long cable (> 300 ns, USB 2.0 Table 7-9), or a fast setting rings** (Grainuum's measured failure, PA S-9); 3.3 V only; no tr/tf table in the DS (OQ10) | scope in T10 shows tr/tf outside 75–300 ns, or overshoot beyond VDD | raise `USB_PORT_OSPEED` one step at a time, keep the series resistors; never capacitors on D± (PA A-8) |
| R13 | Per-part behaviour differences between 002B and 030 (flash controller, IOPORT on port F) | bench1 on both | keep all timing in RAM (Р4) so only clock init differs |
| R14 | Somebody reconfigures SysTick (1 ms reload for a tick, `Delay_Ms` rewrite) → keepalive delta ≠ 48000 → servo silently open-loop (the v1 loader design had exactly this) | `delta_se0_cyccount` telemetry ≈ 0 instead of ≈ 48000; `rx` slope drifts with temperature | Р9 rule + the `SysTick->LOAD` greps in T1/T4/T5/T6/T9/T11 acceptance; a `_Static_assert`-style comment in the shim |
| R15 | Servo lock slower than the host's reset→first-SETUP window (Windows 10/11 xHCI, USB3 ports) → first GET_DESCRIPTOR fails; V-USB's documented regression (PA A-6, L-14) | Win10/11 enumeration fails while Linux works; keepalive count before the first SETUP (OQ9) < lock time | raise `USB_TRIM_FAST_SHIFT` gain / `USB_TRIM_LOCK_N`; start from the factory word (already); HSE on F030 |
| R16 | GPL contamination (LemcUSB, stm32f030-vusb, V-USB, micronucleus) through "translation" of routines | `Provenance:` trailer missing or names a GPL source for code; the Р10 grep matches | revert the commit; re-derive from `rv003usb.S`/own analysis; Р10 is a hard rule |
| R17 | Boot-failure counter false STAY: an app that legitimately resets itself > 3 times before reaching `usb_setup()` (or that never calls it) | loader stays with a healthy app | app calls `py32_app_alive()` early (T3 default in `usb_port_hw_setup()`); `DFU_ENABLE_BOOTCOUNT 0`; explicit `DFU_FLAG_APP` request always wins |
| R18 | Bounded preamble spin (F9 fix) coarsens the sync-edge detection to 4/4/4/7-cycle spacing → sample-offset band drifts outside 14–18 | `rx` histogram min < 14 or max > 18 after T2 | re-derive `USB_RX_SYNC_DELAY`; if still outside, use the +2-per-poll (7/7/7) variant and re-centre, or raise the limit so the unrolled counter check is rarer |

## 11. Open questions (could not be verified from documents)

| OQ | Question | Why it matters | How to close |
|---|---|---|---|
| OQ1 (PA Q-1) | Is 48 MHz HSI officially supported on PY32F002B? DS V1.0 says 24 MHz max, RM B-C says fmax 48 and defines HSI_FS=101; DS has no 48 MHz accuracy row | production viability of target #2 | ask PUYA / check DS ≥ V1.8 listed on puyasemi.com product page; bench6 |
| OQ2 (PA Q-5) | SRAM content retained across SYSRESETREQ? (RM only lists registers) | boot-flag / counter / double-tap scheme (§8) | T10 test; fallback R7 |
| OQ3 (PA Q-2) | HSI_TRIM LSB weight at 48 MHz, **sign**, monotonicity and range of the 13-bit field | servo gain, saturation, lock time | bench6 |
| OQ4 (PA Q-11) | Cortex-M0+ configuration on PY32: fetch width (16 vs 32), multiplier (DS says single-cycle), alignment penalties from SRAM, taken-branch cost 2 (TRM) vs 3 (Grainuum), `bl`/`mov pc` costs for the staircase | ledger validity | bench2 |
| OQ5 (PA Q-6) | Real EXTI entry latency incl. 1-WS vector fetch and GPIO input synchronizer delay (2 cycles assumed) | window §2.2, sample phase | bench3 + VCD `rx` entry stats |
| OQ6 | 002B "Load Flash" boot zone: option-byte programming flow, erase protection reliability, RDP lock-out risk (PA A-11) | brick-proof loader alternative to Р6 | RM002B p20-21/p42; try on hardware after DFU works |
| OQ7 (PA Q-3) | Are all GPIO ports (incl. GPIOF on F030) on the single-cycle IOPORT? (memory map says the whole 0x5000_0000 region) | 1-cycle sample assumption | bench1 per port |
| OQ8 | 5 V tolerance of PY32 I/O (not in DS) | hardware design | irrelevant at VDD 3.3 V; document |
| OQ9 (PA Q-4) | How many keepalives arrive between reset end and the first SETUP on Windows 10/11 xHCI, direct and behind a TT hub? | servo lock budget (Р5, R15) | LA capture + `wg015vcd.py decode` in T10 |
| OQ10 (PA Q-7) | D± tr/tf per OSPEEDR setting into 200–450 pF; are the series resistors needed on PY32 at all? | Р8, R12 | scope in T10 |
| OQ11 (PA Q-8) | Does the ARM TX emit the trailing stuff bit after a six-ones CRC tail? (Structure says yes, §2.5) | 1/64 of DATA packets would be NAKed/ignored otherwise | walker path (T2) + loopback vectors (T11) |
| OQ12 (PA Q-9) | macOS LS host behaviour (GET_STATUS answered with a ZLP, c:480-491 `#if 0`) | PA L-16 | T10 on a Mac; if it objects, implement `GET_STATUS` (2 bytes from `always0`), ≈20 B |
| OQ13 (PA Q-10) | Stock `dfu-util` interop at 8-byte EP0 with `wTransferSize` 128 and `bwPollTimeout` 12 ms | PA D-4; first proof arrives on WG015 (STATE.md) | first hardware, either target |
| OQ14 (PA Q-12) | Can the dispatch tail stay in flash on 002B (R3 fallback) given flat LATENCY=1 and no cache? | 3 KB RAM budget | bench4 + walker with the flash cost model |

## Appendix A — Paper ledger of the branch engine (walker over the real object, TRM costs)

RX (RAM): entry→IDR sample 3; entry→DELAY start 21; DELAY(96) = 96; preamble poll 5/iter
(T2 target: 4/4/4/7 with the bounded counter); detect→DELAY(71) start 10; DELAY(71) = 72;
packet_type top→sample done 22; packet_type iteration 32/32; bit_process zero/one × mid/EOB =
32/32/32/32; one+stuffed 64; sample at +10 in bit_process; SE0 → `bx` 20. First PID sample =
detect + 104 cycles. Keepalive path (T2 target): first instruction → exception return ≤ 96.
Staircase (T2): `bl rv003usb_wait_N` = N exactly, N = 5…40 (`BL` 3 + `NOP`·(N−5) + `MOV PC,LR` 2).

TX (0-WS model = target for T2 step 4; today runs from flash and reaches ≈32 by wait states):

| Path | now | target | pad |
|---|---|---|---|
| entry → turnaround BSRR store | 16 | — | — |
| entry → MODER (drivers on) | 30 | — | — |
| entry → first preamble store | 51 | measure (turnaround budget) | — |
| pre_and_tok zero / one (store idx 9 / 8) | 20 / 19 | 32 / 32, idx 9 / 9 | +12 / +1 before store, +12 after (staircase) |
| pretok last bit → send_inner top | 12 | 32-relative: keep the first data store on the grid | check with walker |
| send_inner zero / one, mid-byte | 21 / 21 | 32 / 32 | +11 / +11 (after the store) |
| send_inner zero / one, end-of-byte | 21 / 20 | 32 / 32 | +11 / +12 |
| one + stuffed (store idx 30), incl. the trailing-stuff case at the last CRC bit | 40 | 64, idx 43 | +13 before, +11 after |
| last data bit → CRC byte 1 → top | 23 | 32 | +9 |
| last CRC bit → SE0 store | 31 | ≈32 | ≈+1 |
| SE0 width | 37 | 64 (2 bit-times = 1.33 µs; spec 1.25–1.5 µs = 60–72 cycles; V003 ≈48, below spec) | +27 (tunable `USB_TX_SE0_PAD`; gate `--gate-se0 60:72`) |
| J-park → MODER release | 19 | ≥16 | 0 |

## Appendix B — cycle walker (seed for `tools/py32_cyc.py`)

Cost model: `ldr/str … [rX,#0|16|24]` with an IOPORT base register = 1; `[pc,#…]` = 2; other
loads/stores = 2; `b<cc>` 2 taken / 1 not; `b`, `bx`, `blx` = 2; `bl` = 3; `mov pc, lr` = 2;
`nop` = 1; `push` 1+N; `pop {…pc}` 3+N; everything else 1; the table is a parameter
(`--cost-table`, R4) so bench2's measured values can replace it. Input: `arm-none-eabi-objdump
-d --no-show-raw-insn <elf>`; paths are segments `(start, end, {branch_addr: taken})`; a `bl`
into the staircase is followed (the callee's `nop`s and `mov pc,lr` are counted in the caller's
path). The engine header must list the label names of every path — RX slots, TX slots incl.
"one+stuffed at the last CRC bit", the keepalive path, and the 36 staircase entries — so the
tool can check `== 32/64/≤96/N` mechanically and exit non-zero otherwise (T2 acceptance, T7
CI). The 60-line script used for Appendix A is reproduced in `doc/py32/ledger_arm.md` by T8
(source: this analysis).

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

## 12. Changelog vs the pre-prior-art plan

One line per change: what — why — source. "v1" = PLAN.md at 176d357.

1. Header: status v2; `S:<n>` now declared as HEAD 5342825 (branch) numbering — v1 claimed master 80b1893 but its `S:` cites were branch-numbered (`handle_se0_keepalive` S:740 vs 673 at master); `c:`/`h:` kept at 80b1893, new `c@HEAD:` form for cites imported from PRIOR_ART — source: `git show 80b1893:rv003usb/rv003usb.S | grep -n`.
2. Header/§1: added the `PA S-/A-/D-/L-/Q-/T-` cite convention and one evidence row per prior-art source with its licence class — source: PRIOR_ART.md §1, §9.
3. §0: verdict extended with the prior-art confirmation of Р1–Р7, the two flipped defaults, the added work and the two v1-internal inconsistencies — source: PRIOR_ART §0 items 1-3; own review.
4. §2.2, §2.4 F9, T2 step 3b, R18: bounded preamble spin (≈512 cycles, 4/4/4/7 unrolled counter, re-derived `USB_RX_SYNC_DELAY`) — resume signalling / stuck line otherwise spins with IRQs masked — source: PA A-16, L-13.
5. §2.3, §7.4, T2 step 4, §7.1 row `rv003usb_wait_<N>`, Appendix A/B, T6 bench2: Grainuum-style exact-N-cycle pad staircase; `lr` rule derived from the engine (TX prologue pushes `lr` arm.S:357; RX `r14` becomes POLY_RX only at arm.S:128-140) — replaces `DELAY_CYCLES` multiples-of-3 and ≈200 B of inline pads — source: PA S-1 (Grainuum `grainuum-phy-ll.s` L433-461, MIT).
6. §2.4 F5: severity "tune" → "must", offset ≥ 14 required by the 260 ns dribble allowance; T10 gate min ≥ 14 — source: PA D-9, L-4 (USB 2.0 §7.1.9/§7.1.14).
7. §2.4 F3: "minor" → required (hosts may abort with a stuffing violation) — source: PA D-7, L-7.
8. §2.5: trailing-stuff-bit structure noted; walker path "one+stuffed at the last CRC bit" added to T2 acceptance and Appendix A; OQ11 — source: PA L-6, Q-8.
9. §2.5, §3.2, Р4: PY32 flash is flat LATENCY=1 with no prefetch/cache; alignment artefact explained; WG015 "flash first" directive does not transfer — source: PA D-2, A-2 (Grainuum issue #1).
10. §2.6 (guard paragraph), T1 conventions `DEFS`, T1 acceptance (`make -n` grep + differing objects), T2 step 0 and acceptance (`#error` when assembled without `-D`): build-hole guard as a hard criterion — source: PA S-11, A-3; branch_notes.md anti-pattern 2.
11. §9 conventions: `-D$(MCU)=1` added so the branch's `#if PY32F002Bx5` is exercised at least once (T1 interim) — source: §2.6 build log.
12. §2.7: Grainuum's "taken branch = 3" claim routed to bench2 — source: PA §1 row 1, Q-11.
13. §2.4.5: field corroboration (Grainuum at −0.058 %) and the lock-budget paragraph — source: PA §5.1, S-12, A-6, L-14.
14. §3.1: TheYkk PLL floor note, "no public working 48 MHz PY32 bit-bang" — source: PA §1 row 6, A-19, §5.1.
15. §3.2: `MOV PC,Rm` = 2 added for the staircase arithmetic; SysTick 24-bit wrap note; CSS→NMI row — source: TRM Table 3-1; PA §5.2, §5.4.
16. §3.3: "no ROM loader on 002B" and the option-byte/RDP rule row — source: PA A-11, A-12, §5.3.
17. §3.4: OSPEEDR "set high speed" → lowest setting + 33 Ω; encoding to be confirmed on RM002B p78 by T3; `SCB.ICSR` added; "no CMSIS file copied" — source: PA D-10/S-9 (Р8); Р9/§8; Р10.
18. §3.5 (new): toolchain/probe/recovery facts and their consequences (no OpenOCD, pyOCD+DFP or J-Link, `puyaisp` F030-only, address guard load-bearing on 002B) — v1 T1 said "openocd -f target/py32f0xx?" — source: PA §5.4, A-12.
19. §4: note that the two post-176d357 commits touch only `doc/py32/`, dry-run stands — source: `git diff --stat 176d357 HEAD`.
20. §5: rows for the loopback bench (T11), the `--gate-se0` VCD gate (T6), `check-cycles` (T7), provenance ledger (T8) — source: PA S-6, T-2, T-1, Р10.
21. Р1: Grainuum runtime-struct comparison — source: PA D-1.
22. Р2: seams extended with `USB_DPU_DELAY_MS` and the boot-counter clear — source: PA A-10, S-7.
23. Р3: TheYkk cautionary note — source: PA A-19.
24. Р4: staircase in RAM; unanimity of prior art; corollary `nm` gates for descriptors and DFU reply pointers — source: PA S-4, D-2.
25. Р5: two-rate servo law with the tune knobs `USB_TRIM_LOCK_N/FAST_SHIFT/SLOW_SHIFT/SAT/SIGN`, early EXTI ack and the ≤ 96-cycle keepalive budget; CSS NMI for HSE builds — source: PA S-12, D-5, A-6; own derivation from USB 2.0 §7.1.18-19.
26. Р6: boot words moved to an ld-fixed top-of-RAM block shared by loader and app — v1's `.noinit` variable would have had a different address in each image (found while placing the boot counter) — source: own review of v1 §8/T3; PA S-7.
27. Р7: RAM vector table only if bench3 shows the need — source: PA D-11.
28. Р8 (new): D± drive strength lowest + 33 Ω, reasons stated, every "high" occurrence fixed (§3.4, T1 README, T3, R12) — source: PA S-9, D-10, A-8, L-18; S:857-861; README.md:31.
29. Р9 (new): SysTick free-running rule; loader timebase = 32-bit cycles from wrap-counted SysTick, `DFU_CYCLES_PER_MS 48000` — v1's 1 ms-reload tick would have zeroed the engine's keepalive delta and silently disabled the servo — source: own review of v1 §7.1 vs §8; PA L-21, A-5.
30. Р10 (new): licence/provenance rule (MIT adopt with attribution; GPL ideas only; `Provenance:` trailer; grep guard; task tags) — source: task brief; PRIOR_ART §1 licence column.
31. §7.1: new macro rows `USB_RX_PREAMBLE_LIMIT`, `USB_TRIM_*`, staircase entries; `USB_TICK_ADDR` annotated with the LOAD rule; pads "cycles, never µs" — source: PA A-16, S-12, S-1, S-3.
32. §7.2: "only CRC-valid packets reach C" stated for T11 — source: arm.S:262-314.
33. §8: `DFU_POLL_ERASE_MS/PROG_MS` 8 → 12 with the worst-case arithmetic (3.0 + 5.0 + 1.5 = 9.5 ms masked window end, +1 ms host phase, → 12) — source: PA S-3; DS002B p39 Table 5-15; dfu.c:124-125, 223, 231-233.
34. §8: `dfu_port_cycles()` rewritten (wrap counter + `PENDSTSET` check), `DFU_CYCLES_PER_MS` 1 → 48000 — source: Р9; PA L-21.
35. §8: boot-failure counter (`DFU_ENABLE_BOOTCOUNT`, `py32_boot_count`, > 3 → STAY, app clears via `py32_app_alive()`) — source: PA S-7 (joyboot `bootloader.c` L64-90, MIT).
36. §8: `DFU_APP_BASE` never lowered (guard load-bearing on 002B), option bytes never written, joyboot `idle_func` rejected, V003 XIP routine named as the model, stock dfu-util — source: PA A-12, A-11, D-3, §5.3, D-4.
37. T0: dry-run commit named; acceptance adds `git diff origin/py32 -- rv003usb/rv003usb-arm.S` empty — source: own.
38. T1: `DEFS` symmetric to `.c`/`.S`; `-D$(MCU)=1`; fixed `.noinit` block + `PROVIDE`d boot words; `py32_app_alive()`; `NMI_Handler` for HSE; `flash` via pyOCD/J-Link, no OpenOCD; `check-cycles` target; README items (drive, 22 pF, DPU delay, SysTick rule, probes); acceptance adds the build-log grep, the differing-objects check, `py32_boot_flag` address, `SysTick->LOAD` single-writer grep — source: PA S-11, §5.4, A-8, A-10; Р6, Р9.
39. T3: `USB_PORT_OSPEED` default 0, `USB_DPU_DELAY_MS`, `py32_app_alive()` call, boot flag as ld symbol; acceptance greps — source: Р8, PA A-10, S-7, Р6.
40. T8: provenance table in STATE.md; task list T0–T11 — source: Р10.
41. T2: steps 0 (guard + MIT header), 3b (bounded spin), 4 (staircase, SE0 spec numbers, trailing-stuff path), 5 (early ack, two-rate law, ≤ 96 cycles, written from rv003usb.S only), 9 (walker `--cost-table`, path list, exit code); acceptance adds the no-`-D` failure test, keepalive/staircase paths, `.timecrit` ≤ 960 B, `#if PY32F002Bx5` count 0, Grainuum attribution grep; tags [MIT-attrib]/[GPL-ideas-only] — source: PA S-11, A-16, S-1, A-14, S-12, T-1; Р10.
42. T4: descriptor-placement `nm` gate and `SysTick->LOAD` grep — source: PA S-4; Р9.
43. T6: bench2 items (taken-branch = 3?, staircase timing), bench6 sign, bench1 per port incl. GPIOF, `--gate-se0 LO:HI` in `eval_gates` with a selftest case, weak-symbol menu + Makefile glob so T11 can add a bench without touching T6 files; ownership changed from `py32_bench/*` to an explicit file list — source: PA Q-11, Q-2, Q-3, T-2, A-14, L-2; S-6.
44. T5: content per §8 v2; acceptance adds same-address `py32_boot_flag` check, RAM check for `dfu_status`/`dfu_upload_buf`, 12/48000 greps, OPTR/RDP grep, `SysTick->LOAD` grep, `DFU_ENABLE_BOOTCOUNT` grep; tags — source: Р6, PA S-4, S-3, A-11; Р9; S-7.
45. T7: `check-cycles` in `all` and CI; new dependency on T2; "no OpenOCD/pyOCD in CI" — source: PA T-1, §5.4.
46. T9: boot words = the Р6 block; `SysTick->LOAD` grep — source: Р6, Р9.
47. T11 (new task): physical writer→reader loopback bench with generated vector set (six-ones CRC tails, 0x7E/0xFE runs, stuffing violation, SE0 glitch), reader with own handler stubs answering ACK; depends on T2, T6 — source: PA S-6 (Grainuum 33C3, Pico `test_ll.c`), A-7, L-3, L-6, L-7.
48. T10: rig (J-Link/DAPLink, `DBG_IDCODE`), `--gate-entry 55`, `--gate-se0`, offset min ≥ 14, scope tr/tf per OSPEEDR, keepalive count on Win10/11 xHCI → `USB_TRIM_LOCK_N`, loopback run, host matrix (xHCI/TT hub/USB3 hub/RPi; Linux/Windows/macOS), warm re-enumeration + HID-out first packet, suspend/resume, macOS GET_STATUS, stock dfu-util, boot-counter crash test — source: PA A-12, L-9, L-2, L-4, Q-7, Q-4, S-6, L-19, L-11, L-13, L-16, Q-10, S-7.
49. Ownership matrix: licence-tag column; T6 explicit file list; T11 row — source: Р10; own.
50. Wave table (new): dependency edges re-checked; T7 → T2 and T11 → T2, T6 added; waves unchanged — source: own.
51. §10: R2 fallback names the slow-rate knob; R3 mentions the staircase and bench4; R4 `BL` and `--cost-table`; R7 counter degrades safely; R8 names the ACK-first branch/commit; R9 BOOTCOUNT first; R10 errata; R12 rewritten for the low-slew decision; new R14 (SysTick misuse), R15 (lock vs host window), R16 (GPL contamination), R17 (false STAY), R18 (bounded-spin jitter) — source: PA S-2, D-3, A-6/L-14, A-8; Р8, Р9, Р10; own.
52. §11: PA Q-n cross-references on every existing OQ; OQ3 adds sign; OQ4 adds branch/staircase costs; OQ6 adds RDP risk; new OQ9 (keepalive count), OQ10 (edge times), OQ11 (trailing stuff bit), OQ12 (macOS GET_STATUS), OQ13 (dfu-util interop), OQ14 (dispatch in flash) — source: PRIOR_ART §8.
53. Appendix A: bounded-poll spacing, keepalive ≤ 96, staircase identity, trailing-stuff row, SE0 spec numbers and gate — source: PA A-16, S-1, L-6, A-14/L-2.
54. Appendix B: `bl`/`mov pc,lr`/`nop` costs, `--cost-table`, staircase walk-through, path-list contents and exit code — source: PA S-1, T-1; R4.
55. Task count 11 → 12 (T11); every task's acceptance re-expressed so each item is a command, size, symbol/address, or grep — source: task brief.
56. §2.5, §3.2, §3.3: four table rows inherited from v1 had unescaped `|` inside code spans (Bus turnaround, VTOR, Factory trim) and the SRAM-retention row lacked its Source cell — escaped / filled (`same | RM002B p56, p51/p53`), content unchanged — source: markdown column check over the file.
