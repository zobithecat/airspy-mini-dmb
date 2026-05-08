"""Stage-1 GUI: pick a Korean T-DMB channel, tune via eti-cmdline-airspy,
parse FIC, show the ensemble service list.

Stage 2/3 will hook MSC FEC and PyAV-based video playback into the right pane.
"""
from __future__ import annotations
import sys
from PyQt6 import QtCore, QtGui, QtWidgets

from ..channels import SEOUL_METRO, Channel, by_name
from ..eti import parse_frame, FRAME_SIZE
from ..eti.fic import FicAccumulator, Ensemble
from ..process import EtiTuner, TunerConfig, iter_eti_chunks


class _TunerWorker(QtCore.QThread):
    """Owns the eti-cmdline subprocess + ETI parser. Emits ensemble updates."""

    ensemble_updated = QtCore.pyqtSignal(object)   # Ensemble
    status = QtCore.pyqtSignal(str)
    stderr_line = QtCore.pyqtSignal(str)

    def __init__(self, cfg: TunerConfig):
        super().__init__()
        self.cfg = cfg
        self._stop = False
        self.fic = FicAccumulator()

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:
        from threading import Thread
        try:
            with EtiTuner(self.cfg) as t:
                self.status.emit(f"tuned {self.cfg.channel.name} ({self.cfg.channel.freq_hz/1e6:.3f} MHz)")
                proc = t.proc
                assert proc is not None

                def pump_stderr() -> None:
                    assert proc.stderr is not None
                    for raw in proc.stderr:
                        try:
                            line = raw.decode("utf-8", "replace").rstrip()
                        except Exception:
                            continue
                        if line:
                            self.stderr_line.emit(line)

                Thread(target=pump_stderr, daemon=True).start()

                frames = 0
                last_emit = 0
                for chunk in iter_eti_chunks(proc, FRAME_SIZE):
                    if self._stop:
                        break
                    if len(chunk) != FRAME_SIZE:
                        continue
                    try:
                        f = parse_frame(chunk)
                    except Exception:
                        continue
                    if f.fic_present and f.fic:
                        self.fic.feed_fic(f.fic)
                    frames += 1
                    if frames - last_emit >= 25:  # ~600 ms
                        last_emit = frames
                        self.ensemble_updated.emit(self.fic.ensemble)
                        self.status.emit(
                            f"frames={frames}  FIB ok={self.fic.fib_ok}/{self.fic.fib_total}"
                        )
        except FileNotFoundError as e:
            self.status.emit(f"error: {e}")
        except Exception as e:
            self.status.emit(f"tuner crashed: {e}")


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Airspy Mini — Korean T-DMB (stage 1)")
        self.resize(900, 600)

        self.worker: _TunerWorker | None = None

        # --- left: channel picker
        self.channel_combo = QtWidgets.QComboBox()
        for c in SEOUL_METRO:
            self.channel_combo.addItem(f"{c.name}  {c.freq_hz/1e6:.3f} MHz  {c.note}", c.name)

        self.gain_spin = QtWidgets.QSpinBox()
        self.gain_spin.setRange(1, 21)
        self.gain_spin.setValue(18)

        self.tune_btn = QtWidgets.QPushButton("Tune")
        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.setEnabled(False)

        left = QtWidgets.QFormLayout()
        left.addRow("Channel:", self.channel_combo)
        left.addRow("Gain (1..21):", self.gain_spin)
        left.addRow(self.tune_btn)
        left.addRow(self.stop_btn)
        left_w = QtWidgets.QWidget()
        left_w.setLayout(left)

        # --- center: ensemble + service tree
        self.ensemble_label = QtWidgets.QLabel("Ensemble: —")
        self.ensemble_label.setStyleSheet("font-weight: bold;")
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["SId / SubCh", "Type", "Bitrate", "Protection", "Label"])
        self.tree.setColumnWidth(0, 140)
        self.tree.setColumnWidth(4, 240)

        center = QtWidgets.QVBoxLayout()
        center.addWidget(self.ensemble_label)
        center.addWidget(self.tree, 1)
        center_w = QtWidgets.QWidget()
        center_w.setLayout(center)

        # --- right: stderr / log
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(500)
        self.log.setStyleSheet("font-family: Menlo, monospace; font-size: 11px;")

        splitter = QtWidgets.QSplitter()
        splitter.addWidget(left_w)
        splitter.addWidget(center_w)
        splitter.addWidget(self.log)
        splitter.setSizes([200, 500, 250])
        self.setCentralWidget(splitter)

        self.statusBar().showMessage("idle")

        self.tune_btn.clicked.connect(self._on_tune)
        self.stop_btn.clicked.connect(self._on_stop)

    # --- slots ---------------------------------------------------------
    def _on_tune(self) -> None:
        if self.worker:
            return
        ch_name = self.channel_combo.currentData()
        cfg = TunerConfig(channel=by_name(ch_name), gain=self.gain_spin.value())
        self.worker = _TunerWorker(cfg)
        self.worker.ensemble_updated.connect(self._on_ensemble)
        self.worker.status.connect(self.statusBar().showMessage)
        self.worker.stderr_line.connect(lambda s: self.log.appendPlainText(s))
        self.worker.finished.connect(self._on_worker_done)
        self.worker.start()
        self.tune_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.tree.clear()
        self.ensemble_label.setText("Ensemble: scanning…")

    def _on_stop(self) -> None:
        if self.worker:
            self.worker.stop()

    def _on_worker_done(self) -> None:
        self.worker = None
        self.tune_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.statusBar().showMessage("stopped")

    def _on_ensemble(self, ens: Ensemble) -> None:
        eid_txt = f"0x{ens.eid:04X}" if ens.eid is not None else "?"
        label = ens.label or "(unlabeled)"
        self.ensemble_label.setText(f"Ensemble: {label}   EId={eid_txt}")

        self.tree.clear()
        for sid in sorted(ens.services):
            svc = ens.services[sid]
            top = QtWidgets.QTreeWidgetItem([
                f"SId 0x{sid:0{8 if svc.is_data else 4}X}",
                "data" if svc.is_data else "audio/video",
                "", "",
                svc.label,
            ])
            self.tree.addTopLevelItem(top)
            for c in svc.components:
                if c.sub_ch_id is None:
                    continue
                sub = ens.sub_channels.get(c.sub_ch_id)
                br = f"{sub.bitrate_kbps} kbps" if sub else ""
                prot = sub.protection if sub else ""
                top.addChild(QtWidgets.QTreeWidgetItem([
                    f"  SubCh {c.sub_ch_id}",
                    f"{c.transport} ({c.ascty_or_dscty})",
                    br, prot,
                    c.label,
                ]))
        self.tree.expandAll()


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
