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
