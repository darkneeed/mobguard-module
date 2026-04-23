from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Any


MAX_EVENT_BATCH_SIZE = 1000
MAX_SPOOL_EVENTS = 100_000
DEFAULT_ENV_PATH = ".env"


def load_env_file(path: str) -> dict[str, str]:
    if not os.path.exists(path):
        return {}
    values: dict[str, str] = {}
    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env_int(values: dict[str, str], key: str, default: int) -> int:
    raw_value = values.get(key, "")
    try:
        return int(raw_value) if raw_value else default
    except ValueError:
        return default


def _config_int(
    value: Any,
    default: int,
    *,
    field_name: str,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be an integer") from exc
    if parsed < minimum:
        raise ValueError(f"{field_name} must be >= {minimum}")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{field_name} must be <= {maximum}")
    return parsed


def _config_tags(value: Any) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list):
        raise ValueError("rules.inbound_tags must be a list")
    return tuple(str(item).strip() for item in value if str(item).strip())


@dataclass(frozen=True)
class ModuleConfig:
    panel_base_url: str
    module_id: str
    module_token: str
    access_log_path: str
    state_dir: str
    spool_dir: str
    heartbeat_interval_seconds: int = 30
    config_poll_interval_seconds: int = 60
    flush_interval_seconds: int = 3
    event_batch_size: int = 100
    max_spool_events: int = 5000
    protocol_version: str = "v1"
    config_revision: int = 0
    inbound_tags: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env_path: str | None = None) -> "ModuleConfig":
        resolved_env_path = str(
            env_path
            or os.getenv("MOBGUARD_MODULE_ENV_FILE")
            or os.getenv("MOBGUARD_ENV_FILE")
            or DEFAULT_ENV_PATH
        )
        values = {
            **load_env_file(resolved_env_path),
            **{key: value for key, value in os.environ.items() if value is not None},
        }
        return cls(
            panel_base_url=str(values.get("PANEL_BASE_URL", "")).rstrip("/"),
            module_id=str(values.get("MODULE_ID", "")).strip(),
            module_token=str(values.get("MODULE_TOKEN", "")).strip(),
            access_log_path=str(values.get("ACCESS_LOG_PATH", "/var/log/remnanode/access.log")).strip(),
            state_dir=str(values.get("STATE_DIR", "./state")).strip(),
            spool_dir=str(values.get("SPOOL_DIR", "./state/spool")).strip(),
            heartbeat_interval_seconds=max(_env_int(values, "HEARTBEAT_INTERVAL_SECONDS", 30), 1),
            config_poll_interval_seconds=max(_env_int(values, "CONFIG_POLL_INTERVAL_SECONDS", 60), 1),
            flush_interval_seconds=max(_env_int(values, "FLUSH_INTERVAL_SECONDS", 3), 1),
            event_batch_size=max(_env_int(values, "EVENT_BATCH_SIZE", 100), 1),
            max_spool_events=max(_env_int(values, "MAX_SPOOL_EVENTS", 5000), 1),
        )

    def apply_remote_config(self, envelope: dict[str, Any] | None) -> "ModuleConfig":
        if not envelope:
            return self
        runtime = envelope.get("module_runtime", {}) if isinstance(envelope.get("module_runtime"), dict) else {}
        rules = envelope.get("rules", {}) if isinstance(envelope.get("rules"), dict) else {}
        inbound_tags = rules.get("inbound_tags")
        if inbound_tags in (None, ""):
            inbound_tags = rules.get("mobile_tags", [])
        event_batch_size = _config_int(
            runtime.get("event_batch_size"),
            self.event_batch_size,
            field_name="module_runtime.event_batch_size",
            maximum=MAX_EVENT_BATCH_SIZE,
        )
        max_spool_events = _config_int(
            runtime.get("max_spool_events"),
            self.max_spool_events,
            field_name="module_runtime.max_spool_events",
            maximum=MAX_SPOOL_EVENTS,
        )
        if event_batch_size > max_spool_events:
            raise ValueError("module_runtime.event_batch_size must not exceed module_runtime.max_spool_events")
        return replace(
            self,
            heartbeat_interval_seconds=_config_int(
                runtime.get("heartbeat_interval_seconds"),
                self.heartbeat_interval_seconds,
                field_name="module_runtime.heartbeat_interval_seconds",
            ),
            config_poll_interval_seconds=_config_int(
                runtime.get("config_poll_interval_seconds"),
                self.config_poll_interval_seconds,
                field_name="module_runtime.config_poll_interval_seconds",
            ),
            flush_interval_seconds=_config_int(
                runtime.get("flush_interval_seconds"),
                self.flush_interval_seconds,
                field_name="module_runtime.flush_interval_seconds",
            ),
            event_batch_size=event_batch_size,
            max_spool_events=max_spool_events,
            config_revision=_config_int(
                envelope.get("config_revision"),
                self.config_revision or 1,
                field_name="config_revision",
            ),
            inbound_tags=_config_tags(inbound_tags),
        )
