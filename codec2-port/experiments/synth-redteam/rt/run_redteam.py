"""SYNTH-REDTEAM main runner.

Adversarial re-examination of the round-1 synth bake-off:
  mission 1: defend the losers (meander, cycle-replay) with best-lawyer forms
  mission 2: attack the winner (impulse-iir + SOS-CSD): stress envelopes,
             high F0, subframe re-CSD interpolation, Q15 state quantization
             (limit cycles / idle-channel noise)
  mission 3: second wave: G1 lattice, G2 SVF, G8 LSP-allpass, G3 parallel-SOS
             (+ noise mixing), G5 period-domain-IIR recirculation

Usage: python3 run_redteam.py [model_dump.txt] [--only sec1,sec2,...]
Sections: steady, dynamic, real, attack, q15, wave2sd, cost
Metrics and reference are the round-1 bench modules, imported verbatim.
"""

import csv
import json
import os
import sys
import time

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(ROOT, "bench_r1"))

import common
from common import FS, ENVELOPES, env_mag, make_frame, steady_frames, synth_reference
from engines import ENGINES as ENGINES_R1
from metrics import (harmonic_amps, amp_error_db, spur_level_db, nmr_proxy_db,
                     click_metric, lsd_db, _analysis_segment)
import c2sim_parse
import engines_rt
from engines_rt import ENGINES_RT

RESULTS = os.path.join(ROOT, "results")
PLOTS = os.path.join(ROOT, "plots")
os.makedirs(RESULTS, exist_ok=True)
os.makedirs(PLOTS, exist_ok=True)

# --- stress envelopes (mission 2): closely spaced / narrow formants --------
ENVELOPES_RT = {
    "cf": [(650, 60), (780, 60), (2400, 140)],     # close F1-F2 pair
    "nf": [(500, 35), (1520, 40), (2600, 50)],     # narrow formants
    "nfhi": [(300, 90), (2900, 70), (3150, 70)],   # close narrow high pair
}
ENVELOPES.update(ENVELOPES_RT)

F0_GRID = [50, 80, 120, 180, 250, 330, 400]
F0_STRESS = [80, 120, 250, 350, 380, 400]
N_FRAMES = 25
SETTLE = 5

# round-1 controls re-run in-situ so every number in this report shares a run
CONTROLS = ["osc-bank", "impulse-iir", "impulse-iir-csd-sos",
            "meander-sq", "meander-tri", "cycle-replay", "cycle-replay-2x"]
DEFENDERS = ["meander-sq-bl-exact", "meander-tri-bl-exact",
             "meander-sq-mip-table", "meander-sq-mip-lin", "meander-sq-blep",
             "cr-rt-full", "cr-rt-inc", "cr-rt-inc-1db", "cr-rt-inc-m2",
             "cr-rt-nn"]
WAVE2 = ["kl-lattice-csd3", "lsp-allpass-csd3", "lsp-allpass-csd2",
         "svf-csd3", "svf-csd2", "parallel-sos-csd3", "parallel-sos-noise",
         "ks-period-iir"]


def get_engine(name):
    if name in ENGINES_R1:
        return ENGINES_R1[name]
    return ENGINES_RT[name]


