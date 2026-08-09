#!/usr/bin/env python3
"""Voicing classifiers from FFT-free features; scored against MBE reference.

(a) single-threshold NACF baseline (threshold tuned on clean corpus,
    leave-one-file-out spread reported for honesty);
(b) small combined rules, MCU-trivial (no ML runtime):
    - "handrule": NACF threshold + low/high-band-ratio rescue/kill, a direct
      time-domain transplant of est_voicing_mbe's own eratio post-processing;
    - "tree2": greedy depth-2 decision stump tree over the candidate features
      (thresholds found by exhaustive quantile search, printed as C code).

Confidence definition (documented choice): the MBE SNR *is* dumpable
(prefix_snr.txt, --phase0), so a reference decision is called CONFIDENT when
  |snr - V_THRESH| > 5 dB  AND  the raw snr>V_THRESH decision was not
  overturned by est_voicing_mbe's eratio post-processing.
i.e. frames where the reference sits well away from its own decision boundary.

All thresholds are tuned on the CLEAN condition only; noisy conditions are
scored with clean-tuned thresholds (that is the deployment situation).
"""
import sys
import os
import glob
import json
import numpy as np

V_THRESH = 6.0
CONF_MARGIN = 5.0
FEATS = ['nacf_wo', 'nacf_best', 'nacf_sub', 'yin_wo', 'yin_best',
         'r1r0', 'zcr', 'lhr_db', 'energy_db']
COLS = ['frame', 'ref_v', 'snr', 'eratio', 'Wo', 'P', 'L'] + FEATS
CI = {c: i for i, c in enumerate(COLS)}
TREE_FEATS = ['nacf_wo', 'nacf_best', 'nacf_sub', 'yin_wo', 'yin_best',
              'r1r0', 'zcr', 'lhr_db']
MARGIN_BANDS = [(0, 1), (1, 2), (2, 3), (3, 5), (5, 999)]


def load_condition(dir_):
    data, files = [], []
    for f in sorted(glob.glob(os.path.join(dir_, '*.csv'))):
        arr = np.loadtxt(f, delimiter=',', skiprows=1)
        data.append(arr)
        files.append(os.path.basename(f)[:-4])
    return files, data


def confident_mask(arr):
    snr = arr[:, CI['snr']]
    ref = arr[:, CI['ref_v']].astype(int)
    raw = (snr > V_THRESH).astype(int)
    return (np.abs(snr - V_THRESH) > CONF_MARGIN) & (raw == ref)


def score(files, data, predict):
    """predict(arr) -> 0/1 vector; returns metrics dict."""
    cm = np.zeros((2, 2), dtype=int)   # cm[ref][pred]
    tot = conf_tot = conf_flip = 0
    per_file = {}
    band_flip = np.zeros(len(MARGIN_BANDS))
    band_n = np.zeros(len(MARGIN_BANDS))
    for name, arr in zip(files, data):
        ref = arr[:, CI['ref_v']].astype(int)
        pred = predict(arr).astype(int)
        flips = ref != pred
        for r, p in zip(ref, pred):
            cm[r][p] += 1
        cmask = confident_mask(arr)
        margin = np.abs(arr[:, CI['snr']] - V_THRESH)
        for bi, (lo, hi) in enumerate(MARGIN_BANDS):
            sel = (margin >= lo) & (margin < hi)
            band_n[bi] += sel.sum()
            band_flip[bi] += (flips & sel).sum()
        tot += len(ref)
        conf_tot += cmask.sum()
        conf_flip += (flips & cmask).sum()
        per_file[name] = round(100.0 * flips.mean(), 2)
    flip = 100.0 * (cm[0][1] + cm[1][0]) / tot
    return {'confusion': cm.tolist(),
            'flip_pct': round(flip, 2),
            'confident_flip_pct': round(100.0 * conf_flip / max(conf_tot, 1), 3),
            'confident_frames': int(conf_tot),
            'total_frames': int(tot),
            'flip_pct_by_snr_margin': {
                f'[{lo},{hi})': round(100.0 * band_flip[bi] / max(band_n[bi], 1), 2)
                for bi, (lo, hi) in enumerate(MARGIN_BANDS)},
            'per_file_flip_pct': per_file}


# ---------------------------------------------------------------- baseline (a)

def tune_threshold(data, feat, sense=+1):
    """Best single threshold on feature (sense=+1: value>t => voiced)."""
    x = np.concatenate([a[:, CI[feat]] for a in data]) * sense
    y = np.concatenate([a[:, CI['ref_v']] for a in data]).astype(int)
    order = np.argsort(x)
    xs, ys = x[order], y[order]
    # error(t) for t between consecutive xs: pred=1 for x>t
    n1 = ys.sum()
    cum1 = np.concatenate([[0], np.cumsum(ys)])        # voiced among first i
    i = np.arange(len(xs) + 1)
    cum0 = i - cum1
    # errors: voiced below-threshold (cum1[i]) + unvoiced above ((n0 - cum0[i]))
    err = cum1 + ((len(ys) - n1) - cum0)
    best = int(np.argmin(err))
    t = -np.inf if best == 0 else (xs[best - 1] + xs[min(best, len(xs) - 1)]) / 2
    return t * sense if sense > 0 else t * sense, err[best] / len(ys)


