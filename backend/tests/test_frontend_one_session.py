"""There is ONE browser session for a workspace, so there is one place that stores it.

THE BUG THIS EXISTS FOR. The dashboard, the portal and the admin console are three bundles served
from the same host under the same key, which makes them one sign-in whether or not anybody wrote
that down. They did not behave like it, because the console kept its own copy of the rule:

    dashboard:  sessionStorage.getItem(TOKEN_KEY) || localStorage.getItem(TOKEN_KEY)
    console:    localStorage.getItem(TOKEN_KEY)

Three real consequences, none of which look like a bug in either file on its own. Signing in to
the dashboard WITHOUT "remember me" put the token in sessionStorage, so opening /console asked you
to sign in again to the same account on the same host. The console's sign-out cleared only
localStorage, so a dashboard session SURVIVED signing out of the console. And the console always
wrote to localStorage while never asking the server to remember, so the browser kept a token past
the tab closing that the server had given a short life -- the storage promise and the expiry
disagreeing, on exactly the shared machine the promise is for.

So the rule is not "read both stores", which is the fix rather than the property. It is that the
KEY and the stores are reachable from one module, because every one of those three came from a
second implementation drifting from the first -- and api.js already carried a comment saying two
components had done this before. The second app did it again anyway.

The OPERATOR console is deliberately exempt: it has its own key (`cc_platform_token`) because a
platform session and a tenant session must not be able to stand in for each other -- see the
PlatformUser model. A test that folded them together would be arguing for the opposite of what
that separation is for, so it is named here rather than matched by accident.
"""
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
OWNER = "session.js"                       # the one module allowed to name the key or the stores
TENANT_KEY = "cc_token"

pytestmark = pytest.mark.skipif(not SRC.exists(), reason="frontend not present")


def _files():
    return sorted(p for p in list(SRC.rglob("*.js")) + list(SRC.rglob("*.jsx"))
                  if "node_modules" not in p.parts)


def _code(path: Path) -> str:
    """Comments blanked, newlines kept, so a note ABOUT the rule cannot trip the rule."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return re.sub(r"(?<!:)//[^\n]*", "", text)


def test_only_one_module_names_the_session_key():
    offenders = [str(p.relative_to(SRC)) for p in _files()
                 if p.name != OWNER and TENANT_KEY in _code(p)]
    assert not offenders, (
        f'"{TENANT_KEY}" is spelled outside {OWNER}: ' + ", ".join(offenders) +
        f". Import it from session.js instead — a second literal is how the console came to have "
        "its own session rule.")


def test_nothing_else_reads_or_writes_the_session_store():
    """Catches the shape rather than the key: `localStorage.getItem(TOKEN_KEY)` names no literal,
    and that is precisely what the console had."""
    touches = re.compile(r"\b(?:local|session)Storage\s*\.\s*(?:get|set|remove)Item\s*\(\s*TOKEN_KEY")
    offenders = []
    for path in _files():
        if path.name == OWNER:
            continue
        for n, line in enumerate(_code(path).splitlines(), 1):
            if touches.search(line):
                offenders.append(f"{path.relative_to(SRC)}:{n}: {line.strip()[:90]}")
    assert not offenders, (
        "these reach into the session store directly instead of going through session.js:\n  "
        + "\n  ".join(offenders))


def test_the_operator_realm_keeps_its_own_key():
    """Not an oversight — a guard. A platform session and a tenant session must not be able to
    stand in for one another, so folding these together would be a security regression, and this
    says so out loud in the place somebody would otherwise 'tidy' it."""
    operator = (SRC / "operator" / "api.js")
    assert operator.exists(), "the operator console moved; re-point this test"
    code = _code(operator)
    assert "cc_platform_token" in code
    assert TENANT_KEY not in code, "the operator console must never read a tenant session"
