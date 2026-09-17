"""Dashboard data + figure layer (Day-9).

Pure, importable functions that turn transaction lists and API responses into the
DataFrames and matplotlib figures the Streamlit app renders. Kept separate from
the Streamlit UI so the whole data path is unit-testable headlessly (the Day-9
harness imports this module directly — no Streamlit runtime required).

Media discipline: the demo stream is synthetic (seeded), never real financial
data. In the running app these transactions come from the JWT-scoped
`GET /transactions` endpoint instead.
"""
from __future__ import annotations

import random
from collections import defaultdict
from datetime import date, timedelta

import matplotlib
matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
import numpy as np
import pandas as pd

CATEGORIES = ["groceries", "dining", "transport", "utilities", "rent",
              "entertainment", "health", "shopping"]

# representative merchants per category (synthetic; mirrors the categorizer's vocab)
_MERCHANTS = {
    "groceries": ["WALMART", "COSTCO", "KROGER", "ALDI", "SAFEWAY"],
    "dining": ["STARBUCKS", "CHIPOTLE", "DOORDASH", "MCDONALDS", "UBER EATS"],
    "transport": ["UBER", "SHELL FUEL", "LYFT", "METRO TRANSIT", "PARKING"],
    "utilities": ["PG&E", "COMCAST", "VERIZON", "AT&T", "CITY WATER"],
    "rent": ["GREENBRIAR APARTMENTS"],
    "entertainment": ["NETFLIX", "SPOTIFY", "HULU", "DISNEY+", "STEAM GAMES"],
    "health": ["CVS PHARMACY", "QUEST DIAGNOSTIC", "DENTAL CLINIC"],
    "shopping": ["AMAZON", "TARGET", "BEST BUY", "NIKE", "IKEA"],
}


def synthetic_user_stream(seed: int = 7, months: int = 8) -> list[dict]:
    """A seeded, synthetic per-user transaction stream (income + spend + 1 anomaly)."""
    rng = random.Random(seed)
    start = date(2025, 11, 1) - timedelta(days=30 * months)
    txns: list[dict] = []
    for m in range(months):
        month_start = date(start.year + (start.month - 1 + m) // 12,
                           (start.month - 1 + m) % 12 + 1, 1)
        # monthly income
        txns.append({"date": month_start.isoformat(), "merchant": "ACME PAYROLL",
                     "category": "income", "amount": round(rng.uniform(4200, 4800), 2)})
        # fixed rent
        txns.append({"date": (month_start + timedelta(days=1)).isoformat(),
                     "merchant": "GREENBRIAR APARTMENTS", "category": "rent",
                     "amount": -1650.0})
        # variable spend
        for _ in range(rng.randint(18, 26)):
            cat = rng.choice(CATEGORIES)
            merch = rng.choice(_MERCHANTS[cat])
            base = {"groceries": 70, "dining": 28, "transport": 22, "utilities": 120,
                    "rent": 1650, "entertainment": 15, "health": 45, "shopping": 60}[cat]
            amt = -round(abs(rng.gauss(base, base * 0.4)) + 1, 2)
            day = month_start + timedelta(days=rng.randint(2, 27))
            txns.append({"date": day.isoformat(), "merchant": merch,
                         "category": cat, "amount": amt})
    # one injected anomaly (large electronics splurge)
    txns.append({"date": (start + timedelta(days=30 * (months - 1) + 12)).isoformat(),
                 "merchant": "BEST BUY", "category": "shopping", "amount": -2399.0})
    return txns


# --------------------------------------------------------------------------- #
# DataFrames
# --------------------------------------------------------------------------- #
def _month_key(d: str) -> str:
    return str(d)[:7]


def category_month_matrix(transactions: list[dict]) -> pd.DataFrame:
    """month x category spend matrix (absolute outflow), for the heat map."""
    agg: dict = defaultdict(lambda: defaultdict(float))
    for t in transactions:
        amt = float(t["amount"])
        if amt >= 0:
            continue  # spend only
        cat = t.get("category", "other")
        agg[_month_key(t["date"])][cat] += -amt
    months = sorted(agg.keys())
    cats = CATEGORIES
    mat = pd.DataFrame([[round(agg[mn].get(c, 0.0), 2) for c in cats] for mn in months],
                       index=months, columns=cats)
    return mat


