"""Smarter PMT injection: declare correct MPEG-4 stream_types + include
MPEG-4 IOD descriptor pointing at the OD stream, so dmb-ffmpeg's SL-OD
parser kicks in and unwraps SL-packetized video/audio into raw H.264 +
BSAC elementary streams.

PMT layout (mirrors a real DMB broadcast as observed in Eo & Bahk 2024
Fig. 4):
  Program 1 → PMT PID 0x100
    program_info_loop:
      MPEG-4_IOD_descriptor (tag 0x1D)  → IOD with OD ES_ID = 0x01
    ES loop:
      PID 0x111 stream_type 0x13 (ISO/IEC 14496-1 sections, OD)
                + SL_descriptor pointing to ES_ID 0x01
      PID 0x112 stream_type 0x13 (ISO/IEC 14496-1 sections, SD/BIFS)
                + SL_descriptor pointing to ES_ID 0x02
      PID 0x113 stream_type 0x12 (MPEG-4 PES, video)
                + SL_descriptor pointing to ES_ID 0x03
      PID 0x114 stream_type 0x12 (MPEG-4 PES, audio)
                + SL_descriptor pointing to ES_ID 0x04
"""
from __future__ import annotations
import argparse
import struct
import sys
from pathlib import Path

POLY = 0x04C11DB7
def crc32_mpeg2(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b << 24
        for _ in range(8):
            crc = ((crc << 1) ^ POLY) & 0xFFFFFFFF if (crc & 0x80000000) else (crc << 1) & 0xFFFFFFFF
    return crc


def fix_section_length(section: bytes) -> bytes:
    """Section header: table_id(8) syntax(1) zero(1) reserved(2) length(12) ..."""
    s = bytearray(section)
    section_length = len(s) - 3 + 4   # body + CRC bytes after header
    s[1] = 0xb0 | ((section_length >> 8) & 0x0F)
    s[2] = section_length & 0xff
    crc = crc32_mpeg2(bytes(s))
    return bytes(s) + struct.pack('>I', crc)


def build_pat(prog_pmt_pid_pairs, ts_id=1, version=0):
    body = bytearray()
    body.append(0x00)        # PAT
    body.extend(b'\xb0\x00') # length placeholder
    body.extend(struct.pack('>H', ts_id))
    body.append(0xc1 | (version << 1))
    body.append(0x00)
    body.append(0x00)
    for prog, pmt_pid in prog_pmt_pid_pairs:
        body.extend(struct.pack('>HH', prog, 0xe000 | (pmt_pid & 0x1fff)))
    return fix_section_length(bytes(body))


def sl_descriptor(es_id: int) -> bytes:
    """ISO/IEC 13818-1 SL_descriptor (tag 0x1F) — points an ES to an MPEG-4
    ES_ID."""
    payload = struct.pack('>H', es_id)
    return bytes([0x1F, len(payload)]) + payload


def es_id_inc_descriptor(es_id: int) -> bytes:
    """Inside IOD's ES_Descriptor field, identify each ES by ES_ID."""
    payload = struct.pack('>H', es_id)
    return bytes([0x0E, len(payload)]) + payload   # tag 0x0E = ES_ID_Inc


def iod_descriptor() -> bytes:
    """MPEG-4_IOD_descriptor (tag 0x1D) — includes a small InitialObjectDescriptor.

    Minimal IOD body:
      Scope_of_IOD_label (8)  = 0x00
      IOD_label          (8)  = 0x01
      InitialObjectDescriptor (MPEG-4 Systems class tag 0x02):
        ObjectDescriptorID(10) URL_Flag(1) includeInlineProfileLevelFlag(1) reserved(4)
        ODProfileLevelIndication(8)
        sceneProfileLevelIndication(8)
        audioProfileLevelIndication(8)
        visualProfileLevelIndication(8)
        graphicsProfileLevelIndication(8)
        ES_ID_Inc descriptors for each ES
    """
    iod = bytearray()
    # InitialObjectDescriptor body (after class-tag + length bytes)
    iod_body = bytearray()
    # ObjectDescriptorID(10) URL(1) IncProfile(1) reserved(4) — 16 bits packed
    OD_ID = 1
    URL = 0
    IncProfile = 1
    iod_body.append((OD_ID >> 2) & 0xFF)
    iod_body.append(((OD_ID & 0x03) << 6) | (URL << 5) | (IncProfile << 4) | 0x0F)
    # Profile level indications
    iod_body.append(0xFE)  # OD: 0xFE = no OD profile
    iod_body.append(0xFF)  # scene: 0xFF = none required
    iod_body.append(0xFF)  # audio: 0xFF = no audio profile required (MPEG-4 audio)
    iod_body.append(0xFF)  # visual: 0xFF
    iod_body.append(0xFF)  # graphics: 0xFF
    # ES_ID_Inc descriptors
    iod_body.extend(es_id_inc_descriptor(0x01))   # OD
    iod_body.extend(es_id_inc_descriptor(0x02))   # SD
    iod_body.extend(es_id_inc_descriptor(0x03))   # video
    iod_body.extend(es_id_inc_descriptor(0x04))   # audio
    # Wrap with InitialObjectDescriptor class tag (0x02) + length
    inner = bytearray()
    inner.append(0x02)            # IOD class tag
    inner.append(len(iod_body))   # length (short form, <= 127)
    inner.extend(iod_body)
    # MPEG-4_IOD_descriptor (PMT level): tag 0x1D, length, scope, label, IOD
    body = bytearray()
    body.append(0x00)             # Scope_of_IOD_label
    body.append(0x01)             # IOD_label
    body.extend(inner)
    return bytes([0x1D, len(body)]) + body


def build_pmt(prog: int, pcr_pid: int, streams) -> bytes:
    """streams = list of (pid, stream_type, es_id_or_None)"""
    body = bytearray()
    body.append(0x02)
    body.extend(b'\xb0\x00')
    body.extend(struct.pack('>H', prog))
    body.append(0xc1)
    body.extend(b'\x00\x00')
    body.extend(struct.pack('>H', 0xe000 | (pcr_pid & 0x1fff)))
    iod = iod_descriptor()
    body.extend(struct.pack('>H', 0xf000 | (len(iod) & 0x0FFF)))
    body.extend(iod)
    for pid, stype, es_id in streams:
        body.append(stype)
        body.extend(struct.pack('>H', 0xe000 | (pid & 0x1fff)))
        info = sl_descriptor(es_id) if es_id is not None else b''
        body.extend(struct.pack('>H', 0xf000 | (len(info) & 0x0FFF)))
        body.extend(info)
    return fix_section_length(bytes(body))


def build_ts_packet(pid: int, payload: bytes, pusi=False, cc=0) -> bytes:
    pkt = bytearray(188)
    pkt[0] = 0x47
    pkt[1] = (0x40 if pusi else 0x00) | ((pid >> 8) & 0x1F)
    pkt[2] = pid & 0xFF
    pkt[3] = 0x10 | (cc & 0x0F)
    if pusi:
        pkt[4] = 0x00
        room = 188 - 5
        pkt[5:5 + min(len(payload), room)] = payload[:room]
        for k in range(5 + len(payload), 188):
            pkt[k] = 0xFF
    else:
        room = 188 - 4
        pkt[4:4 + min(len(payload), room)] = payload[:room]
        for k in range(4 + len(payload), 188):
            pkt[k] = 0xFF
    return bytes(pkt)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("inp", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--video-pid", type=lambda s: int(s, 0), default=0x113)
    ap.add_argument("--audio-pid", type=lambda s: int(s, 0), default=0x114)
    ap.add_argument("--od-pid", type=lambda s: int(s, 0), default=0x111)
    ap.add_argument("--sd-pid", type=lambda s: int(s, 0), default=0x112)
    ap.add_argument("--pmt-pid", type=lambda s: int(s, 0), default=0x100)
    args = ap.parse_args()

    pat = build_pat([(1, args.pmt_pid)])
    pmt = build_pmt(prog=1, pcr_pid=args.video_pid, streams=[
        (args.od_pid,    0x13, 0x01),  # ISO 14496-1 sections (OD) + ES_ID 1
        (args.sd_pid,    0x13, 0x02),  # ISO 14496-1 sections (SD) + ES_ID 2
        (args.video_pid, 0x12, 0x03),  # MPEG-4 PES (SL-pkt video) + ES_ID 3
        (args.audio_pid, 0x12, 0x04),  # MPEG-4 PES (SL-pkt audio) + ES_ID 4
    ])
    print(f"PAT ({len(pat)} B): {pat.hex(' ')}")
    print(f"PMT ({len(pmt)} B): {pmt.hex(' ')}")

    pat_pkt = build_ts_packet(0x000, pat, pusi=True, cc=0)
    pmt_pkt = build_ts_packet(args.pmt_pid, pmt, pusi=True, cc=0)

    inp = args.inp.read_bytes()
    n_pkts = len(inp) // 188
    with args.out.open("wb") as f:
        # Open with PSI table set
        f.write(pat_pkt); f.write(pmt_pkt)
        for i in range(n_pkts):
            pkt = inp[i * 188:(i + 1) * 188]
            if pkt[0] != 0x47: continue
            pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
            if pid in (0x000, args.pmt_pid):
                continue            # drop original (corrupted) PSI
            f.write(pkt)
            if (i % 200) == 0:
                f.write(pat_pkt); f.write(pmt_pkt)
    print(f"\nWrote {args.out} ({args.out.stat().st_size} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
