"""Common test-bench infrastructure for the D4 synthesis-engine bake-off.

Frame convention: a frame is a dict with keys
    Wo   : fundamental in rad/sample (Fs=8000)
    A    : np.ndarray of harmonic amplitudes, A[0] is harmonic 1 ... A[L-1] harmonic L
    phi  : np.ndarray of per-harmonic phases (min-phase model), same indexing
    N    : number of output samples for this frame
Engines receive a list of frames and return one contiguous float64 signal.
"""

import numpy as np

FS = 8000
FRAME_N = 160          # 20 ms
NFFT_ENV = 1024        # dense grid for envelope evaluation


# ----------------------------------------------------------------------------
# Speech-like spectral envelopes: formant resonances + spectral tilt
# ----------------------------------------------------------------------------

# name -> list of (formant_freq_hz, bandwidth_hz)
ENVELOPES = {
    "aa": [(730, 90), (1090, 110), (2440, 140)],   # open back vowel
    "iy": [(270, 60), (2290, 100), (3010, 140)],   # close front vowel
    "uw": [(300, 65), (870, 90), (2240, 140)],     # close back vowel
}

TILT_DB_PER_OCT = -6.0   # glottal source tilt


def env_mag(freqs_hz, formants, tilt_db_per_oct=TILT_DB_PER_OCT):
    """Magnitude of a speech-like envelope at freqs_hz (linear scale).

    Formants are genuine 2nd-order resonator magnitudes (all-pole), so an
    order-10 LPC fit is *plausible* but not exact (tilt adds a non-all-pole
    component + we normalize).  Result normalized to max 1 over 0..4kHz.
    """
    f = np.atleast_1d(np.asarray(freqs_hz, dtype=float))
    w = 2 * np.pi * f / FS
    h = np.ones_like(f)
    for (ff, bw) in formants:
        r = np.exp(-np.pi * bw / FS)
        th = 2 * np.pi * ff / FS
        p = r * np.exp(1j * th)
        # |1/((1-p e^-jw)(1-p* e^-jw))|
        z = np.exp(-1j * w)
        h = h / np.abs((1 - p * z) * (1 - np.conj(p) * z))
    # tilt: -6 dB/oct above 300 Hz corner (keeps LF finite)
    fc = 300.0
    tilt = (1.0 + (f / fc) ** 2) ** (tilt_db_per_oct / 12.0)  # per-oct in power
    h = h * tilt
    # normalize on dense grid so different calls are consistent
    fg = np.linspace(1, FS / 2 - 1, 2048)
    wg = 2 * np.pi * fg / FS
    hg = np.ones_like(fg)
    for (ff, bw) in formants:
        r = np.exp(-np.pi * bw / FS)
        th = 2 * np.pi * ff / FS
        p = r * np.exp(1j * th)
        zg = np.exp(-1j * wg)
        hg = hg / np.abs((1 - p * zg) * (1 - np.conj(p) * zg))
    hg = hg * (1.0 + (fg / fc) ** 2) ** (tilt_db_per_oct / 12.0)
    return h / hg.max()


def min_phase_of_env(env_fn, nfft=NFFT_ENV):
    """Min-phase response sampled on nfft/2+1 rfft grid, from |H| via cepstrum.

    Returns (freqs_hz, phase_radians) on the rfft grid.  This mirrors what
    codec2's phase.c does (mag_to_phase): real cepstrum folding.
    """
    f = np.arange(nfft // 2 + 1) * FS / nfft
    mag = env_fn(np.maximum(f, 1.0))
    mag = np.maximum(mag, 1e-6)
    logm = np.log(mag)
    # even extension -> real cepstrum
    full = np.concatenate([logm, logm[-2:0:-1]])
    cep = np.fft.ifft(full).real
    # fold: min phase reconstruction
    w = np.zeros(nfft)
    w[0] = 1.0
    w[1:nfft // 2] = 2.0
    w[nfft // 2] = 1.0
    minph_spec = np.fft.fft(cep * w)
    phase = np.imag(minph_spec[:nfft // 2 + 1])
    return f, phase


def make_frame(f0_hz, env_name, N=FRAME_N, amp_norm="peak1"):
    """Build one synthetic voiced frame: harmonics of f0 under a named envelope."""
    formants = ENVELOPES[env_name]
    Wo = 2 * np.pi * f0_hz / FS
    L = int(np.floor(np.pi / Wo))
    # keep harmonics strictly below Nyquist: a component AT fs/2 is degenerate
    # (its measured amplitude depends on phase), which poisons metrics only
    while L * f0_hz >= FS / 2 - 1:
        L -= 1
    k = np.arange(1, L + 1)
    A = env_mag(k * f0_hz, formants)
    if amp_norm == "peak1":
        A = A / A.max()
    fgrid, ph = min_phase_of_env(lambda ff: env_mag(ff, formants))
    phi = np.interp(k * f0_hz, fgrid, ph)
    return {"Wo": Wo, "A": A, "phi": phi, "N": N, "f0": f0_hz, "env": env_name}


def steady_frames(f0_hz, env_name, n_frames=25, N=FRAME_N):
    fr = make_frame(f0_hz, env_name, N)
    return [dict(fr) for _ in range(n_frames)]


# ----------------------------------------------------------------------------
# Reference synthesizer: codec2-style OLA of per-frame direct sinusoid sums
# ----------------------------------------------------------------------------

def synth_reference(frames):
    """Direct sum of L sinusoids per frame, triangular-window OLA (sine.c style).

    Fundamental phase track is continuous (an excitation phase accumulator,
    like ex_phase in codec2); per-harmonic phase = k*ex_phase + phi_k.
    Each frame contributes a 2N-long triangular-windowed segment centered on
    the frame -> smooth parameter interpolation, the codec2 way.
    """
    total = sum(f["N"] for f in frames)
    out = np.zeros(total + frames[-1]["N"])
    wsum = np.zeros_like(out)
    ex_phase = 0.0
    pos = 0
    for f in frames:
        N, Wo = f["N"], f["Wo"]
        L = len(f["A"])
        # phase of fundamental at frame center (end of this frame's advance)
        ex_end = ex_phase + Wo * N
        n = np.arange(2 * N) - N          # centered on frame end boundary
        seg = np.zeros(2 * N)
        k = np.arange(1, L + 1)[:, None]
        ph = k * (ex_end + n[None, :] * Wo) + f["phi"][:, None]
        seg = (f["A"][:, None] * np.cos(ph)).sum(axis=0)
        win = 1.0 - np.abs(n) / N          # triangular, 2N wide
        start = pos
        sl = slice(start, start + 2 * N)
        out[sl] += seg * win
        wsum[sl] += win
        ex_phase = np.mod(ex_end, 2 * np.pi)
        pos += N
    wsum[wsum < 1e-9] = 1.0
    sig = out / wsum
    # first half-frame and last half-frame are edge-windowed; keep total len
    return sig[:total]
