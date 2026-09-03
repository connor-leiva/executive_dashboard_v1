#!/usr/bin/env bash
set -uo pipefail

ROOTS="frontend/src/console frontend/src/intranet"
if [ -d "src/console" ] || [ -d "src/intranet" ]; then
  ROOTS="src/console src/intranet"
fi

FAIL=0

while IFS= read -r f; do
  case "$f" in */constants.js) continue ;; esac
  if grep -qE '^\s*(export\s+)?const\s+[A-Z_]+\s*=\s*\[\s*$' "$f" \
     && grep -qE '^\s*\{.*:' "$f"; then
    echo "MOCK DATA: array of objects in $f"
    FAIL=1
  fi
done < <(find $ROOTS \( -name '*.js' -o -name '*.jsx' \) 2>/dev/null)

if grep -rnE '>[[:space:]]*[0-9]{2,}[[:space:]]+(SOPs|courses|lessons|people|seats|agents|tiles|docs)' $ROOTS 2>/dev/null; then
  echo "MOCK DATA: hardcoded count above"
  FAIL=1
fi

if grep -rnE '"[0-9]+ (min|hr|hour|day)s? ago"' $ROOTS 2>/dev/null; then
  echo "MOCK DATA: literal relative timestamp above"
  FAIL=1
fi

# A FROZEN CALENDAR DATE. The intranet shipped `MOCK_DATE = "MONDAY, AUGUST 17"` -- the mockup's
# instant, hardcoded -- and rendered it above the greeting on every screen, months later. The rule
# above catches "5 min ago" but not that, because the list was written from the fakes somebody had
# already found rather than from what a fake looks like.
#
# Narrow on purpose: a weekday AND a month, or a month AND a day number. "Monday" on its own is a
# legitimate week-starts-on option and must not trip this.
if grep -rniE '"[^"]*(mon|tues|wednes|thurs|fri|satur|sun)day[^"]*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[^"]*"' $ROOTS 2>/dev/null    || grep -rnE '"[^"]*(January|February|March|April|May|June|July|August|September|October|November|December) [0-9]{1,2}[^"]*"' $ROOTS 2>/dev/null; then
  echo "MOCK DATA: frozen calendar date above - render the viewer's own date instead"
  FAIL=1
fi

exit $FAIL
