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
import hashlib
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
    ("/#PartnersA", "Partners"),
    ("/#Contact", "Contact"),
    ("/parsco/", "PARSCO"),
]
NAV_LOGO_HTML = (
    '<a href="/" class="archive-logo" aria-label="TRANSECT home">'
    '<img src="/assets/img/transect-logo.png" alt="TRANSECT" /></a>'
)


def build_fallback_nav() -> str:
    links = "".join(f'<a href="{href}">{label}</a>' for href, label in FALLBACK_NAV_ITEMS)
    return (f'<nav id="archive-nav" aria-label="Archived site navigation">'
            f'{NAV_LOGO_HTML}{links}</nav>')


# Project team, rebuilt (the original section's content was lost). Photos live in
# static/assets/team/<slug>.jpg; slugs match the team/ sub-directories. Ordered
# group leader -> senior -> postdoc -> PhD researchers -> coordinator.
TEAM_MEMBERS = [
    {
        "name": "Dr. Michael Spies", "role": "Group leader", "slug": "dr-michael-spies",
        "desc": "Michael is a human geographer with broad experience in "
                "transdisciplinary human-environmental research. As initiator and "
                "group leader of TRANSECT, his main research interests are reflected "
                "in the project design and include social-ecological dynamics of "
                "farming systems, participatory approaches to natural resource "
                "management, and transboundary perspectives of agrarian change in "
                "Central Asia and Pakistan.",
        "interests": ["Agricultural transformation processes", "Agroforestry",
                       "Transboundary dimensions of farming systems",
                       "Central Asia and Pakistan",
                       "Participatory approaches to natural resource management",
                       "Theories of human-environmental relations"],
    },
    {
        "name": "Dr Henryk Alff", "role": "Senior researcher", "slug": "dr-henryk-alff",
        "desc": "Trained in Human Geography and Area Studies (Slavic and Central "
                "Asian Studies), Henryk draws on eighteen years of experience in "
                "development and transformation research in different regional "
                "contexts. Based on long-term fieldwork, his work in the TRANSECT "
                "project scrutinises the various political, socio-economic and "
                "ecological transitions in the agricultural sector of south-eastern "
                "Kazakhstan.",
        "interests": ["Migration and mobility research",
                       "Geographical development research",
                       "Border & boundary studies", "(Post-) area studies",
                       "Translocality and positionality", "Critical spatial theory",
                       "Actor-based and ethnographical approaches",
                       "Qualitative social research"],
    },
    {
        "name": "Dr. Christoph Raab", "role": "Postdoctoral researcher",
        "slug": "christoph-raab",
        "desc": "Christoph is a geographer by training with a focus on remote "
                "sensing and nature conservation. He is especially interested in "
                "the application of machine learning algorithms to understand "
                "changes in agricultural land use and grassland ecosystems.",
        "interests": ["Satellite remote sensing", "Machine learning",
                       "Change analysis", "Grassland ecosystems",
                       "Biodiversity and nature conservation"],
    },
    {
        "name": "Aksana Zakirova", "role": "PhD researcher", "slug": "aksana-zakirova",
        "desc": "Aksana has a grounded knowledge in major development problems in "
                "the agricultural sector in Central Asia. Her research is on "
                "agricultural transformations in rural Tajikistan, with a particular "
                "focus on cotton production. Building up on her past work experience "
                "in rural Kyrgyzstan and her knowledge of Russian, Aksana is a great "
                "asset to the research group.",
        "interests": ["Agricultural transformations", "Community development",
                       "Value chain analyses of aquaculture production",
                       "Climate change impact on aquaculture production",
                       "Central Asia, Tajikistan, Kyrgyz Republic"],
    },
    {
        "name": "Mehwish Zuberi", "role": "PhD researcher", "slug": "mehwish-zuberi",
        "desc": "Mehwish has a keen interest in sustainability transitions "
                "underpinned by social equity. Her previous research experiences "
                "encompass community based natural resource management, "
                "cross-sectoral natural resource policies, and multi-level "
                "governance with a regional focus on Europe and South Asia. As part "
                "of the TRANSECT project, she will conduct in-depth field research "
                "on agricultural transformations in Punjab, Pakistan.",
        "interests": ["Forest and environmental policy analysis",
                       "Socio-ecological sustainability of bioeconomy",
                       "Agricultural transformations", "South Asia"],
    },
    {
        "name": "Madlen Mählis", "role": "Project coordinator", "slug": None,
        "desc": "", "interests": [],
    },
]


def _esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_team_html() -> str:
    rows = []
    for m in TEAM_MEMBERS:
        photo = (f'<img class="team-photo" src="/assets/team/{m["slug"]}.jpg" '
                 f'alt="{_esc(m["name"])}" loading="lazy" />') if m["slug"] else ""
        desc = f'<p class="team-desc">{_esc(m["desc"])}</p>' if m["desc"] else ""
        interests = ""
        if m["interests"]:
            items = "".join(f"<li>{_esc(i)}</li>" for i in m["interests"])
            interests = (f'<p class="team-interests-label">Research interests</p>'
                         f'<ul class="team-interests">{items}</ul>')
        rows.append(
            f'<div class="team-member{"" if m["slug"] else " no-photo"}">{photo}'
            f'<div class="team-info"><h3 class="team-name">{_esc(m["name"])}</h3>'
            f'<p class="team-role">{_esc(m["role"])}</p>{desc}{interests}</div></div>'
        )
    return f'<div id="archive-team">{"".join(rows)}</div>'


