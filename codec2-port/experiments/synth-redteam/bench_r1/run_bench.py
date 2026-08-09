"""SYNTH-BAKEOFF main runner.

Outputs into results/ : CSVs with all metrics, markdown table fragments,
and plots/ : PNGs.  Run via ../run_all.sh
"""

import csv
import json
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import FS, ENVELOPES, env_mag, make_frame, steady_frames, synth_reference
from engines import ENGINES
from metrics import (harmonic_amps, amp_error_db, spur_level_db, nmr_proxy_db,
                     click_metric, lsd_db, _analysis_segment)
from cost_model import cost_table
import c2sim_parse

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RESULTS = os.path.join(ROOT, "results")
PLOTS = os.path.join(ROOT, "plots")
os.makedirs(RESULTS, exist_ok=True)
os.makedirs(PLOTS, exist_ok=True)

F0_GRID = [50, 80, 120, 180, 250, 330, 400]
ENV_GRID = list(ENVELOPES.keys())
N_FRAMES = 25          # 0.5 s per steady case
SETTLE = 5

MAIN_ENGINES = ["osc-bank", "impulse-iir", "impulse-iir-csd",
                "impulse-iir-csd-sos", "meander-sq", "meander-tri",
                "cycle-replay", "cycle-replay-2x", "cycle-replay-nn"]


def _grid(n):
    cols = 2
    rows = (n + cols - 1) // cols
    return rows, cols


def run_steady():
    rows = []
    spectra_cache = {}
    for env_name in ENV_GRID:
        for f0 in F0_GRID:
            frames = steady_frames(f0, env_name, N_FRAMES)
            L = len(frames[0]["A"])
            ref = synth_reference(frames)
            ref_seg = _analysis_segment(ref, frames, SETTLE)
            env_fn = lambda ff: env_mag(ff, ENVELOPES[env_name])
            for name in MAIN_ENGINES:
                x = ENGINES[name](frames)
                seg = _analysis_segment(x, frames, SETTLE)
                meas = harmonic_amps(seg, f0, L)
                err, stats = amp_error_db(meas, frames[0]["A"])
                spur = spur_level_db(seg, f0, L)
                nmr = nmr_proxy_db(seg, ref_seg, env_fn)
                rows.append({
                    "case": f"{env_name}-{f0}Hz", "env": env_name, "f0": f0,
                    "L": L, "engine": name,
                    "amp_shape_mean_db": round(stats["shape_mean_abs_db"], 3),
                    "amp_shape_max_db": round(stats["shape_max_abs_db"], 3),
                    "amp_gain_db": round(stats["gain_db"], 3),
                    "spur_db": round(spur, 2),
                    "nmr_proxy_db": round(nmr, 2),
                })
                if f0 in (120,) and env_name == "aa":
                    spectra_cache[name] = (seg, ref_seg, L, f0)
            print(f"steady {env_name}-{f0} done", flush=True)
    with open(os.path.join(RESULTS, "steady.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    return rows, spectra_cache


def run_transitions():
    """Wo walk +-5%/frame + envelope morph aa->uw; click + spectrogram."""
    rng = np.random.default_rng(42)
    n_fr = 40
    f0 = 120.0
    rows = []
    seq = []
    envs = ["aa", "uw"]
    f0s = []
    for i in range(n_fr):
        f0 = float(np.clip(f0 * (1.0 + rng.uniform(-0.05, 0.05)), 60, 380))
        f0s.append(f0)
        # envelope morph: interpolate the two envelopes in dB per harmonic
        fr_a = make_frame(f0, "aa")
        fr_b = make_frame(f0, "uw")
        t = 0.5 - 0.5 * np.cos(2 * np.pi * i / n_fr)   # slow morph
        La = len(fr_a["A"])
        A = 10 ** ((np.log10(fr_a["A"]) * (1 - t) + np.log10(np.maximum(fr_b["A"][:La], 1e-9)) * t))
        fr = dict(fr_a)
        fr["A"] = A / A.max()
        seq.append(fr)
    sigs = {}
    ref = synth_reference(seq)
    sigs["reference"] = ref
    rows.append({"engine": "reference", **click_metric(ref, seq)})
    for name in MAIN_ENGINES:
        x = ENGINES[name](seq)
        sigs[name] = x
        cm = click_metric(x, seq)
        rows.append({"engine": name,
                     "click_ratio": round(cm["click_ratio"], 2),
                     "max_jump_over_rms": round(cm["max_jump_over_rms"], 3)})
        print(f"transition {name} done", flush=True)
    with open(os.path.join(RESULTS, "transitions.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["engine", "click_ratio", "max_jump_over_rms"])
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 3) if isinstance(v, float) else v)
                        for k, v in r.items()})
    # spectrograms
    names = ["reference"] + MAIN_ENGINES
    r, c = _grid(len(names))
    fig, axes = plt.subplots(r, c, figsize=(12, 3.2 * r), sharex=True, sharey=True)
    for ax in axes.flat[len(names):]:
        ax.axis("off")
    for ax, name in zip(axes.flat, names):
        x = sigs[name]
        ax.specgram(x, NFFT=256, Fs=FS, noverlap=192, cmap="magma",
                    vmin=-90, vmax=0)
        ax.set_title(name, fontsize=10)
        ax.set_ylim(0, 4000)
    fig.suptitle("Transition test: Wo walk ±5%/frame + envelope morph aa→uw")
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, "transitions_spectrograms.png"), dpi=110)
    plt.close(fig)
    return rows


