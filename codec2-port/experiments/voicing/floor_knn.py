#!/usr/bin/env python3
"""Nonparametric floor: how well can ANY rule over these FFT-free features
replicate the MBE voicing decision?  k-NN (k=15) on standardized features,
leave-one-file-out.  If this floor is far above the target flip rate, no
MCU-trivial rule (nor any fancier one) can reach the target with this feature
set -- the gap is an information limit of the features, not rule simplicity.
"""
import sys
import os
import glob
import numpy as np

V_THRESH = 6.0
FEATS = ['nacf_wo', 'nacf_best', 'nacf_sub', 'yin_wo', 'yin_best',
         'r1r0', 'zcr', 'lhr_db', 'energy_db']
COLS = ['frame', 'ref_v', 'snr', 'eratio', 'Wo', 'P', 'L'] + FEATS
CI = {c: i for i, c in enumerate(COLS)}
K = 15


def main(feat_dir):
    files, arrs = [], []
    for f in sorted(glob.glob(os.path.join(feat_dir, '*.csv'))):
        files.append(os.path.basename(f)[:-4])
        arrs.append(np.loadtxt(f, delimiter=',', skiprows=1))
    fcols = [CI[f] for f in FEATS]
    X = np.vstack([a[:, fcols] for a in arrs])
    mu, sd = X.mean(0), X.std(0) + 1e-9
    tot = fl = conf_tot = conf_fl = 0
    per = {}
    for i, name in enumerate(files):
        Xtr = np.vstack([(a[:, fcols] - mu) / sd
                         for j, a in enumerate(arrs) if j != i])
        ytr = np.concatenate([a[:, CI['ref_v']]
                              for j, a in enumerate(arrs) if j != i]).astype(int)
        Xte = (arrs[i][:, fcols] - mu) / sd
        yte = arrs[i][:, CI['ref_v']].astype(int)
        snr = arrs[i][:, CI['snr']]
        pred = np.empty(len(Xte), int)
        for i0 in range(0, len(Xte), 500):
            D = ((Xte[i0:i0 + 500, None, :] - Xtr[None, :, :]) ** 2).sum(-1)
            idx = np.argpartition(D, K, axis=1)[:, :K]
            pred[i0:i0 + 500] = (ytr[idx].mean(1) > 0.5).astype(int)
        flips = pred != yte
        raw = (snr > V_THRESH).astype(int)
        conf = (np.abs(snr - V_THRESH) > 5) & (raw == yte)
        per[name] = round(100 * flips.mean(), 2)
        tot += len(yte)
        fl += flips.sum()
        conf_tot += conf.sum()
        conf_fl += (flips & conf).sum()
    print(f'kNN({K}) LOFO floor: overall flip {100*fl/tot:.2f}%   '
          f'confident-flip {100*conf_fl/conf_tot:.2f}% ({conf_tot} conf frames)')
    print(f'  per-file: {per}')


if __name__ == '__main__':
    main(sys.argv[1])
