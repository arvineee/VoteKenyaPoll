import os, re, io, csv, uuid, hmac, secrets, time
from datetime import datetime
from functools import wraps

from flask import (Flask, jsonify, render_template, request, abort, url_for,
                   session, redirect, flash, Response)
from sqlalchemy import func
from werkzeug.utils import secure_filename

import payments, security, seo
from schema import upgrade_schema
from config import Config
from models import db, Candidate, Payment, create_vote, get_by_ref

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)
if Config.TRUST_PROXY:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
security.register(app)       # must come first: it creates the per-request CSP nonce
seo.register(app)

ADMIN_PASSWORD = Config.ADMIN_PASSWORD
PRICE = Config.VOTE_PRICE_KES
UPLOAD_DIR = os.path.join(app.root_path, "static", "candidates")
ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def photo_src(c):
    if not c.photo:
        return ""
    if c.photo.startswith("http"):
        return c.photo
    return url_for("static", filename="candidates/" + c.photo)


def tally():
    rows = dict(
        db.session.query(Payment.candidate_id, func.count(Payment.id))
        .filter(Payment.state == "paid").group_by(Payment.candidate_id).all()
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
    return render_template("index.html", results=results, total=total, price=PRICE,
                           enabled=payments.is_configured(), faq=seo.FAQ,
                           schema=seo.home_schema(results))


@app.get("/api/results")
def api_results():
    results, total = tally()
    return jsonify(results=results, total=total)


@app.post("/api/vote")
def vote():
    data = request.get_json(silent=True) or {}
    cand = db.session.get(Candidate, data.get("candidate_id"))
    phone = payments.normalize_phone(data.get("phone"))
    email = (data.get("email") or "").strip()
    if not cand or not cand.active:
        return jsonify(error="Choose a candidate."), 400
    if not EMAIL_RE.match(email):
        return jsonify(error="Enter a valid email address."), 400
    if not phone:
        return jsonify(error="Enter a valid Kenyan mobile number, e.g. 0712 345 678."), 400
    if not payments.is_configured():
        return jsonify(error="Voting is not available right now."), 503

    # The amount always comes from the server, never from the browser.
    p = create_vote(cand.id, phone, email, PRICE)
    return_url = f"{Config.SITE_URL}{url_for('vote_return', ref=p.ref)}"
    try:
        url = payments.create_checkout(p, cand.name, return_url)
    except payments.PaymentError as exc:
        p.state, p.note = "failed", str(exc)[:300]
        db.session.commit()
        return jsonify(error="We could not start the payment. Please try again."), 502
    return jsonify(url=url)


@app.get("/vote/return")
def vote_return():
    p = get_by_ref(request.args.get("ref", ""))
    if not p:
        abort(404)
    cand = db.session.get(Candidate, p.candidate_id)
    return render_template("vote_status.html", p=p, cand=cand)


@app.get("/vote/status/<ref>")
def vote_status(ref):
    p = get_by_ref(ref)
    if not p:
        abort(404)
    return jsonify(status=p.state)


@app.post("/webhooks/intasend")
def intasend_webhook():
    expected = Config.INTASEND_WEBHOOK_CHALLENGE
    if not expected:
        return jsonify(ok=False, error="webhook not configured"), 503
    payload = request.get_json(silent=True) or request.form.to_dict() or {}
    if not hmac.compare_digest(str(payload.get("challenge", "")), expected):
        return jsonify(ok=False, error="bad challenge"), 403
    try:
        result = payments.handle_webhook(payload)
    except payments.PaymentError:
        return jsonify(ok=False), 502          # IntaSend will retry
    app.logger.info("IntaSend webhook for %s: %s", payload.get("api_ref"), result)
    return jsonify(ok=True)


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
    complete = Payment.query.filter_by(state="paid")
    revenue = db.session.query(func.coalesce(func.sum(Payment.amount), 0)).filter(Payment.state == "paid").scalar()
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    stats = {
        "revenue": revenue, "votes": complete.count(),
        "today": complete.filter(Payment.created_at >= today).count(),
        "pending": count("pending"), "failed": count("failed"), "review": count("review"),
        "voters": db.session.query(func.count(func.distinct(Payment.phone))).filter(Payment.state == "paid").scalar(),
    }
    results, total = tally()
    names = {c.id: c.name for c in Candidate.query.all()}
    recent = Payment.query.order_by(Payment.created_at.desc()).limit(10).all()
    return render_template("admin_dashboard.html", stats=stats, results=results, recent=recent, names=names,
                           problem=payments.config_problem())


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
    if term: q = q.filter(Payment.phone.contains(term) | Payment.ref.contains(term) | Payment.invoice_id.contains(term) | Payment.email.contains(term))
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
    w.writerow(["time_utc", "ref", "invoice_id", "candidate", "phone", "email", "amount", "state", "note"])
    names = {c.id: c.name for c in Candidate.query.all()}
    for p in payments_query():
        w.writerow([p.created_at, p.ref, p.invoice_id, names.get(p.candidate_id), p.phone, p.email, p.amount, p.state, p.note])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=payments.csv"})


ERRORS = {400: ("Request not accepted", "Please go back and try again."),
          403: ("Not allowed", "You do not have access to this page."),
          404: ("Page not found", "That page does not exist."),
          413: ("File too large", "Photos must be 3 MB or smaller."),
          500: ("Something went wrong", "Please try again in a moment.")}


def _error(e):
    code = getattr(e, "code", 500)
    title, message = ERRORS.get(code, ERRORS[500])
    return render_template("error.html", title=title, message=message), code


for _c in ERRORS:
    app.register_error_handler(_c, _error)


@app.cli.command("check-payments")
def check_payments():
    """Check the IntaSend settings for common mistakes."""
    mode = "sandbox (test)" if Config.INTASEND_TEST_MODE else "live"
    print("Mode:", mode)
    print("Secret key starts with:", (Config.INTASEND_SECRET_KEY or "-")[:18])
    print("Publishable key starts with:", (Config.INTASEND_PUBLISHABLE_KEY or "-")[:16])
    print("Site URL:", Config.SITE_URL)
    print("Webhook challenge set:", bool(Config.INTASEND_WEBHOOK_CHALLENGE))
    print("Problem:", payments.config_problem() or "none found")


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
    upgrade_schema(db)

if __name__ == "__main__":
    app.run(debug=True)