def run_real(dump_path):
    if not os.path.exists(dump_path):
        return None
    model = c2sim_parse.parse_model_dump(dump_path)
    runs = c2sim_parse.voiced_runs(model, min_len=12)
    if not runs:
        return None
    rows = []
    # concatenate all voiced runs' metrics
    for name in MAIN_ENGINES:
        lsds, clicks = [], []
        for run in runs:
            frames = c2sim_parse.to_bench_frames(run)
            ref = synth_reference(frames)
            x = ENGINES[name](frames)
            # empirically zero offset aligns best: engines ramp parameters
            # toward the frame's values just like the OLA reference does
            lsds.append(lsd_db(x, ref, frame_n=80))
            clicks.append(click_metric(x, frames)["click_ratio"])
        rows.append({"engine": name,
                     "lsd_db_mean": round(float(np.nanmean(lsds)), 2),
                     "lsd_db_max": round(float(np.nanmax(lsds)), 2),
                     "click_ratio_mean": round(float(np.nanmean(clicks)), 2)})
        print(f"real {name} done", flush=True)
    with open(os.path.join(RESULTS, "real_hts1a.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    # spectrogram of longest run
    run = max(runs, key=len)
    frames = c2sim_parse.to_bench_frames(run)
    names = ["reference"] + MAIN_ENGINES
    r, c = _grid(len(names))
    fig, axes = plt.subplots(r, c, figsize=(12, 3.2 * r), sharex=True, sharey=True)
    for ax in axes.flat[len(names):]:
        ax.axis("off")
    for ax, name in zip(axes.flat, names):
        x = synth_reference(frames) if name == "reference" else ENGINES[name](frames)
        ax.specgram(x, NFFT=128, Fs=FS, noverlap=96, cmap="magma", vmin=-90, vmax=0)
        ax.set_title(name, fontsize=10)
        ax.set_ylim(0, 4000)
    fig.suptitle("hts1a longest voiced run (c2sim model dump)")
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, "real_hts1a_spectrograms.png"), dpi=110)
    plt.close(fig)
    return rows


def plot_spectra(spectra_cache):
    from metrics import spectrum
    order = ["reference"] + MAIN_ENGINES
    r, c = _grid(len(order))
    fig, axes = plt.subplots(r, c, figsize=(12, 2.9 * r), sharex=True, sharey=True)
    for ax in axes.flat[len(order):]:
        ax.axis("off")
    ref_seg = None
    for name, (seg, rseg, L, f0) in spectra_cache.items():
        ref_seg = rseg
    for ax, name in zip(axes.flat, order):
        if name == "reference":
            seg = ref_seg
            L, f0 = spectra_cache["osc-bank"][2], spectra_cache["osc-bank"][3]
        else:
            seg, _, L, f0 = spectra_cache[name]
        f, mag = spectrum(seg)
        db = 20 * np.log10(np.maximum(mag / mag.max(), 1e-6))
        ax.plot(f, db, lw=0.4)
        ax.set_title(name, fontsize=10)
        ax.set_ylim(-110, 3)
        ax.set_xlim(0, 4000)
        ax.grid(alpha=0.3)
    fig.suptitle("Steady case aa-120Hz: magnitude spectra (dB rel. peak)")
    fig.supxlabel("Hz")
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, "steady_spectra_aa120.png"), dpi=110)
    plt.close(fig)


def plot_summary(steady_rows):
    """Aggregate per engine: mean over cases of key metrics + per-f0 curves."""
    engines = MAIN_ENGINES
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.2))
    metrics_keys = [("amp_shape_mean_db", "envelope error (mean |dB|)"),
                    ("spur_db", "worst spur (dB rel. peak)"),
                    ("nmr_proxy_db", "inv-envelope NMR proxy (dB)")]
    colors = plt.cm.tab10(np.linspace(0, 1, len(engines)))
    for ax, (key, title) in zip(axes, metrics_keys):
        for c, name in zip(colors, engines):
            xs, ys = [], []
            for f0 in F0_GRID:
                vals = [r[key] for r in steady_rows
                        if r["engine"] == name and r["f0"] == f0]
                xs.append(f0)
                ys.append(np.mean(vals))
            ax.plot(xs, ys, "o-", color=c, label=name, lw=1.2, ms=3.5)
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("F0 Hz")
        ax.grid(alpha=0.3)
    axes[0].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(os.path.join(PLOTS, "steady_summary.png"), dpi=110)
    plt.close(fig)


