"""Tiny automatic upgrade for databases created by earlier versions (adds missing columns,
back-fills references, normalises payment states). Safe to run on every start."""
import secrets
from sqlalchemy import inspect, text


def upgrade_schema(db):
    insp = inspect(db.engine)
    tables = set(insp.get_table_names())
    cols = lambda t: {c["name"] for c in insp.get_columns(t)}
    with db.engine.begin() as conn:
        if "candidate" in tables:
            have = cols("candidate")
            if "photo" not in have:
                conn.execute(text("ALTER TABLE candidate ADD COLUMN photo VARCHAR(300) DEFAULT ''"))
            if "party_color" not in have:
                conn.execute(text("ALTER TABLE candidate ADD COLUMN party_color VARCHAR(9) DEFAULT '#0B5D3B'"))
        if "payment" in tables:
            have = cols("payment")
            if "email" not in have:
                conn.execute(text("ALTER TABLE payment ADD COLUMN email VARCHAR(160)"))
            if "note" not in have:
                conn.execute(text("ALTER TABLE payment ADD COLUMN note VARCHAR(300) DEFAULT ''"))
            if "ref" not in have:
                conn.execute(text("ALTER TABLE payment ADD COLUMN ref VARCHAR(32)"))
            for (pid,) in conn.execute(text("SELECT id FROM payment WHERE ref IS NULL")).fetchall():
                conn.execute(text("UPDATE payment SET ref=:r WHERE id=:i"),
                             {"r": "V" + secrets.token_hex(7).upper(), "i": pid})
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_payment_ref ON payment (ref)"))
            conn.execute(text("UPDATE payment SET state='paid' WHERE state='COMPLETE'"))
            conn.execute(text("UPDATE payment SET state='failed' WHERE state='FAILED'"))
            conn.execute(text("UPDATE payment SET state='pending' WHERE state IN ('PENDING','PROCESSING')"))

