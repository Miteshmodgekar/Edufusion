"""
ML Model Trainer — Standalone script.
Generates synthetic academic data, trains a Logistic Regression model,
saves model.pkl and scaler.pkl to the ml/ directory.

Usage:
    python ml/train_model.py

After running, the Flask app will use this model for predictions.
"""

import os
import sys
import pickle
import numpy as np

# Add parent directory to path so we can import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def generate_training_data(n_samples: int = 300):
    """
    Generate realistic synthetic academic data for training.
    Features: [attendance_pct, internal_marks, external_marks]
    Label: 1 = PASS, 0 = FAIL
    """
    np.random.seed(42)

    # ── Strong performers (PASS) ──────────────────────────────────────────
    n_pass = int(n_samples * 0.65)
    pass_att  = np.random.normal(88, 8, n_pass).clip(75, 100)
    pass_int  = np.random.normal(36, 7, n_pass).clip(20, 50)
    pass_ext  = np.random.normal(65, 12, n_pass).clip(35, 100)

    # ── Weak performers (FAIL) ────────────────────────────────────────────
    n_fail = n_samples - n_pass
    fail_att  = np.random.normal(62, 12, n_fail).clip(30, 85)
    fail_int  = np.random.normal(16, 6,  n_fail).clip(0,  35)
    fail_ext  = np.random.normal(28, 10, n_fail).clip(0,  50)

    X = np.vstack([
        np.column_stack([pass_att, pass_int, pass_ext]),
        np.column_stack([fail_att, fail_int, fail_ext]),
    ])
    y = np.array([1] * n_pass + [0] * n_fail)

    # Shuffle
    idx = np.random.permutation(len(X))
    return X[idx], y[idx]


def train_and_save():
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import (accuracy_score, classification_report,
                                     confusion_matrix)
    except ImportError:
        print("ERROR: scikit-learn not installed.")
        print("Run: pip install scikit-learn numpy")
        sys.exit(1)

    print("=" * 55)
    print("CSE Smart System — ML Model Trainer")
    print("=" * 55)

    # Generate data
    print("\n[1] Generating synthetic training data...")
    X, y = generate_training_data(300)
    print(f"    Samples: {len(X)}  (PASS: {y.sum()}, FAIL: {(y==0).sum()})")

    # Split
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    print(f"    Train: {len(X_train)}  |  Test: {len(X_test)}")

    # Scale
    print("\n[2] Scaling features (StandardScaler)...")
    scaler  = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_test_s  = scaler.transform(X_test)

    # Train
    print("\n[3] Training Logistic Regression...")
    model = LogisticRegression(
        C=1.0, max_iter=500, random_state=42, solver="lbfgs"
    )
    model.fit(X_train_s, y_train)

    # Evaluate
    print("\n[4] Evaluation:")
    y_pred = model.predict(X_test_s)
    acc    = accuracy_score(y_test, y_pred)
    print(f"    Accuracy: {acc * 100:.2f}%")
    print("\n    Classification Report:")
    report = classification_report(y_test, y_pred,
                                   target_names=["FAIL", "PASS"])
    for line in report.split("\n"):
        print(f"      {line}")

    cm = confusion_matrix(y_test, y_pred)
    print(f"\n    Confusion Matrix:")
    print(f"      [TN={cm[0][0]}  FP={cm[0][1]}]")
    print(f"      [FN={cm[1][0]}  TP={cm[1][1]}]")

    # Feature importance (coefficients)
    features = ["Attendance %", "Internal Marks", "External Marks"]
    print("\n    Feature Coefficients:")
    for fname, coef in zip(features, model.coef_[0]):
        bar = "█" * int(abs(coef) * 5)
        direction = "+" if coef > 0 else "-"
        print(f"      {fname:<20} {direction}{abs(coef):.3f}  {bar}")

    # Save
    save_dir = os.path.dirname(os.path.abspath(__file__))
    model_path  = os.path.join(save_dir, "model.pkl")
    scaler_path = os.path.join(save_dir, "scaler.pkl")

    print(f"\n[5] Saving model...")
    with open(model_path,  "wb") as f: pickle.dump(model,  f)
    with open(scaler_path, "wb") as f: pickle.dump(scaler, f)
    print(f"    model.pkl  → {model_path}")
    print(f"    scaler.pkl → {scaler_path}")

    print("\n" + "=" * 55)
    print(f"  Model trained successfully! Accuracy: {acc*100:.2f}%")
    print("  Flask app will now use this model for predictions.")
    print("=" * 55)

    return {"accuracy": round(acc * 100, 2), "samples": len(X)}


if __name__ == "__main__":
    train_and_save()
