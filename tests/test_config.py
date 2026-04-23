import pytest

from mobguard_module.config import MAX_EVENT_BATCH_SIZE, ModuleConfig


def test_module_config_can_bootstrap_from_process_env(monkeypatch, tmp_path):
    monkeypatch.setenv("PANEL_BASE_URL", "https://panel.example.com")
    monkeypatch.setenv("MODULE_ID", "module-test")
    monkeypatch.setenv("MODULE_TOKEN", "token-test")
    monkeypatch.setenv("ACCESS_LOG_PATH", "/var/log/remnanode/access.log")

    cfg = ModuleConfig.from_env(str(tmp_path / "missing.env"))

    assert cfg.panel_base_url == "https://panel.example.com"
    assert cfg.module_id == "module-test"
    assert cfg.module_token == "token-test"
    assert cfg.access_log_path == "/var/log/remnanode/access.log"


def test_module_config_prefers_inbound_tags_and_falls_back_to_mobile_tags(monkeypatch, tmp_path):
    monkeypatch.setenv("PANEL_BASE_URL", "https://panel.example.com")
    monkeypatch.setenv("MODULE_ID", "module-test")
    monkeypatch.setenv("MODULE_TOKEN", "token-test")

    cfg = ModuleConfig.from_env(str(tmp_path / "missing.env"))
    updated = cfg.apply_remote_config(
        {
            "config_revision": 3,
            "rules": {
                "inbound_tags": ["INBOUND-A", "INBOUND-B"],
                "mobile_tags": ["OLD-A"],
            },
            "module_runtime": {
                "heartbeat_interval_seconds": 10,
                "config_poll_interval_seconds": 20,
                "flush_interval_seconds": 5,
                "event_batch_size": 50,
                "max_spool_events": 500,
            },
        }
    )

    assert updated.inbound_tags == ("INBOUND-A", "INBOUND-B")
    assert updated.config_revision == 3
    assert updated.heartbeat_interval_seconds == 10

    fallback = cfg.apply_remote_config(
        {
            "config_revision": 4,
            "rules": {
                "mobile_tags": ["OLD-A", "OLD-B"],
            },
            "module_runtime": {
                "heartbeat_interval_seconds": 30,
                "config_poll_interval_seconds": 60,
                "flush_interval_seconds": 3,
                "event_batch_size": 100,
                "max_spool_events": 5000,
            },
        }
    )

    assert fallback.inbound_tags == ("OLD-A", "OLD-B")


def test_module_config_rejects_pathological_batch_sizes(monkeypatch, tmp_path):
    monkeypatch.setenv("PANEL_BASE_URL", "https://panel.example.com")
    monkeypatch.setenv("MODULE_ID", "module-test")
    monkeypatch.setenv("MODULE_TOKEN", "token-test")

    cfg = ModuleConfig.from_env(str(tmp_path / "missing.env"))

    with pytest.raises(ValueError, match="event_batch_size"):
        cfg.apply_remote_config(
            {
                "config_revision": 2,
                "module_runtime": {
                    "event_batch_size": MAX_EVENT_BATCH_SIZE + 1,
                    "max_spool_events": MAX_EVENT_BATCH_SIZE + 10,
                },
            }
        )

    with pytest.raises(ValueError, match="must not exceed"):
        cfg.apply_remote_config(
            {
                "config_revision": 2,
                "module_runtime": {
                    "event_batch_size": 200,
                    "max_spool_events": 100,
                },
            }
        )


def test_module_config_can_bootstrap_from_explicit_local_env_file(monkeypatch, tmp_path):
    env_path = tmp_path / "local.env"
    env_path.write_text(
        "\n".join(
            [
                "PANEL_BASE_URL=http://127.0.0.1:8000",
                "MODULE_ID=module-local",
                "MODULE_TOKEN=token-local",
                "ACCESS_LOG_PATH=/tmp/module-access.log",
                "",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MOBGUARD_MODULE_ENV_FILE", str(env_path))

    cfg = ModuleConfig.from_env()

    assert cfg.panel_base_url == "http://127.0.0.1:8000"
    assert cfg.module_id == "module-local"
    assert cfg.module_token == "token-local"
    assert cfg.access_log_path == "/tmp/module-access.log"
