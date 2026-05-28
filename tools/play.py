"""Live T-DMB playback: tune via patched eti-cmdline → extract sub-channel
TS → pipe to dmb-oss patched FFmpeg (BSAC + SL OD support) → play.

This is the Stage-3 bridge that wires our Stages 1+2 to a working video
player. It assumes:
  - tools/build_dmb_ffmpeg.sh has been run (local/dmb-ffmpeg/bin/ffmpeg)
  - eti-stuff has been built with our patches
  - You are tuning to a Korean T-DMB ensemble that *isn't* HD (HD-DMB
    is conditional-access encrypted and cannot be played).

Usage:
  python tools/play.py --channel K8B --subch 1
  python tools/play.py --eti-file capture.eti --subch 1     # offline
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from tdmb.eti import parse_frame, FRAME_SIZE
from tdmb.eti.fic import FicAccumulator
from tdmb.msc import extract_subchannel
from tdmb.channels import by_name
from tdmb.fec.outer import KoreanTDmbOuterFec

ETI_BIN = REPO / "eti-stuff" / "eti-cmdline" / "build" / "eti-cmdline-airspy"
DMB_FFMPEG = REPO / "local" / "dmb-ffmpeg" / "bin" / "ffmpeg"
DMB_FFPLAY = REPO / "local" / "dmb-ffmpeg" / "bin" / "ffplay"
LIBPATH = "/opt/homebrew/lib"

SLOT = 204
TS_PKT = 188


def iter_frames_from_pipe(stream):
    while True:
        chunk = stream.read(FRAME_SIZE)
        if len(chunk) != FRAME_SIZE:
            return
        try:
            yield parse_frame(chunk)
        except Exception:
            continue


def iter_frames_from_file(path: Path):
    with path.open("rb") as f:
        while True:
            chunk = f.read(FRAME_SIZE)
            if len(chunk) != FRAME_SIZE:
                return
            try:
                yield parse_frame(chunk)
            except Exception:
                continue


def find_slot_offset(probe: bytes, slot: int = SLOT) -> int:
    """The 0x47-rich byte position within a 204-byte cycle."""
    best = (0, 0)
    for off in range(slot):
        hits = sum(1 for i in range(off, len(probe), slot) if probe[i] == 0x47)
        if hits > best[0]:
            best = (hits, off)
    return best[1]


def stream_ts_packets(frames, sub_ch_id: int, probe_bytes: int = 100_000):
    """Extract RS-corrected 188-byte TS packets from a CIF byte stream.

    Uses our sync-aligned Forney deinterleaver + RS(204,188) decoder
    (tdmb.fec.outer.KoreanTDmbOuterFec) — the same fix that takes K8B
    from 0% to 87.3% in offline mode.
    """
    import time
    fec = KoreanTDmbOuterFec()
    cif_iter = extract_subchannel(frames, sub_ch_id)
    n_packets = 0
    n_bytes_in = 0
    n_chunks = 0
    t0 = time.time()
    last_status = t0
    for chunk in cif_iter:
        if not chunk:
            continue
        n_chunks += 1
        n_bytes_in += len(chunk)
        # Periodic diagnostic so user knows progress
        now = time.time()
        if now - last_status > 2.0:
            elapsed = now - t0
            print(f"[play] {elapsed:.1f}s: rx={n_bytes_in} bytes "
                  f"({n_chunks} chunks), aligned={fec._aligned}, "
                  f"phase={fec._phase_offset}, packets_out={n_packets}",
                  file=sys.stderr, flush=True)
            last_status = now
        for ts in fec.feed(chunk):
            if ts.rs_errors < 0:
                pass
            yield ts.data
            n_packets += 1
            if n_packets == 1:
                print(f"[play] outer FEC locked: phase={fec._phase_offset}, "
                      f"streaming TS packets", file=sys.stderr, flush=True)


def open_eti_source(args) -> tuple:
    """Return (frames_iter, cleanup_callback)."""
    if args.eti_file:
        return iter_frames_from_file(args.eti_file), (lambda: None)

    ch = by_name(args.channel.lstrip("K"))
    print(f"[play] tuning {args.channel} ({ch.freq_hz/1e6:.3f} MHz)", file=sys.stderr)
    env = os.environ.copy()
    env["DYLD_LIBRARY_PATH"] = LIBPATH + ":" + env.get("DYLD_LIBRARY_PATH", "")
    proc = subprocess.Popen(
        [str(ETI_BIN),
         "-C", "K" + args.channel.lstrip("K"),
         "-G", str(args.gain),
         "-d", str(args.detect),
         "-D", str(args.detect),
         "-O", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        bufsize=0,
    )
    # Pump stderr to our stderr in a thread
    import threading
    def _pump():
        for line in proc.stderr:
            sys.stderr.buffer.write(b"[eti] " + line)
            sys.stderr.flush()
    threading.Thread(target=_pump, daemon=True).start()
    return iter_frames_from_pipe(proc.stdout), (lambda: proc.terminate())


def open_player(args) -> subprocess.Popen:
    """Spawn dmb-oss ffplay (or ffmpeg piping to mpv) reading TS from stdin."""
    if not DMB_FFMPEG.exists():
        sys.exit(f"dmb-ffmpeg not built — run tools/build_dmb_ffmpeg.sh first ({DMB_FFMPEG})")
    if args.dump_only:
        return subprocess.Popen(["cat"], stdin=subprocess.PIPE,
                                 stdout=open(args.dump_only, "wb"))
    if DMB_FFPLAY.exists():
        # Tolerance settings: live T-DMB stream is bursty (PAT/PMT every ~500ms,
        # TS only flows after outer-FEC alignment, ~20% RS-uncorrectable at
        # marginal SNR).  Push analyzeduration + probesize to 50 s / 50 MB so
        # ffplay waits for enough valid TS packets before declaring failure.
        # Force MPEG-TS demuxer with `-f mpegts` so it doesn't try alternative
        # format probing on the early junk bytes.
        argv = [str(DMB_FFPLAY),
                "-f", "mpegts",
                "-fflags", "+genpts+igndts+discardcorrupt+nobuffer",
                "-err_detect", "ignore_err",
                "-analyzeduration", "50000000",
                "-probesize", "50000000",
                "-i", "pipe:0"]
    else:
        # Fallback: ffmpeg → wav stdout → system audio? Use ffmpeg + mpv
        # But simpler: ffmpeg with autoexit playing decoded video to a window
        argv = [str(DMB_FFMPEG),
                "-fflags", "+genpts+igndts+discardcorrupt",
                "-err_detect", "ignore_err",
                "-i", "pipe:0",
                "-f", "matroska", "-c", "copy", "pipe:1"]
        # then pipe to mpv... too involved; recommend installing ffplay
    return subprocess.Popen(argv, stdin=subprocess.PIPE)


def main() -> int:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--channel", help="Korean channel (e.g. K8B)")
    src.add_argument("--eti-file", type=Path, help="play from captured ETI file")
    ap.add_argument("--subch", type=int, required=True, help="sub-channel id (e.g. 1)")
    ap.add_argument("--gain", type=int, default=0, help="airspy gain (0=AGC)")
    ap.add_argument("--detect", type=int, default=30, help="OFDM sync timeout (s)")
    ap.add_argument("--dump-only", type=Path, help="instead of playing, write TS to file")
    args = ap.parse_args()

    frames_iter, cleanup = open_eti_source(args)

    # Don't launch ffplay until we have a TS warmup buffer.  Otherwise the
    # player sees ~15-30 s of empty/garbage stdin during the eti-cmdline
    # lock+FEC-align phase and declares the input invalid before real TS
    # ever arrives.  Buffer ~1000 packets (~2-3 s worth) THEN spawn ffplay
    # and stream the buffer in.
    WARMUP_PACKETS = 1000
    warmup = bytearray()
    player = None
    try:
        n_pkts = 0
        for ts in stream_ts_packets(frames_iter, args.subch):
            if player is None:
                warmup.extend(ts)
                n_pkts += 1
                if n_pkts >= WARMUP_PACKETS:
                    print(f"[play] warmup buffer ready ({n_pkts} packets, "
                          f"{len(warmup)} bytes); launching ffplay",
                          file=sys.stderr)
                    player = open_player(args)
                    assert player.stdin is not None
                    player.stdin.write(bytes(warmup))
                    warmup = bytearray()
            else:
                try:
                    player.stdin.write(ts)
                except BrokenPipeError:
                    break
                n_pkts += 1
                if n_pkts % 1000 == 0:
                    print(f"[play] {n_pkts} TS packets piped", file=sys.stderr)
    finally:
        if player is not None:
            try:
                player.stdin.close()
            except Exception:
                pass
        cleanup()
        if player is not None:
            try:
                player.wait(timeout=5)
            except Exception:
                player.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
