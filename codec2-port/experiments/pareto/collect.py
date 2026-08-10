#!/usr/bin/env python3
"""collect.py — assemble every measured configuration from all experiments into
ONE multi-dimensional tradeoff dataset: results/pareto.csv.

One row per (engine/config x rung-set where applicable). Numbers are READ from
the committed result files of the source experiments — nothing is re-measured
and nothing is fabricated: axes that were not measured for a config stay EMPTY.

Sources (all under codec2-port/experiments/):
  synth-bakeoff/results/{cost_model.csv, steady_aggregate.json, real_hts1a.csv}
  synth-redteam/results/{cost_rt.csv, cost_crosscheck.json,
                         steady_rt_aggregate.json, real_hts1a_rt.csv,
                         q15_idle.csv}
  tube-ladder/results/{metrics.csv, cost_ladder.csv, warpq.json}
  metrics-adequacy/results/{classic.csv, neural.csv, warpq.json}
  error-injector/results/budgets.yaml   (constraints; cited in REPORT, not rows)
  coverage/results/{neural_ladder.csv, knees_metrics.csv,
                    real_engines_3utt.csv, ladder_ram_flash.csv,
                    cycles_p2.json}     (round-4 gap closure)

Column semantics / jurisdictions (per each experiment's REPORT.md):
  * env/spur/nmr        — synthetic steady grid vs sum-of-sinusoids reference
                          (engine jurisdiction; bake-off + red-team stand).
  * lsd_engine_db       — real speech hts1a, engine vs sinusoid reference;
                          common floor ~3.5 dB — only deltas are informative.
  * lsd_sys_db/estoi/…  — full-decoder rungs vs codec2 phase0 reference,
                          q1300 condition, 3 utterances (tube-ladder stand);
                          floor ~7.6 dB (uq-ref vs q1300-ref).
  * warpq_*             — WARP-Q (lower better). Per metrics-adequacy, WARP-Q
                          (and NMR) have NO jurisdiction over variants with
                          intentional shaped noise: warpq_valid=no there; raw
                          values are still recorded for transparency but MUST
                          NOT drive verdicts.
  * nisqa_mos/dns_ovrl  — no-reference neural judges, mean over 3 utterances
                          (metrics-adequacy); only per-corpus deltas/ranks are
                          meaningful, not absolutes.
  * mhz_p1              — MHz-equivalent on P1 (1-cycle-mul core, 24-48 MHz
                          class: PY32F003 / CH32V006) and on P3 (hw-mul RISC-V).
  * mhz_p2              — MHz-equivalent on P2 (RV32EC, no mul, 48 MHz,
                          CH32V003). Red-team asm-audited numbers are used
                          where they exist (cost_source=asm-audit).
  * stability           — by-construction / by-testing / by-luck / unstable
                          (red-team "dishes" framing; by-testing = stability
                          region checkable+fixable at runtime, e.g. SOS
                          triangle + gamma-retry).
  * tier                — simplification-map tier framing: 0 = etalon
                          (osc-bank / reference), 1 = ladder knee
                          ("diamond for copper"), 2 = bare tube / floor.
"""
import csv
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def find_exp_root() -> Path:
    cands = []
    if os.environ.get("CODEC2_EXPERIMENTS"):
        cands.append(Path(os.environ["CODEC2_EXPERIMENTS"]))
    cands.append(HERE.parent)                                # sibling experiments
    cands.append(Path("/home/user/rv003usb/codec2-port/experiments"))
    for c in cands:
        if (c / "synth-bakeoff/results/cost_model.csv").exists():
            return c
    sys.exit("cannot find experiments root (set CODEC2_EXPERIMENTS)")


EXP = find_exp_root()

COLUMNS = [
    "config", "family", "tier", "L", "stability",
    # quality — engine jurisdiction (synthetic grid + real hts1a vs sinusoid ref)
    "env_mean_db", "env_max_db", "spur_worst_db", "nmr_worst_db",
    "lsd_engine_db", "lsd_engine_3utt_db", "click_ratio",
    # quality — system jurisdiction (q1300 decode vs codec2 phase0 ref, 3 utts)
    "lsd_sys_db", "estoi_orig", "crest_delta_db",
    "warpq_ref", "warpq_orig", "warpq_valid",
    "nisqa_mos", "dns_ovrl",
    # cost
    "mhz_p1", "mhz_p2", "cost_source", "ram_b", "flash_b",
    "verdict", "latency_note", "notes",
]


