"""PMT v2: try alternative stream_types — H.264 = 0x1B directly, AAC = 0x0F."""
import sys, struct
sys.path.insert(0, '/tmp')
exec(open('/tmp/inject_pmt.py').read().split("# Build PSI")[0])  # reuse functions

PMT_PID = 0x100
pat_section = build_pat([(1, PMT_PID)])
# Try stream_type 0x1B (H.264) for video and 0x0F (AAC ADTS) for audio
pmt_section = build_pmt(prog=1, pcr_pid=0x113, streams=[
    (0x113, 0x1B),  # H.264 (raw, not SL-packetized)
    (0x114, 0x0F),  # AAC ADTS
])
print(f"PMT v2: video=0x1B (H.264), audio=0x0F (AAC)")
print(f"  {pmt_section.hex(' ')}")

pat_pkt = build_ts_packet(0x000, pat_section, pusi=True, cc=0)
pmt_pkt = build_ts_packet(PMT_PID, pmt_section, pusi=True, cc=0)

inp = open(sys.argv[1], 'rb').read()
with open(sys.argv[2], 'wb') as out:
    out.write(pat_pkt); out.write(pmt_pkt)
    n_in = len(inp) // 188
    for i in range(n_in):
        pkt = inp[i*188:(i+1)*188]
        if pkt[0] != 0x47: continue
        pid = ((pkt[1] & 0x1F) << 8) | pkt[2]
        if pid in (0x000, 0x100): continue
        out.write(pkt)
        if (i % 250) == 0:
            out.write(pat_pkt); out.write(pmt_pkt)
print(f"Wrote {sys.argv[2]}")
