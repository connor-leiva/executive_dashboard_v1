# QBO cutover runbook — split "Spring B" into three routed entities

Accounting split the single **Spring B** QuickBooks account into three separate QBO
companies (Coaching, The Forum, beCollective). This is the one-time cutover to reflect
that in the dashboard. **Everything is done in Settings › Integrations › QuickBooks — no
code or deploy needed.** Do the steps in order; the ordering avoids a transient
double-count.

> Why the order matters: portfolio totals sum every entity's P&L. The old combined
> Spring B P&L must stop counting *before* the three split entities are connected, or
> revenue is briefly counted twice.

## Steps

1. **Exclude the old Spring B P&L from the portfolio first.**
   Expand QuickBooks → **Edit** on *Spring B* → uncheck **Include in portfolio totals** →
   *Save changes*. (Its operational Forum/beCollective member views are GHL-fed and are
   unaffected — this only drops its stale combined P&L from the roll-up.)

2. **Disconnect the old combined Spring B QBO.**
   **Disconnect** on *Spring B*. Tokens are cleared; synced history stays (that's fine —
   it no longer counts). If you want its stale P&L snapshots gone entirely, remove the
   entity instead (delete), but disconnect is enough for the cutover.

3. **Connect The Forum's QBO.**
   **Connect another entity** → Name `The Forum` → *Show its P&L on* → **The Forum** →
   keep **Feed into Acumyn Books** checked → *Continue to QuickBooks* → authorize the
   Forum's QuickBooks company in Intuit.

4. **Connect beCollective's QBO.**
   **Connect another entity** → Name `beCollective` → *Show its P&L on* → **beCollective**
   → authorize.

5. **Connect the Coaching QBO.**
   **Connect another entity** → Name `Spring B Coaching` (or whatever you prefer) →
   *Show its P&L on* → **Its own new page** (or an existing page — your call) → authorize.

6. **Verify.**
   - Each page (The Forum, beCollective, the coaching page) shows its **own** P&L.
   - Portfolio revenue/NOI = the sum of the three (plus ULRG + Sympli) — not the old
     combined Spring B figure on top.
   - New pages appear in the left nav automatically.

## After the cutover — Books onboarding

Each newly-connected entity enters **Books in onboarding mode**: it backfills
transactions and its cross-entity transfers show up as intercompany escalations, but
those **won't block month-end close** until you activate real intercompany rules for
them (rent, co-op cap, payroll split). Do that in the Books module when you're ready,
then the onboarding flag clears.

- Default backfill starts Jan 1 of the current year. To cap initial volume, set a
  **Backfill transactions from** date in the connect modal.
- To keep an entity's P&L on its page but *out* of Books, uncheck **Feed into Acumyn
  Books** when connecting (or via Edit later).

## If you mis-route an entity

Just **Edit** it and change *Show its P&L on* — routing is display-only, so the ledger
keys never move and nothing has to re-sync.
