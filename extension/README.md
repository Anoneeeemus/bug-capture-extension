# Bug Report extension

Report a bug or ask for a feature on any website. One click takes a full page screenshot. You then
answer two questions, and the report is saved on your own PC, ready to zip and send.

Use it in Google Chrome, the tested and supported browser. Edge is untested but should work. Brave saves
each report as a zip instead, unless its folder access flag is turned on (see Brave below). Nothing is
sent anywhere, and the extension makes no network calls.

## Install

1. Unzip this folder somewhere it can stay, for example `Documents\Bug Report`.
2. In Chrome, open `chrome://extensions`.
3. Turn on **Developer mode** (top right).
4. Click **Load unpacked** and pick the unzipped folder.
5. Pin it: click the puzzle piece icon in the toolbar, then the pin next to **Bug Report**.
6. The options page opens from the extension's **Details → Extension options**. Type your name there once.

Edge (untested): the same steps at `edge://extensions`, where Developer mode is in the left sidebar.

## Use

1. On the page with the problem, click the **Bug Report** button, or press **Alt+Shift+B**.
2. A new tab opens with the screenshot. Choose **Bug** or **Feature**, answer both questions and type your name.
3. Click **Save report**.

The first time, Save asks where to keep reports. Create a folder called `BUG_REPORTS`, for example
on your Desktop, pick it, and allow the browser to edit files in it. Chrome won't allow Desktop, Documents
or Downloads themselves, so it must be a folder inside one of them. From then on every report saves straight into that folder
with no prompts. The report tab shows **Saving to: <folder>**, and **Change folder** picks a different one.
After a browser restart it may ask once more to allow editing the folder. If you refuse, nothing is saved:
click **Try again** or **Choose another folder**. If you close the folder picker, nothing is saved either,
and clicking **Save report** again reopens it.

The folder holds one list of all reports and their screenshots:

```
BUG_REPORTS\
  reports.csv                                    one row per report, opens in Excel
  screenshots\
    2026-10-06 1432 Bug - Checkout page.png
    2026-10-06 1501 Feature - Search results.png
```

Each report adds one row to `reports.csv`, with the answers, the page, your name and the path of its
screenshot. Existing rows are never rewritten. Pages that can't be captured get a row with an empty
screenshot column. To send reports, right click the folder, choose **Send to → Compressed (zipped) folder**,
and email the zip.
The options page can also choose or forget the folder.

### Brave

Brave turns folder access off by default. The report tab then says the browser blocks folder access,
and each report saves as one zip in `Downloads\BugReports\` instead, for example
`2026-10-06 1432 Bug - Checkout page.zip`. Send those zips as they are. To use a folder, open
`brave://flags/#file-system-access-api`, set it to **Enabled** and restart Brave. The next Save asks
for a folder.

## What the warnings mean

On install the browser warns that the extension can "read and change all your data on all websites"
and "manage your downloads". The first comes from the debugger permission, which is the only way
to take a full page screenshot in one go. It is used only when you click Report, only on that tab,
and only to take the picture. While the screenshot is taken, a bar saying "'Bug Report' started
debugging this browser" appears for a few seconds, also on the report tab, and closes by itself.
Clicking its **Cancel** only stops that one full page screenshot: the report then keeps just the
visible part of the page.
The downloads permission is only used for the zip in a browser that blocks folder access. Folder
access is granted separately, by you, when the first Save asks for a folder.

## Limits

- Pages taller than 16,000 px are cut off at that height, and the report's `truncated` column says `true`.
- Browser pages such as `chrome://` and the extension stores can't be captured. The report saves
  without a screenshot.
- If a work PC's policy blocks the debugger, only the visible part of the page is captured
  (`visible-only` in the `captureMethod` column).
- In a browser that blocks folder access, a browser set to "Ask where to save each file" asks once
  per report, for the zip. With folder access there are no prompts after the first Save.
