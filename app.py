"""FinTrack web app (Flask): overview, transactions, insights, receipts, investing.

Every page reads only the signed-in user's rows (SQL filtered by session['user_id'])
and derives the rest on read through src.insights. Run locally with:

    flask --app app run        # DB_SERVER / DB_PORT / DB_USER / DB_PASS / DB_NAME, SECRET_KEY
"""
import os
import secrets
from datetime import date, datetime
from functools import wraps

import pymysql
from flask import (Flask, flash, jsonify, redirect, render_template, request,
                   session, url_for)

from database import fetch_user_transactions, get_db_connection
from extract_bill import extract_bill_bp
from invest import invest_bp
from login import login_bp
from signup import signup_bp
from src import insights

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key')

# The app is often embedded in a cross-site iframe (e.g. the Hugging Face Space
# page). A default SameSite=Lax session cookie is dropped in that third-party
# context, so after login the redirect to '/' arrives with no session and bounces
# back to the login page. SameSite=None + Secure lets the cookie ride inside the
# HTTPS iframe. (Harmless when the app is opened directly, first-party.)
app.config.update(
    SESSION_COOKIE_SAMESITE='None',
    SESSION_COOKIE_SECURE=True,
    MAX_CONTENT_LENGTH=10 * 1024 * 1024,  # receipt uploads
)

app.register_blueprint(login_bp)
app.register_blueprint(signup_bp)
app.register_blueprint(invest_bp)
app.register_blueprint(extract_bill_bp)

app.add_template_filter(insights.format_inr, 'inr')
app.add_template_filter(insights.format_inr_compact, 'inr_compact')


# --------------------------------------------------------------------------- #
# session helpers
# --------------------------------------------------------------------------- #
def csrf_token():
    if '_csrf' not in session:
        session['_csrf'] = secrets.token_urlsafe(32)
    return session['_csrf']


app.jinja_env.globals['csrf_token'] = csrf_token


@app.before_request
def check_csrf():
    """Every POST carries the session's token. The session cookie is SameSite=None
    (see above), so without this any site could post into a signed-in session."""
    if request.method != 'POST':
        return None
    sent = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token') or ''
    if session.get('_csrf') and secrets.compare_digest(sent, session['_csrf']):
        return None
    message = 'Your session expired. Refresh the page and try again.'
    if request.headers.get('X-Requested-With') == 'fetch':
        return jsonify(success=False, message=message), 400
    flash(message, 'warning')
    return redirect(url_for('overview'))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login_bp.login'))
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_user():
    email = session.get('email', '')
    name = session.get('name') or email.split('@')[0]
    return {'user_name': name, 'user_email': email,
            'user_initial': (name[:1] or '?').upper(),
            'today_iso': date.today().isoformat(), 'year': date.today().year}


def _safe_next(target, default='transactions'):
    """Only same-site paths are followed after a form post."""
    if target and target.startswith('/') and not target.startswith('//') and '\\' not in target:
        return target
    return url_for(default)


def _load_rows():
    try:
        return insights.prepare(fetch_user_transactions(session['user_id'])), None
    except pymysql.MySQLError:
        app.logger.exception('could not load transactions')
        return [], "We couldn't reach the database just now, so your figures may be missing."


# Signals run IsolationForest and the forecaster (~0.4 s), so they are reused until
# the user's ledger (or the day) changes.
_signals_cache = {}


def _signals(rows):
    key = (session['user_id'], date.today(),
           hash(tuple((r['id'], r['amount'], r['date']) for r in rows)))
    if key not in _signals_cache:
        if len(_signals_cache) > 256:
            _signals_cache.clear()
        _signals_cache[key] = insights.signals(rows)
    return _signals_cache[key]


# --------------------------------------------------------------------------- #
# pages
# --------------------------------------------------------------------------- #
@app.route('/')
@login_required
def overview():
    rows, db_error = _load_rows()
    return render_template(
        'overview.html', active='overview', db_error=db_error, has_rows=bool(rows),
        summary=insights.summarize(rows), flow=insights.cash_flow(rows),
        mix=insights.spend_mix(rows), trend=insights.balance_trend(rows),
        recent=rows[:6], signals=_signals(rows) if rows else None)


