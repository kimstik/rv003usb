# METRICS Track Report: Controlling Quality Loss in a Float→Fixed Port of Codec2

**Question addressed:** "как контролировать потери?" — how do we control quality loss when porting codec2 (a lossy sinusoidal speech codec, 700–3200 bit/s) to a tiny fixed-point MCU, where bit-exactness with the float reference is impossible?

**Answer in one sentence:** Split the problem in two — (A) *float→fixed* loss is controlled **on the host** with a per-stage tolerance budget plus corpus-level perceptual metrics (WARP-Q + STOI + spectral-distortion gates), and (B) *host-fixed→MCU-fixed* is made **bit-exact by construction** (pure integer C), so on-target testing degenerates to a cheap byte-compare — exactly the ETSI/3GPP fixed-point-codec pattern and exactly the pattern codec2's own stm32 `tst/` framework already implements.

Labels: **VERIFIED** (checked in code or a primary source, URL given), **REPORTED** (secondary source, not independently re-derived), **PROPOSAL** (design recommendation).

---

## 1. How codec2's own CI validates DSP changes today (VERIFIED, from the repo clone)

### 1.1 Host-side ctests (top-level `CMakeLists.txt`)

- CI = GitHub Actions (`.github/workflows/cmake.yml`): ubuntu-22.04, runs `ctest --output-on-failure` for the full suite; `cmake-sm1000.yml` cross-builds stm32 firmware but does **not** run on-target tests in CI (needs a physical Discovery board).
- **The key codec-quality test is a port test with explicit per-stage tolerances**: `test_codec2_700c_octave_port` runs `c2sim --dump` + `unittest/tnewamp1`, then Octave `octave/tnewamp1.m` compares the C implementation against the Octave reference *stage by stage* (`PASS_REGULAR_EXPRESSION "fails: 0"`). Tolerances in `tnewamp1.m` lines 187–195:

  | Stage | Tolerance |
  |---|---|
  | Equaliser, rate_K surface, mean, rate_K_surface_, interpolated surface | `0.01` |
  | interpolated **Wo_** | `0.001` |
  | interpolated **voicing** | exact |
  | rate-L **Am** surface | `0.1` |
  | phase surface H | exact/default |

  This is a ready-made template for a per-stage tolerance harness: dump intermediates from both implementations, compare arrays with per-stage epsilons, count fails.
- **Built-in objective metrics already in the codebase** (`src/c2sim.c` ~line 1100): `LPC->{Am} SNR av: %5.2f dB` (average LPC-modelling SNR); `LSP quantiser SD: %5.2f dB*dB` (classic spectral distortion); `c2enc --var` VQ distortion variance, used as a CI **gate** in `unittest/test_700c_eq.sh` (run twice, assert `var_eq <= var` via a one-line Python exit).
- Whole-codec audio tests (lines 1137–1157) merely run `c2enc | c2dec | sox → wav` for every mode (3200…700C) — "doesn't crash", **no automatic quality score**.
- Comparison utilities: `unittest/compare_ints.c` (byte/short compare with `-t tolerance`, `-n max-errors`, prints RMS error) and `compare_floats.c`.
- **Dump infrastructure** (`src/dump.c`): ~30 per-stage dump functions — `dump_model, dump_quantised_model, dump_Sn, dump_Sw, dump_phase, dump_lsp, dump_lsp_, dump_ak, dump_snr, dump_lpc_snr, dump_E, dump_mel, dump_weights…` — activated by `c2sim --dump prefix`. A shadow-comparison harness can reuse this format; `stm32/unittest/scripts/tst_ofdm_demod_check` already contains a full Octave-text→NumPy reader.

### 1.2 The stm32 `tst/` framework — the exact pattern for our port (VERIFIED)

`stm32/unittest/` (README_unittest.md, `scripts/`): each ctest is a **3-phase pipeline**:

1. **`*_setup`** (host): generate input (`dd` N frames from `raw/hts1.raw`) and the **golden reference** by running the *x86 build of the same code* (`c2enc 1300 stm_in.raw ref_enc.raw`).
2. **run** (`run_stm32_tst`): execute the same test on STM32F4 via OpenOCD + semihosting; target reads `stm_in.raw`/`stm_cfg.txt`, writes `stm_out.raw`.
3. **`*_check`** (host): compare target output to host reference.
   - `tst_codec2_enc_check`: `compare_ints -b1 -c ref_enc.raw stm_out.raw`; mode **1300** passes if `error_count <= 2` bytes differ; mode **700C** requires **exact** match — upstream already accepts "*near* bit-exact" x86↔ARM float differences with a tiny numeric budget.
   - `tst_ofdm_demod_check` (Python/NumPy): explicit tolerances per quantity — `tolerance_ber = 0.001…0.01`, `tolerance_output_differences = 0…5`, `tolerance_syms/amps = 0.01`, relaxed per sub-case (ideal/AWGN/fade).
   - `tst_codec2_dec_check`: **"Must manually listen to this!" + `aplay`** — the decoder quality gate is a human ear. Upstream itself does not trust a single objective number for synthesis quality.

