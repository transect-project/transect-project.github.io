#!/usr/bin/env python3
"""Turn the raw Wayback snapshot into the served static site.

Consumes the output of fetch_snapshot.py (pages) and fetch_assets.py (CDN assets
+ _assetmap.json), and writes the finished site to --dest (default: repo root):

  1. Copy pages into place; convert extensionless pages (e.g. ``news``,
     ``team/dr-henryk-alff``) into ``<name>/index.html`` so GitHub Pages serves
     them as HTML. Copy harvested assets to ``/assets/``.
  2. Rewrite all CDN/font URLs (incl. signed & &amp;-encoded query variants) to
     local ``/assets/...`` paths, strip residual Wayback prefixes, and normalize
     the site's own absolute links to root-relative.
  3. Copy lazy-loaded ``data-src`` onto ``src`` so images render without the
     builder's JavaScript.
  4. Inject the fixed "archived copy" banner and a static fallback navigation
     (so the site is navigable even if the builder's JS menu doesn't initialize),
     including a link to the new PARCSA page.

Uses BeautifulSoup (pip install beautifulsoup4) for DOM edits, with a regex
fallback for the banner/nav.

Usage:
    python3 scripts/postprocess.py --src _snapshot --dest . --domain www.transect.de
"""
from __future__ import annotations

import argparse
import json
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
# Static fallback nav: always-working links to the site's main destinations.
FALLBACK_NAV_ITEMS = [
    ("/", "Home"),
    ("/#Team", "Team"),
    ("/#Publications", "Publications"),
    ("/news/", "News"),
    ("/#PartnersA", "Partners"),
    ("/#Contact", "Contact"),
    ("/parcsa/", "PARCSA Methodology"),
]


def build_fallback_nav() -> str:
    links = "".join(f'<a href="{href}">{label}</a>' for href, label in FALLBACK_NAV_ITEMS)
    return f'<nav id="archive-nav" aria-label="Archived site navigation">{links}</nav>'


def iter_files(root: str, exts: tuple[str, ...] | None = None) -> list[str]:
    out = []
    for base, _d, files in os.walk(root):
        for f in files:
            if exts is None or f.lower().endswith(exts):
                out.append(os.path.join(base, f))
    return out


def is_extensionless_page(path: str) -> bool:
    return "." not in os.path.basename(path)


def copy_tree(src: str, dest: str) -> None:
    """Copy snapshot -> dest, relocating extensionless pages to <name>/index.html
    and harvested _ext/* to /assets/. Skips bookkeeping + redundant query dupes."""
    ext_root = os.path.join(src, "_ext")
    for base, _dirs, files in os.walk(src):
        # Assets handled separately.
        if base == ext_root or base.startswith(ext_root + os.sep):
            continue
        for f in files:
            if f in ("_manifest.json", "_assetmap.json"):
                continue
            sp = os.path.join(base, f)
            rel = os.path.relpath(sp, src)
            # Drop redundant "?query" capture variants when the base file exists.
            if "__" in f:
                bare = os.path.join(base, f.split("__", 1)[0])
                if os.path.exists(bare):
                    continue
            if is_extensionless_page(sp):
                dp = os.path.join(dest, os.path.dirname(rel), f, "index.html")
            else:
                dp = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            shutil.copy2(sp, dp)
    # Assets -> /assets/
    if os.path.isdir(ext_root):
        shutil.copytree(ext_root, os.path.join(dest, "assets"), dirs_exist_ok=True)


def load_asset_map(src: str) -> dict[str, str]:
    p = os.path.join(src, "_assetmap.json")
    if not os.path.exists(p):
        return {}
    with open(p) as fh:
        return json.load(fh)


def _schemeless(url: str) -> str:
    return re.sub(r"^https?:", "", url)  # -> //host/path[?query]


