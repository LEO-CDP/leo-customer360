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


def test_private_network_preflight_includes_pna_header() -> None:
    response = TestClient(app).options(
        "/api/v1/tracking/logs",
        headers={
            "Origin": "https://docs.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
            "Access-Control-Request-Private-Network": "true",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-private-network"] == "true"
