"""Just enough of Follow Up Boss to run the real sync against, over httpx.

Cursor paging (`_metadata.next`, and `_metadata.nextLink` -- the whole next URL -- as FUB sends
both), the filters the sync uses, a 429 on request, and a switch to refuse `dueStart` -- the one
parameter whose format FUB does not document, so the sync's fallback has something to fall back
from. `updatedAfter` can likewise be ignored or refused, for the lead-source walk's check of it.
"""
from __future__ import annotations

import datetime as dt

import httpx
import pytest

from app.integrations import fub


def _ts(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _param_ts(value: str) -> dt.datetime:
    return dt.datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)


class FakeFub:
    def __init__(self, users=(), people=(), tasks=(), *, today: dt.date | None = None):
        self.users, self.people, self.tasks = list(users), list(people), list(tasks)
        self.today = today or dt.datetime.now(dt.timezone.utc).date()
        self.calls: list[tuple[str, dict]] = []
        self.throttle: dict[str, int] = {}           # path -> how many 429s to send first
        self.refuse_due_start = False
        self.refuse_key = False
        self.refuse_sort = False        # 400 on any `sort`
        self.ignore_updated_after = False   # accept `updatedAfter` and filter nothing by it
        self.refuse_updated_after = False   # 400 on `updatedAfter`
        self.next_link = True               # send `_metadata.nextLink` beside `next`
        self.ignore_sort_order = False  # accept `-created` but answer oldest first
        self.identity = {"account": {"id": 777, "domain": "acme"},
                         "user": {"id": 1, "name": "Owner", "email": "owner@acme.test"}}
        self.smart_lists: list[dict] = []     # {"id": 35, "name": "01. Recently Active"}
        # Activity, read per person (`personId`) the way the lead-source walk reads it.
        self.activity: dict[str, list[dict]] = {"calls": [], "textMessages": [], "emEvents": [],
                                                "appointments": [], "deals": []}

    # ── routing ──────────────────────────────────────────────────────────────────────────
    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        params = dict(request.url.params)
        self.calls.append((path, params))
        if self.refuse_key:
            return httpx.Response(401, json={"errorMessage": "Invalid API key"})
        if self.throttle.get(path):
            self.throttle[path] -= 1
            return httpx.Response(429, headers={"Retry-After": "0"})
        if path == "/identity":
            return httpx.Response(200, json=self.identity)
        if path == "/users":
            return self._page("users", self.users, params)
        if path.startswith("/users/"):
            uid = path.rsplit("/", 1)[-1]
            user = next((u for u in self.users if str(u["id"]) == uid), None)
            return httpx.Response(200, json=user) if user else httpx.Response(404, json={})
        if path == "/people":
            if self.refuse_sort and "sort" in params:
                return httpx.Response(400, json={"errorMessage": "Invalid sort"})
            if self.refuse_updated_after and "updatedAfter" in params:
                return httpx.Response(400, json={"errorMessage": "Invalid updatedAfter"})
            return self._page("people", self._people(params), params,
                              request.url if self.next_link else None)
        if path == "/smartLists":
            return self._page("smartlists", self.smart_lists, params)
        if path.lstrip("/") in self.activity:
            key = path.lstrip("/")
            rows = self.activity[key]
            if "personId" in params:
                rows = [r for r in rows if str(r.get("personId")) == str(params["personId"])]
            return self._page(key, rows, params)
        if path == "/tasks":
            if self.refuse_due_start and "dueStart" in params:
                return httpx.Response(400, json={"errorMessage": "Invalid dueStart"})
            return self._page("tasks", self._tasks(params), params)
        return httpx.Response(404, json={"errorMessage": f"no route {path}"})

    def calls_to(self, path: str) -> list[dict]:
        return [p for called, p in self.calls if called == path]

    # ── filters ──────────────────────────────────────────────────────────────────────────
    def _people(self, params: dict) -> list[dict]:
        rows = self.people
        if "id" in params:
            wanted = set(params["id"].split(","))
            return [p for p in rows if str(p["id"]) in wanted]
        if params.get("includeTrash") != "true":
            rows = [p for p in rows if p.get("stage") != "Trash"]
        if "contacted" in params:
            flag = params["contacted"] == "true"
            rows = [p for p in rows if bool(p.get("contacted")) == flag]
        if "lastActivityAfter" in params:
            after = _param_ts(params["lastActivityAfter"])
            rows = [p for p in rows if _ts(p.get("lastActivity")) and _ts(p["lastActivity"]) > after]
        if "source" in params:                         # the exact string, any case
            rows = [p for p in rows if str(p.get("source") or "").lower() == params["source"].lower()]
        if "createdAfter" in params:
            after = _param_ts(params["createdAfter"])
            rows = [p for p in rows if _ts(p.get("created")) and _ts(p["created"]) > after]
        if "updatedAfter" in params and not self.ignore_updated_after:
            after = _param_ts(params["updatedAfter"])
            rows = [p for p in rows if _ts(p.get("updated")) and _ts(p["updated"]) > after]
        if "q" in params:                              # FUB's search reads more than the source
            q = params["q"].lower()
            rows = [p for p in rows if q in " ".join(str(p.get(k) or "") for k in ("name", "source")).lower()]
        if params.get("sort") == "-created":
            rows = sorted(rows, key=lambda p: _ts(p.get("created")) or dt.datetime.min.replace(
                tzinfo=dt.timezone.utc), reverse=not self.ignore_sort_order)
        return rows

    def _tasks(self, params: dict) -> list[dict]:
        rows = self.tasks
        if params.get("isCompleted") == "false":
            rows = [t for t in rows if not t.get("isCompleted")]
        due = params.get("due")
        if due == "today":
            rows = [t for t in rows if t.get("dueDate") == self.today.isoformat()]
        elif due == "overdue":
            rows = [t for t in rows if t.get("dueDate") and t["dueDate"] < self.today.isoformat()]
        if "dueStart" in params:
            start = _param_ts(params["dueStart"]).date()
            rows = [t for t in rows if t.get("dueDate") and t["dueDate"] >= start.isoformat()]
        return rows

    @staticmethod
    def _page(key: str, rows: list, params: dict, url: httpx.URL | None = None) -> httpx.Response:
        start = int(params.get("next") or 0)
        limit = int(params.get("limit") or 10)
        chunk = rows[start:start + limit]
        nxt = str(start + limit) if start + limit < len(rows) else None
        meta = {"collection": key, "offset": start, "limit": limit, "total": len(rows), "next": nxt}
        if url is not None and nxt is not None:
            meta["nextLink"] = str(url.copy_set_param("next", nxt))
        return httpx.Response(200, json={key: chunk, "_metadata": meta})


