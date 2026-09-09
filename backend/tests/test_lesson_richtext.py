"""The lesson-body sanitizer.

This is the first authored HTML in the product, which makes it the first place one person's markup
reaches another person's browser. Almost every test here is about what must NOT survive.

The design's "Formatting reference" view is the acceptance list: every mark it shows must round
trip, and everything it says is stripped must be gone.
"""
import pytest

from app.services import lesson_richtext as rt

TENANT = "11111111-1111-1111-1111-111111111111"


def clean(html):
    return rt.sanitize(html, tenant_id=TENANT)


# ── what must not survive ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("html", [
    '<script>alert(1)</script>',
    '<p>a</p><script>alert(1)</script><p>b</p>',
    '<iframe src="https://evil.test"></iframe>',
    '<img src=x onerror="alert(1)">',
    '<p onclick="alert(1)">text</p>',
    '<svg><script>alert(1)</script></svg>',
    '<object data="evil.swf"></object>',
    '<p style="position:fixed;top:0">overlay</p>',
])
def test_nothing_executable_survives(html):
    out = clean(html)
    for banned in ("<script", "<iframe", "onerror", "onclick", "<object", "<svg", "style="):
        assert banned not in out.lower(), f"{banned!r} survived in {out!r}"


def test_script_takes_its_contents_with_it():
    """Dropping the element is not enough -- the body of a <script> would otherwise land as a
    paragraph of JavaScript that reads like content."""
    assert "alert" not in clean("<script>alert(1)</script><p>after</p>")
    assert clean("<script>alert(1)</script><p>after</p>") == "<p>after</p>"


@pytest.mark.parametrize("scheme", ["javascript:alert(1)", "data:text/html,<b>x", "vbscript:x"])
def test_a_dangerous_href_drops_the_link_and_keeps_the_words(scheme):
    """The spec is specific: the anchor goes, the text stays. A bare <a> with no href would be
    neither -- it looks like a link and does nothing."""
    out = clean(f'<p><a href="{scheme}">click me</a> after</p>')
    assert "click me" in out and "after" in out
    assert "<a" not in out


def test_class_and_id_are_stripped():
    """Authored markup must not be able to reach the portal's own stylesheet."""
    out = clean('<p class="ut-rail" id="main">x</p>')
    assert out == "<p>x</p>"


# ── what must survive ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("html", [
    "<p>Body copy.</p>",
    "<h2>Heading</h2>",
    "<h3>Subheading</h3>",
    "<p><strong>bold</strong> and <em>italic</em></p>",
    "<ul><li>one</li><li>two</li></ul>",
    "<ol><li>one</li><li>two</li></ol>",
    "<blockquote><p>Worth knowing</p></blockquote>",
    "<hr>",
])
def test_every_mark_the_editor_can_produce_round_trips(html):
    assert clean(html) == html


def test_an_internal_link_survives_as_a_path():
    """Internal links stay in the portal, so a site-relative href has to be kept."""
    assert clean('<p><a href="/sops">SOPs</a></p>') == '<p><a href="/sops">SOPs</a></p>'


def test_an_external_link_survives_as_a_bare_href():
    """No `rel` is stored. Whether a link opens in a new tab is a RENDER decision the portal makes
    from the href (spec 3.4), so storing rel here would put that decision in two places and only
    one of them would be updated. The portal-side assertion lives with the portal."""
    out = clean('<p><a href="https://x.test">docs</a></p>')
    assert out == '<p><a href="https://x.test">docs</a></p>'


def test_mailto_survives():
    assert 'href="mailto:a@b.test"' in clean('<p><a href="mailto:a@b.test">mail</a></p>')


# ── normalisation ─────────────────────────────────────────────────────────────────────────

def test_b_and_i_become_strong_and_em():
    """Word, Docs and older editors all emit the presentational pair. One spelling is stored."""
    assert clean("<p><b>b</b><i>i</i></p>") == "<p><strong>b</strong><em>i</em></p>"


