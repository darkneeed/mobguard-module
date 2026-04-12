from __future__ import annotations

import time
from typing import Any

from .collector import AccessLogCollector
from .config import ModuleConfig
from .protocol import PanelProtocolClient
from .state import LocalState


def _apply_remote_config(config: ModuleConfig, state: LocalState, response: dict[str, Any] | None) -> ModuleConfig:
    envelope = (response or {}).get("config") if isinstance((response or {}).get("config"), dict) else response
    if not isinstance(envelope, dict):
        return config
    state.save_cached_config(envelope)
    return config.apply_remote_config(envelope)


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
    except RuntimeError:
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
            except RuntimeError:
                pass
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
                except RuntimeError:
                    pass
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
                        "details": {
                            "spool_depth": len(state.read_spool(config.max_spool_events)),
                            "access_log_path": config.access_log_path,
                        },
                    }
                )
                desired_revision = int((heartbeat or {}).get("desired_config_revision") or config.config_revision)
                if desired_revision != config.config_revision:
                    config_response = client.fetch_config(config.module_id, config.protocol_version)
                    config = _apply_remote_config(config, state, config_response)
                    collector = AccessLogCollector(config, state)
            except RuntimeError:
                pass
            last_heartbeat = now

        time.sleep(0.5)
