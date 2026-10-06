# Automated test spec for the Bug Report extension

Write `tests/run_e2e.py`, a single Python 3 script that uses only the stdlib and Playwright for Python
(sync API, already installed, Chromium bundled). It loads the unpacked extension from `../extension`
(relative to the script) and checks the extension behaviour against SPEC.md, which is included below.
It exits 0 only if every check passes. It prints one line per check (`PASS name` or `FAIL name: detail`)
and a final count. Keep the output short, never dump the page HTML or base64.

## Setup

- Use a fresh temp profile dir and a temp downloads dir, and delete both at the end unless `--keep` is given.
- Launch with `chromium.launch_persistent_context(profile, headless=True, args=["--disable-extensions-except=<ext>", "--load-extension=<ext>"])`.
  If that headless run fails to load the extension, fall back to `channel="chromium"`.
- Downloads trap: Playwright normally intercepts downloads and saves them under GUID names. The
  extension's `chrome.downloads` subfolder layout must be tested exactly as a user would get it. Before
  any report, open a browser level CDP session (`context.browser` may be None for persistent contexts, so
  use `context.new_cdp_session(page)` on a page and send `Browser.setDownloadBehavior` with
  `{"behavior": "allow", "downloadPath": <tmp downloads dir>}`). If that does not route
  `chrome.downloads` files, write a `Default/Preferences` JSON into the profile before launch with
  `download.default_directory` and `download.prompt_for_download: false`, and launch with
  `accept_downloads=False`. Pick whichever approach makes files appear at
  `<downloads>/<subfolder>/<folder>/...`. Print which approach was used.
- Get the extension service worker: `context.service_workers` or `context.wait_for_event("serviceworker")`.
- Serve a local test page from a `http.server` thread on 127.0.0.1 on a free port. The page is 1280px wide content,
  body height 5000px, title `Test: a/b "page" <x>?`, and a red 50px bar at the very bottom.

## Checks

1. `full_page_capture`: open the test page, then in the worker evaluate
   `chrome.tabs.query({url: "<page url>"})` and `self.startReport(tab)`. Expect `captureOk === true` and
   `method === "full-page"`.
2. `report_tab_opens`: a page whose URL contains `report.html?id=` appears. Expect the screenshot thumbnail
   image to have loaded (`naturalWidth > 0`).
3. `labels_switch`: with Bug selected, expect the label texts "What were you trying to do?" / "What went
   wrong?". Switch to Feature and expect "What couldn't you do?" / "What would you like different?". Switch back to Bug.
4. `bug_submit`: fill the name `=Tester`, both boxes (q2 contains a comma, a double quote and a newline),
   submit, wait for text starting "Saved to".
5. `files_bug`: exactly one folder under `<downloads>/BugReports/` matching
   `^\d{4}-\d{2}-\d{2} \d{4} Bug - ` whose name contains none of `<>:"/\|?*`. Inside it, `screenshot.png` and
   `report.json`. Read the PNG height from the IHDR chunk with `struct` and expect it to be >= 4900 px.
   Also check that some pixel near the bottom is red: decode with zlib only if simple, otherwise skip that sub-assert and say so.
6. `report_json`: `schema == 1`, `type == "bug"`, `user == "=Tester"`, the q2 text round-trips exactly, `page.url` matches,
   `capture.method == "full-page"`, `capturedAt` and `submittedAt` parse as ISO 8601 with an offset.
7. `index_csv`: `BugReports/index.csv` starts with a UTF-8 BOM, the header matches SPEC exactly, there is one data row,
   the parsed `user` field equals `'=Tester` (formula guard), and the multi-line q2 field parses correctly with the `csv` module.
8. `no_screenshot_page`: open `chrome://version`, run startReport on it. Expect `captureOk === false`.
   The report page shows "No screenshot could be taken". Submit as Feature. Expect a new `... Feature - ...` folder
   with `report.json` (capture.ok false) and no `screenshot.png`. `index.csv` now has 2 data rows.
9. `options_validation`: open the options page (`chrome-extension://<id>/options.html`). Set the subfolder
   to `../evil` and try to save, expecting `chrome.storage.local` subfolder to be unchanged. Set it to `Reports/Test` and save,
   expecting it to be stored.
10. `custom_subfolder`: run one more Bug report on the test page. Expect it under `<downloads>/Reports/Test/` with its own
    `index.csv`. Nothing must have been written outside the downloads dir.
11. `idb_cleanup`: after the submits, in the worker, expect the IndexedDB report store to be empty.

Wait for downloads with polling of the filesystem (timeout 15 s per file), not fixed sleeps. Make the failures
specific: the expected vs actual value.
