"""Just enough of Follow Up Boss to run the real sync against, over httpx.

Cursor paging (`_metadata.next`, as FUB does it), the filters the sync uses, a 429 on request, and
a switch to refuse `dueStart` -- the one parameter whose format FUB does not document, so the
sync's fallback has something to fall back from.
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
        self.ignore_sort_order = False  # accept `-created` but answer oldest first
        self.identity = {"account": {"id": 777, "domain": "acme"},
                         "user": {"id": 1, "name": "Owner", "email": "owner@acme.test"}}
        self.smart_lists: list[dict] = []     # {"id": 35, "name": "01. Recently Active"}

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
            return self._page("people", self._people(params), params)
        if path == "/smartLists":
            return self._page("smartlists", self.smart_lists, params)
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
    def _page(key: str, rows: list, params: dict) -> httpx.Response:
        start = int(params.get("next") or 0)
        limit = int(params.get("limit") or 10)
        chunk = rows[start:start + limit]
        nxt = str(start + limit) if start + limit < len(rows) else None
        return httpx.Response(200, json={key: chunk, "_metadata": {
            "collection": key, "offset": start, "limit": limit, "total": len(rows), "next": nxt}})


def user(i, email=None, status="Active", role="Agent", is_owner=False):
    return {"id": i, "name": f"Agent {i}", "email": email or f"agent{i}@acme.test",
            "status": status, "role": role, "isOwner": is_owner}


def person(i, *, assigned=1, created="2026-09-10T15:00:00Z", stage="Lead", contacted=False,
           last_activity=None, name=None, source="Zillow"):
    return {"id": i, "name": name if name is not None else f"Person {i}", "stage": stage,
            "source": source, "contacted": contacted, "assignedUserId": assigned,
            "created": created, "updated": created, "lastActivity": last_activity or created}


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
