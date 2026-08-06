#!/usr/bin/env python3
"""Download a full static copy of a site from the Wayback Machine.

Enumerates every captured URL for the target host via the Wayback CDX API, then
downloads the *raw original* bytes for each (using the ``id_`` modifier, which
returns the archived file without Wayback's injected toolbar or link rewriting).

Output is a mirrored directory tree under ``--out`` (default: ``_snapshot/``),
which ``postprocess.py`` then turns into the served site.

Usage:
    python3 scripts/fetch_snapshot.py \
        --domain www.transect.de \
        --timestamp 20240419182714 \
        --out _snapshot

Requires only the Python standard library, but needs outbound access to
web.archive.org.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from http.client import IncompleteRead

USER_AGENT = "transect-archive-mirror/1.0 (+https://github.com/transect-project)"
CDX_URL = "https://web.archive.org/cdx/search/cdx"
WEB_BASE = "https://web.archive.org/web"


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def http_get(url: str, retries: int = 4, timeout: int = 60) -> bytes:
    """GET with simple exponential backoff. Returns body bytes."""
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, IncompleteRead, TimeoutError) as err:
            last_err = err
            wait = 2 ** attempt
            log(f"  ! {err} -- retrying in {wait}s ({attempt + 1}/{retries})")
            time.sleep(wait)
    raise RuntimeError(f"GET failed after {retries} attempts: {url} ({last_err})")


def list_captures(domain: str) -> list[dict]:
    """Return the newest 200-status capture per unique URL under the domain."""
    params = urllib.parse.urlencode(
        {
            "url": f"{domain}/*",
            "output": "json",
            "fl": "timestamp,original,mimetype,statuscode",
            "filter": "statuscode:200",
            "collapse": "urlkey",  # one row per unique URL
        }
    )
    log(f"Querying CDX for {domain} ...")
    raw = http_get(f"{CDX_URL}?{params}")
    rows = json.loads(raw.decode("utf-8", "replace"))
    if not rows:
        return []
    header, *data = rows
    return [dict(zip(header, r)) for r in data]


def local_path(out_dir: str, original: str) -> str:
    """Map an original URL to a local file path, using index.html for dirs."""
    parsed = urllib.parse.urlparse(original)
    path = urllib.parse.unquote(parsed.path)
    if not path or path.endswith("/"):
        path = path + "index.html"
    # Preserve query-string variants so distinct pages don't collide.
    if parsed.query:
        root, ext = os.path.splitext(path)
        safe_q = urllib.parse.quote(parsed.query, safe="")
        path = f"{root}__{safe_q}{ext or '.html'}"
    rel = path.lstrip("/")
    full = os.path.normpath(os.path.join(out_dir, rel))
    if not os.path.abspath(full).startswith(os.path.abspath(out_dir)):
        raise ValueError(f"unsafe path escapes out dir: {original}")
    return full


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--domain", default="www.transect.de")
    ap.add_argument("--timestamp", default="20240419182714",
                    help="preferred snapshot timestamp (closest capture is used)")
    ap.add_argument("--out", default="_snapshot")
    ap.add_argument("--delay", type=float, default=0.5,
                    help="seconds to pause between downloads (be polite)")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N files (0 = no limit; for testing)")
    args = ap.parse_args()

    captures = list_captures(args.domain)
    log(f"Found {len(captures)} unique captured URLs.")

    manifest: list[dict] = []
    ok = skipped = failed = 0
    for i, cap in enumerate(captures, 1):
        if args.limit and ok >= args.limit:
            break
        original = cap["original"]
        ts = cap["timestamp"]
        dest = local_path(args.out, original)
        if os.path.exists(dest):
            skipped += 1
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # id_ = raw original bytes, no Wayback rewriting/toolbar.
        fetch_url = f"{WEB_BASE}/{ts}id_/{original}"
        log(f"[{i}/{len(captures)}] {original}")
        try:
            body = http_get(fetch_url)
            with open(dest, "wb") as fh:
                fh.write(body)
            manifest.append({"url": original, "timestamp": ts,
                             "path": os.path.relpath(dest, args.out),
                             "mimetype": cap.get("mimetype", "")})
            ok += 1
        except Exception as err:  # noqa: BLE001 - keep going, record failure
            log(f"  x FAILED: {err}")
            failed += 1
        time.sleep(args.delay)

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    log(f"\nDone. downloaded={ok} skipped={skipped} failed={failed}")
    log(f"Manifest: {os.path.join(args.out, '_manifest.json')}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
