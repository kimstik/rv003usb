"""metrics.py — the bench's signal metrics, reusing the project's designs.

Every metric here is the SAME convention already published in the research
reports, so the numbers on the listening page are comparable with the numbers
in experiments/*/REPORT.md:

  ESTOI   pystoi extended=True, after a single metric-optimal constant lag
          (best_lag_estoi below: the lag in |lag| <= 256 that MAXIMISES ESTOI,
          coarse 16 -> 4 -> 1).
  LSD     per-frame log-spectral distance, 160-sample Hann window on the
          80-sample (10 ms) grid, RMS over 100-3700 Hz, frames gated at
          -40 dB below utterance RMS of the reference
          (experiments/tube-ladder/metrics_ladder.py lsd_stats).
  segSNR  20 ms frame / 10 ms hop, per-frame clamp [-10, +35] dB, -40 dB
          silence gate on the reference, mean and median
          (experiments/oracle/metrics_signal.py convention).

Alignment policy, and why it differs per pair:
  * vs the ORIGINAL (anchor A) every vocoder output has a large delay, so a
    single constant lag is searched and the METRIC BEING REPORTED is the
    thing maximised — the "best-case single-delay alignment" convention
    tube-ladder adopted, applied identically to every condition.
    The correlation-peak lag of proto/decoder/validate.py is NOT used: the raw
    dot product is phase-sensitive and locks onto a wrong peak on parametric
    speech (measured: on mmt1 condition C it returned +144 instead of ~-100
    and depressed ESTOI from 0.328 to 0.186).  Consequence: absolute ESTOI
    here runs slightly HIGHER than the same-named numbers in
    proto/decoder/REPORT.md (hts1a C 0.670 here vs 0.668 there; the reference
    decoder B 0.731 vs 0.676) — the ranking is unchanged, the alignment is
    strictly better, and the lag actually used is printed in every row.
  * vs the CODEC CEILING (anchor B = c2dec) the compared decoders consume the
    same bitstream on the same 40 ms grid, but c2dec synthesises with phase0
    IFFT+OLA while c2tube runs a free-running IIR tube — different group delay
    AND a different phase track.  A single LSD-optimal constant lag is searched
    (tube-ladder convention) and the SAME lag is then used for segSNR.
    segSNR against B therefore remains a WEAK metric (it punishes the phase
    track, not the audible spectrum) and is reported for completeness only.
"""
import numpy as np

FS = 8000
N = 80


# --------------------------------------------------------------------------
# alignment
# --------------------------------------------------------------------------

def aligned(ref, x, lag):
    n = min(len(ref), len(x))
    ref, x = ref[:n], x[:n]
    if lag >= 0:
        return ref[lag:], x[:n - lag]
    return ref[:n + lag], x[-lag:]


def best_lag_xcorr(ref, x, maxlag=256):
    """proto/decoder/validate.py best_lag() — correlation peak, coarse+fine."""
    n = min(len(ref), len(x))
    ref, x = ref[:n], x[:n]
    best, bl = -1e18, 0
    for lag in range(-maxlag, maxlag + 1, 4):
        a, b = aligned(ref, x, lag)
        c = float(np.dot(a, b))
        if c > best:
            best, bl = c, lag
    best2, bl2 = -1e18, bl
    for lag in range(bl - 4, bl + 5):
        if abs(lag) > maxlag:
            continue
        a, b = aligned(ref, x, lag)
        c = float(np.dot(a, b))
        if c > best2:
            best2, bl2 = c, lag
    return bl2


def best_lag_estoi(ref, x, maxlag=256):
    """The constant lag maximising ESTOI (coarse 16 -> 4 -> 1)."""
    def at(lag):
        a, b = aligned(ref, x, lag)
        return estoi(a, b)
    l0 = max(range(-maxlag, maxlag + 1, 16), key=at)
    l1 = max(range(max(-maxlag, l0 - 12), min(maxlag, l0 + 12) + 1, 4), key=at)
    l2 = max(range(max(-maxlag, l1 - 3), min(maxlag, l1 + 3) + 1), key=at)
    return l2


