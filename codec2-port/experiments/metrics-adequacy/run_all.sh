#!/usr/bin/env bash
# run_all.sh — full metrics-adequacy stand, end to end.
#
#   1. build the pinned codec2 float oracle (VERSION: @310777b)
#   2. dump decoded-1300 params + phase0 reference synthesis, 3 utterances
#   3. build the buzzy-vs-smooth degradation stand (LSD-matched pairs)
#      + classic judges (LSD/segSNR/NMR/crest/ESTOI)  -> results/classic.csv
#   4. WARP-Q judge (clones wjassim/WARP-Q)           -> results/warpq.json
#   5. neural judges: DNSMOS (pip speechmos, bundled ONNX) + NISQA
#      (torch CPU; clones gabrielmittag/NISQA)        -> results/neural.csv
#   6. adjudication + gates                           -> results/pairs.csv,
#      adequacy.json, axes.csv, gates_h1.yaml
#
# Deps beyond the repo: numpy scipy pystoi librosa (base harness);
#   pip: speechmos onnxruntime torch (CPU) pandas pyyaml;
#   webrtcvad-wheels + pyvad --no-deps (WARP-Q VAD, see voicing-regate).
# Neural installs are best-effort: DNSMOS and NISQA failures degrade the
# stand to classic+WARP-Q (analyze.py copes with missing columns).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

./build_oracle.sh

BIN=build/codec2/build_host/src
RAW=build/codec2/raw
for utt in hts1a hts2a ve9qrp_10s; do
  d=build/dumps/q1300/$utt
  if [ ! -f "$d/$utt.npz" ]; then
    mkdir -p "$d"
    $BIN/c2sim $RAW/$utt.raw --rate 1300 --dump $d/$utt \
        -o $d/${utt}_ref.raw >"$d/c2sim.log" 2>&1
    python3 ../tube-ladder/dump_params.py "$d/$utt" "$d/$utt.npz"
  fi
done

python3 make_pairs.py
python3 run_warpq.py  || echo "WARP-Q failed (non-fatal, documented)"
python3 run_neural.py || echo "neural judges failed (non-fatal, documented)"
python3 analyze.py | tee results/analyze.log

# refresh the committed listening pairs (the human-protocol material)
for f in hts1a.ref hts1a.buzz-l0 hts1a.par-plain hts1a.par-noise-2000 \
         hts1a.smooth-mix-1500 hts1a.buzz-spur@4 hts1a.smooth-valley@4 \
         hts2a.ref hts2a.buzz-l0 hts2a.par-plain hts2a.par-noise-2000 \
         hts2a.smooth-mix-1500 \
         ve9qrp_10s.ref ve9qrp_10s.par-plain ve9qrp_10s.par-noise-2000; do
  cp "build/wavs/$f.wav" wavs/
done
echo "run_all done"
