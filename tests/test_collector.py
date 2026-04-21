from pathlib import Path

from mobguard_module.collector import AccessLogCollector, parse_access_line
from mobguard_module.config import ModuleConfig
from mobguard_module.state import LocalState


def _module_config(tmp_path: Path, access_log_path: Path) -> ModuleConfig:
    return ModuleConfig(
        panel_base_url="https://panel.example.com",
        module_id="module-test",
        module_token="token-test",
        access_log_path=str(access_log_path),
        state_dir=str(tmp_path / "state"),
        spool_dir=str(tmp_path / "state" / "spool"),
        inbound_tags=("SELFSTEAL_RU-YANDEX_TCP",),
    )


def test_parse_access_line_extracts_uuid_ip_and_tag():
    payload = parse_access_line(
        "2026-01-01 accepted email: 123e4567-e89b-12d3-a456-426614174000 from tcp:1.2.3.4 SELFSTEAL_RU-YANDEX_TCP",
        ("SELFSTEAL_RU-YANDEX_TCP",),
    )

    assert payload is not None
    assert payload["uuid"] == "123e4567-e89b-12d3-a456-426614174000"
    assert payload["ip"] == "1.2.3.4"
    assert payload["tag"] == "SELFSTEAL_RU-YANDEX_TCP"
    assert "system_id" not in payload


def test_parse_access_line_maps_numeric_identifier_to_system_id():
    payload = parse_access_line(
        "2026-01-01 accepted email: 215 from tcp:1.2.3.4 SELFSTEAL_RU-YANDEX_TCP",
        ("SELFSTEAL_RU-YANDEX_TCP",),
    )

    assert payload is not None
    assert payload["system_id"] == 215
    assert payload["ip"] == "1.2.3.4"
    assert "uuid" not in payload


def test_parse_access_line_maps_non_uuid_string_to_username():
    payload = parse_access_line(
        "2026-01-01 accepted email: alice from tcp:1.2.3.4 SELFSTEAL_RU-YANDEX_TCP",
        ("SELFSTEAL_RU-YANDEX_TCP",),
    )

    assert payload is not None
    assert payload["username"] == "alice"
    assert "uuid" not in payload


def test_parse_access_line_ignores_non_matching_line():
    assert parse_access_line("denied from tcp:1.2.3.4", ("TAG",)) is None


def test_collect_once_resets_cursor_after_log_rotation(tmp_path: Path):
    access_log_path = tmp_path / "access.log"
    access_log_path.write_text(
        "2026-01-01 accepted email: 215 from tcp:1.2.3.4 SELFSTEAL_RU-YANDEX_TCP\n",
        encoding="utf-8",
    )
    config = _module_config(tmp_path, access_log_path)
    state = LocalState(config.state_dir, config.spool_dir)
    state.ensure_dirs()
    collector = AccessLogCollector(config, state)

    first_batch = collector.collect_once(config)
    assert len(first_batch) == 1

    rotated_path = tmp_path / "access.log.1"
    access_log_path.rename(rotated_path)
    access_log_path.write_text(
        "2026-01-01 accepted email: alice from tcp:5.6.7.8 SELFSTEAL_RU-YANDEX_TCP\n",
        encoding="utf-8",
    )

    second_batch = collector.collect_once(config)
    assert len(second_batch) == 1
    assert second_batch[0]["username"] == "alice"
    assert second_batch[0]["ip"] == "5.6.7.8"
