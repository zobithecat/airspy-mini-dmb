"""Detect DAB Mode I null symbols (1.297 ms null every 96 ms) in raw I/Q.

DAB Mode I:
  - Transmission frame: 96 ms total
  - Null symbol: 2656 samples @ 2.048 MSPS = 1.297 ms
  - 76 OFDM symbols of 2552 samples each + 504-sample CP

When eti-cmdline says "There does not seem to be a DAB signal here", its
null detector failed. This script gives a second opinion: bandpass-filter
around DAB, decimate to 2.048 MSPS, look for envelope dips with the right
duration and period.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

import numpy as np


def load_iq(path: Path) -> np.ndarray:
    raw = np.fromfile(path, dtype="<i2").astype(np.float32) / 32768.0
    return raw[::2] + 1j * raw[1::2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("iq", type=Path)
    ap.add_argument("--fs", type=int, default=6_000_000)
    ap.add_argument("--bw", type=float, default=1.6e6,
                    help="bandpass BW (DAB occupies 1.536 MHz)")
    args = ap.parse_args()

    iq = load_iq(args.iq)
    fs = args.fs
    print(f"Loaded {len(iq):,} samples = {len(iq)/fs:.2f}s @ {fs/1e6:.3f} MSPS")

    # Decimate to ~2 MSPS via polyphase
    from scipy.signal import resample_poly, butter, sosfilt
    target = 2_048_000
    # Use approximate rational ratio
    from fractions import Fraction
    f = Fraction(target, fs).limit_denominator(256)
    up, down = f.numerator, f.denominator
    print(f"  resampling {fs} -> {target}  ({up}/{down})")
    iq2 = resample_poly(iq, up, down)
    fs2 = fs * up / down
    print(f"  fs2 = {fs2:.0f} Hz, samples = {len(iq2):,}")

    # Envelope smoothed with ~50 sample window (~25us)
    env = np.abs(iq2)
    W = 256  # ~125 us
    sm = np.convolve(env, np.ones(W) / W, mode="same")

    mu = np.mean(sm)
    p10 = np.percentile(sm, 10)
    p50 = np.percentile(sm, 50)
    p90 = np.percentile(sm, 90)
    print(f"  envelope μ={mu:.4f}  p10={p10:.4f}  p50={p50:.4f}  p90={p90:.4f}")

    # Find dips below 80% of median
    threshold = 0.85 * p50
    mask = sm < threshold

    # Find run starts
    runs = []
    in_dip, start = False, 0
    for i, m in enumerate(mask):
        if m and not in_dip:
            start, in_dip = i, True
        elif not m and in_dip:
            duration_samples = i - start
            # DAB null is 2656 samples at 2.048 MSPS → ~1.3 ms
            # We expect 1500..3500 samples at our resampled rate
            if 1500 < duration_samples < 3500:
                runs.append((start, i - start))
            in_dip = False

    print(f"\n  DAB-shaped dips (1500-3500 samp / 0.7-1.7 ms): {len(runs)}")
    if runs:
        starts = [r[0] for r in runs]
        durations = [r[1] for r in runs]
        gaps = np.diff(starts) / fs2 * 1000  # ms
        print(f"  durations:  median={np.median(durations):.0f} samples "
              f"({np.median(durations)/fs2*1000:.2f} ms)")
        print(f"  gaps between dip starts (ms): {[f'{g:.1f}' for g in gaps[:30]]}")
        # 96 ms expected for Mode I
        near96 = [g for g in gaps if 90 < g < 105]
        near48 = [g for g in gaps if 45 < g < 50]  # Mode II/III
        near24 = [g for g in gaps if 23 < g < 26]
        print(f"  gaps near 96 ms: {len(near96)}/{len(gaps)} → Mode I")
        print(f"  gaps near 48 ms: {len(near48)}/{len(gaps)}")
        print(f"  gaps near 24 ms: {len(near24)}/{len(gaps)}")

        if len(near96) >= 3:
            print("  ✓ DAB Mode I null pattern detected!")
        elif len(near48) >= 3:
            print("  ⚠ Looks like Mode II/III, not Korean T-DMB (uses Mode I)")
        else:
            print("  ✗ No DAB-like periodicity")

    # Coarser threshold scan
    print(f"\n  Threshold sweep (looking for 96 ms periodicity)...")
    for frac in (0.70, 0.75, 0.80, 0.85, 0.90, 0.95):
        thr = frac * p50
        mask = sm < thr
        runs2 = []
        in_dip, start = False, 0
        for i, m in enumerate(mask):
            if m and not in_dip:
                start, in_dip = i, True
            elif not m and in_dip:
                if 1500 < i - start < 3500:
                    runs2.append(start)
                in_dip = False
        if runs2:
            gaps = np.diff(runs2) / fs2 * 1000
            near96 = [g for g in gaps if 90 < g < 105]
            print(f"    thr={frac*100:.0f}% of p50: {len(runs2)} dips, "
                  f"{len(near96)} near 96ms")


if __name__ == "__main__":
    main()
