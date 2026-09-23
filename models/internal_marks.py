"""
InternalMarks Model — VTU CIE Format.

Subject Types:
  ipcc_theory : Q1-Q4 (a,b,c,d) x 2 IAs, scaled to 25M, Assignment 25M
                CIE = avg(IA1_scaled, IA2_scaled) + Assignment
  cc_theory   : Q1-Q4 (a,b,c,d) x 2 IAs, scaled to 15M, Assignment 20M
                CIE = IA1_scaled + IA2_scaled + Assignment
  ipcc_lab    : Q1-Q4 x 2 IAs (scaled 15M) + Lab IA 25M + Assignment 10M
                CIE = IA1_scaled + IA2_scaled + Lab IA + Assignment
  cc_activity : Module 1-5 (20M each) = Total 100M
  cc_oe       : Gen 30M + CIE 20M = Total 50M

Structure per IA (for ipcc_theory / cc_theory / ipcc_lab):
  Q1(a,b,c,d) = Module-1 option 1   Q2(a,b,c,d) = Module-1 option 2
  Q3(a,b,c,d) = Module-2 option 1   Q4(a,b,c,d) = Module-2 option 2
  Module-1 best = MAX(Q1_total, Q2_total)  (max 25)
  Module-2 best = MAX(Q3_total, Q4_total)  (max 25)
  IA Total (raw) = Module-1 best + Module-2 best  (max 50)
"""
from extensions import db
from datetime import datetime, timezone, timedelta

_IST = timezone(timedelta(hours=5, minutes=30))
def _ist_now(): return datetime.now(_IST).replace(tzinfo=None)

# Exported constants used by marks blueprint
ASSIGN_MAX = {
    'ipcc_theory': 25, 'cc_theory': 20, 'ipcc_lab': 10,
    'cc_activity': 0,  'cc_oe': 0,
}


def _q_total(a, b, c, d):
    return round(sum(v or 0 for v in [a, b, c, d]), 2)


# Scale factors: raw IA (out of 50) multiplied by this to get scaled mark
# ipcc_lab = combined IPCC: best IA(15) + Assignment(10) + Lab IA(25) = 50
_SCALE = {
    'ipcc_theory': 25 / 50,
    'cc_theory':   15 / 50,
    'ipcc_lab':    15 / 50,   # each IA out of 50 → scaled to 15M
}

# Default max assignment marks per type
_ASSIGN_MAX = {
    'ipcc_theory': 25,
    'cc_theory':   20,
    'ipcc_lab':    10,   # Assignment 10M for IPCC combined
    'cc_activity': 0,
    'cc_oe':       0,
}


