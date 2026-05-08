"""MSC sub-channel byte extractor.

ETI MST holds the concatenated sub-channel data (per stream described in STC).
For each ETI frame we have one CIF (Common Interleaved Frame) per sub-channel.
The eti-cmdline binary already removed the inner FEC (Viterbi+puncturing), so
the bytes we receive are post-inner-decode, ready for the *outer* RS+TI stage
that Korean T-DMB applies.

For a given sub-channel id we slice out the right portion of MST per frame and
yield raw byte stream that must then be fed through the time deinterleaver and
RS decoder to recover MPEG-2 TS packets.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterator

from .eti.frame import EtiFrame


@dataclass
class SubChannelStream:
    sub_ch_id: int
    bytes_per_cif: int      # = sub-channel size_cu * 8 bytes (capacity unit -> 8 bytes)


def extract_subchannel(frames: Iterator[EtiFrame], sub_ch_id: int) -> Iterator[bytes]:
    """Yield the bytes belonging to `sub_ch_id` from each frame's MST."""
    for fr in frames:
        if fr.err != 0xFF:    # 0xFF == no error per ETI(NI)
            # mark with empty payload to keep cadence
            yield b""
            continue
        # MST is laid out per stream order in fr.streams. We just match SCID.
        offset = 0
        for s in fr.streams:
            length = s.length_bytes
            if s.scid == sub_ch_id:
                yield bytes(fr.msc[offset : offset + length])
                break
            offset += length
        else:
            yield b""


def find_subchannel_size(frames: Iterator[EtiFrame], sub_ch_id: int) -> int | None:
    """Inspect a frame's STC and return bytes-per-CIF for the matching SubChId."""
    for fr in frames:
        for s in fr.streams:
            if s.scid == sub_ch_id:
                return s.length_bytes
        return None  # first frame without match → unknown
    return None
