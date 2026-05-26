"""Soft-FIB averaging: recover clean FIBs from many noisy receptions.

welle.io patched to dump every received FIB (regardless of CRC) as a 34-byte
record: [pos(1)][crc_ok(1)][packed_bits(32)]. Same logical FIB (= same FIG
contents) tends to appear at the same FIB position (0..11) within each 96-ms
transmission frame, so we can per-bit majority-vote across many samples at
the same position and recover a clean FIB even when individual receptions
have ~5-10% bit-error rate.

CRC-16-CCITT polynomial 0x1021 covers the 240 useful bits of each FIB;
recovered FIBs whose CRC passes get parsed for FIG 0/0 (EId), 0/1 (sub-channel
org), 0/2 (service org), 1/0 (ensemble label), 1/1 (service label).
"""
from __future__ import annotations
import argparse
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path


def crc16_ccitt(bits: list) -> int:
    """CRC-16-CCITT computed over a list of 0/1 ints (length 240).
    Per ETSI EN 300 401: poly 0x1021, init 0xFFFF, final XOR 0xFFFF."""
    crc = 0xFFFF
    for b in bits:
        bit = (b ^ ((crc >> 15) & 1)) & 1
        crc = ((crc << 1) & 0xFFFF) ^ (0x1021 if bit else 0)
    return crc ^ 0xFFFF


def fib_check_crc(bytes32: bytes) -> bool:
    """Check FIB CRC: 240 useful bits + 16-bit CRC."""
    bits = []
    for b in bytes32:
        for i in range(8):
            bits.append((b >> (7 - i)) & 1)
    body = bits[:240]
    rx_crc = 0
    for i in range(16):
        rx_crc = (rx_crc << 1) | bits[240 + i]
    return crc16_ccitt(body) == rx_crc


def load_fibs(path: Path) -> list:
    """Yield (position, crc_ok, bytes32) tuples."""
    raw = path.read_bytes()
    n = len(raw) // 34
    out = []
    for i in range(n):
        rec = raw[i * 34 : (i + 1) * 34]
        out.append((rec[0], bool(rec[1]), rec[2:34]))
    return out


