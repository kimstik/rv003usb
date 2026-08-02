# K1921VG015 GPIO research — bitbang USB (rv003usb port)

Date: 2026-07-31. Sources: РП = manual.txt (Руководство пользователя К1921ВГ015, 19.02.2025), SDK = niiet_riscv_sdk (local clone), app notes osobennosti.txt/quickstart.txt.
File paths cited relative to scratchpad `/tmp/claude-0/-home-user-rv003usb/2cc76999-0266-5060-b0a5-13e0eb56e9cd/scratchpad/`.

## 1. Register semantics (per РП §11 "Порты ввода-вывода" + А.6)

3 identical 16-bit ports A/B/C; base 2800_0000/1000/2000h (РП А.6, manual.txt:16839-16841). Ports are held in reset and unclocked after power-up; enable via RCU CGCFGAHB/RSTDISAHB (РП §11, manual.txt:3901-3902). After reset all pins = GPIO inputs, tri-state (РП §11.1, manual.txt:3978-3979: "После сброса все выводы конфигурируются как выводы общего назначения (режим GPIO) и находятся в третьем состоянии").

Offsets (РП А.6): DATA +00, DATAOUT +04, DATAOUTSET +08, DATAOUTCLR +0C, DATAOUTTGL +10, PULLMODE +20, OUTMODE +24, OUTENSET +2C, OUTENCLR +30, ALTFUNCSET +34, ALTFUNCCLR +38, ALTFUNCNUM +3C, SYNCSET +44, SYNCCLR +48, QUALSET +4C, QUALCLR +50, QUALMODESET +54, QUALMODECLR +58, QUALSAMPLE +5C, INTENSET +60, INTENCLR +64, INTTYPESET +68, INTTYPECLR +6C, INTPOLSET +70, INTPOLCLR +74, INTEDGESET +78, INTEDGECLR +7C, INTSTATUS +80, DMAREQSET +84, DMAREQCLR +88, ADCSOCSET +8C, ADCSOCCLR +90, LOCKKEY +9C (W) / LOCKSTAT +9C (R), LOCKSET +A0, LOCKCLR +A4, MASKLB +400..+7FC, MASKHB +800..+BFC (manual.txt:16857-18331; matches SDK GPIO_TypeDef, K1921VG015.h:6218-6364 incl. Reserved words at +14..1C, +28, +40).

