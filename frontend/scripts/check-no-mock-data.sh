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

exit $FAIL
