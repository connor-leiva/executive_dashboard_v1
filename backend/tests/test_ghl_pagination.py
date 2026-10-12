"""The GHL cursor paginators must always terminate.

get_opportunities / get_contacts follow startAfterId until the API stops handing one back. If the API
ever returns rows with a cursor that does NOT advance (a stuck/looping cursor), the old `while True`
would spin until the per-tenant sync timeout cancelled it — which is how one tenant's sync wedged the
whole scheduler for ~2.5 days (2026-10-10). Both loops now stop the moment the cursor stalls, and a
page cap backstops a pathologically large location."""
from app.integrations import ghl


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


async def test_get_opportunities_stops_when_the_cursor_stops_advancing(monkeypatch):
    calls = {"n": 0}

    async def fake_request(method, path, **kw):
        calls["n"] += 1                                  # the API keeps handing back the SAME cursor
        return _FakeResp({"opportunities": [{"id": f"o{calls['n']}"}], "meta": {"startAfterId": "STUCK"}})

    monkeypatch.setattr(ghl, "ghl_request", fake_request)
    out = await ghl.get_opportunities("tok", "loc")
    assert calls["n"] == 2, "a non-advancing cursor must stop after it is seen twice, not loop forever"
    assert [o["id"] for o in out] == ["o1", "o2"]


async def test_get_opportunities_is_capped_for_a_runaway_location(monkeypatch):
    monkeypatch.setattr(ghl, "_MAX_SEARCH_PAGES", 5)
    n = {"i": 0}

    async def fake_request(method, path, **kw):
        n["i"] += 1                                      # a genuinely advancing but endless cursor
        return _FakeResp({"opportunities": [{"id": f"o{n['i']}"}], "meta": {"startAfterId": f"c{n['i']}"}})

    monkeypatch.setattr(ghl, "ghl_request", fake_request)
    out = await ghl.get_opportunities("tok", "loc")
    assert n["i"] == 5 and len(out) == 5, "the page cap must bound an endless cursor"


async def test_get_contacts_stops_when_the_cursor_stops_advancing(monkeypatch):
    calls = {"n": 0}

    async def fake_request(method, path, **kw):
        calls["n"] += 1
        return _FakeResp({"contacts": [{"id": f"c{calls['n']}"}], "meta": {"startAfterId": "STUCK"}})

    monkeypatch.setattr(ghl, "ghl_request", fake_request)
    out = await ghl.get_contacts("tok", "loc")
    assert calls["n"] == 2
    assert len(out) == 2


async def test_pagination_still_follows_a_normal_advancing_cursor(monkeypatch):
    """The guard must not break ordinary pagination: a cursor that advances and then ends is followed
    to the last page."""
    pages = [
        {"opportunities": [{"id": "a"}], "meta": {"startAfterId": "p1"}},
        {"opportunities": [{"id": "b"}], "meta": {"startAfterId": "p2"}},
        {"opportunities": [{"id": "c"}], "meta": {}},        # no cursor → last page
    ]
    i = {"k": 0}

    async def fake_request(method, path, **kw):
        r = _FakeResp(pages[i["k"]])
        i["k"] += 1
        return r

    monkeypatch.setattr(ghl, "ghl_request", fake_request)
    out = await ghl.get_opportunities("tok", "loc")
    assert [o["id"] for o in out] == ["a", "b", "c"]
