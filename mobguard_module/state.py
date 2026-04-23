from __future__ import annotations

import json
import os
from typing import Any


def _load_json(path: str) -> dict[str, Any] | None:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        try:
            payload = json.load(handle)
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def _atomic_write_text(path: str, content: str) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.replace(tmp_path, path)


def _atomic_write_json(path: str, payload: dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


class LocalState:
    COMPACT_MIN_HEAD_BYTES = 64 * 1024

    def __init__(self, state_dir: str, spool_dir: str):
        self.state_dir = state_dir
        self.spool_dir = spool_dir
        self.cursor_path = os.path.join(state_dir, "cursor.txt")
        self.cursor_meta_path = os.path.join(state_dir, "cursor-meta.json")
        self.config_cache_path = os.path.join(state_dir, "config-cache.json")
        self.spool_path = os.path.join(spool_dir, "events.jsonl")
        self.spool_meta_path = os.path.join(spool_dir, "meta.json")

    def ensure_dirs(self) -> None:
        os.makedirs(self.state_dir, exist_ok=True)
        os.makedirs(self.spool_dir, exist_ok=True)
        if not os.path.exists(self.spool_path):
            with open(self.spool_path, "w", encoding="utf-8"):
                pass
        self._load_spool_meta()

    def get_cursor(self) -> int:
        return int(self.get_cursor_state()["offset"])

    def set_cursor(self, offset: int) -> None:
        state = self.get_cursor_state()
        self.set_cursor_state(offset, state.get("file_fingerprint"))

    def get_cursor_state(self) -> dict[str, Any]:
        raw_offset = 0
        if os.path.exists(self.cursor_path):
            with open(self.cursor_path, "r", encoding="utf-8") as handle:
                raw_value = handle.read().strip()
            try:
                raw_offset = int(raw_value) if raw_value else 0
            except ValueError:
                raw_offset = 0
        payload = _load_json(self.cursor_meta_path) or {}
        fingerprint = payload.get("file_fingerprint")
        return {
            "offset": max(raw_offset, 0),
            "file_fingerprint": str(fingerprint) if fingerprint not in (None, "") else None,
        }

    def set_cursor_state(self, offset: int, file_fingerprint: str | None) -> None:
        normalized_offset = max(int(offset), 0)
        _atomic_write_text(self.cursor_path, str(normalized_offset))
        _atomic_write_json(
            self.cursor_meta_path,
            {
                "offset": normalized_offset,
                "file_fingerprint": str(file_fingerprint) if file_fingerprint not in (None, "") else None,
            },
        )

    def load_cached_config(self) -> dict[str, Any] | None:
        return _load_json(self.config_cache_path)

    def save_cached_config(self, payload: dict[str, Any]) -> None:
        _atomic_write_json(self.config_cache_path, payload)

    def get_spool_depth(self) -> int:
        meta = self._load_spool_meta()
        return int(meta["item_count"])

    def append_events(self, items: list[dict[str, Any]], max_items: int) -> None:
        if not items:
            return
        meta = self._load_spool_meta()
        with open(self.spool_path, "a", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        meta["item_count"] += len(items)
        self._save_spool_meta(meta)
        normalized_max_items = max(int(max_items), 1)
        overflow = meta["item_count"] - normalized_max_items
        if overflow > 0:
            self.drop_spool_items(overflow)

    def read_spool(self, max_items: int) -> list[dict[str, Any]]:
        normalized_max_items = max(int(max_items), 0)
        if normalized_max_items <= 0:
            return []
        meta = self._load_spool_meta()
        if meta["item_count"] == 0 or not os.path.exists(self.spool_path):
            return []
        rows: list[dict[str, Any]] = []
        with open(self.spool_path, "r", encoding="utf-8") as handle:
            handle.seek(int(meta["head_offset"]))
            while len(rows) < normalized_max_items:
                raw_line = handle.readline()
                if not raw_line:
                    break
                line = raw_line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
        return rows

    def drop_spool_items(self, count: int) -> None:
        normalized_count = max(int(count), 0)
        if normalized_count <= 0:
            return
        meta = self._load_spool_meta()
        current_count = int(meta["item_count"])
        if current_count == 0:
            return
        if normalized_count >= current_count:
            self._reset_spool()
            return
        dropped = 0
        new_head_offset = int(meta["head_offset"])
        with open(self.spool_path, "r", encoding="utf-8") as handle:
            handle.seek(new_head_offset)
            while dropped < normalized_count:
                raw_line = handle.readline()
                if not raw_line:
                    break
                if not raw_line.strip():
                    continue
                dropped += 1
                new_head_offset = handle.tell()
        meta["head_offset"] = new_head_offset
        meta["item_count"] = max(current_count - dropped, 0)
        self._save_spool_meta(meta)
        self._maybe_compact_spool(meta)

    def _load_spool_meta(self) -> dict[str, int]:
        if not os.path.exists(self.spool_path):
            return self._reset_spool()
        file_size = os.path.getsize(self.spool_path)
        payload = _load_json(self.spool_meta_path)
        if not isinstance(payload, dict):
            return self._rebuild_spool_meta()
        try:
            head_offset = max(int(payload.get("head_offset", 0)), 0)
            item_count = max(int(payload.get("item_count", 0)), 0)
        except (TypeError, ValueError):
            return self._rebuild_spool_meta()
        if head_offset > file_size:
            return self._rebuild_spool_meta()
        if item_count == 0 and head_offset != 0:
            return self._reset_spool()
        meta = {"head_offset": head_offset, "item_count": item_count}
        self._save_spool_meta(meta)
        return meta

    def _save_spool_meta(self, meta: dict[str, int]) -> None:
        _atomic_write_json(
            self.spool_meta_path,
            {
                "head_offset": max(int(meta.get("head_offset", 0)), 0),
                "item_count": max(int(meta.get("item_count", 0)), 0),
            },
        )

    def _rebuild_spool_meta(self) -> dict[str, int]:
        if not os.path.exists(self.spool_path):
            return self._reset_spool()
        count = 0
        with open(self.spool_path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                if raw_line.strip():
                    count += 1
        meta = {"head_offset": 0, "item_count": count}
        self._save_spool_meta(meta)
        return meta

    def _maybe_compact_spool(self, meta: dict[str, int]) -> None:
        if int(meta.get("item_count", 0)) == 0:
            self._reset_spool()
            return
        if not os.path.exists(self.spool_path):
            self._reset_spool()
            return
        file_size = os.path.getsize(self.spool_path)
        head_offset = int(meta.get("head_offset", 0))
        if head_offset < self.COMPACT_MIN_HEAD_BYTES and head_offset < file_size // 2:
            return
        tmp_path = f"{self.spool_path}.tmp"
        kept_count = 0
        with open(self.spool_path, "r", encoding="utf-8") as source, open(tmp_path, "w", encoding="utf-8") as handle:
            source.seek(head_offset)
            for raw_line in source:
                if not raw_line.strip():
                    continue
                handle.write(raw_line if raw_line.endswith("\n") else f"{raw_line}\n")
                kept_count += 1
        os.replace(tmp_path, self.spool_path)
        self._save_spool_meta({"head_offset": 0, "item_count": kept_count})

    def _reset_spool(self) -> dict[str, int]:
        os.makedirs(self.spool_dir, exist_ok=True)
        with open(self.spool_path, "w", encoding="utf-8"):
            pass
        meta = {"head_offset": 0, "item_count": 0}
        self._save_spool_meta(meta)
        return meta
