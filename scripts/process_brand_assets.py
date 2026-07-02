"""One-time brand-asset pipeline: frontend/brand-src/ (git-ignored originals from
Spring's delivered library) -> frontend/public/brand/ (what the app consumes).

NOTE ON THE DELIVERY: unlike the brand spec's assumption of "flattened RGB PNGs
with no alpha", this delivery ships proper RGBA — the icons and logos are
black art on a TRANSPARENT ground (the alpha channel is the shape), so they are
used directly as CSS masks (no luminance-invert, which would turn an all-black
icon into a solid square). The ribbed gradients arrive at 2849px / ~4.7 MB each
and are composited over white + downsized + re-encoded so heroes stay light.

Run:  cd <repo root> && backend/.venv/Scripts/python.exe scripts/process_brand_assets.py
Re-run only when the team ships new files.
"""
from __future__ import annotations

import pathlib
from PIL import Image, ImageDraw

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "brand-src"
OUT = ROOT / "frontend" / "public" / "brand"

EVERGREEN = (0, 46, 44, 255)
PARCHMENT = (248, 245, 242, 255)

# Utility icons the brand spec maps (name -> category/file under Icons/PNG). These
# are already alpha masks; the <Icon> component tints them via CSS mask.
ICONS = {
    "sync": "general/sync", "settings": "general/settings", "logout": "general/logout",
    "puzzle": "general/puzzle", "search": "general/search", "notification": "general/notification",
    "spark": "general/spark", "home": "general/home", "bar_chart": "charts/bar_chart",
    "growth_graph": "business/growth_graph", "line_chart": "charts/line_chart",
    "open": "general/open", "link": "general/link", "download": "general/download",
    "warning": "general/warning", "info": "general/info", "check_circled": "general/check_circled",
    "check": "general/check", "close": "general/close", "chevron_down": "general/chevron_down",
}


def process_icons():
    (OUT / "icons").mkdir(parents=True, exist_ok=True)
    for name, path in ICONS.items():
        src = SRC / "Icons" / "PNG" / f"{path}.png"
        if not src.exists():
            print(f"  ! missing icon {src}")
            continue
        Image.open(src).convert("RGBA").save(OUT / "icons" / f"{name}.png", optimize=True)
    print(f"  icons -> {len(ICONS)}")


def process_logos():
    (OUT / "logo").mkdir(parents=True, exist_ok=True)
    # Signature + bare-"s" logomark: alpha = the drawn shape, tinted via CSS mask.
    Image.open(SRC / "Logos" / "spring_logo_black.png").convert("RGBA") \
        .save(OUT / "logo" / "spring_logo.png", optimize=True)
    Image.open(SRC / "Logos" / "spring_logomark_1_black.png").convert("RGBA") \
        .save(OUT / "logo" / "spring_logomark.png", optimize=True)
    print("  logos -> spring_logo, spring_logomark")


def favicon(size: int) -> Image.Image:
    """Spring's mark: Evergreen disc + Parchment 's' (from the bare-s logomark)."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(canvas).ellipse([0, 0, size - 1, size - 1], fill=EVERGREEN)
    s = Image.open(SRC / "Logos" / "spring_logomark_1_black.png").convert("RGBA")
    glyph = Image.new("RGBA", s.size, PARCHMENT)
    glyph.putalpha(s.getchannel("A"))
    gw = int(size * 0.60)
    gh = int(gw * s.size[1] / s.size[0])
    glyph = glyph.resize((gw, gh), Image.LANCZOS)
    canvas.alpha_composite(glyph, ((size - gw) // 2, (size - gh) // 2))
    return canvas


def process_favicons():
    for sz in (512, 180, 32, 16):
        favicon(sz).save(OUT / "logo" / f"favicon-{sz}.png")
    print("  favicons -> 512/180/32/16")


def process_ribbed():
    # Colored ribs on a light ground; composite over white -> downsize -> JPG.
    # Heroes place these over the token color with background-blend-mode: multiply.
    n = 0
    for p in sorted(SRC.glob("Assets/RibbedGradient_*.png")):
        im = Image.open(p).convert("RGBA")
        comp = Image.alpha_composite(Image.new("RGBA", im.size, (255, 255, 255, 255)), im).convert("RGB")
        w = 1440
        comp = comp.resize((w, int(w * comp.size[1] / comp.size[0])), Image.LANCZOS)
        comp.save(OUT / f"{p.stem}.jpg", quality=84, optimize=True)
        n += 1
    print(f"  ribbed gradients -> {n}")


def process_photos():
    (OUT / "photos").mkdir(parents=True, exist_ok=True)
    for f in ("gradient_1.png", "gradient_2.png"):
        src = SRC / "Assets" / f
        if src.exists():
            im = Image.open(src).convert("RGB")
            if im.size[0] > 1600:
                im = im.resize((1600, int(1600 * im.size[1] / im.size[0])), Image.LANCZOS)
            im.save(OUT / "photos" / f.replace(".png", ".jpg"), quality=82, optimize=True)
    for f in ("lady_motionblur.png", "GlassEffect_Flower_1.png"):
        src = SRC / "Photos" / f
        if src.exists():
            im = Image.open(src).convert("RGB")
            if im.size[0] > 1600:
                im = im.resize((1600, int(1600 * im.size[1] / im.size[0])), Image.LANCZOS)
            im.save(OUT / "photos" / f.replace(".png", ".jpg"), quality=82, optimize=True)
    print("  photos -> login backdrop set")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"brand pipeline: {SRC} -> {OUT}")
    process_icons()
    process_logos()
    process_favicons()
    process_ribbed()
    process_photos()
    print("done.")
