import os, re, zlib, random, secrets
import pymysql, pymysql.cursors
from html.parser import HTMLParser
from functools import wraps
from datetime import date
from flask import (Flask, g, render_template, request, redirect, url_for,
                   session, flash, send_from_directory, abort)
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB_CFG = dict(host=os.environ.get("DB_HOST", "localhost"), user=os.environ.get("DB_USER", "wladimir"),
              password=os.environ.get("DB_PASSWORD", "StrongPass123!"), database=os.environ.get("DB_NAME", "wladimir_portal"))
UP = os.path.join(BASE, "uploads")
os.makedirs(UP, exist_ok=True)
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

# name: (title, columns, per_student)
T = {
 "course_registration": ("Course Registration", ["course_code","course_title","credits","semester","status"], 1),
 "programme_structure": ("Programme Structure", ["programme","year","semester","course_code","course_title","credits","type"], 0),
 "registration_history": ("Registration Status / History", ["academic_year","semester","reg_date","status"], 1),
 "programme_transfer": ("Programme Transferred", ["from_programme","to_programme","reason","status"], 1),
 "postponed_study": ("Postponed Studies", ["academic_year","reason","from_date","to_date","status"], 1),
 "exam_registration": ("Exam Registration", ["course_code","course_title","academic_year","status"], 1),
 "exam_timetable": ("Exam Time Table", ["course_code","course_title","exam_date","exam_time","venue"], 0),
 "exam_results": ("Exam Results", ["course_code","course_title","marks"], 1),
 "supp_registration": ("Supplementary Registration", ["course_code","course_title","academic_year","status"], 1),
 "supp_timetable": ("Supplementary Time Table", ["course_code","course_title","exam_date","exam_time","venue"], 0),
 "course_evaluation": ("Course Evaluation", ["course_code","lecturer","rating","comment"], 1),
 "logbook": ("Logbook", ["log_date","week","activity","supervisor_comment","status"], 1),
 "placements": ("My Placements", ["organization","district","year_of_study","supervisor_name","supervisor_phone","supervisor_email","arrived"], 1),
 "hostels": ("Available Hostels", ["name","gender","location","fee"], 0),
 "rooms": ("Rooms", ["hostel","room_no","capacity","status"], 0),
 "bookings": ("Booked Hostels", ["hostel","room_no","academic_year","status"], 1),
 "booking_payments": ("Booking History / Payment", ["hostel","room_no","amount","control_number","status"], 1),
 "nhif": ("NHIF Payments", ["control_number","amount","status","created"], 1),
 "payment_status": ("Payment Status", ["description","control_number","amount","status"], 1),
}
ST = lambda ks: [(k, T[k][0]) for k in ks]
MENU = {
 "ACADEMIC APPS": [
  ("Registration","&#127891;",ST(["course_registration","programme_structure","registration_history","programme_transfer","postponed_study"]),False),
  ("Academic Records","&#128203;",ST(["exam_registration","exam_timetable","exam_results","supp_registration","supp_timetable","course_evaluation"]),False),
  ("Field & Project","&#128279;",[("placements","My Placements")],False)],
 "OTHER APPS": [
  ("Accommodation","&#128716;",ST(["hostels","rooms","bookings","booking_payments"]),False),
  ("NHIF","&#127973;",[("nhif","NHIF")],True),
  ("Payment Status","&#128179;",[("payment_status","Payment Status")],True),
  ("Students' Guide","&#128214;",[("guide","Students' Guide")],True)],
}
STUDENT_RW = {"placements"}
MIN_FEE = float(os.environ.get("MIN_FEE", 500000))   # minimum tuition paid before exam registration (TZS)
NHIF_FEE = float(os.environ.get("NHIF_FEE", 50400))  # NHIF yearly amount (TZS)
PASS_MARK = 40
NUM = {"credits","marks","amount","fee","capacity","rating","year","week"}

def grade(m):
    try: m = float(m)
    except (TypeError, ValueError): return "-"
    return "D" if m < 40 else "C" if m < 50 else "B" if m < 60 else "B+" if m < 70 else "A"

class Row(dict):
    def __getitem__(self, k):
        return list(self.values())[k] if isinstance(k, int) else super().__getitem__(k)

class Cur:
    def __init__(self, c): self.c = c
    def fetchone(self):
        r = self.c.fetchone(); return Row(r) if r else None
    def fetchall(self): return [Row(r) for r in self.c.fetchall()]
    def __iter__(self): return iter(self.fetchall())

