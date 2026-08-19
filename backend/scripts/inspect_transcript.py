"""Print the SHAPE of a Recall transcript, with the words redacted.

Every time this integration guessed at a payload it was wrong - automatic_leave's nesting,
the status field, meeting_url being an object. So before a transcript parser gets written,
this shows what Recall actually returns: the segment structure, whether speakers are
labelled, and whether timings are per-segment or per-word.

Actual spoken words are NEVER printed - only counts, types and field names. Safe to paste.

    .venv\\Scripts\\python.exe scripts\\inspect_transcript.py <bot-id>
"""
import json
import os
import sys

import httpx

STRUCTURAL = {"speaker", "speaker_id", "is_final", "language", "confidence", "channel",
              "participant", "id", "start", "end", "start_time", "end_time", "timestamp",
              "offset", "duration"}


def shape(node, path="", depth=0):
    pad = "  " * (depth + 1)
    if isinstance(node, dict):
        for k, v in node.items():
            shape(v, f"{path}.{k}" if path else k, depth)
    elif isinstance(node, list):
        print(f"{pad}{path}[]  ({len(node)} items)")
        if node:
            shape(node[0], f"{path}[0]", depth)
    else:
        leaf = path.rsplit(".", 1)[-1].split("[")[0]
        if leaf in STRUCTURAL or isinstance(node, (int, float, bool)) or node is None:
            print(f"{pad}{path} = {node!r}")
        else:
            # spoken content - report only that it exists and how big
            print(f"{pad}{path} = <{type(node).__name__}, {len(str(node))} chars, REDACTED>")


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: inspect_transcript.py <bot-id>")
    key = os.getenv("RECALL_API_KEY")
    if not key:
        sys.exit("set RECALL_API_KEY first")
    bot = sys.argv[1]
    base = f"https://{os.getenv('RECALL_REGION', 'us-west-2')}.recall.ai"
    H = {"Authorization": f"Token {key}"}

    r = httpx.get(f"{base}/api/v1/bot/{bot}/", headers=H, timeout=30)
    if r.status_code >= 300:
        sys.exit(f"FAIL {r.status_code}: {r.text[:300]}")
    body = r.json()
    recs = body.get("recordings") or []
    if not recs:
        sys.exit("this bot has no recordings")
    tr = ((recs[0].get("media_shortcuts") or {}).get("transcript") or {})
    data = tr.get("data") or {}
    print(f"transcript status : {((tr.get('status') or {}).get('code'))}")
    print(f"provider          : {list((tr.get('provider') or {}).keys())}")
    print(f"diarization       : {tr.get('diarization')!r}")
    print(f"data keys         : {sorted(data.keys())}\n")

    url = data.get("download_url")
    if not url:
        sys.exit("no transcript download_url on this recording")

    t = httpx.get(url, timeout=60)
    if t.status_code >= 300:
        sys.exit(f"FAIL fetching transcript {t.status_code}")
    print(f"payload bytes     : {len(t.content)}")
    try:
        doc = t.json()
    except Exception:
        print("NOT json - first 200 chars are text; parser must handle plain text")
        return

    print(f"top level         : {type(doc).__name__}"
          + (f", {len(doc)} items" if isinstance(doc, list) else f", keys={sorted(doc.keys())}"))
    print("\nSHAPE (words redacted):")
    shape(doc if not isinstance(doc, list) else {"root": doc})

    # The three questions a parser has to answer.
    first = doc[0] if isinstance(doc, list) and doc else doc
    if isinstance(first, dict):
        keys = set(first.keys())
        print("\nfor the parser:")
        print(f"  speaker labelled?      {'YES' if keys & {'speaker', 'speaker_id', 'participant'} else 'NO'}")
        print(f"  segment-level timing?  {'YES' if keys & {'start', 'start_time', 'offset', 'timestamp'} else 'NO'}")
        wl = first.get("words")
        print(f"  word-level timing?     {'YES, ' + str(len(wl)) + ' words in segment 1' if isinstance(wl, list) else 'no'}")


if __name__ == "__main__":
    main()
