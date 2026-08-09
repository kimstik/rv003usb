#!/usr/bin/env bash
# run_all.sh -- VOICING-REGATE experiment, end to end.
#
# Voicing-swap A/B through the real codec: encode the corpus with mode 1300
# three ways (stock voicing / FFT-free-rule voicing / random-control voicing
# at the matched flip rate), decode everything with stock c2dec, and measure
# paired perceptual-proxy deltas.  See REPORT.md for the verdict.
#
# Needs: gcc/cmake/make, git, python3 + numpy scipy pystoi.
# Optional: librosa+dtw-python+pandas+soundfile for the WARP-Q step (step 8
# is best-effort and never fails the run).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${REGATE_WORK:-$HERE/work}"
export REGATE_WORK="$WORK"
SEEDS="1 2 3"
FLOOR_FILE=hts1a          # single file for the -O2/-O3 float noise floor

B="$WORK/codec2/build_host/src"
B2="$WORK/codec2/build_o2/src"
RAW="$WORK/codec2/raw"

echo "=== 1. build codec2 (pinned + voicing-override patch; -O3 and -O2)"
"$HERE/build.sh"

echo "=== 2. c2sim reference dumps (Wo + MBE voicing/SNR per 10 ms frame)"
mkdir -p "$WORK/dumps"
for raw in "$RAW"/*.raw; do
    name=$(basename "$raw" .raw)
    if [ ! -f "$WORK/dumps/${name}_model.txt" ]; then
        "$B/c2sim" "$raw" --dump "$WORK/dumps/$name" --phase0 -o /dev/null > /dev/null
    fi
done

echo "=== 3. recompute the FFT-free rule decisions (tree2) per frame"
python3 "$HERE/rule.py" "$RAW" "$WORK/dumps" "$WORK/decisions"

echo "=== 4. random-control decisions (matched flip rate, seeds: $SEEDS)"
# shellcheck disable=SC2086
python3 "$HERE/make_controls.py" "$RAW" "$WORK/decisions" $SEEDS

echo "=== 5. encode: stock / swap / random controls (mode 1300)"
mkdir -p "$WORK/bits"
for raw in "$RAW"/*.raw; do
    name=$(basename "$raw" .raw)
    "$B/c2enc" 1300 "$raw" "$WORK/bits/$name.stock.bit" 2> /dev/null
    C2_VOICING_OVERRIDE="$WORK/decisions/$name.rule.txt" \
        "$B/c2enc" 1300 "$raw" "$WORK/bits/$name.swap.bit" 2> /dev/null
    for s in $SEEDS; do
        C2_VOICING_OVERRIDE="$WORK/decisions/$name.rand$s.txt" \
            "$B/c2enc" 1300 "$raw" "$WORK/bits/$name.rand$s.bit" 2> /dev/null
    done
done
# noise-floor probe: full -O2 chain (encode+decode) on one file
"$B2/c2enc" 1300 "$RAW/$FLOOR_FILE.raw" "$WORK/bits/$FLOOR_FILE.stock_o2.bit" 2> /dev/null

echo "=== 6. verify overrides landed (bit-level)"
# shellcheck disable=SC2086
python3 "$HERE/verify_bitstream.py" "$WORK/bits" "$WORK/decisions" "$RAW" $SEEDS

echo "=== 7. decode everything with stock c2dec 1300"
mkdir -p "$WORK/audio"
for raw in "$RAW"/*.raw; do
    name=$(basename "$raw" .raw)
    for ver in stock swap $(for s in $SEEDS; do echo rand$s; done); do
        "$B/c2dec" 1300 "$WORK/bits/$name.$ver.bit" "$WORK/audio/$name.$ver.raw"
    done
done
"$B2/c2dec" 1300 "$WORK/bits/$FLOOR_FILE.stock_o2.bit" \
    "$WORK/audio/$FLOOR_FILE.stock_o2.raw"

echo "=== 8. WARP-Q (best-effort, timeboxed; failure is non-fatal)"
python3 "$HERE/warpq_run.py" "$WORK" "$HERE/results/warpq.json" $SEEDS \
    || echo "   WARP-Q step skipped/failed (documented in REPORT.md)"

echo "=== 9. paired metrics (segSNR/ESTOI/LSD/NMR + dESTOI vs original)"
mkdir -p "$HERE/results"
# shellcheck disable=SC2086
python3 "$HERE/run_metrics.py" "$RAW" "$WORK/audio" "$WORK/decisions" \
    "$HERE/results/metrics.json" $SEEDS

echo "=== 10. summary tables"
python3 "$HERE/summarize.py" "$HERE/results/metrics.json" \
    "$HERE/results/warpq.json" "$FLOOR_FILE" $SEEDS \
    | tee "$HERE/results/summary.md"

echo "=== done; see results/ and REPORT.md"
