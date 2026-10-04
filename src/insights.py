"""Ledger insights for the Flask web app.

The web app stores only (description, amount, date) per transaction, so everything
the UI shows beyond the raw ledger is derived on read here, by composing the src
champions over ONE user's rows (already scoped by user_id in SQL):

    category per row        src.categorization (TF-IDF + LinearSVC, keyword fallback)
    unusual spending        src.anomaly.detect_anomalies (IsolationForest)
    recurring / duplicates  src.anomaly.find_recurring_groups / find_duplicate_charges
    next-month spend        src.forecast.forecast_cashflow

plus the plain arithmetic the overview needs (balance, month totals, spend mix).
Everything here is pure, so it is tested without a database (tests/test_insights.py).
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date, datetime, timedelta

CATEGORY_LABELS = {
    "groceries": "Groceries", "dining": "Dining", "transport": "Transport",
    "utilities": "Utilities", "rent": "Rent", "entertainment": "Entertainment",
    "health": "Health", "shopping": "Shopping", "income": "Income", "other": "Other",
}

# The shipped categorizer was trained on US merchant strings (data/), while the
# web app keeps its books in rupees, so everyday Indian merchants would otherwise
# land in "other". These whole-word hints run before the model; the model and its
# measured results are untouched.
_INR_HINTS = {
    "dining": ["swiggy", "zomato", "dominos", "domino's", "haldiram", "haldirams",
               "chaayos", "cafe coffee day", "barbeque nation"],
    "groceries": ["bigbasket", "big basket", "blinkit", "zepto", "dmart", "d-mart",
                  "jiomart", "reliance fresh", "more supermarket", "nature's basket",
                  "kirana", "vegetables"],
    "transport": ["ola", "rapido", "irctc", "indigo", "vistara", "akasa", "redbus",
                  "fastag", "petrol", "diesel", "auto rickshaw"],
    "utilities": ["jio", "airtel", "vodafone", "bsnl", "bescom", "tata power",
                  "adani electricity", "mahanagar gas", "indane", "electricity",
                  "broadband", "recharge", "tata play"],
    "rent": ["rent", "society maintenance", "maintenance charges"],
    "entertainment": ["hotstar", "jiocinema", "bookmyshow", "pvr", "inox", "sonyliv",
                      "zee5", "prime video"],
    "health": ["apollo", "pharmeasy", "1mg", "netmeds", "medplus", "practo", "cult.fit",
               "cultfit"],
    "shopping": ["flipkart", "myntra", "ajio", "nykaa", "meesho", "croma",
                 "reliance digital", "decathlon", "tanishq"],
}
_HINT_RES = [(re.compile(r"\b(?:%s)\b" % "|".join(re.escape(w) for w in words), re.I), cat)
             for cat, words in _INR_HINTS.items()]


# --------------------------------------------------------------------------- #
# formatting (registered as Jinja filters in app.py)
# --------------------------------------------------------------------------- #
def format_inr(value, decimals: int = 2, signed: bool = False) -> str:
    """₹ with Indian digit grouping: 1234567.5 -> '₹12,34,567.50', negatives '−₹…'."""
    v = float(value or 0)
    sign = "−" if v < 0 else ("+" if signed and v > 0 else "")
    whole, _, frac = f"{abs(v):.{decimals}f}".partition(".")
    if len(whole) > 3:
        head, groups = whole[:-3], [whole[-3:]]
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups)
    return f"{sign}₹{whole}" + (f".{frac}" if frac else "")


def format_inr_compact(value) -> str:
    """Short form for axes and tiles: ₹950, ₹12.5K, ₹1.2L, ₹3.4Cr."""
    v = float(value or 0)
    sign, a = ("−" if v < 0 else ""), abs(v)
    for unit, size in (("Cr", 1e7), ("L", 1e5), ("K", 1e3)):
        if a >= size:
            n = a / size
            txt = f"{n:.1f}".rstrip("0").rstrip(".") if n < 100 else f"{n:.0f}"
            return f"{sign}₹{txt}{unit}"
    return f"{sign}₹{a:.0f}"


# --------------------------------------------------------------------------- #
# rows -> normalised, categorised rows
# --------------------------------------------------------------------------- #
def _to_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _hint(description: str):
    for rx, cat in _HINT_RES:
        if rx.search(description):
            return cat
    return None


def suggest_category(description: str, is_income: bool = False) -> str:
    """Category for one description (the add-transaction form's live hint)."""
    if is_income:
        return "income"
    hint = _hint(description)
    if hint:
        return hint
    from src.categorization import get_classifier
    cat = get_classifier().predict(description)["category"]
    return "other" if cat == "income" else cat  # an outflow is never income


def categorizer_name() -> str:
    from src.categorization import get_classifier
    model = get_classifier().model_id
    return {"tfidf_linsvc": "TF-IDF + LinearSVC",
            "keyword_fallback": "keyword rules"}.get(model, model)


def prepare(rows) -> list[dict]:
    """Normalise DB rows (Decimal amounts, date objects) and attach a category.

    Returns {id, description, amount: float, date: date, category, category_label},
    newest first. Rows whose date cannot be read are dropped.
    """
    out = []
    for r in rows:
        d = _to_date(r.get("date"))
        if d is None:
            continue
        out.append({"id": r.get("id"),
                    "description": str(r.get("description") or "").strip(),
                    "amount": round(float(r.get("amount") or 0), 2),
                    "date": d})

    pending = []
    for r in out:
        if r["amount"] > 0:
            r["category"] = "income"
        else:
            r["category"] = _hint(r["description"])
            if r["category"] is None:
                pending.append(r)
    if pending:
        from src.categorization import get_classifier
        preds = get_classifier().predict_batch([r["description"] for r in pending])
        for r, p in zip(pending, preds):
            r["category"] = "other" if p["category"] == "income" else p["category"]
    for r in out:
        r["category_label"] = CATEGORY_LABELS.get(r["category"], r["category"].title())

    out.sort(key=lambda r: (r["date"], r["id"] or 0), reverse=True)
    return out


# --------------------------------------------------------------------------- #
# months
# --------------------------------------------------------------------------- #
def _ym(d: date) -> tuple[int, int]:
    return d.year, d.month


def _shift(ym: tuple[int, int], k: int) -> tuple[int, int]:
    i = ym[0] * 12 + (ym[1] - 1) + k
    return i // 12, i % 12 + 1


def month_name(ym: tuple[int, int], long: bool = True) -> str:
    return date(ym[0], ym[1], 1).strftime("%B %Y" if long else "%b")


def anchor_month(rows: list[dict], today: date) -> tuple[int, int]:
    """The month the overview reports on: the current month when it has activity,
    otherwise the latest month with any (so an old ledger still shows something)."""
    if not rows:
        return _ym(today)
    current = _ym(today)
    months = {_ym(r["date"]) for r in rows}
    if current in months:
        return current
    past = [m for m in months if m < current]
    return max(past) if past else max(months)


def _totals(rows):
    income = sum(r["amount"] for r in rows if r["amount"] > 0)
    spend = sum(-r["amount"] for r in rows if r["amount"] < 0)
    return round(income, 2), round(spend, 2)


def _change(now: float, before: float):
    if not before:
        return None
    return round((now - before) / before * 100, 1)


# --------------------------------------------------------------------------- #
# overview numbers
# --------------------------------------------------------------------------- #
def summarize(rows: list[dict], today: date | None = None) -> dict:
    """Balance plus the anchor month's income, spend and savings rate.

    When the anchor is the current (partial) month, the comparison is against the
    same point of the previous month, so day 4 is never compared with a whole month.
    """
    today = today or date.today()
    anchor = anchor_month(rows, today)
    prev = _shift(anchor, -1)
    partial = anchor == _ym(today)

    this_rows = [r for r in rows if _ym(r["date"]) == anchor]
    prev_rows = [r for r in rows if _ym(r["date"]) == prev
                 and (not partial or r["date"].day <= today.day)]
    income, spend = _totals(this_rows)
    p_income, p_spend = _totals(prev_rows)
    all_income, all_spend = _totals(rows)

    return {
        "count": len(rows),
        "balance": round(all_income - all_spend, 2),
        "income_total": all_income,
        "spend_total": all_spend,
        "month": month_name(anchor),
        "month_short": month_name(anchor, long=False),
        "prev_month_short": month_name(prev, long=False),
        "partial": partial,
        "income": income,
        "spend": spend,
        "income_change": _change(income, p_income),
        "spend_change": _change(spend, p_spend),
        "savings_rate": round((income - spend) / income * 100, 1) if income else None,
    }


def _nice_ticks(top_value: float, count: int = 4) -> list[float]:
    if top_value <= 0:
        return [0.0, 1.0]
    raw = top_value / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    n = math.ceil(top_value / step - 1e-9)
    return [round(i * step, 2) for i in range(n + 1)]


def cash_flow(rows: list[dict], today: date | None = None, months: int = 6) -> dict:
    """Income vs spend per month for the last `months` months up to the anchor month,
    with clean y-axis ticks and each bar's height as a % of the top tick."""
    today = today or date.today()
    anchor = anchor_month(rows, today)
    keys = [_shift(anchor, -k) for k in range(months - 1, -1, -1)]
    bucket = {k: [0.0, 0.0] for k in keys}
    for r in rows:
        k = _ym(r["date"])
        if k in bucket:
            bucket[k][0 if r["amount"] > 0 else 1] += abs(r["amount"])
    ticks = _nice_ticks(max([v for pair in bucket.values() for v in pair] + [0]))
    top = ticks[-1]
    out = []
    for k in keys:
        inc, sp = (round(v, 2) for v in bucket[k])
        out.append({"label": month_name(k, long=False), "long_label": month_name(k),
                    "income": inc, "spend": sp, "net": round(inc - sp, 2),
                    "income_pct": round(inc / top * 100, 2), "spend_pct": round(sp / top * 100, 2),
                    "partial": k == _ym(today)})
    return {"months": out, "ticks": [{"value": t, "pct": round(t / top * 100, 2)} for t in ticks]}


def spend_mix(rows: list[dict], today: date | None = None, months: int = 3,
              top: int = 5) -> dict:
    """Spend by category over the last `months` months, largest first; the tail
    past `top` folds into one 'The rest' row."""
    today = today or date.today()
    anchor = anchor_month(rows, today)
    start = _shift(anchor, -(months - 1))
    totals = defaultdict(float)
    for r in rows:
        if r["amount"] < 0 and start <= _ym(r["date"]) <= anchor:
            totals[r["category"]] += -r["amount"]
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    if len(ranked) > top + 1:
        ranked = ranked[:top] + [("__rest", sum(v for _, v in ranked[top:]))]
    whole = sum(v for _, v in ranked) or 1.0
    biggest = max((v for _, v in ranked), default=1.0) or 1.0
    items = [{"category": c,
              "label": "The rest" if c == "__rest" else CATEGORY_LABELS.get(c, c.title()),
              "amount": round(v, 2), "share": round(v / whole * 100, 1),
              "width": round(v / biggest * 100, 2)} for c, v in ranked]
    return {"items": items, "total": round(sum(totals.values()), 2),
            "window": f"{month_name(start, long=False)} – {month_name(anchor, long=False)}"
            if months > 1 else month_name(anchor)}


def balance_trend(rows: list[dict], today: date | None = None, days: int = 182,
                  step: int = 7, width: float = 120.0, height: float = 36.0) -> dict | None:
    """Running balance over the last `days` days, sampled every `step` days, as SVG
    path data (line + area) for the balance tile's sparkline. None when there is too
    little history."""
    if len(rows) < 2:
        return None
    today = today or date.today()
    ordered = sorted(rows, key=lambda r: r["date"])
    end = max(today, ordered[-1]["date"])
    start = end - timedelta(days=days)
    running, daily = 0.0, {}
    for r in ordered:
        running += r["amount"]
        daily[r["date"]] = running
    before = [v for d, v in daily.items() if d < start]
    level = before[-1] if before else 0.0
    points = []
    for i in range(days + 1):
        level = daily.get(start + timedelta(days=i), level)
        if i % step == 0 or i == days:
            points.append(level)
    lo, hi = min(points), max(points)
    span = (hi - lo) or 1.0
    last = len(points) - 1
    xy = [(i / last * width, height - 2 - (v - lo) / span * (height - 4))
          for i, v in enumerate(points)]
    line = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in xy)
    return {"line": line, "area": f"{line} L{width:.2f},{height:.2f} L0,{height:.2f} Z",
            "end_x": round(xy[-1][0], 2), "end_y": round(xy[-1][1], 2),
            "width": width, "height": height}


