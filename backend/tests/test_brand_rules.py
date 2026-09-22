"""Brand rules that nothing but a test enforces.

The frontend has no test runner, so a convention that lives only in a comment survives exactly until
the next person tidies up. These run in the backend suite because that is the suite that runs: an
unusual home for assertions about .jsx, and better than not asserting them.

This file began as the operator console's greyscale guard. The greyscale console was replaced on
2026-09-17 by the Axcion-branded operator console (OPERATOR-CONSOLE.md), whose own rules live in
test_operator_console.py; what remains here are the brand rules for the product itself.
"""
import pytest


def test_the_hero_watermark_is_one_component_not_a_copied_treatment():
    """The mark reached two views out of fifteen, and the reason was mechanical rather than
    aesthetic: the treatment was written inline with its numbers spelled out, so every view built
    afterwards either reproduced them from memory or skipped it. The Ads hero did neither — it
    shipped an <img> at one customer's LOGOMARK, hardcoded, at a different opacity, tinted with a
    CSS invert filter.

    So the treatment lives in HeroMark and nowhere else. An inline copy is how this regresses.
    """
    import re
    from pathlib import Path

    src_dir = Path(__file__).resolve().parents[2] / "frontend" / "src"
    offenders = {}
    for path in sorted(src_dir.rglob("*.jsx")):
        if path.name == "Brand.jsx":
            continue                                  # where the component legitimately lives
        text = path.read_text(encoding="utf-8", errors="replace")
        # A signature positioned absolutely at low opacity IS the watermark, however it is spelled.
        for m in re.finditer(r"<(?:Spring|Brand)Signature[^>]*>", text, re.S):
            tag = m.group(0)
            if "position" in tag and "opacity" in tag:
                offenders.setdefault(path.name, []).append(tag[:70])
    assert not offenders, f"hero watermark written inline instead of using HeroMark: {offenders}"


def test_no_view_hardcodes_a_brand_asset_path():
    """A workspace's logo comes from its own configuration. `/brand/logo/...` inlined in a view is
    one customer's file rendered to everybody — which is exactly what the Ads hero did."""
    import re
    from pathlib import Path

    src_dir = Path(__file__).resolve().parents[2] / "frontend" / "src"
    offenders = {}
    for path in sorted(src_dir.rglob("*.js*")):
        text = path.read_text(encoding="utf-8", errors="replace")
        code = "\n".join(l for l in text.splitlines() if not l.strip().startswith(("*", "//")))
        for m in re.finditer(r'["\'](/brand/logo/[^"\']+)["\']', code):
            offenders.setdefault(path.name, []).append(m.group(1))
    assert not offenders, f"a brand asset is hardcoded into a view: {offenders}"


def test_type_reaches_the_product_through_variables_not_literals():
    """The typeface picker set --font-display and --font-text, and NOTHING read them.

    714 components named the family inline instead, so a workspace could choose a pairing, see it
    saved, and watch nothing change. A setting that persists and does nothing is worse than one
    that is missing: the product tells you it worked.

    Two exemptions, both deliberate:
      * Monospace stacks. An API key or a content hash must stay monospaced whatever a workspace
        picks — the alignment IS the information.
      * Axcion's own brand module. brand/axcion.jsx names Axcion's type explicitly: the platform's
        identity must not drift toward whichever workspace was configured last. Axcion's own
        surfaces (the marketing site, the operator console) take it from there as TYPE.*.
    """
    import re
    from pathlib import Path

    src_dir = Path(__file__).resolve().parents[2] / "frontend" / "src"
    exempt = {"typefaces.js", "palette.js", "axcion.jsx"}
    offenders = {}
    for path in sorted(src_dir.rglob("*.js*")):
        if path.name in exempt or "sample" in path.name.lower():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        code = "\n".join(l for l in text.splitlines() if not l.strip().startswith(("*", "//")))
        # A brand family named directly rather than through the variable.
        hits = re.findall(r'(?:fontFamily:\s*|font-family:\s*|=\s*)["\']?'
                          r'(Inter|Poppins|Archivo|Space Grotesk|Instrument Sans|Fraunces)[,"\']',
                          code)
        if hits:
            offenders[path.name] = sorted(set(hits))
    assert not offenders, f"font families named directly instead of via --font-*: {offenders}"


