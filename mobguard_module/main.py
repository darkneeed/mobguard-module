from __future__ import annotations

import os
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from .collector import AccessLogCollector
from .config import ModuleConfig
from .protocol import PanelProtocolClient
from .state import LocalState


LOOP_SLEEP_SECONDS = 0.5


def _utcnow() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat()


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
        self.spool_depth = state.get_spool_depth()
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


@dataclass(frozen=True)
class ModuleRuntime:
    config: ModuleConfig
    state: LocalState
    client: PanelProtocolClient
    collector: AccessLogCollector
    health: ModuleHealthState


def _apply_remote_config(runtime: ModuleRuntime, response: dict[str, Any] | None) -> ModuleRuntime:
    envelope = (response or {}).get("config") if isinstance((response or {}).get("config"), dict) else response
    if not isinstance(envelope, dict):
        return runtime
    updated_config = runtime.config.apply_remote_config(envelope)
    runtime.state.save_cached_config(envelope)
    return replace(runtime, config=updated_config, collector=AccessLogCollector(updated_config, runtime.state))


def _bootstrap_runtime(env_path: str | None = None) -> tuple[ModuleRuntime, bool]:
    config = ModuleConfig.from_env(env_path)
    if not config.panel_base_url or not config.module_id or not config.module_token:
        raise SystemExit("PANEL_BASE_URL, MODULE_ID and MODULE_TOKEN are required")
    state = LocalState(config.state_dir, config.spool_dir)
    state.ensure_dirs()
    cached_config = state.load_cached_config()
    if cached_config:
        config = config.apply_remote_config(cached_config)
    health = ModuleHealthState()
    runtime = ModuleRuntime(
        config=config,
        state=state,
        client=PanelProtocolClient(config.panel_base_url, config.module_token),
        collector=AccessLogCollector(config, state),
        health=health,
    )
    runtime.health.mark_ok(runtime.config, runtime.state)
    return runtime, bool(cached_config)


def _register_payload(runtime: ModuleRuntime) -> dict[str, Any]:
    return {
        "module_id": runtime.config.module_id,
        "module_name": "",
        "version": "1.0.0",
        "protocol_version": runtime.config.protocol_version,
        "config_revision_applied": runtime.config.config_revision,
    }


def _batch_payload(runtime: ModuleRuntime, batch: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "module_id": runtime.config.module_id,
        "protocol_version": runtime.config.protocol_version,
        "items": batch,
    }


def _heartbeat_payload(runtime: ModuleRuntime) -> dict[str, Any]:
    return {
        "module_id": runtime.config.module_id,
        "status": "online",
        "version": "1.0.0",
        "protocol_version": runtime.config.protocol_version,
        "config_revision_applied": runtime.config.config_revision,
        "details": runtime.health.to_details(runtime.config, runtime.state),
    }


def _run_register_phase(runtime: ModuleRuntime, *, allow_cached_bootstrap: bool) -> ModuleRuntime:
    try:
        response = runtime.client.register(_register_payload(runtime))
        runtime = _apply_remote_config(runtime, response)
        runtime.health.mark_ok(runtime.config, runtime.state)
    except ValueError as exc:
        runtime.health.mark_error(runtime.config, runtime.state, f"Invalid remote config on register: {exc}", issue_source="config")
        if not allow_cached_bootstrap:
            raise
    except RuntimeError as exc:
        runtime.health.mark_warn(runtime.config, runtime.state, f"Register failed: {exc}", issue_source="register")
    return runtime


def _run_config_sync_phase(runtime: ModuleRuntime) -> ModuleRuntime:
    try:
        response = runtime.client.fetch_config(runtime.config.module_id, runtime.config.protocol_version)
        runtime = _apply_remote_config(runtime, response)
        runtime.health.mark_ok(runtime.config, runtime.state)
    except ValueError as exc:
        runtime.health.mark_error(runtime.config, runtime.state, f"Invalid remote config: {exc}", issue_source="config")
    except RuntimeError as exc:
        runtime.health.mark_warn(runtime.config, runtime.state, f"Config sync failed: {exc}", issue_source="config_fetch")
    return runtime


def _run_collect_phase(runtime: ModuleRuntime) -> None:
    new_events = runtime.collector.collect_once(runtime.config)
    if new_events:
        runtime.state.append_events(new_events, max_items=runtime.config.max_spool_events)


def _run_flush_phase(runtime: ModuleRuntime) -> None:
    batch = runtime.state.read_spool(runtime.config.event_batch_size)
    if batch:
        try:
            runtime.client.send_events(_batch_payload(runtime, batch))
            runtime.state.drop_spool_items(len(batch))
            if runtime.health.issue_source == "batch":
                runtime.health.mark_ok(runtime.config, runtime.state)
        except RuntimeError as exc:
            runtime.health.mark_warn(runtime.config, runtime.state, f"Event batch upload failed: {exc}", issue_source="batch")
    elif runtime.health.issue_source == "batch":
        runtime.health.mark_ok(runtime.config, runtime.state)


def _run_heartbeat_phase(runtime: ModuleRuntime) -> ModuleRuntime:
    if runtime.health.issue_source == "register":
        return _run_register_phase(runtime, allow_cached_bootstrap=True)
    try:
        heartbeat = runtime.client.heartbeat(_heartbeat_payload(runtime))
        desired_revision = int((heartbeat or {}).get("desired_config_revision") or runtime.config.config_revision)
        if desired_revision != runtime.config.config_revision:
            runtime = _run_config_sync_phase(runtime)
        elif runtime.health.issue_source in {"heartbeat", "register"}:
            runtime.health.mark_ok(runtime.config, runtime.state)
    except RuntimeError as exc:
        runtime.health.mark_warn(runtime.config, runtime.state, f"Heartbeat failed: {exc}", issue_source="heartbeat")
    return runtime


def main() -> None:
    runtime, has_cached_config = _bootstrap_runtime()
    runtime = _run_register_phase(runtime, allow_cached_bootstrap=has_cached_config)

    last_heartbeat = 0.0
    last_config_poll = 0.0
    last_flush = 0.0

    while True:
        now = time.monotonic()

        if now - last_config_poll >= runtime.config.config_poll_interval_seconds:
            runtime = _run_config_sync_phase(runtime)
            last_config_poll = now

        _run_collect_phase(runtime)

        if now - last_flush >= runtime.config.flush_interval_seconds:
            _run_flush_phase(runtime)
            last_flush = now

        if now - last_heartbeat >= runtime.config.heartbeat_interval_seconds:
            runtime = _run_heartbeat_phase(runtime)
            last_heartbeat = now

        time.sleep(LOOP_SLEEP_SECONDS)
