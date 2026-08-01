# CloudBEAR BM-310S6 core research (beyond K1921VG015 РП)

Status: IN PROGRESS (2026-07-31). Goal: characterize the core itself — I-cache ownership,
pipeline timing, mtvec modes, TCM semantics, ISA extensions — from CloudBEAR + sibling-MCU sources.

## 1. BM-310 family overview / configuration options
Source: cloudbear.ru/bm_310.html (product page, fetched 2026-07-31; RU mirror cloudbear.ru/ru/bm_310.html):
- Pipeline: «2-3 стадийный конвейер» — configurable 2 or 3 stages. Perf: 1.81/1.72 DMIPS/MHz, 4.2/3.71 CoreMark/MHz (2-stage/3-stage figures).
- ISA configurable: I + M, C, A, F, D, **N**, B (битовые операции), K (crypto incl. GOST Kuznechik/Stribog/Magma), P (DSP).
- Memory: «Настраиваемый диапазон адресов для TCM-памятей» (configurable TCM address ranges); **«Опциональный кэш инструкций»** (optional I-cache) with «настраиваемое количество каналов» (ways) and «настраиваемый размер кэш линии» (line size). Search snippet: "customizable instruction and data cache".
- Interrupts: PLIC, CLINT, **CLIC**, NMI — all listed as core options.
- Security: up to 64 PMP regions, ECC (SEC-DED), Smepmp.
- No public databook/PDF links on page — cycle tables not published openly.

