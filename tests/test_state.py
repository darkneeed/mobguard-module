from pathlib import Path

from mobguard_module.state import LocalState


def test_local_state_cursor_and_spool_roundtrip(tmp_path: Path):
    state = LocalState(str(tmp_path / "state"), str(tmp_path / "state" / "spool"))
    state.ensure_dirs()

    state.set_cursor(128)
    state.append_events([{"event_uid": "1"}, {"event_uid": "2"}], max_items=10)

    assert state.get_cursor() == 128
    assert [item["event_uid"] for item in state.read_spool(10)] == ["1", "2"]

    state.drop_spool_items(1)

    assert [item["event_uid"] for item in state.read_spool(10)] == ["2"]
