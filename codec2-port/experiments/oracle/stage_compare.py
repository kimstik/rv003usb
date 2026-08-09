#!/usr/bin/env python3
"""stage_compare.py — parameter-domain metrics between two codec2 param sets.

Implements the project's level-1 validation (codec2-port/README.md §4 item 1):
both param sets must be on the SAME 10 ms frame grid (same input, same framing),
so there is no alignment problem and metrics are exact.

Metrics (ref = first arg, test = second arg):
  wo_rel_rmse_pct    sqrt(mean(((Wo_t - Wo_r)/Wo_r)^2)) * 100
                     budget anchor: < 0.2 % (log-quantiser step ~1.6 %)
  voicing_flip_pct   100 * mean(v_t != v_r)          (budget: < 0.5 %)
  voicing_flip_confident_pct
                     flips restricted to frames where the ref MBE voicing SNR
                     has margin > 1 dB from the 6 dB threshold
                     (budget: exactly 0 such flips)
  amp_mean_abs_dB / amp_rms_dB / amp_max_abs_dB
                     per-harmonic log-amplitude error 20*log10(A_t/A_r) over
                     harmonics 1..min(L_r,L_t) whose ref amplitude is within
                     `--amp-window` dB (default 60) of the frame peak
                     (budget anchor: mean < 0.3 dB)
  sd_mean_dB / sd_p95_dB / sd_outlier2_pct / sd_outlier4_pct
                     envelope spectral distortion per frame: both harmonic
                     amplitude sets are interpolated (dB vs Hz) onto a fixed
                     grid (default 100..3700 Hz, 64 pts), SD_f = RMS dB
                     difference; Paliwal-Atal style gate: mean <= 1 dB,
                     < 2 % frames in 2..4 dB
  e_rms_dB           RMS difference of frame energy E_dB (if both sets have it)

Usage:
  stage_compare.py ref.npz test.npz [--json out.json]
  stage_compare.py --selftest ref.npz     (identity + noise-perturbed checks)
"""
import argparse
import json
import sys

import numpy as np

EPS_AMP = 1e-12          # linear amplitude floor before log
V_THRESH_DB = 6.0        # est_voicing_mbe threshold (defines.h V_THRESH)
CONF_MARGIN_DB = 1.0     # "confident" = |snr - 6| > 1 dB
FS = 8000.0


def _db(a):
    return 20.0 * np.log10(np.maximum(a, EPS_AMP))


def compare(ref, test, amp_window_db=60.0, grid_lo=100.0, grid_hi=3700.0,
            grid_n=64):
    """Compare two parsed param dicts (arrays as produced by dump_params).

    Returns a flat dict of metrics.
    """
    F = len(ref["Wo"])
    if len(test["Wo"]) != F:
        raise ValueError(
            f"frame grids differ: ref {F} vs test {len(test['Wo'])} frames")

    out = {"frames": F}

    # --- Wo ------------------------------------------------------------------
    rel = (test["Wo"] - ref["Wo"]) / ref["Wo"]
    out["wo_rel_rmse_pct"] = float(np.sqrt(np.mean(rel ** 2)) * 100.0)
    out["wo_rel_max_pct"] = float(np.max(np.abs(rel)) * 100.0)

    # --- voicing -------------------------------------------------------------
    vr = np.asarray(ref["voiced"], dtype=int)
    vt = np.asarray(test["voiced"], dtype=int)
    flips = vr != vt
    out["voicing_flip_pct"] = float(100.0 * flips.mean())
    out["voicing_flips"] = int(flips.sum())
    snr = np.asarray(ref.get("snr_mbe", np.full(F, np.nan)), dtype=float)
    conf = np.abs(snr - V_THRESH_DB) > CONF_MARGIN_DB
    conf &= np.isfinite(snr)
    n_conf = int(conf.sum())
    out["confident_frames"] = n_conf
    if n_conf:
        out["voicing_flip_confident_pct"] = float(
            100.0 * (flips & conf).sum() / n_conf)
        out["voicing_flips_confident"] = int((flips & conf).sum())
    else:
        out["voicing_flip_confident_pct"] = float("nan")
        out["voicing_flips_confident"] = -1

    # --- per-harmonic log amplitudes ----------------------------------------
    Ar, At = np.asarray(ref["A"]), np.asarray(test["A"])
    Lr, Lt = np.asarray(ref["L"]), np.asarray(test["L"])
    errs = []
    frame_mean_abs = np.full(F, np.nan)
    for f in range(F):
        Lmin = int(min(Lr[f], Lt[f]))
        if Lmin < 1:
            continue
        ar = Ar[f, :Lmin]
        at = At[f, :Lmin]
        adb_r = _db(ar)
        keep = adb_r > (adb_r.max() - amp_window_db)
        if not keep.any():
            continue
        e = _db(at[keep]) - adb_r[keep]
        errs.append(e)
        frame_mean_abs[f] = np.mean(np.abs(e))
    if errs:
        e_all = np.concatenate(errs)
        out["amp_harmonics_compared"] = int(e_all.size)
        out["amp_mean_abs_dB"] = float(np.mean(np.abs(e_all)))
        out["amp_rms_dB"] = float(np.sqrt(np.mean(e_all ** 2)))
        out["amp_max_abs_dB"] = float(np.max(np.abs(e_all)))
        out["amp_frame_mean_abs_p95_dB"] = float(
            np.nanpercentile(frame_mean_abs, 95))
    else:
        out["amp_harmonics_compared"] = 0

    # --- envelope spectral distortion on a fixed grid ------------------------
    grid = np.linspace(grid_lo, grid_hi, grid_n)
    sd = np.full(F, np.nan)
    for f in range(F):
        Lm_r, Lm_t = int(Lr[f]), int(Lt[f])
        if Lm_r < 2 or Lm_t < 2:
            continue
        f_r = np.arange(1, Lm_r + 1) * ref["Wo"][f] * FS / (2 * np.pi)
        f_t = np.arange(1, Lm_t + 1) * test["Wo"][f] * FS / (2 * np.pi)
        env_r = np.interp(grid, f_r, _db(Ar[f, :Lm_r]))
        env_t = np.interp(grid, f_t, _db(At[f, :Lm_t]))
        d = env_t - env_r
        sd[f] = np.sqrt(np.mean(d ** 2))
    ok = np.isfinite(sd)
    if ok.any():
        out["sd_mean_dB"] = float(np.nanmean(sd))
        out["sd_p95_dB"] = float(np.nanpercentile(sd, 95))
        out["sd_outlier2_pct"] = float(100.0 * np.mean(sd[ok] > 2.0))
        out["sd_outlier4_pct"] = float(100.0 * np.mean(sd[ok] > 4.0))

    # --- frame energy --------------------------------------------------------
    if "E_dB" in ref and "E_dB" in test and len(ref["E_dB"]) == F \
            and len(test["E_dB"]) == F:
        de = np.asarray(test["E_dB"]) - np.asarray(ref["E_dB"])
        out["e_rms_dB"] = float(np.sqrt(np.mean(de ** 2)))

    return out


