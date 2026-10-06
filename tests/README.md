# Tests

`run_e2e.py` is the end to end suite. It loads `../extension` unpacked into Chromium through
Playwright (headless, fresh temporary profile and downloads directory), serves a local test page,
and checks the behaviour in [docs/SPEC.md](../docs/SPEC.md): full page capture, the report form,
folder-first saving into an OPFS directory handle (which stands in for a real folder, so no picker is
needed), the `reports.csv` append and formula guard, the picker cancel and permission refused paths,
the single zip fallback when `showDirectoryPicker` is missing, and IndexedDB clean up.

```
python3 tests/run_e2e.py [--keep] [--chrome /path/to/chrome]
```

- Requires Python 3 and `pip install playwright` plus `python3 -m playwright install chromium`.
- `--chrome` (or the `E2E_CHROME` environment variable) picks the browser binary, useful when the
  bundled Chromium does not match the installed Playwright.
- `--keep` keeps the temporary profile and downloads directory and prints their paths.
- Output is one `PASS name` or `FAIL name: detail` line per check and a final count. The exit code
  is 0 only when every check passes.