def read_csv(p):
    with open(p, newline="") as f:
        return list(csv.DictReader(f))


def read_json(p):
    with open(p) as f:
        return json.load(f)


# ---------------------------------------------------------------- source data
bake_cost = read_csv(EXP / "synth-bakeoff/results/cost_model.csv")
bake_steady = read_json(EXP / "synth-bakeoff/results/steady_aggregate.json")
rt_cost = read_csv(EXP / "synth-redteam/results/cost_rt.csv")
rt_steady = read_json(EXP / "synth-redteam/results/steady_rt_aggregate.json")
rt_real = {r["engine"]: r for r in read_csv(EXP / "synth-redteam/results/real_hts1a_rt.csv")}
bake_real = {r["engine"]: r for r in read_csv(EXP / "synth-bakeoff/results/real_hts1a.csv")}
crosscheck = read_json(EXP / "synth-redteam/results/cost_crosscheck.json")

tube_metrics = read_csv(EXP / "tube-ladder/results/metrics.csv")
tube_cost = {r["rung"]: r for r in read_csv(EXP / "tube-ladder/results/cost_ladder.csv")}
tube_warpq = read_json(EXP / "tube-ladder/results/warpq.json")

adeq_classic = read_csv(EXP / "metrics-adequacy/results/classic.csv")
adeq_neural = read_csv(EXP / "metrics-adequacy/results/neural.csv")
adeq_warpq = read_json(EXP / "metrics-adequacy/results/warpq.json")

# round-4 coverage results (gap closure; optional so pre-round-4 trees replay)
COV = EXP / "coverage/results"
cov_neural = read_csv(COV / "neural_ladder.csv") if (COV / "neural_ladder.csv").exists() else []
cov_knees = read_csv(COV / "knees_metrics.csv") if (COV / "knees_metrics.csv").exists() else []
cov_real3 = read_csv(COV / "real_engines_3utt.csv") if (COV / "real_engines_3utt.csv").exists() else []
cov_sizes = read_csv(COV / "ladder_ram_flash.csv") if (COV / "ladder_ram_flash.csv").exists() else []
cov_cycles = read_json(COV / "cycles_p2.json") if (COV / "cycles_p2.json").exists() else {}

cov_real_mean = {r["engine"]: r for r in cov_real3 if r["utt"] == "MEAN-3UTT"}

UTTS = ["hts1a", "hts2a", "ve9qrp_10s"]


def cov_neural_avg(variant):
    """mean NISQA-MOS / DNSMOS-OVRL over 3 utts from the round-4 direct
    measurement of the ladder wavs (coverage/run_neural_ladder.py)."""
    rs = [r for r in cov_neural if r["variant"] == variant]
    if not rs:
        return None, None
    return (mean([float(r["nisqa_mos"]) for r in rs]),
            mean([float(r["dns_ovrl"]) for r in rs]))


def cov_knee_agg(variant):
    rs = [r for r in cov_knees if r["variant"] == variant]
    if not rs:
        return None
    return {
        "lsd_sys_db": mean([float(r["lsd_mean"]) for r in rs]),
        "estoi_orig": mean([float(r["estoi_orig"]) for r in rs]),
        "crest_delta_db": mean([float(r["crest_delta_median"]) for r in rs]),
    }


def _cov_size(prefix):
    for r in cov_sizes:
        if r["stage"].startswith(prefix):
            return (int(r["state_ram_b"]),
                    int(r["table_flash_b"]) + int(r["code_flash_b"]))
    return (0, 0)


# per-stage RAM/flash increments (state; tables+code), measured round-4
# census of proto/decoder (L1 = documented estimate, no C twin)
SIZE_INC = {"L1w": _cov_size("+L1"), "L2": _cov_size("+L2"),
            "L3": (0, 0), "L4": _cov_size("+L4")}


