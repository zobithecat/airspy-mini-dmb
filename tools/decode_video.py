"""End-to-end Korean T-DMB video decoder.

Drives the complete Stage 1+2+3 pipeline on a stored ETI capture:

  ETI(NI) frames
    → MSC sub-channel byte stream                (tdmb.msc.extract_subchannel)
    → Forney conv deinterleaver (sync-aligned)   (tdmb.fec.outer, this fix is
                                                  what un-stuck RS — see
                                                  outer.py docstring)
    → RS(204,188) decode → clean 188-byte TS     (tdmb.fec.outer)
    → SL packet header strip (9 B, AU-start)     (extract_sl_h264_v2 logic)
    → H.264 NAL unit per PES (no start codes)
    → prepend SPS + PPS from SD/BIFS stream      (extract_avcc_from_sd logic)
    → Annex-B byte stream
    → dmb-ffmpeg → decoded YUV / PNG / mp4

Usage:
  python tools/decode_video.py data/captures/k8b_100pct.eti \\
      --subch 1 --video-pid 0x113 --sd-pid 0x112 \\
      --out-h264 /tmp/k8b_video.h264 \\
      --out-mp4  /tmp/k8b_video.mp4

  python tools/decode_video.py data/captures/k8b_100pct.eti \\
      --subch 1 --idr-thumbnails /tmp/idr      # extract IDR PNG snapshots only
"""
from __future__ import annotations
import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from tdmb.fec.outer import collect_msc_to_ts


# --- TS demux ---------------------------------------------------------------

