"""Render the CodeUp guides from their editable HTML sources.

Usage:
    py scripts/build_guide_pdf.py            # build both guides
    py scripts/build_guide_pdf.py quick      # build only the Quick How-To Guide
    py scripts/build_guide_pdf.py full       # build only the full September guide

Sources and outputs (docs/guide/):
    quick-how-to-guide.html -> CodeUp_How_To_Use_Guide.pdf
    full-how-to-guide.html  -> CodeUp_How_To_Guide_September_2026.pdf

Uses the Playwright Chromium that the browser test suite already installs
(`playwright install chromium`), so no office suite is needed.
"""

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
GUIDE_DIR = ROOT / "docs" / "guide"

_FONT = "font-size: 8pt; color: #4a4036; width: 100%; padding: 0 0.75in;"

GUIDES = {
    "quick": {
        "source": GUIDE_DIR / "quick-how-to-guide.html",
        "out": GUIDE_DIR / "CodeUp_How_To_Use_Guide.pdf",
        "header": "CODEUP &nbsp;|&nbsp; QUICK HOW-TO GUIDE",
        "footer": "",
        "font": "'Segoe UI', Arial, sans-serif",
        "align": "right",
    },
    "full": {
        "source": GUIDE_DIR / "full-how-to-guide.html",
        "out": GUIDE_DIR / "CodeUp_How_To_Guide_September_2026.pdf",
        "header": "<strong>CODEUP | HOW-TO GUIDE</strong>",
        "footer": "CodeUp | How-To Guide &nbsp;&nbsp;",
        "font": "'Noto Serif', Georgia, serif",
        "align": "left",
    },
}


def build(name: str) -> Path:
    guide = GUIDES[name]
    style = f"font-family: {guide['font']}; {_FONT} text-align: {guide['align']};"
    header = f'<div style="{style}">{guide["header"]}</div>'
    footer = f'<div style="{style}">{guide["footer"]}Page <span class="pageNumber"></span></div>'
    out_path = guide["out"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto(guide["source"].as_uri())
            page.pdf(
                path=str(out_path),
                format="Letter",
                print_background=True,
                prefer_css_page_size=True,
                display_header_footer=True,
                header_template=header,
                footer_template=footer,
            )
        finally:
            browser.close()
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("guides", nargs="*", metavar="GUIDE",
                        help=f"which guides to build: {', '.join(GUIDES)} (default: all)")
    args = parser.parse_args()
    unknown = [name for name in args.guides if name not in GUIDES]
    if unknown:
        parser.error(f"unknown guide(s): {', '.join(unknown)}; choose from {', '.join(GUIDES)}")
    for name in args.guides or list(GUIDES):
        print(f"Wrote {build(name)}")


if __name__ == "__main__":
    main()
