"""Minimal URL shortener built with Flask and SQLite."""

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
        columns = {row["name"] for row in get_db().execute("PRAGMA table_info(links)")}
        if "expires_at" not in columns:  # databases created before links could expire
            get_db().execute("ALTER TABLE links ADD COLUMN expires_at TEXT")
        get_db().commit()

    def now():
        return app.config["NOW"]()

    @app.route("/", methods=["GET", "POST"])
    def index():
        short_url = error = None
        if request.method == "POST":
            url = request.form.get("url", "").strip()
            alias = request.form.get("alias", "").strip()
            code, error = shorten(url, alias)
            if code:
                short_url = url_for("follow", code=code, _external=True)
        return render_template("index.html", short_url=short_url, error=error)

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
        db.execute("INSERT INTO links (code, url, expires_at) VALUES (?, ?, ?)", (code, url, expires_at))
        db.commit()
        return code, None

    def link_json(code, url, clicks, expires_at=None):
        return {
            "code": code,
            "url": url,
            "short_url": url_for("follow", code=code, _external=True),
            "clicks": clicks,
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
        return jsonify(link_json(code, data["url"].strip(), 0, row["expires_at"])), 201

    @app.get("/api/links/<code>")
    def api_get_link(code):
        row = get_db().execute(
            "SELECT code, url, clicks, expires_at FROM links WHERE code = ?", (code,)
        ).fetchone()
        if row is None:
            return jsonify(error="Not found"), 404
        return jsonify(link_json(row["code"], row["url"], row["clicks"], row["expires_at"]))

    @app.route("/stats")
    def stats():
        rows = get_db().execute(
            "SELECT code, url, clicks FROM links ORDER BY clicks DESC, code LIMIT 50"
        ).fetchall()
        return render_template("stats.html", links=rows)

    @app.route("/<code>")
    def follow(code):
        db = get_db()
        row = db.execute("SELECT url, expires_at FROM links WHERE code = ?", (code,)).fetchone()
        if row is None:
            abort(404)
        if is_expired(row["expires_at"], now()):
            abort(410)
        db.execute("UPDATE links SET clicks = clicks + 1 WHERE code = ?", (code,))
        db.commit()
        return redirect(row["url"])

    return app


def is_valid_url(url):
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


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
