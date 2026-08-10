#!/usr/bin/env bash
# build_oracle.sh — clone (shallow, pinned) and build the codec2 float oracle on host.
#
# The float build of codec2 is the project's golden oracle (codec2-port/README.md §4):
# it is the source of reference parameters/audio, never a source of code.
#
# Produces:
#   build/codec2/            pinned source checkout
#   build/codec2/build_host/ cmake Release build (c2enc, c2dec, c2sim, ...)
#
# Idempotent: re-running skips completed steps. `./build_oracle.sh --clean` wipes build/.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$HERE/build"
SRC="$BUILD/codec2"
BDIR="$SRC/build_host"

# --- pinned version -----------------------------------------------------------
CODEC2_REPO=""
CODEC2_COMMIT=""
# shellcheck disable=SC1090
source <(grep -E '^CODEC2_(REPO|COMMIT)=' "$HERE/VERSION")
[ -n "$CODEC2_REPO" ] && [ -n "$CODEC2_COMMIT" ] || {
  echo "ERROR: VERSION must define CODEC2_REPO and CODEC2_COMMIT" >&2; exit 1; }

if [ "${1:-}" = "--clean" ]; then rm -rf "$BUILD"; fi
mkdir -p "$BUILD"

# --- fetch (shallow, exact commit) -------------------------------------------
if [ ! -e "$SRC/.git" ] || [ "$(git -C "$SRC" rev-parse HEAD 2>/dev/null)" != "$CODEC2_COMMIT" ]; then
  rm -rf "$SRC"
  mkdir -p "$SRC"
  git -C "$SRC" init -q
  git -C "$SRC" remote add origin "$CODEC2_REPO"
  # GitHub allows fetching an arbitrary reachable commit by full SHA.
  git -C "$SRC" fetch -q --depth 1 origin "$CODEC2_COMMIT"
  git -C "$SRC" checkout -q FETCH_HEAD
fi
echo "codec2 @ $(git -C "$SRC" rev-parse HEAD)"
[ "$(git -C "$SRC" rev-parse HEAD)" = "$CODEC2_COMMIT" ] || {
  echo "ERROR: checked-out commit does not match VERSION pin" >&2; exit 1; }

# --- build (host, Release, no extras) ----------------------------------------
if [ ! -x "$BDIR/src/c2sim" ]; then
  # -DDUMP enables c2sim --dump (upstream ties it to Debug builds only; we want
  # Release speed + dump instrumentation for the oracle harness).
  cmake -S "$SRC" -B "$BDIR" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_FLAGS="-DDUMP" >"$BUILD/cmake.log" 2>&1 || {
    tail -30 "$BUILD/cmake.log" >&2; exit 1; }
  cmake --build "$BDIR" -j"$(nproc)" >"$BUILD/make.log" 2>&1 || {
    tail -30 "$BUILD/make.log" >&2; exit 1; }
fi

# --- verify the three tools actually work ------------------------------------
C2ENC="$BDIR/src/c2enc"; C2DEC="$BDIR/src/c2dec"; C2SIM="$BDIR/src/c2sim"
for t in "$C2ENC" "$C2DEC" "$C2SIM"; do
  [ -x "$t" ] || { echo "ERROR: missing tool $t" >&2; exit 1; }
done

RAW="$SRC/raw/hts1a.raw"
[ -f "$RAW" ] || { echo "ERROR: corpus file $RAW missing" >&2; exit 1; }
TMP="$BUILD/selftest"; mkdir -p "$TMP"

"$C2ENC" 1300 "$RAW" "$TMP/hts1a.c2" >/dev/null
"$C2DEC" 1300 "$TMP/hts1a.c2" "$TMP/hts1a_1300.raw" >/dev/null
"$C2SIM" "$RAW" -o "$TMP/hts1a_sim.raw" >/dev/null 2>&1

in_bytes=$(stat -c %s "$RAW")
c2_bytes=$(stat -c %s "$TMP/hts1a.c2")
dec_bytes=$(stat -c %s "$TMP/hts1a_1300.raw")
sim_bytes=$(stat -c %s "$TMP/hts1a_sim.raw")
# 1300 bps mode: 52 bits/40 ms frame -> 7 bytes per 640 audio bytes.
[ "$c2_bytes" -gt 0 ] && [ "$dec_bytes" -gt 0 ] && [ "$sim_bytes" -gt 0 ] || {
  echo "ERROR: encode/decode/sim self-test produced empty output" >&2; exit 1; }

echo "oracle OK: c2enc/c2dec/c2sim built and ran"
echo "  hts1a.raw $in_bytes B -> .c2 $c2_bytes B -> dec $dec_bytes B; c2sim out $sim_bytes B"
echo "  binaries: $BDIR/src/{c2enc,c2dec,c2sim}"
