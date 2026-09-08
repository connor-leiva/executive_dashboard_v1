"""Render the Acumyn mark to the four favicon PNGs.

WHY A SCRIPT AND NOT FOUR EXPORTED FILES. The mark is specified as geometry, not artwork — the
identity guide gives radius, weight, sweep and gap axes precisely so it can be rebuilt at any size
without redrawing by eye, and src/brand/acumyn.jsx already builds it from those numbers for the
screen. Exporting PNGs by hand would fork that: the favicon would slowly stop being the mark.
Re-running this is how they stay the same shape.

THE NUMBERS ARE DUPLICATED FROM acumyn.jsx ON PURPOSE. This is Python and that is JSX; importing
across the boundary would mean a build step to generate a static asset. They are asserted against
each other by a test instead, which fails if either side moves.

  artboard 64u · blade radius 22u · sweep 92 degrees · gap axes 90/210/330 · pupil 6.5u
  standard cut: blade weight 8u, pupil 6.5u
  small cut (guide section 02, mark at or below 24px): blade weight 10u, pupil 8u

TWO DECISIONS THIS MAKES, both of which a browser tab forces:

  * ONE COLOUR, ON A DISC. Every approved treatment is a mark on transparency, and the guide's
    default (cadet blades on white) disappears against a dark tab strip — which is half of
    Chrome's users. So the mark is knocked out in white on a Cadet disc: Cadet is mid-tone and
    holds against a light strip and a dark one, and a one-value cut is exactly what the guide
    prescribes "anywhere two values will not reproduce". A 16px icon is that place.

  * THE CUT IS CHOSEN BY THE MARK'S SIZE, NOT THE CANVAS'S. The guide's 24px threshold is about
    rasterisation, and on a disc the mark is inset, so the two are not the same number. Only the
    16 lands on the small cut. That was settled by rendering both cuts at 1:1 and looking: at 32
    the standard cut is comfortably legible and keeps the mark's character, where the small cut's
    heavier blades make it read as a blunter shape that is not quite the mark.

Run:  python frontend/scripts/gen_favicons.py
"""
from __future__ import annotations

import pathlib

from PIL import Image, ImageDraw

# ── the guide's geometry (section 01) ────────────────────────────────────────────────────────
ART = 64
CENTRE = ART / 2
BLADE_RADIUS = 22
SWEEP = 92                      # degrees of arc per blade
GAP_AXES = (90, 210, 330)       # the centre of each gap; 90 is top dead centre

STANDARD = {"weight": 8, "pupil": 6.5}
SMALL = {"weight": 10, "pupil": 8}      # section 02, at or below 24px
SMALL_AT = 24

# ── colour (section 06 / the knockout treatment, section 04) ─────────────────────────────────
CADET = "#3F6B66"
WHITE = "#FFFFFF"

# HOW BIG THE MARK IS ON ITS DISC, as a fraction of the canvas -- and why it is not one number.
#
# A tab icon and an app icon want different answers, so they get different answers:
#
#   * AT 16 AND 32 the icon is already at the edge of legibility, and clear space costs more
#     than it buys. Rendered and compared at 1:1, 0.72 lost the top gap entirely and merged the
#     pupil into the blades; 0.90 holds all three. A ring of ground still shows, which is what
#     keeps the mark from bleeding into the tab strip.
#   * AT 180 AND 512 the operating system masks and crops the tile -- iOS rounds it, Android may
#     circle it -- so the mark needs room at the edge that it will not get back.
MARK_FRACTION = {"tab": 0.90, "app": 0.78}
APP_ICON_AT = 180

SUPERSAMPLE = 16                # drawn large and reduced; PIL's own arc antialiasing is poor

SIZES = (16, 32, 180, 512)
OUT = pathlib.Path(__file__).resolve().parents[1] / "public" / "brand" / "logo"


def half_gap() -> float:
    """Half the angular width of one gap, derived so the three stay equal by construction:
    three blades of 92 leave 84 degrees, so each gap is 28 and a blade starts 14 past its axis."""
    return (360 - SWEEP * len(GAP_AXES)) / len(GAP_AXES) / 2


def in_gap(deg: float) -> bool:
    """True when `deg` falls inside a gap. in_gap(90) must hold: the guide lists a blade at top
    dead centre under misuse, and it is the one property arithmetic alone will not catch."""
    h = half_gap()
    d = deg % 360
    return any(abs(((d - axis + 540) % 360) - 180) < h for axis in GAP_AXES)


def _pil_arc(start_math: float, sweep: float) -> tuple[float, float]:
    """Guide angles to PIL angles.

    The guide is standard maths — Y up, counterclockwise from east. PIL measures clockwise from
    east with Y down, so a maths angle t is PIL angle -t, and a counterclockwise sweep becomes a
    clockwise one running from the far end back to the near one. Get this wrong and the mark
    mirrors: the gaps stay 120 degrees apart and every measurement still checks out, but a blade
    lands at top dead centre.
    """
    return (-(start_math + sweep)) % 360, (-start_math) % 360


def render(px: int):
    ss = px * SUPERSAMPLE
    img = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # the disc
    draw.ellipse((0, 0, ss - 1, ss - 1), fill=CADET)

    # Scale from the MARK's outer diameter rather than the artboard's, so the fraction above means
    # what it says -- and so the two cuts, which have different blade weights and therefore
    # different outer diameters at the same radius, come out the same visual size.
    frac = MARK_FRACTION["app" if px >= APP_ICON_AT else "tab"]
    mark_px = ss * frac / SUPERSAMPLE

    cut = SMALL if mark_px <= SMALL_AT else STANDARD
    unit = (ss * frac) / (2 * (BLADE_RADIUS + cut["weight"] / 2))

    cx = cy = ss / 2
    r = BLADE_RADIUS * unit
    width = max(1, round(cut["weight"] * unit))

    for axis in GAP_AXES:
        start, end = _pil_arc(axis + half_gap(), SWEEP)
        # PIL draws the thick arc with radial ends, which is the guide's butt terminal.
        draw.arc((cx - r, cy - r, cx + r, cy + r), start=start, end=end, fill=WHITE, width=width)

    pr = cut["pupil"] * unit
    draw.ellipse((cx - pr, cy - pr, cx + pr, cy + pr), fill=WHITE)

    return img.resize((px, px), Image.LANCZOS), cut, mark_px


def main() -> None:
    assert in_gap(90), "a blade sits at top dead centre -- the angle conversion is mirrored"
    assert not in_gap(150), "the gap is too wide"
    assert abs(half_gap() - 14) < 1e-9, f"gap geometry moved: {half_gap()}"

    OUT.mkdir(parents=True, exist_ok=True)
    for px in SIZES:
        img, cut, mark_px = render(px)
        path = OUT / f"favicon-{px}.png"
        img.save(path, "PNG", optimize=True)
        name = "small" if cut is SMALL else "standard"
        print(f"{path.name:20s} {px:4d}px  mark {mark_px:6.1f}px  {name} cut  "
              f"{path.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
