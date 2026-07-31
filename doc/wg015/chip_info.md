# К1921ВГ015 (K1921VG015, NIIET) — facts for rv003usb bitbang-USB port

Date: 2026-07-30. Purpose: verified chip facts for porting rv003usb (bitbang low-speed USB, 1.5 Mbps) from CH32V003 to K1921VG015, code running from flash.
Rule: every claim has a source. Unverified items are in the last section.
Local SDK copy used below: cloned from https://gitflic.ru/project/niiet/niiet_riscv_sdk (official NIIET repo). Paths cited as `SDK/...` = `platform/Device/K1921VG015/...` in that repo.

## 1. CPU core

- Core: CloudBEAR **BM-310S6**, 32-bit RISC-V (compel.ru/lib/306214 "32-разрядное процессорное ядро BM-310S6 разработки CloudBEAR"; SDK `plic.c`/`plic.h` header comment: "PLIC header file bm310s6 core").
- SDK builds with `MARCH := rv32imfc_zicsr`, `MABI` ilp32f, libs from `rv32imfc_zicsr/ilp32f` (SDK/gcc/makefile lines 11–28). So compiled ISA = **RV32IMFC + Zicsr** (FPU single precision present).
- compel.ru article: core supports extended set "RVB32IMFCN_ZBA_ZBB_ZBC_ZBS" (per web-search snippet of compel.ru/lib/306214) — but SDK compiles only rv32imfc_zicsr; treat B-extensions as unverified for silicon.
- Pipeline: **2-stage**, "most RV32IMC operations execute in one cycle" (compel.ru/lib/306214; also BM-310 marketing: cnx-software.com/2020/10/20/bm-310-risc-v-mcu-core-iot-applications/).
- Privilege modes: machine + user (compel.ru/lib/306214 via search snippet).
- Exact cycle counts for taken branch / load / store: **UNVERIFIED** (no public BM-310S6 cycle table found; needs measurement on hardware).

## 2. Clocks

- Max core frequency 50 MHz (habr.com/ru/articles/883220/; SDK sets PLL to 50 MHz: `system_k1921vg015.c` comments "Fout0 = 50 000 000 Hz").
- Clock sources: HSI 1 MHz internal RC, HSE external crystal, LSI 32768 Hz, PLL (SDK `system_k1921vg015.h`: `HSICLK_VAL 1000000`, `LSICLK_VAL 32768`; `system_k1921vg015.c` ClkInit switch on SYSCLK_HSI/HSE/LSI/PLL).
- System PLL formula: `PLLCLK = REFCLK * (FBDIV + FRAC/2^24) / (REFDIV*(1+PD0A)*(1+PD0B))` — fractional-N (comment at `system_k1921vg015.c:146`). Two outputs Fout0/Fout1 with separate PD1A/PD1B dividers.
- **48 MHz is achievable exactly** with integer dividers: e.g. HSE=12 MHz, REFDIV=1, FBDIV=48, (1+PD0A)(1+PD0B)=12 → 12*48/12 = 48 MHz. (Derived from the PLL formula in `system_k1921vg015.c:146`; SDK examples officially support HSE = 10/12/16/20/24 MHz, ibid. lines 155–232.)
- SDK example configs all target 50 MHz with FBDIV=100 and various REFDIV/PD (SDK `system_k1921vg015.c:155-232`).
- Separate **USB PLL** inside USB block (`USB->PLLUSBCFG0..3`), same formula, fed from HSE; USB clock selectable USB-PLL or sysclk (`SystemCoreClockUpdate()` in `system_k1921vg015.c:76-96`).
- HSI accuracy poor: measured ~947 kHz vs 1 MHz; default trim `PMURTC->HSI_TRIM=7` (~938 kHz), 8 gives ~1012 kHz (habr.com/ru/articles/883220/). HSI unusable for USB timing.
- CKO clock-out on GPIOC.7 altfunc 3 (`system_k1921vg015.c:104-133`).

## 3. Memories

