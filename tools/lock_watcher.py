"""Watch a Korean T-DMB channel; when DAB null pattern (96 ms) appears,
fire off eti-cmdline immediately to catch the favourable fading moment.

Marginal-antenna captures only succeed when multipath fading lines up
right. Polling the PSD passively + null detector lets us *react* to
those moments instead of blindly hammering eti-cmdline.

  python tools/lock_watcher.py --channel K8B --window 4 --captures 5
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

from tdmb.channels import by_name
from snr_monitor import measure_psd       # type: ignore  # tools/ in path
sys.path.insert(0, str(REPO / "tools"))
from snr_monitor import measure_psd       # noqa: F811

ETI_BIN = REPO / "eti-stuff" / "eti-cmdline" / "build" / "eti-cmdline-airspy"
LIBPATH = "/opt/homebrew/lib"


def burst_capture(channel: str, lna: int, mixer: int, vga: int,
                  detect: int, dwell: int, out_path: Path) -> dict:
    """One eti-cmdline run. Returns dict with results."""
    if not channel.startswith("K"):
        channel = "K" + channel
    out_path.unlink(missing_ok=True)
    env = os.environ.copy()
    env["DYLD_LIBRARY_PATH"] = LIBPATH + ":" + env.get("DYLD_LIBRARY_PATH", "")
    env["AIRSPY_LNA"] = str(lna)
    env["AIRSPY_MIXER"] = str(mixer)
    env["AIRSPY_VGA"] = str(vga)
    proc = subprocess.Popen(
        [str(ETI_BIN), "-C", channel, "-G", "1",
         "-d", str(detect), "-D", str(detect), "-t", str(dwell),
         "-O", str(out_path), "-J"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env=env, text=True, bufsize=1,
    )
    locked = False
    fib = []
    services = []
    try:
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip()
            if "ensemble" in line and "detected" in line:
                locked = True
                print(f"        ✓ {line.strip()}", flush=True)
            if "program" in line and "is in the list" in line:
                services.append(line.split("program")[1].split("is in")[0].strip())
            if "Further waiting" in line:
                break
            import re
            m = re.search(r"fibquality\s+(\d+)", line)
            if m: fib.append(int(m.group(1)))
        proc.wait(timeout=2)
    except Exception:
        pass
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try: proc.wait(timeout=4)
            except subprocess.TimeoutExpired: proc.kill()
    return {
        "locked": locked,
        "size": out_path.stat().st_size if out_path.exists() else 0,
        "fib_max": max(fib) if fib else 0,
        "fib_avg": (sum(fib) // len(fib)) if fib else 0,
        "n_services": len(services),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="K8B")
    ap.add_argument("--window", type=int, default=8,
                    help="seconds between PSD probes")
    ap.add_argument("--captures", type=int, default=10,
                    help="how many successful captures to grab before exiting")
    ap.add_argument("--lna", type=int, default=14)
    ap.add_argument("--mixer", type=int, default=14)
    ap.add_argument("--vga", type=int, default=10)
    ap.add_argument("--outdir", type=Path, default=REPO / "data" / "watched")
    ap.add_argument("--max-attempts", type=int, default=60)
    args = ap.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)
    ch = by_name(args.channel.lstrip("K"))
    print(f"\n  watching {args.channel} @ {ch.freq_hz/1e6:.3f} MHz "
          f"(L={args.lna} M={args.mixer} V={args.vga})\n")
    print(f"  trigger:  null period = 90–110 ms (DAB Mode I), Δ ≥ 8 dB\n")

    n_caps = 0
    n_attempts = 0
    best_fib = 0
    while n_caps < args.captures and n_attempts < args.max_attempts:
        n_attempts += 1
        r = measure_psd(ch.freq_hz, gain=14)
        if not r:
            print(f"  [{n_attempts}] PSD probe failed", flush=True)
            time.sleep(args.window)
            continue
        c, e, d, n = r
        nstr = f"{n:.0f} ms" if n == n else "—"
        good = (n == n) and (90 < n < 110) and d > 7
        prefix = "🟢 LOCK candidate" if good else "                 "
        print(f"  [{n_attempts:>2}] {prefix}  Δ={d:+5.1f} dB  null={nstr}", flush=True)
        if not good:
            time.sleep(args.window)
            continue
        # Pull eti-cmdline now
        out_path = args.outdir / f"{args.channel}_{int(time.time())}.eti"
        print(f"        🎯 firing eti-cmdline → {out_path.name}")
        info = burst_capture(args.channel, args.lna, args.mixer, args.vga,
                             detect=15, dwell=30, out_path=out_path)
        if info["locked"] and info["size"] > 100_000:
            n_caps += 1
            best_fib = max(best_fib, info["fib_max"])
            print(f"        ✅ captured {info['size']//1024} KB, "
                  f"FIB max={info['fib_max']}%, services={info['n_services']}",
                  flush=True)
        else:
            print(f"        ✗ capture failed ({info})")
            out_path.unlink(missing_ok=True)
        # always settle Airspy USB
        subprocess.run(["pkill", "-9", "-f", "eti-cmdline"], capture_output=True)
        time.sleep(2)

    print(f"\n  ===== summary =====")
    print(f"  total attempts: {n_attempts}")
    print(f"  successful captures: {n_caps}")
    print(f"  best FIB: {best_fib}%")
    return 0 if n_caps > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
