"""
Edge / WebView2 Old Version Cleaner
------------------------------------
Scans:
    C:\\Program Files (x86)\\Microsoft\\EdgeWebView\\Application
    C:\\Program Files (x86)\\Microsoft\\EdgeCore
for old version-numbered folders (e.g. 152.0.4191.62), kills any
process locking those files (msedge / msedgewebview2 / edge update
helpers, plus anything running from inside those folders), then
deletes the old folders. Requires elevation (UAC) because the
target paths live under Program Files.

Requirements (run on Windows):
    pip install PySide6 psutil

Run:
    python edge_cleaner.py
The app will relaunch itself elevated (UAC prompt) if not already
running as Administrator.
"""

import ctypes
import os
import re
import shutil
import stat
import sys
import time

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QCheckBox, QPushButton, QLabel,
    QProgressBar, QPlainTextEdit, QHeaderView, QMessageBox, QAbstractItemView
)

try:
    import psutil
except ImportError:
    psutil = None

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

TARGET_DIRS = [
    r"C:\Program Files (x86)\Microsoft\EdgeWebView\Application",
    r"C:\Program Files (x86)\Microsoft\EdgeCore",
]

VERSION_RE = re.compile(r"^\d+(\.\d+){2,3}$")

PROCESS_NAMES = [
    "msedge.exe",
    "msedgewebview2.exe",
    "msedgewebview2broker.exe",
    "microsoftedgeupdate.exe",
    "identity_helper.exe",
    "edge_installer.exe",
]

CHARCOAL_QSS = """
QMainWindow, QWidget {
    background-color: #1e1f22;
    color: #e6e6e6;
    font-family: 'Segoe UI';
    font-size: 10pt;
}
QLabel#Header {
    font-size: 15pt;
    font-weight: 600;
    color: #f2f2f2;
    padding: 4px 0px;
}
QLabel#SubHeader {
    color: #9aa0a6;
    padding-bottom: 6px;
}
QTableWidget {
    background-color: #26282b;
    alternate-background-color: #2c2e32;
    gridline-color: #3a3d42;
    border: 1px solid #3a3d42;
    border-radius: 6px;
    selection-background-color: #3a5a6b;
}
QHeaderView::section {
    background-color: #2c2e32;
    color: #cfd3d8;
    padding: 6px;
    border: none;
    border-bottom: 1px solid #3a3d42;
    font-weight: 600;
}
QPushButton {
    background-color: #33363b;
    color: #e6e6e6;
    border: 1px solid #45484e;
    border-radius: 6px;
    padding: 8px 16px;
}
QPushButton:hover {
    background-color: #3d4046;
    border: 1px solid #5c9eb0;
}
QPushButton:pressed {
    background-color: #2a2c30;
}
QPushButton:disabled {
    color: #6b6f75;
    background-color: #2a2c30;
    border: 1px solid #33363b;
}
QPushButton#DangerButton {
    background-color: #5c3232;
    border: 1px solid #8a4a4a;
}
QPushButton#DangerButton:hover {
    background-color: #703c3c;
    border: 1px solid #b05a5a;
}
QPlainTextEdit {
    background-color: #17181a;
    color: #b7c8ce;
    border: 1px solid #3a3d42;
    border-radius: 6px;
    font-family: Consolas, monospace;
    font-size: 9pt;
}
QProgressBar {
    background-color: #26282b;
    border: 1px solid #3a3d42;
    border-radius: 6px;
    text-align: center;
    color: #e6e6e6;
    height: 20px;
}
QProgressBar::chunk {
    background-color: #5c9eb0;
    border-radius: 5px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
}
"""

# --------------------------------------------------------------------------
# Elevation helpers
# --------------------------------------------------------------------------

def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def relaunch_as_admin():
    params = " ".join(f'"{a}"' for a in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, f'"{os.path.abspath(sys.argv[0])}" {params}', None, 1
    )


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

def folder_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def human_size(n):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def version_key(v):
    return tuple(int(p) for p in v.split("."))


def scan_all():
    """Returns list of dicts: base_dir, version, path, is_current"""
    results = []
    for base in TARGET_DIRS:
        if not os.path.isdir(base):
            continue
        versions = []
        for entry in os.scandir(base):
            if entry.is_dir() and VERSION_RE.match(entry.name):
                versions.append(entry.name)
        if not versions:
            continue
        latest = max(versions, key=version_key)
        for v in versions:
            results.append({
                "base_dir": base,
                "version": v,
                "path": os.path.join(base, v),
                "is_current": (v == latest),
            })
    return results


# --------------------------------------------------------------------------
# Worker thread: kill processes + delete folders
# --------------------------------------------------------------------------

