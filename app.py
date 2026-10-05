import os, re, io, csv, uuid, hmac, secrets, time
from functools import wraps
from datetime import datetime
from dotenv import load_dotenv
from flask import (Flask, jsonify, render_template, request, abort, url_for,
                   session, redirect, flash, Response)
from werkzeug.utils import secure_filename
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from intasend import APIService

load_dotenv()
app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev"),
    SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", "sqlite:///poll.db"),
    MAX_CONTENT_LENGTH=3 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
UPLOAD_DIR = os.path.join(app.root_path, "static", "candidates")
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}
db = SQLAlchemy(app)

PRICE = int(os.getenv("VOTE_PRICE_KES", 10))
CHALLENGE = os.getenv("INTASEND_WEBHOOK_CHALLENGE", "")


def intasend():
    return APIService(
        token=os.getenv("INTASEND_TOKEN"),
        publishable_key=os.getenv("INTASEND_PUBLISHABLE_KEY"),
        test=os.getenv("INTASEND_TEST", "true").lower() == "true",
    )


class Candidate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    party = db.Column(db.String(120), default="")
    slogan = db.Column(db.String(200), default="")
    photo = db.Column(db.String(300), default="")  # filename in static/candidates/ or a full https URL
    party_color = db.Column(db.String(9), default="#0B5D3B")
    active = db.Column(db.Boolean, default=True)