class Conn:
    def __init__(self):
        self.con = pymysql.connect(cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4", **DB_CFG)
    def execute(self, sql, args=()):
        cur = self.con.cursor(); cur.execute(sql.replace("%", "%%").replace("?", "%s"), tuple(args)); return Cur(cur)
    def commit(self): self.con.commit()
    def close(self): self.con.close()

def db():
    if "db" not in g: g.db = Conn()
    return g.db

@app.teardown_appcontext
def close(_):
    d = g.pop("db", None)
    if d: d.close()

ENG = " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
def init_db():
    try:  # create the database if the MySQL user is allowed to
        t = pymysql.connect(host=DB_CFG["host"], user=DB_CFG["user"], password=DB_CFG["password"])
        t.cursor().execute(f"CREATE DATABASE IF NOT EXISTS {DB_CFG['database']} CHARACTER SET utf8mb4"); t.close()
    except Exception: pass
    c = Conn()
    c.execute("CREATE TABLE IF NOT EXISTS users(id INT AUTO_INCREMENT PRIMARY KEY, username VARCHAR(100) UNIQUE, password VARCHAR(255), role VARCHAR(20), full_name VARCHAR(200), reg_no VARCHAR(50), programme VARCHAR(200), photo VARCHAR(100), email VARCHAR(200))" + ENG)
    if "email" not in [r["Field"] for r in c.execute("SHOW COLUMNS FROM users").fetchall()]:
        c.execute("ALTER TABLE users ADD COLUMN email VARCHAR(200)")
    for n, (_, cols, ps) in T.items():
        defs = ("student VARCHAR(50)," if ps else "") + ",".join(f"{x} {'MEDIUMTEXT' if x == 'activity' else 'TEXT'}" for x in cols)
        c.execute(f"CREATE TABLE IF NOT EXISTS {n}(id INT AUTO_INCREMENT PRIMARY KEY, {defs})" + ENG)
        have = [r["Field"] for r in c.execute(f"SHOW COLUMNS FROM {n}").fetchall()]
        for col in cols:
            if col not in have: c.execute(f"ALTER TABLE {n} ADD COLUMN {col} TEXT")
    if not c.execute("SELECT 1 FROM users").fetchone():
        ins = "INSERT INTO users(username,password,role,full_name,reg_no,programme) VALUES(?,?,?,?,?,?)"
        c.execute(ins, ("superadmin", generate_password_hash("admin123"), "super", "Super Administrator", None, None))
        c.execute(ins, ("student1", generate_password_hash("student123"), "student", "Demo Student", "WU/2024/001", "BSc Computer Science"))
        S = "WU/2024/001"
        c.execute("INSERT INTO course_registration(student,course_code,course_title,credits,semester,status) VALUES(?,?,?,?,?,?)", (S,"CS101","Intro to Programming","10","1","Registered"))
        c.execute("INSERT INTO exam_results(student,course_code,course_title,marks) VALUES(?,?,?,?)", (S,"CS101","Intro to Programming","72.5"))
        c.execute("INSERT INTO programme_structure(programme,year,semester,course_code,course_title,credits,type) VALUES(?,?,?,?,?,?,?)", ("BSc Computer Science","1","1","CS101","Intro to Programming","10","Core"))
        c.execute("INSERT INTO hostels(name,gender,location,fee) VALUES(?,?,?,?)", ("Block A","Mixed","Main Campus","250000"))
        c.execute("INSERT INTO rooms(hostel,room_no,capacity,status) VALUES('Block A','101','2','Available')")
        c.execute("INSERT INTO rooms(hostel,room_no,capacity,status) VALUES('Block A','102','2','Available')")
    c.commit(); c.close()

def csrf():
    if "csrf" not in session: session["csrf"] = secrets.token_hex(16)
    return session["csrf"]

@app.before_request
def lockguard():
    if session.get("locked") and request.endpoint not in ("unlock", "logout", "login"):
        return redirect(url_for("unlock"))

SUP_OK = {"dashboard","sup_page","sup_review","profile","settings","logout","lock","unlock","photo","login","index","static"}
@app.before_request
def supguard():
    u = user() if "uid" in session else None
    if u and u["role"] == "supervisor" and request.endpoint not in SUP_OK:
        return redirect(url_for("sup_page"))

@app.before_request
def guard():
    if request.method == "POST" and request.form.get("csrf") != session.get("csrf"):
        abort(400, "Bad CSRF token")

def user():
    if "uid" not in session: return None
    return db().execute("SELECT * FROM users WHERE id=?", (session["uid"],)).fetchone()

def login_required(f):
    @wraps(f)
    def w(*a, **k):
        if not user(): return redirect(url_for("login"))
        return f(*a, **k)
    return w

def staff_required(f):
    @wraps(f)
    @login_required
    def w(*a, **k):
        if user()["role"] not in ("super", "admin"): abort(403)
        return f(*a, **k)
    return w

def super_required(f):
    @wraps(f)
    @login_required
    def w(*a, **k):
        if user()["role"] != "super": abort(403)
        return f(*a, **k)
    return w

@app.context_processor
def ctx():
    return dict(me=user(), menu=MENU, csrf_token=csrf, grade=grade, locked=session.get("locked"))

@app.route("/")
def index(): return redirect(url_for("dashboard"))

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        u = db().execute("SELECT * FROM users WHERE username=?", (request.form["username"].strip(),)).fetchone()
        if u and check_password_hash(u["password"], request.form["password"]):
            session.clear(); session["uid"] = u["id"]; return redirect(url_for("dashboard"))
        flash("Invalid username or password")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    u = user(); stats = {}
    if u["role"] == "supervisor":
        stats["Entries awaiting your approval"] = len([r for r in sup_entries(u) if r["status"] == "Pending Approval"])
    elif u["role"] == "student":
        for n, lbl in [("course_registration","Registered courses"),("exam_results","Results published"),("bookings","Hostel bookings"),("payment_status","Payment records")]:
            stats[lbl] = db().execute(f"SELECT COUNT(*) FROM {n} WHERE student=?", (u["reg_no"],)).fetchone()[0]
    else:
        stats["Students"] = db().execute("SELECT COUNT(*) FROM users WHERE role='student'").fetchone()[0]
        stats["Staff admins"] = db().execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0]
        stats["Course registrations"] = db().execute("SELECT COUNT(*) FROM course_registration").fetchone()[0]
        stats["Exam results"] = db().execute("SELECT COUNT(*) FROM exam_results").fetchone()[0]
    return render_template("dashboard.html", stats=stats)