def test_form_controls_inherit_the_workspace_typeface():
    """Browsers give buttons and inputs their own font rather than inheriting one, so every button
    in the app rendered in the UA default while the text beside it followed the workspace. One
    line in index.html, and it moved coverage from 58% of rendered elements to 99%.

    Deliberately narrow. This is not a global reset — there is no box-sizing reset in this app and
    adding one would move a great deal of layout.
    """
    from pathlib import Path

    html = (Path(__file__).resolve().parents[2] / "frontend" / "index.html").read_text(
        encoding="utf-8")
    assert "button, input, select, textarea { font-family: inherit; }" in html
    # Checked against the CSS RULES, not the file text — the comment above the rule explains why
    # there is no box-sizing reset, and a naive substring search matches its own explanation.
    # Checked against the CSS RULES rather than the file text: the comment above the rule
    # explains why there is no box-sizing reset, and a naive substring search matches its own
    # explanation. That is exactly what happened when this was first written.
    rules = [l for l in html.splitlines() if not l.strip().startswith(("*", "/*", "//"))]
    assert not any("box-sizing:" in l for l in rules), (
        "a box-sizing reset would move layout across the app")


def test_the_brand_payload_carries_the_typeface_name():
    """brand() drops every key not declared in BRAND_DEFAULTS, and `typeface` was not declared.

    So a workspace's stored pairing never reached the browser. The SPA fell back to the platform
    pairing, FETCHED the platform's fonts, and a second call then overwrote the variables with the
    workspace's legacy stacks — which named Poppins while nothing had downloaded Poppins. Every
    surface rendered in a generic sans while the CSS said otherwise.
    """
    from app.services.roles import BRAND_DEFAULTS, brand

    assert "typeface" in BRAND_DEFAULTS, "the pairing name will be silently dropped again"

    class _T:
        name = "Acme"
        config = {"brand": {"typeface": "classic",
                            "type": {"display": "Poppins,sans-serif"}}}

    assert brand(_T())["typeface"] == "classic"


def test_only_one_place_applies_type():
    """Two callers used to set it: applyBrand resolved a pairing and fetched its fonts, then
    applyType(brand.type) overwrote the variables with a stack whose fonts nobody had requested.
    Naming a family and loading it are different jobs, and the second caller did only the first.
    """
    import re
    from pathlib import Path

    src_dir = Path(__file__).resolve().parents[2] / "frontend" / "src"
    offenders = {}
    for path in sorted(src_dir.rglob("*.js*")):
        if path.name in {"palette.js", "Appearance.jsx"}:
            continue                     # where it legitimately lives, and the live preview
        text = path.read_text(encoding="utf-8", errors="replace")
        code = "\n".join(l for l in text.splitlines() if not l.strip().startswith(("*", "//")))
        if re.search(r"\bapplyType\s*\(", code):
            offenders[path.name] = True
    assert not offenders, f"applyType called outside applyBrand: {sorted(offenders)}"


def test_the_brand_payload_carries_everything_the_browser_needs_to_render_it():
    """brand() drops every key not declared in BRAND_DEFAULTS, silently. That has now cost two
    separate outages: `typeface` went missing, so fonts were named and never fetched; `seeds`
    went missing, so a workspace with no explicit palette had nothing to derive from and rendered
    in the platform's colours while its own sat in the database.

    The list is asserted rather than trusted, because the failure mode is a key quietly absent
    from a dict — which nothing else in the system will ever complain about.
    """
    from app.services.roles import BRAND_DEFAULTS, brand

    for key in ("palette", "seeds", "typeface", "type", "logo", "logomark",
                "hero_image", "photo", "product_name", "hero_plates"):
        assert key in BRAND_DEFAULTS, f"{key} will be dropped on the way to the browser"

    class _T:
        name = "Acme"
        config = {"brand": {"palette": {"ink": "#111111"}, "seeds": {"brand": "#222222"},
                            "typeface": "classic"}}

    out = brand(_T())
    assert out["palette"] == {"ink": "#111111"}
    assert out["seeds"] == {"brand": "#222222"}
    assert out["typeface"] == "classic"


def test_every_brand_key_the_browser_reads_is_declared_on_the_server():
    """The list in the test above is written from the keys that have already broken -- which is
    exactly why it did not contain `hero_plates` until `hero_plates` broke, three keys and three
    incidents into the same defect.

    This asks the other side instead. Brand.jsx reads the payload through a single module-level
    holder, so every key the browser depends on appears as `_brand.<name>`; if one of those is not
    declared in BRAND_DEFAULTS it is dropped in transit and the feature silently renders its
    fallback. Adding a reader to the SPA now fails here rather than in production.
    """
    import re
    from pathlib import Path

    from app.services.roles import BRAND_DEFAULTS

    src = Path(__file__).resolve().parents[2] / "frontend" / "src" / "Brand.jsx"
    if not src.exists():                       # backend-only checkout
        return

    read = set(re.findall(r"_brand\.([a-z_][a-z0-9_]*)", src.read_text(encoding="utf-8")))
    # display_name is synthesised by brand() from the tenant row rather than stored in config.
    missing = sorted(read - set(BRAND_DEFAULTS) - {"display_name"})
    assert not missing, f"Brand.jsx reads {missing}, which brand() will drop on the way out"


