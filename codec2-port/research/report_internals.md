# Codec2 Internals Analysis for Tiny-MCU Porting (20–50 MIPS, 2–8 KB RAM class)

Source: fresh clone of `drowe67/codec2` @ `310777b`. Paths relative to `src/` unless noted. **MEASURED** = compiled/ran host-side C (gcc x86-64); **ESTIMATE** = derived from loop counts.

## 1. Encode/decode pipeline per mode

### Common facts

* All modes run at **Fs = 8000 Hz** internally (`codec2_create`, codec2.c:132 → `c2const_create(8000, N_S)`), **10 ms internal subframe** (`N_S 0.01`, defines.h:39; `n_samp = 80`).
* Pitch analysis window `m_pitch = 320` samples/40 ms (defines.h:59; sine.c:69), analysis window `nw = 279` (sine.c:74), pitch period 20..160 samples (defines.h:60-61) → `Wo ∈ [0.0393, 0.314]` rad, `L = π/Wo ∈ [10, 80]` harmonics (`max_amp=80` at 8 kHz, sine.c:66; arrays sized `MAX_AMP 160`, defines.h:41).
* **Encoder subframe pipeline** — `analyse_one_frame()` (codec2.c:1694-1725), once per 10 ms:
  1. shift `Sn[320]`, insert 80 samples (codec2.c:1703-1704)
  2. `dft_speech()` — windowed 512-pt complex FFT → `Sw` (codec2.c:1706, sine.c:232-258)
  3. `nlp()` — square/notch/48-tap FIR/decimate÷5/512-pt complex FFT/peak-pick/sub-multiple search (codec2.c:1709, nlp.c:210-362)
  4. `two_stage_pitch_refinement()` — harmonic-sum search, coarse ±5 step 1.0 then fine ±1 step 0.25 (sine.c:297-326)
  5. `estimate_amplitudes()` — `A[m]=sqrt(Σ|Sw|²)` per harmonic band; atan2f phase only in ML-dump mode (sine.c:400-431)
  6. `est_voicing_mbe()` — MBE SNR over first 1000 Hz, threshold 6 dB (`V_THRESH`, defines.h:53) + eratio fix-ups (sine.c:444-548)
* **Decoder subframe pipeline** — `synthesise_one_frame()` (codec2.c:1649-1681), once per 10 ms:
  1. LPC modes: `sample_phase()` (phase.c:51-66) + `phase_synth_zero_order()` (phase.c:159-208); 700C: phase precomputed by `determine_phase()`, only `phase_synth_zero_order` runs (codec2.c:1653-1662)
  2. `postfilter()` bg-noise phase randomisation (postfilter.c:103-138)
  3. `synthesise()` — L harmonics into 512-bin spectrum, inverse real FFT, trapezoidal OLA (sine.c:593-649)
  4. gain, `ear_protection()`, int16 clamp (codec2.c:1667-1680)

### Per-mode structure

| Mode | samples/packet | dur | bits | analysis subframes | spectral quantiser | Wo/E | Source |
|---|---|---|---|---|---|---|---|
| 3200 | 160 | 20 ms | 64 | 2 | ΔLSP scalar ×10, 50 b | Wo 7b + E 5b | codec2.c:432-468 |
| 2400 | 160 | 20 ms | 48 | 2 | LSP scalar 36 b | WoE VQ 8b | codec2.c:574-611 |
| 1600 | 320 | 40 ms | 64 | 4 | LSP scalar 36 b | Wo 7b + E 5b ×2 | codec2.c:729-785 |
| 1300 | 320 | 40 ms | 52 | 4 (gray coded) | LSP scalar 36 b | Wo 7b + E 5b | codec2.c:1077-1125 |
| 700C | 320 | 40 ms | 28 | 4 | newamp1 rate-K=20, 2×9b mbest VQ | 4b E + 6b log-Wo/voicing | codec2.c:1439-1486 |

