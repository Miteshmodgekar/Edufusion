"""
ML Blueprint - Performance Prediction (PASS/FAIL) + Risk Classification.
Uses Scikit-learn Logistic Regression or rule-based fallback.
"""

import os
import pickle
import numpy as np
from flask import Blueprint, request, jsonify, render_template, current_app
from flask_login import login_required, current_user
from extensions import db
from models.placement import StudentPerformance
from models.attendance import AttendanceSummary
from models.user import User
from config import Config
from utils.live_attendance import get_avg_attendance_for_student, get_live_attendance_path

ml_bp = Blueprint("ml", __name__)


# ── Model helpers ─────────────────────────────────────────────────────────────

def _load_model():
    try:
        if os.path.exists(Config.ML_MODEL_PATH):
            with open(Config.ML_MODEL_PATH, "rb") as f:
                return pickle.load(f)
    except Exception:
        pass
    return None


def _load_scaler():
    try:
        if os.path.exists(Config.ML_SCALER_PATH):
            with open(Config.ML_SCALER_PATH, "rb") as f:
                return pickle.load(f)
    except Exception:
        pass
    return None


# ── Core prediction logic ─────────────────────────────────────────────────────

def predict_performance(attendance_pct, internal_marks, external_marks):
    """PASS/FAIL prediction — ML model if trained, else rule-based."""
    model  = _load_model()
    scaler = _load_scaler()

    features = np.array([[attendance_pct, internal_marks, external_marks]])

    if model and scaler:
        scaled     = scaler.transform(features)
        prediction = model.predict(scaled)[0]
        proba      = model.predict_proba(scaled)[0]
        return {
            "result":     "PASS" if prediction == 1 else "FAIL",
            "confidence": round(float(max(proba)) * 100, 1),
            "method":     "ml_model",
        }

    # Rule-based fallback
    total  = internal_marks + external_marks
    passed = (attendance_pct >= 75.0 and internal_marks >= 20.0
              and external_marks >= 35.0 and total >= 50.0)
    conf   = 90 if (total > 70 and attendance_pct > 85) else 75
    return {"result": "PASS" if passed else "FAIL", "confidence": conf,
            "method": "rule_based"}


def classify_risk(attendance_pct, internal_marks, progress_pct):
    """Low / Medium / High risk — rule-based classification."""
    score   = 0
    reasons = []

    if attendance_pct < 75:
        score += 3
        reasons.append(f"Critical attendance ({attendance_pct}%)")
    elif attendance_pct < 85:
        score += 2
        reasons.append(f"Low attendance ({attendance_pct}%)")

    if internal_marks < 15:
        score += 3
        reasons.append(f"Very low internal marks ({internal_marks}/50)")
    elif internal_marks < 25:
        score += 2
        reasons.append(f"Below average internal marks ({internal_marks}/50)")

    if progress_pct < 30:
        score += 2
        reasons.append(f"Poor project progress ({progress_pct}%)")
    elif progress_pct < 60:
        score += 1
        reasons.append(f"Moderate project progress ({progress_pct}%)")

    if score >= 5:
        level, color = "HIGH",   "danger"
        action = "Immediate intervention required. Contact mentor and parents."
    elif score >= 2:
        level, color = "MEDIUM", "warning"
        action = "Monitor closely. Counselling recommended."
    else:
        level, color = "LOW",    "success"
        action = "Student is performing well. Keep it up!"

    return {"risk_level": level, "risk_score": score,
            "color": color, "action": action, "reasons": reasons}


# ── Routes ────────────────────────────────────────────────────────────────────

@ml_bp.route("/predict", methods=["GET"])
@login_required
def predict_page():
    # Pass current user's USN so the template can auto-fill and auto-load
    my_usn = getattr(current_user, "roll_number", None) or getattr(current_user, "username", "")
    return render_template("ml/predict.html", my_usn=my_usn)


@ml_bp.route("/predict", methods=["POST"])
@login_required
def predict():
    data           = request.get_json()
    attendance_pct = float(data.get("attendance_pct", 0))
    internal_marks = float(data.get("internal_marks", 0))
    external_marks = float(data.get("external_marks", 0))
    result         = predict_performance(attendance_pct, internal_marks, external_marks)
    return jsonify({"success": True, **result})


@ml_bp.route("/risk", methods=["POST"])
@login_required
def risk():
    data           = request.get_json()
    attendance_pct = float(data.get("attendance_pct", 0))
    internal_marks = float(data.get("internal_marks", 0))
    progress_pct   = float(data.get("progress_pct", 0))
    result         = classify_risk(attendance_pct, internal_marks, progress_pct)
    return jsonify({"success": True, **result})


