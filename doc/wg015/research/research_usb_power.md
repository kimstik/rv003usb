# K1921VG015 — USB block / power / reset-boot / SERVEN / JTAG / package

Sources: РП К1921ВГ015 19.02.2025 (`manual.txt` line refs = extracted text of the PDF), NIIET SDK (`niiet_riscv_sdk`), NIIET app notes. Line refs are to the scratchpad copies.

## 1. HW USB block (context) — FS only, NO low-speed, pins NOT GPIO-muxable
- Block title: "30 Контроллер интерфейса USB FullSpeed"; features list: "USB 2.0 FullSpeed (Device)" — РП разд. 30, manual.txt:14538; РП разд. 1, manual.txt:85, :400.
- LOW-SPEED: NOT supported / not documented. Grep over whole РП for `низкоскор|LowSpeed|Low Speed|Low-Speed|1,5 Мбит|12 Мбит` = 0 hits. OPERATIONS register (+18h): "CURRENTSPEED 2 Индикатор скорости работы контроллера устройства: 0 Full Speed, 1 Зарезервировано" — РП А.23, manual.txt:24942-24944. (SVD shows the IP is HS-derived: CURRENTSPEED 1 = "High speed", HISPEED/chirp leftovers — K1921VG015.svd:10797-10908 — i.e. down, not up: no LS mode in the IP.)
- Endpoints: "одну контрольную точку (CEP) и четыре конечных точки (EP0 – EP3)" + dedicated 1 KB EP RAM — РП 30.1, manual.txt:14557-14560. Full register list (base 2001_0000h): INTSTAT0/1, INTEN0/1, OPERATIONS, FRAMECNT, USBADDR, CEP_*, EPx_* (x=0..3), DMA_*, AHB_DMA_ADDR, PHY_PD, PLLUSBCFG0-3, PLLUSBSTAT — РП А.23 manual.txt:24839-25535; SVD USB peripheral register list.
- Clocking: "Для работы блока USB выходную частоту блока USBPLL необходимо настроить на частоту 60 МГц" — РП 4.x, manual.txt:1538-1540; also 30.x manual.txt:14695-14700. Clock source select USBCLKCFG.USBCLKSEL: 0=PLLUSB0CLK, 1=SYSCLK — manual.txt:25505-25514.
- Pins: USB_DN = pin 31, USB_DP = pin 30, "I/O Вход/выход" — in the DEDICATED pin table 2.2 (with RST/JTAG/SERVEN), NOT in alt-function table 2.1 of ports A/B/C — РП табл. 2.2, manual.txt:869-870; УГО рис. 2.2 shows them separate from PORT A/B/C — manual.txt:530-533. => DP/DM wire to the integrated PHY only; they cannot be driven as GPIO.
- Integrated PHY exists: PHY_PD reg (+7C0h) with TX/RX/CMN powerdown bits — РП А.23, manual.txt:25440-25453; power modes: "USB – отключается PHY блок USB для экономии электроэнергии" — manual.txt:1731.
- Integrated DP pull-up: NOT documented. No pull-up / soft-connect bit anywhere in РП А.23 or in the SVD USB register set (grepped `PUEN|PULLUP|DPPU|SOFTCONN`) => assume external 1.5k pull-up needed even for the HW block (GAP: РП silent).
- Note (external claim, not local-sourced): habr errata alleges the USB block "едва работает". 4-EP claim is confirmed above. РП/app notes contain no errata for USB (osobennosti.txt is a marketing quick-start deck; it only says errata lives in the GitFlic repo — osobennosti.txt:486-492).
- Conclusion for this project: the HW USB block cannot do LS device (FS only, 60 MHz PHY clock, speed bit reserved), and USB_DP/USB_DN are not GPIO-muxable, so they cannot be bitbanged either. Bitbang LS must use ordinary GPIOA/B/C pins; the HW block is irrelevant except as a possible future FS target.

