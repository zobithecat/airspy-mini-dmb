"""Sweep every Korean T-DMB channel until ensembles are found.

For each channel: spawn eti-cmdline-airspy briefly, watch stderr for the
"there might be a DAB signal here" line. If sync succeeds, let it run a bit
longer to dump ensemble JSON, then move on.

    python tools/scan.py                       # full Band III sweep
    python tools/scan.py --channels K12A K8B   # subset
    python tools/scan.py --gain 14 --detect 25 # tweak knobs
"""
from __future__ import annotations
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from tdmb.channels import all_korea_band3, by_name   # noqa: E402

BIN = REPO / "eti-stuff" / "eti-cmdline" / "build" / "eti-cmdline-airspy"
ENV_LIBPATH = "/opt/homebrew/lib"

SYNC_OK = "there might be a DAB signal here"
SYNC_FAIL = "There does not seem to be a DAB signal here"


def run_one(ch_name: str, gain: int, detect_s: int, dwell_s: int, outdir: Path) -> dict:
    if not ch_name.startswith("K"):
        ch_name = "K" + ch_name
    json_path = REPO / f"ensemble-ch-{ch_name}.json"
    if json_path.exists():
        json_path.unlink()
    eti_path = outdir / f"{ch_name}.eti"

    argv = [
        str(BIN),
        "-C", ch_name,
        "-G", str(gain),
        "-d", str(detect_s),     # OFDM time-sync timeout (default 5)
        "-D", str(detect_s),     # frequency-sync / ensemble-info collection time
        "-t", str(dwell_s),
        "-J",
        "-O", str(eti_path),
    ]
    env = os.environ.copy()
    env["DYLD_LIBRARY_PATH"] = ENV_LIBPATH + ":" + env.get("DYLD_LIBRARY_PATH", "")

    t0 = time.time()
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    locked = False
    last_lines: list[str] = []
    try:
        assert proc.stderr is not None
        while True:
            line = proc.stderr.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            line = line.rstrip()
            last_lines.append(line)
            if len(last_lines) > 8:
                last_lines.pop(0)
            if SYNC_OK in line:
                locked = True
            if SYNC_FAIL in line:
                break
            # Cut off if dwell exceeded
            if time.time() - t0 > detect_s + dwell_s + 5:
                break
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()

    elapsed = time.time() - t0
    info = {"channel": ch_name, "locked": locked, "elapsed_s": round(elapsed, 1)}
    if json_path.exists():
        try:
            info["ensemble"] = json.loads(json_path.read_text())
        except Exception:
            info["ensemble"] = "<unparseable json>"
        # move into outdir for later inspection
        json_path.replace(outdir / json_path.name)
    if not info.get("ensemble"):
        info["last_stderr"] = last_lines[-3:]
    return info


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channels", nargs="*", default=None,
                    help="explicit channel list (default: full Band III sweep)")
    ap.add_argument("--gain", type=int, default=18)
    ap.add_argument("--detect", type=int, default=20, help="seconds to wait for time sync")
    ap.add_argument("--dwell", type=int, default=8, help="extra seconds after lock to dump ensemble")
    ap.add_argument("--outdir", type=Path, default=REPO / "data" / "scan")
    args = ap.parse_args()

    if args.channels:
        chans = args.channels
    else:
        # Try metro favourites first, then the rest of Band III.
        priority = ["K12A", "K12B", "K12C", "K8B"]
        rest = [c.name if c.name.startswith("K") else "K" + c.name
                for c in all_korea_band3()]
        chans = priority + [c for c in rest if c not in priority]

    args.outdir.mkdir(parents=True, exist_ok=True)
    print(f"sweep {len(chans)} channels  gain={args.gain}  detect={args.detect}s  dwell={args.dwell}s")
    print(f"output: {args.outdir}")
    print("=" * 64)

    summary = []
    for ch in chans:
        try:
            freq = by_name(ch.lstrip("K")).freq_hz / 1e6
            print(f"{ch:>5s}  {freq:7.3f} MHz  ... ", end="", flush=True)
        except KeyError:
            print(f"{ch}: unknown channel"); continue
        info = run_one(ch, args.gain, args.detect, args.dwell, args.outdir)
        summary.append(info)
        if info["locked"]:
            ens = info.get("ensemble")
            if isinstance(ens, dict):
                ens_label = ens.get("ensemble") or ens.get("Ensemble") or "?"
                print(f"LOCK  ({info['elapsed_s']}s)  {ens_label}")
            else:
                print(f"LOCK  ({info['elapsed_s']}s)  (ensemble json missing)")
        else:
            tail = info.get("last_stderr", [])
            tag = tail[-1] if tail else "no signal"
            print(f"miss  ({info['elapsed_s']}s)  {tag[:60]}")

    print("=" * 64)
    locked = [s for s in summary if s["locked"]]
    print(f"\nlocked on {len(locked)}/{len(summary)} channels:")
    for s in locked:
        ens = s.get("ensemble")
        if isinstance(ens, dict):
            print(f"  {s['channel']}: {ens}")
        else:
            print(f"  {s['channel']}")
    return 0 if locked else 2


if __name__ == "__main__":
    raise SystemExit(main())
