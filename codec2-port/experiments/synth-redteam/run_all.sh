#!/bin/sh
# SYNTH-REDTEAM: full reproduction script (adversarial cross-check of the
# round-1 synth bake-off, experiments/synth-bakeoff).
# 1. (optional) clone+build codec2 and dump real model params from hts1a
#    (same recipe and revision policy as round-1's run_all.sh)
# 2. run the red-team bench: steady grid + stress envelopes + transitions +
#    subframe-update attack + Q15 idle-channel probe + wave-2 SD study +
#    real-speech section (skipped gracefully if step 1 unavailable)
# 3. LSP->pole-pair conversion attack (rt_lsp_approx.py)
# 4. cost-model audit: static RV32EC asm counts vs round-1 ranking model
#    (rt_cost.py; flags discrepancies > 1.5x)
# Round-1 bench modules are imported verbatim from bench_r1/ -- every number
# in REPORT.md shares metrics and reference with round 1.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
C2="$HERE/third_party/codec2"
DUMP_DIR="$HERE/out/dump"
MODEL="$DUMP_DIR/hts1a_model.txt"

if [ ! -f "$MODEL" ]; then
    mkdir -p "$HERE/third_party" "$DUMP_DIR"
    if [ ! -d "$C2" ]; then
        git clone --depth 1 https://github.com/drowe67/codec2.git "$C2" || true
    fi
    if [ -d "$C2" ]; then
        # DUMP is only compiled in Debug builds (-DDUMP)
        cmake -S "$C2" -B "$C2/build" -DCMAKE_BUILD_TYPE=Debug >/dev/null
        make -C "$C2/build" -j4 c2sim >/dev/null
        (cd "$C2/build" && ./src/c2sim ../raw/hts1a.raw --dump "$DUMP_DIR/hts1a" >/dev/null)
    fi
fi

{
    python3 "$HERE/rt/run_redteam.py" "$MODEL"
    python3 "$HERE/rt/rt_lsp_approx.py"
    python3 "$HERE/rt/rt_cost.py"
} 2>&1 | tee "$HERE/results/run_log.txt"
echo "results in $HERE/results, plots in $HERE/plots"
