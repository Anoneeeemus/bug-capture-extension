# Changelog

All dates are release dates of the unpacked extension.

## 1.2.2 (2026-10-06)

- Suggested folder renamed to `BUG_REPORTS`.

## 1.2.1 (2026-10-06)

Text only, found while testing in real Chrome on Windows 11.

- Manifest description now says reports go to `reports.csv` and a `screenshots` folder in a folder
  you choose, not to Downloads.
- First save line now tells testers to create and pick a dedicated folder (`BUG_REPORTS`, for
  example on the Desktop), because Chrome refuses Desktop, Documents and Downloads themselves
  ("Can't open this folder ... contains system files").
- If the folder picker rejects with `SecurityError` or `NotAllowedError`, the report tab (and the
  options page) explains that Chrome won't allow that folder. Nothing is saved.
- Options page no longer claims that reports go to a zip without a folder.
- User README describes the "started debugging this browser" bar accurately: it stays a few seconds
  and its Cancel only stops that one full page capture.

## 1.2.0 (2026-10-06)

Folder first, and a simpler layout on disk.

- In 1.1.0, with no folder chosen in options, every report silently became a zip download, and with
  "Ask where to save each file" on that meant a Save As dialog per report. Nobody opens the options
  page, so in practice the folder mode was never used.
- The first Save now asks for the folder from the Save click itself, stores the handle, and saves
  silently from then on. The report tab shows "Saving to: <folder>" with **Change folder**.
- Closing the picker, or refusing permission for a stored folder, saves nothing and keeps the form,
  with **Try again** and **Choose another folder**. There is never a silent fall back to a zip.
- The zip fallback is now used only when `showDirectoryPicker` does not exist (Brave without its
  flag), and the report tab says so before saving.
- Storage in the chosen folder changed from one folder per report plus `report.json` and a rebuilt
  `index.csv` to one appended `reports.csv` plus `screenshots/<name>.png`. Rows are appended with
  `keepExistingData` and never rewritten. The PNG is written first, and a retry never appends a
  second row.
- The zip fallback still contains `screenshot.png` and `report.json`.

## 1.1.0 (2026-10-06)

Saving without dialogs.

- With Chrome's "Ask where to save each file" on, `chrome.downloads.download({saveAs: false})` does
  not suppress the dialog, so 1.0.0 showed three Save As dialogs per report plus an overwrite prompt
  for `index.csv`.
- New Mode A: a folder chosen in the options page through the File System Access API, written
  silently with `createWritable`. `index.csv` is rebuilt from the folder contents, so it stays true
  after deletes or a reinstall.
- New Mode B: one store-only zip per report via downloads, written by a small in-page zip builder
  (CRC32, UTF-8 names, DOS dates, central directory) that Windows Explorer opens.
- Brave hides `showDirectoryPicker` unless `brave://flags/#file-system-access-api` is enabled, so the
  options page explains that and Brave falls back to Mode B.
- The Downloads subfolder setting and the stored index rows were removed. IndexedDB moved to version
  2 with a settings store for the folder handle.
- Capture stability: a capture taken while the tab is being shown can fail or come back blank below
  the viewport, and which attempt is hit varies. The worker now captures until two consecutive
  captures (never counting the first) are identical, 150 ms apart.
- Review fixes: a retry only reuses its folder name in the same root folder, an unreadable
  `report.json` keeps the old `index.csv` instead of silently dropping a row, only valid reports are
  indexed, and the folder handle is loaded when the report tab opens so the Save click keeps a fresh
  user activation.

## 1.0.0 (2026-10-06)

First version.

- Toolbar button and Alt+Shift+B start a report. The screenshot is taken before any UI opens, using
  `chrome.debugger` and `Page.captureScreenshot` with `captureBeyondViewport`, falling back to the
  visible tab. Pages that cannot be captured still get a report.
- Report tab with Bug / Feature, two questions and the tester's name. Files saved with
  `chrome.downloads` to `Downloads/BugReports/<folder>/` plus a rewritten `index.csv`.
- Hardening after review, before release:
  - The viewport is no longer resized for capture (it distorted `100vh` layouts and could trigger
    infinite scroll).
  - CSV formula guard covers leading tab, CR and LF as well as `= + - @`.
  - Saves are serialised across report tabs with `navigator.locks`.
  - A retry keeps the first attempt's timestamp and folder name.
  - Timed out downloads are cancelled before their blob URL is revoked.
  - Abandoned reports older than 24 hours are deleted, and a failure to start a report shows a `!`
    badge on the toolbar button.
  - Permissions reduced to `debugger`, `activeTab`, `downloads` and `storage` (no `tabs`, no
    `identity`), minimum Chrome 118.

The test scripts for 1.0.0 and 1.1.0 (`run_e2e_v1.py`, `run_e2e_v11.py`) were superseded by
`tests/run_e2e.py` and removed before this repository was created.
