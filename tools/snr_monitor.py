"""Real-time SNR / signal-strength monitor for a Korean T-DMB channel.

Continuously samples the Airspy at the chosen frequency and prints:
  - in-band power vs out-of-band power (rough SNR estimate)
  - per-second peak hold
  - DAB null-symbol detection (96 ms periodicity = real DAB signal)
  - color bar visualisation

Use this while moving / rotating the antenna to find the best position.

    python tools/snr_monitor.py --channel K8B
    python tools/snr_monitor.py --freq 183.008
"""
from __future__ import annotations
import argparse
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from numpy.fft import fft, fftshift

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from tdmb.channels import by_name


def measure_psd(freq_hz: int, gain: int = 14, samples: int = 6_000_000):
    """Capture 1 second of I/Q and return (in_band_db, edge_db, delta_db, null_period_ms)."""
    iq_path = "/tmp/snr_iq.bin"
    fs = 6_000_000
    # Use airspy_rx to capture (1 second = 6 M samples at 6 MSPS)
    subprocess.run(
        ["airspy_rx", "-r", iq_path, "-f", str(freq_hz / 1e6),
         "-a", str(fs), "-h", str(gain),
         "-t", "2", "-n", str(samples)],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if not os.path.exists(iq_path) or os.path.getsize(iq_path) < 1000:
        return None
    raw = np.fromfile(iq_path, dtype="<i2").astype(np.float32) / 32768.0
    iq = raw[::2] + 1j * raw[1::2]
    # use full second for null detection, but shorter for PSD
    seg = iq[:200_000]

    # PSD: in-band = central 1.536 MHz, edges = outer 10%
    nfft = 32768
    seg_w = seg[:nfft] * np.hanning(nfft)
    S = np.abs(fftshift(fft(seg_w))) ** 2
    band = int(0.768 / (fs / 2 / 1e6) * nfft / 2)  # 1.536/2 MHz in bins
    center_pwr = 10 * np.log10(np.mean(S[nfft // 2 - band : nfft // 2 + band]) + 1e-12)
    edge_pwr = 10 * np.log10(np.mean(np.r_[S[: nfft // 10], S[-nfft // 10 :]]) + 1e-12)
    delta_db = center_pwr - edge_pwr

    # Null-symbol detection (DAB Mode I has 96 ms transmission frame
    # with a ~1.297 ms null at the start). Decimate the FULL captured
    # second to ~2 MSPS, look for envelope dips below threshold of mean
    # separated by ~96 ms.
    try:
        from scipy.signal import resample_poly
        # use whole capture (1 second @ 6 MSPS) for null detection
        iq_full = iq[:samples]
        iq2 = resample_poly(iq_full, 256, 750)  # 6 MSPS → ~2.048 MSPS
        fs2 = fs * 256 / 750
        env = np.abs(iq2)
        W = 256
        sm = np.convolve(env, np.ones(W) / W, mode="same")
        mu = np.mean(sm)
        if mu < 1e-6:
            null_period_ms = float("nan")
        else:
            # look for the deepest dips — DAB null drops to ~30% of average
            # but with low SNR the dip is shallower. Try 80% threshold.
            mask = sm < 0.8 * mu
            runs = []
            in_dip, start = False, 0
            for i, m in enumerate(mask):
                if m and not in_dip:
                    start, in_dip = i, True
                elif not m and in_dip:
                    if i - start > 1500:  # > 0.7 ms (null is ~1.3 ms)
                        runs.append(start)
                    in_dip = False
            if len(runs) >= 3:
                gaps = np.diff(runs) / fs2 * 1000  # ms
                near96 = gaps[(gaps > 85) & (gaps < 110)]
                # need at least 2 valid 96-ms gaps for a real lock
                if near96.size >= 2:
                    null_period_ms = float(np.median(near96))
                else:
                    null_period_ms = float("nan")
            else:
                null_period_ms = float("nan")
    except ImportError:
        null_period_ms = float("nan")

    return center_pwr, edge_pwr, delta_db, null_period_ms


def bar(value: float, lo: float = 0, hi: float = 30, width: int = 40) -> str:
    """ANSI color bar."""
    if value != value:  # NaN
        return " " * width
    v = max(lo, min(hi, value))
    n = int((v - lo) / (hi - lo) * width)
    if value < 5:
        color = "\033[31m"  # red
    elif value < 10:
        color = "\033[33m"  # yellow
    elif value < 14:
        color = "\033[36m"  # cyan
    else:
        color = "\033[32m"  # green
    reset = "\033[0m"
    return color + "█" * n + " " * (width - n) + reset


def main() -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--channel", help="Korean channel (e.g. K8B)")
    src.add_argument("--freq", type=float, help="frequency in MHz")
    ap.add_argument("--gain", type=int, default=14)
    ap.add_argument("--interval", type=float, default=1.5,
                    help="seconds between measurements (longer = more samples)")
    args = ap.parse_args()

    if args.channel:
        ch = by_name(args.channel.lstrip("K"))
        freq_hz = ch.freq_hz
        label = args.channel
    else:
        freq_hz = int(args.freq * 1e6)
        label = f"{args.freq} MHz"

    print(f"\n  monitoring {label} ({freq_hz/1e6:.3f} MHz) — Ctrl-C to stop\n")
    print(f"  Δ ≥ 12 dB and null period ≈ 96 ms means lockable T-DMB signal\n")

    peak_delta = 0.0
    peak_null_count = 0
    n_locks = 0  # times null pattern detected
    t0 = time.time()
    try:
        while True:
            r = measure_psd(freq_hz, gain=args.gain)
            if r is None:
                print("  ⚠ airspy_rx returned no data", flush=True)
                time.sleep(1)
                continue
            center, edge, delta, null_ms = r
            peak_delta = max(peak_delta, delta)
            null_ok = (null_ms == null_ms) and (90 < null_ms < 105)
            if null_ok:
                n_locks += 1

            elapsed = time.time() - t0
            null_str = f"{null_ms:5.1f} ms" if null_ms == null_ms else "  ---  "
            lock = "🟢 DAB LOCK" if null_ok else "    ---   "
            print(f"  {elapsed:5.0f}s   Δ={delta:+5.1f} dB  peak={peak_delta:+5.1f}  "
                  f"null={null_str}  {lock}  |{bar(delta)}|",
                  flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n\n  best Δ: {peak_delta:+.1f} dB,  DAB-pattern detections: {n_locks}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
