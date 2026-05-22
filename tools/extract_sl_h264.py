"""Extract H.264 elementary stream from Korean T-DMB MPEG-4 SL-packetized PES.

Korean T-DMB carries video as:
  H.264 NAL → MPEG-4 SL packet → MPEG-2 PES (stream_id=0xFA) → TS (PID 0x113)
                                                                                 → MSC sub-channel 1

Even when the broadcast PMT/IOD is bit-error-corrupted (so dmb-ffmpeg's
auto SL-OD demuxer can't bind), the SL-packetized PES is intact in MSC
SubCh 1. This script:

  1. Scan the slot-extracted TS for PES start codes with stream_id=0xFA
  2. Strip PES header (variable length)
  3. For each SL packet, peel off the minimal header (1 byte for
     Korean T-DMB profile) and concatenate AUs
  4. Look for H.264 NAL start codes and write as raw .h264

This bypasses the synthetic-PMT + dmb-ffmpeg SL/OD path entirely.
"""
from __future__ import annotations
import argparse
import sys
from collections import Counter
from pathlib import Path


def collect_ts_payload(ts_path: Path, target_pid: int) -> bytes:
    """Concat every TS packet's PES payload for one PID (slot-direct style)."""
    data = ts_path.read_bytes()
    out = bytearray()
    for i in range(len(data) // 188):
        pkt = data[i*188:(i+1)*188]
        if pkt[0] != 0x47: continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid != target_pid: continue
        afc = (pkt[3] >> 4) & 0x3
        off = 4 + ((pkt[4] + 1) if (afc & 0x2) else 0)
        if (afc & 0x1) and off < 188:
            out.extend(pkt[off:188])
    return bytes(out)


def find_pes_packets(buf: bytes, stream_id: int):
    """Yield (start_offset, header_end, declared_length) for each PES with the
    given stream_id. start_offset is at the 0x00 0x00 0x01 prefix."""
    n = len(buf)
    i = 0
    while i < n - 9:
        if buf[i] == 0 and buf[i+1] == 0 and buf[i+2] == 1 and buf[i+3] == stream_id:
            pes_len = (buf[i+4] << 8) | buf[i+5]
            # If pes_len == 0, "unbounded" PES (allowed for video).
            # Optional PES header (only present for normal streams 0xC0..0xEF, 0xFA, etc.)
            # stream_id = 0xfa = MPEG-4 SL-packetized → has optional header
            flags2 = buf[i+7]
            hdr_len = buf[i+8]
            payload_start = i + 9 + hdr_len
            payload_end = (i + 6 + pes_len) if pes_len else n
            yield (i, payload_start, payload_end)
            i = payload_start
        else:
            i += 1


def search_h264_nals(buf: bytes):
    """Return list of (offset, nal_type) for valid-looking H.264 NAL starts."""
    nals = []
    n = len(buf)
    i = 0
    while i < n - 5:
        prefix_len = 0
        if i + 2 < n and buf[i] == 0 and buf[i+1] == 0 and buf[i+2] == 1:
            prefix_len = 3
        elif i + 3 < n and buf[i] == 0 and buf[i+1] == 0 and buf[i+2] == 0 and buf[i+3] == 1:
            prefix_len = 4
        if prefix_len:
            nal_byte = buf[i + prefix_len]
            forbidden = (nal_byte >> 7) & 1
            nal_type = nal_byte & 0x1F
            if forbidden == 0 and 1 <= nal_type <= 23:
                nals.append((i + prefix_len, nal_type))
                i += prefix_len + 1
                continue
        i += 1
    return nals


NAL_NAMES = {1:"non-IDR", 2:"DPA", 3:"DPB", 4:"DPC", 5:"IDR", 6:"SEI",
             7:"SPS", 8:"PPS", 9:"AUD", 10:"end-seq", 11:"end-strm",
             12:"filler", 13:"SPS_ext", 14:"prefix", 15:"SubsetSPS",
             19:"aux-slice", 20:"slice-ext"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ts", type=Path, help="slot-extracted MSC TS")
    ap.add_argument("--pid", type=lambda s: int(s, 0), default=0x113)
    ap.add_argument("--stream-id", type=lambda s: int(s, 0), default=0xFA,
                    help="PES stream_id (0xFA = MPEG-4 SL packetized)")
    ap.add_argument("--sl-header-len", type=int, default=0,
                    help="bytes to strip from each SL packet payload; 0 = treat "
                         "PES payload as raw ES (predefined SLConfig 0x02)")
    ap.add_argument("--out", type=Path, default=Path("/tmp/sl_video.h264"),
                    help="output H.264 elementary stream")
    args = ap.parse_args()

    raw = collect_ts_payload(args.ts, args.pid)
    print(f"PID 0x{args.pid:04x} payload: {len(raw):,} B")

    pes_packets = list(find_pes_packets(raw, args.stream_id))
    print(f"PES packets (stream_id=0x{args.stream_id:02x}): {len(pes_packets)}")
    if not pes_packets:
        print("  no SL-packetized PES found — try a different stream_id (0xFF, 0xBD, 0xE0)")
        return 1

    es = bytearray()
    for i, (psc, body_start, body_end) in enumerate(pes_packets):
        body = raw[body_start:min(body_end, len(raw))]
        if args.sl_header_len:
            body = body[args.sl_header_len:]
        es.extend(body)

    print(f"Concatenated ES payload: {len(es):,} B")
    nals = search_h264_nals(bytes(es))
    types = Counter(t for _, t in nals)
    print(f"NAL units found: {len(nals)}")
    print(f"NAL type histogram:")
    for t in sorted(types):
        print(f"  type {t:>2}  {NAL_NAMES.get(t, '?'):10s}  {types[t]:6d}")

    has_sps = 7 in types
    has_pps = 8 in types
    has_idr = 5 in types
    print()
    if has_sps and has_pps:
        print("✓ SPS + PPS present — codec config decodable")
        if has_idr:
            print("✓ IDR present — random access point available")
    elif has_sps:
        print("⚠ SPS only — partial config")
    else:
        print("✗ no SPS")

    args.out.write_bytes(bytes(es))
    print(f"\nWrote {args.out} ({args.out.stat().st_size:,} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
