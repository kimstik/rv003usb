# Codec2 on Tiny MCUs — BRAINSTORM Track Report

Target family: CH32V003 (RV32EC, 48 MHz, 2 KB RAM, 16 KB flash, **no HW multiplier/divider**), PY32F003 (Cortex-M0+, ~24–48 MHz, 3–8 KB RAM, 16–64 KB flash), CH570 (RISC-V + BLE, HW mul), CH32V203 (RV32IMAC, 144 MHz, 20 KB RAM, 64 KB flash). MCU spec values: PLAUSIBLE (common datasheet knowledge, not re-verified here except V003 via the rv003usb repo context).

Evidence labels: **VERIFIED** = checked in the codec2 source in this session. **PLAUSIBLE** = standard DSP knowledge / derivable, not measured here. **SPECULATIVE** = needs an experiment to believe.

---

## 0. Ground truth: what the pipeline actually costs (VERIFIED from source)

### Decoder, LPC modes (3200 / 2400 / 1600 / 1300), per 10 ms subframe
All LPC modes converge on the same per-subframe machinery (`codec2.c`, `quantise.c:aks_to_M2`, `sine.c:synthesise`, `phase.c`):

| Stage | Cost per 10 ms subframe | Source |
|---|---|---|
| `aks_to_M2` LPC→amplitudes | 512-pt **real FFT** + 256 float divides | quantise.c:424 |
| `lpc_post_filter` | a **second** 512-pt real FFT + 256 `sqrtf` + **256 `powf`** + 512 mults | quantise.c |
| `phase_synth_zero_order` | per harmonic (L ≤ 80): `cosf`+`sinf`+cplx mult+`atan2f` | phase.c:159 |
| `synthesise` | 512-pt real **IFFT** + windowed overlap-add | sine.c:593 |
| `lsp_to_lpc`, interp, unpack | per-*frame* (20/40 ms), small | codec2.c |

So the float decoder ≈ **3× 512-pt real FFTs + ~256 powf + ~80 atan2 + ~160 sincos per 10 ms** = 300 FFTs/s + 25,600 powf/s + 8,000 atan2/s. Important: **1300 decode calls `aks_to_M2` + `synthesise` 4× per 40 ms — the same 100 subframes/s as 3200.** Lower bitrate ≠ lower decoder MIPS (VERIFIED codec2.c decode_1300 loop).

An in-tree comment (quantise.c) records **~21 ms → 17 ms per 40 ms frame for 1300 decode on STM32F4** (168 MHz, HW FPU) — decode alone ≈ 0.42× realtime on an FPU part. Corollary: float on a 24–48 MHz soft-float M0+/RV32EC is hopeless; fixed-point + algorithm cuts are mandatory.

### Decoder, 700C, per 40 ms frame (4 subframes)
- Unpack 9+9+4+6 bits; 2-stage VQ lookup (2 × 512 × 20 **dB-domain** float tables = 80 KB flash as float). VERIFIED (`train_120_1/2.txt` headers "20 512"; values are dB).
- `post_filter_newamp1`: works **entirely in dB at K=20 points**; only 2×20 `POW10F` for energy normalisation. VERIFIED (newamp1.c). Already 90 % of the way to LNS.
- `resample_rate_L`: parabolic interp (`interp_para`, 3 divides/point). VERIFIED.
- `determine_phase` ×4: interp to 65 pts + `mag_to_phase` = **two 128-pt complex FFTs** each (min-phase via cepstrum), then per-harmonic sincos. VERIFIED (newamp1.c:354, phase.c:224).
- `synthesise` ×4: 512-pt real IFFT each. VERIFIED.

**No LPC, no LSP, no lpc_post_filter, no powf storm, no mel warp at decode** (mel only positions the fixed rate-K grid, computed once at init — VERIFIED). 3200 has no mel warp either (scalar LSP-difference quant — VERIFIED encode_3200).

### Encoder (all modes), per 10 ms
`analyse_one_frame`: NLP pitch = squaring + 48-tap FIR at 8 kHz (384 k MAC/s) + **512-pt complex FFT** + peak/submultiple search (VERIFIED nlp.c); `dft_speech` = another 512-pt complex FFT; `two_stage_pitch_refinement` ≈ 24 candidate pitches × L bin sums; `estimate_amplitudes`; `est_voicing_mbe` (bins < 1 kHz only). LPC modes add autocorrelation (10×320 MAC), Levinson, LPC→LSP root search. 700C encode adds mbest VQ search ≈ 2×512×20 MACs per 40 ms (≈ 0.5–1.5 MMAC/s — small). VERIFIED structure.