def steady(name):
    """merged steady-grid aggregate: red-team stand re-measured round-1 engines
    bit-comparably; prefer red-team where present."""
    d = rt_steady.get(name) or bake_steady.get(name)
    return d or {}


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def fmt(x, nd=3):
    if x is None or x == "":
        return ""
    if isinstance(x, float):
        return f"{x:.{nd}f}".rstrip("0").rstrip(".")
    return str(x)


rows = []


def add(config, family, tier, L, stability, mhz_p1, mhz_p2, cost_source,
        ram_b, flash_b, latency, notes, q=None, real=None, sysq=None):
    r = dict.fromkeys(COLUMNS, "")
    r.update(config=config, family=family, tier=tier, L=L, stability=stability,
             mhz_p1=fmt(mhz_p1), mhz_p2=fmt(mhz_p2), cost_source=cost_source,
             ram_b=fmt(ram_b, 0), flash_b=fmt(flash_b, 0),
             latency_note=latency, notes=notes)
    if q:  # steady-grid aggregate dict
        r["env_mean_db"] = fmt(q.get("env_mean_db"))
        r["env_max_db"] = fmt(q.get("env_max_db"))
        r["spur_worst_db"] = fmt(q.get("spur_worst_db"))
        r["nmr_worst_db"] = fmt(q.get("nmr_worst_db"))
    if real:  # real_hts1a row
        r["lsd_engine_db"] = real["lsd_db_mean"]
        r["click_ratio"] = real["click_ratio_mean"]
    if sysq:
        r.update({k: fmt(v) if not isinstance(v, str) else v
                  for k, v in sysq.items()})
    rows.append(r)
    return r


# ------------------------------------------------------------------- engines
def cost_row(table, engine, L):
    for r in table:
        if r["engine"] == engine and str(r["L"]) == str(L):
            return r
    return None


STREAM = "streaming, no OLA; ~frame latency only"

# osc-bank: tier-0 etalon; P2 = asm-audited soft-mul where audited
for L, p2, src in ((20, crosscheck["osc-bank-L20"]["asm_mhz"], "asm-audit"),
                   (40, None, "model"),
                   (80, crosscheck["osc-bank-L80"]["asm_mhz"], "asm-audit")):
    c = cost_row(bake_cost, "osc-bank", L)
    add(f"osc-bank L={L}", "engine", 0, L, "by-construction (no feedback)",
        float(c["MHz@mul"]), p2 if p2 else float(c["MHz@nomul"]),
        src if p2 else "model", c["RAM_B"], c["flash_B"], STREAM,
        "P3 winner; transparent to model; phase stage D3 disappears",
        q=steady("osc-bank"), real=rt_real.get("osc-bank"))

# impulse-iir float (L-independent)
c = cost_row(bake_cost, "impulse-iir", 20)
add("impulse-iir", "engine", 2, "any",
    "by-construction (poles from bitstream-ordered LSP; float)",
    float(c["MHz@mul"]), float(c["MHz@nomul"]), "model",
    c["RAM_B"], c["flash_B"], STREAM,
    "P1 winner; cost independent of L; quality ceiling is LPC-10 model, not numeric",
    q=steady("impulse-iir"), real=rt_real.get("impulse-iir"))

# impulse-iir-csd direct — killed
c = cost_row(bake_cost, "impulse-iir-csd", 20)
add("impulse-iir-csd-direct-3t", "engine", "", "any",
    "unstable (67-76% pre-fixup, 3/21 diverged)",
    float(c["MHz@mul"]), float(c["MHz@nomul"]), "model",
    c["RAM_B"], c["flash_B"], STREAM,
    "KILLED: direct-form order-10 cannot carry 2-3-term CSD",
    q=steady("impulse-iir-csd"), real=bake_real.get("impulse-iir-csd"))

# impulse-iir-csd-sos, honest asm-audited forms (red-team CHANGED-1)
Q15NOTE = ("Q15: naive int16 state = tonal limit cycles up to 265 LSB, "
           "SNR -7.5 dB @ -48 dBFS; round + >=8 guard bits (int32 state) -> "
           "SNR 46-64 dB, idle < 0.4 LSB (q15_idle.csv)")
