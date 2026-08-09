"""Red-team cost audit.

Part 1 -- RV32EC static-assembly cross-check of the round-1 ranking model:
hand-written inner loops in asm/ are statically counted (count_asm.py, same
cycle prices as the round-1 model: alu/shift 1, load 2, store 2, taken
branch 2, __mulsi3 = 120+call) and compared against cost_model.py numbers.
Discrepancy > 1.5x is flagged.

Part 2 -- model-class cost rows (round-1 conventions, engine_cost-style) for
the red-team engines, incl. per-frame conversion costs the round-1 model
booked as "~0" (re-CSD per subframe, JIT emit, a->k soft-divs, and the
LSP->pole-pair problem measured in rt_lsp_approx.py).
"""

import csv
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(ROOT, "bench_r1"))

import count_asm  # noqa: E402
from cost_model import engine_cost, MUL_SOFT, FRAME_RATE, FS  # noqa: E402

RESULTS = os.path.join(ROOT, "results")
ASM = os.path.join(_HERE, "asm")

# per-subframe (100 Hz) conversion costs, cycles (static estimates, RV32EC):
RECSD_CYC = 1200      # greedy CSD of 10 coeffs x 3 terms, no CLZ on RV32EC
JIT_EMIT_CYC = 500    # ~70 instr emitted at ~7 cyc/instr into SRAM buffer
LSP2LPC_CYC = 400     # LSP -> A(z) polynomial expansion, shift-add class
A2K_CYC = 2000        # a->k downdate: 10 soft-divisions dominate
SUBFRAME_HZ = 100
FIRE_MUL_CYC = 130    # split-impulse frac: 1 soft-mul per pitch period


def asm_crosscheck():
    out = {}
    jit = count_asm.count_file(os.path.join(ASM, "sos_csd_jit.s"))
    itp = count_asm.count_file(os.path.join(ASM, "sos_csd_interp.s"))
    osc = count_asm.count_file(os.path.join(ASM, "oscbank_step.s"))

    # --- SOS-CSD cascade, P2 (no mul) ---
    model = engine_cost("impulse-iir-csd-sos", 20)
    f0_mid = 150.0
    for tag, cyc, extra_frame in (
            ("jit", jit["cycles_per_iter"],
             RECSD_CYC + JIT_EMIT_CYC + LSP2LPC_CYC),
            ("interp", itp["cycles_per_iter"],
             RECSD_CYC + LSP2LPC_CYC)):
        mhz = (cyc * FS + FIRE_MUL_CYC * f0_mid
               + extra_frame * SUBFRAME_HZ) / 1e6
        ratio = mhz / model["mhz_nomul"]
        out[f"sos-csd-{tag}"] = {
            "asm_cycles_per_sample": cyc,
            "subframe_overhead_cyc": extra_frame,
            "asm_mhz": round(mhz, 2),
            "round1_model_mhz": round(model["mhz_nomul"], 2),
            "ratio": round(ratio, 2),
            "flag_gt_1.5x": bool(ratio > 1.5),
        }

    # --- osc-bank per-harmonic, P2 soft-mul ---
    per_h = osc["cycles_per_iter"]
    for L in (20, 80):
        m = engine_cost("osc-bank", L)
        cyc = per_h * L + 15                      # +acc init/output scaffolding
        mhz = (cyc * FS + m["setup_cycles_per_frame_nomul"] * FRAME_RATE) / 1e6
        ratio = mhz / m["mhz_nomul"]
        out[f"osc-bank-L{L}"] = {
            "asm_cycles_per_sample": cyc,
            "asm_mhz": round(mhz, 2),
            "round1_model_mhz": round(m["mhz_nomul"], 2),
            "ratio": round(ratio, 2),
            "flag_gt_1.5x": bool(ratio > 1.5),
        }
    return out


# ----------------------------------------------------------------------------
# model-class rows for red-team engines (same op-price convention as round-1)
# ----------------------------------------------------------------------------

def _cycles(c, mul_cost):
    return (c.get("mul", 0) * mul_cost + c.get("add", 0) + c.get("shift", 0)
            + c.get("mem", 0) * 2 + c.get("branch", 0) * 2)


def _mhz(s, f, mul_cost, frame_hz=FRAME_RATE):
    return (_cycles(s, mul_cost) * FS + _cycles(f, mul_cost) * frame_hz) / 1e6