### Memory ground truth
- `struct CODEC2` holds `W[512]` floats (2 KB) + heap `Sn[320]`, `w[320]`, `Pn`, `Sn_`, `bpf_buf` — several KB of float state, most **encoder-only**. VERIFIED (codec2_internal.h).
- kiss_fft in-tree already has **`FIXED_POINT` int16/int32 support** (kiss_fft.h:36–46). VERIFIED. Near-drop-in.
- Modes strippable at compile time: `CODEC2_MODE_EN_DEFAULT=0 -DCODEC2_MODE_3200_EN=1`. VERIFIED (codec2.h:46–77).
- STM32F4 embedded port exists in-tree (`stm32/`) — float+FPU; useful as harness/reference only. VERIFIED.

---

## 1. Scope cuts

**S1. Decode-only port first (RX radio/toy).** Dropping the encoder removes both 512-pt analysis FFTs, NLP FIR, voicing estimation, LPC/LSP analysis — roughly half the MIPS and most of the awkward float state. VERIFIED clean split at `c2->encode/decode` function pointers.
Score: feasibility **5**, saving **~50 % + removes hardest stages**, quality risk **none**, effort **S**. Kill test: none needed; grep confirms no decode path touches `analyse_one_frame`.

**S2. Single-mode build; which mode?**
- *3200*: cheapest flash (scalar LSPd tables, no VQ), lowest latency, no mel warp (VERIFIED), full aks_to_M2+postfilter per 10 ms.
- *1300*: same decoder MIPS as 3200 (VERIFIED), 1-bit voicing, more interpolation; good for radio.
- *700C*: **cheapest decoder CPU once phase FFTs are substituted** (no LPC/powf, dB-native amplitudes), but codebooks need int8/int4 compression and ~20+ KB flash.
Recommendation: 700C for 32–64 KB parts; 1300/3200 for 16 KB parts.
Score: feasibility **5**, saving **large**, risk **none**, effort **S**. Kill test: static — link each single-mode build, compare .text/.rodata; count FFT calls/s per mode from source.

**S3. Compile-time strip of legacy modes** — already supported upstream (VERIFIED). Effort **S**, day 1.

**S4. Drop `lpc_post_filter` in v0, restore in log-domain later.** It is one of the three 512-pt FFTs *and* all 25.6 k powf/s; on by default (VERIFIED `c2->lpc_pf=1`) and quality-significant.
Score: feasibility **5**, saving **~1/3 of decoder FFT cost + all powf**, risk **medium** (duller speech), effort **S**. Kill test: host decode with pf off, ABX/PESQ delta on a small corpus.

**S5. Fs=8000-only build.** Strip Fs==16000 branches (NLP resampler, nw=511). VERIFIED both exist. Feasibility **5**, saving small-medium, risk none, effort **S**.

---

## 2. Arithmetic strategies

**A1. Q-format plan per stage.** PLAUSIBLE, standard:
- samples & windows: **Q15**; synthesis accumulation in int32.
- Wo: store as **Q9.7 FFT-bin increment** (Wo·N/2π ∈ [3.2, 25.6] for N=512) so harmonic bin = integer add per harmonic.
- log-magnitudes: **Q8.8 in log2 units** (or Q6.10). Energy dB: Q8.8.
- LSPs: Q2.13 radians; LPC ak: Q4.11 (order-10 coeffs can exceed ±4; needs range instrumentation).
- phases: **unsigned Q0.16 turns** — wraps free on uint16 overflow, kills all `floorf(x/2π)` range reduction.
Score: feasibility **4**, enabling, risk **low if instrumented**, effort **M**. Kill test: instrument the float build with min/max recorders per signal; confirm chosen formats never clip on a corpus.

**A2. kiss_fft FIXED_POINT=16 as drop-in.** VERIFIED code paths exist. Caveat: per-stage right-shifts lose ~9 bits on quiet 512-pt frames → pair with A3.
Score: feasibility **5**, saving **huge on soft-float parts**, risk **medium (SNR floor)**, effort **S**. Kill test: host build with `-DFIXED_POINT=16`; kill if segmental SNR of synthesized speech < ~30 dB vs float.

