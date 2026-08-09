#!/bin/bash
# build.sh -- clone + build codec2 (pinned), extract reference voicing dumps.
#
# Reference voicing extraction (verified against src/dump.c and src/c2sim.c
# at the pinned commit):
#
#   c2sim file.raw --dump prefix --phase0 -o /dev/null
#
# produces per 10 ms analysis frame:
#   prefix_model.txt : "Wo L A[1]..A[160] voiced"  (dump_model, dump.c:201)
#   prefix_snr.txt   : MBE voicing SNR in dB       (dump_snr,  dump.c:311)
#
# IMPORTANT alignment quirk: in c2sim.c the call order per frame is
#   dump_model(&model);            // c2sim.c:659  <-- voiced flag NOT yet updated
#   snr = est_voicing_mbe(...);    // c2sim.c:688
#   dump_snr(snr);                 // c2sim.c:698  (only under --phase0)
# so the "voiced" column on line k of *_model.txt is frame k-1's decision,
# while *_snr.txt line k is frame k's MBE SNR.  features.py therefore
# RECONSTRUCTS frame k's voicing decision from snr[k] > V_THRESH(=6 dB) plus
# the eratio post-processing of est_voicing_mbe (sine.c:444) applied to the
# A[]/Wo dumped on the same line; this reconstruction matches the shifted
# voiced column exactly (validated in features.py, --selfcheck).
#
# --phase0 is required to get the SNR dump; it does not change the analysis
# or the voicing decision (it only affects the synthesis path).
#
# Usage:
#   ./build.sh          clone+build (if needed) and dump the clean corpus
#   ./build.sh dump <file.raw> <outprefix>   dump one extra raw file (noisy probes)

set -euo pipefail

EXP_DIR="$(cd "$(dirname "$0")" && pwd)"
WORK="${VOICING_WORK:-$EXP_DIR/work}"
CODEC2_URL="https://github.com/drowe67/codec2"
CODEC2_COMMIT="310777b1c6f1af0bc7c72f5b32f80f6fd9136962"   # == codec2-port pin 310777b
C2SIM="$WORK/codec2/build_linux/src/c2sim"

mkdir -p "$WORK"

clone_and_build() {
    if [ ! -d "$WORK/codec2/.git" ]; then
        echo "== shallow clone codec2 @ $CODEC2_COMMIT"
        mkdir -p "$WORK/codec2"
        git -C "$WORK/codec2" init -q
        git -C "$WORK/codec2" remote add origin "$CODEC2_URL" 2>/dev/null || true
        git -C "$WORK/codec2" fetch -q --depth 1 origin "$CODEC2_COMMIT"
        git -C "$WORK/codec2" checkout -q FETCH_HEAD
    fi
    HEAD_NOW=$(git -C "$WORK/codec2" rev-parse HEAD)
    if [ "$HEAD_NOW" != "$CODEC2_COMMIT" ]; then
        echo "ERROR: codec2 checkout at $HEAD_NOW, expected $CODEC2_COMMIT" >&2
        exit 1
    fi
    if [ ! -x "$C2SIM" ]; then
        echo "== build c2sim (Debug => -DDUMP enabled, see top-level CMakeLists CMAKE_C_FLAGS_DEBUG)"
        mkdir -p "$WORK/codec2/build_linux"
        (cd "$WORK/codec2/build_linux" && \
            cmake -DCMAKE_BUILD_TYPE=Debug -DUNITTEST=0 .. > cmake.log 2>&1 && \
            make c2sim -j"$(nproc)" > make.log 2>&1)
    fi
}

dump_one() {
    local raw="$1" prefix="$2"
    mkdir -p "$(dirname "$prefix")"
    "$C2SIM" "$raw" --dump "$prefix" --phase0 -o /dev/null > /dev/null
}

case "${1:-all}" in
    dump)
        clone_and_build
        dump_one "$2" "$3"
        ;;
    all)
        clone_and_build
        echo "== dump clean corpus (all codec2/raw/*.raw)"
        mkdir -p "$WORK/dumps/clean"
        for raw in "$WORK"/codec2/raw/*.raw; do
            name=$(basename "$raw" .raw)
            dump_one "$raw" "$WORK/dumps/clean/$name"
            echo "   $name: $(wc -l < "$WORK/dumps/clean/${name}_model.txt") frames"
        done
        ;;
    *)
        echo "usage: $0 [all | dump file.raw outprefix]" >&2; exit 1;;
esac
