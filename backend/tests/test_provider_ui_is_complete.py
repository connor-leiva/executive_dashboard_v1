"""Every provider the API can return has a working card AND a working button.

THREE TIMES IN ONE WEEK this exact thing shipped:

  1. Sisu and Follow Up Boss were added to the backend allow-list with no connect form. The
     buttons opened nothing. It reached production and read as an outage.
  2. meta_ads was added to the source list with no DESC entry. Calling the missing description
     threw during render and took the whole Settings page down - a white screen.
  3. meta_ads was added with no modal branch. The ternary chain fell through to the GoHighLevel
     form, so a Meta button opened a GoHighLevel dialog.

Every one passed every test, because the tests call the API directly and never render anything.
The API half and the UI half live in different languages and different files, and nothing tied
them together.

This ties them together. It reads the backend's list of connectable providers and asserts the
frontend can actually SHOW each one and OPEN each one. It is a static check over .jsx from a
Python suite, which is unusual - and the alternative is finding out from a customer for the
fourth time.
"""
import re
from pathlib import Path

import pytest

SETTINGS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "Settings.jsx"

pytestmark = pytest.mark.skipif(not SETTINGS.exists(), reason="frontend not present")


def _settings_source() -> str:
    return SETTINGS.read_text(encoding="utf-8")


def _connectable() -> set[str]:
    """The providers the API offers a Connect button for."""
    from app.services.integrations_view import CONNECTABLE_KIND
    return set(CONNECTABLE_KIND)


def _all_offered() -> set[str]:
    """Every provider the integrations payload can contain, connectable or not."""
    from app.services.integrations_view import ORDER
    return set(ORDER)


def test_every_offered_provider_has_a_description():
    """A provider with no DESC entry white-screened the entire Settings page: decorate() assigned
    `desc: DESC[provider]`, the card called `s.desc(s)`, and calling undefined threw during
    render. There is a fallback now, but a provider shipping without its own copy is still a
    half-finished feature."""
    src = _settings_source()
    block = src[src.index("const DESC = {"):]
    block = block[: block.index("\n};")]
    described = set(re.findall(r"^\s{2}(\w+):", block, re.M))
    missing = _all_offered() - described - {"qbo"}      # qbo's copy lives on its own form
    assert not missing, (
        f"these providers are returned by the API with no description in Settings.jsx: "
        f"{sorted(missing)} - the card renders with a blank subtitle")


def test_every_offered_provider_has_a_monogram():
    src = _settings_source()
    block = src[src.index("const MONO = {"):]
    block = block[: block.index("};")]
    known = set(re.findall(r"(\w+):", block))
    missing = _all_offered() - known
    assert not missing, f"no monogram for: {sorted(missing)}"


def test_every_connectable_provider_can_actually_open_its_form():
    """THE DEAD-BUTTON TEST.

    The modal is a ternary chain on connecting.provider with a final fall-through. A provider
    with no branch does not get "nothing" - it gets whatever the fall-through happens to be,
    which is how a Meta Ads button opened a GoHighLevel dialog. Both failures are silent: no
    error, no console warning, just the wrong thing or no thing.
    """
    src = _settings_source()
    dispatched = set(re.findall(r'connecting\.provider === "(\w+)"', src))
    missing = _connectable() - dispatched
    assert not missing, (
        f"these providers have a Connect button and NO form to open: {sorted(missing)} - the "
        f"click falls through to whichever form ends the chain")


def test_every_connectable_provider_is_reachable_from_connect_source():
    """connectSource decides what the button does. A provider it cannot construct a modal payload
    for is a button that does nothing at all - which is precisely how Sisu and Follow Up Boss
    shipped."""
    src = _settings_source()
    fn = src[src.index("function connectSource("):]
    fn = fn[: fn.index("\n  }")]
    # The single generic branch covers every provider by passing s.provider straight through.
    generic = "provider: s.provider" in fn
    assert generic or not (_connectable() - set(re.findall(r'=== "(\w+)"', fn))), (
        "connectSource neither handles providers generically nor names them all")


def test_the_backend_allow_list_and_the_offered_list_agree():
    """A provider the settings page offers but create_integration refuses returns 400 and the
    form fails with no explanation - the shape of the original Sisu and FUB bug, one layer down."""
    routers = Path(__file__).resolve().parents[1] / "app" / "routers" / "integrations.py"
    src = routers.read_text(encoding="utf-8")
    block = src[src.index("if provider not in ("):]
    block = block[: block.index("):")]
    allowed = set(re.findall(r'"(\w+)"', block))
    offered = _connectable()
    missing = offered - allowed
    assert not missing, (
        f"the settings page offers {sorted(missing)} but create_integration rejects them with "
        f"400 - the connect form will fail with no useful message")
