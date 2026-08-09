#!/bin/bash
# run_all.sh -- full VOICING-NOFFT experiment (idea B2), end to end.
# Needs: gcc/cmake/make, git, python3 + numpy (pip install numpy).
set -euo pipefail
EXP_DIR="$(cd "$(dirname "$0")" && pwd)"
WORK="${VOICING_WORK:-$EXP_DIR/work}"
export VOICING_WORK="$WORK"

echo "=== 1. build codec2 + dump reference voicing (clean corpus)"
"$EXP_DIR/build.sh" all

echo "=== 2. generate 20 dB SNR noisy corpus (white + babble-like)"
python3 "$EXP_DIR/make_noisy.py" "$WORK/codec2/raw" "$WORK/noisy"

echo "=== 3. dump reference voicing on noisy corpus (same-audio reference)"
for raw in "$WORK"/noisy/*.raw; do
    name=$(basename "$raw" .raw)
    case "$name" in
        *_white20)  dd="$WORK/dumps/white20";;
        *_babble20) dd="$WORK/dumps/babble20";;
    esac
    "$EXP_DIR/build.sh" dump "$raw" "$dd/$name"
done

echo "=== 4. extract features"
python3 "$EXP_DIR/features.py" "$WORK/codec2/raw" "$WORK/dumps/clean" \
        "$WORK/features/clean" --selfcheck
for cond in white20 babble20; do
    python3 "$EXP_DIR/features.py" "$WORK/noisy" "$WORK/dumps/$cond" \
            "$WORK/features/$cond"
done

echo "=== 5. classify + score"
mkdir -p "$EXP_DIR/results"
python3 "$EXP_DIR/classify.py" "$WORK/features" \
        "$EXP_DIR/results/summary.json" | tee "$EXP_DIR/results/classify_output.txt"

echo "=== 6. nonparametric floor (kNN LOFO on clean)"
python3 "$EXP_DIR/floor_knn.py" "$WORK/features/clean" \
        | tee "$EXP_DIR/results/floor_knn.txt"

echo "=== done; see results/summary.json, results/classify_output.txt, REPORT.md"