(1400 = 1600 with WoE VQ, codec2.c:909-960; 1200 = 1400 with 27-bit 3-stage lspjmv VQ, codec2.c:1264-1318.)

* NLP + sinusoidal analysis + MBE voicing run **every 10 ms subframe in every mode**. `speech_to_uq_lsps()` (quantise.c:653-693): 3200/2400 1×/20 ms; 1600/1400 2×/40 ms (codec2.c:758,775); 1300 1×/40 ms (codec2.c:1115); 1200 2×/40 ms (codec2.c:1292,1307). 700C has **no LPC**; `newamp1_model_to_indexes()` (newamp1.c:467-513): `resample_const_rate_f` (log-domain L→K=20, newamp1.c:128-156), mean removal, optional eq, 2-stage mbest VQ depth 5 (newamp1.c:168-215, newamp1.h:38), scalar energy, `encode_log_Wo` 6b.
* Decoder LPC modes: unpack → decode LSPs/Wo/E → interpolate untransmitted subframes (`interp_Wo/interp_energy/interpolate_lsp_ver2`, codec2.c:525-532, 855-866, 1194-1200) → per subframe: `lsp_to_lpc` (lsp.c:256-313), `aks_to_M2` (512-pt real FFT + `lpc_post_filter` with second real FFT, quantise.c:424-545, 322-412), `apply_lpc_correction` (quantise.c:893-897), `synthesise_one_frame`. 4 synthesis passes per 40 ms packet (1600/1300), 2 per 20 ms (3200/2400).
* Decoder 700C: `newamp1_indexes_to_model()` (newamp1.c:594-656): VQ lookup + `post_filter_newamp1` (newamp1.c:232-261) + energy → linear interp of rate-K surface 25→100 Hz (newamp1.c:523-537) → per subframe `resample_rate_L` (parabolic interp + POW10F per harmonic, newamp1.c:320-350) and `determine_phase` (interp to 65-pt grid + `mag_to_phase` cepstral method, **two 128-pt complex FFTs**, newamp1.c:363-390, phase.c:224-275) → `synthesise_one_frame` gain 1.5 (codec2.c:1542-1543).

## 2. FFT usage

kiss_fft in float (`USE_KISS_FFT`, codec2_fft.h:17-34); `FDV_ARM_MATH` swaps in CMSIS `arm_cfft_f32/arm_rfft_fast_f32` (codec2_fft.h:19-22, codec2_fft.c:80-95; enabled in stm32/unittest/src/Makefile).

| FFT | Type | Size | Used by | Where |
|---|---|---|---|---|
| `fft_fwd_cfg` | complex fwd | 512 (`FFT_ENC` defines.h:51) | `dft_speech` (enc/subframe), window init | sine.c:257, 168 |
| NLP `fft_cfg` | complex fwd | 512 (`PE_FFT_SIZE` nlp.c:49) | `nlp()` (enc/subframe); imag inputs 0, FIXME nlp.c:311-313 | nlp.c:313 |
| `fftr_fwd_cfg` | real fwd | 512 | `aks_to_M2` + `lpc_post_filter` (dec) | quantise.c:457, 349 |
| `fftr_inv_cfg` | real inv | 512 (`FFT_DEC`) | `synthesise` (dec/subframe) | sine.c:629 |
| `phase_fft_fwd/inv_cfg` | complex fwd+inv | 128 (`NEWAMP1_PHASE_NFFT` newamp1.h:35-36) | `mag_to_phase` (700C dec, 2/subframe) | phase.c:244, 264 |

Counts: **encoder (all modes): 2 complex-512 per 10 ms = 200/s** (no other encoder FFTs). **Decoder LPC modes: 3 real-512 per 10 ms = 300/s.** **Decoder 700C: 2 complex-128 + 1 real-512 inverse per 10 ms = 200 + 100/s** (no 512 forward). Both encoder FFTs are complex-on-real-data (factor 2 recoverable, drafted at sine.c:259-284); NLP FFT input has only `m/DEC = 64` nonzero points (nlp.c:303-305) — prunable.

