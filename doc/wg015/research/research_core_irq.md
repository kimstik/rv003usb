# K1921VG015 — core / trap entry / PLIC (ISR latency budget)

Status: DONE. Sources: РП = manual.txt ("Руководство пользователя К1921ВГ015", 19.02.2025); SDK = /tmp/.../scratchpad/niiet_riscv_sdk (paths below relative to repo root). Every uncited statement is in GAPS.

## 1. Core (BM-310S6, ISA, timing)

- Core: CloudBEAR BM-310S6. «Микроконтроллер К1921ВГ015 спроектирован на базе RISC-V ядра ВМ-310S6» — РП §3 "Архитектура изделия", manual.txt:1281. SDK plic.c header comment: "PLIC source handler file bm310s6 core" (platform/Device/K1921VG015/source/plic.c:3).
- ISA: «поддерживает систему команд RV32IMFCN_ZBA_ZBB_ZBC_ZBS», privilege modes machine + user — РП §3, manual.txt:1282-1285; repeated РП §8, manual.txt:2130-2133. So: F (FPU), N (user-level interrupts), Zba/Zbb/Zbc/Zbs present.
- Pipeline: 2 stages. «Конвейер BM-310S6 состоит из двух стадий»: 1) fetch request to program-memory subsystem (PMS); 2) read code fragment from PMS + decode + execute — РП §3 manual.txt:1286-1290, §8 manual.txt:2134-2138. FE = IFU + IDU + IFQ (fetch queue); a code fragment holds 1 or 2 instructions — manual.txt:1293-1297, 2150.
- Instruction timing (РП §8, manual.txt:2156-2169): «Выполнение всех команд RV32IMC занимает один такт, кроме команд умножения/деления»:
  - MUL / MULH / MULHS / MULHSU — 2 cycles (pipelined multiplier, manual.txt:2166-2167);
  - DIV / DIVU / REM / REMU — 2..16 cycles (iterative divider, manual.txt:2162-2165);
  - CSR-access instructions «исполняются в пустом конвейере» (drain the pipeline first) — manual.txt:2168-2169. Budget CSR reads/writes in the ISR as >1 cycle effective.
  - РП gives NO taken-branch penalty, NO load-use latency, NO separate store timing, and NO full cycle table (CloudBEAR databook not reproduced) → GAPS.
- Counters: РП Table 8.1 lists RDCYCLE/RDCYCLEH, RDTIME/RDTIMEH, RDINSTRET/RDINSTRETH as supported (pseudo-instructions) — manual.txt:2286-2310. ⇒ cycle counter exists and rdcycle is executable. SDK actively uses it: `#define rdcycle() read_csr(cycle)` — platform/Device/K1921VG015/include/csr.h:56; mcycle/mcycleh/minstret accessors in include/riscv-csr.h:681-726, 3199-3243 (generic header, weak evidence by itself).

## 2. mtvec modes / CLINT / PLIC-only

- РП never mentions mtvec (grep "mtvec|MTVEC": 0 hits in manual.txt). Direct-vs-vectored support is NOT documented → GAPS.
- SDK evidence — both trap paths use DIRECT mode (mtvec MODE bits = 0):
  - startup: `load_addrword_abs t0, trap_entry; csrw mtvec, t0` — platform/Device/K1921VG015/source/startup_k1921vg015.S:35-37; trap_entry is `.align 6` (64-byte aligned), single entry for all traps — same file:245-269.
  - alt path: `csr_write_mtvec(irq_entry)` with irq_entry `__attribute__((interrupt("machine"), optimize("align-functions=4")))` — source/riscv-irq.c:7-14. mcause dispatch in SW (riscv-irq.c:47-89).
  - No SDK code anywhere sets mtvec.MODE=1 (vectored). riscv-csr.h defines MTVEC_MODE bit fields (include/riscv-csr.h:306-309) but that header is generic RISC-V.
- CLINT present (machine SW + timer IRQs only): РП §9.1, manual.txt:2332-2345. Offsets: 0x0000 CLINT_MSIP0, 0x4000 CLINT_MTIMECMP, 0xBFF8 CLINT_MTIME. Base 0x0200_0000 (Table 6.1, manual.txt:1943); SDK confirms: `#define RISCV_MTIMECMP_ADDR (0x2000000 + 0x4000)`, `RISCV_MTIME_ADDR (0x2000000 + 0xBFF8)` — include/mtimer.h:6-7.
- No CLIC anywhere. All external (peripheral) IRQs are funneled ONLY through the PLIC into mip.MEIP — РП §9.2/§9.5, manual.txt:2367-2390, 2527-2532.

