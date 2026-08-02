# К1921ВГ015 — clock tree & PLL research (48 MHz exact configs)

Date: 2026-07-31. Sources: РП К1921ВГ015 (19.02.2025) = `manual.txt` (line refs) / `manual.pdf` (page refs); NIIET SDK (`niiet_riscv_sdk`); NIIET app notes (`osobennosti.txt`, `quickstart.txt`).
Rule: every claim cites source; no source => GAPS.

## 1. PLL formula & knob ranges

РП §4.2 «Синтезатор частоты PLL» (manual.txt:1369-1428):

- Input: «В качестве опорной частоты fREF блока PLL используется PLLSYSREFCLK» (manual.txt:1374).
- Formula 4.1 (manual.txt:1381-1386):
  `fOUT0 = (fREF / REFDIV) * (FBDIV + FRAC/2^24 * DSMEN) / ((1+PD0A)*(1+PD0B))`
- Formula 4.2 (manual.txt:1388-1393): same with PD1A/PD1B for fOUT1.
- Formula 4.3 (manual.txt:1395-1399): `fVCO = fREF * (FBDIV + FRAC/2^24 * DSMEN) / REFDIV`.
- DSMEN = delta-sigma modulator enable (fractional mode), DACEN = «ЦАП с дробным шумоподавлением в режиме дробного делителя» (manual.txt:1375-1377).
- Constraints, quoted verbatim (РП §4.2, manual.txt:1402-1411):
  - «1 ≤ REFDIV ≤ 63»
  - integer mode: «(без дробного делителя) 16 ≤ FBDIV ≤ 160»
  - fractional mode: «(с дробным делителем) 20 ≤ FBDIV ≤ 160»
  - «0 ≤ PD0A (PD1A) ≤ 7» (divide 1–8)
  - «0 ≤ PD0B (PD1B) ≤ 63» (divide 1–64)
  - «входная частота fREF должна находиться в диапазоне от 10 МГц до 30 МГц» — **PLL input (PLLSYSREFCLK) must be 10–30 MHz**. 8 MHz HSE cannot feed the PLL legally; HSI (1 MHz) cannot either.
  - «значение частоты fVCO должно быть в диапазоне (200 – 1600) МГц» — **VCO 200–1600 MHz; 576 MHz VCO is legal** (well inside range).
  - «значение выходной частоты fOUT должно быть в диапазоне 390 кГц – 60 МГц».
- Note (manual.txt:1412-1413): «Настоятельно рекомендуется максимизировать значение PD0A в паре делителей PD0A, PD0B» (same for PD1A/PD1B) — put the larger factor in *A.
- Setup order (РП §4.2, manual.txt:1417-1428): configure PLL before selecting it as a clock; clear BYP in PLLSYSCFG0; set REFDIV+FBDIV; if fractional — set DSMEN and write FRAC to PLLSYSCFG1, else DSMEN must be 0; then set PD*A/PD*B, then FOUTEN* and PLLEN; «При правильной установке всех значений и выходе блока PLL на рабочий режим будет установлен бит LOCK в регистре PLLSYSSTAT» — lock indicated by LOCK bit, **no numeric lock time given in §4.2**.
- Fractional-mode jitter: РП §4.2 gives **no jitter number for the SYSPLL fractional mode**. The often-quoted phrase «джиттер периода, не превышающий одного периода FIN» is from РП §26 (CAN clock fractional divider, formula 26.3 `fFOUT = fFIN * STEP/1024`), manual.txt:10727-10730: «сигнал может иметь джиттер периода, не превышающий одного периода FIN, в связи с чем, не рекомендуется использовать режим дробного деления при высоких скоростях передач». It describes the CAN divider, NOT the PLL. Do not attribute it to SYSPLL.

### 1.1 PLL register fields (РП Приложение А.1, RCU base)