## 2. Power / electrical — 3.3 V IO confirmed
- Operating ranges (РП табл. 2.5): UCC1 (digital: core LDO, flash, PLL, periph, IO) 1.62–3.6 V; UCC2 (analog) 1.62–3.6 V; V_BAT 1.7–3.6 V — manual.txt:1193-1195. Nominal text: VCC1 1.7–3.6 V, total ≤150 mA incl. GPIO load; VCC2 nominal 3.3 V — manual.txt:453-456, 463-464.
- Single VCC1 rail powers core LDO + Flash/PLL + IO (pins 12/16/61 have dedicated roles: APC in, LDO1 core, Flash+PLL — manual.txt:457-462). No separate VDDIO. At VCC1 = 3.3 V the IO is 3.3 V CMOS: UIH = 0.7·UCC1..UCC1, UIL ≤ 0.8 V — табл. 2.5, manual.txt:1197-1198. => 3.3 V USB LS signaling levels OK.
- Flash+PLL supply constraint: VCC1 pin 61 needs 2.25–3.6 V when Flash AND PLL run ("UCC61FPLL 2,25 3,6"); flash-only 1.7–3.6 V — табл. 2.5 items 14-15, manual.txt:1220-1225. 48 MHz-from-PLL operation => keep VCC1 ≥ 2.25 V; 3.3 V is fine.
- GPIO output current norm: ±4 mA (IOL/IOH), abs max ±10 mA ≤5 s — табл. 2.5 items 9-10 manual.txt:1205-1206; табл. 2.6 manual.txt:1270-1271.
- Not 5V-tolerant: abs max input UCC1+0.6 V — табл. 2.6, manual.txt:1257. USB data lines: "Максимальный входной уровень сигнала по линиям данных USB … UUSBIN – UCC1" — табл. 2.5 item 19, manual.txt:1238-1240.
- Max SYSCLK: fCI = 50 MHz — табл. 2.5 item 11, manual.txt:1207-1209 (48 MHz target within spec). Flash wait states at LDO1=1.2 V: ≤30 MHz → 0 WS, ≤60 MHz → 1 WS (reset default 1) — РП табл. 7.1, manual.txt:2035-2043. => at 48 MHz: 1 flash wait state; flash fetch goes through a 128-bit 2-buffer prefetcher (РП 7.1 "Операция предвыборки", manual.txt:2065-2083) — cycle-exactness of code running from flash must account for this.
- Brown-out: no classic VCC1 BOR documented. Battery domain has UVLO: "UVLOZ – событие понижения напряжения батареи и внешнего питания ниже порога 1,8 В" (wake event, not reset) — РП 5.2, manual.txt:1758-1759; UVDIS bit in RTC_CFG0 — manual.txt:15529; WKUVLO status — manual.txt:15596. See GAPS.
- GPIO pulls: pull-UP only, no pull-down: PULLMODE "0 Подтяжка отключена / 1 Подтяжка к уровню логической единицы (pull-up)" — РП А.x GPIO, manual.txt:17001-17011; SDK K1921VG015.h:4821-4823 (Disable/PU). Pull resistance value not specified in РП (GAP).

## 3. Reset & boot
- External reset: RST pin 95, active low ≥10 us — РП разд. 2, manual.txt:440-442, 858.
- Reset causes readable in RCU.RSTSTAT (+20h, RCU base 3000_E000h): SYSRST (bit 4), WDT (bit 2), POR (bit 1) — РП А.1, manual.txt:14916-14925; base manual.txt:14789-14792, табл. 6.2 manual.txt:1966.
- Soft reset (reboot-to-bootloader): RCU.RSTSYS (+C0h): write KEY[31:16] = A55Ah with RSTEN (bit 0) = 1 — РП А.1, manual.txt:15350-15360.
- Boot-flag storage surviving reset: PMURTC block (base 3801_1000h) has RTC_REG[0..15] — "массив регистров пользователя", offset +20h + 4·n, 32-bit VAL each — РП А.3, manual.txt:15486-15488, 15777-15784. RTC/PMURTC regs "сбрасываются только при включении микроконтроллера после полного отключения питания … по выводам VCC1 и V_BAT" — РП 18, manual.txt:7169-7171 => they survive soft/WDT/pin reset. Caveat: SDK startup zeroes PMURTC->RTC_HISTORY (0x38011008) but does NOT touch RTC_REG — startup_k1921vg015.S:23-25.
- RAM1 64 KB @1000_0000h is in the battery power domain — РП 6, manual.txt:1941, 1950 (retention across soft reset not explicitly stated; RTC_REG is the documented-safe choice).
- Watchdogs: WDT @3000_B000h (system, reset source per RSTSTAT.WDT) and IWDT @3801_2000h (battery domain; configured via PMURTC.IWDG_CFG; its event is a WKUP[3] wake source, not listed in RSTSTAT) — РП табл. 6.2 manual.txt:1959, 1970; manual.txt:7567-7569, 7477.
- Memory map (РП табл. 6.1/6.2): Flash 1 MB @8000_0000h; RAM0 256 KB @4000_0000h (TCM-A/B halves); RAM1 64 KB @1000_0000h; PLIC @0C00_0000h; CLINT @0200_0000h; Debug @0000_0000h — manual.txt:1938-1944, 2011-2013.
- Reset PC: РП does not state it explicitly. SDK evidence: all flash linker scripts put ENTRY(_start) at ROM ORIGIN = 0x80000000 — k1921vg015_flash.ld:6-9; => core boots from flash base 0x8000_0000 (SDK-inferred, no РП quote).
- mtvec after reset: not documented in РП (grep `mtvec` = 0 hits); SDK startup writes mtvec = trap_entry early in _start — startup_k1921vg015.S:35-37. Treat mtvec as undefined until set by software.
- No mask-ROM bootloader found in РП/SDK. NIIET "UART BootLoader" is user-preloaded into the FIRST 8 KB of main flash: "UART BootLoader must be preloaded in flash (ROM_BL)", ROM_BL @0x80000000 8K, app @0x80002000 — k1921vg015_flash_bl.ld:2-11. "Загрузочная память" is mentioned once in РП only as an erase target of service mode — manual.txt:2122 (see GAPS).
- CFGWORD (config word in NVR flash, offset +1FF0h of 2nd NVR page; NVR = 2×4 KB pages @0x0000-0x1FFF via FLASH regs only): JTAGEN (bit 2, default 1 = debug on), CFGWE (bit 1), FLASHWE (bit 0 = main-flash write protect). Read by hardware after every POR — РП 7.1, manual.txt:2057-2106, 2088-2089.

