import io
from urllib.error import HTTPError, URLError
from unittest.mock import patch

import pytest

from mobguard_module.protocol import PanelProtocolClient, PanelProtocolError


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def read(self) -> bytes:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_fetch_config_retries_once_on_transport_failure():
    client = PanelProtocolClient("https://panel.example.com", "token", retry_delay_seconds=0)
    with patch(
        "mobguard_module.protocol.urlopen",
        side_effect=[
            URLError("network down"),
            FakeResponse(b'{"config": {"config_revision": 3}}'),
        ],
    ):
        payload = client.fetch_config("module-test")

    assert payload["config"]["config_revision"] == 3


def test_malformed_json_payload_is_reported_as_payload_error():
    client = PanelProtocolClient("https://panel.example.com", "token")
    with patch("mobguard_module.protocol.urlopen", return_value=FakeResponse(b"not-json")):
        with pytest.raises(PanelProtocolError, match="invalid JSON payload") as exc_info:
            client.fetch_config("module-test")

    assert exc_info.value.kind == "payload"


def test_http_error_is_reported_without_retry():
    client = PanelProtocolClient("https://panel.example.com", "token")
    error = HTTPError(
        url="https://panel.example.com/module/events/batch",
        code=503,
        msg="service unavailable",
        hdrs=None,
        fp=io.BytesIO(b'{"detail":"busy"}'),
    )
    with patch("mobguard_module.protocol.urlopen", side_effect=error):
        with pytest.raises(PanelProtocolError, match="HTTP 503") as exc_info:
            client.send_events({"items": []})

    assert exc_info.value.kind == "http"
    assert exc_info.value.status_code == 503
