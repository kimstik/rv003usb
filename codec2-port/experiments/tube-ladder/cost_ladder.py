#!/usr/bin/env python3
"""cost_ladder.py — static MCU cost model for the excitation-ladder rungs.

Extends synth-bakeoff/bench/cost_model.py (same cycle prices, same two
cores) with the per-rung DELTAS of the ladder.  Prices: add/sub/shift/
logic=1, load/store=2, taken branch=2; mul=1 on P1 (single-cycle mul:
PY32F003 M0+, CH32V006), mul=120 on P2 soft-mul (RV32EC __mulsi3, calibrated
in the bake-off from this repo's own demo_pikoball_hid measurement).

P2 nuance: multiplies by CONSTANT coefficients (dispersion taps, fixed
crossover biquads) are priced as baked CSD (3 terms: ~2 add + 3 shift = 5
cycles) rather than soft-mul — the same trick that made D4-f viable as
SOS-CSD in the bake-off.  Data*data multiplies (none in the ladder's inner
loops) would pay full soft-mul price.

Counted per output sample at 8 kHz plus per-10ms-frame setup.  The baseline
L0 matches the bake-off's impulse-iir entry (10-mul IIR + excitation) with
the LFSR noise source added (unvoiced path; ~4 ops).  Dispersion cost is
pitch-dependent (T taps stamped once per period): reported for the WORST
case P=20 (F0=400 Hz) and the typical P=80 (F0=100 Hz).

This is a ranking model, not cycle-exact numbers (bake-off caveat applies).
"""

FS = 8000
FRAME_RATE = 100          # 10 ms subframes
MUL_SOFT = 120
CSD3 = 5                  # baked-CSD constant multiply on P2: ~2 add + 3 shift
LOAD = 2

DISP_TAPS = 65


def cyc(c, mul_cost, cmul_cost):
    """c: dict mul (data*data), cmul (constant coeff), add, shift, mem, branch."""
    return (c.get("mul", 0) * mul_cost + c.get("cmul", 0) * cmul_cost
            + c.get("add", 0) + c.get("shift", 0) + c.get("mem", 0) * LOAD
            + c.get("branch", 0) * 2)


def rung_costs(P_worst=20, P_typ=80):
    """Return [(name, per_sample_delta, per_frame_delta, note), ...] where the
    dicts are DELTAS on top of the previous rung (worst-case pitch)."""
    rungs = []

    # L0: impulse-iir baseline (bake-off) + LFSR noise source.
    # IIR order 10: 10 coeff-mults, 10 adds, state loads/store; excitation:
    # phase add + wrap branch + DC-removal add; LFSR (UV frames): shift, xor,
    # mask, test ~4 ops (worst path: UV; voiced path cheaper).
    L0_s = {"cmul": 10, "add": 14, "shift": 1, "mem": 11, "branch": 1,
            "lfsr": 4}
    L0_f = {"cmul": 140, "add": 140, "shift": 10, "mem": 40}
    rungs.append(("L0", dict(L0_s, add=L0_s["add"] + L0_s.pop("lfsr")), L0_f,
                  "impulse train / LFSR noise -> LPC-IIR(10); bake-off "
                  "impulse-iir + noise source"))

    # L1 delta: stamp T dispersion taps per pitch period (2 stamps for the
    # fractional split): 2T cmul + 2T add + 2T mem per period.
    for P, tag in ((P_worst, "worst F0=400Hz"), (P_typ, "typ F0=100Hz")):
        d_s = {"cmul": 2 * DISP_TAPS / P, "add": 2 * DISP_TAPS / P,
               "mem": 2 * DISP_TAPS / P}
        if P == P_worst:
            L1_s = d_s
        rungs.append((f"L1 delta ({tag})", d_s, {},
                      f"stamp {DISP_TAPS}-tap dispersed pulse, 2 stamps/period, "
                      f"P={P}"))

    # L2 delta: LP biquad on pulse stream + HP biquad on noise + LFSR now
    # runs on voiced frames too.  Biquad: 4 cmul (b0=b2 sym) + 4 add +
    # 4 mem; noise gain: 1 cmul.
    L2_s = {"cmul": 9, "add": 12, "mem": 8}
    rungs.append(("L2 delta", L2_s, {},
                  "2 fixed biquads (power-complementary crossover) + LFSR on "
                  "voiced + noise gain"))

    # L3 delta: per pulse: 1 LFSR draw + 1 cmul (jitter scale) + add;
    # per frame: weak-voicing test.  Amortized over P_worst.
    L3_s = {"cmul": 1.0 / P_worst, "add": 6.0 / P_worst}
    L3_f = {"add": 4, "mem": 2}
    rungs.append(("L3 delta", L3_s, L3_f,
                  "per-period jitter draw on weakly-voiced frames"))

    # L4 delta: numerator FIR(10) + denominator IIR(10) + tilt (1st order)
    # + AGC multiply; per frame: scale a_k by g1^k,g2^k (20 cmul via table),
    # tilt k1 from truncated h (~22 MAC -> priced as cmul), AGC energy
    # ratio (2 MAC/sample folded into per-sample below + 1 rsqrt ~ 30 ops).
    # AGC gain is data-dependent but changes once per frame -> on P2 it is
    # CSD-quantized per frame (+-3% gain error, inaudible) so the per-sample
    # multiply stays a cmul; the conversion cost sits in the frame setup.
    L4_s = {"cmul": 23, "add": 24, "mem": 22}
    L4_f = {"cmul": 42, "add": 60, "mem": 44, "misc_rsqrt_csd": 60}
    rungs.append(("L4 delta", L4_s,
                  dict(L4_f, add=L4_f["add"] + L4_f.pop("misc_rsqrt_csd")),
                  "A(z/g1)/A(z/g2) + tilt + per-frame AGC; coeff scaling "
                  "via g^k tables"))
    return rungs


def mhz(s, f, mul_cost, cmul_cost):
    return (cyc(s, mul_cost, cmul_cost) * FS
            + cyc(f, mul_cost, cmul_cost) * FRAME_RATE) / 1e6


def main():
    rungs = rung_costs()
    print(f"{'rung':26s} {'MHz P1(mul)':>12s} {'MHz P2(CSD)':>12s}  note")
    cum1 = cum2 = 0.0
    lines = []
    for name, s, f, note in rungs:
        m1 = mhz(s, f, 1, 1)
        m2 = mhz(s, f, MUL_SOFT, CSD3)
        if "typ" not in name:
            cum1 += m1
            cum2 += m2
        lines.append((name, m1, m2, note))
        print(f"{name:26s} {m1:12.3f} {m2:12.3f}  {note}")
    print(f"{'CUMULATIVE L0..L4 (worst)':26s} {cum1:12.3f} {cum2:12.3f}")

    import csv
    import os
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "results", "cost_ladder.csv")
    with open(out, "w", newline="") as fo:
        w = csv.writer(fo)
        w.writerow(["rung", "MHz_P1_mul", "MHz_P2_csd", "note"])
        for name, m1, m2, note in lines:
            w.writerow([name, f"{m1:.3f}", f"{m2:.3f}", note])
        w.writerow(["CUMULATIVE_L0..L4_worst", f"{cum1:.3f}", f"{cum2:.3f}",
                    "sum of worst-case deltas"])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