## 3. Float usage / numerical delicacy

* `log10f`/`POW10F` (defines.h:116-120): energy quantisers (quantise.c:915, 943), WoE VQ in log2-pitch/dB-energy domain (quantise.c:1024-1025, 1080-1081; `powf(2.0,xq[0])` 1042/1120), log-Wo (quantise.c:610, 638), newamp1 dB-domain everywhere (newamp1.c:137, 346; postfilter newamp1.c:247-255 = 2 POW10F + 1 log10f per rate-K bin), voicing SNR (sine.c:498, 522), postfilter bg (postfilter.c:113, 127).
* `powf`: BW expansion `powf(0.994,i)` (quantise.c:684); worst: `lpc_post_filter` `powf(Rw[i],beta)` for **256 bins every 10 ms** (quantise.c:390) + 256 sqrtf (quantise.c:364).
* `atan2f`: `phase_synth_zero_order` — per harmonic per subframe in every mode (phase.c:205); optional in `estimate_amplitudes` (sine.c:428).
* `cosf/sinf`: excitation per harmonic (phase.c:186-195), `synthesise` (sine.c:623-624), 700C phase (newamp1.c:387-388), `lsp_to_lpc` 10 cosf (lsp.c:270), `lpc_to_lsp` 10 acosf (lsp.c:239).
* Division: `est_voicing_mbe` Am/den (sine.c:485-486), `Pw=1/(|Aw|²+1e-6)` 256 bins/subframe (quantise.c:466-481, incl. STM32-motivated loop split "1120 ms → 242 ms"), `interp_para` ~5 divides/point (newamp1.c:80-85).
* `double` occurrences: kiss twiddle init (kiss_fft.c:380-382, init-only); `pow(10.0,…)` in ML-only `determine_autoc` (newamp1.c:429-432); `pow(…,2.0)` encoder stats (newamp1.c:493); `floor()` init (sine.c:66-69, nlp.c:327-328); `PI` double literal (defines.h:43).
* Dynamic range: NLP squares 16-bit speech (≤1.07e9, nlp.c:241) then FFT + magsq → `Fw.real` up to ~1e18 (nlp.c:316-317) — beyond Q31; the "+1.0" hack (nlp.c:274-281) exists because tiny values already stall kiss_fft. Energy −10..40 dB (quantise.h:38-39). 700C clips envelope to a **50 dB window below peak** (newamp1.c:146-151) — fixed-point friendly. LSPs ∈ (0,π), guarded by `check_lsp_order` (quantise.c:266-282), `bw_expand_lsps` (quantise.c:843-861); Levinson clamps |k|>1→0 (lpc.c:156); LSP root search grid delta 0.01 (quantise.c:47, lsp.c:131-243) is precision sensitive (encoder only).

## 4. Memory footprint (MEASURED)

RAM/state per instance: `struct CODEC2` **3920 B** (incl. W[512] 2048, prev MODEL 1300; codec2_internal.h:36-97); `Sn` 1280; `w` 1280; `Pn` 640; `Sn_` 640; **dead `bpf_buf` 1684 B** (alloc/zero/free only, codec2.c:201-203, 285); NLP state **1768 B** (nlp.c:87-97); kiss cfgs: complex-512 fwd 4360 (codec) + 4360 (NLP duplicate) + real-512 fwd 5408 + real-512 inv 5408; 700C adds 2×1288 (complex-128). `MODEL` = 1300 B (defines.h:86-92). **Totals ≈30.7 KB (LPC modes) / 33.3 KB (700C)**; kiss cfgs ≈19.5 KB are runtime-generated twiddles (kiss_fft.c:361-383; caller-memory API exists, kiss_fft.h:57-77).

