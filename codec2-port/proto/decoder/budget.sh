#!/usr/bin/env bash
# budget.sh — static RAM/flash census for the c2tube decoder.
#   - sizeof(state struct) via a host census program
#   - scratch (stack) census: counted from the decode_frame locals
#   - flash: gcc -Os on host x86-64 (proxy) and riscv rv32ec ilp32e when the
#     toolchain is present (gcc-riscv64-unknown-elf with rv32e multilib)
# Output: results/budget.txt
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$HERE/results" "$HERE/build"
OUT="$HERE/results/budget.txt"
: > "$OUT"

cat > "$HERE/build/census.c" <<'EOF'
#include <stdio.h>
#include "../c2tube_dec.h"
int main(void) {
  printf("sizeof(c2tube_dec state struct) = %zu bytes\n", sizeof(c2tube_dec));
  return 0;
}
EOF
gcc -std=c99 -I"$HERE" -o "$HERE/build/census" "$HERE/build/census.c"
"$HERE/build/census" | tee -a "$OUT"

{
  echo ""
  echo "decode_frame scratch (stack, per 40 ms frame call):"
  echo "  exc[84] int32 ................. 336 B"
  echo "  ybuf[80] int32 ................ 320 B"
  echo "  pP/pQ[12] int64 (poly build) .. 192 B   (int32 on target: 96 B)"
  echo "  a_q12/num/den[11] int32 ....... 132 B"
  echo "  h[22] int64 (tilt) ............ 176 B   (int32 on target: 88 B)"
  echo "  f_q2/c_q14/cp/cq int32 ........ 120 B"
  echo "  ctp/ctq csd_terms[10] ......... 150 B"
  echo "  misc locals ................... ~64 B"
  echo "  TOTAL scratch ................. ~1.5 KB (host int64 forms)"
  echo "                                  ~1.2 KB (target int32 forms)"
} | tee -a "$OUT"

echo "" | tee -a "$OUT"
echo "== flash census: host x86-64 -Os (proxy) ==" | tee -a "$OUT"
gcc -std=c99 -Os -c -o "$HERE/build/c2tube_dec_host.o" "$HERE/c2tube_dec.c"
size "$HERE/build/c2tube_dec_host.o" | tee -a "$OUT"

RV=riscv64-unknown-elf-gcc
if command -v $RV >/dev/null 2>&1; then
  echo "" | tee -a "$OUT"
  echo "== flash census: $RV -march=rv32ec_zicsr -mabi=ilp32e -Os ==" | tee -a "$OUT"
  $RV -std=c99 -march=rv32ec_zicsr -mabi=ilp32e -Os -c \
      -o "$HERE/build/c2tube_dec_rv32ec.o" "$HERE/c2tube_dec.c"
  riscv64-unknown-elf-size "$HERE/build/c2tube_dec_rv32ec.o" | tee -a "$OUT"
  # table payload split (rodata is inside .o text/rodata sections)
  riscv64-unknown-elf-objdump -h "$HERE/build/c2tube_dec_rv32ec.o" \
      | grep -E "\.text|\.rodata|srodata" | tee -a "$OUT" || true
else
  echo "riscv toolchain not found; host -Os proxy only" | tee -a "$OUT"
fi

echo "" | tee -a "$OUT"
python3 "$HERE/gen_tables.py" | tee -a "$OUT"
echo "budget written to results/budget.txt"
