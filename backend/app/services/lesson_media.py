"""How a lesson's source becomes something the portal can actually play.

The design shows a video sitting in the page. Some of these sources can do that and some cannot,
and the difference is not a detail to paper over: Skool, PLACE and eXp are logged-in platforms
that send X-Frame-Options and refuse to be framed. An iframe pointed at one renders a blank
rectangle or a browser refusal message -- a player that looks broken rather than a link that
works, which is strictly worse than what the checklist did.

So this resolves each source to one of three honest modes:

    video   -- a file the browser can play itself, in a <video> element
    iframe  -- a provider whose embed URL is designed to be framed (Loom, YouTube, Vimeo), or a PDF
    link    -- everything else: rendered as a card that launches the source in a new tab

The mode is decided HERE rather than in the portal because it is a fact about the URL, it is the
same answer for every screen that will ever show a lesson, and it is testable in the suite that
actually runs.

WHAT IT DOES NOT DO is trust `source_type` over the URL. A team pastes a YouTube link into a
lesson whose type says "Hosted" because Hosted is the first option in the dropdown, and the
resulting <video src="https://youtube.com/watch?v=..."> plays nothing at all. The URL is the
evidence; the declared type is a hint used only when the URL says nothing.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

# Extensions a browser plays natively in a <video>. Deliberately short: this is the list of things
# that WILL play, not the list of things that are video.
VIDEO_EXTENSIONS = (".mp4", ".m4v", ".webm", ".ogv", ".mov")

LOOM = re.compile(r"loom\.com/(?:share|embed)/([0-9a-zA-Z]+)")
YOUTUBE_LONG = re.compile(r"youtube\.com/watch")
YOUTUBE_SHORT = re.compile(r"youtu\.be/([\w-]+)")
YOUTUBE_EMBED = re.compile(r"youtube\.com/embed/([\w-]+)")
VIMEO = re.compile(r"vimeo\.com/(?:video/)?(\d+)")

# How each source spells its own name. The stored value is an uppercase enum, and neither
# "This lesson lives in EXP" nor a button reading "Open SKOOL" is something a person would write.
# One map, used for both the sentence and the button, so they cannot disagree.
NAMES = {"SKOOL": "Skool", "PLACE": "PLACE", "EXP": "eXp", "LOOM": "Loom",
         "HERE": "the video", "PDF": "the PDF"}

# Sources that are a logged-in product, not a video host. Framing these produces a refusal.
WALLED = {"SKOOL", "PLACE", "EXP"}


def source_name(source_type: str | None) -> str:
    """What to call this source in a sentence somebody reads."""
    kind = (source_type or "").strip().upper()
    return NAMES.get(kind) or (source_type or "the lesson")


# The library card's badge, which answers a different question from `source_name`: not "where do I
# open this" but "what kind of thing is this course made of". Loom and a hosted file are both just
# video to somebody choosing what to work through, so they collapse; the platforms stay named
# because "this one is in Skool" is exactly what a person wants to know before they start.
BADGES = {"LOOM": "Video", "HERE": "Video", "PDF": "PDF", "SKOOL": "Skool",
          "PLACE": "PLACE", "EXP": "eXp"}
_BADGE_ORDER = ["Video", "PDF", "Skool", "PLACE", "eXp"]


def course_media(source_types) -> str:
    """One badge for a whole course: "Video", "PDF + Video", "Skool".

    Ordered by _BADGE_ORDER rather than by first appearance, so re-ordering the lessons does not
    silently re-order the badge. Capped at two -- a card reading "Video + PDF + Skool + eXp" has
    stopped telling anybody anything.
    """
    kinds = {BADGES.get((t or "").strip().upper()) for t in (source_types or [])}
    kinds.discard(None)
    ordered = [k for k in _BADGE_ORDER if k in kinds]
    if not ordered:
        return "Course"
    if len(ordered) > 2:
        return f"{ordered[0]} + {len(ordered) - 1} more"
    return " + ".join(ordered)


def _clean(url: str | None) -> str:
    return (url or "").strip()


def resolve(source_type: str | None, source_ref: str | None) -> dict:
    """{"mode": video|iframe|link|none, "url": ..., "reason": ...} for one lesson.

    `reason` is filled only for `link`, and says WHY it is a link, so the portal can put a useful
    sentence on the launch card instead of an unexplained button.
    """
    url = _clean(source_ref)
    kind = (source_type or "").strip().upper()

    name = source_name(source_type)
    if not url:
        return {"mode": "none", "url": None, "label": name,
                "reason": "This lesson has no source attached yet."}

    # Evidence from the URL first -- see the note above about a YouTube link typed as "Hosted".
    loom = LOOM.search(url)
    if loom:
        return {"mode": "iframe", "url": f"https://www.loom.com/embed/{loom.group(1)}",
                "label": name, "reason": None}

    if YOUTUBE_EMBED.search(url):
        return {"mode": "iframe", "url": url, "label": name, "reason": None}
    short = YOUTUBE_SHORT.search(url)
    if short:
        return {"mode": "iframe", "url": f"https://www.youtube.com/embed/{short.group(1)}",
                "label": name, "reason": None}
    if YOUTUBE_LONG.search(url):
        video_id = (parse_qs(urlsplit(url).query).get("v") or [""])[0]
        if video_id:
            return {"mode": "iframe", "url": f"https://www.youtube.com/embed/{video_id}",
                    "label": name, "reason": None}

    vimeo = VIMEO.search(url)
    if vimeo:
        return {"mode": "iframe", "url": f"https://player.vimeo.com/video/{vimeo.group(1)}",
                "label": name, "reason": None}

    path = urlsplit(url).path.lower()
    if path.endswith(VIDEO_EXTENSIONS):
        return {"mode": "video", "url": url, "label": name, "reason": None}
    if path.endswith(".pdf") or kind == "PDF":
        return {"mode": "iframe", "url": url, "label": name, "reason": None}

    if kind in WALLED:
        # Named, not hidden. "Open in Skool" is a working instruction; a grey box where a video
        # should be is a bug report.
        return {"mode": "link", "url": url, "label": name,
                "reason": f"This lesson lives in {name}, which has to be opened there."}

    return {"mode": "link", "url": url, "label": name,
            "reason": "This source opens in a new tab."}
