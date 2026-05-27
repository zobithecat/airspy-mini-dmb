"""Korean T-DMB outer FEC pipeline (ETSI TS 102 427).

Chain:
  1. Convolutional time deinterleaver (Forney, 12 branches × 17 bytes)
  2. Reed-Solomon (204, 188) decoder

Input:  raw MSC sub-channel byte stream from ETI (one CIF per ETI frame,
        concatenated). The stream contains 204-byte RS blocks that have
        been Forney-conv-interleaved at the transmitter.
Output: 188-byte MPEG-2 TS packets (sync byte 0x47 first).

Per Eo & Bahk 2024 (§III-B) and our own analysis on `k8b_100pct.eti`:
    "The deinterleaver requires the sync byte of a TS packet to be the
    first in an input byte chunk."

Why: the TX-side Forney interleaver routes byte i of an RS block through
branch (i mod 12) with i*17 cycles of delay.  Byte 0 (= 0x47) goes through
branch 0 with no delay, so 0x47 lands at TX-output positions 0, 204, 408,…
But our ETI captures don't start exactly at "byte 0 of RS block #0" — we
typically start somewhere in the middle (empirically offset 160 within the
204-byte cycle).  If we feed those bytes straight into the RX deinterleaver
which always starts on branch 0, the branch indices are off by
(160 mod 12) = 4 → RS blocks come out byte-scrambled and RS decode fails
on every block, even though the 0x47 sync pattern is preserved at the
right cadence.

Fix: in `feed()` we first locate the 0x47 alignment by hit-counting at
204-byte stride across the raw input, then DISCARD the bytes up to that
offset and only feed the deinterleaver from the 0x47 onwards.  After the
2244-byte settling transient (= (N-1)·M·N) the deinterleaver emits clean
204-byte RS codewords ready for systematic RS(204,188) correction.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterator

from .interleaver import ConvolutionalDeinterleaver, N_BRANCHES, M_DEPTH
from .rs import RSDecoder

TS_PACKET_SIZE = 188
RS_BLOCK_SIZE = 204
# Conv interleaver chain latency = (N-1)·M·N = 11·17·12 = 2244 bytes
DEINTERLEAVER_LATENCY = (N_BRANCHES - 1) * M_DEPTH * N_BRANCHES
# Min bytes we need to confidently pick the 0x47 phase before locking in.
# 50 RS blocks ≈ 10 200 bytes ⇒ ~50 hits expected if signal is real.
PRESYNC_PROBE_BYTES = RS_BLOCK_SIZE * 50


@dataclass
class TsPacket:
    data: bytes               # 188 bytes, byte 0 = 0x47
    rs_errors: int            # 0..8 corrected, -1 if RS uncorrectable


class KoreanTDmbOuterFec:
    """Stream pipeline: align → deinterleave → RS decode → TS packets.

    Usage::

        fec = KoreanTDmbOuterFec()
        for chunk in subch_chunks:           # one CIF worth of bytes
            for ts in fec.feed(chunk):
                # ts.data is a 188-byte MPEG-2 TS packet
                ...
        stats = fec.stats
    """

    def __init__(self) -> None:
        self._presync_buf = bytearray()   # raw bytes waiting for phase lock
        self._aligned = False             # did we lock the 0x47 phase?
        self._phase_offset = -1           # selected 204-byte cycle phase

        self._di: ConvolutionalDeinterleaver | None = None
        self._di_skip_remaining = DEINTERLEAVER_LATENCY  # transient to drop
        self._post_di_buf = bytearray()
        self._rs = RSDecoder()

        self.bytes_in = 0
        self.bytes_discarded_presync = 0
        self.uncorrectable = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def feed(self, data: bytes) -> Iterator[TsPacket]:
        """Push raw MSC sub-channel bytes; yield 0..N TS packets."""
        if not data:
            return
        self.bytes_in += len(data)

        if not self._aligned:
            self._presync_buf.extend(data)
            if len(self._presync_buf) < PRESYNC_PROBE_BYTES:
                return
            self._try_align()
            if not self._aligned:
                # Keep buffer bounded if we never find sync
                if len(self._presync_buf) > 4 * PRESYNC_PROBE_BYTES:
                    drop = len(self._presync_buf) - PRESYNC_PROBE_BYTES
                    del self._presync_buf[:drop]
                    self.bytes_discarded_presync += drop
                return
            # Aligned: feed the survivor bytes through deinterleaver
            yield from self._feed_aligned_initial()
        else:
            # Steady-state
            yield from self._feed_aligned(data)

    @property
    def stats(self) -> dict:
        return {
            "bytes_in": self.bytes_in,
            "aligned": self._aligned,
            "phase_offset": self._phase_offset,
            "rs_total": self._rs.frames_total,
            "rs_ok": self._rs.frames_ok,
            "rs_corrected": self._rs.frames_corrected,
            "rs_failed": self._rs.frames_failed,
            "uncorrectable": self.uncorrectable,
            "bytes_discarded_presync": self.bytes_discarded_presync,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _try_align(self) -> None:
        """Score each of 204 phases by 0x47 hit-rate; lock if any phase
        clearly dominates the background.

        Two acceptance paths:
          High-SNR (k8b_100pct ~13 dB):  best > 40% of blocks AND
                                          best > 5× rank2.
          Low-SNR  (k8b_rust   ~7  dB):  best > 10% of blocks AND
                                          best > 15× rank2.
        The ratio-based path catches weak captures where bit errors
        corrupt enough 0x47 bytes to drag the hit rate below 40%, but
        where the surviving 0x47 cadence at the correct phase is still
        a runaway winner vs. random background (~0.5%/phase)."""
        buf = self._presync_buf
        n = len(buf)
        n_blocks = n // RS_BLOCK_SIZE
        if n_blocks < 20:
            return
        scores = [0] * RS_BLOCK_SIZE
        for k in range(n_blocks):
            base = k * RS_BLOCK_SIZE
            for ph in range(RS_BLOCK_SIZE):
                if buf[base + ph] == 0x47:
                    scores[ph] += 1
        best = max(range(RS_BLOCK_SIZE), key=lambda p: scores[p])
        sorted_hits = sorted(scores, reverse=True)
        first, second = sorted_hits[0], sorted_hits[1]
        # Path 1: classic high-SNR criterion
        if first >= max(8, n_blocks * 0.40) and first >= second * 5:
            self._phase_offset = best
            self._aligned = True
            return
        # Path 2: low-SNR ratio-based criterion
        if first >= max(8, n_blocks * 0.10) and first >= second * 15:
            self._phase_offset = best
            self._aligned = True
            return

    def _feed_aligned_initial(self) -> Iterator[TsPacket]:
        """First call after alignment: trim presync buffer to the 0x47
        position and start the deinterleaver from there."""
        if self._phase_offset > 0:
            del self._presync_buf[: self._phase_offset]
            self.bytes_discarded_presync += self._phase_offset
        self._di = ConvolutionalDeinterleaver()
        survivor = bytes(self._presync_buf)
        self._presync_buf = bytearray()
        yield from self._feed_aligned(survivor)

    def _feed_aligned(self, data: bytes) -> Iterator[TsPacket]:
        assert self._di is not None
        deint = self._di.feed(data)
        if self._di_skip_remaining > 0:
            if len(deint) <= self._di_skip_remaining:
                self._di_skip_remaining -= len(deint)
                return
            deint = deint[self._di_skip_remaining:]
            self._di_skip_remaining = 0
        self._post_di_buf.extend(deint)
        yield from self._drain_rs()

    def _drain_rs(self) -> Iterator[TsPacket]:
        buf = self._post_di_buf
        while len(buf) >= RS_BLOCK_SIZE:
            block = bytes(buf[:RS_BLOCK_SIZE])
            del buf[:RS_BLOCK_SIZE]
            res = self._rs.decode(block)
            if not res.ok:
                self.uncorrectable += 1
                yield TsPacket(data=res.data[:TS_PACKET_SIZE], rs_errors=-1)
            else:
                yield TsPacket(data=res.data[:TS_PACKET_SIZE],
                               rs_errors=res.errors)


def collect_msc_to_ts(eti_path, sub_ch_id: int) -> tuple[list[TsPacket], dict]:
    """Convenience: run the full RS+TI pipeline on every CIF of `sub_ch_id`
    from an ETI(NI) capture; return (packets, fec.stats)."""
    from pathlib import Path
    import sys

    # Local imports so this module stays usable without these deps loaded
    from tdmb.eti import parse_frame, FRAME_SIZE
    from tdmb.msc import extract_subchannel

    def _frames(path):
        with Path(path).open("rb") as f:
            while True:
                chunk = f.read(FRAME_SIZE)
                if len(chunk) != FRAME_SIZE:
                    return
                try:
                    yield parse_frame(chunk)
                except Exception:
                    continue

    fec = KoreanTDmbOuterFec()
    packets: list[TsPacket] = []
    for chunk in extract_subchannel(_frames(eti_path), sub_ch_id):
        if chunk:
            for ts in fec.feed(chunk):
                packets.append(ts)
    return packets, fec.stats