for form, ck in (("jit", "sos-csd-jit"), ("interp", "sos-csd-interp")):
    cc = crosscheck[ck]
    cr = cost_row(rt_cost, f"sos-csd-{form}-honest", "any")
    add(f"impulse-iir-csd-sos ({form})", "engine", 2, "any",
        "by-testing (SOS triangle + gamma-retry)",
        0.78, cc["asm_mhz"], "asm-audit", cr["RAM_B"], cr["flash_B"], STREAM,
        f"P2 winner-with-prescriptions; {cc['asm_cycles_per_sample']} cyc/smp "
        f"static asm + {cc['subframe_overhead_cyc']} cyc/subframe; "
        "pole pairs NOT free from LSP (cheap conversions cost 3.6-6.2 dB SD) "
        "-> bake SOS codebook offline or use G8/lattice. " + Q15NOTE,
        q=steady("impulse-iir-csd-sos"), real=rt_real.get("impulse-iir-csd-sos"))

# wave-2 forms (red-team, honest model costs incl. setup)
W2 = {
    "kl-lattice-csd3": (2, "by-construction (|k|<1)",
                        "CONTENDER P2: best SD per CSD term (0.20 dB med); "
                        "setup = a->k 10 soft-div/subframe dominates cost"),
    "lsp-allpass-csd3": (2, "by-construction (|cos w|<1 + bitstream LSP order)",
                         "RECOMMENDED P2 (G8): zero conversion from bitstream "
                         "(cos LSP as-is), best worst-case NMR on stress; "
                         "needs LSP interleave gap >=2^-9 in codebook; "
                         "Q15 twin not yet built (structural argument only)"),
    "lsp-allpass-csd2": ("", "by-construction",
                         "KILLED: 2 terms not enough (env max 27.6, spur -1.2)"),
    "svf-csd3": ("", "by-construction (f/q clamp)",
                 "KILLED P2: no advantage over lattice/G8; sqrt per section in setup"),
    "parallel-sos-csd3": ("", "by-testing (gamma-retry)",
                          "CONTENDER only as H1-noise carrier; residue solve per frame"),
    "ks-period-iir": ("", "by-construction (float)",
                      "KILLED: = impulse-iir computed per period, no win anywhere"),
}
# round-4 asm audit replaced the model P2 numbers for G8 and the lattice
# (coverage/cycles_p2.json; engine-only jurisdiction, same as the model rows)
AUDIT_P2 = {}
if cov_cycles:
    AUDIT_P2 = {
        "lsp-allpass-csd3": cov_cycles["g8_engine_only_audited_mhz"],
        "kl-lattice-csd3": cov_cycles["lattice_engine_only_audited_mhz"],
    }
for name, (tier, stab, note) in W2.items():
    cr = cost_row(rt_cost, name if name != "ks-period-iir" else "kl-lattice-csd3", "any")
    if name == "ks-period-iir":
        cr = None  # no cost row committed for it
    p2 = float(cr["MHz@nomul"]) if cr else None
    src = "model" if cr else ""
    if name in AUDIT_P2:
        p2, src = AUDIT_P2[name], "asm-audit"
        note += ("; P2 asm-audited round-4 (coverage): model was "
                 f"{float(cr['MHz@nomul']):.2f}")
    add(name, "engine", tier, "any", stab,
        float(cr["MHz@mul"]) if cr else None, p2,
        src, cr["RAM_B"] if cr else None,
        cr["flash_B"] if cr else None, STREAM, note,
        q=steady(name), real=rt_real.get(name))

# parallel-sos-noise: classic metrics have no jurisdiction (H1)
cr = cost_row(rt_cost, "parallel-sos-csd3", "any")
r = add("parallel-sos-noise (G3+H1)", "engine", "", "any",
        "by-testing (gamma-retry)",
        float(cr["MHz@mul"]), float(cr["MHz@nomul"]), "model",
        cr["RAM_B"], cr["flash_B"], STREAM,
        "cost = parallel-sos-csd3 + LFSR ~3 ops/smp; env/spur/NMR shown for "
        "transparency but classic judges have NO jurisdiction (H1); "
        "perceptual verdict via par-noise-* rows below",
        q=steady("parallel-sos-noise"), real=rt_real.get("parallel-sos-noise"))
r["warpq_valid"] = "no"

