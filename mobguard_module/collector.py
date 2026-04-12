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
        "occurred_at": datetime.utcnow().replace(microsecond=0).isoformat(),
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
        self.config = config
        self.state = state

    def collect_once(self, config: ModuleConfig) -> list[dict[str, Any]]:
        if not os.path.exists(config.access_log_path):
            return []
        offset = self.state.get_cursor()
        size = os.path.getsize(config.access_log_path)
        if offset > size:
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
                    parsed["event_uid"] = hashlib.sha256(
                        f"{config.module_id}|{line_offset}|{line}".encode("utf-8")
                    ).hexdigest()
                    events.append(parsed)
            offset = handle.tell()
        self.state.set_cursor(offset)
        return events
