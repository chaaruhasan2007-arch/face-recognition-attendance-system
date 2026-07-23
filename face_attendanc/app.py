from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from apscheduler.schedulers.background import BackgroundScheduler
import cv2, face_recognition, numpy as np
import os, json, base64, pickle
from datetime import datetime, date as date_type
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
app.secret_key = "facetrack_secret_2024_xK9mP"   # change in production

# ── Paths ──────────────────────────────────────────────────────────────────
BASE_DIR       = os.path.dirname(os.path.abspath(__file__))
DATA_DIR       = os.path.join(BASE_DIR, "data")
FACES_DIR      = os.path.join(DATA_DIR, "faces")
ENCODINGS_FILE = os.path.join(DATA_DIR, "encodings.pkl")
STUDENTS_FILE  = os.path.join(DATA_DIR, "students.json")
ATTENDANCE_FILE= os.path.join(DATA_DIR, "attendance.json")
LOG_FILE       = os.path.join(DATA_DIR, "activity_log.json")
CREDS_FILE     = os.path.join(DATA_DIR, "credentials.json")
HOLIDAY_FILE   = os.path.join(DATA_DIR, "holidays.json")
SESSION_FILE   = os.path.join(DATA_DIR, "session_state.json")
ALERTS_FILE    = os.path.join(DATA_DIR, "alerts.json")
PERMISSIONS_FILE = os.path.join(DATA_DIR, "permissions.json")
os.makedirs(FACES_DIR, exist_ok=True)

# ── EMAIL CONFIG (Update with your SMTP details) ──────────────────────────
EMAIL_CONFIG = {
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "sender_email": "dhivyan0812@gmail.com",  # CHANGE THIS
    "sender_password": "vlunnvxifhzadoxa",   # CHANGE THIS (use app-specific password)
    "low_attendance_threshold": 75  # Alert if attendance < 75%
}

# ── Helpers ────────────────────────────────────────────────────────────────
def load_students():
    if os.path.exists(STUDENTS_FILE):
        with open(STUDENTS_FILE) as f: return json.load(f)
    return {}

def save_students(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STUDENTS_FILE, "w") as f: json.dump(d, f, indent=2)

def load_attendance():
    if os.path.exists(ATTENDANCE_FILE):
        with open(ATTENDANCE_FILE) as f: return json.load(f)
    return []

def save_attendance(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ATTENDANCE_FILE, "w") as f: json.dump(d, f, indent=2)

def load_encodings():
    if os.path.exists(ENCODINGS_FILE):
        with open(ENCODINGS_FILE, "rb") as f: return pickle.load(f)
    return {"encodings": [], "ids": []}

def save_encodings(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ENCODINGS_FILE, "wb") as f: pickle.dump(d, f)

def load_credentials():
    if os.path.exists(CREDS_FILE):
        with open(CREDS_FILE) as f: return json.load(f)
    return {
        "teacher": {"username": "teacher", "password": generate_password_hash("teacher123")},
    }

def save_credentials(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CREDS_FILE, "w") as f: json.dump(d, f, indent=2)

def ensure_credentials():
    """Create default credentials file if it doesn't exist."""
    if not os.path.exists(CREDS_FILE):
        save_credentials(load_credentials())

def load_holidays():
    if os.path.exists(HOLIDAY_FILE):
        with open(HOLIDAY_FILE) as f: return json.load(f)
    return []

def save_holidays(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(HOLIDAY_FILE, "w") as f: json.dump(d, f, indent=2)

def load_session_state():
    if os.path.exists(SESSION_FILE):
        with open(SESSION_FILE) as f: return json.load(f)
    return {"active": False}

def save_session_state(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SESSION_FILE, "w") as f: json.dump(d, f)

def load_log():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE) as f: return json.load(f)
    return []

def save_log(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_FILE, "w") as f: json.dump(d, f, indent=2)

def load_alerts():
    """Load email alerts tracking."""
    if os.path.exists(ALERTS_FILE):
        with open(ALERTS_FILE) as f: return json.load(f)
    return {}

def save_alerts(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(ALERTS_FILE, "w") as f: json.dump(d, f, indent=2)

def load_permissions():
    """Load update permissions for students."""
    if os.path.exists(PERMISSIONS_FILE):
        with open(PERMISSIONS_FILE) as f: return json.load(f)
    return {}

def save_permissions(d):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PERMISSIONS_FILE, "w") as f: json.dump(d, f, indent=2)

def log_action(action, details, actor="System"):
    """Append an entry to the activity log."""
    log  = load_log()
    log.append({
        "id":        len(log) + 1,
        "action":    action,
        "details":   details,
        "actor":     actor,
        "timestamp": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
    })
    if len(log) > 500:
        log = log[-500:]
    save_log(log)

def calc_attendance_pct(reg_number, attendance):
    """Calculate attendance percentage for a student."""
    all_dates = sorted(set(a["date"] for a in attendance if a.get("status") == "Present"))
    if not all_dates:
        return 0, 0, 0
    total_days    = len(all_dates)
    present_days  = sum(1 for a in attendance
                        if a.get("reg_number") == reg_number and a.get("status") == "Present")
    pct = round((present_days / total_days) * 100) if total_days else 0
    return pct, present_days, total_days

