#!/usr/bin/env python3
"""Turn a raw Wayback snapshot (from fetch_snapshot.py) into the served site.

Steps, applied to a copy of the snapshot written to the repo root (or --dest):
  1. Copy the snapshot tree to the destination.
  2. Strip any residual ``web.archive.org/web/...`` prefixes in HTML/CSS.
  3. Normalize the original site's absolute links (http/https://<domain>/...) to
     root-relative (/...), leaving genuinely external links intact.
  4. Inject the "archived copy" banner into every HTML page (markup after <body>
     + a stylesheet link in <head>) and add a nav link to the PARCSA page.

The banner text is notice-only (no outbound link), per project decision.

Usage:
    python3 scripts/postprocess.py --src _snapshot --dest . --domain www.transect.de

Uses BeautifulSoup if available (pip install beautifulsoup4), else falls back to
regex-based editing. The nav-injection selector may need tuning once the real
markup is known -- see NAV_HINTS below.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys

try:
    from bs4 import BeautifulSoup  # type: ignore
    HAVE_BS4 = True
except Exception:  # noqa: BLE001
    HAVE_BS4 = False

BANNER_CSS_HREF = "/archive-banner.css"
BANNER_HTML = (
    '<div id="archive-banner" role="note">'
    "This is an archived copy of the original TRANSECT project website "
    "(www.transect.de), preserved for reference. The project has ended and the "
    "site is no longer maintained."
    "</div>"
)
# Candidate ways to locate the main navigation, tried in order. Refine after
# inspecting the scraped markup.
NAV_HINTS = ["nav ul", "ul.nav", "#nav ul", ".menu ul", "header ul", "nav"]
PARCSA_LINK_HTML = '<li class="parcsa-nav"><a href="/parcsa/">PARCSA Methodology</a></li>'


def iter_files(root: str, exts: tuple[str, ...]) -> list[str]:
    out = []
    for base, _dirs, files in os.walk(root):
        for f in files:
            if f.lower().endswith(exts):
                out.append(os.path.join(base, f))
    return out


def normalize_text(text: str, domain: str) -> str:
    """Regex fixes safe to apply to both HTML and CSS as raw text."""
    # Drop Wayback prefixes if any slipped through (e.g. inside inline CSS/JS).
    text = re.sub(r"https?://web\.archive\.org/web/\d+(?:id_|im_|cs_|js_)?/", "", text)
    # Absolute links to the site's own host -> root-relative.
    text = re.sub(rf"https?://{re.escape(domain)}(?=[/\"')\s])", "", text)
    # Also handle the bare apex if present in captures.
    apex = domain[4:] if domain.startswith("www.") else domain
    text = re.sub(rf"https?://{re.escape(apex)}(?=[/\"')\s])", "", text)
    return text


def inject_html(path: str) -> None:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        html = fh.read()

    if HAVE_BS4:
        soup = BeautifulSoup(html, "html.parser")
        # Stylesheet link in <head>.
        if soup.head and not soup.find("link", href=BANNER_CSS_HREF):
            link = soup.new_tag("link", rel="stylesheet", href=BANNER_CSS_HREF)
            soup.head.append(link)
        # Banner right after <body>.
        if soup.body and not soup.find(id="archive-banner"):
            banner = BeautifulSoup(BANNER_HTML, "html.parser")
            soup.body.insert(0, banner)
        # Nav link to PARCSA (first hint that matches, once).
        if not soup.find("li", class_="parcsa-nav"):
            for hint in NAV_HINTS:
                target = soup.select_one(hint)
                if target is not None:
                    target.append(BeautifulSoup(PARCSA_LINK_HTML, "html.parser"))
                    break
        out = str(soup)
    else:
        out = html
        if BANNER_CSS_HREF not in out:
            out = re.sub(r"</head>",
                         f'<link rel="stylesheet" href="{BANNER_CSS_HREF}"></head>',
                         out, count=1, flags=re.IGNORECASE)
        if 'id="archive-banner"' not in out:
            out = re.sub(r"(<body[^>]*>)", r"\1" + BANNER_HTML,
                         out, count=1, flags=re.IGNORECASE)
        # Nav injection in fallback mode is left for manual review.

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="_snapshot")
    ap.add_argument("--dest", default=".")
    ap.add_argument("--domain", default="www.transect.de")
    args = ap.parse_args()

    if not os.path.isdir(args.src):
        print(f"source snapshot not found: {args.src}", file=sys.stderr)
        return 1

    # 1. Copy tree (skip the manifest bookkeeping file).
    for name in os.listdir(args.src):
        s = os.path.join(args.src, name)
        d = os.path.join(args.dest, name)
        if name == "_manifest.json":
            continue
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)

    # 2+3. Text normalization on HTML/CSS.
    text_files = iter_files(args.dest, (".html", ".htm", ".css"))
    for path in text_files:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        fixed = normalize_text(content, args.domain)
        if fixed != content:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(fixed)

    # 4. Banner + nav injection on HTML only.
    html_files = iter_files(args.dest, (".html", ".htm"))
    for path in html_files:
        inject_html(path)

    print(f"Post-processed {len(html_files)} HTML files, "
          f"{len(text_files)} text files total.")
    if not HAVE_BS4:
        print("NOTE: bs4 not installed -- nav link injection skipped; "
              "install beautifulsoup4 and re-run for automatic nav links.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