def load_npz(path):
    with np.load(path) as z:
        return {k: z[k] for k in z.files}


FMT = [
    ("frames", "frames", "{:d}"),
    ("wo_rel_rmse_pct", "Wo rel RMSE %%", "{:.4f}"),
    ("wo_rel_max_pct", "Wo rel max %%", "{:.4f}"),
    ("voicing_flip_pct", "voicing flips %%", "{:.2f}"),
    ("voicing_flip_confident_pct", "flips on confident %%", "{:.2f}"),
    ("amp_mean_abs_dB", "amp mean|err| dB", "{:.3f}"),
    ("amp_rms_dB", "amp RMS dB", "{:.3f}"),
    ("amp_max_abs_dB", "amp max|err| dB", "{:.2f}"),
    ("sd_mean_dB", "SD mean dB", "{:.3f}"),
    ("sd_p95_dB", "SD p95 dB", "{:.3f}"),
    ("sd_outlier2_pct", "SD >2dB frames %%", "{:.1f}"),
    ("e_rms_dB", "E RMS dB", "{:.3f}"),
]


def print_report(m, title):
    print(f"--- stage_compare: {title}")
    for key, label, fmt in FMT:
        if key in m and m[key] == m[key]:  # skip NaN
            print(f"  {label.replace('%%', '%'):24s} "
                  f"{fmt.format(m[key])}")


def perturb(ref, rng):
    """Noise-perturbed copy: known small deviations for the self-test."""
    t = {k: np.array(v, copy=True) for k, v in ref.items()}
    F = len(t["Wo"])
    t["Wo"] = t["Wo"] * (1.0 + 0.001 * rng.standard_normal(F))  # ~0.1 % RMS
    # ~0.25 dB RMS log-amp noise on all harmonics
    t["A"] = t["A"] * 10.0 ** (0.25 * rng.standard_normal(t["A"].shape) / 20.0)
    # flip ~2 % of voicing decisions
    flip = rng.random(F) < 0.02
    t["voiced"] = np.where(flip, 1 - t["voiced"], t["voiced"])
    if "E_dB" in t:
        t["E_dB"] = t["E_dB"] + 0.1 * rng.standard_normal(F)
    return t


def selftest(path):
    ref = load_npz(path)
    ok = True

    m0 = compare(ref, ref)
    print_report(m0, f"{path} vs itself (expect zeros)")
    for k in ("wo_rel_rmse_pct", "voicing_flip_pct", "amp_mean_abs_dB",
              "sd_mean_dB"):
        if abs(m0.get(k, 0.0)) > 1e-9:
            print(f"  SELFTEST FAIL: {k} = {m0[k]} != 0")
            ok = False

    rng = np.random.default_rng(2026)
    m1 = compare(ref, perturb(ref, rng))
    print_report(m1, f"{path} vs noise-perturbed copy (expect nonzero, sane)")
    checks = [
        ("wo_rel_rmse_pct", 0.05, 0.2),    # injected 0.1 % RMS
        ("voicing_flip_pct", 0.5, 5.0),    # injected ~2 %
        ("amp_mean_abs_dB", 0.1, 0.4),     # injected 0.25 dB RMS -> ~0.2 mean
        ("sd_mean_dB", 0.05, 1.0),
    ]
    for k, lo, hi in checks:
        v = m1.get(k, float("nan"))
        if not (lo <= v <= hi):
            print(f"  SELFTEST FAIL: {k} = {v} not in [{lo}, {hi}]")
            ok = False
    print(f"selftest: {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("ref")
    ap.add_argument("test", nargs="?")
    ap.add_argument("--json", help="write metrics dict as JSON")
    ap.add_argument("--selftest", action="store_true",
                    help="run identity + perturbation self-test on ref")
    ap.add_argument("--amp-window", type=float, default=60.0)
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    if args.selftest:
        sys.exit(0 if selftest(args.ref) else 1)

    if not args.test:
        ap.error("test.npz required unless --selftest")
    m = compare(load_npz(args.ref), load_npz(args.test),
                amp_window_db=args.amp_window)
    print_report(m, args.title or f"{args.ref} vs {args.test}")
    if args.json:
        with open(args.json, "w") as f:
            json.dump(m, f, indent=2)


if __name__ == "__main__":
    main()
