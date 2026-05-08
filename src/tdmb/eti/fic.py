"""FIC / FIB / FIG dispatcher.

Mode I: 4 FIBs of 32 bytes per ETI frame. Each valid FIB carries a sequence of
FIGs terminated by 0xFF. FIG header byte: TYPE[3] | LEN[5] (LEN excludes header).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterator

from .crc import fib_ok


@dataclass
class Fig:
    type: int
    data: bytes  # FIG payload, length per header

    @property
    def ext(self) -> int:
        # FIG type 0 / 1 / 2 first payload byte holds (CN, OE, P/D, Extension)
        # We expose just the lower 5 bits as extension code.
        return self.data[0] & 0x1F if self.data else -1


def iter_figs(fib: bytes) -> Iterator[Fig]:
    """Yield FIGs from a single 32-byte FIB. Skips silently if CRC fails."""
    if not fib_ok(fib):
        return
    payload = fib[:30]
    i = 0
    while i < len(payload):
        hdr = payload[i]
        if hdr == 0xFF:  # end marker
            return
        ftype = (hdr >> 5) & 0x07
        flen = hdr & 0x1F
        i += 1
        if i + flen > len(payload):
            return  # malformed
        yield Fig(type=ftype, data=bytes(payload[i : i + flen]))
        i += flen


def iter_figs_from_fic(fic: bytes) -> Iterator[Fig]:
    """Iterate FIGs across all FIBs in a 96-byte FIC block (Mode I)."""
    for k in range(0, len(fic), 32):
        yield from iter_figs(fic[k : k + 32])


# ---------------------------------------------------------------------------
# Ensemble model that accumulates state across many ETI frames.
# ---------------------------------------------------------------------------

@dataclass
class SubChannel:
    sub_ch_id: int
    start_addr: int   # CU start address in CIF
    size_cu: int      # capacity units
    protection: str   # human readable: "EEP-2A", "UEP-3", etc.
    bitrate_kbps: int
    is_long_form: bool


@dataclass
class ServiceComponent:
    sub_ch_id: int | None       # for stream mode
    sc_id_s: int                # service component id within service
    transport: str              # "stream-audio", "stream-data", "fidc", "packet"
    ascty_or_dscty: int         # audio service type or data type
    is_primary: bool
    label: str = ""


@dataclass
class Service:
    sid: int                    # service identifier
    is_data: bool               # False=programme, True=data
    label: str = ""
    short_label: str = ""
    components: list[ServiceComponent] = None  # type: ignore

    def __post_init__(self) -> None:
        if self.components is None:
            self.components = []


@dataclass
class Ensemble:
    eid: int | None = None
    label: str = ""
    short_label: str = ""
    sub_channels: dict[int, SubChannel] = None  # type: ignore
    services: dict[int, Service] = None  # type: ignore

    def __post_init__(self) -> None:
        if self.sub_channels is None:
            self.sub_channels = {}
        if self.services is None:
            self.services = {}


# Helpers --------------------------------------------------------------------

def _decode_label(payload: bytes) -> str:
    """16 bytes of label, padded ASCII/EBU. Returns trimmed UTF-8 best-effort."""
    raw = bytes(payload[:16])
    # Korean T-DMB often uses default ASCII labels; non-ASCII via UTF-8/EBU
    # complete tables (EBU Latin) are large; fall back to latin-1 then strip.
    try:
        s = raw.decode("utf-8")
    except UnicodeDecodeError:
        s = raw.decode("latin-1", errors="replace")
    return s.rstrip(" \x00")


# Bitrate / size from FIG 0/1 protection encoding -- see ETSI EN 300 401 §6.2.1
# UEP table (short form): index -> (bitrate, sub-channel size in CU, protection level label)
_UEP_TABLE = {
    0: (32, 16, "UEP-5"), 1: (32, 21, "UEP-4"), 2: (32, 24, "UEP-3"), 3: (32, 29, "UEP-2"),
    4: (48, 24, "UEP-5"), 5: (48, 29, "UEP-4"), 6: (48, 35, "UEP-3"), 7: (48, 42, "UEP-2"),
    8: (56, 29, "UEP-5"), 9: (56, 35, "UEP-4"), 10: (56, 42, "UEP-3"), 11: (56, 52, "UEP-2"),
    12: (64, 32, "UEP-5"), 13: (64, 42, "UEP-4"), 14: (64, 48, "UEP-3"), 15: (64, 58, "UEP-2"),
    16: (80, 40, "UEP-5"), 17: (80, 52, "UEP-4"), 18: (80, 58, "UEP-3"), 19: (80, 70, "UEP-2"),
    20: (96, 48, "UEP-5"), 21: (96, 58, "UEP-4"), 22: (96, 70, "UEP-3"), 23: (96, 84, "UEP-2"),
    24: (112, 58, "UEP-5"), 25: (112, 70, "UEP-4"), 26: (112, 84, "UEP-3"), 27: (112, 104, "UEP-2"),
    28: (128, 64, "UEP-5"), 29: (128, 84, "UEP-4"), 30: (128, 96, "UEP-3"), 31: (128, 116, "UEP-2"),
    32: (160, 80, "UEP-5"), 33: (160, 104, "UEP-4"), 34: (160, 116, "UEP-3"), 35: (160, 140, "UEP-2"),
    36: (192, 96, "UEP-5"), 37: (192, 116, "UEP-4"), 38: (192, 140, "UEP-3"), 39: (192, 168, "UEP-2"),
    40: (224, 116, "UEP-5"), 41: (224, 140, "UEP-4"), 42: (224, 168, "UEP-3"), 43: (224, 208, "UEP-2"),
    44: (256, 128, "UEP-5"), 45: (256, 168, "UEP-4"), 46: (256, 192, "UEP-3"), 47: (256, 232, "UEP-2"),
    48: (320, 168, "UEP-5"), 49: (320, 232, "UEP-2"),
    50: (384, 208, "UEP-5"), 51: (384, 280, "UEP-2"), 52: (384, 280, "UEP-1"),
    53: (32, 24, "UEP-1"),
    54: (96, 48, "UEP-1"),
    55: (128, 96, "UEP-1"),
    56: (192, 116, "UEP-1"),
    57: (256, 192, "UEP-1"),
    58: (384, 280, "UEP-1"),
    59: (32, 16, "UEP-5"),
    60: (32, 16, "UEP-5"),
    61: (32, 16, "UEP-5"),
    62: (32, 16, "UEP-5"),
    63: (32, 16, "UEP-5"),
}

# EEP option 1: A-level CU sizes per kbps step. EEP-2A common for T-DMB.
_EEP_A = {1: 12, 2: 8, 3: 6, 4: 4}   # CUs per 8 kbps for opt=0
_EEP_B = {1: 27, 2: 21, 3: 18, 4: 15}  # CUs per 32 kbps for opt=1


def _eep_size(opt: int, level: int, sub_size_cu: int, n: int) -> tuple[int, int, str]:
    """Return (bitrate_kbps, size_cu, label) for EEP given size."""
    if opt == 0:  # EEP-A: bitrate = 8*n, CU = m * n where m = _EEP_A[level]
        m = _EEP_A.get(level + 1, 0)
        if m == 0:
            return 0, sub_size_cu, f"EEP-{level + 1}A"
        n = sub_size_cu // m
        return 8 * n, sub_size_cu, f"EEP-{level + 1}A"
    else:  # EEP-B: bitrate = 32*n
        m = _EEP_B.get(level + 1, 0)
        if m == 0:
            return 0, sub_size_cu, f"EEP-{level + 1}B"
        n = sub_size_cu // m
        return 32 * n, sub_size_cu, f"EEP-{level + 1}B"


# FIG handlers ---------------------------------------------------------------

def _handle_fig0_0(ens: Ensemble, payload: bytes) -> None:
    # bytes: [hdr][EId hi][EId lo][ChangeFlags ...]
    if len(payload) < 4:
        return
    eid = (payload[1] << 8) | payload[2]
    ens.eid = eid


def _handle_fig0_1(ens: Ensemble, payload: bytes) -> None:
    """Sub-channel basic organization. Iterates entries until end of payload."""
    i = 1  # skip header byte
    while i + 2 <= len(payload):
        b0 = payload[i]
        b1 = payload[i + 1]
        sub_ch_id = (b0 >> 2) & 0x3F
        start_addr = ((b0 & 0x03) << 8) | b1
        i += 2
        if i >= len(payload):
            break
        # next bit: short(0) or long(1) form
        b2 = payload[i]
        long_form = (b2 >> 7) & 1
        if long_form:
            if i + 2 > len(payload):
                break
            option = (b2 >> 4) & 0x07
            level = (b2 >> 2) & 0x03
            sub_size = ((b2 & 0x03) << 8) | payload[i + 1]
            i += 2
            br, cu, lbl = _eep_size(option, level, sub_size, sub_size)
            ens.sub_channels[sub_ch_id] = SubChannel(
                sub_ch_id=sub_ch_id, start_addr=start_addr,
                size_cu=cu or sub_size, protection=lbl,
                bitrate_kbps=br, is_long_form=True,
            )
        else:
            table_sw = (b2 >> 6) & 1  # always 0
            table_idx = b2 & 0x3F
            i += 1
            br, cu, lbl = _UEP_TABLE.get(table_idx, (0, 0, f"UEP?{table_idx}"))
            ens.sub_channels[sub_ch_id] = SubChannel(
                sub_ch_id=sub_ch_id, start_addr=start_addr,
                size_cu=cu, protection=lbl,
                bitrate_kbps=br, is_long_form=False,
            )


def _handle_fig0_2(ens: Ensemble, payload: bytes, p_d: int) -> None:
    """Service organization. P/D bit determines SId width: 0=16b, 1=32b."""
    i = 1
    while i < len(payload):
        if p_d == 0:
            if i + 2 > len(payload):
                return
            sid = (payload[i] << 8) | payload[i + 1]
            i += 2
        else:
            if i + 4 > len(payload):
                return
            sid = ((payload[i] << 24) | (payload[i + 1] << 16)
                   | (payload[i + 2] << 8) | payload[i + 3])
            i += 4
        if i >= len(payload):
            return
        b = payload[i]
        i += 1
        # b: Local(1) | CAId(3) | NumComp(4)
        ncomp = b & 0x0F
        is_data = (p_d == 1)
        svc = ens.services.setdefault(sid, Service(sid=sid, is_data=is_data))

        def _add(comp: ServiceComponent) -> None:
            # Idempotent on (sub_ch_id, sc_id_s, transport): FIG 0/2 is
            # transmitted in every FIB, we only want one copy.
            for c in svc.components:
                if (c.sub_ch_id == comp.sub_ch_id
                        and c.sc_id_s == comp.sc_id_s
                        and c.transport == comp.transport):
                    return
            svc.components.append(comp)

        for _ in range(ncomp):
            if i + 2 > len(payload):
                return
            w = (payload[i] << 8) | payload[i + 1]
            i += 2
            tmid = (w >> 14) & 0x03
            if tmid == 0:        # MSC stream audio
                ascty = (w >> 8) & 0x3F
                sub_ch_id = (w >> 2) & 0x3F
                primary = (w >> 1) & 1
                _add(ServiceComponent(
                    sub_ch_id=sub_ch_id, sc_id_s=0,
                    transport="stream-audio", ascty_or_dscty=ascty,
                    is_primary=bool(primary),
                ))
            elif tmid == 1:      # MSC stream data
                dscty = (w >> 8) & 0x3F
                sub_ch_id = (w >> 2) & 0x3F
                primary = (w >> 1) & 1
                _add(ServiceComponent(
                    sub_ch_id=sub_ch_id, sc_id_s=0,
                    transport="stream-data", ascty_or_dscty=dscty,
                    is_primary=bool(primary),
                ))
            elif tmid == 2:      # FIDC
                dscty = (w >> 8) & 0x3F
                sub_ch_id = (w >> 2) & 0x3F
                primary = (w >> 1) & 1
                _add(ServiceComponent(
                    sub_ch_id=sub_ch_id, sc_id_s=0,
                    transport="fidc", ascty_or_dscty=dscty,
                    is_primary=bool(primary),
                ))
            else:                # MSC packet mode
                scid = (w >> 2) & 0xFFF
                primary = (w >> 1) & 1
                _add(ServiceComponent(
                    sub_ch_id=None, sc_id_s=scid,
                    transport="packet", ascty_or_dscty=0,
                    is_primary=bool(primary),
                ))


def _handle_fig1(ens: Ensemble, payload: bytes) -> None:
    """FIG type 1 — labels. Common layout: [hdr][SId/EId ...][16B label][2B mask]."""
    if len(payload) < 1:
        return
    hdr = payload[0]
    charset = (hdr >> 4) & 0x0F
    ext = hdr & 0x07
    body = payload[1:]
    if ext == 0:  # Ensemble label
        if len(body) < 2 + 16 + 2:
            return
        ens.label = _decode_label(body[2 : 2 + 16])
    elif ext == 1:  # Programme service label (16-bit SId)
        if len(body) < 2 + 16 + 2:
            return
        sid = (body[0] << 8) | body[1]
        label = _decode_label(body[2 : 2 + 16])
        svc = ens.services.setdefault(sid, Service(sid=sid, is_data=False))
        svc.label = label
    elif ext == 5:  # Data service label (32-bit SId)
        if len(body) < 4 + 16 + 2:
            return
        sid = ((body[0] << 24) | (body[1] << 16) | (body[2] << 8) | body[3])
        label = _decode_label(body[4 : 4 + 16])
        svc = ens.services.setdefault(sid, Service(sid=sid, is_data=True))
        svc.label = label


# Public driver --------------------------------------------------------------

class FicAccumulator:
    """Feed FIC blocks (96 B per ETI frame); query Ensemble state."""

    def __init__(self) -> None:
        self.ensemble = Ensemble()
        self.fib_total = 0
        self.fib_ok = 0

    def feed_fic(self, fic: bytes) -> None:
        for k in range(0, len(fic), 32):
            self.fib_total += 1
            fib = fic[k : k + 32]
            if len(fib) < 32:
                continue
            self.feed_fib(fib)

    def feed_fib(self, fib: bytes) -> None:
        from .crc import fib_ok as _ok
        if not _ok(fib):
            return
        self.fib_ok += 1
        for fig in iter_figs(fib):
            try:
                self._dispatch(fig)
            except Exception:
                pass

    def _dispatch(self, fig: Fig) -> None:
        if fig.type == 0:
            # FIG type 0 first byte: CN(1) OE(1) P/D(1) Ext(5)
            if not fig.data:
                return
            p_d = (fig.data[0] >> 5) & 1
            ext = fig.data[0] & 0x1F
            if ext == 0:
                _handle_fig0_0(self.ensemble, fig.data)
            elif ext == 1:
                _handle_fig0_1(self.ensemble, fig.data)
            elif ext == 2:
                _handle_fig0_2(self.ensemble, fig.data, p_d)
        elif fig.type == 1:
            _handle_fig1(self.ensemble, fig.data)
