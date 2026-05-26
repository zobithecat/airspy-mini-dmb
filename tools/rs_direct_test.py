"""Test hypothesis: eti-cmdline already de-interleaved the MSC sub-channel.
If so, we should apply RS(204,188) decode directly without convolutional
deinterleaving.

Strategy: only test offsets where the byte at offset is 0x47, decode just
1000 blocks per candidate, report best.
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from tdmb.eti import parse_frame, FRAME_SIZE
from tdmb.msc import extract_subchannel
from tdmb.fec.interleaver import ConvolutionalDeinterleaver
from tdmb.fec.rs import RSDecoder


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


def collect_subch_bytes(eti_path: Path, sub_ch_id: int) -> bytes:
    out = bytearray()
    for chunk in extract_subchannel(iter_frames(eti_path), sub_ch_id):
        if chunk:
            out.extend(chunk)
    return bytes(out)


def find_candidate_offsets(buf: bytes, top_n: int = 5) -> list:
    """Score each of 204 offsets by 0x47 hit rate across entire buffer.
    Return top N most likely sync offsets."""
    scores = []
    n_blocks = len(buf) // 204
    for off in range(204):
        hits = sum(1 for k in range(n_blocks) if off + k*204 < len(buf)
                   and buf[off + k*204] == 0x47)
        scores.append((hits, off))
    scores.sort(reverse=True)
    print(f"    top 5 offset hit-rates: " +
          ", ".join(f"off={o} hits={h}({h*100//n_blocks}%)"
                    for h, o in scores[:5]))
    return [o for h, o in scores[:top_n] if h > n_blocks // 20]  # ≥5%


def rs_test(buf: bytes, label: str, max_blocks: int = 2000):
    """Sample-test first max_blocks at every candidate offset."""
    cands = find_candidate_offsets(buf)
    print(f"  {label}: {len(cands)} candidate offsets")
    if not cands:
        # No 0x47 alignment found at all
        return 0, 0, -1
    rs = RSDecoder()
    best_ok = 0
    best_off = cands[0]
    best_total = 0
    for off in cands[:20]:  # limit
        rs2 = RSDecoder()
        ok = 0
        total = 0
        for i in range(max_blocks):
            pos = off + i * 204
            if pos + 204 > len(buf):
                break
            blk = buf[pos:pos+204]
            r = rs2.decode(blk)
            total += 1
            if r.ok and len(r.data) >= 188 and r.data[0] == 0x47:
                ok += 1
        if ok > best_ok:
            best_ok = ok
            best_off = off
            best_total = total
        print(f"    offset={off:>3}: RS_ok={ok}/{total} ({ok*100//max(1,total)}%)")
    return best_ok, best_total, best_off


def main():
    eti_path = Path(sys.argv[1] if len(sys.argv) > 1
                    else "data/captures/k8b_100pct.eti")
    sub_ch = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    print(f"ETI: {eti_path}  SubCh: {sub_ch}")
    buf = collect_subch_bytes(eti_path, sub_ch)
    print(f"SubCh bytes: {len(buf):,}\n")

    print("=== PATH A: direct RS on raw MSC bytes ===")
    ok_a, tot_a, off_a = rs_test(buf, "raw")

    print("\n=== PATH B: through Forney deinterleaver, then RS ===")
    di = ConvolutionalDeinterleaver()
    deint = di.feed(buf)
    deint_clean = deint[2244:]
    print(f"  deinterleaved bytes (after skip 2244): {len(deint_clean):,}")
    ok_b, tot_b, off_b = rs_test(deint_clean, "deint")

    print()
    print(f"Direct:  {ok_a}/{tot_a} at offset {off_a}")
    print(f"Deint:   {ok_b}/{tot_b} at offset {off_b}")
    if ok_a > ok_b:
        print(f"✓ Direct RS WINS: eti-cmdline already deinterleaves.")
    elif ok_b > ok_a:
        print(f"✓ Deint+RS WINS: pipeline correct, BER too high.")
    else:
        print(f"✗ Tied — likely both fail due to BER, no info gained.")


if __name__ == "__main__":
    main()
