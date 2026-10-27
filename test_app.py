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
    body = resp.get_json()
    assert len(body.pop("delete_token")) >= 32
    assert body == {
        "code": "docs",
        "url": "https://example.com/docs",
        "short_url": "http://localhost/docs",
        "clicks": 0,
        "preview_url": "http://localhost/docs+",
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


def test_clicks_are_recorded_per_day(tmp_path):
    import sqlite3
    from datetime import datetime, timedelta, timezone

    db_path = tmp_path / "test.db"
    app = create_app(str(db_path))
    day1 = datetime(2026, 10, 24, 23, 0, tzinfo=timezone.utc)
    app.config["NOW"] = lambda: day1
    client = app.test_client()
    client.post("/api/links", json={"url": "https://example.com", "alias": "daily"})
    client.get("/daily")
    client.get("/daily")
    app.config["NOW"] = lambda: day1 + timedelta(hours=2)
    client.get("/daily")
    rows = sqlite3.connect(db_path).execute(
        "SELECT day, clicks FROM daily_clicks WHERE code = 'daily' ORDER BY day"
    ).fetchall()
    assert rows == [("2026-10-24", 2), ("2026-10-25", 1)]


def test_api_daily_clicks(tmp_path):
    from datetime import datetime, timedelta, timezone

    app = create_app(str(tmp_path / "test.db"))
    start = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    app.config["NOW"] = lambda: start
    client = app.test_client()
    client.post("/api/links", json={"url": "https://example.com", "alias": "trend"})
    for offset, clicks in [(0, 2), (2, 1), (4, 3)]:
        app.config["NOW"] = lambda offset=offset: start + timedelta(days=offset)
        for _ in range(clicks):
            client.get("/trend")
    data = client.get("/api/links/trend/clicks?days=3").get_json()
    assert data == {
        "code": "trend",
        "days": [
            {"day": "2026-10-22", "clicks": 1},
            {"day": "2026-10-23", "clicks": 0},
            {"day": "2026-10-24", "clicks": 3},
        ],
        "total": 4,
    }
    assert len(client.get("/api/links/trend/clicks").get_json()["days"]) == 7


def test_api_daily_clicks_errors(client):
    assert client.get("/api/links/missing/clicks").status_code == 404
    client.post("/api/links", json={"url": "https://example.com", "alias": "abc"})
    for bad in ["0", "91", "x", "-1"]:
        assert client.get(f"/api/links/abc/clicks?days={bad}").status_code == 400, bad


def test_preview_shows_destination_without_counting(client):
    client.post("/api/links", json={"url": "https://example.com/very/long/path?q=1", "alias": "peek"})
    page = client.get("/peek+")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "example.com" in html and "https://example.com/very/long/path?q=1" in html
    assert "Clicked 0 times" in html
    assert client.get("/api/links/peek").get_json()["clicks"] == 0


def test_preview_unknown_and_expired(tmp_path):
    from datetime import datetime, timedelta, timezone

    app = create_app(str(tmp_path / "test.db"))
    start = datetime(2026, 10, 26, tzinfo=timezone.utc)
    app.config["NOW"] = lambda: start
    client = app.test_client()
    assert client.get("/nope+").status_code == 404
    client.post("/api/links", json={"url": "https://example.com", "alias": "old", "expires_in_days": 1})
    app.config["NOW"] = lambda: start + timedelta(days=2)
    html = client.get("/old+").get_data(as_text=True)
    assert "This link has expired." in html and "Continue to" not in html


def test_index_links_to_preview(client):
    html = client.post("/", data={"url": "https://example.com", "alias": "home"}).get_data(as_text=True)
    assert 'href="http://localhost/home+"' in html


def test_delete_link_with_token(client):
    token = client.post("/api/links", json={"url": "https://example.com", "alias": "gone"}).get_json()["delete_token"]
    client.get("/gone")
    assert client.delete("/api/links/gone").status_code == 403
    assert client.delete("/api/links/gone", headers={"Authorization": "Bearer wrong"}).status_code == 403
    assert client.delete("/api/links/gone", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    assert client.get("/gone").status_code == 404
    assert client.get("/api/links/gone/clicks").status_code == 404
    assert client.delete("/api/links/gone", headers={"Authorization": f"Bearer {token}"}).status_code == 404


def test_token_is_stored_hashed_and_old_links_cannot_be_deleted(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE links (code TEXT PRIMARY KEY, url TEXT NOT NULL, clicks INTEGER NOT NULL DEFAULT 0)")
    con.execute("INSERT INTO links (code, url) VALUES ('legacy', 'https://example.com')")
    con.commit()
    con.close()
    client = create_app(str(db)).test_client()
    token = client.post("/api/links", json={"url": "https://example.org", "alias": "fresh"}).get_json()["delete_token"]
    stored = sqlite3.connect(db).execute("SELECT token_hash FROM links WHERE code = 'fresh'").fetchone()[0]
    assert token not in stored and len(stored) == 64
    assert client.delete("/api/links/legacy", headers={"Authorization": f"Bearer {token}"}).status_code == 403


def test_form_shows_delete_token_once(client):
    import re as _re

    html = client.post("/", data={"url": "https://example.com", "alias": "mine"}).get_data(as_text=True)
    token = _re.search(r"<code>([^<]+)</code>", html).group(1)
    assert client.delete("/api/links/mine", headers={"Authorization": f"Bearer {token}"}).status_code == 204
    assert "<code>" not in client.get("/").get_data(as_text=True)


def test_bulk_create(client):
    resp = client.post("/api/links/bulk", json={"links": [
        {"url": "https://example.com/a", "alias": "bulk-a"},
        {"url": "not a url"},
        {"url": "https://example.com/b", "expires_in_days": 3},
        {"url": "https://example.com/c", "alias": "bulk-a"},
        "nope",
    ]})
    assert resp.status_code == 201
    data = resp.get_json()
    assert (data["created"], data["failed"]) == (2, 3)
    links = data["links"]
    assert links[0]["code"] == "bulk-a" and "delete_token" in links[0]
    assert "valid http(s) URL" in links[1]["error"]
    assert links[2]["expires_at"] is not None
    assert "already taken" in links[3]["error"]
    assert links[4] == {"error": "Each link must be an object."}
    assert client.get("/bulk-a").headers["Location"] == "https://example.com/a"


def test_bulk_create_rejects_bad_requests(client):
    assert client.post("/api/links/bulk", json={"links": []}).status_code == 400
    assert client.post("/api/links/bulk", json=[{"url": "https://example.com"}]).status_code == 400
    too_many = {"links": [{"url": f"https://example.com/{i}"} for i in range(101)]}
    assert client.post("/api/links/bulk", json=too_many).status_code == 400
    all_bad = client.post("/api/links/bulk", json={"links": [{"url": "x"}]})
    assert all_bad.status_code == 400 and all_bad.get_json()["failed"] == 1