def inject_team_section(soup) -> bool:
    """Insert the rebuilt team member grid into the homepage #Team section."""
    team = soup.find(id="Team")
    if not team or soup.find(id="archive-team"):
        return False
    target = team.find(class_="dmRespColsWrapper") or team
    target.append(BeautifulSoup(build_team_html(), "html.parser"))
    return True


def copy_static(dest: str, static_dir: str = "static") -> int:
    """Overlay the new/served hand-authored files (logo, team photos, downloads,
    the PARSCO page) from static/ onto the build. Returns files copied."""
    if not os.path.isdir(static_dir):
        return 0
    count = 0
    for base, _dirs, files in os.walk(static_dir):
        for f in files:
            sp = os.path.join(base, f)
            rel = os.path.relpath(sp, static_dir)
            dp = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(dp), exist_ok=True)
            shutil.copy2(sp, dp)
            count += 1
    return count


# Directories under the destination that are inputs/tooling, never served output;
# the post-processing passes must not descend into them (esp. the _snapshot source
# and the static/ overlay, which is copied into place separately).
_EXCLUDE_DIRS = {"_snapshot", ".git", "scripts", "node_modules", "static"}

# Captured pages to drop from the build (e.g. the News blog, whose item sub-pages
# were largely never archived).
_EXCLUDE_PAGES = {"news"}


def iter_files(root: str, exts: tuple[str, ...] | None = None) -> list[str]:
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _EXCLUDE_DIRS]
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
            if f in _EXCLUDE_PAGES:  # drop dropped pages (e.g. News)
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
        # Remove the original builder navigation menu (its JS-driven links are
        # unreliable in the static archive); the injected #archive-nav replaces
        # it. The logo/header container is left intact.
        for orig_nav in soup.select("nav.main-navigation"):
            orig_nav.decompose()
        # Rebuild the lost project-team section on the homepage.
        inject_team_section(soup)
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


def restore_parallax_backgrounds(dest: str) -> int:
    """Re-attach section background photos that the live site's parallax
    JavaScript applied at runtime.

    Several section elements carry a capitalised semantic class (e.g. ``u_Goals``)
    while the stylesheet's ``background-image`` rule is keyed on the lower-case
    form (``.u_goals``). The builder's JS bridged that at load time; statically it
    never matches, so the (present) photo never paints. For each such lower-case
    background rule whose image exists on disk, add the matching lower-case class
    to the element that has its capitalised twin, so the existing background (and
    its overlay) rules apply. Returns the number of classes added.
    """
    if not HAVE_BS4:
        return 0
    rule_re = re.compile(
        r"\.(u_[A-Za-z0-9]+)\s*\{[^{}]*?background-image:\s*url\((/assets/[^),'\"]+)\)",
        re.I,
    )
    added = 0
    for path in iter_files(dest, (".html", ".htm")):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            html = fh.read()
        bg_classes = {
            cls for cls, img in rule_re.findall(html)
            if cls.islower() and os.path.exists(os.path.join(dest, img.lstrip("/")))
        }
        if not bg_classes:
            continue
        soup = BeautifulSoup(html, "html.parser")
        changed = False
        for el in soup.find_all(class_=True):
            classes = el.get("class", [])
            to_add = [
                c.lower() for c in classes
                if c.lower() != c and c.lower() in bg_classes and c.lower() not in classes
            ]
            if to_add:
                el["class"] = classes + to_add
                added += len(to_add)
                changed = True
        if changed:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(str(soup))
    return added


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
    for base, dirs, files in os.walk(assets):
        # Skip the raw author source drop (assets/content) -- not served, and its
        # files may be open/locked (docx, large PDFs).
        dirs[:] = [d for d in dirs if os.path.join(base, d) != os.path.join(assets, "content")]
        used = set(os.listdir(base))
        for f in sorted(files):  # stable order for reproducible builds
            new = _sanitize_basename(f)
            if new == f:
                continue
            if new in used and os.path.join(base, new) != os.path.join(base, f):
                # Deterministic disambiguation (hash() is per-process randomised).
                stem, ext = os.path.splitext(new)
                digest = hashlib.md5(f.encode("utf-8")).hexdigest()[:6]
                new = f"{stem}-{digest}{ext}"
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
    static_n = copy_static(args.dest)

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
    bg_added = restore_parallax_backgrounds(args.dest)
    print(f"Post-processed {len(html_files)} HTML files; overlaid {static_n} static "
          f"files; remapped {remapped} missing versioned CSS/JS refs to captured "
          f"siblings; sanitized {renamed} asset filenames for GitHub Pages; "
          f"restored {bg_added} parallax section backgrounds.")
    if not HAVE_BS4:
        print("NOTE: bs4 not installed -- data-src->src conversion skipped; "
              "install beautifulsoup4 and re-run for static image rendering.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
