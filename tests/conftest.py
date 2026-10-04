"""Shared pytest fixtures for the FinTrack test suite (Day-10 Phase 8).

Puts the repo root on sys.path so `import src...`, `import api`, `extract_bill`,
etc. resolve when pytest is run from anywhere, and exposes the public evaluation
fixtures (real SROIE receipts, synthetic transactions) as fixtures.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(scope="session")
def eval_receipts() -> list[dict]:
    """The first few real SROIE receipts with ground-truth amount/date/merchant."""
    path = os.path.join(ROOT, "tests", "fixtures", "receipts.jsonl")
    if not os.path.exists(path):
        pytest.skip("eval receipts not present")
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()][:10]


@pytest.fixture
def user_a_txns() -> list[dict]:
    """A tidy synthetic month of user A's transactions (signed; negative=spend)."""
    return [
        {"date": "2024-01-05", "merchant": "WHOLE FOODS", "category": "groceries", "amount": -84.20},
        {"date": "2024-01-06", "merchant": "STARBUCKS", "category": "dining", "amount": -6.75},
        {"date": "2024-01-07", "merchant": "UBER", "category": "transport", "amount": -18.30},
        {"date": "2024-01-10", "merchant": "ACME PAYROLL", "category": "income", "amount": 3200.00},
        {"date": "2024-01-15", "merchant": "COMCAST", "category": "utilities", "amount": -79.99},
    ]


@pytest.fixture
def user_b_txns() -> list[dict]:
    """A clearly different set for user B (used in isolation regressions)."""
    return [
        {"date": "2024-01-03", "merchant": "SHELL", "category": "transport", "amount": -52.10},
        {"date": "2024-01-11", "merchant": "NETFLIX", "category": "entertainment", "amount": -15.49},
    ]