# --------------------------------------------------------------------------- #
# ML signals
# --------------------------------------------------------------------------- #
_CADENCE = ((5, 9, "Weekly", 7), (12, 16, "Fortnightly", 14), (26, 35, "Monthly", 30.4))


def _cadence(gap_days: float):
    for lo, hi, label, period in _CADENCE:
        if lo <= gap_days <= hi:
            return label, period
    return "Regular", gap_days


def _median(values):
    s = sorted(values)
    n = len(s)
    return (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2) if n else 0.0


def _anomalies(outflows: list[dict], txns: list[dict], recurring_keys: set, limit: int,
               min_ratio: float = 2.0):
    """IsolationForest flags, best first, kept only when they can be explained to the
    user: at least `min_ratio`× their median spend in that category, and not part of
    a recurring pattern (rent and fortnightly fuel are fixed costs, not surprises).
    Without scikit-learn the median-ratio test alone decides."""
    from src.anomaly.detector import _norm_merchant

    by_cat = defaultdict(list)
    for r in outflows:
        by_cat[r["category"]].append(-r["amount"])
    medians = {c: _median(v) for c, v in by_cat.items()}

    try:
        from src.anomaly import detect_anomalies
        flagged = [f for f in detect_anomalies(txns)["flags"] if f["is_anomaly"]]
        engine = "IsolationForest"
    except ImportError:
        flagged = sorted(txns, key=lambda t: t["amount"] / max(medians.get(t["category"], 1), 1))
        engine = "median-ratio rule"

    items = []
    for f in flagged:
        med = medians.get(f["category"], 0.0)
        ratio = -f["amount"] / med if med else 0.0
        if ratio < min_ratio or _norm_merchant(f["merchant"]) in recurring_keys:
            continue
        label = CATEGORY_LABELS.get(f["category"], f["category"])
        items.append({"date": _to_date(f["date"]), "description": f["merchant"],
                      "category": f["category"], "category_label": label,
                      "amount": f["amount"], "ratio": round(ratio, 1),
                      "reason": f"{ratio:.1f}× your usual {label.lower()} spend "
                                f"of {format_inr(med, 0)}"})
        if len(items) == limit:
            break
    return items, engine


