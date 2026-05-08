"""Extract one Korean T-DMB sub-channel from an ETI capture, run the
outer FEC (RS+TI), and write a clean MPEG-2 TS file.

    python tools/extract_ts.py /tmp/k8b_eti.eti --subch 1 --out /tmp/myth.ts

If --subch is omitted the script lists available sub-channels and exits.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from tdmb.eti import parse_frame, FRAME_SIZE                # noqa: E402
from tdmb.eti.fic import FicAccumulator                     # noqa: E402
from tdmb.msc import extract_subchannel                     # noqa: E402
from tdmb.fec import KoreanTDmbOuterFec                     # noqa: E402


def iter_frames(path: Path):
    with path.open("rb") as f:
        while True:
            chunk = f.read(FRAME_SIZE)
            if len(chunk) != FRAME_SIZE:
                return
            try:
                yield parse_frame(chunk)
            except Exception:
                continue


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("eti", type=Path)
    ap.add_argument("--subch", type=int, default=None,
                    help="sub-channel id to extract (omit to just list)")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--limit-cifs", type=int, default=0,
                    help="stop after this many CIFs of payload (0=all)")
    args = ap.parse_args()

    # First pass: build ensemble & list sub-channels
    fic = FicAccumulator()
    n_frames = 0
    sub_seen: dict[int, int] = {}
    for fr in iter_frames(args.eti):
        n_frames += 1
        if fr.fic_present and fr.fic:
            fic.feed_fic(fr.fic)
        for s in fr.streams:
            sub_seen[s.scid] = s.length_bytes

    print(f"frames: {n_frames}, FIB ok: {fic.fib_ok}/{fic.fib_total}")
    ens = fic.ensemble
    print(f"Ensemble: {ens.label!r}  EId=0x{(ens.eid or 0):04X}")
    print("Sub-channels (from STC headers / FIC):")
    for scid, br_bytes in sorted(sub_seen.items()):
        sub = ens.sub_channels.get(scid)
        info = (f"{sub.bitrate_kbps} kbps {sub.protection}"
                if sub else "(no FIC info yet)")
        print(f"  SubCh {scid:2d}  {br_bytes:5d} bytes/CIF  {info}")

    if args.subch is None:
        return 0

    if args.out is None:
        args.out = REPO / "data" / f"subch{args.subch}.ts"
    args.out.parent.mkdir(parents=True, exist_ok=True)

    fec = KoreanTDmbOuterFec()
    n_pkts = 0
    n_cifs = 0
    with args.out.open("wb") as fout:
        for chunk in extract_subchannel(iter_frames(args.eti), args.subch):
            if not chunk:
                continue
            n_cifs += 1
            for ts in fec.feed(chunk):
                fout.write(ts.data)
                n_pkts += 1
            if args.limit_cifs and n_cifs >= args.limit_cifs:
                break

    print(f"\nfed {n_cifs} CIFs ({fec.bytes_in} bytes) -> {n_pkts} TS packets")
    print(f"stats: {fec.stats}")
    print(f"output: {args.out}  ({args.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