Stack peaks: encoder ≈**12.5 KB** (`Sw[512]` 4096, codec2.c:1695 + `Fw[512]` 4096, nlp.c:220 + inplace copy 4096, codec2_fft.c:135-138); decoder LPC ≈**15-16 KB** (`model[4]` 5200 + `Aw[512]` 4096, codec2.c:1139-1149; aks_to_M2 `a[512]`+`Pw[256]` 3072, quantise.c:450-463; lpc_post_filter `x`+`Ww`+`Rw` 5132, quantise.c:326-328; synthesise 4104, sine.c:600-601); decoder 700C ≈**11 KB** (`HH[4][161]` 5152, codec2.c:1515; mag_to_phase 4×COMP[128] 4096, phase.c:228).

Flash codebooks (generated with `generate_codebook`, headers verified): `lsp_cb` 132 floats = **528 B** (2400/1600/1400/1300); `lsp_cbd` **1280 B** (3200); `lsp_cbjmv` **40960 B** (1200); `ge_cb` **2048 B** (2400/1400/1200); `newamp1vq_cb` 2×512×20 = **81920 B** + `newamp1_energy_cb` 64 B (700C). Scalar LSP tables are integer Hz (codebook/lsp1.txt) → int16-compressible; 700C VQ is dB in ±~20 dB → int8 plausible (ESTIMATE 4× → ~20.5 KB).

Flash code: -Os host build of core = **55.7 KB text** (codec2.o 18.4 KB all modes, quantise.o 10.0 KB, newamp1.o 6.9 KB, kiss_fft 3.1 KB, nlp 2.7 KB…). Single-mode decode-only ESTIMATE 12-20 KB.

## 5. Cost per frame (ESTIMATE; cFFT512 ≈10k MAC, rFFT512 ≈5.5k, cFFT128 ≈2.2k)

Encoder per 10 ms: dft_speech 10.3k; NLP FFT 10k; NLP FIR 48×80 = **3840 MAC** (run at full rate before decimation, nlp.c:286-293 — 5× reducible); NLP misc 1.4k; pitch refinement 0.4-3.2k (≈20 candidates × L × 2, sine.c:345-388); estimate_amplitudes 0.8k + ≤80 sqrtf; voicing 0.7k. **≈28k MAC/10 ms**. Per-packet: autocorrelate 3.5k (lpc.c:121-124) + Levinson 0.1k + lpc_to_lsp 1.5-15k + 10 acosf; LSP scalar search 0.3k; 1200 jmv 10.2k; **700C mbest 61.4k/40 ms** (512×20 + 5×512×20, newamp1.c:188-197). Totals: 3200/2400 ≈3.1 MMAC/s; 1600/1300 ≈3.0; 1200 ≈3.2; **700C ≈4.4 MMAC/s**; + 10-20k transcendentals/s. Top-5 encoder: (1) 2× cFFT512 ≈2 MMAC/s; (2) 700C mbest 1.5 MMAC/s / 1200 jmv 0.26; (3) NLP FIR 0.38 MMAC/s; (4) pitch refinement 0.1-0.3; (5) autocorrelation 0.1-0.35.

Decoder LPC per 10 ms: aks_to_M2 rFFT 5.5k; Pw 0.5k + **256 fdiv**; lpc_post_filter rFFT+misc 7k + **256 sqrtf + 256 powf**; band mags 0.5k + ≤80 sqrtf; lsp_to_lpc 0.25k + 10 cosf; phase 0.5k + ≤80×(cos+sin+atan2); postfilter 0.2k; synthesise 6.3k + ≤80×(cos+sin). **≈21k MAC + ~750 transcendental calls/10 ms → 2.1 MMAC/s + ~75k transc/s** (25.6k powf/s + 25.6k div/s + 25.6k sqrtf/s from postfilter path alone). Top-5: 3× rFFT512; lpc_post_filter powf/sqrtf; phase_synth trig/atan2; synthesise trig; Pw reciprocals. Anchor: **1300 decode = 17-21 ms per 40 ms frame @168 MHz STM32F4 with FPU+CMSIS** (quantise.c:469-474) ≈70 MIPS-equivalent.

