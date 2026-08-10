#!/usr/bin/env bash
# run_all.sh — regenerate the whole testbench from scratch.
#
#   ./run_all.sh              full run
#   ./run_all.sh --no-neural  skip the NISQA/DNSMOS column (fast, no torch)
#   ./run_all.sh --clean      wipe build/ and out/ first
#
# Reads codec2-port/{proto,experiments} (never writes there); everything it
# produces lands under testbench/build/ (oracle checkout) and testbench/out/.
# Set C2PORT_ROOT if the research tree is not the parent directory of this one.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

NEURAL=1
for a in "$@"; do
  case "$a" in
    --clean) rm -rf "$HERE/build" "$HERE/out/build" "$HERE/out/wavs" \
                    "$HERE/out/corpus" "$HERE/out/results" ;;
    --no-neural) NEURAL=0 ;;
    *) echo "unknown option: $a" >&2; exit 2 ;;
  esac
done

export C2PORT_ROOT="${C2PORT_ROOT:-$(cd "$HERE/.." && pwd)}"
echo "== research tree: $C2PORT_ROOT"

echo "== 1/7 oracle (pinned codec2, VERSION)"
./build_oracle.sh

echo "== 2/7 external corpus fetch (best effort)"
./fetch_ext.sh

echo "== 3/7 corpus assembly"
python3 corpus.py

echo "== 4/7 decoder builds (full ladder + L0-only)"
python3 mk_l0_variant.py
./build_decoders.sh

echo "== 5/7 condition matrix + signal metrics"
python3 run_matrix.py

echo "== 6/7 reference-free neural judges"
if [ "$NEURAL" = 1 ]; then python3 neural.py; else python3 neural.py --skip; fi

echo "== 7/7 pages"
python3 make_listen.py
python3 make_experiments.py

ls -lh out/listen.html out/experiments.html
echo "done — open out/listen.html and out/experiments.html from file://"
