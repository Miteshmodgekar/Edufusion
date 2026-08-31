# AI-Driven Smart Academic, Attendance & Placement Management System

> A comprehensive CSE department management system with ML prediction,
> PWA support, multi-level leave approval, attendance automation,
> placement tracking, email notifications, and push alerts.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER LAYER                               │
│   Student · Faculty · HOD · Admin                               │
└──────────────────────┬──────────────────────────────────────────┘
                       │ HTTPS · Flask-Login Sessions
┌──────────────────────▼──────────────────────────────────────────┐
│                    FRONTEND LAYER                               │
│   Jinja2 Templates + Bootstrap 5 + Chart.js                    │
│   15 HTML pages across 8 modules                               │
└──────────────────────┬──────────────────────────────────────────┘
                       │ Flask Blueprints · REST JSON API
┌──────────────────────▼──────────────────────────────────────────┐
│                    BACKEND LAYER (Flask)                        │
│   9 Blueprints  ·  55 Routes                                   │
│   auth · attendance · leave · ml · placement                   │
│   project · alert · admin · dashboard                          │
└────────┬─────────────┬──────────────┬───────────────────────────┘
         │             │              │
    ┌────▼────┐  ┌─────▼─────┐  ┌────▼──────┐
    │  ML     │  │  PDF      │  │  Email +  │
    │ Engine  │  │ Generator │  │  Push     │
    │Scikit   │  │ReportLab  │  │Flask-Mail │
    │-learn   │  │6 reports  │  │pywebpush  │
    └────┬────┘  └─────┬─────┘  └────┬──────┘
         │             │              │
┌────────▼─────────────▼──────────────▼───────────────────────────┐
│                    DATA LAYER (SQLAlchemy ORM)                  │
│   User · AttendanceRecord · AttendanceSummary                  │
│   LeaveRequest · PlacementProfile · Internship                 │
│   Project · ProjectUpdate · StudentPerformance                 │
│   PushSubscription · NotificationLog                           │
│                  SQLite (dev) · PostgreSQL (prod)               │
└──────────────────────┬──────────────────────────────────────────┘
                       │ Web Push API · VAPID · Service Worker
┌──────────────────────▼──────────────────────────────────────────┐
│                      PWA LAYER                                  │
│   manifest.json · sw.js · Push Notifications · Offline Cache   │
└─────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

```bash
# 1. Extract and enter project
cd smart_academic_system

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate       # Mac/Linux
venv\Scripts\activate          # Windows

# 3. Install all dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — at minimum set SECRET_KEY

# 5. Run
python app.py
# → http://localhost:5000
```

---

## Demo Credentials

| Role    | Username   | Password   | Access                              |
|---------|-----------|------------|-------------------------------------|
| Student | CS21001   | student123 | Attendance, leave, ML, placement    |
| Faculty | FAC001    | faculty123 | Upload, approve, analytics          |
| HOD     | HOD001    | hod123     | Final approvals, dept overview      |
| Admin   | admin     | admin123   | Full system, reports, user management|

---

## Project Structure

