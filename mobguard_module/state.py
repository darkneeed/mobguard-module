from __future__ import annotations

import json
import os
from typing import Any


class LocalState:
    def __init__(self, state_dir: str, spool_dir: str):
        self.state_dir = state_dir
        self.spool_dir = spool_dir
        self.cursor_path = os.path.join(state_dir, "cursor.txt")
        self.config_cache_path = os.path.join(state_dir, "config-cache.json")
        self.spool_path = os.path.join(spool_dir, "events.jsonl")

    def ensure_dirs(self) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        os.makedirs(self.spool_dir, exist_ok=True)

    def get_cursor(self) -> int:
        if not os.path.exists(self.cursor_path):
            return 0
        with open(self.cursor_path, "r", encoding="utf-8") as handle:
            raw_value = handle.read().strip()
        try:
            return int(raw_value) if raw_value else 0
        except ValueError:
            return 0

    def set_cursor(self, offset: int) -> None:
        with open(self.cursor_path, "w", encoding="utf-8") as handle:
            handle.write(str(max(int(offset), 0)))

    def load_cached_config(self) -> dict[str, Any] | None:
        if not os.path.exists(self.config_cache_path):
            return None
        with open(self.config_cache_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def save_cached_config(self, payload: dict[str, Any]) -> None:
        with open(self.config_cache_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def append_events(self, items: list[dict[str, Any]], max_items: int) -> None:
        existing = self.read_spool(max_items=10 ** 9)
        combined = (existing + list(items))[-max_items:]
        self._write_spool(combined)

    def read_spool(self, max_items: int) -> list[dict[str, Any]]:
        if not os.path.exists(self.spool_path):
            return []
        rows: list[dict[str, Any]] = []
        with open(self.spool_path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
                if len(rows) >= max_items:
                    break
        return rows

    def drop_spool_items(self, count: int) -> None:
        if count <= 0:
            return
        remaining = self.read_spool(max_items=10 ** 9)[count:]
        self._write_spool(remaining)

    def _write_spool(self, items: list[dict[str, Any]]) -> None:
        with open(self.spool_path, "w", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
