"""Search for H.264 NAL units in MSC sub-channel video PID raw bytes.

Even when the captured PMT/OD is too corrupted for ffmpeg to resolve
codec parameters, the underlying H.264 elementary stream is still
present in the PES payload of the video PID — wrapped in MPEG-4 SL
packets, but with NAL start codes intact (when not bit-mauled).

Counting NAL types tells you how clean the signal really is:
  * thousands of NALs / many IDR + SPS + PPS  → playable
  * dozens / 1 SPS                             → too corrupted, codec
                                                  parameters won't bind
"""
from __future__ import annotations
import argparse
import sys
from collections import Counter
from pathlib import Path


def collect_pid_payload(ts_path: Path, target_pid: int) -> bytes:
    data = ts_path.read_bytes()
    out = bytearray()
    for i in range(len(data) // 188):
        pkt = data[i*188:(i+1)*188]
        if pkt[0] != 0x47: continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid != target_pid: continue
        afc = (pkt[3] >> 4) & 0x3
        offset = 4 + ((pkt[4] + 1) if (afc & 0x2) else 0)
        if (afc & 0x1) and offset < 188:
            out.extend(pkt[offset:188])
    return bytes(out)


def find_nals(pl: bytes):
    short, long_, types = [], [], Counter()
    n = len(pl)
    i = 0
    while i < n - 5:
        if pl[i] == 0 and pl[i+1] == 0:
            if pl[i+2] == 1:
                nal_byte = pl[i+3]
                if (nal_byte >> 7) & 1 == 0:
                    nal_type = nal_byte & 0x1F
                    if 1 <= nal_type <= 23:
                        short.append(i)
                        types[nal_type] += 1
            elif pl[i+2] == 0 and pl[i+3] == 1:
                nal_byte = pl[i+4]
                if (nal_byte >> 7) & 1 == 0 and 1 <= (nal_byte & 0x1F) <= 23:
                    long_.append(i)
        i += 1
    return short, long_, types


NAL_NAMES = {1:"non-IDR slice", 2:"DPA", 3:"DPB", 4:"DPC", 5:"IDR slice", 6:"SEI",
             7:"SPS", 8:"PPS", 9:"AUD", 10:"end-seq", 11:"end-stream", 12:"filler"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ts", type=Path, help="extracted MSC TS file")
    ap.add_argument("--pid", type=lambda s: int(s, 0), default=0x113, help="video PID")
    args = ap.parse_args()

    pl = collect_pid_payload(args.ts, args.pid)
    print(f"PID 0x{args.pid:04x} payload: {len(pl)} B")
    short, long_, types = find_nals(pl)
    print(f"  short NAL prefix (00 00 01): {len(short)}")
    print(f"  long  NAL prefix (00 00 00 01): {len(long_)}")
    print(f"\nNAL type histogram:")
    for t in sorted(types):
        print(f"  type {t:>2}  {NAL_NAMES.get(t, '?'):14s}  {types[t]}")
    if 7 in types and 8 in types:
        print("\n✓ SPS + PPS present — codec config decodable in principle")
    elif 7 in types:
        print("\n⚠ SPS only (no PPS) — partial config, won't decode")
    else:
        print("\n✗ no SPS — too corrupted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
