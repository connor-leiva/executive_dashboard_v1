"""Axcion's web brand assets, derived from the delivered masters.

This replaces gen_favicons.py, which DREW the mark from geometry because there was no mark yet --
the identity guide specified derivable construction, so the product built a stand-in from those
numbers. The designed mark arrived on 2026-09-22 (brand-src/axcion/, see its README) and it is a
different shape entirely: crossed tapered blades with a detached leaf, not three arcs around a
pupil. Nothing is derived from geometry any more. What this script does is resize.

TWO KINDS OF OUTPUT, and the difference matters:

  * COPIED VERBATIM -- the favicons and app icons. The designer drew a separate micro mark for
    small sizes; the brief is explicit that a favicon is not the master shrunk ("Do not simply
    shrink the master symbol to create the favicon", 12) and that is visible in the files: the
    16px favicon is a solid cadet silhouette, while 32 and 64 are the ink tile. Resampling those
    would throw away the work that makes them legible and replace it with a blur.

  * RESIZED -- the mark and the lockups, which are one artwork at whatever size the page needs.
    Downscaled with Lanczos from the 1024px/2255px masters.

The masters are PNG. The brief asked for vector (23: "Vector masters: AI, EPS, SVG") and the
delivery did not include them, so the web assets are raster at a generous size rather than an
<svg> that scales for free. If SVG masters arrive later this script is what changes.

TWO DESTINATIONS, and that difference matters too:

  * public/brand/axcion/ -- the favicons and app icons, named by <link> tags in the five
    index.html files. Those are URLs, not imports.
  * src/brand/axcion/ -- the mark and the lockups, which axcion.jsx IMPORTS. It has to: four of
    the five Vite entries set `publicDir: false` (frontend/public is the dashboard's, and it holds
    a customer's photographs), so a runtime path into it 404s on the marketing site and the
    operator console. An import is bundled into whichever entry uses it. This is the same
    convention brand/marks/ already uses for the integration marks.

Run:  python frontend/scripts/brand_assets.py
"""
import pathlib
import shutil

from PIL import Image

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "brand-src" / "axcion"
PUBLIC = ROOT / "public" / "brand" / "axcion"
BUNDLED = ROOT / "src" / "brand" / "axcion"

# Copied byte for byte: the designer sized these deliberately.
VERBATIM = {
    "AXCION-favicon-16.png": "favicon-16.png",
    "AXCION-favicon-32.png": "favicon-32.png",
    "AXCION-favicon-64.png": "favicon-64.png",
    "AXCION-appicon-ink-180.png": "appicon-180.png",     # apple-touch-icon
    # 192 and 512 are in the delivery and are not emitted: they are web-app-manifest sizes and
    # there is no manifest. Adding one would turn on Chrome's install prompt, which is a product
    # decision rather than a consequence of a new logo.
}

# Resized. The height is what the page sets, so these are keyed by rendered height: 256 for the
# mark covers a 64px display at 4x, and the mark is never shown larger than that in the product.
# 128 for the horizontal lockup covers the 26px nav at 4.9x.
RESIZED = [
    ("AXCION-mark-primary.png", "mark-primary.png", 256),
    ("AXCION-mark-reversed.png", "mark-reversed.png", 256),
    ("AXCION-mark-white.png", "mark-white.png", 256),
    ("AXCION-mark-ink.png", "mark-ink.png", 256),
    ("AXCION-mark-cadet.png", "mark-cadet.png", 256),
    ("AXCION-lockup-horizontal-primary.png", "lockup-primary.png", 128),
    ("AXCION-lockup-horizontal-reversed.png", "lockup-reversed.png", 128),
    ("AXCION-lockup-horizontal-white.png", "lockup-white.png", 128),
    ("AXCION-lockup-horizontal-ink.png", "lockup-ink.png", 128),
]
# The stacked lockup and the bare wordmark are in brand-src and nothing loads them. They are not
# emitted: an asset in the tree that no entry imports is one nobody notices has gone stale.


def main() -> None:
    PUBLIC.mkdir(parents=True, exist_ok=True)
    BUNDLED.mkdir(parents=True, exist_ok=True)
    wrote = []

    for src_name, out_name in VERBATIM.items():
        shutil.copyfile(SRC / src_name, PUBLIC / out_name)
        with Image.open(PUBLIC / out_name) as im:
            wrote.append(("public", out_name, im.size, (PUBLIC / out_name).stat().st_size, "copied"))

    for src_name, out_name, height in RESIZED:
        with Image.open(SRC / src_name) as im:
            im = im.convert("RGBA")
            width = round(im.width * height / im.height)
            # Lanczos on a transparent PNG: resize the whole RGBA so the alpha edge is resampled
            # with the colour rather than being cut to a hard edge afterwards.
            im.resize((width, height), Image.LANCZOS).save(BUNDLED / out_name, optimize=True)
        wrote.append(("src", out_name, (width, height), (BUNDLED / out_name).stat().st_size, "resized"))

    total = sum(w[3] for w in wrote)
    for where, name, size, nbytes, how in wrote:
        print(f"  {where:7s} {name:28s} {size[0]:>4}x{size[1]:<4} {nbytes / 1024:>6.1f} KB  {how}")
    print(f"  {len(wrote)} files, {total / 1024:.0f} KB total")


if __name__ == "__main__":
    main()
