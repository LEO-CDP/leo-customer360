from fastapi.testclient import TestClient

from app import app


def test_proxy_html_served_from_canonical_path() -> None:
    response = TestClient(app).get("/cdp-sdk/html/cdp-event-proxy.html")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_legacy_proxy_html_paths_are_not_exposed() -> None:
    client = TestClient(app)

    assert client.get("/cdp-event-proxy.html").status_code == 404
    assert client.get("/data/cdp-event-proxy.html").status_code == 404
