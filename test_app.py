import pytest

from app import create_app, is_valid_url


@pytest.fixture
def client(tmp_path):
    app = create_app(str(tmp_path / "test.db"))
    app.config["TESTING"] = True
    return app.test_client()


def test_is_valid_url():
    assert is_valid_url("https://example.com")
    assert not is_valid_url("javascript:alert(1)")
    assert not is_valid_url("not a url")


def test_shorten_redirect_and_count(client):
    resp = client.post("/", data={"url": "https://example.com/page"})
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    code = html.split('href="http://localhost/')[1].split('"')[0]

    follow = client.get(f"/{code}")
    assert follow.status_code == 302
    assert follow.headers["Location"] == "https://example.com/page"

    stats = client.get("/stats").get_data(as_text=True)
    assert code in stats
    assert "<td>1</td>" in stats


def test_invalid_url_shows_error(client):
    resp = client.post("/", data={"url": "ftp://nope"})
    assert "valid http(s) URL" in resp.get_data(as_text=True)


def test_unknown_code_is_404(client):
    assert client.get("/doesnotexist").status_code == 404


def test_custom_alias(client):
    client.post("/", data={"url": "https://example.com", "alias": "my-link"})
    assert client.get("/my-link").headers["Location"] == "https://example.com"


def test_duplicate_and_reserved_alias_rejected(client):
    client.post("/", data={"url": "https://example.com", "alias": "taken"})
    dup = client.post("/", data={"url": "https://example.org", "alias": "taken"})
    assert "already taken" in dup.get_data(as_text=True)
    reserved = client.post("/", data={"url": "https://example.org", "alias": "stats"})
    assert "already taken" in reserved.get_data(as_text=True)


def test_invalid_alias_rejected(client):
    resp = client.post("/", data={"url": "https://example.com", "alias": "a b"})
    assert "Alias must be" in resp.get_data(as_text=True)


def test_api_create_and_get_link(client):
    resp = client.post("/api/links", json={"url": "https://example.com/docs", "alias": "docs"})
    assert resp.status_code == 201
    assert resp.get_json() == {
        "code": "docs",
        "url": "https://example.com/docs",
        "short_url": "http://localhost/docs",
        "clicks": 0,
        "expires_at": None,
        "expired": False,
    }
    client.get("/docs")
    assert client.get("/api/links/docs").get_json()["clicks"] == 1


def test_api_generates_code_without_alias(client):
    data = client.post("/api/links", json={"url": "https://example.com"}).get_json()
    assert len(data["code"]) == 6
    assert client.get(f"/{data['code']}").headers["Location"] == "https://example.com"


def test_api_errors(client):
    assert client.post("/api/links", json={"url": "nope"}).status_code == 400
    assert client.post("/api/links", data="not json").status_code == 400
    client.post("/api/links", json={"url": "https://example.com", "alias": "dup"})
    assert client.post("/api/links", json={"url": "https://example.org", "alias": "dup"}).status_code == 409
    assert client.post("/api/links", json={"url": "https://example.org", "alias": "api"}).status_code == 409
    assert client.get("/api/links/missing").status_code == 404


def test_link_expires(tmp_path):
    from datetime import datetime, timedelta, timezone

    app = create_app(str(tmp_path / "test.db"))
    start = datetime(2026, 10, 24, tzinfo=timezone.utc)
    app.config["NOW"] = lambda: start
    client = app.test_client()
    client.post("/api/links", json={"url": "https://example.com", "alias": "soon", "expires_in_days": 2})
    assert client.get("/soon").status_code == 302
    app.config["NOW"] = lambda: start + timedelta(days=2)
    assert client.get("/soon").status_code == 410
    data = client.get("/api/links/soon").get_json()
    assert data["expires_at"] == "2026-10-26T00:00:00+00:00"
    assert data["expired"] is True


def test_expiry_validation(client):
    for value in [0, 366, "3", 2.5, True]:
        resp = client.post("/api/links", json={"url": "https://example.com", "expires_in_days": value})
        assert resp.status_code == 400, value
    created = client.post("/api/links", json={"url": "https://example.com"}).get_json()
    assert created["expires_at"] is None and created["expired"] is False


def test_old_database_gets_expiry_column(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE links (code TEXT PRIMARY KEY, url TEXT NOT NULL, clicks INTEGER NOT NULL DEFAULT 0)")
    con.execute("INSERT INTO links (code, url) VALUES ('old', 'https://example.com')")
    con.commit()
    con.close()
    client = create_app(str(db)).test_client()
    assert client.get("/old").status_code == 302


def test_form_expiry_and_stats(tmp_path):
    from datetime import datetime, timedelta, timezone

    app = create_app(str(tmp_path / "test.db"))
    start = datetime(2026, 10, 24, tzinfo=timezone.utc)
    app.config["NOW"] = lambda: start
    client = app.test_client()
    client.post("/", data={"url": "https://example.com", "alias": "week", "expires_in_days": "7"})
    client.post("/", data={"url": "https://example.org", "alias": "keep", "expires_in_days": ""})
    page = client.get("/stats").get_data(as_text=True)
    assert "2026-10-31" in page and "never" in page and "expired</span>" not in page
    app.config["NOW"] = lambda: start + timedelta(days=8)
    assert "expired</span>" in client.get("/stats").get_data(as_text=True)
    assert client.get("/week").status_code == 410
    assert client.get("/keep").status_code == 302
