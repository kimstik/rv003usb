#!/usr/bin/env bash
# run_all.sh — reproduce the whole tube-ladder experiment end to end.
#
#   1. build_oracle.sh   clone+build pinned codec2 (gitignored under build/)
#   2. make_dumps.sh     c2sim dumps + reference syntheses (uq + q1300)
#   3. run_ladder.py     synthesize L0..L4 variants, signal metrics
#                        -> results/metrics.csv, results/aggregate.json
#   4. cost_ladder.py    static MCU cost deltas -> results/cost_ladder.csv
#   5. WARP-Q            clone (pinned) + score all wavs -> results/warpq.json
#   6. plots.py          spectrograms + knee curve -> plots/*.png
#
# Python deps: numpy scipy pystoi matplotlib librosa (pip3 install --user ...).
# WARP-Q's pyvad dependency is deliberately NOT installed (no webrtcvad wheel
# in this container); warpq_ladder.py stubs it and runs with apply_vad=False.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$HERE/build_oracle.sh"
"$HERE/make_dumps.sh"

python3 -c "import numpy, scipy, pystoi, matplotlib" 2>/dev/null || {
  echo "installing python deps..."; pip3 install --user numpy scipy pystoi matplotlib; }

python3 "$HERE/run_ladder.py"
python3 "$HERE/cost_ladder.py"

# WARP-Q (optional: skipped with a warning if librosa can't be installed)
WARPQ_DIR="$HERE/build/WARP-Q"
WARPQ_COMMIT=bdf8616dc21dc4d7e8ae504bb162cc7f04b188a2
if [ ! -d "$WARPQ_DIR" ]; then
  git clone -q https://github.com/wjassim/WARP-Q.git "$WARPQ_DIR"
  git -C "$WARPQ_DIR" checkout -q "$WARPQ_COMMIT" || true
fi
if python3 -c "import librosa" 2>/dev/null || pip3 install --user librosa pandas tqdm joblib; then
  python3 "$HERE/warpq_ladder.py"
else
  echo "WARNING: librosa unavailable -> skipping WARP-Q (see REPORT.md caveats)"
fi

python3 "$HERE/plots.py"
echo "tube-ladder: all done. results/ and plots/ updated."
