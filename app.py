import os
from datetime import datetime, date
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET","change-me")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(BASE_DIR, "bistroops.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"

# ---------- Models ----------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    full_name = db.Column(db.String(120), default="")
    role = db.Column(db.String(20), default="waiter")  # waiter, shift_lead, manager
    password_hash = db.Column(db.String(255), nullable=False)
    def set_password(self, p): self.password_hash = generate_password_hash(p)
    def check_password(self, p): return check_password_hash(self.password_hash, p)

class Shift(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    employee = db.Column(db.String(120), nullable=False)
    role = db.Column(db.String(120))
    start = db.Column(db.DateTime, nullable=False)
    end = db.Column(db.DateTime, nullable=False)

class Reservation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer = db.Column(db.String(120), nullable=False)
    size = db.Column(db.Integer, default=2)
    at = db.Column(db.DateTime, nullable=False)
    notes = db.Column(db.Text)

class ShiftReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    lead_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    revenue = db.Column(db.Float, default=0.0)
    issues = db.Column(db.Text)
    notes = db.Column(db.Text)
    lead = db.relationship("User")

class TimeEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    start = db.Column(db.DateTime, nullable=False)
    end = db.Column(db.DateTime, nullable=False)
    note = db.Column(db.String(200))
    user = db.relationship("User")

class ClothingDeposit(db.Model):   # Kleiderpfand
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    item = db.Column(db.String(120), nullable=False)
    size = db.Column(db.String(20))
    amount = db.Column(db.Float, default=0.0)
    date = db.Column(db.Date, default=date.today)
    returned = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)
    user = db.relationship("User")

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ---------- Helpers ----------
def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()
            if current_user.role not in roles:
                flash("Keine Berechtigung.", "error")
                return redirect(url_for("dashboard"))
            return fn(*args, **kwargs)
        return wrapper
    return deco

# ---------- CLI ----------
@app.cli.command("db-init")
def db_init():
    db.create_all()
    def ensure(u, p, role, name):
        if not User.query.filter_by(username=u).first():
            x = User(username=u, role=role, full_name=name); x.set_password(p)
            db.session.add(x); db.session.commit()
    ensure("admin","admin123","manager","Betriebsleiter")
    ensure("lead","lead123","shift_lead","Schichtleiter")
    ensure("waiter","waiter123","waiter","Kellner")
    print("Users ready: admin/lead/waiter")

# ---------- Auth ----------
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        u = User.query.filter_by(username=request.form["username"]).first()
        if u and u.check_password(request.form["password"]):
            login_user(u)
            return redirect(url_for("dashboard"))
        flash("Falsche Zugangsdaten", "error")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# ---------- Dashboard ----------
@app.route("/")
@login_required
def dashboard():
    stats = {
        "shifts": Shift.query.count(),
        "reservations": Reservation.query.count(),
        "reports": ShiftReport.query.count(),
        "hours": TimeEntry.query.count(),
        "deposits": ClothingDeposit.query.count(),
        "users": User.query.count()
    }
    return render_template("dashboard.html", stats=stats)

# ---------- Team directory (all roles) ----------
@app.route("/team")
@login_required
def team():
    people = User.query.order_by(User.role.desc(), User.username.asc()).all()
    return render_template("team.html", people=people)

# ---------- Shifts (all roles view, manager/lead can create) ----------
@app.route("/shifts", methods=["GET","POST"])
@login_required
def shifts():
    if request.method == "POST":
        if current_user.role not in ("shift_lead","manager"):
            flash("Nur Schichtleitung/Manager dürfen Schichten anlegen.", "error")
            return redirect(url_for("shifts"))
        s = Shift(employee=request.form["employee"], role=request.form.get("role",""),
                  start=datetime.fromisoformat(request.form["start"]),
                  end=datetime.fromisoformat(request.form["end"]))
        db.session.add(s); db.session.commit()
        return redirect(url_for("shifts"))
    data = Shift.query.order_by(Shift.start.desc()).all()
    return render_template("shifts.html", data=data)

@app.route("/shifts/<int:id>/delete", methods=["POST"])
@login_required
@role_required("shift_lead","manager")
def shifts_delete(id):
    db.session.delete(Shift.query.get_or_404(id)); db.session.commit()
    return redirect(url_for("shifts"))

# ---------- Reservations (lead/manager) ----------
@app.route("/reservations", methods=["GET","POST"])
@login_required
@role_required("shift_lead","manager")
def reservations():
    if request.method == "POST":
        r = Reservation(customer=request.form["customer"],
                        size=int(request.form["size"] or 2),
                        at=datetime.fromisoformat(request.form["at"]),
                        notes=request.form.get("notes",""))
        db.session.add(r); db.session.commit()
        return redirect(url_for("reservations"))
    data = Reservation.query.order_by(Reservation.at.desc()).all()
    return render_template("reservations.html", data=data)

@app.route("/reservations/<int:id>/delete", methods=["POST"])
@login_required
@role_required("shift_lead","manager")
def res_delete(id):
    db.session.delete(Reservation.query.get_or_404(id)); db.session.commit()
    return redirect(url_for("reservations"))

