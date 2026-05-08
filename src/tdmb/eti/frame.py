"""ETI(NI, G.703) frame parser per ETSI EN 300 799.

Each frame is 6144 bytes (= 24 ms @ 2.048 Mbit/s) with structure:
    SYNC (4)  ERR(1) + FSYNC(3, alternates 07-3A-B6 / F8-C5-49)
    FC   (4)  FCT[8] FICF[1] NST[7] FP[3] MID[2] FL[11]
    STC  (4*NST) per stream: SCID[6] SAD[10] TPL[6] STL[10]
    EOH  (4)  MNSC[16] CRC[16]
    MST  (variable) FIC + sub-channel CIFs
    EOF  (4)  CRC[16] RFU[16]
    TIST (4)
    + padding to 6144

The MST always begins with the FIC when FICF=1 (Mode I: 96 bytes = 4 FIBs).
Each FIB = 30 byte payload + 2 byte CRC. Sub-channel data follows in MST.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Iterable, Iterator

FRAME_SIZE = 6144
FSYNC_EVEN = b"\x07\x3a\xb6"
FSYNC_ODD = b"\xf8\xc5\x49"
FIB_SIZE = 32
FIC_FIBS_MODE_I = 4  # mode I: 4 FIBs per frame


@dataclass
class StreamChar:
    scid: int    # sub-channel id (0..63)
    sad: int     # start address in CIF (0..863)
    tpl: int     # protection level encoding
    stl: int     # stream length in 64-bit groups (i.e. *8 bytes)

    @property
    def length_bytes(self) -> int:
        return self.stl * 8


@dataclass
class EtiFrame:
    err: int
    fct: int                 # frame counter (0..249)
    fic_present: bool
    nst: int                 # number of streams in MST
    fp: int                  # frame phase
    mid: int                 # mode id (1=TM-I, 2=TM-II, 3=TM-III, 0=TM-IV)
    fl: int                  # frame length in 32-bit words
    streams: list[StreamChar] = field(default_factory=list)
    fic: bytes = b""         # FIC bytes (raw, includes CRCs)
    msc: bytes = b""         # sub-channel data bytes (concatenated CIFs)


class EtiSyncError(ValueError):
    pass


def _u16be(b: bytes, off: int) -> int:
    return (b[off] << 8) | b[off + 1]


def parse_frame(buf: bytes) -> EtiFrame:
    if len(buf) < FRAME_SIZE:
        raise EtiSyncError(f"short frame {len(buf)}")
    fsync = buf[1:4]
    if fsync != FSYNC_EVEN and fsync != FSYNC_ODD:
        raise EtiSyncError(f"bad fsync {fsync.hex()}")

    err = buf[0]

    # FC: 4 bytes at offset 4. Layout:
    #   byte 4: FCT (8b)
    #   byte 5: FICF(1) | NST(7)
    #   byte 6: FP(3) | MID(2) | FL_hi(3)
    #   byte 7: FL_lo(8)
    fct = buf[4]
    ficf = (buf[5] >> 7) & 1
    nst = buf[5] & 0x7F
    fp = (buf[6] >> 5) & 0x07
    mid = (buf[6] >> 3) & 0x03
    fl = ((buf[6] & 0x07) << 8) | buf[7]

    streams: list[StreamChar] = []
    off = 8
    for _ in range(nst):
        # STC 32-bit fields:
        #   SCID(6) SAD(10) TPL(6) STL(10)
        w = (buf[off] << 24) | (buf[off + 1] << 16) | (buf[off + 2] << 8) | buf[off + 3]
        scid = (w >> 26) & 0x3F
        sad = (w >> 16) & 0x3FF
        tpl = (w >> 10) & 0x3F
        stl = w & 0x3FF
        streams.append(StreamChar(scid, sad, tpl, stl))
        off += 4

    # EOH (4 bytes) -> skip
    off += 4

    # MST: FIC (if FICF) + sub-channel data
    fic_len = (FIB_SIZE * FIC_FIBS_MODE_I) if (ficf and mid != 3) else 0
    # mode III uses 3 FIBs of 32 bytes (older spec); we treat anything but 0 as mode I
    fic = buf[off:off + fic_len] if fic_len else b""
    off += fic_len

    msc_total = sum(s.length_bytes for s in streams)
    msc = buf[off:off + msc_total]

    return EtiFrame(
        err=err,
        fct=fct,
        fic_present=bool(ficf),
        nst=nst,
        fp=fp,
        mid=mid,
        fl=fl,
        streams=streams,
        fic=fic,
        msc=msc,
    )


def iter_frames(stream: Iterable[bytes]) -> Iterator[EtiFrame]:
    """Yield EtiFrame from a chunked byte iterable.

    Resyncs by scanning for FSYNC if a frame fails to parse cleanly.
    """
    buf = bytearray()
    for chunk in stream:
        buf.extend(chunk)
        while len(buf) >= FRAME_SIZE:
            try:
                yield parse_frame(bytes(buf[:FRAME_SIZE]))
                del buf[:FRAME_SIZE]
            except EtiSyncError:
                # try to find next sync
                start = _find_sync(buf, 1)
                if start < 0:
                    # keep the last 3 bytes for partial sync match
                    del buf[: max(0, len(buf) - 3)]
                    break
                del buf[:start]


def _find_sync(buf: bytes | bytearray, start: int) -> int:
    n = len(buf) - 4
    i = start
    while i < n:
        s = bytes(buf[i + 1 : i + 4])
        if s == FSYNC_EVEN or s == FSYNC_ODD:
            return i
        i += 1
    return -1
