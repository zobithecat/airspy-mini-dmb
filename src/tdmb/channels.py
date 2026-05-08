"""Korean T-DMB channel/frequency table.

Korea uses VHF Band III TV channels 7-13 (174-216 MHz), each 6 MHz TV
channel divided into three 1.536 MHz DMB sub-channels labelled A/B/C.

Center frequency formula:
    f_center = TV_low + 1.28 + (sub - 1) * 1.728  [MHz]
where TV_low = 174 + (TV_ch - 7) * 6, sub: A=1, B=2, C=3.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class Channel:
    name: str
    freq_hz: int
    note: str = ""


def _f(tv_ch: int, sub: str) -> int:
    sub_idx = "ABC".index(sub) + 1
    tv_low = 174 + (tv_ch - 7) * 6
    return int((tv_low + 1.28 + (sub_idx - 1) * 1.728) * 1_000_000)


# Sinnonhyeon (Seoul, Gwanaksan/Namsan coverage) preset.
# Confirmed transmitters cover: 8B (YTN), 12A (MBC), 12B (U-KBS), 12C (SBS U).
SEOUL_METRO: list[Channel] = [
    Channel("8A", _f(8, "A"), "guard"),
    Channel("8B", _f(8, "B"), "YTN DMB"),
    Channel("8C", _f(8, "C"), "guard"),
    Channel("12A", _f(12, "A"), "MBC DMB"),
    Channel("12B", _f(12, "B"), "U-KBS"),
    Channel("12C", _f(12, "C"), "SBS u"),
    Channel("12D", _f(12, "D") if False else int((204 + 1.28 + 3 * 1.728) * 1e6), "spare"),
]


def all_korea_band3() -> list[Channel]:
    """Full Korean T-DMB raster for spectrum scan."""
    out: list[Channel] = []
    for tv in range(7, 14):
        for sub in "ABC":
            out.append(Channel(f"{tv}{sub}", _f(tv, sub)))
    return out


def by_name(name: str) -> Channel:
    for c in SEOUL_METRO + all_korea_band3():
        if c.name == name:
            return c
    raise KeyError(name)


if __name__ == "__main__":
    for c in SEOUL_METRO:
        print(f"{c.name:>4s}  {c.freq_hz/1e6:7.3f} MHz  {c.note}")
