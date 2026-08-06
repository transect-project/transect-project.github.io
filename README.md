# transect.github.io

Archival website for the **TRANSECT** research project.

The TRANSECT project website was originally hosted at `https://www.transect.de/`.
The domain lapsed and the original site was taken down. This repository rebuilds a
faithful **static** copy from the Internet Archive's Wayback Machine and serves it via
**GitHub Pages** on the re-secured custom domain **`www.transect.de`**.

- **Source snapshot:** <https://web.archive.org/web/20240419182714/https://www.transect.de/>
- **Reference capture timestamp:** `20240419182714` (2024-04-19)

Two deliberate additions sit on top of the archived content:

1. A **fixed "archived copy" banner** on every page (notice only — see
   `archive-banner.css`).
2. A **PARCSA methodology** page at [`/parcsa/`](parcsa/index.html) documenting the
   research methodology the project developed. (Currently a scaffold awaiting the
   project's own content.)

There is intentionally **no CMS** — the site is frozen and edited directly as static
files on the rare occasions it changes.

## Repository layout

```
/                     served site root (processed HTML + assets go here)
  CNAME               custom domain for GitHub Pages: www.transect.de
  .nojekyll           serve files verbatim (no Jekyll build)
  archive-banner.css  styling for the injected archive banner
  parcsa/index.html   new PARCSA methodology page
  scripts/
    fetch_snapshot.py raw download of the site from the Wayback Machine
    postprocess.py    link normalization + banner/nav injection
```

## Regenerating the archived content

The scraped pages are produced by two scripts. Both need outbound access to
`web.archive.org` (the fetch step) and, ideally, `beautifulsoup4` (the post-process
step, for automatic nav-link injection).

```bash
# 1. Download the full site into _snapshot/ (raw, unmodified archive bytes)
python3 scripts/fetch_snapshot.py --domain www.transect.de --timestamp 20240419182714 --out _snapshot

# 2. (optional but recommended) enable nav-link injection
pip install beautifulsoup4

# 3. Copy into the repo root, normalize links, inject the banner + PARCSA nav link
python3 scripts/postprocess.py --src _snapshot --dest . --domain www.transect.de
```

After the first run, inspect the scraped navigation markup and, if needed, adjust
`NAV_HINTS` in `scripts/postprocess.py` so the PARCSA link lands in the real menu, then
re-run step 3. The `_snapshot/` directory is the pristine capture and can be kept for
provenance or discarded once the processed site is committed.

## Previewing locally

```bash
python3 -m http.server 8000
# then open http://localhost:8000/
```

Check that pages render with their original CSS/images, internal links work, the banner
appears on every page, and `/parcsa/` renders and is linked from the navigation.

## Deployment (GitHub Pages)

This is an organization Pages site (`transect-project.github.io`), so Pages publishes
from the **default branch (`main`) root**.

1. Merge this content to `main`.
2. In **Settings → Pages**, set the source to **Deploy from a branch → `main` / `/`**.
3. Confirm the custom domain shows `www.transect.de` (read from the `CNAME` file).
4. DNS: add a **CNAME** record for `www` pointing to `transect-project.github.io`.
5. GitHub automatically provisions a TLS certificate once DNS resolves; enable
   **Enforce HTTPS**.
