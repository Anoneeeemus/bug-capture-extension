// Classic service worker: captures the tab first, stores it, then opens the report page.
importScripts('db.js');

var MAX_CAPTURE_HEIGHT = 16000;

function base64ToBlob(b64, type) {
  var bin = atob(b64);
  var bytes = new Uint8Array(bin.length);
  for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Blob([bytes], { type: type });
}

function errText(e) {
  if (!e) return 'unknown error';
  return (e && e.message) ? e.message : String(e);
}

// Primary: full page via the DevTools protocol. Always detaches.
async function captureFullPage(tabId) {
  var target = { tabId: tabId };
  await chrome.debugger.attach(target, '1.3');
  try {
    var metrics = await chrome.debugger.sendCommand(target, 'Page.getLayoutMetrics');
    var size = metrics && metrics.cssContentSize;
    if (!size || !(size.width > 0) || !(size.height > 0)) {
      throw new Error('Page.getLayoutMetrics returned no cssContentSize');
    }
    var width = Math.ceil(size.width);
    var fullHeight = Math.ceil(size.height);
    var height = Math.min(fullHeight, MAX_CAPTURE_HEIGHT);
    var params = {
      format: 'png',
      captureBeyondViewport: true,
      clip: { x: 0, y: 0, width: width, height: height, scale: 1 }
    };
    // Around the moment a tab is shown, a capture can come back with nothing painted below the
    // viewport, or fail with "Unable to capture screenshot". Which capture is hit varies, so keep
    // capturing, 150 ms apart, until two in a row are identical. The first successful capture never
    // counts towards that pair, so the earliest accepted match is captures 2 and 3. At most 6 tries;
    // an animated page never matches, and then the last capture is used. No viewport resize.
    var shot = null;
    var prev = null;
    var lastErr = null;
    var successes = 0;
    for (var attempt = 0; attempt < 6; attempt++) {
      if (successes > 0) await new Promise(function (r) { setTimeout(r, 150); });
      try {
        shot = await chrome.debugger.sendCommand(target, 'Page.captureScreenshot', params);
      } catch (e) {
        lastErr = e;
        await new Promise(function (r) { setTimeout(r, 300); });
        continue;
      }
      successes++;
      if (successes >= 3 && prev === shot.data) break;
      prev = successes >= 2 ? shot.data : null;
    }
    if (!shot || !shot.data) throw lastErr || new Error('Page.captureScreenshot returned no data');
    // Without the tabs permission the tab object only carries url/title when activeTab was
    // granted by a click. The attached target knows both either way.
    var info = null;
    try { info = (await chrome.debugger.sendCommand(target, 'Target.getTargetInfo')).targetInfo; } catch (e) { /* optional */ }
    return { blob: base64ToBlob(shot.data, 'image/png'), truncated: fullHeight > MAX_CAPTURE_HEIGHT,
      url: info ? info.url : '', title: info ? info.title : '' };
  } finally {
    try { await chrome.debugger.detach(target); } catch (e) { /* already detached */ }
  }
}

// Fallback: what is visible. Only valid if the source tab is the one showing in its window.
async function captureVisible(tab) {
  var current = await chrome.tabs.get(tab.id);
  if (!current.active) throw new Error('the tab is not the visible tab in its window');
  var dataUrl = await chrome.tabs.captureVisibleTab(current.windowId, { format: 'png' });
  var comma = dataUrl.indexOf(',');
  if (comma < 0) throw new Error('captureVisibleTab returned no image');
  return base64ToBlob(dataUrl.slice(comma + 1), 'image/png');
}

async function createReport(tab) {
  if (!tab || typeof tab.id !== 'number') throw new Error('startReport needs a tab');
  var capturedAt = Date.now();
  var capture = { ok: false, method: null, truncated: false, fallbackReason: null, error: null };
  var screenshot = null;
  var page = { url: tab.url || tab.pendingUrl || '', title: tab.title || '' };

  try {
    var full = await captureFullPage(tab.id);
    screenshot = full.blob;
    capture.ok = true;
    capture.method = 'full-page';
    capture.truncated = full.truncated;
    if (!page.url) page.url = full.url || '';
    if (!page.title) page.title = full.title || '';
  } catch (e) {
    capture.fallbackReason = errText(e);
    try {
      screenshot = await captureVisible(tab);
      capture.ok = true;
      capture.method = 'visible-only';
    } catch (e2) {
      screenshot = null;
      capture.ok = false;
      capture.method = null;
      capture.error = errText(e2);
      if (capture.fallbackReason && capture.fallbackReason !== capture.error) {
        capture.error = capture.fallbackReason + '; ' + capture.error;
      }
      capture.fallbackReason = null;
    }
  }

  var id = crypto.randomUUID();
  await BugDB.put({
    id: id,
    url: page.url,
    title: page.title,
    capturedAt: capturedAt,
    screenshot: screenshot,
    capture: capture
  });

  var createProps = { url: chrome.runtime.getURL('report.html?id=' + encodeURIComponent(id)), active: true };
  if (typeof tab.windowId === 'number') createProps.windowId = tab.windowId;
  if (typeof tab.index === 'number') createProps.index = tab.index + 1;
  createProps.openerTabId = tab.id;
  try {
    await chrome.tabs.create(createProps);
  } catch (e) {
    // Source window may have gone; open it anywhere rather than lose the report.
    await chrome.tabs.create({ url: createProps.url, active: true });
  }

  return { id: id, captureOk: capture.ok, method: capture.method };
}

var DEFAULT_ACTION_TITLE = 'Report a bug or feature';
var STALE_RECORD_MS = 7 * 24 * 60 * 60 * 1000;  // long enough that an open, unsaved report survives overnight

// Wraps createReport: drops abandoned records, and makes a failure to open the report visible
// on the toolbar button, since the user otherwise sees nothing.
async function startReport(tab) {
  try {
    await BugDB.removeOlderThan(Date.now() - STALE_RECORD_MS);
  } catch (e) {
    console.warn('Bug Report: could not clean up old reports:', e);
  }
  try {
    var result = await createReport(tab);
  } catch (e) {
    try {
      await chrome.action.setBadgeText({ text: '!' });
      await chrome.action.setTitle({ title: 'Bug Report could not start: ' + errText(e) });
    } catch (e2) { /* nothing more we can show */ }
    throw e;
  }
  try {
    await chrome.action.setBadgeText({ text: '' });
    await chrome.action.setTitle({ title: DEFAULT_ACTION_TITLE });
  } catch (e) { /* cosmetic only */ }
  return result;
}

self.startReport = startReport;

chrome.action.onClicked.addListener(function (tab) {
  startReport(tab).catch(function (e) { console.error('Bug Report: could not start report:', e); });
});

// v1.1 keeps nothing in these v1 keys (index.csv is rebuilt from disk, the zip folder is fixed).
chrome.runtime.onInstalled.addListener(function () {
  chrome.storage.local.remove(['subfolder', 'usedFolders', 'indexRows']).catch(function () {});
});
