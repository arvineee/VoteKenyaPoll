"""Security headers (CSP with per-request nonce, HSTS, framing, MIME, referrer, permissions),
no-index/no-store for private paths, and a cross-site guard for admin POSTs."""
import secrets
from urllib.parse import urlparse

from flask import abort, g, request

PRIVATE_PREFIXES = ("/admin", "/vote", "/webhooks", "/api")


def build_csp(nonce, secure):
    parts = [
        "default-src 'self'",
        f"script-src 'self' 'nonce-{nonce}'",
        f"style-src 'self' 'nonce-{nonce}' https://fonts.googleapis.com",
        "style-src-attr 'unsafe-inline'",          # style="..." attributes only; no inline <style> without the nonce
        "font-src https://fonts.gstatic.com",
        "img-src 'self' data: https:",              # candidate photos may be https links
        "connect-src 'self'",
        "form-action 'self'",
        "base-uri 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
    ]
    if secure:
        parts.append("upgrade-insecure-requests")
    return "; ".join(parts)


def register(app):
    @app.before_request
    def _nonce_and_origin_guard():
        g.csp_nonce = secrets.token_urlsafe(16)
        if request.method == "POST" and request.path.startswith("/admin"):
            origin = request.headers.get("Origin") or request.referrer
            if origin and urlparse(origin).netloc != request.host:
                abort(403)

    @app.context_processor
    def _inject_nonce():
        return {"csp_nonce": g.get("csp_nonce", "")}

    @app.after_request
    def _security_headers(resp):
        h, secure = resp.headers, request.is_secure
        h["Content-Security-Policy"] = build_csp(g.get("csp_nonce", ""), secure)
        if secure:
            h["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "strict-origin-when-cross-origin"
        h["Permissions-Policy"] = ("accelerometer=(), camera=(), geolocation=(), gyroscope=(), "
                                   "magnetometer=(), microphone=(), payment=(), usb=()")
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        h["Cross-Origin-Resource-Policy"] = "same-site"
        h["X-Permitted-Cross-Domain-Policies"] = "none"
        if request.path.startswith(PRIVATE_PREFIXES):
            h["X-Robots-Tag"] = "noindex, nofollow, noarchive"
            h["Cache-Control"] = "no-store"
        if request.path in ("/robots.txt", "/sitemap.xml", "/site.webmanifest"):
            h["Cache-Control"] = "public, max-age=3600"
        return resp
