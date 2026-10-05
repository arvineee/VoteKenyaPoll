# People's Pick – paid presidential poll (Flask + IntaSend)

## Run locally
    python -m venv venv && source venv/bin/activate
    pip install -r requirements.txt
    cp .env.example .env        # add your IntaSend SANDBOX keys
    flask --app app add-candidate   # repeat for each candidate
    flask --app app run

## Go live
1. Create IntaSend account, get live keys, set INTASEND_TEST=false.
2. In IntaSend dashboard set webhook URL: https://YOUR-DOMAIN/webhook/intasend
   with the same challenge string as INTASEND_WEBHOOK_CHALLENGE.
3. Deploy: gunicorn app:app (Render, Railway, PythonAnywhere).

## Improve later
- One-vote-per-phone rule (query Payment by phone)
- Admin dashboard + login, payment export
- Rate limiting (Flask-Limiter), CAPTCHA
- Postgres via DATABASE_URL, candidate photos

## Candidate photos and party
- Put image files in `static/candidates/` (square, 400x400 works well) and enter the filename when adding a candidate, or paste a full https image URL.
- Party colour is a hex code (e.g. #F5B700) and tints the photo ring, party dot and result bar.
- Change a photo later: `flask --app app set-photo`
- Upgrading an existing database: delete `instance/poll.db` (if no real votes yet), or run
  `ALTER TABLE candidate ADD COLUMN photo VARCHAR(300) DEFAULT ''; ALTER TABLE candidate ADD COLUMN party_color VARCHAR(9) DEFAULT '#0B5D3B';`

## Admin area
Set `ADMIN_PASSWORD` in `.env`, then open `/admin`. You can add, edit, hide and remove candidates (with photo upload), see revenue, votes and standings, and search, filter and export all payments to CSV. Use HTTPS in production and keep the password long.