Decoder 700C per 10 ms: 2× cFFT128 5k; rFFT512 inv 6.3k; interp ≈1.5k (~30 div); L× log10f + L× POW10F + L×2-4 trig + L× atan2; per-packet postfilter ~60 exp/log on K=20. **≈13-14k MAC + 400-500 transc/10 ms → 1.4 MMAC/s + ~45k transc/s** — cheapest decoder.

## 6. Encoder-only vs decoder-only

Encoder-only: nlp.c entirely (+cfg+state ≈6.1 KB), `dft_speech`, pitch refinement, `estimate_amplitudes`, `est_voicing_mbe`, `make_analysis_window` + `W[]`/`w[]`/`Sn[]` buffers (codec2_internal.h:44-48), `speech_to_uq_lsps`/`autocorrelate`/`levinson_durbin`/`lpc_to_lsp`, all `encode_*`, mbest.c, `newamp1_model_to_indexes` chain. Decoder-only: interp.c, `lsp_to_lpc`, `aks_to_M2`+`lpc_post_filter`, `sample_phase`, `phase_synth_zero_order`, `mag_to_phase`, postfilter.c, `synthesise`+`make_synthesis_window`, all `decode_*`, `newamp1_indexes_to_model` chain. Shared: pack.c (pure integer), codebooks, kiss core. Decode-only needs only rFFT512 fwd+inv (LPC modes) or cFFT128×2+rFFT512 inv (700C); heap falls to ≈14 KB as-is, ESTIMATE 4-6 KB after restructuring (const twiddles, drop phi[] for 700C, subframe-at-a-time synthesis instead of `model[4]`+`Aw[512]` stack blocks).

## 7. Existing fixed-point / embedded accommodations

kiss_fft FIXED_POINT 16/32 with saturating fractional macros (kiss_fft.h:36-48, _kiss_fft_guts.h:60-107) — but codec2's wrapper is float-typed (codec2_fft.h:30-34), so a conversion layer is required. `__EMBEDDED__` → const codebooks in flash (defines.h:100-104), no file I/O (codec2.c:1875-1897). `CORTEX_M4` strips ML dumps (codec2.c:1463-1478). `CODEC2_MODE_EN*` compile-time mode stripping with constant-folding `CODEC2_MODE_ACTIVE` (codec2.h:46-82, codec2.c:115-123). stm32/ contains a working STM32F407 port (`-DSTM32F40_41xxx -DCORTEX_M4 -D__EMBEDDED__`, stm32/CMakeLists.txt:69) with machdep profiling and CMSIS FFT option. Already integer: pack/unpack+gray, `codec2_rand` LCG (sine.c:652-657). Fixed-point-friendly data: integer-Hz LSP codebooks, 50 dB-clipped 700C envelopes, 6-7-bit Wo indices. Memory-aware helpers: `codec2_fft_inplace` (codec2_fft.c:129-141), kiss `lenmem` static placement, drafted real-FFT `dft_speech` (sine.c:259-284).

**Bottom line:** decode-only fixed-point **700C** is the best algorithmic fit (1.4 MMAC/s, no LPC powf storm, no NLP, 28-bit frames) at the price of 82 KB (int8: ~20 KB) VQ flash; decode-only **1300** costs 0.5 KB tables but is transcendental-bound unless `lpc_pf` is simplified (toggle exists, codec2.c:1763). A straight float port cannot fit 20-50 integer MIPS (STM32F4-with-FPU anchor ≈70 MIPS for 1300 decode alone); the delicate fixed-point work is NLP dynamic range, dB↔linear conversions, per-harmonic trig/atan2, and (encoder-only) LSP root finding. RAM must be restructured from today's ~30 KB heap + 12-16 KB stack toward a 4-6 KB decoder to approach the 2-8 KB class.
