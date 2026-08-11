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
        key = _schemeless(base)
        # Skip host-only URLs (e.g. preconnect/dns-prefetch hints) -- there's no
        # real file to localize and rewriting them yields broken /index paths.
        if re.fullmatch(r"//[^/]+/?", key):
            continue
        path_based[key] = served
        # Only assets whose local filename encodes a query hash (font CSS,
        # versioned runtime CSS) need query-specific rules; signed image URLs
        # share one path and are handled by the path rule below.
        if "?" in url and "__" in os.path.basename(served):
            exact[_schemeless(url)] = served

    rules: list[tuple[re.Pattern, str]] = []
    for key in sorted(exact, key=len, reverse=True):
        # Match '&' as '&' or the HTML entity '&amp;'; escape each segment
        # separately so parentheses in the pattern stay balanced.
        body = "(?:&|&amp;)".join(re.escape(seg) for seg in key.split("&"))
        rules.append((re.compile(r"(?:https?:)?" + body), exact[key]))
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
        # Define the builder's image-error handler (its defining script was not
        # captured), so uncaptured images don't throw and abort later scripts
        # (e.g. the menu initializer); hide broken images to avoid blank frames.
        if soup.head and not soup.find(id="archive-img-shim"):
            shim = soup.new_tag("script", id="archive-img-shim")
            shim.string = ("window.handleImageLoadError=window.handleImageLoadError||"
                           "function(el){try{if(el&&el.style)el.style.display='none';}"
                           "catch(e){}};")
            soup.head.insert(0, shim)
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


def _css_js_identity(ref_or_path: str) -> str:
    """Version-agnostic identity for a CSS/JS asset: basename with a trailing
    ``__<hex>`` version hash stripped. Lets a page's reference to a version we
    did not capture be remapped to a captured sibling of the same file."""
    b = os.path.basename(ref_or_path)
    return re.sub(r"__[0-9a-f]{6,}$", "", b)


def remap_missing_versioned(dest: str) -> int:
    """Rewrite references to versioned CSS/JS assets we failed to capture onto a
    captured sibling (same file, different version stamp). Returns #rewrites."""
    # Index present css/js on disk by identity; prefer the most complete copy.
    present: dict[str, str] = {}
    for base, _d, files in os.walk(os.path.join(dest, "assets")):
        for f in files:
            if f.lower().endswith((".css", ".js")):
                served = "/" + os.path.relpath(os.path.join(base, f), dest).replace(os.sep, "/")
                present.setdefault(_css_js_identity(f), served)

    ref_re = re.compile(r"/assets/[^\s\"'()<>\\]+\.(?:css|js)")
    total = 0
    for path in iter_files(dest, (".html", ".htm", ".css")):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        changed = False

        def sub(m: re.Match) -> str:
            nonlocal changed, total
            ref = m.group(0)
            if os.path.exists(os.path.join(dest, ref.lstrip("/"))):
                return ref
            sib = present.get(_css_js_identity(ref))
            if sib and sib != ref:
                changed = True
                total += 1
                return sib
            return ref

        new = ref_re.sub(sub, text)
        if changed:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
    return total


_SAFE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_basename(name: str) -> str:
    """Reduce a filename to characters GitHub Pages serves reliably.

    Pages (Fastly) decodes '+' in a path to a space and is unreliable with
    parentheses, spaces and non-ASCII, so images with those names 404 even
    though a local `http.server` serves them fine. Underscores are kept (used by
    our version-hash suffixes); extensions are lowercased for case-safety.
    """
    root, ext = os.path.splitext(name)
    root = _SAFE_RE.sub("-", root)
    root = re.sub(r"-{2,}", "-", root).strip("-") or "file"
    return root + ext.lower()


def sanitize_asset_filenames(dest: str) -> int:
    """Rename /assets files to Pages-safe names and rewrite every reference to
    them across the built HTML/CSS. Returns the number of files renamed."""
    assets = os.path.join(dest, "assets")
    if not os.path.isdir(assets):
        return 0
    mapping: dict[str, str] = {}  # old served path -> new served path
    for base, _dirs, files in os.walk(assets):
        used = set(os.listdir(base))
        for f in files:
            new = _sanitize_basename(f)
            if new == f:
                continue
            if new in used and os.path.join(base, new) != os.path.join(base, f):
                stem, ext = os.path.splitext(new)
                new = f"{stem}-{abs(hash(f)) % 100000}{ext}"
            os.rename(os.path.join(base, f), os.path.join(base, new))
            used.discard(f)
            used.add(new)
            old_served = "/" + os.path.relpath(os.path.join(base, f), dest).replace(os.sep, "/")
            new_served = "/" + os.path.relpath(os.path.join(base, new), dest).replace(os.sep, "/")
            mapping[old_served] = new_served
    if not mapping:
        return 0
    # Rewrite references (longest paths first to avoid partial overlaps).
    ordered = sorted(mapping.items(), key=lambda kv: len(kv[0]), reverse=True)
    for path in iter_files(dest, (".html", ".htm", ".css")):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        new_text = text
        for old, new in ordered:
            if old in new_text:
                new_text = new_text.replace(old, new)
        if new_text != text:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new_text)
    return len(mapping)


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

    remapped = remap_missing_versioned(args.dest)
    renamed = sanitize_asset_filenames(args.dest)
    print(f"Post-processed {len(html_files)} HTML files; "
          f"remapped {remapped} missing versioned CSS/JS refs to captured siblings; "
          f"sanitized {renamed} asset filenames for GitHub Pages.")
    if not HAVE_BS4:
        print("NOTE: bs4 not installed -- data-src->src conversion skipped; "
              "install beautifulsoup4 and re-run for static image rendering.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
