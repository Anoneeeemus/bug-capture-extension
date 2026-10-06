// Options page: the save folder (a File System Access handle kept in IndexedDB) and the reporter's name.
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  var BLOCKED = 'This browser blocks folder access. In Brave, enable brave://flags/#file-system-access-api ' +
    'and restart. Until then each report saves as one .zip in Downloads.';

  function setStatus(msg, isError) {
    $('status').textContent = msg;
    $('status').className = isError ? 'error' : 'success';
  }

  async function showFolder() {
    if (typeof window.showDirectoryPicker !== 'function') {
      $('chooseBtn').hidden = true;
      $('forgetBtn').hidden = true;
      $('folderStatus').textContent = BLOCKED;
      return;
    }
    var h = null;
    try { h = await BugDB.getDirHandle(); } catch (e) { h = null; }
    $('folderStatus').textContent = h ? 'Folder: ' + h.name : 'No folder chosen';
    $('forgetBtn').hidden = !h;
  }

  async function chooseFolder() {
    var h;
    try {
      h = await window.showDirectoryPicker({ id: 'bug-reports', mode: 'readwrite' });
    } catch (e) {
      if (e && e.name === 'AbortError') return; // picker closed
      $('folderStatus').textContent = (e && (e.name === 'SecurityError' || e.name === 'NotAllowedError'))
        ? "Chrome won't allow that folder. Create a folder such as Desktop\\BUG_REPORTS and pick that."
        : 'Could not use that folder: ' + (e && e.message ? e.message : e);
      return;
    }
    await BugDB.setDirHandle(h);
    await showFolder();
  }

  async function forgetFolder() {
    await BugDB.clearDirHandle();
    await showFolder();
  }

  async function init() {
    var s = await chrome.storage.local.get({ userName: '' });
    $('userName').value = s.userName || '';

    $('chooseBtn').addEventListener('click', chooseFolder);
    $('forgetBtn').addEventListener('click', forgetFolder);
    $('optionsForm').addEventListener('submit', async function (ev) {
      ev.preventDefault();
      try {
        await chrome.storage.local.set({ userName: $('userName').value.trim() });
        setStatus('Saved', false);
      } catch (e) {
        setStatus('Could not save: ' + (e && e.message ? e.message : e), true);
      }
    });
    await showFolder();
  }

  init();
})();
