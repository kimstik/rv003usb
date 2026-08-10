#!/usr/bin/env python3
"""size_ladder.py — static RAM/flash census of the excitation-ladder stages
(pareto REPORT.md coverage gap #3: only the L0 engine had RAM/flash numbers;
the L1/L2/L4 increments were never itemized).

Ground truth where it exists: proto/decoder (an integer G8 + L0+L2+L4 decoder
with a measured budget, results/budget.txt).  Three measurement layers:

  1. STATE (RAM): sizeof() census of the c2tube_dec struct fields, grouped by
     stage — compiled and run on host (same technique as proto budget.sh).
  2. TABLES (flash .rodata): sizeof() census of the c2tube_tables.h arrays,
     grouped by stage.
  3. CODE (flash .text): rv32ec -Os compile of c2tube_dec.c plus two
     marker-based stage-stripped variants (no-L2, no-L4) generated from the
     pristine source; the .text delta of each variant measures the stage's
     code increment the same way a #ifdef would.  (The proto source has no
     compile-time stage switches; the stripping anchors on its unique
     comment markers and fails loudly if they move.)

L1 (dispersion) has NO C implementation (proto gap #1) — its line is a
static estimate from tube.py's golden model (65-tap int16 Q14 FIR table;
excitation tail widened from 4 to 67 int32 words; ~30-instruction stamp
loop), marked estimate in the output.

Output: results/ladder_ram_flash.csv + .json
"""
import csv
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROTO = os.path.abspath(os.path.join(HERE, "..", "..", "proto", "decoder"))
BUILD = os.path.join(HERE, "build", "size")
RESULTS = os.path.join(HERE, "results")
RV = "riscv64-unknown-elf-gcc"
RVSIZE = "riscv64-unknown-elf-size"

# struct fields per stage (c2tube_dec.h)
STATE_GROUPS = {
    "param-memories": ["prev_lsp_q2", "prev_wo_num_q2", "prev_lg2e_q8",
                       "prev_voiced"],
    "L0-excitation": ["tau_q7", "lfsr", "exc_tail"],
    "G8-filter": ["s1p", "s2p", "s1q", "s2q", "sp_last", "sq_last"],
    "L2-crossover": ["zlp", "zhp"],
    "L4-postfilter": ["ynum_hist", "yden_hist", "tilt_state"],
}
# tables per stage (c2tube_tables.h)
TABLE_GROUPS = {
    "param-path": ["LSP_BITS_TAB", "LSP_CB_FLAT", "LSP_CB_OFF", "LG2E_Q8",
                   "COS_Q14", "EXP2_Q14", "LOG2_Q8"],
    "L2-crossover": ["B_LP_Q14", "A_LP_Q14", "B_HP_Q14", "A_HP_Q14"],
    "L4-postfilter": ["G1POW_Q14", "G2POW_Q14"],
}


def census_sizes():
    """sizeof() of struct fields and tables via a host census program."""
    lines = ["#include <stdio.h>", "#include <stddef.h>",
             '#include "c2tube_dec.h"', '#include "c2tube_tables.h"',
             "int main(void){"]
    fields = [f for g in STATE_GROUPS.values() for f in g]
    tables = [t for g in TABLE_GROUPS.values() for t in g]
    for f in fields:
        lines.append(f'printf("field {f} %zu\\n",'
                     f' sizeof(((c2tube_dec*)0)->{f}));')
    for t in tables:
        lines.append(f'printf("table {t} %zu\\n", sizeof({t}));')
    lines.append('printf("struct total %zu\\n", sizeof(c2tube_dec));')
    lines.append("return 0;}")
    os.makedirs(BUILD, exist_ok=True)
    src = os.path.join(BUILD, "census.c")
    with open(src, "w") as fh:
        fh.write("\n".join(lines))
    exe = os.path.join(BUILD, "census")
    subprocess.run(["gcc", "-std=c99", "-I", PROTO, "-o", exe, src],
                   check=True)
    out = subprocess.run([exe], check=True, capture_output=True, text=True)
    sizes = {}
    for line in out.stdout.splitlines():
        kind, name, sz = line.split()
        sizes[(kind, name)] = int(sz)
    return sizes


# ---------------------------------------------------------------------------
# stage-stripped compile variants (code .text deltas)
# ---------------------------------------------------------------------------

def _cut(src, start_marker, end_marker, repl=""):
    a = src.index(start_marker)
    b = src.index(end_marker, a)
    return src[:a] + repl + src[b:]