# ---------- Shift reports (lead/manager) ----------
@app.route("/reports", methods=["GET","POST"])
@login_required
@role_required("shift_lead","manager")
def reports():
    if request.method == "POST":
        rep = ShiftReport(date=datetime.fromisoformat(request.form["date"]).date(),
                          lead_id=current_user.id,
                          revenue=float(request.form.get("revenue",0) or 0),
                          issues=request.form.get("issues",""),
                          notes=request.form.get("notes",""))
        db.session.add(rep); db.session.commit()
        return redirect(url_for("reports"))
    data = ShiftReport.query.order_by(ShiftReport.date.desc()).all()
    return render_template("reports.html", data=data)

@app.route("/reports/<int:id>/delete", methods=["POST"])
@login_required
@role_required("shift_lead","manager")
def reports_delete(id):
    db.session.delete(ShiftReport.query.get_or_404(id)); db.session.commit()
    return redirect(url_for("reports"))

# ---------- Time entries (lead/manager can add any, waiter can add own) ----------
@app.route("/hours", methods=["GET","POST"])
@login_required
def hours():
    if request.method == "POST":
        # waiter may only create for self
        user_id = int(request.form["user_id"])
        if current_user.role == "waiter" and user_id != current_user.id:
            flash("Kellner dürfen nur eigene Stunden eintragen.", "error")
            return redirect(url_for("hours"))
        if current_user.role == "waiter":
            allowed = True
        else:
            allowed = True  # lead/manager can add any
        if allowed:
            t = TimeEntry(user_id=user_id,
                          start=datetime.fromisoformat(request.form["start"]),
                          end=datetime.fromisoformat(request.form["end"]),
                          note=request.form.get("note",""))
            db.session.add(t); db.session.commit()
        return redirect(url_for("hours"))
    # waiter sees own; lead/manager see all
    if current_user.role == "waiter":
        data = TimeEntry.query.filter_by(user_id=current_user.id).order_by(TimeEntry.start.desc()).all()
        users = [current_user]
    else:
        data = TimeEntry.query.order_by(TimeEntry.start.desc()).all()
        users = User.query.order_by(User.username.asc()).all()
    return render_template("hours.html", data=data, users=users)

@app.route("/hours/<int:id>/delete", methods=["POST"])
@login_required
def hours_delete(id):
    t = TimeEntry.query.get_or_404(id)
    if current_user.role == "waiter" and t.user_id != current_user.id:
        flash("Kellner dürfen nur eigene Einträge löschen.", "error")
        return redirect(url_for("hours"))
    if current_user.role in ("shift_lead","manager") or t.user_id == current_user.id:
        db.session.delete(t); db.session.commit()
    return redirect(url_for("hours"))

# ---------- Clothing deposit (manager only) ----------
@app.route("/deposit", methods=["GET","POST"])
@login_required
@role_required("manager")
def deposit():
    if request.method == "POST":
        d = ClothingDeposit(user_id=int(request.form["user_id"]),
                            item=request.form["item"],
                            size=request.form.get("size",""),
                            amount=float(request.form.get("amount",0) or 0),
                            date=datetime.fromisoformat(request.form["date"]).date(),
                            returned=("returned" in request.form),
                            notes=request.form.get("notes",""))
        db.session.add(d); db.session.commit()
        return redirect(url_for("deposit"))
    data = ClothingDeposit.query.order_by(ClothingDeposit.date.desc()).all()
    users = User.query.order_by(User.username.asc()).all()
    return render_template("deposit.html", data=data, users=users)

@app.route("/deposit/<int:id>/toggle", methods=["POST"])
@login_required
@role_required("manager")
def deposit_toggle(id):
    d = ClothingDeposit.query.get_or_404(id)
    d.returned = not d.returned
    db.session.commit()
    return redirect(url_for("deposit"))

@app.route("/deposit/<int:id>/delete", methods=["POST"])
@login_required
@role_required("manager")
def deposit_delete(id):
    db.session.delete(ClothingDeposit.query.get_or_404(id)); db.session.commit()
    return redirect(url_for("deposit"))

# ---------- User management (manager only) ----------
@app.route("/users", methods=["GET","POST"])
@login_required
@role_required("manager")
def users():
    if request.method == "POST":
        u = User(username=request.form["username"],
                 full_name=request.form.get("full_name",""),
                 role=request.form.get("role","waiter"))
        u.set_password(request.form["password"])
        db.session.add(u); db.session.commit()
        return redirect(url_for("users"))
    data = User.query.order_by(User.username.asc()).all()
    return render_template("users.html", data=data)

@app.route("/users/<int:id>/delete", methods=["POST"])
@login_required
@role_required("manager")
def users_delete(id):
    if id == current_user.id:
        flash("Eigenen Account nicht löschen.", "error")
        return redirect(url_for("users"))
    db.session.delete(User.query.get_or_404(id)); db.session.commit()
    return redirect(url_for("users"))

# ---------- Run ----------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        # falls keine Benutzer existieren → Standardaccounts anlegen
        if not User.query.first():
            from app import db_init
            db_init()
    app.run(debug=True)