- `PLLSYSCFG0` (+50h, reset 0): PD1B[30:25], PD1A[24:22], PD0B[21:16], PD0A[15:13], REFDIV[12:7], FOUTEN[6:5] (per-output enable), DSMEN[4], DACEN[3], BYP[2:1] (per-output bypass FREF->out), PLLEN[0] (manual.txt:15009-15026).
- `PLLSYSCFG1` (+54h): FRAC[23:0] (manual.txt:15033-15041).
- `PLLSYSCFG2` (+58h): FBDIV[11:0] (manual.txt:15044-15052).
- `PLLSYSCFG3` (+5Ch, reset 0): REFSEL[24] «0 REFCLK / 1 SRCCLK», plus DSKEW* calibration fields (manual.txt:15059-15122).
- `PLLSYSSTAT` (+60h): LOCK[0] «Устанавливается, если выходная частота блока PLLSYS стабильна» (manual.txt:15125-15134).

### 1.2 PLL reference mux — РП vs SDK CONTRADICTION (critical)

Figure 4.1 (manual.pdf p.24, rendered): HSI(1 MHz) -> REFMUXCLK -> mux(sel=PowerDown controller; in1=RTCCLK) -> **REFCLK**; second mux «PLLSYSCFG_REG REFCLKMUX»: in0 = REFCLK, in1 = **HSECLK** -> PLLSYSFREFCLK -> SYS PLL. So per РП, SRCCLK = HSECLK and **REFSEL=1 is required to feed the PLL from HSE**; REFSEL=0 gives HSI-derived REFCLK (1 MHz — violates the 10–30 MHz PLL input constraint).
BUT: no SDK code ever writes REFSEL (grep over whole SDK: REFSEL appears only in headers/SVD; `system_k1921vg015.c` ClkInit and `plib015_rcu.c:398-404` never touch PLLSYSCFG3), yet SDK computes/gets 50 MHz from HSE with PLLSYSCFG3 at reset value 0 (works on real boards per NIIET examples). => Either the РП reset value (0h, manual.txt:15062) or the figure's mux polarity is wrong, or hardware defaults differ. **Port action: verify on hardware; do not blindly set REFSEL=1.**

### 1.3 Lock time / fractional jitter

- Lock: only «будет установлен бит LOCK в регистре PLLSYSSTAT» (manual.txt:1427-1428). **No numeric lock time anywhere in РП** (searched LOCK/захват/время). SDK waits a ~1000-iteration dummy loop then polls LOCK forever (`system_k1921vg015.c:233-237`).
- Fractional jitter: no number for SYSPLL (see §1 last bullet — the famous quote is about the CAN divider).

## 2. Verified-legal exact-48 MHz integer configs

All integer mode (DSMEN=0, FRAC=0). Constraint set from §1: fREF(HSE) in [10,30] MHz, REFDIV in [1,63], FBDIV in [16,160], fVCO in [200,1600] MHz, (1+PD0A)<=8, (1+PD0B)<=64, fOUT<=60 MHz. РП recommendation: maximize PD0A (manual.txt:1412-1413).

| HSE | REFDIV | FBDIV | fVCO | PD0A | PD0B | post-div | fOUT0 | checks |
|-----|--------|-------|------|------|------|----------|-------|--------|
| 16 MHz | 1 | 96  | 16*96=1536 MHz | 7 (÷8) | 3 (÷4) | 32 | 1536/32 = **48.000000** | FBDIV 96∈[16,160]; VCO 1536∈[200,1600]; fREF 16∈[10,30] — all OK |
| 16 MHz | 1 | 48  | 768 MHz  | 7 (÷8) | 1 (÷2) | 16 | 768/16 = **48.0** | all in range |
| 12 MHz | 1 | 96  | 1152 MHz | 7 (÷8) | 2 (÷3) | 24 | 1152/24 = **48.0** | all in range |
| 12 MHz | 1 | 48  | **576 MHz** | 5 (÷6) | 1 (÷2) | 12 | 576/12 = **48.0** | **576 MHz VCO legal** (200–1600, manual.txt:1410) |
| 24 MHz | 1 | 48  | 1152 MHz | 7 (÷8) | 2 (÷3) | 24 | **48.0** | all in range |
| 25 MHz | 1 | 48  | 1200 MHz | 4 (÷5) | 4 (÷5) | 25 | 1200/25 = **48.0** | exact from 25 MHz, all in range |
| 20 MHz | 1 | 48  | 960 MHz  | 4 (÷5) | 3 (÷4) | 20 | **48.0** | all in range |
| 10 MHz | 1 | 48  | 480 MHz  | 4 (÷5) | 1 (÷2) | 10 | **48.0** | all in range |