def user(i, email=None, status="Active", role="Agent", is_owner=False):
    return {"id": i, "name": f"Agent {i}", "email": email or f"agent{i}@acme.test",
            "status": status, "role": role, "isOwner": is_owner}


def person(i, *, assigned=1, created="2026-09-10T15:00:00Z", stage="Lead", contacted=False,
           last_activity=None, name=None, source="Zillow", emails=None, updated=None,
           assigned_to=None, price=None):
    """`emails` is FUB's own shape: a LIST of {value, isPrimary}, where order is NOT priority."""
    row = {"id": i, "name": name if name is not None else f"Person {i}", "stage": stage,
           "source": source, "contacted": contacted, "assignedUserId": assigned,
           "created": created, "updated": updated or created, "lastActivity": last_activity or created}
    if assigned_to is not None:
        row["assignedTo"] = assigned_to
    if price is not None:
        row["price"] = price
    if emails is not None:
        row["emails"] = emails
    return row


def task(i, *, person_id, due: dt.date, assigned=1, name="Call back", kind="Call",
         at: str | None = None, completed=False):
    return {"id": i, "personId": person_id, "assignedUserId": assigned, "name": name, "type": kind,
            "dueDate": due.isoformat(), "dueDateTime": at, "isCompleted": completed}


@pytest.fixture
def fake(monkeypatch):
    server = FakeFub()
    monkeypatch.setattr(fub, "TRANSPORT", httpx.MockTransport(server))

    async def _no_wait(_seconds):
        return None
    monkeypatch.setattr(fub, "_sleep", _no_wait)
    return server


def call(i, *, person_id, created, duration=0, note="", outbound=True):
    return {"id": i, "personId": person_id, "created": created, "duration": duration, "note": note,
            "isIncoming": not outbound}


def text(i, *, person_id, created, outbound=True):
    return {"id": i, "personId": person_id, "created": created, "isIncoming": not outbound}


def em_event(i, *, person_id, occurred, kind="sent"):
    return {"id": i, "personId": person_id, "occurred": occurred, "type": kind}


def appointment(i, *, person_id, start, outcome=None):
    return {"id": i, "personId": person_id, "start": start, "outcome": outcome}


def deal(i, *, person_id, stage="Offer Written", status="Active", close=None):
    """FUB's deals payload names the stage `stageName`; the person is listed under `people`."""
    row = {"id": i, "personId": person_id, "people": [{"id": person_id}], "stageName": stage,
           "status": status}
    if close:
        row["actualCloseDate"] = close
    return row