def best_lag_lsd(ref, x, maxlag=96, step=8):
    """tube-ladder find_lag_lsd() — the lag minimising mean frame LSD."""
    def at(lag):
        a, b = aligned(ref, x, lag)
        v = lsd_stats(b, a)["lsd_mean"]
        return 1e9 if not np.isfinite(v) else v
    coarse = range(-maxlag, maxlag + 1, step)
    l0 = min(coarse, key=at)
    fine = range(max(-maxlag, l0 - step + 1), min(maxlag, l0 + step - 1) + 1)
    return min(fine, key=at)


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------

def _frames(x, ref, frame_n=N, win_n=2 * N):
    win = np.hanning(win_n)
    n = min(len(x), len(ref))
    rms = np.sqrt(np.mean(ref[:n] ** 2)) + 1e-12
    floor = rms * 10 ** (-40.0 / 20)
    for start in range(0, n - win_n, frame_n):
        r = ref[start:start + win_n]
        if np.sqrt(np.mean(r ** 2)) < floor:
            continue
        yield (np.abs(np.fft.rfft(x[start:start + win_n] * win)),
               np.abs(np.fft.rfft(r * win)))


def lsd_stats(x, ref, lo_hz=100.0, hi_hz=3700.0):
    f = np.fft.rfftfreq(2 * N, 1 / FS)
    sel = (f >= lo_hz) & (f <= hi_hz)
    vals = []
    for X, R in _frames(x, ref):
        lx = 20 * np.log10(np.maximum(X[sel], 1e-6))
        lr = 20 * np.log10(np.maximum(R[sel], 1e-6))
        vals.append(np.sqrt(np.mean((lx - lr) ** 2)))
    v = np.array(vals)
    if v.size == 0:
        return {"lsd_mean": float("nan"), "lsd_median": float("nan"),
                "lsd_p90": float("nan"), "lsd_frames": 0}
    return {"lsd_mean": float(v.mean()), "lsd_median": float(np.median(v)),
            "lsd_p90": float(np.percentile(v, 90)), "lsd_frames": int(v.size)}


def seg_snr(ref, test, frame_ms=20.0, hop_ms=10.0):
    n_len = int(FS * frame_ms / 1000)
    hop = int(FS * hop_ms / 1000)
    n = min(len(ref), len(test))
    ref, test = ref[:n], test[:n]
    rms = np.sqrt(np.mean(ref ** 2)) + 1e-12
    floor = rms * 10 ** (-40.0 / 20)
    vals = []
    for s0 in range(0, n - n_len + 1, hop):
        s = ref[s0:s0 + n_len]
        if np.sqrt(np.mean(s ** 2)) < floor:
            continue
        e = s - test[s0:s0 + n_len]
        es, ee = float(np.sum(s ** 2)), float(np.sum(e ** 2))
        snr = 10 * np.log10(es / ee) if ee > 0 else 35.0
        vals.append(np.clip(snr, -10.0, 35.0))
    v = np.array(vals)
    if v.size == 0:
        return {"segsnr_mean": float("nan"), "segsnr_median": float("nan"),
                "segsnr_frames": 0}
    return {"segsnr_mean": float(v.mean()),
            "segsnr_median": float(np.median(v)),
            "segsnr_frames": int(v.size)}


def estoi(ref, x):
    from pystoi import stoi
    n = min(len(ref), len(x))
    return float(stoi(ref[:n], x[:n], FS, extended=True))


def active_rms_dbfs(x, floor_db=40.0):
    """RMS over frames above the silence floor, dBFS (level sanity column)."""
    n_len, hop = 160, 80
    rms = np.sqrt(np.mean(x ** 2)) + 1e-12
    floor = rms * 10 ** (-floor_db / 20)
    acc, cnt = 0.0, 0
    for s0 in range(0, len(x) - n_len + 1, hop):
        s = x[s0:s0 + n_len]
        r = np.sqrt(np.mean(s ** 2))
        if r < floor:
            continue
        acc += float(np.mean(s ** 2))
        cnt += 1
    if cnt == 0:
        return float("nan")
    return float(20 * np.log10(np.sqrt(acc / cnt) + 1e-12))
