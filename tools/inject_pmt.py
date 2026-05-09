"""Inject synthetic PMT into our extracted TS so dmb-ffmpeg can decode.

Korean T-DMB structure (known from earlier captures):
  PMT PID = 0x100, program = 1
  PCR PID = 0x113 (video)
  ES streams:
    PID 0x111: OD       (stream_type = 0x13 = ISO/IEC 14496-1 sections)
    PID 0x112: SD       (stream_type = 0x13)
    PID 0x113: Video    (stream_type = 0x12 = MPEG-4 PES SL-packetized) → H.264
    PID 0x114: Audio    (stream_type = 0x11 = MPEG-4 LATM AAC)          → BSAC
"""
import sys, struct

POLY = 0x04C11DB7
def crc32_mpeg2(data):
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b << 24
        for _ in range(8):
            crc = ((crc << 1) ^ POLY) & 0xFFFFFFFF if (crc & 0x80000000) else (crc << 1) & 0xFFFFFFFF
    return crc

def build_pat(prog_pmt_pid_pairs, ts_id=1, version=0):
    body = bytearray()
    body.append(0x00)                # table_id = PAT
    # section_syntax(1) | '0' | reserved(2) | section_length(12) — fill later
    body.append(0xb0); body.append(0x00)
    body.append((ts_id >> 8) & 0xff); body.append(ts_id & 0xff)
    body.append(0xc1 | (version << 1))   # reserved(2) | version(5) | current_next(1)
    body.append(0x00)                # section_number
    body.append(0x00)                # last_section_number
    for prog, pmt_pid in prog_pmt_pid_pairs:
        body += struct.pack('>HH', prog, 0xe000 | (pmt_pid & 0x1fff))
    crc = crc32_mpeg2(body)
    body += struct.pack('>I', crc)
    sec_len = len(body) - 3 + 4 - 4   # exclude header3, include CRC4
    section_length = len(body) - 3
    body[1] = 0xb0 | ((section_length >> 8) & 0x0F)
    body[2] = section_length & 0xff
    # recompute CRC after fixing length
    crc2 = crc32_mpeg2(body[:-4])
    body[-4:] = struct.pack('>I', crc2)
    return bytes(body)

def build_pmt(prog, pcr_pid, streams, version=0):
    """streams = [(pid, stream_type)]"""
    body = bytearray()
    body.append(0x02)                # table_id = PMT
    body.append(0xb0); body.append(0x00)  # section_length placeholder
    body += struct.pack('>H', prog)
    body.append(0xc1 | (version << 1))
    body.append(0x00)                # section_number
    body.append(0x00)                # last_section_number
    body += struct.pack('>H', 0xe000 | (pcr_pid & 0x1fff))   # PCR_PID
    body += struct.pack('>H', 0xf000)   # program_info_length = 0
    for pid, stream_type in streams:
        body.append(stream_type)
        body += struct.pack('>H', 0xe000 | (pid & 0x1fff))
        body += struct.pack('>H', 0xf000)   # ES_info_length = 0
    section_length = len(body) - 3 + 4
    body[1] = 0xb0 | ((section_length >> 8) & 0x0F)
    body[2] = section_length & 0xff
    crc = crc32_mpeg2(body)
    body += struct.pack('>I', crc)
    return bytes(body)

def build_ts_packet(pid, payload, pusi=False, cc=0, with_pcr=False, pcr=0):
    pkt = bytearray(188)
    pkt[0] = 0x47
    pkt[1] = (0x40 if pusi else 0) | ((pid >> 8) & 0x1f)
    pkt[2] = pid & 0xff
    pkt[3] = 0x10 | (cc & 0x0F)        # afc=01 (payload only), cc
    if pusi:
        pkt[4] = 0x00                  # pointer_field
        plen = 188 - 5
        pkt[5:5+min(len(payload), plen)] = payload[:plen]
        # pad with 0xff
        for k in range(5+len(payload), 188):
            pkt[k] = 0xff
    else:
        plen = 188 - 4
        pkt[4:4+min(len(payload), plen)] = payload[:plen]
        for k in range(4+len(payload), 188):
            pkt[k] = 0xff
    return bytes(pkt)

# Build PSI
PMT_PID = 0x100
pat_section = build_pat([(1, PMT_PID)])
pmt_section = build_pmt(prog=1, pcr_pid=0x113, streams=[
    (0x111, 0x13),  # OD/SD
    (0x112, 0x13),
    (0x113, 0x12),  # MPEG-4 PES (SL-packetized) → video
    (0x114, 0x11),  # MPEG-4 LATM AAC → BSAC
])

print(f"PAT section: {len(pat_section)} bytes")
print(f"  {pat_section.hex(' ')}")
print(f"PMT section: {len(pmt_section)} bytes")
print(f"  {pmt_section.hex(' ')}")

# Verify our PAT/PMT CRCs
for sec in (pat_section, pmt_section):
    sl = ((sec[1] & 0x0F) << 8) | sec[2]
    full = sec[:3+sl]
    given = struct.unpack('>I', full[-4:])[0]
    calc = crc32_mpeg2(full[:-4])
    print(f"  CRC ok: {given == calc}")

pat_pkt = build_ts_packet(0x000, pat_section, pusi=True, cc=0)
pmt_pkt = build_ts_packet(PMT_PID, pmt_section, pusi=True, cc=0)

# Now read input TS, prepend our PSI every ~250 packets, write out
inp = open(sys.argv[1], 'rb').read()
out = open(sys.argv[2], 'wb')
out.write(pat_pkt)
out.write(pmt_pkt)
n_in = len(inp) // 188
n_psi_inserts = 1
for i in range(n_in):
    pkt = inp[i*188:(i+1)*188]
    if pkt[0] != 0x47: continue
    pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
    # Skip the existing (corrupt) PMT/PAT to avoid confusing decoder
    if pid in (0x000, 0x100):
        continue
    out.write(pkt)
    if (i % 250) == 0:
        out.write(pat_pkt)
        out.write(pmt_pkt)
        n_psi_inserts += 1
out.close()
print(f"\nWrote {sys.argv[2]} with {n_psi_inserts} PAT/PMT inserts")