# meanders — killed (kept for the volumetric picture)
for var in ("meander-sq", "meander-tri"):
    for L in (20, 40, 80):
        c = cost_row(bake_cost, var, L)
        add(f"{var} L={L}", "engine", "", L, "by-construction (feedforward)",
            float(c["MHz@mul"]), float(c["MHz@nomul"]), "model",
            c["RAM_B"], c["flash_B"], STREAM,
            "KILLED: aliasing of naive waves (proven by bl-exact); "
            "intelligibility floor only (tri)",
            q=steady(var), real=rt_real.get(var))

# meander rescue forms (red-team defense — failed): mip/blep have cost rows
for var in ("meander-mip-nn", "meander-mip-lin", "meander-blep"):
    qname = {"meander-mip-nn": "meander-sq-mip-table",
             "meander-mip-lin": "meander-sq-mip-lin",
             "meander-blep": "meander-sq-blep"}[var]
    for L in (20, 40, 80):
        c = cost_row(rt_cost, var, L)
        add(f"{var} L={L}", "engine", "", L, "by-construction (feedforward)",
            float(c["MHz@mul"]), float(c["MHz@nomul"]), "model",
            c["RAM_B"], c["flash_B"], STREAM,
            "defense failed: anti-aliasing brings mul back (wavetable synthesis); "
            + c["note"],
            q=steady(qname), real=rt_real.get(qname))

# bl-exact (advocate ideal, no cheap implementation -> no cost)
add("meander-bl-exact", "engine", "", "any", "by-construction (feedforward)",
    None, None, "", None, None, STREAM,
    "band-limited ideal proves the killer was aliasing (0.00 dB, spurs at "
    "window floor); no cheap implementation exists — cost axes empty",
    q=steady("meander-sq-bl-exact"), real=rt_real.get("meander-sq-bl-exact"))

# cycle-replay family
for var, note in (("cycle-replay", "CONTENDER P1 niche: top-N harmonics or high F0; setup ~P^2/2"),
                  ("cycle-replay-2x", "best cheap engine by real-speech LSD (3.88); setup 2x table"),
                  ("cycle-replay-nn", "KILLED: nearest worse than linear everywhere")):
    for L in (20, 40, 80):
        c = cost_row(bake_cost, var, L)
        add(f"{var} L={L}", "engine",
            2 if var == "cycle-replay-2x" else "", L,
            "by-construction (feedforward)",
            float(c["MHz@mul"]), float(c["MHz@nomul"]), "model",
            c["RAM_B"], c["flash_B"],
            "table rebuild burst at frame boundary (setup ~P*L MAC)",
            note, q=steady(var), real=rt_real.get(var) or bake_real.get(var))

# cr-rt fixed-pow2-table forms (red-team defense — quality yes, price no)
for var in ("cr-rt-lin", "cr-rt-lin-sym"):
    for L in (20, 40, 80):
        c = cost_row(rt_cost, var, L)
        add(f"{var} L={L}", "engine", "", L, "by-construction (feedforward)",
            float(c["MHz@mul"]), float(c["MHz@nomul"]), "model",
            c["RAM_B"], c["flash_B"],
            "table rebuild burst (deadband does NOT amortise: 91% harmonics "
            "update per frame, measured)",
            "defense half-won: env 0.15/2.15 on grid but LSD 5.12 on speech; "
            "P2 dead, P1 top-N niche only. " + c["note"],
            q=steady("cr-rt-full"), real=rt_real.get("cr-rt-full"))


# round-4: 3-utterance real-speech LSD for the engines that sit on the P2
# engine front (coverage/run_real_engines.py; hts1a column reproduced the
# committed red-team numbers exactly before extension)
_COV3_MAP = [("osc-bank", "osc-bank"),
             ("impulse-iir-csd-sos", "impulse-iir-csd-sos"),
             ("impulse-iir-csd-direct", None),
             ("impulse-iir", "impulse-iir"),
             ("lsp-allpass-csd3", "lsp-allpass-csd3"),
             ("kl-lattice-csd3", "kl-lattice-csd3")]
for r in rows:
    for prefix, eng in _COV3_MAP:
        if r["config"].startswith(prefix):
            if eng and eng in cov_real_mean:
                r["lsd_engine_3utt_db"] = cov_real_mean[eng]["lsd_db_mean"]
            break