- Flash 1 MB @ `0x8000_0000` (`K1921VG015.h`: `MEM_FLASH_BASE 0x80000000`; linker `k1921vg015_flash.ld`: ROM ORIGIN 0x80000000 LENGTH 1024K). Page-erasable (compel.ru/lib/306214).
- RAM0 256 KB @ `0x4000_0000`, RAM1 64 KB @ `0x1000_0000` (`K1921VG015.h`: `MEM_RAM0_BASE`, `MEM_RAM1_BASE`; same in ld script). RAM0 is dual-interface TCM (TCM-A/TCM-B), RAM1 is in battery domain (compel.ru/lib/306214).
- Code executes from flash: default linker script places .text in ROM region (SDK `ldscripts/k1921vg015_flash.ld`). Code can run from RAM: SDK ships `k1921vg015_ram.ld` (ORIGIN 0x40000000) — RAM execution officially supported.
- Flash controller (registers @ `0x3000_D000`): `FLASH->CTRL` has `LAT` (4-bit latency, bits 16–19), `CEN` (cache enable, bit 1), `CFLUSH` (bit 8) (`K1921VG015.h` lines ~9350–9363). SDK sets `LAT=3; CEN=1` when switching to 50 MHz PLL (`system_k1921vg015.c:242-243`).
- => **Flash has wait states (3 @ 50 MHz) and a cache**; both are jitter sources for bitbang loops running from flash. LAT-vs-frequency table: **UNVERIFIED** (need spec PDF; niiet.ru down 2026-07-30).

## 4. GPIO

- 3 ports: GPIOA `0x2800_0000`, GPIOB `0x2800_1000`, GPIOC `0x2800_2000` (`K1921VG015.h:12056-12058`). 16 pins per port register layout (PIN0..PIN15 fields).
- Clock/reset enable via `RCU->CGCFGAHB` / `RCU->RSTDISAHB` bits GPIOxEN (`system_k1921vg015.c:107-108`) — **GPIO sits on AHB** (register names CGCFG**AHB**).
- Registers (all in `K1921VG015.h` GPIO_TypeDef, lines ~6150–6364):
  - `DATA` (input, RO), `DATAOUT` (RW), `DATAOUTSET`, `DATAOUTCLR`, `DATAOUTTGL` (WO atomic set/clear/toggle).
  - `OUTENSET`/`OUTENCLR` (direction), `ALTFUNCSET`/`ALTFUNCCLR`/`ALTFUNCNUM` (2 bits/pin, altfunc 0–3).
  - `PULLMODE`: 1 bit/pin, only Disable / **Pull-Up** (enum GPIO_PULLMODE_PIN0: Disable=0, PU=1) — **no internal pull-down** (`K1921VG015.h:4821-4823`).
  - `OUTMODE`: 2 bits/pin: 0=push-pull, 1=**open-drain**, 2=open-source (`K1921VG015.h:4958-4960`).
  - `SYNCSET`/`SYNCCLR`: "additional double flip-flop synchronization" on inputs — enable/disable per pin (input latency control!) (`K1921VG015.h:6270-6275`).
  - `QUALSET/QUALCLR/QUALMODESET/QUALMODECLR/QUALSAMPLE`: input qualifier (digital filter) (`K1921VG015.h:6276-6295`).
  - `MASKLB[256]`/`MASKHB[256]`: masked byte access windows (`K1921VG015.h:6362-6363`).
  - `LOCKKEY` (0xADEADBEE) + `LOCKSET/LOCKCLR` pin-config lock (`K1921VG015.h:6349-6360`).
- GPIO interrupts per pin: `INTENSET/INTENCLR`, `INTTYPESET/CLR` (type), `INTPOLSET/CLR` (polarity), `INTEDGESET/CLR` (every-edge), `INTSTATUS` (`K1921VG015.h:6296-6331`). All three ports share ONE PLIC line: IRQ 5 `IsrVect_IRQ_GPIO` (`K1921VG015.h:46`, `plic.h: PLIC_GPIO_VECTNUM 5`).
- GPIO access cycle cost, 5V tolerance: **UNVERIFIED** (needs spec/datasheet; niiet.ru down).

