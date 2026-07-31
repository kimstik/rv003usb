# K1921VG015 — FLASH controller & fetch determinism (research)

Status: IN PROGRESS
Sources: РП К1921ВГ015 (19.02.2025) = manual.txt (line refs), manual.pdf (page refs), niiet_riscv_sdk (file:line).

## 1. MFLASH register set / CTRL fields

Base 0x3000_D000 (Табл. 6.2, manual.txt:1968). РП section 7 «Контроллер Flash-памяти» + А.4 «Регистры контроллера Flash-памяти» (manual.txt:16080-16366, PDF pp.325-333).

Registers (РП А.4):
- ADDR +00h: «Адрес... Должен быть выровнен по 16 байт. Не выровненные адреса выравниваются автоматически» (manual.txt:16087-16097)
- DATA0..3 +04h..+10h: 4×32-bit data words; «Все слова данных должны быть загружены в регистры до установки бита команды записи» (manual.txt:16101-16112)
- TACCR +1Ch: cycles of clk per 20 ns, reset 0x02 (manual.txt:16232-16241)
- TNVSR +20h: clk cycles per 5 ms, reset 500 (manual.txt:16245-16254)
- TERSR +24h: clk cycles per 100 ms, reset 10000000 (manual.txt:16272-16281)
- TNVHR +28h: clk cycles per 5 µs, reset 500 (manual.txt:16284-16293)
- TNVH1R +2Ch: clk cycles per 100 µs, reset 10000 (manual.txt:16311-16320)
- TRCVR +30h: clk cycles per 10 µs, reset 1000 (manual.txt:16323-16332)
- TPGSR +34h: clk cycles per 10 µs, reset 1000 (manual.txt:16349-16358)
- CMD +44h, reset DEC0_0000h: KEY[31:16] must = C0DEh to launch; NVRON[8]; ALLSEC[3]; ERSEC[2]; WR[1]; RD[0]; «Команды должны выполняться по одной» (manual.txt:16129-16156)
- STAT +48h: BUSY[0], IRQF[1]. Note: «при работе на высоких частотах ядра необходимо добавлять задержку между записью регистра CMD и чтением флага BUSY, например, 5 NOP команд» (manual.txt:16179-16200)
- CTRL +4Ch, reset 0x1_0000: **only field LAT[18:16]** «Поле задания количества дополнительных тактов ожидания при чтении из Flash-памяти»; bits 31-19 and 15-0 reserved (manual.txt:16214-16226)

**No CEN / CFLUSH / cache-enable bit exists in РП CTRL description.** The 0x3000D000 controller CTRL has LAT only. Prefetch (2×128-bit buffers, see §3) is described as always-on behavior with no enable bit documented. (Cross-check vs SDK/SVD: see §7.)

## 2. LAT vs frequency

Flash cell read time: «Минимальное время чтения данных из Flash-памяти составляет до 60 нс (типовое значение задержки – от 30 нс)» (РП 7.1, manual.txt:2023-2024).

Табл. 7.1 (LDO1 = 1.2 V, manual.txt:2035-2043):
- fSYSCLK ≤ 60 MHz → LAT = 1
- fSYSCLK ≤ 30 MHz → LAT = 0
- reset value = 1

Табл. 7.2 (LDO1 = 0.9 V low-power, manual.txt:2045-2055): ≤60→3, ≤45→2, ≤30→1, ≤15→0. (In 0.9 V mode only flash READ allowed: manual.txt:2030-2033.)

=> At 48 MHz: LAT=1. At 25 MHz: LAT=0. LAT=0 max freq = 30 MHz (1.2 V).

## 3. Fetch path, prefetch buffers, determinism

- Flash read via two AHB buses: «Чтение Flash-памяти осуществляется через две шины AHB: I-code (для команд) и D-code (для данных). Чтение D-code шины имеет приоритет.» (РП 7.1, manual.txt:2014-2015). => D-code (loads from flash, e.g. constants in .rodata kept in flash) can stall I-code fetches — nondeterminism source if code+const both in flash.
- «Операция предвыборки» (РП 7.1, manual.txt:2065-2083): on miss, ready is deasserted, **4×32-bit words (128 bits)** are read from flash into first buffer; requested word returned; immediately the **next 128-bit line** is read into a second buffer. Hits in either buffer answer «мгновенно» (immediately, 0 extra WS). Miss outside both buffers → back to step 1 (full flash access with LAT waits).
- => Read width per access = 128 bits (matches ADDR 16-byte alignment). Sequential fetches within a 16-byte line and into the next prefetched line are 0-wait; a taken branch outside the two 128-bit buffers costs a full flash access (LAT extra wait states + base access).
- Determinism: РП gives NO cycle-count formula for miss cost and no way to disable the prefetch buffers (no CEN). Timing is deterministic only in the sense of the fixed algorithm above; actual fetch latency depends on buffer hit/miss and on concurrent D-code priority accesses. No РП statement guarantees fixed N cycles per fetch.

