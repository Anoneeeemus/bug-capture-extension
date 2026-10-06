"""Build the Bug Report guide.

Embeds every screenshot in ../shots into guide.template.html as a base64 data URI,
writes the self-contained HTML next to this folder, then prints it to an A4 PDF
with headless Chromium through Playwright.

Usage:
    python3 build.py --chrome /path/to/chrome
    CHROME_PATH=/path/to/chrome python3 build.py
"""
import argparse, base64, os, pathlib, re, sys

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--chrome", default=os.environ.get("CHROME_PATH"),
                help="Chromium or Chrome executable used to print the PDF (default: $CHROME_PATH)")
args = ap.parse_args()
if not args.chrome:
    sys.exit("build.py: no browser given. Pass --chrome /path/to/chrome or set CHROME_PATH.")
if not pathlib.Path(args.chrome).is_file():
    sys.exit(f"build.py: browser not found at {args.chrome}")

here = pathlib.Path(__file__).resolve().parent
shots = here.parent / "shots"
s = (here / "guide.template.html").read_text(encoding="utf-8")

def sub(m):
    path = shots / m.group(1)
    if not path.is_file():
        sys.exit(f"build.py: missing screenshot {path.name}")
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()

out = re.sub(r"\{\{([^}]+\.png)\}\}", sub, s)
assert "{{" not in out
dest = here.parent / "Bug Report - Install and User Guide.html"
dest.write_text(out, encoding="utf-8")
print(dest.name, dest.stat().st_size)

# PDF: A4, page size and margins come from the CSS @page rule
from playwright.sync_api import sync_playwright
pdf = dest.with_suffix(".pdf")
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=args.chrome)
    pg = b.new_page()
    pg.goto(dest.as_uri())
    pg.wait_for_load_state("load")
    pg.pdf(path=str(pdf), format="A4", print_background=True, prefer_css_page_size=True,
           display_header_footer=True, header_template="<span></span>",
           footer_template='<div style="font-size:8px;color:#777;width:100%;text-align:center"><span class="pageNumber"></span> / <span class="totalPages"></span></div>')
    b.close()
print(pdf.name, pdf.stat().st_size)
