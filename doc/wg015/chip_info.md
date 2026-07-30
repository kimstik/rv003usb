# К1921ВГ015 (K1921VG015, NIIET) — facts for rv003usb bitbang-USB port

Date: 2026-07-30. Purpose: verified chip facts for porting rv003usb (bitbang low-speed USB, 1.5 Mbps) from CH32V003 to K1921VG015, code running from flash.
Rule: every claim has a source. Unverified items are in the last section.

## 1. CPU core

- RISC-V 32-bit core, max 50 MHz (niiet.ru product page https://niiet.ru/product/к1921вг015/ ; habr.com/ru/articles/883220/).
- Single trap entry point at `mtvec` — "единственная точка входа в обработчик", no hardware vector table for external IRQs; PLIC handler dispatch done in software (`mach_plic_handler` table) (habr.com/ru/articles/883220/).
- Exact core vendor/name, ISA string, pipeline, branch/load/store cycle counts: **UNVERIFIED — see TODO**.

## 2. Clocks

- Max core frequency 50 MHz (niiet.ru product page; habr).
- Internal RC oscillator (HSI) 1 MHz; external crystal supported; PLL up to 50 MHz (habr.com/ru/articles/883220/).
- HSI accuracy poor: measured ~947 kHz instead of 1 MHz (-5%); default trim `PMURTC->HSI_TRIM = 7` gives ~938 kHz, value 8 gives ~1012 kHz (habr.com/ru/articles/883220/).
- Whether PLL can output exactly 48 MHz (integer multiple of 1.5 MHz): **UNVERIFIED — see TODO** (likely yes since chip has USB device needing 48 MHz clock, but must check PLL formula in datasheet).

## 3. Memories

- Flash: 1 MB at `0x8000_0000` (habr.com/ru/articles/883220/; niiet.ru product page: "1 Мбайт Flash").
- RAM: 256 KB at `0x4000_0000`; second RAM 64 KB at `0x1000_0000` (habr.com/ru/articles/883220/).
- Peripherals at `0x2000_0000` (habr.com/ru/articles/883220/).
- Flash wait states, prefetch/cache, exec-from-RAM: **UNVERIFIED — see TODO**.

## 4. GPIO

- Registers organized as set/clear/toggle: `DATAOUTSET`, `DATAOUTCLR`, `DATAOUTTGL`, plus masked-write regs `MASKLB`/`MASKHB` (habr.com/ru/articles/883220/).
- Undocumented `DIFF` register exists (habr.com/ru/articles/883220/).
- Port base addresses, bus (AHB/APB), access cycle counts, 5V tolerance, open-drain: **UNVERIFIED — see TODO**.

## 5. Interrupts

- PLIC (Platform-Level Interrupt Controller); single mtvec entry, software dispatch (habr.com/ru/articles/883220/).
- GPIO edge interrupt capability + latency: **UNVERIFIED — see TODO**.

## 6. Timers

- **UNVERIFIED — see TODO** (machine timer mtime presence/width/clock).

## 7. USB hardware

- Chip HAS a hardware USB device controller, 4 endpoints; per habr review "он едва работает" (barely works) (habr.com/ru/articles/883220/). Official SDK has USB HID example (github.com/siimteam/k1921vg015-usb-hid-cmake-ninja mirrors it).
- So bitbang remains a reasonable plan; HW USB is device-only/limited — details TODO.

## 8. Power / voltage

- **UNVERIFIED — see TODO** (supply and IO voltage; expected 3.3 V IO).

## 9. Tooling

- Official SDK: `niiet_riscv_sdk` on GitFlic: https://gitflic.ru/project/niiet/niiet_riscv_sdk (web search; habr).
- Toolchain: standard riscv GCC (habr).
- Debug: JTAG, via patched OpenOCD `openocd-k1921vk` (v0.12.0-k1921vk), FT2232/FT4232-based adapters (habr.com/ru/articles/883220/).
- VSCode extension `niiet-aspect-x.x.x.vsix` exists (web search summary of niiet.ru).
- Quick start doc: "Быстрый старт с микроконтроллером К1921ВГ015" https://niiet.ru/wp-content/uploads/2024/10/Быстрый_старт_К1921ВГ015_240716.pdf and newer combined https://niiet.ru/wp-content/uploads/2025/09/Быстрый_старт_NIIET_RISCV-1.pdf
- Community repos: github.com/bw2012/k1921vg015 (easy start), github.com/Gennadiy-V/Run_leds_K1921VG015, github.com/x893/CANopenNode-VG015, github.com/LeikoDmitry/k1921vg015 (devboard), github.com/ponikrf/k1921vg015-devboard (GitHub search 2026-07-30).
- ch32fun (local submodule in rv003usb) has NO K1921/NIIET support (grep of /home/user/rv003usb, 2026-07-30).

## 10. Errata / known issues (habr.com/ru/articles/883220/)

- HSI ~-5% off nominal; wrong default HSI_TRIM (7 instead of 8).
- JTAG flashing cannot fully reset the chip.
- ADC channel 7 non-functional; offset issues on ch 0–6.
- No ROM bootloader.
- Possible permanent brick via wrong register writes; recovery via SERVEN pin mode.
- USB device controller "barely works", 4 endpoints.

## UNVERIFIED / TODO

- Exact CPU core (SCR1 / CloudBEAR / other), ISA string, pipeline, cycle timings.
- PLL formula; achievable 48 MHz; crystal frequency range.
- Flash wait states vs frequency; prefetch buffer; jitter of fetch from flash.
- Code exec from RAM possibility (likely yes, addresses exist) — confirm.
- GPIO base addresses, bus type, write/read latency.
- GPIO IRQ (edge) support and latency; PLIC vector/priority details; mtvec vectored mode support.
- mtime/mtimecmp presence, width, clock source.
- Supply/IO voltage, 5V tolerance, open-drain.
- SVD file location; linker script + startup examples (SDK paths).

## Open risks for bitbang USB

- 50 MHz max clock: 48 MHz (32× 1.5 MHz) must be produced by PLL — TBD.
- Flash fetch wait states/prefetch may add jitter — may need to run TX/RX loop from RAM.
- No hardware vectored interrupts (single mtvec + PLIC software dispatch) → interrupt entry latency higher and variable vs CH32V003 (PFIC + HPE). Bitbang RX relies on fast, low-jitter edge IRQ entry.
- HSI is 1 MHz and inaccurate → external crystal essentially mandatory for USB timing.
