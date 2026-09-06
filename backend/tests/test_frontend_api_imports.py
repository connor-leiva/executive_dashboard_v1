"""Every api.js helper a frontend module CALLS, it must also IMPORT.

Live production bug this exists to prevent (found 2026-09-06, shipped for weeks):
IntranetApp.jsx called `putJSON(...)` — the sole write path for Win the Day, Training,
Onboarding and SOP state — and never imported it. Four things conspired to hide it:

  * Vite/rollup does not fail, or even warn, on an unresolved free identifier. It emits the
    bare name and lets the browser deal with it.
  * The call sits behind `if (!API_BASE)`. In a dev build that constant folds to a localStorage
    branch and the call is DEAD-CODE-ELIMINATED, so no local build contains the bug at all.
    Only a build with VITE_API_BASE set — production — keeps it.
  * The ReferenceError is thrown while evaluating the call, so the trailing `.catch(() => {})`
    never sees it, and it happens inside a setTimeout, so nothing else does either.
  * There is no frontend test runner and no linter, so `no-undef` never ran.

The result was silent: no error a user would report, no failed request in the network tab. A
checkbox simply un-ticked itself on reload.

Derived, not listed: it reads api.js's real exports and each module's real import list, so a
helper added later is covered without anybody remembering this file exists.
"""
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"

pytestmark = pytest.mark.skipif(not SRC.exists(), reason="frontend not present")

EXPORT = re.compile(r"^export\s+(?:async\s+)?(?:function|const|let)\s+([A-Za-z_$][\w$]*)", re.M)
IMPORT = re.compile(r"import\s*\{([^}]*)\}\s*from\s*[\"']([^\"']*api\.js)[\"']")


def _binds_locally(text: str, name: str) -> bool:
    """Whether the module supplies this name itself.

    Both real false positives are here on purpose: `const [token, setToken] = useState()` is a
    destructured binding, and `export function loadBrandOnce(getJSON)` is a parameter. A checker
    that flagged those would be noise, and noise is how a guard gets deleted.
    """
    n = re.escape(name)
    return any(re.search(p, text) for p in (
        rf"(?:function|const|let|var|class)\s+{n}\b",          # plain declaration
        rf"(?:const|let|var)\s*[\[{{][^\]}}]*\b{n}\b",         # destructured binding
        rf"function\s*\w*\s*\([^)]*\b{n}\b",                   # function parameter
        rf"\(\s*[^)]*\b{n}\b[^)]*\)\s*=>",                     # arrow parameter
        rf"\b{n}\s*=>",                                        # bare single arrow parameter
    ))


def _missing(text: str, exports: set[str], imported: set[str]) -> list[str]:
    out = []
    for helper in sorted(exports - imported):
        called = re.search(rf"(?<![\w$.]){re.escape(helper)}\s*\(", text)
        if called and not _binds_locally(text, helper):
            out.append(helper)
    return out


def test_the_checker_actually_detects_the_bug_it_was_written_for():
    """A heuristic detector that quietly degrades into a no-op is worse than no test, because
    the suite still goes green. This pins both directions with synthetic source."""
    exports = {"getJSON", "putJSON", "setToken"}
    caught = 'import { getJSON } from "../api.js";\nputJSON(`/x`, {a: 1});'
    assert _missing(caught, exports, {"getJSON"}) == ["putJSON"]

    # ...and does not fire on the shapes that legitimately supply the name themselves.
    for benign in ('const [token, setToken] = useState("");\nsetToken(e.target.value);',
                   "export function loadBrandOnce(getJSON) { return getJSON('/me'); }",
                   "const putJSON = (p) => 1;\nputJSON('/x');",
                   "api.putJSON('/x');"):
        assert _missing(benign, exports, set()) == [], benign


def test_no_module_calls_an_api_helper_it_forgot_to_import():
    offenders = []
    for path in sorted(SRC.rglob("*.jsx")) + sorted(SRC.rglob("*.js")):
        text = path.read_text(encoding="utf-8")
        for names, spec in IMPORT.findall(text):
            target = (path.parent / spec).resolve()
            if not target.exists():
                continue
            exports = set(EXPORT.findall(target.read_text(encoding="utf-8")))
            imported = {n.split(" as ")[0].strip() for n in names.split(",") if n.strip()}
            for helper in _missing(text, exports, imported):
                offenders.append(f"{path.relative_to(SRC)} calls {helper}() "
                                 f"but never imports it from {spec}")
    assert not offenders, "\n".join(offenders)