- **DATA** (+00, reset 0000_xxxxh): read "Возвращает текущее состояние порта", write ignored (manual.txt:16855-16864). Read value comes AFTER the input synchronizer — see §2. Bits 15:0.
- **DATAOUT**: read returns state; write drives pins (manual.txt:16874-16884). DATAOUTSET/CLR/TGL: W1 sets/clears/toggles bits of DATAOUT, read returns DATAOUT; РП warns against read-modify-write on TGL/xxxCLR registers (§11.1 note, manual.txt:3910-3913).
- **OUTENSET/OUTENCLR**: output driver enable per pin, W1-set / W1-clear, read returns OUTENSET state (manual.txt:17071-17188).
- **OUTMODE** (+24): 2 bits/pin, 00=push-pull, 01=**open-drain**, 10=open-source, 11=reserved (manual.txt:17064-17069). РП §11.1: in open-drain/open-source modes pin state is driven via DATAOUT/SET/CLR/TGL "при этом режим выхода должен быть отключен записью в регистр OUTENCLR" (manual.txt:3984-3986) — i.e. for OD operation OUTEN must be DISABLED (unusual; OD is not gated by OUTEN).
- **PULLMODE** (+20): 1 bit/pin, 0=disabled, 1=pull-up. "1 Подтяжка к уровню логической единицы (pull-up)" (manual.txt:17008-17011). **Pull-up only, no pull-down option in GPIO; no kOhm value anywhere in РП** — only pull-up current spec: IIL1 = −200…−10 µA @ VCC=3.6 V, UIL=0 (табл. 2.4 п.5, manual.txt:984-988) => effective Rpu ≈ 18 kΩ…360 kΩ. (Note: табл. 2.4 also lists pull-**down** currents IIL2/IIH2, manual.txt:996-1005 — pull-down circuits exist on some pins (JTAG?) but are not selectable via GPIO PULLMODE.)
- **DRIVEMODE / drive strength: NO such register.** РП §11.1 claims per-pin "нагрузочная способность и быстродействие вывода" are configurable (manual.txt:3924-3925, also intro manual.txt:429-432), but no register for it exists in РП А.6, in SDK GPIO_TypeDef (K1921VG015.h:6218-6364), or in SVD (grep DRIVE = 0 hits). Reserved offsets +28 and +40 exist in the struct. => drive-strength control undocumented/absent.
- **ALTFUNCSET/CLR** (1 bit/pin, W1) + **ALTFUNCNUM** (2 bits/pin, values 0..3 = none/AF1/AF2/AF3) (manual.txt:17191-17288). AF input priority scheme: lower AF number wins; tie → lower port letter/pin number (РП §11.2, manual.txt:4002-4007).
- **SYNCSET** (+44): per-pin. **0 = signal reaches DATA after TWO-clock synchronization (базовая, always on); 1 = FOUR-clock sync (базовая + дополнительная)**. Exact quote (manual.txt:17308-17312): "0 Сигнал с вывода n передается в регистр DATA после двухтактной синхронизации (базовая); 1 Сигнал с вывода n передается в регистр DATA после четырехтактной синхронизации (базовая и дополнительная)". SYNCCLR W1 disables the additional stage (manual.txt:17323-1337). => **Cannot bypass the base 2-FF synchronizer for DATA reads; minimum input latency = 2 FCLK.**
- **QUAL\*** (input qualifier/filter): default OFF (reset 0h, manual.txt:17390). QUALSET enables per-pin filter; QUALMODESET: 0=3-sample, 1=6-sample majority-agree filtering; QUALSAMPLE.PERIOD (20-bit) = interval in FCLK ticks between samples, one value per port (РП §11.3, manual.txt:4017-4024; А.6 manual.txt:17387-17568). If samples disagree, signal state does not change (manual.txt:4018-4020). **Quirk: setting SYNCSET and QUALSET for the same pin simultaneously = signal passes straight through the filter block** — "при установленных единицах в одних и тех же разрядах SYNCSET и QUALSET сигнал будет проходить напрямую с входа фильтра на его выход" (§11.3, manual.txt:4025-4027). Whether this also removes the base 2-clock sync is NOT stated (the SYNCSET reg description implies base sync is outside the filter and always applies).
- **MASKLB[256]/MASKHB[256]**: address = GPIOp + 400h/800h + 4*mask (mask = address bits 9:2) (§11.7, manual.txt:4154-4160; А.6 manual.txt:18295-18318). WRITE: value is hardware-masked and placed in DATAOUT; masked-off bits unchanged (manual.txt:4161-4176: "будет аппаратно маскировано и размещено в регистре порта DATAOUT"). Example: mask 1100_0011b → address 2800_070Ch for port A LB (manual.txt:4172-4175). MASKLB covers bits 7:0 (value bits 7-0), MASKHB bits 15:8 (value bits 15-8) (manual.txt:18302-18315). **READ semantics: РП does not describe reads in words; access marker is "з ч" (write+read) and reset = 000000xxh / 0000_xx00h (xx = pin-dependent)** (manual.txt:18298, 18310) — strongly implies masked read of pin state (ARM CMSDK-style), but not explicitly documented → GAP.
- **LOCKKEY/LOCKSET/LOCKCLR**: config lock; unlock by writing ADEADBEEh to LOCKKEY; when locked, only INTSTATUS and QUALSAMPLE remain writable for locked pins (§11.6, manual.txt:4101-4116; А.6 manual.txt:18150-18245). Useful to protect D+/D- config from accidental clobber.

