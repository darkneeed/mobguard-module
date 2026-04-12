from mobguard_module.collector import parse_access_line


def test_parse_access_line_extracts_uuid_ip_and_tag():
    payload = parse_access_line(
        "2026-01-01 accepted email: 123e4567-e89b-12d3-a456-426614174000 from tcp:1.2.3.4 SELFSTEAL_RU-YANDEX_TCP",
        ("SELFSTEAL_RU-YANDEX_TCP",),
    )

    assert payload is not None
    assert payload["uuid"] == "123e4567-e89b-12d3-a456-426614174000"
    assert payload["ip"] == "1.2.3.4"
    assert payload["tag"] == "SELFSTEAL_RU-YANDEX_TCP"


def test_parse_access_line_ignores_non_matching_line():
    assert parse_access_line("denied from tcp:1.2.3.4", ("TAG",)) is None
