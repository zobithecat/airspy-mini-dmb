"""V2 SL→H.264 extractor: based on careful reverse-engineering of the
RS-corrected K8B mYTN stream.

Structure observed (verified against >3000 PES packets in
data/captures/k8b_100pct.eti, SubCh 1, PID 0x0113):

  PES header (9 B):  00 00 01 FA | pes_len(2) | flags(2) | hdr_len=0
  SL packet body (rest of PES):
    Byte 0:      0xCF flags
                   bit7 au_start_flag = 1
                   bit6 au_end_flag   = 1
                   bit5 padding_flag  = 0
                   bit4 rand_acc_pt   = 0
                   bit3 dts_flag      = 1
                   bit2 cts_flag      = 1
                   bits1-0: start of DTS field
    Bytes 0..8:  flags (6 bits) + DTS (33 bits) + CTS (33 bits)  → 9 bytes total
    Bytes 9..N:  ONE H.264 NAL unit (no 00 00 01 start code), starting with
                 NAL header byte (NRI/type) followed by payload.

For PID 0x0113 (video), the typical first NAL byte is 0x41 (non-IDR slice,
NRI=2, type=1).  SPS/PPS are NOT in the video ES — per MPEG-4 Systems they
live in the OD stream (PID 0x0111) as part of an
AVCDecoderConfigurationRecord.

Output: H.264 byte stream (Annex B), prepending 00 00 00 01 to each NAL.

Usage:
  python tools/extract_sl_h264_v2.py /tmp/k8b_rs.ts \\
      --video-pid 0x113 --out /tmp/k8b_video.h264

This deliberately does NOT include SPS/PPS — feed it to ffplay along with
codec config extracted separately (see tools/extract_avcc_from_od.py once
written).
"""
from __future__ import annotations
import argparse
import sys
from collections import Counter
from pathlib import Path


