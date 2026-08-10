#!/usr/bin/env bash
# COVERAGE (round 4): full reproduction script.
# Closes the coverage gaps itemized in experiments/pareto/REPORT.md:
#   1. neural judges (NISQA/DNSMOS) for every tube-ladder rung + the two
#      EXACT recommended knee subsets (P1 = L0+L1+L2.5k+L4, P2 = L0+L2.5k+L4)
#   2. classic metrics (LSD/NMR/crest/ESTOI) for those exact subsets
#   3. engine real-speech LSD extended from hts1a to the 3-utterance corpus
#   4. RAM/flash census of the ladder stages (proto/decoder ground truth)
#   5. audited P2 cycle breakdown G8/L2/L4/lattice (asm/, count_asm.py)
# then re-collates the pareto dataset + fronts + plots.
#
# Deps: the pinned codec2 oracle build of ../tube-ladder (build_oracle.sh +
# make_dumps.sh), python3 with numpy/scipy/pystoi, speechmos + torch for the
# judges (NISQA repo cloned/copied by the metrics-adequacy recipe), and
# riscv64-unknown-elf-gcc for the flash census.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
LADDER="$HERE/../tube-ladder"

# 0. oracle + dumps + rung wavs (idempotent; run_ladder regenerates the
#    round-2 wavs the neural judges consume — its metrics.csv reproduction
#    against the committed one is the sanity gate)
[ -x "$LADDER/build/codec2/build_host/src/c2sim" ] || (cd "$LADDER" && ./build_oracle.sh)
[ -f "$LADDER/build/dumps/q1300/hts1a/hts1a.npz" ] || (cd "$LADDER" && ./make_dumps.sh)
[ -f "$LADDER/build/wavs/q1300_hts1a_L0.wav" ] || (cd "$LADDER" && python3 run_ladder.py)

python3 "$HERE/run_knees.py"          # -> results/knees_metrics.csv (+wavs)
python3 "$HERE/run_neural_ladder.py"  # -> results/neural_ladder.csv
python3 "$HERE/run_real_engines.py"   # -> results/real_engines_3utt.csv
python3 "$HERE/size_ladder.py"        # -> results/ladder_ram_flash.{csv,json}
python3 "$HERE/cycles_g8.py"          # -> results/cycles_p2.{csv,json}

# re-collate the tradeoff dataset with the new cells
(cd "$HERE/../pareto" && python3 collect.py && python3 fronts.py && python3 plots.py)
echo "coverage round-4 done; results in $HERE/results, pareto refreshed"
