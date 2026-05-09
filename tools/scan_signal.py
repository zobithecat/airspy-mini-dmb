"""Quickly scan all Korean channels and rank by signal strength + DAB pattern."""
import sys, os, subprocess, time
sys.path.insert(0, 'tools')
from snr_monitor import measure_psd

CHANNELS = [
    ('K7A', 175.28), ('K7B', 177.008), ('K7C', 178.736),
    ('K8A', 181.28), ('K8B', 183.008), ('K8C', 184.736),
    ('K9A', 187.28), ('K9B', 189.008), ('K9C', 190.736),
    ('K10A', 193.28), ('K10B', 195.008), ('K10C', 196.736),
    ('K11A', 199.28), ('K11B', 201.008), ('K11C', 202.736),
    ('K12A', 205.28), ('K12B', 207.008), ('K12C', 208.736),
    ('K13A', 211.28), ('K13B', 213.008), ('K13C', 214.736),
]

results = []
for name, freq in CHANNELS:
    r = measure_psd(int(freq*1e6), gain=14)
    if r:
        c, e, d, n = r
        nstr = f'{n:.0f}ms' if n==n else '   --'
        flag = '🟢' if (n==n and 90<n<105) else ('🟡' if d>5 else '  ')
        print(f"  {flag} {name:5s} {freq:7.3f} MHz: Δ={d:+5.1f} dB  null={nstr}", flush=True)
        results.append((name, freq, d, n))

print("\n=== Top 5 by Δ ===")
for name, freq, d, n in sorted(results, key=lambda x: -x[2])[:5]:
    nstr = f'null={n:.0f}ms' if n==n else 'no null'
    print(f"  {name:5s} {freq:7.3f}: Δ={d:+5.1f} dB  {nstr}")
