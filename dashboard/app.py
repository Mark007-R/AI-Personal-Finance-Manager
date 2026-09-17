"""FinTrack ML dashboard — Streamlit (Day-9 Phase-7).

A per-user analytics dashboard that is a *client of the FastAPI ML service*, not
the Flask app: it logs in over JWT, seeds the caller's transactions, then renders
the ML-backed views — category heat map, anomaly alerts, cash-flow forecast, and
per-user balance trend. Because every call carries the user's token, the whole
dashboard is tenant-scoped end-to-end.

Run:
    uvicorn api:app --port 8000          # the ML service
    streamlit run dashboard/app.py       # this dashboard

All heavy lifting lives in `dashboard/data.py` (headless-testable); this file is
the thin Streamlit shell.
"""
from __future__ import annotations

import os

import httpx
import streamlit as st

from dashboard import data as D
from dashboard import ui_theme

API_URL = os.getenv("FINTRACK_API_URL", "http://localhost:8000")

st.set_page_config(page_title="FinTrack — ML Dashboard", page_icon="💸", layout="wide")
ui_theme.apply_theme()


def _client() -> httpx.Client:
    return httpx.Client(base_url=API_URL, timeout=30.0)


def _auth_headers() -> dict:
    tok = st.session_state.get("token")
    return {"Authorization": f"Bearer {tok}"} if tok else {}


def login_panel():
    st.sidebar.header("🔐 Sign in")
    username = st.sidebar.text_input("Username", value="demo_user")
    password = st.sidebar.text_input("Password", value="demo_pass_123", type="password")
    col1, col2 = st.sidebar.columns(2)
    with _client() as c:
        if col1.button("Register"):
            r = c.post("/auth/register", json={"username": username, "password": password})
            if r.status_code == 200:
                st.session_state["token"] = r.json()["access_token"]
                st.sidebar.success("Registered + signed in.")
            elif r.status_code == 409:
                st.sidebar.warning("User exists — use Sign in.")
            else:
                st.sidebar.error(r.text)
        if col2.button("Sign in", type="primary"):
            r = c.post("/auth/token", data={"username": username, "password": password})
            if r.status_code == 200:
                st.session_state["token"] = r.json()["access_token"]
                st.sidebar.success("Signed in.")
            else:
                st.sidebar.error("Bad credentials.")


def seed_button():
    if st.sidebar.button("Seed demo transactions"):
        stream = D.synthetic_user_stream()
        with _client() as c:
            c.post("/transactions", json={"transactions": stream}, headers=_auth_headers())
        st.sidebar.success(f"Seeded {len(stream)} transactions.")


def main():
    st.title("💸 FinTrack — ML Analytics Dashboard")
    st.caption("Per-user, JWT-scoped views served by the FinTrack ML API.")
    login_panel()

    if not st.session_state.get("token"):
        st.info("Sign in from the sidebar to load your dashboard.")
        return

    seed_button()
    with _client() as c:
        h = _auth_headers()
        txns = c.get("/transactions", headers=h).json().get("transactions", [])
        if not txns:
            st.warning("No transactions yet — click **Seed demo transactions**.")
            return
        anomaly = c.post("/anomaly", json={"transactions": txns, "top_k": 10}, headers=h).json()
        forecast = c.post("/forecast", json={"transactions": txns, "horizon_months": 1}, headers=h).json()

    trend = D.balance_trend(txns)
    c1, c2, c3 = st.columns(3)
    c1.metric("Transactions", len(txns))
    c2.metric("Balance", f"${trend['balance'].iloc[-1]:,.0f}")
    c3.metric("Anomalies", anomaly.get("n_flagged", 0))

    st.subheader("Category heat map")
    st.pyplot(D.fig_category_heatmap(D.category_month_matrix(txns)))

    left, right = st.columns(2)
    with left:
        st.subheader("Balance trend")
        st.pyplot(D.fig_balance_trend(trend))
    with right:
        st.subheader("Cash-flow forecast")
        st.pyplot(D.fig_cashflow_forecast(trend, D.forecast_table(forecast)))

    st.subheader("🚨 Anomaly alerts")
    alerts = D.anomaly_alert_table(anomaly)
    st.dataframe(alerts if not alerts.empty else "No anomalies flagged.", use_container_width=True)


if __name__ == "__main__":
    main()
