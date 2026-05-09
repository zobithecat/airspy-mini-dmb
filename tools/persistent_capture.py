"""Keep retrying eti-cmdline-airspy until it actually locks + captures.

Korean T-DMB on a marginal antenna fades in and out (Δ varies 5–12 dB
over seconds). One eti-cmdline run gives up after `-d` seconds; this
script just keeps relaunching until we get a non-empty ETI file with
a valid ensemble.
"""
from __future__ import annotations
import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from tdmb.eti import parse_frame, FRAME_SIZE
from tdmb.eti.fic import FicAccumulator

ETI_BIN = REPO / "eti-stuff" / "eti-cmdline" / "build" / "eti-cmdline-airspy"
LIBPATH = "/opt/homebrew/lib"


def run_one(channel: str, gain: int, detect_s: int, dwell_s: int, out_path: Path) -> bool:
    if not channel.startswith("K"):
        channel = "K" + channel
    out_path.unlink(missing_ok=True)
    env = os.environ.copy()
    env["DYLD_LIBRARY_PATH"] = LIBPATH + ":" + env.get("DYLD_LIBRARY_PATH", "")
    proc = subprocess.Popen(
        [str(ETI_BIN), "-C", channel, "-G", str(gain),
         "-d", str(detect_s), "-D", str(detect_s), "-t", str(dwell_s),
         "-O", str(out_path), "-J"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env=env, text=True,
    )
    locked = False
    try:
        assert proc.stderr is not None
        for line in proc.stderr:
            if "ensemble" in line and "detected" in line:
                locked = True
                print(f"      → ensemble locked: {line.strip()}", flush=True)
            if "Further waiting" in line:
                break
        proc.wait(timeout=2)
    except Exception:
        pass
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try: proc.wait(timeout=4)
            except subprocess.TimeoutExpired: proc.kill()
    return locked and out_path.exists() and out_path.stat().st_size > 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="K8B")
    ap.add_argument("--gain", type=int, default=0)
    ap.add_argument("--detect", type=int, default=20)
    ap.add_argument("--dwell", type=int, default=60)
    ap.add_argument("--retries", type=int, default=10)
    ap.add_argument("--out", type=Path, default=REPO / "data" / "live.eti")
    args = ap.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)

    print(f"\n  persistent capture: {args.channel} gain={args.gain} retries={args.retries}\n")
    for i in range(args.retries):
        print(f"  [{i+1}/{args.retries}] launching eti-cmdline ({args.detect}s detect, {args.dwell}s dwell)…",
              flush=True)
        ok = run_one(args.channel, args.gain, args.detect, args.dwell, args.out)
        size = args.out.stat().st_size if args.out.exists() else 0
        if ok and size > 100_000:
            print(f"  ✓ captured {size//1024} KB → analyzing")
            # quick FIC analysis
            fic = FicAccumulator()
            n = 0
            with args.out.open("rb") as f:
                while True:
                    chunk = f.read(FRAME_SIZE)
                    if len(chunk) != FRAME_SIZE: break
                    try: fr = parse_frame(chunk)
                    except: continue
                    n += 1
                    if fr.fic_present and fr.fic:
                        fic.feed_fic(fr.fic)
            pct = 100 * fic.fib_ok / fic.fib_total if fic.fib_total else 0
            ens = fic.ensemble
            print(f"\n  RESULTS:")
            print(f"    file: {args.out} ({size//1024} KB, {n} ETI frames)")
            print(f"    FIB pass-rate: {pct:.1f}%")
            print(f"    Ensemble: {ens.label}  EId=0x{(ens.eid or 0):04X}")
            print(f"    Services ({len(ens.services)}):")
            for sid, svc in sorted(ens.services.items()):
                kind = "data" if svc.is_data else "audio"
                print(f"      {sid:08X}  {kind:5s}  {svc.label or '(no label)'}")
            return 0
        else:
            print(f"      capture failed (size={size}); retry…")
            subprocess.run(["pkill", "-9", "-f", "eti-cmdline"], capture_output=True)
            time.sleep(2)

    print(f"\n  ✗ all {args.retries} retries failed — signal currently below sync threshold")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
