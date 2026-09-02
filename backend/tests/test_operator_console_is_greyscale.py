"""The operator console must stay greyscale, and nothing else enforces it.

The frontend has no test runner, so a convention that lives only in a comment is a convention
that survives exactly until the next person tidies up. This runs in the backend suite because
that is the suite that runs — an unusual home for an assertion about .jsx, and better than the
alternative of not asserting it.

Why it is worth a test at all: the greyscale is not decoration. The console and a customer's
dashboard are both dense tables of similar-looking numbers, and the console is the one whose
buttons suspend paying customers. The palette is the only thing on screen that tells an operator
which of the two they are looking at. A well-meaning brand pass that "makes the admin match the
product" would delete that signal and look like an improvement in the diff.
"""
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
THEME = SRC / "theme.js"
CONSOLE = SRC / "platform" / "PlatformConsole.jsx"

pytestmark = pytest.mark.skipif(not CONSOLE.exists(), reason="frontend not present")


def _ops_tokens() -> dict[str, str]:
    text = THEME.read_text(encoding="utf-8")
    block = text[text.index("export const OPS"):]
    block = block[: block.index("};")]
    return dict(re.findall(r"^\s{2}([a-zA-Z]+):\s*\"(#[0-9A-Fa-f]{6})\"", block, re.M))


def test_every_operator_token_is_an_equal_channel_grey():
    tokens = _ops_tokens()
    assert tokens, "OPS palette not found in theme.js"
    for name, hex_value in tokens.items():
        r, g, b = (int(hex_value[i:i + 2], 16) for i in (1, 3, 5))
        assert r == g == b, (
            f"OPS.{name} is {hex_value}, which has a hue. The operator console is greyscale on "
            f"purpose: it is what distinguishes the screen that suspends customers from the "
            f"screen that reports on them.")


def test_the_console_uses_no_colour_from_the_customer_palette():
    """T is Spring's brand — evergreen, parchment, poppy. Importing it here is the specific
    regression this guards: it would compile, look tidy, and quietly make the operator console
    indistinguishable from a customer's dashboard."""
    src = CONSOLE.read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("*"))
    assert not re.search(r"\bT\.[a-zA-Z]", code), "operator console references the customer palette"
    assert not re.search(r"import\s*\{[^}]*\bT\b[^}]*\}\s*from\s*[\"']\.\./theme", code)


def test_the_console_hardcodes_no_colours_of_its_own():
    """The repo's rule is that components take colour from theme.js and never inline it. A raw
    hex here would also sidestep the greyscale check above, which reads the palette, not the
    component."""
    src = CONSOLE.read_text(encoding="utf-8")
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("*"))
    found = re.findall(r"#[0-9A-Fa-f]{3,8}\b", code)
    assert not found, f"hardcoded colours in the operator console: {found}"


def test_the_console_is_reachable_only_on_the_operator_host():
    """It used to be a /platform route inside the customer bundle, which put the operator login
    on every customer's domain and shared an origin — so an operator signed into a customer
    dashboard in one tab and the console in another had both sessions on the same storage."""
    auth = (SRC / "auth.jsx").read_text(encoding="utf-8")
    assert "IS_OPERATOR_HOST" in auth
    assert '"/platform/*"' not in auth, "the console is routed on customer hosts again"
    assert "lazy(() => import(\"./platform/PlatformConsole.jsx\"))" in auth, (
        "a static import puts the operator console in every customer's bundle")


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
      * Acumyn's own surfaces. brand/acumyn.jsx and the operator console name Acumyn's type
        explicitly, for the same reason the console is greyscale: the platform's identity must not
        drift toward whichever workspace was configured last.
    """
    import re
    from pathlib import Path

    src_dir = Path(__file__).resolve().parents[2] / "frontend" / "src"
    exempt = {"typefaces.js", "palette.js", "acumyn.jsx", "PlatformConsole.jsx"}
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