## 5. Interrupts

- **PLIC** @ `0x0C00_0000` (SDK `source/plic.c:37`), 31 sources (list in `plic.h`: WDT=1 … PMURTC=31), priorities 0–7 (threshold set 0..7 in `system_k1921vg015.c:269,281`), machine + supervisor targets (`plic.h` enum Plic_Target).
- Single trap entry: startup writes one handler address to `mtvec` (`startup_k1921vg015.S:35-37`); SDK C dispatch: `irq_entry` reads `mcause`, then `PLIC_MachHandler` claims/dispatches via `riscv_handler_map[]` table (`source/riscv-irq.c`, `source/plic.c`). **No hardware vectored mode used by SDK; habr review states no vector table support** (habr.com/ru/articles/883220/). mtvec vectored (mode=1) support on BM-310S6: **UNVERIFIED**.
- Interrupt entry latency (cycles): **UNVERIFIED**; but total latency for GPIO edge = core trap entry + mcause check + PLIC claim register read (AHB/APB read) + table dispatch — clearly worse than CH32V003 PFIC unless hand-written asm trap handler is used.
- GPIO edge IRQ exists (see §4) but shared single PLIC line for all ports.

## 6. Timers

- Standard RISC-V machine timer (CLINT-style): `MTIMECMP @ 0x0200_4000`, `MTIME @ 0x0200_BFF8`, 64-bit (SDK `include/mtimer.h`; API in `source/mtimer.c`). mtime clocked at sysclk per SDK default `MTIME_FREQ_HZ = 50000000` when PLL used / `HSECLK_VAL` when HSE (`mtimer.h`).
- Peripheral timers: TMR32 (32-bit) @0x30000000, TMR0/1/2 (16-bit?) @0x30001000..3, WDT, IWDT (`K1921VG015.h:12059-12080`); capture/compare per compel.ru/lib/306214.
- `mcycle` CSR availability: standard RISC-V M-mode requirement, expected present — **UNVERIFIED on silicon**.

## 7. USB hardware

- Chip HAS hardware **USB 2.0 FullSpeed device** controller @ `0x2001_0000` with its own PLL (compel.ru/lib/306214 "USB 2.0 FullSpeed"; `K1921VG015.h: USB_BASE`, `PLLUSBCFG0..3` regs; SDK has `platform/ntusb` USB stack). 4 endpoints, quality questionable: "он едва работает" (habr.com/ru/articles/883220/). USB HID SDK example exists (github.com/siimteam/k1921vg015-usb-hid-cmake-ninja).
- Note: HW USB is FS device. Low-speed *host-side* signaling (what rv003usb-style keyboard emulation uses as a device is LS device) — HW block presumably does FS device only; bitbang LS remains the plan per task. HW block LS-device capability: **UNVERIFIED**.

## 8. Power / voltage

- Deep sleep STOP/POWEROFF down to 8 µA; battery domain with auto VCC/VBAT switch; RTC (compel.ru/lib/306214).
- Supply/IO voltage numbers, 5V tolerance: **UNVERIFIED** (spec PDF needed; niiet.ru under maintenance 2026-07-30). USB LS needs 3.3 V signaling — assume 3.3 V IO but VERIFY.

## 9. Tooling

