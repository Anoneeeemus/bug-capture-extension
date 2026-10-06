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
