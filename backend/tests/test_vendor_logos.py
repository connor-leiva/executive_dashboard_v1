"""Tool tiles get a vendor logo from the tool's NAME, or keep their initials.

Nobody configures a logo: the console's Tool Launchpad collects a name and a URL, and a third
field for picking a logo out of a list only we can see is a field to get wrong. So the name has to
carry it, and the matching has to be conservative -- a wrong logo is worse than no logo, because
initials read as "we don't have one" and Canva's mark on an LMS reads as a fact.

The first version matched on substrings and gave "Canvas" Canva's logo. These are the cases that
came from fixing it, run against the real module rather than a copy of the rules.
"""
import json
import re
import subprocess
import shutil
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src" / "intranet"
MODULE = SRC / "vendor-logos.js"

pytestmark = pytest.mark.skipif(
    not MODULE.exists() or shutil.which("node") is None,
    reason="frontend or node not present")


def _resolve(names: list[str]) -> dict[str, str | None]:
    """Run the REAL matcher under node.

    The import lines are stripped and each logo replaced with its own filename, because Vite
    resolves `import x from "./a.png"` and node does not -- and reimplementing the rules in Python
    would be testing a copy, which is how a guard passes while the thing it guards is broken.
    """
    source = MODULE.read_text(encoding="utf-8")
    source = re.sub(r'^import\s+(\w+)\s+from\s+"([^"]+)";\s*$',
                    lambda m: f'const {m.group(1)} = "{Path(m.group(2)).name}";',
                    source, flags=re.M)
    script = source + "\nconsole.log(JSON.stringify(" + json.dumps(names) \
        + ".map((n) => [n, logoFor(n)])));\n"
    out = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return dict(json.loads(out.stdout))


def test_a_tool_named_exactly_for_its_product_gets_its_logo():
    got = _resolve(["Sunburst", "Sisu", "Slack", "Brivity", "Canva", "Skool", "SkySlope"])
    assert got["Sunburst"] == "sunburst.png"
    assert got["Sisu"] == "sisu.png"
    assert got["Slack"] == "slack.png"
    assert got["Canva"] == "canva.png"


def test_a_multi_word_product_matches_however_it_is_punctuated():
    """A workspace types "Follow Up Boss", "follow-up-boss" or "FollowUpBoss" and means one thing."""
    got = _resolve(["Follow Up Boss", "follow-up-boss", "FollowUpBoss", "Sky Slope"])
    assert set(got.values()) == {"follow-up-boss.png", "skyslope.png"}


def test_a_qualified_name_still_matches_its_product():
    """Real launchpads say "Sisu Dashboard" and "Slack #help", not the bare product name."""
    got = _resolve(["Sisu Dashboard", "Slack #help", "Brivity CRM"])
    assert got["Sisu Dashboard"] == "sisu.png"
    assert got["Slack #help"] == "slack.png"
    assert got["Brivity CRM"] == "brivity.png"


def test_canvas_is_not_canva():
    """The bug that prompted word-boundary matching. Canvas is an LMS a training-heavy team
    plausibly links to, and substring matching put a design tool's mark on it."""
    got = _resolve(["Canvas", "Canvas LMS", "canvas"])
    assert set(got.values()) == {None}, got


def test_the_longer_product_name_wins():
    """"eXp World Campus" must not be claimed by "eXp Enterprise", and "Sympli Mortgage" not by
    the bare "Sympli" -- which is why the list is sorted by length before matching."""
    got = _resolve(["eXp World Campus", "eXp Enterprise", "Sympli Mortgage", "Sympli"])
    assert got["eXp World Campus"] == "exp-world-campus.png"
    assert got["eXp Enterprise"] == "exp-enterprise.png"
    assert got["Sympli Mortgage"] == "sympli-mortgage.png"
    assert got["Sympli"] == "sympli-mortgage.png"


def test_an_unknown_tool_keeps_its_initials():
    """Eleven products out of everything a team might link to. A miss has to look deliberate."""
    got = _resolve(["Zillow", "Dotloop", "", "   ", "Our Own Portal"])
    assert set(got.values()) == {None}, got


def test_every_bundled_logo_is_reachable_by_its_own_name():
    """Derived from the files on disk, not a list written beside them -- a logo added to the folder
    and forgotten in the map is a file that ships and never renders."""
    files = sorted(p.stem for p in (SRC / "assets" / "logos").glob("*.png"))
    names = [f.replace("-", " ") for f in files]
    got = _resolve(names)
    missing = [n for n in names if got[n] is None]
    assert not missing, f"bundled logo(s) no name resolves to: {missing}"
