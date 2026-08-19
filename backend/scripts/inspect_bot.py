"""Print the SHAPE of a Recall bot's payload, with every value redacted.

Why this exists: the dashboard reads a recording's media URL from a nested path that we
took from Recall's docs, not from a real response. This shows what a real completed bot
actually returns so that path can be verified instead of assumed.

Safe to paste anywhere. Signed URLs, transcript text and attendee names are replaced with
their type and length; only structural fields (status codes, timestamps, ids) print as-is.

    set RECALL_API_KEY, then:
    .venv\\Scripts\\python.exe scripts\\inspect_bot.py <bot-id>
"""
import os
import re
import sys

import httpx

SAFE_LEAVES = {
    "status", "code", "state", "sub_code", "event", "kind", "format", "type",
    "created_at", "updated_at", "join_at", "started_at", "completed_at", "expires_at",
    "media_retention_end", "bot_name", "id",
}


def show(node, path=""):
    if isinstance(node, dict):
        if not node:
            print(f"  {path} = {{}}")
        for k, v in node.items():
            show(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        print(f"  {path}[]  ({len(node)} items)")
        if node:
            show(node[0], f"{path}[0]")
    else:
        leaf = path.rsplit(".", 1)[-1].rstrip("]").split("[")[0]
        if isinstance(node, str) and re.match(r"https?://", node):
            print(f"  {path} = <URL, {len(node)} chars, host={node.split('/')[2][:44]}>")
        elif leaf in SAFE_LEAVES or node is None or isinstance(node, (bool, int, float)):
            print(f"  {path} = {node!r}")
        else:
            print(f"  {path} = <{type(node).__name__}, {len(str(node))} chars>")


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: inspect_bot.py <bot-id>")
    key = os.getenv("RECALL_API_KEY")
    if not key:
        sys.exit("set RECALL_API_KEY first")
    bot = sys.argv[1]
    base = f"https://{os.getenv('RECALL_REGION', 'us-west-2')}.recall.ai"

    r = httpx.get(f"{base}/api/v1/bot/{bot}/",
                  headers={"Authorization": f"Token {key}"}, timeout=30)
    if r.status_code >= 300:
        sys.exit(f"FAIL {r.status_code}: {r.text[:300]}")
    body = r.json()

    print(f"bot {bot} - payload shape (values redacted)\n")
    show(body)

    # The two paths the dashboard tries, checked against reality.
    recs = body.get("recordings") or []
    data = (((recs[0] if recs else {}).get("media_shortcuts") or {}).get("video_mixed") or {}).get("data") or {}
    print("\nwhat app/services/recall.py looks for:")
    print(f"  recordings[0].media_shortcuts.video_mixed.data.download_url  ->  "
          f"{'FOUND' if data.get('download_url') else 'MISSING'}")
    print(f"  top-level video_url (older shape)                            ->  "
          f"{'FOUND' if body.get('video_url') else 'MISSING'}")

    # Transcript isn't wired up yet; report whether it is there to wire.
    tdata = (((recs[0] if recs else {}).get("media_shortcuts") or {}).get("transcript") or {}).get("data") or {}
    print(f"  transcript available                                         ->  "
          f"{'YES ' + str(sorted(tdata.keys())) if tdata else 'no'}")


if __name__ == "__main__":
    main()
