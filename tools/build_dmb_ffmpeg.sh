#!/usr/bin/env bash
# Build the dmb-oss FFmpeg fork (BSAC + SL OD stream + DMB mpegts patches).
# Installs into ./local/dmb-ffmpeg/{bin,lib,include}.
#
# Usage: ./tools/build_dmb_ffmpeg.sh
#
# References the work of Sungbo Eo & Saewoong Bahk (ICTC 2024). The patches
# applied are documented at:
#   https://github.com/dmb-oss/FFmpeg/commits/master
# Notable commits:
#   43291d7e — Rebase BSAC patch on top of FFmpeg 7.0
#   713535c8 — Trim BSAC struct
#   3a0d40a7 — Fix BSAC playback when numOfSubFrame > 1
#   e4ba4ac1 — Improve parsing of SL-packetized OD stream
#   7a8fc990 — Suppress invalid timebase warnings on DMB streams
#   3d10a890 — Read extended channel configuration when extended AOT is BSAC

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="${REPO_ROOT}/local/dmb-ffmpeg"
SRC="${REPO_ROOT}/build/dmb-ffmpeg-src"

mkdir -p "${REPO_ROOT}/build" "${PREFIX}"

if [[ ! -d "${SRC}/.git" ]]; then
    echo "[1/3] cloning dmb-oss/FFmpeg ..."
    git clone --filter=blob:none https://github.com/dmb-oss/FFmpeg.git "${SRC}"
fi

cd "${SRC}"

# CRITICAL: dmb-oss/FFmpeg's master is a vanilla upstream mirror.
# The DMB patches live on the `dev-dmb` branch. Make sure we are on it.
git fetch origin dev-dmb 2>/dev/null || true
git checkout dev-dmb 2>/dev/null || git checkout -b dev-dmb origin/dev-dmb
echo "checked out: $(git rev-parse --short HEAD) — $(git log -1 --pretty=%s)"

echo "[2/3] configuring (this can take a moment)..."
# macOS Homebrew library paths
EXTRA_CFLAGS=""
EXTRA_LDFLAGS=""
if [[ "$(uname)" == "Darwin" ]]; then
    EXTRA_CFLAGS="-I/opt/homebrew/include"
    EXTRA_LDFLAGS="-L/opt/homebrew/lib"
fi

./configure \
    --prefix="${PREFIX}" \
    --enable-gpl --enable-version3 --enable-nonfree \
    --enable-shared --disable-static \
    --disable-doc --disable-htmlpages --disable-manpages \
    --enable-ffplay \
    --enable-sdl2 \
    --extra-cflags="${EXTRA_CFLAGS}" \
    --extra-ldflags="${EXTRA_LDFLAGS}"

echo "[3/3] building (parallel)..."
NPROC=$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)
make -j"${NPROC}"
make install

echo
echo "✅ dmb-ffmpeg installed to ${PREFIX}"
echo
"${PREFIX}/bin/ffmpeg" -version 2>&1 | head -3
echo
echo "BSAC support check:"
"${PREFIX}/bin/ffmpeg" -hide_banner -decoders 2>&1 | grep -i bsac || echo "(no bsac line — may be enabled but unlisted)"
