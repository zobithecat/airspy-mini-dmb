"""Find any PMT instance with valid CRC32-MPEG-2."""
import sys

POLY = 0x04C11DB7
def crc32_mpeg2(data):
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b << 24
        for _ in range(8):
            if crc & 0x80000000:
                crc = ((crc << 1) ^ POLY) & 0xFFFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFFFF
    return crc

# Verify on test vector
test_crc = crc32_mpeg2(b"123456789")
expected = 0x0376E6E7
print(f"CRC32-MPEG2 test: got 0x{test_crc:08X}, expected 0x{expected:08X}: {'OK' if test_crc == expected else 'FAIL'}")
print()

# Now find PMT in capture
with open(sys.argv[1], 'rb') as f:
    data = f.read()

pid_target = 0x100
# Build sections by reassembling continuity
sections = []
cur = bytearray()
in_section = False

for i in range(len(data) // 188):
    pkt = data[i*188:(i+1)*188]
    if pkt[0] != 0x47: continue
    pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
    if pid != pid_target: continue
    pusi = (pkt[1] >> 6) & 1
    afc = (pkt[3] >> 4) & 0x3
    offset = 4
    if afc & 0x2:
        offset = 5 + pkt[4]
    if afc & 0x1 and offset < 188:
        payload = pkt[offset:188]
        if pusi:
            if cur:
                sections.append(bytes(cur))
            if len(payload) > 0:
                ptr = payload[0]
                if 1 + ptr <= len(payload):
                    cur = bytearray(payload[1+ptr:])
                else:
                    cur = bytearray()
            else:
                cur = bytearray()
        else:
            if cur:
                cur.extend(payload)
if cur:
    sections.append(bytes(cur))

print(f"Reassembled {len(sections)} candidate sections from PID 0x100")
n_valid = 0
for sec in sections:
    if len(sec) < 12: continue
    table_id = sec[0]
    section_length = ((sec[1] & 0x0F) << 8) | sec[2]
    full_len = 3 + section_length
    if full_len > len(sec) or full_len < 4: continue
    section = sec[:full_len]
    expected_crc = (section[-4]<<24) | (section[-3]<<16) | (section[-2]<<8) | section[-1]
    if crc32_mpeg2(section[:-4]) == expected_crc:
        n_valid += 1
        if n_valid <= 3:
            print(f"\n✓ Valid section: table_id=0x{table_id:02x}, length={section_length}")
            print(f"  raw: {section[:32].hex(' ')}")
            if table_id == 0x02:
                # PMT
                prog = (section[3]<<8)|section[4]
                pcr_pid = ((section[8]&0x1F)<<8)|section[9]
                pil = ((section[10]&0x0F)<<8)|section[11]
                print(f"  PMT: prog={prog} PCR=0x{pcr_pid:04x} info_len={pil}")
                es_off = 12 + pil
                end = full_len - 4
                while es_off + 5 <= end:
                    st = section[es_off]
                    epid = ((section[es_off+1]&0x1F)<<8)|section[es_off+2]
                    eil = ((section[es_off+3]&0x0F)<<8)|section[es_off+4]
                    name = {0x10:"MPEG-4 Vis",0x11:"BSAC/AAC",0x12:"MPEG-4 PES",0x13:"OD/SD",0x1B:"H.264"}.get(st, "?")
                    print(f"    PID 0x{epid:04x}: type=0x{st:02x} ({name}) info_len={eil}")
                    es_off += 5 + eil

print(f"\nTotal valid CRC: {n_valid}/{len(sections)}")
