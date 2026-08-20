"""Chart of accounts: the standard chart and its seeder.

The chart is authored in SPEC-chart-of-accounts.md and transcribed here. That document is the
source of truth for the accounting decisions; this module is the source of truth for what the
database contains. When they disagree, the document wins and this file gets corrected.

The governing rule from the spec: **buckets are universal, leaf accounts come from an archetype
library.** Every entity uses the same top-level shape and the same numbering ranges, but a
brokerage never opens 5220 Food and Beverage and a membership business never opens 5010 Agent
Commission. Consolidation merges on `bucket`, so a shared bucket taxonomy stays safe even when
the accounts underneath differ completely.

DECISIONS ALREADY MADE. Recorded here rather than only in the spec, because this file is what
somebody edits when they think a row looks wrong.

- **Revenue is GROSS on both ULRG and Spring B** (Connor, 2026-08-20; chart-document Decision
  1). ULRG books the full commission at 4010/4020 with the agent split as cost of sale at
  5010/5020, and reports Company Dollar rather than Gross Profit. Spring B books the full
  membership price as revenue with the Special Forces and EmpireBuilders commission at 5040.
  Each entity sets its own price, controls delivery and carries refund risk, which is the
  principal test. Net presentation would understate both top lines by millions and make the
  two businesses incomparable.
- **Charitable donations sit below the line, at 9410** (Connor, 2026-08-20; Decision 4).
- **PLACE flow-through sits below the line**, 9030/9040 as other income and 9920 as other
  expense, and is excluded from margin.
- **Provenance colour is a declared exception to the daffodil rule** (Connor, 2026-08-20;
  mapping-spec open question 5), which unblocks Phase 5. The daffodil law is scoped to the
  Forum view in `docs-forum-spec.md`; Books adopts a faint daffodil wash for allocated rows.
  The circular marker in mapping-spec 7.1 is NOT optional either way — colour never carries
  meaning on its own.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import StandardAccount
from .audit import audit

# ── vocabularies ──────────────────────────────────────────────────────────────────────────
# Held as tuples rather than DB enums: this codebase stores enum-ish values as String columns
# (see models.Business.kind), which keeps SQLite and Postgres behaving identically and avoids
# ALTER TYPE on every chart revision. The chart WILL be revised.

BUCKETS = (
    "revenue_transactional", "revenue_program", "revenue_event", "revenue_property",
    "revenue_partnership", "revenue_intercompany", "contra_revenue",
    "cos_producer", "cos_transaction", "cos_delivery", "cos_merchant", "cos_other",
    "salaries_wages", "occupancy", "advertising", "sales_promotion",
    "office_expense", "general_admin",
    "other_income", "interest_expense", "depreciation_amortization", "income_tax",
    "other_expense",
    "balance_sheet",
)

# The five below-the-line buckets stay separate rather than collapsing into one `other`,
# because Net Operating Income is everything above `other_income` and that boundary has to be
# explicit in the vocabulary rather than inferred from account numbers.
BELOW_THE_LINE = ("other_income", "interest_expense", "depreciation_amortization",
                  "income_tax", "other_expense")

SECTIONS = ("revenue", "cogs", "opex", "other_income", "other_expense",
            "asset", "liability", "equity")
RECOGNITION = ("point_in_time", "ratable", "event_date", "not_applicable")
ARCHETYPES = ("transactional", "program", "event", "property", "holding", "dormant")

# Archetype shorthands for the seed table below.
_TXN = ("transactional",)
_PROG = ("program",)
_EVENT = ("event",)
_PROP = ("property",)
_PROG_EVENT = ("program", "event")
_TXN_PROG = ("transactional", "program")
_SELLING = ("transactional", "program", "event")               # everything that sells something
_TXN_PROG_PROP = ("transactional", "program", "property")
# Operating expenses and the balance sheet are universal — every entity that is not dormant
# pays rent, buys software, and has a bank account.
_ALL = ("transactional", "program", "event", "property", "holding")

# The activation matrix uses Yes / Sometimes / Rarely / No. Yes and Sometimes seed the account;
# Rarely and No do not. "Rarely" earns a manual add rather than a dead row on every P&L —
# fifteen unused accounts is exactly the clutter this chart exists to remove.

# ── the chart ─────────────────────────────────────────────────────────────────────────────
# (code, name, bucket, statement, section, normal_balance, recognition, archetypes, definition)
_D = "debit"
_C = "credit"
_NA = "not_applicable"

CHART: tuple[tuple, ...] = (
    # ── 1000 Assets ───────────────────────────────────────────────────────────────────────
    # The chart document defines 4000-9999 and assigns the 1000/2000/3000 series but does not
    # enumerate them, so the balance-sheet minimum is carried forward from the mapping spec's
    # section 3.2 unchanged. None of those codes collide with the new numbering.
    ("1000", "Operating Cash", "balance_sheet", "bs", "asset", _D, None, _ALL, None),
    ("1050", "Undeposited Funds", "balance_sheet", "bs", "asset", _D, None, _ALL, None),
    ("1100", "Accounts Receivable", "balance_sheet", "bs", "asset", _D, None, _ALL, None),
    ("1200", "Prepaid Expenses", "balance_sheet", "bs", "asset", _D, None, _ALL, None),
    ("1300", "Due From Affiliates", "balance_sheet", "bs", "asset", _D, None, _ALL,
     "Receivable from another entity in the portfolio. Eliminates on consolidation."),
    ("1500", "Fixed Assets at Cost", "balance_sheet", "bs", "asset", _D, None, _ALL, None),
    ("1590", "Accumulated Depreciation", "balance_sheet", "bs", "asset", _C, None, _ALL,
     "Contra-asset. Credit-normal; the Fixed Assets tab of Standing-Schedules drives it."),

    # ── 2000 Liabilities ──────────────────────────────────────────────────────────────────
    ("2000", "Accounts Payable", "balance_sheet", "bs", "liability", _C, None, _ALL, None),
    ("2100", "Credit Cards Payable", "balance_sheet", "bs", "liability", _C, None, _ALL, None),
    ("2200", "Accrued Liabilities", "balance_sheet", "bs", "liability", _C, None, _ALL, None),
    ("2300", "Deferred Revenue", "balance_sheet", "bs", "liability", _C, None, _ALL,
     "The offset to every 4200 and 4400 account. Must tie to the Deferred Revenue schedule."),
    ("2400", "Due To Affiliates", "balance_sheet", "bs", "liability", _C, None, _ALL,
     "Payable to another entity in the portfolio. Eliminates on consolidation."),
    ("2500", "Loans Payable", "balance_sheet", "bs", "liability", _C, None, _ALL, None),

    # ── 3000 Equity ───────────────────────────────────────────────────────────────────────
    ("3000", "Member Equity", "balance_sheet", "bs", "equity", _C, None, _ALL, None),
    ("3100", "Member Draws and Distributions", "balance_sheet", "bs", "equity", _D, None, _ALL,
     "Debit-normal within equity: draws reduce it."),
    ("3200", "Retained Earnings", "balance_sheet", "bs", "equity", _C, None, _ALL, None),
    ("3900", "Opening Balance Equity", "balance_sheet", "bs", "equity", _C, None, _ALL,
     "A QuickBooks artifact. A non-zero balance here is a cleanup item, not a real account."),

    # ── 4000-4199 Transactional Revenue · earned at a point in time, no deferral ───────────
    ("4010", "Gross Commission Income, Listing Side", "revenue_transactional", "pl", "revenue",
     _C, "point_in_time", _TXN, None),
    ("4020", "Gross Commission Income, Buyer Side", "revenue_transactional", "pl", "revenue",
     _C, "point_in_time", _TXN, None),
    ("4030", "Referral Fee Income", "revenue_transactional", "pl", "revenue",
     _C, "point_in_time", _TXN, None),
    ("4040", "Loan Origination Commission", "revenue_transactional", "pl", "revenue",
     _C, "point_in_time", _TXN, None),
    ("4050", "Transaction and Admin Fee Income", "revenue_transactional", "pl", "revenue",
     _C, "point_in_time", _TXN, None),
    ("4090", "Other Transactional Revenue", "revenue_transactional", "pl", "revenue",
     _C, "point_in_time", _TXN, None),

    # ── 4200-4399 Program and Membership · earned ratably. Every account here defers. ──────
    ("4210", "Membership Revenue, beCollective", "revenue_program", "pl", "revenue",
     _C, "ratable", _PROG, None),
    ("4220", "Mastermind Revenue, The Forum", "revenue_program", "pl", "revenue",
     _C, "ratable", _PROG, None),
    ("4230", "Coaching and Consulting Revenue", "revenue_program", "pl", "revenue",
     _C, "ratable", _PROG, None),
    ("4240", "Course and Digital Product Revenue", "revenue_program", "pl", "revenue",
     _C, "ratable", _PROG, None),
    ("4290", "Other Program Revenue", "revenue_program", "pl", "revenue",
     _C, "ratable", _PROG, None),

    # ── 4400-4499 Event · fully deferred until the event, then releases at once ────────────
    ("4410", "Ticket Revenue, General", "revenue_event", "pl", "revenue",
     _C, "event_date", _PROG_EVENT, None),
    ("4420", "Ticket Revenue, VIP and Upgrades", "revenue_event", "pl", "revenue",
     _C, "event_date", _PROG_EVENT, None),
    ("4430", "Sponsorship Revenue", "revenue_event", "pl", "revenue",
     _C, "event_date", _PROG_EVENT, None),
    ("4440", "Merchandise and Onsite Sales", "revenue_event", "pl", "revenue",
     _C, "event_date", _PROG_EVENT, None),
    ("4490", "Other Event Revenue", "revenue_event", "pl", "revenue",
     _C, "event_date", _PROG_EVENT, None),

    # ── 4500-4599 Property · earned ratably over the lease ────────────────────────────────
    ("4510", "Rental Income", "revenue_property", "pl", "revenue", _C, "ratable", _PROP, None),
    ("4520", "Common Area and Expense Reimbursement", "revenue_property", "pl", "revenue",
     _C, "ratable", _PROP, None),
    ("4590", "Other Property Revenue", "revenue_property", "pl", "revenue",
     _C, "ratable", _PROP, None),

    # ── 4600-4699 Partnership and Passive ─────────────────────────────────────────────────
    ("4610", "Joint Venture Income", "revenue_partnership", "pl", "revenue",
     _C, "point_in_time", _TXN_PROG_PROP, None),
    ("4620", "Revenue Share Income, Third Party", "revenue_partnership", "pl", "revenue",
     _C, "point_in_time", _TXN_PROG_PROP, None),
    ("4630", "Royalty and Licensing Income", "revenue_partnership", "pl", "revenue",
     _C, "point_in_time", _TXN_PROG_PROP, None),
    ("4690", "Other Partnership Income", "revenue_partnership", "pl", "revenue",
     _C, "point_in_time", _TXN_PROG_PROP, None),

    # ── 4700-4749 Intercompany Revenue · MUST eliminate on consolidation ──────────────────
    # Without a designated range, consolidated revenue is overstated by the full amount of
    # every internal transfer and nobody notices, because each entity's own P&L is correct.
    ("4710", "Intercompany Revenue Share", "revenue_intercompany", "pl", "revenue",
     _C, "point_in_time", _ALL, None),
    ("4720", "Intercompany Management Fee", "revenue_intercompany", "pl", "revenue",
     _C, "point_in_time", _ALL, None),
    ("4730", "Intercompany Rent", "revenue_intercompany", "pl", "revenue",
     _C, "point_in_time", _ALL, None),

    # ── 4900-4999 Contra-Revenue · negative revenue, never an expense ──────────────────────
    # Booking these as expenses overstates gross revenue, understates gross margin, and hides
    # the churn signal entirely. This is where the Forum renewal work reconciles to the books.
    ("4910", "Refunds and Cancellations", "contra_revenue", "pl", "revenue",
     _D, "point_in_time", _SELLING, None),
    ("4920", "Chargebacks", "contra_revenue", "pl", "revenue",
     _D, "point_in_time", _SELLING, None),
    ("4930", "Discounts and Scholarships", "contra_revenue", "pl", "revenue",
     _D, "point_in_time", _SELLING, None),
    ("4940", "Failed and Written-Off Payments", "contra_revenue", "pl", "revenue",
     _D, "point_in_time", _SELLING, None),

    # ── 5000-5099 Producer Compensation · paid to whoever generated the revenue ────────────
    ("5010", "Agent Commission, Listing Side", "cos_producer", "pl", "cogs", _D, _NA, _TXN, None),
    ("5020", "Agent Commission, Buyer Side", "cos_producer", "pl", "cogs", _D, _NA, _TXN, None),
    ("5030", "Loan Officer Compensation", "cos_producer", "pl", "cogs", _D, _NA, _TXN, None),
    ("5040", "Sales Commission, Third Party Closers", "cos_producer", "pl", "cogs",
     _D, _NA, _TXN_PROG,
     "Special Forces and EmpireBuilders land here. Third-party commission on membership "
     "enrollment is textbook cost of sale, not an operating expense."),
    ("5050", "Referral Fees Paid", "cos_producer", "pl", "cogs", _D, _NA, _TXN_PROG, None),
    ("5090", "Other Producer Compensation", "cos_producer", "pl", "cogs", _D, _NA, _TXN_PROG, None),

    # ── 5100-5199 Third-Party Transaction Costs ───────────────────────────────────────────
    ("5110", "Broker and Franchise Fees", "cos_transaction", "pl", "cogs", _D, _NA, _TXN, None),
    ("5120", "Transaction Coordination", "cos_transaction", "pl", "cogs", _D, _NA, _TXN, None),
    ("5130", "Credit Reports and Verifications", "cos_transaction", "pl", "cogs", _D, _NA, _TXN, None),
    ("5140", "Appraisal and Inspection, Pass-Through", "cos_transaction", "pl", "cogs",
     _D, _NA, _TXN, None),
    ("5190", "Other Transaction Costs", "cos_transaction", "pl", "cogs", _D, _NA, _TXN, None),

    # ── 5200-5299 Delivery and Fulfillment · the cost of delivering what was sold ──────────
    ("5210", "Venue and Facilities", "cos_delivery", "pl", "cogs", _D, _NA, _PROG_EVENT, None),
    ("5220", "Food and Beverage", "cos_delivery", "pl", "cogs", _D, _NA, _PROG_EVENT, None),
    ("5230", "Speaker and Talent Fees", "cos_delivery", "pl", "cogs", _D, _NA, _PROG_EVENT, None),
    ("5240", "Production, AV and Staging", "cos_delivery", "pl", "cogs", _D, _NA, _PROG_EVENT, None),
    ("5250", "Event Staffing and Contract Labor", "cos_delivery", "pl", "cogs",
     _D, _NA, _PROG_EVENT, None),
    ("5260", "Attendee Materials and Swag", "cos_delivery", "pl", "cogs", _D, _NA, _PROG_EVENT, None),
    ("5270", "Platform and Delivery Technology", "cos_delivery", "pl", "cogs",
     _D, _NA, _PROG_EVENT,
     "Only the seats or usage that scale with member count. General SaaS is 8520. If the line "
     "is ambiguous it goes to 8520 — calling a fixed cost variable flatters unit economics."),
    ("5290", "Other Delivery Costs", "cos_delivery", "pl", "cogs", _D, _NA, _PROG_EVENT, None),

    # ── 5300-5399 Merchant and Financing ──────────────────────────────────────────────────
    ("5310", "Merchant Processing Fees", "cos_merchant", "pl", "cogs", _D, _NA, _SELLING,
     "Fees that scale with revenue volume, e.g. card processing on a $13,000 enrollment. "
     "Fees for holding a bank account are 8620."),
    ("5320", "Payment Platform Fees", "cos_merchant", "pl", "cogs", _D, _NA, _SELLING, None),
    ("5330", "Financing and Installment Costs", "cos_merchant", "pl", "cogs", _D, _NA, _SELLING, None),
    ("5340", "Chargeback Fees", "cos_merchant", "pl", "cogs", _D, _NA, _SELLING,
     "The fee only. The lost revenue is contra-revenue at 4920."),

    # ── 5400-5499 Other Direct Costs ──────────────────────────────────────────────────────
    ("5410", "Marketing Costs, Directly Attributable", "cos_other", "pl", "cogs",
     _D, _NA, _SELLING,
     "Deliberately narrow: spend traceable to a specific closed transaction. Campaign spend "
     "is 6000 Advertising. When in doubt it is 6000."),
    ("5490", "Other Cost of Sale", "cos_other", "pl", "cogs", _D, _NA, _SELLING, None),

    # ── 6000 Advertising ──────────────────────────────────────────────────────────────────
    ("6010", "Print and Direct Mail", "advertising", "pl", "opex", _D, _NA, _ALL, None),
    ("6020", "Billboard", "advertising", "pl", "opex", _D, _NA, _ALL, None),
    ("6030", "Radio", "advertising", "pl", "opex", _D, _NA, _ALL, None),
    ("6040", "Online Marketing, SEO Organic and Paid", "advertising", "pl", "opex",
     _D, _NA, _ALL, None),
    ("6050", "Online Marketing, Social", "advertising", "pl", "opex", _D, _NA, _ALL, None),
    ("6060", "Online Marketing, Lead Buy", "advertising", "pl", "opex", _D, _NA, _ALL, None),
    ("6061", "Internet Lead Sources", "advertising", "pl", "opex", _D, _NA, _ALL,
     "Expandable by source as sub-accounts. Per-source lead spend is one of the few places "
     "account-level detail is genuinely actionable for a team."),
    ("6070", "Sign Purchase", "advertising", "pl", "opex", _D, _NA, _ALL, None),
    ("6080", "Sign Installation", "advertising", "pl", "opex", _D, _NA, _ALL, None),
    ("6090", "Other Advertising", "advertising", "pl", "opex", _D, _NA, _ALL, None),

    # ── 6500 Sales Promotion ──────────────────────────────────────────────────────────────
    ("6510", "Travel", "sales_promotion", "pl", "opex", _D, _NA, _ALL, None),
    ("6520", "Lodging", "sales_promotion", "pl", "opex", _D, _NA, _ALL, None),
    ("6530", "Meals and Entertainment", "sales_promotion", "pl", "opex", _D, _NA, _ALL, None),
    ("6540", "Gifts", "sales_promotion", "pl", "opex", _D, _NA, _ALL, None),
    ("6550", "Coaching, Training and Education", "sales_promotion", "pl", "opex",
     _D, _NA, _ALL, None),
    ("6560", "Conferences and Conventions", "sales_promotion", "pl", "opex", _D, _NA, _ALL, None),
    # Charitable Donations moved OUT of this bucket to 9410, below the line (Connor,
    # 2026-08-20). Do not re-add a 6570 here — migration 0040 repoints it.
    ("6590", "Other Sales Promotion", "sales_promotion", "pl", "opex", _D, _NA, _ALL, None),

    # ── 7000 Occupancy ────────────────────────────────────────────────────────────────────
    ("7010", "Rent", "occupancy", "pl", "opex", _D, _NA, _ALL, None),
    ("7020", "Utilities", "occupancy", "pl", "opex", _D, _NA, _ALL, None),
    ("7030", "Repairs and Maintenance", "occupancy", "pl", "opex", _D, _NA, _ALL, None),
    ("7040", "Commercial Property Taxes", "occupancy", "pl", "opex", _D, _NA, _ALL, None),
    ("7090", "Other Occupancy", "occupancy", "pl", "opex", _D, _NA, _ALL,
     "Property entities have no cost of sale; their operating costs land in this bucket."),

    # ── 7500 Office Expenses ──────────────────────────────────────────────────────────────
    # Computer Software deliberately absent: the MREA template listed it in two places, which
    # is exactly the ambiguity that causes miscategorization. Recurring SaaS has one home, 8520.
    ("7510", "Telephone", "office_expense", "pl", "opex", _D, _NA, _ALL, None),
    ("7520", "Internet", "office_expense", "pl", "opex", _D, _NA, _ALL, None),
    ("7530", "Mobile Phone", "office_expense", "pl", "opex", _D, _NA, _ALL, None),
    ("7540", "Communication Equipment and Services", "office_expense", "pl", "opex",
     _D, _NA, _ALL, None),
    ("7550", "Office Supplies", "office_expense", "pl", "opex", _D, _NA, _ALL, None),
    ("7560", "Postage", "office_expense", "pl", "opex", _D, _NA, _ALL, None),
    ("7570", "Office Equipment Rental and Repairs", "office_expense", "pl", "opex",
     _D, _NA, _ALL, None),
    ("7580", "Computer Hardware", "office_expense", "pl", "opex", _D, _NA, _ALL, None),
    ("7590", "Other Office Expense", "office_expense", "pl", "opex", _D, _NA, _ALL, None),

    # ── 8000 Salaries, Wages and Contract Labor ───────────────────────────────────────────
    # Contract Labor is absent from the MREA template and is a larger line than the entire
    # Occupancy or Advertising bucket on ULRG. Do not drop it.
    ("8010", "Salaries, Staff", "salaries_wages", "pl", "opex", _D, _NA, _ALL, None),
    ("8020", "Payroll Taxes, Staff", "salaries_wages", "pl", "opex", _D, _NA, _ALL, None),
    ("8030", "Other Employee Benefits, Staff", "salaries_wages", "pl", "opex", _D, _NA, _ALL, None),
    ("8040", "Payroll Processing Fees", "salaries_wages", "pl", "opex", _D, _NA, _ALL, None),
    ("8050", "Contract Labor, Named Contractors", "salaries_wages", "pl", "opex",
     _D, _NA, _ALL, None),
    ("8060", "Contract Labor, Virtual Assistants", "salaries_wages", "pl", "opex",
     _D, _NA, _ALL, None),
    ("8070", "Contract Labor, Other", "salaries_wages", "pl", "opex", _D, _NA, _ALL, None),

    # ── 8500 General and Administrative ───────────────────────────────────────────────────
    ("8510", "Dues and Subscriptions", "general_admin", "pl", "opex", _D, _NA, _ALL, None),
    ("8520", "Computer Technology Subscriptions", "general_admin", "pl", "opex", _D, _NA, _ALL,
     "The single home for recurring SaaS. The member-scaling portion is 5270."),
    ("8530", "Vehicle Expense", "general_admin", "pl", "opex", _D, _NA, _ALL, None),
    ("8540", "Professional Services, Accounting and Tax Prep", "general_admin", "pl", "opex",
     _D, _NA, _ALL, None),
    ("8550", "Professional Services, Legal", "general_admin", "pl", "opex", _D, _NA, _ALL, None),
    ("8560", "Professional Services, Other", "general_admin", "pl", "opex", _D, _NA, _ALL, None),
    ("8570", "Personal Property Taxes", "general_admin", "pl", "opex", _D, _NA, _ALL, None),
    ("8580", "Business Licenses and Taxes", "general_admin", "pl", "opex", _D, _NA, _ALL, None),
    ("8590", "Business Use Tax", "general_admin", "pl", "opex", _D, _NA, _ALL, None),
    ("8600", "E and O Insurance", "general_admin", "pl", "opex", _D, _NA, _ALL, None),
    ("8610", "Other Insurance", "general_admin", "pl", "opex", _D, _NA, _ALL, None),
    ("8620", "Bank Charges", "general_admin", "pl", "opex", _D, _NA, _ALL,
     "Fees for holding an account: monthly service charge, wire fees, incoming ACH. Fees that "
     "scale with revenue volume are 5310."),
    ("8630", "Bad Debt Expense", "general_admin", "pl", "opex", _D, _NA, _ALL, None),
    ("8690", "Other General and Administrative", "general_admin", "pl", "opex", _D, _NA, _ALL, None),

    # ── 9000 Below the operating line ─────────────────────────────────────────────────────
    # This is what makes Net Operating Income mean something. With depreciation and interest
    # inside the operating buckets, NOI is net income wearing the wrong label and every margin
    # comparison is distorted by capital structure rather than operations — the property
    # holdcos carry mortgages and the coaching entities do not.
    ("9010", "Interest Income", "other_income", "pl", "other_income", _C, _NA, _ALL, None),
    ("9020", "Gain or Loss on Asset Disposal", "other_income", "pl", "other_income",
     _C, _NA, _ALL, None),
    # PLACE flow-through, resolved: below the line, as other income and other expense.
    # Roughly $3.49M out and $2.76M in on ULRG. Run through revenue and cost of sale it would
    # inflate the top line by millions of pass-through and make every margin percentage on the
    # dashboard meaningless. Below the line it is outside gross profit and outside Net
    # Operating Income by construction, which is the whole reason that section exists.
    ("9030", "PLACE Expense Reimbursement", "other_income", "pl", "other_income",
     _C, _NA, _TXN, "PLACE flow-through. Below the line, so outside every margin calculation."),
    ("9040", "PLACE Profit Share", "other_income", "pl", "other_income",
     _C, _NA, _TXN, "PLACE flow-through. Below the line, so outside every margin calculation."),
    ("9090", "Other Income", "other_income", "pl", "other_income", _C, _NA, _ALL, None),
    ("9110", "Interest Expense", "interest_expense", "pl", "other_expense", _D, _NA, _ALL,
     "Posting target for the Debt Amortization tab of Standing-Schedules.xlsx."),
    ("9210", "Depreciation Expense", "depreciation_amortization", "pl", "other_expense",
     _D, _NA, _ALL, "Posting target for the Fixed Assets tab of Standing-Schedules.xlsx."),
    ("9220", "Amortization Expense", "depreciation_amortization", "pl", "other_expense",
     _D, _NA, _ALL, None),
    ("9310", "State Income and Franchise Tax", "income_tax", "pl", "other_expense",
     _D, _NA, _ALL, None),
    # Below the line by Connor's decision (2026-08-20), resolving Decision 4. Giving to a cause
    # is not a cost of running the business, and leaving it inside an operating bucket makes
    # Net Operating Income move with a discretionary choice — two entities with identical
    # operations would show different operating margins. Below the line it is visible and
    # outside every margin. The entity-level DEDUCTIBILITY is a separate question and still
    # Acuity's; presentation here does not decide it.
    ("9410", "Charitable Contributions", "other_expense", "pl", "other_expense",
     _D, _NA, _ALL,
     "Below the operating line, so outside gross profit, Net Operating Income and every "
     "margin. Presentation policy only — tax treatment is Acuity's call."),
    ("9910", "Other Expense", "other_expense", "pl", "other_expense", _D, _NA, _ALL, None),
    ("9920", "PLACE Income Transfer", "other_expense", "pl", "other_expense",
     _D, _NA, _TXN, "PLACE flow-through. Below the line, so outside every margin calculation."),
)

# Eliminate on consolidation, and exempt from provenance flagging (SPEC 6.4).
_INTERCOMPANY_CODES = {"1300", "2400", "4710", "4720", "4730"}


def chart_rows() -> list[dict]:
    """The chart as plain dicts, in statement order. Shared by the seeder and its tests."""
    rows = []
    for i, (code, name, bucket, statement, section, normal, recog, arch, definition) in \
            enumerate(CHART):
        rows.append({
            "code": code, "name": name, "bucket": bucket, "statement": statement,
            "section": section, "normal_balance": normal,
            "recognition": recog, "archetypes": list(arch),
            "sort_order": (i + 1) * 10,
            "is_intercompany_account": code in _INTERCOMPANY_CODES,
            "definition": definition,
        })
    return rows


async def seed_standard_chart(s: AsyncSession, tenant_id: uuid.UUID,
                              actor_user_id: uuid.UUID | None = None) -> dict:
    """Seed or refresh the standard chart for one tenant. Idempotent.

    Existing accounts are updated in place rather than replaced, so `standard_account.id` stays
    stable — `coa_map.standard_account_id` points at it, and re-seeding must never orphan a
    map that someone spent hours building. Accounts a tenant has added by hand are left alone:
    the chart is a starting point, not a cage.
    """
    have = {a.code: a for a in (await s.execute(
        select(StandardAccount).where(StandardAccount.tenant_id == tenant_id))).scalars()}
    created = updated = 0
    for row in chart_rows():
        existing = have.get(row["code"])
        if existing is None:
            s.add(StandardAccount(tenant_id=tenant_id, is_active=True, **row))
            created += 1
            continue
        # Refresh the policy fields; leave is_active alone so a deliberate deactivation sticks.
        changed = False
        for field, value in row.items():
            if getattr(existing, field) != value:
                setattr(existing, field, value)
                changed = True
        updated += int(changed)

    result = {"created": created, "updated": updated, "total": len(CHART)}
    audit(s, tenant_id, actor_user_id, "coa.seed_standard_chart",
          target_type="tenant", target_id=tenant_id, detail=result)
    await s.commit()
    return result


def accounts_for_archetype(archetype: str) -> list[dict]:
    """Which leaf accounts an entity of this archetype opens.

    `dormant` gets the balance sheet only — a dormant entity still holds cash and owes money,
    it just is not trading. Sympli today is the case in point: 155 accounts, 13 with any
    activity, and $304 of operating expense in the month.
    """
    if archetype == "dormant":
        return [r for r in chart_rows() if r["statement"] == "bs"]
    return [r for r in chart_rows() if archetype in (r["archetypes"] or ())]
