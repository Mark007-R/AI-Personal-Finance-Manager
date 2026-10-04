"""Seed a demo account with a synthetic rupee ledger (local development and demos).

    python db/seed_demo.py                      # reads DB_SERVER/DB_PORT/DB_USER/DB_PASS/DB_NAME
    python db/seed_demo.py --email you@example.test --password something-long --months 10

Creates the tables from db/schema.sql if they are missing, then (re)creates the demo
user and fills it with ~8 months of transactions ending today: salary, rent,
subscriptions, groceries, dining, transport, one unusually large purchase and one
double charge, so every chart and insight in the web app has something to show.
Re-running replaces the demo user's rows. All data is synthetic.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from werkzeug.security import generate_password_hash  # noqa: E402

from database import get_db_connection  # noqa: E402

DEMO_EMAIL = "demo@fintrack.test"
DEMO_PASSWORD = "fintrack-demo"
DEMO_NAME = "Riya"


def _months(today: date, n: int):
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append((y, m))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out[::-1]


def build_ledger(today: date, months: int = 8, seed: int = 7) -> list[tuple[str, float, date]]:
    rng = random.Random(seed)
    rows: list[tuple[str, float, date]] = []

    def add(desc, amount, d):
        if d <= today:
            rows.append((desc, round(amount, 2), d))

    keys = _months(today, months)
    for i, (y, m) in enumerate(keys):
        def day(n):
            return date(y, m, min(n, 28))

        salary = 85000 if i < months // 2 else 92000
        add("Salary - Northwind Analytics", salary, day(1))
        add("House rent", -22000, day(2))
        add("Electricity bill BESCOM", -rng.uniform(1450, 2600), day(7))
        add("Airtel broadband", -1178, day(9))
        add("Netflix subscription", -649, day(12))
        add("Spotify Premium", -119, day(15))
        add("BookMyShow tickets", -rng.uniform(450, 1300), day(rng.randint(16, 27)))
        if i % 3 == 1:
            add("Freelance payout - Upwork", rng.uniform(12000, 24000), day(rng.randint(18, 26)))

        for week in range(4):
            add(rng.choice(["BigBasket order", "DMart groceries", "Blinkit order"]),
                -rng.uniform(900, 3200), day(3 + week * 7 + rng.randint(0, 2)))
        for _ in range(rng.randint(6, 9)):
            add(rng.choice(["Swiggy order", "Zomato order", "Chaayos", "Starbucks"]),
                -rng.uniform(180, 950), day(rng.randint(1, 28)))
        for _ in range(rng.randint(5, 8)):
            add(rng.choice(["Uber ride", "Ola ride", "Rapido bike taxi"]),
                -rng.uniform(90, 620), day(rng.randint(1, 28)))
        for d in (6, 21):
            add("Petrol - HP fuel station", -rng.uniform(1500, 2400), day(d))
        for _ in range(rng.randint(1, 3)):
            add(rng.choice(["Amazon order", "Myntra", "Flipkart order", "Decathlon"]),
                -rng.uniform(499, 4999), day(rng.randint(1, 28)))
        if rng.random() < 0.6:
            add("Apollo Pharmacy", -rng.uniform(240, 1800), day(rng.randint(1, 28)))

    # Jio prepaid renews every 28 days, not on a calendar date.
    d = date(keys[0][0], keys[0][1], 4)
    while d <= today:
        add("Jio recharge", -666, d)
        d += timedelta(days=28)

    # One purchase far above this user's usual shopping, and one double charge.
    y, m = keys[-3]
    add("Croma - laptop", -74990, date(y, m, 18))
    y, m = keys[-2]
    add("Zomato order", -486, date(y, m, 23))
    add("Zomato order", -486, date(y, m, 23))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--email", default=DEMO_EMAIL)
    ap.add_argument("--password", default=DEMO_PASSWORD)
    ap.add_argument("--name", default=DEMO_NAME)
    ap.add_argument("--months", type=int, default=8)
    args = ap.parse_args()

    with open(os.path.join(ROOT, "db", "schema.sql"), encoding="utf-8") as f:
        statements = [s.strip() for s in "\n".join(
            line for line in f if not line.lstrip().startswith("--")).split(";") if s.strip()]

    rows = build_ledger(date.today(), args.months)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
            cur.execute("SELECT id FROM users1 WHERE email = %s", (args.email,))
            user = cur.fetchone()
            pw = generate_password_hash(args.password)
            if user:
                user_id = user["id"]
                cur.execute("UPDATE users1 SET firstname = %s, password = %s WHERE id = %s",
                            (args.name, pw, user_id))
                cur.execute("DELETE FROM transactions WHERE user_id = %s", (user_id,))
            else:
                cur.execute("INSERT INTO users1 (firstname, lastname, email, password) "
                            "VALUES (%s, '', %s, %s)", (args.name, args.email, pw))
                user_id = cur.lastrowid
            cur.executemany("INSERT INTO transactions (user_id, description, amount, date) "
                            "VALUES (%s, %s, %s, %s)",
                            [(user_id, desc, amt, d) for desc, amt, d in rows])
        conn.commit()
    finally:
        conn.close()
    print(f"seeded {len(rows)} transactions for {args.email} (user id {user_id})")


if __name__ == "__main__":
    main()
