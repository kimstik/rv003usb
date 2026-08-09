# Prior-Art Report: Porting Codec2 to Tiny MCUs (20–50 MIPS, 2–8 KB RAM, some without HW multiplier)

**Evidence labels:** **VERIFIED** = read in a primary source; **REPORTED** = secondary source or search snippet of a primary source that could not be fully loaded (rowetel.com intermittently returned 503 throughout this session); **ESTIMATE** = extrapolation with stated assumptions.

## 1. Official codec2 embedded ports (STM32F405 / SM1000)

### 1.1 What exists
- [drowe67/codec2](https://github.com/drowe67/codec2) contains `stm32/` targeting the STM32F405 (Cortex-M4F, 168 MHz, FPU) of the SM1000. The current [stm32/README.md](https://raw.githubusercontent.com/drowe67/codec2/main/stm32/README.md) is a build quickstart with **no published CPU/RAM/flash tables** (VERIFIED).
- A profiling harness exists in-tree: `stm32/unittest/src/tst_api_mod_700d_profile.c`, `tst_api_demod_700d_profile.c`, `stm32/src/stm32f4_machdep.c`, `stm32/unittest/lib/python/sum_profiles.py` (VERIFIED via GitHub code search). Per-function cycle numbers are measurable with existing tooling but not published.

### 1.2 Measured / stated CPU numbers on STM32F4-class parts

| Data point | Value | Status | Source |
|---|---|---|---|
| Whole FreeDV stack (modem+codec+FEC+drivers), half duplex | "We run the entire FreeDV stack ... in a 168 MHz stm32f4 OK (half duplex)" | VERIFIED | [mail-archive msg06585](https://www.mail-archive.com/freetel-codec2@lists.sourceforge.net/msg06585.html) |
| Codec2 alone (float) | "about half a floating point STM32 ... roughly 80 MHz" | VERIFIED (Rowe's estimate) | same msg06585 |
| SM1000 bring-up: Codec 2 encoder + FDMDV modulator | "about 25% of the STM32F4 CPU" | REPORTED | [SM1000 Part 2](https://www.rowetel.com/wordpress/?p=3488) |
| FreeDV 1600 full path (incl. modem) on 168 MHz F4 | ~70% load ("168/120 * 70% load = 98% load" for a 120 MHz MK22) | VERIFIED | [OpenGD77 thread msg06524](https://www.mail-archive.com/freetel-codec2@lists.sourceforge.net/msg06524.html) |
| FreeDV RAM (codec+modem, 700D era) | "roughly 64KB of RAM" | VERIFIED | same msg06524 |
| SM1000 V2: FreeDV 700D + Codec 2 700C + OFDM + LDPC | "all running on a 168MHz micro-controller in just 192k of RAM" | REPORTED | [SM1000 V2 Firmware](https://www.rowetel.com/?p=6835) |
| Cautionary -O0 baseline: STM32F446 @180 MHz | 40 ms frame encoded in ~47 ms at -O0, ~15 ms at -O1 | VERIFIED | [msg06963](https://www.mail-archive.com/freetel-codec2@lists.sourceforge.net/msg06963.html) |
| ESP32-S3, FreeDV 700D incl. modem | TX ~90 ms / RX ~40 ms per 160 ms frame | VERIFIED | [msg06948](https://www.mail-archive.com/freetel-codec2@lists.sourceforge.net/msg06948.html) |

### 1.3 Freshest clean measurement: M17 "Codec2-mod" (Dec 2025)
[Codec2-mod](https://github.com/M17-Project/Codec2-mod): codec2 **3200 bps only**, C only, **fully static allocation (including KISS FFT)**, bitstream-compatible. Benchmarks on **STM32F405 @168 MHz, FPU, -Os**, 1000 frames (VERIFIED, [M17 blog](https://m17project.org/2025/12/29/codec2-mod-released-for-testing/)): encode — reference 9.804 s vs mod 4.341 s (2.25×); decode — 11.487 s vs 9.612 s (1.2×). Derived (ESTIMATE): encode 4.34 ms/20 ms ≈ 22% ≈ **36 F4-MHz**; decode 9.61 ms/20 ms ≈ 48% ≈ **81 F4-MHz**; full duplex ≈ 117 MHz-equivalent — consistent with Rowe's "~80 MHz" ballpark. These are *float-with-FPU* numbers.

### 1.4 Optimization history worth mining
- [Issue #27 WP9000 — Optimisation](https://github.com/drowe67/codec2/issues/27): CPU-load and run-time memory optimization work package.
- [Porting a LDPC Decoder to a STM32](https://www.rowetel.com/wordpress/?p=6413) (Don Reid, 2018): initial 700D LDPC decode "300–2500 ms per frame" vs a 160 ms budget; fixed via `phi0()` math replacement, loop restructuring, shrinking `DecodedBits` from ~90 KB to 224 B (REPORTED). Lesson: even the official 168 MHz port required exactly the surgery a tiny-MCU port needs far more of.

## 2. Fixed-point codec2: attempts and Rowe's position

### 2.1 Rowe's stated position (2019, direct quotes, VERIFIED, [msg06585](https://www.mail-archive.com/freetel-codec2@lists.sourceforge.net/msg06585.html))
- "The codec DSP is all floating point."
- "In general it takes more MIPS in fixed point than float, so it's likely the MIPS (and hence clock speed requirements) will go up." *(Context: FPU-equipped STM32s.)*
- "The chips are cheap, so might not be worth the bother."
- "Happy to work with you if you want to try some fixed point porting. This would involve writing a bunch of tests to verify float versus fixed."

Fixed point has been "Further Work" since the [DCC 2010 presentation](https://tapr.org/wp-content/uploads/DCC2010-Codec2-presentation-K6BP-VK5DGR.pdf) ("Fixed point and DSP chip implementation" — VERIFIED). [Issue #36](https://github.com/drowe67/codec2/issues/36): unit tests motivated by "ports to other (non C99) compilers, fixed point, FPGA etc."

### 2.2 Actual fixed-point code
- **[maksimus1210/c2fxp](https://github.com/maksimus1210/c2fxp)** — "fixed point codec2", C, LGPL-2.1, 22 commits, 0 stars, minimal README, encoder/decoder + FFT + fixed-point math utils, no performance claims, apparently abandoned (VERIFIED — inspected). The **only** GitHub hit for "codec2 fixed point".
- 2013-era thread "Fixed Point implementation of Codec2" at [narkive](https://freetel-codec2.narkive.com/45K11cbK/fixed-point-implementation-of-codec2) — content unverified (REPORTED, title only).
- **No fixed-point port has ever shipped in mainline.**

### 2.3 Ports to smaller/other MCUs (all floating-point builds of reference code)

| Project | MCU | Notes |
|---|---|---|
| [blanu/codec2-arduino](https://github.com/blanu/codec2-arduino) | nRF52 (M4F, 64 MHz) | PoC, mode 700B, no perf data |
| [deulis/ESP32_Codec2](https://github.com/deulis/ESP32_Codec2) | ESP32 (2×240 MHz, FPU) | LoRa walkie-talkies; no benchmarks |
| [sh123/esp32_loradv](https://github.com/sh123/esp32_loradv) | ESP32 | DV handhelds; no numbers |
| [piratfm/codec2_m4f](https://github.com/piratfm/codec2_m4f) | STM32F4-Discovery | Encoder+modulator only, decoder never done, archived 2024; CMSIS-DSP, CCM RAM |
| [blues/codec2](https://github.com/blues/codec2) | STM32-class | Commercial fork: **2400 bps encoder-only subset** for Whisper transcription downstream |
| OpenGD77 analysis | MK22FN512 (M4F 120 MHz) | 1600D "98% load. 2% cycles left"; 700D "surely not powerful enough" ([msg06524](https://www.mail-archive.com/freetel-codec2@lists.sourceforge.net/msg06524.html)) |
| [M17 Module17/OpenRTX](https://github.com/M17-Project/Module_17) | STM32F4 @168 MHz | M17 standardizes on Codec2 3200; F405 runs vocoder + 4FSK modem + UI |

**Negative results (searched, not found):** any codec2 port to Cortex-M0/M0+/RP2040 or FPU-less RISC-V; any project named "QDV" using codec2.
**Every existing deployment uses an FPU core ≥64 MHz; the practical field floor is the 168 MHz STM32F405.**

## 3. Fixed-point precedents from other codecs

### 3.1 Speex — the canonical float→fixed conversion ([LCA2006 paper](https://jmvalin.ca/papers/speex_lca2006.pdf), VERIFIED)
- "Since November 2003 (version 1.1.1), work has been going on to convert Speex to fixed-point." By Nov 2005 (~2 years part-time): narrowband "completely converted for all constant bit-rates from 3.95 to 18.2 kbps".
- Methodology: "arithmetic operator abstraction (macros)" — one codebase, `FIXED_POINT` define. Platforms: Blackfin, ARM v4/v5E, TI C55x/C54x/C6x.
- Rowe's Blackfin work: ~21 Speex encoders realtime on 500 MHz Blackfin ≈ **24 MHz/encoder** (REPORTED); decoder ≈ **5 MIPS** (REPORTED).
- Commercial worst-case: [Adaptive Digital Speex on Cortex-M4](http://www.adaptivedigital.com/product/arm/speex-arm.shtml): NB encode 193.8 MIPS, decode 6.05 MIPS — encoder complexity is hugely tunable and the **decoder is nearly free**.

### 3.2 MELPe — codec2's algorithm class fits in ~25–60 fixed-point DSP MIPS
[comp.dsp thread](https://www.dsprelated.com/showthread/comp.dsp/50647-1.php) (VERIFIED): "MELP-2400 takes somewhat **25 MIPS + 10k**, MELPe-1200 ~ **100 MIPS + 100k**"; Compandent: "about **60 MIPS including the Noise-PreProcessor, about 45 MIPS without it**"; low-rate tables "60K words = 120Kbytes". [melpe.org FAQ](https://melpe.org/melpe_faq/) confirms C54xx/C55xx/ARM packages.

### 3.3 AMBE/DVSI
AMBE+ is proprietary single-vendor fixed-point DSP silicon (DCC2010 PDF) — more evidence MBE-family coding fits small fixed-point silicon.

### 3.4 Quality anchors
Rowe: "Codec 2 700C is better than MELPe 600" / "as good or a little better, sample-dependent" ([p=5520](https://www.rowetel.com/wordpress/?p=5520), REPORTED). Adversarial comparison: [DSP Innovations codec2 vs MELPe vs TWELP @1200](https://dspini.com/twelp/codec2-vs-melpe-vs-twelp-1200) (REPORTED).

### 3.5 Neural vocoders — out of scope
LPCNet: 1.5–6 GFLOPS ([arXiv 1810.11846](https://arxiv.org/abs/1810.11846)); FARGAN: 600 MFLOPS ([arXiv 2405.21069](https://arxiv.org/pdf/2405.21069)). Budget is 20–50 integer MIPS → 2–4 orders of magnitude short.

### 3.6 Fixed-point FFT building blocks
[kissfft](https://github.com/mborgerding/kissfft) supports "float, double, Q15 short or Q31" with scaling for overflow prevention (VERIFIED). [CMSIS-DSP](https://arm-software.github.io/CMSIS-DSP/latest/index.html) provides q7/q15/q31 CFFT/RFFT, tested down to Cortex-M0 (VERIFIED).

## 4. RISC-V-without-multiplier precedents

### 4.1 Audio on CH32V003 — synthesis/decode side proven
[atomic14/ch32v003-audio](https://github.com/atomic14/ch32v003-audio) (VERIFIED): on the 8-pin $0.10 CH32V003J4M6 — 1-bit music, 8-voice polyphonic PWM synth, compressed playback (**IMA ADPCM, 2-bit ADPCM, QOA**) at 8 kHz/8-bit PWM, and **Talkie LPC speech synthesis**. 8 kHz output, ADPCM decode, and LPC synthesis filters all run at 48 MHz/2 KB/no-mul.

### 4.2 Measured soft-multiply cost on RV32EC (first-party, from this very repo)
[rv003usb `demo_pikoball_hid/color_utilities.h`](https://github.com/cnlohr/rv003usb/blob/master/demo_pikoball_hid/color_utilities.h) lines 139–195 (VERIFIED locally):

```
//  No multiply:            21.3% CPU Usage
//  Assembly below:         42.4% CPU Usage
//  C version:              41.4% CPU Usage
//  Using GCC (__mulsi3):   65.4% CPU Usage
```

One libgcc `__mulsi3` per loop iteration **tripled** total loop cost; a hand shift-add early-exit multiply cost 2×. Also: "The CH32V003 can branch unbelievably fast". RV32E softlib reliance confirmed in [riscv-gnu-toolchain #329](https://github.com/riscv-collab/riscv-gnu-toolchain/issues/329). No published cycle-exact `__mulsi3` count for CH32V003 — measure ourselves.

### 4.3 WCH escape hatch: CH570
**CH570** ≈$0.10: QingKe **RV32IMBC (hardware multiply + bit-manip), 100 MHz, 12 KB SRAM, 256 KB flash**, 2.4 GHz radio, USB (VERIFIED — [openwch/ch570](https://github.com/openwch/ch570), [CNX Software](https://www.cnx-software.com/2025/04/02/10-cents-wch-ch570-ch572-risc-v-mcu-features-2-4ghz-wireless-bluetooth-le-5-0-usb-2-0/), [datasheet](https://www.mikrocontroller.net/attachment/670693/CH572DS1_en.PDF)). No codec2 prior art on it.

### 4.4 Cortex-M0+ multiplier variants (PY32F003 leg)
The M0+ core ships with either a **single-cycle ("fast") or 32-cycle iterative ("small") MULS** ([ARM Cortex-M0+ TRM](https://developer.arm.com/documentation/ddi0484/c/CHDCICDF)). **Which one PY32F003 uses was not found in any indexed source** — must be measured; it swings the encoder budget ~2–4×.

## 5. MIPS scaling with explicit assumptions

**Anchors:** A1 Codec2-mod: enc 36 / dec 81 F4-MHz (float+FPU, derived). A2 Rowe: codec ≈80 F4-MHz. A3 MELP-2400 ≈25 DSP-MIPS (+10 K), MELPe w/NPP 45–60. A4 Speex fixed-point enc ≈24 MHz/ch Blackfin, dec ≈5 MIPS. A5 RV32EC soft-mul: 3× (libgcc) / 2× (tuned) on mul-heavy loops.

**Assumptions (ESTIMATE):**
- S1: softfloat ≈10–20 int instructions per float op ⇒ float codec2 on 48 MHz M0+ needs ~1200 MHz-equivalent ⇒ **~25× over budget; fixed point is mandatory** (Rowe's "not worth it" was about FPU parts).
- S2: 1 C54x/Blackfin DSP-MIPS ≈ 2–3 plain-RISC integer MIPS for filter/FFT kernels ⇒ MELP-class fixed-point coder on 1-cycle-mul 48 MHz core ≈ 50–75 MHz full duplex — **marginal**; one-directional or decoder-only **feasible** (dec 10–15 MHz).
- S3: fixed-point codec2-3200 should cost ≤ MELP-2400 (same family, no NPP): projected **enc ~15–30 MHz, dec ~20–40 MHz** on a 48 MHz 1-cycle-mul core. Wide bars because *nobody has ever measured it* — the central gap.
- S4: 32-cycle-mul M0+ ⇒ mul-dominated kernels 3–8× slower ⇒ full duplex out; encoder-only 3200 possibly viable with Q15 FFT + NLP decimation. PY32F003 MUL latency is the gating unknown.
- S5: CH32V003 (no mul, 2 KB RAM): A5 × S3 ⇒ **2–5× over budget** before the RAM wall (512-pt int16 complex FFT workspace ≈ 2 KB alone). Realistic scope: **decoder-only with restructured synthesis (Talkie precedent) or I/O coprocessor**. CH570: S3 ÷ 100 MHz ⇒ **15–40% CPU, 12 KB RAM vs ~4–8 KB int16 state — the plausible full-codec WCH target**.
- S6: flash: 3200/1300 tables modest; 700C newamp1 VQ large (MELPe precedent: low rates ≈100 K tables) ⇒ prefer 3200 first on 16–64 KB flash parts.

**Sanity check:** OpenGD77's verified "98% load on 120 MHz M4F for FreeDV 1600 incl. modem" is consistent with the bare codec landing in the tens-of-MHz band on lesser cores.

## 6. Gaps = opportunities
1. No working, quality-verified fixed-point codec2 exists; everything below 64 MHz M4F is unexplored.
2. No per-function cycle profile of codec2 is published anywhere despite the in-tree stm32 harness — running it (or instrumenting Codec2-mod) is the cheapest first step to ground S3.
3. PY32F003 MUL latency undocumented — measure (10-instruction MULS timing loop).
4. Proven method on the shelf: Speex operator-abstraction macros + float-vs-fixed regression tests (Rowe offered to help with exactly these), Codec2-mod as static single-mode base, kissfft int16, CMSIS-DSP q15 (M0+ leg), cnlohr-style shift-add multiplies (RV32EC leg).
5. Mode choice: 3200 first (cheapest tables, M17-compatible, Codec2-mod base); 1300/700C later if flash allows. Quality floor defensible: 700C ≳ MELPe 600.
