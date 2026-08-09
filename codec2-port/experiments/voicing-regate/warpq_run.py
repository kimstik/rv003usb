#!/usr/bin/env python3
"""WARP-Q scores for the voicing-swap pairs (best-effort step).

Uses the modern `warpq` package from github.com/wjassim/WARP-Q (cloned into
work/warpq_repo by this script if absent), default metric configuration
(sr=16000, VAD on, mean score) -- 8 kHz inputs are resampled to 16 kHz by
the metric itself (native_sr=False), as in the WARP-Q papers.

Scored pairs per corpus file:
  ref=stock-decode  vs  swap / rand<seed> decodes   (direct A/B distance)
  ref=original      vs  stock / swap decodes        (absolute codec distance;
                                                     the delta is the swap's
                                                     end-to-end cost)
  ref=stock-decode  vs  stock_o2 decode             (float noise floor, one file)

raw_warpq_score: lower = closer (it is a DTW distance, not MOS-like).

Deps beyond the base harness: librosa, pyvad (webrtcvad-wheels + pyvad
--no-deps worked in this container; stock webrtcvad fails to build with new
setuptools), tqdm, joblib.  Any failure exits non-zero; run_all.sh treats
that as non-fatal.

Usage: warpq_run.py <work_dir> <out_json> <seeds...>
"""
import glob
import json
import os
import subprocess
import sys
import wave

REPO_URL = 'https://github.com/wjassim/WARP-Q.git'


def raw_to_wav(raw_path, wav_path, fs=8000):
    if os.path.exists(wav_path):
        return
    with open(raw_path, 'rb') as fh:
        data = fh.read()
    with wave.open(wav_path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(data)


def main():
    work, out_json = sys.argv[1], sys.argv[2]
    seeds = sys.argv[3:]
    repo = os.path.join(work, 'warpq_repo')
    if not os.path.isdir(repo):
        subprocess.run(['git', 'clone', '-q', '--depth', '1', REPO_URL, repo],
                       check=True)
    sys.path.insert(0, repo)
    from warpq.core import warpqMetric   # noqa: E402

    metric = warpqMetric(sr=16000, native_sr=False)

    wav_dir = os.path.join(work, 'wav')
    os.makedirs(wav_dir, exist_ok=True)

    def wav(path):
        out = os.path.join(wav_dir,
                           os.path.basename(path).replace('.raw', '.wav'))
        raw_to_wav(path, out)
        return out

    results = {}
    for raw in sorted(glob.glob(os.path.join(work, 'codec2/raw/*.raw'))):
        name = os.path.basename(raw)[:-4]
        audio = os.path.join(work, 'audio')
        stock = os.path.join(audio, name + '.stock.raw')
        if not os.path.exists(stock):
            continue
        pairs = {'stock_vs_swap': (stock, os.path.join(audio, name + '.swap.raw'))}
        for s in seeds:
            pairs[f'stock_vs_rand{s}'] = (
                stock, os.path.join(audio, f'{name}.rand{s}.raw'))
        pairs['orig_vs_stock'] = (raw, stock)
        pairs['orig_vs_swap'] = (raw, os.path.join(audio, name + '.swap.raw'))
        o2 = os.path.join(audio, name + '.stock_o2.raw')
        if os.path.exists(o2):
            pairs['stock_vs_stock_o2'] = (stock, o2)
        r = {}
        for key, (a, b) in pairs.items():
            res = metric.evaluate(wav(a), wav(b))
            r[key] = res['raw_warpq_score']
        results[name] = r
        print(f'   {name}: ' + ', '.join(f'{k}={v:.3f}' for k, v in r.items()))

    with open(out_json, 'w') as fh:
        json.dump(results, fh, indent=1)
    print(f'   wrote {out_json}')


if __name__ == '__main__':
    main()
