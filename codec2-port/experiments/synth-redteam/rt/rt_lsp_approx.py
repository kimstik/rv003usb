"""Attack on the round-1 SOS setup-cost claim: "LSP pairs ARE conjugate pole
pairs -- natural fit, ~0 extra" (cost_model.py comment, and the P2 verdict's
setup accounting).

Reality check: LSP roots lie ON the unit circle and are displaced from the
LPC pole angles; converting A(z) -> biquad sections exactly requires finding
roots of a degree-10 polynomial (not an MCU per-subframe operation).  The
cheap practical recipes measured here:

  lsp-pair-mid : pole angle = midpoint of adjacent LSP pair,
                 radius r = exp(-(w2-w1)/2)  (classic pseudo-formant rule:
                 LSP separation ~ bandwidth)
  lsp-pair-g   : same angles, fixed radius gamma = 0.97

SD (gain-refit rms over harmonics) of the resulting cascade vs the true
1/A(z) response, float coefficients (CSD noise NOT included -- this isolates
the conversion error alone).  Exact-by-construction alternatives for
comparison: lattice (a->k recursion, ~10 soft-divs) and G8 LSP-allpass
(cos(LSP) consumed directly) have ZERO conversion error by definition.
"""

import csv
import json
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(ROOT, "bench_r1"))

from common import ENVELOPES, make_frame  # noqa: E402
from engines import lpc_from_env, lpc_harmonic_mags  # noqa: E402
from engines_rt import a_to_lsp_cos  # noqa: E402

RESULTS = os.path.join(ROOT, "results")

ENVELOPES_RT = {
    "cf": [(650, 60), (780, 60), (2400, 140)],
    "nf": [(500, 35), (1520, 40), (2600, 50)],
    "nfhi": [(300, 90), (2900, 70), (3150, 70)],
}
ENVELOPES.update(ENVELOPES_RT)

F0_GRID = [50, 80, 120, 180, 250, 330, 400]


def cascade_from_lsp(a, radius="sep", gamma=0.97):
    cp, cq = a_to_lsp_cos(a)
    w = np.sort(np.concatenate([np.arccos(np.clip(cp, -1, 1)),
                                np.arccos(np.clip(cq, -1, 1))]))
    sec = []
    for i in range(0, len(w) - 1, 2):
        w1, w2 = w[i], w[i + 1]
        th = 0.5 * (w1 + w2)
        r = np.exp(-0.5 * (w2 - w1)) if radius == "sep" else gamma
        r = min(r, 0.9995)
        sec.append((1.0, -2 * r * np.cos(th), r * r))
    A = np.array([1.0])
    for (_, b1, b2) in sec:
        A = np.convolve(A, [1.0, b1, b2])
    return A


def main():
    rows = []
    for env_name in ["aa", "iy", "uw", "cf", "nf", "nfhi"]:
        for f0 in F0_GRID:
            fr = make_frame(f0, env_name)
            Wo, Am = fr["Wo"], fr["A"]
            L = len(Am)
            a, G = lpc_from_env(Am, Wo)
            Mf = lpc_harmonic_mags(a, G, Wo, L)
            for form, kw in (("lsp-pair-mid", {"radius": "sep"}),
                             ("lsp-pair-g0.97", {"radius": "g", "gamma": 0.97})):
                Aq = cascade_from_lsp(a, **kw)
                Mq = lpc_harmonic_mags(Aq, G, Wo, L)
                d = 20 * np.log10(np.maximum(Mq, 1e-9) / np.maximum(Mf, 1e-9))
                d = d - np.mean(d)
                rows.append({"case": f"{env_name}-{f0}Hz", "form": form,
                             "sd_rms_db": round(float(np.sqrt(np.mean(d ** 2))), 2),
                             "sd_max_db": round(float(np.max(np.abs(d))), 2)})
    with open(os.path.join(RESULTS, "lsp_approx_sd.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    agg = {}
    for form in ("lsp-pair-mid", "lsp-pair-g0.97"):
        sel = [r for r in rows if r["form"] == form]
        agg[form] = {
            "sd_med": round(float(np.median([r["sd_rms_db"] for r in sel])), 2),
            "sd_worst": round(float(np.max([r["sd_rms_db"] for r in sel])), 2),
            "sdmax_worst": round(float(np.max([r["sd_max_db"] for r in sel])), 2),
        }
    with open(os.path.join(RESULTS, "lsp_approx_aggregate.json"), "w") as fh:
        json.dump(agg, fh, indent=1)
    print(json.dumps(agg, indent=1))


if __name__ == "__main__":
    main()
