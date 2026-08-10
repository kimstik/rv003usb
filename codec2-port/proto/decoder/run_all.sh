#!/usr/bin/env bash
# run_all.sh — reproduce the whole c2tube prototype experiment end to end.
#
#   1. build_oracle.sh   clone+build pinned codec2 @310777b (gitignored build/)
#   2. gen_tables.py     regenerate c2tube_tables.h + tables.py from the
#                        pinned codebooks (asserts they are integer Hz)
#   3. build the C decoder (host, -O2 for speed; -Os variants in budget.sh)
#   4. validate.py       c2enc 1300 bitstreams -> C decode, golden.py decode,
#                        byte-exact assert; float twins; segSNR + ESTOI
#                        -> results/validate.json
#   5. budget.sh         RAM/flash census (host -Os + rv32ec if toolchain)
#                        -> results/budget.txt
#
# Python deps: numpy scipy pystoi.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$HERE/build_oracle.sh"
python3 -c "import numpy, scipy, pystoi" 2>/dev/null || {
  echo "installing python deps..."; pip3 install --user numpy scipy pystoi; }

python3 "$HERE/gen_tables.py"
mkdir -p "$HERE/build/out"
gcc -std=c99 -Wall -Wextra -O2 -o "$HERE/build/c2tube" \
    "$HERE/c2tube_dec.c" "$HERE/c2tube_main.c"

python3 "$HERE/validate.py"
"$HERE/budget.sh"
echo "c2tube prototype: all done. results/ updated."
