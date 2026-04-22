from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_TIMEOUT_SECONDS = 10
DEFAULT_EVENT_BATCH_TIMEOUT_SECONDS = 60
DEFAULT_RETRY_DELAY_SECONDS = 0.25


class PanelProtocolError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        kind: str,
        retryable: bool = False,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.status_code = status_code


class PanelProtocolClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds
        self.retry_delay_seconds = retry_delay_seconds

    def _build_url(self, path: str, query: dict[str, Any] | None = None) -> str:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode({key: value for key, value in query.items() if value not in (None, '')})}"
        return url

    def _build_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> Request:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return Request(
            self._build_url(path, query=query),
            headers=headers,
            method=method,
            data=data,
        )

    def _decode_response(self, method: str, path: str, raw_payload: bytes) -> dict[str, Any]:
        if not raw_payload:
            return {}
        try:
            payload = json.loads(raw_payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PanelProtocolError(
                f"{method} {path} returned invalid JSON payload",
                kind="payload",
            ) from exc
        if not isinstance(payload, dict):
            raise PanelProtocolError(
                f"{method} {path} returned unexpected payload type",
                kind="payload",
            )
        return payload

    def _perform_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        request = self._build_request(method, path, payload=payload, query=query)
        try:
            with urlopen(request, timeout=timeout_seconds or self.timeout_seconds) as response:
                return self._decode_response(method, path, response.read())
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="ignore")
            raise PanelProtocolError(
                f"{method} {path} failed with HTTP {exc.code}: {details}",
                kind="http",
                status_code=exc.code,
            ) from exc
        except URLError as exc:
            raise PanelProtocolError(
                f"{method} {path} failed: {exc.reason}",
                kind="network",
                retryable=True,
            ) from exc

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        retries: int = 0,
        timeout_seconds: int | None = None,
    ) -> dict[str, Any]:
        attempt = 0
        while True:
            try:
                return self._perform_request(
                    method,
                    path,
                    payload=payload,
                    query=query,
                    timeout_seconds=timeout_seconds,
                )
            except PanelProtocolError as exc:
                if exc.retryable and attempt < retries:
                    attempt += 1
                    time.sleep(self.retry_delay_seconds * attempt)
                    continue
                raise

    def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/module/register", payload=payload)

    def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/module/heartbeat", payload=payload)

    def fetch_config(self, module_id: str, protocol_version: str = "v1") -> dict[str, Any]:
        return self._request(
            "GET",
            "/module/config",
            query={"module_id": module_id, "protocol_version": protocol_version},
            retries=1,
        )

    def send_events(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request(
            "POST",
            "/module/events/batch",
            payload=payload,
            timeout_seconds=DEFAULT_EVENT_BATCH_TIMEOUT_SECONDS,
        )
