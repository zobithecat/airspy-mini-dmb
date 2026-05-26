"""Sweep AIRSPY_LNA / AIRSPY_MIXER / AIRSPY_VGA on a channel and report
how each combo fares: ofdm-sync? freq-sync? FIB rate?

Usage:
  python tools/gain_sweep.py --channel K12C --secs 20

Strategy: try LNA=14 (max for NF), sweep M and V coarsely.
For finer search add --fine.
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
ETI_BIN = REPO / "eti-stuff" / "eti-cmdline" / "build" / "eti-cmdline-airspy"
LIBPATH = "/opt/homebrew/lib"

FIB_RX = re.compile(r"fib quality:\s*([\d.]+)")
ENS_OK_RX = re.compile(r"ensemble.*name|EId.*0x[0-9a-fA-F]")
NULL_RX = re.compile(r"DAB signal here")
SYNC_RX = re.compile(r"frame sync|ensembleId")


def run_combo(channel: str, lna: int, mix: int, vga: int, secs: int, bias: bool) -> dict:
    env = os.environ.copy()
    env["DYLD_LIBRARY_PATH"] = LIBPATH
    env["AIRSPY_LNA"] = str(lna)
    env["AIRSPY_MIXER"] = str(mix)
    env["AIRSPY_VGA"] = str(vga)
    out_path = f"/tmp/sweep_{lna}_{mix}_{vga}.eti"
    cmd = [str(ETI_BIN), "-C", channel,
           "-d", str(secs), "-D", str(secs), "-t", str(secs),
           "-O", out_path, "-J"]
    if bias:
        cmd.append("-b")
    t0 = time.time()
    p = subprocess.Popen(cmd, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = p.communicate(timeout=secs + 20)
    elapsed = time.time() - t0
    blob = (out or b"") + (err or b"")
    txt = blob.decode("utf-8", "replace")
    sig = "DAB signal here" in txt and "There does not" not in txt
    freq_sync = "ensembleId" in txt or "frame sync" in txt or "FIB" in txt
    size = os.path.getsize(out_path) if os.path.exists(out_path) else 0
    fib = 0.0
    m = FIB_RX.search(txt)
    if m:
        fib = float(m.group(1))
    return {
        "L": lna, "M": mix, "V": vga,
        "size": size,
        "signal_seen": sig,
        "freq_sync": freq_sync,
        "fib_pct": fib,
        "elapsed": round(elapsed, 1),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="K12C")
    ap.add_argument("--secs", type=int, default=15)
    ap.add_argument("--bias", action="store_true")
    ap.add_argument("--fine", action="store_true", help="denser sweep")
    args = ap.parse_args()

    if args.fine:
        L_list = [10, 12, 14]
        M_list = [5, 8, 10, 12, 15]
        V_list = [6, 10, 13, 15]
    else:
        L_list = [10, 12, 14]
        M_list = [5, 10, 15]
        V_list = [8, 12, 15]

    n_total = len(L_list) * len(M_list) * len(V_list)
    print(f"sweep {n_total} combos on {args.channel}, {args.secs}s each "
          f"(~{n_total*args.secs/60:.1f} min total)")
    print(f"{'L':>3} {'M':>3} {'V':>3}  {'size':>10}  {'sig':>4} {'sync':>5} {'fib%':>5}  {'sec':>4}")
    print("-" * 50)
    results = []
    i = 0
    for L in L_list:
        for M in M_list:
            for V in V_list:
                i += 1
                r = run_combo(args.channel, L, M, V, args.secs, args.bias)
                results.append(r)
                sig_s = "Y" if r["signal_seen"] else "."
                sync_s = "Y" if r["freq_sync"] else "."
                print(f"{r['L']:>3} {r['M']:>3} {r['V']:>3}  "
                      f"{r['size']:>10,}  {sig_s:>4} {sync_s:>5} "
                      f"{r['fib_pct']:>5.1f}  {r['elapsed']:>4.1f}  "
                      f"({i}/{n_total})", flush=True)

    # Rank
    print("\n--- top 5 by capture size ---")
    for r in sorted(results, key=lambda x: x["size"], reverse=True)[:5]:
        print(f"  L={r['L']:>2} M={r['M']:>2} V={r['V']:>2}  "
              f"{r['size']:>10,} bytes  fib={r['fib_pct']:.1f}%  "
              f"sync={r['freq_sync']}")

    best = max(results, key=lambda x: (x["size"], x["freq_sync"], x["fib_pct"]))
    print(f"\nbest: L={best['L']} M={best['M']} V={best['V']} → "
          f"{best['size']:,} bytes, fib={best['fib_pct']:.1f}%")


if __name__ == "__main__":
    main()
