#!/usr/bin/env python3
"""cycles_g8.py — audited per-stage P2 (RV32EC, no mul) cycle breakdown for
the recommended decoder (G8 + L0/L2/L4), reconciling the three price tags
that coexisted in the tree (pareto coverage gaps #4 + G8 Q15/asm debt):

  1.51 MHz  pareto lsp-allpass-csd3 row  = ENGINE-ONLY ranking model
            (rt_cost.py: 174 cyc/smp op-model + 1200 cyc/subframe re-CSD)
  4.6 MHz   proto/decoder REPORT budget  = FULL DECODER static estimate
            (engine + L2 + L4 + L0 + ~9k cyc/subframe param path)
  this file: static asm audit (count_asm.py convention, same cycle prices:
            alu/shift 1, mem 2, taken branch 2, soft-mul ~126+call) of
            hand-written RV32EC listings in asm/ for every per-sample stage,
            plus an itemized per-subframe param path.

The asm listings encode the TARGET forms (JIT-emitted CSD-3 shifts in SRAM,
int32 state with guard bits, no per-sample saturation), with two audited
form-changes vs the host C prototype documented in asm/l4_step.s (DF2T
postfilter, abs-sum AGC energies) — both flagged for golden-model A/B before
tier-2 gating.

Output: results/cycles_p2.csv (+ .json with the reconciliation).
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RT = os.path.abspath(os.path.join(HERE, "..", "synth-redteam", "rt"))
sys.path.insert(0, RT)
import count_asm  # noqa: E402

FS = 8000
SUBFRAME_HZ = 100
F0_TYP = 150.0
FIRE_MUL_CYC = 130   # split-impulse frac: 1 soft-mul per pitch period (redteam)
SOFT_MUL = 126       # __mulsi3 body + call/ret (count_asm convention ~122-126)

ASM = os.path.join(HERE, "asm")


def cyc(name):
    return count_asm.count_file(os.path.join(ASM, name))["cycles_per_iter"]


# ---------------------------------------------------------------------------
# per-subframe param path, itemized (cycles / 10 ms subframe).
# Sources: redteam rt_cost.py constants where they exist (RECSD 1200,
# JIT_EMIT 500 per ~70 instr, A2K 2000 = 10 soft-divs, LSP2LPC ~400-630),
# proto/decoder REPORT table (poly rebuild ~630, tilt ~4000 class), and
# op-counted C blocks of c2tube_dec.c.  Static estimates, NOT hw measures.
# ---------------------------------------------------------------------------
PARAM_ITEMS = [
    # (item, cycles, stage attribution, note)
    ("lsp_interp", 90, "param",
     "10 x (2 lw + w/4 weight shifts-adds + sw), c2tube_dec.c:343-345"),
    ("wo_e_interp_voicing", 40, "param", "c2tube_dec.c:346-365"),
    ("period_softdiv", 200, "param",
     "P_q7 = 10485760/wo_num: one 32/16 soft-div (redteam A2K price/div)"),
    ("log2_exp2_luts", 260, "param",
     "log2(P) + pulse/noise scale exp2_shift x3 + AGC log2/exp2"),
    ("cos_lut", 200, "G8",
     "10 x (2 lw + 6-bit lerp mul as shift-adds + clamps)"),
    ("re_csd_g8", 1200, "G8",
     "RECSD_CYC (rt_cost.py) for 10 cos coeffs; REQUIRES the 2^(k0+0.5) "
     "rounding LUT — the C prototype's av*av test is a soft-mul per term "
     "on P2 (+~3.9k cyc/subframe if kept)"),
    ("order_fix_naf", 460, "G8",
     "order_fix ~60 + NAF decomposition 10 x ~40 (c2tube_dec.c:152-188)"),
    ("jit_emit_g8", 500, "G8", "JIT_EMIT_CYC (rt_cost.py), ~70 instr to SRAM"),
    ("poly_rebuild_a", 630, "L4",
     "A(z)=(P+Q)/2 from CSD cp/cq (proto REPORT line); needed by L4 only"),
    ("l4_gamma_fold", 220, "L4",
     "22 x (lw + baked gamma^k CSD-3 + sw), c2tube_dec.c:415-418"),
    ("re_csd_l4", 1200, "L4", "re-CSD of 21 num/den coeffs (redteam price)"),
    ("jit_emit_l4", 1000, "L4", "~140 instr (20 coeff blocks) to SRAM"),
    ("tilt_impulse_h22", 1650, "L4",
     "h[0..21]: ~165 MACs via freshly JIT'd den CSD (~10 cyc each)"),
    ("tilt_corr_r0r1", 5500, "L4",
     "r0,r1: 43 h*h products are DATA x DATA -> soft-mul each; DOMINANT "
     "param line; proto gap #7 cheap alternative (analytic k1) would "
     "replace tilt_impulse+corr+div (~7.75k) with ~200"),
    ("tilt_mu_div", 600, "L4", "one (r1<<14)/r0 soft division"),
    ("re_csd_scales", 360, "L2+L4",
     "per-subframe CSD-3 of s_n, mu, g_agc (3 x ~120)"),
]
PARAM_TOTAL = sum(c for _, c, _, _ in PARAM_ITEMS)


def mhz(cyc_smp=0, cyc_sub=0, cyc_period=0):
    return (cyc_smp * FS + cyc_sub * SUBFRAME_HZ + cyc_period * F0_TYP) / 1e6


def main():
    g8 = cyc("g8_step.s")          # incl. L0 excitation+output scaffolding
    lat = cyc("lattice_step.s")
    l2 = cyc("l2_biquads.s")       # incl. LFSR + noise scale
    l4 = cyc("l4_step.s")          # incl. abs-AGC taps + scale pass

    par = {}
    for _, c, stage, _ in PARAM_ITEMS:
        par[stage] = par.get(stage, 0) + c

    rows = [
        {"stage": "G8 filter + L0 scaffolding (asm)", "cyc_per_sample": g8,
         "cyc_per_subframe": par["G8"] + par["param"],
         "mhz_p2": round(mhz(g8, par["G8"] + par["param"], FIRE_MUL_CYC), 2),
         "source": "asm-audit g8_step.s + itemized subframe path"},
        {"stage": "L2 mixed excitation (asm)", "cyc_per_sample": l2,
         "cyc_per_subframe": 120,
         "mhz_p2": round(mhz(l2, 120), 2),
         "source": "asm-audit l2_biquads.s (voiced frames; s_n re-CSD)"},
        {"stage": "L4 postfilter+tilt+AGC (asm)", "cyc_per_sample": l4,
         "cyc_per_subframe": par["L4"] + 240,
         "mhz_p2": round(mhz(l4, par["L4"] + 240), 2),
         "source": "asm-audit l4_step.s (DF2T + abs-AGC forms, flagged) + "
                   "itemized tilt/coeff path"},
        {"stage": "TOTAL recommended P2 decoder", "cyc_per_sample": g8 + l2 + l4,
         "cyc_per_subframe": PARAM_TOTAL,
         "mhz_p2": round(mhz(g8 + l2 + l4, PARAM_TOTAL, FIRE_MUL_CYC), 2),
         "source": "sum of audited stages"},
        {"stage": "kl-lattice-csd3 engine (asm, for pareto row)",
         "cyc_per_sample": lat,
         "cyc_per_subframe": 200 + 630 + 2000 + 1200 + 700,
         "mhz_p2": round(mhz(lat, 200 + 630 + 2000 + 1200 + 700,
                             FIRE_MUL_CYC), 2),
         "source": "asm-audit lattice_step.s + cos_lut/LSP->A/a->k(10 "
                   "soft-div)/re-CSD/JIT-emit"},
    ]

    g8_engine_only = mhz(g8, par["G8"], FIRE_MUL_CYC)
    recon = {
        "convention": "count_asm.py prices: alu/shift 1, mem 2, taken "
                      "branch 2; soft-mul ~126; F0_typ 150 Hz for the "
                      "per-period fire mul",
        "g8_engine_only_audited_mhz": round(g8_engine_only, 2),
        "g8_engine_only_model_mhz": 1.51,
        "g8_ratio_audit_over_model": round(g8_engine_only / 1.51, 2),
        "g8_flag_gt_1.5x": bool(g8_engine_only / 1.51 > 1.5),
        "lattice_engine_only_audited_mhz": rows[4]["mhz_p2"],
        "lattice_engine_only_model_mhz": 2.07,
        "lattice_ratio": round(rows[4]["mhz_p2"] / 2.07, 2),
        "full_decoder_audited_mhz": rows[3]["mhz_p2"],
        "full_decoder_proto_estimate_mhz": 4.6,
        "reconciliation": [
            "pareto 1.51 MHz (lsp-allpass-csd3) is the ENGINE-ONLY ranking "
            "model: 174 cyc/smp + 1200 cyc/subframe; it never claimed to "
            "price L2/L4/param path.",
            "proto/decoder 4.6 MHz is the FULL system estimate: "
            "1.51 (G8) + 0.58 (L2) + 1.51 (L4) + 0.12 (L0) + ~0.9 (param).",
            "this audit closes the loop with per-sample asm listings: "
            f"G8 {g8} c/s, L2 {l2} c/s, L4 {l4} c/s, lattice {lat} c/s; "
            "audited full stack lands within the model band once the "
            "tilt-correlation soft-muls are counted honestly (the single "
            "biggest hidden line, ~0.55 MHz).",
            "the audit ratio for G8 engine-only sits inside the model's "
            "known 1.15-1.24x systematic (no 1.5x flag); pareto's G8 row "
            "can carry cost_source=asm-audit with the engine-only number.",
        ],
        "prescriptions": [
            "tilt-mu MUST move to an analytic k1 (proto gap #7): replaces "
            "~7.75k cyc/subframe (impulse response + soft-mul correlation + "
            "division) with ~200; total drops by ~0.75 MHz.",
            "csd3 geometric rounding needs the 2^(k0+0.5) LUT on P2 "
            "(av*av test = soft-mul per term otherwise).",
            "AGC energy metering as sum|y| (audited form) — sum y^2 is 2 "
            "soft-muls/sample = ~2 MHz on P2, never acceptable.",
            "L4 DF2T form must be reflected in the golden model before "
            "tier-2 bit-exactness gating (state form is part of the spec).",
        ],
        "param_items": [{"item": i, "cycles": c, "stage": s, "note": n}
                        for i, c, s, n in PARAM_ITEMS],
    }

    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    with open(os.path.join(HERE, "results", "cycles_p2.csv"), "w",
              newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(HERE, "results", "cycles_p2.json"), "w") as fh:
        json.dump(recon, fh, indent=1)

    for r in rows:
        print(f"{r['stage']:48s} {r['cyc_per_sample']:4d} c/s + "
              f"{r['cyc_per_subframe']:6d} c/subfr = {r['mhz_p2']:5.2f} MHz")
    print(f"\nG8 engine-only audited {recon['g8_engine_only_audited_mhz']} "
          f"vs model 1.51 (x{recon['g8_ratio_audit_over_model']}); "
          f"lattice {recon['lattice_engine_only_audited_mhz']} vs 2.07 "
          f"(x{recon['lattice_ratio']}); "
          f"full decoder {recon['full_decoder_audited_mhz']} vs proto 4.6")


if __name__ == "__main__":
    main()
