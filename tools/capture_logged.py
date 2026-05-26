"""Capture a T-DMB ETI file with full metadata sidecar.

Wraps `eti-cmdline-airspy` and writes a JSON metadata file alongside the
`.eti` capture, recording every parameter that matters for reproducibility:

  - RF: channel, frequency, sample rate, gain mode + values, bias-tee
  - Environment: antenna model, indoor/outdoor location, city, date/time
  - SDR: device serial, firmware version
  - Capture results: file size, duration, OFDM lock achieved?

Output:
    data/long_captures/<tag>.eti
    data/long_captures/<tag>.json     ← sidecar metadata

The sidecar follows a stable schema so it can be:
  1. Re-loaded by analysis tools (e.g. plot SNR vs gain across captures)
  2. Cited verbatim in the arXiv paper (Table I / dataset table)
  3. Published with the dataset on Zenodo if we open-source captures

Usage:
    python tools/capture_logged.py --channel K8B --duration 60 \\
        --tag k8b_2026_05_26_window \\
        --antenna "Brisa flat-panel indoor with USB LNA" \\
        --location "Seoul, indoor near window"

Or interactively (will prompt for missing metadata):
    python tools/capture_logged.py --channel K8B --duration 60 --tag mycap
"""
from __future__ import annotations
import argparse
import datetime
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ETI_BIN = REPO / "eti-stuff" / "eti-cmdline" / "build" / "eti-cmdline-airspy"
LIBPATH = "/opt/homebrew/lib"
DEFAULT_OUTPUT_DIR = REPO / "data" / "long_captures"

# Korean K-block raster (matches eti-cmdline patched band table)
K_FREQ_MHZ = {
    "K7A": 175.280, "K7B": 177.008, "K7C": 178.736,
    "K8A": 181.280, "K8B": 183.008, "K8C": 184.736,
    "K9A": 187.280, "K9B": 189.008, "K9C": 190.736,
    "K10A": 193.280, "K10B": 195.008, "K10C": 196.736,
    "K11A": 199.280, "K11B": 201.008, "K11C": 202.736,
    "K12A": 205.280, "K12B": 207.008, "K12C": 208.736,
    "K13A": 211.280, "K13B": 213.008, "K13C": 214.736,
}

# Known operators (per our README & FIC dumps)
KOREA_K_OPERATORS = {
    "K8B":  "YTN DMB (mYTN/HD mYTN/4DRIVE/LOTTE/EWS)",
    "K12A": "MBC DMB (DMB라디오/MBCTV/4DRIVE/EWS)",
    "K12B": "U-KBS (KBS DMB)",
    "K12C": "SBS u (SBS DMB)",
}


