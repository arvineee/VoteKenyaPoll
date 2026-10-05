"""IntaSend payments through IntaSend's hosted checkout (M-Pesa STK Push + card).
Same flow as the Arval site:
  1. Voter submits phone + email -> we save a pending vote and call collect.checkout(...),
     which returns a hosted payment URL. The voter is redirected there.
  2. After paying, IntaSend sends the voter back to /vote/return?ref=<ref>.
  3. IntaSend calls our webhook. We check the shared challenge, then ask IntaSend for the invoice
     itself (we never trust the webhook body alone), check the amount, and count the vote.
"""
import re
from decimal import Decimal, InvalidOperation

from flask import current_app

import models
from config import Config as cfg


class PaymentError(Exception):
    """Raised when IntaSend can't be reached or refuses a request. Message is safe to log."""


def is_configured():
    return bool(cfg.INTASEND_SECRET_KEY and cfg.INTASEND_PUBLISHABLE_KEY)


def normalize_phone(raw):
    """'0712 345 678' / '+254712345678' / '712345678' -> '254712345678', or None if invalid."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("254"):
        pass
    elif len(digits) == 10 and digits.startswith("0"):
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "71":
        digits = "254" + digits
    else:
        return None
    return digits if digits[3] in "71" else None


def _service():
    try:
        from intasend import APIService
    except ImportError as exc:
        raise PaymentError("intasend-python is not installed (pip install intasend-python)") from exc
    return APIService(token=cfg.INTASEND_SECRET_KEY,
                      publishable_key=cfg.INTASEND_PUBLISHABLE_KEY,
                      test=cfg.INTASEND_TEST_MODE)


def create_checkout(payment, candidate_name, redirect_url):
    """Ask IntaSend for a hosted checkout URL for this vote and return it."""
    try:
        resp = _service().collect.checkout(
            phone_number=payment.phone, email=payment.email, amount=payment.amount,
            currency=cfg.PAYMENT_CURRENCY, comment=f"Poll vote: {candidate_name} ({payment.ref})",
            redirect_url=redirect_url, api_ref=payment.ref,
            first_name="Voter", last_name="",
        )
    except Exception as exc:
        current_app.logger.exception("IntaSend checkout failed for %s", payment.ref)
        raise PaymentError(f"IntaSend checkout failed: {exc}") from exc
    url = (resp or {}).get("url")
    if not url:
        raise PaymentError(f"IntaSend returned no checkout URL: {resp}")
    return url


def fetch_invoice(invoice_id):
    """The authoritative invoice record from IntaSend (state, value, api_ref, ...)."""
    try:
        resp = _service().collect.status(invoice_id=invoice_id)
    except Exception as exc:
        current_app.logger.exception("IntaSend status check failed for %s", invoice_id)
        raise PaymentError(f"IntaSend status check failed: {exc}") from exc
    return (resp or {}).get("invoice") or resp or {}


def _to_decimal(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return Decimal(0)


def handle_webhook(payload):
    """Verify a (challenge-checked) webhook against IntaSend and settle the matching vote.
    Raises PaymentError if IntaSend can't be reached; the caller answers 5xx so IntaSend retries."""
    ref = str(payload.get("api_ref") or "")
    invoice_id = str(payload.get("invoice_id") or "")
    payment = models.get_by_ref(ref)
    if not payment or not invoice_id:
        return "ignored: unknown vote"

    invoice = fetch_invoice(invoice_id)
    if invoice.get("api_ref") and str(invoice["api_ref"]) != ref:
        return "ignored: invoice belongs to a different vote"

    state = str(invoice.get("state") or "").upper()
    if state == "COMPLETE":
        if _to_decimal(invoice.get("value")) < Decimal(payment.amount):
            models.settle(ref, "review", invoice_id, "Paid amount is lower than the vote price")
            return "flagged: amount mismatch"
        models.settle(ref, "paid", invoice_id)
        return "paid"
    if state == "FAILED":
        reason = invoice.get("failed_reason") or invoice.get("failed_code") or "Payment failed"
        models.settle(ref, "failed", invoice_id, str(reason))
        return "failed"
    return f"pending ({state or 'unknown'})"
