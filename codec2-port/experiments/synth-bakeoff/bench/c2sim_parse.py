"""Parse c2sim --dump model files (Wo, L, A[1..160], voiced per 10 ms frame)."""

import numpy as np


def parse_model_dump(path):
    frames = []
    with open(path) as fh:
        for line in fh:
            vals = line.split()
            if len(vals) < 3:
                continue
            Wo = float(vals[0])
            L = int(vals[1])
            A = np.array([float(v) for v in vals[2:2 + L]])
            voiced = int(vals[-1])
            frames.append({"Wo": Wo, "L": L, "A": A, "voiced": voiced})
    return frames


def voiced_runs(frames, min_len=12):
    """Contiguous voiced frame runs of at least min_len frames."""
    runs, cur = [], []
    for fr in frames:
        if fr["voiced"] and fr["L"] > 0 and fr["A"].max() > 1.0:
            cur.append(fr)
        else:
            if len(cur) >= min_len:
                runs.append(cur)
            cur = []
    if len(cur) >= min_len:
        runs.append(cur)
    return runs


def to_bench_frames(run, N=80, norm=None):
    """Convert a voiced run into bench frames (10 ms => N=80 samples).

    Phases: min-phase from the harmonic envelope is expensive to recompute
    here; use zero phases for the reference AND the engines that consume phi
    (identical treatment -> fair).  norm: scale amplitudes by this factor
    (default: 1/max over run).
    """
    if norm is None:
        norm = 1.0 / max(f["A"].max() for f in run)
    out = []
    for f in run:
        L = f["L"]
        while L > 1 and L * f["Wo"] >= np.pi - 1e-3:   # avoid Nyquist harmonic
            L -= 1
        out.append({
            "Wo": f["Wo"],
            "A": f["A"][:L] * norm,
            "phi": np.zeros(L),
            "N": N,
        })
    return out
