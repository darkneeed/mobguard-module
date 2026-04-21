from pathlib import Path

from mobguard_module.collector import AccessLogCollector
from mobguard_module.config import ModuleConfig
from mobguard_module.main import (
    ModuleHealthState,
    ModuleRuntime,
    _run_heartbeat_phase,
    _run_register_phase,
)
from mobguard_module.state import LocalState


class FakeClient:
    def __init__(self):
        self.register_response = None
        self.register_error = None
        self.heartbeat_response = None
        self.heartbeat_error = None
        self.fetch_config_response = None
        self.fetch_config_error = None

    def register(self, payload):
        if self.register_error:
            raise self.register_error
        return self.register_response or {}

    def heartbeat(self, payload):
        if self.heartbeat_error:
            raise self.heartbeat_error
        return self.heartbeat_response or {}

    def fetch_config(self, module_id, protocol_version="v1"):
        if self.fetch_config_error:
            raise self.fetch_config_error
        return self.fetch_config_response or {}

    def send_events(self, payload):
        return {}


def _runtime(tmp_path: Path, *, config_revision: int = 1, inbound_tags: tuple[str, ...] = ("TAG",)):
    access_log_path = tmp_path / "access.log"
    access_log_path.write_text("", encoding="utf-8")
    state = LocalState(str(tmp_path / "state"), str(tmp_path / "state" / "spool"))
    state.ensure_dirs()
    config = ModuleConfig(
        panel_base_url="https://panel.example.com",
        module_id="module-test",
        module_token="token-test",
        access_log_path=str(access_log_path),
        state_dir=str(tmp_path / "state"),
        spool_dir=str(tmp_path / "state" / "spool"),
        config_revision=config_revision,
        inbound_tags=inbound_tags,
    )
    health = ModuleHealthState()
    runtime = ModuleRuntime(
        config=config,
        state=state,
        client=FakeClient(),
        collector=AccessLogCollector(config, state),
        health=health,
    )
    runtime.health.mark_ok(runtime.config, runtime.state)
    return runtime


def test_register_phase_recovers_when_cached_config_is_available(tmp_path: Path):
    runtime = _runtime(tmp_path)
    runtime.state.save_cached_config(
        {
            "config_revision": 2,
            "rules": {"inbound_tags": ["TAG"]},
            "module_runtime": {"heartbeat_interval_seconds": 10},
        }
    )
    runtime.client.register_error = RuntimeError("panel offline")

    updated = _run_register_phase(runtime, allow_cached_bootstrap=True)

    assert updated.health.health_status == "warn"
    assert updated.health.issue_source == "register"
    assert "Register failed" in updated.health.error_text


def test_heartbeat_phase_refreshes_config_when_desired_revision_changes(tmp_path: Path):
    runtime = _runtime(tmp_path, config_revision=1, inbound_tags=("OLD",))
    runtime.client.heartbeat_response = {"desired_config_revision": 3}
    runtime.client.fetch_config_response = {
        "config": {
            "config_revision": 3,
            "rules": {"inbound_tags": ["CANARY"]},
            "module_runtime": {
                "heartbeat_interval_seconds": 10,
                "config_poll_interval_seconds": 20,
                "flush_interval_seconds": 5,
                "event_batch_size": 50,
                "max_spool_events": 500,
            },
        }
    }

    updated = _run_heartbeat_phase(runtime)

    assert updated.config.config_revision == 3
    assert updated.config.inbound_tags == ("CANARY",)
    assert updated.health.health_status == "ok"
