"""Korean T-DMB outer FEC pipeline (ETSI TS 102 427).

Combines:
  1. Convolutional time deinterleaver (Forney, 12 branches × 17 bytes)
  2. Reed-Solomon (204, 188) decoder

Input:  MSC sub-channel byte stream (one CIF per ETI frame)
Output: 188-byte MPEG-2 TS packets

The TS sync byte (0x47) at the start of every 188-byte packet gives us
sync; once we've located it we can chop the post-RS stream into proper
TS packets even before lock is fully acquired.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Iterator

from .interleaver import ConvolutionalDeinterleaver
from .rs import RSDecoder, RsResult

TS_PACKET_SIZE = 188
RS_BLOCK_SIZE = 204


@dataclass
class TsPacket:
    data: bytes               # 188 bytes
    rs_errors: int            # 0..8 corrected, -1 uncorrectable


class KoreanTDmbOuterFec:
    """Stream pipeline: deinterleave -> RS decode -> TS packets.

    Usage:
        fec = KoreanTDmbOuterFec()
        for chunk in subch_chunks:           # one CIF worth of bytes
            for ts in fec.feed(chunk):
                # ts.data is a 188-byte MPEG-2 TS packet
                ...
    """

    def __init__(self) -> None:
        self.di = ConvolutionalDeinterleaver()
        self.rs = RSDecoder()
        self._buf = bytearray()         # post-deinterleave buffer
        self._synced = False            # have we found 0x47 alignment?
        self._sync_offset = 0           # byte offset within 204-byte block
        self.uncorrectable = 0
        self.bytes_in = 0

    def feed(self, data: bytes) -> Iterator[TsPacket]:
        """Push CIF bytes; yield 0..N TS packets."""
        if not data:
            return
        self.bytes_in += len(data)
        # Apply deinterleaver
        deint = self.di.feed(data)
        self._buf.extend(deint)

        # We need to find sync of 204-byte RS blocks. After RS decode the
        # first byte of each 188-byte packet is 0x47 (or 0xB8 = 0x47^0xFF
        # if DVB sync inversion applies — Korean T-DMB does NOT invert).
        # Strategy: try 204 candidate offsets, decode 4 consecutive blocks
        # and check that all yield 0x47 at byte 0.
        if not self._synced:
            yield from self._try_sync()
            return

        # Synced: decode whole 204-byte blocks
        yield from self._decode_aligned()

    # ----------------------------------------------------------------
    def _try_sync(self) -> Iterator[TsPacket]:
        # Need enough buffer to test several consecutive RS blocks.
        # Convolutional interleaver has 2244-byte end-to-end latency, so
        # the first ~2244 bytes are guaranteed garbage. Scan the whole
        # buffer for any position where 4 consecutive 204-byte blocks
        # RS-decode to a 0x47-starting TS packet.
        need = 204 * 5
        if len(self._buf) < need:
            return
        n = len(self._buf)
        # Look at every starting position; bail early once we've found one.
        for start in range(0, n - 204 * 4):
            # Quick filter: need 0x47 at output AFTER RS decode, but the
            # input byte at start is part of the RS codeword (often 0x47
            # because RS systematic encoding keeps data bytes intact).
            if self._buf[start] != 0x47:
                continue
            ok = 0
            for blk in range(4):
                p = start + blk * 204
                if p + 204 > n:
                    break
                res = self.rs.decode(bytes(self._buf[p:p + 204]))
                if res.ok and res.data and res.data[0] == 0x47:
                    ok += 1
                else:
                    break
            if ok >= 4:
                self._synced = True
                self._sync_offset = start % 204
                del self._buf[:start]
                yield from self._decode_aligned()
                return
        # Not found in this buffer; trim oldest data to keep bounded
        if n > 8 * 204:
            del self._buf[: n - 4 * 204]

    def _decode_aligned(self) -> Iterator[TsPacket]:
        while len(self._buf) >= 204:
            blk = bytes(self._buf[:204])
            del self._buf[:204]
            res = self.rs.decode(blk)
            if not res.ok:
                self.uncorrectable += 1
                # If we lose alignment, drop sync and re-search
                # (a single bad block can be tolerated)
                yield TsPacket(data=res.data[:188], rs_errors=-1)
                continue
            yield TsPacket(data=res.data[:188], rs_errors=res.errors)

    # ----------------------------------------------------------------
    @property
    def stats(self) -> dict:
        return {
            "bytes_in": self.bytes_in,
            "synced": self._synced,
            "rs_total": self.rs.frames_total,
            "rs_ok": self.rs.frames_ok,
            "rs_corrected": self.rs.frames_corrected,
            "rs_failed": self.rs.frames_failed,
            "uncorrectable": self.uncorrectable,
        }
