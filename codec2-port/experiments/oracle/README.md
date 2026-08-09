# experiments/oracle — float-codec2 oracle + automated metrics harness

Implements the measurement side of the port methodology
(`codec2-port/README.md` §4): the float build of upstream codec2 is the
**golden oracle**; all future fixed-point/alternative implementations are
compared against it fully automatically, first in the parameter domain
(level 1), then in the signal domain (level 2). No humans in the loop.

Pinned oracle: `drowe67/codec2 @ 310777b1c6f1af0bc7c72f5b32f80f6fd9136962`
(see `VERSION`) — the same commit the research reports were written against.

## Usage

```sh
./build_oracle.sh          # clone (shallow, pinned) + cmake Release build + smoke test
./run_all.sh               # full chain on hts1a.raw and ve9qrp_10s.raw + summary table
```

Individual tools:

```sh
# dump per-frame model params (10 ms grid) and parse them
build/codec2/build_host/src/c2sim <in.raw> --dump <prefix> --lpc 10 --phase0
./dump_params.py <prefix> out.npz

# parameter-domain metrics between two param sets on the same frame grid
./stage_compare.py ref.npz test.npz [--json m.json]
./stage_compare.py --selftest ref.npz      # identity==0 + perturbed==sane

# signal-domain metrics between two raw/wav files (same fs, no alignment)
./metrics_signal.py ref.raw test.raw [--fs 8000] [--json m.json]
```

Python deps: `pip3 install numpy scipy pystoi` (worked through the proxy).

## What was verified to run (2026-08-09, this container)

- `build_oracle.sh`: fetch of exactly the pinned commit by SHA (shallow),
  cmake 3.28 / gcc Release build, `c2enc`/`c2dec`/`c2sim` smoke test
  (hts1a 48000 B -> 532 B @1300 -> 48000 B decoded).
- `c2sim --dump` with `--lpc 10 --phase0` producing all dump files below;
  requires **`-DDUMP`** in CFLAGS — upstream only sets it for
  `CMAKE_BUILD_TYPE=Debug`, so `build_oracle.sh` passes
  `-DCMAKE_C_FLAGS="-DDUMP"` explicitly on the Release build.
- `dump_params.py` on hts1a (300 frames) and ve9qrp_10s (1000 frames).
- `stage_compare.py --selftest` PASS on both utterances
  (self-compare exactly zero; injected 0.1 % Wo noise / 0.25 dB amp noise /
  2 % voicing flips recovered at the expected magnitudes).
- `stage_compare.py` on a real stage pair (measured harmonic amplitudes vs
  the LPC+postfilter-recovered amplitudes from the same run): mean |err|
  ~3.2 dB, envelope SD ~4.2 dB — consistent with c2sim's own
  "LPC->{Am} SNR av: 11.5 dB" report, i.e. the harness measures a known
  real distortion at a plausible size.
- `metrics_signal.py`: identity gives segSNR 35 dB (clamp) / ESTOI 1.0;
  c2dec-1300 output vs original gives ESTOI ~0.52–0.56.
- ESTOI via `pystoi 0.4.1` (installed through the proxy with pip).

**NOT verified / not implemented:** WARP-Q (see below), .wav path of
`metrics_signal.py` beyond code inspection (corpus is headerless .raw),
`dump_params.py` on dumps made *without* `--lpc`/`--phase0` (falls back to
shifted voicing / NaN snr — code path exists, untested on real data).

## c2sim dump format (codec2 @ 310777b, src/dump.c — inspected + verified)

`c2sim <raw> --dump <prefix>` writes `<prefix>_*.txt`, one (or two) text
lines per 10 ms analysis frame, tab/space-separated floats:

| file | per frame | contents |
|---|---|---|
| `_model.txt` | 1 line, 163 cols | `Wo L A[1..160] voiced`. Wo in rad/sample (F0 = Wo·8000/2π Hz); L = harmonic count = ⌊π/Wo⌋; `A[l]` **linear** harmonic magnitudes, zero-padded above L to MAX_AMP=160; `voiced` 0/1 — **stale by one frame**, see below |
| `_qmodel.txt` | 1 line, 163 cols | same layout, decode path (needs `--lpc`): A[] are LPC+postfilter-recovered amplitudes after `aks_to_M2`, `voiced` is the **correct** per-frame decision |
| `_snr.txt` | 1 float | MBE voicing SNR dB from `est_voicing_mbe` (needs `--phase0`); threshold V_THRESH = 6 dB plus eratio post-processing |
| `_lsp.txt` / `_lsp_.txt` | 10 floats | unquantised / processed LSPs, rad/sample (needs `--lpc 10`) |
| `_ak.txt` / `_ak_.txt` | order+1 floats | LPC coeffs, a[0]=1 (needs `--lpc`) |
| `_E.txt` | 1 float | LPC residual energy, dB = 10·log10(e) |
| `_sn.txt`, `_sq.txt` | **2 lines**, m_pitch total | windowed input speech (split in half to keep lines short) |
| `_sw.txt`, `_sw_.txt`, `_ew.txt` | 256 floats | 10·log10 power spectrum of the 512-pt analysis FFT (can contain `-inf` on digital silence) |
| `_phase.txt`, `_fw.txt`, `_pw.txt`, `_dec.txt`, ... | varies | phase/NLP/LPC internals; not parsed by this harness |

