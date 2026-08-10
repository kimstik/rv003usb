#!/bin/sh
# Reproduce the pareto dataset, fronts and plots from the COMMITTED result
# files of the source experiments (no rebuilds, no synthesis — pure collation).
# If this experiment is checked out without its siblings, point
# CODEC2_EXPERIMENTS at a tree that has them, e.g.:
#   CODEC2_EXPERIMENTS=/path/to/codec2-port/experiments ./run_all.sh
set -e
cd "$(dirname "$0")"
python3 collect.py
python3 fronts.py
python3 plots.py
echo "done: results/pareto.csv results/fronts.{json,md} plots/*.png"