def redteam_cost_rows(cr_stats_path=os.path.join(RESULTS,
                                                 "cr_update_stats.json")):
    rows = []

    def emit(engine, L, s, f, ram, flash, note="", frame_hz=FRAME_RATE):
        rows.append({
            "engine": engine, "L": L,
            "mul/smp": round(s.get("mul", 0), 1),
            "add/smp": round(s.get("add", 0), 1),
            "shift/smp": round(s.get("shift", 0), 1),
            "mem/smp": round(s.get("mem", 0), 1),
            "MHz@mul": round(_mhz(s, f, 1, frame_hz), 2),
            "MHz@nomul": round(_mhz(s, f, MUL_SOFT, frame_hz), 2),
            "RAM_B": ram, "flash_B": flash, "note": note,
        })

    # --- meander, band-limited mipmap, nearest replay (mul returns!) ---
    import math
    for L in (20, 40, 80):
        terms = int(0.5 * L * math.log(max(L, 2))) + L
        solve = {"add": 3 * terms, "shift": 2 * terms, "mem": terms}
        s = {"mul": L, "add": 2 * L, "shift": 2 * L, "mem": 2 * L}
        emit("meander-mip-nn", L, s, solve, 6 * L + 16, 2176 + 700,
             "mul is back: B*table load; solve setup kept")
        s = {"mul": 2 * L, "add": 4 * L, "shift": 2 * L, "mem": 3 * L}
        emit("meander-mip-lin", L, s, solve, 6 * L + 16, 2176 + 800,
             "linear interp adds 2nd mul per basis")
    # --- meander polyBLEP: corrections cost ~ f0*L^2 ---
    for L, f0 in ((20, 200.0), (40, 100.0), (80, 50.0)):
        ev = 4.0 * f0 * L * (L + 1) / 2 / FS   # correction samples per sample
        s = {"mul": ev, "add": 2 * L + 2 * ev, "shift": 0,
             "mem": L, "branch": 2 * L}
        terms = int(0.5 * L * math.log(max(L, 2))) + L
        f = {"add": 3 * terms + 20 * L, "shift": 2 * terms, "mem": terms}
        emit("meander-blep", L, s, f, 8 * L + 16, 900,
             f"f0={f0:.0f}: {ev:.0f} blep-mults/smp; recips need L soft-divs/frame")

    # --- cycle-replay red-team: fixed table, measured deadband updates ---
    upd_frac = 0.91
    if os.path.exists(cr_stats_path):
        upd_frac = json.load(open(cr_stats_path))["eps0.5"]["update_fraction"]
    for L in (20, 40, 80):
        Nt = 64 if L <= 16 else 128 if L <= 32 else 256 if L <= 64 else 512
        upd = upd_frac * L
        s = {"mul": 1, "add": 3, "shift": 1, "mem": 2}
        f = {"mul": upd * Nt, "add": 2 * upd * Nt, "shift": upd * Nt,
             "mem": upd * Nt}
        emit("cr-rt-lin", L, s, f, 2 * Nt + 4 * (Nt // 2 + 1) + 16, 1300,
             f"Nt={Nt}, measured {100*upd_frac:.0f}% harmonics update/frame")
        f2 = {k: v / 2 for k, v in f.items()}
        emit("cr-rt-lin-sym", L, s, f2, 2 * Nt + 4 * (Nt // 2 + 1) + 16, 1400,
             "half-table odd/even split: setup /2, +2 ops/smp replay")

    # --- G1 lattice CSD 3t ---
    s = {"add": 84, "shift": 61, "mem": 25, "branch": 2}
    f = {"add": (LSP2LPC_CYC + A2K_CYC + RECSD_CYC * 2) // 1}
    emit("kl-lattice-csd3", "any", s, f, 75, 1000,
         "setup=LSP->A->k: 10 soft-divs/subframe dominate", frame_hz=SUBFRAME_HZ)
    # --- G2 SVF CSD 3t ---
    s = {"add": 69, "shift": 45, "mem": 5, "branch": 2}
    f = {"add": RECSD_CYC + LSP2LPC_CYC + 600}
    emit("svf-csd3", "any", s, f, 61, 950,
         "f,q from b1,b2 need sqrt per section (LUT) + same pole-pair problem",
         frame_hz=SUBFRAME_HZ)
    # --- G8 LSP-allpass CSD 3t ---
    s = {"add": 70, "shift": 30, "mem": 35, "branch": 2}
    f = {"add": RECSD_CYC}
    emit("lsp-allpass-csd3", "any", s, f, 90, 1000,
         "cos(LSP) straight from bitstream: NO pole-pair conversion at all",
         frame_hz=SUBFRAME_HZ)
    # --- G3 parallel SOS CSD 3t ---
    s = {"add": 89, "shift": 61, "mem": 25, "branch": 2}
    f = {"add": RECSD_CYC * 2 + LSP2LPC_CYC + 800}
    emit("parallel-sos-csd3", "any", s, f, 110, 1100,
         "residues need per-frame solve (est.); +LFSR noise ~3 ops/smp",
         frame_hz=SUBFRAME_HZ)
    # --- round-1 winner with the honest overheads, both variants ---
    jit = count_asm.count_file(os.path.join(ASM, "sos_csd_jit.s"))
    itp = count_asm.count_file(os.path.join(ASM, "sos_csd_interp.s"))
    for tag, cyc, ovh, ram in (("jit", jit["cycles_per_iter"],
                                RECSD_CYC + JIT_EMIT_CYC + LSP2LPC_CYC, 61 + 300),
                               ("interp", itp["cycles_per_iter"],
                                RECSD_CYC + LSP2LPC_CYC, 61 + 30)):
        mhz = (cyc * FS + ovh * SUBFRAME_HZ + FIRE_MUL_CYC * 150) / 1e6
        rows.append({"engine": f"sos-csd-{tag}-honest", "L": "any",
                     "mul/smp": 0, "add/smp": "-", "shift/smp": "-",
                     "mem/smp": "-", "MHz@mul": "-",
                     "MHz@nomul": round(mhz, 2), "RAM_B": ram, "flash_B": 900,
                     "note": f"{cyc} cyc/smp from static asm + "
                             f"{ovh} cyc/subframe conversions"})
    return rows


def main():
    cc = asm_crosscheck()
    with open(os.path.join(RESULTS, "cost_crosscheck.json"), "w") as fh:
        json.dump(cc, fh, indent=1)
    print(json.dumps(cc, indent=1))
    rows = redteam_cost_rows()
    with open(os.path.join(RESULTS, "cost_rt.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print(f"{r['engine']:22s} L={r['L']!s:4s} MHz@mul {r['MHz@mul']!s:6s} "
              f"MHz@nomul {r['MHz@nomul']!s:7s} RAM {r['RAM_B']} "
              f"| {r['note']}")


if __name__ == "__main__":
    main()