@app.route('/transactions')
@login_required
def transactions():
    rows, db_error = _load_rows()
    months = []
    for r in rows:  # newest first, so groups come out in order
        label = r['date'].strftime('%B %Y')
        if not months or months[-1]['label'] != label:
            months.append({'label': label, 'rows': [], 'net': 0.0})
        months[-1]['rows'].append(r)
        months[-1]['net'] += r['amount']
    categories = sorted({(r['category'], r['category_label']) for r in rows},
                        key=lambda c: c[1])
    return render_template('transactions.html', active='transactions', db_error=db_error,
                           months=months, count=len(rows), categories=categories)


@app.route('/insights')
@login_required
def insights_page():
    rows, db_error = _load_rows()
    return render_template(
        'insights.html', active='insights', db_error=db_error, has_rows=bool(rows),
        summary=insights.summarize(rows),
        mix=insights.spend_mix(rows, months=6, top=8),
        signals=_signals(rows) if rows else None)


# --------------------------------------------------------------------------- #
# transactions: add / delete (POST only, always scoped to the signed-in user)
# --------------------------------------------------------------------------- #
@app.route('/transactions', methods=['POST'])
@login_required
def add_transaction():
    next_url = _safe_next(request.form.get('next'))
    description = (request.form.get('description') or '').strip()
    kind = request.form.get('kind', 'expense')
    try:
        amount = round(abs(float((request.form.get('amount') or '').replace(',', ''))), 2)
        day = datetime.strptime(request.form.get('date') or '', '%Y-%m-%d').date()
    except ValueError:
        flash('Enter an amount and a valid date.', 'warning')
        return redirect(next_url)
    if not description or len(description) > 255:
        flash('Add a short description (up to 255 characters).', 'warning')
        return redirect(next_url)
    if not 0 < amount < 1e10:  # DECIMAL(12,2)
        flash('Enter an amount greater than zero.', 'warning')
        return redirect(next_url)

    signed = amount if kind == 'income' else -amount
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO transactions (user_id, description, amount, date) "
                "VALUES (%s, %s, %s, %s)",
                (session['user_id'], description, signed, day))
        conn.commit()
        flash(f'Added {description} · {insights.format_inr(signed, signed=True)}', 'success')
    except pymysql.MySQLError:
        app.logger.exception('insert failed')
        flash("That transaction wasn't saved. Please try again.", 'danger')
    finally:
        if conn is not None:
            conn.close()
    return redirect(next_url)


@app.route('/transactions/<int:txn_id>/delete', methods=['POST'])
@login_required
def delete_transaction(txn_id):
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cursor:
            # scoped to this user, so A can never delete B's rows
            removed = cursor.execute(
                "DELETE FROM transactions WHERE id = %s AND user_id = %s",
                (txn_id, session['user_id']))
        conn.commit()
        if removed:
            flash('Transaction removed.', 'success')
        else:
            flash('That transaction was already gone.', 'warning')
    except pymysql.MySQLError:
        app.logger.exception('delete failed')
        flash("That transaction wasn't removed. Please try again.", 'danger')
    finally:
        if conn is not None:
            conn.close()
    return redirect(_safe_next(request.form.get('next')))


@app.route('/api/category')
@login_required
def category_hint():
    """Live category for the add-transaction form."""
    text = (request.args.get('q') or '').strip()[:255]
    if not text:
        return jsonify(category=None, label=None)
    cat = insights.suggest_category(text, request.args.get('kind') == 'income')
    return jsonify(category=cat, label=insights.CATEGORY_LABELS.get(cat, cat.title()))


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_bp.login'))


@app.errorhandler(413)
def too_large(_):
    flash('That file is over 10 MB. Try a smaller PDF.', 'warning')
    return redirect(url_for('extract_bill_bp.extract_bill'))


@app.errorhandler(404)
def not_found(_):
    return render_template('404.html'), 404


if __name__ == '__main__':
    app.run(debug=True)
