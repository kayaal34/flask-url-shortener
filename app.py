"""Minimal URL shortener built with Flask and SQLite."""

import secrets
import sqlite3
import string
from urllib.parse import urlparse

from flask import Flask, abort, g, redirect, render_template, request, url_for

ALPHABET = string.ascii_letters + string.digits
CODE_LENGTH = 6


def create_app(database="links.db"):
    app = Flask(__name__)
    app.config["DATABASE"] = database

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
        get_db().commit()

    @app.route("/", methods=["GET", "POST"])
    def index():
        short_url = error = None
        if request.method == "POST":
            url = request.form.get("url", "").strip()
            if not is_valid_url(url):
                error = "Please enter a valid http(s) URL."
            else:
                code = new_code(get_db())
                get_db().execute("INSERT INTO links (code, url) VALUES (?, ?)", (code, url))
                get_db().commit()
                short_url = url_for("follow", code=code, _external=True)
        return render_template("index.html", short_url=short_url, error=error)

    @app.route("/stats")
    def stats():
        rows = get_db().execute(
            "SELECT code, url, clicks FROM links ORDER BY clicks DESC, code LIMIT 50"
        ).fetchall()
        return render_template("stats.html", links=rows)

    @app.route("/<code>")
    def follow(code):
        db = get_db()
        row = db.execute("SELECT url FROM links WHERE code = ?", (code,)).fetchone()
        if row is None:
            abort(404)
        db.execute("UPDATE links SET clicks = clicks + 1 WHERE code = ?", (code,))
        db.commit()
        return redirect(row["url"])

    return app


def is_valid_url(url):
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def new_code(db):
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        if db.execute("SELECT 1 FROM links WHERE code = ?", (code,)).fetchone() is None:
            return code


if __name__ == "__main__":
    create_app().run(debug=True)
