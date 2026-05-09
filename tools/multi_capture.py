"""Capture every Korean Band III channel, measure FIB quality + ensemble.

For each of the 21 channels:
  1. Run eti-cmdline-airspy with AGC for `--time` seconds
  2. Parse ETI → FIB pass rate, ensemble, services
  3. Save .eti file under data/multi/<CH>.eti for later use

Output: data/multi/summary.txt with ranked results.
"""
from __future__ import annotations
import argparse
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from tdmb.channels import all_korea_band3, by_name
from tdmb.eti import parse_frame, FRAME_SIZE
from tdmb.eti.fic import FicAccumulator

ETI_BIN = REPO / "eti-stuff" / "eti-cmdline" / "build" / "eti-cmdline-airspy"
LIBPATH = "/opt/homebrew/lib"


def capture(channel: str, gain: int, detect_s: int, dwell_s: int, out_path: Path) -> dict:
    if not channel.startswith("K"):
        channel = "K" + channel
    env = os.environ.copy()
    env["DYLD_LIBRARY_PATH"] = LIBPATH + ":" + env.get("DYLD_LIBRARY_PATH", "")
    proc = subprocess.Popen(
        [str(ETI_BIN), "-C", channel, "-G", str(gain),
         "-d", str(detect_s), "-D", str(detect_s), "-t", str(dwell_s),
         "-O", str(out_path), "-J"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env=env, text=True,
    )
    fib_qualities = []
    snrs = []
    detected_at = None
    t0 = time.time()
    deadline = t0 + detect_s + dwell_s + 10
    try:
        assert proc.stderr is not None
        for line in proc.stderr:
            t = time.time()
            if t > deadline: break
            if "ensemble" in line and "detected" in line:
                detected_at = round(t - t0, 1)
            m = re.search(r"fibquality\s+(\d+)", line)
            if m: fib_qualities.append(int(m.group(1)))
            m = re.search(r"estimated snr:\s*(\d+)", line)
            if m: snrs.append(int(m.group(1)))
            if "Further waiting does not seem useful" in line:
                break
    except Exception:
        pass
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill()
    return {
        "channel": channel,
        "size": out_path.stat().st_size if out_path.exists() else 0,
        "detected_at": detected_at,
        "fib_min": min(fib_qualities) if fib_qualities else None,
        "fib_max": max(fib_qualities) if fib_qualities else None,
        "fib_avg": round(sum(fib_qualities) / len(fib_qualities), 1) if fib_qualities else None,
        "snr_avg": round(sum(snrs) / len(snrs), 1) if snrs else None,
        "elapsed": round(time.time() - t0, 1),
    }


def analyze_eti(path: Path) -> dict:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    fic = FicAccumulator()
    n_frames = 0
    with path.open("rb") as f:
        while True:
            chunk = f.read(FRAME_SIZE)
            if len(chunk) != FRAME_SIZE: break
            try:
                fr = parse_frame(chunk)
            except Exception:
                continue
            n_frames += 1
            if fr.fic_present and fr.fic:
                fic.feed_fic(fr.fic)
    return {
        "frames": n_frames,
        "fib_pass_pct": round(100 * fic.fib_ok / fic.fib_total, 1) if fic.fib_total else 0,
        "ensemble": fic.ensemble.label,
        "eid": fic.ensemble.eid,
        "services": [(svc.label, sid) for sid, svc in fic.ensemble.services.items()],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--time", type=int, default=20, help="seconds per channel")
    ap.add_argument("--detect", type=int, default=20)
    ap.add_argument("--gain", type=int, default=0, help="0 = AGC")
    ap.add_argument("--channels", nargs="*", default=None,
                    help="explicit list, default = all 21 Korean channels")
    ap.add_argument("--outdir", type=Path, default=REPO / "data" / "multi")
    args = ap.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    if args.channels:
        chans = args.channels
    else:
        chans = ["K" + c.name for c in all_korea_band3()]

    print(f"\n  scanning {len(chans)} channels — gain={args.gain} ({'AGC' if args.gain == 0 else 'manual'}), {args.time}s each\n")
    print(f"  {'CH':<5}  {'freq':>9}  {'lock':>7}  {'size':>8}  {'FIB%':>6}  {'frames':>6}  {'ensemble':<20}  services")
    print(f"  {'-'*5}  {'-'*9}  {'-'*7}  {'-'*8}  {'-'*6}  {'-'*6}  {'-'*20}  {'-'*30}")

    results = []
    # Kill any leftover eti-cmdline before starting
    subprocess.run(["pkill", "-9", "-f", "eti-cmdline"], capture_output=True)
    time.sleep(1)
    for name in chans:
        try:
            ch = by_name(name.lstrip("K"))
            freq_mhz = ch.freq_hz / 1e6
        except KeyError:
            print(f"  {name}: unknown")
            continue
        out_path = args.outdir / f"{name}.eti"
        cap = capture(name, args.gain, args.detect, args.time, out_path)
        an = analyze_eti(out_path) if cap["size"] > 0 else {}
        lock = "✓" if (cap.get("detected_at") is not None) else "✗"
        ensemble = an.get("ensemble") or "-"
        n_svc = len(an.get("services") or [])
        fib_pct = an.get("fib_pass_pct", 0)
        frames = an.get("frames", 0)
        size_kb = cap["size"] // 1024
        svc_names = ", ".join(s[0] for s in (an.get("services") or [])[:5])
        print(f"  {name:<5}  {freq_mhz:>7.3f}M  {lock:>7}  {size_kb:>6} KB  {fib_pct:>5.1f}%  {frames:>6}  {ensemble:<20}  {svc_names}",
              flush=True)
        results.append({**cap, **an, "freq_mhz": freq_mhz, "ensemble_label": ensemble, "n_services": n_svc})
        # Always allow USB/airspy to settle between captures
        subprocess.run(["pkill", "-9", "-f", "eti-cmdline"], capture_output=True)
        time.sleep(2)

    print(f"\n  Top by FIB pass-rate:")
    locked = [r for r in results if r.get("fib_pass_pct", 0) > 0]
    locked.sort(key=lambda r: -r.get("fib_pass_pct", 0))
    for r in locked[:5]:
        print(f"    {r['channel']:<6} {r['freq_mhz']:.3f} MHz  FIB {r.get('fib_pass_pct',0):>5.1f}%  "
              f"{r.get('ensemble_label','-')}  ({r.get('n_services',0)} services)")

    summary = args.outdir / "summary.txt"
    with summary.open("w") as f:
        f.write(f"# Korean T-DMB multi-capture: {time.strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"# gain={args.gain}, time={args.time}s\n\n")
        for r in sorted(results, key=lambda r: -r.get("fib_pass_pct", 0)):
            f.write(f"{r['channel']}\t{r['freq_mhz']:.3f}\t{r.get('fib_pass_pct',0)}\t"
                    f"{r.get('ensemble_label','-')}\t{r.get('n_services',0)}\n")
    print(f"\n  summary: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