class Payment(db.Model):
    """One row per vote attempt. A vote counts only when state == COMPLETE."""
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(db.String(64), unique=True, index=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidate.id"), nullable=False)
    phone = db.Column(db.String(15))
    amount = db.Column(db.Integer, default=PRICE)
    state = db.Column(db.String(20), default="PENDING")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def normalize_phone(raw):
    p = re.sub(r"\D", "", raw or "")
    if p.startswith("0") and len(p) == 10:
        p = "254" + p[1:]
    elif len(p) == 9 and p[0] in "17":
        p = "254" + p
    return p if re.fullmatch(r"254[17]\d{8}", p) else None


def photo_src(c):
    if not c.photo:
        return ""
    if c.photo.startswith("http"):
        return c.photo
    return url_for("static", filename="candidates/" + c.photo)


def tally():
    rows = dict(
        db.session.query(Payment.candidate_id, func.count(Payment.id))
        .filter(Payment.state == "COMPLETE").group_by(Payment.candidate_id).all()
    )
    cands = Candidate.query.filter_by(active=True).all()
    total = sum(rows.get(c.id, 0) for c in cands)
    out = [
        {"id": c.id, "name": c.name, "party": c.party, "slogan": c.slogan,
         "photo": photo_src(c), "party_color": c.party_color or "#0B5D3B",
         "votes": rows.get(c.id, 0),
         "pct": round(rows.get(c.id, 0) * 100 / total, 1) if total else 0}
        for c in cands
    ]
    return sorted(out, key=lambda r: -r["votes"]), total


@app.get("/")
def index():
    results, total = tally()
    return render_template("index.html", results=results, total=total, price=PRICE)


@app.get("/api/results")
def api_results():
    results, total = tally()
    return jsonify(results=results, total=total)


@app.post("/api/vote")
def vote():
    data = request.get_json(silent=True) or {}
    cand = db.session.get(Candidate, data.get("candidate_id"))
    phone = normalize_phone(data.get("phone"))
    if not cand or not cand.active:
        return jsonify(error="Choose a candidate."), 400
    if not phone:
        return jsonify(error="Enter a valid Safaricom number, e.g. 0712345678."), 400
    try:
        resp = intasend().collect.mpesa_stk_push(
            phone_number=phone, email="voter@example.com", amount=PRICE,
            narrative=f"Poll vote #{cand.id}", api_ref=f"cand-{cand.id}",
        )
        invoice_id = resp["invoice"]["invoice_id"]
    except Exception as e:
        app.logger.exception("STK push failed: %s", e)
        return jsonify(error="Could not start the M-Pesa prompt. Try again."), 502
    db.session.add(Payment(invoice_id=invoice_id, candidate_id=cand.id, phone=phone, amount=PRICE))
    db.session.commit()
    return jsonify(invoice_id=invoice_id)


@app.get("/api/status/<invoice_id>")
def status(invoice_id):
    p = Payment.query.filter_by(invoice_id=invoice_id).first_or_404()
    if p.state in ("PENDING", "PROCESSING"):
        try:
            r = intasend().collect.status(invoice_id=invoice_id)
            p.state = r["invoice"]["state"]
            db.session.commit()
        except Exception as e:
            app.logger.warning("status check failed: %s", e)
    return jsonify(state=p.state)


@app.post("/webhook/intasend")
def webhook():
    data = request.get_json(silent=True) or {}
    if not CHALLENGE or data.get("challenge") != CHALLENGE:
        abort(403)
    p = Payment.query.filter_by(invoice_id=data.get("invoice_id")).first()
    if p and data.get("state"):
        p.state = data["state"]
        db.session.commit()
    return "", 200



# ───────────────────────── Admin ─────────────────────────
def admin_required(f):
    @wraps(f)
    def wrap(*a, **k):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return f(*a, **k)
    return wrap


@app.context_processor
def inject_csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return {"csrf_token": session["csrf"]}


@app.before_request
def csrf_protect():
    if request.method == "POST" and request.path.startswith("/admin"):
        if not hmac.compare_digest(request.form.get("csrf", ""), session.get("csrf", "x")):
            abort(400)


def save_photo(file):
    if not file or not file.filename:
        return None
    ext = secure_filename(file.filename).rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError("Photo must be PNG, JPG or WEBP.")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    file.save(os.path.join(UPLOAD_DIR, name))
    return name


def remove_photo(name):
    if name and not name.startswith("http"):
        try:
            os.remove(os.path.join(UPLOAD_DIR, os.path.basename(name)))
        except OSError:
            pass


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        time.sleep(0.8)  # slows brute force
        pw = request.form.get("password", "")
        if ADMIN_PASSWORD and hmac.compare_digest(pw, ADMIN_PASSWORD):
            session.clear(); session["admin"] = True
            return redirect(url_for("admin_dashboard"))
        flash("Wrong password.", "err")
    return render_template("admin_login.html", disabled=not ADMIN_PASSWORD)


@app.post("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.get("/admin")
@admin_required
def admin_dashboard():
    count = lambda s: Payment.query.filter_by(state=s).count()
    complete = Payment.query.filter_by(state="COMPLETE")
    revenue = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.state == "COMPLETE").scalar()
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    stats = {
        "revenue": revenue, "votes": complete.count(),
        "today": complete.filter(Payment.created_at >= today).count(),
        "pending": count("PENDING") + count("PROCESSING"), "failed": count("FAILED"),
        "voters": db.session.query(func.count(func.distinct(Payment.phone))).filter(Payment.state == "COMPLETE").scalar(),
    }
    results, total = tally()
    names = {c.id: c.name for c in Candidate.query.all()}
    recent = Payment.query.order_by(Payment.created_at.desc()).limit(10).all()
    return render_template("admin_dashboard.html", stats=stats, results=results, recent=recent, names=names)


@app.route("/admin/candidates", methods=["GET", "POST"])
@admin_required
def admin_candidates():
    if request.method == "POST":
        try:
            name = request.form["name"].strip()
            if not name:
                raise ValueError("Name is required.")
            photo = save_photo(request.files.get("photo_file")) or request.form.get("photo_url", "").strip()
            db.session.add(Candidate(
                name=name, party=request.form.get("party", "").strip(),
                slogan=request.form.get("slogan", "").strip(), photo=photo,
                party_color=request.form.get("party_color", "#0B5D3B")))
            db.session.commit()
            flash("Candidate added.", "ok")
        except ValueError as e:
            flash(str(e), "err")
        return redirect(url_for("admin_candidates"))
    cands = Candidate.query.order_by(Candidate.id).all()
    return render_template("admin_candidates.html", cands=cands, photo_src=photo_src, edit=None)


@app.route("/admin/candidates/<int:cid>", methods=["GET", "POST"])
@admin_required
def admin_candidate_edit(cid):
    c = db.get_or_404(Candidate, cid)
    if request.method == "POST":
        try:
            c.name = request.form["name"].strip() or c.name
            c.party = request.form.get("party", "").strip()
            c.slogan = request.form.get("slogan", "").strip()
            c.party_color = request.form.get("party_color", c.party_color)
            c.active = "active" in request.form
            new = save_photo(request.files.get("photo_file"))
            url = request.form.get("photo_url", "").strip()
            if new:
                remove_photo(c.photo); c.photo = new
            elif url and url != c.photo:
                remove_photo(c.photo); c.photo = url
            db.session.commit()
            flash("Saved.", "ok")
            return redirect(url_for("admin_candidates"))
        except ValueError as e:
            flash(str(e), "err")
    cands = Candidate.query.order_by(Candidate.id).all()
    return render_template("admin_candidates.html", cands=cands, photo_src=photo_src, edit=c)


@app.post("/admin/candidates/<int:cid>/delete")
@admin_required
def admin_candidate_delete(cid):
    c = db.get_or_404(Candidate, cid)
    if Payment.query.filter_by(candidate_id=cid).count():
        c.active = False
        flash("Candidate has payments, so they were hidden instead of deleted.", "ok")
    else:
        remove_photo(c.photo); db.session.delete(c)
        flash("Candidate deleted.", "ok")
    db.session.commit()
    return redirect(url_for("admin_candidates"))


def payments_query():
    q = Payment.query
    state, cand, term = request.args.get("state"), request.args.get("candidate", type=int), request.args.get("q", "").strip()
    if state: q = q.filter_by(state=state)
    if cand: q = q.filter_by(candidate_id=cand)
    if term: q = q.filter(Payment.phone.contains(term) | Payment.invoice_id.contains(term))
    return q.order_by(Payment.created_at.desc())


@app.get("/admin/payments")
@admin_required
def admin_payments():
    page = request.args.get("page", 1, type=int)
    pg = payments_query().paginate(page=page, per_page=25, error_out=False)
    names = {c.id: c.name for c in Candidate.query.all()}
    qs = {k: v for k, v in request.args.items() if k != "page" and v}
    return render_template("admin_payments.html", pg=pg, names=names, args=request.args, qs=qs)


@app.get("/admin/payments.csv")
@admin_required
def admin_payments_csv():
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["time_utc", "invoice_id", "candidate", "phone", "amount", "state"])
    names = {c.id: c.name for c in Candidate.query.all()}
    for p in payments_query():
        w.writerow([p.created_at, p.invoice_id, names.get(p.candidate_id), p.phone, p.amount, p.state])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=payments.csv"})


@app.cli.command("init-db")
def init_db():
    db.create_all()
    print("Database ready.")


@app.cli.command("add-candidate")
def add_candidate():
    import click
    name = click.prompt("Name"); party = click.prompt("Party", default="")
    slogan = click.prompt("Slogan", default="")
    photo = click.prompt("Photo (file in static/candidates/ or https URL)", default="")
    color = click.prompt("Party colour hex", default="#0B5D3B")
    db.create_all()
    db.session.add(Candidate(name=name, party=party, slogan=slogan, photo=photo, party_color=color))
    db.session.commit()
    print("Added.")


@app.cli.command("set-photo")
def set_photo():
    import click
    cid = click.prompt("Candidate id", type=int)
    c = db.session.get(Candidate, cid)
    if not c:
        print("Not found."); return
    c.photo = click.prompt("Photo (file in static/candidates/ or https URL)")
    db.session.commit()
    print("Updated.")


with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True)
