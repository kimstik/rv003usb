# K1921VG015 — FLASH controller & fetch determinism (research)

Status: DONE 2026-07-31
Sources: РП К1921ВГ015 (19.02.2025) = manual.txt (line refs; page numbers as printed in text), manual.pdf, niiet_riscv_sdk (file:line), osobennosti.txt / quickstart.txt (NIIET app notes).
SDK root = /tmp/.../scratchpad/niiet_riscv_sdk (paths below relative to it).

## 1. FLASH controller register set (base 0x3000_D000)

Base per Табл. 6.2 (manual.txt:1968). Registers per РП Приложение А.4 «Регистры контроллера Flash-памяти» (manual.txt:16080-16366, printed pp.325-333):

| Reg | Off | Reset | Fields (РП) | manual.txt |
|---|---|---|---|---|
| ADDR | +00h | 0 | VAL[31:0], «выровнен по 16 байт. Не выровненные адреса выравниваются автоматически» | 16087-16097 |
| DATA0..3 | +04..+10h | FFFF_FFFF | 4×32-bit data words | 16101-16112 |
| TACCR | +1Ch | 0x02 | clk cycles per 20 ns (flash access time base) | 16232-16241 |
| TNVSR | +20h | 500 | clk cycles per 5 ms | 16245-16254 |
| TERSR | +24h | 10000000 | clk cycles per 100 ms (erase timebase) | 16272-16281 |
| TNVHR | +28h | 500 | clk cycles per 5 µs | 16284-16293 |
| TNVH1R | +2Ch | 10000 | clk cycles per 100 µs | 16311-16320 |
| TRCVR | +30h | 1000 | clk cycles per 10 µs (recovery) | 16323-16332 |
| TPGSR | +34h | 1000 | clk cycles per 10 µs (program pulse) | 16349-16358 |
| CMD | +44h | DEC0_0000 | KEY[31:16]=C0DEh, NVRON[8], ALLSEC[3], ERSEC[2], WR[1], RD[0] | 16129-16156 |
| STAT | +48h | 0 | IRQF[1], BUSY[0] | 16179-16200 |
| CTRL | +4Ch | 1_0000 | **LAT[18:16] only**; rest reserved | 16214-16226 |
| LP | +C8h | 0 | LPEN[0] (SVD only; РП mentions «в регистре LP установить бит LPEN», manual.txt:2032) | SVD:16443-16461 |

Timing registers are write-locked while STAT.BUSY=1 (each register description, e.g. manual.txt:16241). Reset defaults correspond to a 100 MHz clk (e.g. TACCR=2 cycles per 20 ns) — must be reprogrammed to actual SYSCLK before program/erase (derived from register definitions; no explicit РП sentence — see GAPS).

### CEN / CFLUSH: РП vs SDK discrepancy
- РП CTRL documents **only LAT[18:16]**, «Поле задания количества дополнительных тактов ожидания при чтении из Flash-памяти», bits 31-19 & 15-0 reserved (manual.txt:16221-16226).
- SDK header defines CTRL.CEN bit1 «Cache enable bit», CTRL.CFLUSH bit8 «Cache bit» (write-only per SVD), LAT as **[19:16] 4-bit** (platform/Device/K1921VG015/include/K1921VG015.h:9345-9363; tools/svd/K1921VG015.svd:16420-16441).
- SDK startup actually sets `FLASH->CTRL_bit.LAT = 3; FLASH->CTRL_bit.CEN = 1;` before switching SYSCLK to 50 MHz PLL (platform/Device/K1921VG015/source/system_k1921vg015.c:241-243).
- What CEN gates (the 2×128-bit prefetch of §3? a separate cache?) is stated NOWHERE. РП describes the prefetch with no enable bit. CFLUSH/CEN semantics beyond the SVD one-liners: undocumented (GAPS).
- Note: the CPU core additionally has its own **2 KB instruction cache** («Cache-I 2 кБайт», osobennosti.txt:36; «кэшем команд», РП manual.txt:370; quickstart.txt:50). РП gives no I-cache control/bypass mechanism; SDK startup has cache_flush hooks compiled out (PLF_CACHE_CFG unset, startup_k1921vg015.S:44-49; PLF_CACHELINE_SIZE defaults 0, include/arch.h:60-62).

