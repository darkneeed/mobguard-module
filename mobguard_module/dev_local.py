from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]
DEV_DIR = ROOT_DIR / "runtime-logs" / "local-dev"
LOG_DIR = DEV_DIR / "logs"
ENV_DIR = DEV_DIR / "env"
PID_PATH = DEV_DIR / "module.pid"
STDOUT_PATH = LOG_DIR / "module.stdout.log"
STDERR_PATH = LOG_DIR / "module.stderr.log"
DEFAULT_ACCESS_LOG_PATH = DEV_DIR / "access.log"


def _read_env_file(path: Path) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    ordered_keys: list[str] = []
    if not path.exists():
        return values, ordered_keys
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        if not normalized_key:
            continue
        if normalized_key not in ordered_keys:
            ordered_keys.append(normalized_key)
        values[normalized_key] = value.strip()
    return values, ordered_keys


def _write_env_file(path: Path, values: dict[str, str], ordered_keys: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized_keys = list(ordered_keys)
    for key in sorted(values):
        if key not in serialized_keys:
            serialized_keys.append(key)
    payload = "\n".join(f"{key}={values[key]}" for key in serialized_keys if key in values)
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def build_local_env() -> tuple[Path, dict[str, str]]:
    ENV_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_ACCESS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_ACCESS_LOG_PATH.touch(exist_ok=True)
    base_env_path = ROOT_DIR / ".env"
    if not base_env_path.exists():
        base_env_path = ROOT_DIR / ".env.example"
    base_values, ordered_keys = _read_env_file(base_env_path)
    merged_values = dict(base_values)
    override_path = ROOT_DIR / ".env.local.dev"
    if override_path.exists():
        override_values, override_keys = _read_env_file(override_path)
        for key in override_keys:
            if key not in ordered_keys:
                ordered_keys.append(key)
        merged_values.update(override_values)
    merged_values["PANEL_BASE_URL"] = "http://127.0.0.1:8000"
    if "PANEL_BASE_URL" not in ordered_keys:
        ordered_keys.append("PANEL_BASE_URL")
    if not merged_values.get("ACCESS_LOG_PATH"):
        merged_values["ACCESS_LOG_PATH"] = str(DEFAULT_ACCESS_LOG_PATH)
        if "ACCESS_LOG_PATH" not in ordered_keys:
            ordered_keys.append("ACCESS_LOG_PATH")
    env_path = ENV_DIR / "module.env"
    _write_env_file(env_path, merged_values, ordered_keys)
    return env_path, merged_values


def _is_pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _terminate_pid_tree(pid: int) -> None:
    if not _is_pid_running(pid):
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
        return

    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + 3
    while time.time() < deadline:
        if not _is_pid_running(pid):
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def stop_local_dev() -> dict[str, Any]:
    stopped = False
    pid = None
    if PID_PATH.exists():
        raw_pid = PID_PATH.read_text(encoding="utf-8", errors="ignore").strip()
        try:
            pid = int(raw_pid)
        except ValueError:
            pid = None
        if pid and _is_pid_running(pid):
            _terminate_pid_tree(pid)
            stopped = True
        PID_PATH.unlink(missing_ok=True)
    return {"pid": pid, "stopped": stopped}


def start_local_dev() -> dict[str, Any]:
    stop_local_dev()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    env_path, merged_values = build_local_env()
    spawn_env = {**os.environ, "MOBGUARD_MODULE_ENV_FILE": str(env_path), "PYTHONUNBUFFERED": "1"}
    spawn_kwargs: dict[str, Any] = {
        "cwd": str(ROOT_DIR),
        "env": spawn_env,
        "stdout": STDOUT_PATH.open("ab"),
        "stderr": STDERR_PATH.open("ab"),
    }
    if os.name == "nt":
        spawn_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        spawn_kwargs["start_new_session"] = True
    process = subprocess.Popen([sys.executable, "mobguard-module.py"], **spawn_kwargs)
    spawn_kwargs["stdout"].close()
    spawn_kwargs["stderr"].close()
    PID_PATH.write_text(f"{process.pid}\n", encoding="utf-8")
    return {
        "pid": process.pid,
        "env_path": str(env_path),
        "access_log_path": merged_values.get("ACCESS_LOG_PATH", ""),
    }


def status_local_dev() -> dict[str, Any]:
    pid = None
    if PID_PATH.exists():
        try:
            pid = int(PID_PATH.read_text(encoding="utf-8", errors="ignore").strip())
        except ValueError:
            pid = None
    return {
        "pid": pid,
        "running": bool(pid and _is_pid_running(pid)),
        "env_path": str(ENV_DIR / "module.env"),
        "stdout_log": str(STDOUT_PATH),
        "stderr_log": str(STDERR_PATH),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone local module helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Start the local module process.")
    start_parser.set_defaults(func=lambda _args: start_local_dev())

    stop_parser = subparsers.add_parser("stop", help="Stop the local module process.")
    stop_parser.set_defaults(func=lambda _args: stop_local_dev())

    status_parser = subparsers.add_parser("status", help="Show module local-dev status.")
    status_parser.set_defaults(func=lambda _args: status_local_dev())
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    payload = args.func(args)
    for key, value in payload.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
