from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any


def _safe_read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _truncate(value: str, limit: int = 160) -> str:
    normalized = " ".join(str(value or "").split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: max(limit - 3, 0)]}..."


class SystemTelemetryCollector:
    def __init__(self) -> None:
        self._prev_cpu_times: tuple[int, int] | None = None
        self._prev_disk_io: tuple[int, int] | None = None
        self._prev_disk_io_time: float | None = None

    def collect(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "cpu_percent": self._read_cpu_percent(),
            "cpu_cores": int(os.cpu_count() or 0),
            "load_avg_1m": 0.0,
            "load_avg_5m": 0.0,
            "load_avg_15m": 0.0,
            "memory_total_bytes": 0,
            "memory_used_bytes": 0,
            "memory_percent": 0.0,
            "disk_total_bytes": 0,
            "disk_used_bytes": 0,
            "disk_percent": 0.0,
            "disk_read_bps": 0,
            "disk_write_bps": 0,
            "uptime_seconds": self._read_uptime_seconds(),
        }

        try:
            load_1m, load_5m, load_15m = os.getloadavg()
            payload["load_avg_1m"] = round(float(load_1m), 2)
            payload["load_avg_5m"] = round(float(load_5m), 2)
            payload["load_avg_15m"] = round(float(load_15m), 2)
        except (AttributeError, OSError):
            pass

        memory_total, memory_used = self._read_memory_bytes()
        payload["memory_total_bytes"] = memory_total
        payload["memory_used_bytes"] = memory_used
        if memory_total > 0:
            payload["memory_percent"] = round(memory_used / memory_total * 100, 1)

        disk_total, disk_used = self._read_disk_bytes()
        payload["disk_total_bytes"] = disk_total
        payload["disk_used_bytes"] = disk_used
        if disk_total > 0:
            payload["disk_percent"] = round(disk_used / disk_total * 100, 1)

        disk_read_bps, disk_write_bps = self._read_disk_io_bps()
        payload["disk_read_bps"] = disk_read_bps
        payload["disk_write_bps"] = disk_write_bps
        return payload

    def _read_cpu_percent(self) -> float:
        raw = _safe_read_text("/proc/stat")
        first_line = raw.splitlines()[0] if raw else ""
        parts = first_line.split()
        if len(parts) < 5:
            return 0.0
        values = [int(value) for value in parts[1:] if value.isdigit()]
        if len(values) < 4:
            return 0.0
        idle = values[3]
        total = sum(values)
        if self._prev_cpu_times is None:
            self._prev_cpu_times = (idle, total)
            return 0.0
        prev_idle, prev_total = self._prev_cpu_times
        self._prev_cpu_times = (idle, total)
        delta_total = max(total - prev_total, 0)
        delta_idle = max(idle - prev_idle, 0)
        if delta_total <= 0:
            return 0.0
        usage = (1.0 - delta_idle / delta_total) * 100.0
        return round(max(0.0, min(100.0, usage)), 1)

    def _read_memory_bytes(self) -> tuple[int, int]:
        raw = _safe_read_text("/proc/meminfo")
        if not raw:
            return (0, 0)
        values: dict[str, int] = {}
        for line in raw.splitlines():
            if ":" not in line:
                continue
            key, tail = line.split(":", 1)
            parts = tail.strip().split()
            if not parts:
                continue
            try:
                values[key.strip()] = int(parts[0]) * 1024
            except ValueError:
                continue
        total = int(values.get("MemTotal", 0))
        available = int(values.get("MemAvailable", 0))
        used = total - available if total and available else total
        return (total, max(used, 0))

    def _read_disk_bytes(self) -> tuple[int, int]:
        try:
            stat = os.statvfs("/")
        except OSError:
            return (0, 0)
        total = int(stat.f_blocks * stat.f_frsize)
        free = int(stat.f_bfree * stat.f_frsize)
        used = max(total - free, 0)
        return (total, used)

    def _read_disk_io_bps(self) -> tuple[int, int]:
        raw = _safe_read_text("/proc/diskstats")
        if not raw:
            return (0, 0)
        total_read_sectors = 0
        total_write_sectors = 0
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) < 14:
                continue
            device_name = parts[2]
            if not any(device_name.startswith(prefix) for prefix in ("sd", "vd", "xvd", "nvme")):
                continue
            if device_name[-1].isdigit() and not device_name.startswith("nvme"):
                continue
            if device_name.startswith("nvme") and "p" in device_name.split("n", 1)[-1]:
                continue
            try:
                total_read_sectors += int(parts[5])
                total_write_sectors += int(parts[9])
            except (TypeError, ValueError):
                continue
        sector_size = 512
        read_bytes = total_read_sectors * sector_size
        write_bytes = total_write_sectors * sector_size
        now = time.monotonic()
        if self._prev_disk_io is None or self._prev_disk_io_time is None:
            self._prev_disk_io = (read_bytes, write_bytes)
            self._prev_disk_io_time = now
            return (0, 0)
        prev_read, prev_write = self._prev_disk_io
        elapsed = now - self._prev_disk_io_time
        self._prev_disk_io = (read_bytes, write_bytes)
        self._prev_disk_io_time = now
        if elapsed <= 0:
            return (0, 0)
        return (
            max(int((read_bytes - prev_read) / elapsed), 0),
            max(int((write_bytes - prev_write) / elapsed), 0),
        )

    def _read_uptime_seconds(self) -> int:
        raw = _safe_read_text("/proc/uptime").strip()
        if not raw:
            return 0
        try:
            return max(int(float(raw.split()[0])), 0)
        except (IndexError, ValueError):
            return 0


