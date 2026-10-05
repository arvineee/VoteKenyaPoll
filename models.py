from datetime import datetime
import secrets
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Candidate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    party = db.Column(db.String(120), default="")
    slogan = db.Column(db.String(200), default="")
    photo = db.Column(db.String(300), default="")  # filename in static/candidates/ or a full https URL
    party_color = db.Column(db.String(9), default="#0B5D3B")
    active = db.Column(db.Boolean, default=True)


class Payment(db.Model):
    """One row per vote attempt. A vote counts only when state == 'paid'.
    States: pending -> paid | failed | review (paid amount too low, check by hand)."""
    id = db.Column(db.Integer, primary_key=True)
    ref = db.Column(db.String(32), unique=True, index=True, nullable=False)   # sent to IntaSend as api_ref
    invoice_id = db.Column(db.String(64), index=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidate.id"), nullable=False)
    phone = db.Column(db.String(15))
    email = db.Column(db.String(160))
    amount = db.Column(db.Integer, nullable=False)
    state = db.Column(db.String(12), default="pending", index=True)
    note = db.Column(db.String(300), default="")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


def create_vote(candidate_id, phone, email, amount):
    p = Payment(ref="V" + secrets.token_hex(7).upper(), candidate_id=candidate_id,
                phone=phone, email=email, amount=amount)
    db.session.add(p)
    db.session.commit()
    return p


def get_by_ref(ref):
    return Payment.query.filter_by(ref=ref).first() if ref else None


def settle(ref, state, invoice_id="", note=""):
    """Move a payment to a new state. A paid vote is never downgraded.
    Returns True only the first time it becomes 'paid' (so the vote is counted once)."""
    p = get_by_ref(ref)
    if not p or p.state == "paid":
        return False
    p.state, p.note = state, (note or "")[:300]
    if invoice_id:
        p.invoice_id = invoice_id
    db.session.commit()
    return state == "paid"