def bytes_to_bits(b: bytes) -> list:
    return [(b[i // 8] >> (7 - (i % 8))) & 1 for i in range(len(b) * 8)]


def bits_to_bytes(bits: list) -> bytes:
    out = bytearray(len(bits) // 8)
    for i, bit in enumerate(bits):
        out[i // 8] |= bit << (7 - (i % 8))
    return bytes(out)


def per_bit_majority(samples: list) -> bytes:
    """Per-bit majority vote across samples (each 32 bytes)."""
    if not samples:
        return b""
    n = len(samples)
    sums = [0] * 256
    for s in samples:
        bits = bytes_to_bits(s)
        for i, b in enumerate(bits):
            sums[i] += b
    voted = [1 if sums[i] * 2 > n else 0 for i in range(256)]
    return bits_to_bytes(voted)


def parse_figs(fib_bytes: bytes) -> list:
    """Parse FIG headers in a 30-byte FIB payload (skipping CRC)."""
    out = []
    payload = fib_bytes[:30]
    i = 0
    while i < 30:
        hdr = payload[i]
        if hdr == 0xFF:
            break  # end marker
        fig_type = (hdr >> 5) & 0x07
        length = hdr & 0x1F
        if length == 0 or i + 1 + length > 30:
            break
        data = payload[i + 1 : i + 1 + length]
        out.append((fig_type, length, data))
        i += 1 + length
    return out


def decode_fig_0_0(data: bytes) -> dict:
    """FIG 0/0 — Ensemble Id, Country/ECC, CIF count."""
    if len(data) < 4:
        return {}
    extension = data[0] & 0x1F
    if extension != 0:
        return {}
    eid = (data[1] << 8) | data[2]
    change_flags = (data[3] >> 6) & 0x3
    cif_count_hi = data[3] & 0x1F
    return {"eid": f"0x{eid:04X}", "extension": 0, "change_flags": change_flags,
            "cif_count_hi": cif_count_hi}


def decode_fig_0(data: bytes) -> tuple:
    """Decode FIG type 0 header byte + return extension and data."""
    if len(data) < 1:
        return (None, b"")
    ext = data[0] & 0x1F
    return (ext, data[1:])


def decode_fig_1(data: bytes) -> dict:
    """FIG type 1 = labels (16 bytes ASCII/EBU Latin)."""
    if len(data) < 4:
        return {}
    charset = (data[0] >> 4) & 0x0F
    extension = data[0] & 0x07
    if extension == 0:
        # Ensemble label
        if len(data) < 20:
            return {}
        eid = (data[1] << 8) | data[2]
        label = data[3:19]
        return {"type": "ensemble", "eid": f"0x{eid:04X}",
                "label": label.decode("latin-1", "replace").rstrip()}
    elif extension == 1:
        # Programme service label
        if len(data) < 20:
            return {}
        sid = (data[1] << 8) | data[2]
        label = data[3:19]
        return {"type": "service", "sid": f"0x{sid:04X}",
                "label": label.decode("latin-1", "replace").rstrip()}
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fib_file", type=Path)
    ap.add_argument("--min-samples", type=int, default=10,
                    help="minimum samples per position to attempt vote")
    args = ap.parse_args()

    fibs = load_fibs(args.fib_file)
    print(f"Loaded {len(fibs)} FIB records from {args.fib_file}")

    # Original CRC pass rate
    n_crc_ok = sum(1 for _, ok, _ in fibs if ok)
    print(f"Raw CRC pass: {n_crc_ok}/{len(fibs)}  ({n_crc_ok*100/max(1,len(fibs)):.1f}%)")

    # Group by position
    by_pos = defaultdict(list)
    for pos, ok, data in fibs:
        by_pos[pos].append(data)

    print(f"\nFIB position distribution:")
    for pos in sorted(by_pos):
        print(f"  position {pos:>2}: {len(by_pos[pos])} samples")

    # Per-position majority vote
    print(f"\n=== Per-position majority vote ===")
    recovered_fibs = {}
    for pos in sorted(by_pos):
        samples = by_pos[pos]
        if len(samples) < args.min_samples:
            continue
        voted = per_bit_majority(samples)
        ok = fib_check_crc(voted)
        status = "✓ CRC OK" if ok else "✗ CRC fail"
        print(f"  pos {pos:>2}  n={len(samples):>3}  {status}  first bytes: {voted[:8].hex()}")
        if ok:
            recovered_fibs[pos] = voted

    if recovered_fibs:
        print(f"\n=== Recovered {len(recovered_fibs)} FIBs — parsing FIGs ===")
        all_eids = set()
        all_labels = []
        for pos, fib in sorted(recovered_fibs.items()):
            figs = parse_figs(fib)
            for fig_type, length, data in figs:
                if fig_type == 0:
                    ext, sub = decode_fig_0(data)
                    if ext == 0:
                        info = decode_fig_0_0(data)
                        if info.get("eid"):
                            all_eids.add(info["eid"])
                            print(f"  FIB#{pos} FIG 0/0: EId={info['eid']}")
                elif fig_type == 1:
                    info = decode_fig_1(data)
                    if info.get("label"):
                        print(f"  FIB#{pos} FIG 1/{info.get('type','?')}: '{info['label']}'")
                        all_labels.append(info)

        print(f"\n=== Summary ===")
        print(f"  Ensemble IDs found: {sorted(all_eids)}")
        for lbl in all_labels:
            print(f"  Label: {lbl}")

    # If per-position vote fails, try content clustering on best-CRC samples
    if not recovered_fibs:
        print(f"\n=== Per-position vote failed; trying content clustering ===")
        # Use Hamming-distance clustering on first 4 bytes (FIG header tends to be stable)
        # ... TODO: implement clustering if needed
        print("  (not implemented — try collecting more FIBs)")


if __name__ == "__main__":
    main()