## 3. Trap entry mechanics

- РП §9.5 (manual.txt:2526-2543): PLIC → int_meip → mip.MEIP (bit 11). If mie.MEIE=1 and mstatus.MIE=1: «управление передается в обработчик прерывания (выполняется переход на адрес обработчика прерываний), а в регистр mcause записывается "причина"». mcause = Machine external interrupt for all PLIC sources (single cause code — SW must claim via MICC to learn the source). Return: MRET (manual.txt:2541).
- HW saves only CSR state (mepc/mcause/mstatus per RISC-V privileged spec, which РП references as [3], manual.txt:2541-2543). NO GPR stacking by hardware — confirmed by SDK doing full SW context save: context_save macro saves x1-x31 (include/memasm.h:104-157), trap_entry calls it then reads mcause/mepc (startup_k1921vg015.S:248-268).
- mstatus.MIE handling on entry/MRET: not described in РП (standard RISC-V MIE→MPIE assumed but not stated) → GAPS.
- IRQ-assert-to-first-instruction cycle count: NOT documented in РП. MRET cycle count: NOT documented. → GAPS. Known pieces of the pipeline: IGW adds 1 clk (request formed «в следующем такте» after int_global pulse — manual.txt:2374-2376); CSR ops execute in a drained pipeline (manual.txt:2168-2169).

## 4. PLIC (РП §9.2-9.5)

- Address space: «0C00_0000h – 0CFF_FFFFh Регистры PLIC» — Table 6.1, manual.txt:1942. SDK: `#define PLIC_BASE (0x0C000000UL)` — source/plic.c:37.
- Register map (РП Table 9.2, manual.txt:2462-2493; struct mirror in plic.c:17-35):
  - 0x0C000000 + 4n: PRI[n], n=1..31 (PRI[1]=0x0C000004 ... PRI[31]=0x0C00007C)
  - 0x0C001000: IPM0 — pending bits, sources 1-31, RO, bit0 hardwired 0
  - 0x0C002000: MIEM0 — M-mode per-source enable mask (sources 1-31)
  - 0x0C002080: UIEM0 — U-mode enable mask
  - 0x0C004000 NINT / 0x0C004004 NPRI — counts, RO
  - 0x0C200000: MTHR — M-mode priority threshold
  - 0x0C200004: MICC — M-mode claim/complete
  - 0x0C201000 UTHR / 0x0C201004 UICC — U-mode
- Priorities: 7 levels, 1=lowest, 7=highest; reset priority = 0 = source disabled; priority MUST be configured before enabling — manual.txt:2495-2500. EIP asserted only if pending priority strictly > threshold — manual.txt:2410-2416.
- Gateway (IGW): all int_global inputs are edge-sensitive («Запросы прерываний int_global являются edge-sensitive сигналами», manual.txt:2372-2373). A 1-clk active level → IGW issues request to PLIC next cycle; «Запрос в PLIC будет удерживаться до прихода от PLIC подтверждения о завершении выполнения прерывания (interrupt completion). Значение бита сигнала int_global игнорируется после выставления запроса и до подтверждения» — manual.txt:2374-2379. And: «Модуль gateway направляет новый запрос прерывания в ядро контроллера PLIC только после получения уведомления о том, что обработка предыдущего запроса прерывания из того же источника, завершена» — manual.txt:2507-2509.
  ⇒ If SW never writes complete, that source can never fire again — a hard per-source "no re-entry / never preempt" guarantee (other sources unaffected). Deliberate deferral of the MICC write is a legal way to hold off further GPIO IRQs during a USB frame.