- **8 MHz HSE cannot legally feed the PLL**: fREF=8 < 10 MHz minimum (manual.txt:1409). (8 MHz crystal itself is allowed on HSE — table 2.5 row 12, manual.txt:1217-1218 — but then no PLL.)
- 48 MHz sysclk is under the 50 MHz max (see §5) and fOUT<=60 (manual.txt:1411). Example register write for the 16 MHz board config: `PLLSYSCFG2=96; PLLSYSCFG1=0; PLLSYSCFG0: REFDIV=1, PD0A=7, PD0B=3, DSMEN=0, PLLEN=1` (+FOUTEN bit0 of field; PD1x/Fout1 free — SDK leaves Fout1 bypassed via BYP=2, `system_k1921vg015.c:238`).
- Precedent for the formula/encoding: SDK's official 50 MHz configs (`system_k1921vg015.c:155-232`): e.g. HSE=16: REFDIV=1, FBDIV=100, PD0A=7, PD0B=3 -> VCO=1600 (at max), 1600/32=50; HSE=10: REFDIV=1, FBDIV=100, PD0A=4, PD0B=3 -> VCO=1000, /20=50. Arithmetic matches РП formula 4.1.

### 2.1 Board crystals

- NIIET boards (both NIIET-DEV-K1921VG015 and NIIET-MINI-K1921VG015): BSP files contain no crystal value (`hardware/bsp/*/bsp.{c,h}` — LEDs/buttons only). But every SDK build config sets `HSECLK_VAL=16000000` + `SYSCLK_HSE`: `platform/Device/K1921VG015/gcc/makefile:31`, `templates/k1921vg015-bare/sc-dt/.cproject:74`, `projects/plib015/*/.cproject`, `platform/plib015/inc/plib015_rcu.h` (plib5t/plib3t `rcu.h:54` default 16000000). => **16 MHz HSE is the SDK-assumed board crystal** (inference from build defaults, not a schematic statement).
- quickstart.txt / osobennosti.txt: no crystal value mentioned (grep МГц/кварц — no board crystal hits).
- Community boards: no info in local files.

## 3. HSE / HSI / mtime / free-running counters

### 3.1 HSE

- Feature list: «внутренний осциллятор HSE для подключения внешнего резонатора от 2 МГц до 30 МГц» (manual.txt:45-47).
- Electrical spec, table 2.5 row 12 (manual.txt:1210-1218): OSC pin frequency — «при работе с внешним тактовым генератором» fC = **2–30 MHz**; «при работе с кварцевым резонатором» **8–24 MHz**. Figure 4.1 labels OSC «(8-24)МГц» (manual.pdf p.24).
- Bypass/external-clock mode: XI_OSC pin accepts an external clock source 2–30 MHz («Выводы XI_OSC и XO_OSC предназначены для подключения внешнего источника тактового сигнала микроконтроллера с частотой (2 – 30) МГц», manual.txt:448-449; table 2.5 gives XI_OSC input levels, manual.txt:1198-1201). No explicit «HSE BYPASS» control bit found in РП for the main OSC (the BYPASS bit at manual.txt:15655 is in RTC_TRIM and applies to the 32 kHz RTC oscillator/XI_RTC only). PMU can stop HSE: EXTOSC bits (manual.txt:16006-16007, 16030-16031).

### 3.2 HSI

- «HSICLK – внутренний высокочастотный тактовый сигнал частотой 1 МГц (тактовый сигнал внутреннего RC-генератора)» (manual.txt:1340-1341). **РП specifies no accuracy/tolerance for HSI** (searched точность/стабильн/TRIM; the §18.5 RC-calibration is for the RTC 32 kHz RC only, manual.txt:7419-7439). Community measurement ~-5% is from habr (cited in chip_info.md §2), not РП.

### 3.3 mtime / mcycle / TMR32

