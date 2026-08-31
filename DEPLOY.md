# Deployment Guide — CSE Smart Academic System

## Local Development

```bash
# 1. Clone / extract the project
cd smart_academic_system

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate      # Linux/Mac
venv\Scripts\activate         # Windows

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment
cp .env.example .env
# Edit .env and set SECRET_KEY to a long random string

# 5. Run
python app.py
# App running at http://localhost:5000
```

---

## Deploy to Render (Free Tier)

1. Push code to a GitHub repository
2. Go to https://render.com → New Web Service
3. Connect your GitHub repo
4. Set the following:
   - **Build command:** `pip install -r requirements.txt`
   - **Start command:** `gunicorn wsgi:app`
   - **Environment:** Python 3.11
5. Add environment variables in Render dashboard:
   - `SECRET_KEY` → generate with: `python -c "import secrets; print(secrets.token_hex(32))"`
   - `FLASK_ENV` → `production`
6. Deploy — Render auto-provisions a URL

---

## Deploy to Railway

```bash
# Install Railway CLI
npm install -g @railway/cli

# Login and deploy
railway login
railway init
railway up
```
Add `SECRET_KEY` in Railway Variables dashboard.

---

## Deploy to PythonAnywhere (Free Tier)

1. Upload project zip via Files tab
2. Extract: `unzip smart_academic_system.zip`
3. Create virtualenv: `mkvirtualenv myenv --python=python3.11`
4. Install: `pip install -r requirements.txt`
5. In Web tab → Add new web app → Manual configuration → Python 3.11
6. Set WSGI file to point to `wsgi.py`
7. Set static files: URL `/static/` → path `smart_academic_system/static/`
8. Reload

---

## Docker Deployment

```dockerfile
# Dockerfile (auto-generated — see below)
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 5000
CMD ["gunicorn", "wsgi:app", "--bind", "0.0.0.0:5000", "--workers", "2"]
```

```bash
docker build -t cse-smart .
docker run -p 5000:5000 -e SECRET_KEY=your-secret cse-smart
```

---

## Production Checklist

- [ ] Set `SECRET_KEY` to a random 32+ char string (never use the default)
- [ ] Set `FLASK_ENV=production`
- [ ] Use PostgreSQL instead of SQLite for multi-user production: set `DATABASE_URL`
- [ ] Enable HTTPS (Render/Railway do this automatically)
- [ ] Set `SESSION_COOKIE_SECURE=True` (auto in ProductionConfig)
- [ ] Create uploads/ directory with write permissions
- [ ] Backup database regularly

---

## Switching to PostgreSQL

1. Install: `pip install psycopg2-binary`
2. Set `DATABASE_URL=postgresql://user:pass@host:5432/dbname`
3. Models work as-is — SQLAlchemy handles the dialect difference

---

## Demo Credentials

| Role    | Username      | Password    |
|---------|--------------|-------------|
| Student | CS21001       | student123  |
| Faculty | FAC001        | faculty123  |
| HOD     | HOD001        | hod123      |
| Admin   | admin         | admin123    |
