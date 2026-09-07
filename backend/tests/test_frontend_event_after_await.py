"""A synthetic event's currentTarget must not be read after an await.

FOUND TWICE, IN PRODUCTION, ON THE SAME DAY. React clears `currentTarget` on a synthetic event
once the handler yields, so reading it after an `await` is null. Both places did the same thing:

    await onUpload(...)
    event.currentTarget.reset()

and both threw "Cannot read properties of null (reading 'reset')" AFTER the upload had already
succeeded -- so the console painted a red error over work that had worked, and pointed the
operator at the wrong subsystem. The SOP version upload had no try/catch either, so there it
became an unhandled rejection.

The fix is one line in each case (capture the node before the await), which is exactly why it
will be written again. This is the guard.

It lives in the backend suite because that is the suite that runs -- an unusual home for an
assertion about .jsx, and better than the alternative of not asserting it. Same reasoning as
test_operator_console_is_greyscale.
"""
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"

pytestmark = pytest.mark.skipif(not SRC.exists(), reason="frontend not present")

# `async function name(...) {` or `async (...) => {` — the shapes an event handler takes here.
ASYNC_START = re.compile(r"\basync\s+(?:function\s+\w*\s*)?\(")


BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
# `//` to end of line, but not the one in https:// — hence "not preceded by a colon".
LINE_COMMENT = re.compile(r"(?<!:)//[^\n]*")


def _strip_comments(text: str) -> str:
    """Blank out comments, keeping newlines so line numbers still line up.

    Necessary, not fastidious: the first version of this checker flagged the very fix it exists
    to enforce, because the explanatory comment above that fix contains the word "await" and the
    scan found it before the `currentTarget` on the next line. A checker that reads prose as code
    reports the opposite of the truth.
    """
    def blank(m):
        return "".join(ch if ch == "\n" else " " for ch in m.group(0))
    return LINE_COMMENT.sub(blank, BLOCK_COMMENT.sub(blank, text))


def _offenders() -> list[str]:
    found = []
    for path in sorted(SRC.rglob("*.jsx")) + sorted(SRC.rglob("*.js")):
        text = _strip_comments(path.read_text(encoding="utf-8"))
        for match in ASYNC_START.finditer(text):
            # Walk braces from the opening { to find this function's body, so an await in a
            # LATER function cannot be blamed for a currentTarget in this one.
            brace = text.find("{", match.end())
            if brace == -1:
                continue
            depth, end = 0, len(text)
            for i in range(brace, len(text)):
                if text[i] == "{":
                    depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
            body = text[brace:end]
            first_await = body.find("await ")
            if first_await == -1:
                continue
            after = body[first_await:]
            # Nested arrow callbacks get their own fresh event, so only flag a currentTarget
            # that is not itself inside one.
            for hit in re.finditer(r"(\w+)\.currentTarget", after):
                name = hit.group(1)
                preceding = after[:hit.start()]
                if re.search(rf"\(\s*{re.escape(name)}\s*\)\s*=>[^;]*$", preceding):
                    continue
                # Indices into `body`, offset by where the body starts in `text`. The first
                # version added an index into one string to an index into the other and
                # reported a line that had nothing to do with the finding.
                line = text[:brace + first_await + hit.start()].count("\n") + 1
                found.append(
                    f"{path.relative_to(SRC)}:~{line} reads {name}.currentTarget after an await "
                    f"— capture the node before the await instead")
    return found


def test_no_handler_reads_currenttarget_after_awaiting():
    offenders = _offenders()
    assert not offenders, "\n".join(offenders)


def test_the_checker_would_have_caught_the_bug_it_was_written_for():
    """A heuristic that quietly stops matching is worse than no test, because the suite still
    goes green. This pins it against the exact shape that shipped."""
    import types

    sample = SRC / "console" / "pages" / "BrandIdentity.jsx"
    assert sample.exists()

    # The code as it was, run through the same walker via a temporary override.
    broken = (
        "async function submit(event) {\n"
        "  event.preventDefault();\n"
        "  await onUpload(slot.kind, file);\n"
        "  event.currentTarget.reset();\n"
        "}\n"
    )
    fixed = (
        "async function submit(event) {\n"
        "  const form = event.currentTarget;\n"
        "  await onUpload(slot.kind, file);\n"
        "  form.reset();\n"
        "}\n"
    )

    def scan(text: str) -> bool:
        holder = types.SimpleNamespace(rglob=lambda pat: [])
        # Reuse the real logic by writing to a scratch file the walker can read.
        tmp = SRC / "__event_guard_probe__.jsx"
        try:
            tmp.write_text(text, encoding="utf-8")
            return any("__event_guard_probe__" in o for o in _offenders())
        finally:
            tmp.unlink(missing_ok=True)

    assert scan(broken), "the checker no longer detects the bug it exists for"
    assert not scan(fixed), "the checker flags the correct pattern"