@app.route("/t/<name>")
@login_required
def table(name):
    if name not in T: abort(404)
    title, cols, ps = T[name]; u = user(); q = request.args.get("q", "").strip()
    sql, args = f"SELECT * FROM {name}", []
    if ps and u["role"] == "student":
        sql += " WHERE student=?"; args.append(u["reg_no"])
    elif ps and q:
        sql += " WHERE student LIKE ?"; args.append(f"%{q}%")
    rows = db().execute(sql + " ORDER BY id DESC", args).fetchall()
    edit = u["role"] != "student" or name in STUDENT_RW
    if name == "logbook":
        return render_template("logbook.html", rows=logbook_rows(u, q), today=str(date.today()), editable=editable)
    opts = student_opts(name, u) if u["role"] == "student" else {}
    return render_template("placements.html" if name == "placements" else "table.html",
        name=name, title=title, cols=cols, ps=ps, rows=rows, q=q, NUM=NUM, edit=edit, opts=opts)

def owns(name, rid):
    u = user(); r = db().execute(f"SELECT * FROM {name} WHERE id=?", (rid,)).fetchone()
    if not r: abort(404)
    if u["role"] == "student" and (name not in STUDENT_RW or r["student"] != u["reg_no"]): abort(403)
    return r

@app.route("/t/<name>/add", methods=["POST"])
@login_required
def add(name):
    if name not in T: abort(404)
    u = user(); _, cols, ps = T[name]; f = request.form
    if u["role"] == "student" and name not in STUDENT_RW: abort(403)
    if u["role"] == "student" and name == "logbook": ("Logbook", ["log_date","week","activity","supervisor_comment","status"], 1),
    names, vals = list(cols), [f.get(c, "").strip() for c in cols]
    if ps:
        reg = u["reg_no"] if u["role"] == "student" else f.get("student", "").strip()
        if not db().execute("SELECT 1 FROM users WHERE reg_no=? AND role='student'", (reg,)).fetchone():
            flash("Unknown student Reg No"); return redirect(url_for("table", name=name))
        names.insert(0, "student"); vals.insert(0, reg)
    db().execute(f"INSERT INTO {name}({','.join(names)}) VALUES({','.join('?'*len(names))})", vals)
    db().commit(); flash("Record added"); return redirect(url_for("table", name=name))

