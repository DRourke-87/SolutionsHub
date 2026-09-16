"""Render one of the HTML documents in docs/ to a print-ready PDF.

Usage:
    python -m scripts.render_pdf docs/SolutionsHub-Approval-Flow.html

Writes alongside the source with a .pdf extension unless a second path is given.
Needs Playwright's bundled Chromium, which is a local authoring tool rather than an
application dependency:

    pip install playwright && playwright install chromium

Set CHROMIUM_PATH to reuse a Chromium that is already on the machine.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

FOOTER = """
<div style="width:100%;font-family:Helvetica,Arial,sans-serif;font-size:7pt;color:#7C8285;
            padding:0 14mm;display:flex;justify-content:space-between;">
  <span>Amentum · Internal — SolutionsHub approval flow</span>
  <span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>
</div>
"""


def render(source: Path, target: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - authoring tool, not part of the app
        sys.exit("Playwright is not installed. Run: pip install playwright && playwright install chromium")

    launch_args = {}
    if chromium := os.environ.get("CHROMIUM_PATH"):
        # Use an already-installed Chromium instead of Playwright's own download.
        launch_args["executable_path"] = chromium

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch_args)
        page = browser.new_page()
        page.goto(source.resolve().as_uri())
        page.wait_for_load_state("networkidle")
        page.emulate_media(media="print")
        page.pdf(
            path=str(target),
            format="A4",
            print_background=True,
            margin={"top": "16mm", "bottom": "18mm", "left": "14mm", "right": "14mm"},
            display_header_footer=True,
            header_template="<div></div>",
            footer_template=FOOTER,
        )
        browser.close()


def main(argv: list[str]) -> None:
    if not argv:
        sys.exit(__doc__)
    source = Path(argv[0])
    if not source.is_file():
        sys.exit(f"No such file: {source}")
    target = Path(argv[1]) if len(argv) > 1 else source.with_suffix(".pdf")
    render(source, target)
    print(f"Wrote {target} ({target.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1:])
