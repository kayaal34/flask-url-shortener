# Shorty – Flask URL shortener

A small URL-shortener website built with **Flask** and **SQLite**.

- Shorten any `http(s)` link to a random 6-character code
- Visiting `/<code>` redirects to the original URL
- Optionally pick your own alias (e.g. `/my-link`)
- Links can expire: `"expires_in_days": 7` in the API (or the form field) makes the link return `410 Gone` after a week
- Add `+` to a short link (`/my-link+`) to see where it goes before following it; previews don't count as clicks
- `/stats` lists the most clicked links

## JSON API

```bash
curl -X POST http://127.0.0.1:5000/api/links -H "Content-Type: application/json" -d '{"url": "https://example.com", "alias": "ex"}'
# {"code": "ex", "url": "https://example.com", "short_url": "http://127.0.0.1:5000/ex", "clicks": 0}

curl http://127.0.0.1:5000/api/links/ex
```

Clicks per day for the last N days (1-90, default 7), with 0 for days without clicks:

```bash
curl "http://127.0.0.1:5000/api/links/ex/clicks?days=3"
# {"code": "ex", "days": [{"day": "2026-10-23", "clicks": 0}, ...], "total": 5}
```

Creating a link (in the API or the form) returns a `delete_token`, shown only once.
Only a SHA-256 hash of it is stored. Use it to delete the link and its click history:

```bash
curl -X DELETE http://127.0.0.1:5000/api/links/ex -H "Authorization: Bearer <delete_token>"
```

Links created before tokens existed can't be deleted through the API.

Errors come back as `{"error": "..."}` with status 400 (invalid input), 404 (unknown code) or 409 (alias taken).

## Run locally

```bash
pip install -r requirements.txt
python app.py
```

Then open http://127.0.0.1:5000.

## Tests

```bash
pip install pytest
pytest
```

![tests](https://github.com/kayaal34/flask-url-shortener/actions/workflows/tests.yml/badge.svg)
