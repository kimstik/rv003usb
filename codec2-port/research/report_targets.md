# TARGETS Report: Candidate MCU Capabilities and Arithmetic Cost Model

Confidence labels: **VERIFIED** = read from vendor datasheet/RM or measured first-party source; **REPORTED** = credible secondary source; **ESTIMATE** = derived, assumptions stated.

## 1. Chip-by-chip capability audit

### 1.1 Puya PY32F003 (Cortex-M0+)

| Item | Value | Status | Source |
|---|---|---|---|
| Core | Arm Cortex-M0+, "single-cycle multipliers" explicitly stated | VERIFIED | [PY32F003 Datasheet Rev1.7 EN](https://download.py32.org/Datasheet/en/PY32F003_Datasheet_Rev1.7.pdf) |
| Max clock | **32 MHz** (24 MHz on MSOP10 variant) — the 48 MHz sibling is PY32F030 | VERIFIED | same |
| Variants | x4 = 16K flash/**2K** SRAM; x6 = 32K/**4K**; x8 = 64K/**8K** (x7 48K/6K also appears) | VERIFIED | same, Tables 1-1..1-3 |
| Flash wait | 0 WS ≤24 MHz; **1 WS above** | VERIFIED | [PY32F003 RM V1.1](https://www.puyasemi.com/download_path/%E7%94%A8%E6%88%B7%E6%89%8B%E5%86%8C/MCU%20%E5%BE%AE%E5%A4%84%E7%90%86%E5%99%A8/PY32F003_Reference_Manual_V1.1.pdf) §4.2.2 |
| Price | ~$0.08–0.20 | REPORTED | [Jay Carlson](https://jaycarlson.net/2023/02/04/the-cheapest-flash-microcontroller-you-can-buy-is-actually-an-arm-cortex-m0/) |

`MULS` is 32×32→**32** only (no `UMULL` in ARMv6-M); 32×32→64 = 4 partial 16×16 products, ~17–25 cycles (ESTIMATE).

### 1.2 WCH CH32V003 (QingKe V2A, RV32EC)

- QingKe V2A, RV32EC + XW compressed, 48 MHz, **2 KB SRAM / 16 KB flash**, ~$0.10 — VERIFIED ([DS V1.8](https://ch32-riscv-ug.github.io/CH32V003/datasheet_en/CH32V003DS0.PDF)).
- **No hardware multiply/divide** — VERIFIED ([QingKe V2 manual](https://ch405-labs.com/content/files/2023/11/QingKeV2_Processor_Manual.PPDF) lists V2A as RV32EC only; `mul` traps).
- Flash: 0 WS ≤24 MHz, **1 WS 24–48 MHz** — VERIFIED ([RM V1.9](https://ch32-riscv-ug.github.io/CH32V003/datasheet_en/CH32V003RM.PDF) §16.3.1).
- **`__mulsi3` cost (ESTIMATE)**: 16×16→32 ≈ **100–160 cycles**; 32×32→32 ≈ 200–300; 32×32→64 ≈ 400–600. Sustained MAC ≈ **0.3–0.45 M/s** at 48 MHz.

### 1.3 WCH CH32V002/V004/V005/V006/V007 (QingKe V2C, RV32EmC)

- **V2C = RV32EmC**: "the 'm' extension in EmC implements the **multiplication subset** of the M extension" (mul/mulh family, **no divide**) — VERIFIED ([CH32V002 DS V1.7](https://ch32-riscv-ug.github.io/CH32V006/datasheet_en/CH32V002DS0.PDF), [CH32V00X RM](https://ch32-riscv-ug.github.io/CH32V006/datasheet_en/CH32V00XRM.PDF)). Mul latency undocumented; assume 1–2 cycles (ESTIMATE).
- CH32V002: 48 MHz, **4 KB/16 KB**. CH32V006: 48 MHz, **8 KB/62 KB** ([LCSC](https://www.lcsc.com/product-detail/C52753342.html) ~$0.15–0.20).
- Flash wait (V00x family): **0 WS ≤15 MHz, 1 WS ≤24 MHz, 2 WS 24–48 MHz** — VERIFIED. Worse per-clock fetch than V003 at 48 MHz.

### 1.4 WCH CH32X035 — QingKe **V4C, RV32IMAC** (full hw mul+div), 48 MHz, **20 KB/62 KB** — VERIFIED ([DS V2.1](https://ch32-riscv-ug.github.io/CH32X035/datasheet_en/CH32X035DS0.PDF)). ~$0.25–0.40 (ESTIMATE).

### 1.5 WCH CH32V203 — QingKe **V4B, RV32IMAC, 144 MHz**, **20 KB/64 KB** (C8/F8), ~$0.40 — REPORTED ([SoCXin](https://github.com/SoCXin/CH32V203), [cpldcpu decap](https://cpldcpu.com/2024/05/01/decapsulating-the-ch32v203-reveals-a-separate-flash-die/)). Two-die construction: zero-wait from the cached region, community-measured **~10× slower** uncached. 224 KB physical code flash on the flash die.

### 1.6 WCH CH570/CH572 — QingKe **V3C, RV32IMBC** (hw mul, div, bitmanip), up to **100 MHz**, **12 KB SRAM**, 240 KB usable code flash — VERIFIED/REPORTED ([openwch/ch570](https://github.com/openwch/ch570), [CNX](https://www.cnx-software.com/2025/04/02/10-cents-wch-ch570-ch572-risc-v-mcu-features-2-4ghz-wireless-bluetooth-le-5-0-usb-2-0/), [EEVblog](https://www.eevblog.com/forum/microcontrollers/wch-new-10c-ch570-rv32imbc-mu-mode-100-mhz-12k-ram-240k-flash-usb-2-4-ghz-radio/)). CH572 = CH570 + **BLE 5.0** (broadcaster/peripheral only). Prices: **CH570Q $0.146, CH572Q $0.202, CH572D $0.218** ([LCSC](https://www.lcsc.com/product-detail/C49260680.html)). Flash wait at 100 MHz undocumented; family pattern suggests hot code belongs in SRAM — ESTIMATE.

### 1.7 WCH CH582 / CH592

- CH582: QingKe **V4A**, RV32IMAC, Fsys **15–80 MHz**, **32 KB SRAM**, **448 KB CodeFlash**, BLE 5.3 — VERIFIED ([CH583/582/581 DS V1.6](https://static.chipdip.ru/lib/393/DOC047393310.pdf)). "Basically zero wait at 20 MHz" flash; power specs assume code-in-RAM.
- CH592: QingKe **V4C**, RV32IMAC, 15–80 MHz, **26 KB SRAM**, 448 KB, BLE 5.4 — VERIFIED ([CH592 DS V1.7](https://285624.selcdn.ru/syms1/iblock/be3/be340ddc1d1bcbae9bfaa2c0766346d1/CH592F-Datasheet.pdf)). Secondary claims of "60 MHz max" are wrong — datasheet Fsys range wins.
- Prices ~$0.5–1.0 — ESTIMATE.

## 2. Arithmetic cost model

| Operation | M0+ 1-cyc mul (PY32) | RV32EC no mul (V003) | RV32EmC (V002/006) | RV32IMAC/IMBC |
|---|---|---|---|---|
| 16×16→32 | 1 | ~100–160 soft (EST) | 1–2 (EST) | 1–2 (EST) |
| 32×32→64 | ~17–25 (EST) | ~400–600 (EST) | 2–4 mul+mulh (EST) | 2–4 (EST) |
| Q15 MAC inner loop | ~8–10 | ~120–180 | ~7–10 | ~6–8 |
| Divide | soft | soft, very slow | soft | hardware |
| Soft-float add/mul/div | gcc 102/166/475; Qfplib 76/62/83 — VERIFIED ([Qfplib](https://github.com/mysterywolf/Qfplib-M0-full)) | ~400–700/op (EST) | ~90–200/op (EST) | ~60–150/op (EST) |

Sustained 16-bit-MAC throughput (incl. wait states, ESTIMATE): V003 **~0.35 M/s**; PY32F003 @32 MHz **~3.5 M/s**; V002/V006 **~5 M/s**; X035 **~7 M/s**; CH582/592 **~8–11 M/s** (code in RAM); CH570/572 **~12–14 M/s** (hot code in SRAM); CH32V203 **~20+ M/s**.

**256/512-pt int16 real FFT** (radix-2; Q15 butterfly = 4 mul + 6 add/sub + ~8 mem ops):
- 1-cycle-mul cores: ~22–35 cyc/butterfly → **256-pt ≈ 13–25 k cycles, 512-pt ≈ 30–55 k** (0.3–1.7 ms).
- CH32V003 soft-mul: ~550–700 cyc/butterfly → **512-pt ≈ 600–750 k cycles (12–16 ms) @48 MHz** — over the 10 ms frame budget on FFTs alone.

**Whole-codec calibration (measured):**
- STM32F405/446 @168–180 MHz M4F: full codec2 + modem realtime half-duplex — VERIFIED (mailing list).
- **talkberry** ([hrvach/talkberry](https://github.com/hrvach/talkberry), RP2040 M0+ @125 MHz): float codec2-1300 **3.542 s/s** (3.5× too slow); fixed-point **0.473 s/s**, **11,396 B RAM**, 380 KB flash — VERIFIED.
- Normalized requirement: full fixed-point codec ≈ **59 MHz-equivalents** of 1-cycle-mul in-order CPU; decode-only ≈ 25–30; float ≈ 440.

## 3. Memory reality check

State ~2–4 KB + scratch ~4–8 KB (512-pt Q15 FFT ≈ 2–3 KB) + tables (trimmed 1300-only build ~40–80 KB flash ESTIMATE; talkberry full binary 380 KB VERIFIED). RAM verdicts: 2 KB & 4 KB excluded; 8 KB decode-only marginal; 12 KB just fits full 1300 (zero slack, no BLE stack alongside); 20 KB+ comfortable. Flash: 16 KB parts excluded on tables alone (for a stock port); 62–64 KB tight single-mode; 240–448 KB comfortable.

## 4. Toolchain

- CH32V003: `-march=rv32ec_zicsr -mabi=ilp32e`, GCC ≥12 (xPack riscv-none-elf-gcc). ilp32e = 16 regs → fewer accumulators before spilling in MAC kernels.
- V2C parts: `-march=rv32ec_zmmul_zicsr` (Zmmul, GCC ≥13) or WCH MounRiver GCC — REPORTED; verify multilib.
- IMAC parts: stock `rv32imac/ilp32`.
- Naive float fallback nonviable everywhere: 50–100× short on V003, ~3–4× short even at 100–144 MHz IMAC (talkberry scaling). Qfplib halves gcc's soft-float cost but doesn't close the gap.

## 5. Final comparison table

| Chip | Core | MHz | mul? | RAM | Flash | Est. 16-bit MAC | codec2 verdict |
|---|---|---|---|---|---|---|---|
| CH32V003 | V2A RV32EC | 48 | **No** | 2 K | 16 K | 0.35 M/s | **Excluded for a standard port** (RAM+flash+MAC each fatal) |
| PY32F003x4/x6 | M0+ | 32 | 1-cyc | 2/4 K | 16/32 K | 3.5 M/s | **Excluded** (memory) |
| PY32F003x8 | M0+ | 32 | 1-cyc | 8 K | 64 K | 3.5 M/s | Decode-only, marginal |
| CH32V002 | V2C RV32EmC | 48 | mul, no div | 4 K | 16 K | 5 M/s | **Excluded** (memory) |
| CH32V006 | V2C RV32EmC | 48 | mul, no div | 8 K | 62 K | 5 M/s | Decode-only plausible |
| CH32X035 | V4C RV32IMAC | 48 | mul+div | 20 K | 62 K | 7 M/s | Half-duplex viable |
| CH570/572 | V3C RV32IMBC | 100 | mul+div+B | 12 K | 240 K | 12–14 M/s | **Viable, best $/perf** ($0.15–0.22); RAM is the constraint; BLE+codec won't coexist |
| CH32V203 | V4B RV32IMAC | 144 | mul+div | 20 K | 64 K (0-wait cached) | 20+ M/s | **Comfortable — recommended primary** (~$0.40) |
| CH582 | V4A RV32IMAC | 80 | mul+div | 32 K | 448 K | 8–11 M/s | Comfortable; BLE 5.3 + codec fits; DSP from SRAM |
| CH592 | V4C RV32IMAC | 80 | mul+div | 26 K | 448 K | 8–11 M/s | Comfortable; BLE 5.4 |
