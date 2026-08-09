#!/usr/bin/env bash
# run_all.sh — exercise the whole oracle harness chain end to end.
#
# Per utterance (hts1a, ve9qrp_10s):
#   1. c2sim --dump           -> per-frame model parameter text dumps
#   2. dump_params.py         -> .npz (measured model + LPC-stage amplitudes)
#   3. stage_compare selftest -> identity == zeros, perturbed == sane nonzero
#   4. stage_compare          -> measured vs LPC+postfilter amplitude stage
#                                (a real, meaningful stage pair of the oracle)
#   5. c2enc/c2dec 1300       -> decoded audio
#   6. metrics_signal.py      -> segSNR + ESTOI of decoded vs original
#      (NOTE: codec2 is parametric, not waveform-preserving: segSNR vs the
#      original is LOW by design; it is reported as a chain demo. The metric
#      is intended for fixed-point-vs-float decoder outputs, which share
#      synthetic phases and DO track in waveform.)
#
# Writes results/summary.txt (committed) and prints it.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$HERE/build"
SRC="$BUILD/codec2"
BIN="$SRC/build_host/src"
RAWDIR="$SRC/raw"
DUMPS="$BUILD/dumps"
RESULTS="$HERE/results"
UTTS=(hts1a ve9qrp_10s)

"$HERE/build_oracle.sh"

python3 -c "import numpy" 2>/dev/null || {
  echo "installing python deps (numpy scipy pystoi)..."
  pip3 install --user numpy scipy pystoi >/dev/null
}

mkdir -p "$DUMPS" "$RESULTS"
SUMMARY="$RESULTS/summary.txt"

declare -A R  # metrics collected for the summary table

for utt in "${UTTS[@]}"; do
  raw="$RAWDIR/$utt.raw"
  [ -f "$raw" ] || { echo "ERROR: $raw missing from corpus" >&2; exit 1; }
  d="$DUMPS/$utt"; mkdir -p "$d"
  echo "=== $utt ==="

  # 1. dump params (10 ms frame grid; --lpc 10 also gives the decode-path
  #    qmodel dump with correct per-frame voicing, --phase0 gives MBE snr)
  "$BIN/c2sim" "$raw" --dump "$d/$utt" --lpc 10 --phase0 \
      -o "$d/${utt}_sim.raw" >"$d/c2sim.log" 2>&1

  # 2. parse to npz
  python3 "$HERE/dump_params.py" "$d/$utt" "$d/$utt.npz"

  # derived npz: same grid, amplitudes replaced by the LPC-recovered stage
  python3 - "$d/$utt.npz" "$d/${utt}_lpcstage.npz" <<'EOF'
import sys, numpy as np
z = dict(np.load(sys.argv[1]))
assert "A_lpc" in z, "qmodel dump missing (need c2sim --lpc)"
z["A"] = z["A_lpc"]
np.savez_compressed(sys.argv[2], **z)
EOF

  # 3. self-test of the comparator
  python3 "$HERE/stage_compare.py" --selftest "$d/$utt.npz" \
      | tee "$d/selftest.txt" | grep -E '^selftest'
  R[$utt,selftest]=$(grep -oE 'PASS|FAIL' "$d/selftest.txt" | tail -1)

  # 4. real stage compare: measured harmonic amps vs LPC+postfilter amps
  python3 "$HERE/stage_compare.py" "$d/$utt.npz" "$d/${utt}_lpcstage.npz" \
      --title "$utt: measured vs LPC+PF amplitude stage" \
      --json "$d/stage_lpc.json"

  # 5. encode/decode at 1300
  "$BIN/c2enc" 1300 "$raw" "$d/$utt.c2" >/dev/null 2>&1
  "$BIN/c2dec" 1300 "$d/$utt.c2" "$d/${utt}_1300.raw" >/dev/null 2>&1

  # 6. signal metrics (chain demo, see header note)
  python3 "$HERE/metrics_signal.py" "$raw" "$d/${utt}_1300.raw" \
      --json "$d/signal_1300.json"

  # collect numbers for the summary
  eval "$(python3 - "$d/$utt.npz" "$d/stage_lpc.json" "$d/signal_1300.json" \
      "$utt" <<'EOF'
import json, sys, numpy as np
npz, sjson, gjson, utt = sys.argv[1:5]
z = np.load(npz)
s = json.load(open(sjson)); g = json.load(open(gjson))
est = g["estoi"]
est = f"{est:.3f}" if isinstance(est, float) else "n/a"
print(f'R[{utt},frames]={len(z["Wo"])}')
print(f'R[{utt},voiced]={100 * z["voiced"].mean():.1f}')
print(f'R[{utt},sd]={s["sd_mean_dB"]:.2f}')
print(f'R[{utt},amp]={s["amp_mean_abs_dB"]:.2f}')
print(f'R[{utt},segsnr]={g["segsnr_mean_dB"]:.2f}')
print(f'R[{utt},estoi]={est}')
EOF
)"
done

{
  echo "oracle harness summary  ($(date -u +%Y-%m-%dT%H:%M:%SZ), codec2 @ $(git -C "$SRC" rev-parse --short HEAD))"
  echo
  printf '%-12s %7s %8s %10s %12s %12s %7s %9s\n' \
    utterance frames voiced% selftest 'ampLPC(dB)' 'SD-LPC(dB)' ESTOI 'segSNR(dB)'
  printf '%-12s %7s %8s %10s %12s %12s %7s %9s\n' \
    ------------ ------ ------- -------- ---------- ---------- ------ ---------
  for utt in "${UTTS[@]}"; do
    printf '%-12s %7s %8s %10s %12s %12s %7s %9s\n' \
      "$utt" "${R[$utt,frames]}" "${R[$utt,voiced]}" "${R[$utt,selftest]}" \
      "${R[$utt,amp]}" "${R[$utt,sd]}" "${R[$utt,estoi]}" "${R[$utt,segsnr]}"
  done
  echo
  echo "columns: ampLPC/SD-LPC = stage_compare of measured vs LPC+postfilter"
  echo "harmonic amplitudes (real oracle stage pair, expected O(few dB));"
  echo "ESTOI/segSNR = c2dec 1300 output vs original (parametric codec ->"
  echo "low segSNR by design; harness demo, not a port quality gate)."
} | tee "$SUMMARY"

echo
echo "full per-utterance artifacts in $DUMPS/<utt>/, summary in $SUMMARY"