## 4. SERVEN service mode (mass erase / unbrick)
- Pin: SERVEN = pin 50, "Вход сигнала активации сервисного режима" — РП табл. 2.2, manual.txt:864.
- Entry: SERVEN = 1 (pulled to 3.3 V) during reset => service mode: flash reads return zeros, all flash ops forbidden except full erase, CFGWORD.JTAGEN is IGNORED (i.e. unbrick path even with debug disabled) — РП разд. 2 manual.txt:443-445; РП 7.2 manual.txt:2113-2119.
- Erase: via JTAG write 0000_0100h to PMUSYS.SERVCTL (+104h, PMUSYS base 3000_F000h); erases "всех областей основной и загрузочной памяти"; DONE flag (bit 8) signals completion; SERVEN bit 0 = service-mode status — РП 7.2 manual.txt:2120-2123; SERVCTL manual.txt:15443-15459; base manual.txt:15367.
- Normal boot requires SERVEN held at 0 during reset — РП 7.2, manual.txt:2124-2125.

## 5. JTAG / debug
- Pins (dedicated, table 2.2, no alt functions): TCK=73, TDI=72, TDO=71, TMS=70, TRST=76 — manual.txt:859-863; "Порт JTAG … включает в свой состав пять выводов TCK, TMS, TDI, TDO, TRST" — manual.txt:435-437. Not repurposable as GPIO per РП.
- Debug toolchain per РП: Eclipse + GCC RISC-V + OpenOCD; adapters Olimex ARM-USB-OCD-H or J-Link — РП разд. 31, manual.txt:14761-14768. App note adds FTDI and Sipeed RV — osobennosti.txt:494-498.
- JTAG disable: CFGWORD.JTAGEN=0 kills debug; recover only via SERVEN service erase — manual.txt:2097-2099, 2119.
- 'JTAG cannot fully reset chip' errata: NO hint in РП (chapter 31 is 2 paragraphs, no ndmreset/reset discussion), nor in quickstart.txt/osobennosti.txt. Unverifiable locally — see GAPS.

## 6. Package / pinout
- "Микросхемы выполнены в корпусе LQFP100. Масса … не более 4 г." — РП разд. 2, manual.txt:912-913. Only this package appears in РП.
- 100 pins; GPIO = ports A (pins 34-49), B (53-60, 62-69), C (77-92), 16 bits each => 48 GPIO — УГО рис. 2.2, manual.txt:496-506, 534-537, 575-576.

## GAPS (no local source — do not treat as fact)
1. Integrated USB DP pull-up: РП and SVD show no pull-up/soft-connect control for the USB block. Whether the PHY has a hard-wired pull-up is UNKNOWN; РП is silent.
2. Reset PC value and mtvec reset value are not stated in РП; 0x8000_0000 boot is inferred from SDK linker scripts only.
3. Brown-out/BOR on VCC1: not documented (only battery-domain UVLO 1.8 V wake event). Behavior on slow VCC1 sag unknown.
4. GPIO internal pull-up resistance value: not specified in РП.
5. Whether RAM0/RAM1 contents survive RSTSYS soft reset: not explicitly stated (RAM1 battery-domain retention is stated only for power modes).
6. 'едва работает' USB errata and 'JTAG cannot fully reset chip' claim: external (habr); no errata text in local sources; osobennosti.txt points to errata in the GitFlic SDK repo (osobennosti.txt:490), not present in the local clone.
7. "Загрузочная память" (boot flash region) size/address: mentioned once (service-erase target, manual.txt:2122); NVR 2×4K pages are the likely candidate but РП never equates them.
8. LQFP100 is the only documented package; no die/QFN/other variants in РП.