def test_nested_lists_flatten_to_one_level():
    """The portal styles one indent. A second level would render as an unstyled surprise."""
    out = clean("<ul><li>one<ul><li>nested</li></ul></li><li>two</li></ul>")
    assert out.count("<ul>") == 1
    assert out == "<ul><li>one</li><li>nested</li><li>two</li></ul>"


def test_empty_blocks_are_dropped():
    assert clean("<p></p><p>   </p><h2></h2><p>real</p>") == "<p>real</p>"


def test_a_word_paste_collapses_rather_than_being_rejected():
    """Real Word markup: MsoNormal paragraphs wrapped in divs with inline styles and a font tag."""
    word = ('<div class="WordSection1"><p class="MsoNormal" style="margin:0cm">'
            '<span style="font-size:11pt"><font face="Calibri">First para</font></span></p>'
            '<p class="MsoNormal"><b><span>Bold bit</span></b></p></div>')
    out = clean(word)
    assert out == "<p>First para</p><p><strong>Bold bit</strong></p>", out


def test_a_google_docs_paste_collapses_rather_than_being_rejected():
    """Docs wraps everything in `<b style="font-weight:normal">` and styles every span."""
    docs = ('<b style="font-weight:normal" id="docs-internal-guid-1">'
            '<p dir="ltr" style="line-height:1.38"><span style="font-size:11pt">Para one</span></p>'
            '<p dir="ltr"><span style="font-style:italic">Slanted</span></p></b>')
    out = clean(docs)
    assert "<p>Para one</p>" in out
    assert "style=" not in out and "id=" not in out
    assert "docs-internal-guid" not in out


def test_loose_text_becomes_paragraphs_rather_than_running_together():
    """A `<div>` per paragraph is the common hand-written shape. Unwrapping without a break would
    concatenate two paragraphs into one sentence."""
    assert clean("<div>One</div><div>Two</div>") == "<p>One</p><p>Two</p>"


def test_a_callout_keeps_its_closing_tag():
    """An earlier version split blocks with a regex and matched `<blockquote><p>x</p>` as one
    unit, silently dropping the `</blockquote>`."""
    assert clean("<blockquote><p>Q</p></blockquote><p>After</p>") \
        == "<blockquote><p>Q</p></blockquote><p>After</p>"


def test_sanitizing_twice_changes_nothing():
    """It runs on write AND on read, so a body that shifted on the second pass would drift every
    time somebody opened the lesson."""
    for html in ("<p>a</p><h2>b</h2><ul><li>c</li></ul>",
                 "<blockquote><p>q</p></blockquote>",
                 '<figure><img src="https://x.test/a.png" alt="a"><figcaption>c</figcaption></figure>'):
        once = clean(html)
        assert clean(once) == once, html


# ── images ────────────────────────────────────────────────────────────────────────────────

def test_our_own_storage_key_is_kept():
    key = f"intranet/{TENANT}/lessons/1/a.png"
    assert f'src="{key}"' in clean(f'<p><img src="{key}" alt="chart"></p>')


def test_another_workspaces_storage_key_is_refused():
    """The reason the sanitizer takes a tenant at all. nh3 can judge a URL; only we can judge
    whose it is, and a key from another workspace would be read back through our own resolver."""
    out = clean('<p><img src="intranet/99999999-9999-9999-9999-999999999999/x.png" alt="no"></p>')
    assert "<img" not in out


def test_an_https_image_is_allowed_but_a_data_uri_is_not():
    assert "<img" in clean('<p><img src="https://cdn.test/a.png" alt="a"></p>')
    assert "<img" not in clean('<p><img src="data:image/png;base64,AAAA" alt="a"></p>')


def test_a_figure_holds_its_image_directly():
    """<figure><p><img></p></figure> is not the structure the CSS or a screen reader expects."""
    out = clean('<figure><img src="https://x.test/a.png" alt="a"><figcaption>Cap</figcaption></figure>')
    assert out == '<figure><img src="https://x.test/a.png" alt="a"><figcaption>Cap</figcaption></figure>'