# ------------------------------------------------------- system rungs (tube)
def tube_agg(variant, cond="q1300"):
    """aggregate metrics.csv over the 3 utterances for one rung, q1300."""
    rs = [r for r in tube_metrics if r["cond"] == cond and r["variant"] == variant]
    if not rs:
        return None
    return {
        "lsd_sys_db": mean([float(r["lsd_mean"]) for r in rs]),
        "estoi_orig": mean([float(r["estoi_orig"]) for r in rs
                            if r["estoi_orig"] != "nan"]),
        "crest_delta_db": mean([float(r["crest_delta_median"]) for r in rs]),
    }


def tube_wq(variant, cond="q1300"):
    ref, orig = [], []
    for utt in UTTS:
        d = tube_warpq.get(f"{cond}/{utt}", {}).get(variant)
        if isinstance(d, dict):
            ref.append(d["vs_ref"])
            orig.append(d["vs_orig"])
        elif isinstance(d, (int, float)):
            ref.append(d)
    return mean(ref), mean(orig)


def neural_avg(variant):
    rs = [r for r in adeq_neural if r["variant"] == variant]
    if not rs:
        return None, None
    return (mean([float(r["nisqa_mos"]) for r in rs]),
            mean([float(r["dns_ovrl"]) for r in rs]))


LADDER = [
    # rung, tier, cum stages, warpq_valid, cost keys (cumulative deltas)
    ("L0", 2, ["L0"], "yes"),
    ("L1", 2, ["L0", "L1w"], "yes"),
    ("L2-1500", 2, ["L0", "L1w", "L2"], "no"),
    ("L2-2000", 2, ["L0", "L1w", "L2"], "no"),
    ("L2-2500", 2, ["L0", "L1w", "L2"], "no"),
    ("L3", 2, ["L0", "L1w", "L2", "L3"], "no"),
    ("L4-0.50", 1, ["L0", "L1w", "L2", "L3", "L4"], "no"),
    ("L4-0.65", 1, ["L0", "L1w", "L2", "L3", "L4"], "no"),
    ("L4-0.75", 1, ["L0", "L1w", "L2", "L3", "L4"], "no"),
]
CK = {"L0": "L0", "L1w": "L1 delta (worst F0=400Hz)", "L2": "L2 delta",
      "L3": "L3 delta", "L4": "L4 delta"}


def ladder_cost(stages):
    p1 = sum(float(tube_cost[CK[s]]["MHz_P1_mul"]) for s in stages)
    p2 = sum(float(tube_cost[CK[s]]["MHz_P2_csd"]) for s in stages)
    return p1, p2


# anchor: reference decode vs original (the codec's own ceiling)
ref_sysq = tube_agg("REF-vs-ORIG")
wq_ref_orig = mean([tube_warpq[f"q1300/{u}"]["REF-vs-ORIG"] for u in UTTS])
add("REF phase0 q1300 (anchor)", "anchor", 0, "", "",
    None, None, "", None, None, "IFFT+OLA: frame + overlap latency",
    "codec2's own phase0 decode vs original: quality ceiling of the codec "
    "itself; needs FFT infra (P3-class)",
    sysq={"lsd_sys_db": ref_sysq["lsd_sys_db"],  # LSD here is ref-vs-ORIG
          "estoi_orig": ref_sysq["estoi_orig"],
          "crest_delta_db": ref_sysq["crest_delta_db"],
          "warpq_orig": wq_ref_orig, "warpq_valid": "yes"})

