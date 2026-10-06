#!/usr/bin/env python3
"""Exercise the unpacked extension. Requires only Python 3 and Playwright.

Run: python3 tests/run_e2e.py [--keep] [--chrome PATH]
Tests v1.2 appended CSV storage, folder-first saves and the single-ZIP fallback.
No web servers, accounts, or packages other than Playwright are needed.
"""

import argparse
import csv
import datetime as dt
import html
import io
import json
import os
import re
import shutil
import struct
import tempfile
import threading
import time
import zlib
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


TIMEOUT = 15
TITLE = 'Test: a/b "page" <x>?'
Q1 = "I wanted to report a problem."
Q2 = 'It failed, saying "try again".\nHere is the second line.'
A_Q1 = '=A1, "quoted"\nsecond line'
HEADER = ["id", "submittedAt", "type", "user", "url", "title", "q1_label", "q1",
          "q2_label", "q2", "screenshot", "captureMethod", "truncated", "browser",
          "extensionVersion"]
LABELS = {
    "bug": ("What were you trying to do?", "What went wrong?"),
    "feature": ("What couldn't you do?", "What would you like different?"),
}
CHECKS = (
    "mode_a_setup", "full_page_capture", "report_tab_opens", "labels_switch",
    "first_save_picks_folder", "files_bug", "csv_bug", "no_screenshot_page", "reports_csv",
    "csv_append", "csv_guard_controls", "picker_cancel", "picker_security_error", "permission_denied",
    "no_picker_zip", "downloads_audit", "idb_cleanup",
)
POLL_EVENT = threading.Event()


def brief(value):
    """Bound all diagnostics, including Playwright's potentially long errors."""
    return " ".join(str(value).split())[:300]


def equal(actual, expected, field):
    if actual != expected:
        raise AssertionError(f"{field}: expected {brief(repr(expected))}, actual {brief(repr(actual))}")


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def poll(getter, description, timeout=TIMEOUT):
    """Poll conditions, never assume that a download finishes after a sleep."""
    deadline = time.monotonic() + timeout
    last = None
    while True:
        try:
            value = getter()
            if value:
                return value
            last = value
        except (OSError, ValueError) as exc:
            last = brief(exc)
        if time.monotonic() >= deadline:
            raise AssertionError(f"expected {description} within {timeout}s; actual {brief(last)}")
        POLL_EVENT.wait(min(0.1, max(0, deadline - time.monotonic())))


class TestPage(BaseHTTPRequestHandler):
    def do_GET(self):
        body = (f'<!doctype html><html><head><meta charset="utf-8">'
                f'<title>{html.escape(TITLE)}</title><style>'
                'html,body{margin:0;padding:0}body{width:1280px;height:5000px;'
                'position:relative;background:white}main{padding:20px}'
                '#bottom{position:absolute;left:0;bottom:0;width:1280px;'
                'height:50px;background:rgb(255,0,0)}</style></head>'
                '<body><main>Full page screenshot test</main>'
                '<div id="bottom"></div></body></html>').encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def png_info(data):
    equal(data[:8], b"\x89PNG\r\n\x1a\n", "PNG signature")
    equal(data[12:16], b"IHDR", "first PNG chunk")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", data[16:29])
    require(height >= 4900, f"PNG height: expected >= 4900, actual {height}")
    if depth != 8 or color not in (2, 6) or compression or filtering or interlace:
        raise AssertionError(f"unsupported PNG depth={depth}, color={color}, interlace={interlace}")
    # Chromium ordinarily produces non-interlaced 8-bit RGB/RGBA. Decode these
    # with stdlib only, including all five PNG scanline filters.
    chunks = []
    offset = 8
    while offset + 12 <= len(data):
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        if kind == b"IDAT":
            chunks.append(data[offset + 8:offset + 8 + size])
        offset += size + 12
        if kind == b"IEND":
            break
    raw = zlib.decompress(b"".join(chunks))
    channels = 3 if color == 2 else 4
    stride = width * channels
    equal(len(raw), height * (stride + 1), "PNG decompressed length")
    previous = bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        kind = raw[start]
        row = bytearray(raw[start + 1:start + 1 + stride])
        require(kind <= 4, f"PNG filter: expected 0..4, actual {kind}")
        if kind:
            for x in range(stride):
                left = row[x - channels] if x >= channels else 0
                above = previous[x]
                upper_left = previous[x - channels] if x >= channels else 0
                if kind == 1:
                    prediction = left
                elif kind == 2:
                    prediction = above
                elif kind == 3:
                    prediction = (left + above) // 2
                else:
                    p = left + above - upper_left
                    distances = (abs(p - left), abs(p - above), abs(p - upper_left))
                    prediction = (left, above, upper_left)[distances.index(min(distances))]
                row[x] = (row[x] + prediction) & 255
        if y == height - 1:
            for x in range(0, stride, channels):
                if row[x] >= 220 and row[x + 1] <= 40 and row[x + 2] <= 40:
                    return None
        previous = row
    raise AssertionError("bottom row: expected red, actual no red pixel")