## 2. Input path latency, bus type

- **Input synchronizer: base = 2 SYSCLK, cannot be bypassed for DATA reads.** SYNCSET bit description (РП А.6, manual.txt:17308-17312): "0 — Сигнал с вывода n передается в регистр DATA после двухтактной синхронизации (базовая); 1 — ... после четырехтактной синхронизации (базовая и дополнительная)". Reset SYNCSET=0 (manual.txt:17304) => default input latency 2 FCLK ≈ 41.7 ns @48 MHz.
- §11.1/11.3 mention an "асинхронный" direct path (manual.txt:3976-3977, 4014-4016) — but per the SYNCSET register description this refers to bypassing the FILTER block, not the base 2-FF sync of DATA. The async path presumably feeds alt-function peripherals. Not stated more precisely.
- Quirk (РП §11.3, manual.txt:4025-4027): SYNCSET=1 AND QUALSET=1 on the same pin => "сигнал будет проходить напрямую с входа фильтра на его выход" (filter bypass; effect on the base sync not stated).
- **Bus: GPIO is AHB, clocked by HCLK** (РП табл. 4.3, manual.txt:1531 "GPIO HCLK - CGCFGAHB, RSTDISAHB"; §4.5 manual.txt:1510-1511 lists GPIO among "AHB периферии"). Fig. 3.1 (manual.txt:1299-1331): GPIO is its own slave on the "блок коммутации" (bus fabric), masters = CPU I-CODE/D-CODE/SYS + 3 DMA.
- **Register access wait states: NOT documented anywhere in РП** — no AHB timing/wait-state table exists. Load/store cost to GPIO must be measured on silicon (mcycle).
- HCLK vs SYSCLK relationship (divider?) not stated in the GPIO/RCU text read; SDK treats them as same frequency domain (no separate AHB divider register found in RCU chapter headings). Not verified — GAP.

## 3. Interrupts

Per РП §11.4 (manual.txt:4035-4057) and А.6:
- Any pin can generate an interrupt; flag set in INTSTATUS and request goes to PLIC (manual.txt:4040-4041).
- **INTSTATUS is write-1-to-clear, no hardware auto-clear**: "1 Флаг запроса на прерывание. Бит не сбрасывается аппаратно... Запись единицы — Обнуляет бит PINn" (manual.txt:17917-17927).
- Config matrix: INTTYPESET 0=level/1=edge (manual.txt:17663-17671); INTPOLSET 0=low-level/falling, 1=high-level/rising (manual.txt:17747-17758); **both-edge mode**: set INTTYPE=edge then W1 to INTEDGESET — "В этом режиме состояние регистра полярности INTPOLSET игнорируется" (§11.4, manual.txt:4050-4053); INTEDGECLR reverts to single-edge per INTPOL (manual.txt:4053-4055). In level mode INTEDGESET is ignored (manual.txt:4056-4057).
- Enable: INTENSET/INTENCLR W1 (manual.txt:4043-4045).
- Hardware requests independent of interrupt mask: DMAREQSET (BREQ to DMA) and ADCSOCSET (ADC start-of-conversion) fire on the same pin-event conditions "при этом не важно, маскировано само прерывание или нет" (§11.5, manual.txt:4087-4099).
- **All three ports share ONE PLIC line: vector 5 "GPIO Прерывания портов GPIO"** (РП табл. 9.1, manual.txt:2428).
- **Latency from pin edge to PLIC request: NOT stated in РП** (no cycle numbers anywhere in §11 or §9). Expect ≥2 SYSCLK input sync + PLIC gateway; measure on silicon.

## 4. Pin selection for D+/D-/DPU

Constraints applied: same port; bit positions 0..4 so that (D+|D-) mask fits a positive 6-bit `c.andi` immediate (0..31); both pins read in one `lw` of DATA (any same-port pair satisfies this); minimal loss of useful alt functions; free on DEV/MINI boards.

