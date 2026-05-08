"""Forney convolutional byte (de)interleaver as used by Korean T-DMB
outer FEC (ETSI TS 102 427 §7.2).

12 branches, depth M = 17 bytes per stage. Branch i on the encoder side
has i*M shift register stages; the matching decoder uses (N-1-i)*M, so
total chain delay is constant (N-1)*M*N = 11*17*12 = 2244 stream bytes
for every branch.
"""
from __future__ import annotations

N_BRANCHES = 12
M_DEPTH = 17


class _ShiftReg:
    """Exact-latency shift register: shift(b) outputs the byte that was
    put in `depth` cycles ago. The first `depth` outputs are zeros."""

    __slots__ = ("buf", "head")

    def __init__(self, depth: int) -> None:
        self.buf = [0] * depth   # may be size 0 (passthrough)
        self.head = 0

    def shift(self, b: int) -> int:
        if not self.buf:
            return b
        out = self.buf[self.head]
        self.buf[self.head] = b
        self.head = (self.head + 1) % len(self.buf)
        return out


class ConvolutionalDeinterleaver:
    """Stream-mode deinterleaver. Branch i has (N-1-i)*M stages of delay.

    Total end-to-end (interleaver + deinterleaver) latency is
    (N-1)*M*N = 2244 bytes — the first 2244 output bytes are garbage.
    """

    __slots__ = ("_regs", "_idx", "_pumped")

    def __init__(self) -> None:
        self._regs = [_ShiftReg((N_BRANCHES - 1 - i) * M_DEPTH)
                      for i in range(N_BRANCHES)]
        self._idx = 0
        self._pumped = 0

    def push_pull(self, b: int) -> int:
        out = self._regs[self._idx].shift(b)
        self._idx = (self._idx + 1) % N_BRANCHES
        self._pumped += 1
        return out

    def feed(self, data: bytes) -> bytes:
        out = bytearray(len(data))
        regs = self._regs
        idx = self._idx
        for k, b in enumerate(data):
            out[k] = regs[idx].shift(b)
            idx = (idx + 1) % N_BRANCHES
        self._idx = idx
        self._pumped += len(data)
        return bytes(out)

    @property
    def latency_bytes(self) -> int:
        # End-to-end (encoder+decoder) latency.
        return (N_BRANCHES - 1) * M_DEPTH * N_BRANCHES   # 2244


class ConvolutionalInterleaver:
    """Encoder side, useful for tests. Branch i has i*M stages."""

    __slots__ = ("_regs", "_idx")

    def __init__(self) -> None:
        self._regs = [_ShiftReg(i * M_DEPTH) for i in range(N_BRANCHES)]
        self._idx = 0

    def feed(self, data: bytes) -> bytes:
        out = bytearray(len(data))
        regs = self._regs
        idx = self._idx
        for k, b in enumerate(data):
            out[k] = regs[idx].shift(b)
            idx = (idx + 1) % N_BRANCHES
        self._idx = idx
        return bytes(out)