def test_saving_a_typeface_does_not_discard_a_palette():
    """The appearance endpoint popped `palette` on EVERY save, so a workspace changing its font
    gave up thirty hand-built colours. It is the second way that palette has been destroyed, and
    both times the save did not ask which setting had actually moved."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app" / "routers" / "users.py").read_text(
        encoding="utf-8")
    body = src[src.index("async def set_appearance"):]
    body = body[: body.index("@router.")]
    assert "seeds_changed" in body, "the save no longer distinguishes what changed"
    idx = body.index('brand.pop("palette", None)')
    guard = body[max(0, idx - 200): idx]
    assert "if seeds_changed:" in guard, "palette is popped without checking the seeds moved"


def test_no_module_names_a_brand_plate_file():
    """The hero band's artwork is a per-workspace SETTING, so no module may name a file.

    Four stylesheets did: Financials, the Forum's beCollective card, LaunchSection and the Ads
    tokens each wrote `/brand/RibbedGradient_*.jpg` into a rule. Migration 0050 exists to unpin
    exactly that artwork from workspaces which do not own it, and it could not reach any of these
    -- they are in the JavaScript bundle every workspace downloads, so every workspace displayed
    one customer's licensed gradient no matter what its own brand said.

    Excluded: the migration that seeds the paths (it is configuration, and it is server-side) and
    the bokeh, which is Axcion's own and is the platform default by design.
    """
    import re
    from pathlib import Path

    src_dir = Path(__file__).resolve().parents[2] / "frontend" / "src"
    if not src_dir.exists():
        return

    offenders = {}
    for path in sorted(src_dir.rglob("*.js*")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("*", "//", "/*")):
                continue                       # the comments explaining why it is gone
            if re.search(r"RibbedGradient_\w+\.(jpg|png|svg)", line):
                offenders[f"{path.name}:{n}"] = stripped[:90]
    assert not offenders, f"a brand plate is named in the bundle: {offenders}"


def test_every_hero_band_asks_for_its_surface_rather_than_drawing_one():
    """Eleven panels wear the hero band. They had five different surfaces between them.

    Three called ribbedHero. Four wrote a customer's file path into a rule. Two drew a pinstripe
    with repeating-linear-gradient -- a 13px one in Books, a 118deg one in the Forum -- imitating
    the ribbed gradient rather than asking for it. One painted a flat fill.

    A screenshot of six tabs side by side is what exposed it, which is a slow way to find out. So
    the imitations are asserted against here: a repeating-linear-gradient in this codebase is
    almost always somebody rebuilding the plate by hand.
    """
    import re
    from pathlib import Path

    src_dir = Path(__file__).resolve().parents[2] / "frontend" / "src"
    if not src_dir.exists():
        return

    offenders = {}
    for path in sorted(src_dir.rglob("*.js*")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("*", "//", "/*")):
                continue
            if re.search(r"repeating-linear-gradient", line):
                offenders[f"{path.name}:{n}"] = stripped[:90]
    assert not offenders, f"a hero surface is being drawn by hand: {offenders}"


def test_every_platform_module_has_a_mark():
    """The rail shows a module's ICON and a business's COLOUR, and it decides which by asking
    productIcons for the key. A module with no entry there silently falls back to the dot, so it
    looks like one of the customer's businesses instead of one of our products -- the failure is
    a missing dictionary entry again, and again nothing complains.

    The modules are named on the server: plans.py gates them, so plans.py is the source. `ads` is
    the exception -- it is a rail module but not plan-gated -- and is listed rather than derived,
    because a test that silently skipped it would be no test at all.
    """
    import re
    from pathlib import Path

    from app.plans import _FEATURE_TABS

    src = Path(__file__).resolve().parents[2] / "frontend" / "src" / "brand" / "productIcons.jsx"
    if not src.exists():
        return

    text = src.read_text(encoding="utf-8")
    block = text[text.index("const BY_MODULE = {"):]
    block = block[: block.index("}")]
    has_mark = set(re.findall(r"^\s*([a-z_]+)\s*:", block, re.M))

    expected = set(_FEATURE_TABS) | {"ads"}
    missing = sorted(expected - has_mark)
    assert not missing, f"platform module(s) with no mark, will render as a business dot: {missing}"

    # And the reverse: a mark for a key the server does not serve is a rename nobody finished.
    stale = sorted(has_mark - expected)
    assert not stale, f"productIcons maps {stale}, which is not a module the server knows about"


def test_the_web_brand_assets_still_come_from_the_delivered_masters():
    """The mark used to exist twice — drawn in axcion.jsx for the screen and again in
    gen_favicons.py for the PNGs — and the guard here held those two drawings to each other. The
    designed mark landed on 2026-09-22 and there is nothing left to draw: brand-src/axcion/ is the
    delivery and scripts/brand_assets.py resizes it.

    The drift it guarded against survived the change, though, in a new form. The web assets are
    generated once and COMMITTED, so a second delivery that lands in brand-src without anybody
    re-running the script leaves the product showing the previous mark with nothing to notice it.
    That is the same silent failure as before, one step upstream.

    Compared as pixels rather than bytes: the emitted file is re-encoded by whatever Pillow the
    machine has, so two correct runs do not produce identical files.
    """
    from pathlib import Path

    from PIL import Image

    root = Path(__file__).resolve().parents[2] / "frontend"
    if not root.exists():
        return                                     # backend-only checkout

    # Asserted, not skipped. .gitignore excludes frontend/brand-src and re-includes this one
    # directory precisely so this comparison has something to run against; if the delivery is
    # absent the test would otherwise pass while checking nothing, which is the failure mode the
    # whole file exists to avoid.
    src = root / "brand-src" / "axcion"
    assert src.exists(), "frontend/brand-src/axcion is missing -- it is committed on purpose"

    def sig(path):
        """A 16x16 RGBA thumbnail: enough to tell two artworks apart, coarse enough that a
        resampler's edge pixels do not read as a difference."""
        with Image.open(path) as im:
            return list(im.convert("RGBA").resize((16, 16), Image.LANCZOS).getdata())

    # Copied byte for byte, so held to the byte. These are the sizes the designer drew
    # separately (§12 of the brief: a favicon is not the master shrunk), and resampling one
    # would quietly throw that work away.
    import hashlib

    for master, served in (("AXCION-favicon-16.png", "favicon-16.png"),
                           ("AXCION-favicon-32.png", "favicon-32.png"),
                           ("AXCION-favicon-64.png", "favicon-64.png"),
                           ("AXCION-appicon-ink-180.png", "appicon-180.png")):
        out = root / "public" / "brand" / "axcion" / served
        assert out.exists(), f"{served} is missing -- run scripts/brand_assets.py"
        assert hashlib.sha256(out.read_bytes()).hexdigest() == \
               hashlib.sha256((src / master).read_bytes()).hexdigest(), \
               f"{served} is not {master}: re-run scripts/brand_assets.py"

    # Resized, so held to the artwork instead.
    for master, bundled in (("AXCION-mark-primary.png", "mark-primary.png"),
                            ("AXCION-mark-reversed.png", "mark-reversed.png"),
                            ("AXCION-mark-white.png", "mark-white.png"),
                            ("AXCION-mark-ink.png", "mark-ink.png"),
                            ("AXCION-mark-cadet.png", "mark-cadet.png"),
                            ("AXCION-lockup-horizontal-primary.png", "lockup-primary.png"),
                            ("AXCION-lockup-horizontal-reversed.png", "lockup-reversed.png"),
                            ("AXCION-lockup-horizontal-white.png", "lockup-white.png"),
                            ("AXCION-lockup-horizontal-ink.png", "lockup-ink.png")):
        out = root / "src" / "brand" / "axcion" / bundled
        assert out.exists(), f"{bundled} is missing -- run scripts/brand_assets.py"
        a, b = sig(src / master), sig(out)
        drift = sum(abs(p - q) for pa, pb in zip(a, b) for p, q in zip(pa, pb)) / (16 * 16 * 4)
        assert drift < 10, (f"{bundled} does not match {master} (mean channel drift {drift:.1f}) "
                            f"-- re-run scripts/brand_assets.py")


def test_every_html_entry_point_ships_the_same_icons():
    """Three entry points, and two of them linked only the 32 -- so the console and the intranet
    had no 16px icon and no touch icon, and a phone bookmarking either got a scaled guess.

    Also asserts the files exist. A favicon that 404s does not fall back to anything; the browser
    just shows its own placeholder, and nobody files a bug about a tab icon.

    They live under /brand/axcion/ rather than /brand/logo/, which is not tidying: /brand/logo/ is
    the folder a WORKSPACE's own logo goes in, and the test two above this one exists to stop one
    customer's file being rendered to everybody. Axcion's own identity does not belong in it.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "frontend"
    if not root.exists():
        return

    expected = ("favicon-64.png", "favicon-32.png", "favicon-16.png", "appicon-180.png")
    for name in expected:
        assert (root / "public" / "brand" / "axcion" / name).exists(), f"{name} is missing"

    for page in ("index.html", "console/index.html", "intranet/index.html",
                 "marketing/index.html", "operator/index.html"):
        path = root / page
        if not path.exists():
            continue
        html = path.read_text(encoding="utf-8")
        missing = [n for n in expected if n not in html]
        assert not missing, f"{page} does not link {missing}"