- Official SDK: https://gitflic.ru/project/niiet/niiet_riscv_sdk — contains:
  - Device support: `platform/Device/K1921VG015/` (header `K1921VG015.h` 12.4k lines, `startup_k1921vg015.S`, `system_k1921vg015.c`, `plic.c`, `mtimer.c`, riscv-irq.c).
  - Linker scripts: `ldscripts/k1921vg015_flash.ld`, `k1921vg015_ram.ld`, `k1921vg015_flash_bl.ld`, common `k1921vg015_common.lds`.
  - SVD: `tools/svd/K1921VG015.svd` (vendor NIIET, v1.14).
  - OpenOCD: `tools/openocd/scripts/target/k1921vg015.cfg` — JTAG, irlen 5, expected-id `0x00000D5B`, riscv target, flash driver `k1921vg015` @0x80000000, work-area in RAM0; interface cfgs for J-Link, Sipeed RV-debugger (FT2232-class), onboard FTDI. Service-mode mass-erase scripts (`k1921vg015_*_service_mode_erase.bat`, `openocd-snippets/k1921vg015/srv_erase.cfg`) — unbrick path.
  - Patched OpenOCD releases: https://gitflic.ru/project/niiet/openocd/release (SDK README.md; also patch for Syntacore sc-dt toolkit).
  - VSCode extension `tools/niiet-aspect-1.0.1.vsix`.
  - BSPs: NIIET-DEV-K1921VG015, NIIET-MINI-K1921VG015 boards (`hardware/bsp/`).
  - Peripheral lib `plib015` + examples (`projects/plib015/`), USB stack `platform/ntusb`.
- Debug interface: **JTAG** (openocd cfg above; habr: patched `openocd-k1921vk` v0.12.0, FT2232/FT4232).
- Toolchain: standard riscv64-unknown-elf GCC 13.2 (SDK makefile paths), or Syntacore SDT with NIIET patch (SDK README).
- Community: github.com/bw2012/k1921vg015 (easy start), Gennadiy-V/Run_leds_K1921VG015, x893/CANopenNode-VG015, LeikoDmitry/k1921vg015, ponikrf/k1921vg015-devboard, AzizSuf/K1921VG015 (GitHub search 2026-07-30).
- ch32fun (submodule in rv003usb) has NO K1921/NIIET support (grep of /home/user/rv003usb, 2026-07-30).
- Docs (niiet.ru down 2026-07-30, links from search): Quick start PDF https://niiet.ru/wp-content/uploads/2024/10/Быстрый_старт_К1921ВГ015_240716.pdf ; combined RISC-V quick start https://niiet.ru/wp-content/uploads/2025/09/Быстрый_старт_NIIET_RISCV-1.pdf ; "Особенности микроконтроллера К1921ВГ015" https://niiet.ru/wp-content/uploads/2025/05/Особенности_микроконтроллера_К1921ВГ015.pdf ; mirror of quick start at static.chipdip.ru/lib2/b/421/DOC082421042.pdf.

## 10. Errata / known issues

From habr.com/ru/articles/883220/ (community review, Feb 2025):
- HSI ~-5% off; wrong default HSI_TRIM (7 instead of 8).
- JTAG flashing cannot fully reset the chip.
- ADC channel 7 non-functional; offset on ch 0–6.
- No ROM bootloader.
- Brickable by wrong register writes; recovery = service mode via SERVEN pin (matches SDK `*_service_mode_erase` scripts).
- USB device "barely works" (4 endpoints).
- Undocumented GPIO `DIFF` register; altfunc numbering poorly documented.
Forum thread (more field experience): electronix.ru/forum/topic/200020-k1921vg015-kto-nibud-uzhe-polzovalsya/ — **not yet mined, TODO**.

## CORRECTIONS after deep-dive (2026-07-31, see doc/wg015/research/*)

