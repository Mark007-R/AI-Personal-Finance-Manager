"""Per-user, risk-profiled investment recommendation.

Replaces the audited `invest.py` logic, which (1) summed transactions across ALL
users (no user_id filter — a privacy + correctness bug) and (2) merely filtered a
catalog by `min_investment <= total_balance` with no personalization.

This module computes a per-user risk profile from that user's OWN cash-flow
signals (savings rate + spend volatility + income regularity) and scores each
option by both affordability and risk-fit, so two users with the same balance but
different volatility get different rankings.

`invest.invest()` keeps its route signature and delegates here.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

import numpy as np

# Risk tier ordering used to match an option's risk to the user's tolerance.
_RISK_RANK = {"low": 0, "medium": 1, "high": 2}

# A small synthetic catalog (media-discipline: no proprietary product data) used
# when no DB-backed catalog is supplied. Mirrors the shape of the FinTrack tables
# (RecurringDeposits / Bonds / BankStockData / BankLifeInsurance).
DEFAULT_CATALOG = [
    {"name": "12-Month Recurring Deposit", "type": "Recurring Deposit",
     "min_investment": 500, "expected_return_pct": 6.5, "risk": "low"},
    {"name": "Government Savings Bond", "type": "Bond",
     "min_investment": 1000, "expected_return_pct": 7.2, "risk": "low"},
    {"name": "Investment-Grade Corporate Bond", "type": "Bond",
     "min_investment": 5000, "expected_return_pct": 8.5, "risk": "medium"},
    {"name": "Balanced Index Fund", "type": "Bank Stock",
     "min_investment": 2500, "expected_return_pct": 11.0, "risk": "medium"},
    {"name": "Bank Blue-Chip Equity", "type": "Bank Stock",
     "min_investment": 3000, "expected_return_pct": 13.5, "risk": "high"},
    {"name": "Growth Equity Portfolio", "type": "Bank Stock",
     "min_investment": 7500, "expected_return_pct": 16.0, "risk": "high"},
    {"name": "Whole Life Insurance Plan", "type": "Life Insurance",
     "min_investment": 1200, "expected_return_pct": 5.0, "risk": "low"},
]


def _parse_date(s):
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(str(s)[:10], fmt)
        except Exception:
            continue
    return None


def risk_profile(transactions: list[dict]) -> tuple[float, str]:
    """Derive a [0,1] risk-tolerance score and a label from a user's own txns.

    Higher score => higher risk tolerance. Drivers (see `risk_drivers`):
      * savings rate (income vs spend) — more cushion -> more tolerance
      * spend volatility (CV of monthly outflow) — steadier -> more tolerance
      * income regularity (# of inflow months) — more regular -> more tolerance
    """
    if not transactions:
        return 0.3, "conservative"

    d = risk_drivers(transactions)
    score = 0.5 * d["savings_rate"] + 0.3 * d["stability"] + 0.2 * d["regularity"]
    score = round(float(max(0.0, min(1.0, score))), 3)
    label = "aggressive" if score >= 0.66 else "balanced" if score >= 0.4 else "conservative"
    return score, label


def risk_drivers(transactions: list[dict]) -> dict:
    """The three [0,1] signals `risk_profile` blends (0.5 / 0.3 / 0.2), exposed so
    the UI can show why a user got their profile."""
    inflow = sum(float(t["amount"]) for t in transactions if float(t["amount"]) > 0)
    outflow = sum(-float(t["amount"]) for t in transactions if float(t["amount"]) < 0)

    monthly = defaultdict(float)
    inflow_months = set()
    for t in transactions:
        d = _parse_date(t.get("date"))
        if not d:
            continue
        amt = float(t["amount"])
        if amt < 0:
            monthly[(d.year, d.month)] += -amt
        elif amt > 0:
            inflow_months.add((d.year, d.month))

    savings_rate = 0.0
    if inflow > 0:
        savings_rate = max(0.0, min(1.0, (inflow - outflow) / inflow))

    vals = np.array(list(monthly.values()), dtype=float)
    cv = float(np.std(vals) / (np.mean(vals) + 1e-9)) if len(vals) >= 2 else 0.5
    stability = max(0.0, 1.0 - min(cv, 1.0))

    n_months = len({(_parse_date(t["date"]).year, _parse_date(t["date"]).month)
                    for t in transactions if _parse_date(t.get("date"))})
    regularity = (len(inflow_months) / n_months) if n_months else 0.0

    return {"savings_rate": savings_rate, "stability": stability, "regularity": regularity}


def _balance_from_txns(transactions: list[dict]) -> float:
    return float(sum(float(t.get("amount", 0)) for t in transactions))


def recommend(total_balance: float, risk_score: float,
              catalog: list[dict] | None = None, top_n: int = 6) -> list[dict]:
    """Rank catalog options by affordability x risk-fit for THIS user."""
    catalog = catalog or DEFAULT_CATALOG
    # map the continuous risk score to a target tier (0=low..2=high)
    target_tier = 0 if risk_score < 0.4 else 1 if risk_score < 0.66 else 2
    scored = []
    for opt in catalog:
        mininv = float(opt.get("min_investment", 0))
        if mininv > max(total_balance, 0):
            continue  # genuinely unaffordable
        tier = _RISK_RANK.get(str(opt.get("risk", "medium")).lower(), 1)
        # risk-fit: 1.0 when the option's tier matches the user's, decaying with distance
        risk_fit = 1.0 - 0.4 * abs(tier - target_tier)
        # affordability headroom (sqrt to avoid over-rewarding huge balances)
        headroom = min(1.0, (total_balance / mininv) ** 0.5 / 3.0) if mininv else 1.0
        ret = float(opt.get("expected_return_pct", 0)) / 20.0  # normalise ~[0,1]
        suit = max(0.0, min(1.0, 0.55 * risk_fit + 0.25 * headroom + 0.20 * ret))
        scored.append({**opt, "min_investment": mininv,
                       "expected_return_pct": float(opt.get("expected_return_pct", 0)),
                       "risk": str(opt.get("risk", "medium")),
                       "suitability": round(suit, 4)})
    scored.sort(key=lambda o: o["suitability"], reverse=True)
    return scored[:top_n]


def recommend_for_user(transactions: list[dict], total_balance: float | None = None,
                       catalog: list[dict] | None = None) -> dict:
    """End-to-end: profile the user, then rank options. Used by the API + Flask route."""
    balance = total_balance if total_balance is not None else _balance_from_txns(transactions)
    score, label = risk_profile(transactions)
    return {"total_balance": round(float(balance), 2), "risk_profile": label,
            "risk_score": score,
            "drivers": risk_drivers(transactions) if transactions else None,
            "options": recommend(balance, score, catalog)}