for rung, tier, stages, wqv in LADDER:
    p1, p2 = ladder_cost(stages)
    wq_r, wq_o = tube_wq(rung)
    sysq = tube_agg(rung)
    sysq.update(warpq_ref=wq_r, warpq_orig=wq_o, warpq_valid=wqv)
    # round-4: every rung wav judged directly (coverage/run_neural_ladder.py);
    # falls back to metrics-adequacy buzz-l0 (== bare tube L0) if absent
    nis, dns = cov_neural_avg(rung)
    if nis is None and rung == "L0":
        nis, dns = neural_avg("buzz-l0")
    if nis:
        sysq.update(nisqa_mos=nis, dns_ovrl=dns)
    # engine state/tables base (bake-off convention) + measured round-4 stage
    # increments (proto/decoder census; L3 adds no state)
    if cov_sizes:
        ram = 50 + sum(SIZE_INC.get(s, (0, 0))[0] for s in stages if s != "L0")
        flash = 800 + sum(SIZE_INC.get(s, (0, 0))[1] for s in stages if s != "L0")
    else:
        ram = 50 if rung == "L0" else None
        flash = 800 if rung == "L0" else None
    add(f"tube-{rung}", "system-rung", tier, "any",
        "by-construction (G8 cos-LSP data path; int32 state w/ guard bits per "
        "budgets.yaml)",
        p1, p2, "model (cumulative worst-F0 deltas)", ram, flash,
        "streaming; L1 adds 65-tap dispersion stamp (8 ms @ 8 kHz)"
        if "L1w" in stages else "streaming, no OLA",
        f"cumulative ladder {'+'.join(s.rstrip('w') for s in stages)}; "
        "quality = tube-ladder q1300, 3 utts vs codec2 phase0 ref"
        + ("; WARP-Q N/A for verdicts: intentional shaped noise (H1 "
           "jurisdiction), raw value kept for transparency" if wqv == "no" else ""),
        sysq=sysq)

# recommended subsets: costs are derived sums; quality was measured directly
# in round 4 (coverage/run_knees.py + run_neural_ladder.py: the EXACT
# combinations P1 = L0+L1+L2-2500+L4(0.50), P2 = L0+L2-2500+L4(0.50))
p1r = sum(float(tube_cost[CK[s]]["MHz_P1_mul"]) for s in ["L0", "L1w", "L2", "L4"])
p2r = sum(float(tube_cost[CK[s]]["MHz_P2_csd"]) for s in ["L0", "L2", "L4"])


def knee_sysq(variant):
    sysq = cov_knee_agg(variant)
    if sysq is None:
        return None
    nis, dns = cov_neural_avg(variant)
    if nis:
        sysq.update(nisqa_mos=nis, dns_ovrl=dns)
    return sysq


def knee_size(stages):
    if not cov_sizes:
        return None, None
    ram = 50 + sum(SIZE_INC.get(s, (0, 0))[0] for s in stages)
    flash = 800 + sum(SIZE_INC.get(s, (0, 0))[1] for s in stages)
    return ram, flash


ram1, flash1 = knee_size(["L1w", "L2", "L4"])
q1 = knee_sysq("P1-knee")
add("tube-rec-P1 (L0+L1+L2.5k+L4)", "system-rec", 1, "any",
    "by-construction (as tube rungs)", p1r, None,
    "model (derived sum)", ram1, flash1, "as tube-L4",
    "tube-ladder P1 recommendation; exact subset measured round-4 "
    "(coverage knees: q1300, 3 utts, classic + neural)"
    + ("" if q1 else "; quality rows missing — coverage results not found"),
    sysq=q1)
ram2, flash2 = knee_size(["L2", "L4"])
q2 = knee_sysq("P2-knee")
add("tube-rec-P2 (L0+L2.5k+L4)", "system-rec", 1, "any",
    "by-construction (as tube rungs)", None, p2r,
    "model (derived sum)", ram2, flash2, "streaming, no dispersion FIR",
    "tube-ladder P2 recommendation (no L1); exact subset measured round-4 "
    "(coverage knees); neural judges see NO consistent loss vs the P1 knee "
    "(NISQA-MOS mean 2.38 vs 2.31, sign flips per utt; DNSMOS slightly "
    "prefers P2 on all 3 utts)"
    + ("" if q2 else "; quality rows missing — coverage results not found"),
    sysq=q2)


# --------------------------------------- resynth variants (metrics-adequacy)
def classic_avg(variant):
    rs = [r for r in adeq_classic if r["variant"] == variant]
    if not rs:
        return None
    return {
        "lsd_sys_db": mean([float(r["lsd_mean"]) for r in rs]),
        "estoi_orig": mean([float(r["estoi_orig"]) for r in rs]),
        "crest_delta_db": mean([float(r["crest_delta_median"]) for r in rs]),
    }