def send_email(recipient_email, subject, html_body):
    """Send email via SMTP."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_CONFIG["sender_email"]
        msg["To"] = recipient_email
        
        part = MIMEText(html_body, "html")
        msg.attach(part)
        
        server = smtplib.SMTP(EMAIL_CONFIG["smtp_server"], EMAIL_CONFIG["smtp_port"])
        server.starttls()
        server.login(EMAIL_CONFIG["sender_email"], EMAIL_CONFIG["sender_password"])
        server.sendmail(EMAIL_CONFIG["sender_email"], recipient_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False

def check_and_send_low_attendance_alerts():
    """Check for low attendance and send email alerts."""
    students = load_students()
    attendance = load_attendance()
    alerts = load_alerts()
    threshold = EMAIL_CONFIG["low_attendance_threshold"]
    
    for reg_number, student in students.items():
        pct, _, _ = calc_attendance_pct(reg_number, attendance)
        
        # Check if student has email and attendance is below threshold
        if pct < threshold and student.get("email"):
            alert_key = f"{reg_number}_{datetime.now().strftime('%Y-%m-%d')}"
            
            # Only send one alert per day per student
            if alert_key not in alerts:
                email = student.get("email")
                name = f"{student.get('first_name')} {student.get('last_name')}"
                
                html_body = f"""
                <html>
                <head>
                    <style>
                        body {{ font-family: 'Outfit', Arial, sans-serif; color: #1a1f3c; }}
                        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                        .header {{ background: linear-gradient(135deg, #4361ee, #7209b7); color: white; padding: 24px; border-radius: 12px; text-align: center; }}
                        .content {{ background: white; padding: 20px; border: 1px solid #e5e7eb; border-radius: 8px; margin: 20px 0; }}
                        .alert-box {{ background: rgba(239, 68, 68, 0.08); border-left: 4px solid #ef4444; padding: 16px; border-radius: 6px; }}
                        .footer {{ color: #6b7280; font-size: 12px; text-align: center; margin-top: 20px; }}
                        .btn {{ display: inline-block; background: #06d6a0; color: white; padding: 12px 24px; border-radius: 8px; text-decoration: none; margin-top: 16px; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h1>📊 Attendance Alert</h1>
                        </div>
                        <div class="content">
                            <p>Dear <strong>{name}</strong>,</p>
                            <p>This is a notification regarding your attendance record.</p>
                            <div class="alert-box">
                                <strong>⚠️ Your attendance is currently at {pct}%</strong><br>
                                This is below the recommended threshold of {threshold}%.
                            </div>
                            <p>Please improve your attendance to maintain good academic standing. If you have any concerns, please contact your teacher.</p>
                            <a href="http://localhost:5000/my-attendance" class="btn">View Your Attendance</a>
                        </div>
                        <div class="footer">
                            <p>FaceTrack Attendance System | Auto-generated message</p>
                        </div>
                    </div>
                </body>
                </html>
                """
                
                if send_email(email, f"📊 Low Attendance Alert - {pct}%", html_body):
                    alerts[alert_key] = True
                    save_alerts(alerts)
                    log_action("EMAIL", f"Low attendance alert sent to {name} ({pct}%)")

# ── Auth decorators ───────────────────────────────────────────────────────
def teacher_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") != "teacher":
            return redirect(url_for("teacher_login_page"))
        return f(*args, **kwargs)
    return decorated

def student_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get("role") not in ("student", "teacher"):
            return redirect(url_for("student_login_page"))
        return f(*args, **kwargs)
    return decorated

# ── Page routes ────────────────────────────────────────────────────────────
@app.route("/")
def index():
    ensure_credentials()
    return render_template("index.html")

@app.route("/choose-login")
def choose_login(): return render_template("login.html")

@app.route("/teacher-login")
def teacher_login_page(): return render_template("teacher_login.html")

@app.route("/student-login")
def student_login_page(): return render_template("student_login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/menu")
@teacher_required
def menu(): return render_template("menu.html")

@app.route("/register")
@teacher_required
def register_page(): return render_template("register.html")

@app.route("/attendance")
def attendance_page():
    if session.get("role") == "teacher":
        return render_template("attendance.html")
    state = load_session_state()
    if not state.get("active"):
        return render_template("session_closed.html")
    return render_template("attendance.html")

@app.route("/view")
@teacher_required
def view_page(): return render_template("view.html")

@app.route("/students")
@teacher_required
def students_list():
    return render_template("students.html")

@app.route("/student/<reg_number>")
@teacher_required
def student_profile(reg_number):
    return render_template("student.html", reg_number=reg_number)

@app.route("/activity")
@teacher_required
def activity_page():
    return render_template("activity.html")

@app.route("/my-attendance")
@student_required
def student_dashboard():
    reg_number = session.get("reg_number")
    return render_template("student_dashboard.html", reg_number=reg_number)

# ── API: auth login ────────────────────────────────────────────────────────
@app.route("/api/teacher-login", methods=["POST"])
def api_teacher_login():
    data     = request.json
    username = data.get("username", "").strip()
    password = data.get("password", "")
    creds    = load_credentials()
    teacher  = creds.get("teacher", {})
    if username == teacher.get("username") and check_password_hash(teacher.get("password", ""), password):
        session["role"]     = "teacher"
        session["username"] = username
        return jsonify({"success": True, "redirect": "/menu"})
    return jsonify({"success": False, "message": "Invalid credentials."})

@app.route("/api/student-login", methods=["POST"])
def api_student_login():
    data      = request.json
    reg_num   = data.get("reg_number", "").strip().upper()
    password  = data.get("password", "")
    students  = load_students()
    
    if reg_num not in students:
        return jsonify({"success": False, "message": "Register number not found."})
    
    creds = load_credentials()
    student_creds = creds.get("students", {}).get(reg_num)
    
    # Default password is register number
    if not student_creds:
        if password != reg_num:
            return jsonify({"success": False, "message": "Password is incorrect."})
    else:
        if not check_password_hash(student_creds.get("password", ""), password):
            return jsonify({"success": False, "message": "Password is incorrect."})
    
    session["role"]      = "student"
    session["reg_number"] = reg_num
    session["student_name"] = students[reg_num]["first_name"]
    return jsonify({"success": True, "redirect": "/my-attendance"})

# ── API: register student with face ────────────────────────────────────────
@app.route("/api/register", methods=["POST"])
@teacher_required
def api_register():
    data = request.json
    first_name = data.get("first_name", "").strip()
    last_name = data.get("last_name", "").strip()
    reg_number = data.get("reg_number", "").strip().upper()
    email = data.get("email", "").strip()  # NEW: email field
    image_b64 = data.get("image", "")
    
    if not all([first_name, last_name, reg_number, image_b64]):
        return jsonify({"success": False, "message": "All fields including photo are required."})
    
    if not email:  # NEW: email validation
        return jsonify({"success": False, "message": "Email is required."})
    
    students = load_students()
    if reg_number in students:
        return jsonify({"success": False, "message": f"Register number {reg_number} already registered."})
    
    # Check for duplicate by name
    for s in students.values():
        if s["first_name"].lower() == first_name.lower() and s["last_name"].lower() == last_name.lower():
            return jsonify({"success": False, "message": "A student with this name already exists."})
    
    try:
        image_data = base64.b64decode(image_b64.split(",")[1] if "," in image_b64 else image_b64)
        image_array = np.frombuffer(image_data, dtype=np.uint8)
        frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        
        if frame is None:
            return jsonify({"success": False, "message": "Invalid image. Please try again."})
        
        face_encodings = face_recognition.face_encodings(frame)
        if not face_encodings:
            return jsonify({"success": False, "message": "No face detected. Please try again."})
        
        face_encoding = face_encodings[0]
        
        # Check for duplicate by face
        encodings_data = load_encodings()
        for existing_id, existing_encoding in zip(encodings_data["ids"], encodings_data["encodings"]):
            dist = np.linalg.norm(np.array(existing_encoding) - face_encoding)
            if dist < 0.5:  # Similar face
                existing_student = students.get(existing_id, {})
                return jsonify({"success": False, 
                    "message": f"Face matches existing student: {existing_student.get('first_name')} {existing_student.get('last_name')}"})
        
        # Save face image
        face_filename = f"{reg_number}.jpg"
        face_path = os.path.join(FACES_DIR, face_filename)
        cv2.imwrite(face_path, frame)
        
        # Save encoding
        encodings_data["encodings"].append(face_encoding.tolist())
        encodings_data["ids"].append(reg_number)
        save_encodings(encodings_data)
        
        # Save student data with email
        students[reg_number] = {
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "registered_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        save_students(students)
        
        # Create default password (register number)
        creds = load_credentials()
        if "students" not in creds:
            creds["students"] = {}
        creds["students"][reg_number] = {"password": generate_password_hash(reg_number)}
        save_credentials(creds)
        
        log_action("REGISTER", f"{first_name} {last_name} (ID: {reg_number}, Email: {email}) registered")
        
        return jsonify({"success": True, "message": f"✅ {first_name} registered successfully!"})
    
    except Exception as e:
        return jsonify({"success": False, "message": f"Registration failed: {str(e)}"})

# ── API: get student data ──────────────────────────────────────────────────
@app.route("/api/student-data/<reg_number>")
@teacher_required
def api_get_student_data(reg_number):
    students = load_students()
    attendance = load_attendance()
    permissions = load_permissions()

    reg = (reg_number or "").strip().upper()
    if reg not in students:
        return jsonify({"success": False, "message": "Student not found."}), 404

    student = students[reg]
    pct, present, total = calc_attendance_pct(reg, attendance)
    history = [
        a for a in attendance
        if (a.get("reg_number") or "").strip().upper() == reg
    ]
    history.sort(key=lambda x: (x.get("date", ""), x.get("time", "")), reverse=True)

    return jsonify({
        "success": True,
        "student": {
            "reg_number": reg,
            "first_name": student.get("first_name", ""),
            "last_name": student.get("last_name", ""),
            "email": student.get("email", ""),
            "registered_at": student.get("registered_at", "")
        },
        "stats": {
            "percentage": pct,
            "present": present,
            "total": total,
            "absent": max(total - present, 0)
        },
        "history": history,
        "can_update": permissions.get(reg, False)
    })

# ── API: allow student to update details ───────────────────────────────────
@app.route("/api/student-update-permission/<reg_number>", methods=["POST"])
@teacher_required
def api_allow_student_update(reg_number):
    students = load_students()
    if reg_number not in students:
        return jsonify({"success": False, "message": "Student not found."})
    
    permissions = load_permissions()
    permissions[reg_number] = True
    save_permissions(permissions)
    
    log_action("UPDATE", f"Student {reg_number} permission granted to update profile")
    
    return jsonify({"success": True, "message": f"Update permission granted to {students[reg_number]['first_name']}"})

# ── API: deny student update ───────────────────────────────────────────────
@app.route("/api/student-update-permission/<reg_number>/revoke", methods=["POST"])
@teacher_required
def api_revoke_student_update(reg_number):
    permissions = load_permissions()
    if reg_number in permissions:
        permissions[reg_number] = False
    save_permissions(permissions)
    
    students = load_students()
    log_action("UPDATE", f"Student {reg_number} update permission revoked")
    
    return jsonify({"success": True, "message": f"Update permission revoked from {students.get(reg_number, {}).get('first_name')}"})

# ── API: student updates own details ───────────────────────────────────────
@app.route("/api/student/update-profile", methods=["POST"])
@student_required
def api_student_update_profile():
    reg_number = session.get("reg_number")
    permissions = load_permissions()
    
    if not permissions.get(reg_number, False):
        return jsonify({"success": False, "message": "Teacher has not allowed profile updates yet."})
    
    data = request.json
    email = data.get("email", "").strip()
    
    if not email:
        return jsonify({"success": False, "message": "Email is required."})
    
    students = load_students()
    if reg_number not in students:
        return jsonify({"success": False, "message": "Student not found."})
    
    students[reg_number]["email"] = email
    save_students(students)
    
    log_action("UPDATE", f"Student {reg_number} updated their email to {email}")
    
    return jsonify({"success": True, "message": "Profile updated successfully!"})

# ── API: check if student can update ───────────────────────────────────────
@app.route("/api/student/check-update-permission")
@student_required
def api_check_update_permission():
    reg_number = session.get("reg_number")
    permissions = load_permissions()
    students = load_students()
    student = students.get(reg_number, {})
    
    return jsonify({
        "success": True,
        "can_update": permissions.get(reg_number, False),
        "current_email": student.get("email", "")
    })

@app.route("/api/students")
@teacher_required
def api_students():
    students = load_students()
    attendance = load_attendance()

    result = []
    for reg_number, stu in students.items():
        pct, present, total = calc_attendance_pct(reg_number, attendance)
        result.append({
            "reg_number": reg_number,
            "first_name": stu.get("first_name", ""),
            "last_name": stu.get("last_name", ""),
            "email": stu.get("email", ""),
            "registered_at": stu.get("registered_at", ""),
            "attendance_pct": pct,
            "present_days": present,
            "total_days": total
        })

    result.sort(key=lambda s: ((s.get("first_name") or "").lower(), (s.get("reg_number") or "")))

    return jsonify({
        "success": True,
        "students": result
    })



@app.route("/api/email-alerts/manual", methods=["POST"])
@teacher_required
def api_manual_email_alerts():
    """Manually send low-attendance emails for selected threshold."""
    data = request.get_json(silent=True) or {}
    try:
        threshold = int(data.get("threshold", 75))
    except (TypeError, ValueError):
        return jsonify({"success": False, "message": "Invalid threshold."}), 400

    if threshold not in (25, 50, 75):
        return jsonify({"success": False, "message": "Threshold must be 25, 50, or 75."}), 400

    students = load_students()
    attendance = load_attendance()

    sent = 0
    failed = 0
    skipped_no_email = 0
    matched = 0
    details = []

    for reg_number, student in students.items():
        pct, present_days, total_days = calc_attendance_pct(reg_number, attendance)

        # Only alert students who have attendance records and are below selected threshold
        if total_days <= 0 or pct >= threshold:
            continue

        matched += 1
        email = (student.get("email") or "").strip()
        name = f"{student.get('first_name', '')} {student.get('last_name', '')}".strip() or reg_number

        if not email:
            skipped_no_email += 1
            details.append({"reg_number": reg_number, "name": name, "status": "skipped", "reason": "No email"})
            continue

        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; color:#1a1f3c;">
          <div style="max-width:600px;margin:auto;padding:20px;">
            <div style="background:linear-gradient(135deg,#4361ee,#7209b7);color:white;padding:22px;border-radius:12px;text-align:center;">
              <h2 style="margin:0;">Attendance Alert</h2>
            </div>
            <div style="border:1px solid #e5e7eb;border-radius:10px;padding:20px;margin-top:18px;">
              <p>Dear <b>{name}</b>,</p>
              <p>Your current attendance is <b>{pct}%</b>.</p>
              <p>This is below the selected alert threshold of <b>{threshold}%</b>.</p>
              <p>Please improve your attendance and contact your teacher if you need help.</p>
              <p style="font-size:13px;color:#6b7280;">Present days: {present_days} / Total days: {total_days}</p>
            </div>
            <p style="font-size:12px;color:#6b7280;text-align:center;">FaceTrack Attendance System</p>
          </div>
        </body>
        </html>
        """

        ok = send_email(email, f"Attendance Alert - Below {threshold}%", html_body)
        if ok:
            sent += 1
            details.append({"reg_number": reg_number, "name": name, "email": email, "attendance_pct": pct, "status": "sent"})
        else:
            failed += 1
            details.append({"reg_number": reg_number, "name": name, "email": email, "attendance_pct": pct, "status": "failed"})

    log_action("EMAIL", f"Manual email alert: threshold {threshold}%, matched {matched}, sent {sent}, failed {failed}, skipped {skipped_no_email}", actor="Teacher")

    return jsonify({
        "success": True,
        "threshold": threshold,
        "matched": matched,
        "sent": sent,
        "failed": failed,
        "skipped_no_email": skipped_no_email,
        "message": f"Email alert completed. Sent: {sent}, Failed: {failed}, No email: {skipped_no_email}.",
        "details": details[:50]
    })

# ── API: get stats ─────────────────────────────────────────────────────────
@app.route("/api/stats")
def api_stats():
    students = load_students()
    attendance = load_attendance()
    today = datetime.now().strftime("%Y-%m-%d")
    present_today = sum(1 for a in attendance if a.get("date") == today and a.get("status") == "Present")
    
    return jsonify({
        "registered": len(students),
        "present_today": present_today
    })

# ── API: liveness detection ────────────────────────────────────────────────
@app.route("/api/liveness", methods=["POST"])
def api_liveness():
    data = request.json
    frame1_b64 = data.get("frame1", "")
    frame2_b64 = data.get("frame2", "")
    
    try:
        def decode_frame(b64):
            if "," in b64:
                b64 = b64.split(",")[1]
            image_data = base64.b64decode(b64)
            image_array = np.frombuffer(image_data, dtype=np.uint8)
            return cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        
        frame1 = decode_frame(frame1_b64)
        frame2 = decode_frame(frame2_b64)
        
        if frame1 is None or frame2 is None:
            return jsonify({"live": False, "message": "Invalid frame"})
        
        # Simple liveness: if eyes are detected in both frames and there's motion
        detector = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye.xml')
        eyes1 = detector.detectMultiScale(frame1, 1.3, 5)
        eyes2 = detector.detectMultiScale(frame2, 1.3, 5)
        
        if len(eyes1) > 0 and len(eyes2) > 0:
            return jsonify({"live": True, "message": "Liveness confirmed"})
        
        return jsonify({"live": False, "message": "Unable to confirm liveness"})
    except:
        return jsonify({"live": False, "message": "Liveness check failed"})



# ── API: recognize faces live for attendance.html ─────────────────────────
@app.route("/api/recognize", methods=["POST"])
def api_recognize():
    """Receives camera frame from attendance.html and returns face boxes + student details."""
    try:
        data = request.get_json(silent=True) or {}
        image_b64 = data.get("image", "")

        if not image_b64:
            return jsonify({"success": False, "message": "No image received", "faces": []})

        # Decode base64 image
        if "," in image_b64:
            image_b64 = image_b64.split(",", 1)[1]
        image_data = base64.b64decode(image_b64)
        image_array = np.frombuffer(image_data, dtype=np.uint8)
        frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)

        if frame is None:
            return jsonify({"success": False, "message": "Invalid image", "faces": []})

        encodings_data = load_encodings()
        students = load_students()

        if not encodings_data.get("encodings") or not encodings_data.get("ids"):
            return jsonify({"success": True, "faces": [], "message": "No registered faces found"})

        # Detect + encode faces
        face_locations = face_recognition.face_locations(frame)
        face_encodings = face_recognition.face_encodings(frame, face_locations)

        faces_result = []
        known_encodings = [np.array(e) for e in encodings_data.get("encodings", [])]
        known_ids = encodings_data.get("ids", [])

        for (top, right, bottom, left), face_encoding in zip(face_locations, face_encodings):
            name = "Unknown"
            reg_number = None
            best_distance = 0.50  # lower = stricter, 0.50 is safer than 0.60

            for student_id, known_encoding in zip(known_ids, known_encodings):
                dist = np.linalg.norm(known_encoding - face_encoding)
                if dist < best_distance:
                    best_distance = dist
                    reg_number = student_id

            if reg_number and reg_number in students:
                stu = students[reg_number]
                name = f"{stu.get('first_name', '')} {stu.get('last_name', '')}".strip() or reg_number
            else:
                reg_number = None

            faces_result.append({
                "name": name,
                "reg_number": reg_number,
                "box": {
                    "top": int(top),
                    "right": int(right),
                    "bottom": int(bottom),
                    "left": int(left)
                }
            })

        return jsonify({"success": True, "faces": faces_result})

    except Exception as e:
        print("/api/recognize error:", e)
        return jsonify({"success": False, "message": str(e), "faces": []})


# ── API: mark attendance by register number for attendance.html ───────────
@app.route("/api/mark_attendance", methods=["POST"])
def api_mark_attendance_by_reg_number():
    """Marks attendance using reg_number. This route matches attendance.html fetch('/api/mark_attendance')."""
    try:
        state = load_session_state()
        if not state.get("active") and session.get("role") != "teacher":
            return jsonify({"success": False, "message": "Attendance session not active."})

        data = request.get_json(silent=True) or {}
        reg_number = (data.get("reg_number") or "").strip().upper()

        if not reg_number:
            return jsonify({"success": False, "message": "reg_number missing"})

        students = load_students()
        if reg_number not in students:
            return jsonify({"success": False, "message": "Student not found"})

        student = students[reg_number]
        attendance = load_attendance()
        today = datetime.now().strftime("%Y-%m-%d")
        current_time = datetime.now().strftime("%I:%M %p")

        existing = next((a for a in attendance
                         if (a.get("reg_number") or "").strip().upper() == reg_number
                         and a.get("date") == today
                         and a.get("status") == "Present"), None)
        if existing:
            return jsonify({
                "success": False,
                "already_marked": True,
                "message": f"Already marked present: {student.get('first_name', reg_number)}"
            })

        attendance.append({
            "reg_number": reg_number,
            "first_name": student.get("first_name", ""),
            "last_name": student.get("last_name", ""),
            "date": today,
            "time": current_time,
            "status": "Present"
        })
        save_attendance(attendance)

        full_name = f"{student.get('first_name', '')} {student.get('last_name', '')}".strip() or reg_number
        log_action("ATTENDANCE", f"{full_name} marked present at {current_time}")

        return jsonify({
            "success": True,
            "message": f"✅ {full_name} marked present",
            "name": full_name,
            "reg_number": reg_number,
            "time": current_time
        })

    except Exception as e:
        print("/api/mark_attendance error:", e)
        return jsonify({"success": False, "message": str(e)})

# ── API: mark attendance ───────────────────────────────────────────────────
@app.route("/api/mark-attendance", methods=["POST"])
def api_mark_attendance():
    state = load_session_state()
    if not state.get("active") and session.get("role") != "teacher":
        return jsonify({"success": False, "message": "Attendance session not active."})
    
    data = request.json
    image_b64 = data.get("image", "")
    
    try:
        image_data = base64.b64decode(image_b64.split(",")[1] if "," in image_b64 else image_b64)
        image_array = np.frombuffer(image_data, dtype=np.uint8)
        frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        
        if frame is None:
            return jsonify({"success": False, "message": "Invalid image"})
        
        face_encodings = face_recognition.face_encodings(frame)
        if not face_encodings:
            return jsonify({"success": False, "message": "No face detected"})
        
        face_encoding = face_encodings[0]
        encodings_data = load_encodings()
        
        best_match_distance = 0.6
        best_match_id = None
        
        for existing_id, existing_encoding in zip(encodings_data["ids"], encodings_data["encodings"]):
            dist = np.linalg.norm(np.array(existing_encoding) - face_encoding)
            if dist < best_match_distance:
                best_match_distance = dist
                best_match_id = existing_id
        
        if not best_match_id:
            return jsonify({"success": False, "message": "Face not recognized. Please register first."})
        
        students = load_students()
        student = students[best_match_id]
        
        attendance = load_attendance()
        today = datetime.now().strftime("%Y-%m-%d")
        current_time = datetime.now().strftime("%I:%M %p")
        
        existing = next((a for a in attendance if a.get("reg_number") == best_match_id and a.get("date") == today), None)
        if existing:
            return jsonify({"success": False, "message": f"Already marked present: {student['first_name']}"})
        
        attendance.append({
            "reg_number": best_match_id,
            "first_name": student["first_name"],
            "last_name": student["last_name"],
            "date": today,
            "time": current_time,
            "status": "Present"
        })
        save_attendance(attendance)
        
        log_action("ATTENDANCE", f"{student['first_name']} {student['last_name']} marked present at {current_time}")
        
        return jsonify({
            "success": True,
            "message": f"✅ {student['first_name']} {student['last_name']} marked present",
            "name": f"{student['first_name']} {student['last_name']}"
        })
    
    except Exception as e:
        return jsonify({"success": False, "message": f"Error: {str(e)}"})

# ── API: get attendance history ────────────────────────────────────────────
@app.route("/api/attendance/<reg_number>")
@student_required
def api_get_attendance(reg_number):
    attendance = load_attendance()
    students = load_students()
    
    if reg_number not in students:
        return jsonify({"success": False, "message": "Student not found"})
    
    student_records = [a for a in attendance if a.get("reg_number") == reg_number]
    pct, present, total = calc_attendance_pct(reg_number, attendance)
    
    return jsonify({
        "success": True,
        "student": students[reg_number],
        "attendance": student_records,
        "percentage": pct,
        "present_days": present,
        "total_days": total
    })

@app.route("/api/attendance")
@teacher_required
def api_all_attendance():
    attendance = load_attendance()
    students = load_students()

    date_filter = request.args.get("date", "").strip()
    q = request.args.get("q", "").strip().lower()
    status_filter = request.args.get("status", "").strip().lower()

    enriched = []
    for rec in attendance:
        reg = (rec.get("reg_number") or "").strip().upper()
        stu = students.get(reg, {})
        row = {
            "reg_number": reg,
            "first_name": rec.get("first_name") or stu.get("first_name", ""),
            "last_name": rec.get("last_name") or stu.get("last_name", ""),
            "date": rec.get("date", ""),
            "status": rec.get("status", ""),
            "time": rec.get("time", "")
        }
        enriched.append(row)

    if date_filter:
        enriched = [r for r in enriched if (r.get("date") or "") == date_filter]

    if status_filter and status_filter != "all":
        enriched = [r for r in enriched if (r.get("status") or "").lower() == status_filter]

    if q:
        enriched = [
            r for r in enriched
            if q in (r.get("first_name") or "").lower()
            or q in (r.get("last_name") or "").lower()
            or q in (r.get("reg_number") or "").lower()
            or q in (r.get("date") or "").lower()
        ]

    enriched.sort(key=lambda r: ((r.get("date") or ""), (r.get("first_name") or "").lower(), (r.get("reg_number") or "")), reverse=True)

    return jsonify({
        "success": True,
        "attendance": enriched,
        "total": len(enriched)
    })

@app.route("/api/attendance/override", methods=["POST"])
@teacher_required
def api_attendance_override():
    data = request.get_json(silent=True) or {}
    reg_number = (data.get("reg_number") or "").strip().upper()
    date_value = (data.get("date") or "").strip()
    status = (data.get("status") or "Present").strip().title()

    if not reg_number or not date_value:
        return jsonify({"success": False, "message": "Register number and date are required."}), 400

    if status not in ("Present", "Absent"):
        return jsonify({"success": False, "message": "Status must be Present or Absent."}), 400

    students = load_students()
    if reg_number not in students:
        return jsonify({"success": False, "message": "Student not found."}), 404

    attendance = load_attendance()
    student = students[reg_number]
    existing = next((a for a in attendance if (a.get("reg_number") == reg_number and a.get("date") == date_value)), None)
    current_time = datetime.now().strftime("%I:%M %p")

    if existing:
        existing["first_name"] = student.get("first_name", existing.get("first_name", ""))
        existing["last_name"] = student.get("last_name", existing.get("last_name", ""))
        existing["status"] = status
        existing["time"] = current_time
        message = f"Attendance updated to {status} for {student.get('first_name', reg_number)}."
    else:
        attendance.append({
            "reg_number": reg_number,
            "first_name": student.get("first_name", ""),
            "last_name": student.get("last_name", ""),
            "date": date_value,
            "time": current_time,
            "status": status
        })
        message = f"Attendance added as {status} for {student.get('first_name', reg_number)}."

    save_attendance(attendance)
    log_action("OVERRIDE", f"{status} set for {student.get('first_name', '')} {student.get('last_name', '')} ({reg_number}) on {date_value}", actor=session.get("username", "Teacher"))
    return jsonify({"success": True, "message": message})

@app.route("/api/attendance/clear", methods=["POST"])
@teacher_required
def api_attendance_clear():
    save_attendance([])
    log_action("CLEAR", "All attendance records cleared", actor=session.get("username", "Teacher"))
    return jsonify({"success": True, "message": "All attendance records cleared."})

@app.route("/api/student_photo/<reg_number>")
def api_student_photo(reg_number):
    from flask import send_file

    reg_number = (reg_number or "").strip().upper()
    photo_path = os.path.join(FACES_DIR, f"{reg_number}.jpg")
    if os.path.exists(photo_path):
        return send_file(photo_path, mimetype="image/jpeg")

    blank_gif = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")
    return app.response_class(blank_gif, mimetype="image/gif")


# ── API: activity log ──────────────────────────────────────────────────────
@app.route("/api/activity")
def api_activity():
    log    = load_log()
    q      = request.args.get("q", "").lower()
    action = request.args.get("action", "")
    limit  = int(request.args.get("limit", 100))

    result = list(reversed(log))
    result = [l for l in result if not (
        "Teacher logged in" in l.get("details", "") or
        "Teacher password changed" in l.get("details", "")
    )]
    if action:
        result = [l for l in result if l.get("action") == action]
    if q:
        result = [l for l in result if q in l.get("details","").lower()
                                    or q in l.get("action","").lower()]
    return jsonify({"success": True, "log": result[:limit], "total": len(result)})

@app.route("/api/activity/clear", methods=["POST"])
@teacher_required
def api_clear_activity():
    save_log([])
    return jsonify({"success": True})

# ── API: student changes own password ──────────────────────────────────────
@app.route("/api/student/change-password", methods=["POST"])
def api_change_student_password():
    if session.get("role") != "student":
        return jsonify({"success": False, "message": "Unauthorized."})
    reg_number   = session.get("reg_number")
    data         = request.json
    current_pass = data.get("current_password", "")
    new_pass     = data.get("new_password", "")
    confirm_pass = data.get("confirm_password", "")

    if not current_pass or not new_pass or not confirm_pass:
        return jsonify({"success": False, "message": "All fields are required."})
    if new_pass != confirm_pass:
        return jsonify({"success": False, "message": "New passwords do not match."})
    if len(new_pass) < 6:
        return jsonify({"success": False, "message": "Password must be at least 6 characters."})
    if new_pass == reg_number:
        return jsonify({"success": False, "message": "New password cannot be the same as your register number."})

    creds = load_credentials()
    student_creds = creds.get("students", {}).get(reg_number)

    if not student_creds:
        if current_pass != reg_number:
            return jsonify({"success": False, "message": "Current password is incorrect."})
    else:
        if not check_password_hash(student_creds.get("password", ""), current_pass):
            return jsonify({"success": False, "message": "Current password is incorrect."})

    if "students" not in creds:
        creds["students"] = {}
    creds["students"][reg_number] = {"password": generate_password_hash(new_pass)}
    save_credentials(creds)
    return jsonify({"success": True, "message": "Password changed successfully!"})

# ── API: today's attendance (public — used by attendance.html on load) ─────
@app.route("/api/attendance-today")
def api_attendance_today():
    """Returns today's Present records. No auth required so attendance.html
    can sync already-marked students even when the user is not logged in."""
    attendance = load_attendance()
    today = datetime.now().strftime("%Y-%m-%d")
    present = [
        {
            "reg_number": a.get("reg_number", ""),
            "first_name": a.get("first_name", ""),
            "last_name":  a.get("last_name", ""),
            "time":       a.get("time", ""),
            "status":     a.get("status", "")
        }
        for a in attendance
        if a.get("date") == today and a.get("status") == "Present"
    ]
    return jsonify({"success": True, "attendance": present})

# ── API: session state ─────────────────────────────────────────────────────
@app.route("/api/session-state")
def api_get_session_state():
    return jsonify({"success": True, "active": load_session_state().get("active", False)})

@app.route("/api/session-state/on", methods=["POST"])
@teacher_required
def api_session_on():
    save_session_state({"active": True})
    log_action("UPDATE", "Teacher opened attendance session")
    return jsonify({"success": True, "message": "Attendance session opened."})

@app.route("/api/session-state/off", methods=["POST"])
@teacher_required
def api_session_off():
    save_session_state({"active": False})
    log_action("UPDATE", "Teacher closed attendance session")
    return jsonify({"success": True, "message": "Attendance session closed."})

# ── Auto-mark absent ───────────────────────────────────────────────────────
def auto_mark_absent(target_date=None):
    """Mark all students who have no attendance record as Absent for target_date."""
    if target_date is None:
        target_date = datetime.now().strftime("%Y-%m-%d")

    students   = load_students()
    attendance = load_attendance()
    holidays   = load_holidays()

    if not students:
        return 0

    if target_date in holidays:
        return 0

    existing = {a["reg_number"] for a in attendance if a.get("date") == target_date}

    count = 0
    for reg_num, stu in students.items():
        if reg_num not in existing:
            attendance.append({
                "reg_number": reg_num,
                "first_name": stu["first_name"],
                "last_name":  stu["last_name"],
                "date":   target_date,
                "time":   "—",
                "status": "Absent"
            })
            count += 1

    if count > 0:
        save_attendance(attendance)
        log_action("ATTENDANCE", f"Auto-marked {count} student(s) absent for {target_date}")
        # Check for low attendance and send alerts
        check_and_send_low_attendance_alerts()

    return count

# ── API: manually trigger auto-absent ──────────────────────────────────────
@app.route("/api/auto-absent", methods=["POST"])
@teacher_required
def api_auto_absent():
    data        = request.json or {}
    target_date = data.get("date", datetime.now().strftime("%Y-%m-%d"))
    count       = auto_mark_absent(target_date)
    return jsonify({"success": True,
                    "message": f"Marked {count} student(s) absent for {target_date}.",
                    "count": count})

# ── API: holiday management ────────────────────────────────────────────────
@app.route("/api/holidays", methods=["GET"])
def api_get_holidays():
    return jsonify({"success": True, "holidays": load_holidays()})

@app.route("/api/holidays/add", methods=["POST"])
@teacher_required
def api_add_holiday():
    data        = request.json
    target_date = data.get("date", "").strip()
    reason      = data.get("reason", "Holiday").strip()
    if not target_date:
        return jsonify({"success": False, "message": "Date required."})

    holidays = load_holidays()
    if isinstance(holidays, list):
        holidays = {d: "Holiday" for d in holidays}

    holidays[target_date] = reason

    students   = load_students()
    attendance = load_attendance()
    attendance = [a for a in attendance if a.get("date") != target_date]
    for reg_num, stu in students.items():
        attendance.append({
            "reg_number": reg_num,
            "first_name": stu["first_name"],
            "last_name":  stu["last_name"],
            "date":   target_date,
            "time":   "—",
            "status": "Holiday"
        })

    save_attendance(attendance)
    save_holidays(holidays)
    log_action("UPDATE", f"Holiday declared: {target_date} — {reason}")
    return jsonify({"success": True,
                    "message": f"Holiday set for {target_date}. All {len(students)} students marked."})

@app.route("/api/holidays/remove", methods=["POST"])
@teacher_required
def api_remove_holiday():
    data        = request.json
    target_date = data.get("date", "").strip()
    if not target_date:
        return jsonify({"success": False, "message": "Date required."})

    holidays = load_holidays()
    if isinstance(holidays, dict) and target_date in holidays:
        del holidays[target_date]
    elif isinstance(holidays, list) and target_date in holidays:
        holidays.remove(target_date)
    save_holidays(holidays)

    attendance = load_attendance()
    attendance = [a for a in attendance
                  if not (a.get("date") == target_date and a.get("status") == "Holiday")]
    save_attendance(attendance)
    log_action("UPDATE", f"Holiday removed for {target_date}")
    return jsonify({"success": True, "message": f"Holiday removed for {target_date}."})

# ── APScheduler: auto-absent at midnight & check alerts ────────────────────
scheduler = BackgroundScheduler(daemon=True)
scheduler.add_job(auto_mark_absent, 'cron', hour=23, minute=59,
                  id='auto_absent_job', replace_existing=True)
scheduler.add_job(check_and_send_low_attendance_alerts, 'cron', hour=12, minute=0,
                  id='alert_job', replace_existing=True)
scheduler.start()



if __name__ == "__main__":
    app.run(debug=True, port=5000)