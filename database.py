"""MySQL access for the Flask web app.

Every blueprint used to carry its own copy of ``get_db_connection`` (and
invest.py silently defaulted to a local root/pass123 database). This is the one
copy, reading the same env vars as before: DB_SERVER, DB_PORT, DB_USER, DB_PASS,
DB_NAME. The schema is in db/schema.sql.
"""
from __future__ import annotations

import os

import pymysql


def get_db_connection():
    return pymysql.connect(
        host=os.getenv('DB_SERVER'),
        port=int(os.getenv('DB_PORT', '3306')),  # cloud MySQL (e.g. Railway) uses a non-3306 public port
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASS'),
        database=os.getenv('DB_NAME'),
        cursorclass=pymysql.cursors.DictCursor,
    )


def fetch_user_transactions(user_id: int) -> list[dict]:
    """All of one user's transactions, newest first. Always scoped by user_id."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, description, amount, date FROM transactions "
                "WHERE user_id = %s ORDER BY date DESC, id DESC",
                (user_id,))
            return list(cursor.fetchall())
    finally:
        conn.close()