class InternalMarks(db.Model):
    __tablename__ = "internal_marks"

    id            = db.Column(db.Integer, primary_key=True)
    student_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    faculty_id    = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    subject       = db.Column(db.String(120), nullable=False)
    subject_code  = db.Column(db.String(30),  nullable=True)
    semester      = db.Column(db.Integer,      nullable=False)
    section       = db.Column(db.String(5),    nullable=True)
    academic_year = db.Column(db.String(12),   nullable=True)

    # Subject type — determines marks structure and computation
    subject_type  = db.Column(db.String(20), nullable=True, default='cc_theory')

    # ── IA1 — Q1 (Module-1 option 1)  a+b+c+d <= 25 ────────────────────────
    ia1_q1_a = db.Column(db.Float, nullable=True)
    ia1_q1_b = db.Column(db.Float, nullable=True)
    ia1_q1_c = db.Column(db.Float, nullable=True)
    ia1_q1_d = db.Column(db.Float, nullable=True)
    # ── IA1 — Q2 (Module-1 option 2) ─────────────────────────────────────────
    ia1_q2_a = db.Column(db.Float, nullable=True)
    ia1_q2_b = db.Column(db.Float, nullable=True)
    ia1_q2_c = db.Column(db.Float, nullable=True)
    ia1_q2_d = db.Column(db.Float, nullable=True)
    # ── IA1 — Q3 (Module-2 option 1) ─────────────────────────────────────────
    ia1_q3_a = db.Column(db.Float, nullable=True)
    ia1_q3_b = db.Column(db.Float, nullable=True)
    ia1_q3_c = db.Column(db.Float, nullable=True)
    ia1_q3_d = db.Column(db.Float, nullable=True)
    # ── IA1 — Q4 (Module-2 option 2) ─────────────────────────────────────────
    ia1_q4_a = db.Column(db.Float, nullable=True)
    ia1_q4_b = db.Column(db.Float, nullable=True)
    ia1_q4_c = db.Column(db.Float, nullable=True)
    ia1_q4_d = db.Column(db.Float, nullable=True)
    ia1_total = db.Column(db.Float, nullable=True)   # raw total out of 50

    # ── IA2 — same structure ──────────────────────────────────────────────────
    ia2_q1_a = db.Column(db.Float, nullable=True)
    ia2_q1_b = db.Column(db.Float, nullable=True)
    ia2_q1_c = db.Column(db.Float, nullable=True)
    ia2_q1_d = db.Column(db.Float, nullable=True)
    ia2_q2_a = db.Column(db.Float, nullable=True)
    ia2_q2_b = db.Column(db.Float, nullable=True)
    ia2_q2_c = db.Column(db.Float, nullable=True)
    ia2_q2_d = db.Column(db.Float, nullable=True)
    ia2_q3_a = db.Column(db.Float, nullable=True)
    ia2_q3_b = db.Column(db.Float, nullable=True)
    ia2_q3_c = db.Column(db.Float, nullable=True)
    ia2_q3_d = db.Column(db.Float, nullable=True)
    ia2_q4_a = db.Column(db.Float, nullable=True)
    ia2_q4_b = db.Column(db.Float, nullable=True)
    ia2_q4_c = db.Column(db.Float, nullable=True)
    ia2_q4_d = db.Column(db.Float, nullable=True)
    ia2_total = db.Column(db.Float, nullable=True)

    # ── Assignment ────────────────────────────────────────────────────────────
    assignment     = db.Column(db.Float, nullable=True)
    max_assignment = db.Column(db.Float, default=10.0)
    remarks        = db.Column(db.Text,  nullable=True)

    # ── Lab IA (ipcc_lab type — BCS303 style) ─────────────────────────────────
    lab_ia = db.Column(db.Float, nullable=True)   # Lab IA marks out of 25

    # ── Activity Modules (cc_activity type — BSCK307 style) ──────────────────
    mod1 = db.Column(db.Float, nullable=True)   # Module 1 — max 20
    mod2 = db.Column(db.Float, nullable=True)   # Module 2 — max 20
    mod3 = db.Column(db.Float, nullable=True)   # Module 3 — max 20
    mod4 = db.Column(db.Float, nullable=True)   # Module 4 — max 20
    mod5 = db.Column(db.Float, nullable=True)   # Module 5 — max 20

    # ── Open Elective (cc_oe type — BCS3058A style) ───────────────────────────
    oe_gen = db.Column(db.Float, nullable=True)   # Gen marks — max 30
    oe_cie = db.Column(db.Float, nullable=True)   # CIE marks — max 20

    updated_at = db.Column(db.DateTime, default=_ist_now, onupdate=_ist_now)

    student = db.relationship("User", foreign_keys=[student_id], backref="internal_marks")
    faculty = db.relationship("User", foreign_keys=[faculty_id])

    # ── Q totals (raw sub-part sums) ──────────────────────────────────────────
    def ia1_q1_total(self): return _q_total(self.ia1_q1_a, self.ia1_q1_b, self.ia1_q1_c, self.ia1_q1_d)
    def ia1_q2_total(self): return _q_total(self.ia1_q2_a, self.ia1_q2_b, self.ia1_q2_c, self.ia1_q2_d)
    def ia1_q3_total(self): return _q_total(self.ia1_q3_a, self.ia1_q3_b, self.ia1_q3_c, self.ia1_q3_d)
    def ia1_q4_total(self): return _q_total(self.ia1_q4_a, self.ia1_q4_b, self.ia1_q4_c, self.ia1_q4_d)
    def ia1_parta(self):    return max(self.ia1_q1_total(), self.ia1_q2_total())  # Module-1 best
    def ia1_partb(self):    return max(self.ia1_q3_total(), self.ia1_q4_total())  # Module-2 best

    def ia2_q1_total(self): return _q_total(self.ia2_q1_a, self.ia2_q1_b, self.ia2_q1_c, self.ia2_q1_d)
    def ia2_q2_total(self): return _q_total(self.ia2_q2_a, self.ia2_q2_b, self.ia2_q2_c, self.ia2_q2_d)
    def ia2_q3_total(self): return _q_total(self.ia2_q3_a, self.ia2_q3_b, self.ia2_q3_c, self.ia2_q3_d)
    def ia2_q4_total(self): return _q_total(self.ia2_q4_a, self.ia2_q4_b, self.ia2_q4_c, self.ia2_q4_d)
    def ia2_parta(self):    return max(self.ia2_q1_total(), self.ia2_q2_total())
    def ia2_partb(self):    return max(self.ia2_q3_total(), self.ia2_q4_total())

    # ── Scale helpers ──────────────────────────────────────────────────────────
    @property
    def _sf(self):
        """Scale factor: multiply raw IA total (out of 50) to get scaled mark."""
        return _SCALE.get(self.subject_type or 'cc_theory', 15 / 50)

    @property
    def ia1_scaled(self):
        return round((self.ia1_total or 0) * self._sf, 1)

    @property
    def ia2_scaled(self):
        return round((self.ia2_total or 0) * self._sf, 1)

    @property
    def scale_to(self):
        st = self.subject_type or 'cc_theory'
        if st == 'ipcc_theory': return 25
        if st == 'ipcc_lab':    return 15   # each IA scaled to 15M
        if st == 'cc_theory':   return 15
        return 0

    def recompute_totals(self):
        st = self.subject_type or 'cc_theory'
        if st in ('ipcc_theory', 'cc_theory', 'ipcc_lab'):
            self.ia1_total = round(self.ia1_parta() + self.ia1_partb(), 2)
            self.ia2_total = round(self.ia2_parta() + self.ia2_partb(), 2)

    @property
    def total_cie(self):
        st = self.subject_type or 'cc_theory'
        if st == 'ipcc_theory':
            avg = round((self.ia1_scaled + self.ia2_scaled) / 2, 1)
            return round(avg + (self.assignment or 0), 2)
        elif st == 'cc_theory':
            return round(self.ia1_scaled + self.ia2_scaled + (self.assignment or 0), 2)
        elif st == 'ipcc_lab':
            # Combined IPCC: best IA(15) + Assignment(10) + Lab IA(25) = 50 max
            best_ia = max(self.ia1_scaled, self.ia2_scaled)
            return round(best_ia + (self.assignment or 0) + (self.lab_ia or 0), 2)
        elif st == 'cc_activity':
            return round(sum((getattr(self, f'mod{i}') or 0) for i in range(1, 6)), 2)
        elif st == 'cc_oe':
            return round((self.oe_gen or 0) + (self.oe_cie or 0), 2)
        return 0

    def to_dict(self):
        st = self.subject_type or 'cc_theory'
        base = {
            "id": self.id, "student_id": self.student_id,
            "student_name":  self.student.name        if self.student else "—",
            "roll_number":   self.student.roll_number  if self.student else "—",
            "subject": self.subject, "subject_code": self.subject_code or "",
            "semester": self.semester, "section": self.section or "",
            "academic_year": self.academic_year or "",
            "subject_type": st, "scale_to": self.scale_to,
            "assignment": self.assignment, "max_assignment": self.max_assignment or 10,
            "total_cie": self.total_cie,
            "remarks": self.remarks or "",
            "updated_at": self.updated_at.strftime("%d %b %Y, %I:%M %p") if self.updated_at else "—",
            "faculty_name": self.faculty.name if self.faculty else "—",
        }

        if st in ('ipcc_theory', 'cc_theory', 'ipcc_lab'):
            base.update({
                "ia1_q1_a": self.ia1_q1_a, "ia1_q1_b": self.ia1_q1_b,
                "ia1_q1_c": self.ia1_q1_c, "ia1_q1_d": self.ia1_q1_d,
                "ia1_q1_total": self.ia1_q1_total(),
                "ia1_q2_a": self.ia1_q2_a, "ia1_q2_b": self.ia1_q2_b,
                "ia1_q2_c": self.ia1_q2_c, "ia1_q2_d": self.ia1_q2_d,
                "ia1_q2_total": self.ia1_q2_total(),
                "ia1_q3_a": self.ia1_q3_a, "ia1_q3_b": self.ia1_q3_b,
                "ia1_q3_c": self.ia1_q3_c, "ia1_q3_d": self.ia1_q3_d,
                "ia1_q3_total": self.ia1_q3_total(),
                "ia1_q4_a": self.ia1_q4_a, "ia1_q4_b": self.ia1_q4_b,
                "ia1_q4_c": self.ia1_q4_c, "ia1_q4_d": self.ia1_q4_d,
                "ia1_q4_total": self.ia1_q4_total(),
                "ia1_parta": self.ia1_parta(), "ia1_partb": self.ia1_partb(),
                "ia1_total": self.ia1_total or 0, "ia1_scaled": self.ia1_scaled,
                "ia2_q1_a": self.ia2_q1_a, "ia2_q1_b": self.ia2_q1_b,
                "ia2_q1_c": self.ia2_q1_c, "ia2_q1_d": self.ia2_q1_d,
                "ia2_q1_total": self.ia2_q1_total(),
                "ia2_q2_a": self.ia2_q2_a, "ia2_q2_b": self.ia2_q2_b,
                "ia2_q2_c": self.ia2_q2_c, "ia2_q2_d": self.ia2_q2_d,
                "ia2_q2_total": self.ia2_q2_total(),
                "ia2_q3_a": self.ia2_q3_a, "ia2_q3_b": self.ia2_q3_b,
                "ia2_q3_c": self.ia2_q3_c, "ia2_q3_d": self.ia2_q3_d,
                "ia2_q3_total": self.ia2_q3_total(),
                "ia2_q4_a": self.ia2_q4_a, "ia2_q4_b": self.ia2_q4_b,
                "ia2_q4_c": self.ia2_q4_c, "ia2_q4_d": self.ia2_q4_d,
                "ia2_q4_total": self.ia2_q4_total(),
                "ia2_parta": self.ia2_parta(), "ia2_partb": self.ia2_partb(),
                "ia2_total": self.ia2_total or 0, "ia2_scaled": self.ia2_scaled,
            })
            if st == 'ipcc_lab':
                base["lab_ia"] = self.lab_ia
        elif st == 'cc_activity':
            for i in range(1, 6):
                base[f'mod{i}'] = getattr(self, f'mod{i}')
        elif st == 'cc_oe':
            base["oe_gen"] = self.oe_gen
            base["oe_cie"] = self.oe_cie

        return base