# -------------------------------------------------------------- hand rule (b1)

def make_handrule(t1, t_rescue, t_kill, t_kill_lowpitch):
    def predict(arr):
        v = (arr[:, CI['nacf_wo']] > t1).astype(int)
        lhr = arr[:, CI['lhr_db']]
        P = arr[:, CI['P']]
        v[(v == 0) & (lhr > t_rescue)] = 1
        v[(v == 1) & (lhr < t_kill)] = 0
        v[(v == 1) & (lhr < t_kill_lowpitch) & (P >= 133)] = 0   # Wo<=60Hz
        return v
    return predict


def tune_handrule(data):
    ref = np.concatenate([a[:, CI['ref_v']] for a in data]).astype(int)
    big = np.concatenate(data)
    best = None
    for t1 in np.arange(0.20, 0.75, 0.02):
        for tr in (8.0, 10.0, 12.0, 15.0, 99.0):        # 99 = rescue disabled
            for tk in (-14.0, -10.0, -6.0, -99.0):
                for tkl in (-4.0, -2.0, -99.0):
                    pred = make_handrule(t1, tr, tk, tkl)(big)
                    e = np.mean(pred != ref)
                    if best is None or e < best[0]:
                        best = (e, (round(t1, 2), tr, tk, tkl))
    return best[1], best[0]


# ------------------------------------------------------------- depth-2 tree (b2)

def best_stump(X, y, w=None):
    """Exhaustive quantile search; returns (feat, thr, sense, err)."""
    n = len(y)
    best = (None, 0.0, +1, 1.0)
    for fi, feat in enumerate(TREE_FEATS):
        x = X[:, fi]
        qs = np.unique(np.quantile(x, np.linspace(0.005, 0.995, 199)))
        for t in qs:
            for sense in (+1, -1):
                pred = (sense * x > sense * t).astype(int)
                e = np.mean(pred != y)
                if e < best[3]:
                    best = (feat, float(t), sense, float(e))
    return best


def fit_tree2(data, min_leaf=200):
    X = np.concatenate([a[:, [CI[f] for f in TREE_FEATS]] for a in data])
    y = np.concatenate([a[:, CI['ref_v']] for a in data]).astype(int)
    root = best_stump(X, y)
    fi = TREE_FEATS.index(root[0])
    go_r = (root[2] * X[:, fi] > root[2] * root[1])
    leaves = {}
    for side, mask in (('right', go_r), ('left', ~go_r)):
        ys = y[mask]
        maj = int(round(ys.mean()))
        if mask.sum() > min_leaf and 0.02 < ys.mean() < 0.98:
            sub = best_stump(X[mask], ys)
            leaves[side] = ('stump', sub)
        else:
            leaves[side] = ('const', maj)
    return {'root': root, 'leaves': leaves}


def tree2_predict(tree):
    def predict(arr):
        X = arr[:, [CI[f] for f in TREE_FEATS]]
        feat, thr, sense, _ = tree['root']
        fi = TREE_FEATS.index(feat)
        go_r = (sense * X[:, fi] > sense * thr)
        out = np.zeros(len(X), dtype=int)
        for side, mask in (('right', go_r), ('left', ~go_r)):
            kind, val = tree['leaves'][side]
            if kind == 'const':
                out[mask] = val
            else:
                f2, t2, s2, _ = val
                fj = TREE_FEATS.index(f2)
                out[mask] = (s2 * X[mask, fj] > s2 * t2).astype(int)
        return out
    return predict


def tree2_as_c(tree):
    feat, thr, sense, _ = tree['root']
    op = '>' if sense > 0 else '<'
    lines = [f'if ({feat} {op} {thr:.4g}) {{']
    for side in ('right', 'left'):
        kind, val = tree['leaves'][side]
        if kind == 'const':
            body = f'    v = {val};'
        else:
            f2, t2, s2, _ = val
            op2 = '>' if s2 > 0 else '<'
            body = f'    v = ({f2} {op2} {t2:.4g});'
        if side == 'right':
            lines.append(body)
            lines.append('} else {')
        else:
            lines.append(body)
            lines.append('}')
    return '\n'.join(lines)


# ----------------------------------------------------------------------- main

