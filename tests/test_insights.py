"""src.insights — the web app's on-read analytics over one user's ledger.

Pure functions, so every case runs on in-memory rows; the synthetic ledger from
db/seed_demo.py stands in for a real account in the end-to-end signal checks.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from db.seed_demo import build_ledger
from src import insights as I
from src.reco.investments import risk_drivers, risk_profile

TODAY = date(2026, 10, 4)


def _rows(*items):
    return [{"id": i, "description": d, "amount": a, "date": dt}
            for i, (d, a, dt) in enumerate(items, start=1)]


@pytest.fixture(scope="module")
def ledger():
    return I.prepare(_rows(*build_ledger(TODAY)))


# --------------------------------------------------------------- formatting
@pytest.mark.parametrize("value,kwargs,expected", [
    (1234567.5, {}, "₹12,34,567.50"),
    (999, {}, "₹999.00"),
    (100000, {"decimals": 0}, "₹1,00,000"),
    (-486, {}, "−₹486.00"),
    (92000, {"decimals": 0, "signed": True}, "+₹92,000"),
    (0, {}, "₹0.00"),
])
def test_format_inr_uses_indian_grouping(value, kwargs, expected):
    assert I.format_inr(value, **kwargs) == expected


@pytest.mark.parametrize("value,expected", [
    (950, "₹950"), (12500, "₹12.5K"), (100000, "₹1L"), (125000, "₹1.2L"),
    (34000000, "₹3.4Cr"), (-74990, "−₹75K"),
])
def test_format_inr_compact(value, expected):
    assert I.format_inr_compact(value) == expected


# ------------------------------------------------------------ preparation
def test_prepare_normalises_db_rows_and_sorts_newest_first():
    out = I.prepare([
        {"id": 1, "description": "Salary", "amount": Decimal("50000.00"), "date": date(2026, 9, 1)},
        {"id": 2, "description": "  Swiggy order ", "amount": Decimal("-420.50"), "date": "2026-09-03"},
        {"id": 3, "description": "Broken", "amount": -1, "date": "Not found"},
    ])
    assert [r["id"] for r in out] == [2, 1]            # bad date dropped, newest first
    assert out[0]["amount"] == -420.5 and isinstance(out[0]["amount"], float)
    assert out[0]["description"] == "Swiggy order"


def test_positive_amounts_are_income_and_outflows_never_are():
    out = {r["description"]: r["category"] for r in I.prepare(_rows(
        ("Refund from store", 300, TODAY), ("Payroll deposit reversal", -300, TODAY)))}
    assert out["Refund from store"] == "income"
    assert out["Payroll deposit reversal"] != "income"


@pytest.mark.parametrize("desc,cat", [
    ("Swiggy order", "dining"), ("Zomato", "dining"), ("BigBasket order", "groceries"),
    ("Ola ride", "transport"), ("Jio recharge", "utilities"), ("Myntra", "shopping"),
    ("Apollo Pharmacy", "health"), ("BookMyShow tickets", "entertainment"),
])
def test_inr_merchant_hints(desc, cat):
    assert I.suggest_category(desc) == cat


def test_hints_match_whole_words_only():
    # "ola" must not fire inside "Coca-Cola" or "Motorola"
    assert I._hint("Coca-Cola six pack") is None
    assert I._hint("Motorola charger") is None


# ---------------------------------------------------------------- overview
def test_summarize_compares_partial_month_with_same_point_last_month():
    s = I.summarize(I.prepare(_rows(
        ("Salary", 10000, date(2026, 10, 1)),
        ("Groceries", -1000, date(2026, 10, 3)),
        ("Salary", 10000, date(2026, 9, 1)),
        ("Groceries", -800, date(2026, 9, 2)),     # inside the same-point window
        ("Rent", -5000, date(2026, 9, 20)),        # after day 4: excluded from the comparison
    )), today=TODAY)
    assert s["partial"] and s["month"] == "October 2026" and s["prev_month_short"] == "Sep"
    assert s["spend"] == 1000 and s["spend_change"] == 25.0
    assert s["income_change"] == 0.0
    assert s["savings_rate"] == 90.0
    assert s["balance"] == 13200


def test_summarize_falls_back_to_latest_month_with_activity():
    s = I.summarize(I.prepare(_rows(("Rent", -100, date(2026, 6, 2)))), today=TODAY)
    assert s["month"] == "June 2026" and not s["partial"]
    assert s["savings_rate"] is None


def test_empty_ledger_is_safe():
    assert I.summarize([], today=TODAY)["balance"] == 0
    assert I.balance_trend([], today=TODAY) is None
    assert I.spend_mix([], today=TODAY)["items"] == []
    assert len(I.cash_flow([], today=TODAY)["months"]) == 6


def test_cash_flow_months_and_ticks(ledger):
    cf = I.cash_flow(ledger, today=TODAY)
    assert [m["label"] for m in cf["months"]] == ["May", "Jun", "Jul", "Aug", "Sep", "Oct"]
    assert cf["months"][-1]["partial"] and not cf["months"][0]["partial"]
    ticks = [t["value"] for t in cf["ticks"]]
    top = max(max(m["income"], m["spend"]) for m in cf["months"])
    assert ticks[0] == 0 and ticks[-1] >= top
    assert all(0 <= m["income_pct"] <= 100 and 0 <= m["spend_pct"] <= 100 for m in cf["months"])


def test_spend_mix_folds_the_tail(ledger):
    mix = I.spend_mix(ledger, today=TODAY, months=6, top=3)
    labels = [i["label"] for i in mix["items"]]
    assert len(labels) == 4 and labels[-1] == "The rest"
    assert sum(i["share"] for i in mix["items"]) == pytest.approx(100, abs=0.5)
    assert mix["items"][0]["width"] == 100


def test_balance_trend_is_svg_path_data(ledger):
    t = I.balance_trend(ledger, today=TODAY)
    assert t["line"].startswith("M") and t["area"].endswith("Z")
    assert 0 <= t["end_y"] <= t["height"]


# ------------------------------------------------------------------ signals
def test_signals_on_the_demo_ledger(ledger):
    s = I.signals(ledger, today=TODAY)

    flagged = {a["description"] for a in s["anomalies"]}
    assert "Croma - laptop" in flagged                  # the planted outlier
    assert "House rent" not in flagged                  # a fixed cost is not a surprise
    laptop = next(a for a in s["anomalies"] if a["description"] == "Croma - laptop")
    assert laptop["ratio"] > 10 and "shopping" in laptop["reason"]

    recurring = {r["description"]: r for r in s["recurring"]}
    assert recurring["Netflix subscription"]["cadence"] == "Monthly"
    assert recurring["House rent"]["monthly"] == 22000

    assert [d["description"] for d in s["duplicates"]] == ["Zomato order"]

    fc = s["forecast"]
    assert fc["month"] == "October 2026"                # built from complete months only
    assert fc["predicted"] > 0 and fc["so_far"] > 0
    assert s["engines"]["anomaly"] in {"IsolationForest", "median-ratio rule"}


def test_risk_drivers_reproduce_the_profile_score(ledger):
    txns = [{"amount": r["amount"], "date": r["date"].isoformat()} for r in ledger]
    d = risk_drivers(txns)
    score, _ = risk_profile(txns)
    assert all(0 <= v <= 1 for v in d.values())
    assert score == round(0.5 * d["savings_rate"] + 0.3 * d["stability"] + 0.2 * d["regularity"], 3)


def test_anomalies_fall_back_without_scikit_learn(ledger, monkeypatch):
    # the hosted Flask image may not ship scikit-learn; the page must still work
    import src.anomaly

    def no_sklearn(*args, **kwargs):
        raise ImportError("No module named 'sklearn'")

    monkeypatch.setattr(src.anomaly, "detect_anomalies", no_sklearn)
    s = I.signals(ledger, today=TODAY)
    assert s["engines"]["anomaly"] == "median-ratio rule"
    assert s["anomalies"][0]["description"] == "Croma - laptop"
    assert "House rent" not in {a["description"] for a in s["anomalies"]}
