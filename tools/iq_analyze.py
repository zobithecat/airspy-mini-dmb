"""Deep spectral analysis of a raw I/Q capture to verify whether a DAB
signal exists at all.

  - Full-band PSD with 1 Hz bin resolution
  - DAB null-symbol autocorrelation at 96 ms
  - ADC fill statistics (dynamic-range usage)
  - Frequency-offset estimate from null detection
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

import numpy as np
from numpy.fft import fft, fftshift


def load_iq(path: Path, fs: int) -> np.ndarray:
    raw = np.fromfile(path, dtype="<i2").astype(np.float32) / 32768.0
    iq = raw[::2] + 1j * raw[1::2]
    return iq


def psd_summary(iq: np.ndarray, fs: int) -> dict:
    """Compute PSD with several windows, report in-band/edge ratio."""
    nfft = 65536
    n = min(len(iq), nfft * 16)
    iq = iq[:n]
    # Welch-style averaging
    n_segs = max(1, n // nfft)
    psd = np.zeros(nfft)
    win = np.hanning(nfft)
    for k in range(n_segs):
        seg = iq[k*nfft:(k+1)*nfft] * win
        psd += np.abs(fftshift(fft(seg))) ** 2
    psd /= n_segs
    psd_db = 10 * np.log10(psd + 1e-15)

    freqs = np.linspace(-fs / 2, fs / 2, nfft) / 1e6  # MHz

    # In-band: central ±0.768 MHz (1.536 MHz DAB BW)
    band_idx = np.abs(freqs) < 0.768
    in_band = np.mean(psd_db[band_idx])

    # Outer edges: outer 25% of spectrum on each side
    edge_idx = np.abs(freqs) > fs / 2 / 1e6 * 0.7
    edge = np.mean(psd_db[edge_idx])

    # Peak in-band - mean edge
    peak = np.max(psd_db[band_idx])

    return {
        "in_band_db": in_band,
        "peak_in_band_db": peak,
        "edge_db": edge,
        "delta_db": in_band - edge,
        "peak_minus_edge_db": peak - edge,
        "psd_db": psd_db,
        "freqs_mhz": freqs,
    }


def adc_fill(iq: np.ndarray) -> dict:
    """How well is the ADC dynamic range used? Clipping = bad, too low = noisy."""
    re = np.real(iq)
    im = np.imag(iq)
    return {
        "re_mean": float(np.mean(re)),
        "re_std": float(np.std(re)),
        "im_mean": float(np.mean(im)),
        "im_std": float(np.std(im)),
        "re_max": float(np.max(np.abs(re))),
        "im_max": float(np.max(np.abs(im))),
        "re_clip_pct": float(100 * np.mean(np.abs(re) > 0.99)),
        "im_clip_pct": float(100 * np.mean(np.abs(im) > 0.99)),
    }


def null_period(iq: np.ndarray, fs: int) -> dict:
    """Look for DAB null symbol pattern (96 ms period, ~1.297 ms null)."""
    # decimate to 2 MSPS via simple stride (good enough for envelope)
    dec = max(1, fs // 2_000_000)
    iq2 = iq[::dec]
    fs2 = fs / dec
    env = np.abs(iq2)
    # smooth with 256-tap rolling mean
    W = 256
    sm = np.convolve(env, np.ones(W) / W, mode="same")
    mu = float(np.mean(sm))
    sigma = float(np.std(sm))

    # dips below mu * 0.8
    mask = sm < 0.8 * mu
    runs = []
    in_dip, start = False, 0
    for i, m in enumerate(mask):
        if m and not in_dip:
            start, in_dip = i, True
        elif not m and in_dip:
            if i - start > 1500:  # > 0.75 ms
                runs.append(start)
            in_dip = False

    gaps_ms = np.diff(runs) / fs2 * 1000 if len(runs) >= 2 else np.array([])
    near96 = gaps_ms[(gaps_ms > 85) & (gaps_ms < 110)] if gaps_ms.size else np.array([])
    return {
        "mu": mu, "sigma": sigma,
        "n_dips": len(runs),
        "n_near96ms": int(near96.size),
        "median_gap_ms": float(np.median(near96)) if near96.size else float("nan"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("iq", type=Path)
    ap.add_argument("--fs", type=int, default=6_000_000)
    args = ap.parse_args()

    iq = load_iq(args.iq, args.fs)
    print(f"\nLoaded {len(iq):,} I/Q samples at {args.fs/1e6:.3f} MSPS "
          f"({len(iq)/args.fs:.3f}s)\n")

    print("=== ADC fill ===")
    a = adc_fill(iq)
    print(f"  Re: mean={a['re_mean']:+.4f}  std={a['re_std']:.4f}  "
          f"max={a['re_max']:.3f}  clip%={a['re_clip_pct']:.2f}")
    print(f"  Im: mean={a['im_mean']:+.4f}  std={a['im_std']:.4f}  "
          f"max={a['im_max']:.3f}  clip%={a['im_clip_pct']:.2f}")
    dr_used = 20 * np.log10((a['re_std'] + a['im_std']) / 2 + 1e-10)
    print(f"  → mean fill: {dr_used:.1f} dBFS  (ideal: -6 to -3 dBFS)")
    if a['re_clip_pct'] > 0.1 or a['im_clip_pct'] > 0.1:
        print(f"  ⚠ Clipping detected — reduce gain")
    elif dr_used < -25:
        print(f"  ⚠ Under-filled ADC — increase gain")

    print("\n=== PSD ===")
    s = psd_summary(iq, args.fs)
    print(f"  in-band  (±0.768 MHz):  {s['in_band_db']:+.1f} dB")
    print(f"  peak in-band:           {s['peak_in_band_db']:+.1f} dB")
    print(f"  edges    (outer 25%):   {s['edge_db']:+.1f} dB")
    print(f"  Δ (mean):               {s['delta_db']:+.1f} dB")
    print(f"  Δ (peak):               {s['peak_minus_edge_db']:+.1f} dB")
    if s['delta_db'] > 8:
        print(f"  ✓ Clear DAB-like signal energy")
    elif s['delta_db'] > 3:
        print(f"  ~ Weak signal, marginal")
    else:
        print(f"  ✗ No clear in-band excess — signal likely below noise floor")

    print("\n=== DAB null-symbol detection (96 ms period) ===")
    n = null_period(iq, args.fs)
    print(f"  envelope μ={n['mu']:.4f}  σ={n['sigma']:.4f}")
    print(f"  dips detected: {n['n_dips']}")
    if n['n_near96ms'] > 0:
        print(f"  ✓ {n['n_near96ms']} dips with ~96 ms spacing  median={n['median_gap_ms']:.1f} ms")
        print(f"  → DAB transmission frame structure visible")
    else:
        print(f"  ✗ No 96 ms periodicity found")

    # Spectrum text plot
    print("\n=== Spectrum (text plot, full BW) ===")
    psd_db = s['psd_db']
    freqs = s['freqs_mhz']
    bins = 80
    step = len(psd_db) // bins
    binned = np.array([np.mean(psd_db[i*step:(i+1)*step]) for i in range(bins)])
    binned_freqs = np.array([np.mean(freqs[i*step:(i+1)*step]) for i in range(bins)])
    floor = np.min(binned)
    ceil = np.max(binned)
    span = max(ceil - floor, 1)
    for i in range(bins):
        n_bars = int((binned[i] - floor) / span * 40)
        mark = "|" if abs(binned_freqs[i]) < 0.05 else " "
        print(f"  {binned_freqs[i]:+6.2f} MHz {mark} {'█'*n_bars}{' '*(40-n_bars)} {binned[i]:+5.1f}")


if __name__ == "__main__":
    main()