def build_asset_regexes(asset_map: dict[str, str]) -> list[tuple[re.Pattern, str]]:
    """Rewrite rules, most-specific first:

    1. Exact rules for URLs that carry a meaningful query (e.g. font CSS
       ``?family=...``) so query variants map to their own local file. ``&`` is
       matched as ``&`` or ``&amp;`` to catch HTML-entity encoding.
    2. Path rules matching optional scheme + optional query, so signed/expiring
       image URLs (all sharing one path) collapse to a single local file.
    """
    exact: dict[str, str] = {}
    path_based: dict[str, str] = {}
    for url, served in asset_map.items():
        base = url.split("?", 1)[0]
        path_based[_schemeless(base)] = served
        if "?" in url:
            exact[_schemeless(url)] = served

    rules: list[tuple[re.Pattern, str]] = []
    for key in sorted(exact, key=len, reverse=True):
        pat = re.compile(r"(?:https?:)?" + re.escape(key).replace("&", "(?:&|&amp;)"))
        rules.append((pat, exact[key]))
    for key in sorted(path_based, key=len, reverse=True):
        pat = re.compile(r"(?:https?:)?" + re.escape(key) + r"(?:\?[^\s\"'()<>\\]*)?")
        rules.append((pat, path_based[key]))
    return rules


def normalize_text(text: str, domain: str, asset_rules) -> str:
    for pat, served in asset_rules:
        text = pat.sub(served, text)
    # Strip any residual Wayback prefixes.
    text = re.sub(r"https?://web\.archive\.org/web/\d+(?:id_|im_|cs_|js_)?/", "", text)
    # Own-host absolute links -> root-relative.
    text = re.sub(rf"https?://{re.escape(domain)}(?=[/\"')\s])", "", text)
    apex = domain[4:] if domain.startswith("www.") else domain
    text = re.sub(rf"https?://{re.escape(apex)}(?=[/\"')\s])", "", text)
    return text


def inject_html(path: str) -> None:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        html = fh.read()

    if HAVE_BS4:
        soup = BeautifulSoup(html, "html.parser")
        if soup.head and not soup.find("link", href=BANNER_CSS_HREF):
            soup.head.append(soup.new_tag("link", rel="stylesheet", href=BANNER_CSS_HREF))
        if soup.body and not soup.find(id="archive-banner"):
            soup.body.insert(0, BeautifulSoup(BANNER_HTML, "html.parser"))
        if soup.body and not soup.find(id="archive-nav"):
            # Insert the fallback nav right after the banner.
            anchor = soup.find(id="archive-banner")
            nav = BeautifulSoup(build_fallback_nav(), "html.parser")
            (anchor.insert_after(nav) if anchor else soup.body.insert(0, nav))
        # Lazy images -> eager: copy data-src onto src.
        for img in soup.find_all("img"):
            ds = img.get("data-src")
            if ds and (not img.get("src") or img["src"].startswith("data:")):
                img["src"] = ds
        out = str(soup)
    else:
        out = html
        if BANNER_CSS_HREF not in out:
            out = re.sub(r"</head>", f'<link rel="stylesheet" href="{BANNER_CSS_HREF}"></head>',
                         out, count=1, flags=re.I)
        if 'id="archive-banner"' not in out:
            out = re.sub(r"(<body[^>]*>)", r"\1" + BANNER_HTML + build_fallback_nav(),
                         out, count=1, flags=re.I)

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

    asset_map = load_asset_map(args.src)
    asset_rules = build_asset_regexes(asset_map)
    print(f"Loaded {len(asset_map)} asset-map entries -> {len(asset_rules)} rewrite rules.")

    copy_tree(args.src, args.dest)

    text_files = iter_files(args.dest, (".html", ".htm", ".css"))
    # Exclude our own scaffolding files from rewriting.
    skip = {os.path.abspath(os.path.join(args.dest, "archive-banner.css"))}
    for path in text_files:
        if os.path.abspath(path) in skip:
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
        fixed = normalize_text(content, args.domain, asset_rules)
        if fixed != content:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(fixed)

    html_files = [p for p in iter_files(args.dest, (".html", ".htm"))
                  if os.path.abspath(p) not in skip]
    for path in html_files:
        inject_html(path)

    print(f"Post-processed {len(html_files)} HTML files.")
    if not HAVE_BS4:
        print("NOTE: bs4 not installed -- data-src->src conversion skipped; "
              "install beautifulsoup4 and re-run for static image rendering.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
