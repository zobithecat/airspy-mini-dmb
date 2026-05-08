"""CRC-16-CCITT used by FIBs and FIC. Polynomial 0x1021, init 0xFFFF, output XOR 0xFFFF."""
from __future__ import annotations


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc ^ 0xFFFF


def fib_ok(fib: bytes) -> bool:
    """A FIB is 32 bytes: 30 payload + 2 byte CRC (big-endian)."""
    if len(fib) != 32:
        return False
    expected = (fib[30] << 8) | fib[31]
    return crc16_ccitt(fib[:30]) == expected
