import httpx


async def get_json(url, headers=None, params=None, auth=None, timeout=30):
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.get(url, headers=headers or {}, params=params, auth=auth)
        r.raise_for_status()
        return r.json()