def _latest_by_merchant(outflows: list[dict]) -> dict:
    """The detectors group rows by a normalised merchant key (upper-case, first three
    words); map each key back to the user's own wording and latest date."""
    from src.anomaly.detector import _norm_merchant

    latest = {}
    for r in outflows:  # newest first, so the first hit is the latest charge
        latest.setdefault(_norm_merchant(r["description"]), r)
    return latest


def _recurring(outflows: list[dict], txns: list[dict]):
    from src.anomaly import find_recurring_groups

    latest = _latest_by_merchant(outflows)
    last_seen = {k: r["date"] for k, r in latest.items()}
    names = {k: r["description"] for k, r in latest.items()}
    items = []
    for g in find_recurring_groups(txns):
        label, period = _cadence(g["median_gap_days"])
        last = last_seen.get(g["merchant"])
        items.append({"key": g["merchant"],
                      "description": names.get(g["merchant"], g["merchant"].title()),
                      "cadence": label, "times": g["n"], "amount": g["mean_amount"],
                      "monthly": round(g["mean_amount"] * 30.4 / period, 2),
                      "last": last,
                      "next": last + timedelta(days=round(g["median_gap_days"])) if last else None})
    items.sort(key=lambda i: i["monthly"], reverse=True)
    return items


def signals(rows: list[dict], today: date | None = None, anomaly_limit: int = 6) -> dict:
    """Anomalies, recurring charges, duplicate charges and the spend forecast."""
    today = today or date.today()
    outflows = [r for r in rows if r["amount"] < 0]
    txns = [{"date": r["date"].isoformat(), "merchant": r["description"],
             "category": r["category"], "amount": r["amount"]} for r in outflows]

    recurring = _recurring(outflows, txns)
    anomalies, anomaly_engine = _anomalies(outflows, txns, {i["key"] for i in recurring},
                                           anomaly_limit)

    from src.anomaly import find_duplicate_charges
    latest = _latest_by_merchant(outflows)
    duplicates = [{"description": latest[d["merchant"]]["description"] if d["merchant"] in latest
                   else d["merchant"].title(), "amount": d["amount"],
                   "date": _to_date(d["date"]), "prior_date": _to_date(d["prior_date"])}
                  for d in find_duplicate_charges(txns)]
    duplicates.sort(key=lambda d: d["date"], reverse=True)

    # Forecast from complete months only: a half-finished current month would be
    # read as a low-spend month. The forecast then lands on the current month, so
    # it can be shown against what has been spent so far.
    anchor = anchor_month(rows, today)
    cutoff = date(anchor[0], anchor[1], 1) if anchor == _ym(today) else date.max
    from src.forecast import forecast_cashflow
    fc = forecast_cashflow([{"date": r["date"].isoformat(), "amount": r["amount"]}
                            for r in rows if r["date"] < cutoff])
    forecast = None
    if fc["forecast"]:
        y, m = (int(p) for p in fc["forecast"][0]["month"].split("-"))
        predicted = fc["forecast"][0]["predicted_spend"]
        so_far = sum(-r["amount"] for r in outflows if _ym(r["date"]) == (y, m))
        forecast = {"month": month_name((y, m)), "predicted": predicted,
                    "last_actual": fc["last_actual"], "so_far": round(so_far, 2),
                    "progress": round(min(so_far / predicted * 100, 100), 1) if predicted else None,
                    "method": {"prophet": "Prophet",
                               "seasonal_naive_lag12": "seasonal-naive"}.get(fc["method"], fc["method"]),
                    "history_months": fc["history_months"]}

    return {"anomalies": anomalies, "recurring": recurring, "duplicates": duplicates,
            "forecast": forecast,
            "recurring_monthly": round(sum(i["monthly"] for i in recurring), 2),
            "engines": {"categorizer": categorizer_name(), "anomaly": anomaly_engine,
                        "forecast": forecast["method"] if forecast else None}}