class CleanWorker(QThread):
    log = Signal(str)
    progress = Signal(int)
    finished_ok = Signal(bool, str)

    def __init__(self, folders):
        super().__init__()
        self.folders = folders  # list of path strings to delete

    def run(self):
        try:
            self.progress.emit(2)
            self.log.emit("Stopping Edge / WebView2 related processes...")
            self._kill_related_processes()
            self.progress.emit(20)

            time.sleep(1.0)  # let file handles release

            total = len(self.folders)
            if total == 0:
                self.log.emit("Nothing to delete.")
                self.progress.emit(100)
                self.finished_ok.emit(True, "No old folders were selected.")
                return

            step = 75.0 / total
            current_progress = 20.0
            failures = []

            for folder in self.folders:
                self.log.emit(f"Deleting: {folder}")
                ok = self._remove_folder(folder)
                if not ok:
                    failures.append(folder)
                current_progress += step
                self.progress.emit(int(current_progress))

            self.progress.emit(100)
            if failures:
                msg = "Finished with errors. Could not remove:\n" + "\n".join(failures)
                self.finished_ok.emit(False, msg)
            else:
                self.finished_ok.emit(True, "All selected old versions were removed successfully.")

        except Exception as e:
            self.log.emit(f"Fatal error: {e}")
            self.finished_ok.emit(False, str(e))

    # -- helpers -----------------------------------------------------

    def _kill_related_processes(self):
        if psutil is None:
            self.log.emit("psutil not available - skipping automatic process termination.")
            self.log.emit("Please make sure Edge/WebView2 apps are closed manually.")
            return

        target_dirs_lower = [d.lower() for d in TARGET_DIRS]
        killed = set()

        for proc in psutil.process_iter(["pid", "name", "exe"]):
            try:
                name = (proc.info.get("name") or "").lower()
                exe = (proc.info.get("exe") or "")
                exe_lower = exe.lower()

                should_kill = name in PROCESS_NAMES or any(
                    exe_lower.startswith(td) for td in target_dirs_lower
                )
                if should_kill:
                    proc.terminate()
                    killed.add(f"{proc.info.get('name')} (PID {proc.info.get('pid')})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if killed:
            time.sleep(1.5)
            # force kill anything still alive with matching names
            for proc in psutil.process_iter(["pid", "name", "exe"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    exe_lower = (proc.info.get("exe") or "").lower()
                    if name in PROCESS_NAMES or any(exe_lower.startswith(td) for td in target_dirs_lower):
                        proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            for k in sorted(killed):
                self.log.emit(f"  Closed: {k}")
        else:
            self.log.emit("No related running processes found.")

    def _remove_folder(self, path, retries=3):
        def onerror(func, p, exc_info):
            try:
                os.chmod(p, stat.S_IWRITE)
                func(p)
            except Exception:
                pass

        for attempt in range(1, retries + 1):
            try:
                if os.path.exists(path):
                    shutil.rmtree(path, onerror=onerror)
                if not os.path.exists(path):
                    return True
            except Exception as e:
                self.log.emit(f"  Attempt {attempt} failed: {e}")
                time.sleep(1.0)
        return not os.path.exists(path)


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Edge / WebView2 Old Version Cleaner")
        self.resize(760, 520)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        header = QLabel("Edge / WebView2 Old Version Cleaner")
        header.setObjectName("Header")
        layout.addWidget(header)

        sub = QLabel(
            "Removes leftover old version folders from EdgeWebView and EdgeCore.\n"
            "Running processes locking these files will be closed automatically."
        )
        sub.setObjectName("SubHeader")
        layout.addWidget(sub)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Remove", "Location", "Version", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, stretch=3)

        btn_row = QHBoxLayout()
        self.rescan_btn = QPushButton("Rescan")
        self.rescan_btn.clicked.connect(self.populate_table)
        self.clean_btn = QPushButton("Delete Selected Old Versions")
        self.clean_btn.setObjectName("DangerButton")
        self.clean_btn.clicked.connect(self.start_clean)
        btn_row.addWidget(self.rescan_btn)
        btn_row.addStretch()
        btn_row.addWidget(self.clean_btn)
        layout.addLayout(btn_row)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)

        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setFixedHeight(140)
        layout.addWidget(self.log_box, stretch=1)

        self.rows_data = []  # list of (checkbox, entry_dict)
        self.worker = None

        self.populate_table()

    # ------------------------------------------------------------

    def populate_table(self):
        self.table.setRowCount(0)
        self.rows_data.clear()
        self.log_box.clear()

        entries = scan_all()
        if not entries:
            self.log("No target folders found on this system.")
        for entry in entries:
            row = self.table.rowCount()
            self.table.insertRow(row)

            checkbox = QCheckBox()
            checkbox.setChecked(not entry["is_current"])
            checkbox.setEnabled(not entry["is_current"])
            cell_widget = QWidget()
            cb_layout = QHBoxLayout(cell_widget)
            cb_layout.addWidget(checkbox)
            cb_layout.setAlignment(Qt.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(row, 0, cell_widget)

            self.table.setItem(row, 1, QTableWidgetItem(entry["base_dir"]))
            self.table.setItem(row, 2, QTableWidgetItem(entry["version"]))
            status = "CURRENT (kept)" if entry["is_current"] else "OLD"
            status_item = QTableWidgetItem(status)
            self.table.setItem(row, 3, status_item)

            self.rows_data.append((checkbox, entry))

        self.log(f"Scan complete. {len(entries)} version folder(s) found.")

    def log(self, msg):
        self.log_box.appendPlainText(msg)

    # ------------------------------------------------------------

    def start_clean(self):
        selected = [entry["path"] for cb, entry in self.rows_data if cb.isChecked()]

        if not selected:
            QMessageBox.information(self, "Nothing selected",
                                     "There are no old version folders selected for removal.")
            return

        confirm = QMessageBox.question(
            self,
            "Confirm deletion",
            "This will close Edge / WebView2 related processes and permanently delete:\n\n"
            + "\n".join(selected) +
            "\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        self.clean_btn.setEnabled(False)
        self.rescan_btn.setEnabled(False)
        self.progress.setValue(0)
        self.log("Starting cleanup...")

        self.worker = CleanWorker(selected)
        self.worker.log.connect(self.log)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.finished_ok.connect(self.on_clean_finished)
        self.worker.start()

    def on_clean_finished(self, success, message):
        self.clean_btn.setEnabled(True)
        self.rescan_btn.setEnabled(True)
        self.log(message)
        if success:
            QMessageBox.information(self, "Done", message)
        else:
            QMessageBox.warning(self, "Completed with issues", message)
        self.populate_table()


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main():
    if os.name == "nt" and not is_admin():
        relaunch_as_admin()
        sys.exit(0)

    app = QApplication(sys.argv)
    app.setStyleSheet(CHARCOAL_QSS)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