## 4. Write/erase, read-while-write

- Organization: 1 MB main = **256 pages × 4 KB**, 0x8000_0000..0x800F_FFFF (РП 7.1, manual.txt:2011-2013). (Табл. 6.1 line manual.txt:1938 says range 8000_0000–801F_FFFFh — inconsistent with 1 MB; 800F_FFFF is the arithmetically correct end.)
- Program unit: 128 bits = 4×32-bit words via DATA0..3 + ADDR (16-byte aligned) + CMD.WR with KEY=C0DE (А.4, manual.txt:16095-16097, 16109-16111, 16150-16151).
- Erase: per page (CMD.ERSEC, page from ADDR) or full (ALLSEC+ERSEC) (manual.txt:16146-16149).
- «Запись необходимо производить в предварительно очищенную ячейку памяти» (manual.txt:2021).
- **No read-while-write**: «чтении во время, когда Flash занята (стирание, запись), транзакция проходит успешно с неопределенными данными на выходе» (РП 7.1, manual.txt:2016-2018). Reads (incl. instruction fetch) during program/erase return undefined data without bus error => self-flashing code MUST execute from RAM (and take no flash-resident interrupts) until BUSY clears.
- Poll STAT.BUSY; ≥5 NOP between CMD write and first BUSY read (manual.txt:16197-16199).
- Program/erase times: not given as absolute numbers in РП section 7; encoded via timing registers (defaults: erase timebase 100 ms — TERSR, program pulse 10 µs — TPGSR, etc., manual.txt:16272-16358). Absolute typ/max t_prog/t_erase per page: NOT found in manual.txt (see GAPS).
- Write protect: CFGWORD.FLASHWE=0 blocks write to main region; write is then «интерпретируется как операция чтения» (manual.txt:2103-2111).

## 5. NVR / boot / SERVEN

- NVR: «дополнительная NVR область (две страницы по 4 Кбайт в диапазоне 0x0000 – 0x1FFF)... доступна для чтения, записи и постраничного стирания только через регистры блока FLASH» (РП 7.1, manual.txt:2057-2062). Accessed with CMD.NVRON=1 (manual.txt:16143-16145).
- CFGWORD at NVR offset +1FF0h (last cell of 2nd NVR page): JTAGEN[2] (default 1 = debug on), CFGWE[1], FLASHWE[0] (Табл. 7.3, manual.txt:2085-2106). Read by controller on every POR (manual.txt:2088-2089).
- Service full-erase: SERVEN pin =1 at reset → flash read-disabled (reads return zeros), JTAGEN ignored; then JTAG writes 0000_0100h to PMUSYS->SERVCTL → full erase of «всех областей основной и загрузочной памяти», DONE flag on completion (РП 7.2, manual.txt:2113-2125). Note the phrase «загрузочной памяти» (boot memory) = the NVR area; no separate ROM bootloader is described anywhere in РП.
- Boot address / vector: see §7 SDK (linker/startup); РП statement pending (see GAPS below if not found).

## 6. RAM0 (TCM)

- «Младшие 128 Кбайт ОЗУ0 подключены к интерфейсу TCM-A, а старшие 128 Кбайт - к TCM-B. Для получения максимальной производительности при исполнении из ОЗУ0 рекомендуется переменные и исполняемый код располагать в регионах, подключенных к разным интерфейсам TCM.» (Прим. к табл. 6.1, manual.txt:1945-1948).
- RAM0 = 256 KB @ 0x4000_0000; RAM1 = 64 KB @ 0x1000_0000, battery domain (manual.txt:1939-1941, 1950).
- More TCM/wait-state details: TBD

## 7. SDK cross-check

TBD

## GAPS

TBD
