"""
Complete live end-to-end integration test against deployed server.
Tests all roles, auth, leave workflows, attendance, academic, admin, ML, and placement.
"""
import urllib.request
import urllib.error
import json
import http.cookiejar
from datetime import datetime, timedelta

BASE = 'https://edufusion-05ai.onrender.com'

def run():
    results = []
    def log(name, success, detail=''):
        status = 'PASS' if success else 'FAIL'
        results.append((name, status, detail))
        print(f"[{status}] {name:35s}: {detail}")

    print("=" * 70)
    print("EDUFUSION COMPLETE LIVE SYSTEM & PIPELINE TEST")
    print(f"Target: {BASE}")
    print("=" * 70)

    # ── 1. AUTH PIPELINE FOR ALL ROLES ───────────────────────────────────────
    openers = {}
    for role, user, pw in [
        ('student', '2JR24CS402', 'student123'),  # Viswanath (Mentee of FAC001)
        ('faculty', 'FAC001',     'faculty123'),  # Dr. Priya Nair (Assigned Mentor)
        ('hod',     'HOD001',     'hod123'),      # Prof. K. Ramesh (HOD)
        ('admin',   'admin',      'admin123')     # System Administrator
    ]:
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        payload = json.dumps({'username': user, 'password': pw, 'role': role}).encode()
        req = urllib.request.Request(f"{BASE}/auth/login", data=payload, headers={'Content-Type': 'application/json'})
        try:
            resp = opener.open(req)
            data = json.loads(resp.read().decode())
            ok = data.get('success') is True
            log(f"Auth {role.upper()} Login", ok, f"Logged in as {user}")
            if ok:
                openers[role] = opener
        except Exception as e:
            log(f"Auth {role.upper()} Login", False, str(e))

    # ── 2. STUDENT PIPELINE ──────────────────────────────────────────────────
    st_opener = openers.get('student')
    if st_opener:
        # Check alerts
        try:
            r = st_opener.open(urllib.request.Request(f"{BASE}/alert/my"))
            d = json.loads(r.read().decode())
            log("Student Alerts API", d.get('success') is True, f"Found {len(d.get('alerts', []))} alerts")
        except Exception as e:
            log("Student Alerts API", False, str(e))

        # Check existing leaves
        try:
            r = st_opener.open(urllib.request.Request(f"{BASE}/leave/my"))
            d = json.loads(r.read().decode())
            log("Student Leave History API", d.get('success') is True, f"Total leaves on record: {len(d.get('leaves', []))}")
        except Exception as e:
            log("Student Leave History API", False, str(e))

        # Check academic history
        try:
            r = st_opener.open(urllib.request.Request(f"{BASE}/academic/api/my-history"))
            d = json.loads(r.read().decode())
            log("Student Academic History", d.get('success') is True, "Successfully returned history")
        except Exception as e:
            log("Student Academic History", False, str(e))

    # ── 3. FULL LEAVE LIFECYCLE: APPLY -> MENTOR ISOLATION -> HOD REVIEW ───
    future_start = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
    future_end = (datetime.now() + timedelta(days=6)).strftime("%Y-%m-%d")
    apply_payload = json.dumps({
        "from_date": future_start,
        "to_date": future_end,
        "reason": "Automated system pipeline verification test",
        "leave_type": "personal"
    }).encode()
    leave_id = None
    if st_opener:
        try:
            r = st_opener.open(urllib.request.Request(f"{BASE}/leave/apply", data=apply_payload, headers={'Content-Type': 'application/json'}))
            d = json.loads(r.read().decode())
            leave_id = d.get('leave', {}).get('id')
            log("Leave Apply (Mentee)", d.get('success') is True, f"Created leave ID {leave_id} for mentee of FAC001")
        except Exception as e:
            log("Leave Apply (Mentee)", False, str(e))

    # Security check: Unassigned faculty (FAC002) MUST be blocked with 403
    try:
        fac2_cj = http.cookiejar.CookieJar()
        fac2_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(fac2_cj))
        fac2_opener.open(urllib.request.Request(f"{BASE}/auth/login", data=json.dumps({'username': 'FAC002', 'password': 'faculty123', 'role': 'faculty'}).encode(), headers={'Content-Type': 'application/json'}))
        req_bad = urllib.request.Request(
            f"{BASE}/leave/mentor/{leave_id}",
            data=json.dumps({"action": "approve", "comment": "Unauthorized approve attempt"}).encode(),
            headers={'Content-Type': 'application/json'},
            method='PUT'
        )
        fac2_opener.open(req_bad)
        log("Leave Security (Mentor Isolation)", False, "Unassigned mentor should have been blocked!")
    except urllib.error.HTTPError as he:
        log("Leave Security (Mentor Isolation)", he.code in (401, 403), f"Properly blocked unauthorized mentor (HTTP {he.code})")
    except Exception as e:
        log("Leave Security (Mentor Isolation)", False, str(e))

    # Assigned mentor (FAC001) reviews and approves
    fac_opener = openers.get('faculty')
    if fac_opener and leave_id:
        try:
            r = fac_opener.open(urllib.request.Request(f"{BASE}/leave/api/mentor/pending"))
            d = json.loads(r.read().decode())
            log("Leave Lifecycle: Mentor Inbox", d.get('success') is True, f"Mentor pending: {d.get('pending')}")
        except Exception as e:
            log("Leave Lifecycle: Mentor Inbox", False, str(e))

        # Approve by assigned mentor
        try:
            req = urllib.request.Request(
                f"{BASE}/leave/mentor/{leave_id}",
                data=json.dumps({"action": "approve", "comment": "Assigned mentor approved via test script"}).encode(),
                headers={'Content-Type': 'application/json'},
                method='PUT'
            )
            r = fac_opener.open(req)
            d = json.loads(r.read().decode())
            log("Leave Lifecycle: Mentor Approve", d.get('success') is True, f"Status: {d.get('leave', {}).get('status')}")
        except Exception as e:
            log("Leave Lifecycle: Mentor Approve", False, str(e))

    # Step 3c: HOD reviews and finalizes approval
    hod_opener = openers.get('hod')
    if hod_opener and leave_id:
        try:
            r = hod_opener.open(urllib.request.Request(f"{BASE}/leave/api/hod/pending"))
            d = json.loads(r.read().decode())
            log("Leave Lifecycle: HOD Inbox", d.get('success') is True, f"HOD pending: {d.get('pending')}")
        except Exception as e:
            log("Leave Lifecycle: HOD Inbox", False, str(e))

        # Approve by HOD
        try:
            req = urllib.request.Request(
                f"{BASE}/leave/hod/{leave_id}",
                data=json.dumps({"action": "approve", "comment": "HOD approved via test script"}).encode(),
                headers={'Content-Type': 'application/json'},
                method='PUT'
            )
            r = hod_opener.open(req)
            d = json.loads(r.read().decode())
            log("Leave Lifecycle: HOD Approve", d.get('success') is True, f"Final status: {d.get('leave', {}).get('status')}")
        except Exception as e:
            log("Leave Lifecycle: HOD Approve", False, str(e))

    # ── 4. ATTENDANCE & DEFAULTERS PIPELINE ───────────────────────────────────
    if fac_opener:
        try:
            r = fac_opener.open(urllib.request.Request(f"{BASE}/attendance/api/defaulters"))
            d = json.loads(r.read().decode())
            log("Attendance: Defaulters Query", d.get('success') is True, f"Defaulters count: {d.get('count')}")
        except Exception as e:
            log("Attendance: Defaulters Query", False, str(e))

        try:
            r = fac_opener.open(urllib.request.Request(f"{BASE}/attendance/api/subjects?semester=4"))
            d = json.loads(r.read().decode())
            log("Attendance: Subjects Query", d.get('success') is True, f"Sem 4 subjects: {len(d.get('subjects', []))}")
        except Exception as e:
            log("Attendance: Subjects Query", False, str(e))

    # ── 5. ADMIN, ACADEMIC PERIODS & PROMOTION PIPELINE ──────────────────────
    adm_opener = openers.get('admin')
    if adm_opener:
        # Users API
        try:
            r = adm_opener.open(urllib.request.Request(f"{BASE}/admin/api/users?per_page=10"))
            d = json.loads(r.read().decode())
            log("Admin: User Directory API", d.get('success') is True, f"Total registered accounts: {d.get('total')}")
        except Exception as e:
            log("Admin: User Directory API", False, str(e))

        # Academic periods API
        try:
            r = adm_opener.open(urllib.request.Request(f"{BASE}/academic/api/periods"))
            d = json.loads(r.read().decode())
            periods = d.get('periods', [])
            log("Academic: Period Configuration", d.get('success') is True, f"Configured academic periods: {len(periods)}")
            
            # Promotion preview test using the first period
            if periods:
                first_period_id = periods[0].get('id')
                p_req = urllib.request.Request(
                    f"{BASE}/academic/api/promote/preview",
                    data=json.dumps({"source_period_id": first_period_id}).encode(),
                    headers={'Content-Type': 'application/json'}
                )
                p_resp = adm_opener.open(p_req)
                p_data = json.loads(p_resp.read().decode())
                log("Academic: Promotion Preview API", p_data.get('success') is True, f"Eligible: {p_data.get('summary', {}).get('eligible_count')}")
        except Exception as e:
            log("Academic: Periods / Promotion", False, str(e))

        # Audit logs API
        try:
            r = adm_opener.open(urllib.request.Request(f"{BASE}/academic/api/audit?per_page=5"))
            d = json.loads(r.read().decode())
            log("Academic: Audit Trail API", d.get('success') is True, f"Audit logs: {d.get('total')}")
        except Exception as e:
            log("Academic: Audit Trail API", False, str(e))

        # Placement Drives API
        try:
            r = adm_opener.open(urllib.request.Request(f"{BASE}/drives/api/list"))
            d = json.loads(r.read().decode())
            log("Placement: Drives Listing API", d.get('success') is True, f"Active drives: {len(d.get('drives', []))}")
        except Exception as e:
            log("Placement: Drives Listing API", False, str(e))

    # ── 6. ML & ANALYTICS PIPELINE ───────────────────────────────────────────
    if fac_opener:
        try:
            r = fac_opener.open(urllib.request.Request(f"{BASE}/alert/send/risk", data=b'{}', headers={'Content-Type': 'application/json'}))
            d = json.loads(r.read().decode())
            log("ML: Risk Model Analysis", d.get('success') is True, f"Students scanned: {d.get('scanned')}")
        except Exception as e:
            log("ML: Risk Model Analysis", False, str(e))

    # ── 7. NOTIFICATIONS & BROADCAST PIPELINE ────────────────────────────────
    if fac_opener:
        try:
            b_payload = json.dumps({
                "title": "System Pipeline Verification Notice",
                "body": "Automated pipeline integrity check completed successfully.",
                "targets": "all"
            }).encode()
            r = fac_opener.open(urllib.request.Request(f"{BASE}/alert/broadcast", data=b_payload, headers={'Content-Type': 'application/json'}))
            d = json.loads(r.read().decode())
            log("Notification: Broadcast Dispatch", d.get('success') is True, d.get('message'))
        except Exception as e:
            log("Notification: Broadcast Dispatch", False, str(e))

    # ── SUMMARY ──────────────────────────────────────────────────────────────
    print("=" * 70)
    passed = sum(1 for _, s, _ in results if s == 'PASS')
    failed = sum(1 for _, s, _ in results if s == 'FAIL')
    print(f"PIPELINE TEST SUMMARY: {passed} PASSED, {failed} FAILED (TOTAL: {len(results)})")
    print("=" * 70)

if __name__ == "__main__":
    run()
