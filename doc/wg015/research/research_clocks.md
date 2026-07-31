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

## 2. Verified-legal exact-48 MHz integer configs

TODO

## 3. HSE / HSI / mtime / free-running counters

TODO

## 4. RCU sysclk switch sequence & flash LAT ordering

TODO

## 5. Max core frequency

TODO

## GAPS

TODO