**HW USB D+/D- are dedicated pins, NOT GPIO-muxed:** USB_DP = pin 30, USB_DN = pin 31, listed in РП табл. 2.2 "Функциональное назначение выводов, НЕ имеющих альтернативных функций" (manual.txt:801, 869-870: "USB_DN 31 I/O Вход/выход «USB Dn»; USB_DP 30 I/O Вход/выход «USB Dp»"). => The on-board USB connector (wired to 30/31) **cannot be reused for bitbang**; bitbang USB needs its own connector wired to port pins. Max input level on USB lines = UCC1 (табл. 2.5 п.19, manual.txt:1238-1240).

Alt functions of low pins (РП табл. 2.1; list order = AF1..AF3, confirmed by SDK: CLKOUT is 3rd entry for C7 in manual.txt:789-793 and SDK sets `GPIOC->ALTFUNCNUM_bit.PIN7 = 3` in system_k1921vg015.c:109-110; UART0 is 1st entry for A0/A1 and retarget.c:37-39 sets AF=1):
- A0/A1: UART0 RX/TX (manual.txt:592-598) — **SDK console/retarget uses UART0 on GPIOA A0/A1** (retarget.h:30-32, retarget.c:32-39). Avoid.
- A2/A3: UART1 RX/TX, TMR2_OUT2/3 (manual.txt:600-606); A4: UART2_RX, TMR1_CCIA, QSPI_CLK (manual.txt:608-611).
- B0-B4: SPI0 CLK/FSS/RX/TX, SPI1_CLK, UART1/UART4 modem+data, TMR32/TMR0/TMR1_EXTIN (manual.txt:668-696).
- C0-C4: TMR32_OUT0-3, TMR32_EXTIN, UART3_RTS/DTR, UART4_CTS/DCD/DSR (manual.txt:756-780) — only timer outputs and UART modem-control lines: cheapest to sacrifice.

Board usage: DEV board LEDs = A12-A15, button = A11 (DEV bsp.h:35-54); MINI board LED = C6, button = C7 (MINI bsp.h:35-47). C0-C4 and B0-B4 unused by both BSPs.

**Candidate set 1 (primary): GPIOC — D+ = C0, D− = C1, DPU = C2** (package pins 34/35/36, manual.txt:756-766).
- Justification: same port, bits 0..1 → USB pin mask = 0x03, DPU mask 0x04, all `c.andi`-able; lost alt functions are only TMR32_OUT0/1/2 + UART3_RTS/DTR + UART4_CTS; free on both NIIET boards; adjacent package pins simplify wiring; port C used by MINI board only at C6/C7.
- Note: rv003usb convention = 1.5k pull-up from DPU pin to D− (LS device identification).

**Candidate set 2 (fallback): GPIOB — D+ = B2, D− = B3, DPU = B4** (package pins 55/56/57, manual.txt:678-696).
- Justification: mask 0x0C (fits c.andi), DPU 0x10; keeps whole port C for TMR32 PWM outputs if the application needs them; sacrifices SPI0_RX/TX and SPI1_CLK (SPI0 still partly usable, CLK/FSS on B0/B1 remain); free on both boards. Use if port C is needed for timer PWM.
- (If UART1/2 and QSPI are unneeded, A2/A3/A4 works identically — but port A hosts console UART0 (A0/A1) and DEV-board LEDs/button (A11-A15); keeping the USB mask port free of other traffic is safer for masked/atomic ops.)

IRQ: any choice shares PLIC vector 5 (see §3), so no port is preferable for interrupt reasons.

## 5. Electrical

