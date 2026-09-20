"""A render must not wait on a query it deliberately switched off.

FOUND IN TWO SCREENS AT ONCE, both shipped, both invisible to review. Training and the SOP Library
each had:

    const isNew = selectedId === NEW_ID || !selectedId;
    const q = useThing(isNew ? "" : selectedId, Boolean(selectedId && !isNew));   // disabled
    ...
    {(isNew || detail) && !q.isPending && !q.error ? <Detail .../> : null}

In React Query v5 `isPending` means "no data yet", and a query with `enabled: false` will never
have any -- so it is pending FOREVER. The guard therefore evaluated false on exactly the branch it
was meant to allow, and the new-course / new-SOP form could never render: clicking "New Course"
updated the state and painted nothing at all. Reported as "when I click New Course nothing
happens", and from the outside indistinguishable from a button with no handler.

It survives review because every part of it reads correctly. "Don't render the detail pane while
its query is loading" is the right instinct; it is only wrong when the query is one you turned off
yourself, and that fact sits three lines away.

The precise shape is REQUIRING a query to be ready (`!someQuery.isPending`) in an expression whose
whole point is that `isNew` should let it through. Note how narrow that has to be:

    {(isNew || detail) && !courseQuery.isPending ...}   BAD  -- isNew is allowed through, but the
                                                                disabled query blocks it anyway
    {courseQuery.isPending && !isNew ? <Loading/> ...}  FINE -- shows a spinner only when NOT new,
                                                                which is exactly when it is enabled

Both mention isNew and isPending, and only one is wrong, so "they appear together" is not the
rule -- the first version of this test used it and failed on the correct line, which is how tests
get deleted rather than fixed. The rule is the combination of a NON-NEGATED `isNew` with a
NEGATED `isPending`.

It lives in the backend suite because that is the suite that runs -- same reasoning as
test_frontend_event_after_await.
"""
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"

pytestmark = pytest.mark.skipif(not SRC.exists(), reason="frontend not present")

# Per LINE, not per braced expression. The real gate opens `{(isNew || detail) && ... ? (` and
# closes many lines later, so a regex wanting a matching `}` on the same line matched nothing at
# all -- it passed on the very code it was written for, which is worse than not having it.
#
# "this query must be ready before we render", the half that can never come true:
NEEDS_READY = re.compile(r"!\s*(\w+)\.isPending\b")
# `isNew` NOT preceded by `!` -- the branch that is supposed to be allowed through. `!isNew` is
# the correct usage and must not match.
ALLOWS_NEW = re.compile(r"(?<![!\w])isNew\b")


def _jsx() -> list[Path]:
    return sorted(SRC.rglob("*.jsx"))


def _code_only(text: str) -> str:
    """Comments blanked, line numbers preserved.

    Not fussiness: the note above the fixed line in Training.jsx quotes the broken expression to
    explain it, and a guard that trips on the comment explaining the guard is one somebody deletes
    rather than fixes. Newlines are kept so reported line numbers still point at the file.
    """
    def blank(m: re.Match) -> str:
        return "\n" * m.group(0).count("\n")

    text = re.sub(r"/\*.*?\*/", blank, text, flags=re.S)
    return re.sub(r"(?<!:)//[^\n]*", "", text)      # (?<!:) so https:// survives


def test_no_render_gate_waits_on_a_query_that_isnew_disables():
    offenders = []
    for path in _jsx():
        code = _code_only(path.read_text(encoding="utf-8"))
        for n, line in enumerate(code.splitlines(), 1):
            # A mutation's isPending is fine and common -- `busy={save.isPending}` is exactly
            # right. Only a QUERY's is the one isNew disables.
            waits_on = [q for q in NEEDS_READY.findall(line) if q.lower().endswith("query")]
            if waits_on and ALLOWS_NEW.search(line):
                offenders.append(f"{path.relative_to(SRC)}:{n}: {line.strip()[:110]}")

    assert not offenders, (
        "a render gate waits on a query that is disabled whenever isNew is true, so it can never "
        "become ready and the new-item form will never appear:\n  " + "\n  ".join(offenders))


# --- and the same fault wearing different clothes -------------------------------------------
#
# The test above encodes ONE SHAPE: a render gate. Connor then reported the SOP Library as "there
# is nothing to link, upload, write out, or save", and the cause was the same disabled query three
# lines BELOW the comment warning about it -- folded into `busy`, so every button in the detail
# form, including Create SOP, was disabled forever:
#
#     const versionsQuery = useSopVersions(detail?.id || "", Boolean(detail?.id));   // disabled
#     ...
#     busy={busy || versionsQuery.isPending}
#
# Only a workspace with NO procedures can hit it, so it survived every test and every click-through
# that started from an existing one -- which is every new workspace, on the screen whose job is to
# create the first thing.
#
# So the rule here is not "and also check busy=". It is about the flag itself: a query that can be
# switched off must never be asked `isPending`, because in v5 that only means "no data" and a
# disabled query has none, forever. `isLoading` (`isPending && isFetching`) is the honest question
# and is false while the query is off, so the consumer cannot get it wrong no matter what it feeds.
#
# The hook list is DERIVED from the query modules, not kept here: a new conditional hook is covered
# the day it is written, and this test cannot go stale the way a list of screens does.

ASSIGNED = re.compile(r"(?:const|let)\s+(\w+)\s*=\s*(use\w+)\(")
DESTRUCTURED = re.compile(r"(?:const|let)\s*\{([^}]*)\}\s*=\s*(use\w+)\(")


def _switchable_hooks() -> dict[str, str]:
    """Every exported hook whose useQuery takes an `enabled:` option."""
    hooks = {}
    for mod in sorted(SRC.rglob("*.js")):
        text = _code_only(mod.read_text(encoding="utf-8"))
        for m in re.finditer(r"export function (use\w+)\(", text):
            nxt = text.find("\nexport function ", m.end())
            body = text[m.end(): nxt if nxt != -1 else len(text)]
            if "useQuery(" in body and re.search(r"\benabled:", body):
                hooks[m.group(1)] = str(mod.relative_to(SRC))
    return hooks


def test_a_query_that_can_be_switched_off_is_never_asked_ispending():
    hooks = _switchable_hooks()
    assert hooks, "found no conditionally-enabled query hooks -- the scan broke, not the code"

    offenders = []
    for path in _jsx():
        code = _code_only(path.read_text(encoding="utf-8"))
        owners = {m.group(1): m.group(2) for m in ASSIGNED.finditer(code) if m.group(2) in hooks}
        for names, hook in ((m.group(1), m.group(2)) for m in DESTRUCTURED.finditer(code)):
            if hook in hooks and re.search(r"\bisPending\b", names):
                offenders.append(f"{path.relative_to(SRC)}: destructures isPending from {hook}")
        if not owners:
            continue
        for n, line in enumerate(code.splitlines(), 1):
            for var, hook in owners.items():
                if re.search(rf"\b{var}\.isPending\b", line):
                    offenders.append(f"{path.relative_to(SRC)}:{n}: [{hook}] {line.strip()[:100]}")

    assert not offenders, (
        "these read `isPending` on a query that can be disabled, and a disabled query is pending "
        "forever in React Query v5 -- whatever it feeds (a render gate, a spinner, a `busy` or "
        "`disabled` prop) is stuck in that state for as long as the query is off. Ask "
        "`isLoading` instead:\n  " + "\n  ".join(offenders))