- CLINT: MTIMECMP @ offset 0x4000, MTIME @ 0xBFF8 (table 9.1, manual.txt:2339-2345); CLINT base 0x0200_0000 (SDK `mtimer.h:6-7`). mtime «увеличивается по сигналу timer_pulse внешнего интерфейса процессорного комплекса» (manual.txt:2350-2351) — **РП does not state the timer_pulse frequency**. SDK equates MTIME_FREQ with the sysclk source: HSI->1 MHz, HSE->HSECLK_VAL, PLL->50 MHz (`mtimer.h:9-17`) => mtime runs at SYSCLK per SDK (unconfirmed by РП).
- **Free-running core-clock counter exists**: instruction table 8.1 lists RDCYCLE/RDCYCLEH «чтения счетчика количества выполненных циклов процессора» (manual.txt:2293-2298) and RDTIME/RDTIMEH «низкочастотного системного таймера» + RDINSTRET (manual.txt:2299-2310). RDCYCLE reads the cycle CSR — usable for HCLK-synchronous keepalive timing. mcycle CSR itself is not named in РП (РП defers to the RISC-V ISA manual, manual.txt:2326-2327).
- TMR32/TMR0-2/I2C/CMP are clocked by **PCLK**; GPIO/CAN/USB/CRC/HASH/CRYPTO by **HCLK** (table 4.3, manual.txt:1518-1536). Figure 4.1: the SYSCLKCFG SRC mux output drives HCLK, PCLK and SYSCLK directly — **no APB/AHB prescaler shown or documented**; TMR chapter example assumes fPCLK=50 MHz at 50 MHz sysclk (manual.txt:9185). => PCLK = HCLK = SYSCLK (no divider found in РП).
- So for keepalive frame timing: rdcycle (core clock) is the cleanest; TMR32 (PCLK=SYSCLK) and mtime (SYSCLK per SDK) also count in the same clock domain.

### 3.4 USB PLL (context)

- Separate PLLUSB, same structure as SYSPLL, must output 60 MHz for the USB block: «Данный блок должен быть настроен на выходную частоту 60 МГц» (manual.txt:1538-1540, 14695-14700). USB clock selectable PLLUSB0CLK vs sysclk via USBCLKSEL (manual.txt:14699-14700; SDK `system_k1921vg015.c:76-96`). Irrelevant for bitbang but confirms fOUT up to 60 MHz is real.

## 4. RCU sysclk switch sequence & flash LAT ordering

### 4.1 Registers

- `SYSCLKCFG` (+30h, reset 0): SRC[1:0]: 00=HSICLK(1MHz), 01=HSECLK, 10=SYSPLL0CLK, 11=LSICLK; SECEN[16] enables sysclk supervision (manual.txt:14931-14946). (Note: §4.3 prose calls the field «SYSSEL» — same field, naming inconsistency; manual.txt:1473, 1483-1485.)
- `CLKSTAT` (+3Ch): SRC[1:0] = current actual source; CLKGOODx/CLKERRx per source (manual.txt:14980-15002).
- Reset default sysclk = HSI 1 MHz (SYSCLKCFG reset 0h = HSICLK, manual.txt:14933-14939).

### 4.2 Sequence (РП §4.2 + SDK ClkInit)

РП order (manual.txt:1417-1428): configure PLL **before** selecting it; BYP off -> REFDIV/FBDIV (in range) -> DSMEN/FRAC if fractional -> PD*A/PD*B -> FOUTEN+PLLEN -> wait LOCK.
SDK ClkInit (`system_k1921vg015.c:144-263`) concrete safe sequence:
1. `SYSCLKCFG.SRC = HSE`, poll `CLKSTAT.SRC == SYSCLKCFG.SRC` (lines 148-154) — run from a non-PLL source while touching the PLL.
2. Write PLLSYSCFG0 (PDs, REFDIV, DSMEN=0, BYP=3, PLLEN=1), PLLSYSCFG1=0, PLLSYSCFG2=FBDIV (155-232).
3. `FOUTEN=1`; dummy delay ~1000; poll `PLLSYSSTAT.LOCK==1` (233-237).
4. `BYP=2` (clear Fout0 bypass, keep Fout1 bypassed) (238).
5. **Before switching**: `FLASH->CTRL_bit.LAT=3; CEN=1` (241-243).
6. `SYSCLKCFG.SRC = SYSPLL0CLK`; poll `CLKSTAT.SRC` match with timeout 100 (254-259).
(plib015 variant `plib015_rcu.c:398-404` first CLEARs BYP|PLLEN then sets dividers — closer to РП wording; note РП says clear BYP first, SDK device init instead sets BYP=3 during config. Both work per SDK usage.)