Deep-dive over РП 19.02.2025 + official errata Rev.4 (25.07.2025) supersedes items above:
1. **LAT@48MHz = 1, not 3** (РП табл. 7.1: ≤60 MHz→1, ≤30→0; reset=1). SDK's LAT=3+CEN=1 is unexplained conservatism (research_flash.md §2).
2. Flash prefetch documented: 128-bit lines, 2 buffers, hits «мгновенно», miss = LAT waits; **no disable bit in РП**; CEN/CFLUSH exist only in SDK/SVD, semantics unknown. Plus **undocumented 2 KB core I-cache**. No fixed-cycle fetch guarantee anywhere (research_flash.md §3).
3. **No read-while-write**: fetches during program/erase return garbage silently (РП 7.1) — flasher + any live ISR must be in RAM (research_flash.md §4).
4. **HW USB officially broken** (errata №3,4: no internal D+ pull-up, only CEP+EP4 usable) — bitbang is the pragmatic path, not a stunt (research_errata.md §2a).
5. PLL: integer-mode 48.000 MHz configs verified legal for HSE 10/12/16/20/24/25 (VCO 200–1600 allowed); **fREF min 10 MHz — 8 MHz crystal can't feed PLL**; the "fractional jitter ≤ FIN period" quote is about the CAN divider, NOT SYSPLL (research_clocks.md §1-2). SDK-implied board crystal = 16 MHz.
6. GPIO: input always behind **2-clk synchronizer** (SYNCSET=1 adds 2 more; base not bypassable); **no Schmitt trigger** (forum p.17); INTSTATUS W1C; all ports → PLIC line 5; MASKLB masked *write* documented, masked read only inferred (research_gpio.md).
7. PLIC: claim = read MICC, complete = write MICC; **gateway blocks same-source re-request until complete** — free "never preempt" guarantee; clear INTSTATUS before MICC complete (research_core_irq.md §4).
8. Core: RV32IMC ops = 1 cycle (РП §8), MUL=2, DIV=2..16, **CSR ops drain the pipeline**; rdcycle available. Taken-branch/load real cost still unmeasured.
9. Not 5V-tolerant (abs max VCC+0.6, ≤5 s). IO 4 mA. Errata №1: RTC_REG[14] corrupt — don't use for boot flag.
10. REFSEL contradiction: РП fig. 4.1 implies REFSEL=1 for HSE→PLL, SDK never sets it and works — resolve on hardware (research_clocks.md §1.2).

## UNVERIFIED / TODO

- Cycle timing of BM-310S6 (branch/load/store, GPIO store latency over AHB) — no public table; measure with `mcycle` on hardware.
- mtvec vectored mode support; exact trap entry latency in cycles.
- Flash LAT table vs frequency; behavior/penalty of flash cache miss; whether LAT can be 0 @ ≤25 MHz.
- Supply voltage / IO voltage / 5V tolerance (datasheet needed — niiet.ru under maintenance during research).
- B-extension (Zba/Zbb/Zbc/Zbs) actually enabled in silicon.
- USB HW block: LS mode support, pull-up config.
- GPIO input path latency with SYNC disabled (SYNCCLR) — is async read allowed?
- electronix.ru forum errata not yet reviewed.
- Official spec/datasheet PDF not yet obtained (site down); retry niiet.ru or web.archive.org.

## Open risks for bitbang USB

1. **Clock**: 48 MHz (32 cycles/bit @1.5 Mbps) is reachable via fractional/integer PLL from HSE (formula verified), under the 50 MHz max. HSE crystal required; HSI (1 MHz, ±5%) useless for USB.
2. **Flash jitter**: 3 wait states + cache at 50 MHz (`FLASH->CTRL.LAT=3, CEN=1`). Cache hit/miss variance can break cycle-exact TX/RX. Mitigation: run critical bitbang loops from RAM0 (TCM, official `k1921vg015_ram.ld` exists) or characterize cache determinism.
3. **IRQ entry**: no vectored interrupts; PLIC claim + software dispatch adds many cycles and possible jitter to SE0/keep-alive edge response. rv003usb expects fast EXTI-style entry; need minimal asm mtvec handler that checks GPIO first.
4. **Single GPIO IRQ line** for all ports (PLIC #5) — fine if only D+/D- IRQ used, but any other GPIO IRQ shares it.
5. **2-stage core, IPC ~1** similar to CH32V003 core class, but untested: bit timing budget (32 cycles/bit at 48 MHz vs 33.33 at 50 MHz) must be re-verified by measurement. Note: running at 50 MHz gives non-integer 33.33 cycles/bit — must use 48 MHz.
6. **No internal pull-down**, only pull-up — external 1.5k pull-up on D- needed anyway (LS device); ensure IO is 3.3 V (unverified).
7. USB LS on 3.3 V requires IO at 3.3 V and pins tolerant to host signaling; 5V tolerance unverified.
