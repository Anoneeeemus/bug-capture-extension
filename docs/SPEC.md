# Bug Report browser extension: build spec

A Manifest V3 extension for Chrome, Brave and Edge. People outside our company use it to send
bug reports and feature requests about websites. Reports are saved on their own PC, and they zip
the folder and send it to a central person. There is no server and no network call of any kind.

## Behaviour

1. The toolbar button (action, NO default_popup) starts a report for the active tab.
2. The background service worker immediately captures a full page PNG of that tab, BEFORE any UI
   opens, so the form never appears in the shot.
3. It stores the capture and the tab's URL, title and timestamp in IndexedDB under a new report id,
   then opens `report.html?id=<id>` in a new tab next to the source tab.
4. The report page shows: the source page title and URL, a thumbnail of the screenshot (click
   opens it full size in a new tab), a Bug / Feature toggle (default Bug), and two textareas whose
   labels depend on the toggle:
   - Bug: "What were you trying to do?" and "What went wrong?"
   - Feature: "What couldn't you do?" and "What would you like different?"
   Both boxes are required. A "Your name" field is shown prefilled from settings and is required.
   If the name changes, save it back to settings.
5. Submit saves three things via `chrome.downloads.download` with `saveAs: false`:
   - `<subfolder>/<folder>/screenshot.png`
   - `<subfolder>/<folder>/report.json`
   - `<subfolder>/index.csv`, rewritten in full every time with `conflictAction: "overwrite"`.
   `<folder>` is `YYYY-MM-DD HHMM <Bug|Feature> - <page title>`. Strip the title of `<>:"/\|?*`,
   control characters and a trailing dot or space, collapse whitespace and cut it to 60 characters.
   If the folder name already exists for this report set, append ` (2)`, ` (3)`.
   Use local time.
   Report pages create blob URLs with `URL.createObjectURL` and revoke them once the download completes.
6. After a successful save, show "Saved to Downloads/<subfolder>/<folder>" with a Close tab button,
   then delete the report's IndexedDB record. On failure show the error and keep the form intact.
7. A Cancel button deletes the IndexedDB record and closes the tab.
8. If capture fails, for example on chrome://, the web store or the new tab page, still open the
   report page. Show "No screenshot could be taken: <reason>" and allow submit without
   `screenshot.png`.

## Capture

- Primary method: `chrome.debugger.attach({tabId}, "1.3")`, `Page.getLayoutMetrics`, then
  `Page.captureScreenshot` with `format: "png"`, `captureBeyondViewport: true` and
  `clip: {x:0, y:0, width: cssContentSize.width, height: min(cssContentSize.height, 16000), scale: 1}`.
  Always detach in a `finally` block. Record `capture.method = "full-page"` and, if the height was
  capped, `capture.truncated = true`.
- Fallback when attach or capture throws (policy block, another debugger attached, and so on):
  `chrome.tabs.captureVisibleTab(windowId, {format: "png"})`. Record
  `capture.method = "visible-only"` and the reason in `capture.fallbackReason`.
- Expose `self.startReport(tab)` on the service worker global. The action click handler calls it, and the automated test
  calls it directly. It returns `Promise<{id, captureOk, method}>`.

## report.json shape

```json
{
  "schema": 1,
  "type": "bug" | "feature",
  "answers": {"q1": {"label": "...", "text": "..."}, "q2": {"label": "...", "text": "..."}},
  "user": "name from the form",
  "page": {"url": "...", "title": "..."},
  "capturedAt": "ISO 8601 with offset",
  "submittedAt": "ISO 8601 with offset",
  "capture": {"ok": true, "method": "full-page" | "visible-only" | null, "truncated": false, "fallbackReason": null, "error": null},
  "browser": "navigator.userAgent",
  "extensionVersion": "from the manifest"
}
```

## index.csv

Header: `submittedAt,type,user,url,title,folder,q1,q2`. Keep the rows in `chrome.storage.local` under
`indexRows` (one row per submitted report), append the new row, then write the whole CSV. Each row records its subfolder, and each subfolder's
`index.csv` lists only the reports saved in that subfolder, so the index always sits beside the folders it describes. Use RFC 4180
quoting: quote every field and double inner quotes. Prefix a UTF-8 BOM so Excel opens it correctly.
To stop Excel running formulas, prefix any field beginning with `=`, `+`, `-` or `@` with a single quote.

## Options page

