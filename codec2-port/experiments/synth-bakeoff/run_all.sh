#!/bin/sh
# SYNTH-BAKEOFF: full reproduction script.
# 1. (optional) clone+build codec2 and dump real model params from hts1a
# 2. run the python bench: synthetic grid + transitions + CSD study + cost
#    model + real-speech section (skipped gracefully if step 1 unavailable)
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

python3 "$HERE/bench/run_bench.py" "$MODEL"
echo "results in $HERE/results, plots in $HERE/plots"