- Pending (IP) bit cleared by claim («Start of interrupt») — manual.txt:2505-2507.
- Claim/complete (§9.5, manual.txt:2549-2553): claim = READ MICC (returns source number, signals start-of-interrupt); complete = WRITE MICC (source number). Minimal ISR cost = 1 load + 1 store to 0x0C20_0004 (system bus / MMR space; РП does not specify per-access cycle cost → GAPS). Note: it is 1 load + 1 store, not "2 loads + 1 store".
- Poll model officially allowed (§9.5, manual.txt:2554-2563): SW may read IPM0 and/or claim by reading MICC without trapping. Caveat: «чтение IPM не гарантирует, что последующее чтение ICC вернет прерывание с тем же номером» (ICC may return 0 = none) — manual.txt:2560-2563.
- Skipping claim/complete and only poking GPIO INTSTATUS: NOT viable when the IRQ is delivered as a trap. Pending clears only via claim; gateway+IP keep mip.MEIP asserted, so the trap re-enters after MRET. No bypass documented. (Inference from manual.txt:2374-2379, 2505-2509 — not a quoted РП statement.)
- Ordering caution (inference): GPIO INTSTATUS is W1C and NOT cleared by HW (РП §11.4 manual.txt:4040-4043; INTSTATUS reg, offset +80h, manual.txt:17910-17927). Clear INTSTATUS before writing completion to MICC, else the still-asserted GPIO line re-triggers the edge-sensitive IGW after completion.
- Vector table (РП Table 9.1, manual.txt:2421-2454): 1 WDT, 2/3 CAN0/1, 4 USB, 5 GPIO «Прерывания портов GPIO», 6 TMR32, 7-9 TMR0-2, 10 QSPI, 11/12 SPI0/1, 13-20 DMA, 21 I2C, 22-26 UART0-4, 27 CRYPTO_HASH_CRC, 28 TRNG, "28" ADC (misprint; = 29 per SDK), 30 CMP, 31 PMU_RTC.
  - GPIO: ONE shared vector 5 for all ports. РП: table says «портов» (plural) and GPIO chapter §11.4 says a pin interrupt «выставляется прерывание в контроллере прерываний PLIC» (manual.txt:4040-4041) with no per-port vectors. SDK: single `IsrVect_IRQ_GPIO = 5` (include/K1921VG015.h:46), `PLIC_GPIO_VECTNUM 5` (include/plic.h:28); both boards' bsp.h map buttons to it (hardware/bsp/NIIET-MINI-K1921VG015/bsp.h:44). Contrast: sibling K1921VG3T has per-port vectors GPIOA..G = 6..12 (Device/K1921VG3T/Include/K1921VG3T.h:47-53) — VG015 deliberately merged them.
- §9.3 "Цикл прерывания" mentions a per-source MODE register (manual.txt:2398-2402) but no MODE register exists in Table 9.2; SDK has PLIC_SetMode commented out referencing nonexistent `PLIC->SRC_MODE` (plic.c:72-75). Generic CloudBEAR boilerplate; actual sources are edge-only via IGW.

## 5. Masking / SDK GPIO IRQ enable path

- Per-source enable = PLIC MIEM0 bit (source n = bit n) — РП manual.txt:2517-2524 (reset = all 0; set 1 to enable; recommended to enable only existing sources).
- SDK enable chain (platform/Device/K1921VG015/source/plic.c):
  - PLIC_SetPriority: `PLIC->PRI[isr_num] = pri` — plic.c:63-66
  - PLIC_IntEnable: read-modify-write `PLIC->MIEM0 |= 1<<isr_num` — plic.c:81-96
  - SetIrqHandler(vect, handler, pri) = SetIrqHandler + SetPriority + IntEnable — plic.c:98-103
  - Dispatch: trap_handler (plic.c:191-228) → PLIC_MachHandler (plic.c:165-176): claim = read MICC (plic.c:130-137), call table handler, complete = write MICC (plic.c:143-150). Note plic.c:170-175: completion written ONLY if a handler exists — a spurious/unhandled source claims but never completes and its gateway locks (SDK quirk, useful to know).
  - Global side: mie.MEIE via csr_set_bits_mie, mstatus.MIE via csr_set_bits_mstatus — source/riscv-irq.c:18-42.
  - GPIO-side: pin unmask via GPIO INTENSET, config INTTYPESET/INTPOLSET/INTEDGESET — РП §11.4, manual.txt:4035-4057.
- Latency jitter / bus stalls: nothing anywhere in РП, osobennosti.txt, or quickstart.txt about interrupt latency or its jitter → GAPS. Only indirect jitter sources documented: flash prefetch misses (see §6), CSR ops draining the pipeline (manual.txt:2168-2169), D-code priority over I-code on flash (manual.txt:2014-2015).

## 6. TCM / RAM0 / FLASH execution