- **Supply**: single digital domain VCC1, nominal 1.7–3.6 V (РП §2, manual.txt:453-455: "Номинальное значение напряжения должно находиться в диапазоне от 1,7 до 3,6 В"); предельно допустимый range 1.62–3.6 V (табл. 2.5 п.1, manual.txt:1193). VCC1 pin 61 must be ≥2.25 V when Flash+PLL run (табл. 2.5 п.14, manual.txt:1220-1222). => run at 3.3 V for USB.
- **NOT 5V-tolerant.** Quotes: табл. 2.5 (предельно допустимые режимы) п.6 (manual.txt:1198): "Входное напряжение высокого уровня, В — UIH — 0,7UCC1 … UCC1"; табл. 2.6 (предельные режимы, ≤5 с) п.4 (manual.txt:1257): "Входное напряжение высокого уровня, В — UIH — не более UCC1+0,6" with note "Время работы в одном из предельных режимов должно быть не более 5 с" (manual.txt:1275). At VCC1=3.3 V the absolute ceiling is 3.9 V for ≤5 s. Also §2 note (manual.txt:481-482): "Запрещено подавать напряжение на функциональные выводы при выключенном питании микроконтроллера" — relevant for bus-powered USB: host must not drive D+/D− while MCU unpowered.
- **Drive strength**: no programmable drive (see §1). DC spec (табл. 2.4 пп.1-2, manual.txt:951-967): UOL ≤ 0.4 V @ IOL = 4.0 mA, UOH ≥ UCC1−0.4 V @ IOH = −4.0 mA (VCC=3.0 V). Limit values: IOL/IOH ≤ 4 mA continuous (табл. 2.5 пп.9-10, manual.txt:1205-1206), ≤ 10 mA for ≤5 s (табл. 2.6 пп.7-8, manual.txt:1270-1271). Load capacitance CL ≤ 40 pF (табл. 2.5 п.13, manual.txt:1219). Total port A+B+C current ≤ 150 mA (manual.txt:931-932).
- **Pull-up strength**: IIL1 (input current, pull-up on, UIL=0, VCC=3.6 V) = −200…−10 µA (табл. 2.4 п.5, manual.txt:984-988) => Rpu ≈ 18–360 kΩ. Far too weak/loose for USB 1.5k — external 1.5 kΩ DPU resistor mandatory (as in rv003usb anyway).
- **Max GPIO toggle frequency: NOT specified in РП** (no AC switching table for IO; only CL=40 pF limit). GAP.
- USB LS feasibility at 3.3 V: output levels UOL ≤0.4/UOH ≥VCC−0.4 @4 mA meet USB LS static levels with VCC1=3.3 V; 4 mA into 1.5 kΩ + host pull-down is within budget (USB LS current ≈ 3.3V/1.5k ≈ 2.2 mA).

## GAPS

1. GPIO register access cost over AHB (wait states, load/store cycles) — no data in РП; measure with mcycle.
2. Pin-edge → PLIC-request latency in cycles — not stated in РП §11/§9.
3. MASKLB/MASKHB READ semantics — РП documents writes only ("аппаратно маскировано и размещено в регистре DATAOUT", manual.txt:4175-4176); access marker "з ч" + reset "000000xxh" imply masked reads of pin state (ARM CMSDK-style) but this is inference, not documented. Verify on silicon before using masked reads in RX path.
4. Whether SYNCSET+QUALSET "filter bypass" quirk also removes the base 2-clock DATA synchronizer — text says only "напрямую с входа фильтра на его выход" (manual.txt:4025-4027); unclear, test on silicon (would give async DATA reads if true).
5. Drive-strength / slew ("нагрузочная способность и быстродействие") register: claimed configurable in §11.1 (manual.txt:3924-3925) but absent from А.6, SDK header and SVD (reserved offsets +28/+40). Undocumented — matches habr errata about undocumented GPIO regs.
6. Max GPIO toggle frequency — not specified in РП.
7. HCLK vs SYSCLK ratio (AHB divider) — not found in the sections read; assumed 1:1 per SDK usage, unverified.
8. Pull-down circuits appear in electrical table (IIL2/IIH2, manual.txt:996-1005) but no GPIO register selects pull-down — which pins have it (JTAG?) is not stated.
9. Internal pull-up value in kΩ — never given; only current range IIL1.