def steady_grid(engines, envs, f0s, tag, fname):
    rows = []
    for env_name in envs:
        for f0 in f0s:
            frames = steady_frames(f0, env_name, N_FRAMES)
            L = len(frames[0]["A"])
            ref = synth_reference(frames)
            ref_seg = _analysis_segment(ref, frames, SETTLE)
            env_fn = lambda ff: env_mag(ff, ENVELOPES[env_name])
            for name in engines:
                x = get_engine(name)(frames)
                seg = _analysis_segment(x, frames, SETTLE)
                meas = harmonic_amps(seg, f0, L)
                err, stats = amp_error_db(meas, frames[0]["A"])
                spur = spur_level_db(seg, f0, L)
                nmr = nmr_proxy_db(seg, ref_seg, env_fn)
                rows.append({
                    "set": tag, "case": f"{env_name}-{f0}Hz", "env": env_name,
                    "f0": f0, "L": L, "engine": name,
                    "amp_shape_mean_db": round(stats["shape_mean_abs_db"], 3),
                    "amp_shape_max_db": round(stats["shape_max_abs_db"], 3),
                    "spur_db": round(spur, 2),
                    "nmr_proxy_db": round(nmr, 2),
                })
            print(f"[{tag}] {env_name}-{f0} done", flush=True)
    with open(os.path.join(RESULTS, fname), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    return rows


def agg_rows(rows, engines):
    agg = {}
    for name in engines:
        sel = [r for r in rows if r["engine"] == name]
        if not sel:
            continue
        agg[name] = {
            "env_mean_db": round(float(np.nanmean([r["amp_shape_mean_db"] for r in sel])), 2),
            "env_max_db": round(float(np.nanmax([r["amp_shape_max_db"] for r in sel])), 2),
            "spur_median_db": round(float(np.nanmedian([r["spur_db"] for r in sel])), 1),
            "spur_worst_db": round(float(np.nanmax([r["spur_db"] for r in sel])), 1),
            "nmr_median_db": round(float(np.nanmedian([r["nmr_proxy_db"] for r in sel])), 1),
            "nmr_worst_db": round(float(np.nanmax([r["nmr_proxy_db"] for r in sel])), 1),
        }
    return agg


# ---------------------------------------------------------------------------
# transitions (round-1 recipe, importable engines by name)
# ---------------------------------------------------------------------------

def make_transition_seq(n_fr=40, subframe=False, seed=42):
    rng = np.random.default_rng(seed)
    f0 = 120.0
    seq = []
    for i in range(n_fr):
        f0 = float(np.clip(f0 * (1.0 + rng.uniform(-0.05, 0.05)), 60, 380))
        fr_a = make_frame(f0, "aa")
        fr_b = make_frame(f0, "uw")
        t = 0.5 - 0.5 * np.cos(2 * np.pi * i / n_fr)
        La = len(fr_a["A"])
        A = 10 ** ((np.log10(fr_a["A"]) * (1 - t)
                    + np.log10(np.maximum(fr_b["A"][:La], 1e-9)) * t))
        fr = dict(fr_a)
        fr["A"] = A / A.max()
        if subframe:
            fr1 = dict(fr, N=80)
            fr2 = dict(fr, N=80)
            seq.extend([fr1, fr2])
        else:
            seq.append(fr)
    return seq


def run_dynamic(engines, fname="transitions_rt.csv"):
    seq = make_transition_seq()
    rows = []
    ref = synth_reference(seq)
    cm = click_metric(ref, seq)
    rows.append({"engine": "reference",
                 "click_ratio": round(cm["click_ratio"], 2),
                 "max_jump_over_rms": round(cm["max_jump_over_rms"], 3)})
    for name in engines:
        x = get_engine(name)(seq)
        cm = click_metric(x, seq)
        rows.append({"engine": name,
                     "click_ratio": round(cm["click_ratio"], 2),
                     "max_jump_over_rms": round(cm["max_jump_over_rms"], 3)})
        print(f"[dyn] {name} done", flush=True)
    with open(os.path.join(RESULTS, fname), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    return rows


def run_real(dump_path, engines, fname="real_hts1a_rt.csv"):
    if not dump_path or not os.path.exists(dump_path):
        print("[real] no model dump, skipped")
        return None
    model = c2sim_parse.parse_model_dump(dump_path)
    runs = c2sim_parse.voiced_runs(model, min_len=12)
    rows = []
    for name in engines:
        lsds, clicks = [], []
        for run in runs:
            frames = c2sim_parse.to_bench_frames(run)
            ref = synth_reference(frames)
            x = get_engine(name)(frames)
            lsds.append(lsd_db(x, ref, frame_n=80))
            clicks.append(click_metric(x, frames)["click_ratio"])
        rows.append({"engine": name,
                     "lsd_db_mean": round(float(np.nanmean(lsds)), 2),
                     "lsd_db_max": round(float(np.nanmax(lsds)), 2),
                     "click_ratio_mean": round(float(np.nanmean(clicks)), 2)})
        print(f"[real] {name} done", flush=True)
    with open(os.path.join(RESULTS, fname), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    return rows


def run_cr_update_stats(dump_path):
    """Deadband update counts for cycle-replay-rt on real speech + synth."""
    out = {}
    seqs = {"transitions": make_transition_seq()}
    if dump_path and os.path.exists(dump_path):
        model = c2sim_parse.parse_model_dump(dump_path)
        runs = c2sim_parse.voiced_runs(model, min_len=12)
        for i, run in enumerate(runs):
            seqs[f"hts1a-run{i}"] = c2sim_parse.to_bench_frames(run)
    for eps in (0.25, 0.5, 1.0):
        tot_up, tot_fr, tot_L = 0, 0, 0
        for sname, frames in seqs.items():
            st = []
            engines_rt.synth_cycle_replay_rt(frames, eps_db=eps, stats=st)
            tot_up += sum(st)
            tot_fr += len(st)
            tot_L += sum(len(f["A"]) for f in frames)
        out[f"eps{eps}"] = {
            "updates_per_frame_mean": round(tot_up / tot_fr, 1),
            "L_mean": round(tot_L / tot_fr, 1),
            "update_fraction": round(tot_up / max(tot_L, 1), 3),
        }
    with open(os.path.join(RESULTS, "cr_update_stats.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print("[cr-stats]", json.dumps(out))
    return out


# ---------------------------------------------------------------------------
# mission 2: winner attack
# ---------------------------------------------------------------------------

def run_attack_subframe():
    """Coefficient update rate x re-CSD chatter: 50 Hz vs 100 Hz updates."""
    rows = []
    for tag, sub in (("50Hz", False), ("100Hz", True)):
        seq = make_transition_seq(subframe=sub)
        ref = synth_reference(seq)
        cm = click_metric(ref, seq)
        rows.append({"update": tag, "engine": "reference",
                     "click_ratio": round(cm["click_ratio"], 2),
                     "max_jump_over_rms": round(cm["max_jump_over_rms"], 3)})
        for name in ("impulse-iir", "impulse-iir-csd-sos", "kl-lattice-csd3",
                     "lsp-allpass-csd3", "svf-csd3"):
            x = get_engine(name)(seq)
            cm = click_metric(x, seq)
            rows.append({"update": tag, "engine": name,
                         "click_ratio": round(cm["click_ratio"], 2),
                         "max_jump_over_rms": round(cm["max_jump_over_rms"], 3)})
            print(f"[subframe {tag}] {name} done", flush=True)
    with open(os.path.join(RESULTS, "attack_subframe.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    return rows


def _tail_analysis(tail, fs=FS):
    """Idle-channel tail: RMS in LSB, tonality of the noise spectrum."""
    lsb = tail * 32768.0
    rms = float(np.sqrt(np.mean(lsb ** 2)))
    peak = float(np.max(np.abs(lsb)))
    if rms < 1e-6:
        return {"rms_lsb": 0.0, "peak_lsb": 0.0, "tone_db": np.nan,
                "tone_hz": np.nan, "sfm_db": np.nan}
    w = np.hanning(len(tail))
    X = np.abs(np.fft.rfft(tail * w))
    f = np.fft.rfftfreq(len(tail), 1 / fs)
    sel = f > 30.0
    Xs = X[sel]
    fsel = f[sel]
    pk = np.argmax(Xs)
    tone_db = 20 * np.log10(Xs[pk] / max(np.median(Xs), 1e-15))
    p = Xs ** 2 + 1e-30
    sfm = 10 * np.log10(np.exp(np.mean(np.log(p))) / np.mean(p))
    return {"rms_lsb": round(rms, 2), "peak_lsb": round(peak, 1),
            "tone_db": round(float(tone_db), 1),
            "tone_hz": round(float(fsel[pk]), 0),
            "sfm_db": round(float(sfm), 1)}


def run_q15():
    """Q15 state quantization: idle-channel limit cycles + low-level SNR."""
    from engines_rt import synth_sos_csd_q15
    from engines import synth_impulse_iir
    rows = []
    tail_frames = 40           # 0.8 s of zero excitation
    cases = [("aa", 120), ("iy", 120), ("nf", 120), ("nf", 80), ("uw", 80)]
    for env_name, f0 in cases:
        frames = steady_frames(f0, env_name, 15)
        # float twin with the same CSD coefficients, for SNR
        xf = synth_impulse_iir(frames, csd=True, csd_terms=3, csd_form="sos")
        for level_db in (-12.0, -48.0):
            scale = 10 ** (level_db / 20.0)
            for mode, gb in (("trunc", 0), ("round", 0), ("dither", 0),
                             ("trunc", 8), ("round", 8)):
                x = synth_sos_csd_q15(frames, mode=mode, level_scale=scale,
                                      tail_frames=tail_frames, guard_bits=gb)
                n_sig = sum(f["N"] for f in frames)
                sig = x[:n_sig]
                ref = xf * scale
                nseg = min(len(sig), len(ref))
                err = sig[:nseg] - ref[:nseg]
                snr = 10 * np.log10(np.sum(ref[:nseg] ** 2)
                                    / max(np.sum(err ** 2), 1e-30))
                tail = x[n_sig + 10 * 160:]       # skip decay, keep last part
                ta = _tail_analysis(tail)
                rows.append({"case": f"{env_name}-{f0}", "level_db": level_db,
                             "mode": f"{mode}-g{gb}" if gb else mode,
                             "snr_db": round(float(snr), 1),
                             **{f"idle_{k}": v for k, v in ta.items()}})
                print(f"[q15] {env_name}-{f0} {level_db}dB {mode}-g{gb}: "
                      f"snr {snr:.1f} idle_rms {ta['rms_lsb']} LSB "
                      f"tone {ta['tone_db']} dB @ {ta['tone_hz']} Hz", flush=True)
    with open(os.path.join(RESULTS, "q15_idle.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    return rows


# ---------------------------------------------------------------------------
# mission 3: SD study of quantization-only distortion, all filter coordinates
# ---------------------------------------------------------------------------

def run_wave2_sd():
    from engines import lpc_from_env, lpc_harmonic_mags, csd_quantize
    from engines import poles_to_sos, _sos_stable
    from engines_rt import (a_to_k, k_to_a, quantize_k_csd, sos_to_svf,
                            quantize_svf_csd, svf_equivalent_poly,
                            a_to_lsp_cos, lsp_cos_to_a, quantize_lsp_csd,
                            parallel_sections, quantize_parallel_csd,
                            parallel_response, K_CLAMP)
    rows = []
    case_sets = [("std", ["aa", "iy", "uw"], F0_GRID),
                 ("stress", list(ENVELOPES_RT.keys()), F0_STRESS)]
    for tag, envs, f0s in case_sets:
        for env_name in envs:
            for f0 in f0s:
                fr = make_frame(f0, env_name)
                Wo, A = fr["Wo"], fr["A"]
                L = len(A)
                a, G = lpc_from_env(A, Wo)
                Mf = lpc_harmonic_mags(a, G, Wo, L)

                def sd_of(a_q):
                    Mq = lpc_harmonic_mags(a_q, G, Wo, L)
                    d = 20 * np.log10(np.maximum(Mq, 1e-9)
                                      / np.maximum(Mf, 1e-9))
                    d = d - np.mean(d)
                    return (round(float(np.sqrt(np.mean(d ** 2))), 3),
                            round(float(np.max(np.abs(d))), 3))

                def emit(form, terms, a_q, unstable, note=""):
                    sd, sdmax = sd_of(a_q)
                    rows.append({"set": tag, "case": f"{env_name}-{f0}Hz",
                                 "form": form, "terms": terms,
                                 "sd_rms_db": sd, "sd_max_db": sdmax,
                                 "unstable_before_fixup": int(unstable),
                                 "note": note})

                # SOS control (round-1 winner)
                sec = poles_to_sos(a)
                for terms in (2, 3, 4):
                    qs = [(1.0, csd_quantize(b1, terms), csd_quantize(b2, terms))
                          for (_, b1, b2) in sec]
                    unstable = not _sos_stable(qs)
                    aq = np.array([1.0])
                    for (_, b1, b2) in qs:
                        aq = np.convolve(aq, [1.0, b1, b2])
                    emit("sos", terms, aq, unstable)
                # G1 lattice
                k = a_to_k(a)
                for terms in (2, 3, 4):
                    kq = quantize_k_csd(k, terms)
                    clamped = int(np.any(np.abs(kq) >= K_CLAMP - 1e-12))
                    emit("lattice", terms, k_to_a(kq), False,
                         note=f"kclamp={clamped}")
                # G2 SVF
                fqf = sos_to_svf(sec)
                for terms in (1, 2, 3):
                    fq = quantize_svf_csd(fqf, terms)
                    emit("svf", terms, svf_equivalent_poly(fq), False)
                # G8 LSP allpass
                cp, cq = a_to_lsp_cos(a)
                for terms in (2, 3):
                    cpq, cqq, fixes = quantize_lsp_csd(cp, cq, terms)
                    a_q = lsp_cos_to_a(cpq, cqq)
                    root_ok = np.all(np.abs(np.roots(a_q)) < 1.0)
                    emit("lsp-allpass", terms, a_q, not root_ok,
                         note=f"order_fixes={fixes}")
                # G3 parallel (response is a sum -> SD on summed response)
                psec = parallel_sections(a, G)
                for terms in (3, 4):
                    pq = quantize_parallel_csd(psec, terms)
                    Mq = parallel_response(pq, Wo, L)
                    d = 20 * np.log10(np.maximum(Mq, 1e-9)
                                      / np.maximum(Mf, 1e-9))
                    d = d - np.mean(d)
                    rows.append({"set": tag, "case": f"{env_name}-{f0}Hz",
                                 "form": "parallel", "terms": terms,
                                 "sd_rms_db": round(float(np.sqrt(np.mean(d ** 2))), 3),
                                 "sd_max_db": round(float(np.max(np.abs(d))), 3),
                                 "unstable_before_fixup": 0, "note": ""})
            print(f"[wave2-sd] {tag} {env_name} done", flush=True)
    with open(os.path.join(RESULTS, "wave2_sd.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    # aggregate
    agg = {}
    for tag in ("std", "stress"):
        for form in ("sos", "lattice", "svf", "lsp-allpass", "parallel"):
            for terms in (1, 2, 3, 4):
                sel = [r for r in rows if r["set"] == tag
                       and r["form"] == form and r["terms"] == terms]
                if not sel:
                    continue
                agg[f"{tag}/{form}-{terms}t"] = {
                    "sd_med": round(float(np.median([r["sd_rms_db"] for r in sel])), 2),
                    "sd_worst": round(float(np.max([r["sd_rms_db"] for r in sel])), 2),
                    "sdmax_worst": round(float(np.max([r["sd_max_db"] for r in sel])), 2),
                    "pct_unstable": round(100 * np.mean(
                        [r["unstable_before_fixup"] for r in sel]), 1),
                }
    with open(os.path.join(RESULTS, "wave2_sd_aggregate.json"), "w") as fh:
        json.dump(agg, fh, indent=1)
    print(json.dumps(agg, indent=1))
    return rows, agg


def plot_noise_divergence():
    """G3 noise-mixing: spectra with and without per-formant noise (H1)."""
    frames = steady_frames(120, "aa", N_FRAMES)
    ref = synth_reference(frames)
    from metrics import spectrum
    fig, axes = plt.subplots(1, 3, figsize=(15, 3.6), sharey=True)
    for ax, name in zip(axes, ["reference", "parallel-sos-csd3",
                               "parallel-sos-noise"]):
        x = ref if name == "reference" else get_engine(name)(frames)
        seg = _analysis_segment(x, frames, SETTLE)
        f, mag = spectrum(seg)
        db = 20 * np.log10(np.maximum(mag / mag.max(), 1e-6))
        ax.plot(f, db, lw=0.4)
        ax.set_title(name, fontsize=10)
        ax.set_ylim(-110, 3)
        ax.set_xlim(0, 4000)
        ax.grid(alpha=0.3)
    fig.suptitle("G3 per-formant noise mixing (aa-120): classic metrics punish "
                 "the deliberate noise floor >1.5 kHz")
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, "g3_noise_divergence.png"), dpi=110)
    plt.close(fig)


def main():
    args = [a for a in sys.argv[1:]]
    dump = ""
    only = None
    for a in args:
        if a.startswith("--only"):
            only = a.split("=", 1)[1].split(",")
        else:
            dump = a
    def want(s):
        return only is None or s in only

    t0 = time.time()
    if want("steady"):
        rows = steady_grid(CONTROLS + DEFENDERS + WAVE2,
                           ["aa", "iy", "uw"], F0_GRID,
                           "std", "steady_rt.csv")
        agg = agg_rows(rows, CONTROLS + DEFENDERS + WAVE2)
        with open(os.path.join(RESULTS, "steady_rt_aggregate.json"), "w") as fh:
            json.dump(agg, fh, indent=1)
        print(json.dumps(agg, indent=1))
    if want("stress"):
        srows = steady_grid(["osc-bank", "impulse-iir", "impulse-iir-csd-sos",
                             "kl-lattice-csd3", "lsp-allpass-csd3", "svf-csd3",
                             "parallel-sos-csd3"],
                            list(ENVELOPES_RT.keys()), F0_STRESS,
                            "stress", "attack_stress.csv")
        sagg = agg_rows(srows, ["osc-bank", "impulse-iir",
                                "impulse-iir-csd-sos", "kl-lattice-csd3",
                                "lsp-allpass-csd3", "svf-csd3",
                                "parallel-sos-csd3"])
        with open(os.path.join(RESULTS, "attack_stress_aggregate.json"), "w") as fh:
            json.dump(sagg, fh, indent=1)
        print(json.dumps(sagg, indent=1))
    if want("dynamic"):
        run_dynamic(CONTROLS + DEFENDERS + WAVE2)
        run_attack_subframe()
    if want("real"):
        run_real(dump, CONTROLS + DEFENDERS + WAVE2)
        run_cr_update_stats(dump)
    if want("q15"):
        run_q15()
    if want("wave2sd"):
        run_wave2_sd()
        plot_noise_divergence()
    print(f"ALL DONE in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
