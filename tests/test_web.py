"""Flask web app — routes, CSRF, and user scoping, against a fake MySQL connection.

Every SQL statement the app sends is recorded, so the tests can assert that writes
are scoped to the signed-in user and that nothing is written without a CSRF token.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

import app as webapp
import extract_bill
import invest

HTTPS = "https://localhost"   # the session cookie is Secure


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.lastrowid = 7

    def execute(self, sql, params=None):
        self.conn.log.append((" ".join(sql.split()), params))
        return 1

    def fetchall(self):
        return list(self.conn.rows)

    def fetchone(self):
        return self.conn.rows[0] if self.conn.rows else None

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    def __init__(self, log, rows=()):
        self.log, self.rows = log, rows

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.log.append(("COMMIT", None))

    def close(self):
        pass


def _ledger():
    today = date.today()
    rows, i = [], 0
    for back in range(5):
        d = (today.replace(day=1) - timedelta(days=31 * back)).replace(day=2)
        for desc, amt in (("Salary", 50000), ("House rent", -15000), ("Netflix", -649),
                          ("Swiggy order", -420 - back * 10)):
            i += 1
            rows.append({"id": i, "description": desc, "amount": amt, "date": d})
    return rows


@pytest.fixture
def sql_log(monkeypatch):
    log = []
    rows = _ledger()
    monkeypatch.setattr(webapp, "get_db_connection", lambda: FakeConn(log))
    monkeypatch.setattr(webapp, "fetch_user_transactions", lambda uid: rows if uid == 42 else [])
    monkeypatch.setattr(invest, "get_db_connection",
                        lambda: FakeConn(log, [{"amount": r["amount"], "date": r["date"]} for r in rows]))
    webapp._signals_cache.clear()
    return log


@pytest.fixture
def client(sql_log):
    webapp.app.config["TESTING"] = True
    c = webapp.app.test_client()
    with c.session_transaction(base_url=HTTPS) as s:
        s["user_id"], s["email"], s["name"] = 42, "riya@example.test", "Riya"
    return c


def _csrf(client, path="/transactions"):
    html = client.get(path, base_url=HTTPS).get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


# ---------------------------------------------------------------- access
def test_pages_require_sign_in(sql_log):
    c = webapp.app.test_client()
    for path in ("/", "/transactions", "/insights", "/invest", "/extract_bill"):
        r = c.get(path, base_url=HTTPS)
        assert r.status_code == 302 and r.headers["Location"].endswith("/login")


@pytest.mark.parametrize("path,needle", [
    ("/", "Cash flow"), ("/transactions", "Every rupee"), ("/insights", "Recurring charges"),
    ("/invest", "Ranked for you"), ("/extract_bill", "Scan a receipt"),
])
def test_signed_in_pages_render(client, path, needle):
    r = client.get(path, base_url=HTTPS)
    assert r.status_code == 200
    assert needle in r.get_data(as_text=True)


def test_overview_shows_the_users_numbers(client):
    html = client.get("/", base_url=HTTPS).get_data(as_text=True)
    assert "Riya" in html
    assert "₹" in html and "Netflix" in html


def test_empty_ledger_shows_the_empty_state(client):
    with client.session_transaction(base_url=HTTPS) as s:
        s["user_id"] = 99                                  # no rows for this user
    html = client.get("/", base_url=HTTPS).get_data(as_text=True)
    assert "Your ledger is empty" in html


def test_logout_clears_the_session(client):
    r = client.get("/logout", base_url=HTTPS)
    assert r.headers["Location"].endswith("/login")
    assert client.get("/", base_url=HTTPS).status_code == 302


def test_unknown_page_is_404(client):
    assert client.get("/nope", base_url=HTTPS).status_code == 404


# ---------------------------------------------------------------- writes
def test_post_without_csrf_writes_nothing(client, sql_log):
    r = client.post("/transactions", base_url=HTTPS,
                    data={"description": "x", "amount": "10", "date": "2026-10-01"})
    assert r.status_code == 302
    assert not any("INSERT" in sql for sql, _ in sql_log)


def test_add_expense_is_negative_and_owned_by_the_user(client, sql_log):
    token = _csrf(client)
    r = client.post("/transactions", base_url=HTTPS, data={
        "csrf_token": token, "kind": "expense", "description": "Swiggy order",
        "amount": "1,250.5", "date": "2026-10-01", "next": "/"})
    assert r.headers["Location"].endswith("/")
    sql, params = next((s, p) for s, p in sql_log if s.startswith("INSERT INTO transactions"))
    assert params[0] == 42 and params[1] == "Swiggy order"
    assert params[2] == -1250.5 and str(params[3]) == "2026-10-01"


def test_add_income_is_positive(client, sql_log):
    client.post("/transactions", base_url=HTTPS, data={
        "csrf_token": _csrf(client), "kind": "income", "description": "Salary",
        "amount": "50000", "date": "2026-10-01"})
    params = next(p for s, p in sql_log if s.startswith("INSERT"))
    assert params[2] == 50000


@pytest.mark.parametrize("form", [
    {"description": "", "amount": "10", "date": "2026-10-01"},
    {"description": "x", "amount": "0", "date": "2026-10-01"},
    {"description": "x", "amount": "abc", "date": "2026-10-01"},
    {"description": "x", "amount": "10", "date": "Not found"},
])
def test_invalid_add_is_rejected(client, sql_log, form):
    client.post("/transactions", base_url=HTTPS, data={"csrf_token": _csrf(client), **form})
    assert not any(s.startswith("INSERT") for s, _ in sql_log)


def test_next_never_leaves_the_site(client):
    r = client.post("/transactions", base_url=HTTPS, data={
        "csrf_token": _csrf(client), "description": "x", "amount": "1",
        "date": "2026-10-01", "next": "//evil.example/steal"})
    assert r.headers["Location"].endswith("/transactions")


def test_delete_is_post_only_and_scoped(client, sql_log):
    assert client.get("/transactions/5/delete", base_url=HTTPS).status_code == 405
    client.post("/transactions/5/delete", base_url=HTTPS, data={"csrf_token": _csrf(client)})
    sql, params = next((s, p) for s, p in sql_log if s.startswith("DELETE"))
    assert "user_id = %s" in sql and params == (5, 42)


def test_category_hint(client):
    assert client.get("/api/category?q=Swiggy+order", base_url=HTTPS).json["category"] == "dining"
    assert client.get("/api/category?q=Bonus&kind=income", base_url=HTTPS).json["category"] == "income"


# ---------------------------------------------------------------- receipts
def test_receipt_scan_shows_a_review_and_saves_nothing(client, sql_log, monkeypatch):
    monkeypatch.setattr(extract_bill, "extract_text_from_pdf",
                        lambda path: "OJC MARKETING SDN BHD\nDate 05/06/2024\nTOTAL 30.00\n")
    import io
    r = client.post("/extract_bill", base_url=HTTPS, content_type="multipart/form-data", data={
        "csrf_token": _csrf(client, "/extract_bill"),
        "pdf": (io.BytesIO(b"%PDF-1.4"), "../../receipt.pdf")})
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "Check what we found" in html
    assert 'value="30.00"' in html and 'value="2024-06-05"' in html
    assert "receipt.pdf" in html and "../" not in html.split("Check what we found")[1][:400]
    assert not any(s.startswith("INSERT") for s, _ in sql_log)


def test_receipt_scan_rejects_non_pdf(client):
    import io
    r = client.post("/extract_bill", base_url=HTTPS, content_type="multipart/form-data", data={
        "csrf_token": _csrf(client, "/extract_bill"), "pdf": (io.BytesIO(b"hi"), "notes.txt")})
    assert "Only PDF receipts" in r.get_data(as_text=True)