def adeq_wq(variant):
    return mean([adeq_warpq.get(f"{u}.{variant}") for u in UTTS
                 if adeq_warpq.get(f"{u}.{variant}") is not None])


VARIANTS = [
    ("par-plain", "", "by-testing (gamma-retry)", 1.99, 1.99,
     "parallel SOS, all-impulse excitation (float form of parallel-sos-csd3); "
     "cost = parallel-sos-csd3 model row", "yes"),
    ("par-noise-2000", "", "by-testing (gamma-retry)", 1.99, 1.99,
     "G3+H1 fc=2000: CONDITIONALLY CONFIRMED comfort-noise carrier (NISQA "
     "+0.58 MOS on low-male-F0 buzz; ~0 on female; DNSMOS against on noisy "
     "rec); cost = parallel-sos-csd3 + LFSR ~3 ops/smp", "no"),
    ("par-noise-1000", "", "by-testing (gamma-retry)", 1.99, 1.99,
     "G3+H1 fc=1000: KILLED by all judges at once (ESTOI -0.07)", "no"),
    ("smooth-mix-800", 2, "by-construction (as tube rungs)", 0.757, 1.421,
     "tube rung-2 with generous noise share (fc=800); cost = L0+L2 cumulative",
     "no"),
    ("smooth-mix-1500", 2, "by-construction (as tube rungs)", 0.757, 1.421,
     "tube rung-2 with generous noise share (fc=1500); NISQA_dis 3.41 vs 2.72 "
     "of par-noise-2000 — beats G3-form at lower cost", "no"),
]
for name, tier, stab, p1, p2, note, wqv in VARIANTS:
    sysq = classic_avg(name) or {}
    nis, dns = neural_avg(name)
    sysq.update(warpq_ref=adeq_wq(name), warpq_valid=wqv,
                nisqa_mos=nis, dns_ovrl=dns)
    add(name, "variant", tier, "any", stab, p1, p2,
        "model (mapped, float golden measured)", None, None, STREAM,
        note + "; quality vs q1300 phase0 ref (metrics-adequacy stand)",
        sysq=sysq)

# ------------------------------------------------- verdicts (from REPORT.md)
# bake-off + red-team + metrics-adequacy point verdicts, carried as a column so
# fronts can refuse to seat configs killed for OFF-front-axis reasons (grid
# worst cases, spurs, instability) that a single LSD axis does not capture.
VERDICT = [
    ("osc-bank", "winner-P3"),
    ("impulse-iir-csd-direct", "killed"),
    ("impulse-iir-csd-sos", "winner-P2-with-prescriptions"),
    ("impulse-iir", "winner-P1"),
    ("kl-lattice-csd3", "contender-P2"),
    ("lsp-allpass-csd3", "recommended-P2"),
    ("lsp-allpass-csd2", "killed"),
    ("svf-", "killed"),
    ("parallel-sos-noise", "deferred->conditional (see par-noise-2000)"),
    ("parallel-sos-csd3", "contender (H1 noise carrier)"),
    ("ks-period-iir", "killed"),
    ("meander-bl-exact", "posthumous-exoneration (no cheap form)"),
    ("meander-", "killed"),
    ("cycle-replay-nn", "killed"),
    ("cycle-replay-2x", "contender (top-N / high F0 niche)"),
    ("cycle-replay", "contender (top-N / high F0 niche)"),
    ("cr-rt-", "contender-P1-niche (P2 dead)"),
    ("tube-rec-", "recommended (tier-1 knee)"),
    ("tube-L4", "tier-1 knee"),
    ("tube-", "measured rung"),
    ("REF phase0", "anchor"),
    ("par-noise-1000", "killed"),
    ("par-noise-2000", "conditionally-confirmed (ears pending)"),
    ("par-plain", "baseline for G3 A/B"),
    ("smooth-mix", "measured variant"),
]
for r in rows:
    for prefix, v in VERDICT:
        if r["config"].startswith(prefix):
            r["verdict"] = v
            break

# ---------------------------------------------------------------------- out
out = HERE / "results/pareto.csv"
out.parent.mkdir(exist_ok=True)
with open(out, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=COLUMNS)
    w.writeheader()
    w.writerows(rows)
print(f"wrote {out}: {len(rows)} configs from {EXP}")
