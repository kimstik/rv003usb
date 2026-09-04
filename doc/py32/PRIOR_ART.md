# PY32 port — prior art: what to steal, what to avoid, where we diverge

Status: v1, 2026-09-04. Decision document merging six prior-art sweeps (cortex-m0-bitbang,
rp2040-pio-usb, py32-ecosystem, vusb-lineage, timing-verification, usb-ls-spec-traps; all dated
2026-09-03) with the repo state at HEAD 176d357. Companion to `doc/py32/PLAN.md` (owned by
another agent; nothing here modifies it — where this document disagrees with PLAN it says so
in §4 and leaves the change to PLAN's owner).

Evidence conventions (same as PLAN.md): `S:<n>` = `rv003usb/rv003usb.S` at 176d357, `c:<n>` =
`rv003usb/rv003usb.c`, `arm.S:<n>` = `rv003usb/rv003usb-arm.S` in `origin/py32` (0ad3c42),
`dfu.c:<n>` = `bootloader_dfu/dfu.c`, `dfu_015.h:<n>` / `dfu_v003.h:<n>` /
`dfu_rv003usb.h:<n>` = the DFU port headers, `vcd:<n>` = `tools/wg015_vcd/wg015vcd.py`,
`PLAN §x` = `doc/py32/PLAN.md`, `Sweep n` = the sweep that found the fact, `TRM` = Arm
Cortex-M0+ TRM r0p0 DDI0484B, `RM002B`/`RMBC`/`RM030`/`DS002B`/`DS030` as in PLAN §1.
Grainuum line numbers are from `grainuum-phy-ll.s` at `xobs/grainuum@master` fetched today;
TheYkk line numbers from `TheYkk/py32f030-bitbang-usb@main` fetched today. Anything not
independently verified in this session carries **UNVERIFIED**.

Scope: USB low-speed (1.5 Mbit/s) device only. No full-speed or host-mode bit-bang on a
Cortex-M0/M0+ exists anywhere in the six sweeps (Sweep 1 summary, Sweep 2 §3); it is not a
goal and not a fallback.

## 0. Verdict

Nothing in the field is a drop-in for this port, and nothing needs to be. Every working
software LS-USB stack on Cortex-M0+ (Grainuum, LemcUSB, joyboot) is exactly what PLAN.md
already prescribes — a cycle-counted Thumb PHY executed from RAM at 48 MHz / 32 cycles per bit
with interrupts masked — and the single PY32-specific attempt on record (TheYkk, Jan 2026) is a
verbatim Grainuum copy whose published clock setup and GPIO base cannot run on a PY32F030 at
all (§1 row 6, §3 A-19). The engineering worth taking is small, concrete and mostly MIT:
Grainuum's cycle staircase and loopback test, joyboot's boot-failure counter and jump sanity
checks, Pico-PIO-USB's cycle-derived-with-quantization-slack timing discipline and its
"nothing timing-adjacent in flash, data included" rule, V-USB's two documented failure modes
(pattern-specific stuffing bugs, Windows 10's shortened post-reset window), and the LS-spec
numbers that turn into gates for `tools/wg015_vcd`. The decisions:

1. Keep PLAN Р1–Р7 (separate Thumb engine, per-target `usb_port_*.h`, no vendor submodule,
   everything clocked in RAM, keepalive servo, 4 KB DFU loader at 0x08000000, EXTI at
   priority 0). Prior art unanimously confirms Р4 (RAM) and Р5 (servo) — §4 D-2, D-5.
2. Change two PLAN defaults on prior-art evidence: D± drive strength **low, not high** (§4
   D-10) and DFU `bwPollTimeout` **12 ms, not 8** on PY32 (§2 S-3).
3. Add four cheap items to the PY32 work: a bounded preamble spin (§3 A-16), a `#error` guard
   against the assembler-`-D` build hole (§3 A-3), a SE0-width gate in `wg015vcd.py` (§6 T-2),
   and a servo lock-time budget measured against a Windows xHCI host (§7 L-14).
4. Do **not** adopt joyboot's cooperative flash wait (§4 D-3), Pico's 1 ms SE0 reset poll
   (D-6), Grainuum's runtime GPIO struct (D-1), per-word program verify (D-8), or any host
   tool other than stock `dfu-util` (D-4).
5. No third-party simulator gives Cortex-M0+ cycle truth (QEMU and Renode disclaim it in their
   own docs, Sweep 5 §1); the pre-hardware gate stays the static walker `tools/py32_cyc.py`
   (PLAN Appendix B) plus the LA loop through `tools/wg015_vcd` — §6.

## 1. Inventory

Aliveness figures are those recorded by the sweeps on 2026-09-03 (GitHub API), re-checked
today only where noted. "Verdict" is relevance to *this* port, not project quality.

| # | Project | Chip / core | Technique | LS/FS, role | Licence | Aliveness | Verdict for us |
|---|---|---|---|---|---|---|---|
| 1 | **Grainuum** (xobs) — https://github.com/xobs/grainuum | Kinetis KL02/KL17/KW01, Cortex-M0+ @48 MHz (also 47.972 MHz from a 32.768 kHz FLL ×1464) | Cycle-counted Thumb PHY (`grainuum-phy-ll.s`, `.section .ramtext` L33-35) with IRQs masked; C protocol layer; GPIO register addresses passed in a runtime `struct GrainuumUSB` | LS device, EP0 + 2 EPs | MIT (LICENSE fetched) | 197★, last push 2026-08-10; issue #1 "Running deterministic from Flash" open since 2016 | **Closest peer.** Steal S-1 (cycle staircase), S-6 (writer→reader loopback), S-9 (slew finding). Its "taken branch = 3 cycles" note (L96-99) contradicts TRM Table 3-1 (2) → bench2, §8 Q-12 |
| 2 | **joyboot** (xobs) — https://github.com/xobs/joyboot | KL02/KL17 M0+ | Grainuum-based bootloader; RAM vector table; flash primitive with `idle_func` callback | LS device | MIT (LICENSE.md fetched) | 4★, last push 2024-07-10 | Steal S-7 (boot-failure counter), S-8 (SP/PC sanity, already in PLAN §8). Do not adopt `idle_func` (D-3) |
| 3 | Chibi Chip (Chibitronics "Love to Code") | KL02 M0+ | Grainuum in a shipped consumer product (2017) | LS device | product | shipped; no public defect data (**UNVERIFIED** reliability) | Only field-deployment evidence for M0+ bit-bang LS; nothing to copy |
| 4 | **LemcUSB** — https://github.com/lemcu/LemcUSB | EFM32ZG110 M0+ @24 MHz (HFXO crystal only) | Cycle-counted Thumb in `.functioninRAM`; 16 cyc/bit | LS device | GPLv3 + emlib linking exception (LICENSE.txt fetched) | 114★, push 2026-08-24; issues #1/#2/#3 (Keil, "migrate to other Cortex-M", EFR32) open | Ideas only (GPL): RAM execution, and issue #2's point that plain Cortex-M0 lacks the single-cycle I/O port. RC-oscillator operation never shipped (Sweep 4 §5a) → A-5 |
| 5 | **stm32f030-vusb** (ads830e) — https://github.com/ads830e/stm32f030-vusb | STM32F030F4 Cortex-M0 (3-stage, GPIO on AHB) @48 MHz from HSI+PLL | V-USB `usb_rx.s`/`usb_tx.s` translated to M0 asm, hand-tuned NOPs | LS device (HID mouse) | GPL-3.0 (LICENSE fetched) | 38★, push 2026-09-03 | Ideas only. Proves 2-cycle GPIO is enough at 32 cyc/bit; "works perfectly as a mouse" is one anecdote → A-18 |
| 6 | **py32f030-bitbang-usb** (TheYkk) — https://github.com/TheYkk/py32f030-bitbang-usb | PY32F030x6/x8 M0+ | **Verbatim Grainuum** (`User/grainuum-phy-ll.s` 821 lines, `grainuum-phy.c`, `grainuum-state.c`; `main.c` L1-14 "This uses the Grainuum software USB stack") + `grainuum_py32f030.h` config; PA0=D−, PA1=D+ (68 Ω series), PA2 pull-up | LS device, CDC-ACM | none stated ("as-is", README) | 0★, created and last pushed 2026-01-30 | **Non-functional as published**: `GPIOA_BASE_ADDR 0x48000000` (grainuum_py32f030.h L18) is the STM32 base, PY32 GPIOA is 0x50000000 (RM002B p15-18, PLAN §3.4); clock code enables PLL from the 8 MHz HSI that `SystemInit` sets (system_py32f0xx.c L138-141) with the comment "HSI = 8MHz, multiply by 6 = 48MHz" (main.c L199-204) — the F030 PLL is ×2 with a 12–24 MHz input floor (DS030 Table 5-17) → 16 MHz or no lock. Sweeps 1/3 called it "plain C": wrong, it is Grainuum. Zero technique to take; its README is an AVOID list (A-8, A-9, A-15) |
| 7 | PY32F002B_USB_LOCK (Piwiwiwiw) | PY32F002B | unknown (MDK skeleton) | ? | ? | README 404 on main/master today | **UNVERIFIED**, ignore |
| 8 | IOsetting/py32f0-template issue #12 "Bit-Banged usb?" | — | request citing Grainuum, stm32f030-vusb, LemcUSB, rv003usb | — | — | open, unanswered since 2023-06-06 | Evidence that no mainstream PY32 soft-USB exists (Sweep 1 §7) |
| 9 | PY32F07x/F040 hardware USB (decaday/musb, OpenPuya/PY32F07x_USB_Test) | PY32F072/F040 (musb IP) | hardware peripheral + CTC auto-trim on USBD_SOF | FS device | MIT / vendor | active 2026-02 | Out of scope; CTC is the hardware analogue of our keepalive servo (§5 F-6) |
| 10 | **rv003usb** (cnlohr) + this fork | CH32V003 RV32EC @48 MHz; WG015 port in progress | our base | LS device | MIT | active | Base. Internal branch `rx-tx-branchless-ch32v003-rebased` (in-packet resync, ACK-first CRC pipeline) is the fallback for R8/turnaround (branch_notes.md Part B) |
| 11 | **Pico-PIO-USB** (sekigon-gonnoc) — https://github.com/sekigon-gonnoc/Pico-PIO-USB @5a37a66 | RP2040 (PIO does the PHY) | PIO NRZI/edge-resync PHY; C protocol; DMA TX | LS+FS, host+device | MIT (LICENSE fetched) | active (2026-07-22) | PIO side non-transferable (Sweep 2 §10); software side yields S-3, S-4, S-5, S-6 and the checklist numbers in §7 |
| 12 | V-USB / micronucleus | AVR ATtiny | per-clock hand-written `usbdrvasm*.inc`; host-paced flash sleep published in a device reply | LS device | GPLv2 / commercial (**UNVERIFIED** file) | mature | Lessons only: A-1, A-6, A-7; its write-sleep is our `bwPollTimeout` (S-3) |
| 13 | Mecrimus-B / bbusb / boot430 | MSP430 @15 MHz (10 cyc/bit), 32.768 kHz-trimmed DCO variant | asm; flash deferred to a detected USB-idle window; ISR vector multiplexed by a clock-register fingerprint | LS device | **UNVERIFIED** | dormant | We already defer flash to a quiet window (dfu.c:219-240); vector trick n/a (VTOR) |
| 14 | 16FUSB | PIC16F628 @24 MHz (overclocked) | asm; Timer0 armed for bit boundaries | LS device | **UNVERIFIED** | dormant | Timer-assisted bit sync not needed with a TRM cost model; nothing to take |
| 15 | espusb (cnlohr) | ESP8266 Xtensa | C with a hand-measured per-instruction cycle table; generated NRZI/stuff table | LS device | see repo LICENSE (fetched, header not parsed) | dormant | The "measure the core's real cycle costs before trusting the ledger" idea = `py32_bench` (PLAN T6) |
| 16 | uf2-samdx1 (Adafruit/Microsoft) | SAMD21 M0+ (hardware USB) | double-tap magic in the last SRAM word, POR-qualified | FS | MIT (LICENSE fetched) | maintained | Already adopted in `dfu_015.h:44-76`; PY32 `.noinit` variant in PLAN §8 |
| 17 | samdx1-usb-dfu-bootloader (the model of `dfu.c`, README:5) | SAMD11 (hardware USB) | DFU 1.1 over EP0, GETSTATUS "busy, poll me" trick | FS | **UNVERIFIED** | — | Already the design of `dfu.c:118-132` |
| 18 | Excluded: femto-usb, USBug, kevinmehall/usb (hardware USB peripherals); MSP430 hackaday 2012; InputUnreal/USBPD_BitBang (USB-PD, not D±); JNNGL/esp32-bitbang-usb (Xtensa PoC) | — | — | — | — | — | Sweep 1 Tier 3; no content for this port |

## 2. STEAL — techniques to adopt, and where they land

| # | Technique | Source | Lands in | Decision / cost |
|---|---|---|---|---|
| S-1 | **Cycle staircase**: consecutive `nop`s with one label per entry, ending in `mov pc, lr`; `bl usb_phy__wait_N_cycles` gives an exact N-cycle delay for any N ≥ 5 with no scratch register and one 4-byte call site | Grainuum `grainuum-phy-ll.s` L433-461 (`usb_phy__wait_32_cycles: nop` … `usb_phy__wait_5_cycles: mov pc, lr`); MIT, copying is fine | `rv003usb/rv003usb-arm.S` pad sites of PLAN T2 step 4 (`USB_TX_*_PAD`, `USB_RX_ENTRY_DELAY`, `USB_RX_SYNC_DELAY` from `rv003usb/py32/usb_port_py32_tune.h`), placed in `.timecrit` next to the engine; `tools/py32_cyc.py` models `bl`=3, `nop`=1, `mov pc,lr`=2 (TRM Table 3-1) | **Adopt.** The branch's `DELAY_CYCLES(c)` (arm.S:58: `mov; sub; bne .-1`) only reaches multiples of 3 and burns `SCRATCH`; inline `nop`/`b .+2` padding costs RAM at every site (≈8 TX sites × ≈12 cycles × 2 B ≈ 200 B on a 3 KB part). Staircase: 60 B once + 4 B per site. Constraint: `bl` range is ±16 MB, so the staircase may live anywhere in RAM; keep it in `.timecrit` so it is never fetched from 1-WS flash |
| S-2 | **CRC verdict ready at EOP** (incremental CRC during reception so the ACK/ignore decision costs no latency after EOP) | Pico-PIO-USB `pio_usb.c` L186-260 (`crc_prev2` pipeline) | Already ours: in-slot CRC `S:331 data_crc`, `S:350-471`, arm.S:167-184 (Domkeykong trick); the residual check `S:564-567` (`0xb001`) | **Keep**, and make the *response* latency the gate: `wg015vcd.py tx --gate-turnaround 7.5` (vcd:879-884) on every PY32 capture. If PY32's flash-resident `usb_pid_handle_data` (c:291) blows 7.5 bit-times (PLAN R8), the portable fix is the internal ACK-first idea (branch_notes.md Part B, commit 3735518), not more asm |
| S-3 | **Cycle-derived protocol waits with explicit slack for timer quantization** ("we're starting the timing somewhere in the current microsecond so always assume the first one is less than a full microsecond") | Pico-PIO-USB `pio_usb.c` L141-157 (timeouts), L196-253 (`busy_wait_at_least_cycles`, "essential … specially when we overclocked the mcu") | `bootloader_dfu/dfu_py32.h` (PLAN §8): `dfu_port_cycles()` is a 1 ms SysTick-IRQ tick, so `dfu.c:223`'s "3 ms" quiet wait is really 2.0–3.0 ms and the flash op (page erase 5.0 ms max + program 1.5 ms max, DS002B p39 Table 5-15; 4.5 + 1.5 on F030, DS030 p64 Table 5-18) ends up to ≈9.5 ms after arming — later than PLAN §8's `DFU_POLL_ERASE_MS = DFU_POLL_PROG_MS = 8`, so the host's next GETSTATUS can land inside the IRQ-masked window (`dfu.c:231-233`) and be lost | **Adopt: set both poll timeouts to 12 ms on PY32** (3 + 6.5 + 1 quantization, rounded up). Throughput cost is nil (24 KB / 128 B = 192 blocks × ≈15 ms ≈ 3 s). Same discipline for every engine pad: cycles from `usb_port_py32_tune.h`, never µs |
| S-4 | **Nothing timing-adjacent in flash — data included** (CRC tables and hand-rolled `busy_wait_1_us` pinned to RAM "so it lives in RAM") | Pico-PIO-USB `usb_crc.c` L4/L18, `pio_usb_host.c` L214-220 (`__not_in_flash`); LemcUSB `.functioninRAM`; joyboot `.ramtext` + RAM vector table (`reset_handler.c` L14-45); Grainuum `.ramtext` | PLAN Р4 already: `.timecrit` for ISR+TX+dispatch+literals+`always0`, `.rodata.usbdesc` → RAM, `dfu_status`/`dfu_upload_buf` in RAM (dfu.c:58-67, 166-181). Missing piece: the **DFU reply pointers handed to `e->opaque`** (dfu_rv003usb.h:47-49) must never point into flash — today they point at `dfu_status`/`dfu_upload_buf` (RAM) — add a link-time check (`nm`: no `.rodata` symbol referenced from `usb_config.h` descriptors without `USBDESC`, PLAN T4) | **Keep + gate.** The prior-art unanimity is the strongest single confirmation of Р4 (§4 D-2) |
| S-5 | **Internal decode state mirrored to a spare GPIO for scope/LA correlation** (debug side-set variants) | Pico-PIO-USB `usb_rx.pio` L54-154, `pio_usb_configuration.h` L11-20 | Already PLAN §7.3 (`USB_DBG_MARK_SET/CLR`, r10 mask, BSRR-write-0 no-op) and `tools/wg015_vcd` (`--marker-edge rise` request, PLAN T6) | **Keep.** Nothing to add |
| S-6 | **Writer→reader loopback without a host** ("Hook the writer to the reader for testing" — two boards D± to D±, PRBS/corner-case payloads: bit-stuffing, word boundaries; Pico `test_ll.c` does the same on two root ports and checks byte identity) | Grainuum 33C3 slides (Sweep 4 §5b); Pico-PIO-USB `examples/test_ll/test_ll.c` L153-203 | New `py32_bench/bench7_loopback.c` (PLAN T6 owns `py32_bench/*`): board A runs `usb_send_data()` (S:830 / arm.S:345) with a fake token every 1 ms over a vector set {0x00, 0xFF, 0x7E/0xFE runs, six-ones-before-EOP CRC tails, random}; board B runs the RX ISR + `usb_pid_handle_data` and counts CRC pass/fail on UART; LA on the wire feeds `wg015vcd.py decode/rx/tx` | **Adopt (optional, after T2).** It is the only cheap way to exercise §7 L-6/L-7 corner cases deterministically; V-USB's bit-6 unstuff bug survived years because only real hosts were used (A-7) |
| S-7 | **Boot-failure counter**: RAM word incremented on every non-POR reset before jumping to the app, cleared by the app once it is alive; `> 3` → stay in the loader | joyboot `bootloader.c` L64-69, L86-90 | `bootloader_dfu/dfu_py32.h::dfu_port_flag_read_and_clear()` next to the double-tap code (mirror of `dfu_015.h:53-76`): `.noinit` word `dfu_bootcount`, zeroed when `RCC_CSR` reports POR (`PWRRSTF`, RM002B p56-57), `++` otherwise, `> 3` → return `DFU_FLAG_STAY`; the app clears it through the reboot-seam header (`rv003usb/py32/usb_port_py32.h`, PLAN T3). Core `dfu.c` untouched | **Adopt behind `DFU_ENABLE_BOOTCOUNT`** (default 1 on F030, 0 on 002B until the 4 KB budget is known — PLAN R9). ≈24 B. Covers the case the CRC gate cannot: a CRC-valid app that crashes before it can request DFU |
| S-8 | **Vector-table sanity before jumping** (SP inside SRAM, reset PC inside app flash with the Thumb bit, else stay) | joyboot `bootloader.c` L92-100; uf2-samdx1 `main.c` L108-153 | PLAN §8 `dfu_port_jump_app()` already specifies it | **Keep** (already planned) |
| S-9 | **Measured slew finding**: high GPIO slew caused overshoot/failures on longer traces; fixed by the slow slew-rate setting | Grainuum 33C3 slides (Sweep 4 §5b) | `rv003usb/py32/usb_port_py32.h::usb_port_hw_setup()` OSPEEDR for D± (PLAN T3) | **Adopt — this flips PLAN R12's "OSPEEDR high"** (§4 D-10) |
| S-10 | **Publish the flash dead time to the host and let the host pace** (micronucleus `MICRONUCLEUS_WRITE_SLEEP` in the device-info reply; boot430's idle-window deferral) | micronucleus `firmware/main.c` L180-216; boot430 `boot430.c` L160-192, L404-436 | Already ours: `bwPollTimeout` in `dfu_status[1]` (dfu.c:124-125) + quiet-window deferral (dfu.c:219-240) | **Keep**; only the numbers change (S-3) |
| S-11 | **Assembler-flag guard**: the branch's TX `#if PY32F002Bx5` variant was never assembled because `rules.mk` passed `-D` only to C | PLAN §2.6 (build log); branch_notes.md anti-pattern 2 | `rv003usb/rv003usb-arm.S` first lines: `#ifndef RV003USB_PY32` → `#error "assembled without the target defines"`; `rv003usb/py32/Makefile.py32` (PLAN T1) passes the same `-D` set to `.S` and `.c` rules; T1 acceptance greps the build log | **Adopt** (2 lines) |
| S-12 | **Fast-lock, then gentle: servo driven by a periodic bus reference, but immune to the post-reset gap** | V-USB `osccalASM.s` (calibrates once after reset; broke when Windows 10 shortened the reset→first-request window, forum thread t=9959, Sweep 6 §7); rv003usb keepalive servo `S:740-797`; PY32F07x CTC on `USBD_SOF` (§5 F-6) | PLAN T2 step 5 (`USB_TRIM_ACTUATE` → `RCC_ICSCR.HSI_TRIM`): add a two-rate law — larger step for the first N keepalives after a reset/keepalive gap, then the V003-style decimated integrator (`S:788-796`, `srai … 9` at `S:790`) — and record the lock budget in `usb_port_py32_tune.h` | **Adopt.** The device gets keepalives only from port-enable onward; the host may send GET_DESCRIPTOR as soon as 10 ms after reset (USB 2.0 §7.1.7.3 recovery, SPRAAT5A ST8). Starting from the factory word (±0.7 % @25 °C) the loop must land inside ≈0.25 % within ≤8 keepalives. Measure N on Windows/xHCI (§7 L-14, §8 Q-4) |

## 3. AVOID — mistakes visible in prior art

| # | Mistake | Evidence | Our guard |
|---|---|---|---|
| A-1 | Cycle tuning by ear with per-part `#if … nop` variants and hand re-annotation of every slot | Branch arm.S:402-533 (`#if PY32F002Bx5` nops), arm.S:421-423 alignment `.error`; V-USB's seven `usbdrvasm*.inc` per clock (Sweep 4 §0); branch comment "// 4 cycles?" | PLAN Р1: zero `#if <part>` in the engine, pads only from `usb_port_py32_tune.h`; `tools/py32_cyc.py` equality gate 32/64 on every named path (PLAN T2 acceptance) — the same shape as `grahambates/68kcounter` (Sweep 5 §5) |
| A-2 | Timing code fetched from wait-state flash, "reaching 32 cycles only because of the wait states" | Branch TX engine in `.text` (PLAN §2.1, §2.5); Grainuum issue #1; Grainuum L49-51 "jumps of more than 48 bytes can cause random amounts of jitter" (Kinetis flash speculation); LemcUSB RAM requirement | PLAN Р4 + T2 acceptance (`nm` shows every engine symbol in SRAM) |
| A-3 | Build system silently drops preprocessor defines for assembly → a variant nobody ever ran | PLAN §2.6 (`rules.mk:49` vs `:53`; object built without `-DPY32F002Bx5`) | S-11 |
| A-4 | Missing bounds in the bit engine: endpoint off-by-one (`bhi` vs `bhs`), no packet-length bound, stuffed slot consumed blind | PLAN F1/F2/F3 (arm.S:276-277, :145, :200-209); V-USB Readme admits spec checks are skipped on AVR (Sweep 6 §3); Pico removes stuff bits "without error check" (`usb_rx.pio` L99-123) | PLAN T2 step 3 (fixes F1–F3); keep the RISC-V check `S:461` semantics |
| A-5 | Open-loop RC oscillator | Branch keepalive stub arm.S:217-220 (F4); LemcUSB never shipped RC operation; TheYkk README "requires precise 48 MHz" with no trim at all; V-USB needs ±1 % and says so | PLAN Р5/§2.4.5: servo mandatory on every HSI build; HSE builds (F030 + 24 MHz crystal) make `USB_TRIM_ACTUATE` empty |
| A-6 | One-shot calibration that depends on host timing that later changed (Windows 10 shortened reset→first request; devices enumerate on Win7/Linux, fail on Win10/USB3 ports) | obdev forum thread t=9959 (Sweep 6 §7) | S-12 (continuous servo with a measured lock budget); never put anything but the trim word load in the reset→first-packet gap |
| A-7 | Timing bugs that only appear on specific data patterns (V-USB 16 MHz unstuff routine 1 cycle long at bit 6 → desync on repeated 0xFE/0x7E; OpenTitan accepts six ones before EOP silently) | obdev forum thread p=10489; lowRISC/opentitan #24129 (Sweep 6 §3) | Walker equality on *every* path incl. stuffed (64) and end-of-byte; S-6 loopback vectors; §7 L-6/L-7 |
| A-8 | "Add 22 pF on D+/D−" and high-slew outputs as a fix for instability | TheYkk README Troubleshooting; Grainuum overshoot finding | D-10: drive strength low + 33 Ω series, edges scoped against USB 2.0 Table 7-9 (75–300 ns) — capacitance is not a timing fix |
| A-9 | Borrowed VID/PID (ST 0483/5740) | TheYkk README; Sweep 1 §6 | Keep pid.codes 1209 and the `bcdDevice` gating convention (`bootloader_dfu/wg015/usb_config.h:50-54`) |
| A-10 | Asserting the D− pull-up the instant the loader boots, colliding with a charger-detect IC sharing D± (BQ25611D needs ≈2 s) → Windows never enumerates | rv003usb issue #137 (Sweep 6 §6; resolution **UNVERIFIED**) | `USB_DPU_DELAY_MS` knob in `usb_port_hw_setup()` (default 0; documented in `rv003usb/py32/README.md`) |
| A-11 | Touching option bytes / RDP from firmware — RDP Level 1 on a PY32F003 could not be undone with J-Link (open issue) | IOsetting/py32f0-template #36 (Sweep 3 §d) | Loader never writes `FLASH_OPTR`/RDP; the 002B "Load Flash" option-byte flow stays OQ6 (PLAN) |
| A-12 | Assuming the debug probe just works: pyOCD on PY32F002x5 fails on most DAPLink clones, ST-LINK v2 and J-Link ("Unexpected ACK", "Get IDCODE error"), only one clone worked | pyOCD #1523, open (Sweep 3 §b) | Bring-up rig = J-Link with Puya DFP or a known-good DAPLink; F030 keeps the ROM UART loader (`puyaisp`) as recovery; **002B has no ROM loader** (nBOOT0/nBOOT1 → main flash / SRAM / Load Flash, RM002B p20-21) → SWD is its only recovery, so the DFU address guard (dfu.c:143-149) is load-bearing |
| A-13 | 50 MB vendor submodule for ≈300 lines, plus an object-path hack to bypass its rules | Branch `Makefile.py32:42`, `:110-114` (PLAN §2.6, Р3) | PLAN Р3 (`rv003usb/py32/py32_min.h`, own startup/ld) |
| A-14 | EOP SE0 shorter than the transmitter spec (V003 ≈48 cycles = 1.0 µs, below the 1.25 µs minimum; works only because receivers must accept ≥670 ns) | `S:1084-1119` (V003 SE0 hold `nx6p3delay(7)` at `S:1109` = 45 cycles + stores ≈ 48); PLAN Appendix A "SE0 width 37 → 64"; USB 2.0 §7.1.13.2 via SPRAAT5A LS11/LS12 (Sweep 6 §2); K1921 errata Rev.4 item 3 (EOP misread from pull-up impedance) shows hosts do misjudge EOPs | PY32 target 64 cycles (`USB_TX_SE0_PAD`); new gate §6 T-2 |
| A-15 | LS CDC / LS Ethernet class choices | rv003usb README ("Windows apparently doesn't like low speed CDC", Linux blacklists LS Ethernet); TheYkk CDC-ACM; USB 2.0 §5.6.4/§8.6.5 (no bulk on LS) | PY32 targets are HID + DFU only (both control/interrupt) |
| A-16 | Unbounded wait for a bus transition inside the ISR: resume signalling is K for ≥20 ms (§7.1.7.5), a stuck-K line is forever | `S:182-205 preamble_loop` and arm.S:70-74 spin until a change or SE0 with IRQs masked; no counter | Bounded spin in the Thumb engine: give up after ≈16 bit-times (512 cycles) → `done_usb_message`. Costs one low register in the poll loop (r4 `SCRATCH` is free there, PLAN §2.3) and +2 cycles per poll (granularity 5 → 7, absorbed by `USB_RX_SYNC_DELAY`). Request to PLAN T2 |
| A-17 | Moving code between flash and RAM (or between parts) without re-deriving cycles | `rv003usb/notes_on_porting_to_v00x.md` (V00x flash 1.5–2× slower); `origin/runfromram` "RAM is a deadend that ends in madness" (branch_notes.md); branch alignment assert | Any placement change re-runs `tools/py32_cyc.py` and the LA loop; `.timecrit` is the only home for engine code |
| A-18 | Validation = "it enumerated on my PC" | stm32f030-vusb README; TheYkk README; micronucleus `t85_aggressive` "worked reliably in all tests, but is possibly less stable" (Sweep 4 §0); Pico-PIO-USB has no timing test beyond loopback (Sweep 2 §8) | PLAN T10 matrix + §7 L-14/L-19: ≥100 replugs, 1 h soak, temperature sweep, three host stacks, direct/USB2-hub(TT)/USB3-hub |
| A-19 | Wrong peripheral base / wrong clock in a "48 MHz" project that nobody ever ran | TheYkk `grainuum_py32f030.h` L18 (`0x48000000`), `main.c` L199-204, `system_py32f0xx.c` L138-141 | `py32_min.h` `_Static_assert`s with RM page citations (PLAN T1); `startup_py32.S` clock bring-up verified by bench6/MCO before any USB work (PLAN T10) |

## 4. DIVERGE — where we deliberately differ, and where prior art says we should reconsider

| # | Prior art | We do | Verdict and reason |
|---|---|---|---|
| D-1 | Grainuum: one asm PHY + runtime `struct GrainuumUSB` of GPIO register addresses per pin (`usbdnIAddr/SAddr/CAddr/DAddr/Shift/Mask`), so integrators never touch asm | Compile-time per-site macro contract (`usb_port_py32_asm.h`, PLAN §7.1) in a per-ISA engine file | **Right.** Every address in the runtime struct is a 2-cycle AHB load inside 32-cycle slots (TRM Table 3-1 footnote b), plus Thumb-1 register pressure (PLAN §2.3). Compile-time literals cost 1 cycle from the IOPORT-based register. Grainuum's portability goal is met differently: a new M0+ chip = a new `usb_port_<chip>_asm.h` (PLAN Р1) |
| D-2 | WG015 track directive "flash first" (STATE.md rules); LemcUSB/Grainuum/joyboot/Pico: RAM | PY32: everything clocked in RAM (PLAN Р4) | **Right for PY32, despite the WG015 directive.** PY32 flash is a flat LATENCY=1 (RM030 §4.2.2, RM002B p38 — no prefetch buffer or cache anywhere in the RM, Sweep 5 §3), but the core fetches 32 bits ahead (TRM §2.2.1) over 16-bit Thumb instructions, so the cost of a branch target depends on its half-word alignment — exactly the artefact the branch's `.ifeq … .error` guards (arm.S:421-423). SRAM is 0-WS and alignment-free per TRM. The WG015 chip is different (cache + prefetch buffer); the directive does not transfer |
| D-3 | joyboot: flash primitive in `.ramtext` calls an `idle_func()` while polling `CCIF`, IRQs masked only around the command kick (`flash.c` L33-74) | `dfu.c:231-233` masks IRQs for the whole page op; the host paces via `bwPollTimeout` | **Right.** RM002B p23-24: any flash read while program/erase is busy stalls the AHB — every C handler the ISR calls (`usb_pid_handle_data`, `dfu_class_request`) lives in flash, so servicing USB during the op is impossible unless the whole loader is in RAM (not on a 3 KB part). Cost: ≤6.5 ms of missed keepalives — the servo's ±4000-count sanity window (`S:762-772`, mirrored in PLAN T2 step 5) rejects the doubled delta, so no false trim step |
| D-4 | Every bootloader in the lineage ships a custom host tool (micronucleus CLI, joyboot "still being written", boot430 tool, our `wg015hostcli`) | Stock `dfu-util`, DFU 1.1 over EP0 only (`bootloader_dfu/README.md`) | **Right.** Zero host software; DFU is LS-legal (control transfers only). Cost: 8-byte EP0 packets → 16 OUT packets + status per 128 B block, ≈3 s per 24 KB — acceptable. Interop with real `dfu-util` at 8-byte EP0 is still unproven on hardware (STATE.md, Q-10) |
| D-5 | V-USB: one-shot OSCCAL after reset; LemcUSB: crystal only; PY32F07x: hardware CTC on SOF | Continuous keepalive servo on `RCC_ICSCR.HSI_TRIM` (13-bit) with telemetry, actuator as a port macro (PLAN Р5) | **Right**, with S-12's lock-time budget added. It is the software CTC the F030/002B lack |
| D-6 | Pico device: busy-poll SE0 for 1000 consecutive µs to declare bus reset (`pio_usb_device.c` L409-439) | No reset detector; `my_address` is never cleared; tokens to address 0 are always accepted (`S:519-533`) and `SET_ADDRESS` overwrites (c:476-479) | **Right.** Re-enumeration after any reset works through the address-0 path (this is also how the Windows/old-Linux "read 8 bytes, reset, read again" scheme is survived). A keepalive-gap detector would misfire on suspend (no keepalives for seconds, then resume without re-enumeration). Residual risk (stale address answered after a warm reset while the host reassigns it) is an exotic host bug, not worth the ISR cycles |
| D-7 | Pico: stuff bits removed "without error check" (CRC is the only gate) | Stuffed slot validated (`S:461` RISC-V; PLAN T2 step 3 fixes arm F3) | **Right.** 4 cycles inside existing padding; rejects the OpenTitan class of malformed packets before CRC |
| D-8 | joyboot: per-longword `PROGCHECK` margin verify and refusal to program over non-0xFF (`flash.c` L128-188) | Manifest-time CRC32 over the whole image (dfu.c:98-106, 242-251) + `DFU_UPLOAD` readback | **Right.** Same coverage, no per-page cost, no core API change |
| D-9 | Sample point: the branch samples at cell offset ≈8–12/32 (PLAN F5) | PLAN targets 14–18/32 | **Reconsider is already in PLAN — the physical reason is stronger than "centred"**: a receiver must accept a last bit lengthened by up to 260 ns (dribble, USB 2.0 §7.1.9/§7.1.14 via SPRAAT5A LS14) = 12.5 cycles; a sample earlier than that in the slot after the last data bit reads the dribble as a spurious 1 → non-byte-aligned frame → aborted (`S:475-477`). Offset ≥ 14 is a requirement, not a preference. Gate with `wg015vcd.py rx` offset histogram |
| D-10 | Grainuum: "slow slew rate below 15 MHz" fixed overshoot; V003 drives D± at the slowest CFGLR speed (2 MHz, `S:857-861`) | PLAN R12: "OSPEEDR high, 22-33 Ω series" | **Prior art says we are wrong → set OSPEEDR to the lowest setting that meets the LS edge spec** (USB 2.0 §7.1.2.1 Table 7-9: tLR/tLF 75–300 ns into 200–450 pF — not from the sweeps, added here), keep 33 Ω series (README recommends 33/47 Ω), verify with a scope in T10. High slew on an unterminated 1–2 m cable is ringing plus EMI, and a fast edge buys nothing at 32 cycles per bit |
| D-11 | joyboot copies the vector table to RAM at every boot (`reset_handler.c` L14-45); the vendor `SystemInit` also relocates vectors to SRAM by default (TheYkk `system_py32f0xx.c` L143-149) | PLAN Р7: vector table stays in flash (+1–2 deterministic cycles inside the +55 window); RAM table optional (192 B) | **Right on a 3 KB part.** The 1-WS vector fetch is deterministic (flat LATENCY); bench3 confirms (Q-6). Switch only if the measured entry spread eats the window |
| D-12 | Nobody in the LS bit-bang lineage implements suspend (V-USB, Grainuum, rv003usb all run flat out) | Same | **Accept, document.** Bus-powered suspend current (≤500 µA, USB 2.0 §7.2.3) is not met; hosts do not enforce it. But resume signalling *does* reach us (A-16) — the bounded spin is required, suspend support is not |
| D-13 | Pico device ISR: recognise token, arm DMA, defer everything else to `pio_usb_device_task()` (`pio_usb_device.c` L124-192) | The ISR runs the whole protocol including C handlers (`S:519-589` dispatch → c:291 `usb_pid_handle_data` → `usb_send_empty`) | **Right, by necessity.** There is no hardware to hold a response: the ACK/DATA must be on the wire 2–6.5 bit-times after EOP (§7.1.18), so the decision and the transmit must happen inside the ISR. Consequence: `dfu_class_request()` (dfu.c:113-197) executes inside the turnaround budget on a SETUP's status path — gated by S-2 |
| D-14 | Grainuum/Pico/V-USB: SYNC generated by the same encoder as data | rv003usb: separate `pre_and_tok_send_inner_loop` (`S:904`) with no stuff counter (SYNC+PID+token can never run six ones) | **Right.** Fewer cycles per preamble bit, one fewer register in the hot loop; the branchless branch dropped the vestigial counter for the same reason |

## 5. PY32 hard facts for the port

### 5.1 Which parts reach exactly 48 MHz (32 cycles per 1.5 Mbit/s bit)

| Part | Max f (datasheet) | 48 MHz path | HSI accuracy relevant to the cell margin | Verdict |
|---|---|---|---|---|
| **PY32F030x6/x8** | **48 MHz** (DS030 V1.8 p2/p5) | HSI 24 MHz × PLL2 (crystal-less) or HSE 4–32 MHz × PLL2 with a 24 MHz crystal; PLL input 12–24 MHz, output ≤48 MHz, period jitter ≤0.3 ns, lock 15/40 µs typ/max (DS030 p64 Table 5-17) | 24 MHz row 23.83–24.17 MHz @25 °C/3.3 V (≈±0.7 %), ±2 % 0–85 °C, −4/+2 % −40…85 °C, trim step 0.1 % (DS030 p63 Table 5-15) | **Target #1.** HSI build: servo mandatory (margin ≈0.25 % early sample / 0.5 % centred, PLAN §2.4.5). HSE build: servo off |
| **PY32F002B ("B-C" silicon)** | DS002B V1.0: **24 MHz**; RMBC p14: **fmax 48 MHz** | `HSI_FS = 101` (48 MHz), factory trim word at `0x1FFF0104`, 48 MHz flash-timing set at `0x1FFF0130…0x140`; no PLL, no HSE (PLAN §3.1, §3.3) | No 48 MHz accuracy row in any datasheet; 24 MHz row as F030 | **Target #2**, HSI-only, servo mandatory; 48 MHz support is "documented in RM B-C, unspecified in DS" → Q-1 / PLAN R1 (require B-C silicon: `DBG_IDCODE` reset value per RMBC p265/269) |
| PY32F002A | 24 MHz, no PLL (DS002A p2) | none | — | Excluded. The EEVblog "F002A is really an F030 die" claim is **UNVERIFIED** per unit and unwarranted by Puya (Sweep 3 §c) |
| PY32F003 | 32 MHz, no PLL (DS003) | none (33.3 cycles/bit) | — | Excluded |
| PY32F07x / F040 | 72 MHz, PLL ×2/×3, hardware USB + CTC | 48 MHz reachable | — | Out of scope (hardware USB) |

Corroboration from the field: Grainuum runs LS USB at 47.972352 MHz (−0.058 %) from a
32.768 kHz FLL — "close enough" (33C3 slides, Sweep 1/4) — so a fixed offset well under the
cell margin is harmless; what kills a device is drift beyond ≈0.25 % without a servo (A-5).
No public evidence of a working 48 MHz bit-bang on any PY32 exists: the only attempt never
configured 48 MHz (§1 row 6).

### 5.2 Core and timing (all verified in PLAN §3.2 / Sweep 5 §2 from the TRM)

| Fact | Source |
|---|---|
| ALU/move 1; `B<cc>` 2 taken / 1 not; `B` 2; `BL` 3; `BX/BLX` 2; `MOV PC` 2; `LDR/STR` 2 on AHB, **1 on the single-cycle I/O port**; `PUSH` 1+N; `POP{…,PC}` 3+N; `MULS` 1 on PY32 (DS030 p17 "single-cycle multipliers") | TRM Table 3-1 pp. 3-4…3-6 + footnotes b/e; DS030 p17 |
| GPIO A/B/C(/F) sit on the IOPORT at `0x5000_0000` ("Fast toggle capable of changing every single cycle") → `ldr rd,[rbase,#IDR]` = 1 cycle. Sweep 5's remark that "PY32 appears to put GPIO on plain AHB" is an inference from `#if` structure, contradicted by the RM memory map — bench1 settles it per port (Q-3) | RM002B p15-18, p76; RM030 p18-20, p100; DS030 p16, p54 |
| Worst-case interrupt latency 15 cycles (zero WS, highest priority, no jitter suppression); LDM/STM abandoned and restarted; late-arrival/tail-chaining | TRM §3.6.1 p3-10 |
| Fetch-ahead limited to 32 bits; no instruction cache in the core; fetch width 16/32 is a vendor option (unknown on PY32 → bench2) | TRM §2.2.1, Table 1-1 |
| NVIC: 4 priority levels (2 bits); EXTI vectors shared: `EXTI0_1`, `EXTI2_3`, `EXTI4_15` (IRQ 5/6/7) | RM002B p97; py32f002bx5.h |
| SysTick 24-bit down-counter (wraps every 349 ms at 48 MHz) → ms tick by IRQ for the loader (PLAN §8) | TRM/CMSIS; RM002B p97 |
| GPIO input synchroniser delay assumed 2 cycles — **UNVERIFIED** (Q-6) | PLAN OQ5 |

### 5.3 Flash geometry, sequences, timing

| Item | PY32F002B(-C) | PY32F030 | Source |
|---|---|---|---|
| Flash / SRAM | 24 KB @0x08000000 (aliased at 0) / 3 KB | 16–64 KB / 2–8 KB | RM002B p18 Table 3-1; py32f030x8.h |
| Page / sector | 128 B / 4 KB | 128 B / 4 KB | RM002B p22 §4.1 |
| Programming granularity | **whole page only**, 32 words, word writes only (HardFault on half-word/byte): unlock `KEYR` → `PG`+`EOPIE` → words 1–31 → `PGSTRT` → word 32 → poll `BSY` → `EOP` | same | RM002B p24-25; HAL `FLASH_Program_Page` |
| Times | tprog 1.0/1.5 ms, tERASE 3.5/5.0 ms (typ/max) | 1.0/1.5, 3.0/4.5 ms | DS002B p39 Table 5-15; DS030 p64 Table 5-18 |
| Wait states | `FLASH_ACR.LATENCY` 0 (≤24 MHz) / 1 (48 MHz: "two system clock cycles are required for each Flash read"); **no prefetch buffer, no cache documented** (517-page RM030 grep, Sweep 5 §3) | same | RM002B p38; RM030 §4.2.2 p26, §4.8.1 p42-43 |
| Reads during program/erase | "any attempt to read the Flash memory will stall the bus" → XIP programming is legal, the core just stalls; writing `FLASH_CR` while `BSY` stalls too | same | RM002B p23-24; RM030 p27-28 |
| Timing registers | `TS0…PRETPE` (`FLASH_R_BASE+0x100…0x120`) must hold the factory set for the running HSI frequency: 24 MHz set at `0x1FFF011C…`, **48 MHz set at `0x1FFF0130…0x140` (B-C only)** | 24 MHz set at `0x1FFF0F6C…` | RM002B p33-35, p44-46; RMBC p24 Table 4-2, p30; `__HAL_FLASH_TIMMING_SEQUENCE_CONFIG` |
| Boot modes | `nBOOT1/nBOOT0` option bytes: main flash / SRAM / **Load Flash** (1–4 KB hardware-protected zone at the top of flash); **no ROM UART loader** | BOOT0 pin + `nBOOT1`: main flash / **system memory 3.5 KB ROM UART loader** (`puyaisp`) / SRAM | RM002B p20-21 §3.6, p42; RM030 p21, p24-25; Sweep 3 §b |
| Reset cause | `RCC_CSR` @+0x60: `SFTRSTF` 28, `PWRRSTF` 27, `PINRSTF` 26, `RMVF` 23; software reset = `SCB->AIRCR = 0x05FA0004` | same | RM002B p56-57, p73 |
| SRAM retention across `SYSRESETREQ` | RM only lists registers as reset; SRAM not mentioned → **UNVERIFIED** (Q-5) | | RM002B p56 |
| Endurance | 100 K cycles | | DS002B p39 Table 5-16 |

Consequences already drawn in PLAN §8 and confirmed here: one DFU block = one 128 B page
(`DFU_XFER_SIZE 128`, erase+program every block), `dfu_port_flash_write_block()` may run XIP
(the V003 port `dfu_v003.h:84-112` is the model, not the WG015 RAM routine `dfu_015.h:120-145`),
flash timing registers must be loaded from the factory set matching `ICSCR.HSI_FS` before the
first program/erase (PLAN R6).

### 5.4 Toolchain and programming facts that bite (Sweep 3)

| Fact | Consequence |
|---|---|
| No PY32 support in mainline OpenOCD; only forks (`OlegGalizin/openocd-for-py32f0xx`, `l0ud/openocd-puya`, both experimental) | Do not make CI or docs depend on OpenOCD; flash via pyOCD + Puya DFP or J-Link |
| pyOCD needs Puya's `PY32F0xx_DFP` imported by hand (not in the pack index); PY32F002x5 connect failures across most probes (pyOCD #1523, open) | A-12 |
| `puyaisp` (`pip install puyaisp`) drives the F030 ROM UART loader (RX on PA2/PA9/PA14, TX on PA3/PA10/PA15); some QFN F002A packages have no BOOT0 pin (reset-while-powered workaround, **UNVERIFIED**) | F030 recovery path; 002B has none |
| Community GCC template `IOsetting/py32f0-template` (GNU Arm 12.2+) is what the branch and TheYkk used; `rules.mk` passes `-D` to C only | S-11 |
| No public errata sheet for any PY32; `decaday/py32-data` claims errata content that could not be located (**UNVERIFIED**) | Every RM number in `py32_min.h` cites a page (PLAN T1); silicon revision recorded in `calibration.md` |
| CSS falls back to HSI and raises an NMI if HSE fails | HSE builds must handle NMI (or run HSI-only) — a silent fallback to untrimmed HSI drops the link |

### 5.5 Peripheral map the engine touches (PLAN §3.4, both families unless noted)

`GPIOx`: `MODER 0x00, OSPEEDR 0x08, PUPDR 0x0C, IDR 0x10, ODR 0x14, BSRR 0x18, BRR 0x28`, base
`0x50000000 + 0x400·{A0,B1,C2,F5}`; BSRR write of 0 is a no-op (RM002B p79) — the marker
trick. `EXTI 0x40021800`: `RTSR 0x00, FTSR 0x04, PR 0x0C (W1C), EXTICR[n] 0x60+4n, IMR 0x80`.
`RCC`: `CR 0x00, ICSCR 0x04 (HSI_TRIM[12:0], HSI_FS[15:13]), CFGR 0x08, PLLCFGR 0x0C (F030),
IOPENR 0x34, CSR 0x60`. 5 V tolerance is not specified anywhere → VDD 3.3 V (PLAN OQ8).

### 5.6 The hardware analogue of our servo

PY32F07x/F040 carry a CTC that auto-trims HSI against GPIO pulses, LSE or **USBD_SOF** to
get a USB-grade PLL48M (PY32F07x DS §2.19 p21, Sweep 3 §c); F030/002B have no CTC and no USB
— Puya itself never specs their 48 MHz as USB-accurate. Our keepalive servo (S-12) is that
CTC in software with the LS keep-alive (1 ms SE0) as the reference: same loop, same reason.

## 6. Tooling we can install and run offline

Checked in this container today: `arm-none-eabi-gcc 13.2.1` and `riscv64-unknown-elf-gcc`
present; `qemu-system-arm` (apt candidate 8.2.2) and `sigrok-cli` (0.7.2) installable;
PyMuPDF present (datasheet extraction); no pyocd/openocd/renode/unicorn installed.

| # | Tool | Gives | Does not give | Install | Use here |
|---|---|---|---|---|---|
| T-1 | **`tools/py32_cyc.py`** — static cycle walker over `arm-none-eabi-objdump -d`, cost table = TRM Table 3-1 + IOPORT=1 + flash LATENCY model (PLAN Appendix B seed; T2 creates it) | Exact per-path cycle sums for straight-line slot code with known branch outcomes; pass/fail exit code | Dynamic effects (IRQ entry, synchroniser, bus contention) | none (python3 + the installed binutils) | **The pre-hardware gate.** Run in CI as `make check-cycles` (PLAN T7) — precedent `grahambates/68kcounter`, Sweep 5 §5 |
| T-2 | **`tools/wg015_vcd/wg015vcd.py`** (present; selftest `tools/wg015_vcd/selftest/run_selftest.sh`, 60/60) | From an LA VCD: ISR entry latency, sample offsets/drift/excursion, TX cell periods, turnaround, EOP SE0 width (`vcd:448`, `:678-701`) with gates `--gate-entry`, `--gate-excursion`, `--gate-turnaround` (`vcd:861-893`) | A **SE0-width gate** (reported, not gated) and rise/fall times (2-level VCD) | none | Add `--gate-se0 LO:HI` (default 60:72 cycles = 1.25–1.5 µs) and `--marker-edge rise\|both` (PLAN T6). Capture ≥100 MS/s, trigger on D− falling, ≥2–3 ms (README) |
| T-3 | **sigrok-cli + libsigrokdecode** (`usb_signalling`, `usb_packet` decoders; LS supported) | Headless capture from DSLogic/fx2lafw-class analysers; J/K/SE0 symbol decode; **VCD export** (`sigrok-cli … -o cap.vcd -O vcd`) that feeds T-2 directly | Timing/jitter metrics (annotations only, Sweep 5 §4) | `apt install sigrok-cli` (0.7.2 candidate here) | Cheap LA path instead of a Saleae; the decoder is a second opinion on our packet decode |
| T-4 | **dfu-util** | Host side of `bootloader_dfu` (`-l`, `-D app.dfu`, `-U readback.bin`) | — | `apt install dfu-util` | T5/T10 interop; udev rule in `bootloader_dfu/README.md` |
| T-5 | **usbmon + Wireshark/tshark** (Linux) | Host-side enumeration traces (which request failed, retries, reset timing) | Wire timing | `modprobe usbmon; apt install tshark` | §7 L-14/L-19 diagnosis; count keepalive→first SETUP gap only with the LA (T-2) |
| T-6 | **pyOCD** + Puya `PY32F0xx_DFP` | SWD flash/debug | reliability on 002x5 (A-12) | `pip install pyocd`, then `pyocd pack -i <PY32F0xx_DFP.pack>` (pack from Puya's SDK, manual) | Bring-up rig; J-Link fallback |
| T-7 | **puyaisp** | F030 ROM UART loader | 002B (no ROM loader) | `pip install puyaisp` | Recovery on F030 |
| T-8 | **Thumbulator** (dwelch67; ELMO fork calibrated to STM32F0 at 1.55 % MAPE) | A hackable C Thumb ISS one could fit with our own M0+ cost table for a *dynamic* ledger | M0+ 2-stage pipeline, PY32 flash/IOPORT, NVIC latency — all would have to be modelled | `git clone https://github.com/dwelch67/thumbulator && make` | **Not now**: our slot code is straight-line, T-1 is sufficient; revisit only if bench2 shows costs the static table cannot express |
| T-9 | qemu-system-arm | Functional ARMv6-M execution (microbit board only) | Cycle timing ("icount … should not be confused with cycle accurate emulation", QEMU docs); no PY32 board | `apt install qemu-system-arm` | Skip |
| T-10 | Renode | Peripheral-level simulation, MIPS-quantum time | Cycle truth (renode #512 STM32G0 4× drift); no PY32 platform | `.deb` from github.com/renode/renode/releases | Skip |
| T-11 | Unicorn / libthumb2sim / armv6m-sim / usim / CMEmu | Functional ISA (Unicorn), untested cycle claims (libthumb2sim), M3-only (CMEmu) | M0+ cycle truth | `pip install unicorn`; git clones | Skip (Sweep 5 §1 table) |
| T-12 | `arm-none-eabi-gcc 13.2.1`, `objdump`, `nm`, `size`; `python3 tools/wg015mkdfu.py --selfcheck` | Build, placement checks (`nm` for `.timecrit`/`.noinit`), size gates (`bootloader_dfu/wg015/Makefile:14-22` pattern), image suffix self-test | — | installed | T1/T2/T5 acceptance |

Bottom line (Sweep 5): no vendor ISS exists for PUYA and no general simulator models M0+
cycles; the static walker plus the LA loop is not a stopgap, it is the state of the art.

## 7. LS-USB traps checklist — what the implementation and tests must cover

Numbers at 48 MHz: 1 bit = 32 cycles = 666.7 ns. Spec citations via TI SPRAAT5A (Sweep 6)
unless marked; "gate" names the mechanical check.

| # | Trap | Spec | Our implementation point | Test / gate |
|---|---|---|---|---|
| L-1 | Turnaround 2–6.5 bit-times (64–208 cycles), 7.5 with a captive cable; host times out at 16–18 | §7.1.18, §7.1.19 | TX entry → first preamble store (`S:830-903`; arm.S:362-389, PLAN Appendix A "entry → first preamble store 51") plus the ISR dispatch and C handler before it (D-13) | `wg015vcd.py tx --gate-turnaround 7.5` on SETUP/OUT/IN traffic; PLAN R8 fallback |
| L-2 | EOP SE0 width at the transmitter 1.25–1.5 µs (60–72 cycles) | §7.1.13.2 (LS11) | `USB_TX_SE0_PAD` → 64 cycles (PLAN T2 step 4); V003 ships ≈48 (A-14) | new `--gate-se0 60:72` (T-2) |
| L-3 | Receiver must accept SE0 670 ns–1.76 µs as EOP and ignore SE0 < 210 ns (10 cycles) | §7.1.13.2 (LS12), §7.1.4 (LS5) | RX sees SE0 only at its one sample per slot (`S:387`, `S:454`; arm.S bit_process) — a ≤10-cycle glitch is seen only if it straddles the sample point → packet dropped, host retries. Acceptable | Loopback vector with a deliberate 8-cycle SE0 glitch (S-6, optional) |
| L-4 | Dribble: last bit lengthened ≤260 ns (12.5 cycles) | §7.1.9/§7.1.14 (LS14) | Sample offset ≥ 14/32 (D-9) | `wg015vcd.py rx` offset histogram, target 14–18 |
| L-5 | First-bit distortion ±25 ns | §7.1.14 (LS13) | Sync catcher re-locks on the first observed edge (`S:150-163`; arm.S:70-77 poll every 5 cycles = 104 ns granularity, inside the cell) | covered by L-4's histogram |
| L-6 | Bit stuffing applies to CRC and **must insert a stuff bit even as the last bit before EOP** | §7.1.9 (B5/B6) | RISC-V: `send_inner_loop` decrements `a4` and branches to `insert_stuffed_bit` (`S:1023-1025`, `S:1133-1141`) *before* `send_end_bit_complete` tests the bit count (`S:1058-1062`) → the trailing stuff bit is emitted. ARM: same structure claimed (PLAN §2.5) — **verify on the walker path list** | Loopback/LA vector whose CRC16 tail (sent LSB-first) ends in six ones; walker path "one+stuffed at last bit" = 64 |
| L-7 | Receiver must reject a stuffing violation, including at the last bit; a host may deliberately use one to abort (OpenTitan gap) | RB6/RB8; §8.6.4 per Sweep 6 | `S:461` check; ARM F3 fix (PLAN T2 step 3) | Loopback vector with seven ones (S-6); CRC as backstop |
| L-8 | Non-byte-aligned frame must be discarded | — | `S:475-477` (`andi a0, s1, 7`); arm.S `se0_complete_flash` `sub BITCOUNT,#8; bne` | walker: EOB paths |
| L-9 | Keep-alive: an EOP (2 bit-times SE0, 64 cycles) every ≈1 ms on an idle LS bus; the ISR must sample SE0 before it ends | §7.1.7 area (exact clause **UNVERIFIED**, Sweep 6 §4) | `S:104` "MUST check SE0 immediately" (5th instruction); arm.S:41-51 (3rd instruction); entry + first sample ≤ ≈55 cycles | `wg015vcd.py rx --gate-entry 55`; `py32_bench/bench3_irq.c` (pattern: `wg015_bench/bench3_irq.c`) |
| L-10 | Keep-alive-driven trim must ignore out-of-window deltas (missed keepalives during flash ops, resets, suspend) and must not hunt | — | `S:758-776` (±4000 counts = ±8.3 %); PLAN T2 step 5 mirrors it; saturation ±64 LSB | bench6 (HSI_TRIM LSB weight, Q-2); `rx.slope_cyc_per_bit` ≤ 0.16 after lock |
| L-11 | Reset: ≥10 ms SE0 from the host; device must not treat SE0 < 2.5 µs as reset and must be ready ≤10 ms after SE0 start (SPRAAT5A prints "2.5 ms" — extraction artefact, Sweep 6 §6) | §7.1.7.3 (ST7/ST8) | Entry sample SE0 → keepalive path; no reset detector (D-6); address 0 always accepted (`S:519-533`); EP0 toggles reset by every SETUP (`S:707-727`). **Non-EP0 OUT toggles are not reset by a bus reset** (`c:305-308` drops a repeated `which_data`) → first interrupt-OUT packet after a warm re-enumeration may be ACKed and dropped — irrelevant for DFU (EP0 only) and HID-in demos; test for HID-out | T10: warm re-enumeration ×100 (`usbreset`/Windows "reset device"), HID-out demo first-packet check |
| L-12 | Suspend after 3 ms idle; ≤500 µA bus-powered (not enforced by hosts) | §7.1.7.4, §7.2.3 | Not implemented (D-12) | document only |
| L-13 | Resume: K for ≥20 ms then an LS EOP; any bus activity wakes | §7.1.7.5 | D− falls at K start → ISR enters, sees K, **spins in the preamble loop until the EOP** (`S:182-205`; arm.S:70-74) with IRQs masked — 20 ms | A-16 bounded spin; T10: Windows selective suspend → resume, device must keep working and the loader main loop must not stall |
| L-14 | Enumeration windows: hosts may issue GET_DESCRIPTOR as early as 10 ms after reset; Windows 10 shortened the gap vs Windows 7; USB3 ports worse | §7.1.7.3 recovery; obdev thread t=9959 | S-12 lock budget; factory trim word as the starting point (`0x1FFF0104` / `0x1FFF0F10`) | LA: count keepalives between reset end and first SETUP on Win10/11 xHCI (Q-4); enumerate ≥100× on each host |
| L-15 | LS descriptor rules: `bMaxPacketSize0 = 8`; only control + interrupt endpoints; LS interrupt `bInterval` ≥ 10 ms; host reads 8 bytes of the device descriptor, resets, reads again ("old scheme") | §5.6.4, §8.6.5 (P7); Linux `old_scheme_first` | `bootloader_dfu/wg015/usb_config.h:46` (8), EP0-only DFU (`ENDPOINTS 1`); `demo_hidapi`/`demo_gamepad` interrupt IN | `lsusb -v`; dmesg `device descriptor read/8` is normal for LS, `error -71` loops are not |
| L-16 | Standard requests we answer with a ZLP instead of data: GET_STATUS, GET_CONFIGURATION, GET_INTERFACE (`#if 0`, `c:480-491`) | Chapter 9 | Fine on Linux/Windows so far; USBCV Chapter 9 would fail; macOS behaviour **UNVERIFIED** (Sweep 6 §7) | T10 on macOS; if it objects, implement `GET_STATUS` (2 bytes from `always0`) — ≈20 B |
| L-17 | D− pull-up at boot vs charger-detect ICs on shared D± | rv003usb #137 | `USB_DPU_DELAY_MS` (A-10) | — |
| L-18 | Edge rates 75–300 ns into 200–450 pF; overshoot on long cables | USB 2.0 §7.1.2.1 Table 7-9 (added here); Grainuum | OSPEEDR low + 33 Ω (D-10) | Scope in T10 (PLAN R12); LA skew estimate `wg015vcd.py tx` "D+/D− edge-to-edge skew" |
| L-19 | Hub/TT and xHCI paths: LS behind a USB2 hub uses split transactions; a USB3 switcher hub broke rv003usb devices on Windows (mouse misidentified as keyboard); RPi `dwc_otg` fails LS behind hubs; Pico-PIO-USB's TinyUSB host missed an LS keyboard | rv003usb #124; raspberrypi/linux #273; Pico-PIO-USB #83 (Sweep 6 §6/§8) | Nothing device-side beyond spec compliance | T10 matrix: direct xHCI, via USB2 hub (TT), via USB3 hub, RPi; Linux + Windows + macOS |
| L-20 | Flash-op dead window: tokens and keepalives arriving while IRQs are masked are lost (≤6.5 ms) | — | `dfu.c:231-233`; `bwPollTimeout` 12 ms (S-3); servo window (L-10) | `dfu-util -D` ×100 (PLAN T10) |
| L-21 | 24-bit SysTick wrap (349 ms) under a 32-bit unsigned subtraction terminates waits early | — | PLAN §8: ms tick via SysTick IRQ at priority 3 (≤40 cycles per README rule) | T5 acceptance |
| L-22 | Shared EXTI vector (lines 2/3 or 4–15) livelocks if a user pin fires on the USB vector | RM002B p97 | F6 hook (`RV003_ADD_EXTI_MASK/HANDLER` port, PLAN T2 step 7) | build both demos with a user EXTI line |
| L-23 | No preemption of the USB ISR; other ISRs ≤ 40 cycles or at lower priority; nothing else at priority 0 | README "Care surrounding interrupts" | PLAN Р7 (`NVIC_SetPriority`, F7 fix in `usb_port_hw_setup()`) | assert in `usb_port_hw_setup()` (PLAN R5) |

## 8. Open questions the sweeps could not settle

| # | Question | Why it matters | How to close |
|---|---|---|---|
| Q-1 | Is 48 MHz HSI officially supported on PY32F002B (DS V1.0 says 24 MHz; RM B-C says fmax 48, `HSI_FS=101`, factory word at `0x1FFF0104`, no accuracy row)? | Viability of target #2 | Ask Puya / newer DS; bench6 + MCO; `DBG_IDCODE` check for B-C silicon (PLAN R1) |
| Q-2 | `HSI_TRIM` LSB weight at 48 MHz, monotonicity, range of the 13-bit field | Servo gain, saturation, lock time (S-12) | `py32_bench/bench6_trim.c` against a 1 kHz reference / LA |
| Q-3 | Are all GPIO ports (incl. GPIOF on F030) on the single-cycle IOPORT? Sweep 5 inferred "AHB", the RM map says IOPORT | 1- vs 2-cycle sample, every RX slot ledger | bench1 per port |
| Q-4 | How many keepalives arrive between reset end and the first SETUP on Windows 10/11 xHCI, direct and behind a TT hub? | Servo lock budget (S-12, L-14) | LA capture + `wg015vcd.py decode` |
| Q-5 | SRAM retained across `SYSRESETREQ`? | `.noinit` boot flag, double-tap, boot counter (S-7) | T10 test; fallback PLAN R7 |
| Q-6 | Real EXTI entry latency incl. 1-WS vector fetch and GPIO synchroniser | The [11,74]-cycle entry window (PLAN §2.2), L-9 | bench3 + `wg015vcd.py rx` entry stats |
| Q-7 | D± edge times per OSPEEDR setting into 200–450 pF; are series resistors needed on PY32 at all? DS has no tr/tf table | L-18, D-10 | Scope in T10 |
| Q-8 | Does the ARM TX emit the trailing stuff bit after a six-ones CRC tail (L-6)? | 1/64 of DATA packets would be NAKed/ignored otherwise | Walker path + loopback vector |
| Q-9 | macOS LS host behaviour (no LS-specific data found; GET_STATUS ZLP tolerance) | L-16 | T10 on a Mac |
| Q-10 | Stock `dfu-util` interop at 8-byte EP0 with `wTransferSize` 128 and `bwPollTimeout` 12 ms | D-4; first proof arrives on WG015 (STATE.md) | First hardware, either target |
| Q-11 | Does the alignment-dependent branch cost the branch guarded against exist from SRAM (Grainuum's "taken branch = 3" vs TRM's 2)? | Every 32/64 ledger; whether `.balign 4` on loop heads is needed | bench2 (PLAN OQ4/R4) |
| Q-12 | Can the dispatch tail stay in flash on 002B (R3 fallback) given flat LATENCY=1 and no cache? | 3 KB RAM budget | bench4 (flash fetch profile) + walker with the flash cost model |
| Q-13 | Do we care about suspend current (≤500 µA) for any PY32 product? | D-12 | Product decision; if yes, WFI on 3 ms idle + resume handling is a separate task |
| Q-14 | Piwiwiwiw/PY32F002B_USB_LOCK — anything at all? | Completeness only | Ignore unless someone can read the repo |
| Q-15 | Resolution of rv003usb #137 (DPU delay vs button hold) and root cause of #124 (USB3 switcher hub) | A-10, L-19 | Read the closed issue with API access; cannot be fetched anonymously |

## 9. Source index

Sweeps (2026-09-03): 1 cortex-m0-bitbang, 2 rp2040-pio-usb, 3 py32-ecosystem, 4 vusb-lineage,
5 timing-verification, 6 usb-ls-spec-traps. Repositories fetched today: `xobs/grainuum`
(`grainuum-phy-ll.s`, LICENSE), `TheYkk/py32f030-bitbang-usb` (`README.md`, `Makefile`,
`User/main.c`, `User/grainuum_py32f030.h`, `Libraries/CMSIS/Device/PY32F0xx/Source/system_py32f0xx.c`,
file existence of `User/grainuum*.{c,h,s}`, `User/usb_descriptors.c`), LICENSE files of
joyboot, LemcUSB, stm32f030-vusb, Pico-PIO-USB, uf2-samdx1, espusb, rv003usb. Primary
documents: Arm DDI0484B; PY32F002B DS V1.0 / RM V1.0 / RM B-C V1.0; PY32F030 DS V1.8 / RM V1.7;
USB 2.0 via TI SPRAAT5A; USB 2.0 §7.1.2.1 Table 7-9 (edge rates, cited from the spec directly).
Repo files: `doc/py32/PLAN.md`, `doc/wg015/{STATE,TODO,branch_notes,stack_portability}.md`,
`rv003usb/rv003usb.{S,c,h}`, `bootloader_dfu/*`, `tools/wg015_vcd/*`, `wg015_bench/bench3_irq.c`.