## 2. LAT vs frequency

- Cell access time: «Минимальное время чтения данных из Flash-памяти составляет до 60 нс (типовое значение задержки – от 30 нс)» (РП 7.1, manual.txt:2023-2024).
- Табл. 7.1, LDO1=1.2 V (manual.txt:2035-2043): fSYSCLK ≤ **60 MHz → LAT=1**; ≤ **30 MHz → LAT=0**. «Значение параметра после сброса равно 1».
- Табл. 7.2, LDO1=0.9 V low-power (manual.txt:2045-2055): ≤60→3, ≤45→2, ≤30→1, ≤15→0; in 0.9 V mode flash is read-only (manual.txt:2030-2033).
- => **48 MHz needs LAT=1; 25 MHz allows LAT=0; LAT=0 ceiling = 30 MHz** (normal 1.2 V).
- SDK uses LAT=3 at 50 MHz (system_k1921vg015.c:242) — more conservative than РП table; reason unknown.

## 3. Fetch path & determinism

- Two AHB read buses: «I-code (для команд) и D-code (для данных). Чтение D-code шины имеет приоритет.» (РП 7.1, manual.txt:2014-2015). D-code (data reads from flash, e.g. .rodata) preempts instruction fetch — jitter source if constants live in flash.
- «Операция предвыборки» (РП 7.1, manual.txt:2065-2083), exact mechanism:
  1. On request to a non-prefetched address: HREADY=0, transaction stalls.
  2. **4×32-bit words (128 бит)** read from flash into buffer 1.
  3. Requested word returned, ready set.
  4. Immediately the **next 128-bit line** is read into buffer 2; hits in buffer 1 during this answer «мгновенно», other addresses stall until buffer-2 fill completes, then goto 2.
  5. Hits in buffer 1 or 2 answer «мгновенно»; on hit in buffer 2 → buffer 1 := buffer 2, next line prefetched; miss both → goto 1.
- So: read width = **128 bits/access** (matches ADDR 16-byte alignment); sequential code within the current + next 16-byte line runs with **0 extra wait states**; a miss (taken branch outside the two buffers) costs a full flash access with LAT extra wait cycles. РП gives **no cycle formula** for miss cost (only LAT = "additional wait cycles" + ~30-60 ns cell time).
- No documented way to disable the prefetcher (no CEN in РП); with SDK CEN=0 semantics unknown (§1).
- On top sits the core's 2 KB I-cache (§1) — hit/miss behavior, line size, and any lockdown are undocumented in all local sources.
- **Conclusion: РП contains no statement guaranteeing fixed N-cycle fetch from flash in any mode.** Determinism from flash is algorithmically bounded (buffer scheme) but not cycle-exact across branches; two independent caching layers (flash prefetch + core I-cache) and D-code priority make flash execution timing formally non-deterministic. Only measurement can characterize it.

## 4. Write/erase, read-while-write

- Geometry: main flash 1 MB = **256 pages × 4 KB**, 0x8000_0000..0x800F_FFFF (РП 7.1, manual.txt:2011-2013). (Табл. 6.1 prints range 8000_0000h–801F_FFFFh for «1 Мбайт» — arithmetically wrong/2MB-window; manual.txt:1938.)
- Program unit per РП: **128 bits** = DATA0..3 at 16-byte-aligned ADDR, CMD = KEY|WR (manual.txt:16095-16097, 16109-16111, 16150-16151). Write only to erased cells (manual.txt:2021).
- Erase: page (ERSEC, page from ADDR) or full area (ALLSEC+ERSEC); NVRON selects NVR area (manual.txt:16143-16149).
- Sequence (per РП А.4 + SDK driver platform/plib015/src/plib015_flash.c:93-130): write ADDR → write DATA words → write CMD with KEY=C0DEh + WR (or ERSEC) → **≥5 NOP** → poll STAT.BUSY==0. The 5-NOP note is normative: «при работе на высоких частотах ядра необходимо добавлять задержку между записью регистра CMD и чтением флага BUSY, например, 5 NOP команд» (manual.txt:16197-16199). One command at a time (manual.txt:16138-16140).
- **No read-while-write.** «чтении во время, когда Flash занята (стирание, запись), транзакция проходит успешно с неопределенными данными на выходе» (РП 7.1, manual.txt:2016-2018) — fetches during program/erase return garbage, no bus fault. => Self-flashing code (bootloader) must run from RAM with interrupts to flash-resident handlers disabled until BUSY clears.
- Absolute t_prog/t_erase (typ/max µs/ms) are NOT stated in РП; hardware timing is derived from TERSR/TPGSR/... registers (defaults imply erase timebase 100 ms, program pulse 10 µs; manual.txt:16272-16358).
- Write protection: CFGWORD.FLASHWE=0 turns register writes into reads (manual.txt:2103-2111).
- **SDK bug/ambiguity:** K1921VG015.h:84 `MEM_FLASH_BUS_WIDTH_WORDS = 16UL`, and plib015 FLASH_WriteData/ReadData loop 16 words through `FLASH->DATA[i]` (plib015_flash.c:81-83,102-104) although the header struct has only DATA[4] + Reserved (K1921VG015.h:9373-9392) and РП says d=0..3. 16 words would cover +04h..+40h (64-byte row). РП and SVD (dim=4, svd:16294-16299) say 4. Program unit 16 vs 64 bytes needs hardware verification.

