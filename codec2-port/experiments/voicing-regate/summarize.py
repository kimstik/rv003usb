#!/usr/bin/env python3
"""Aggregate results/metrics.json (+ optional warpq.json) into markdown tables.

Usage: summarize.py <metrics.json> <warpq.json> <floor_file> <seeds...>
(warpq.json may be missing -- reported as n/a.)
"""
import json
import os
import sys

import numpy as np


def fmt(v, prec=3):
    return f'{v:.{prec}f}' if isinstance(v, (int, float)) else str(v)


def main():
    metrics_path, warpq_path, floor_file = sys.argv[1:4]
    seeds = sys.argv[4:]
    M = json.load(open(metrics_path))
    W = json.load(open(warpq_path)) if os.path.exists(warpq_path) else None

    names = sorted(M)
    rand_keys = [f'rand{s}' for s in seeds]

    print('## Per-file: swap (rule voicing) vs stock decode\n')
    print('| file | flip% | segSNR mean dB | ESTOI(vs stock) | LSD dB | '
          'NMR dB | ESTOI(orig,stock) | dESTOI(orig) | err mass near '
          'flips±2 | err mass near trans±2 |')
    print('|---|---|---|---|---|---|---|---|---|---|')
    for n in names:
        r = M[n]
        s = r['swap']
        a = s['attribution']
        print(f'| {n} | {r["flip_rate_pct"]:.2f} | '
              f'{s["segsnr_mean_dB"]:.2f} | {s["estoi_vs_stock"]:.4f} | '
              f'{s["lsd_dB"]:.2f} | {s["nmr_dB"]:.1f} | '
              f'{r["estoi_orig_stock"]:.4f} | {s["destoi_orig"]:+.4f} | '
              f'{100 * a["err_mass_near_flips"]:.1f}% | '
              f'{100 * a["err_mass_near_transitions"]:.1f}% |')

    print('\n## Per-file: random control (matched flip rate) vs stock decode')
    print('(each cell: mean over seeds ' + ','.join(seeds) + ')\n')
    print('| file | segSNR mean dB | ESTOI(vs stock) | LSD dB | NMR dB | '
          'dESTOI(orig) |')
    print('|---|---|---|---|---|---|')
    agg = {k: [] for k in ['swap_seg', 'swap_estoi', 'swap_lsd', 'swap_nmr',
                           'swap_de', 'rand_seg', 'rand_estoi', 'rand_lsd',
                           'rand_nmr', 'rand_de']}
    for n in names:
        r = M[n]
        rs = [r[k] for k in rand_keys]
        seg = np.mean([x['segsnr_mean_dB'] for x in rs])
        est = np.mean([x['estoi_vs_stock'] for x in rs])
        lsd = np.mean([x['lsd_dB'] for x in rs])
        nmr = np.mean([x['nmr_dB'] for x in rs])
        de = np.mean([x['destoi_orig'] for x in rs])
        print(f'| {n} | {seg:.2f} | {est:.4f} | {lsd:.2f} | {nmr:.1f} | '
              f'{de:+.4f} |')
        agg['rand_seg'].append(seg)
        agg['rand_estoi'].append(est)
        agg['rand_lsd'].append(lsd)
        agg['rand_nmr'].append(nmr)
        agg['rand_de'].append(de)
        s = r['swap']
        agg['swap_seg'].append(s['segsnr_mean_dB'])
        agg['swap_estoi'].append(s['estoi_vs_stock'])
        agg['swap_lsd'].append(s['lsd_dB'])
        agg['swap_nmr'].append(s['nmr_dB'])
        agg['swap_de'].append(s['destoi_orig'])

    print('\n## Aggregate (mean over files)\n')
    print('| version | segSNR mean dB | ESTOI(vs stock) | LSD dB | NMR dB | '
          'dESTOI(orig) |')
    print('|---|---|---|---|---|---|')
    print(f'| swap (rule) | {np.mean(agg["swap_seg"]):.2f} | '
          f'{np.mean(agg["swap_estoi"]):.4f} | {np.mean(agg["swap_lsd"]):.2f} | '
          f'{np.mean(agg["swap_nmr"]):.1f} | {np.mean(agg["swap_de"]):+.4f} |')
    print(f'| random ctrl | {np.mean(agg["rand_seg"]):.2f} | '
          f'{np.mean(agg["rand_estoi"]):.4f} | {np.mean(agg["rand_lsd"]):.2f} | '
          f'{np.mean(agg["rand_nmr"]):.1f} | {np.mean(agg["rand_de"]):+.4f} |')

    real = [n for n in names if n != 'testframes_700d']
    idx = [names.index(n) for n in real]
    print(f'| swap, speech only (no testframes_700d) | '
          f'{np.mean([agg["swap_seg"][i] for i in idx]):.2f} | '
          f'{np.mean([agg["swap_estoi"][i] for i in idx]):.4f} | '
          f'{np.mean([agg["swap_lsd"][i] for i in idx]):.2f} | '
          f'{np.mean([agg["swap_nmr"][i] for i in idx]):.1f} | '
          f'{np.mean([agg["swap_de"][i] for i in idx]):+.4f} |')
    print(f'| random, speech only | '
          f'{np.mean([agg["rand_seg"][i] for i in idx]):.2f} | '
          f'{np.mean([agg["rand_estoi"][i] for i in idx]):.4f} | '
          f'{np.mean([agg["rand_lsd"][i] for i in idx]):.2f} | '
          f'{np.mean([agg["rand_nmr"][i] for i in idx]):.1f} | '
          f'{np.mean([agg["rand_de"][i] for i in idx]):+.4f} |')

    if floor_file in M and 'stock_o2' in M[floor_file]:
        f = M[floor_file]['stock_o2']
        print(f'\n## Float noise floor (-O2 vs -O3 full chain, {floor_file})\n')
        print(f'segSNR mean {f["segsnr_mean_dB"]:.2f} dB, '
              f'ESTOI {f["estoi_vs_stock"]:.4f}, LSD {f["lsd_dB"]:.2f} dB, '
              f'NMR {f["nmr_dB"]:.1f} dB')

    if W:
        print('\n## WARP-Q (raw score = DTW distance, lower = closer)\n')
        print('| file | stock vs swap | stock vs rand (mean) | orig vs stock '
              '| orig vs swap | d(orig) |')
        print('|---|---|---|---|---|---|')
        acc = {'sw': [], 'rd': [], 'os': [], 'ow': []}
        for n in sorted(W):
            r = W[n]
            rd = np.mean([r[f'stock_vs_rand{s}'] for s in seeds])
            print(f'| {n} | {r["stock_vs_swap"]:.3f} | {rd:.3f} | '
                  f'{r["orig_vs_stock"]:.3f} | {r["orig_vs_swap"]:.3f} | '
                  f'{r["orig_vs_swap"] - r["orig_vs_stock"]:+.3f} |')
            acc['sw'].append(r['stock_vs_swap'])
            acc['rd'].append(rd)
            acc['os'].append(r['orig_vs_stock'])
            acc['ow'].append(r['orig_vs_swap'])
        print(f'| **mean** | {np.mean(acc["sw"]):.3f} | {np.mean(acc["rd"]):.3f} '
              f'| {np.mean(acc["os"]):.3f} | {np.mean(acc["ow"]):.3f} | '
              f'{np.mean(acc["ow"]) - np.mean(acc["os"]):+.3f} |')
        for n in sorted(W):
            if 'stock_vs_stock_o2' in W[n]:
                print(f'\nWARP-Q noise floor ({n}, stock -O3 decode vs -O2 '
                      f'full chain): {W[n]["stock_vs_stock_o2"]:.3f}')
    else:
        print('\n(WARP-Q results not available)')


if __name__ == '__main__':
    main()
