#!/usr/bin/env python3
"""dump_params.py — parse c2sim --dump text files into a per-utterance .npz.

Dump format (codec2 @ 310777b, src/dump.c; verified by inspection AND by
parsing real dumps — see README.md "Dump format" for the full notes):

  <prefix>_model.txt   one line per 10 ms analysis frame:
                       Wo L A[1] ... A[160] voiced        (163 columns)
                       Wo    float, rad/sample (pitch = 2*pi/Wo samples @ 8 kHz)
                       L     int, number of harmonics (= floor(pi/Wo))
                       A[l]  float, LINEAR harmonic magnitudes, zero-padded
                             above L up to MAX_AMP=160
                       voiced int 0/1 — CAUTION: dump_model() runs BEFORE
                             est_voicing_mbe() in the c2sim loop, so this
                             column is the PREVIOUS frame's decision
                             (verified: model[i+1].voiced == qmodel[i].voiced
                             on all frames of hts1a).
  <prefix>_qmodel.txt  same 163-column layout, dumped in the decode path
                       (requires --lpc): Wo/L identical to _model.txt when
                       no quantisation/decimation flags are given, A[] are the
                       LPC-recovered amplitudes after aks_to_M2 + postfilter,
                       voiced is the CORRECT per-frame decision.
  <prefix>_snr.txt     one float per frame (requires --phase0): MBE voicing
                       SNR in dB from est_voicing_mbe. Threshold V_THRESH=6 dB
                       (plus eratio post-processing, so snr>6 alone does NOT
                       reproduce the voiced flag — 81/300 frames differ on
                       hts1a).
  <prefix>_lsp.txt     10 floats per frame (requires --lpc 10): unquantised
                       LSPs in rad/sample.
  <prefix>_ak.txt      order+1 floats per frame: LPC coeffs a[0]=1, a[1..10].
  <prefix>_E.txt       one float per frame: LPC residual energy, dB
                       (10*log10(e)).
  <prefix>_sn.txt, _sq.txt   TWO lines per frame (m_pitch split in half).
  <prefix>_sw.txt      256 floats per frame: 10*log10 |S(w)|^2 of the 512-pt
                       analysis FFT (may contain -inf on silence).

.npz keys written (F = number of 10 ms frames):
  Wo (F,), L (F,) int, A (F,160) linear amps of the MEASURED model,
  voiced (F,) int — corrected per-frame decision (from qmodel when present,
                    else _model.txt shifted by one; last frame falls back to
                    the stale value),
  voiced_raw (F,) int — the stale column exactly as dumped,
  snr_mbe (F,) — voicing SNR dB (NaN-filled if _snr.txt absent),
  A_lpc (F,160) — LPC-recovered amps from _qmodel.txt (if present),
  lsp (F,10), ak (F,11), E_dB (F,) — if present.

Usage: dump_params.py <prefix> <out.npz>
"""
import os
import sys

import numpy as np

MAX_AMP = 160


def _load(path, **kw):
    if not os.path.exists(path):
        return None
    a = np.loadtxt(path, **kw)
    return np.atleast_2d(a) if a.ndim == 1 and a.size > 1 else a


def parse_model_file(path):
    """Parse a _model.txt/_qmodel.txt file -> (Wo, L, A, voiced)."""
    m = np.loadtxt(path)
    m = np.atleast_2d(m)
    if m.shape[1] != 3 + MAX_AMP:
        raise ValueError(
            f"{path}: expected {3 + MAX_AMP} columns (Wo L A[160] voiced), "
            f"got {m.shape[1]}")
    Wo = m[:, 0]
    L = m[:, 1].astype(np.int32)
    A = m[:, 2:2 + MAX_AMP]
    voiced = m[:, -1].astype(np.int32)
    if not np.all((Wo > 0) & (Wo < np.pi)):
        raise ValueError(f"{path}: Wo out of range (0, pi)")
    if not np.all((L >= 1) & (L <= MAX_AMP)):
        raise ValueError(f"{path}: L out of range [1, {MAX_AMP}]")
    return Wo, L, A, voiced


def parse_dump(prefix):
    """Parse all recognised dump files for <prefix> into a dict of arrays."""
    model_path = prefix + "_model.txt"
    if not os.path.exists(model_path):
        raise FileNotFoundError(
            f"{model_path} not found — run c2sim <raw> --dump {prefix} "
            "(build must have -DDUMP)")
    Wo, L, A, v_raw = parse_model_file(model_path)
    F = len(Wo)
    out = {"Wo": Wo, "L": L, "A": A, "voiced_raw": v_raw}

    qpath = prefix + "_qmodel.txt"
    if os.path.exists(qpath):
        qWo, qL, qA, qv = parse_model_file(qpath)
        if len(qWo) != F:
            raise ValueError(f"{qpath}: frame count {len(qWo)} != {F}")
        out["A_lpc"] = qA
        # qmodel is dumped after est_voicing_mbe: authoritative voicing.
        out["voiced"] = qv
    else:
        # _model.txt voiced is stale by one frame: shift left, keep last as-is.
        v = np.concatenate([v_raw[1:], v_raw[-1:]])
        out["voiced"] = v

    snr = _load(prefix + "_snr.txt")
    if snr is not None:
        out["snr_mbe"] = np.asarray(snr).reshape(-1)[:F]
    else:
        out["snr_mbe"] = np.full(F, np.nan)

    lsp = _load(prefix + "_lsp.txt")
    if lsp is not None:
        out["lsp"] = np.atleast_2d(lsp)[:F]
    ak = _load(prefix + "_ak.txt")
    if ak is not None:
        out["ak"] = np.atleast_2d(ak)[:F]
    E = _load(prefix + "_E.txt")
    if E is not None:
        out["E_dB"] = np.asarray(E).reshape(-1)[:F]
    return out


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    prefix, out_npz = sys.argv[1], sys.argv[2]
    d = parse_dump(prefix)
    np.savez_compressed(out_npz, **d)
    F = len(d["Wo"])
    have = ", ".join(sorted(d.keys()))
    print(f"{out_npz}: {F} frames ({F * 10} ms), keys: {have}")
    vr = d["voiced"].mean()
    f0 = 8000.0 * d["Wo"] / (2 * np.pi)
    print(f"  voiced {100 * vr:.1f}%  F0 median {np.median(f0):.1f} Hz  "
          f"L range {d['L'].min()}..{d['L'].max()}")


if __name__ == "__main__":
    main()