**A3. Block floating point around the FFT.** Normalize each frame to full scale (CLZ), carry exponent; exponent folds into the log-magnitude domain as an **integer add**, since amplitudes go log anyway — synergy unique to this codec. PLAUSIBLE. Feasibility **4**, rescues A2 quality, risk **low**, effort **M**. Kill test: A2 harness with/without BFP.

**A4. CORDIC for sincos/atan2/magnitude.** Shifts/adds only → attractive on V003. But C6 *eliminates* atan2, and F3 eliminates per-sample sincos; CORDIC then only serves per-harmonic-per-frame phasor generation (~8 k/s — cheap either way). On mul parts, LUT+lerp wins.
Feasibility **5**, saving **medium on V003, small elsewhere**, risk **none**, effort **M**. Kill test: static count of surviving transcendental call sites after C6/C9/F3; if < ~20 k/s, a 1 KB LUT wins and CORDIC is dead.

**A5. LUT+interp for log2/exp2/sqrt + "work in log2, not dB".** Rescale all dB constants and **the VQ codebooks offline from dB to log2 units** (lossless affine table transform) so runtime never multiplies by 20/log₂10. `POW10F(x/10)` → one exp2 LUT walk; sqrt in log domain = shift right 1.
Feasibility **5**, saving **large (kills powf/log10f/sqrtf classes)**, risk **low**, effort **M**. Kill test: host golden model with 256-entry+lerp tables; max dB-path error < 0.1 dB — pure numerics.

**A6. Where 32×32 is unavoidable vs 16×16.** 16×16→32 suffices for FFT butterflies, oscillators, windows, int8 VQ MACs. 32-bit/48-bit accumulation needed for band-energy sums and (encoder) autocorrelation — handle with BFP/shifts. Division: only `Pw=E/|Aw|²` (256/subframe) and interp_para — both removed by LNS (negate log) and precomputed-slope interp.
Feasibility **4**, enabling, risk low, effort **M**. Kill test: static census annotating every `*` and `/` in the decode call graph with operand widths; count survivors needing 32×32.