## 5. NVR / boot / SERVEN

- NVR: «дополнительная NVR область (две страницы по 4 Кбайт в диапазоне 0x0000 – 0x1FFF)», read/write/page-erase **only via FLASH registers** (CMD.NVRON=1), not memory-mapped (РП 7.1, manual.txt:2057-2062; 16143-16145). plib015 calls it «NVR область (загрузочная)» (plib015_flash.h:79).
- CFGWORD at NVR offset +1FF0h: JTAGEN[2] (default 1), CFGWE[1], FLASHWE[0]; latched on every POR (Табл. 7.3, manual.txt:2085-2106; SDK CFGWORD_BASE 0x00001FF0, K1921VG015.h:95).
- **No ROM bootloader** in any local source: РП describes none; quickstart flashing is JTAG/OpenOCD with flash driver (quickstart.txt:159; osobennosti.txt:492); SDK's UART bootloader is user-preloaded into flash at 0x8000_0000 («UART BootLoader must be preloaded in flash (ROM_BL)», ldscripts/k1921vg015_flash_bl.ld:1-14: ROM_BL 8K @0x80000000, app @0x80002000). Corroborates habr claim already in chip_info.md:97.
- Boot address: РП has **no explicit reset-PC statement** (searched: вектор/стартовый/начальный адрес/загрузка). Evidence: linker ENTRY(_start) with .startup.entry placed at ORIGIN(REGION_TEXT)=0x8000_0000 (ldscripts/k1921vg015_flash.ld:6-14, k1921vg015_common.lds:17-19); no alias of flash at 0x0 — 0x0000_0000 is Debug registers (Табл. 6.1, manual.txt:1944). => CPU starts from flash base 0x8000_0000 (SDK-implied; РП-unconfirmed — GAPS).
- SERVEN service mode (РП 7.2, manual.txt:2113-2125; pin №50, manual.txt:864; manual.txt:443-445): SERVEN=1 during reset → flash reads return zeros, JTAGEN ignored, all flash ops forbidden except full erase; JTAG writes 0000_0100h to PMUSYS->SERVCTL (+104h: DONE[8], SERVEN[0] status; manual.txt:15443-15462) → full erase of «всех областей основной и загрузочной памяти», DONE flag on completion. SERVEN must be held 0 in normal boot (manual.txt:2124-2125).
- After reset SYSCLK = HSICLK 1 MHz (SYSCLKCFG reset 0h, SRC=00b=HSICLK; manual.txt:14931-14942) — with reset LAT=1 flash is always safely readable at boot.

## 6. RAM0 (TCM)

