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
