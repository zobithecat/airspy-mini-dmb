"""Deep RF probe — try every airspy gain combination and look for DAB
null-symbol periodicity at multiple detection thresholds.

The Retevis-style AGC was likely fighting with wideband VHF noise,
folding the gain back when the actual DAB signal was the small bump
it was trying to *amplify*. Manual LNA/Mixer/VGA gives finer control.

For each (LNA, Mixer, VGA) point:
  - capture 1.5 s of I/Q at 6 MSPS
  - decimate to 2.048 MSPS with proper anti-alias
  - sweep null-detection threshold 0.50 → 0.90 of mean envelope
  - record any threshold where >=3 dips are 96 ms apart (DAB Mode I)
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from numpy.fft import fft, fftshift

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from tdmb.channels import by_name


def capture_iq(freq_hz: int, gains: dict, samples: int, path: str = "/tmp/probe_iq.bin") -> bool:
    """Capture I/Q with explicit LNA/Mixer/VGA gains (or 'h' for sensitivity)."""
    fs = 6_000_000
    args = ["airspy_rx", "-r", path, "-f", str(freq_hz / 1e6),
            "-a", str(fs), "-t", "2", "-n", str(samples)]
    if "h" in gains:
        args += ["-h", str(gains["h"])]
    if "l" in gains:
        args += ["-l", str(gains["l"])]
    if "m" in gains:
        args += ["-m", str(gains["m"])]
    if "v" in gains:
        args += ["-v", str(gains["v"])]
    r = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    return os.path.exists(path) and os.path.getsize(path) >= samples * 2


def analyse(iq_path: str, fs: float = 6e6) -> dict:
    raw = np.fromfile(iq_path, dtype="<i2").astype(np.float32) / 32768.0
    iq = raw[::2] + 1j * raw[1::2]
    if len(iq) < 200_000:
        return {"ok": False}

    # PSD over 1.5 MHz center vs edges
    nfft = 32768
    seg = iq[:nfft] * np.hanning(nfft)
    S = np.abs(fftshift(fft(seg))) ** 2
    band_bins = int(0.768 / (fs / 2 / 1e6) * nfft / 2)
    center_db = 10 * np.log10(np.mean(S[nfft // 2 - band_bins : nfft // 2 + band_bins]) + 1e-12)
    edge_db = 10 * np.log10(np.mean(np.r_[S[: nfft // 10], S[-nfft // 10 :]]) + 1e-12)

    # Decimate to 2.048 MSPS using polyphase
    from scipy.signal import resample_poly
    iq2 = resample_poly(iq, 256, 750)
    fs2 = fs * 256 / 750
    env = np.abs(iq2)
    W = 256
    sm = np.convolve(env, np.ones(W) / W, mode="same")
    mu = float(np.mean(sm))

    # Sweep multiple thresholds
    null_results = {}
    for thr in (0.50, 0.60, 0.70, 0.80, 0.85, 0.90):
        mask = sm < thr * mu
        runs = []
        in_dip, start = False, 0
        for i, m in enumerate(mask):
            if m and not in_dip:
                start, in_dip = i, True
            elif not m and in_dip:
                if i - start > 1500:  # > 0.7 ms (null = 1.3 ms)
                    runs.append(start)
                in_dip = False
        if len(runs) >= 3:
            gaps = np.diff(runs) / fs2 * 1000.0  # ms
            near96 = gaps[(gaps > 85) & (gaps < 110)]
            if near96.size >= 2:
                null_results[thr] = (len(near96), float(np.median(near96)))

    return {
        "ok": True,
        "center_db": center_db,
        "edge_db": edge_db,
        "delta_db": center_db - edge_db,
        "envelope_mean": mu,
        "envelope_min_pct": float(np.min(sm) / mu) if mu > 0 else 1.0,
        "null_per_thr": null_results,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="K8B")
    ap.add_argument("--samples", type=int, default=9_000_000, help="I/Q samples per attempt (default 1.5s)")
    args = ap.parse_args()

    ch = by_name(args.channel.lstrip("K"))
    print(f"\n  deep probe: {args.channel} @ {ch.freq_hz/1e6:.3f} MHz, {args.samples/6e6:.1f}s per attempt\n")

    # Strategy 1: simplified sensitivity gain sweep
    print("=== A. sensitivity gain sweep (-h) ===")
    print(f"  {'gain':>4}  {'Δ(PSD)':>8}  {'env-min%':>8}  {'null pattern':<40}")
    best = (None, -1e9)
    for h in [4, 8, 12, 16, 18, 20, 21]:
        if not capture_iq(ch.freq_hz, {"h": h}, args.samples): continue
        r = analyse("/tmp/probe_iq.bin")
        if not r["ok"]: continue
        nulls = ""
        for thr, (cnt, ms) in sorted(r["null_per_thr"].items()):
            nulls += f"thr={thr:.2f}:{cnt} ({ms:.0f}ms)  "
        if not nulls: nulls = "—"
        print(f"  h={h:>2}    {r['delta_db']:+5.1f} dB  {r['envelope_min_pct']*100:>5.1f}%   {nulls}", flush=True)
        score = r['delta_db'] + (10 if r['null_per_thr'] else 0)
        if score > best[1]: best = ((f"h={h}", r), score)

    # Strategy 2: linearity gain sweep
    print("\n=== B. linearity gain sweep (-g implies different distribution) ===")
    print(f"  {'gain':>4}  {'Δ(PSD)':>8}  {'env-min%':>8}  {'null pattern':<40}")
    for g in [4, 10, 14, 18, 21]:
        if not capture_iq(ch.freq_hz, {"l": min(14, g), "m": min(15, g), "v": min(15, g)}, args.samples):
            continue
        r = analyse("/tmp/probe_iq.bin")
        if not r["ok"]: continue
        nulls = ""
        for thr, (cnt, ms) in sorted(r["null_per_thr"].items()):
            nulls += f"thr={thr:.2f}:{cnt} ({ms:.0f}ms)  "
        if not nulls: nulls = "—"
        print(f"  L={min(14,g)} M={min(15,g)} V={min(15,g)}: {r['delta_db']:+5.1f} dB  {r['envelope_min_pct']*100:>5.1f}%   {nulls}", flush=True)

    # Strategy 3: targeted "low-IF, high-LNA" combos for weak signals
    print("\n=== C. targeted LNA/Mixer/VGA combos ===")
    print(f"  {'LMV':>10}  {'Δ(PSD)':>8}  {'env-min%':>8}  {'null pattern':<40}")
    combos = [
        (14, 8, 8),   # max LNA, low post = good NF
        (14, 12, 8),  # max LNA, mid mixer
        (14, 14, 5),  # max LNA, max mixer, low VGA (avoid clip)
        (14, 14, 10),
        (14, 14, 15), # all max — likely overload
        (10, 10, 10),
        (8, 14, 12),  # lower LNA, more mixer
        (4, 12, 14),  # weak front-end, strong post
    ]
    for L, M, V in combos:
        if not capture_iq(ch.freq_hz, {"l": L, "m": M, "v": V}, args.samples): continue
        r = analyse("/tmp/probe_iq.bin")
        if not r["ok"]: continue
        nulls = ""
        for thr, (cnt, ms) in sorted(r["null_per_thr"].items()):
            nulls += f"thr={thr:.2f}:{cnt} ({ms:.0f}ms)  "
        if not nulls: nulls = "—"
        tag = f"L{L} M{M} V{V}"
        print(f"  {tag:>10}: {r['delta_db']:+5.1f} dB  {r['envelope_min_pct']*100:>5.1f}%   {nulls}", flush=True)

    print(f"\n  best so far: {best[0]} (score {best[1]:.1f})\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