**Voicing-lag gotcha (verified empirically):** `dump_model()` is called
*before* `est_voicing_mbe()` in the c2sim loop, so the `voiced` column of
`_model.txt` is the previous frame's decision — on hts1a,
`model[i+1].voiced == qmodel[i].voiced` for all 300 frames, while the naive
`snr > 6 dB` rule differs from the real flag on 81/300 frames (the eratio
post-processing matters). `dump_params.py` therefore takes voicing from
`_qmodel.txt` when present, else shifts the `_model.txt` column by one.

`dump_params.py` output `.npz` keys: `Wo, L, A, voiced, voiced_raw,
snr_mbe, A_lpc, lsp, ak, E_dB` (see its docstring).

## Metrics implemented

`stage_compare.py` (parameter domain, exact — same frame grid by
construction): Wo relative RMSE % (budget anchor < 0.2 %), voicing flip
rate % (< 0.5 %) plus flips restricted to *confident* frames
(|snr−6 dB| > 1 dB; budget: zero), per-harmonic log-amplitude error
mean/RMS/max dB over harmonics within 60 dB of frame peak (< 0.3 dB mean),
per-frame envelope spectral distortion (both amplitude sets interpolated in
dB onto a fixed 100–3700 Hz grid; Paliwal–Atal-style gate: mean ≤ 1 dB,
< 2 % frames above 2 dB), frame-energy RMS dB.

`metrics_signal.py` (signal domain): segmental SNR (20 ms/10 ms, per-frame
clamp [−10, 35] dB, silence-gated at −40 dB rel. RMS; mean+median) and
ESTOI (pystoi). No time alignment is performed — intended for
oracle-vs-port outputs which are sample-aligned by construction
(`--lag N` exists for a known constant shift).

### WARP-Q (documented only, NOT installed/verified)

WARP-Q is the project's primary perceptual metric but is not exercised by
this harness yet. Install steps (untested here):

```sh
git clone https://github.com/wjassim/WARP-Q.git
pip3 install -r WARP-Q/requirements.txt   # librosa, dtw-python, ...
python3 WARP-Q/legacy_code/WARPQ_main_code.py --org ref.wav --deg test.wav
# newer API: from WARPQ.WARPQmetric import warpqMetric (see repo README)
```

Notes for whoever wires it in: WARP-Q wants wav input (convert raw with
`sox -t raw -r 8000 -e signed -b 16 -c 1 in.raw out.wav`), and its score is
a DTW distance (lower = better, not MOS-like); calibrate the gate on the
float −O2 vs −O3 noise floor first, per methodology §4.

## Deviations / caveats

- **Release + `-DDUMP`**: upstream ties dump instrumentation to Debug
  builds; we force it into the Release build (also disables the asserts in
  dump.c via NDEBUG — fopen failures would be silent; dump files are
  checked for existence by `dump_params.py` anyway).
- Without `-DDUMP`, `c2sim --dump <prefix>` silently writes a single
  `<prefix>` file in `dump_pitch_e` format instead of failing — trap for
  the unwary, another reason build_oracle.sh owns the configure step.
- `run_all.sh`'s segSNR/ESTOI columns compare c2dec-1300 output against
  the **original** speech: codec2 is parametric (synthetic phases), so
  segSNR is low by design (~−3 dB). These columns are a chain demo. The
  real use of `metrics_signal.py` is fixed-vs-float outputs of the *same*
  decoder, where segSNR ≥ 25–30 dB gates from the methodology apply.
- The "ampLPC/SD-LPC" summary columns compare two *different real stages*
  of the oracle (measured spectral amplitudes vs LPC-modelled ones), so
  their few-dB values are the size of LPC modelling error, not a defect.
- `stage_compare.py` requires identical frame counts (same input + framing)
  — deliberate: resampling/alignment would blur the level-1 metrics.
- Corpus is the in-repo `codec2/raw/*.raw` (7 files); run_all exercises
  `hts1a` and `ve9qrp_10s`.

## Layout

```
VERSION            pinned repo URL + commit SHA
build_oracle.sh    fetch + build + smoke test        (idempotent, --clean)
dump_params.py     c2sim dump text -> .npz
stage_compare.py   parameter-domain metrics + selftest
metrics_signal.py  segSNR + ESTOI
run_all.sh         whole chain on 2 utterances -> results/summary.txt
results/           committed summary of the last verified run
build/             (gitignored) codec2 clone, binaries, dumps, npz
```
