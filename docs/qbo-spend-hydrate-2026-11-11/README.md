# Realtor.com spend after the QuickBooks ledger hydrate

All four shots are of the development fixture (fictional figures), at 1440 × 900 and 2× density.
None shows a live Utah Life amount.

How the "before" state was made: the fixture's 36 ULRG + Team Purchases were stored the way
Utah Life's older Purchases sit in production, as headers with no `book_txn_line` rows and no
`vendor_qbo_id`. Then `sync_qbo_txns` ran unchanged against a stubbed QuickBooks client that returns
those same 36 Purchases in QuickBooks' shape: the Amex on the header, account 365 on the line,
and vendor 962 or 1568 as `EntityRef`.

| Screen | Before | After |
| --- | --- | --- |
| Settings › Lead sources › Realtor.com › QuickBooks spend | [before](before-settings-quickbooks-spend.jpg): the three rules are correct; "No QuickBooks lines match these rules yet." | [after](after-settings-quickbooks-spend.jpg): "What the saved rules catch" lists Feb to Nov 2026, and the vendors resolve to Realtor.com · 962 and Opcity, Inc · 1568 |
| Realtor.com cohort report, Feb 1 to today | [before](before-realtor-cohorts.jpg): QuickBooks spend $0, ROI "—", cost per lead $0 | [after](after-realtor-cohorts.jpg): spend, net after Sympli's 50%, ROI and cost per lead filled |

The sync logged, in order:

```
[qbo_txns] realm=realm-ulrg mode=hydrate missing_line_headers=36 rows=36 lines=36
[qbo_txns] realm=realm-ulrg mode=cdc rows=0 lines=0
```