def collect_pes_bodies(ts_path: Path, pid: int):
    """Walk TS packets of `pid`, yield one bytes object per PES."""
    data = ts_path.read_bytes()
    cur = bytearray()
    pes_started = False
    n_pkts = 0
    for i in range(len(data) // 188):
        pkt = data[i*188:(i+1)*188]
        if pkt[0] != 0x47:
            continue
        p = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if p != pid:
            continue
        n_pkts += 1
        pusi = (pkt[1] & 0x40) != 0
        afc = (pkt[3] >> 4) & 0x3
        off = 4
        if afc & 0x2:
            off = 4 + 1 + pkt[4]
        if not (afc & 0x1) or off >= 188:
            continue
        payload = pkt[off:188]

        if pusi:
            if pes_started and cur:
                yield bytes(cur)
            cur = bytearray(payload)
            pes_started = True
        elif pes_started:
            cur.extend(payload)
    if pes_started and cur:
        yield bytes(cur)


def split_pes(pes: bytes):
    """Return (pes_header_total_len, body_bytes) or (None, None) on parse fail."""
    if len(pes) < 9 or pes[0:3] != b'\x00\x00\x01':
        return None, None
    stream_id = pes[3]
    pes_len = (pes[4] << 8) | pes[5]
    flags_a = pes[6]   # actually PES_flags_1
    flags_b = pes[7]   # PES_flags_2 (PTS/DTS flags + others)
    hdr_len = pes[8]
    body_start = 9 + hdr_len
    if pes_len:
        body_end = min(6 + pes_len, len(pes))
    else:
        body_end = len(pes)
    if body_start >= body_end:
        return None, None
    return body_start, pes[body_start:body_end]


# SL header is 9 bytes (6 bit flags + 33 bit DTS + 33 bit CTS = 72 bits).
SL_HDR_LEN = 9


def extract_nal_from_sl(body: bytes) -> tuple[int, int, bytes] | None:
    """Strip the 9-byte SL header, return (dts, cts, nal_bytes)."""
    if len(body) <= SL_HDR_LEN:
        return None
    if body[0] != 0xCF:
        # Not the expected Korean T-DMB AU-start SL flag byte.
        return None

    # Pack to bit array then extract DTS/CTS (positions per dmb-ffmpeg
    # mpegts.c read_sl_header(); flag bits 1-6 of byte 0, then 33-bit DTS,
    # 33-bit CTS).
    bits = 0
    for b in body[:SL_HDR_LEN]:
        bits = (bits << 8) | b
    # Total 72 bits.  6 bits of flags at MSB end.
    flags = (bits >> (72 - 6)) & 0x3F
    dts = (bits >> (72 - 6 - 33)) & 0x1FFFFFFFF   # next 33 bits
    cts = bits & 0x1FFFFFFFF                       # last 33 bits
    nal = body[SL_HDR_LEN:]
    return (dts, cts, nal)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ts", type=Path, help="RS-corrected MSC TS (from extract_ts_rs.py)")
    ap.add_argument("--video-pid", type=lambda s: int(s, 0), default=0x113)
    ap.add_argument("--out", type=Path, default=Path("/tmp/dmb_video.h264"))
    ap.add_argument("--summary-only", action="store_true",
                    help="just report stats, don't write H.264")
    args = ap.parse_args()

    print(f"Reading {args.ts}  pid=0x{args.video_pid:03x}")

    total_pes = 0
    parsed_pes = 0
    nal_types = Counter()
    cts_values: list[int] = []
    n_written = 0
    failed_sl_flag = 0
    failed_pes_parse = 0

    out_fh = None if args.summary_only else args.out.open("wb")

    try:
        for pes in collect_pes_bodies(args.ts, args.video_pid):
            total_pes += 1
            body_start, body = split_pes(pes)
            if body is None:
                failed_pes_parse += 1
                continue
            parsed = extract_nal_from_sl(body)
            if parsed is None:
                failed_sl_flag += 1
                continue
            parsed_pes += 1
            dts, cts, nal = parsed
            if not nal:
                continue
            nal_byte = nal[0]
            nal_type = nal_byte & 0x1F
            forbidden = (nal_byte >> 7) & 1
            if forbidden == 0:
                nal_types[nal_type] += 1
            if out_fh is not None:
                # Write as Annex B byte stream
                out_fh.write(b'\x00\x00\x00\x01')
                out_fh.write(nal)
                n_written += 1
            cts_values.append(cts)
    finally:
        if out_fh is not None:
            out_fh.close()

    print(f"\nPES total:    {total_pes}")
    print(f"PES parsed:   {parsed_pes}")
    print(f"PES SL-fail:  {failed_sl_flag}  (first byte != 0xCF — corrupted SL header)")
    print(f"PES hdr-fail: {failed_pes_parse}  (PES start code missing — drop)")

    NAL_NAMES = {1:"non-IDR", 2:"DPA", 3:"DPB", 4:"DPC", 5:"IDR", 6:"SEI",
                 7:"SPS", 8:"PPS", 9:"AUD", 10:"end-seq", 11:"end-strm",
                 12:"filler", 13:"SPS_ext", 14:"prefix", 15:"SubsetSPS",
                 19:"aux-slice", 20:"slice-ext"}
    print(f"\nNAL type histogram (parsed = {sum(nal_types.values())}):")
    for t in sorted(nal_types):
        print(f"  type {t:>2} ({NAL_NAMES.get(t, '?')}): {nal_types[t]}")

    if cts_values:
        cts_values.sort()
        deltas = [cts_values[i+1] - cts_values[i] for i in range(min(10, len(cts_values) - 1))]
        print(f"\nCTS first..last: 0x{cts_values[0]:09x} .. 0x{cts_values[-1]:09x}")
        if deltas:
            avg_delta = sum(deltas) / len(deltas)
            print(f"  inter-frame CTS Δ (90kHz units): {deltas}")
            if avg_delta:
                print(f"  → approx FPS: {90000.0/avg_delta:.2f}")

    if out_fh is not None:
        print(f"\nWrote {args.out} ({args.out.stat().st_size:,} B)  "
              f"{n_written} NAL units")
        print(f"\nNote: SPS/PPS aren't in the video ES — they live in the OD")
        print(f"stream on PID 0x111.  Without them ffmpeg can't decode.  "
              f"Use tools/extract_avcc_from_od.py next.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
