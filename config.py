"""Runtime configuration. Secrets come from environment variables (see .env.example)."""
import os
from dotenv import load_dotenv

load_dotenv()
BASE_DIR = os.path.abspath(os.path.dirname(__file__))


def _env(name, default=""):
    return os.environ.get(name, default)


def _flag(name, default=False):
    return _env(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


class Config:
    BASE_DIR = BASE_DIR
    SECRET_KEY = _env("SECRET_KEY", "dev-secret-key")
    ADMIN_PASSWORD = _env("ADMIN_PASSWORD")            # admin login is disabled while empty
    SQLALCHEMY_DATABASE_URI = _env("DATABASE_URL", "sqlite:///poll.db")
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = _flag("SESSION_COOKIE_SECURE", False)   # True once HTTPS only
    MAX_CONTENT_LENGTH = 3 * 1024 * 1024

    SITE_URL = _env("SITE_URL", "http://127.0.0.1:5000").rstrip("/")
    SITE_NAME = _env("SITE_NAME", "People's Pick")
    SITE_DESCRIPTION = ("Choose your favourite presidential candidate and cast your vote for KES 10 "
                        "with M-Pesa or card. Live results, secure payments by IntaSend.")
    TWITTER_HANDLE = _env("TWITTER_HANDLE")            # e.g. @yourhandle (optional)
    SECURITY_CONTACT = _env("SECURITY_CONTACT")        # e.g. mailto:you@example.com (optional, /.well-known/security.txt)
    TRUST_PROXY = _flag("TRUST_PROXY", False)          # True behind Nginx/Render/PythonAnywhere proxies

    # IntaSend (same setup as the Arval site)
    INTASEND_SECRET_KEY = _env("INTASEND_SECRET_KEY")             # ISSecretKey_test_... / _live_...
    INTASEND_PUBLISHABLE_KEY = _env("INTASEND_PUBLISHABLE_KEY")   # ISPubKey_test_... / _live_...
    INTASEND_TEST_MODE = _flag("INTASEND_TEST_MODE", True)
    INTASEND_WEBHOOK_CHALLENGE = _env("INTASEND_WEBHOOK_CHALLENGE")
    PAYMENT_CURRENCY = "KES"
    VOTE_PRICE_KES = int(_env("VOTE_PRICE_KES", "10") or 10)
