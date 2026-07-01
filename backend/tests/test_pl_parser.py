"""Post-cutover smoke test for the QBO summary ProfitAndLoss parser.

Asserts the six group totals (Income, COGS, GrossProfit, Expenses,
NetOperatingIncome, NetIncome) come back non-null from a representative summary
response. Capture a live response and replace SAMPLE on first real run.
"""
from app.integrations.qbo import parse_pl


def _group(name: str, label: str, total: str) -> dict:
    return {
        "group": name,
        "Summary": {"ColData": [{"value": label}, {"value": total}]},
    }


SAMPLE = {
    "Header": {"ReportName": "ProfitAndLoss"},
    "Columns": {"Column": [{"ColTitle": ""}, {"ColTitle": "Total"}]},
    "Rows": {
        "Row": [
            _group("Income", "Total Income", "420000.00"),
            _group("COGS", "Total Cost of Goods Sold", "252000.00"),
            _group("GrossProfit", "Gross Profit", "168000.00"),
            _group("Expenses", "Total Expenses", "96000.00"),
            _group("NetOperatingIncome", "Net Operating Income", "72000.00"),
            _group("NetIncome", "Net Income", "72000.00"),
        ]
    },
}


def test_parse_pl_returns_six_totals():
    out = parse_pl(SAMPLE)
    assert set(out) == {"revenue", "cogs", "gross_profit", "opex", "noi", "net_income"}
    assert out["revenue"] == 420000.0
    assert out["cogs"] == 252000.0
    assert out["gross_profit"] == 168000.0
    assert out["opex"] == 96000.0
    assert out["noi"] == 72000.0
    assert out["net_income"] == 72000.0
    # None of the six may be null after the modernized Reports cutover.
    assert all(v is not None for v in out.values())


def test_parse_pl_handles_missing_groups_gracefully():
    out = parse_pl({"Rows": {"Row": [_group("Income", "Total Income", "1000")]}})
    assert out["revenue"] == 1000.0
    assert out["noi"] == 0.0  # absent groups default to 0, not error