@app.route("/t/<name>/delete/<int:rid>", methods=["POST"])
@login_required
def delete(name, rid):
    if name not in T: abort(404)
    owns(name, rid)
    db().execute(f"DELETE FROM {name} WHERE id=?", (rid,)); db().commit()
    return redirect(url_for("table", name=name))

@app.route("/placements/<int:rid>/supervisor", methods=["POST"])
@login_required
def sup_update(rid):
    owns("placements", rid); f = request.form
    db().execute("UPDATE placements SET supervisor_name=?,supervisor_phone=?,supervisor_email=? WHERE id=?",
        (f["supervisor_name"].strip(), f["supervisor_phone"].strip(), f["supervisor_email"].strip(), rid))
    db().commit(); flash("Supervisor details saved"); return redirect(url_for("table", name="placements"))

@app.route("/placements/<int:rid>/arrive", methods=["POST"])
@login_required
def arrive(rid):
    r = owns("placements", rid)
    db().execute("UPDATE placements SET arrived=? WHERE id=?", ("" if r["arrived"] == "Yes" else "Yes", rid))
    db().commit(); return redirect(url_for("table", name="placements"))

@app.route("/lock")
@login_required
def lock():
    session["locked"] = True; return redirect(url_for("unlock"))

@app.route("/unlock", methods=["GET","POST"])
def unlock():
    u = user()
    if not u: return redirect(url_for("login"))
    if request.method == "POST":
        if check_password_hash(u["password"], request.form["password"]):
            session.pop("locked", None); return redirect(url_for("dashboard"))
        flash("Wrong password")
    return render_template("lock.html")

@app.route("/users", methods=["GET","POST"])
@staff_required
def users():
    u = user()
    if request.method == "POST":
        f = request.form; role = f["role"]
        if role == "admin" and u["role"] != "super": abort(403)
        if role not in ("admin", "student", "supervisor"): abort(400)
        if len(f["password"]) < 6: flash("Password must be at least 6 characters"); return redirect(url_for("users"))
        try:
            db().execute("INSERT INTO users(username,password,role,full_name,reg_no,programme,email) VALUES(?,?,?,?,?,?,?)",
                (f["username"].strip(), generate_password_hash(f["password"]), role, f["full_name"].strip(),
                 f.get("reg_no", "").strip() or None, f.get("programme", "").strip() or None, f.get("email", "").strip().lower() or None))
            db().commit(); flash("User created")
        except pymysql.err.IntegrityError: flash("Username already exists")
        return redirect(url_for("users"))
    rows = db().execute("SELECT * FROM users WHERE role!='super' ORDER BY role, id DESC").fetchall()
    return render_template("users.html", rows=rows)

@app.route("/users/delete/<int:uid>", methods=["POST"])
@staff_required
def user_delete(uid):
    t = db().execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
    if not t or t["role"] == "super" or (t["role"] == "admin" and user()["role"] != "super"): abort(403)
    db().execute("DELETE FROM users WHERE id=?", (uid,)); db().commit(); return redirect(url_for("users"))

@app.route("/profile", methods=["GET","POST"])
@login_required
def profile():
    u = user()
    if request.method == "POST":
        p = request.files.get("photo")
        if p and p.filename:
            ext = p.filename.rsplit(".", 1)[-1].lower()
            if ext not in ("png", "jpg", "jpeg", "gif", "webp"): flash("Image files only"); return redirect(url_for("profile"))
            fn = f"user{u['id']}.{ext}"; p.save(os.path.join(UP, fn))
            db().execute("UPDATE users SET photo=? WHERE id=?", (fn, u["id"]))
        if u["role"] != "student" and request.form.get("full_name"):
            db().execute("UPDATE users SET full_name=? WHERE id=?", (request.form["full_name"].strip(), u["id"]))
        db().commit(); flash("Profile updated"); return redirect(url_for("profile"))
    return render_template("profile.html")

@app.route("/settings", methods=["GET","POST"])
@login_required
def settings():
    u = user()
    if request.method == "POST":
        if not check_password_hash(u["password"], request.form["old"]) or len(request.form["new"]) < 6:
            flash("Wrong current password or new password too short (min 6)")
        else:
            db().execute("UPDATE users SET password=? WHERE id=?", (generate_password_hash(request.form["new"]), u["id"]))
            db().commit(); flash("Password changed")
        return redirect(url_for("settings"))
    return render_template("settings.html")

