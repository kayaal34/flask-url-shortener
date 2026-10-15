# Shorty – Flask URL shortener

A small URL-shortener website built with **Flask** and **SQLite**.

- Shorten any `http(s)` link to a random 6-character code
- Visiting `/<code>` redirects to the original URL
- Optionally pick your own alias (e.g. `/my-link`)
- `/stats` lists the most clicked links

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
