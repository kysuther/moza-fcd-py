"""System monitor: psutil for CPU/RAM/disk/network/processes, nvidia-smi for the GPU, ping for latency."""

from __future__ import annotations

import os
import re
import subprocess
import time

from . import Worker

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(args, timeout=3.0) -> str:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=NO_WINDOW).stdout


def parse_nvidia_smi(out: str):
    """'NVIDIA GeForce RTX 4080 SUPER, 22, 34, 2897, 16376' -> dict, or None."""
    line = out.strip().splitlines()[0] if out.strip() else ""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 5:
        return None
    try:
        return {"name": parts[0], "util": float(parts[1]), "temp": float(parts[2]),
                "vram_used": float(parts[3]) / 1024, "vram_total": float(parts[4]) / 1024}
    except ValueError:
        return None


def parse_ping(out: str):
    """Windows ping output -> milliseconds (float) or None."""
    m = re.search(r"time[=<](\d+)\s*ms", out, re.IGNORECASE)
    return float(m.group(1)) if m else None


class SystemService(Worker):
    name = "system"
    interval = 1.0

    def __init__(self, ping_host: str = "1.1.1.1") -> None:
        super().__init__()
        self.ping_host = ping_host
        self._last_net = None
        self._procs = {}
        self._tick = 0
        self.snapshot = {"cpu": None, "ram": None, "gpu": None, "ping": None, "disks": [], "procs": [],
                         "net_down": 0.0, "net_up": 0.0, "uptime": 0}

    def setup(self) -> None:
        import psutil
        psutil.cpu_percent(None)  # prime

    def poll(self) -> None:
        import psutil
        snap = dict(self.snapshot)
        snap["cpu"] = psutil.cpu_percent(None)
        vm = psutil.virtual_memory()
        snap["ram"] = vm.percent
        snap["ram_used"], snap["ram_total"] = vm.used / 2**30, vm.total / 2**30
        snap["uptime"] = time.time() - psutil.boot_time()

        now = time.monotonic()
        net = psutil.net_io_counters()
        if self._last_net:
            t0, sent0, recv0 = self._last_net
            dt = max(1e-3, now - t0)
            snap["net_down"] = (net.bytes_recv - recv0) / dt
            snap["net_up"] = (net.bytes_sent - sent0) / dt
        self._last_net = (now, net.bytes_sent, net.bytes_recv)

        if self._tick % 2 == 0:
            try:
                snap["gpu"] = parse_nvidia_smi(_run(["nvidia-smi", "--query-gpu=name,utilization.gpu,temperature.gpu,"
                                                     "memory.used,memory.total", "--format=csv,noheader,nounits"]))
            except (OSError, subprocess.SubprocessError):
                snap["gpu"] = None
        if self._tick % 3 == 0:
            snap["procs"] = self._top_processes(psutil)
        if self._tick % 5 == 0:
            try:
                snap["ping"] = parse_ping(_run(["ping", "-n", "1", "-w", "1000", self.ping_host]))
            except (OSError, subprocess.SubprocessError):
                snap["ping"] = None
        if self._tick % 10 == 0:
            disks = []
            for part in psutil.disk_partitions(all=False):
                if "cdrom" in part.opts or not part.fstype:
                    continue
                try:
                    u = psutil.disk_usage(part.mountpoint)
                except OSError:
                    continue
                disks.append({"name": part.device.rstrip("\\"), "total": u.total / 2**30, "free": u.free / 2**30,
                              "free_pct": 100 - u.percent})
            snap["disks"] = disks
        self._tick += 1
        self.snapshot = snap

    def _top_processes(self, psutil, count: int = 5):
        ncpu = psutil.cpu_count() or 1
        seen, rows = set(), []
        for p in psutil.process_iter(["name"]):
            seen.add(p.pid)
            proc = self._procs.setdefault(p.pid, p)
            try:
                cpu = proc.cpu_percent(None) / ncpu
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            name = (p.info.get("name") or "?")
            if p.pid in (0, 4) or p.pid in self.protected_pids(psutil) or name.lower() in ("system idle process", "idle"):
                continue
            rows.append({"pid": p.pid, "name": name, "cpu": cpu})
        self._procs = {pid: pr for pid, pr in self._procs.items() if pid in seen}
        rows.sort(key=lambda r: r["cpu"], reverse=True)
        return rows[:count]

    _protected = None

    def protected_pids(self, psutil) -> set:
        """This app's process and its ancestors (venv launcher, terminal): never listed or ended, since
        ending one of them would close the app itself."""
        if self._protected is None:
            pids = {os.getpid()}
            try:
                pids |= {p.pid for p in psutil.Process(os.getpid()).parents()}
            except Exception:
                pass
            self._protected = pids
        return self._protected

    def terminate(self, pid: int) -> None:
        def act():
            import psutil
            if pid in self.protected_pids(psutil):
                return
            psutil.Process(pid).terminate()
        self.do(act)