def _prompt(question: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{question}{suffix}: ").strip()
    return val or default


def detect_airspy_info() -> dict:
    """Probe Airspy hardware for serial + firmware via airspy_info CLI."""
    info = {"detected": False}
    try:
        out = subprocess.run(["airspy_info"], capture_output=True, text=True, timeout=5).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return info
    info["detected"] = True
    info["raw_output"] = out.strip()
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Serial Number:"):
            info["serial"] = line.split(":", 1)[1].strip()
        elif line.startswith("Firmware Version:"):
            info["firmware"] = line.split(":", 1)[1].strip()
        elif line.startswith("Part ID Number:"):
            info["part_id"] = line.split(":", 1)[1].strip()
        elif line.startswith("Available sample rates:"):
            info["sample_rates_line"] = line
    return info


def run_capture(args, meta: dict) -> tuple[Path, dict]:
    out_path = DEFAULT_OUTPUT_DIR / f"{args.tag}.eti"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["DYLD_LIBRARY_PATH"] = LIBPATH

    eti_argv = [str(ETI_BIN),
                "-C", args.channel,
                "-d", str(args.detect),
                "-D", str(args.dump_time),
                "-t", str(args.duration),
                "-O", str(out_path),
                "-J"]

    # Gain mode: AGC if --auto-gain (default), else manual via --gain or env vars
    if args.lna is not None or args.mixer is not None or args.vga is not None:
        # Use env-var path (patched airspy-handler accepts these)
        env["AIRSPY_LNA"] = str(args.lna if args.lna is not None else 14)
        env["AIRSPY_MIXER"] = str(args.mixer if args.mixer is not None else 12)
        env["AIRSPY_VGA"] = str(args.vga if args.vga is not None else 11)
        meta["rf"]["gain_mode"] = "manual_lmv"
        meta["rf"]["lna"] = int(env["AIRSPY_LNA"])
        meta["rf"]["mixer"] = int(env["AIRSPY_MIXER"])
        meta["rf"]["vga"] = int(env["AIRSPY_VGA"])
        eti_argv += ["-G", "0"]  # ignored under env-var override but required by CLI
    else:
        eti_argv += ["-G", str(args.gain)]
        if args.gain <= 0:
            meta["rf"]["gain_mode"] = "agc_hardware"
        else:
            meta["rf"]["gain_mode"] = "sensitivity_simplified"
            meta["rf"]["sensitivity_index"] = args.gain
    if args.bias_tee:
        eti_argv += ["-b"]
        meta["rf"]["bias_tee"] = True
    else:
        meta["rf"]["bias_tee"] = False

    if args.sample_rate:
        env["AIRSPY_RATE"] = str(args.sample_rate)
        meta["rf"]["sample_rate"] = args.sample_rate
    else:
        meta["rf"]["sample_rate"] = 3_000_000  # eti-cmdline default

    meta["capture"]["argv"] = eti_argv
    meta["capture"]["env_overrides"] = {k: v for k, v in env.items()
                                        if k.startswith("AIRSPY_")}
    meta["capture"]["start_time_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

    t0 = time.time()
    proc = subprocess.run(eti_argv, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          text=True, errors="replace")
    elapsed = time.time() - t0

    meta["capture"]["elapsed_sec"] = round(elapsed, 1)
    meta["capture"]["return_code"] = proc.returncode
    meta["capture"]["stderr_tail"] = "\n".join(proc.stderr.splitlines()[-12:])

    stderr = proc.stderr or ""
    if "there might be a DAB signal here" in stderr:
        meta["capture"]["time_sync"] = True
    else:
        meta["capture"]["time_sync"] = False
    if "ensembleId" in stderr or "EId" in stderr:
        meta["capture"]["ensemble_recognized"] = True
    else:
        meta["capture"]["ensemble_recognized"] = False

    if out_path.exists():
        meta["capture"]["file_bytes"] = out_path.stat().st_size
        meta["capture"]["eti_frames"] = out_path.stat().st_size // 6144
    else:
        meta["capture"]["file_bytes"] = 0
        meta["capture"]["eti_frames"] = 0

    return out_path, meta


def build_metadata(args) -> dict:
    ch = args.channel.upper()
    if ch in K_FREQ_MHZ:
        freq_hz = int(K_FREQ_MHZ[ch] * 1_000_000)
        operator = KOREA_K_OPERATORS.get(ch, "unknown")
    else:
        freq_hz = None
        operator = "unknown"

    # Prompt for any missing environment details
    if not args.antenna:
        args.antenna = _prompt("Antenna model",
                               "Brisa flat-panel indoor with USB LNA")
    if not args.location:
        args.location = _prompt("Location",
                                "Seoul metropolitan area, indoor near window")
    if not args.operator_notes and ch in KOREA_K_OPERATORS:
        args.operator_notes = KOREA_K_OPERATORS[ch]

    return {
        "schema_version": 1,
        "tag": args.tag,
        "rf": {
            "channel": ch,
            "frequency_hz": freq_hz,
            "frequency_mhz": K_FREQ_MHZ.get(ch),
            "raster": "Korean K-block (1.728 MHz spacing)",
            "operator": operator,
            "operator_notes": args.operator_notes,
        },
        "environment": {
            "antenna": args.antenna,
            "antenna_amplifier_powered": args.antenna_powered,
            "location": args.location,
            "indoor": args.indoor,
            "transmitter_distance_estimate_km": args.tx_distance_km,
        },
        "sdr": detect_airspy_info(),
        "capture": {
            "duration_sec_requested": args.duration,
            "detect_timeout_sec": args.detect,
            "dump_time_sec": args.dump_time,
        },
        "host": {
            "hostname": socket.gethostname(),
            "platform": sys.platform,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description=__doc__)
    ap.add_argument("--channel", required=True, help="e.g. K8B")
    ap.add_argument("--duration", type=int, default=60,
                    help="total capture time (seconds)")
    ap.add_argument("--detect", type=int, default=60,
                    help="OFDM time-sync timeout (-d)")
    ap.add_argument("--dump-time", type=int, default=60,
                    help="freq-sync / ensemble dump time (-D)")
    ap.add_argument("--tag", required=True,
                    help="basename of output file (no ext)")

    # Gain control
    g = ap.add_argument_group("RF gain")
    g.add_argument("--gain", type=int, default=0,
                   help="sensitivity gain 0..21, or 0 for hardware AGC")
    g.add_argument("--lna", type=int, help="manual LNA (0..14) — bypasses --gain")
    g.add_argument("--mixer", type=int, help="manual MIXER (0..15)")
    g.add_argument("--vga", type=int, help="manual VGA (0..15)")
    g.add_argument("--bias-tee", action="store_true",
                   help="enable bias-tee (Airspy -b)")
    g.add_argument("--sample-rate", type=int,
                   help="Airspy sample rate (e.g. 3000000, 6000000)")

    # Environment metadata
    e = ap.add_argument_group("Environment")
    e.add_argument("--antenna", help="antenna model description")
    e.add_argument("--antenna-powered", action="store_true",
                   help="active antenna with built-in amplifier")
    e.add_argument("--location", help="capture location free-text")
    e.add_argument("--indoor", action="store_true", default=True)
    e.add_argument("--outdoor", dest="indoor", action="store_false")
    e.add_argument("--tx-distance-km", type=float,
                   help="rough straight-line distance to transmitter (km)")
    e.add_argument("--operator-notes", help="operator/services (defaults to known K-block)")

    args = ap.parse_args()

    print(f"### Building metadata ###")
    meta = build_metadata(args)
    print(json.dumps(meta, indent=2, ensure_ascii=False))

    print(f"\n### Running eti-cmdline-airspy ###")
    eti_path, meta = run_capture(args, meta)

    sidecar = eti_path.with_suffix(".json")
    sidecar.write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    print(f"\n=== Done ===")
    print(f"  capture:  {eti_path}  ({meta['capture']['file_bytes']:,} B, "
          f"{meta['capture']['eti_frames']} frames)")
    print(f"  metadata: {sidecar}")
    print(f"  time_sync: {meta['capture']['time_sync']}  "
          f"ensemble: {meta['capture']['ensemble_recognized']}")
    return 0 if meta["capture"]["file_bytes"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
