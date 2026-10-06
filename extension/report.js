// Report page: shows the captured report, collects the answers and saves it silently into the chosen
// folder (Mode A, File System Access; the first Save asks where). Only a browser with no folder access
// at all gets one .zip via chrome.downloads (Mode B).
(function () {
  'use strict';

  var LABELS = {
    bug: ['What were you trying to do?', 'What went wrong?'],
    feature: ["What couldn't you do?", 'What would you like different?']
  };
  var ZIP_SUBFOLDER = 'BugReports';
  var CSV_HEADER = ['id', 'submittedAt', 'type', 'user', 'url', 'title', 'q1_label', 'q1', 'q2_label', 'q2',
    'screenshot', 'captureMethod', 'truncated', 'browser', 'extensionVersion'];
  var CSV_FILE = 'reports.csv';
  var SHOT_DIR = 'screenshots';
  var DOWNLOAD_TIMEOUT_MS = 60000;
  var MSG_FIRST_SAVE = "First save: you'll be asked once where to keep reports. Create and pick a folder called " +
    "BUG_REPORTS, for example on your Desktop. Chrome won't allow Desktop, Documents or Downloads themselves.";
  var MSG_FOLDER_REFUSED = "Chrome won't allow that folder. Create a folder such as Desktop\\BUG_REPORTS and pick that.";
  var MSG_ABORTED = 'No folder chosen, report not saved yet. Click Save report to choose one.';
  var MSG_BLOCKED = 'This browser blocks folder access. In Brave, enable brave://flags/#file-system-access-api ' +
    'and restart. Until then each report saves as one .zip in Downloads.';
  var PICKER_OPTIONS = { id: 'bug-reports', mode: 'readwrite' };

  var $ = function (id) { return document.getElementById(id); };
  var reportId = new URLSearchParams(location.search).get('id');
  var record = null;
  var settings = { userName: '' };
  var reservedShot = null; // Mode A PNG name picked on the first attempt; a retry overwrites it in place
  var firstSubmit = null; // time of the first submit attempt, reused on retry
  var rowWritten = null; // the root whose reports.csv already has this report's row
  var thumbUrl = null;
  var dirHandle = null; // the folder chosen in options, loaded when the page opens

  function pad(n, w) { n = String(n); while (n.length < (w || 2)) n = '0' + n; return n; }

  function isoLocal(d) {
    var off = -d.getTimezoneOffset();
    var sign = off >= 0 ? '+' : '-';
    var a = Math.abs(off);
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) +
      'T' + pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds()) +
      '.' + pad(d.getMilliseconds(), 3) + sign + pad(Math.floor(a / 60)) + ':' + pad(a % 60);
  }

  function cleanTitle(title) {
    var t = String(title || '')
      .replace(/[<>:"\/\\|?*\u0000-\u001f\u007f]/g, '')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 60)
      .replace(/[. ]+$/, '');
    return t || 'Untitled';
  }

  function baseFolderName(d, type, title) {
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + ' ' +
      pad(d.getHours()) + pad(d.getMinutes()) + ' ' + (type === 'feature' ? 'Feature' : 'Bug') +
      ' - ' + cleanTitle(title);
  }

  function csvField(v) {
    var s = v == null ? '' : String(v);
    // Formula guard: leading tab/CR/LF, or = + - @ as the first non-whitespace character.
    if (/^[\t\r\n]/.test(s) || /^\s*[=+\-@]/.test(s)) s = "'" + s;
    return '"' + s.replace(/"/g, '""') + '"';
  }

  function csvLine(values) { return values.map(csvField).join(',') + '\r\n'; }

  // Download a blob and resolve only once Chrome reports it complete.
  function downloadBlob(blob, filename, conflictAction) {
    var url = URL.createObjectURL(blob);
    return new Promise(function (resolve, reject) {
      var dlId = null;
      var early = [];
      var finished = false;
      var timer = null;
      var timedOut = false;

      function finish(err) {
        if (finished) return Promise.resolve();
        finished = true;
        clearTimeout(timer);
        chrome.downloads.onChanged.removeListener(onChanged);
        var stop = timedOut && dlId !== null ? chrome.downloads.cancel(dlId).catch(function () {}) : Promise.resolve();
        return stop.then(function () {
          URL.revokeObjectURL(url);
          if (err) reject(err); else resolve(dlId);
        });
      }
      function handle(delta) {
        if (delta.id !== dlId) return;
        if (delta.state && delta.state.current === 'complete') finish(null);
        else if (delta.state && delta.state.current === 'interrupted') {
          var reason = delta.error && delta.error.current ? delta.error.current : 'unknown reason';
          finish(new Error('Saving ' + filename + ' was interrupted: ' + reason));
        }
      }
      function onChanged(delta) {
        if (dlId === null) early.push(delta); else handle(delta);
      }

      chrome.downloads.onChanged.addListener(onChanged);
      timer = setTimeout(function () {
        timedOut = true;
        finish(new Error('Timed out saving ' + filename));
      }, DOWNLOAD_TIMEOUT_MS);

      chrome.downloads.download({ url: url, filename: filename, saveAs: false, conflictAction: conflictAction })
        .then(function (id) {
          if (id === undefined) throw new Error(chrome.runtime.lastError ? chrome.runtime.lastError.message : 'download did not start');
          if (finished) {
            // Started after we gave up waiting: stop it so a retry cannot race it.
            if (timedOut) chrome.downloads.cancel(id).catch(function () {});
            return;
          }
          dlId = id;
          early.splice(0).forEach(handle);
          if (finished) return;
          return chrome.downloads.search({ id: id }).then(function (items) {
            var it = items && items[0];
            if (!it) return;
            if (it.state === 'complete') finish(null);
            else if (it.state === 'interrupted') finish(new Error('Saving ' + filename + ' was interrupted: ' + (it.error || 'unknown reason')));
          });
        })
        .catch(function (e) { finish(new Error('Could not save ' + filename + ': ' + (e && e.message ? e.message : e))); });
    });
  }


  // ---- Mode B: store-only ZIP (no compression), readable by Windows Explorer ----
  var CRC_TABLE = (function () {
    var t = new Uint32Array(256);
    for (var n = 0; n < 256; n++) {
      var c = n;
      for (var k = 0; k < 8; k++) c = c & 1 ? 0xEDB88320 ^ (c >>> 1) : c >>> 1;
      t[n] = c >>> 0;
    }
    return t;
  })();

  function crc32(bytes) {
    var c = 0xFFFFFFFF;
    for (var i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) >>> 0;
  }

  // entries: [{name, bytes: Uint8Array}]. UTF-8 names (flag bit 11), DOS local time/date.
  function buildZip(entries, date) {
    var dosTime = (date.getHours() << 11) | (date.getMinutes() << 5) | (date.getSeconds() >> 1);
    var dosDate = ((Math.max(date.getFullYear(), 1980) - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate();
    var enc = new TextEncoder();
    var parts = [];
    var central = [];
    var offset = 0;
    entries.forEach(function (e) {
      var name = enc.encode(e.name);
      var crc = crc32(e.bytes);
      var size = e.bytes.length;
      var lh = new DataView(new ArrayBuffer(30));
      lh.setUint32(0, 0x04034b50, true); lh.setUint16(4, 20, true); lh.setUint16(6, 0x0800, true);
      lh.setUint16(8, 0, true); lh.setUint16(10, dosTime, true); lh.setUint16(12, dosDate, true);
      lh.setUint32(14, crc, true); lh.setUint32(18, size, true); lh.setUint32(22, size, true);
      lh.setUint16(26, name.length, true); lh.setUint16(28, 0, true);
      parts.push(lh.buffer, name, e.bytes);
      var ch = new DataView(new ArrayBuffer(46));
      ch.setUint32(0, 0x02014b50, true); ch.setUint16(4, 20, true); ch.setUint16(6, 20, true);
      ch.setUint16(8, 0x0800, true); ch.setUint16(10, 0, true); ch.setUint16(12, dosTime, true);
      ch.setUint16(14, dosDate, true); ch.setUint32(16, crc, true); ch.setUint32(20, size, true);
      ch.setUint32(24, size, true); ch.setUint16(28, name.length, true); ch.setUint16(30, 0, true);
      ch.setUint16(32, 0, true); ch.setUint16(34, 0, true); ch.setUint16(36, 0, true);
      ch.setUint32(38, 0, true); ch.setUint32(42, offset, true);
      central.push(ch.buffer, name);
      offset += 30 + name.length + size;
    });
    var cdSize = central.reduce(function (n, b) { return n + b.byteLength; }, 0);
    var end = new DataView(new ArrayBuffer(22));
    end.setUint32(0, 0x06054b50, true); end.setUint16(4, 0, true); end.setUint16(6, 0, true);
    end.setUint16(8, entries.length, true); end.setUint16(10, entries.length, true);
    end.setUint32(12, cdSize, true); end.setUint32(16, offset, true); end.setUint16(20, 0, true);
    return new Blob(parts.concat(central, [end.buffer]), { type: 'application/zip' });
  }

  // ---- Mode A: the chosen folder: reports.csv plus screenshots/ ----
  async function fileExists(dir, name) {
    try {
      await dir.getFileHandle(name);
      return true;
    } catch (e) {
      if (e && e.name === 'NotFoundError') return false;
      if (e && e.name === 'TypeMismatchError') return true; // a folder already has that name
      throw e;
    }
  }

  async function writeFile(dir, name, blob) {
    var fh = await dir.getFileHandle(name, { create: true });
    var w = await fh.createWritable();
    try {
      await w.write(blob);
      await w.close();
    } catch (e) {
      try { await w.abort(); } catch (e2) { /* already closed */ }
      throw e;
    }
  }

  // Appends one row to reports.csv, never rewriting what is there. A new file starts with a BOM
  // (so Excel reads UTF-8) and the header.
  async function appendRow(root, values) {
    var fh = await root.getFileHandle(CSV_FILE, { create: true });
    var size = (await fh.getFile()).size;
    var text = (size === 0 ? '\ufeff' + csvLine(CSV_HEADER) : '') + csvLine(values);
    var w = await fh.createWritable({ keepExistingData: true });
    try {
      await w.seek(size);
      await w.write(new Blob([text], { type: 'text/csv' }));
      await w.close();
    } catch (e) {
      try { await w.abort(); } catch (e2) { /* already closed */ }
      throw e;
    }
  }

  function hasPicker() { return typeof window.showDirectoryPicker === 'function'; }

  // The line above the Save button: where reports go, or why they will be zips.
  function renderFolderLine() {
    if (!hasPicker()) {
      $('folderLine').textContent = MSG_BLOCKED;
      $('changeFolderBtn').hidden = true;
    } else if (dirHandle) {
      $('folderLine').textContent = 'Saving to: ' + dirHandle.name;
      $('changeFolderBtn').hidden = false;
    } else {
      $('folderLine').textContent = MSG_FIRST_SAVE;
      $('changeFolderBtn').hidden = true;
    }
  }

  async function useFolder(h) {
    dirHandle = h;
    $('refused').hidden = true;
    try { await BugDB.setDirHandle(h); } catch (e) { console.warn('Could not remember the folder:', e); }
    renderFolderLine();
  }

  // Runs as the very first await of the Save click, so the picker or the permission prompt gets a
  // fresh user activation. Returns the granted folder, or null when nothing should be saved (the
  // reason is already shown). With no picker at all (Brave without the flag) returns 'zip'.
  async function folderForSave(forcePick) {
    if (!hasPicker()) return 'zip';
    if (forcePick || !dirHandle) {
      var h;
      try {
        h = await window.showDirectoryPicker(PICKER_OPTIONS);
      } catch (e) {
        if (e && e.name === 'AbortError') showError(MSG_ABORTED);
        else if (e && (e.name === 'SecurityError' || e.name === 'NotAllowedError')) showError(MSG_FOLDER_REFUSED);
        else showError('Could not use that folder: ' + (e && e.message ? e.message : e));
        return null;
      }
      await useFolder(h);
      return h;
    }
    var state;
    try {
      state = await dirHandle.requestPermission({ mode: 'readwrite' });
    } catch (e) {
      state = 'denied'; // e.g. the folder was deleted or moved
    }
    if (state === 'granted') { $('refused').hidden = true; return dirHandle; }
    $('refused').hidden = false;
    return null;
  }

  function currentType() { return $('kindFeature').checked ? 'feature' : 'bug'; }

  function applyLabels() {
    var l = LABELS[currentType()];
    $('q1Label').textContent = l[0];
    $('q2Label').textContent = l[1];
  }

  function showError(msg) {
    $('error').textContent = msg;
    $('error').hidden = !msg;
  }

  async function saveNameIfChanged(name) {
    if (name && name !== settings.userName) {
      settings.userName = name;
      await chrome.storage.local.set({ userName: name });
    }
  }

  async function closeThisTab() {
    try {
      var tab = await chrome.tabs.getCurrent();
      if (tab && typeof tab.id === 'number') { await chrome.tabs.remove(tab.id); return; }
    } catch (e) { /* fall through */ }
    window.close();
  }

  var pickOnNextSubmit = false; // set by "Choose another folder"

  async function onSubmit(ev) {
    ev.preventDefault();
    var forcePick = pickOnNextSubmit;
    pickOnNextSubmit = false;
    showError('');
    var type = currentType();
    var q1 = $('q1').value;
    var q2 = $('q2').value;
    var name = $('userName').value.trim();
    var labels = LABELS[type];
    var missing = [];
    if (!q1.trim()) missing.push('"' + labels[0] + '"');
    if (!q2.trim()) missing.push('"' + labels[1] + '"');
    if (!name) missing.push('"Your name"');
    if (missing.length) {
      showError('Please fill in ' + missing.join(', ') + '.');
      ($('q1').value.trim() ? ($('q2').value.trim() ? $('userName') : $('q2')) : $('q1')).focus();
      return;
    }
    if (!record) { showError('This report no longer exists.'); return; }

    $('saveBtn').disabled = true;
    $('cancelBtn').disabled = true;
    try {
      // Folder first, as the first await, while the Save click still counts as a user gesture.
      var target = await folderForSave(forcePick);
      if (!target) {
        $('saveBtn').disabled = false;
        $('cancelBtn').disabled = false;
        return; // nothing saved; the form and the reason stay on screen
      }
      var root = target === 'zip' ? null : target;
      await saveNameIfChanged(name);
      // One save at a time across report tabs, so two tabs cannot take the same screenshot name
      // or interleave their reports.csv appends.
      var saved = await navigator.locks.request('bug-report-save', async function () {
        if (!firstSubmit) firstSubmit = new Date();
        var now = firstSubmit;
        var base = baseFolderName(now, type, record.title);
        var cap = record.capture || {};
        var report = {
          schema: 1,
          type: type,
          answers: { q1: { label: labels[0], text: q1 }, q2: { label: labels[1], text: q2 } },
          user: name,
          page: { url: record.url, title: record.title },
          capturedAt: isoLocal(new Date(record.capturedAt)),
          submittedAt: isoLocal(now),
          capture: {
            ok: !!cap.ok,
            method: cap.method || null,
            truncated: !!cap.truncated,
            fallbackReason: cap.fallbackReason || null,
            error: cap.error || null
          },
          browser: navigator.userAgent,
          extensionVersion: chrome.runtime.getManifest().version
        };
        var json = new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' });

        if (root) {
          // Mode A: PNG first, then one appended row. A retry in the same root never appends twice.
          if (rowWritten && await root.isSameEntry(rowWritten)) {
            return { text: 'Saved to ' + root.name + '/' + CSV_FILE, hint: '' };
          }
          var shotPath = '';
          if (record.screenshot) {
            var shots = await root.getDirectoryHandle(SHOT_DIR, { create: true });
            // Reuse the first attempt's name only in the same root; elsewhere check names afresh.
            var sameRoot = reservedShot && reservedShot.base === base && await root.isSameEntry(reservedShot.root);
            if (!sameRoot) {
              var shotName = base;
              for (var n = 2; await fileExists(shots, shotName + '.png'); n++) shotName = base + ' (' + n + ')';
              reservedShot = { base: base, name: shotName + '.png', root: root };
            }
            await writeFile(shots, reservedShot.name, record.screenshot);
            shotPath = SHOT_DIR + '/' + reservedShot.name;
          }
          await appendRow(root, [reportId, report.submittedAt, report.type, report.user, report.page.url,
            report.page.title, labels[0], q1, labels[1], q2, shotPath, report.capture.method || '',
            report.capture.truncated ? 'true' : 'false', report.browser, report.extensionVersion]);
          rowWritten = root;
          return { text: 'Saved to ' + root.name + '/' + CSV_FILE,
            hint: (shotPath ? 'Screenshot: ' + shotPath + '. ' : '') +
              'Zip the ' + (root.name || 'chosen') + ' folder and send it when you are ready.' };
        }

        // Mode B: one .zip in Downloads/BugReports.
        var entries = [];
        if (record.screenshot) entries.push({ name: 'screenshot.png', bytes: new Uint8Array(await record.screenshot.arrayBuffer()) });
        entries.push({ name: 'report.json', bytes: new Uint8Array(await json.arrayBuffer()) });
        var dlId = await downloadBlob(buildZip(entries, now), ZIP_SUBFOLDER + '/' + base + '.zip', 'uniquify');
        var zipName = base + '.zip';
        try {
          var items = await chrome.downloads.search({ id: dlId });
          if (items && items[0] && items[0].filename) zipName = items[0].filename.split(/[\\/]/).pop();
        } catch (e) { /* keep the requested name */ }
        return { text: 'Saved to Downloads/' + ZIP_SUBFOLDER + '/' + zipName,
          hint: 'This browser blocks folder access, so the report was saved as a zip. In Brave, enable ' +
            'brave://flags/#file-system-access-api and restart to save into a folder without any prompts.' };
      });

      try { await BugDB.remove(reportId); } catch (e) { console.warn('Could not delete report record:', e); }
      record = null;

      $('saved').textContent = saved.text;
      $('savedHint').textContent = saved.hint;
      $('reportForm').hidden = true;
      $('done').hidden = false;
      $('closeBtn').focus();
    } catch (e) {
      showError('Could not save the report: ' + (e && e.message ? e.message : e));
      $('saveBtn').disabled = false;
      $('cancelBtn').disabled = false;
    }
  }

  async function onCancel() {
    $('cancelBtn').disabled = true;
    try { if (reportId) await BugDB.remove(reportId); } catch (e) { console.warn('Could not delete report record:', e); }
    await closeThisTab();
  }

  function openFullSize() {
    if (thumbUrl) chrome.tabs.create({ url: thumbUrl });
  }

  async function init() {
    $('kindBug').addEventListener('change', applyLabels);
    $('kindFeature').addEventListener('change', applyLabels);
    $('reportForm').addEventListener('submit', onSubmit);
    $('cancelBtn').addEventListener('click', onCancel);
    $('tryAgainBtn').addEventListener('click', function () { $('reportForm').requestSubmit(); });
    $('chooseAnotherBtn').addEventListener('click', function () {
      pickOnNextSubmit = true;
      $('reportForm').requestSubmit();
    });
    $('changeFolderBtn').addEventListener('click', async function () {
      try {
        await useFolder(await window.showDirectoryPicker(PICKER_OPTIONS));
      } catch (e) {
        if (e && (e.name === 'SecurityError' || e.name === 'NotAllowedError')) showError(MSG_FOLDER_REFUSED);
        else if (!e || e.name !== 'AbortError') showError('Could not use that folder: ' + (e && e.message ? e.message : e));
      }
    });
    $('closeBtn').addEventListener('click', closeThisTab);
    $('thumb').addEventListener('click', openFullSize);
    $('thumb').addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openFullSize(); }
    });
    $('userName').addEventListener('change', function () {
      saveNameIfChanged($('userName').value.trim()).catch(function () {});
    });
    applyLabels();

    try {
      settings = await chrome.storage.local.get({ userName: '' });
    } catch (e) { /* keep defaults */ }
    try { dirHandle = await BugDB.getDirHandle(); } catch (e) { dirHandle = null; }
    renderFolderLine();
    $('userName').value = settings.userName || '';
    $('nameHint').hidden = !!settings.userName;

    try {
      record = reportId ? await BugDB.get(reportId) : null;
    } catch (e) {
      record = null;
    }
    if (!record) {
      $('pageTitle').textContent = 'Report not found';
      showError('This report was already saved or cancelled. Click the toolbar button to start a new one.');
      $('saveBtn').disabled = true;
      return;
    }

    $('pageTitle').textContent = record.title || '(no title)';
    $('pageUrl').textContent = record.url || '';
    document.title = 'Bug Report: ' + (record.title || record.url || '');

    var cap = record.capture || {};
    if (record.screenshot) {
      thumbUrl = URL.createObjectURL(record.screenshot);
      $('thumb').src = thumbUrl;
      $('thumb').hidden = false;
      var notes = [];
      if (cap.method === 'visible-only') notes.push('Only the visible part of the page was captured (' + (cap.fallbackReason || 'full page capture failed') + ').');
      if (cap.truncated) notes.push('The page was very long, so the screenshot stops at 16000 pixels.');
      notes.push('Click the screenshot to open it full size.');
      $('shotNote').textContent = notes.join(' ');
      $('shotNote').hidden = false;
    } else {
      $('noShot').textContent = 'No screenshot could be taken: ' + (cap.error || 'unknown reason');
      $('noShot').hidden = false;
    }
  }

  init();
})();
