# Edge / WebView2 Old Version Cleaner

A small PySide6 desktop tool that removes leftover **old version folders**
from Microsoft Edge / WebView2 installations, which normally can't be
deleted manually because the files are still locked by running processes.

It targets these two folders:

```
C:\Program Files (x86)\Microsoft\EdgeWebView\Application
C:\Program Files (x86)\Microsoft\EdgeCore
```

Each of these contains version-numbered subfolders (e.g. `152.0.4191.62`,
`152.0.4191.66`). Only the **latest version is kept**; older ones are safe
to remove once related processes are stopped.

---

## Features

- **Auto-scan** both target directories and list every version folder found
- **Automatically protects the current (latest) version** — its checkbox is
  disabled so it can't be selected for deletion
- **Closes locking processes automatically** before deleting, including:
  - `msedge.exe`
  - `msedgewebview2.exe`
  - `msedgewebview2broker.exe`
  - `MicrosoftEdgeUpdate.exe`
  - `identity_helper.exe`
  - `edge_installer.exe`
  - any other running process whose executable path is inside one of the
    target folders
- **Requests UAC elevation automatically** on startup (Program Files
  requires Administrator rights to modify)
- **Live progress bar** and a log panel showing each step (processes
  closed, folders deleted, errors if any)
- **Confirmation dialog** before anything is deleted
- **Rescan button** to refresh the list after cleanup
- Dark **charcoal theme**, English UI

---

## Requirements

- Windows 10/11
- Python 3.9+
- Packages:
  ```bash
  pip install PySide6 psutil
  ```

> `psutil` is used to detect and terminate locking processes. If it's not
> installed, the app will still run, but you'll need to close
> Edge/WebView2 manually before deleting.

---

## Usage

1. Install the requirements above.
2. Run the script:
   ```bash
   python edge_cleaner.py
   ```
3. A UAC prompt will appear (the app relaunches itself as Administrator
   automatically if it isn't already elevated). Approve it.
4. The app scans both target folders and lists every version found.
   - Rows marked **CURRENT (kept)** are disabled and cannot be selected.
   - Rows marked **OLD** are pre-checked for removal.
5. Uncheck any folder you want to keep, then click
   **Delete Selected Old Versions**.
6. Confirm the dialog. The app will:
   1. Close any Edge/WebView2-related process still running.
   2. Wait briefly for file handles to release.
   3. Delete the selected old version folders.
   4. Show progress and log output live, then a summary when finished.
7. Click **Rescan** anytime to refresh the folder list.

---

## Notes & Safety

- The tool only deletes folders whose name matches a version number
  pattern (e.g. `123.0.4567.89`) inside the two target directories — it
  will not touch `SetupMetrics`, `Optimized`, or other non-version items.
- The **latest version folder in each directory is never deletable**
  through the UI, so the active Edge/WebView2 installation stays intact.
- If a folder fails to delete (e.g. a process still holds a lock), the
  app retries a few times and reports failures in the summary — nothing
  is deleted silently without being logged.
- If you add or need to clean additional folders (e.g. another Edge
  channel path), add them to the `TARGET_DIRS` list near the top of
  `edge_cleaner.py`.

---

## Disclaimer

This tool permanently deletes files under `Program Files`. Review the
selected folders before confirming deletion. Use at your own risk.