def main():
    feat_root, out_json = sys.argv[1], sys.argv[2]
    conds = sorted(d for d in os.listdir(feat_root)
                   if os.path.isdir(os.path.join(feat_root, d)))
    files_c, data_c = load_condition(os.path.join(feat_root, 'clean'))
    results = {'conditions': conds, 'tuning': {}}

    # (a) NACF baseline, tuned on clean
    t_nacf, err = tune_threshold(data_c, 'nacf_wo')
    results['tuning']['nacf_threshold'] = round(float(t_nacf), 4)
    print(f'== (a) NACF baseline: nacf_wo > {t_nacf:.4f} '
          f'(clean in-sample flip {100*err:.2f}%)')
    # leave-one-file-out spread
    lofo = []
    for i in range(len(files_c)):
        tr = [d for j, d in enumerate(data_c) if j != i]
        t_i, _ = tune_threshold(tr, 'nacf_wo')
        ref = data_c[i][:, CI['ref_v']].astype(int)
        pred = (data_c[i][:, CI['nacf_wo']] > t_i).astype(int)
        lofo.append(100.0 * np.mean(pred != ref))
    results['tuning']['nacf_lofo_flip_pct'] = [round(v, 2) for v in lofo]
    print(f'   LOFO per-held-out-file flip%: {[f"{v:.1f}" for v in lofo]}')

    # (b1) hand rule
    hr_params, hr_err = tune_handrule(data_c)
    results['tuning']['handrule_params'] = hr_params
    print(f'== (b1) handrule params (t_nacf, rescue_lhr>, kill_lhr<, '
          f'kill_lowpitch_lhr<): {hr_params} '
          f'(clean in-sample flip {100*hr_err:.2f}%)')

    # (b2) depth-2 tree
    tree = fit_tree2(data_c)
    results['tuning']['tree2'] = {'root': tree['root'],
                                  'leaves': {k: v if v[0] == 'const' else
                                             ['stump', list(v[1])]
                                             for k, v in tree['leaves'].items()}}
    c_code = tree2_as_c(tree)
    results['tuning']['tree2_c'] = c_code
    print('== (b2) depth-2 tree:')
    print('   ' + c_code.replace('\n', '\n   '))

    classifiers = {
        'nacf_baseline': lambda arr: (arr[:, CI['nacf_wo']] > t_nacf).astype(int),
        'handrule': make_handrule(*hr_params),
        'tree2': tree2_predict(tree),
        'ref_raw_snr_no_postproc':
            lambda arr: (arr[:, CI['snr']] > V_THRESH).astype(int),  # calibration
    }

    for cond in conds:
        files, data = load_condition(os.path.join(feat_root, cond))
        results[cond] = {}
        print(f'\n==== condition: {cond} ({sum(len(a) for a in data)} frames)')
        for cname, clf in classifiers.items():
            m = score(files, data, clf)
            results[cond][cname] = m
            cm = m['confusion']
            print(f'  {cname:26s} flip {m["flip_pct"]:5.2f}%   '
                  f'confident-flip {m["confident_flip_pct"]:5.3f}% '
                  f'({m["confident_frames"]} conf frames)   '
                  f'CM [uv->uv {cm[0][0]}, uv->v {cm[0][1]}, '
                  f'v->uv {cm[1][0]}, v->v {cm[1][1]}]')
            print(f'  {"":26s} per-file: {m["per_file_flip_pct"]}')

    # failure analysis on clean, winning rule = lowest clean flip among (b)
    win = min(('handrule', 'tree2'), key=lambda c: results['clean'][c]['flip_pct'])
    results['winner'] = win
    clf = classifiers[win]
    print(f'\n==== failure analysis (clean, winner = {win})')
    fa = {}
    for name, arr in zip(files_c, data_c):
        ref = arr[:, CI['ref_v']].astype(int)
        pred = clf(arr)
        flips = np.where(ref != pred)[0]
        trans = np.where(np.diff(ref) != 0)[0]   # transition between k,k+1
        near = [int(f) for f in flips
                if len(trans) and np.min(np.abs(trans - f)) <= 2]
        # "damaging" = flip on a confident frame NOT near a voicing transition
        # (sustained-region error where the reference was sure => audible risk)
        cmask = confident_mask(arr)
        damaging = [int(f) for f in flips
                    if cmask[f] and (len(trans) == 0
                                     or np.min(np.abs(trans - f)) > 2)]
        fa[name] = {
            'flips': len(flips),
            'near_transition': len(near),
            'damaging_confident_sustained': len(damaging),
            'flip_energy_db_median': round(float(np.median(
                arr[flips, CI['energy_db']])), 1) if len(flips) else None,
            'flip_P_median': round(float(np.median(
                arr[flips, CI['P']])), 1) if len(flips) else None,
            'flip_snr_margin_median': round(float(np.median(
                np.abs(arr[flips, CI['snr']] - V_THRESH))), 2) if len(flips) else None,
        }
        print(f'  {name}: {fa[name]}')
    results['failure_analysis_clean'] = fa

    with open(out_json, 'w') as fh:
        json.dump(results, fh, indent=1, default=str)
    print(f'\nwrote {out_json}')


if __name__ == '__main__':
    main()
