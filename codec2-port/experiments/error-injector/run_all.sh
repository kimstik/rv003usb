#!/usr/bin/env bash
# run_all.sh — reproduce the sensitivity-analysis experiment end to end.
#
#   1. ../tube-ladder/build_oracle.sh   pinned codec2 build (shared oracle)
#   2. ../tube-ladder/make_dumps.sh     c2sim --rate 1300 dumps, 3 utterances
#   3. run_injection.py                 130 (point x etype x level) sweeps
#                                       x 3 utts -> results/curves.csv
#   4. warpq_inject.py                  WARP-Q on the hts1a subset (timebox)
#                                       -> results/warpq.json  [best-effort]
#   5. knees.py                         knees + results/budgets.yaml
#   6. plots.py                         plots/transfer_<point>.png
#
# Python deps: numpy scipy pystoi matplotlib; librosa pandas tqdm joblib for
# WARP-Q (skipped with a warning if unavailable, knees fall back to
# LSD/ESTOI quanta only).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TL="$HERE/../tube-ladder"

"$TL/build_oracle.sh"
[ -f "$TL/build/dumps/q1300/hts1a/hts1a.npz" ] || "$TL/make_dumps.sh"

python3 -c "import numpy, scipy, pystoi, matplotlib" 2>/dev/null || {
  echo "installing python deps..."; pip3 install --user numpy scipy pystoi matplotlib; }

python3 "$HERE/run_injection.py"

if python3 -c "import librosa" 2>/dev/null || pip3 install --user librosa pandas tqdm joblib; then
  python3 "$HERE/warpq_inject.py" || echo "WARNING: WARP-Q failed (non-fatal)"
else
  echo "WARNING: librosa unavailable -> skipping WARP-Q"
fi

python3 "$HERE/knees.py"
python3 "$HERE/plots.py"
echo "error-injector: all done. results/ and plots/ updated."
