"""Read an ETI file and print the recovered ensemble after FIC accumulation.

    python tools/dump_fic.py /path/to/file.eti
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from tdmb.eti import parse_frame, FRAME_SIZE   # noqa: E402
from tdmb.eti.fic import FicAccumulator        # noqa: E402


def main(path: str) -> int:
    p = Path(path)
    if not p.exists():
        print(f"missing: {path}")
        return 1
    fic = FicAccumulator()
    n_frames = 0
    n_bad = 0
    with p.open("rb") as f:
        while True:
            chunk = f.read(FRAME_SIZE)
            if len(chunk) != FRAME_SIZE:
                break
            try:
                fr = parse_frame(chunk)
            except Exception:
                n_bad += 1
                continue
            n_frames += 1
            if fr.fic_present and fr.fic:
                fic.feed_fic(fr.fic)

    ens = fic.ensemble
    print(f"frames parsed: {n_frames}  bad: {n_bad}")
    print(f"FIB ok: {fic.fib_ok}/{fic.fib_total}")
    print(f"Ensemble EId={ens.eid and hex(ens.eid)}  label={ens.label!r}")
    print(f"Sub-channels:")
    for sid, sub in sorted(ens.sub_channels.items()):
        print(f"  SubCh {sid:2d}  start={sub.start_addr:3d}  size={sub.size_cu:3d} CU"
              f"  {sub.protection:8s}  {sub.bitrate_kbps} kbps")
    print(f"Services:")
    for sid, svc in sorted(ens.services.items()):
        kind = "data" if svc.is_data else "prog"
        print(f"  SId 0x{sid:08X}  {kind}  {svc.label!r}")
        for c in svc.components:
            print(f"     -> SubCh {c.sub_ch_id}  {c.transport}  type={c.ascty_or_dscty}"
                  f"  primary={c.is_primary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
