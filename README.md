# Shorty – Flask URL shortener

A small URL-shortener website built with **Flask** and **SQLite**.

- Shorten any `http(s)` link to a random 6-character code
- Visiting `/<code>` redirects to the original URL
- Optionally pick your own alias (e.g. `/my-link`)
- `/stats` lists the most clicked links

## JSON API

```bash
curl -X POST http://127.0.0.1:5000/api/links -H "Content-Type: application/json" -d '{"url": "https://example.com", "alias": "ex"}'
# {"code": "ex", "url": "https://example.com", "short_url": "http://127.0.0.1:5000/ex", "clicks": 0}

curl http://127.0.0.1:5000/api/links/ex
```

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
