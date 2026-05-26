"""Wideband Band III sweep: capture 1 sec per center freq, compute
PSD peak, report any spot showing a 1.5 MHz wide bump.

Catches DAB signals we might have missed (off-center tuning, broken
raster assumption, etc.).
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from numpy.fft import fft, fftshift


def capture(freq_mhz: float, fs: int = 6_000_000, secs: float = 1.0) -> np.ndarray:
    p = "/tmp/sweep_iq.raw"
    n = int(fs * secs)
    subprocess.run(
        ["airspy_rx", "-r", p, "-f", str(freq_mhz),
         "-a", str(fs), "-l", "14", "-m", "15", "-v", "15",
         "-t", "2", "-n", str(n)],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if not os.path.exists(p) or os.path.getsize(p) < 1000:
        return np.zeros(0, dtype=complex)
    raw = np.fromfile(p, dtype="<i2").astype(np.float32) / 32768.0
    return raw[::2] + 1j * raw[1::2]


def find_peak_in_band(iq: np.ndarray, fs: int) -> dict:
    """Slide a 1.5 MHz window across the IF and find max in-band excess
    over flat noise floor."""
    if len(iq) < 32768:
        return {"peak_bump_db": 0.0, "peak_off_mhz": 0.0, "rms_db": 0.0}
    nfft = 32768
    n_segs = min(8, len(iq) // nfft)
    win = np.hanning(nfft)
    psd = np.zeros(nfft)
    for k in range(n_segs):
        seg = iq[k*nfft:(k+1)*nfft] * win
        psd += np.abs(fftshift(fft(seg))) ** 2
    psd /= n_segs
    psd_db = 10 * np.log10(psd + 1e-15)

    # Restrict to flat IF passband: ±2.0 MHz
    freqs = np.linspace(-fs/2, fs/2, nfft)
    flat_mask = np.abs(freqs) < 2_000_000
    psd_flat = psd_db[flat_mask]
    f_flat = freqs[flat_mask]

    # Noise floor estimate: median
    noise_db = float(np.median(psd_flat))

    # Slide a 1.5 MHz wide window
    win_bw = 1_500_000
    df = f_flat[1] - f_flat[0]
    win_bins = int(win_bw / df)
    best_bump = -100
    best_offset = 0.0
    for i in range(0, len(psd_flat) - win_bins, win_bins // 4):
        bump = float(np.mean(psd_flat[i:i+win_bins]) - noise_db)
        if bump > best_bump:
            best_bump = bump
            best_offset = float(np.mean(f_flat[i:i+win_bins]))
    return {
        "peak_bump_db": best_bump,
        "peak_off_mhz": best_offset / 1e6,
        "rms_db": noise_db,
    }


# Korean K-block centers
KOREAN_CENTERS = [
    ("K7A",  175.280), ("K7B",  177.008), ("K7C",  178.736),
    ("K8A",  181.280), ("K8B",  183.008), ("K8C",  184.736),
    ("K9A",  187.280), ("K9B",  189.008), ("K9C",  190.736),
    ("K10A", 193.280), ("K10B", 195.008), ("K10C", 196.736),
    ("K11A", 199.280), ("K11B", 201.008), ("K11C", 202.736),
    ("K12A", 205.280), ("K12B", 207.008), ("K12C", 208.736),
    ("K13A", 211.280), ("K13B", 213.008), ("K13C", 214.736),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, default=1.0)
    ap.add_argument("--fs", type=int, default=6_000_000)
    args = ap.parse_args()

    print(f"Band III sweep with full-gain (L=14 M=15 V=15), {args.secs}s per channel")
    print(f"  Bump threshold: 2 dB = marginal, 5+ dB = signal present")
    print()
    print(f"  {'Ch':>5}  {'Freq':>10}   {'bump':>6}  {'offset':>8}  {'noise':>7}  status")
    print("  " + "-" * 60)

    results = []
    for name, freq in KOREAN_CENTERS:
        iq = capture(freq, fs=args.fs, secs=args.secs)
        if len(iq) == 0:
            print(f"  {name:>5}  {freq:>7.3f}M   capture failed")
            continue
        r = find_peak_in_band(iq, args.fs)
        bump = r["peak_bump_db"]
        if bump > 5:
            status = f"  ✓ signal at +{r['peak_off_mhz']:+.2f} MHz"
        elif bump > 2:
            status = f"  ~ weak bump"
        else:
            status = ""
        print(f"  {name:>5}  {freq:>7.3f}M   {bump:>+5.1f}   "
              f"{r['peak_off_mhz']:>+5.2f}M   {r['rms_db']:>+5.1f}{status}",
              flush=True)
        results.append((name, freq, bump, r['peak_off_mhz']))

    print()
    print("=== TOP 5 by bump ===")
    for n, f, b, o in sorted(results, key=lambda x: x[2], reverse=True)[:5]:
        print(f"  {n:>5}  {f:>7.3f}M  bump {b:+.1f} dB at offset {o:+.2f} MHz")


if __name__ == "__main__":
    main()