- Full РП wording: «Младшие 128 Кбайт ОЗУ0 подключены к интерфейсу TCM-A, а старшие 128 Кбайт - к TCM-B. Для получения максимальной производительности при исполнении из ОЗУ0 рекомендуется переменные и исполняемый код располагать в регионах, подключенных к разным интерфейсам TCM.» (Прим. к Табл. 6.1, manual.txt:1945-1948).
- RAM0 256 KB @0x4000_0000 (TCM-A = 0x40000000-0x4001FFFF, TCM-B = 0x40020000-0x4003FFFF by the 128K split); RAM1 64 KB @0x1000_0000 in battery domain (manual.txt:1939-1941, 1950).
- That one note is ALL the РП says: no wait-state numbers, no CPU-vs-DMA arbitration rules, no statement that TCM fetch is 0-wait. The recommendation to split code/data across TCM-A/TCM-B implies same-bank code+data contention exists, i.e. code in one bank + data in the other avoids it.
- SDK supports RAM execution: ldscripts/k1921vg015_ram.ld (all regions in RAM0). For determinism, place USB bitbang code in TCM-A and its data/stack in TCM-B (or vice versa) and keep DMA off those banks — rule inferred from the РП note, not explicitly specified (GAPS).

## 7. SDK cross-check summary

- system_k1921vg015.c:241-243: on PLL(50 MHz) switch sets `FLASH->CTRL_bit.LAT=3; CEN=1;` (before SYSCLK switch at :255). No CFLUSH use anywhere in SDK.
- Flash write driver: platform/plib015/{inc/plib015_flash.h, src/plib015_flash.c} — FLASH_WriteData/ReadData/ErasePage/EraseFull; sequence ADDR→DATA→CMD(KEY|op|NVRON)→5×NOP→poll BUSY (plib015_flash.c:46-51, 68-145; FLASH_SetCmd at plib015_flash.h:144-147). Comments still say «2 32-битных слова» (copied from К1921ВК015 lib), loop count is 16 — see §4 bug.
- K1921VG015.h:83-95: MEM_FLASH_BASE 0x80000000, PAGE_SIZE 4096, PAGE_TOTAL 256, BUS_WIDTH_WORDS 16 (suspect), CFGWORD_BASE 0x1FF0, RAM0/RAM1 bases/sizes.
- SVD tools/svd/K1921VG015.svd:16255-16463: FLASH @0x3000D000, block size 0xCC, ADDR VAL[18:0], DATA dim=4, CMD/STAT as РП, CTRL{CEN[1], CFLUSH[8] wo, LAT[19:16]} reset claimed 0x0 (РП says 0x10000 — SVD reset values unreliable), LP{LPEN[0]} @+0xC8. SVD omits TACCR..TPGSR timing registers (РП documents them).
- startup_k1921vg015.S:19-40: _start in .startup.entry, mtvec setup; no flash/cache init in startup — LAT/CEN configured only in ClkInit().

## GAPS (not answered by local sources)

1. CEN semantics: whether CEN gates the 2×128-bit prefetch buffers of РП 7.1 or a separate (2 KB?) cache; behavior/timing with CEN=0; CFLUSH usage protocol. Only SVD one-liners exist. Needs NIIET support ticket or HW experiment (toggle CEN, measure with mcycle).
2. Exact cycles per flash fetch: no formula anywhere for miss cost (base access cycles + LAT) or hit cost; no «N тактов на выборку» statement. Measure on HW.
3. Core 2 KB I-cache: organization, line size, enable/bypass/flush controls, effect on flash vs RAM fetch — absent from РП section 8 (only «кэшем команд», manual.txt:370) and app notes.
4. IRQEN bit for FLASH STAT.IRQF: referenced (manual.txt:16189-16190) but not located in any register in РП or SVD (CTRL bit0 reserved — candidate). Unknown.
5. Program unit 4 words (РП/SVD) vs 16 words (SDK MEM_FLASH_BUS_WIDTH_WORDS/driver loop) — needs HW test.
6. Absolute flash program/erase times (µs/ms typ/max) — not in РП; only timebase registers. Datasheet (ТУ/спецификация) needed.
7. Reset PC = 0x8000_0000: implied by SDK linker/startup and memory map, but no explicit РП sentence found.
8. RAM0 TCM wait states / arbitration (CPU 0-wait? DMA stalls?) — РП silent beyond the code/data-in-different-banks recommendation. Measure.
9. Whether the requirement to rescale TACCR..TPGSR to actual SYSCLK before program/erase is mandatory is inferred from defaults (100 MHz base), not stated explicitly.
