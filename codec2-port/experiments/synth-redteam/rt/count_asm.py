"""Static cycle counter for the annotated RV32EC listings in asm/.

Region syntax:   #=== region: NAME repeat=N   ...   #=== end
Costing (identical to the round-1 cost model): ALU/shift/logic 1, load 2,
store 2, taken branch 2 (annotate #taken), untaken 1 (#notaken), j 2,
jal with #call=__mulsi3 expands to 1 + 120 + 1 (soft-mul body + ret).
"""

import os
import re

LOADS = {"lw", "lh", "lhu", "lb", "lbu"}
STORES = {"sw", "sh", "sb"}
BRANCHES = {"beq", "bne", "blt", "bge", "bltu", "bgeu", "beqz", "bnez"}
MULSI3 = 120


def instr_cost(line):
    code = line.split("#")[0].strip()
    if not code or code.endswith(":"):
        return 0
    op = code.split()[0]
    if "#call=__mulsi3" in line:
        return 1 + MULSI3 + 1
    if op in LOADS:
        return 2
    if op in STORES:
        return 2
    if op in BRANCHES:
        return 2 if "#taken" in line else 1
    if op == "j":
        return 2
    return 1


def count_file(path):
    regions = {}
    name, rep, acc = None, 1, 0
    with open(path) as fh:
        for line in fh:
            m = re.match(r"\s*#===\s*region:\s*(\S+)\s+repeat=(\d+)", line)
            if m:
                name, rep, acc = m.group(1), int(m.group(2)), 0
                continue
            if re.match(r"\s*#===\s*end", line):
                if name:
                    regions[name] = {"cycles_once": acc, "repeat": rep,
                                     "cycles_total": acc * rep}
                name = None
                continue
            if name:
                acc += instr_cost(line)
    total = sum(r["cycles_total"] for r in regions.values())
    return {"regions": regions, "cycles_per_iter": total}


if __name__ == "__main__":
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "asm")
    for f in sorted(os.listdir(here)):
        if f.endswith(".s"):
            r = count_file(os.path.join(here, f))
            print(f"{f}: {r['cycles_per_iter']} cycles/iter")
            for n, v in r["regions"].items():
                print(f"   {n:16s} {v['cycles_once']:4d} x{v['repeat']}"
                      f" = {v['cycles_total']}")
