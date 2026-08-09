#!/usr/bin/env bash
# build.sh -- clone pinned codec2, apply the encoder voicing-override patch,
# build the tools for the voicing-swap A/B experiment.
#
# Produces (all under work/, gitignored):
#   work/codec2/                pinned checkout + voicing_override.diff applied
#   work/codec2/build_host/     cmake Release (gcc default -O3) + -DDUMP
#                               -> c2enc (patched, override via env
#                                  C2_VOICING_OVERRIDE), c2dec, c2sim
#   work/codec2/build_o2/       same source, Release with -O2 instead of -O3
#                               -> c2enc/c2dec for the float noise-floor probe
#
# The patch (voicing_override.diff) only touches codec2_encode_1300(): with
# C2_VOICING_OVERRIDE unset the binaries are bit-exact stock (verified by
# verify_bitstream.py which checks the stock bitstream voicing bits against
# the independently reconstructed MBE reference decisions).
#
# Idempotent.  `./build.sh --clean` wipes work/.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK="${REGATE_WORK:-$HERE/work}"
SRC="$WORK/codec2"

CODEC2_REPO=""
CODEC2_COMMIT=""
# shellcheck disable=SC1090
source <(grep -E '^CODEC2_(REPO|COMMIT)=' "$HERE/VERSION")
[ -n "$CODEC2_REPO" ] && [ -n "$CODEC2_COMMIT" ] || {
  echo "ERROR: VERSION must define CODEC2_REPO and CODEC2_COMMIT" >&2; exit 1; }

if [ "${1:-}" = "--clean" ]; then rm -rf "$WORK"; fi
mkdir -p "$WORK"

# --- fetch (shallow, exact commit; pattern from experiments/oracle) -----------
if [ ! -e "$SRC/.git" ]; then
  rm -rf "$SRC"
  mkdir -p "$SRC"
  git -C "$SRC" init -q
  git -C "$SRC" remote add origin "$CODEC2_REPO"
  git -C "$SRC" fetch -q --depth 1 origin "$CODEC2_COMMIT"
  git -C "$SRC" checkout -q FETCH_HEAD
fi
[ "$(git -C "$SRC" rev-parse HEAD)" = "$CODEC2_COMMIT" ] || {
  echo "ERROR: checked-out commit does not match VERSION pin" >&2; exit 1; }
echo "codec2 @ $(git -C "$SRC" rev-parse HEAD)"

# --- apply the voicing override patch (once) ----------------------------------
if [ ! -f "$SRC/.voicing_override_applied" ]; then
  git -C "$SRC" apply "$HERE/voicing_override.diff"
  touch "$SRC/.voicing_override_applied"
  echo "applied voicing_override.diff"
fi
grep -q voicing_override_next "$SRC/src/codec2.c" || {
  echo "ERROR: patch not present in src/codec2.c" >&2; exit 1; }

# --- build: main (Release = -O3) + -DDUMP for c2sim dumps ---------------------
if [ ! -x "$SRC/build_host/src/c2sim" ]; then
  cmake -S "$SRC" -B "$SRC/build_host" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_FLAGS="-DDUMP" -DUNITTEST=0 >"$WORK/cmake_host.log" 2>&1 || {
    tail -30 "$WORK/cmake_host.log" >&2; exit 1; }
  cmake --build "$SRC/build_host" -j"$(nproc)" --target c2enc c2dec c2sim \
    >"$WORK/make_host.log" 2>&1 || { tail -30 "$WORK/make_host.log" >&2; exit 1; }
fi

# --- build: -O2 variant for the float optimization noise floor ----------------
if [ ! -x "$SRC/build_o2/src/c2dec" ]; then
  cmake -S "$SRC" -B "$SRC/build_o2" -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_FLAGS="-DDUMP" -DCMAKE_C_FLAGS_RELEASE="-O2 -DNDEBUG" \
    -DUNITTEST=0 >"$WORK/cmake_o2.log" 2>&1 || {
    tail -30 "$WORK/cmake_o2.log" >&2; exit 1; }
  cmake --build "$SRC/build_o2" -j"$(nproc)" --target c2enc c2dec \
    >"$WORK/make_o2.log" 2>&1 || { tail -30 "$WORK/make_o2.log" >&2; exit 1; }
fi

for t in build_host/src/c2enc build_host/src/c2dec build_host/src/c2sim \
         build_o2/src/c2enc build_o2/src/c2dec; do
  [ -x "$SRC/$t" ] || { echo "ERROR: missing $SRC/$t" >&2; exit 1; }
done
echo "build OK: $SRC/build_host/src/{c2enc,c2dec,c2sim}, $SRC/build_o2/src/{c2enc,c2dec}"