## 2. I-cache: core option vs NIIET flash controller
- CloudBEAR: BM-310 has an **optional instruction cache as a core configuration option** («Опциональный кэш инструкций», configurable ways + line size) — cloudbear.ru/bm_310.html.
- NIIET attributes the I-cache to the CORE: «ядро ... с ... FPU, кэшем команд и поддержкой отладочного интерфейса JTAG» (РП manual.txt:367-370; same wording quickstart.txt:50). Marketing: «Cache-I 2 кБайт» listed under «Параметры процессорного ядра» (osobennosti.txt:33-36).
- Sibling check: Milandr К1986ВК025 (BM-310S, no cache option taken) — its core chapter has **no cache at all** (TCM + AHB I/O only), and its flash controller has **no cache either**, only a 64-bit prefetch + Delay waits (spec ТСКЯ.431296.023СП p.50 §10.1). So a cacheless BM-310 exists; cache is a per-licensee config.
- **Verdict: the 2 KB Cache-I is the CloudBEAR core option (in the core's PMS fetch path), NOT the flash controller** (confidence: high). The NIIET FLASH->CTRL.CEN "cache enable" bit is a separate NIIET-side flash-accelerator control (РП documents 2x128-bit prefetch buffers in the flash chapter with no enable bit; CEN/CFLUSH exist only in SDK header/SVD — see research_flash.md).
  - Caveat (residual uncertainty): no NIIET doc states which unit CEN gates; conceivably NIIET placed the 2KB cache in the flash path and РП §2 wording is loose. Against this: РП core-feature list groups it with FPU/JTAG (core attributes), and CloudBEAR sells exactly such an option. FENCE.I is implemented (РП Табл. 8.1: «Синхронизация потока подкачки команд и потока чтения/записи данных», manual.txt:2284-2286) — consistent with a real I-cache/fetch-queue needing sync.
- No cache-management CSRs found: SDK csr.h/riscv-csr.h contain zero custom CSRs (niiet_riscv_sdk platform/Device/K1921VG015/include/csr.h, riscv-csr.h — grepped 0x7xx/0xbxx custom ranges, none). SDK startup cache hooks (`PLF_CACHE_CFG`, `cache_flush`) are **Syntacore-derived boilerplate** (arch.h:55-56 defines `SCR_CSR_MPU_BASE 0xbc4` — Syntacore SCR naming; same tree serves K1921VG3T with SCR1 core) — not evidence of BM-310 cache CSRs. => Only architectural control is FENCE.I. No enable/bypass/lockdown documented anywhere (GAP).

## 3. Pipeline timing (branch/load/store/jump/IRQ entry/mret)
Source: cnx-software.com/2020/10/20/bm-310-risc-v-mcu-core-iot-applications/ (based on CloudBEAR's own RISC-V Global Forum 2020 material). BM-310 pipeline is a **configurable menu of 4 options**:
- **Option 1 (max efficiency): 2-stage, ZERO-cycle branch penalty**, no branch prediction. "Pipeline option (1) is 19% faster than the reference machine that is Cortex-M4".
- Option 2: 2-stage-ish, **1-cycle branch penalty**, adds branch prediction, higher fmax.
- Option 3: 3-stage, **load-to-use latency = 2 cycles**.
- Option 4 (max perf): **2-cycle branch penalty, 2-cycle load-to-use**.
- K1921VG015 РП says its BM-310S6 is 2-stage (РП §3/§8) => it is pipeline Option 1 or 2; i.e. taken-branch cost is 0 or 1 extra cycle by design. Which one — not stated anywhere (GAP; measure).
- Load-to-use in 2-stage options: implied ~1 cycle (options 3/4 are called out as *increasing* it to 2) — inference, not a quoted number (GAP for exact value; TCM vs AHB not broken out).
- No public numbers for: store cost, jal/jalr, IRQ entry latency, MRET cost — GAP.

## 4. mtvec modes / CLIC
- Milandr К1986ВК025 spec, Табл. 480 «Регистр базового адреса вектора прерывания mtvec»: MODE[1:0]: «0 – все прерывания устанавливают PC в значение BASE; **1, 2, 3 – зарезервированные комбинации бит MODE**» (ТСКЯ.431296.023СП, CSR chapter). => Milandr's BM-310S implements **direct mode only; vectored (MODE=1) reserved/absent**.
- NIIET РП: `mtvec` is **never documented** (grep of full manual.txt: zero hits); SDK uses direct mode only (startup_k1921vg015.S:35-37); habr.com/ru/articles/883220/ review states no vector-table support.
- CloudBEAR product page lists CLIC as a core OPTION, but K1921VG015 instantiates PLIC+CLINT (РП §9, memory map 0x0C00_0000 PLIC / 0x0200_0000 CLINT, manual.txt:1941-1943) — no CLIC.
- **Verdict: assume mtvec direct-only on BM-310S6 (vectored reserved), single trap entry + software dispatch** (confidence: medium-high — proven for sibling BM-310S, unprovable for S6 without HW test: write mtvec.MODE=1, read back).

## 5. TCM-A / TCM-B semantics
- K1921VG015: RAM0 256K split — lower 128K on TCM-A, upper 128K on TCM-B; «Для получения максимальной производительности при исполнении из ОЗУ0 рекомендуется переменные и исполняемый код располагать в регионах, подключенных к разным интерфейсам TCM» (РП Табл. 6.1 note, manual.txt:1945-1948). => Two independent TCM ports; contention only when code and data hit the SAME port; the stated cure is placement, implying same-port I+D access costs stall cycles (magnitude undocumented — GAP).
- Milandr sibling detail (BM-310S): «Интерфейс TCM разделен на две части: интерфейс PMS и интерфейс DMS. Ширина шин адреса интерфейса TCM 14 бит, адресация пословная, поддерживается память размером до 128КБ. Ширина шин данных равна 32 бита» (ТСКЯ.431296.023СП p.64-65). => Per-port limit 128 KB (14-bit word address) — explains NIIET's 2x128K split; 32-bit data per port; fetch fragment = 1-2 instructions (32-bit fetch, i.e. 2 RVC or 1 full insn per cycle — no wide/dual fetch).
- «Выполнение всех команд RV32IMC занимает один такт» (both РП manual.txt:2156 and Milandr spec) — implies 0-wait TCM load/store and 1-cycle fetch from TCM.
- AHB side (both docs, near-identical CloudBEAR text): strong ordering — «следующий запрос на AHB шине не будет выставлен, до окончания исполнения текущего»; AHB error => access fault; code execution from AHB/I-O range allowed (Milandr p.64; NIIET flash is on the PMS side behind the 2KB I-cache + flash prefetch).

## 6. ISA: "N" extension, bitmanip (Zba/Zbb/Zbc/Zbs)
- РП states the full ISA: **RV32IMFCN_ZBA_ZBB_ZBC_ZBS** (manual.txt:366-367, §8 intro manual.txt:2131; osobennosti.txt:35). => **Zba/Zbb/Zbc/Zbs ARE implemented in silicon** even though the SDK compiles rv32imfc only. For the bitbang stack: `-march=rv32imfc_zba_zbb_zbs` (+_zbc) should work; verify with a probe instruction (e.g. `andn`) on HW — illegal-instruction trap = not present (low risk).
- "N" = user-level interrupt CSRs (ustatus/uie/utvec...), a ratification-abandoned RISC-V extension; CloudBEAR lists N as a core option (cnx-software.com BM-310 article: "user-level interrupts (N)"). Machine-mode-only bare-metal code (our case) never touches it; only implication is the core has M+U privilege modes (РП manual.txt:2132-2134). No impact on timing.
- FPU: F only, single precision (РП manual.txt:369); note FPU fdiv/fsqrt-from-flash erratum (research_errata.md item 5).
- Perf claims: NIIET «1.35 DMIPS/MHz» (osobennosti.txt:38) vs CloudBEAR product page 1.81/1.72 DMIPS/MHz — NIIET's config/measurement is notably lower (flash waits? compiler?) — unexplained (minor GAP).

## 7. Sibling implementations (Milandr MDR32F02/К1986ВК025, ELIOT-1, ...)
### Milandr MDR32F02FI = К1986ВК025 (electricity-meter MCU) — BEST public BM-310 doc
- Spec PDF: support.milandr.ru/upload/iblock/90a/.../К1986ВК025.pdf (ТСКЯ.431296.023СП, 475 pp; local copy: scratchpad/k1986vk025.pdf). Core chapter «Процессорное ядро BM-310S» p.63-65.
- Variant named just "BM-310S" (no S4/S6 suffix anywhere in the spec — the "S4" belief is UNCONFIRMED). RV32IMC, 60 MHz, **3-stage pipeline** («Конвейер BM-310S состоит из трех стадий»: 1 fetch req; 2 read+decode; 3 execute), FE = IFU+IDU+**RAS** (return address stack — present here, absent in NIIET's 2-stage S6 which has IFQ instead).
- Timing (Табл. 26, identical numbers to NIIET РП): all RV32IMC 1 cycle; MUL/MULH* = 2; DIV/REM = 2..16; CSR ops serialize («исполняются в пустом конвейере»). No branch/load penalty table either.
- **No I-cache, no D-cache** in this instantiation; flash: 30 ns array, ~30 MHz native, 64-bit over-fetch («извлекаются избыточные 4 байта»), Delay[2:0]: 0 ws <=30 MHz, 1 ws 30-60 MHz; branch to un-prefetched address = «пауза в несколько тактов» (spec §10.1 p.50). Milandr habr article habr.com/ru/company/milandr/blog/518138/: 3.0 CoreMark/MHz, ~Cortex-M3 class, core area 0.3 mm2, mul 2 такта.
- mtvec: direct-only, MODE 1/2/3 reserved (Табл. 480) — see §4.
### НИИМА Прогресс ELIOT-1 — believed CloudBEAR core: NOT CONFIRMED here (no public core-level doc found in this pass) — GAP.
### K1921VG3T (NIIET's other RISC-V): uses Syntacore SCR1, not CloudBEAR (SDK plf.h `PLF_CORE_VARIANT_SCR1`, niiet_riscv_sdk/platform/Device/K1921VG3T/Include/plf.h:11-13) — explains Syntacore boilerplate leaking into VG015 SDK; do not treat SDK `SCR_*`/cache macros as BM-310 evidence.

## 8. -mfix-cloudbear-0001 (FPU erratum) — pipeline internals
TBD

## GAPS (hardware-measure-only)
TBD

## Sources
TBD
