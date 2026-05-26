"""Run the (sync-aligned) RS+TI outer FEC pipeline on an ETI capture and
write the resulting RS-corrected 188-byte TS packets to a file.

This is what `tools/extract_ts.py` was *supposed* to do, but couldn't until
the deinterleaver alignment bug was fixed (see src/tdmb/fec/outer.py
docstring).

Usage:
    python tools/extract_ts_rs.py data/captures/k8b_100pct.eti \\
        --subch 1 --out /tmp/k8b_subch1.ts

    # Compare to slot-direct (no RS) and probe with ffprobe
    python tools/extract_ts_direct.py data/captures/k8b_100pct.eti \\
        --subch 1 --out /tmp/k8b_subch1_slot.ts
    ./local/dmb-ffmpeg/bin/ffprobe /tmp/k8b_subch1.ts
"""
from __future__ import annotations
import argparse
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from tdmb.fec.outer import collect_msc_to_ts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("eti", type=Path)
    ap.add_argument("--subch", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--include-uncorrectable", action="store_true",
                    help="also write packets where RS gave up (best-effort)")
    args = ap.parse_args()

    packets, stats = collect_msc_to_ts(args.eti, args.subch)

    pid_ctr = Counter()
    written = 0
    uncorr_written = 0
    with args.out.open("wb") as f:
        for p in packets:
            if p.rs_errors < 0:
                if args.include_uncorrectable and p.data and p.data[0] == 0x47:
                    f.write(p.data)
                    uncorr_written += 1
                continue
            if not p.data or p.data[0] != 0x47:
                continue
            f.write(p.data)
            pid = ((p.data[1] & 0x1F) << 8) | p.data[2]
            pid_ctr[pid] += 1
            written += 1

    total_bytes = written * 188 + uncorr_written * 188
    print(f"\nwrote {args.out}  {total_bytes:,} bytes  "
          f"({written:,} clean + {uncorr_written:,} uncorrectable TS packets)")

    print(f"\nRS stats:")
    print(f"  blocks total:       {stats['rs_total']:,}")
    print(f"  ok (no errors):     {stats['rs_ok']:,}")
    print(f"  corrected:          {stats['rs_corrected']:,}")
    print(f"  failed:             {stats['rs_failed']:,}")
    ok_pct = (stats['rs_ok'] + stats['rs_corrected']) * 100.0 / max(1, stats['rs_total'])
    print(f"  success rate:       {ok_pct:.1f}%")

    print(f"\nPID histogram:")
    for pid, c in pid_ctr.most_common(10):
        marker = ""
        if pid == 0x1fff: marker = "  (null pad)"
        elif pid == 0x0000: marker = "  (PAT)"
        elif pid == 0x0100: marker = "  (likely PMT)"
        elif pid == 0x0111: marker = "  (OD)"
        elif pid == 0x0112: marker = "  (SD/BIFS)"
        elif pid == 0x0113: marker = "  (video)"
        elif pid == 0x0114: marker = "  (audio)"
        print(f"  PID 0x{pid:04x}: {c:6d}{marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
