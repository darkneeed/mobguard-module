from mobguard_module.config import ModuleConfig


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
