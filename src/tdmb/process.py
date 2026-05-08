"""Subprocess wrapper for eti-cmdline-airspy.

Spawns the binary tuned to a given Korean T-DMB channel and exposes a
streaming iterator of raw ETI bytes from stdout.
"""
from __future__ import annotations
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .channels import Channel


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BIN = REPO_ROOT / "eti-stuff" / "eti-cmdline" / "build" / "eti-cmdline-airspy"


@dataclass
class TunerConfig:
    channel: Channel
    gain: int = 18              # 1..21
    bias_t: bool = False
    binary: Path = DEFAULT_BIN
    libpath: str = "/opt/homebrew/lib"
    detect_seconds: int = 20    # -D
    record_seconds: int = 0     # -t (0 = unlimited)


class EtiTuner:
    """Spawns eti-cmdline-airspy with stdout=ETI byte stream."""

    def __init__(self, cfg: TunerConfig):
        self.cfg = cfg
        self.proc: subprocess.Popen[bytes] | None = None

    def start(self) -> subprocess.Popen[bytes]:
        if not self.cfg.binary.exists():
            raise FileNotFoundError(f"binary missing: {self.cfg.binary}")
        # Korean channel keys are K-prefixed (K12A, K8B, ...).
        ch_key = self.cfg.channel.name
        if not ch_key.startswith("K"):
            ch_key = "K" + ch_key
        argv = [
            str(self.cfg.binary),
            "-C", ch_key,
            "-G", str(self.cfg.gain),
            "-D", str(self.cfg.detect_seconds),
        ]
        if self.cfg.record_seconds > 0:
            argv += ["-t", str(self.cfg.record_seconds)]
        if self.cfg.bias_t:
            argv += ["-b"]
        env = os.environ.copy()
        # Make sure the binary can dlopen libairspy/libusb.
        env["DYLD_LIBRARY_PATH"] = self.cfg.libpath + ":" + env.get("DYLD_LIBRARY_PATH", "")
        self.proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )
        return self.proc

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    def __enter__(self) -> "EtiTuner":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()


def iter_eti_chunks(proc: subprocess.Popen[bytes], chunk: int = 6144):
    assert proc.stdout is not None
    while True:
        b = proc.stdout.read(chunk)
        if not b:
            return
        yield b
