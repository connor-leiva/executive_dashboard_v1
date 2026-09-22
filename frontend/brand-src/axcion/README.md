# Axcion identity — delivered masters

These 22 files are the designed AXCION identity as delivered on **2026-09-22**, unmodified. They
are the source of every Axcion mark in the product; `frontend/scripts/brand_assets.py` resizes
them into `frontend/public/brand/axcion/`, which is what the apps actually load.

**Nothing here is regenerated, traced or recoloured.** The mark that shipped before this date was
*drawn* — `src/brand/axcion.jsx` built three arcs around a pupil from the identity guide's
construction numbers, because the guide existed and the mark did not. The designed mark is a
different shape (crossed tapered blades with a detached leaf) and it replaces that drawing
outright. If a size or a colourway is needed that is not here, it comes from the designer, not
from this repo.

## What is in the pack

| Group | Files | Notes |
| --- | --- | --- |
| Mark | `AXCION-mark-{primary,reversed,cadet,ink,white}.png` | 831×1024 — **not square**; the leaf hangs below the X |
| Wordmark | `AXCION-wordmark-{ink,white}.png` | 1527×328 |
| Horizontal lockup | `AXCION-lockup-horizontal-{primary,reversed,ink,white}.png` | 2255×605 |
| Stacked lockup | `AXCION-lockup-stacked-{primary,reversed}.png` | 1391×1153 |
| App icon | `AXCION-appicon-{cadet,light}-1024.png`, `AXCION-appicon-ink-{1024,512,192,180}.png` | rounded container |
| Favicon | `AXCION-favicon-{16,32,64}.png` | a separate micro mark, not the master shrunk |

All RGBA with real transparency. The colours are the existing palette exactly — Cadet `#3F6B66`,
Ink `#16201F`, Sage `#8FB3AE` — which is what the brief asked for ("inherits the established
Acumyn color palette"), so `CORE` in `src/brand/axcion.jsx` needed no change.

The favicon sizes are genuinely different artwork from each other: 16 is a solid cadet silhouette,
32 and 64 are the ink tile. That is the brief's instruction (§12, "Do not simply shrink the master
symbol to create the favicon") carried out, and it is why those files are copied byte for byte
rather than resampled.

## What is missing

The brief's deliverables list (§23) asks for **vector masters: AI, EPS, SVG**. The delivery is
PNG only. Consequences, in the order they will bite:

1. Every Axcion mark in the product is a raster `<img>` rather than an inline `<svg>`, so it
   cannot be recoloured at runtime — each treatment is its own file. That is fine while the
   approved treatments are the only ones used, and it is why `AxcionMark` no longer takes a
   `color` prop.
2. Print, engraving and embroidery (§14) have nothing to work from.
3. Any size beyond the delivered raster needs the designer.

Ask the designer for the vector masters and the clear-space/minimum-size sheet (§23) when
convenient. `brand_assets.py` is the one file that changes when they arrive.
