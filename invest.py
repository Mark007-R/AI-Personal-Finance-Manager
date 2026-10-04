"""Investment recommendation route.

Day-5 fixes (audit findings):
  * Multi-tenancy: the old query ``SELECT amount FROM transactions`` summed EVERY
    user's money into one balance. It now filters by ``session['user_id']`` so each
    user sees only their own balance.
  * Personalization: the old logic just matched ``min_investment <= total_balance``.
    It now derives a per-user risk profile from that user's own cash-flow signals
    and ranks options by affordability x risk-fit (``src.reco.investments``).
  * Connector consistency: switched from ``mysql.connector`` to ``pymysql`` (used
    everywhere else in the app).

The template receives the ranked options in the recommender's normalised shape
(name / type / min_investment / expected_return_pct / risk / suitability) plus the
risk profile, its score and the three drivers behind it.
"""
from flask import Blueprint, render_template, session, redirect, url_for
import pymysql

from database import get_db_connection
from src.reco.investments import recommend_for_user, DEFAULT_CATALOG

# Define Blueprint
invest_bp = Blueprint('invest_bp', __name__)


def _load_catalog(cursor):
    """Assemble the investment catalog from the DB tables, normalised to the
    recommender's option shape. Falls back to a synthetic catalog if a table is
    missing so the route never hard-fails in a demo environment."""
    catalog = []
    try:
        cursor.execute("SELECT * FROM RecurringDeposits")
        for r in cursor.fetchall():
            catalog.append({"name": r.get("name") or "Recurring Deposit",
                            "type": "Recurring Deposit",
                            "min_investment": float(r.get("min_investment", 0) or 0),
                            "expected_return_pct": float(r.get("interest_rate", 6.5) or 6.5),
                            "risk": "low"})
        cursor.execute("""SELECT b.*, bank.bank_name FROM Bonds b
                          JOIN Banks bank ON b.bank_id = bank.bank_id""")
        for r in cursor.fetchall():
            catalog.append({"name": r.get("bank_name") or "Bond", "type": "Bond",
                            "min_investment": float(r.get("minimum_investment", 0) or 0),
                            "expected_return_pct": float(r.get("coupon_rate", 7.5) or 7.5),
                            "risk": "low"})
        cursor.execute("SELECT * FROM BankStockData")
        for r in cursor.fetchall():
            catalog.append({"name": r.get("BankName") or "Bank Stock", "type": "Bank Stock",
                            "min_investment": float(r.get("CurrentPrice", 0) or 0),
                            "expected_return_pct": 12.0, "risk": "high"})
        cursor.execute("SELECT * FROM BankLifeInsurance")
        for r in cursor.fetchall():
            catalog.append({"name": r.get("plan_name") or "Life Insurance",
                            "type": "Life Insurance",
                            "min_investment": float(r.get("premium_range", 0) or 0),
                            "expected_return_pct": 5.0, "risk": "low"})
    except Exception:
        pass
    return catalog or DEFAULT_CATALOG


@invest_bp.route('/invest', methods=['GET'], endpoint='invest')
def invest():
    if 'user_id' not in session:
        return redirect(url_for('login_bp.login'))
    user_id = session['user_id']

    db_error = None
    txns, catalog = [], DEFAULT_CATALOG
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # Per-user transactions only (multi-tenancy fix) — date is needed for the
            # risk profile, so we pull the rows rather than just SUM(amount).
            cursor.execute(
                "SELECT amount, date FROM transactions WHERE user_id = %s", (user_id,))
            txns = [{"amount": float(r["amount"]), "date": str(r.get("date"))}
                    for r in cursor.fetchall()]
            catalog = _load_catalog(cursor)
    except pymysql.MySQLError:
        db_error = "We couldn't reach the database just now, so your balance may be missing."
    finally:
        if conn is not None:
            conn.close()

    rec = recommend_for_user(txns, catalog=catalog)
    return render_template('invest.html', active='invest', db_error=db_error,
                           has_rows=bool(txns),
                           options=rec["options"],
                           total_balance=rec["total_balance"],
                           risk_profile=rec["risk_profile"],
                           risk_score=rec["risk_score"],
                           drivers=rec["drivers"],
                           catalog_size=len(catalog))