### 4.3 Flash LAT vs frequency (РП §7, manual.txt:2023-2055)

- Flash read: «Минимальное время чтения данных из Flash-памяти составляет до 60 нс (типовое значение задержки – от 30 нс)» (manual.txt:2023-2024).
- **Table 7.1** (LDO1 = 1.2 V, normal): fSYSCLK <= 60 MHz -> **LAT=1**; <= 30 MHz -> LAT=0. «Значение параметра после сброса равно 1» (manual.txt:2035-2043).
- **Table 7.2** (LDO1 = 0.9 V low-power, read-only flash): <=60 -> 3, <=45 -> 2, <=30 -> 1, <=15 -> 0 (manual.txt:2045-2055).
- => At 48 MHz / 1.2 V: **LAT=1 required (and sufficient); it is also the reset default**, so no LAT raise is strictly needed before switching to 48 MHz. SDK's LAT=3 at 50 MHz is more conservative than РП requires.
- Ordering: РП gives no explicit "set LAT before switching" rule (only «исходя из выбранной рабочей частоты, следует задать определенное количество дополнительных тактов ожидания», manual.txt:2024-2026); SDK sets LAT before raising frequency (`system_k1921vg015.c:241-243` precedes 255).
- Register: FLASH `CTRL` (+4Ch, reset 1_0000h): РП documents **only LAT[18:16]** (manual.txt:16214-16226). SDK header additionally defines CEN (bit1, cache enable), CFLUSH (bit8), and a 4-bit LAT mask 0xF0000 (`K1921VG015.h:9350-9363`) — **CEN/CFLUSH are undocumented in РП** (РП p.6/§3 mentions the core has «кэшем команд», manual.txt:370).

## 5. Max core frequency

- Electrical spec table 2.5 row 11: «Системная частота … SYSCLK, МГц fCI – 50» — **max 50 MHz** (manual.txt:1207-1209).
- Feature list: «от 32 КГц до 50 МГц», «системная PLL: до 50 МГц» (manual.txt:42, 57; same in osobennosti.txt:46).
- Headroom hints (not permission): flash LAT table has a «<=60 MHz» row (manual.txt:2041), PLL fOUT max 60 MHz (manual.txt:1411), USB block runs at 60 MHz internally (manual.txt:1540). No overclock evidence in any local file (SDK, app notes, manual).

## GAPS

- PLL lock time: no numeric value in РП; only LOCK status bit. SDK uses dummy delay + poll.
- SYSPLL fractional-mode jitter: no quantitative spec in РП (the «джиттер … одного периода FIN» quote is CAN-divider-only, manual.txt:10727-10730).
- REFSEL contradiction (§1.2): РП figure says REFSEL=1 needed for HSE reference, SDK never sets it and works. Cannot resolve from local sources; needs hardware test or NIIET erratum.
- mtime (timer_pulse) frequency: not stated in РП; SDK implies = SYSCLK. Unconfirmed by РП.
- HSI 1 MHz accuracy/tolerance: absent from РП. (habr community measurement ~-5%, via chip_info.md, non-РП.)
- Physical crystal on NIIET-DEV/MINI boards: only inferable from SDK build defaults (16 MHz); no schematic/BOM in local sources. Community boards: nothing local.
- APB (PCLK) prescaler: no divider register found in РП; PCLK=HCLK=SYSCLK inferred from figure 4.1 + TMR example — not stated as an explicit rule.
- FLASH CTRL CEN/CFLUSH (cache enable/flush) bits: present in SDK header, absent from РП CTRL description — cache behavior/penalties undocumented.
- SECPRD/PLL supervision minimum reaction formula uses HSICLK as REFCLK (manual.txt:1443-1461) — behavior when HSI trimmed off-nominal: not specified.