def read_csv(data):
    require(data.startswith(b"\xef\xbb\xbf"), "reports.csv: expected UTF-8 BOM, actual missing BOM")
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline=""), strict=True))
    require(bool(rows), "reports.csv: expected header, actual empty file")
    equal(rows[0], HEADER, "CSV header")
    for row in rows[1:]:
        equal(len(row), len(HEADER), "CSV row field count")
    # Re-encode data rows to check all-field quoting, doubled quotes and CRLF,
    # including embedded newlines. The header may itself be quoted or plain.
    header_end = data.find(b"\r\n")
    require(header_end >= 0, "reports.csv: expected CRLF header ending")
    encoded = io.StringIO(newline="")
    csv.writer(encoded, quoting=csv.QUOTE_ALL, lineterminator="\r\n").writerows(rows[1:])
    equal(data[header_end + 2:], encoded.getvalue().encode("utf-8"), "CSV row encoding")
    return [dict(zip(HEADER, row)) for row in rows[1:]]


def iso_timestamp(value, field):
    require(isinstance(value, str), f"{field}: expected ISO 8601 string, actual {brief(value)}")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AssertionError(f"{field}: expected ISO 8601, actual {brief(value)}") from None
    require(parsed.utcoffset() is not None,
            f"{field}: expected ISO 8601 offset, actual {brief(value)}")


