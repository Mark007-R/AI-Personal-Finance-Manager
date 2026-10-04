"""Train + persist the champion expense categorizer.

Reproduces the Day-3 champion (TF-IDF word+char + LinearSVC) on the SAME
stratified 70/30 split + seed, reports held-out macro-F1 (must match the Day-3
number, ~0.975), then retrains on ALL rows for the shipped artifact and writes
it to models/expense_classifier.joblib together with a small metrics sidecar.

    python -m src.categorization.train
"""
from __future__ import annotations

import csv
import json
import os
import time
from collections import defaultdict

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data", "transactions.csv")
MODEL_DIR = os.path.join(ROOT, "models")
MODEL_PATH = os.path.join(MODEL_DIR, "expense_classifier.joblib")
SEED = 20260627  # identical to run_day3_categorize.py for parity


def _tfidf_union():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.pipeline import FeatureUnion
    return FeatureUnion([
        ("w", TfidfVectorizer(analyzer="word", ngram_range=(1, 2), sublinear_tf=True)),
        ("c", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True)),
    ])


def _build_pipeline():
    from sklearn.pipeline import Pipeline
    from sklearn.svm import LinearSVC
    return Pipeline([("feats", _tfidf_union()),
                     ("clf", LinearSVC(class_weight="balanced", C=1.0))])


def _stratified_split(rows, test_frac=0.30, seed=SEED):
    rng = np.random.RandomState(seed)
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    train, test = [], []
    for _, items in by_cat.items():
        idx = rng.permutation(len(items))
        k = max(1, int(round(len(items) * test_frac)))
        test += [items[i] for i in idx[:k]]
        train += [items[i] for i in idx[k:]]
    return train, test


def main():
    from sklearn.metrics import f1_score, accuracy_score

    rows = list(csv.DictReader(open(DATA, encoding="utf-8")))
    labels = sorted(set(r["category"] for r in rows))
    train, test = _stratified_split(rows)
    Xtr = [r["description"] for r in train]; ytr = [r["category"] for r in train]
    Xte = [r["description"] for r in test]; yte = [r["category"] for r in test]

    # 1) held-out evaluation (parity with Day 3)
    t0 = time.time()
    pipe = _build_pipeline()
    pipe.fit(Xtr, ytr)
    fit_sec = time.time() - t0
    yp = pipe.predict(Xte)
    macro = round(float(f1_score(yte, yp, labels=labels, average="macro", zero_division=0)), 4)
    acc = round(float(accuracy_score(yte, yp)), 4)
    print(f"[held-out] macro-F1={macro}  acc={acc}  fit={fit_sec:.2f}s  "
          f"(Day-3 champion was 0.975)")

    # 2) ship: retrain on ALL rows for the production artifact
    final = _build_pipeline()
    final.fit([r["description"] for r in rows], [r["category"] for r in rows])

    os.makedirs(MODEL_DIR, exist_ok=True)
    import joblib
    joblib.dump({"pipeline": final, "classes": labels, "model_id": "tfidf_linsvc",
                 "seed": SEED, "n_train_total": len(rows)}, MODEL_PATH)
    sidecar = {"model_id": "tfidf_linsvc", "champion_day": 3,
               "heldout_macro_f1": macro, "heldout_accuracy": acc,
               "fit_sec": round(fit_sec, 3), "n_classes": len(labels),
               "classes": labels, "n_train_heldout": len(train),
               "n_test_heldout": len(test), "n_train_shipped": len(rows)}
    json.dump(sidecar, open(os.path.join(MODEL_DIR, "expense_classifier.metrics.json"), "w"),
              indent=2)
    print(f"saved {MODEL_PATH}  ({len(rows)} rows, {len(labels)} classes)")
    return sidecar


if __name__ == "__main__":
    main()
