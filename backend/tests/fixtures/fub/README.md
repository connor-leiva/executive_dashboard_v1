# Follow Up Boss response fixtures

Replayed by `tests/test_lead_source_ingest.py` to drive the scoped lead-source fetcher through a
real two-page walk.

They follow the shape Follow Up Boss documents for `GET /v1/people` and `GET /v1/deals`:
`_metadata` with `collection`, `offset`, `limit`, `total`, the `next` token and the whole
`nextLink` URL; people with `emails` and `phones` as lists of `{value, isPrimary}` (integer
flags, order not priority), `contacted` as 0/1, `assignedTo` as a name beside `assignedUserId`,
and `price` as a string on one person.

**They were not captured from a live account.** No production Follow Up Boss key was available
when they were written. The people, emails and phone numbers are fictional (555 numbers,
`example.com` addresses), and nothing here comes from Utah Life or any other customer. Re-record
them from a real account before relying on a field name these files assume, notably the deal's
`stageName` and `actualCloseDate`.
