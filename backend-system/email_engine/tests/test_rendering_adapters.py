"""Unit tests for the pure rendering / tracking / adapter helpers (no DB)."""

import base64

from email_engine.adapters import (
    MockDispatchAdapter,
    SMTPDispatchAdapter,
    build_adapter,
)
from email_engine.rendering import (
    inject_tracking_pixel,
    render_string,
    rewrite_links_for_click_tracking,
)
from email_engine.tracking import encode_tracking_token


# --- rendering -------------------------------------------------------------

def test_render_string_substitutes_known_and_blanks_unknown():
    out = render_string("Hi {{ first_name }} <{{ email }}> [{{ missing }}]",
                        {"first_name": "Ada", "email": "ada@x.io"})
    assert out == "Hi Ada <ada@x.io> []"


def test_render_string_none_template_is_empty():
    assert render_string(None, {"a": "b"}) == ""


def test_inject_tracking_pixel_before_body_close():
    html = "<html><body><p>hi</p></body></html>"
    out = inject_tracking_pixel(html, "https://t/open?u=abc")
    assert out.index("<img") < out.index("</body>")
    assert 'src="https://t/open?u=abc"' in out


def test_inject_tracking_pixel_noop_without_url():
    assert inject_tracking_pixel("<p>x</p>", None) == "<p>x</p>"


def test_rewrite_links_wraps_http_but_not_anchor_or_mailto():
    html = '<a href="https://shop/x">buy</a> <a href="#f">f</a> <a href="mailto:a@b">m</a>'
    out = rewrite_links_for_click_tracking(html, "https://t/click", "TOKEN")
    assert "https://t/click?u=TOKEN&url=https%3A%2F%2Fshop%2Fx" in out
    assert 'href="#f"' in out
    assert 'href="mailto:a@b"' in out


def test_rewrite_links_does_not_double_wrap():
    html = '<a href="https://t/click?u=TOKEN&url=x">already</a>'
    out = rewrite_links_for_click_tracking(html, "https://t/click", "TOKEN")
    assert out == html


# --- tracking token --------------------------------------------------------

def test_encode_token_is_deterministic_and_decodable():
    token = encode_tracking_token("t1", "c1", "p1", secret="s")
    assert token == encode_tracking_token("t1", "c1", "p1", secret="s")
    padded = token + "=" * (-len(token) % 4)
    raw = base64.urlsafe_b64decode(padded).decode()
    assert raw.startswith("t1|c1|p1|")


def test_encode_token_differs_per_recipient():
    a = encode_tracking_token("t1", "c1", "p1", secret="s")
    b = encode_tracking_token("t1", "c1", "p2", secret="s")
    assert a != b


# --- adapters --------------------------------------------------------------

def test_mock_adapter_always_ok_with_message_id():
    result = MockDispatchAdapter().send(to_email="a@b.io", subject="s", html_body="<p>x</p>", text_body="x")
    assert result.ok and result.provider_message_id.startswith("mock-")


def test_build_adapter_from_db_config_selects_smtp():
    adapter = build_adapter({"provider": "smtp", "smtp_host": "mail.x", "smtp_port": 2525})
    assert isinstance(adapter, SMTPDispatchAdapter)
    assert adapter.host == "mail.x" and adapter.port == 2525


def test_build_adapter_unknown_provider_falls_back_to_mock():
    assert isinstance(build_adapter({"provider": "carrier-pigeon"}), MockDispatchAdapter)


def test_build_adapter_mock_config():
    assert isinstance(build_adapter({"provider": "mock"}), MockDispatchAdapter)