def balance_trend(transactions: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(transactions)
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    df["balance"] = df["amount"].astype(float).cumsum()
    return df[["date", "amount", "balance"]].reset_index(drop=True)


def anomaly_alert_table(anomaly_response: dict) -> pd.DataFrame:
    flags = [f for f in anomaly_response.get("flags", []) if f.get("is_anomaly")]
    if not flags:
        return pd.DataFrame(columns=["date", "merchant", "category", "amount", "score", "reason"])
    df = pd.DataFrame(flags)
    keep = [c for c in ["date", "merchant", "category", "amount", "score", "reason"] if c in df.columns]
    return df[keep].sort_values("score", ascending=False).reset_index(drop=True)


def forecast_table(forecast_response: dict) -> pd.DataFrame:
    fc = forecast_response.get("forecast", [])
    return pd.DataFrame(fc) if fc else pd.DataFrame(columns=["month", "predicted_spend"])


# --------------------------------------------------------------------------- #
# Figures (matplotlib, Agg — testable / embeddable)
# --------------------------------------------------------------------------- #
# Paper palette for the charts: the same values as dashboard/ui_theme.py (Pine
# accent), repeated here so this module stays importable without Streamlit.
_PAPER = "#fefaf5"
_CARD = "#fffdfa"
_INK = "#1c1714"
_INK_2 = "#57504a"
_LINE = "#e9dbcd"
_LINE_STRONG = "#d8c3b2"
_ACCENT = "#1D6B3A"
_ACCENT_STRONG = "#16552E"
_ACCENT_SOFT = "#4C8F63"
_WARM_NEUTRAL = "#c9a27e"
_SPEND_CMAP = LinearSegmentedColormap.from_list(
    "paper_pine", ["#faf2e9", _ACCENT_SOFT, _ACCENT_STRONG])


def _paper_axes(fig, ax, title: str, grid_axis: str | None = "y") -> None:
    """Paper figure, card-coloured plot area, warm hairlines and ink text."""
    fig.patch.set_facecolor(_PAPER)
    ax.set_facecolor(_CARD)
    for side, spine in ax.spines.items():
        spine.set_color(_LINE_STRONG)
        if grid_axis and side in ("top", "right"):
            spine.set_visible(False)
    ax.tick_params(colors=_LINE_STRONG, labelcolor=_INK_2)
    ax.xaxis.label.set_color(_INK_2)
    ax.yaxis.label.set_color(_INK_2)
    ax.set_title(title, fontsize=11, fontweight="semibold", color=_INK, loc="left", pad=10)
    if grid_axis:
        ax.grid(True, axis=grid_axis, color=_LINE, linewidth=0.8)
        ax.set_axisbelow(True)


def fig_category_heatmap(matrix: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 4.5))
    data = matrix.values.astype(float)
    im = ax.imshow(data, aspect="auto", cmap=_SPEND_CMAP)
    ax.set_xticks(range(len(matrix.columns)))
    ax.set_xticklabels(matrix.columns, rotation=40, ha="right", fontsize=8)
    ax.set_yticks(range(len(matrix.index)))
    ax.set_yticklabels(matrix.index, fontsize=8)
    _paper_axes(fig, ax, "Monthly spend by category ($)", grid_axis=None)
    lo = float(data.min()) if data.size else 0.0
    hi = float(data.max()) if data.size else 0.0
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            dark_cell = hi > lo and (data[i, j] - lo) / (hi - lo) > 0.55
            ax.text(j, i, f"{data[i, j]:.0f}", ha="center", va="center",
                    fontsize=6, color=_PAPER if dark_cell else _INK)
    cbar = fig.colorbar(im, ax=ax, shrink=0.8, label="$ spent")
    cbar.outline.set_edgecolor(_LINE)
    cbar.ax.tick_params(colors=_LINE_STRONG, labelcolor=_INK_2)
    cbar.ax.yaxis.label.set_color(_INK_2)
    fig.tight_layout()
    return fig


def fig_balance_trend(trend: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 3.8))
    ax.plot(trend["date"], trend["balance"], color=_ACCENT, lw=1.8)
    ax.fill_between(trend["date"], trend["balance"], alpha=0.10, color=_ACCENT)
    ax.axhline(0, color=_LINE_STRONG, lw=0.8, ls="--")
    ax.set_ylabel("$")
    _paper_axes(fig, ax, "Running balance (per-user)")
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


def fig_cashflow_forecast(trend: pd.DataFrame, forecast_df: pd.DataFrame):
    fig, ax = plt.subplots(figsize=(9, 3.8))
    # historical monthly outflow
    hist = trend.copy()
    hist["month"] = hist["date"].dt.strftime("%Y-%m")
    spend = hist[hist["amount"] < 0].groupby("month")["amount"].sum().abs()
    ax.bar(range(len(spend)), spend.values, color=_WARM_NEUTRAL, label="actual spend")
    if not forecast_df.empty and "predicted_spend" in forecast_df:
        n = len(spend)
        ax.bar(range(n, n + len(forecast_df)), forecast_df["predicted_spend"].values,
               color=_ACCENT, alpha=0.85, label="forecast")
    ax.set_ylabel("$ outflow")
    _paper_axes(fig, ax, "Cash-flow: monthly spend + next-month forecast")
    legend = ax.legend(fontsize=8, frameon=False)
    for text in legend.get_texts():
        text.set_color(_INK_2)
    fig.tight_layout()
    return fig