**Takeaway:** codec2's culture is (a) per-stage numeric tolerances against a golden host reference, (b) tiny explicit budgets where FP differs across platforms, (c) human listening as the final decoder gate. Our harness industrialises exactly this, replacing "aplay + ear" with automated perceptual metrics per commit and MUSHRA-lite per release.

### 1.3 Test corpus in-repo (VERIFIED)

`raw/`: hts1.raw, hts1a.raw, hts2a.raw, kristoff.raw, ve9qrp.raw, ve9qrp_10s.raw, testframes_700d.raw (8 kHz s16le mono); `wav/`: 3 files. Fine for smoke gates, too small for metric stability (§5.4).

---

## 2. Objective metrics for a 700–3200 bit/s sinusoidal vocoder

### 2.1 PESQ (ITU-T P.862) — do **not** gate on it
- **VERIFIED (licensing):** proprietary (BT/Psytechnics + KPN/OPTICOM); any use beyond conformance testing requires a license ([pesq.org FAQ](http://www.pesq.org/information/faq.html), [ITU-T P.862](https://www.itu.int/rec/T-REC-P.862/)). P.862/.1/.2/.3 **withdrawn by ITU 2024-01-05** in favour of P.863 ([arXiv:2505.19760 "Navigating PESQ"](https://arxiv.org/abs/2505.19760)). pip [`pesq`](https://github.com/ludlows/PESQ)/[`pypesq`](https://github.com/vBaiCai/python-pesq) are code-open but convey no algorithm license (REPORTED gray zone).
- **VERIFIED (validity):** P.862 Table 3 explicitly lists CELP/hybrid codecs **below 4 kbit/s** as not validated — the entire codec2 range.
- **VERIFIED (codec2-specific):** David Rowe, ["Codec 2 and TWELP"](https://www.rowetel.com/wordpress/?p=6513): PESQ "throws away all phase information"; reported differences "are unlikely to be statistically valid"; **"I suggest you disregard the PESQ numbers."** The user's premise is confirmed.

### 2.2 POLQA (P.863) — excluded. Commercial only; also reported unreliable for non-waveform-preserving low-rate codecs (WARP-Q paper).

### 2.3 ViSQOL v3 — open, trend indicator
- **VERIFIED:** Apache-2.0, C++/Bazel, CLI + Python API, MOS-LQO output; [github.com/google/visqol](https://github.com/google/visqol), [arXiv:2004.09584](https://arxiv.org/abs/2004.09584). Use speech mode (16 kHz; upsample our 8 kHz output).
- **REPORTED caveat:** WARP-Q paper shows ViSQOL/POLQA underestimate and mis-rank non-waveform-preserving vocoders; codec2's sinusoidal synthesis is likewise non-waveform-preserving. Gate on the **float-vs-fixed delta**, not absolute values (PROPOSAL).

### 2.4 STOI / ESTOI — open intelligibility floor
- **VERIFIED:** `pip install pystoi` (MIT, [github.com/mpariente/pystoi](https://github.com/mpariente/pystoi)); STOI correlates with intelligibility of degraded incl. **vocoded** speech (Taal 2011; ESTOI Jensen & Taal 2016 — [torchmetrics docs](https://lightning.ai/docs/torchmetrics/stable/audio/short_time_objective_intelligibility.html)).
- Role: cheap, monotone regression canary; gate on ΔESTOI per file (PROPOSAL).

### 2.5 WARP-Q — best-matched full-reference metric
- **VERIFIED:** designed because POLQA/ViSQOL fail on generative/low-rate codecs; subsequence-DTW cost over MFCCs; consistent, correctly ranked scores on codec sets including **MELP 2.4k, Opus 6k, Speex 4k, LPCNet 1.6k** — codec2's neighbourhood. [arXiv:2102.10449](https://arxiv.org/abs/2102.10449), [IET SP 2022](https://ietresearch.onlinelibrary.wiley.com/doi/full/10.1049/sil2.12151), code [github.com/wjassim/WARP-Q](https://github.com/wjassim/WARP-Q) (Python/librosa; clone+requirements, no official PyPI package — REPORTED). Polarity: it's a **cost**, higher = worse (inverse correlation with MOS); a MOS-mapped mode exists in the 2022 version (REPORTED).
- Role: primary perceptual gate (§5).

### 2.6 Reference-free neural metrics — secondary
- **VERIFIED:** DNSMOS in [github.com/microsoft/DNS-Challenge](https://github.com/microsoft/DNS-Challenge) (ONNX); NISQA [github.com/gabrielmittag/NISQA](https://github.com/gabrielmittag/NISQA) (MIT; MOS + Noisiness/Coloration/Discontinuity/Loudness).
- **REPORTED caveat:** trained on telephony/noise corpora; out-of-domain for a 1.3 kb/s sinusoidal vocoder. But reference-free and alignment-free → excellent *artifact detectors* (overflow clicks, limit-cycle whistles crater Discontinuity/OVRL). Nightly alarm, not gate (PROPOSAL).

### 2.7 Summary

| Metric | License/impl | Valid for 0.7–3.2 kb/s sinusoidal? | Role in CI |
|---|---|---|---|
| PESQ P.862 | proprietary; withdrawn 2024 | **No** (Table 3; Rowe: "disregard") | none/info |
| POLQA P.863 | commercial | unreliable for this class (REPORTED) | none |
| ViSQOL v3 | Apache-2.0 | biased for non-waveform codecs; deltas OK | trend + Δ-gate |
| STOI/ESTOI | MIT (pystoi) | yes, incl. vocoded speech | fast per-commit Δ-gate |
| **WARP-Q** | open (GitHub) | **designed for exactly this class** | primary perceptual gate |
| DNSMOS/NISQA | free/MIT | out-of-domain absolute; artifact detector | nightly alarm |
| segSNR/SD/param RMSE | trivial | not perceptual but diagnostic | per-stage budget (§3) |

---

## 3. Per-stage loss-control methodology (the tolerance budget)

### 3.1 What the standards world does
- **ETSI/3GPP bit-exact route (VERIFIED):** AMR/EVS ship fixed-point ANSI-C written entirely in standardized 16/32-bit saturating **basic operators** (`basicop2.c`: `add(), L_mac()…`) so every implementation is bit-exact against reference vectors on any platform ([Design&Reuse on AMR porting](https://www.design-reuse.com/article/58069-solving-amr-speech-codec-porting-challenges/); [3GPP TS 26.442](https://www.etsi.org/deliver/etsi_ts/126400_126499/126442/12.05.00_60/ts_126442v120500p.pdf)). The operator library is ITU-T **G.191 STL**, openly maintained at [github.com/openitu/STL](https://github.com/openitu/STL) (ITU-T "General Public License", GPL-like). **Lesson:** bit-exactness is achieved *between fixed-point builds*, never float↔fixed; the float→fixed step was a one-time effort judged by listening + spectral distortion, then frozen.
- **Speex route (VERIFIED from [Valin, LCA 2006](https://jmvalin.ca/papers/speex_lca2006.pdf) §V-A):** incremental conversion from Nov 2003 via "arithmetic operator abstraction (macros)" (`FIXED_POINT`), modes converted one at a time, unconverted parts left in float emulation — a gradual per-stage port with a working codec at every step. Speex/Opus trees also carry `fixed_debug.h`, macros that runtime-check 16/32-bit range violations (REPORTED). Adopt both ideas.
- **LSP transparency rule (VERIFIED):** Paliwal & Atal 1993 ([IEEE](https://ieeexplore.ieee.org/document/221364/)): transparent when (1) **mean SD ≤ 1 dB**, (2) **<2 % outliers 2–4 dB**, (3) **none >4 dB**. Float→fixed error is formally just another envelope perturbation; `c2sim` already prints this SD statistic, so the gate is nearly free.

### 3.2 Proposed tolerance budget per codec2 stage (PROPOSAL)

Principle: **a stage's fixed-point error must be small relative to the quantization noise the bitstream already imposes on that parameter.** Codec2 quantizes brutally (`src/quantise.h: WO_BITS 7`; energy 5 bits; LSPs ~36 bits scalar; VQ at 700C — VERIFIED), so an error 10× under the quantizer step is inaudible by construction.

| Stage | Metric (float vs fixed, same input) | Budget | Rationale |
|---|---|---|---|
| NLP pitch estimator | Wo relative RMS error | **< 0.2 %** | Wo log-quantizer step ≈1.6 % (7 bits over 50–400 Hz); stay ≈8× under |
| | gross pitch-candidate disagreement | **< 0.3 % frames** | octave errors are audible; allow only boundary ties |
| Voicing (`est_voicing_mbe`) | flip rate | **< 0.5 % frames**, none where float margin >1 dB | 1 bit/frame; boundary flips inherent, confident flips are bugs |
| Am estimation | mean abs error over harmonics | **< 0.3 dB**, max < 1 dB | below LSP-quantizer floor |
| LPC/LSP + quantization | Paliwal–Atal SD | **ΔSD < 0.1 dB vs float; combined still ≤1 dB / <2 % / 0 % >4 dB** | keep transparency end-to-end |
| Energy quantizer | index disagreement | **< 0.1 %**, ±1 only | ties only |
| VQ search (700C) | index disagreement / `--var` | `var_fixed ≤ var_float + 0.05 dB²` | reuse `test_700c_eq.sh` pattern |
| FFT | per-transform SNR vs float FFT | **> 60 dB** | standard 16-bit FFT quality; poisons all later stages |
| Phase synthesis | downstream audio only | — | phases pseudo-random by design |
| Decoder synthesis | segSNR(float_out, fixed_out), 10 ms segs | **> 25 dB median, > 15 dB min** | below this, audible artifacts |
| Whole codec | ΔWARP-Q, ΔESTOI, ΔViSQOL | §5 gates | catches interactions |

Two hard rules: **one stage per PR** (Speex pattern; regression bisection trivial); **budgets are consumed, not shared** — end-to-end perceptual gates enforce the sum.

### 3.3 Numeric hygiene (PROPOSAL, Speex-inspired)
- All fixed-point math via macros with three build flavours: (a) target int (bit-exact everywhere), (b) `FIXED_DEBUG` — every op checks overflow/shift range, per-callsite saturation counters, (c) float fallback.
- CI gate: **zero non-whitelisted saturations** on the corpus.
- Torture inputs (all-zero, ±FS square, 1 kHz tone, impulse) for limit cycles/wraparound, checked by max-abs + DNSMOS alarm.

---

## 4. Two-tier bit-exactness architecture (PROPOSAL — key design decision)

```
float codec2 (host, golden)            ← quality reference, never changes
      │  controlled loss: §3 budgets + §5 perceptual gates (HOST CI, no hardware)
      ▼
fixed-point codec2, host build         ← same C sources, pure integer
      │  BIT-EXACT by construction (no float, no UB, no compiler-dependent math)
      ▼
fixed-point codec2 on MCU (CH32V003)   ← verified by cheap byte-compare, stm32-tst style
```

Because tier 2 is integer-only C, host and MCU outputs match to the byte; the `stm32/unittest` setup/run/check + `compare_ints` pattern is reused verbatim (semihosting/serial dump on the target). **All quality reasoning happens on the host at native speed**; hardware-in-the-loop shrinks to a nightly `memcmp == 0` smoke test. This is precisely why ETSI codecs define basic operators.

---

## 5. Concrete harness design (PROPOSAL)

### 5.1 Components
```
tools/
  golden/           # float build: c2enc_f, c2dec_f, c2sim_f (upstream, pinned commit)
  fixed/            # fixed build: c2enc_x, c2dec_x + FIXED_DEBUG variants
  metrics/
    stage_compare.py   # Wo RMSE, voicing flips, SD, Am error, segSNR (NumPy)
    percept.py         # WARP-Q, ESTOI (pystoi), ViSQOL wrapper
    gates.yaml         # all thresholds in one reviewed file
  corpus/
```

### 5.2 Shadow comparison flow (per commit)
```bash
for f in corpus/*.raw; do
  c2sim_f $f --dump ref_$f            # float, per-stage dumps (dump.c format)
  c2sim_x $f --dump fix_$f            # fixed (host build)
  python stage_compare.py ref_$f fix_$f --gates gates.yaml
  for m in 3200 1300 700C; do
    c2enc_f $m $f - | c2dec_f $m - out_f.raw
    c2enc_x $m $f - | c2dec_x $m - out_x.raw
    python percept.py $f out_f.raw out_x.raw --mode $m >> metrics.jsonl
  done
done
python gate_report.py metrics.jsonl --gates gates.yaml   # exit 1 on violation
```
Core formulas: Wo RMSE = `sqrt(mean(((Wo_x-Wo_f)/Wo_f)**2))`; voicing flip rate = `mean(v_x != v_f)`; SD/frame = `sqrt(mean((20log10|A_f| - 20log10|A_x|)**2))` over 4 kHz on the LPC envelope; segSNR per 80-sample segment clipped to [0, 60] dB.

### 5.3 Acceptance gates (paired deltas, fixed vs float, same file)

| Gate | Threshold | Fail action |
|---|---|---|
| All §3.2 stage budgets | as tabled | block merge |
| ΔWARP-Q (raw cost, median) | ≤ +0.05; worst file ≤ +0.10 | block merge |
| ΔESTOI (median) | ≥ −0.01; worst file ≥ −0.03 | block merge |
| ΔViSQOL MOS-LQO (median) | ≥ −0.10 | warn → weekly review |
| FIXED_DEBUG saturation counters | 0 non-whitelisted | block merge |
| DNSMOS/NISQA nightly | no file drops >0.3 OVRL vs rolling baseline | alarm |
| MCU byte-compare (nightly, HW rig) | memcmp == 0 vs host fixed build | block release |

Calibrate thresholds in week 1 against the metric spread between two independent float builds (-O2 vs -O3, x86 vs ARM float); gates must sit above that noise floor. The ±0.5-MOS confidence interval of PESQ-class metrics (Rowe's point) is exactly why we gate on paired deltas over a fixed corpus, never absolute scores.

### 5.4 Corpus
- **Tier A (per-commit, ~1 min):** codec2's own `raw/` set — continuity with upstream numbers.
- **Tier B (nightly, 30–60 min), resampled to 8 kHz/16-bit:** Harvard/IEEE sentences (e.g. [Open Speech Repository](http://www.voiptroubleshooter.com/open_speech/) — REPORTED availability); **ITU-T P.501** speech annexes ([itu.int](https://www.itu.int/rec/T-REC-P.501)) — designed for exactly this; LibriSpeech test-clean subset (CC-BY 4.0, [openslr.org/12](https://www.openslr.org/12)), ~100 utterances 16k→8k; hard cases: male <80 Hz, female/child >300 Hz, non-English, babble at 20 dB SNR (codec2's pitch/voicing are most fragile there). Checksum-pin the corpus; never tune thresholds and corpus in the same PR.

---

## 6. Human-in-the-loop: MUSHRA-lite final gate (PROPOSAL)

- **Tool (VERIFIED):** [webMUSHRA](https://github.com/audiolabs/webMUSHRA) (AudioLabs; ITU-R BS.1534-compliant; YAML-configured; Docker; [JORS paper](https://openresearchsoftware.metajnl.com/articles/10.5334/jors.187)).
- **Design:** per milestone (each mode fully ported): 8–10 items from Tier B; conditions: hidden reference, low anchor (3.5 kHz LPF per BS.1534), float codec2, fixed codec2 — ~15 min session; 6–10 listeners (team + ham-radio volunteers; the codec2 community has a strong listening tradition — Rowe's posts always ship A/B samples).
- **Gate:** paired Wilcoxon on float-vs-fixed: pass if median difference <5 MUSHRA points and p ≥ 0.05. Ultra-cheap fallback: forced-choice A/B ("which is worse?"); fixed must not be chosen worse significantly above 50 % (binomial, n ≥ 60 trials).

## 7. Risks
- Full-reference metrics tolerate rare clicks → keep max-abs/segSNR-min gate + DNSMOS Discontinuity alarm specifically for overflow clicks.
- WARP-Q has no absolute anchor → track paired deltas + a frozen known-good baseline; re-baseline only consciously.
- Outsiders will demand PESQ numbers → publish as informational with the P.862 Table 3 / Rowe caveat, never as a gate.
- Host-fixed ≠ MCU-fixed if any float/`int`-width/UB sneaks in → `-Wall -Wextra -fsanitize=undefined` on the host fixed build + the nightly byte-compare; that is the whole point of tier 2.

**Key repo files referenced:** `CMakeLists.txt` (lines 390–397, 1137–1157), `octave/tnewamp1.m` (187–196), `src/c2sim.c` (~1100), `src/dump.c`, `src/quantise.h` (WO_BITS 7), `unittest/compare_ints.c`, `unittest/test_700c_eq.sh`, `stm32/unittest/README_unittest.md`, `stm32/unittest/scripts/*`, `.github/workflows/cmake.yml`.
