from __future__ import annotations

import hashlib
import os
import re
import uuid as uuidlib
from datetime import datetime, timedelta
from typing import Any

from .config import ModuleConfig
from .state import LocalState


REGEX_UUID = re.compile(r"email: (\S+)")
REGEX_IP = re.compile(r"from (?:tcp:|udp:)?(\d+\.\d+\.\d+\.\d+)")
REGEX_HEADER_TOKEN = re.compile(
    r'(?:^|\s)(?P<key>[a-z0-9_-]+)=(?P<value>"[^"]*"|.*?)(?=(?:\s+[a-z0-9_-]+=)|$)',
    re.IGNORECASE,
)
SUPPRESSION_WINDOW_SECONDS = 300


def _utcnow() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _event_uid(module_id: str, line_offset: int, line: str) -> str:
    return hashlib.sha256(f"{module_id}|{line_offset}|{line}".encode("utf-8")).hexdigest()


def file_fingerprint(path: str) -> str | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    inode = getattr(stat, "st_ino", 0)
    device = getattr(stat, "st_dev", 0)
    if inode:
        return f"{device}:{inode}"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _extract_header_tokens(line: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for match in REGEX_HEADER_TOKEN.finditer(line):
        key = str(match.group("key") or "").strip().lower()
        value = str(match.group("value") or "").strip().strip('"')
        if key and value:
            tokens[key] = value
    return tokens


def _parse_user_agent(raw_value: str) -> tuple[str | None, str | None]:
    normalized = str(raw_value or "").strip()
    if not normalized:
        return None, None
    primary_token = normalized.split(None, 1)[0].strip()
    if "/" in primary_token:
        app_name, app_version = primary_token.split("/", 1)
        return app_name.strip() or None, app_version.strip() or None
    return primary_token or None, None


def _event_identity(payload: dict[str, Any]) -> str:
    for key in ("uuid", "system_id", "telegram_id", "username"):
        value = payload.get(key)
        if value not in (None, ""):
            return f"{key}:{value}"
    return ""


def _suppression_key(payload: dict[str, Any]) -> str:
    identity = _event_identity(payload)
    ip = str(payload.get("ip") or "").strip()
    tag = str(payload.get("tag") or "").strip()
    if not identity or not ip or not tag:
        return ""
    return f"{identity}|{ip}|{tag}"


def parse_access_line(line: str, inbound_tags: tuple[str, ...]) -> dict[str, Any] | None:
    if "accepted" not in line:
        return None
    tag = next((item for item in inbound_tags if item and item in line), None)
    if not tag:
        return None
    uuid_match = REGEX_UUID.search(line)
    ip_match = REGEX_IP.search(line)
    if not uuid_match or not ip_match:
        return None
    raw_identifier = uuid_match.group(1).strip()
    payload: dict[str, Any] = {
        "occurred_at": _utcnow(),
        "ip": ip_match.group(1),
        "tag": tag,
    }
    header_tokens = _extract_header_tokens(line)
    device_id = header_tokens.get("x-hwid")
    device_label = header_tokens.get("x-device-model")
    os_family = header_tokens.get("x-device-os")
    os_version = header_tokens.get("x-ver-os")
    app_name, app_version = _parse_user_agent(header_tokens.get("user-agent", ""))
    if device_id:
        payload["client_device_id"] = device_id
    if device_label:
        payload["client_device_label"] = device_label
    if os_family:
        payload["client_os_family"] = os_family
    if os_version:
        payload["client_os_version"] = os_version
    if app_name:
        payload["client_app_name"] = app_name
    if app_version:
        payload["client_app_version"] = app_version
    if raw_identifier.isdigit():
        payload["system_id"] = int(raw_identifier)
        return payload
    try:
        uuidlib.UUID(raw_identifier)
        payload["uuid"] = raw_identifier
        return payload
    except ValueError:
        payload["username"] = raw_identifier
        return payload


class AccessLogCollector:
    def __init__(self, config: ModuleConfig, state: LocalState):
        self.state = state

    def _suppress_recent_duplicates(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not events:
            return []
        markers = self.state.load_recent_event_markers()
        now = datetime.utcnow().replace(microsecond=0)
        cutoff = now - timedelta(seconds=SUPPRESSION_WINDOW_SECONDS)
        pruned_markers: dict[str, str] = {}
        for marker_key, marker_timestamp in markers.items():
            try:
                marker_dt = datetime.fromisoformat(marker_timestamp)
            except ValueError:
                continue
            if marker_dt >= cutoff:
                pruned_markers[marker_key] = marker_dt.replace(microsecond=0).isoformat()

        accepted: list[dict[str, Any]] = []
        for item in events:
            suppression_key = _suppression_key(item)
            occurred_at = str(item.get("occurred_at") or "").strip()
            try:
                occurred_dt = datetime.fromisoformat(occurred_at) if occurred_at else now
            except ValueError:
                occurred_dt = now
            if not suppression_key:
                accepted.append(item)
                continue
            previous = pruned_markers.get(suppression_key)
            if previous:
                try:
                    previous_dt = datetime.fromisoformat(previous)
                except ValueError:
                    previous_dt = None
                if previous_dt is not None and occurred_dt - previous_dt < timedelta(seconds=SUPPRESSION_WINDOW_SECONDS):
                    continue
            accepted.append(item)
            pruned_markers[suppression_key] = occurred_dt.replace(microsecond=0).isoformat()

        self.state.save_recent_event_markers(pruned_markers)
        return accepted

    def collect_once(self, config: ModuleConfig) -> list[dict[str, Any]]:
        if not os.path.exists(config.access_log_path):
            return []
        cursor_state = self.state.get_cursor_state()
        offset = int(cursor_state.get("offset") or 0)
        current_fingerprint = file_fingerprint(config.access_log_path)
        stored_fingerprint = cursor_state.get("file_fingerprint")
        size = os.path.getsize(config.access_log_path)
        # Reset cursor when the file shrinks or when the path now points at a rotated file.
        if offset > size or (stored_fingerprint and current_fingerprint and stored_fingerprint != current_fingerprint):
            offset = 0
        events: list[dict[str, Any]] = []
        with open(config.access_log_path, "r", encoding="utf-8", errors="ignore") as handle:
            handle.seek(offset)
            while True:
                line_offset = handle.tell()
                line = handle.readline()
                if not line:
                    break
                parsed = parse_access_line(line, config.inbound_tags)
                if parsed:
                    parsed["log_offset"] = line_offset
                    parsed["event_uid"] = _event_uid(config.module_id, line_offset, line)
                    events.append(parsed)
            offset = handle.tell()
        self.state.set_cursor_state(offset, current_fingerprint)
        return self._suppress_recent_duplicates(events)
