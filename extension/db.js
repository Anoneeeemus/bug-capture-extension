// Tiny IndexedDB helper shared by the service worker (importScripts), report.html and options.html.
// Store "reports": one record per open report {id, url, title, capturedAt, screenshot: Blob|null, capture}.
// Store "settings": the chosen save folder, key "dirHandle" (a FileSystemDirectoryHandle).
var BugDB = (function () {
  'use strict';
  var DB_NAME = 'bug-report';
  var DB_VERSION = 2;
  var STORE = 'reports';
  var SETTINGS = 'settings';
  var dbPromise = null;

  function open() {
    if (!dbPromise) {
      dbPromise = new Promise(function (resolve, reject) {
        var req = indexedDB.open(DB_NAME, DB_VERSION);
        req.onupgradeneeded = function () {
          // Runs for new installs (from 0) and upgrades from v1 (reports only).
          var db = req.result;
          if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: 'id' });
          if (!db.objectStoreNames.contains(SETTINGS)) db.createObjectStore(SETTINGS);
        };
        req.onsuccess = function () {
          var db = req.result;
          db.onversionchange = function () { db.close(); dbPromise = null; };
          resolve(db);
        };
        req.onerror = function () { dbPromise = null; reject(req.error); };
        req.onblocked = function () { dbPromise = null; reject(new Error('IndexedDB upgrade blocked by another open tab')); };
      });
    }
    return dbPromise;
  }

  function run(store, mode, fn) {
    return open().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(store, mode);
        var req = fn(tx.objectStore(store));
        tx.oncomplete = function () { resolve(req ? req.result : undefined); };
        tx.onerror = function () { reject(tx.error); };
        tx.onabort = function () { reject(tx.error || new Error('IndexedDB transaction aborted')); };
      });
    });
  }

  return {
    DB_NAME: DB_NAME,
    STORE: STORE,
    put: function (record) { return run(STORE, 'readwrite', function (s) { return s.put(record); }); },
    get: function (id) { return run(STORE, 'readonly', function (s) { return s.get(id); }); },
    remove: function (id) { return run(STORE, 'readwrite', function (s) { return s.delete(id); }); },
    // Deletes records captured before `cutoff` (ms since epoch), e.g. reports whose tab was closed.
    removeOlderThan: function (cutoff) {
      return run(STORE, 'readwrite', function (s) {
        var req = s.openCursor();
        req.onsuccess = function () {
          var cur = req.result;
          if (!cur) return;
          var v = cur.value;
          if (!v || typeof v.capturedAt !== 'number' || v.capturedAt < cutoff) cur.delete();
          cur.continue();
        };
        return null;
      });
    },
    count: function () { return run(STORE, 'readonly', function (s) { return s.count(); }); },
    setDirHandle: function (h) { return run(SETTINGS, 'readwrite', function (s) { return s.put(h, 'dirHandle'); }); },
    getDirHandle: function () {
      return run(SETTINGS, 'readonly', function (s) { return s.get('dirHandle'); })
        .then(function (h) { return h || null; });
    },
    clearDirHandle: function () { return run(SETTINGS, 'readwrite', function (s) { return s.delete('dirHandle'); }); }
  };
})();
self.BugDB = BugDB;
