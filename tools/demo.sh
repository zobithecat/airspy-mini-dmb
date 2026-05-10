#!/usr/bin/env bash
# End-to-end pipeline demo on a saved ETI capture.
# Walks: ETI → FIC parse → MSC extract → TS → synthetic PMT (incl. IOD)
# → ffmpeg probe → H.264 NAL forensics on the SL payload.

set -e
cd "$(dirname "$0")/.."

ETI="${1:-data/captures/k8b_72pct.eti}"
FFMPEG="local/dmb-ffmpeg/bin/ffmpeg"
LIBPATH="local/dmb-ffmpeg/lib:/opt/homebrew/lib"

echo "=== input: $ETI ==="
ls -la "$ETI"
echo

echo "=== Stage 1: ETI parse + FIC decode → ensemble + services ==="
python3 tools/dump_fic.py "$ETI" | head -25
echo

echo "=== Stage 2a: MSC sub-channel 1 → 188-byte TS slot extract ==="
TS=/tmp/demo.ts
python3 tools/extract_ts_direct.py "$ETI" --subch 1 --out "$TS" | tail -3
echo

echo "=== Stage 2b: synthetic PAT+PMT injection (with IOD descriptor) ==="
TS_PMT=/tmp/demo_pmt.ts
python3 tools/inject_pmt_full.py "$TS" "$TS_PMT" 2>&1 | tail -3
echo "    output: $(stat -f%z "$TS_PMT") bytes"
echo

echo "=== Stage 3a: dmb-ffmpeg recognises stream layout ==="
DYLD_LIBRARY_PATH="$LIBPATH" "$FFMPEG" -hide_banner -err_detect ignore_err \
  -analyzeduration 30000000 -probesize 30000000 \
  -i "$TS_PMT" -t 1 -f null - 2>&1 | grep -E "Stream #|Input #" | head -10
echo

echo "=== Stage 3b: H.264 NAL forensics on SL payload ==="
python3 tools/find_nal.py "$TS"
echo

echo "=== Stage 3c: TSDuck PSI analysis (independent verification) ==="
which tsanalyze >/dev/null 2>&1 && {
    tsanalyze "$TS" 2>&1 | grep -E "Bytes:|TS packets:|invalid sync|transport error" | head -5
}
echo

echo "=== Reading: ==="
cat <<EOM
    Stage 1 = ✓ Ensemble + services decoded (FIB pass rate visible)
    Stage 2 = ✓ TS packets extracted (sync rate ≥ 60% means signal lockable)
    Stage 3a = ✓ dmb-ffmpeg sees the right PIDs (stream_type 0x12 = MPEG-4 SL)
    Stage 3b = ✓ Real H.264 NAL units present in PES payload
    Stage 3c = independent confirmation by TSDuck

    With clean signal (SNR ≥ 14 dB, FIB > 99%):
      - many SPS/PPS/IDR NALs → ffmpeg opens video decoder
      - PMT CRC32 passes → no need for synthetic PMT injection
      - playback via:  python3 tools/play.py --channel K8B --subch 1
EOM