**A7. Mul-free formulations for RV32EC:**
- *(a) LNS for the whole spectral path* — see F1; the amplitude path is **already logarithmic by design** (VERIFIED dB codebooks).
- *(b) Quarter-square multiplier*: a·b = QS(a+b)−QS(a−b); 9-bit-operand table = 1 KB flash; competitive with gcc `__mulsi3` only for 8–9-bit operands → use for int8 VQ math, window scaling. PLAUSIBLE.
- *(c) Distributed arithmetic* for fixed-coefficient FIRs — only encoder NLP FIR qualifies; decode-only port doesn't need it.
- *(d) Shift-add constants*: synthesis window is a trapezoid (VERIFIED) — flat section is a copy; only 2×tw ramp samples/frame need multiplies; choose 2·tw = power of two.
Combined: feasibility **3–4**, **enables V003 at all**, risk low-med, effort **L**. Kill test: cycle-model spreadsheet: (#muls/s from A6 census) × (measured `__mulsi3` cycles, ~35–110); if > ~35 MIPS after LNS+F3, V003 full decode dies → fall back to F7 or system split (E).

---

## 3. Algorithm substitutions

**C1. Pitch: ASDF/AMDF at 2 kHz instead of NLP's FFT** (encoder). Decimate ×4 (box/CIC — adds only), ASDF for lags 5–40 @2 kHz ≈ 0.3 MMAC/s vs NLP's 512-pt FFT + 384 k MAC/s FIR. Crucially keep NLP's *sub-multiple post-processing logic* (VERIFIED nlp.c post_process_sub_multiples) applied to ASDF minima — that is where the robustness lives. PLAUSIBLE.
Feasibility **4**, saving **large (encoder)**, risk **medium (gross pitch errors)**, effort **M**. Kill test: swap estimator behind the `nlp()` interface on host; kill if gross-error rate (>20 % deviation) > ~2× NLP's on a corpus.

**C2. Goertzel banks.** Decoder needs no analysis bins (decode-only). Encoder's `est_voicing_mbe` uses only bins < 1 kHz (VERIFIED), but pitch refinement + amplitude estimation touch all bins → the full FFT amortizes better. Verdict: documented dead-end; keep one encoder FFT, kill only NLP's via C1.
Feasibility **3**, saving small, effort **M**. Kill test: static bin-coverage count per consumer (done — Goertzel loses).

**C3. Sparse synthesis: top-N harmonics.** Amplitudes are in dB before synthesis → threshold at (max − 30 dB) or top N≈24; UV frames use shaped LFSR noise instead of 80 random-phase sinusoids. Cuts F3 cost 2–3×. PLAUSIBLE.
Feasibility **4**, saving **medium-large**, risk **medium (muffling)**, effort **S**. Kill test: host sim with top-N mask, PESQ/ABX vs N curve; if knee is at N > 60, idea dead.

**C4. Recurrence/phasor oscillators vs per-sample cosf.** 2 muls/sample/harmonic; at L=80 that is 1.28 M mult/s — **loses to the 512-pt IFFT** (~0.9 M mult/s for all harmonics + OLA) on mul-capable chips. So: IFFT synthesis on mul chips; F3 (log-wavetable) on mul-free chips; phasors only as a stepping stone. Kill test: static mult-count comparison (done).

**C5. Postfilter in the log domain.** LPC modes: `Pw *= Rw^{2β}` → `PwdB += 2β·RwdB` (256 powf → 256 MACs); better, apply at the ≤80 harmonics *after* band summation (reordering — must A/B). 700C: `post_filter_newamp1` is already dB-domain at K=20 (VERIFIED); only its two 20-term energy sums need exp2-LUT + Gauss-log addition.
Feasibility **5**, saving **large**, risk **low-medium**, effort **S-M**. Kill test: bin-domain vs harmonic-domain A/B, spectral distortion + ABX.

**C6. Eliminate the atan2→sincos round trip** *(cheap, exact, VERIFIED)*. `phase_synth_zero_order` computes `phi = atan2f(A_.imag, A_.real)` (phase.c:205); `synthesise` immediately recomputes `cos(phi), sin(phi)` (sine.c:623). Replace with `(A[m]/|A_[m]|)·A_[m]` — one Newton rsqrt per harmonic instead of atan2+cos+sin, bit-identical in exact arithmetic.
Feasibility **5**, saving **medium (8 k atan2 + 16 k sincos /s)**, risk **none**, effort **S**. Kill test: grep that `model->phi[]` has no other decode-path consumer + regression SNR.

**C7. Replace `interp_para` divides.** 3 divides/point (VERIFIED) at 100+ points/subframe; the rate-K grid is **fixed** (VERIFIED init-time mel grid) → precompute reciprocals, or drop to linear interp in dB.
Feasibility **5**, saving medium, risk low, effort **S**. Kill test: envelope RMS error linear-vs-parabolic (< ~0.5 dB).

**C8. Exploit decimated frame rate harder.** Do the expensive envelope→harmonics conversion at 40 ms rate and interpolate **per-harmonic log-amplitudes** at 10 ms rate, instead of running aks_to_M2 4× (current 1300 interpolates LSPs then does full LPC→amp per subframe — VERIFIED). Up to ~4× on the dominant stage. SPECULATIVE quality-wise.
Feasibility **3**, saving **large**, risk **medium**, effort **M**. Kill test: host sim of AmdB interpolation vs reference; PESQ delta.

**C9. Cepstral-series min-phase instead of `mag_to_phase`'s FFT pair** (700C). Min-phase identity: θ(ω) = −2·Σ_{n≥1} c[n]·sin(nω), c[n] = real cepstrum of log|H|. Envelope lives on K=20 points → c[1..16] via a precomputed 20×16 matrix (320 MACs), then θ at L harmonics (L×16 MACs + sin LUT) ≈ 1.6 k MAC vs two 128-pt complex FFTs + 65-pt interp, ×4 per frame. Truncation error is the only approximation, measurable offline. PLAUSIBLE identity; SPECULATIVE until tested.
Feasibility **4**, saving **large (kills all 700C phase FFTs — 200 FFTs/s)**, risk **low-medium**, effort **M**. Kill test: phase RMS error vs mag_to_phase over a corpus; kill if > ~0.2 rad *and* audible in ABX. Also test the lazy bracket: skip min-phase entirely (excitation phase only) to bound the risk.

---

## 4. Memory tricks

**M1. Codebooks → int8/int4 in flash.** 700C VQ: 80 KB float → **20 KB int8** (dB fits int8 at 0.5 dB LSB; observed values ±~18 dB — VERIFIED sample rows) → **10 KB int4** with per-vector scale or delta across dims. Decode = table read + shift-add rescale; int8 VQ distances are cheap integer MACs for the encoder too.
Feasibility **5**, flash saving **60–70 KB**, risk **low**, effort **S-M**. Kill test: quantize offline, run float-otherwise c2dec; kill int4 if extra spectral distortion > ~1 dB² vs int8.

**M2. Invert the desktop advice: store in flash, don't generate into RAM.** Decode-only needs no `w`/`W` at all (encoder-only — VERIFIED consumers), and the synthesis window is a trapezoid computable inline with an accumulator (VERIFIED) → the decoder needs **zero window tables**.
Feasibility **5**, RAM saving **~3–4 KB**, risk none, effort **S**. Kill test: static rodata/state listing after decode-only strip.

**M3. Overlay scratch buffers.** `Fw[512] COMP`, `a[512]`, `Pw[256]`, `Ww[257]`, `Sw_[257]`, `sw_[512]` are live in disjoint phases (VERIFIED per-function locals). One static union (~2 KB int16) replaces ~10 KB peak stack. Mandatory below 8 KB RAM.
Feasibility **5**, RAM saving **large**, risk none, effort **S-M**. Kill test: mechanical buffer-liveness table across the call graph.

**M4. Is streaming decode state ≤ 2 KB even in theory? Yes — if FFT-free.** Minimal 700C-LNS decoder: prev rate-K vec (40 B) + Wo/voicing/energy + phase accumulators (≤160 B) + output frame (160 B) + scratch ≈ **< 1 KB**. With int16 IFFT synthesis instead: +2 KB scratch → does *not* fit V003 alongside stack + USB. Conclusion: on 2 KB parts, oscillator synthesis (F3) is not optional. PLAUSIBLE. Kill test: static `sizeof` audit of the proposed state struct.

**M5. Flash as LUT space.** V003 16 KB: code 4–6 KB + exp2/log2 (1 KB) + log-sine wavetable (1–2 KB) + Gauss-log table (0.5 KB) + cepstral matrix (0.3 KB) + int4 VQ 10 KB → **over budget** ⇒ V003 gets 1300 (tiny tables) or a pruned 256-entry VQ (SPECULATIVE quality). 32–64 KB parts: full int8 700C fits with room. Kill test: static flash-map spreadsheet per configuration.

---

## 5. System-level creativity (rv003usb synergy)

**E1. RX dongle: host encodes / V003 decodes → PWM audio.** Low-speed USB interrupt EP = 8 B/poll; even at 10 ms polling that is 6.4 kbit/s > 3.2 kbit/s — **coded bits fit trivially; raw PCM does not** (128 kbit/s). Natural split: bits over the wire, DSP at the edge. PLAUSIBLE (USB LS arithmetic; rv003usb is software LS USB — VERIFIED repo README).
Feasibility **4** (given F3), effort **M**. Kill test: static bandwidth table incl. framing; then loopback latency with the existing HID demo.

**E2. TX dongle: V003 does *analysis-lite*; bitstream stays valid.** Key insight: **the bitstream is just packed quantized model parameters (VERIFIED pack() calls), so bitstream compatibility does not require algorithm compatibility.** V003-grade encoder: ASDF pitch (C1), autocorrelation LPC (10×160 MAC/10 ms — trivial), energy, voicing from ZCR + pitch-gain + low/high-band ratio, scalar LSP quant → emits valid 3200/1300 frames. PLAUSIBLE/SPECULATIVE (voicing substitute is the risk; VERIFIED phase.c comment says voicing errors sound clicky/staticy).
Feasibility **3**, effort **L**, risk **medium-high**. Kill test: host prototype → encode → reference decode → PESQ + voicing-error rate; kill if voicing errors > few %.

**E3. TX split v1: 4-bit IMA-ADPCM uplink (32 kbit/s fits LS budget with margin; 8-bit μ-law at 64 kbit/s has zero margin), host runs the real encoder.** Zero-DSP V003, full quality minus small transcoding loss.
Feasibility **5**, effort **S**, risk **low**. Kill test: tandem ADPCM→codec2 PESQ loss (< ~0.1).

**E4. Two-chip / CH570 BLE walkie-talkie.** CH570 (BLE + HW mul) runs the Fairway-A engine and uses **BLE as the channel** — 3.2 kbit/s is trivial; V003 does UI or is dropped. Turns the port into a product (toy walkie-talkie, babyphone). SPECULATIVE until CH570 MHz/RAM pinned.
Feasibility **3–4**, effort **L**. Kill test: cycle model vs datasheet MIPS; split if > ~60 % load.

**E5. Overclocking V003 (community overclocks exist — PLAUSIBLE).** Treat as margin only, never design budget. Policy decision, no kill test.

---

## 6. Wild but checkable

**F1. LNS end-to-end for the spectral path.** ⭐ The codec is already half-LNS: codebooks in dB, postfilters in dB, thresholds in dB (all VERIFIED). Keep every amplitude as log2(A) Q6.10 from bitstream to oscillator: multiply→add, divide→subtract, sqrt→shift, `Rw^β`→shift-add, `E/|Aw|²`→negate-and-add. Only amplitude *additions* (band sums, energy normalization) leave the log domain — Gauss-log table (max + correction LUT) for few-term sums, exp2 only at the final sample sum. On no-mul RV32EC the amplitude path becomes loads and adds. PLAUSIBLE; the fit to this codec is this report's central creative claim.
Feasibility **3**, **enables V003**, risk **medium (LUT noise accumulation)**, effort **L**. Kill test: python golden model with parameterized LUT widths → SNR curve; kill if 16-bit tables can't reach ~25–30 dB seg-SNR vs float.

**F2. 8-bit NN distillation — honest check: NO.** LPCNet-class needs MBs + ≫100 MMAC/s (the in-tree `fmlfeat` hooks — VERIFIED — exist to feed *bigger* models, not smaller). A micro-MLP replacing resample/interp costs ≈ what it replaces with no quality guarantee. Salvageable crumb: **offline codebook remapping** (bake enhancement into decoder tables, zero runtime cost) — but `post_filter_newamp1` acts on the *summed* two-stage vector (VERIFIED), so only partial baking works.
Feasibility **2**, risk high. Static kill criterion: any net whose MAC/frame exceeds the replaced stage is DOA — this already kills all obvious candidates.

**F3. Log-wavetable additive synthesis: phase accumulators + log-sine ROM.** ⭐ Classic additive-synth hardware ran dozens of partials at low clocks using phase accumulator + waveform ROM + log-domain amplitude (proven in silicon). Software inner loop per harmonic-sample: `acc += inc` (uint16 wrap); `s = logsin[acc>>6]`; `out += exp2[(logA + s)>>k]` — **no multiplies**, ~8–16 RV32EC cycles. L=80 → 640 k harmonic-samples/s ≈ 5–10 MIPS; top-24 (C3) ≈ 2–4 MIPS. Excitation phase = m·φ₁ (adds); envelope phase from C9. Replaces IFFT+OLA; frame transitions via per-sample log-amplitude ramps (adds). Noise floor set by exp2 LUT width — measurable. SPECULATIVE as a pipeline; components PLAUSIBLE.
Feasibility **3–4**, **the V003 enabler**, risk **medium (quantization hiss, UV noise quality)**, effort **L**. Kill test: python golden model vs float `synthesise` on decoded params; sweep LUT sizes; kill if ~2 KB of tables can't reach ~30 dB seg-SNR / acceptable ABX.

**F4. Differential porting: soft-float allowed in cold spots.** ⭐ Per-*frame* code (unpack, LSP decode, lsp_to_lpc, interp weights, VQ index math) runs at 25–100 Hz × O(100) flops = **< 0.1 MIPS even as gcc soft-float** — port it last or never. Only per-sample/per-bin loops must be fixed-point. Cuts porting surface ~70 % of lines for ~2 % of cycles. PLAUSIBLE; loop-rate census VERIFIED from structure.
Feasibility **5**, saving **effort**, risk none. Kill test: mechanical loop-trip-count annotation of the decode call graph.

**F7. LPC-IIR "floor" decoder for LPC modes.** ⭐ Guaranteed-feasible fallback: the 3200/1300 bitstream carries LSPs+Wo+E+voicing (VERIFIED) → classic LPC-10-style time-domain synthesis (pulse/noise excitation → 10-tap IIR) at ~10 MAC/sample ≈ **1–3 MIPS integer, ~1 KB RAM, no FFT, no tables**. codec2's own comment says phase0 sinusoidal synthesis is "effectively the same model, yet sounds much better" (VERIFIED phase.c) — quality drops toward LPC-10e but stays intelligible; shippable v0 for V003 with a *valid codec2 bitstream*.
Feasibility **5**, saving **~10×**, risk **high but bounded/known**, effort **M**. Kill test: can't be killed, only ranked — informal DRT vs reference decode.

---

## 7. Porting fairways (форватеры)

### Fairway A — "Integer mainstream" (CH32V203 / CH570 / PY32 with mul; 32–64 KB flash)
1. **Gate 0 (static):** single-mode decode-only build (S1–S3, S5); flash/RAM map; loop census (F4).
2. int16 kiss FFT + BFP (A2+A3); C6; log-domain postfilter (C5/S4); Q-plan with overflow instrumentation (A1, A6). **Gate 1:** host fixed-point seg-SNR ≥ ~30 dB vs float, PESQ delta < ~0.2.
3. int8 codebooks (M1), overlays (M3), LUT transcendentals (A5). **Gate 2:** measured cycles ≤ 50 % budget for decode.
4. Encoder: ASDF pitch (C1) + one analysis FFT + fixed-point voicing. **Gate 3:** voicing- and pitch-gross-error rates vs reference.
5. Product: CH570 BLE walkie-talkie (E4).
End state: full duplex 1300/700C on 40–60 MIPS mul-capable parts.

### Fairway B — "No-FFT LNS decoder" (CH32V003 / smallest PY32; the creative bet)
1. **Gate 0 (static):** mult/div census of 700C decode (A6); state sizeof ≤ 1.5 KB (M4); flash map with int4 VQ (M5) — if > 16 KB, retarget mode 1300 or PY32F003x8.
2. Python golden model: log2 tables (A5), LNS envelope path (F1), linear rate-K interp (C7), cepstral min-phase (C9), top-N log-wavetable synthesis (F3+C3), shaped-noise UV. **Gate 1 (the big one):** seg-SNR/ABX vs float 700C — each substitution independently revertible to a costlier variant.
3. C port with soft-float only at parameter rate (F4); cycle model ≤ 30 MIPS. **Gate 2:** measured on a V003 board.
4. Fallback ladder: N↓ / LUTs↑ → Fairway-A engine on PY32 → **F7 LPC-IIR floor on V003 (cannot fail)**.
End state: RX-only speech on a 10-cent MCU, ~1 KB RAM, zero FFTs — fully validatable on host *before any firmware exists*.

### Fairway C — "System split around rv003usb" (ships while B matures)
1. RX dongle: host encoder → 3.2 kbit/s over LS USB (fits) → V003 decoder (Fairway-B engine, or F7 floor on day 1) → PWM. **Gate:** end-to-end latency < ~120 ms.
2. TX v1: 4-bit ADPCM uplink (E3) + host codec2 encoder. **Gate:** tandem PESQ loss < ~0.1.
3. TX v2: bitstream-compatible analysis-lite encoder on-chip (E2). **Gate:** voicing errors < few %, else stay at v1.
4. Capstone: CH570-only BLE handheld (E4).

---

## 8. Top-5 ranked bets

| # | Idea | Why it wins | Cheapest kill test |
|---|---|---|---|
| 1 | F1+A5: LNS/log2 spectral path | codec is already half-log (VERIFIED); mul-free amplitude math | python golden model, SNR vs LUT width |
| 2 | F3+C3: top-N log-wavetable synthesis | removes all decoder FFTs; ~2–4 MIPS mul-free | golden model, ABX vs N |
| 3 | C9: cepstral-series min-phase | kills 200 FFTs/s in 700C with a 20×16 matrix | phase RMS error vs mag_to_phase |
| 4 | C6: atan2/sincos round-trip removal | exact, verified redundancy, free | grep phi consumers + regression |
| 5 | E2: bitstream ≠ algorithm compatibility | unlocks V003-grade encoders, staged quality | substitute-analysis PESQ on host |

Cheapest overall discovery: **decoder MIPS ranking is 700C < 3200 ≈ 1300 (once phase FFTs are substituted), while flash ranking is the reverse — so "which mode" is a function of the chip's flash size, not of the bitrate.**