def make_variants():
    with open(os.path.join(PROTO, "c2tube_dec.c")) as fh:
        full = fh.read()

    # --- no-L2: pulse goes straight into g8_step, no biquads, no noise mix
    nol2 = _cut(full,
                "      s_n = exp2_shift(",
                "      for (n = 0; n < C2TUBE_N; n++) {\n"
                "        int32_t pulse = sat32((int64_t)exc[n] - dc_q);")
    nol2 = nol2.replace(
        "      for (n = 0; n < C2TUBE_N; n++) {\n"
        "        int32_t pulse = sat32((int64_t)exc[n] - dc_q);\n"
        "        int32_t nq = lfsr_step(&d->lfsr);\n"
        "        int32_t nn = sat32((int64_t)nq * s_n >> 15);\n"
        "        int32_t lp = biquad(pulse, B_LP_Q14, A_LP_Q14, d->zlp);\n"
        "        int32_t hp = biquad(nn, B_HP_Q14, A_HP_Q14, d->zhp);\n"
        "        int32_t x = sat32((int64_t)lp + hp);\n",
        "      for (n = 0; n < C2TUBE_N; n++) {\n"
        "        int32_t x = sat32((int64_t)exc[n] - dc_q);\n")
    assert "biquad(pulse" not in nol2 and "s_n = exp2_shift" not in nol2

    # --- no-L4: drop pf coeffs + tilt-mu block, postfilter loop, AGC
    nol4 = _cut(full,
                "    /* -- L4 postfilter coefficients + tilt mu",
                "    /* -- excitation (L0 + L2) -- */")
    nol4 = _cut(nol4,
                "    /* -- L4 postfilter: num/den + tilt, in place",
                "    /* -- guard-bit removal with rounding, output int16 -- */")
    nol4 = nol4.replace(
        "      int64_t t = (int64_t)ybuf[n] * g_q14 >> 14;\n",
        "      int64_t t = (int64_t)ybuf[n];\n")
    # g_q14 / e_in / e_out now unused: neutralize declarations
    nol4 = nol4.replace("    uint64_t e_in = 0, e_out = 0;\n"
                        "    int32_t g_q14;\n", "")
    nol4 = nol4.replace("        e_in += (uint64_t)((int64_t)y * y);\n", "")
    nol4 = nol4.replace(
        "    int32_t a_q12[11], num_q12[11], den_q12[11];\n",
        "    int32_t a_q12[11];\n")
    nol4 = nol4.replace("    int32_t mu_q15;\n", "")
    assert "num_q12" not in nol4 and "e_out" not in nol4

    return {"full": full, "nol2": nol2, "nol4": nol4}


def rv_text_size(tag, src_text):
    src = os.path.join(BUILD, f"c2tube_{tag}.c")
    with open(src, "w") as fh:
        fh.write(src_text)
    obj = os.path.join(BUILD, f"c2tube_{tag}.o")
    subprocess.run([RV, "-std=c99", "-march=rv32ec_zicsr", "-mabi=ilp32e",
                    "-Os", "-I", PROTO, "-c", "-o", obj, src], check=True)
    out = subprocess.run([RVSIZE, obj], check=True, capture_output=True,
                         text=True).stdout.splitlines()[1].split()
    return int(out[0])  # text column (includes rodata for .o census)


def main():
    os.makedirs(RESULTS, exist_ok=True)
    sizes = census_sizes()

    state = {g: sum(sizes[("field", f)] for f in fs)
             for g, fs in STATE_GROUPS.items()}
    tables = {g: sum(sizes[("table", t)] for t in ts)
              for g, ts in TABLE_GROUPS.items()}

    variants = make_variants()
    text = {tag: rv_text_size(tag, s) for tag, s in variants.items()}
    code_l2 = text["full"] - text["nol2"]
    code_l4 = text["full"] - text["nol4"]

    rows = [
        {"stage": "L0+G8 engine+param path (all-but-L2/L4)",
         "state_ram_b": state["param-memories"] + state["L0-excitation"]
                        + state["G8-filter"],
         "table_flash_b": tables["param-path"],
         "code_flash_b": text["full"] - code_l2 - code_l4,
         "source": "measured (census + rv32ec -Os stripped variants)"},
        {"stage": "+L1 dispersion (NOT in proto; tube.py model)",
         "state_ram_b": 63 * 4,
         "table_flash_b": 65 * 2,
         "code_flash_b": 60,
         "source": "estimate: exc tail 4->67 int32 words; 65-tap int16 Q14 "
                   "FIR; ~30-instr stamp loop (proto gap #1)"},
        {"stage": "+L2 mixed excitation",
         "state_ram_b": state["L2-crossover"],
         "table_flash_b": tables["L2-crossover"],
         "code_flash_b": code_l2,
         "source": "measured"},
        {"stage": "+L4 postfilter+tilt+AGC",
         "state_ram_b": state["L4-postfilter"],
         "table_flash_b": tables["L4-postfilter"],
         "code_flash_b": code_l4,
         "source": "measured"},
    ]
    total = {"stage": "TOTAL full decoder (struct sizeof cross-check)",
             "state_ram_b": sizes[("struct", "total")],
             "table_flash_b": sum(tables.values()),
             "code_flash_b": text["full"],
             "source": f"struct={sizes[('struct', 'total')]} B; rv32ec .o "
                       f"text+rodata={text['full']} B (budget.txt: 236 B / "
                       "6706 B)"}
    rows.append(total)

    with open(os.path.join(RESULTS, "ladder_ram_flash.csv"), "w",
              newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    detail = {"field_sizes": {f: sizes[("field", f)]
                              for g in STATE_GROUPS.values() for f in g},
              "table_sizes": {t: sizes[("table", t)]
                              for g in TABLE_GROUPS.values() for t in g},
              "rv32ec_text_bytes": text,
              "code_delta_l2": code_l2, "code_delta_l4": code_l4}
    with open(os.path.join(RESULTS, "ladder_ram_flash.json"), "w") as fh:
        json.dump(detail, fh, indent=1)
    for r in rows:
        print(f"{r['stage']:45s} RAM {r['state_ram_b']:4d}  "
              f"tables {r['table_flash_b']:4d}  code {r['code_flash_b']:5d}  "
              f"[{r['source'][:40]}]")


if __name__ == "__main__":
    main()
