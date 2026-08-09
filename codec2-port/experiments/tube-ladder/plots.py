#!/usr/bin/env python3
"""plots.py — spectrograms of a voiced-rich segment for the report.

Picks the longest voiced run of hts1a (q1300 condition — the real decode) and
plots original / reference phase0 synthesis / L0 / L2-2000 / L4-0.65, plus a
knee curve (pooled LSD & crest delta vs cumulative P1 cost per rung).
"""
import json
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FS = 8000
N = 80


def load_raw(p):
    return np.fromfile(p, dtype="<i2").astype(float)


def load_wav(p):
    import wave
    with wave.open(p, "rb") as w:
        return np.frombuffer(w.readframes(w.getnframes()),
                             dtype="<i2").astype(float)


def spectrogram_panel(ax, x, title):
    from scipy.signal import spectrogram as sg
    f, t, S = sg(x, FS, window="hann", nperseg=256, noverlap=192)
    ax.pcolormesh(t, f, 10 * np.log10(np.maximum(S, 1e-2)), cmap="magma",
                  shading="auto", vmin=10, vmax=70)
    ax.set_title(title, fontsize=9)
    ax.set_ylabel("Hz", fontsize=8)
    ax.tick_params(labelsize=7)


def main():
    cond, utt = "q1300", "hts1a"
    d = os.path.join(HERE, "build", "dumps", cond, utt)
    z = np.load(os.path.join(d, f"{utt}.npz"))
    v = z["voiced"]
    # longest voiced run
    best, cur, s_best, s_cur = 0, 0, 0, 0
    for i, vi in enumerate(v):
        if vi:
            if cur == 0:
                s_cur = i
            cur += 1
            if cur > best:
                best, s_best = cur, s_cur
        else:
            cur = 0
    lo, hi = s_best * N, (s_best + best) * N
    print(f"voiced run: frames {s_best}..{s_best + best} "
          f"({(hi - lo) / FS:.2f} s)")

    wav = os.path.join(HERE, "build", "wavs")
    panels = [
        (load_raw(os.path.join(HERE, "build", "codec2", "raw",
                               f"{utt}.raw")), "original"),
        (load_raw(os.path.join(d, f"{utt}_ref.raw")),
         "reference: c2sim --rate 1300 phase0 synthesis"),
        (load_wav(os.path.join(wav, f"{cond}_{utt}_L0.wav")),
         "L0: binary excitation (impulse/noise -> LPC-IIR)"),
        (load_wav(os.path.join(wav, f"{cond}_{utt}_L2-2000.wav")),
         "L2: + dispersion + mixed excitation (fc=2 kHz)"),
        (load_wav(os.path.join(wav, f"{cond}_{utt}_L4-0.65.wav")),
         "L4: + jitter + postfilter A(z/0.65)/A(z/0.8)"),
    ]
    fig, axes = plt.subplots(len(panels), 1, figsize=(7.5, 9), sharex=True)
    for ax, (x, title) in zip(axes, panels):
        spectrogram_panel(ax, x[lo:hi], title)
    axes[-1].set_xlabel("s", fontsize=8)
    fig.suptitle(f"{utt} ({cond}), voiced-rich segment "
                 f"frames {s_best}-{s_best + best}", fontsize=10)
    fig.tight_layout()
    out = os.path.join(HERE, "plots")
    os.makedirs(out, exist_ok=True)
    fig.savefig(os.path.join(out, "spectrograms_voiced.png"), dpi=130)
    print("wrote plots/spectrograms_voiced.png")

    # ---- knee curve ------------------------------------------------------
    agg = json.load(open(os.path.join(HERE, "results", "aggregate.json")))
    cost = {}
    import csv
    with open(os.path.join(HERE, "results", "cost_ladder.csv")) as f:
        for r in csv.DictReader(f):
            cost[r["rung"]] = float(r["MHz_P1_mul"])
    trunk = ["L0", "L1", "L2-2000", "L3", "L4-0.65"]
    cum = [cost["L0"],
           cost["L0"] + cost["L1 delta (worst F0=400Hz)"]]
    cum.append(cum[-1] + cost["L2 delta"])
    cum.append(cum[-1] + cost["L3 delta"])
    cum.append(cum[-1] + cost["L4 delta"])
    fig, ax1 = plt.subplots(figsize=(6.5, 4))
    for cnd, mk in (("uq", "o-"), ("q1300", "s--")):
        lsd = [agg[cnd][r]["lsd_mean"] for r in trunk]
        ax1.plot(cum, lsd, mk, label=f"LSD mean, {cnd}")
    ax1.set_xlabel("cumulative decoder cost, MHz (P1, 1-cycle mul)")
    ax1.set_ylabel("LSD vs reference, dB")
    ax2 = ax1.twinx()
    for cnd, mk in (("uq", "o:"), ("q1300", "s:")):
        cr = [agg[cnd][r]["crest_delta_median"] for r in trunk]
        ax2.plot(cum, cr, mk, color="tab:red", alpha=0.6,
                 label=f"crest delta, {cnd}")
    ax2.set_ylabel("crest delta vs reference, dB", color="tab:red")
    for x, r in zip(cum, trunk):
        ax1.annotate(r, (x, ax1.get_ylim()[0]), fontsize=7,
                     textcoords="offset points", xytext=(0, 4))
    ax1.legend(fontsize=7, loc="upper right")
    ax2.legend(fontsize=7, loc="center right")
    ax1.set_title("Ladder trunk: quality vs cumulative cost", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(out, "knee_curve.png"), dpi=130)
    print("wrote plots/knee_curve.png")


if __name__ == "__main__":
    main()