Use `options_ui` with `open_in_tab: true`. Settings are stored in `chrome.storage.local`:
- `subfolder`, default `BugReports`. A relative path under Downloads, `/` separated segments allowed.
  Reject an empty value, a leading `/` or `\`, any `..` segment, a drive letter (`C:`), and the characters
  `<>:"|?*`. Show inline validation and do not save while it is invalid.
- `userName`, default empty. On first open, if empty, prefill it from
  `chrome.identity.getProfileUserInfo({accountStatus: "ANY"})` when that returns an email. It
  returns empty in Brave and when not signed in, which is fine, so wrap it in try/catch.
- An explanation that reports land in Downloads/<subfolder>, and to zip that folder to send it.
- If `userName` is still empty when the report page opens, the report page asks for it (step 4 covers this).

## Manifest

`manifest_version: 3`, `name: "Bug Report"`, `version: "1.0.0"`, `minimum_chrome_version: "116"`.
Permissions: `debugger`, `tabs`, `activeTab`, `downloads`, `storage`, `identity`, `identity.email`.
No host permissions, no remote code, no external libraries, no icons (Chrome draws a default).
The action title is "Report a bug or feature". Add a `commands` entry `_execute_action` with
suggested key `Alt+Shift+B`.

## Files

`manifest.json`, `background.js`, `db.js` (a tiny shared IndexedDB helper used by both the service worker,
via `importScripts`, and report.html), `report.html`, `report.js`, `report.css`, `options.html`, `options.js`.
Plain JavaScript, no build step, no modules (importScripts in the worker, classic scripts in pages).
The style should be clean and neutral, system font, readable at 400px wide, with no framework.

## Fixed UI contract (the automated test relies on it)

- The Bug / Feature toggle is two radio inputs with visible labels exactly `Bug` and `Feature`.
- Every textarea and input has a `<label for=...>`. The question texts are the label texts, exactly as in step 4,
  and the name field label is `Your name`.
- Buttons are real `<button>` elements with the text `Save report`, `Cancel` and `Close tab`.
- The screenshot thumbnail is an `<img alt="Screenshot">`.
- The success message is an element whose text starts `Saved to Downloads/`. The no screenshot notice text starts
  `No screenshot could be taken`.
- Options page: input labelled `Downloads subfolder`, input labelled `Your name`, a `Save` button, and a status
  element that shows `Saved` or the validation error.

## v1.1 SAVING ROUTE (supersedes step 5, step 6's message, and the subfolder part of the options page)

Field evidence: when the browser setting "Ask where to save each file" is on, `saveAs: false` does NOT suppress the
dialog, so v1 showed 3 Save As dialogs per report plus an index.csv overwrite prompt. v1.1 has two modes.

### Mode A: chosen folder, silent (Chrome, Edge, Brave with the flag)
- The options page has a `Choose folder` button calling `showDirectoryPicker({id: "bug-reports", mode: "readwrite"})`.
  Store the handle in IndexedDB (settings store, key `dirHandle`) via `BugDB.setDirHandle(h)` / `BugDB.getDirHandle()`.
  Show the folder name (`handle.name`) and a `Forget folder` button. If `window.showDirectoryPicker` is undefined,
  hide the button and show: "This browser blocks folder access. In Brave, enable brave://flags/#file-system-access-api
  and restart. Until then each report saves as one .zip in Downloads."
- On Save: get the handle and call `queryPermission({mode:"readwrite"})`. If it isn't "granted", call
  `requestPermission({mode:"readwrite"})` (the Save click is the user gesture). If it's still not granted, or there's no handle,
  use Mode B.
- Write `<folder>/screenshot.png` and `<folder>/report.json` with `getDirectoryHandle(folder,{create:true})` /
  `getFileHandle(..., {create:true})` / `createWritable()`. Folder name rules as v1. Uniqueness is decided by
  checking the real directory: if it exists, append ` (2)`, ` (3)`. Drop the `usedFolders` reservations.
- Then rebuild `index.csv` in the chosen folder's root and write it with createWritable, which overwrites silently.
  Rebuild it from the folder itself: list the subdirectories, read each `report.json`, and skip any that fail to parse.
  It never comes from chrome.storage, so it stays true to what's on disk after deletes or a reinstall.
  Same columns, quoting, BOM and formula guard as v1. Drop `indexRows`.
- Success message: `Saved to <handle.name>/<folder>`.

