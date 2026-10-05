# People's Pick – paid presidential poll (Flask + IntaSend)

## Run locally
    python -m venv venv && source venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env        # add your IntaSend SANDBOX keys
    flask --app app add-candidate   # or add them in /admin
    flask --app app run

## IntaSend setup (same as the Arval site)
1. intasend.com -> Developers -> API keys. Start with the sandbox keys.
2. Set `INTASEND_SECRET_KEY`, `INTASEND_PUBLISHABLE_KEY`, `INTASEND_TEST_MODE=true`, and `SITE_URL`.
3. Dashboard -> Webhooks -> add `https://<your-site>/webhooks/intasend` with a challenge string, and put the
   same string in `INTASEND_WEBHOOK_CHALLENGE`.
4. Going live: swap in live keys, set `INTASEND_TEST_MODE=false`, `SESSION_COOKIE_SECURE=true`, deploy with `gunicorn app:app`.

Flow: voter enters phone + email -> hosted IntaSend checkout (M-Pesa or card) -> IntaSend calls the webhook ->
we check the challenge, re-fetch the invoice from IntaSend, check the amount, then count the vote. A vote only
counts when the vote status is `paid`. On your own computer the webhook cannot reach you; use a tunnel such as
ngrok for `SITE_URL` and the webhook while testing.

## Improve later
- One-vote-per-phone rule, rate limiting, CAPTCHA, Postgres, email receipts

## Candidate photos and party
- Put image files in `static/candidates/` (square, 400x400 works well) and enter the filename when adding a candidate, or paste a full https image URL.
- Party colour is a hex code (e.g. #F5B700) and tints the photo ring, party dot and result bar.
- Change a photo later: `flask --app app set-photo`
- Upgrading from an earlier version of this project: the payment table changed, so delete `instance/poll.db` first (only if there are no real votes yet).

## Admin area
Set `ADMIN_PASSWORD` in `.env`, then open `/admin`. You can add, edit, hide and remove candidates (with photo upload), see revenue, votes and standings, and search, filter and export all payments to CSV. Use HTTPS in production and keep the password long.

## Logo, SEO and security
- **Logo:** `static/logo-mark.svg` (mark), `static/logo.svg` (mark + name), favicons and app icons in `static/`. Replace these files to rebrand; keep the same names. `static/og-image.png` (1200x630) is the link preview image.
- **SEO:** every public page gets a title, description, canonical URL, Open Graph and Twitter tags, and JSON-LD (Organization, WebSite, WebPage, FAQ, candidate list). `/robots.txt`, `/sitemap.xml` and `/site.webmanifest` are generated. Set `SITE_URL` to your real domain, then submit the sitemap in Google Search Console. Admin and vote-status pages are `noindex`.
- **Security headers:** Content-Security-Policy with a per-request nonce, HSTS (HTTPS only), X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy, COOP/CORP, and `no-store` on private pages. Admin POSTs from other sites are rejected. Set `TRUST_PROXY=true` behind a proxy so HTTPS is detected, and `SECURITY_CONTACT=mailto:you@example.com` to publish `/.well-known/security.txt`.
- Inline scripts need `nonce="{{ csp_nonce }}"` or the browser will block them.
