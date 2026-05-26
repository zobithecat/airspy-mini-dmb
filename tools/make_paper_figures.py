"""Generate publication-quality figures for the paper.

Outputs (all written to paper/figures/):
  - spectrum_k8b.png         PSD of K8B raw I/Q showing the 1.5 MHz DAB
                             plateau and the narrowband interferer at
                             -1.21 MHz.  Drives Section II.
  - rs_phase_hist.png        Histogram of 0x47 hit counts at each of
                             204 candidate phases on the post-Viterbi
                             MSC byte stream.  Visualises the sync-
                             alignment claim of Section III.

Both files end up referenced from paper/main.tex, paper/main_ko.tex,
and the ICCE short version.

Usage:
    python tools/make_paper_figures.py
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")          # headless render
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parents[1]
FIG_DIR = REPO / "paper" / "figures"
ICCE_FIG_DIR = REPO / "paper" / "icce2027" / "figures"
FIG_DIR.mkdir(parents=True, exist_ok=True)
ICCE_FIG_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPO / "src"))

# Matplotlib defaults tuned for IEEE / arXiv papers (10pt single-column).
plt.rcParams.update({
    "font.family":  "DejaVu Sans",
    "font.size":    9,
    "axes.labelsize": 9,
    "axes.titlesize": 9,
    "legend.fontsize": 8,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "figure.dpi":     300,
    "savefig.dpi":    300,
    "savefig.bbox":   "tight",
    "savefig.pad_inches": 0.05,
    "axes.spines.top":   False,
    "axes.spines.right": False,
})


# --------------------------------------------------------------------
# Figure 1: PSD of K8B raw I/Q
# --------------------------------------------------------------------
def make_spectrum() -> None:
    raw_path = Path("/tmp/k8b_gqrxgains.raw")
    if not raw_path.exists():
        print(f"  ! {raw_path} not present, skipping spectrum figure")
        return
    fs = 6_000_000
    data = np.fromfile(raw_path, dtype="<i2").astype(np.float32) / 32768.0
    iq = data[::2] + 1j * data[1::2]

    nfft = 65536
    n_segs = min(80, len(iq) // nfft)
    win = np.hanning(nfft)
    psd = np.zeros(nfft)
    for k in range(n_segs):
        seg = iq[k*nfft:(k+1)*nfft] * win
        psd += np.abs(np.fft.fftshift(np.fft.fft(seg))) ** 2
    psd /= n_segs
    # Normalise so peak interferer sits at ~0 dB for legibility
    psd_db = 10 * np.log10(psd + 1e-15)
    psd_db -= psd_db.max()
    freqs = np.linspace(-fs/2, fs/2, nfft) / 1e6  # MHz

    fig, ax = plt.subplots(figsize=(3.5, 2.3))
    ax.plot(freqs, psd_db, color="#0a4d6e", linewidth=0.6)
    # Shade K8B DAB band (1.536 MHz wide, centred at 0)
    ax.axvspan(-0.768, 0.768, color="#0a6e6e", alpha=0.13,
               label="K8B DAB band (1.536 MHz)")
    # Annotate interferer
    ax.annotate("narrowband interferer\n(~181.797 MHz)",
                xy=(-1.21, -8), xytext=(-2.7, -28),
                fontsize=7, ha="left",
                arrowprops=dict(arrowstyle="->", color="#aa3333",
                                lw=0.6))
    ax.set_xlim(-2.9, 2.9)
    ax.set_ylim(-65, 5)
    ax.set_xlabel("Frequency offset from 183.008 MHz (MHz)")
    ax.set_ylabel("PSD (dB, peak-normalised)")
    ax.legend(loc="lower right", frameon=False, fontsize=7)
    ax.grid(alpha=0.25, linewidth=0.4)
    out = FIG_DIR / "spectrum_k8b.png"
    fig.savefig(out)
    plt.close(fig)
    # Mirror to ICCE fig dir
    icce_out = ICCE_FIG_DIR / "spectrum_k8b.png"
    icce_out.write_bytes(out.read_bytes())
    print(f"  ✓ {out}  +  {icce_out}")


# --------------------------------------------------------------------
# Figure 2: RS phase histogram (0x47 hit counts at each of 204 phases)
# --------------------------------------------------------------------
def make_phase_hist() -> None:
    eti = REPO / "data" / "captures" / "k8b_100pct.eti"
    if not eti.exists():
        print(f"  ! {eti} not present, skipping phase histogram")
        return

    from tdmb.eti import parse_frame, FRAME_SIZE
    from tdmb.msc import extract_subchannel

    def _frames(path: Path):
        with path.open("rb") as f:
            while True:
                chunk = f.read(FRAME_SIZE)
                if len(chunk) != FRAME_SIZE:
                    return
                try:
                    yield parse_frame(chunk)
                except Exception:
                    continue

    buf = bytearray()
    for chunk in extract_subchannel(_frames(eti), 1):
        if chunk:
            buf.extend(chunk)
    buf = bytes(buf)
    n = len(buf)
    n_blocks = n // 204

    scores = np.zeros(204, dtype=np.int32)
    arr = np.frombuffer(buf[: n_blocks * 204], dtype=np.uint8).reshape(n_blocks, 204)
    scores = (arr == 0x47).sum(axis=0)

    best = int(np.argmax(scores))
    best_pct = 100.0 * scores[best] / n_blocks

    fig, ax = plt.subplots(figsize=(3.5, 2.1))
    colours = ["#cccccc"] * 204
    colours[best] = "#0a4d6e"
    ax.bar(np.arange(204), scores, width=1.0,
           color=colours, edgecolor="none")
    ax.set_xlim(0, 203)
    ax.set_xlabel(r"Candidate phase $\phi$ within 204-byte cycle")
    ax.set_ylabel(r"Blocks with 0x47 at phase $\phi$")
    ax.annotate(rf"$\phi^*={best}$" + "\n" + f"({best_pct:.1f}% hit)",
                xy=(best, scores[best]),
                xytext=(best - 70, scores[best] * 0.6),
                fontsize=8, ha="left",
                arrowprops=dict(arrowstyle="->", color="#0a4d6e", lw=0.6))
    ax.grid(alpha=0.25, linewidth=0.4)
    out = FIG_DIR / "rs_phase_hist.png"
    fig.savefig(out)
    plt.close(fig)
    icce_out = ICCE_FIG_DIR / "rs_phase_hist.png"
    icce_out.write_bytes(out.read_bytes())
    print(f"  ✓ {out}  +  {icce_out}")
    print(f"    best phase = {best},  hit rate = {best_pct:.1f}%")


if __name__ == "__main__":
    print("Generating paper figures…")
    make_spectrum()
    make_phase_hist()
    print("Done.")
