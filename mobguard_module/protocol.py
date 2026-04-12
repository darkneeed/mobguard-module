from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class PanelProtocolClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode({key: value for key, value in query.items() if value not in (None, '')})}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(url, headers=headers, method=method, data=data)
        try:
            with urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            details = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}: {details}") from exc
        except URLError as exc:
            raise RuntimeError(f"{method} {path} failed: {exc.reason}") from exc

    def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/modules/register", payload=payload)

    def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/modules/heartbeat", payload=payload)

    def fetch_config(self, module_id: str, protocol_version: str = "v1") -> dict[str, Any]:
        return self._request(
            "GET",
            "/modules/config",
            query={"module_id": module_id, "protocol_version": protocol_version},
        )

    def send_events(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/modules/events/batch", payload=payload)
