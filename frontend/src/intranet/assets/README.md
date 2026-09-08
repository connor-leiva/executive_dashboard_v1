# Third-party brand assets

Logos belonging to other companies go here as the files those companies publish — not as
something we redraw.

## sunburst-mark.svg (missing)

Sunburst's mark, used on the Sunburst page and the home banner. An earlier version of this
product drew the mark with `stroke-dasharray` from a screenshot. It was close and it was wrong:
the ring's gaps sat on the wrong axis, and the wordmark next to it was DM Sans where theirs is
custom lettering. A traced trademark is worse when it is nearly right than when it is obviously
wrong, because it ships looking legitimate.

Drop the real file here (SVG preferred — it is a logo, and it renders on a dark panel at several
sizes) and wire it in `IntranetApp.jsx`, where the lockup currently renders the name as text.

Colours sampled from their logo already live as tokens in `ui.css` (`--sun-accent`,
`--sun-panel`, `--sun-word`); those are fine to keep either way, but check them against the real
file when it arrives.
