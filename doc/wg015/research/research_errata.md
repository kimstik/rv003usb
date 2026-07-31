# K1921VG015 — field errata & community experience

Topic: hardware gotchas from forum/community sources + local workaround comments.
Status: COMPLETE 2026-07-31. Web was reachable: all 17 electronix.ru pages, habr comments, official NIIET errata PDF (Rev.4, 25.07.2025) mined.

TOP TAKEAWAYS for the bitbang-USB port:
1. HW USB block is broken by official errata (control EP + one EP4 max; 2024 rev: one unstable EP) -> bitbang stack is genuinely useful here.
2. Official errata: USB pads have NO internal D+ pull-up and with standard 1.5k the host misreads EOP; NIIET says use 510-750 Ohm. Suspect weak pad drive/slow edges — relevant to bitbang line interface too.
3. FPU fdiv.s/fsqrt.s corrupt results when operands are fetched from FLASH (official erratum 5) — a real flash-path pipeline hazard on this die; code runs from FLASH.
4. Vendor + community init both use FLASH LAT=3 + CEN=1 at 50 MHz (forum folklore says 1 WS < 60 MHz); no stall-cycle data anywhere -> must measure.
5. Internal RC osc tolerance ~5% (habr comment) -> external crystal mandatory for USB timing; PLL min input 10 MHz (8 MHz HSE won't lock, habr 957832).
6. GPIO inputs have no Schmitt trigger (forum p.17).
7. Nobody has published GPIO toggle rate or IRQ latency numbers.

## 1. electronix.ru forum topic 200020 ("K1921VG015 — кто-нибудь уже пользовался?")
17 pages; URL pattern `.../page/N/`. Fetched all pages 2026-07-31 via WebFetch (summarized by fetch model; quotes as relayed).

### USB block: silicon bug — HW USB nearly unusable in current revision (KEY for this port)
- x893, 2025-03-27 (p.11), quoting NIIET: "В текущей ревизии микроконтроллера К1921ВГ015 не рекомендуется использовать USB интерфейс" — enumeration works, but data transfer via control endpoints can fail. Fix promised in next silicon revision.
- RabbitRabbit, 2025-09-15 (p.12), quoting official errata: "Корректная работа интерфейса USB... возможна c контрольной точкой и одной конечной точкой EP4" — only control EP + single EP4 usable. Kills standard CDC ACM; community workarounds: DFU (control-only), HID, proprietary.
- Driver_GV, 2025-09-15 (p.13): "Ну написано на офсайте, пока только один эндпоинт" — limitation is hardware-level in the USB IP, not firmware.
- Consequence for this project: bitbang USB on GPIO is a genuinely useful alternative on this chip, not just a stunt.

### Clock / overclock
- makc, 2025-02-26 (p.6): РП says "до 50 МГц" (not 80 as claimed elsewhere); 1.35 DMIPS/MHz (Dhrystone 2.1). NOTE: earlier posts (mantech, 2025-02-09, p.3) said "60 MHz" — forum numbers inconsistent; РП is the authority.
- Driver_GV, 2025-04-02 (p.12): overclock +50% (40 -> 60 MHz) worked on a sample marked "2514".
- RabbitRabbit (p.12, per fetch summary): failure at 60 MHz on another "2514" chip — margin varies per part. => running at 48 MHz is above the РП-rated 50?/40? MHz question — see GAPS; do not rely on forum numbers.

### JTAG / OpenOCD
- makc, 2025-02-08/2025-02-26 (p.1, p.4): vendor OpenOCD is a Windows-only binary, closed source: "У них есть сборка OpenOCD и она работает, но есть она только под винду... А исходники они пока не дают."
- Anton Bondarev (p.6): working open-source support at https://github.com/DCVostok/openocd-k1921vk — compile for Linux.
- makc (p.5): any OpenOCD-compatible adapter works (ARM-USB-OCD-H confirmed in discussion).
- Driver_GV, 2025-04-02 (p.12): custom board flashed via OpenOCD successfully.

### Working peripherals (community-confirmed)
- x893/whoami, 2025-09-15 (p.12): CAN, UART, SPI work; CANopenNode port: github.com/x893/CANopenNode-VG015; DFU bootloader over USB EP0 works with dfu-util.
- Anton Bondarev, 2025-02-25 (p.3): Embox RTOS runs on the chip.

### SDK / ecosystem quality
- makc, 2025-02-08 (p.1): "библиотеки SDK/BSP в крайне зачаточном состоянии (уровень китайских поделок раннего WCH)"; no IDE.
- dOb, 2025-02-26 (p.4): interrupts are non-vectored (PLIC, not CLIC): "Не векторное прерывание. Пока разберёшься, кто же выставил запрос..." — makc confirms PLIC without vectoring (vs Milandr MDR1206FI CLIC). => IRQ source dispatch costs extra cycles; matters for cycle-exact USB RX.

### Flash wait states + overclock (p.17)
- mantech / RabbitRabbit (p.17): flash WS ladder as used by community: <30 MHz = 0 WS, <60 MHz = 1 WS, <90 MHz = 2 WS, <120 MHz = 3 WS. At 60 MHz RabbitRabbit uses WS=2 (not 1) for stable operation; vendor guarantees only <=50 MHz.
- Silicon markings seen in the wild: "2436" (Driver_GV, basic blink/UART OK) and "2514" (RabbitRabbit; unstable >60 MHz without extra WS). => At 48 MHz expect 1 WS; cycle-exact asm timing must account for flash fetch stalls — see GAPS (no measured stall numbers anywhere).

### PLL instability (p.17)
- RabbitRabbit, Mar 7 (p.17): deviation from the РП coefficient-calculation procedure causes "неустойчивой работы блока PLL" (unstable PLL); details on the NIIET forum. => follow РП PLL programming sequence exactly.

### USB block clocking (p.17)
- RabbitRabbit, Oct 31 (p.17): vendor recommends clocking the USB block at 59 MHz, not the 60 MHz stated in docs; USB stability improves at 59. (Applies to the HW USB block, not bitbang.)

### GPIO input path (p.17) — matters for bitbang RX
- RabbitRabbit, Nov 12 (p.17): GPIO inputs have NO Schmitt trigger; for non-rectangular external signals add external conditioning. => USB D+/D- edges into plain CMOS inputs; slow edges may double-trigger; consider series R + clean layout.

### Silicon revisions / USB fix (p.14)
- ksv198, 2025-09-22 (p.14): default retail stock is the old revision with broken USB; NIIET rep claims a newer revision with "working USB across all endpoints" exists but must be explicitly ordered (legal entities only). ksv198: "у меня на руках пока не исправленная ревизия".

### Voltage-frequency limits (p.14)
- mantech, 2025-09-18 (p.14): datasheet frequency-vs-voltage graph limits operation to 8 MHz at 3.0 V (per his reading). => verify VDD >= nominal for 48 MHz; check РП/DS graph.

### RTC (p.15)
- makc, 2025-09-23 (p.15): "По факту их там нет, ни в DS, ни в RM. Там нет батарейного домена питания" — no battery-backed RTC domain.

### Debug tools (p.17)
- RabbitRabbit, Feb 25 (p.17): SEGGER J-Link support exists; both main flash and NVR (non-volatile region/info block) accessible.

### Not found in the whole thread (17 pages)
No mention of an undocumented GPIO DIFF register, no brick-recovery stories, no measured GPIO toggle rate or IRQ latency numbers, no flash-fetch stall-cycle measurements.

## 2. habr.com/ru/articles/883220/ — comments
Fetched 2026-07-31 (comments only).
- COKPOWEHEU, 2025-02-17 18:08: per errata, USB has "only one endpoint working, and even that unstably" (unclear which revisions). Confirms electronix USB findings independently.
- GidraVydra, 2025-02-18 20:01: internal RC clock frequency tolerance is ~5% — questioned ADC accuracy implications. => KEY for bitbang USB: 5% internal osc is far outside the ~1.5% LS USB tolerance; external crystal (HSE) is mandatory for the 48 MHz USB timing.
- Vcoderlab, 2025-02-17 19:35: internal Vref spec "(1.250 ±0.125) V" — 10% spread (ADC concern only, not USB-relevant).
- kenny5660, 2025-02-24 16:38: WAKEUP and AT_IN/OUT pins sit in a battery power domain (explains why they are dedicated, not muxed). NOTE: conflicts with makc (electronix p.15) "нет батарейного домена" — unresolved; check РП power-domain chapter.
- No flash/PLL/GPIO/JTAG errata in comments beyond the above.

## 2a. OFFICIAL ERRATA (found via WebSearch): niiet.ru errata_K1921VG015_Rev4_lqfp100.pdf, "Версия от 25.07.2025", Rev.4 LQFP100
Full PDF read 2026-07-31 (6 pages). All 10 items:

1. **RTC_REG[14] corrupt**: read returns RTC_REG[12] OR RTC_REG[14]. Always. WA: "Не использовать регистр RTC_REG[14]." (p.2)
2. **ADCSAR zero offset CH1-CH7**: up to 100-150 mV, sometimes. WA: increase sample time via CH_DELAY[]. (p.2) (Habr 957832 author saw offset on CH0 too.)
3. **USB: no internal D+ pull-up** (p.2): "Внутри блока USB микроконтроллера pullup резистор по линии D+ не реализован. При использовании внешнего pullup резистора номиналом 1,5 кОм (согласно стандарту USB) HOST может некорректно воспринимать окончание посылки." WA: external D+ pull-up **510-750 Ohm** instead of 1.5k. => Implies weak/slow USB pad edges (EOP misread at 1.5k). For LS bitbang on GPIO (pull-up on D-), watch edge rates; the stiffer-pull-up trick may apply.
4. **USB (2025 rev, "с EP4")** (p.3): correct operation only with control EP + single EP4 (USB->EP[3]), IN or OUT: "Корректная работа интерфейса USB (ревизии микроконтроллера с EP4) возможна c контрольной точкой и одной конечной точкой EP4". Fix "possible in next revision".
   4.1 **USB (2024 rev)**: enumeration + control EP OK, only one EP usable and it is unstable ("наблюдается нестабильность в работе конечной точки").
5. **FPU fdiv.s/fsqrt.s wrong result when operand(s) fetched from FLASH** (p.3-4). WA: GCC 14.1 from tools.cloudbear.ru with flag `-mfix-cloudbear-0001` (inserts NOP); in asm: put `nop` before fdiv.s/fsqrt.s; in C without fix: don't use FLASH-resident float constants as div/sqrt operands. => DIRECT relevance: code runs from FLASH; a flash-read/pipeline hazard exists in silicon. Bitbang USB uses no FPU, but this proves flash-fetch timing hazards are real on this die.
6. **AntiTamper wake broken after STOP/POWEROFF** (p.4). WA: 24 kOhm pull-up on AT_OUT to 2.2-3.3 V (+84 uA).
7. **DMA cyclic mode broken on >1 channel** (p.4): "При включении циклического режима более чем на одном канале DMA возникают ошибки передачи данных по всем активным каналам." WA: cyclic on one channel only; others use scatter-gather ("разборка - сборка"). => If bitbang design ever uses DMA for GPIO sampling: max ONE cyclic DMA channel.
8. **POWEROFF current >10 uA** on some samples / VDD<2.6 V (p.4-5). WA: external 2-transistor (AO3415A/AO3416A) cut-off on pins 61 (VCC_PLL_FLASH), 19 (VCC2), 33/51/75/93 (VCC1).
9. **PMURTC->RTC_HISTORY doesn't latch events** except ALARM (p.5). WA: write 0 to RTC_HISTORY, then read.
10. **RTC 1-second trim TRIM1S non-functional** (p.6): writes to PMURTC->RTC_TRIM.TRIM1S have no effect. No WA.

## 3. GitHub/GitFlic bare-metal projects (GPIO toggle / IRQ latency)
WebSearch 2026-07-31:
- embox/embox (github): has niiet/k1921vg015 platform support incl. GPIO subsystem (release notes). No timing numbers published.
- x893/CANopenNode-VG015 (github): CANopen port; confirms CAN/UART/SPI working (see forum p.12). No GPIO/IRQ timing data.
- DCVostok/openocd-k1921vk (github): OpenOCD fork with K1921VG015 flash driver, releases incl. Linux build `xpack-openocd-k1921vk-0.12.0-k1921vk`.
- habr.com/ru/articles/957832/ ("Ещё одна отладочная плата и тесты К1921ВГ015"): PLL min input freq is 10 MHz — 8 MHz HSE would not lock, author had to move to 12 MHz crystal. USB-HID example did not enumerate for the author (suspected missing D+ pull-up — consistent with official erratum 3). No GPIO toggle rate / IRQ latency measured ("это не канал Marco Reps...").
- habr.com/ru/articles/1018758/ ("Risc-V и запуск К1921ВГ015"): toolchain/OpenOCD setup only (DirtyJTAG, NIIET OpenOCD commit ed64294 lacks k1921vg015.cfg — copy manually; GCC14 calloc patch). No HW measurements.
- NO community measurement of GPIO toggle rate or IRQ latency found anywhere.
- habr 883220 (article body, via search snippet): GPIO has DATAOUTSET/DATAOUTCLR/DATAOUTTGL write-1 registers but no STM32-BSRR-style masked atomic set+clear in one write.

## 4. Local mining: community repo + SDK workaround comments
Community repo: /tmp/.../scratchpad/k1921vg015 (BlueBird-VG015 blink example).
- README.md:8-11: GCC pin: use xpack riscv-none-elf-gcc **13.2.0** — "в версии 14.2.0 почему-то нет rv32imfc_zicsr" (multilib missing in 14.2.0 xpack build).
- README.md:23-28: OpenOCD must be the DCVostok build `xpack-openocd-k1921vk-0.12.0-k1921vk` — "Необходима именно эта версия OpenOCD. В ней добавлена возможность прошивки микроконтроллера К1921ВГ015".
- README.md:50-51: BlueBird-VG015 board differs from NIIET dev board (LED on different pin etc.).
- blink/platform/source/system_k1921vg015.c:130-202: PLL configs for HSE 12/16/20/24 MHz all target Fout0 = 50 MHz; after PLL lock and BEFORE sysclk switch it sets `FLASH->CTRL_bit.LAT = 3; FLASH->CTRL_bit.CEN = 1;` (lines 201-202) — i.e. community + vendor code use flash latency 3 (not 1) at 50 MHz, cache/prefetch enabled (CEN).
- Same in official SDK: platform/Device/K1921VG015/source/system_k1921vg015.c:242-243 (`LAT = 3; CEN = 1;`) and both templates — vendor's own 50 MHz init uses LAT=3. (Contrast: forum WS ladder claims 1 WS <60 MHz; vendor code is more conservative. For cycle-exact code, flash fetch = up to 3 extra cycles per line miss unless cache hits.)
- SDK grep for errata/WA/hack markers: nothing beyond the above (matches were noise: "необходимо" contains "обход").
- Community K1921VG015.h:243: "WARNING: struct should be 1024 byte aligned! Allowed addresses 0xXXXXX000, 0xXXXXX400..." (DMA control structure alignment).
- Community K1921VG015.h:460/468/475: RCU_RSTSTAT POR bit comment says "WatchDog Reset status" — copy-paste doc bug in header.

### Undocumented ADCSD DIFF register (header/SVD vs РП mismatch)
- SVD tools/svd/K1921VG015.svd:6809-6811: ADCSD register `DIFF` "Enable differencial mode for channels", offset 0x10. Full SVD ADCSD map: CTRL 0x00, MODE 0x04, AMPL 0x08, ENB 0x0C, DIFF 0x10, READY 0x14, DATAUPD 0x18 (svd:6544-6947). Also in SDK header K1921VG015.h:4267 and community header :4191.
- РП А.15 "Регистры сигма-дельта АЦП" (manual.txt:21000-21148): NO DIFF register at all; lists READY at +10h (manual.txt:21118-21120) and DATAUPD at +0Ch (manual.txt:21135-21137, colliding with ENB +0Ch at 21105-21107). => РП ADCSD register map is wrong/stale; trust SVD/headers. Not USB-relevant, but proof that РП register tables can be off by one slot.

## GAPS
- No community or vendor measurement of GPIO toggle rate, GPIO output propagation, or IRQ (PLIC) latency in cycles — must be measured on hardware.
- No flash-fetch stall-cycle numbers (LAT=1 vs 3, cache hit/miss behavior of CEN) from any source; РП coverage of FLASH CTRL.CEN not mined here (other agent's topic).
- Whether the 48 MHz target with LAT=1 is safe: vendor code uses LAT=3 at 50 MHz, forum users claim 1 WS < 60 MHz works; unverified.
- Silicon revision <-> marking map incomplete: "2436", "2514" seen; official errata covers "Rev.4 LQFP100" and distinguishes "2024 rev" vs "2025 rev with EP4"; no mapping marking->Rev number found.
- electronix.ru pages digested via WebFetch summarizer — exact post wording/dates for some items (e.g. WS ladder on p.17) not verified against raw HTML.
- habr 883220 comment claim of "battery power domain" (kenny5660) vs electronix makc "no battery domain" — unresolved; РП power chapter not checked here.
- GitFlic could not be searched directly (no search fetch performed; only via WebSearch snippets).
- Whether official errata list has more items for other packages/revisions (only Rev.4 LQFP100 version 25.07.2025 read).

## STATUS: COMPLETE (2026-07-31). Web reachable; all 17 forum pages + habr comments + official errata PDF mined.