@ml_bp.route("/class-data", methods=["GET"])
@login_required
def class_data():
    """
    Return prediction + risk data for ALL students in the DB,
    optionally filtered by ?sem=5&sec=A
    Used by the Class Overview and Risk Analysis tabs.
    """
    try:
        sem = request.args.get("sem", "").strip()
        sec = request.args.get("sec", "").strip()

        # Build query — students only
        q = User.query.filter_by(role="student", is_active=True)
        if sem:
            q = q.filter(User.semester == int(sem))
        if sec:
            q = q.filter(User.section.ilike(sec))

        students = q.order_by(User.roll_number).all()

        results = []
        for student in students:
            try:
                # Attendance from DB
                summaries = AttendanceSummary.query.filter_by(student_id=student.id).all()
                avg_att = round(sum(s.attendance_pct for s in summaries) / len(summaries), 1) if summaries else 75.0

                # Marks from DB
                perfs = StudentPerformance.query.filter_by(student_id=student.id).all()
                avg_internal = round(sum(p.internal_marks or 0 for p in perfs) / len(perfs), 1) if perfs else 0.0
                avg_external = round(sum(p.external_marks or 0 for p in perfs) / len(perfs), 1) if perfs else 0.0

                pred = predict_performance(avg_att, avg_internal, avg_external)
                risk = classify_risk(avg_att, avg_internal, 50)

                results.append({
                    "id":           student.id,
                    "name":         student.name,
                    "roll":         student.roll_number or student.username,
                    "sem":          str(student.semester or ""),
                    "sec":          (student.section or "").upper(),
                    "att":          avg_att,
                    "int_m":        avg_internal,
                    "ext_m":        avg_external,
                    "result":       pred["result"],
                    "confidence":   pred["confidence"],
                    "level":        risk["risk_level"],
                    "risk_score":   risk["risk_score"],
                    "reasons":      risk["reasons"],
                    "action":       risk["action"],
                })
            except Exception:
                continue  # skip broken student records silently

        return jsonify({"success": True, "students": results, "total": len(results)})

    except Exception as e:
        import traceback
        current_app.logger.error("class_data error: %s\n%s", e, traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500


@ml_bp.route("/analyze/<student_id>", methods=["GET"])
@login_required
def analyze_student(student_id):
    try:
        # ── 1. Find student (case-insensitive USN or username) ──────────────
        from sqlalchemy import func as sqlfunc
        student = User.query.filter(
            sqlfunc.upper(User.roll_number) == student_id.upper()
        ).first()
        if not student:
            student = User.query.filter(
                sqlfunc.upper(User.username) == student_id.upper()
            ).first()
        if not student and student_id.isdigit():
            student = db.session.get(User, int(student_id))
        if not student:
            return jsonify({"success": False,
                            "message": f"No student found for '{student_id}'. Please check the USN."}), 404

        if current_user.is_student and current_user.id != student.id:
            return jsonify({"success": False, "message": "Access denied."}), 403

        # ── 2. Attendance from DB (simple, no Excel needed) ─────────────────
        from models.attendance import AttendanceSummary
        summaries = AttendanceSummary.query.filter_by(student_id=student.id).all()
        if summaries:
            avg_att = round(sum(s.attendance_pct for s in summaries) / len(summaries), 1)
        else:
            avg_att = 75.0   # safe default so sliders still work

        # ── 3. Internal / External marks from DB ────────────────────────────
        perfs = StudentPerformance.query.filter_by(student_id=student.id).all()
        if perfs:
            avg_internal = round(sum(p.internal_marks or 0 for p in perfs) / len(perfs), 1)
            avg_external = round(sum(p.external_marks or 0 for p in perfs) / len(perfs), 1)
        else:
            avg_internal = 0.0
            avg_external = 0.0

        # ── 4. Predict ───────────────────────────────────────────────────────
        prediction  = predict_performance(avg_att, avg_internal, avg_external)
        risk_result = classify_risk(avg_att, avg_internal, 50)

        return jsonify({
            "success":        True,
            "student_id":     student.id,
            "student_name":   student.name,
            "avg_attendance": avg_att,
            "avg_internal":   avg_internal,
            "avg_external":   avg_external,
            "prediction":     prediction,
            "risk":           risk_result,
        })

    except Exception as e:
        import traceback
        current_app.logger.error("analyze_student crashed: %s\n%s", e, traceback.format_exc())
        return jsonify({"success": False, "message": f"Server error: {e}"}), 500


@ml_bp.route("/train", methods=["POST"])
@login_required
def train_model():
    if not current_user.is_admin:
        return jsonify({"success": False, "message": "Admin only."}), 403

    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import accuracy_score

        perfs = StudentPerformance.query.filter(
            StudentPerformance.internal_marks.isnot(None),
            StudentPerformance.external_marks.isnot(None),
            StudentPerformance.result.isnot(None),
        ).all()

        if len(perfs) < 10:
            return jsonify({
                "success": False,
                "message": f"Need ≥ 10 records. Found {len(perfs)}."
            }), 400

        X, y = [], []
        for p in perfs:
            summaries = AttendanceSummary.query.filter_by(
                student_id=p.student_id, semester=p.semester).all()
            att = (sum(s.attendance_pct for s in summaries) / len(summaries)
                   if summaries else 75)
            X.append([att, p.internal_marks, p.external_marks])
            y.append(1 if p.result == "PASS" else 0)

        X_tr, X_te, y_tr, y_te = train_test_split(
            np.array(X), np.array(y), test_size=0.2, random_state=42)

        scaler = StandardScaler()
        model  = LogisticRegression(max_iter=500)
        model.fit(scaler.fit_transform(X_tr), y_tr)
        acc = accuracy_score(y_te, model.predict(scaler.transform(X_te)))

        os.makedirs(os.path.dirname(Config.ML_MODEL_PATH), exist_ok=True)
        with open(Config.ML_MODEL_PATH,  "wb") as f: pickle.dump(model,  f)
        with open(Config.ML_SCALER_PATH, "wb") as f: pickle.dump(scaler, f)

        return jsonify({"success": True,
                        "accuracy": round(acc * 100, 2),
                        "samples":  len(X),
                        "message":  f"Model trained — {round(acc*100,2)}% accuracy."})

    except ImportError:
        return jsonify({"success": False,
                        "message": "scikit-learn not installed."}), 500
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500