@app.route("/photo/<fn>")
@login_required
def photo(fn): return send_from_directory(UP, fn)

@app.route("/guide")
@login_required
def guide():
    return render_template("guide.html", exists=os.path.exists(os.path.join(UP, "student_guide.pdf")))

@app.route("/guide/download")
@login_required
def guide_download():
    return send_from_directory(UP, "student_guide.pdf", as_attachment=True)

@app.route("/guide/upload", methods=["POST"])
@super_required
def guide_upload():
    p = request.files.get("pdf")
    if not p or not p.filename.lower().endswith(".pdf"): flash("Upload a PDF file")
    else: p.save(os.path.join(UP, "student_guide.pdf")); flash("Student Guide updated")
    return redirect(url_for("guide"))

# ---------- helpers ----------
def acad_year():
    t = date.today(); st = t.year if t.month >= 8 else t.year - 1
    return f"{st}/{st+1}"

def paid_total(reg):
    return float(db().execute("SELECT COALESCE(SUM(CAST(amount AS DECIMAL(10,2))),0) FROM payment_status WHERE student=? AND status='Paid' AND description LIKE 'Tuition%'", (reg,)).fetchone()[0] or 0)

def registered_courses(reg):
    return db().execute("SELECT DISTINCT course_code, course_title FROM course_registration WHERE student=? AND status='Registered'", (reg,)).fetchall()

def exists(sql, *a): return db().execute(sql, a).fetchone() is not None

def student_opts(name, u):
    reg, d, o = u["reg_no"], db(), {}
    if name == "course_registration":
        p = u["programme"] or ""
        o["list"] = d.execute("SELECT DISTINCT course_code,course_title,credits,semester FROM programme_structure WHERE (programme=? OR ?='') AND course_code NOT IN (SELECT course_code FROM course_registration WHERE student=?)", (p, p, reg)).fetchall()
    elif name == "exam_registration":
        o["list"] = [c for c in registered_courses(reg) if not exists("SELECT 1 FROM exam_registration WHERE student=? AND course_code=? AND academic_year=?", reg, c["course_code"], acad_year())]
        o["paid"], o["min"] = paid_total(reg), MIN_FEE
    elif name == "supp_registration":
        o["list"] = d.execute("SELECT course_code,course_title,marks FROM exam_results WHERE student=? AND CAST(marks AS DECIMAL(10,2))<? AND course_code NOT IN (SELECT course_code FROM supp_registration WHERE student=?)", (reg, PASS_MARK, reg)).fetchall()
    elif name == "course_evaluation":
        o["list"] = [c for c in registered_courses(reg) if not exists("SELECT 1 FROM course_evaluation WHERE student=? AND course_code=?", reg, c["course_code"])]
    elif name == "hostels":
        rooms = {}
        for r in d.execute("SELECT * FROM rooms"):
            occ = d.execute("SELECT COUNT(*) FROM bookings WHERE hostel=? AND room_no=? AND academic_year=? AND status='Booked'", (r["hostel"], r["room_no"], acad_year())).fetchone()[0]
            free = int(float(r["capacity"] or 0)) - occ
            if free > 0: rooms.setdefault(r["hostel"], []).append((r["room_no"], free))
        o["rooms"] = rooms
    elif name == "nhif":
        o["done"] = d.execute("SELECT * FROM nhif WHERE student=? AND created LIKE ?", (reg, f"{date.today().year}%")).fetchone()
        o["fee"] = NHIF_FEE
    return o

