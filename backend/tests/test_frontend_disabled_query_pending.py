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