# ── derived numbers ───────────────────────────────────────────────────────────────────────

def test_read_time_is_words_over_two_hundred_and_twenty():
    assert rt.read_minutes("<p>" + " ".join(["word"] * 440) + "</p>") == 2
    assert rt.read_minutes("<p>" + " ".join(["word"] * 1180) + "</p>") == 5


def test_a_very_short_body_still_reads_as_a_minute():
    """round() would give 0 for anything under 110 words, and "0 min read" is not a length."""
    assert rt.read_minutes("<p>three words here</p>") == 1


def test_an_empty_body_has_no_read_time_rather_than_zero():
    """None is "not written yet". 0 is a claim about a lesson somebody is about to write."""
    assert rt.read_minutes("") is None
    assert rt.read_minutes(None) is None
    assert rt.word_count(None) == 0


def test_text_extraction_separates_blocks():
    """For search and the assistant. Without the space, "End.</p><p>Next" indexes as "End.Next"
    and a search for "next" misses it."""
    assert rt.to_text("<p>End.</p><p>Next</p>") == "End. Next"
    assert rt.to_text("<ul><li>a</li><li>b</li></ul>") == "a b"
    assert "<" not in rt.to_text('<p>A <a href="/x">link</a></p>')


# ── image keys and the URLs they are served as ────────────────────────────────────────────

KEY = f"intranet/{TENANT}/lessons/22222222-2222-2222-2222-222222222222/chart.png"


def test_a_key_is_rewritten_to_a_route_the_browser_can_fetch():
    out = rt.image_urls(f'<p><img src="{KEY}" alt="a"></p>', rt.PORTAL_IMAGE_ROUTE)
    assert 'src="/api/v1/intranet/lessons/22222222-2222-2222-2222-222222222222/images/chart.png"' \
        in out
    console = rt.image_urls(f'<p><img src="{KEY}" alt="a"></p>', rt.CONSOLE_IMAGE_ROUTE)
    assert 'src="/api/console/lessons/22222222-2222-2222-2222-222222222222/images/chart.png"' \
        in console


def test_a_served_url_folds_back_to_its_key_on_save():
    """The editor is handed URLs, so the editor posts URLs back. Without this the second save
    would strip every image as unowned, and the third would leave the article blank."""
    served = rt.image_urls(f'<p><img src="{KEY}" alt="a"></p>', rt.CONSOLE_IMAGE_ROUTE)
    assert clean(served) == f'<p><img src="{KEY}" alt="a"></p>'
    portal = rt.image_urls(f'<p><img src="{KEY}" alt="a"></p>', rt.PORTAL_IMAGE_ROUTE)
    assert clean(portal) == f'<p><img src="{KEY}" alt="a"></p>'


def test_the_round_trip_is_stable_over_repeated_saves():
    body = f'<p><img src="{KEY}" alt="a"></p>'
    for _ in range(3):
        body = clean(rt.image_urls(body, rt.CONSOLE_IMAGE_ROUTE))
    assert body == f'<p><img src="{KEY}" alt="a"></p>'


def test_another_lessons_url_is_still_this_tenants_key():
    """The route names a lesson, not a tenant -- the tenant comes from the session. So a URL
    pointing at somebody else's lesson id folds into OUR key, which does not exist, rather than
    into a path that reads their file."""
    other = "/api/console/lessons/33333333-3333-3333-3333-333333333333/images/x.png"
    out = clean(f'<p><img src="{other}" alt="a"></p>')
    assert f"intranet/{TENANT}/lessons/33333333" in out


def test_a_url_that_is_not_ours_is_not_folded():
    assert "<img" not in clean('<p><img src="/api/console/lessons/nope/images/x.png" alt="a"></p>')
    assert "<img" not in clean('<p><img src="/etc/passwd" alt="a"></p>')