def collect_pid_payload(ts_bytes: bytes, pid: int) -> bytes:
    """Concatenate every TS packet payload for a given PID."""
    out = bytearray()
    for i in range(len(ts_bytes) // 188):
        pkt = ts_bytes[i*188:(i+1)*188]
        if pkt[0] != 0x47:
            continue
        if (((pkt[1] & 0x1F) << 8) | pkt[2]) != pid:
            continue
        afc = (pkt[3] >> 4) & 0x3
        off = 4
        if afc & 0x2:
            off = 4 + 1 + pkt[4]
        if (afc & 0x1) and off < 188:
            out.extend(pkt[off:188])
    return bytes(out)


def iter_pes_bodies(ts_bytes: bytes, pid: int):
    """Yield one bytes object per PES seen on `pid` (split at PUSI)."""
    cur = bytearray()
    started = False
    for i in range(len(ts_bytes) // 188):
        pkt = ts_bytes[i*188:(i+1)*188]
        if pkt[0] != 0x47:
            continue
        if (((pkt[1] & 0x1F) << 8) | pkt[2]) != pid:
            continue
        pusi = (pkt[1] & 0x40) != 0
        afc = (pkt[3] >> 4) & 0x3
        off = 4
        if afc & 0x2:
            off = 4 + 1 + pkt[4]
        if not (afc & 0x1) or off >= 188:
            continue
        payload = pkt[off:188]
        if pusi:
            if started and cur:
                yield bytes(cur)
            cur = bytearray(payload)
            started = True
        elif started:
            cur.extend(payload)
    if started and cur:
        yield bytes(cur)


# --- SL packet → H.264 NAL extraction ---------------------------------------

SL_HDR_LEN = 9       # 6-bit flags + 33-bit DTS + 33-bit CTS = 72 bits


def split_pes(pes: bytes) -> bytes | None:
    if len(pes) < 9 or pes[0:3] != b'\x00\x00\x01':
        return None
    pes_len = (pes[4] << 8) | pes[5]
    hdr_len = pes[8]
    body_start = 9 + hdr_len
    body_end = min(6 + pes_len, len(pes)) if pes_len else len(pes)
    if body_start >= body_end:
        return None
    return pes[body_start:body_end]


def extract_nal(pes_body: bytes) -> bytes | None:
    """Strip 9-byte SL header, return raw NAL bytes (no start code)."""
    if len(pes_body) <= SL_HDR_LEN or pes_body[0] != 0xCF:
        return None
    return pes_body[SL_HDR_LEN:]


# --- AVCC search in SD/BIFS stream ------------------------------------------

def find_avcc(buf: bytes) -> tuple[bytes, bytes] | None:
    """Locate the embedded AVCDecoderConfigurationRecord; return (sps, pps)."""
    for i in range(len(buf) - 16):
        if buf[i] != 0x01:
            continue
        prof = buf[i+1]
        if prof not in (0x42, 0x4D, 0x58, 0x64, 0x6E):
            continue
        if not (0x09 <= buf[i+3] <= 0x42):
            continue
        if (buf[i+4] & 0xFC) != 0xFC:
            continue
        if (buf[i+5] & 0xE0) != 0xE0 or (buf[i+5] & 0x1F) != 1:
            continue
        sps_len = (buf[i+6] << 8) | buf[i+7]
        if i + 8 + sps_len > len(buf):
            continue
        sps = buf[i+8:i+8+sps_len]
        if not sps or sps[0] != 0x67:
            continue
        p = i + 8 + sps_len
        if p + 3 > len(buf) or buf[p] != 1:
            continue
        pps_len = (buf[p+1] << 8) | buf[p+2]
        if p + 3 + pps_len > len(buf):
            continue
        pps = buf[p+3:p+3+pps_len]
        if not pps or pps[0] != 0x68:
            continue
        return sps, pps
    return None


# --- Driver ----------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                 description=__doc__)
    ap.add_argument("eti", type=Path, help="ETI(NI) capture file")
    ap.add_argument("--subch", type=int, default=1,
                    help="MSC sub-channel id (default 1 = mYTN on K8B)")
    ap.add_argument("--video-pid", type=lambda s: int(s, 0), default=0x113)
    ap.add_argument("--sd-pid", type=lambda s: int(s, 0), default=0x112,
                    help="PID carrying SD/BIFS sections with AVCC (default 0x112)")
    ap.add_argument("--out-h264", type=Path, default=Path("/tmp/dmb_video.h264"))
    ap.add_argument("--out-mp4", type=Path,
                    help="if set, also remux to MP4 via dmb-ffmpeg")
    ap.add_argument("--idr-thumbnails", type=Path,
                    help="extract IDR-frame PNGs to this dir")
    args = ap.parse_args()

    print(f"### Stage 1+2: ETI → RS-corrected TS ###")
    packets, fec_stats = collect_msc_to_ts(args.eti, args.subch)
    ts_bytes = bytearray()
    pid_ctr = Counter()
    for p in packets:
        if p.rs_errors < 0 or not p.data or p.data[0] != 0x47:
            continue
        ts_bytes.extend(p.data)
        pid_ctr[((p.data[1] & 0x1F) << 8) | p.data[2]] += 1
    ts_bytes = bytes(ts_bytes)

    rs_succ = fec_stats["rs_ok"] + fec_stats["rs_corrected"]
    print(f"  RS success: {rs_succ}/{fec_stats['rs_total']} "
          f"({rs_succ*100/max(1,fec_stats['rs_total']):.1f}%)")
    print(f"  TS packets (clean): {len(ts_bytes)//188:,}")
    print(f"  Top PIDs: {pid_ctr.most_common(5)}")

    print(f"\n### Stage 3a: extract SPS/PPS from SD/BIFS (PID {args.sd_pid:#x}) ###")
    sd_buf = collect_pid_payload(ts_bytes, args.sd_pid)
    avcc = find_avcc(sd_buf)
    if avcc is None:
        print(f"  ERROR: no AVCDecoderConfigurationRecord in PID {args.sd_pid:#x}")
        return 1
    sps, pps = avcc
    print(f"  SPS ({len(sps)} B): {sps.hex()}")
    print(f"    profile_idc=0x{sps[1]:02x} level_idc=0x{sps[3]:02x}")
    print(f"  PPS ({len(pps)} B): {pps.hex()}")

    print(f"\n### Stage 3b: SL → H.264 NAL extraction (PID {args.video_pid:#x}) ###")
    nal_types = Counter()
    out_buf = bytearray()
    # Prepend SPS + PPS at the start of the byte stream
    out_buf += b"\x00\x00\x00\x01" + sps
    out_buf += b"\x00\x00\x00\x01" + pps
    total_pes, parsed = 0, 0
    for pes in iter_pes_bodies(ts_bytes, args.video_pid):
        total_pes += 1
        body = split_pes(pes)
        if body is None:
            continue
        nal = extract_nal(body)
        if nal is None:
            continue
        parsed += 1
        if (nal[0] >> 7) == 0:
            nal_types[nal[0] & 0x1F] += 1
        out_buf += b"\x00\x00\x00\x01" + nal

    args.out_h264.write_bytes(bytes(out_buf))
    print(f"  PES total: {total_pes}  SL parsed: {parsed}")
    NAMES = {1: "non-IDR", 5: "IDR", 7: "SPS", 8: "PPS", 9: "AUD"}
    for t in sorted(nal_types):
        print(f"  NAL type {t} ({NAMES.get(t, '?')}): {nal_types[t]}")
    print(f"  wrote {args.out_h264}  ({args.out_h264.stat().st_size:,} B)")

    ffmpeg = REPO / "local" / "dmb-ffmpeg" / "bin" / "ffmpeg"

    if args.idr_thumbnails:
        print(f"\n### Stage 3c: extract IDR thumbnails → {args.idr_thumbnails} ###")
        args.idr_thumbnails.mkdir(parents=True, exist_ok=True)
        cmd = [str(ffmpeg), "-hide_banner", "-loglevel", "error",
               "-f", "h264", "-i", str(args.out_h264),
               "-vf", "select='eq(pict_type,I)'", "-vsync", "vfr",
               "-frames:v", "20",
               str(args.idr_thumbnails / "idr_%03d.png")]
        subprocess.run(cmd, check=False)
        n = len(list(args.idr_thumbnails.glob("*.png")))
        print(f"  → {n} IDR frame PNG(s) written")

    if args.out_mp4:
        print(f"\n### Stage 3d: remux to MP4 → {args.out_mp4} ###")
        cmd = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error",
               "-f", "h264", "-i", str(args.out_h264),
               "-c:v", "copy", str(args.out_mp4)]
        subprocess.run(cmd, check=False)
        if args.out_mp4.exists():
            print(f"  wrote {args.out_mp4} ({args.out_mp4.stat().st_size:,} B)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
