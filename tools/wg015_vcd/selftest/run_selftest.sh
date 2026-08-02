#!/bin/sh
# Generates synthetic LS-USB VCD cases (nominal; +5000 ppm; entry 40 vs 70 cyc;
# drift +0.05 cyc/bit; malformed) and asserts wg015vcd.py reports match the
# injected values within tolerance. Exit 0 = selftest passed.
set -e
cd "$(dirname "$0")"
exec python3 ./selftest.py "$@"
