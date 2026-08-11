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

The original site was built with the IONOS/1&1 "MyWebsite" builder, so its pages live
on `www.transect.de` but its images, widget CSS, fonts and runtime JS live on
`*.website-editor.net` CDNs (plus Google Fonts). Rebuilding therefore takes **three
steps** — two fetch passes (which need outbound access to `web.archive.org`) and one
post-process pass (offline).

```bash
# 1. Download the site's own pages/assets into _snapshot/ (raw archive bytes)
python3 scripts/fetch_snapshot.py --domain www.transect.de --timestamp 20240419182714 --out _snapshot

# 2. Harvest the external CDN assets (images/CSS/JS/fonts) referenced by those
#    pages, into _snapshot/_ext/, and write _snapshot/_assetmap.json
python3 scripts/fetch_assets.py --snapshot _snapshot --timestamp 20240419182714

# 3. Build the served site into the repo root: relocate extensionless pages to
#    <name>/index.html, localize all CDN URLs, copy data-src->src, and inject the
#    archive banner + fallback nav + PARCSA link. (bs4 recommended.)
pip install beautifulsoup4
python3 scripts/postprocess.py --src _snapshot --dest . --domain www.transect.de
```

The image URLs on the original pages are signed and long-expired, so they can only be
recovered from the Internet Archive (step 2), not the live CDN. `postprocess.py` copies
lazy-loaded `data-src` images onto `src` so they render without the builder's
JavaScript, and injects a static fallback navigation (`#archive-nav`) so the site stays
navigable even if the builder's JS menu does not initialize.

The `_snapshot/` directory is the pristine capture (git-ignored by default); keep it for
provenance/regeneration or discard once the processed site is committed.

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
