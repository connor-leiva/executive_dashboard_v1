"""
Axcion ground plates — generated optical texture.

NOT photographs, and never to be captioned as if they were. They are abstract lens
studies, and they are derived from Axcion's OWN visual language rather than anyone
else's: the identity guide of the time described the mark as "three blades opening on a
fixed point: an aperture", and a three-bladed iris produces curved-triangular bokeh, so
out-of-focus highlights carry that geometry without ever drawing the logo.

THAT MARK IS SUPERSEDED. The designed mark was delivered on 2026-09-22 and it is a
four-pointed crossed form, not an aperture -- so the premise in the paragraph above is
history, and `blades=3` below is no longer an echo of anything. A four-bladed iris would
render the new silhouette, and `iris_kernel(blades=4)` is the whole change. It has not
been made: these plates and their siblings in public/brand/axcion are the hero default for
every workspace that never chose its own imagery, so regenerating them re-skins those
workspaces without them asking. That is a decision, not a consequence of a new logo.

Deliberately not the fluted-glass treatment in Spring's brand photos. That is Spring's
identity; Axcion is a different brand and borrowing it would repeat exactly the mistake
of treating tenant #1's look as the platform's.

Everything here is procedural — no source imagery of any kind.
"""
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# Beside this script, which is where ui.jsx imports the plates from.
OUT = os.path.dirname(os.path.abspath(__file__))
RNG = np.random.default_rng(11)          # fixed seed → regenerating gives identical plates

CADET, SAGE, HAZE = (63, 107, 102), (143, 179, 174), (189, 213, 208)
INK, PAPER, WHITE = (22, 32, 31), (239, 240, 236), (255, 255, 255)


def iris_kernel(size=512, blades=3, curve=0.30, softness=0.05):
    """One out-of-focus highlight, shaped by a three-bladed aperture.

    A real iris of N blades renders points of light as N-sided figures with edges bowed
    outward by the blades' curvature — which is why cheap lenses show pentagons and this
    one shows curved triangles. Drawn as a polygon whose vertices are pushed out along
    each blade's arc, then blurred to give the edge its falloff.
    """
    img = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(img)
    c, r = size / 2, size * 0.30
    pts = []
    for i in range(blades * 24):                     # dense enough to read as a smooth arc
        t = i / (blades * 24) * 2 * math.pi
        # bulge each blade outward: peaks midway between vertices
        bulge = 1.0 + curve * (math.cos(blades * t) * 0.5 + 0.5) ** 2
        pts.append((c + r * bulge * math.cos(t), c + r * bulge * math.sin(t)))
    d.polygon(pts, fill=255)
    return img.filter(ImageFilter.GaussianBlur(size * softness))


IRIS = iris_kernel()


def scatter(w, h, spec, ground):
    """Composite bokeh onto a ground, far ones first so near ones occlude correctly."""
    base = Image.new("RGB", (w, h), ground)
    for (colour, count, rmin, rmax, alpha, cx, cy, spread) in spec:
        for _ in range(count):
            # cluster around a focal point rather than filling the frame evenly
            x = int(RNG.normal(cx * w, spread * w))
            y = int(RNG.normal(cy * h, spread * h))
            rad = int(RNG.uniform(rmin, rmax) * min(w, h))
            if rad < 4:
                continue
            k = IRIS.resize((rad * 2, rad * 2), Image.LANCZOS).rotate(
                RNG.uniform(0, 120), resample=Image.BICUBIC)
            a = RNG.uniform(alpha * 0.45, alpha)
            k = k.point(lambda v, a=a: int(v * a))
            base.paste(Image.new("RGB", k.size, colour), (x - rad, y - rad), k)
    return base


def plate(name, size, ground, spec, *, defocus=5, vignette=0.5, grain=6.0, lift=0.0):
    w, h = size
    img = scatter(w, h, spec, ground)
    img = img.filter(ImageFilter.GaussianBlur(defocus))     # the whole plate is out of focus

    a = np.asarray(img, np.float32)
    if lift:                                                # veiling flare toward the light
        a = a * (1 - lift) + np.array(ground, np.float32) * 0 + 255.0 * lift * 0.35
    if vignette:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        r = np.sqrt(((xx / w - .5) * 2) ** 2 + ((yy / h - .5) * 2) ** 2)
        a *= (1 - vignette * np.clip(r - .5, 0, None) ** 2)[..., None]
    a += RNG.normal(0, grain, (h, w, 1))                    # grain last, luminance only

    out = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")
    path = os.path.join(OUT, name)
    out.save(path, quality=86, optimize=True, subsampling=0)
    print(f"  {name:24} {w}x{h}  {os.path.getsize(path)//1024} KB")


print("generating plates:")

# 1 — light ground. Highlights blooming from the left, plenty of clean space for type.
plate("aperture-light.jpg", (2200, 1375), WHITE, [
    (HAZE,  14, .085, .200, .40, .30, .50, .22),
    (SAGE,  11, .050, .120, .40, .26, .56, .18),
    (CADET,  7, .028, .070, .34, .22, .44, .14),
], defocus=6, vignette=.26, grain=5.5)

# 2 — Ink ground for the dark bands. Sparse, so text stays readable over it.
plate("aperture-ink.jpg", (2200, 1375), INK, [
    (CADET, 13, .075, .190, .58, .68, .52, .21),
    (SAGE,   9, .042, .105, .46, .74, .44, .16),
    (HAZE,   5, .022, .055, .30, .64, .60, .12),
], defocus=7, vignette=.70, grain=4.5)

# 3 — Paper band. Very quiet; texture that never competes with a headline.
plate("aperture-paper.jpg", (2200, 1100), PAPER, [
    (HAZE,  12, .090, .230, .26, .50, .50, .28),
    (SAGE,   7, .050, .120, .20, .38, .56, .20),
], defocus=9, vignette=.20, grain=5.5)

# 4 — portrait crop, denser and more figurative, for cards and the About page.
plate("aperture-detail.jpg", (1400, 1750), WHITE, [
    (CADET, 11, .085, .195, .52, .46, .42, .20),
    (SAGE,   9, .050, .115, .46, .54, .58, .17),
    (HAZE,   6, .026, .065, .34, .38, .34, .14),
], defocus=5, vignette=.40, grain=6.5)