# ---------- student self-service ----------
@app.route("/submit/<kind>", methods=["POST"])
@login_required
def submit(kind):
    u = user()
    if u["role"] != "student": abort(403)
    reg, f, d, ay = u["reg_no"], request.form, db(), acad_year()
    code = f.get("course_code", "").strip()
    def back(t, msg): flash(msg); return redirect(url_for("table", name=t))
    if kind == "course":
        p = u["programme"] or ""
        c = d.execute("SELECT * FROM programme_structure WHERE course_code=? AND (programme=? OR ?='')", (code, p, p)).fetchone()
        if not c: return back("course_registration", "Course not available for your programme")
        if exists("SELECT 1 FROM course_registration WHERE student=? AND course_code=?", reg, code): return back("course_registration", "Already registered")
        d.execute("INSERT INTO course_registration(student,course_code,course_title,credits,semester,status) VALUES(?,?,?,?,?,?)", (reg, code, c["course_title"], c["credits"], c["semester"], "Registered"))
        d.execute("INSERT INTO registration_history(student,academic_year,semester,reg_date,status) VALUES(?,?,?,?,?)", (reg, ay, c["semester"], str(date.today()), "Registered"))
        d.commit(); return back("course_registration", "Course registered")
    if kind in ("exam", "eval"):
        c = next((x for x in registered_courses(reg) if x["course_code"] == code), None)
        t = "exam_registration" if kind == "exam" else "course_evaluation"
        if not c: return back(t, "Register the course first")
        if kind == "exam":
            if paid_total(reg) < MIN_FEE: return back(t, f"Pay at least TZS {MIN_FEE:,.0f} tuition before exam registration (paid: {paid_total(reg):,.0f})")
            if exists("SELECT 1 FROM exam_registration WHERE student=? AND course_code=? AND academic_year=?", reg, code, ay): return back(t, "Already registered")
            d.execute("INSERT INTO exam_registration(student,course_code,course_title,academic_year,status) VALUES(?,?,?,?,?)", (reg, code, c["course_title"], ay, "Registered"))
            d.commit(); return back(t, "Exam registration successful")
        try: rating = int(f.get("rating")); assert 1 <= rating <= 5
        except Exception: return back(t, "Rating must be 1 to 5")
        if exists("SELECT 1 FROM course_evaluation WHERE student=? AND course_code=?", reg, code): return back(t, "Already evaluated")
        d.execute("INSERT INTO course_evaluation(student,course_code,lecturer,rating,comment) VALUES(?,?,?,?,?)", (reg, code, f.get("lecturer", "").strip(), rating, f.get("comment", "").strip()))
        d.commit(); return back(t, "Evaluation submitted")
    if kind == "supp":
        r = d.execute("SELECT * FROM exam_results WHERE student=? AND course_code=? AND CAST(marks AS DECIMAL(10,2))<?", (reg, code, PASS_MARK)).fetchone()
        if not r: return back("supp_registration", f"Supplementary is only for courses below the pass mark ({PASS_MARK})")
        if exists("SELECT 1 FROM supp_registration WHERE student=? AND course_code=?", reg, code): return back("supp_registration", "Already registered")
        d.execute("INSERT INTO supp_registration(student,course_code,course_title,academic_year,status) VALUES(?,?,?,?,?)", (reg, code, r["course_title"], ay, "Registered"))
        d.commit(); return back("supp_registration", "Supplementary registration successful")
    if kind == "postpone":
        a, b = f.get("from_date", ""), f.get("to_date", "")
        if not f.get("reason", "").strip() or not a or not b or b < a: return back("postponed_study", "Fill reason and valid dates")
        d.execute("INSERT INTO postponed_study(student,academic_year,reason,from_date,to_date,status) VALUES(?,?,?,?,?,?)", (reg, ay, f["reason"].strip(), a, b, "Pending"))
        d.commit(); return back("postponed_study", "Postponement request sent")
    if kind == "transfer":
        if not f.get("to_programme", "").strip(): return back("programme_transfer", "Enter the programme")
        d.execute("INSERT INTO programme_transfer(student,from_programme,to_programme,reason,status) VALUES(?,?,?,?,?)", (reg, u["programme"], f["to_programme"].strip(), f.get("reason", "").strip(), "Pending"))
        d.commit(); return back("programme_transfer", "Transfer request sent")
    abort(404)

@app.route("/status/<name>/<int:rid>/<st>", methods=["POST"])
@staff_required
def set_status(name, rid, st):
    if name not in ("postponed_study", "programme_transfer") or st not in ("Approved", "Rejected"): abort(400)
    r = db().execute(f"SELECT * FROM {name} WHERE id=?", (rid,)).fetchone()
    if not r: abort(404)
    db().execute(f"UPDATE {name} SET status=? WHERE id=?", (st, rid))
    if name == "programme_transfer" and st == "Approved":
        db().execute("UPDATE users SET programme=? WHERE reg_no=?", (r["to_programme"], r["student"]))
    db().commit(); return redirect(url_for("table", name=name))

