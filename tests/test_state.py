from pathlib import Path

from mobguard_module.state import LocalState


def test_local_state_cursor_and_spool_roundtrip(tmp_path: Path):
    state = LocalState(str(tmp_path / "state"), str(tmp_path / "state" / "spool"))
    state.ensure_dirs()

    state.set_cursor_state(128, "dev:inode")
    state.append_events([{"event_uid": "1"}, {"event_uid": "2"}], max_items=10)

    assert state.get_cursor() == 128
    assert state.get_cursor_state()["file_fingerprint"] == "dev:inode"
    assert state.get_spool_depth() == 2
    assert [item["event_uid"] for item in state.read_spool(10)] == ["1", "2"]

    state.drop_spool_items(1)

    assert state.get_spool_depth() == 1
    assert [item["event_uid"] for item in state.read_spool(10)] == ["2"]


def test_local_state_recovers_spool_metadata_after_restart(tmp_path: Path):
    state_dir = tmp_path / "state"
    spool_dir = state_dir / "spool"
    state = LocalState(str(state_dir), str(spool_dir))
    state.ensure_dirs()
    state.append_events([{"event_uid": "1"}, {"event_uid": "2"}, {"event_uid": "3"}], max_items=10)
    state.drop_spool_items(2)

    reloaded = LocalState(str(state_dir), str(spool_dir))
    reloaded.ensure_dirs()

    assert reloaded.get_spool_depth() == 1
    assert [item["event_uid"] for item in reloaded.read_spool(10)] == ["3"]


def test_local_state_compacts_spool_without_materializing_active_items(tmp_path: Path, monkeypatch):
    state = LocalState(str(tmp_path / "state"), str(tmp_path / "state" / "spool"))
    state.ensure_dirs()
    monkeypatch.setattr(state, "COMPACT_MIN_HEAD_BYTES", 1)
    state.append_events(
        [
            {"event_uid": "1", "payload": "x" * 128},
            {"event_uid": "2", "payload": "y" * 128},
            {"event_uid": "3", "payload": "z" * 128},
        ],
        max_items=10,
    )

    def _unexpected_read_spool(_max_items: int):
        raise AssertionError("compaction should stream the spool file directly")

    monkeypatch.setattr(state, "read_spool", _unexpected_read_spool)

    state.drop_spool_items(1)

    reloaded = LocalState(str(tmp_path / "state"), str(tmp_path / "state" / "spool"))
    reloaded.ensure_dirs()
    assert reloaded.get_spool_depth() == 2
    assert [item["event_uid"] for item in reloaded.read_spool(10)] == ["2", "3"]