def run_csd_study():
    """Added spectral distortion of CSD-quantized LPC coefficients vs float.

    Pure filter-response comparison (no synthesis): SD rms of
    20log10|H_q(kWo)| - 20log10|H(kWo)| over harmonics, per form x terms.
    """
    from engines import (lpc_from_env, lpc_harmonic_mags, csd_quantize,
                         poles_to_sos, _sos_stable)
    rows = []
    for env_name in ENV_GRID:
        for f0 in F0_GRID:
            fr = make_frame(f0, env_name)
            Wo, A = fr["Wo"], fr["A"]
            L = len(A)
            a, G = lpc_from_env(A, Wo)
            Mf = lpc_harmonic_mags(a, G, Wo, L)
            for form in ("direct", "sos"):
                for terms in (2, 3, 4):
                    unstable = False
                    if form == "direct":
                        aq = np.array([1.0] + [csd_quantize(c, terms) for c in a[1:]])
                        if not np.all(np.abs(np.roots(aq)) < 1.0):
                            unstable = True
                    else:
                        sec = poles_to_sos(a)
                        qs = [(1.0, csd_quantize(b1, terms), csd_quantize(b2, terms))
                              for (_, b1, b2) in sec]
                        if not _sos_stable(qs):
                            unstable = True
                        aq = np.array([1.0])
                        for (_, b1, b2) in qs:
                            aq = np.convolve(aq, [1.0, b1, b2])
                    Mq = lpc_harmonic_mags(aq, G, Wo, L)
                    d = 20 * np.log10(np.maximum(Mq, 1e-9) / np.maximum(Mf, 1e-9))
                    d = d - np.mean(d)   # overall gain refit is free
                    rows.append({
                        "case": f"{env_name}-{f0}Hz", "form": form, "terms": terms,
                        "sd_rms_db": round(float(np.sqrt(np.mean(d ** 2))), 2),
                        "sd_max_db": round(float(np.max(np.abs(d))), 2),
                        "unstable_before_fixup": int(unstable),
                    })
    with open(os.path.join(RESULTS, "csd_study.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    # aggregate
    agg = {}
    for form in ("direct", "sos"):
        for terms in (2, 3, 4):
            sel = [r for r in rows if r["form"] == form and r["terms"] == terms]
            agg[f"{form}-{terms}t"] = {
                "sd_rms_db_median": round(float(np.median([r["sd_rms_db"] for r in sel])), 2),
                "sd_rms_db_worst": round(float(np.max([r["sd_rms_db"] for r in sel])), 2),
                "pct_unstable": round(100.0 * np.mean([r["unstable_before_fixup"] for r in sel]), 1),
            }
    return agg


def write_cost():
    rows = cost_table()
    with open(os.path.join(RESULTS, "cost_model.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    return rows


def aggregate(steady_rows):
    agg = {}
    for name in MAIN_ENGINES:
        sel = [r for r in steady_rows if r["engine"] == name]
        div = sum(1 for r in sel if not np.isfinite(r["amp_shape_mean_db"]))
        agg[name] = {
            "amp_shape_mean_db": round(float(np.nanmean([r["amp_shape_mean_db"] for r in sel])), 2),
            "amp_shape_max_db": round(float(np.nanmax([r["amp_shape_max_db"] for r in sel])), 2),
            "spur_db_median": round(float(np.nanmedian([r["spur_db"] for r in sel])), 1),
            "spur_db_worst": round(float(np.nanmax([r["spur_db"] for r in sel])), 1),
            "nmr_db_median": round(float(np.nanmedian([r["nmr_proxy_db"] for r in sel])), 1),
            "nmr_db_worst": round(float(np.nanmax([r["nmr_proxy_db"] for r in sel])), 1),
            "diverged_cases": div,
        }
    with open(os.path.join(RESULTS, "steady_aggregate.json"), "w") as fh:
        json.dump(agg, fh, indent=1)
    return agg


def main():
    dump = sys.argv[1] if len(sys.argv) > 1 else ""
    steady_rows, spectra_cache = run_steady()
    plot_spectra(spectra_cache)
    plot_summary(steady_rows)
    agg = aggregate(steady_rows)
    csd_agg = run_csd_study()
    with open(os.path.join(RESULTS, "csd_aggregate.json"), "w") as fh:
        json.dump(csd_agg, fh, indent=1)
    trans = run_transitions()
    cost = write_cost()
    real = run_real(dump) if dump else None
    print(json.dumps({"aggregate": agg}, indent=1))
    print("csd study:", json.dumps(csd_agg, indent=1))
    print("transitions:", json.dumps(trans, default=str))
    if real:
        print("real:", json.dumps(real))
    print("cost rows:", len(cost))
    print("DONE")


if __name__ == "__main__":
    main()
