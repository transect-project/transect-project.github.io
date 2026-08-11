#!/usr/bin/env python3
"""Second pass: harvest the site's external CDN assets from the Wayback Machine.

The original TRANSECT site was built with the IONOS/1&1 "MyWebsite" builder, so
its images, widget CSS, fonts and runtime JS live on *.website-editor.net CDNs
(account-scoped by a 32-char hash), plus Google Fonts. The image URLs are signed
and long-expired, so they can only be recovered from the Internet Archive.

This script:
  1. Collects candidate asset URLs by (a) parsing every HTML/CSS file produced by
     fetch_snapshot.py and (b) enumerating the account-scoped media via the CDX API.
  2. Downloads each asset from Wayback (raw ``id_`` bytes, nearest capture to the
     target timestamp), de-duplicating by path.
  3. Recurses into fetched CSS to pull in its url()/@import dependencies (fonts,
     background images).
  4. Writes assets under ``_snapshot/_ext/<host>/<path>`` and a rewrite map to
     ``_snapshot/_assetmap.json`` for postprocess.py to consume.

Requires only the Python standard library, plus outbound access to web.archive.org.

Usage:
    python3 scripts/fetch_assets.py --snapshot _snapshot --timestamp 20240419182714
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "transect-archive-mirror/1.0 (+https://github.com/transect-project)"
WEB_BASE = "https://web.archive.org/web"
CDX_URL = "https://web.archive.org/cdx/search/cdx"

# Hosts whose assets we localize (everything else stays an external link).
VENDOR_HOST_RE = re.compile(
    r"^(?:[a-z0-9-]+\.)?(?:website-editor\.net|multiscreensite\.com|"
    r"multiscreenstore\.com|mywebsite-editor\.com|modernizr\.com)$"
    r"|^fonts\.(?:googleapis|gstatic)\.com$",
    re.I,
)
# Account-scoped CDN roots to enumerate via CDX (filled with the detected hash).
CDX_MEDIA_PATTERNS = [
    "le-cdn.website-editor.net/s/{h}/*",
    "cdn.website-editor.net/{h}/*",
    "cdn.website-editor.net/s/{h}/*",
    "static-cdn.website-editor.net/s/{h}/*",
]
URL_RE = re.compile(r"""(?:https?:)?//[^\s"'()<>\\]+""")
CSS_URL_RE = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""", re.I)
CSS_IMPORT_RE = re.compile(r"""@import\s+['"]([^'"]+)['"]""", re.I)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def http_get(url: str, retries: int = 4, timeout: int = 60) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as err:
            last = err
            wait = 2 ** attempt
            log(f"    ! {err} -- retry in {wait}s ({attempt + 1}/{retries})")
            time.sleep(wait)
    raise RuntimeError(f"GET failed: {url} ({last})")


def host_of(url: str) -> str:
    return urllib.parse.urlparse(normalize_scheme(url)).netloc.lower()


def normalize_scheme(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    return url.replace("http://", "https://", 1) if url.startswith("http://") else url


def is_vendor(url: str) -> bool:
    return bool(VENDOR_HOST_RE.match(host_of(url)))


def local_rel(url: str) -> str:
    """Map an asset URL to '<host>/<path>' (query dropped, unless meaningful)."""
    u = urllib.parse.urlparse(normalize_scheme(url))
    path = urllib.parse.unquote(u.path)
    root, ext = os.path.splitext(os.path.basename(path))
    # Font-CSS endpoints have meaningful queries and often no extension.
    meaningful_query = u.query and (not ext or ext.lower() in (".css",))
    if not ext and path.endswith("/"):
        path += "index"
    if meaningful_query:
        qh = hashlib.md5(u.query.encode()).hexdigest()[:8]
        path = f"{path}__{qh}"
        if not ext:
            path += ".css"
    dirpart = os.path.dirname(path).lstrip("/")
    base = os.path.basename(path) or "index"
    rel = os.path.join(u.netloc, dirpart, base)
    return rel.replace("\\", "/")


def detect_account(text_blob: str, override: str | None) -> str:
    if override:
        return override
    hashes = re.findall(r"website-editor\.net/(?:s/)?([0-9a-f]{32})", text_blob)
    if not hashes:
        raise SystemExit("could not detect account hash; pass --account")
    return max(set(hashes), key=hashes.count)


def cdx_originals(pattern: str) -> list[str]:
    params = urllib.parse.urlencode(
        {"url": pattern, "output": "json", "fl": "original",
         "filter": "statuscode:200", "collapse": "urlkey"}
    )
    try:
        raw = http_get(f"{CDX_URL}?{params}")
        rows = json.loads(raw.decode("utf-8", "replace"))
    except Exception as err:  # noqa: BLE001
        log(f"  CDX failed for {pattern}: {err}")
        return []
    return [r[0] for r in rows[1:]] if rows else []


def read_text_files(root: str) -> dict[str, str]:
    out = {}
    for base, _d, files in os.walk(root):
        if os.path.basename(base) == "_ext":
            continue
        for f in files:
            p = os.path.join(base, f)
            if f in ("_manifest.json", "_assetmap.json"):
                continue
            try:
                with open(p, "r", encoding="utf-8", errors="replace") as fh:
                    out[p] = fh.read()
            except Exception:  # noqa: BLE001
                pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", default="_snapshot")
    ap.add_argument("--timestamp", default="20240419182714")
    ap.add_argument("--account", default=None)
    ap.add_argument("--delay", type=float, default=0.3)
    args = ap.parse_args()

    out_dir = os.path.join(args.snapshot, "_ext")
    os.makedirs(out_dir, exist_ok=True)

    texts = read_text_files(args.snapshot)
    blob = "\n".join(texts.values())
    account = detect_account(blob, args.account)
    log(f"Account hash: {account}")

    # Seed queue from HTML/CSS references.
    seeds: set[str] = set()
    for url in URL_RE.findall(blob):
        url = url.rstrip('.,;)"\'')
        if is_vendor(url):
            seeds.add(normalize_scheme(url))
    log(f"Seed vendor URLs from HTML/CSS: {len(seeds)}")

    # Add account-scoped media from CDX.
    for pat in CDX_MEDIA_PATTERNS:
        found = cdx_originals(pat.format(h=account))
        log(f"CDX {pat.format(h=account)} -> {len(found)}")
        seeds.update(normalize_scheme(u) for u in found if is_vendor(u))

    # BFS download.
    asset_map: dict[str, str] = {}
    queue = list(seeds)
    seen_paths: set[str] = set()
    ok = failed = 0
    while queue:
        url = queue.pop()
        rel = local_rel(url)
        served = "/assets/" + rel
        # Map both the exact and query-stripped forms to the local path.
        asset_map[url] = served
        asset_map[url.split("?")[0]] = served
        if rel in seen_paths:
            continue
        seen_paths.add(rel)
        dest = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # Try exact URL (with query) first, then stripped.
        body = None
        for candidate in (url, url.split("?")[0]):
            try:
                body = http_get(f"{WEB_BASE}/{args.timestamp}id_/{candidate}")
                break
            except Exception:  # noqa: BLE001
                continue
        if body is None:
            log(f"  x FAILED {url}")
            failed += 1
            continue
        with open(dest, "wb") as fh:
            fh.write(body)
        ok += 1
        log(f"  [{ok}] {rel}")
        # Recurse into CSS for nested url()/@import deps.
        if rel.lower().endswith(".css"):
            css = body.decode("utf-8", "replace")
            for ref in CSS_URL_RE.findall(css) + CSS_IMPORT_RE.findall(css):
                if ref.startswith("data:"):
                    continue
                absref = urllib.parse.urljoin(url, ref)
                if is_vendor(absref) and local_rel(absref) not in seen_paths:
                    queue.append(normalize_scheme(absref))
        time.sleep(args.delay)

    map_path = os.path.join(args.snapshot, "_assetmap.json")
    with open(map_path, "w") as fh:
        json.dump(asset_map, fh, indent=2, sort_keys=True)
    log(f"\nDone. downloaded={ok} failed={failed} mapped={len(asset_map)}")
    log(f"Assets: {out_dir}\nMap: {map_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
