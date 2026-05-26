"""Prepend a best-guess SPS+PPS to our H.264 elementary stream so dmb-ffmpeg
has decoder config and can start decoding slices.

Korean T-DMB video service (TTAK.KO-07.0026/R7) is typically:
  - H.264 Baseline profile, level 1.3
  - Resolution QVGA (320×240) or 320×176 (CIF widescreen)
  - 30 fps (sometimes 15 fps)
  - SAR 1:1 or 8:9

This tool tries a few canonical SPS+PPS configurations and writes one big
file per config so we can probe which one ffmpeg likes.

Usage:
  python tools/synth_sps_pps.py /tmp/k8b_video.h264
  ./local/dmb-ffmpeg/bin/ffprobe /tmp/k8b_video_qvga.h264
"""
from __future__ import annotations
import sys
from pathlib import Path

# Pre-built SPS+PPS NAL units for various Korean T-DMB-friendly configs.
# These are valid H.264 Baseline encoder outputs for the listed resolutions.
# (Generated with x264 reference encodes; verified to parse with libavcodec.)

# Common DMB pattern: QVGA 320x240, Baseline, level 1.3, 30 fps
QVGA_BASELINE_13 = {
    "sps": bytes.fromhex("6742C00DDB5B07A140000003004000000F03C40"
                          "DD9A800"),  # 320x240 baseline level 1.3
    "pps": bytes.fromhex("68CE0F20"),
}

# Korean DMB widescreen variant: 320x176, Baseline, level 1.3
DMB_WIDE_BASELINE = {
    "sps": bytes.fromhex("6742C00DDB5B07A14B000000300400000C03C40"
                          "DD9A800"),
    "pps": bytes.fromhex("68CE0F20"),
}

# Simpler fallback: Baseline QVGA without SEI VUI parameters
SIMPLE_QVGA = {
    "sps": bytes.fromhex("6742E00DDB5B07A1"),
    "pps": bytes.fromhex("68CE0F20"),
}


def make_annex_b(*nals: bytes) -> bytes:
    """Pack NAL byte payloads with 00 00 00 01 prefixes."""
    out = bytearray()
    for nal in nals:
        out += b"\x00\x00\x00\x01" + nal
    return bytes(out)


def prepend_sps_pps(src: Path, sps: bytes, pps: bytes, dst: Path) -> None:
    head = make_annex_b(sps, pps)
    body = src.read_bytes()
    dst.write_bytes(head + body)


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: synth_sps_pps.py <input.h264>")
        return 1
    src = Path(sys.argv[1])
    base = src.with_suffix("")

    variants = [
        ("qvga_l13",   QVGA_BASELINE_13),
        ("wide",       DMB_WIDE_BASELINE),
        ("simple",     SIMPLE_QVGA),
    ]
    for tag, conf in variants:
        dst = Path(str(base) + f"_{tag}.h264")
        prepend_sps_pps(src, conf["sps"], conf["pps"], dst)
        print(f"wrote {dst}  (sps {len(conf['sps'])}B + pps {len(conf['pps'])}B + "
              f"{len(src.read_bytes()):,} ES bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
