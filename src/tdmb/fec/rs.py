"""Reed-Solomon (204, 188, T=8) decoder for MPEG-2 TS / Korean T-DMB outer FEC.

Per ETSI TS 102 427 (§7.1), identical to DVB-T:
    Field GF(2^8) with primitive poly 0x11D
    Generator g(x) = ∏_{i=0}^{15} (x - α^i),  α = 2
    fcr = 0,  prim = 1

We delegate to the `reedsolo` library which is pure-Python but fast enough
for ~2 Mbit/s service streams; one TS service has ~700 RS frames/sec.
"""
from __future__ import annotations
from dataclasses import dataclass
import reedsolo

PRIM_POLY = 0x11D    # GF(2^8) primitive polynomial — DVB / MPEG-2 outer code
FCR = 0
GENERATOR = 2
N = 204
K = 188
NSYM = N - K          # 16

RS_OK = 0
RS_FAIL = -1


@dataclass(frozen=True)
class RsResult:
    data: bytes        # 188 bytes (the recovered TS packet)
    errors: int        # number of byte errors corrected; -1 if uncorrectable
    ok: bool


class RSDecoder:
    """Decode 204-byte RS-coded blocks back to 188-byte TS packets."""

    def __init__(self) -> None:
        # reedsolo uses generator-as-power; default fcr=0, generator=2 matches DVB.
        # We need GF(2^8) with prim=0x11D. reedsolo stashes tables in module globals
        # when first used, so initialise once here.
        reedsolo.init_tables(c_exp=8, prim=PRIM_POLY, generator=GENERATOR)
        self._codec = reedsolo.RSCodec(NSYM, fcr=FCR, prim=PRIM_POLY, generator=GENERATOR, c_exp=8)
        self.frames_total = 0
        self.frames_ok = 0
        self.frames_corrected = 0
        self.frames_failed = 0

    def decode(self, block: bytes) -> RsResult:
        if len(block) != N:
            raise ValueError(f"need {N}-byte RS block, got {len(block)}")
        self.frames_total += 1
        try:
            data, _, errata_pos = self._codec.decode(block)
        except reedsolo.ReedSolomonError:
            self.frames_failed += 1
            return RsResult(data=bytes(block[:K]), errors=-1, ok=False)
        n_err = len(errata_pos)
        if n_err == 0:
            self.frames_ok += 1
        else:
            self.frames_corrected += 1
        return RsResult(data=bytes(data), errors=n_err, ok=True)
