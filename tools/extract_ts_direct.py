"""Extract 188-byte MPEG-2 TS packets from MSC, ASSUMING the stream is laid
out as 204-byte slots where the first 188 bytes are the TS packet (and the
trailing 16 bytes are the RS parity / reserved). When the outer FEC isn't
strong enough or simply absent for this sub-channel, the bare TS portion
may still be playable.

    python tools/extract_ts_direct.py /tmp/K8B_retry.eti --subch 1 --out /tmp/myth.ts
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from tdmb.eti import parse_frame, FRAME_SIZE
from tdmb.msc import extract_subchannel


SLOT = 204
TS_PKT = 188


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


def find_sync_offset(data: bytes, slot: int = SLOT) -> int:
    """Find the byte offset within `slot` cycle that has the most 0x47 bytes."""
    best = (0, 0)
    for off in range(slot):
        hits = sum(1 for i in range(off, len(data), slot) if data[i] == 0x47)
        if hits > best[0]:
            best = (hits, off)
    return best[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("eti", type=Path)
    ap.add_argument("--subch", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--probe-bytes", type=int, default=200_000,
                    help="bytes to inspect for slot-sync detection")
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)

    # First pass: gather a buffer of subchannel bytes to find slot-sync offset
    probe = bytearray()
    for chunk in extract_subchannel(iter_frames(args.eti), args.subch):
        if not chunk:
            continue
        probe.extend(chunk)
        if len(probe) >= args.probe_bytes:
            break
    if not probe:
        print(f"no data for subch {args.subch}")
        return 2
    off = find_sync_offset(bytes(probe))
    print(f"slot-sync offset within {SLOT}-byte cycle: {off}")

    # Second pass: write 188-byte TS portion of every 204-byte slot
    n_total = 0
    n_valid = 0
    with args.out.open("wb") as fout:
        # Re-stream so we use ALL data including probe
        carry = bytearray(probe)
        # advance to first slot boundary
        # the "natural" position 0 of the carry is the start of the subchannel
        # stream; the slot offset relative to that is `off`.
        # we keep slot-aligned indexing across CIF concatenation.
        consumed = 0
        # process initial probe
        i = off
        while i + TS_PKT <= len(carry):
            pkt = bytes(carry[i:i + TS_PKT])
            n_total += 1
            if pkt[0] == 0x47:
                fout.write(pkt)
                n_valid += 1
            i += SLOT
        consumed = i
        carry = bytearray(carry[consumed:])
        # carry leftover into stream
        for chunk in extract_subchannel(iter_frames(args.eti), args.subch):
            if not chunk:
                continue
            # skip CIFs we already consumed in probe
            # actually we restarted iteration → already at start, skip past consumed bytes
            pass
        # Simpler approach: just re-iterate, do whole stream from scratch
        carry = bytearray()
        i = off
        n_total = n_valid = 0
        with args.out.open("wb") as fout2:
            for chunk in extract_subchannel(iter_frames(args.eti), args.subch):
                if not chunk:
                    continue
                carry.extend(chunk)
                while i + TS_PKT <= len(carry):
                    pkt = bytes(carry[i:i + TS_PKT])
                    n_total += 1
                    if pkt[0] == 0x47:
                        fout2.write(pkt)
                        n_valid += 1
                    i += SLOT
                # trim consumed from carry
                if i >= len(carry):
                    i -= len(carry)
                    carry = bytearray()
                else:
                    # keep tail
                    carry = bytearray(carry[i:])
                    i = 0
        # NOTE: above writes to args.out from the second open
    print(f"slots inspected: {n_total}, valid TS packets written: {n_valid}"
          f"  ({100 * n_valid / max(1, n_total):.1f}%)")
    print(f"output: {args.out}  ({args.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