```
smart_academic_system/
│
├── app.py                    ← Flask app factory + seeder
├── config.py                 ← All settings (DB, email, VAPID, ML)
├── wsgi.py                   ← Gunicorn production entry point
├── requirements.txt          ← All Python dependencies
├── Procfile                  ← Heroku/Render/Railway deployment
├── Dockerfile                ← Docker containerisation
├── .env.example              ← Environment variable template
├── DEPLOY.md                 ← Deployment guide (Render/Railway/Docker)
│
├── models/                   ← SQLAlchemy ORM models
│   ├── user.py               ← User (Student/Faculty/HOD/Admin)
│   ├── attendance.py         ← AttendanceRecord + AttendanceSummary
│   ├── leave.py              ← LeaveRequest (3-level approval)
│   ├── placement.py          ← PlacementProfile, Internship, Project,
│   │                            ProjectUpdate, StudentPerformance
│   └── notification.py       ← PushSubscription, NotificationLog
│
├── routes/                   ← Flask blueprints (9 total, 55 routes)
│   ├── auth.py               ← Login, logout, register, profile
│   ├── attendance.py         ← Excel upload, analysis, simulator
│   ├── leave.py              ← Apply, mentor approve, HOD approve
│   ├── ml_routes.py          ← PASS/FAIL prediction, risk classification
│   ├── placement.py          ← Profile, internship, eligibility
│   ├── project.py            ← Allocation, updates, guide approval
│   ├── dashboard.py          ← Role-based dashboard data API
│   ├── alert.py              ← Email/push alerts, broadcast, logs
│   └── admin.py              ← Users, audit logs, exports, PDF reports
│
├── utils/                    ← Utility services
│   ├── notifications.py      ← Email templates + push notification logic
│   ├── pdf_generator.py      ← ReportLab PDF reports (6 types)
│   └── generate_vapid.py     ← One-time VAPID key generator
│
├── templates/                ← Jinja2 HTML templates (15 pages)
│   ├── base.html             ← Sidebar layout, Bootstrap 5, PWA
│   ├── login.html            ← Role-based login page
│   ├── profile.html          ← User profile + password change
│   ├── dashboard/index.html  ← Role-aware dashboard with Chart.js
│   ├── attendance/
│   │   ├── upload.html       ← Drag-drop Excel upload + results
│   │   └── simulator.html    ← What-If attendance simulator
│   ├── leave/
│   │   ├── apply.html        ← Student leave application
│   │   ├── mentor_approve.html ← Faculty approval page
│   │   └── hod_approve.html  ← HOD final approval page
│   ├── ml/predict.html       ← ML prediction + risk analysis
│   ├── placement/profile.html ← Placement readiness + internships
│   ├── project/board.html    ← Project monitoring board
│   ├── notifications/centre.html ← Email/push notification centre
│   └── admin/
│       ├── dashboard.html    ← Admin panel (5 tabs)
│       └── register.html     ← User registration
│
├── static/
│   ├── manifest.json         ← PWA manifest (installable app)
│   └── sw.js                 ← Service Worker (offline + push)
│
├── uploads/                  ← Faculty-uploaded Excel files
├── ml/                       ← Trained model files (after training)
└── generate_sample_excel.py  ← Generate test attendance Excel
```

---

## Modules & Features

### 1. Smart Attendance Analyzer
- Excel/CSV upload (drag-drop) with pandas processing
- Auto-calculates attendance % per student per subject
- Detects defaulters (< 85%) with classes-needed count
- Bar chart + doughnut distribution visualization
- One-click CSV export of results + defaulter list
- Sends automatic email + push to all defaulters

### 2. What-If Attendance Simulator
- Formula: `((A + X) / (T + X)) × 100`
- Live projection curve chart (next 20 classes)
- Shows classes needed AND safe-to-skip count
- 4 scenario cards (miss 5, attend 5, etc.)
- Available standalone and embedded in upload page

### 3. Multi-Level Leave Management
- Student applies → Mentor reviews → HOD approves
- Visual 4-step approval pipeline per leave card
- Automatic attendance adjustment on HOD approval
- Email notification at each approval stage
- Leave types: Medical, Personal, Event, On Duty

### 4. ML Performance Prediction
- Algorithm: Logistic Regression (Scikit-learn)
- Features: Attendance %, Internal Marks, External Marks
- Output: PASS/FAIL with confidence %
- Rule-based fallback when model not trained
- Admin can retrain model via dashboard

### 5. Student Risk Classification
- Three levels: LOW / MEDIUM / HIGH
- Factors: Attendance, Internal Marks, Project Progress
- SVG arc risk meter with animated needle
- Automatic email + push for HIGH risk students
- Bulk ML scan trigger from faculty dashboard

### 6. Placement & Internship Tracking
- Readiness score = CGPA(40%) + Attendance(30%) + Skills(20%) + Certs(10%)
- Eligibility check: CGPA ≥ 6.0, no backlogs, attendance ≥ 75%
- Animated SVG score ring (live update)
- Internship tracker with company/role/duration/stipend
- Faculty view: filter eligible students

### 7. Project Monitoring
- Project allocation with guide assignment
- Weekly update submission by students
- Guide approval with comments + auto-progress increment
- SVG progress ring per project
- Supports all statuses: Allocated → In Progress → Review → Completed

### 8. Admin Panel
- User management: view all, enable/disable accounts
- Audit log: real-time event feed from DB
- ML controls: model status, retrain, config thresholds
- Database stats with visual bars
- One-click exports: attendance CSV, defaulters CSV, DB backup