### Mode B: fallback, ONE download per report
- Build a store-only (no compression) ZIP in report.js with CRC32, about 60 lines and no library, containing
  `screenshot.png` (if any) and `report.json`, and download it as `BugReports/<folder>.zip`
  with `conflictAction: "uniquify"`, `saveAs: false`. No index.csv in this mode.
- Success message: `Saved to Downloads/BugReports/<folder>.zip`, plus a hint line: "Tip: choose a folder in the
  extension options to save without any prompts."
- Remove the `subfolder` setting and its validation from the options page (the fixed `BugReports` is fine).
  Remove `usedFolders`/`indexRows` code.

### Concurrency and retry
- Keep `navigator.locks.request('bug-report-save', ...)` around the whole save in both modes. Keep `firstSubmit`.
  In Mode A, a retry reuses the folder chosen on the first attempt, and the files are overwritten in place.

### Fixed UI contract changes
- The options page has `Choose folder`, `Forget folder`, a status element showing `Folder: <name>`, `No folder chosen`
  or the Brave notice, and the `Your name` field with a `Save` button.
- The success text starts `Saved to ` in both modes.
- For tests: options.html must expose `BugDB` on window, so a test can call
  `BugDB.setDirHandle(await navigator.storage.getDirectory())`. An OPFS root handle is a real
  FileSystemDirectoryHandle with permission already granted, which exercises Mode A without a picker.
- manifest version becomes `1.1.0`.

## v1.2: FOLDER FIRST (supersedes the v1.1 Mode A/B selection rules)

Field evidence (2026-10-06): with no folder chosen, v1.1 silently used Mode B, so every report became a zip
behind a Save As dialog. The folder was only choosable in options, which users never open. Mode B must be the rare exception.

- **Save with no folder chosen, where `window.showDirectoryPicker` exists:** the Save click itself calls
  `showDirectoryPicker({id:"bug-reports", mode:"readwrite"})` (the report page is a full tab, so this works), stores the
  handle, and continues the save in Mode A. Show a line above the Save button before the first save:
  "First save: you'll be asked once where to keep reports." After a folder is chosen, show
  "Saving to: <name>" with a small `Change folder` button that re-runs the picker.
- **The user cancels the picker (AbortError):** save nothing, and keep the form. Show
  "No folder chosen, report not saved yet. Click Save report to choose one."
- **requestPermission is denied for a stored handle:** save nothing, and keep the form. Show
  "Folder access was refused." plus two buttons, `Try again` (re-request) and `Choose another folder` (picker).
  Never fall back to a zip silently.
- **Mode B (single zip via downloads) only when `window.showDirectoryPicker` is undefined**, which means Brave without the flag. Show the
  Brave notice from v1.1 on the report page before saving, so the user knows why.
- Options page: keep Choose/Forget folder and the name field.
- Fixed UI contract additions: the `Change folder`, `Try again` and `Choose another folder` buttons, and the texts above verbatim.
- Version becomes `1.2.0`.

## v1.2 STORAGE LAYOUT (2026-10-06; supersedes per-report folders, report.json files and the index rebuild)

In the chosen folder (Mode A):
- `reports.csv`: ONE file, one row APPENDED per report. Create it with a UTF-8 BOM and a header if it's missing. Append with
  `createWritable({keepExistingData: true})`, then `seek(file.size)` and write the row. Never rewrite existing rows.
  Columns, in this order: `id,submittedAt,type,user,url,title,q1_label,q1,q2_label,q2,screenshot,captureMethod,truncated,browser,extensionVersion`.
  `screenshot` is the PNG's path relative to the folder (`screenshots/<name>.png`) or empty. Same RFC 4180 quoting
  (all fields quoted, inner quotes doubled) and the same formula guard as before. Rows end with CRLF.
- `screenshots/<YYYY-MM-DD HHMM> <Bug|Feature> - <title>.png`: the same sanitising rules as the old folder names. Uniqueness
  is checked against the real `screenshots` directory, appending ` (2)`, ` (3)`.
- Order: write the PNG first, then append the row. A retry of the same report must not append a second row. Keep a
  `rowWritten` flag with firstSubmit, and reuse the reserved PNG name, overwriting it.
- No per-report folders, no report.json, no index rebuild.
- Mode B (no showDirectoryPicker only): unchanged, one zip per report containing screenshot.png and report.json (the
  same object as before, so the collector can read it).
