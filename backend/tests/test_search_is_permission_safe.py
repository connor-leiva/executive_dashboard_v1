"""Portal search must only ever find what the server already sent.

Search is a READ PATH, and read paths in this product are permission-gated: the SOP file
endpoint refuses a role whose sop_library is None, and filtering the listing alone would have
been cosmetic because the ids are guessable. Search carries exactly the same hazard in a friendlier
shape -- a result list naming documents you cannot open is a permission leak wearing a search box.

The portal searches the config payload the server has already filtered by capability and role
audience, in the browser, with no endpoint of its own. That makes the leak impossible BY
CONSTRUCTION rather than by remembering to re-apply the rules: if the server did not send it, it
cannot be found.

That property holds only while search.js keeps reading its argument and nothing else. The day
somebody adds a fetch to it "so search can see more", the guarantee is gone and nothing else in
the codebase would notice -- so this asserts it. It lives in the backend suite because that is the
suite that runs.
"""
import re
from pathlib import Path

import pytest

SEARCH = Path(__file__).resolve().parents[2] / "frontend" / "src" / "intranet" / "search.js"

pytestmark = pytest.mark.skipif(not SEARCH.exists(), reason="frontend not present")

# Anything that could reach past the filtered payload for more data.
FETCHERS = ("fetch(", "XMLHttpRequest", "axios", "getJSON", "getBlob", "postJSON", "EventSource",
            "navigator.sendBeacon", "import(")


def test_search_never_fetches_anything():
    text = SEARCH.read_text(encoding="utf-8")
    # Comments explain the rule and may name the thing being forbidden, so they are not code.
    code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    code = re.sub(r"(?<!:)//[^\n]*", "", code)
    found = [f for f in FETCHERS if f in code]
    assert not found, (
        f"search.js reaches for data instead of searching the filtered payload: {found}. "
        "That reintroduces the permission question this design removes -- anything it fetches "
        "would need the capability matrix and every role audience re-applied to it.")


def test_search_reads_only_the_content_it_is_given():
    """It takes `content` as an argument. A module-level import of state, or a window global,
    would be a second source that nothing has filtered."""
    text = SEARCH.read_text(encoding="utf-8")
    code = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    code = re.sub(r"(?<!:)//[^\n]*", "", code)

    assert "export function search(content, query)" in code, (
        "the entry point no longer takes the payload as an argument")
    assert "window." not in code, "search.js reads a global instead of its argument"
    assert not re.search(r"^\s*import\s", code, re.M), (
        "search.js imports a module; it should be a pure function of the payload")


def test_every_searchable_type_comes_from_the_payload():
    """A new content type added to the payload should be searchable, and the only way it can be
    is by being read off `content` here. Named explicitly so a reviewer sees what is covered."""
    code = SEARCH.read_text(encoding="utf-8")
    for key in ("courses", "sops", "tool_groups", "directory", "wtd_lists", "pages"):
        assert f"c.{key}" in code, f"{key} is in the member payload but not searchable"