class Suite:
    def __init__(self, context, worker, downloads, url, extension_version):
        self.context = context
        self.worker = worker
        self.downloads = downloads
        self.url = url
        self.extension_version = extension_version
        self.extension_id = worker.url.split("/")[2]
        self.source = None
        self.result = None
        self.report = None
        self.options = None
        self.saved_reports = []
        self.csv_prefix = b""
        self.zip_path = None
        self.mode_b_before = None
        self.mode_b_history = None
        self.failed = 0
        self.passed = 0

    def check(self, name):
        try:
            note = getattr(self, name)()
        except Exception as exc:
            self.failed += 1
            print(f"FAIL {name}: {brief(exc)}", flush=True)
        else:
            self.passed += 1
            if note:
                print(f"NOTE {name}: {note}", flush=True)
            print(f"PASS {name}", flush=True)

    def need(self, value, prerequisite):
        require(value is not None, f"prerequisite {prerequisite} did not complete")
        return value

    def start(self, source):
        source.bring_to_front()
        return self.worker.evaluate("""async () => {
            const tabs = await chrome.tabs.query({active: true, lastFocusedWindow: true});
            if (!tabs.length) throw new Error('expected an active source tab, actual none');
            return await self.startReport(tabs[0]);
        }""")

    def report_for(self, result):
        require(isinstance(result, dict) and bool(result.get("id")),
                f"startReport: expected report id, actual {brief(result)}")
        target = f"chrome-extension://{self.extension_id}/report.html?id={result['id']}"

        def find():
            # Pump Playwright events while waiting for the new extension tab.
            self.worker.evaluate("() => true")
            return next((p for p in self.context.pages if p.url == target), None)

        page = poll(find, f"report tab {target}")
        page.wait_for_load_state("domcontentloaded")
        return page

    def fill(self, page, kind):
        page.get_by_role("radio", name=kind.capitalize(), exact=True).check()
        page.get_by_label("Your name", exact=True).fill("=Tester")
        page.get_by_label(LABELS[kind][0], exact=True).fill(Q1)
        page.get_by_label(LABELS[kind][1], exact=True).fill(Q2)

    def submit(self, page):
        page.get_by_role("button", name="Save report", exact=True).click()
        message = page.get_by_text(re.compile(r"^Saved to "))
        expect(message).to_be_visible(timeout=15000)
        return message.inner_text()

    def mode_a_setup(self):
        self.options = self.context.new_page()
        self.options.goto(f"chrome-extension://{self.extension_id}/options.html")
        expect(self.options.get_by_label("Your name", exact=True)).to_be_visible()
        self.options.evaluate("""async () => {
            await BugDB.setDirHandle(await navigator.storage.getDirectory());
        }""")
        self.options.reload()
        expect(self.options.get_by_role("button", name="Forget folder", exact=True)).to_be_visible()
        root_name = self.options.evaluate("async () => (await navigator.storage.getDirectory()).name")
        expect(self.options.get_by_text(f"Folder: {root_name}", exact=True)).to_be_visible()
        self.forget_folder()

    def opfs(self):
        # Read the real files from an extension page; the service worker has no
        # DOM/canvas and chrome.downloads does not describe Mode A writes.
        return self.need(self.options, "mode_a_setup").evaluate("""async () => {
            const root = await navigator.storage.getDirectory();
            const screenshots = {};
            const directories = [];
            const rootFiles = [];
            let csv = null;
            for await (const [name, entry] of root.entries()) {
                if (entry.kind === 'file') {
                    rootFiles.push(name);
                    if (name === 'reports.csv') {
                        csv = Array.from(new Uint8Array(await (await entry.getFile()).arrayBuffer()));
                    }
                    continue;
                }
                directories.push(name);
                if (name !== 'screenshots') continue;
                for await (const [filename, handle] of entry.entries()) {
                    if (handle.kind !== 'file' || !filename.endsWith('.png')) {
                        screenshots[filename] = null;
                        continue;
                    }
                    {
                        const file = await handle.getFile();
                        const buffer = await file.arrayBuffer();
                        const view = new DataView(buffer);
                        const signature = Array.from(new Uint8Array(buffer, 0, 8));
                        const ihdr = String.fromCharCode(...new Uint8Array(buffer, 12, 4));
                        const height = view.getUint32(20, false);
                        const bitmap = await createImageBitmap(file);
                        const canvas = document.createElement('canvas');
                        canvas.width = bitmap.width;
                        canvas.height = 1;
                        const ctx = canvas.getContext('2d');
                        ctx.drawImage(bitmap, 0, bitmap.height - 1, bitmap.width, 1,
                                      0, 0, bitmap.width, 1);
                        const pixels = ctx.getImageData(0, 0, bitmap.width, 1).data;
                        let bottomRed = false;
                        for (let x = 0; x < pixels.length; x += 4) {
                            if (pixels[x] >= 220 && pixels[x+1] <= 40 && pixels[x+2] <= 40) {
                                bottomRed = true;
                                break;
                            }
                        }
                        screenshots[filename] = {signature, ihdr, height, bottomRed, decodedHeight: bitmap.height};
                        bitmap.close();
                    }
                }
            }
            return {screenshots, directories: directories.sort(), rootFiles: rootFiles.sort(), csv};
        }""")

    def report_name(self, name, kind, title=None):
        if title is None:
            pattern = rf"^\d{{4}}-\d{{2}}-\d{{2}} \d{{4}} {kind} - .*"
        else:
            sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', title)
            sanitized = re.sub(r'\s+', ' ', sanitized).strip()[:60].rstrip('. ')
            pattern = rf"^\d{{4}}-\d{{2}}-\d{{2}} \d{{4}} {kind} - {re.escape(sanitized)}(?: \((?:[2-9]|[1-9]\d+)\))?$"
        require(re.fullmatch(pattern, name),
                f"report name: expected {pattern}, actual {brief(name)}")

    def file(self, path):
        # Completion in chrome.downloads prevents reading a partial ZIP.
        def completed():
            if not path.is_file() or not path.stat().st_size:
                return False
            items = self.worker.evaluate("() => chrome.downloads.search({})")
            matches = [d for d in items if Path(d.get("filename", "")).resolve() == path.resolve()]
            if not matches:
                return False
            newest = max(matches, key=lambda d: d["id"])
            return path if newest.get("state") == "complete" else False

        return poll(completed, f"completed file {path.relative_to(self.downloads)}")

    def reports(self, count):
        snapshot = self.opfs()
        equal(snapshot["rootFiles"], ["reports.csv"], "OPFS root files")
        equal(snapshot["directories"], ["screenshots"], "OPFS storage directories")
        require(snapshot["csv"] is not None, "OPFS root: expected reports.csv")
        data = bytes(snapshot["csv"])
        equal(data[:len(self.csv_prefix)], self.csv_prefix, "earlier CSV bytes unchanged")
        rows = read_csv(data)
        equal(len(rows), count, "CSV data row count")
        equal([r["id"] for r in rows], [r["id"] for r in self.saved_reports],
              "CSV report append order")
        equal(len({r["id"] for r in rows}), count, "CSV unique report ids")
        paths = [r["screenshot"] for r in rows if r["screenshot"]]
        equal(len(set(paths)), len(paths), "unique screenshot names")
        equal(set(paths), {f"screenshots/{name}" for name in snapshot["screenshots"]},
              "CSV screenshot paths on disk")
        for row, expected in zip(rows, self.saved_reports):
            for field in ("id", "type", "user", "url", "title", "q1", "q2"):
                equal(row[field], expected[field], f"CSV {field}")
            for field, label in zip(("q1_label", "q2_label"), LABELS[row["type"]]):
                equal(row[field], label, f"CSV {field}")
            iso_timestamp(row["submittedAt"], "CSV submittedAt")
            equal(row["extensionVersion"], self.extension_version, "CSV extensionVersion")
            require(bool(row["browser"]), "CSV browser: expected user agent")
            equal(row["truncated"], "false", "CSV truncated")
            if expected["capture_ok"]:
                equal(row["captureMethod"], "full-page", "CSV captureMethod")
                path = row["screenshot"]
                require(path.startswith("screenshots/") and path.endswith(".png"),
                        f"CSV screenshot: expected screenshots/*.png, actual {brief(path)}")
                name = path[len("screenshots/"):]
                self.report_name(name[:-4], row["type"].capitalize(), row["title"])
                require(not re.search(r'[<>:"/\\|?*\x00-\x1f]', name),
                        f"screenshot name not sanitised: {brief(name)}")
                png = snapshot["screenshots"].get(name)
                require(png is not None, f"missing screenshot: {brief(path)}")
                equal(png["signature"], [137, 80, 78, 71, 13, 10, 26, 10], "PNG signature")
                equal(png["ihdr"], "IHDR", "first PNG chunk")
                require(png["height"] >= 4900, f"PNG IHDR height: expected >= 4900, actual {png['height']}")
                equal(png["decodedHeight"], png["height"], "PNG decoded height")
                equal(png["bottomRed"], True, "PNG bottom row has red")
            else:
                equal(row["screenshot"], "", "chrome://version screenshot column")
                equal(row["captureMethod"], "", "CSV failed captureMethod")
        self.csv_prefix = data
        return rows

    def record_save(self, result, kind, url, title, capture_ok, q1=A_Q1, user="'=Tester"):
        self.saved_reports.append({"id": result["id"], "type": kind, "user": user,
                                   "url": url, "title": title, "q1": "'" + q1,
                                   "q2": Q2, "capture_ok": capture_ok})
        return self.reports(len(self.saved_reports))

    def full_page_capture(self):
        self.source = self.context.new_page()
        self.source.goto(self.url)
        equal(self.source.title(), TITLE, "test page title")
        self.result = self.start(self.source)
        equal(self.result.get("captureOk"), True, "captureOk")
        equal(self.result.get("method"), "full-page", "capture method")

    def report_tab_opens(self):
        self.report = self.report_for(self.need(self.result, "full_page_capture"))
        image = self.report.get_by_role("img", name="Screenshot", exact=True)
        expect(image).to_be_visible()
        poll(lambda: image.evaluate("img => img.complete && img.naturalWidth > 0"),
             "screenshot thumbnail naturalWidth > 0")

    def labels_switch(self):
        page = self.need(self.report, "report_tab_opens")
        expect(page.get_by_role("radio", name="Bug", exact=True)).to_be_checked()
        for kind in ("bug", "feature", "bug"):
            page.get_by_role("radio", name=kind.capitalize(), exact=True).check()
            for label in LABELS[kind]:
                expect(page.get_by_label(label, exact=True)).to_be_visible()
                expect(page.get_by_text(label, exact=True)).to_be_visible()

    def first_save_picks_folder(self):
        page = self.need(self.report, "report_tab_opens")
        equal(self.options.evaluate("async () => (await BugDB.getDirHandle()) == null"),
              True, "first save has no stored folder")
        expect(page.get_by_text(
            "First save: you'll be asked once where to keep reports. Create and pick a folder called "
            "BUG_REPORTS, for example on your Desktop. Chrome won't allow Desktop, Documents "
            "or Downloads themselves.", exact=True)).to_be_visible()
        before = self.download_snapshot()
        page.evaluate("""() => {
            window.showDirectoryPicker = async () => navigator.storage.getDirectory();
        }""")
        self.fill(page, "bug")
        page.get_by_label(LABELS["bug"][0], exact=True).fill(A_Q1)
        message = self.submit(page)
        require(not message.startswith("Saved to Downloads/"), "first save unexpectedly used downloads")
        equal(self.options.evaluate("""async () => {
            const handle = await BugDB.getDirHandle();
            return !!handle && await handle.isSameEntry(await navigator.storage.getDirectory());
        }"""), True, "picker stores OPFS handle")
        self.record_save(self.result, "bug", self.url, TITLE, True)
        equal(self.download_snapshot(), before, "first save downloads unchanged")
        next_page = self.report_for(self.start(self.need(self.source, "full_page_capture")))
        expect(next_page.get_by_text(re.compile(r"^Saving to:"))).to_be_visible()
        expect(next_page.get_by_role("button", name="Change folder", exact=True)).to_be_visible()
        self.cancel_report(next_page)

    def files_bug(self):
        self.need(self.saved_reports or None, "first_save_picks_folder")
        self.reports(1)

    def validate_report(self, report, kind, url, title, capture_ok):
        equal(report.get("schema"), 1, "schema")
        equal(report.get("type"), kind, "type")
        equal(report.get("user"), "=Tester", "user")
        for number, label, text in zip(("q1", "q2"), LABELS[kind], (Q1, Q2)):
            equal(report.get("answers", {}).get(number, {}).get("label"), label, f"answers.{number}.label")
            equal(report.get("answers", {}).get(number, {}).get("text"), text, f"answers.{number}.text")
        equal(report.get("page", {}).get("url"), url, "page.url")
        equal(report.get("page", {}).get("title"), title, "page.title")
        equal(report.get("capture", {}).get("ok"), capture_ok, "capture.ok")
        equal(report.get("capture", {}).get("method"), "full-page" if capture_ok else None, "capture.method")
        iso_timestamp(report.get("capturedAt"), "capturedAt")
        iso_timestamp(report.get("submittedAt"), "submittedAt")
        equal(report.get("extensionVersion"), self.extension_version, "extensionVersion")
        require(isinstance(report.get("browser"), str) and bool(report["browser"]), "browser: expected user agent")

    def csv_bug(self):
        self.need(self.saved_reports or None, "first_save_picks_folder")
        self.reports(1)

    def no_screenshot_page(self):
        self.reports(1)
        source = self.context.new_page()
        source.goto("chrome://version")
        title = source.title()
        result = self.start(source)
        page = self.report_for(result)
        equal(result.get("captureOk"), False, "chrome://version captureOk")
        expect(page.get_by_text(re.compile(r"^No screenshot could be taken"))).to_be_visible()
        self.fill(page, "feature")
        page.get_by_label(LABELS["feature"][0], exact=True).fill(A_Q1)
        self.submit(page)
        rows = read_csv(bytes(self.opfs()["csv"]))
        equal(len(rows), 2, "CSV rows after chrome://version report")
        row = rows[-1]
        # Direct startReport calls lack activeTab access to chrome:// metadata.
        require(row["url"] in ("chrome://version/", "chrome://version", ""),
                f"CSV url: expected chrome://version or empty, actual {row['url']!r}")
        require(row["title"] in (title, "About Version", ""),
                f"CSV title: expected About Version or empty, actual {row['title']!r}")
        self.record_save(result, "feature", row["url"], row["title"], False)

    def reports_csv(self):
        self.reports(2)
        equal(self.download_files(), set(), "Mode A downloads")

    def csv_append(self):
        self.reports(2)
        result = self.start(self.need(self.source, "full_page_capture"))
        page = self.report_for(result)
        self.fill(page, "bug")
        page.get_by_label(LABELS["bug"][0], exact=True).fill("\t=1+1")
        page.get_by_label("Your name", exact=True).fill(" -2")
        self.submit(page)
        self.record_save(result, "bug", self.url, TITLE, True, q1="\t=1+1", user="'-2")

    def csv_guard_controls(self):
        rows = self.reports(3)
        equal(rows[0]["q1"], "'" + A_Q1, "CSV q1 formula guard and multiline round-trip")
        equal(rows[0]["user"], "'=Tester", "CSV user formula guard")
        equal(rows[-1]["q1"], "'\t=1+1", "CSV tab-prefixed formula guard")
        equal(rows[-1]["user"], "'-2", "CSV negative name formula guard")

    def download_files(self):
        return {p.relative_to(self.downloads) for p in self.downloads.rglob("*") if p.is_file()}

    def download_snapshot(self):
        return (self.download_files(),
                {d["id"] for d in self.worker.evaluate("() => chrome.downloads.search({})")})

    def forget_folder(self):
        options = self.need(self.options, "mode_a_setup")
        options.reload()
        expect(options.get_by_role("button", name="Forget folder", exact=True)).to_be_visible()
        options.get_by_role("button", name="Forget folder", exact=True).click()
        expect(options.get_by_text("No folder chosen", exact=True)).to_be_visible()
        equal(options.evaluate("async () => (await BugDB.getDirHandle()) == null"), True,
              "forgotten directory handle")

    def cancel_report(self, page):
        with page.expect_event("close"):
            page.get_by_role("button", name="Cancel", exact=True).click()

    def form_intact(self, page):
        expect(page.get_by_role("radio", name="Bug", exact=True)).to_be_checked()
        expect(page.get_by_label("Your name", exact=True)).to_have_value("=Tester")
        expect(page.get_by_label(LABELS["bug"][0], exact=True)).to_have_value(Q1)
        expect(page.get_by_label(LABELS["bug"][1], exact=True)).to_have_value(Q2)
        expect(page.get_by_role("button", name="Save report", exact=True)).to_be_enabled()

    def picker_cancel(self):
        self.forget_folder()
        before_opfs = self.opfs()
        before_downloads = self.download_snapshot()
        page = self.report_for(self.start(self.need(self.source, "full_page_capture")))
        try:
            page.evaluate("""() => {
                window.showDirectoryPicker = async () => { throw new DOMException("x", "AbortError"); };
            }""")
            self.fill(page, "bug")
            page.get_by_role("button", name="Save report", exact=True).click()
            expect(page.get_by_text(
                "No folder chosen, report not saved yet. Click Save report to choose one.",
                exact=True)).to_be_visible()
            self.form_intact(page)
            equal(self.opfs(), before_opfs, "cancelled picker OPFS unchanged")
            equal(self.options.evaluate("async () => (await BugDB.getDirHandle()) == null"),
                  True, "cancelled picker stores no handle")
            equal(self.download_snapshot(), before_downloads, "cancelled picker downloads unchanged")
        finally:
            self.cancel_report(page)

    def picker_security_error(self):
        # picker_cancel already clears the handle, hiding Options' Forget button.
        # Clear storage directly before opening the report so its cached handle is empty.
        self.need(self.options, "mode_a_setup").evaluate("() => BugDB.clearDirHandle()")
        before_opfs = self.opfs()
        before_downloads = self.download_snapshot()
        page = self.report_for(self.start(self.need(self.source, "full_page_capture")))
        try:
            equal(page.evaluate("async () => (await BugDB.getDirHandle()) == null"),
                  True, "security error picker starts without a handle")
            page.evaluate("""() => {
                window.securityPickerCalls = 0;
                window.showDirectoryPicker = async () => {
                    window.securityPickerCalls += 1;
                    throw new DOMException("x", "SecurityError");
                };
            }""")
            self.fill(page, "bug")
            page.get_by_role("button", name="Save report", exact=True).click()
            expect(page.get_by_text(
                "Chrome won't allow that folder. Create a folder such as Desktop\\BUG_REPORTS and pick that.",
                exact=True)).to_be_visible()
            equal(page.evaluate("() => window.securityPickerCalls"), 1,
                  "security error report picker calls")
            self.form_intact(page)
            equal(self.opfs(), before_opfs, "security error picker OPFS unchanged")
            equal(page.evaluate("async () => (await BugDB.getDirHandle()) == null"),
                  True, "security error picker stores no handle")
            equal(self.download_snapshot(), before_downloads, "security error picker downloads unchanged")
        finally:
            self.cancel_report(page)

    def permission_denied(self):
        self.options.evaluate("""async () => {
            await BugDB.setDirHandle(await navigator.storage.getDirectory());
        }""")
        before_opfs = self.opfs()
        before_downloads = self.download_snapshot()
        page = self.report_for(self.start(self.need(self.source, "full_page_capture")))
        page.evaluate("""() => {
            FileSystemDirectoryHandle.prototype.queryPermission = async () => "denied";
            FileSystemDirectoryHandle.prototype.requestPermission = async () => "denied";
        }""")
        self.fill(page, "bug")
        page.get_by_role("button", name="Save report", exact=True).click()
        expect(page.get_by_text("Folder access was refused.", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Try again", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Choose another folder", exact=True)).to_be_visible()
        self.form_intact(page)
        equal(self.opfs(), before_opfs, "denied permission OPFS unchanged")
        equal(self.download_snapshot(), before_downloads, "denied permission downloads unchanged")
        self.cancel_report(page)

    def no_picker_zip(self):
        self.forget_folder()
        before_opfs = self.opfs()
        self.mode_b_before = self.download_files()
        self.mode_b_history = {d["id"] for d in self.worker.evaluate("() => chrome.downloads.search({})")}
        page = self.report_for(self.start(self.need(self.source, "full_page_capture")))
        no_picker = """() => { delete window.showDirectoryPicker; }"""
        page.evaluate(no_picker)
        # Reinitialize the notice with the API absent; preserve the stub across
        # this page's reload only, without affecting other report pages.
        page.add_init_script(f"({no_picker})();")
        page.reload()
        page.evaluate(no_picker)
        equal(page.evaluate("() => typeof window.showDirectoryPicker"), "undefined",
              "directory picker absent")
        expect(page.get_by_text(
            "This browser blocks folder access. In Brave, enable brave://flags/#file-system-access-api "
            "and restart. Until then each report saves as one .zip in Downloads.",
            exact=True)).to_be_visible()
        self.fill(page, "bug")
        message = self.submit(page)
        equal(self.opfs(), before_opfs, "ZIP fallback OPFS unchanged")
        prefix = "Saved to Downloads/BugReports/"
        require(message.startswith(prefix) and message.endswith(".zip"), f"ZIP success text: {brief(message)}")
        filename = message[len(prefix):]
        self.report_name(filename[:-4], "Bug", TITLE)
        self.zip_path = self.downloads / "BugReports" / filename
        self.file(self.zip_path)
        with zipfile.ZipFile(self.zip_path) as archive:
            equal(archive.testzip(), None, "ZIP CRC validation")
            equal(sorted(archive.namelist()), ["report.json", "screenshot.png"], "ZIP contents")
            require(all(i.compress_type == zipfile.ZIP_STORED for i in archive.infolist()),
                    "ZIP: expected store-only entries")
            png_info(archive.read("screenshot.png"))
            report = json.loads(archive.read("report.json").decode("utf-8"))
            self.validate_report(report, "bug", self.url, TITLE, True)

    def downloads_audit(self):
        zip_path = self.need(self.zip_path, "no_picker_zip")
        before = self.need(self.mode_b_before, "no_picker_zip")
        after = self.download_files()
        equal(after - before, {zip_path.relative_to(self.downloads)}, "Mode B new files: single ZIP only")
        equal(before - after, set(), "Mode B removed downloads")
        require(not any(p.name == "index.csv" for p in after), "Mode B: unexpected index.csv download")
        history = self.worker.evaluate("() => chrome.downloads.search({})")
        new = [d for d in history if d["id"] not in self.mode_b_history]
        equal(len(new), 1, "Mode B download count")
        equal(Path(new[0]["filename"]).resolve(), zip_path.resolve(), "Mode B download path")
        equal(new[0].get("state"), "complete", "ZIP download state")
        for path in self.downloads.rglob("*"):
            require(path.resolve().is_relative_to(self.downloads.resolve()),
                    f"download containment: expected inside downloads, actual {brief(path)}")

    def idb_cleanup(self):
        script = """async () => {
            const result = [];
            for (const info of await indexedDB.databases()) {
                if (!info.name) continue;
                const db = await new Promise((resolve, reject) => {
                    const request = indexedDB.open(info.name);
                    request.onsuccess = () => resolve(request.result);
                    request.onerror = () => reject(request.error);
                });
                try {
                    for (const name of db.objectStoreNames) {
                        if (name !== 'reports') continue;
                        const count = await new Promise((resolve, reject) => {
                            const request = db.transaction(name, 'readonly').objectStore(name).count();
                            request.onsuccess = () => resolve(request.result);
                            request.onerror = () => reject(request.error);
                        });
                        result.push({database: info.name, store: name, count});
                    }
                } finally { db.close(); }
            }
            return result;
        }"""
        last = []

        def empty():
            nonlocal last
            last = self.worker.evaluate(script)
            # Settings contains persistent handles; only the reports cache must
            # be empty. Require an existing reports store, without guessing a DB.
            return bool(last) and all(store["count"] == 0 for store in last)

        try:
            poll(empty, "existing IndexedDB report stores with zero records")
        except AssertionError:
            raise AssertionError(f"IndexedDB: expected existing empty report stores, actual {brief(last)}") from None


