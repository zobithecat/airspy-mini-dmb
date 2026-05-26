"""Run the patched RS+TI pipeline (sync-byte-aligned deinterleaver) on
data/captures/k8b_100pct.eti SubCh 1 and report decode rates.

Compares against the legacy "deinterleave-then-search-sync" path to prove
the alignment fix matters.

    python tools/verify_outer_fec.py
    python tools/verify_outer_fec.py data/long_captures/k8b_5min.eti 1
"""
from __future__ import annotations
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from tdmb.eti import parse_frame, FRAME_SIZE
from tdmb.msc import extract_subchannel
from tdmb.fec.outer import KoreanTDmbOuterFec, collect_msc_to_ts


def _iter_frames(path: Path):
    with path.open("rb") as f:
        while True:
            chunk = f.read(FRAME_SIZE)
            if len(chunk) != FRAME_SIZE:
                return
            try:
                yield parse_frame(chunk)
            except Exception:
                continue


def _summarise(packets, label: str) -> None:
    n = len(packets)
    ok = sum(1 for p in packets if p.rs_errors >= 0)
    err = sum(p.rs_errors for p in packets if p.rs_errors > 0)
    pid_ctr = Counter()
    sync_lost = 0
    for p in packets:
        if p.rs_errors < 0:
            continue
        if p.data and p.data[0] == 0x47:
            pid = ((p.data[1] & 0x1F) << 8) | p.data[2]
            pid_ctr[pid] += 1
        else:
            sync_lost += 1

    print(f"\n--- {label} ---")
    print(f"  total TS packets:    {n:,}")
    if n:
        print(f"  RS ok/corrected:     {ok:,} ({ok*100/n:.1f}%)")
    print(f"  RS bytes corrected:  {err}")
    print(f"  packets w/ 0x47:     {sum(pid_ctr.values()):,}")
    print(f"  packets sync-lost:   {sync_lost}")
    print(f"  top PIDs (post-RS):")
    for pid, c in pid_ctr.most_common(6):
        marker = ""
        if pid == 0x1fff:
            marker = "  (null)"
        elif pid == 0x0000:
            marker = "  (PAT)"
        print(f"    PID 0x{pid:04x}: {c:6d}{marker}")


def main():
    eti_path = Path(sys.argv[1] if len(sys.argv) > 1
                    else "data/captures/k8b_100pct.eti")
    sub_ch = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    print(f"ETI: {eti_path}")
    print(f"SubCh: {sub_ch}")

    packets, stats = collect_msc_to_ts(eti_path, sub_ch)

    print("\n=== stats ===")
    for k, v in stats.items():
        print(f"  {k}: {v}")

    _summarise(packets, "patched pipeline")

    if stats["aligned"]:
        print(f"\n  phase_offset (where 0x47 lives in 204 cycle) = {stats['phase_offset']}")
        print(f"  → expected 160 for k8b_100pct based on prior analysis")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
