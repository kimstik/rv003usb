#!/usr/bin/env bash
# build_decoders.sh — build the two c2tube host binaries used by the bench.
#
#   out/build/bin/c2tube      full P2 knee   (L0+L2+L4)  == proto/decoder as merged
#   out/build/bin/c2tube_l0   rung L0 only   (-DC2TUBE_L0_ONLY)
#
# Guard: c2tube is also built from a PRISTINE copy of proto/decoder and the two
# binaries' decoded output must be byte-identical, proving the #ifdef surgery
# in mk_l0_variant.py is inert when the define is absent.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$HERE/out/build/src"
PRI="$HERE/out/build/src_pristine"
BIN="$HERE/out/build/bin"
mkdir -p "$BIN"

CFLAGS="-O2 -std=c99 -Wall -Wno-unused-variable"

cc $CFLAGS -I"$SRC" "$SRC/c2tube_dec.c" "$SRC/c2tube_main.c" -o "$BIN/c2tube"
cc $CFLAGS -DC2TUBE_L0_ONLY -I"$SRC" "$SRC/c2tube_dec.c" "$SRC/c2tube_main.c" \
   -o "$BIN/c2tube_l0"
cc $CFLAGS -I"$PRI" "$PRI/c2tube_dec.c" "$PRI/c2tube_main.c" \
   -o "$BIN/c2tube_pristine"

# --- inertness check on a real bitstream --------------------------------------
C2ENC="$HERE/build/codec2/build_host/src/c2enc"
RAW="$HERE/build/codec2/raw/hts1a.raw"
T="$HERE/out/build/selftest"; mkdir -p "$T"
"$C2ENC" 1300 "$RAW" "$T/hts1a.c2" >/dev/null 2>&1
"$BIN/c2tube"          "$T/hts1a.c2" "$T/a.raw" 2>/dev/null
"$BIN/c2tube_pristine" "$T/hts1a.c2" "$T/b.raw" 2>/dev/null
cmp -s "$T/a.raw" "$T/b.raw" || {
  echo "ERROR: guarded build differs from pristine proto/decoder build" >&2
  exit 1; }
"$BIN/c2tube_l0" "$T/hts1a.c2" "$T/c.raw" 2>/dev/null
cmp -s "$T/a.raw" "$T/c.raw" && {
  echo "ERROR: -DC2TUBE_L0_ONLY changed nothing — guards not compiled" >&2
  exit 1; }

echo "decoders OK: c2tube == pristine proto/decoder; c2tube_l0 differs (L0 rung)"
