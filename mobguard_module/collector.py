from __future__ import annotations

import hashlib
import os
import re
import uuid as uuidlib
from datetime import datetime
from typing import Any

from .config import ModuleConfig
from .state import LocalState


REGEX_UUID = re.compile(r"email: (\S+)")
REGEX_IP = re.compile(r"from (?:tcp:|udp:)?(\d+\.\d+\.\d+\.\d+)")


def _utcnow() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _event_uid(module_id: str, line_offset: int, line: str) -> str:
    return hashlib.sha256(f"{module_id}|{line_offset}|{line}".encode("utf-8")).hexdigest()


def _file_fingerprint(path: str) -> str | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    inode = getattr(stat, "st_ino", 0)
    device = getattr(stat, "st_dev", 0)
    if inode:
        return f"{device}:{inode}"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


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

    def collect_once(self, config: ModuleConfig) -> list[dict[str, Any]]:
        if not os.path.exists(config.access_log_path):
            return []
        cursor_state = self.state.get_cursor_state()
        offset = int(cursor_state.get("offset") or 0)
        current_fingerprint = _file_fingerprint(config.access_log_path)
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
        return events