class MobguardProcessCollector:
    PROCESS_HINTS = ("mobguard", "mobguard_module", "mobguard-module")

    def __init__(self) -> None:
        self._clock_ticks = int(os.sysconf("SC_CLK_TCK"))
        self._page_size = int(os.sysconf("SC_PAGE_SIZE"))
        self._cpu_cores = max(int(os.cpu_count() or 1), 1)
        self._prev_samples: dict[int, tuple[int, float]] = {}

    def collect(self) -> dict[str, Any]:
        snapshots: list[dict[str, Any]] = []
        matched_pids = self._matched_pids()
        next_samples: dict[int, tuple[int, float]] = {}
        now = time.monotonic()
        for pid in matched_pids:
            stat_snapshot = self._read_process_snapshot(pid, now)
            if stat_snapshot is None:
                continue
            snapshots.append(stat_snapshot)
            next_samples[pid] = (
                int(stat_snapshot["cpu_ticks_total"]),
                now,
            )
        self._prev_samples = next_samples
        top = sorted(
            snapshots,
            key=lambda item: (
                float(item.get("cpu_percent") or 0.0),
                int(item.get("rss_bytes") or 0),
            ),
            reverse=True,
        )
        return {
            "match_count": len(top),
            "cpu_percent": round(sum(float(item.get("cpu_percent") or 0.0) for item in top), 1),
            "rss_bytes": sum(int(item.get("rss_bytes") or 0) for item in top),
            "vms_bytes": sum(int(item.get("vms_bytes") or 0) for item in top),
            "top": [
                {
                    "pid": int(item["pid"]),
                    "name": str(item["name"]),
                    "cmdline": str(item["cmdline"]),
                    "cpu_percent": float(item["cpu_percent"]),
                    "rss_bytes": int(item["rss_bytes"]),
                    "vms_bytes": int(item["vms_bytes"]),
                }
                for item in top[:5]
            ],
        }

    def _matched_pids(self) -> list[int]:
        current_pid = os.getpid()
        matched = {current_pid}
        proc_root = Path("/proc")
        if not proc_root.exists():
            return [current_pid]
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if pid == current_pid:
                continue
            cmdline = _safe_read_text(str(entry / "cmdline")).replace("\x00", " ").strip()
            comm = _safe_read_text(str(entry / "comm")).strip()
            haystack = f"{comm} {cmdline}".lower()
            if any(hint in haystack for hint in self.PROCESS_HINTS):
                matched.add(pid)
        return sorted(matched)

    def _read_process_snapshot(self, pid: int, now: float) -> dict[str, Any] | None:
        stat_path = Path(f"/proc/{pid}/stat")
        status_path = Path(f"/proc/{pid}/status")
        if not stat_path.exists():
            return None
        stat_raw = _safe_read_text(str(stat_path)).strip()
        status_raw = _safe_read_text(str(status_path))
        if not stat_raw:
            return None
        try:
            _, remainder = stat_raw.split(") ", 1)
            before_name, _ = stat_raw.split(" (", 1)
            name = stat_raw[len(before_name) + 2 : stat_raw.index(")")]
        except ValueError:
            return None
        parts = remainder.split()
        if len(parts) < 22:
            return None
        try:
            utime_ticks = int(parts[11])
            stime_ticks = int(parts[12])
            vms_bytes = int(parts[20])
            rss_pages = int(parts[21])
        except ValueError:
            return None
        cpu_ticks_total = utime_ticks + stime_ticks
        rss_bytes = max(rss_pages, 0) * self._page_size
        cmdline = _safe_read_text(f"/proc/{pid}/cmdline").replace("\x00", " ").strip()
        previous = self._prev_samples.get(pid)
        cpu_percent = 0.0
        if previous is not None:
            prev_ticks, prev_time = previous
            elapsed = now - prev_time
            if elapsed > 0:
                cpu_percent = max(
                    (cpu_ticks_total - prev_ticks) / self._clock_ticks / elapsed * 100.0,
                    0.0,
                )
        max_cpu = float(self._cpu_cores * 100)
        return {
            "pid": pid,
            "name": name or "process",
            "cmdline": _truncate(cmdline or name or f"pid:{pid}"),
            "cpu_ticks_total": cpu_ticks_total,
            "cpu_percent": round(min(cpu_percent, max_cpu), 1),
            "rss_bytes": rss_bytes,
            "vms_bytes": max(vms_bytes, 0),
            "status": self._read_status_value(status_raw, "State"),
        }

    @staticmethod
    def _read_status_value(raw_status: str, key: str) -> str:
        for line in raw_status.splitlines():
            if not line.startswith(f"{key}:"):
                continue
            return line.split(":", 1)[1].strip()
        return ""


class RuntimeTelemetryCollector:
    def __init__(self) -> None:
        self.system = SystemTelemetryCollector()
        self.processes = MobguardProcessCollector()

    def collect(self) -> dict[str, Any]:
        return {
            "system": self.system.collect(),
            "processes": self.processes.collect(),
            "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
        }