- Memory map (РП Table 6.1, manual.txt:1936-1944): FLASH 1MB @0x8000_0000; RAM0 256KB @0x4000_0000; RAM1 64KB @0x1000_0000 (battery domain); peripherals 0x2000_0000-0x3801_FFFF; PLIC 0x0C00_0000; CLINT 0x0200_0000. Matches SDK ldscripts (Device/K1921VG015/ldscripts/k1921vg015_flash.ld:8-12) and K1921VG015.h:83-92.
- TCM: «Младшие 128 Кбайт ОЗУ0 подключены к интерфейсу TCM-A, а старшие 128 Кбайт - к TCM-B. Для получения максимальной производительности при исполнении из ОЗУ0 рекомендуется переменные и исполняемый код располагать в регионах, подключенных к разным интерфейсам TCM» — Table 6.1 note, manual.txt:1945-1948. ⇒ TCM-A = 0x4000_0000..0x4001_FFFF (code here), TCM-B = 0x4002_0000..0x4003_FFFF (data here), or vice versa. РП does NOT explicitly say "0 wait states" for TCM fetch → GAPS (the recommendation implies contention is the only penalty, but that is inference).
- SDK ships a pure-RAM0 link script (k1921vg015_ram.ld:14-16 REGION_TEXT=RAM0) — running the whole stack from RAM0 is a supported configuration.
- FLASH execution path: two AHB buses, I-code (instructions) + D-code (data), D-code has priority — РП §7.1, manual.txt:2014-2015.
- FLASH wait states (РП §7.1): array read time 30-60 ns → extra wait states, Table 7.1 (LDO1=1.2 V): fSYSCLK ≤60 MHz → 1 WS; ≤30 MHz → 0 WS; reset value = 1 — manual.txt:2035-2043. (LDO1=0.9 V, Table 7.2: ≤60→3, ≤45→2, ≤30→1, ≤15→0 — manual.txt:2045-2055.) ⇒ at 48 MHz / 1.2 V: 1 WS required (= reset default). LAT field = FLASH->CTRL bits 18-16 (manual.txt:16222); SDK sets LAT=3 in PLL path (overly conservative) — source/system_k1921vg015.c:242.
- FLASH prefetch (РП §7.1 "Операция предвыборки", manual.txt:2065-2083): miss → bus stalled, 128-bit line (4 words) filled into buffer 1; next line speculatively into buffer 2; hits in either buffer answered «мгновенно»; jump outside both buffers → stall + refill. Flash bus width 16 words per K1921VG015.h:84 (MEM_FLASH_BUS_WIDTH_WORDS 16 — inconsistent with РП's 4-word description; trust РП for the 128-bit line, see GAPS).
  ⇒ For cycle-exact bitbang at 48 MHz from flash: straight-line code mostly 0-stall, but every ISR entry (jump to trap_entry) and every branch out of the 32-byte buffered window eats a nondeterministic flash-line refill. Critical RX/TX loops belong in RAM0/TCM.

## GAPS (no local source; do not state as fact)

1. mtvec vectored mode: РП silent; SDK only ever uses direct mode. Whether BM-310S6 implements MODE=1 is unknown from local sources (CloudBEAR core databook needed).
2. Trap entry latency (IRQ assert → first handler instruction) in cycles: not documented anywhere local. Same for MRET cycle count and mstatus.MIE/MPIE swap detail.
3. Taken-branch penalty, load/store extra cycles, bus arbitration cost for AHB/MMR accesses (incl. PLIC MICC claim read cost): not documented.
4. TCM "0 wait state" fetch: implied by "TCM" naming and the split-A/B performance note, but never stated numerically.
5. РП interrupt table row "28 ADC" conflicts with "28 TRNG": misprint; SDK says ADC=29 (K1921VG015.h:70) — treat SDK as authoritative here.
6. Flash prefetch line size: РП says 4x32-bit (128 bit) per buffer (manual.txt:2069), SDK header says bus width 16 words (K1921VG015.h:84). Unresolved contradiction.
7. Behavior of IGW when the source line is still high at completion (immediate re-request or need of a new edge): §9.2 text is ambiguous (edge-sensitive capture vs level wording in manual.txt:2374-2379, 2509-2516). Safe rule: clear GPIO INTSTATUS before MICC completion.
8. Whether mip.MEIP can be cleared without claiming (e.g. masking in MIEM0 mid-pending): not documented.