### 9. PDF Report Generation
| Report | Contents |
|--------|----------|
| Attendance | Full summary with defaulter list |
| Performance | Subject-wise marks, PASS/FAIL |
| Risk Analysis | HIGH/MEDIUM/LOW classification |
| Placement | Readiness scores, eligibility |
| Leave Summary | All requests with approval status |
| Full Report | All modules combined |

### 10. Email + PWA Push Notifications
- 4 email templates (attendance alert, leave decision, placement, risk)
- PWA one-click subscribe banner in notification centre
- Service worker handles push events, shows rich notifications
- Broadcast to all / defaulters only / custom message
- Complete NotificationLog table tracks every dispatch

---

## API Reference

### Auth  `url_prefix: /auth`
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/login` | Login page |
| POST | `/login` | Authenticate (JSON) |
| GET | `/logout` | Logout |
| GET/POST | `/register` | Create user (admin) |
| POST | `/change-password` | Change password |
| GET | `/profile` | Profile page |
| GET | `/api/me` | Current user JSON |

### Attendance  `url_prefix: /attendance`
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET/POST | `/upload` | Excel upload + analysis |
| GET | `/my` | Student's attendance JSON |
| GET | `/defaulters` | List all defaulters |
| GET | `/simulator` | Simulator page |
| POST | `/api/simulate` | Run what-if calculation |

### Leave  `url_prefix: /leave`
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/apply` | Apply page |
| POST | `/apply` | Submit leave request |
| GET | `/my` | Student's leave history |
| GET | `/pending/mentor` | Mentor approval page |
| PUT | `/mentor/<id>` | Mentor approve/reject |
| GET | `/pending/hod` | HOD approval page |
| PUT | `/hod/<id>` | HOD approve/reject + adjust attendance |

### ML  `url_prefix: /ml`
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/predict` | Prediction page |
| POST | `/predict` | PASS/FAIL prediction |
| POST | `/risk` | Risk classification |
| GET | `/analyze/<student_id>` | Full student ML analysis |
| POST | `/train` | Retrain model (admin) |

### Alerts  `url_prefix: /alert`
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/my` | Student's alerts |
| GET | `/centre` | Notification centre page |
| POST | `/broadcast` | Send to all/defaulters |
| POST | `/send/attendance` | Email defaulters |
| POST | `/send/risk` | ML scan + email risk |
| GET | `/push/vapid-public` | VAPID public key |
| POST | `/push/subscribe` | Register push subscription |
| DELETE | `/push/unsubscribe` | Remove subscription |
| GET | `/logs` | Notification history |

---

## Configuration Reference

All settings in `config.py` — override via `.env`:

```bash
# Required
SECRET_KEY=your-32-char-random-string

# Database (optional - defaults to SQLite)
DATABASE_URL=sqlite:///database.db

# Email
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=your@gmail.com
MAIL_PASSWORD=your-app-password
MAIL_SUPPRESS_SEND=false        # true in development

# PWA Push (generate with: python utils/generate_vapid.py)
VAPID_PUBLIC_KEY=...
VAPID_PRIVATE_KEY=...
VAPID_CLAIMS_EMAIL=admin@cse.edu
```

---

## Deployment

See `DEPLOY.md` for full instructions. Quick options:

**Render (recommended free tier):**
```bash
# Push to GitHub → connect to Render → set env vars → deploy
# Start command: gunicorn wsgi:app
```

**Docker:**
```bash
docker build -t cse-smart .
docker run -p 5000:5000 -e SECRET_KEY=your-secret cse-smart
```

**Local production:**
```bash
gunicorn wsgi:app --workers 2 --bind 0.0.0.0:5000
```

---

## Technology Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Backend | Python 3.11, Flask 3.0 | Web framework |
| ORM | SQLAlchemy + Flask-SQLAlchemy | Database |
| Auth | Flask-Login + Flask-Bcrypt | Authentication |
| ML | Scikit-learn, NumPy, Pandas | Prediction |
| PDF | ReportLab | Report generation |
| Email | Flask-Mail | SMTP notifications |
| Push | pywebpush + VAPID | PWA push |
| Excel | Pandas + openpyxl | Attendance parsing |
| Frontend | Bootstrap 5, Chart.js | UI |
| PWA | Service Worker, Web Push | Offline + install |
| DB | SQLite (dev), PostgreSQL (prod) | Storage |
| Deploy | Gunicorn, Docker, Render | Production |

---

*CSE Department · AI-Driven Smart Academic Management System*
