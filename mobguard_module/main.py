from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .collector import AccessLogCollector
from .config import ModuleConfig
from .protocol import PanelProtocolClient
from .state import LocalState


def _utcnow() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


def _spool_depth(config: ModuleConfig, state: LocalState) -> int:
    return len(state.read_spool(config.max_spool_events))


@dataclass
class ModuleHealthState:
    health_status: str = "warn"
    error_text: str = ""
    last_validation_at: str = ""
    spool_depth: int = 0
    access_log_exists: bool = False
    issue_source: str = ""

    def _refresh_runtime(self, config: ModuleConfig, state: LocalState) -> tuple[bool, int]:
        self.access_log_exists = os.path.exists(config.access_log_path)
        self.spool_depth = _spool_depth(config, state)
        self.last_validation_at = _utcnow()
        return self.access_log_exists, self.spool_depth

    def mark_ok(self, config: ModuleConfig, state: LocalState) -> None:
        access_log_exists, _ = self._refresh_runtime(config, state)
        self.issue_source = ""
        if not access_log_exists:
            self.health_status = "warn"
            self.error_text = f"Access log path not found: {config.access_log_path}"
            return
        if not config.inbound_tags:
            self.health_status = "warn"
            self.error_text = "No INBOUND tags configured"
            return
        self.health_status = "ok"
        self.error_text = ""

    def mark_warn(self, config: ModuleConfig, state: LocalState, error_text: str, *, issue_source: str) -> None:
        self._refresh_runtime(config, state)
        self.health_status = "warn"
        self.error_text = str(error_text or "").strip()
        self.issue_source = issue_source

    def mark_error(self, config: ModuleConfig, state: LocalState, error_text: str, *, issue_source: str) -> None:
        self._refresh_runtime(config, state)
        self.health_status = "error"
        self.error_text = str(error_text or "").strip()
        self.issue_source = issue_source

    def to_details(self, config: ModuleConfig, state: LocalState) -> dict[str, Any]:
        access_log_exists, spool_depth = self._refresh_runtime(config, state)
        status = self.health_status
        error_text = self.error_text
        if status == "ok":
            if not access_log_exists:
                status = "warn"
                error_text = f"Access log path not found: {config.access_log_path}"
            elif not config.inbound_tags:
                status = "warn"
                error_text = "No INBOUND tags configured"
            else:
                error_text = ""
            self.health_status = status
            self.error_text = error_text
        return {
            "health_status": status,
            "error_text": error_text,
            "last_validation_at": self.last_validation_at,
            "spool_depth": spool_depth,
            "access_log_exists": access_log_exists,
        }


def _apply_remote_config(config: ModuleConfig, state: LocalState, response: dict[str, Any] | None) -> ModuleConfig:
    envelope = (response or {}).get("config") if isinstance((response or {}).get("config"), dict) else response
    if not isinstance(envelope, dict):
        return config
    updated = config.apply_remote_config(envelope)
    state.save_cached_config(envelope)
    return updated


def main() -> None:
    config = ModuleConfig.from_env()
    if not config.panel_base_url or not config.module_id or not config.module_token:
        raise SystemExit("PANEL_BASE_URL, MODULE_ID and MODULE_TOKEN are required")

    state = LocalState(config.state_dir, config.spool_dir)
    state.ensure_dirs()
    cached_config = state.load_cached_config()
    if cached_config:
        config = config.apply_remote_config(cached_config)

    client = PanelProtocolClient(config.panel_base_url, config.module_token)
    collector = AccessLogCollector(config, state)
    health = ModuleHealthState()
    health.mark_ok(config, state)

    try:
        register_response = client.register(
            {
                "module_id": config.module_id,
                "module_name": config.module_id,
                "version": "1.0.0",
                "protocol_version": config.protocol_version,
                "config_revision_applied": config.config_revision,
            }
        )
        config = _apply_remote_config(config, state, register_response)
        collector = AccessLogCollector(config, state)
        health.mark_ok(config, state)
    except ValueError as exc:
        health.mark_error(config, state, f"Invalid remote config on register: {exc}", issue_source="config")
        if not cached_config:
            raise
    except RuntimeError as exc:
        health.mark_warn(config, state, f"Register failed: {exc}", issue_source="register")
        if not cached_config:
            raise

    last_heartbeat = 0.0
    last_config_poll = 0.0
    last_flush = 0.0

    while True:
        now = time.monotonic()

        if now - last_config_poll >= config.config_poll_interval_seconds:
            try:
                config_response = client.fetch_config(config.module_id, config.protocol_version)
                config = _apply_remote_config(config, state, config_response)
                collector = AccessLogCollector(config, state)
                health.mark_ok(config, state)
            except ValueError as exc:
                health.mark_error(config, state, f"Invalid remote config: {exc}", issue_source="config")
            except RuntimeError as exc:
                health.mark_warn(config, state, f"Config sync failed: {exc}", issue_source="config_fetch")
            last_config_poll = now

        new_events = collector.collect_once(config)
        if new_events:
            state.append_events(new_events, max_items=config.max_spool_events)

        if now - last_flush >= config.flush_interval_seconds:
            batch = state.read_spool(config.event_batch_size)
            if batch:
                try:
                    client.send_events(
                        {
                            "module_id": config.module_id,
                            "protocol_version": config.protocol_version,
                            "items": batch,
                        }
                    )
                    state.drop_spool_items(len(batch))
                    if health.issue_source == "batch":
                        health.mark_ok(config, state)
                except RuntimeError as exc:
                    health.mark_warn(config, state, f"Event batch upload failed: {exc}", issue_source="batch")
            elif health.issue_source == "batch":
                health.mark_ok(config, state)
            last_flush = now

        if now - last_heartbeat >= config.heartbeat_interval_seconds:
            try:
                heartbeat = client.heartbeat(
                    {
                        "module_id": config.module_id,
                        "status": "online",
                        "version": "1.0.0",
                        "protocol_version": config.protocol_version,
                        "config_revision_applied": config.config_revision,
                        "details": health.to_details(config, state),
                    }
                )
                desired_revision = int((heartbeat or {}).get("desired_config_revision") or config.config_revision)
                if desired_revision != config.config_revision:
                    try:
                        config_response = client.fetch_config(config.module_id, config.protocol_version)
                        config = _apply_remote_config(config, state, config_response)
                        collector = AccessLogCollector(config, state)
                        health.mark_ok(config, state)
                    except ValueError as exc:
                        health.mark_error(config, state, f"Invalid remote config: {exc}", issue_source="config")
                    except RuntimeError as exc:
                        health.mark_warn(config, state, f"Config sync failed: {exc}", issue_source="config_fetch")
                elif health.issue_source in {"heartbeat", "register"}:
                    health.mark_ok(config, state)
            except RuntimeError as exc:
                health.mark_warn(config, state, f"Heartbeat failed: {exc}", issue_source="heartbeat")
            last_heartbeat = now

        time.sleep(0.5)