def launch(playwright, profile, downloads, extension, chrome=None):
    failures = []
    for channel in (None, "chromium"):
        # Retry from a fresh profile as well, with no state from a failed load.
        shutil.rmtree(profile, ignore_errors=True)
        (profile / "Default").mkdir(parents=True)
        (profile / "Default" / "Preferences").write_text(json.dumps({
            "download": {"default_directory": str(downloads), "prompt_for_download": False},
        }), encoding="utf-8")
        context = None
        try:
            kwargs = {"headless": True, "accept_downloads": False,
                      "viewport": {"width": 1280, "height": 720},
                      "args": [f"--disable-extensions-except={extension}",
                               f"--load-extension={extension}"]}
            if channel:
                kwargs["channel"] = channel
            if chrome:
                kwargs["executable_path"] = chrome
            context = playwright.chromium.launch_persistent_context(str(profile), **kwargs)
            context.set_default_timeout(15000)
            worker = next((w for w in context.service_workers
                           if w.url.startswith("chrome-extension://")), None)
            if worker is None:
                worker = context.wait_for_event("serviceworker", timeout=15000,
                                                predicate=lambda w: w.url.startswith("chrome-extension://"))
            worker.evaluate("() => chrome.runtime.id")
            page = context.pages[0] if context.pages else context.new_page()
            cdp = context.new_cdp_session(page)
            cdp_status = "CDP allow"
            try:
                try:
                    cdp.send("Browser.setDownloadBehavior", {
                        "behavior": "allow", "downloadPath": str(downloads),
                    })
                except Exception as exc:
                    # Preferences already provide the specified native-download
                    # fallback if this Chromium build rejects the CDP command.
                    cdp_status = f"CDP unavailable ({brief(exc)})"
            finally:
                cdp.detach()
            print(f"Downloads routing: profile Preferences + accept_downloads=False; {cdp_status} (native filenames).",
                  flush=True)
            if channel:
                print("Chromium launch: channel=chromium fallback.", flush=True)
            return context, worker
        except Exception as exc:
            failures.append(f"{channel or 'bundled'}: {brief(exc)}")
            if context is not None:
                context.close()
    raise AssertionError("extension setup failed: " + "; ".join(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="keep temporary profile and downloads")
    parser.add_argument("--chrome", metavar="PATH", default=os.environ.get("E2E_CHROME"),
                        help="Chrome executable path (default: E2E_CHROME)")
    args = parser.parse_args()
    extension = (Path(__file__).resolve().parent / "../extension").resolve()
    profile = Path(tempfile.mkdtemp(prefix="bug-report-profile-"))
    downloads = Path(tempfile.mkdtemp(prefix="bug-report-downloads-"))
    server = None
    thread = None
    context = None
    suite = None
    setup_error = None
    cleanup_error = None
    try:
        require((extension / "manifest.json").is_file(),
                f"expected unpacked extension manifest at {extension / 'manifest.json'}, actual missing")
        extension_version = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))["version"]
        server = ThreadingHTTPServer(("127.0.0.1", 0), TestPage)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}/test"
        with sync_playwright() as playwright:
            try:
                context, worker = launch(playwright, profile, downloads, extension, args.chrome)
                suite = Suite(context, worker, downloads, url, extension_version)
                for name in CHECKS:
                    suite.check(name)
            finally:
                if context is not None:
                    context.close()
    except Exception as exc:
        setup_error = brief(exc)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        if args.keep:
            print(f"Kept profile: {profile}\nKept downloads: {downloads}", flush=True)
        else:
            for directory in (profile, downloads):
                try:
                    shutil.rmtree(directory)
                except OSError as exc:
                    cleanup_error = brief(exc)
    if suite is None:
        for name in CHECKS:
            print(f"FAIL {name}: setup: {setup_error or 'unknown error'}", flush=True)
        passed, failed = 0, len(CHECKS)
    else:
        passed, failed = suite.passed, suite.failed
        if setup_error:
            print(f"FAIL shutdown: {setup_error}", flush=True)
            failed += 1
    if cleanup_error:
        print(f"FAIL cleanup: {cleanup_error}", flush=True)
        failed += 1
    print(f"Final: {passed} passed, {failed} failed", flush=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
