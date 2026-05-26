"""Extract the AVCDecoderConfigurationRecord (SPS + PPS) from the DMB SD/BIFS
stream and prepend it to an Annex-B H.264 elementary stream so ffmpeg can
decode the slices.

In Korean T-DMB broadcasts the BIFS/SD stream (typically PID 0x112,
table_id=0x05) carries the InitialObjectDescriptor with the
DecoderConfigDescriptor and DecoderSpecificInfo for the video ES.  The
AVCC bytes can be located by scanning for the byte signature::

    01 PP CC LL FF E1 NN NN <SPS> 01 MM MM <PPS>

    01    configurationVersion
    PP    AVCProfileIndication  (0x42 Baseline / 0x4D Main / 0x64 High)
    CC    profile_compatibility
    LL    AVCLevelIndication    (0x0D = 1.3 typical for QVGA)
    FF    0xFC | (lengthSizeMinusOne)    typically 0xFF
    E1    0xE0 | numOfSPS               numOfSPS=1 → 0xE1
    NN NN big-endian SPS length
    SPS   SPS NAL data (starts with 0x67)
    01    numOfPPS = 1
    MM MM big-endian PPS length
    PPS   PPS NAL data (starts with 0x68)

Usage:
  python tools/extract_avcc_from_sd.py /tmp/k8b_rs.ts \\
      --sd-pid 0x112 --h264-in /tmp/k8b_video.h264 \\
      --out /tmp/k8b_video_full.h264
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path


def collect_pid_payload(ts_path: Path, pid: int) -> bytes:
    data = ts_path.read_bytes()
    out = bytearray()
    for i in range(len(data) // 188):
        pkt = data[i*188:(i+1)*188]
        if pkt[0] != 0x47: continue
        if (((pkt[1] & 0x1F) << 8) | pkt[2]) != pid: continue
        afc = (pkt[3] >> 4) & 0x3
        off = 4
        if afc & 0x2:
            off = 4 + 1 + pkt[4]
        if (afc & 0x1) and off < 188:
            out.extend(pkt[off:188])
    return bytes(out)


def find_avcc(buf: bytes) -> tuple[bytes, bytes] | None:
    """Locate AVCDecoderConfigurationRecord; return (sps, pps) NAL bytes."""
    for i in range(len(buf) - 16):
        if buf[i] != 0x01:
            continue
        prof = buf[i+1]
        if prof not in (0x42, 0x4D, 0x58, 0x64, 0x6E):
            continue
        lvl = buf[i+3]
        if not (0x09 <= lvl <= 0x42):
            continue
        if (buf[i+4] & 0xFC) != 0xFC:
            continue
        if (buf[i+5] & 0xE0) != 0xE0:
            continue
        n_sps = buf[i+5] & 0x1F
        if n_sps != 1:
            continue
        sps_len = (buf[i+6] << 8) | buf[i+7]
        sps_start = i + 8
        if sps_start + sps_len > len(buf): continue
        sps = buf[sps_start:sps_start + sps_len]
        if not sps or sps[0] != 0x67:
            continue
        # PPS section follows
        p = sps_start + sps_len
        if p >= len(buf): continue
        n_pps = buf[p]
        if n_pps != 1:
            continue
        pps_len = (buf[p+1] << 8) | buf[p+2]
        pps_start = p + 3
        if pps_start + pps_len > len(buf): continue
        pps = buf[pps_start:pps_start + pps_len]
        if not pps or pps[0] != 0x68:
            continue
        return (sps, pps)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ts", type=Path)
    ap.add_argument("--sd-pid", type=lambda s: int(s, 0), default=0x112)
    ap.add_argument("--h264-in", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    sd_buf = collect_pid_payload(args.ts, args.sd_pid)
    print(f"SD/BIFS buffer ({args.sd_pid:#x}): {len(sd_buf):,} bytes")

    res = find_avcc(sd_buf)
    if res is None:
        print("ERROR: no AVCDecoderConfigurationRecord found.  Try a different PID?")
        return 1
    sps, pps = res
    print(f"\nSPS ({len(sps)} B): {sps.hex()}")
    print(f"  profile_idc = 0x{sps[1]:02x}  level_idc = 0x{sps[3]:02x}")
    print(f"PPS ({len(pps)} B): {pps.hex()}")

    h264 = args.h264_in.read_bytes()
    # Annex B byte stream: prepend SPS + PPS with 00 00 00 01 start codes
    head = b"\x00\x00\x00\x01" + sps + b"\x00\x00\x00\x01" + pps
    args.out.write_bytes(head + h264)
    print(f"\nwrote {args.out}  "
          f"({len(head)} B header + {len(h264):,} B ES = {args.out.stat().st_size:,} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
