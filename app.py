"""Minimal URL shortener built with Flask and SQLite."""

import hashlib
import hmac
import re
import secrets
import sqlite3
import string
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from flask import Flask, abort, g, jsonify, redirect, render_template, request, url_for

ALPHABET = string.ascii_letters + string.digits
CODE_LENGTH = 6
RESERVED = {"stats", "static", "api"}
MAX_EXPIRY_DAYS = 365


def create_app(database="links.db"):
    app = Flask(__name__)
    app.config["DATABASE"] = database
    # Clock used for link expiry; tests replace it to move time forward.
    app.config["NOW"] = lambda: datetime.now(timezone.utc)

    def get_db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DATABASE"])
            g.db.row_factory = sqlite3.Row
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    with app.app_context():
        get_db().execute(
            """CREATE TABLE IF NOT EXISTS links (
                   code   TEXT PRIMARY KEY,
                   url    TEXT NOT NULL,
                   clicks INTEGER NOT NULL DEFAULT 0
               )"""
        )
        get_db().execute(
            """CREATE TABLE IF NOT EXISTS daily_clicks (
                   code   TEXT NOT NULL,
                   day    TEXT NOT NULL,
                   clicks INTEGER NOT NULL DEFAULT 0,
                   PRIMARY KEY (code, day)
               )"""
        )
        columns = {row["name"] for row in get_db().execute("PRAGMA table_info(links)")}
        if "expires_at" not in columns:  # databases created before links could expire
            get_db().execute("ALTER TABLE links ADD COLUMN expires_at TEXT")
        if "token_hash" not in columns:  # links created before deletion existed have no token
            get_db().execute("ALTER TABLE links ADD COLUMN token_hash TEXT")
        get_db().commit()

    def now():
        return app.config["NOW"]()

    @app.route("/", methods=["GET", "POST"])
    def index():
        short_url = error = delete_token = None
        if request.method == "POST":
            url = request.form.get("url", "").strip()
            alias = request.form.get("alias", "").strip()
            expires = request.form.get("expires_in_days", "").strip()
            code, error = shorten(url, alias, int(expires) if expires.isdigit() else None)
            if code:
                short_url = url_for("follow", code=code, _external=True)
                delete_token = g.last_token
        return render_template("index.html", short_url=short_url, error=error, delete_token=delete_token)

    def shorten(url, alias="", expires_in_days=None):
        """Store a new link. Returns (code, None) on success or (None, error)."""
        db = get_db()
        if not is_valid_url(url):
            return None, "Please enter a valid http(s) URL."
        if expires_in_days is not None and not 1 <= expires_in_days <= MAX_EXPIRY_DAYS:
            return None, f"Expiry must be between 1 and {MAX_EXPIRY_DAYS} days."
        if alias and not is_valid_alias(alias):
            return None, "Alias must be 3-30 letters, digits, '-' or '_'."
        if alias and (alias in RESERVED or code_exists(db, alias)):
            return None, f"The alias '{alias}' is already taken."
        code = alias or new_code(db)
        expires_at = None
        if expires_in_days is not None:
            expires_at = (now() + timedelta(days=expires_in_days)).isoformat(timespec="seconds")
        token = secrets.token_urlsafe(24)
        db.execute(
            "INSERT INTO links (code, url, expires_at, token_hash) VALUES (?, ?, ?, ?)",
            (code, url, expires_at, hash_token(token)),
        )
        db.commit()
        g.last_token = token  # handed back once, only to whoever created the link
        return code, None

    def link_json(code, url, clicks, expires_at=None):
        return {
            "code": code,
            "url": url,
            "short_url": url_for("follow", code=code, _external=True),
            "clicks": clicks,
            "preview_url": url_for("preview", code=code, _external=True),
            "expires_at": expires_at,
            "expired": is_expired(expires_at, now()),
        }

    @app.post("/api/links")
    def api_create_link():
        data = request.get_json(silent=True) or {}
        expires = data.get("expires_in_days")
        if expires is not None and (isinstance(expires, bool) or not isinstance(expires, int)):
            return jsonify(error="expires_in_days must be a whole number of days."), 400
        code, error = shorten(str(data.get("url", "")).strip(), str(data.get("alias", "")).strip(), expires)
        if error:
            status = 409 if "already taken" in error else 400
            return jsonify(error=error), status
        row = get_db().execute("SELECT expires_at FROM links WHERE code = ?", (code,)).fetchone()
        body = link_json(code, data["url"].strip(), 0, row["expires_at"])
        body["delete_token"] = g.last_token
        return jsonify(body), 201

    @app.delete("/api/links/<code>")
    def api_delete_link(code):
        db = get_db()
        row = db.execute("SELECT token_hash FROM links WHERE code = ?", (code,)).fetchone()
        if row is None:
            return jsonify(error="Not found"), 404
        auth = request.headers.get("Authorization", "")
        token = auth[len("Bearer "):] if auth.startswith("Bearer ") else ""
        if not row["token_hash"] or not token or not hmac.compare_digest(hash_token(token), row["token_hash"]):
            return jsonify(error="A valid delete token is required."), 403
        db.execute("DELETE FROM daily_clicks WHERE code = ?", (code,))
        db.execute("DELETE FROM links WHERE code = ?", (code,))
        db.commit()
        return "", 204

    @app.get("/api/links/<code>")
    def api_get_link(code):
        row = get_db().execute(
            "SELECT code, url, clicks, expires_at FROM links WHERE code = ?", (code,)
        ).fetchone()
        if row is None:
            return jsonify(error="Not found"), 404
        return jsonify(link_json(row["code"], row["url"], row["clicks"], row["expires_at"]))

    @app.get("/api/links/<code>/clicks")
    def api_daily_clicks(code):
        db = get_db()
        if not code_exists(db, code):
            return jsonify(error="Not found"), 404
        days = request.args.get("days", "7")
        if not days.isdigit() or not 1 <= int(days) <= 90:
            return jsonify(error="days must be a whole number from 1 to 90."), 400
        today = now().date()
        window = [(today - timedelta(days=offset)).isoformat() for offset in range(int(days) - 1, -1, -1)]
        counts = dict(
            db.execute(
                "SELECT day, clicks FROM daily_clicks WHERE code = ? AND day >= ?",
                (code, window[0]),
            ).fetchall()
        )
        series = [{"day": day, "clicks": counts.get(day, 0)} for day in window]
        return jsonify(code=code, days=series, total=sum(item["clicks"] for item in series))

    @app.route("/stats")
    def stats():
        rows = get_db().execute(
            "SELECT code, url, clicks, expires_at FROM links ORDER BY clicks DESC, code LIMIT 50"
        ).fetchall()
        links = [dict(row, expired=is_expired(row["expires_at"], now())) for row in rows]
        return render_template("stats.html", links=links)

    @app.route("/<code>+")
    def preview(code):
        """Show where a short link goes without following it (or counting a click)."""
        row = get_db().execute(
            "SELECT code, url, clicks, expires_at FROM links WHERE code = ?", (code,)
        ).fetchone()
        if row is None:
            abort(404)
        link = dict(row, expired=is_expired(row["expires_at"], now()))
        return render_template("preview.html", link=link, host=urlparse(row["url"]).netloc)

    @app.route("/<code>")
    def follow(code):
        db = get_db()
        row = db.execute("SELECT url, expires_at FROM links WHERE code = ?", (code,)).fetchone()
        if row is None:
            abort(404)
        if is_expired(row["expires_at"], now()):
            abort(410)
        db.execute("UPDATE links SET clicks = clicks + 1 WHERE code = ?", (code,))
        db.execute(
            """INSERT INTO daily_clicks (code, day, clicks) VALUES (?, ?, 1)
               ON CONFLICT (code, day) DO UPDATE SET clicks = clicks + 1""",
            (code, now().date().isoformat()),
        )
        db.commit()
        return redirect(row["url"])

    return app


def is_valid_url(url):
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def hash_token(token):
    """Only a hash of the delete token is stored, so a leaked database can't delete links."""
    return hashlib.sha256(token.encode()).hexdigest()


def is_expired(expires_at, now):
    return expires_at is not None and datetime.fromisoformat(expires_at) <= now


def is_valid_alias(alias):
    return re.fullmatch(r"[A-Za-z0-9_-]{3,30}", alias) is not None


def code_exists(db, code):
    return db.execute("SELECT 1 FROM links WHERE code = ?", (code,)).fetchone() is not None


def new_code(db):
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        if not code_exists(db, code):
            return code


if __name__ == "__main__":
    create_app().run(debug=True)