@app.route("/nhif/generate", methods=["POST"])
@login_required
def nhif_generate():
    u = user()
    if u["role"] != "student": abort(403)
    y = str(date.today().year)
    ex = db().execute("SELECT control_number FROM nhif WHERE student=? AND created LIKE ?", (u["reg_no"], y + "%")).fetchone()
    if ex: flash(f"Already generated for {y}: {ex[0]}"); return redirect(url_for("table", name="nhif"))
    digits = re.sub(r"\D", "", u["reg_no"])[-8:]
    cn = f"99{digits}{y[2:]}{zlib.crc32(u['reg_no'].encode()) % 100:02d}"
    db().execute("INSERT INTO nhif(student,control_number,amount,status,created) VALUES(?,?,?,?,?)", (u["reg_no"], cn, f"{NHIF_FEE:.2f}", "Pending", str(date.today())))
    db().execute("INSERT INTO payment_status(student,description,control_number,amount,status) VALUES(?,?,?,?,?)", (u["reg_no"], f"NHIF {y}", cn, f"{NHIF_FEE:.2f}", "Pending"))
    db().commit(); flash(f"Control number generated: {cn}"); return redirect(url_for("table", name="nhif"))

@app.route("/book", methods=["POST"])
@login_required
def book():
    u = user()
    if u["role"] != "student": abort(403)
    d, ay = db(), acad_year(); h, rn = request.form.get("hostel", ""), request.form.get("room_no", "")
    back = redirect(url_for("table", name="hostels"))
    if exists("SELECT 1 FROM bookings WHERE student=? AND academic_year=? AND status='Booked'", u["reg_no"], ay):
        flash("You already have a booking for this academic year"); return back
    r = d.execute("SELECT * FROM rooms WHERE hostel=? AND room_no=?", (h, rn)).fetchone()
    if not r: abort(404)
    occ = d.execute("SELECT COUNT(*) FROM bookings WHERE hostel=? AND room_no=? AND academic_year=? AND status='Booked'", (h, rn, ay)).fetchone()[0]
    if occ >= int(float(r["capacity"] or 0)): flash("Room is full"); return back
    hs = d.execute("SELECT fee FROM hostels WHERE name=?", (h,)).fetchone(); fee = hs["fee"] if hs else "0"
    cn = f"88{re.sub(chr(92)+'D','',u['reg_no'])[-8:]}{ay[2:4]}{zlib.crc32(u['reg_no'].encode()) % 100:02d}"
    d.execute("INSERT INTO bookings(student,hostel,room_no,academic_year,status) VALUES(?,?,?,?,?)", (u["reg_no"], h, rn, ay, "Booked"))
    d.execute("INSERT INTO booking_payments(student,hostel,room_no,amount,control_number,status) VALUES(?,?,?,?,?,?)", (u["reg_no"], h, rn, fee, cn, "Pending"))
    d.execute("INSERT INTO payment_status(student,description,control_number,amount,status) VALUES(?,?,?,?,?)", (u["reg_no"], f"Hostel {h} room {rn}", cn, fee, "Pending"))
    if occ + 1 >= int(float(r["capacity"] or 0)): d.execute("UPDATE rooms SET status='Full' WHERE hostel=? AND room_no=?", (h, rn))
    d.commit(); flash(f"Room {rn} booked in {h}. Payment control number: {cn}"); return redirect(url_for("table", name="bookings"))

# ---------- logbook ----------
OK_TAGS = {"p","br","b","strong","i","em","u","s","strike","ul","ol","li","h1","h2","h3","div","span","blockquote","table","thead","tbody","tr","td","th","sub","sup"}
def clean_html(raw):
    out = []
    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag not in OK_TAGS: return
            st = []
            for k, v in attrs:
                if k == "style" and v:
                    m = re.search(r"text-align:\s*(left|right|center|justify)", v)
                    if m: st.append(f"text-align:{m.group(1)}")
                    m = re.search(r"(?<![-\w])color:\s*(#[0-9a-fA-F]{3,6}|rgb\(\d+,\s*\d+,\s*\d+\))", v)
                    if m: st.append(f"color:{m.group(1)}")
                    m = re.search(r"background-color:\s*(#[0-9a-fA-F]{3,6}|rgb\(\d+,\s*\d+,\s*\d+\))", v)
                    if m: st.append(f"background-color:{m.group(1)}")
                    m = re.search(r"font-size:\s*(x-small|small|medium|large|x-large|xx-large|xxx-large)", v)
                    if m: st.append(f"font-size:{m.group(1)}")
            out.append(f"<{tag}" + (f' style="{";".join(st)}"' if st else "") + ">")
        def handle_endtag(self, tag):
            if tag in OK_TAGS and tag != "br": out.append(f"</{tag}>")
        def handle_data(self, data): out.append(data.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    P().feed(raw[:30000]); return "".join(out)

def editable(r):
    return r["log_date"] == str(date.today()) and r["status"] != "Approved"

def sup_entries(u):
    base = "SELECT l.*, us.full_name, p.organization FROM logbook l JOIN users us ON us.reg_no=l.student LEFT JOIN placements p ON p.student=l.student"
    order = " ORDER BY (l.status='Pending Approval') DESC, l.log_date DESC, l.id DESC"
    if u["role"] == "supervisor":
        em = (u["email"] or "").lower()
        rows = db().execute(base + " WHERE ?!='' AND lower(p.supervisor_email)=?" + order, (em, em)).fetchall()
    else: rows = db().execute(base + order).fetchall()
    seen, res = set(), []
    for r in rows:
        if r["id"] not in seen: seen.add(r["id"]); res.append(r)
    return res

def logbook_rows(u, q=""):
    if u["role"] == "student":
        return db().execute("SELECT l.*, '' AS full_name, '' AS organization FROM logbook l WHERE student=? ORDER BY log_date DESC, id DESC", (u["reg_no"],)).fetchall()
    return [r for r in sup_entries(u) if q.lower() in (r["student"] or "").lower()]

@app.route("/logbook/new", methods=["GET","POST"])
@login_required
def log_new():
    u = user()
    if u["role"] != "student": abort(403)
    today = str(date.today())
    ex = db().execute("SELECT id FROM logbook WHERE student=? AND log_date=?", (u["reg_no"], today)).fetchone()
    if ex: flash("You already have today's entry - edit it below"); return redirect(url_for("log_edit", rid=ex["id"]))
    if request.method == "POST":
        txt = clean_html(request.form.get("activity", ""))
        if not re.sub(r"<[^>]+>", "", txt).strip(): flash("Write the working activity"); return redirect(url_for("log_new"))
        db().execute("INSERT INTO logbook(student,log_date,week,activity,supervisor_comment,status) VALUES(?,?,?,?,?,?)", (u["reg_no"], today, request.form.get("week", "").strip(), txt, "", "Pending Approval"))
        db().commit(); flash("Saved. Waiting for local supervisor approval."); return redirect(url_for("table", name="logbook"))
    return render_template("logbook_edit.html", row=None, today=today)

@app.route("/logbook/<int:rid>/edit", methods=["GET","POST"])
@login_required
def log_edit(rid):
    u = user(); r = db().execute("SELECT * FROM logbook WHERE id=?", (rid,)).fetchone()
    if not r or u["role"] != "student" or r["student"] != u["reg_no"]: abort(403)
    if not editable(r): flash("This entry is locked (edits allowed only on its own day, 00:00-23:59, and before approval)"); return redirect(url_for("table", name="logbook"))
    if request.method == "POST":
        txt = clean_html(request.form.get("activity", ""))
        if not re.sub(r"<[^>]+>", "", txt).strip(): flash("Write the working activity"); return redirect(url_for("log_edit", rid=rid))
        db().execute("UPDATE logbook SET week=?, activity=?, status='Pending Approval' WHERE id=?", (request.form.get("week", "").strip(), txt, rid))
        db().commit(); flash("Updated. Waiting for local supervisor approval."); return redirect(url_for("table", name="logbook"))
    return render_template("logbook_edit.html", row=r, today=r["log_date"])

@app.route("/supervisor")
@login_required
def sup_page():
    u = user()
    if u["role"] == "student": abort(403)
    return render_template("supervisor.html", rows=sup_entries(u))

@app.route("/supervisor/<int:rid>/review", methods=["POST"])
@login_required
def sup_review(rid):
    u = user()
    if u["role"] == "student": abort(403)
    if rid not in [r["id"] for r in sup_entries(u)]: abort(403)
    act = request.form.get("action")
    if act not in ("Approved", "Rejected"): abort(400)
    db().execute("UPDATE logbook SET status=?, supervisor_comment=? WHERE id=?", (act, request.form.get("comment", "").strip(), rid))
    db().commit(); flash(f"Entry {act.lower()}"); return redirect(url_for("sup_page"))


init_db()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
