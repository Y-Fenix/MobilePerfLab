#!/usr/bin/env python3
"""
MobilePerfLab - desktop mobile performance testing console.

The app is inspired by mainstream mobile performance profilers: connect a
device, pick a process, stream metrics, mark key moments, then export a report.
It intentionally uses original branding and artwork.
"""

from __future__ import annotations

import asyncio
import csv
import html
import json
import math
import os
import queue
import random
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk


APP_NAME = "MobilePerfLab"
APP_VERSION = "0.1.0"
SAMPLE_LIMIT = 7200
DEFAULT_INTERVAL_SECONDS = 1.0
CHART_VIEW_SECONDS = 30 * 60
PROXY_BUFFER_SIZE = 16 * 1024
WEAK_NETWORK_PROFILES: dict[str, tuple[int, int, float, float, float]] = {
    "不限速": (0, 0, 0.0, 0.0, 0.0),
    "4G 良好": (40, 10, 0.0, 12_000.0, 4_000.0),
    "3G 普通": (120, 40, 0.5, 1_600.0, 768.0),
    "弱网": (300, 120, 2.0, 512.0, 256.0),
    "极弱网": (800, 300, 6.0, 128.0, 64.0),
    "电梯": (1000, 450, 10.0, 96.0, 48.0),
    "地铁": (500, 250, 4.0, 384.0, 128.0),
    "高速": (220, 120, 2.0, 1024.0, 384.0),
    "隧道": (1500, 600, 15.0, 64.0, 32.0),
}


def runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        if ".app/Contents/MacOS" in executable.as_posix():
            return executable.parents[3]
        return executable.parent
    return Path(__file__).resolve().parent


BASE_DIR = runtime_root()
EXPORT_DIR = BASE_DIR / "reports"
SCREENSHOT_DIR = BASE_DIR / "screenshots"


def ensure_dirs() -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)


def now_slug() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_name(value: str) -> str:
    cleaned = "".join(char if char not in '\\/:*?"<>|' else "_" for char in value.strip())
    return cleaned or "untitled"


def which_any(names: list[str]) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    return None


def run_command(args: list[str], timeout: float = 8.0) -> tuple[int, str]:
    try:
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
        return result.returncode, result.stdout.strip()
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        return 124, output.strip() or f"Command timed out: {' '.join(args)}"
    except Exception as exc:
        return 1, str(exc)


def resolve_adb_path() -> str | None:
    candidates = [
        shutil.which("adb"),
        str(BASE_DIR / "platform-tools" / "adb"),
        str(BASE_DIR.parent / "AndroidTools" / "platform-tools" / "adb"),
        str(BASE_DIR.parent / "platform-tools" / "adb"),
        str(Path.home() / "Library/Android/sdk/platform-tools/adb"),
        "/opt/homebrew/bin/adb",
        "/usr/local/bin/adb",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    return None


def resolve_pymobiledevice3_path() -> str | None:
    candidates = [
        shutil.which("pymobiledevice3"),
        str(BASE_DIR / ".venv" / "bin" / "pymobiledevice3"),
        str(Path.home() / "Library/Python/3.14/bin/pymobiledevice3"),
        str(Path.home() / "Library/Python/3.13/bin/pymobiledevice3"),
        str(Path.home() / "Library/Python/3.12/bin/pymobiledevice3"),
        "/opt/homebrew/bin/pymobiledevice3",
        "/usr/local/bin/pymobiledevice3",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    return None


def extract_json_payload(text: str) -> object | None:
    decoder = json.JSONDecoder()
    fallback: object | None = None
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            payload, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            if not payload or any(isinstance(item, (dict, list)) for item in payload):
                return payload
            if fallback is None:
                fallback = payload
            continue
        if fallback is None:
            fallback = payload
    return fallback


def parse_first_float(pattern: str, text: str, default: float = 0.0) -> float:
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        return default
    try:
        return float(match.group(1))
    except ValueError:
        return default


@dataclass
class DeviceInfo:
    platform: str
    serial: str
    name: str
    os_version: str
    model: str
    status: str
    detail: str = ""

    @property
    def display_name(self) -> str:
        label = self.name or self.model or self.serial
        return f"{label} · {self.platform}"


@dataclass
class PerfSample:
    timestamp: float
    elapsed: float
    fps: float = 0.0
    jank_percent: float = 0.0
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    battery_percent: float = 0.0
    temperature_c: float = 0.0
    power_w: float = 0.0
    rx_kbps: float = 0.0
    tx_kbps: float = 0.0
    note: str = ""


@dataclass(frozen=True)
class ProxyVerificationResult:
    confirmed: bool
    expected: str
    actual: str
    status_text: str
    log_text: str


@dataclass(frozen=True)
class WeakNetworkDiagnostics:
    overall_state: str
    summary: str
    rows: list[tuple[str, str, str]]


@dataclass(frozen=True)
class ProxyTrafficSnapshot:
    up_bytes: int = 0
    down_bytes: int = 0
    up_kbps: float = 0.0
    down_kbps: float = 0.0
    active_connections: int = 0
    total_connections: int = 0
    dropped_connections: int = 0
    last_activity_age: float | None = None


def normalize_android_proxy_value(value: str) -> str:
    proxy = (value or "").strip()
    if not proxy or proxy.lower() in {"null", "none", ":0", "0.0.0.0:0"}:
        return ""
    return proxy


def verify_android_proxy_state(expected_proxy: str, actual_proxy: str) -> ProxyVerificationResult:
    expected = normalize_android_proxy_value(expected_proxy)
    actual = normalize_android_proxy_value(actual_proxy)
    actual_label = actual or "未设置"
    if expected and actual == expected:
        return ProxyVerificationResult(
            confirmed=True,
            expected=expected,
            actual=actual,
            status_text=f"Android 代理已确认生效：{expected}",
            log_text=f"Android 代理读回确认：{expected}",
        )
    return ProxyVerificationResult(
        confirmed=False,
        expected=expected,
        actual=actual,
        status_text=f"Android 代理写入后未确认：期望 {expected or '未设置'}，当前{actual_label}",
        log_text=f"Android 代理写入后读回不一致：期望 {expected or '未设置'}，实际 {actual_label}",
    )


def build_weak_network_diagnostics(
    proxy_running: bool,
    endpoint: str,
    device: DeviceInfo | None,
    current_proxy: str,
    proxy_reachable: bool | None = None,
) -> WeakNetworkDiagnostics:
    rows: list[tuple[str, str, str]] = []
    normalized_proxy = normalize_android_proxy_value(current_proxy)
    endpoint = endpoint.strip()

    if proxy_running:
        rows.append(("本机代理", "运行中", endpoint))
    else:
        rows.append(("本机代理", "未启动", "先点击启动代理"))

    if device and device.platform == "Android":
        rows.append(("Android 设备", "已选择", device.name or device.serial))
        if not proxy_running:
            rows.append(("设备代理", "未检查", "启动代理后刷新状态"))
            rows.append(("端口连通", "未检查", "启动代理后检测"))
            return WeakNetworkDiagnostics("warning", "弱网代理未就绪", rows)
        verification = verify_android_proxy_state(endpoint, normalized_proxy)
        if verification.confirmed:
            rows.append(("设备代理", "已确认", normalized_proxy))
            if proxy_reachable is True:
                rows.append(("端口连通", "可达", "Android 可连接本机代理端口"))
                return WeakNetworkDiagnostics("ok", "弱网代理已确认生效，端口可达", rows)
            if proxy_reachable is False:
                rows.append(("端口连通", "不可达", "检查手机和电脑是否同一网络/防火墙"))
                return WeakNetworkDiagnostics("warning", "Android 已写入代理，但端口不可达", rows)
            rows.append(("端口连通", "未检查", "点击刷新状态检测"))
            return WeakNetworkDiagnostics("warning", "Android 代理已确认，端口未检查", rows)
        rows.append(("设备代理", "不一致", normalized_proxy or "未设置"))
        rows.append(("端口连通", "未检查", "代理读回一致后检测"))
        return WeakNetworkDiagnostics("warning", "Android 代理未确认", rows)

    rows.append(("Android 设备", "未选择", "请选择 Android 设备"))
    rows.append(("设备代理", "未检查", "选择设备后刷新状态"))
    rows.append(("端口连通", "未检查", "启动代理并选择设备后检测"))
    return WeakNetworkDiagnostics("warning", "弱网代理未就绪", rows)


def format_bytes(value: int) -> str:
    size = float(max(value, 0))
    if size < 1024:
        return f"{int(size)} B"
    units = ("KB", "MB", "GB")
    for unit in units:
        size /= 1024.0
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
    return f"{size:.1f} GB"


def format_proxy_traffic_snapshot(snapshot: ProxyTrafficSnapshot) -> dict[str, str]:
    activity = "无" if snapshot.last_activity_age is None else f"{snapshot.last_activity_age:.1f}s 前"
    return {
        "down_rate": f"{snapshot.down_kbps:.1f} KB/s",
        "up_rate": f"{snapshot.up_kbps:.1f} KB/s",
        "down_total": format_bytes(snapshot.down_bytes),
        "up_total": format_bytes(snapshot.up_bytes),
        "connections": f"{snapshot.active_connections} 活跃 / {snapshot.total_connections} 总计",
        "drops": str(snapshot.dropped_connections),
        "activity": activity,
    }


def sample_quality_tag(sample: PerfSample) -> str:
    note = sample.note or ""
    if "设备级网络兜底" in note:
        return "fallback"
    if "恢复窗口内" in note:
        return "fallback"
    if LiveQualityTracker._has_quality_issue(note):
        return "issue"
    return "ok"


def quality_intervals_from_points(points: list[tuple[float, str]]) -> list[dict[str, float | str]]:
    intervals: list[dict[str, float | str]] = []
    active_quality = "ok"
    active_start: float | None = None
    active_end: float | None = None
    for elapsed, quality in sorted(points, key=lambda item: item[0]):
        tag = quality if quality in ("issue", "fallback") else "ok"
        current = float(elapsed)
        if tag == "ok":
            if active_start is not None and active_end is not None:
                intervals.append({"start": active_start, "end": active_end, "quality": active_quality})
            active_quality = "ok"
            active_start = None
            active_end = None
            continue
        if active_start is None or active_quality != tag:
            if active_start is not None and active_end is not None:
                intervals.append({"start": active_start, "end": active_end, "quality": active_quality})
            active_quality = tag
            active_start = current
            active_end = current
        else:
            active_end = current
    if active_start is not None and active_end is not None:
        intervals.append({"start": active_start, "end": active_end, "quality": active_quality})
    return intervals


def format_report_seconds(value: float) -> str:
    return f"{float(value):.1f}s"


def quality_event_from_sample(sample: PerfSample) -> tuple[str, str, str] | None:
    tag = sample_quality_tag(sample)
    if tag == "ok":
        return None
    note = sample.note or ""
    if "恢复窗口内" in note:
        return format_report_seconds(sample.elapsed), "前台恢复窗口", "目标应用刚回到前台"
    if tag == "fallback":
        detail = "非目标 App 独占流量" if "非目标 App 独占流量" in note else "网络使用设备级兜底"
        return format_report_seconds(sample.elapsed), "设备级兜底", detail
    detail = note.split("；", 1)[0].strip() if note else "采集异常"
    if "目标应用不在前台" in note:
        detail = "目标应用不在前台"
    return format_report_seconds(sample.elapsed), "采集异常", detail[:80]


@dataclass(frozen=True)
class MetricHealth:
    state: str
    label: str
    detail: str


class MetricHealthAnalyzer:
    METRICS = (
        "fps",
        "jank_percent",
        "cpu_percent",
        "memory_mb",
        "battery_percent",
        "temperature_c",
        "power_w",
        "rx_kbps",
        "tx_kbps",
    )

    LABELS = {
        "ok": "正常",
        "waiting": "等待",
        "idle": "无流量",
        "missing": "异常",
    }

    def analyze(self, sample: PerfSample) -> dict[str, MetricHealth]:
        note = sample.note or ""
        values = asdict(sample)
        return {metric: self._metric_health(metric, float(values.get(metric, 0.0) or 0.0), sample.elapsed, note) for metric in self.METRICS}

    def _metric_health(self, metric: str, value: float, elapsed: float, note: str) -> MetricHealth:
        if self._note_marks_missing(metric, note):
            return self._health("missing", self._missing_detail(metric, note))
        if metric in ("rx_kbps", "tx_kbps"):
            if value > 0:
                return self._health("ok", "正在采集应用网络速率")
            if elapsed < 3.0:
                return self._health("waiting", "等待第二次网络采样")
            return self._health("idle", "当前没有应用网络流量")
        if value > 0:
            return self._health("ok", "指标正常采集中")
        if elapsed < 3.0:
            return self._health("waiting", "等待采样窗口稳定")
        return self._health("missing", self._missing_detail(metric, note))

    @classmethod
    def _health(cls, state: str, detail: str) -> MetricHealth:
        return MetricHealth(state, cls.LABELS.get(state, state), detail)

    @staticmethod
    def _note_marks_missing(metric: str, note: str) -> bool:
        if not note:
            return False
        if metric in ("fps", "jank_percent"):
            return "FPS 未采集" in note or "FPS 当前无帧增量" in note or "FPS 采集失败" in note
        if metric == "cpu_percent":
            return "CPU 当前无进程增量" in note or "CPU/内存" in note and "需要启动" in note
        if metric == "memory_mb":
            return "未匹配到目标 PID" in note or "未找到运行中的" in note
        if metric in ("rx_kbps", "tx_kbps"):
            if "设备级网络兜底" in note:
                return False
            network_tokens = ("网络未匹配", "无法按应用统计", "网络采集失败", "网络采集不可用")
            return any(token in note for token in network_tokens)
        if metric == "power_w":
            return "功耗" in note and ("失败" in note or "不可用" in note)
        return False

    @staticmethod
    def _missing_detail(metric: str, note: str) -> str:
        if metric in ("fps", "jank_percent"):
            return "未拿到帧数据，请保持目标页面可见"
        if metric == "cpu_percent":
            return "未拿到进程 CPU 增量"
        if metric == "memory_mb":
            return "未匹配到目标进程内存"
        if metric == "battery_percent":
            return "未拿到电量信息"
        if metric == "temperature_c":
            return "未拿到温度信息"
        if metric == "power_w":
            return "未拿到电流/电压，功耗不可估算"
        if metric in ("rx_kbps", "tx_kbps"):
            return "未拿到应用 UID 网络统计"
        return note or "暂未采集到数据"


class LiveQualityTracker:
    def __init__(self) -> None:
        self.sample_count = 0
        self.issue_count = 0
        self.network_fallback_count = 0
        self.network_missing_count = 0
        self.network_source = "等待数据"

    def reset(self) -> None:
        self.sample_count = 0
        self.issue_count = 0
        self.network_fallback_count = 0
        self.network_missing_count = 0
        self.network_source = "等待数据"

    def update(self, sample: PerfSample) -> str:
        self.sample_count += 1
        note = sample.note or ""
        has_issue = self._has_quality_issue(note)
        if has_issue:
            self.issue_count += 1
        if "设备级网络兜底" in note:
            self.network_fallback_count += 1
        if "网络未匹配" in note or "无法按应用统计" in note or "网络采集失败" in note or "网络采集不可用" in note:
            self.network_missing_count += 1
        self.network_source = self._network_source(sample, note)
        return self.status_text()

    def status_text(self) -> str:
        total = max(self.sample_count, 1)
        issue_percent = self.issue_count / total * 100.0
        fallback_percent = self.network_fallback_count / total * 100.0
        return (
            f"网络来源：{self.network_source} · "
            f"异常样本 {self.issue_count}/{self.sample_count} ({issue_percent:.1f}%) · "
            f"兜底 {self.network_fallback_count}/{self.sample_count} ({fallback_percent:.1f}%)"
        )

    @staticmethod
    def _has_quality_issue(note: str) -> bool:
        if not note:
            return False
        if "设备级网络兜底" in note and "；" not in note:
            return False
        if "目标应用刚回到前台" in note:
            return False
        tokens = (
            "未采集",
            "无帧增量",
            "无进程增量",
            "未匹配",
            "无法按应用统计",
            "采集失败",
            "采集不可用",
            "未找到运行中的",
            "不在前台",
        )
        return any(token in note for token in tokens)

    @staticmethod
    def _network_source(sample: PerfSample, note: str) -> str:
        if "设备级网络兜底" in note:
            return "设备级兜底"
        if "恢复窗口内" in note:
            return "前台恢复窗口"
        if "网络未匹配" in note or "无法按应用统计" in note or "网络采集失败" in note or "网络采集不可用" in note:
            return "per-UID 不可用"
        if sample.rx_kbps > 0 or sample.tx_kbps > 0:
            return "目标 App per-UID"
        if sample.elapsed < 3.0:
            return "等待网络采样"
        return "无流量"


class MetricStabilizer:
    """Display-only smoothing; raw samples stay unchanged for reports."""

    ALPHA_BY_METRIC = {
        "fps": 0.42,
        "jank_percent": 0.5,
        "cpu_percent": 0.34,
        "memory_mb": 0.25,
        "battery_percent": 0.2,
        "temperature_c": 0.2,
        "power_w": 0.3,
        "rx_kbps": 0.48,
        "tx_kbps": 0.48,
    }
    ZERO_HOLD_SECONDS = {
        "fps": 3.0,
        "cpu_percent": 3.0,
    }
    SPIKE_KEEP_BY_METRIC = {
        "fps": 0.55,
        "jank_percent": 0.75,
        "cpu_percent": 0.7,
        "memory_mb": 0.9,
        "battery_percent": 1.0,
        "temperature_c": 0.9,
        "power_w": 0.7,
        "rx_kbps": 0.9,
        "tx_kbps": 0.9,
    }
    MAX_STEP_RATIO_BY_METRIC = {
        "fps": (0.14, 0.22),
        "cpu_percent": (0.28, 0.55),
        "power_w": (0.35, 0.55),
        "rx_kbps": (0.75, 1.2),
        "tx_kbps": (0.75, 1.2),
    }

    def __init__(self) -> None:
        self._values: dict[str, float] = {}
        self._timestamps: dict[str, float] = {}

    def reset(self) -> None:
        self._values.clear()
        self._timestamps.clear()

    def smooth_sample(self, sample: PerfSample) -> PerfSample:
        payload = asdict(sample)
        for metric in self.ALPHA_BY_METRIC:
            payload[metric] = self._smooth(metric, float(payload.get(metric, 0.0) or 0.0), sample.timestamp)
        return PerfSample(**payload)

    def _smooth(self, metric: str, value: float, timestamp: float) -> float:
        previous = self._values.get(metric)
        if value <= 0 and previous and previous > 0:
            previous_timestamp = self._timestamps.get(metric, timestamp)
            hold_seconds = self.ZERO_HOLD_SECONDS.get(metric, 0.0)
            if hold_seconds and timestamp - previous_timestamp <= hold_seconds:
                held = previous * 0.82
                self._values[metric] = held
                return held
        if previous is None or previous <= 0 or value <= 0:
            self._values[metric] = value
            self._timestamps[metric] = timestamp
            return value
        alpha = self.ALPHA_BY_METRIC.get(metric, 0.35)
        blended = previous + alpha * (value - previous)
        delta_ratio = abs(value - previous) / max(abs(previous), 1.0)
        if delta_ratio > 0.35:
            keep = self.SPIKE_KEEP_BY_METRIC.get(metric, 0.7)
            blended = blended * (1.0 - keep) + value * keep
        blended = self._limit_display_step(metric, previous, blended)
        self._values[metric] = blended
        self._timestamps[metric] = timestamp
        return max(blended, 0.0)

    def _limit_display_step(self, metric: str, previous: float, value: float) -> float:
        limits = self.MAX_STEP_RATIO_BY_METRIC.get(metric)
        if not limits or previous <= 0 or value <= 0:
            return value
        down_ratio, up_ratio = limits
        lower = previous * max(0.0, 1.0 - down_ratio)
        upper = previous * (1.0 + up_ratio)
        return min(max(value, lower), upper)


class WeakNetworkProxy:
    def __init__(self, log_callback) -> None:
        self.log_callback = log_callback
        self.host = "0.0.0.0"
        self.port = 18888
        self.enabled = False
        self.latency_ms = 0
        self.jitter_ms = 0
        self.loss_percent = 0.0
        self.down_kbps = 0.0
        self.up_kbps = 0.0
        self._server_socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._traffic_lock = threading.Lock()
        self._traffic_up_bytes = 0
        self._traffic_down_bytes = 0
        self._traffic_active_connections = 0
        self._traffic_total_connections = 0
        self._traffic_dropped_connections = 0
        self._traffic_last_activity: float | None = None
        self._traffic_rate_base_time = time.time()
        self._traffic_rate_base_up = 0
        self._traffic_rate_base_down = 0

    def configure(
        self,
        port: int,
        latency_ms: int,
        jitter_ms: int,
        loss_percent: float,
        down_kbps: float,
        up_kbps: float,
    ) -> None:
        with self._lock:
            self.port = max(1024, min(int(port), 65535))
            self.latency_ms = max(0, int(latency_ms))
            self.jitter_ms = max(0, int(jitter_ms))
            self.loss_percent = max(0.0, min(float(loss_percent), 100.0))
            self.down_kbps = max(0.0, float(down_kbps))
            self.up_kbps = max(0.0, float(up_kbps))

    def start(self) -> None:
        if self.is_running():
            self.enabled = True
            return
        self._stop_event.clear()
        self.enabled = True
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self.host, self.port))
        server.listen(128)
        server.settimeout(0.5)
        self._server_socket = server
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        self.log_callback(f"弱网代理已启动：{self.local_endpoint()}")

    def stop(self) -> None:
        was_running = self.is_running()
        self.enabled = False
        self._stop_event.set()
        server = self._server_socket
        self._server_socket = None
        if server:
            try:
                server.close()
            except OSError:
                pass
        if was_running:
            self.log_callback("弱网代理已停止。")

    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._server_socket)

    def local_endpoint(self) -> str:
        return f"{self._host_lan_ip()}:{self.port}"

    def reset_traffic(self, now: float | None = None) -> None:
        timestamp = time.time() if now is None else now
        with self._traffic_lock:
            self._traffic_up_bytes = 0
            self._traffic_down_bytes = 0
            self._traffic_active_connections = 0
            self._traffic_total_connections = 0
            self._traffic_dropped_connections = 0
            self._traffic_last_activity = None
            self._traffic_rate_base_time = timestamp
            self._traffic_rate_base_up = 0
            self._traffic_rate_base_down = 0

    def traffic_snapshot(self, now: float | None = None) -> ProxyTrafficSnapshot:
        timestamp = time.time() if now is None else now
        with self._traffic_lock:
            elapsed = max(timestamp - self._traffic_rate_base_time, 0.0)
            up_bytes = self._traffic_up_bytes
            down_bytes = self._traffic_down_bytes
            if elapsed <= 0 or elapsed > 3.0:
                up_kbps = 0.0
                down_kbps = 0.0
            else:
                up_kbps = max(up_bytes - self._traffic_rate_base_up, 0) / 1024.0 / elapsed
                down_kbps = max(down_bytes - self._traffic_rate_base_down, 0) / 1024.0 / elapsed
            last_age = None if self._traffic_last_activity is None else max(timestamp - self._traffic_last_activity, 0.0)
            self._traffic_rate_base_time = timestamp
            self._traffic_rate_base_up = up_bytes
            self._traffic_rate_base_down = down_bytes
            return ProxyTrafficSnapshot(
                up_bytes=up_bytes,
                down_bytes=down_bytes,
                up_kbps=up_kbps,
                down_kbps=down_kbps,
                active_connections=self._traffic_active_connections,
                total_connections=self._traffic_total_connections,
                dropped_connections=self._traffic_dropped_connections,
                last_activity_age=last_age,
            )

    def _record_connection_open(self, now: float | None = None) -> None:
        timestamp = time.time() if now is None else now
        with self._traffic_lock:
            self._traffic_active_connections += 1
            self._traffic_total_connections += 1
            self._traffic_last_activity = timestamp

    def _record_connection_close(self) -> None:
        with self._traffic_lock:
            self._traffic_active_connections = max(self._traffic_active_connections - 1, 0)

    def _record_dropped_connection(self, now: float | None = None) -> None:
        timestamp = time.time() if now is None else now
        with self._traffic_lock:
            self._traffic_dropped_connections += 1
            self._traffic_last_activity = timestamp

    def _record_transfer(self, direction: str, size: int, now: float | None = None) -> None:
        if size <= 0:
            return
        timestamp = time.time() if now is None else now
        with self._traffic_lock:
            if direction == "up":
                self._traffic_up_bytes += size
            else:
                self._traffic_down_bytes += size
            self._traffic_last_activity = timestamp

    @staticmethod
    def _host_lan_ip() -> str:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            sock.close()

    def _serve(self) -> None:
        while not self._stop_event.is_set():
            server = self._server_socket
            if not server:
                break
            try:
                client, address = server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._handle_client, args=(client, address), daemon=True).start()

    def _handle_client(self, client: socket.socket, address: tuple[str, int]) -> None:
        client.settimeout(12)
        remote: socket.socket | None = None
        counted_connection = False
        try:
            header = self._recv_header(client)
            if not header:
                return
            first_line = header.split(b"\r\n", 1)[0].decode("iso-8859-1", errors="replace")
            parts = first_line.split()
            if len(parts) < 2:
                return
            method = parts[0].upper()
            target = parts[1]
            if self._is_health_check_request(header):
                client.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/plain; charset=utf-8\r\n"
                    b"Content-Length: 16\r\n"
                    b"Connection: close\r\n"
                    b"\r\n"
                    b"mobileperflab-ok"
                )
                return
            self._record_connection_open()
            counted_connection = True
            if self._should_drop_connection():
                self._record_dropped_connection()
                self.log_callback(f"弱网丢弃连接：{address[0]} -> {target}")
                return
            if method == "CONNECT":
                host, port = self._split_host_port(target, 443)
                remote = socket.create_connection((host, port), timeout=12)
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            else:
                host, port = self._host_from_http_header(target, header)
                if not host:
                    return
                remote = socket.create_connection((host, port), timeout=12)
                rewritten = self._rewrite_http_request(header, target)
                remote.sendall(rewritten)
                self._record_transfer("up", len(rewritten))
            remote.settimeout(12)
            self._pipe_bidirectional(client, remote)
        except Exception as exc:
            if not self._stop_event.is_set():
                self.log_callback(f"弱网代理连接失败：{self._short_error(str(exc))}")
        finally:
            if counted_connection:
                self._record_connection_close()
            for sock in (client, remote):
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass

    @staticmethod
    def _short_error(text: str) -> str:
        text = text.strip().replace("\n", " ")
        return text[:120] if text else "未知错误"

    @staticmethod
    def _is_health_check_request(header: bytes) -> bool:
        first_line = header.split(b"\r\n", 1)[0].decode("iso-8859-1", errors="replace")
        parts = first_line.split()
        if len(parts) < 2:
            return False
        method, target = parts[0].upper(), parts[1]
        return method == "GET" and target.startswith("/__mobileperflab_health")

    @staticmethod
    def _recv_header(sock: socket.socket) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while total < 64 * 1024:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
            total += len(data)
            if b"\r\n\r\n" in data or b"\n\n" in data:
                break
        return b"".join(chunks)

    @staticmethod
    def _split_host_port(value: str, default_port: int) -> tuple[str, int]:
        if value.startswith("[") and "]" in value:
            host, _, rest = value[1:].partition("]")
            port_text = rest[1:] if rest.startswith(":") else ""
            return host, int(port_text or default_port)
        if ":" in value:
            host, port_text = value.rsplit(":", 1)
            if port_text.isdigit():
                return host, int(port_text)
        return value, default_port

    def _host_from_http_header(self, target: str, header: bytes) -> tuple[str, int]:
        if target.startswith("http://") or target.startswith("https://"):
            parsed = urllib.parse.urlparse(target)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            return parsed.hostname or "", port
        match = re.search(rb"(?im)^Host:\s*([^\r\n]+)", header)
        if not match:
            return "", 80
        host_value = match.group(1).decode("iso-8859-1", errors="replace").strip()
        return self._split_host_port(host_value, 80)

    @staticmethod
    def _rewrite_http_request(header: bytes, target: str) -> bytes:
        if not (target.startswith("http://") or target.startswith("https://")):
            return header
        parsed = urllib.parse.urlparse(target)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        first, sep, rest = header.partition(b"\r\n")
        parts = first.decode("iso-8859-1", errors="replace").split()
        if len(parts) >= 3:
            first = f"{parts[0]} {path} {parts[2]}".encode("iso-8859-1")
        return first + sep + rest

    def _pipe_bidirectional(self, client: socket.socket, remote: socket.socket) -> None:
        done = threading.Event()
        up = threading.Thread(target=self._pipe, args=(client, remote, "up", done), daemon=True)
        down = threading.Thread(target=self._pipe, args=(remote, client, "down", done), daemon=True)
        up.start()
        down.start()
        while not done.is_set() and not self._stop_event.is_set():
            done.wait(0.2)

    def _pipe(self, source: socket.socket, target: socket.socket, direction: str, done: threading.Event) -> None:
        try:
            while not done.is_set() and not self._stop_event.is_set():
                data = source.recv(PROXY_BUFFER_SIZE)
                if not data:
                    break
                self._shape_before_send(direction, len(data))
                target.sendall(data)
                self._record_transfer(direction, len(data))
        except OSError:
            pass
        finally:
            done.set()
            try:
                target.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def _snapshot(self) -> tuple[bool, int, int, float, float, float]:
        with self._lock:
            return (
                self.enabled,
                self.latency_ms,
                self.jitter_ms,
                self.loss_percent,
                self.down_kbps,
                self.up_kbps,
            )

    def _should_drop_connection(self) -> bool:
        enabled, _latency, _jitter, loss, _down, _up = self._snapshot()
        return enabled and loss > 0 and random.random() < loss / 100.0

    def _shape_before_send(self, direction: str, size: int) -> None:
        enabled, latency, jitter, _loss, down, up = self._snapshot()
        if not enabled:
            return
        delay = latency / 1000.0
        if jitter:
            delay += random.uniform(0.0, jitter / 1000.0)
        rate = up if direction == "up" else down
        if rate > 0:
            delay += size / max(rate * 1024.0, 1.0)
        if delay > 0:
            time.sleep(min(delay, 5.0))


class WeakProxyDeviceRegistry:
    def __init__(self) -> None:
        self._devices: dict[str, tuple[DeviceInfo, str]] = {}

    def mark_applied(self, device: DeviceInfo, proxy: str) -> None:
        if device.platform == "Android":
            self._devices[device.serial] = (device, proxy)

    def mark_cleared(self, device: DeviceInfo) -> None:
        self._devices.pop(device.serial, None)

    def cleanup(self, android_adapter: object) -> list[str]:
        cleared: list[str] = []
        for serial, (device, _proxy) in list(self._devices.items()):
            try:
                ok, _detail = android_adapter.clear_http_proxy(device)  # type: ignore[attr-defined]
            except Exception:
                ok = False
            if ok:
                cleared.append(serial)
                self._devices.pop(serial, None)
        return cleared

    def active_devices(self) -> list[DeviceInfo]:
        return [device for device, _proxy in self._devices.values()]


class BaseAdapter:
    platform_name = "Base"

    def is_available(self) -> bool:
        return True

    def capability_note(self) -> str:
        return ""

    def list_devices(self) -> list[DeviceInfo]:
        return []

    def list_apps(self, device: DeviceInfo) -> list[str]:
        return []

    def foreground_app(self, device: DeviceInfo) -> str:
        return ""

    def start_session(self, device: DeviceInfo, app_id: str) -> None:
        return None

    def stop_session(self, device: DeviceInfo, app_id: str) -> None:
        return None

    def collect_sample(self, device: DeviceInfo, app_id: str, start_time: float) -> PerfSample:
        raise NotImplementedError

    def capture_screenshot(self, device: DeviceInfo, target: Path) -> Path | None:
        return None


class AndroidAdapter(BaseAdapter):
    platform_name = "Android"

    def __init__(self) -> None:
        self.adb_path = resolve_adb_path()
        self._frame_cache: dict[tuple[str, str], tuple[float, int, int]] = {}
        self._framestats_cache: dict[tuple[str, str], tuple[float, int]] = {}
        self._surface_frame_cache: dict[tuple[str, str], tuple[float, int]] = {}
        self._surface_cache: dict[tuple[str, str], str] = {}
        self._net_cache: dict[tuple[str, str], tuple[float, int, int]] = {}
        self._device_net_cache: dict[tuple[str, str], tuple[float, int, int]] = {}
        self._network_note_cache: dict[tuple[str, str], str] = {}
        self._uid_cache: dict[tuple[str, str], int] = {}
        self._pid_cache: dict[tuple[str, str], int] = {}
        self._pid_list_cache: dict[tuple[str, str], list[int]] = {}
        self._cpu_proc_cache: dict[tuple[str, str], tuple[float, dict[int, int]]] = {}
        self._clk_tck_cache: dict[str, int] = {}
        self._sample_count: dict[tuple[str, str], int] = {}
        self._foreground_missing: set[tuple[str, str]] = set()
        self._foreground_recovery_remaining: dict[tuple[str, str], int] = {}

    def is_available(self) -> bool:
        return self.adb_path is not None

    def capability_note(self) -> str:
        if self.adb_path:
            return f"ADB: {self.adb_path}"
        return "未找到 adb，可通过 AndroidTools/安装ADB.command 安装或加入 PATH。"

    def _adb(self, serial: str, shell_args: list[str], timeout: float = 8.0) -> tuple[int, str]:
        if not self.adb_path:
            return 1, "adb not found"
        return run_command([self.adb_path, "-s", serial, *shell_args], timeout=timeout)

    def set_http_proxy(self, device: DeviceInfo, host: str, port: int) -> tuple[bool, str]:
        proxy = f"{host}:{int(port)}"
        code, output = self._adb(
            device.serial,
            ["shell", "settings", "put", "global", "http_proxy", proxy],
            timeout=5.0,
        )
        if code != 0:
            return False, output or "settings put global http_proxy failed"
        return True, proxy

    def clear_http_proxy(self, device: DeviceInfo) -> tuple[bool, str]:
        commands = [
            ["shell", "settings", "put", "global", "http_proxy", ":0"],
            ["shell", "settings", "delete", "global", "http_proxy"],
            ["shell", "settings", "delete", "global", "global_http_proxy_host"],
            ["shell", "settings", "delete", "global", "global_http_proxy_port"],
        ]
        last_output = ""
        ok = True
        for command in commands:
            code, output = self._adb(device.serial, command, timeout=4.0)
            last_output = output or last_output
            if code not in (0, 255):
                ok = False
        return ok, last_output

    def current_http_proxy(self, device: DeviceInfo) -> str:
        return self._shell(device.serial, "settings get global http_proxy", timeout=3.0).strip()

    def probe_tcp_connectivity(self, device: DeviceInfo, host: str, port: int) -> tuple[bool, str]:
        safe_host = shlex.quote(host)
        safe_port = int(port)
        health_url = shlex.quote(f"http://{host}:{safe_port}/__mobileperflab_health")
        tcp_probes = [
            f"toybox nc -z -w 2 {safe_host} {safe_port}",
            f"nc -z -w 2 {safe_host} {safe_port}",
        ]
        last_output = ""
        for command in tcp_probes:
            code, output = self._adb(device.serial, ["shell", command], timeout=4.0)
            last_output = output or last_output
            if code == 0:
                return True, f"{host}:{safe_port}"
        http_probes = [
            f"curl -fsS --max-time 3 {health_url}",
            f"toybox wget -T 3 -q -O - {health_url}",
            f"wget -T 3 -q -O - {health_url}",
        ]
        for command in http_probes:
            code, output = self._adb(device.serial, ["shell", command], timeout=5.0)
            last_output = output or last_output
            if code == 0 and "mobileperflab-ok" in output:
                return True, f"{host}:{safe_port}"
        detail = WeakNetworkProxy._short_error(last_output) if last_output else f"{host}:{safe_port} unreachable"
        return False, detail

    def _shell(self, serial: str, command: str, timeout: float = 8.0) -> str:
        code, output = self._adb(serial, ["shell", command], timeout=timeout)
        return output if code == 0 else ""

    def list_devices(self) -> list[DeviceInfo]:
        if not self.adb_path:
            return []
        code, output = run_command([self.adb_path, "devices", "-l"], timeout=6.0)
        if code != 0:
            return []
        devices: list[DeviceInfo] = []
        for raw_line in output.splitlines()[1:]:
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split()
            serial = parts[0]
            status = parts[1] if len(parts) > 1 else "unknown"
            detail = " ".join(parts[2:])
            if status != "device":
                devices.append(DeviceInfo("Android", serial, serial, "", "", status, detail))
                continue
            model = self._shell(serial, "getprop ro.product.model", timeout=3.0).strip()
            brand = self._shell(serial, "getprop ro.product.brand", timeout=3.0).strip()
            version = self._shell(serial, "getprop ro.build.version.release", timeout=3.0).strip()
            name = " ".join(part for part in (brand, model) if part).strip() or serial
            devices.append(DeviceInfo("Android", serial, name, version, model, "ready", detail))
        return devices

    def list_apps(self, device: DeviceInfo) -> list[str]:
        output = self._shell(device.serial, "pm list packages -3", timeout=12.0)
        apps = [line.split(":", 1)[-1].strip() for line in output.splitlines() if line.strip()]
        if not apps:
            output = self._shell(device.serial, "pm list packages", timeout=12.0)
            apps = [line.split(":", 1)[-1].strip() for line in output.splitlines() if line.strip()]
        return sorted(set(apps))

    def foreground_app(self, device: DeviceInfo) -> str:
        for command, timeout in (
            ("dumpsys window", 6.0),
            ("dumpsys activity activities", 7.0),
            ("cmd activity get-foreground-activities", 5.0),
        ):
            output = self._shell(device.serial, command, timeout=timeout)
            app_id = self._parse_foreground_app(output)
            if app_id:
                return app_id
        return ""

    @classmethod
    def _parse_foreground_app(cls, output: str) -> str:
        preferred_tokens = (
            "mCurrentFocus",
            "mFocusedApp",
            "topResumedActivity",
            "mResumedActivity",
            "ResumedActivity",
            "ACTIVITY",
        )
        for line in output.splitlines():
            if not any(token in line for token in preferred_tokens):
                continue
            app_id = cls._package_from_activity_line(line)
            if app_id:
                return app_id
        return cls._package_from_activity_line(output)

    @staticmethod
    def _package_from_activity_line(text: str) -> str:
        patterns = (
            r"\bu\d+\s+([a-zA-Z][\w.]+)/[A-Za-z0-9_.$]+",
            r"\b([a-zA-Z][\w.]+)/(?:[A-Za-z0-9_.$]+)",
            r"Splash Screen\s+([a-zA-Z][\w.]+)",
            r"\bpackageName=([a-zA-Z][\w.]+)",
            r"\bcmp=([a-zA-Z][\w.]+)/[A-Za-z0-9_.$]+",
        )
        ignored_prefixes = ("android.", "com.android.", "com.google.android.")
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                package = match.group(1)
                if "." not in package or package.startswith(ignored_prefixes):
                    continue
                return package
        return ""

    def start_session(self, device: DeviceInfo, app_id: str) -> None:
        if app_id:
            self._shell(device.serial, f"dumpsys gfxinfo {shlex.quote(app_id)} reset", timeout=4.0)
        key = (device.serial, app_id)
        self._frame_cache.pop(key, None)
        self._framestats_cache.pop(key, None)
        self._surface_frame_cache.pop(key, None)
        self._surface_cache.pop(key, None)
        self._net_cache.pop(key, None)
        self._device_net_cache.pop(key, None)
        self._network_note_cache.pop(key, None)
        self._pid_cache.pop(key, None)
        self._pid_list_cache.pop(key, None)
        self._cpu_proc_cache.pop(key, None)
        self._sample_count.pop(key, None)
        self._foreground_missing.discard(key)
        self._foreground_recovery_remaining.pop(key, None)
        surface = self._surface_name(device, app_id) if app_id else ""
        if surface:
            self._shell(device.serial, f"dumpsys SurfaceFlinger --latency-clear {shlex.quote(surface)}", timeout=3.0)

    def _cpu_percent(self, device: DeviceInfo, app_id: str) -> float:
        if not app_id:
            return 0.0
        proc_cpu = self._cpu_percent_from_proc(device, app_id)
        if proc_cpu is not None:
            return proc_cpu
        output = self._shell(device.serial, f"dumpsys cpuinfo {app_id}", timeout=5.0)
        best = 0.0
        escaped = re.escape(app_id)
        for line in output.splitlines():
            if app_id not in line:
                continue
            match = re.search(r"(\d+(?:\.\d+)?)%\s+\d+/" + escaped, line)
            if match:
                best = max(best, float(match.group(1)))
                continue
            match = re.search(r"(\d+(?:\.\d+)?)%", line)
            if match:
                best = max(best, float(match.group(1)))
        return best

    def _process_pid(self, device: DeviceInfo, app_id: str) -> int | None:
        pids = self._process_pids(device, app_id)
        return pids[0] if pids else None

    def _process_pids(self, device: DeviceInfo, app_id: str) -> list[int]:
        key = (device.serial, app_id)
        cached = self._pid_list_cache.get(key)
        if cached:
            return cached
        output = self._shell(device.serial, f"pidof {shlex.quote(app_id)}", timeout=2.0)
        pids = self._parse_pid_list(output)
        if not pids:
            output = self._shell(device.serial, f"pgrep -f {shlex.quote(app_id)}", timeout=2.0)
            pids = self._parse_pid_list(output)
        if pids:
            self._pid_list_cache[key] = pids
            self._pid_cache[key] = pids[0]
        return pids

    @staticmethod
    def _parse_pid_list(output: str) -> list[int]:
        pids: list[int] = []
        seen: set[int] = set()
        for value in re.findall(r"\b\d+\b", output):
            pid = int(value)
            if pid <= 0 or pid in seen:
                continue
            seen.add(pid)
            pids.append(pid)
        return pids

    def _cpu_percent_from_proc(self, device: DeviceInfo, app_id: str) -> float | None:
        pids = self._process_pids(device, app_id)
        if not pids:
            return None
        process_jiffies: dict[int, int] = {}
        for pid in pids:
            stat = self._shell(device.serial, f"cat /proc/{pid}/stat", timeout=2.0)
            jiffies = self._jiffies_from_proc_stat(stat)
            if jiffies is not None:
                process_jiffies[pid] = jiffies
        if not process_jiffies:
            self._pid_cache.pop((device.serial, app_id), None)
            self._pid_list_cache.pop((device.serial, app_id), None)
            return None
        clk_tck = self._clock_ticks_per_second(device)
        if clk_tck <= 0:
            return None
        key = (device.serial, app_id)
        now = time.time()
        previous = self._cpu_proc_cache.get(key)
        self._cpu_proc_cache[key] = (now, process_jiffies)
        if not previous:
            return None
        previous_time, previous_jiffies_by_pid = previous
        elapsed = max(now - previous_time, 0.1)
        delta_jiffies = 0
        for pid, jiffies in process_jiffies.items():
            previous_jiffies = previous_jiffies_by_pid.get(pid)
            if previous_jiffies is None:
                continue
            delta_jiffies += max(jiffies - previous_jiffies, 0)
        return min((delta_jiffies / clk_tck) / elapsed * 100.0, 1000.0)

    @staticmethod
    def _jiffies_from_proc_stat(stat: str) -> int | None:
        try:
            after_name = stat.rsplit(") ", 1)[1].split()
            return int(after_name[11]) + int(after_name[12])
        except Exception:
            return None

    def _clock_ticks_per_second(self, device: DeviceInfo) -> int:
        cached = self._clk_tck_cache.get(device.serial)
        if cached:
            return cached
        output = self._shell(device.serial, "getconf CLK_TCK", timeout=2.0)
        try:
            value = int(re.findall(r"\d+", output)[0])
        except Exception:
            value = 100
        self._clk_tck_cache[device.serial] = value
        return value

    def _memory_mb(self, device: DeviceInfo, app_id: str) -> float:
        if not app_id:
            return 0.0
        output = self._shell(device.serial, f"dumpsys meminfo {app_id}", timeout=7.0)
        patterns = [
            r"TOTAL\s+PSS:\s+(\d+)",
            r"TOTAL:\s+(\d+)",
            r"^\s*TOTAL\s+(\d+)",
        ]
        for pattern in patterns:
            value = parse_first_float(pattern, output, -1.0)
            if value >= 0:
                return value / 1024.0
        return 0.0

    def _battery(self, device: DeviceInfo) -> tuple[float, float, float]:
        output = self._shell(device.serial, "dumpsys battery", timeout=4.0)
        level = parse_first_float(r"level:\s*(\d+)", output)
        temp_raw = parse_first_float(r"temperature:\s*(-?\d+)", output)
        temperature = temp_raw / 10.0 if temp_raw else 0.0
        voltage_mv = parse_first_float(r"voltage:\s*(\d+)", output)
        current_raw_text = self._shell(device.serial, "cat /sys/class/power_supply/battery/current_now", timeout=2.0).strip()
        voltage_raw_text = self._shell(device.serial, "cat /sys/class/power_supply/battery/voltage_now", timeout=2.0).strip()
        power_w = 0.0
        try:
            current_values = re.findall(r"-?\d+", current_raw_text)
            if current_values:
                current_micro_amp = abs(float(current_values[0]))
            else:
                current_micro_amp = abs(parse_first_float(r"current now:\s*(-?\d+)", output))
            voltage_values = re.findall(r"\d+", voltage_raw_text)
            voltage_micro_v = float(voltage_values[0]) if voltage_values else voltage_mv * 1000.0
            if 0 < voltage_micro_v < 100_000:
                voltage_micro_v *= 1000.0
            power_w = (current_micro_amp / 1_000_000.0) * (voltage_micro_v / 1_000_000.0)
        except Exception:
            power_w = 0.0
        return level, temperature, power_w

    def _app_uid(self, device: DeviceInfo, app_id: str) -> int | None:
        key = (device.serial, app_id)
        if key in self._uid_cache:
            return self._uid_cache[key]
        output = self._shell(device.serial, f"dumpsys package {app_id}", timeout=5.0)
        match = re.search(r"userId=(\d+)", output)
        if not match:
            match = re.search(r"appId=(\d+)", output)
        if not match:
            return None
        uid = int(match.group(1))
        self._uid_cache[key] = uid
        return uid

    def _net_totals(self, device: DeviceInfo, app_id: str) -> tuple[int, int]:
        uid = self._app_uid(device, app_id) if app_id else None
        if uid is None:
            return 0, 0
        rx_text = self._shell(device.serial, f"cat /proc/uid_stat/{uid}/tcp_rcv", timeout=2.0).strip()
        tx_text = self._shell(device.serial, f"cat /proc/uid_stat/{uid}/tcp_snd", timeout=2.0).strip()
        try:
            return int(re.findall(r"\d+", rx_text)[0]), int(re.findall(r"\d+", tx_text)[0])
        except Exception:
            pass
        output = self._shell(device.serial, "cat /proc/net/xt_qtaguid/stats", timeout=4.0)
        rx_total, tx_total = self._parse_qtaguid_stats(output, uid)
        if rx_total or tx_total:
            return rx_total, tx_total
        return self._net_totals_from_netstats(device, uid)

    def _net_totals_from_netstats(self, device: DeviceInfo, uid: int) -> tuple[int, int]:
        output = self._shell(device.serial, "dumpsys netstats detail", timeout=6.0)
        return self._parse_netstats_detail_for_uid(output, uid)

    @staticmethod
    def _parse_qtaguid_stats(output: str, uid: int) -> tuple[int, int]:
        rx_total = 0
        tx_total = 0
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 8 or not parts[0].isdigit():
                continue
            try:
                if int(parts[3]) != uid:
                    continue
                rx_total += int(parts[5])
                tx_total += int(parts[7])
            except Exception:
                continue
        return rx_total, tx_total

    @classmethod
    def _parse_netstats_detail_for_uid(cls, output: str, uid: int) -> tuple[int, int]:
        rx_total = 0
        tx_total = 0
        active_uid = False
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            uid_match = re.search(r"\buid[=:\s]+(-?\d+)", line)
            if uid_match:
                active_uid = int(uid_match.group(1)) == uid
            elif "Bucket{" in line or "uid=" in line:
                active_uid = False
            if str(uid) in line and not active_uid:
                numbers = [int(value) for value in re.findall(r"\b\d+\b", line)]
                active_uid = uid in numbers
            if not active_uid:
                continue
            named = cls._rx_tx_from_named_bytes(line)
            if named:
                rx, tx = named
                rx_total += rx
                tx_total += tx
                continue
            positional = cls._rx_tx_from_positional_netstats(line, uid)
            if positional:
                rx, tx = positional
                rx_total += rx
                tx_total += tx
        return rx_total, tx_total

    @staticmethod
    def _rx_tx_from_named_bytes(line: str) -> tuple[int, int] | None:
        rx_match = re.search(r"\brxBytes[=:\s]+(\d+)", line)
        tx_match = re.search(r"\btxBytes[=:\s]+(\d+)", line)
        if rx_match and tx_match:
            return int(rx_match.group(1)), int(tx_match.group(1))
        return None

    @staticmethod
    def _rx_tx_from_positional_netstats(line: str, uid: int) -> tuple[int, int] | None:
        numbers = [int(value) for value in re.findall(r"\b\d+\b", line)]
        if uid not in numbers:
            return None
        uid_index = numbers.index(uid)
        tail = numbers[uid_index + 1 :]
        if len(tail) >= 4:
            return tail[0], tail[2]
        if len(tail) >= 2:
            return tail[0], tail[1]
        return None

    def _device_net_totals(self, device: DeviceInfo) -> tuple[int, int]:
        output = self._shell(device.serial, "cat /proc/net/dev", timeout=2.0)
        return self._parse_proc_net_dev(output)

    @staticmethod
    def _parse_proc_net_dev(output: str) -> tuple[int, int]:
        rx_total = 0
        tx_total = 0
        ignored = ("lo", "dummy", "ifb", "sit", "ip6tnl")
        for raw_line in output.splitlines():
            if ":" not in raw_line:
                continue
            name, payload = raw_line.split(":", 1)
            iface = name.strip()
            if not iface or iface.startswith(ignored):
                continue
            parts = payload.split()
            if len(parts) < 16:
                continue
            try:
                rx_total += int(parts[0])
                tx_total += int(parts[8])
            except Exception:
                continue
        return rx_total, tx_total

    def _network_kbps(self, device: DeviceInfo, app_id: str, now: float) -> tuple[float, float]:
        key = (device.serial, app_id)
        self._network_note_cache[key] = ""
        rx_total, tx_total = self._net_totals(device, app_id)
        if rx_total <= 0 and tx_total <= 0:
            device_rx, device_tx = self._device_net_totals(device)
            previous_device = self._device_net_cache.get(key)
            self._device_net_cache[key] = (now, device_rx, device_tx)
            if previous_device:
                prev_time, prev_rx, prev_tx = previous_device
                delta = max(now - prev_time, 0.1)
                rx_kbps = max(device_rx - prev_rx, 0) / 1024.0 / delta
                tx_kbps = max(device_tx - prev_tx, 0) / 1024.0 / delta
                if rx_kbps > 0 or tx_kbps > 0:
                    self._network_note_cache[key] = "Android 网络使用设备级网络兜底，非目标 App 独占流量。"
                    return rx_kbps, tx_kbps
        previous = self._net_cache.get(key)
        self._net_cache[key] = (now, rx_total, tx_total)
        if not previous:
            return 0.0, 0.0
        prev_time, prev_rx, prev_tx = previous
        delta = max(now - prev_time, 0.1)
        rx_kbps = max(rx_total - prev_rx, 0) / 1024.0 / delta
        tx_kbps = max(tx_total - prev_tx, 0) / 1024.0 / delta
        return rx_kbps, tx_kbps

    def _fps_and_jank(self, device: DeviceInfo, app_id: str, now: float) -> tuple[float, float]:
        if not app_id:
            return 0.0, 0.0
        for collector in (
            self._gfxinfo_counter_fps_and_jank,
            self._gfxinfo_framestats_fps_and_jank,
            self._surface_fps_and_jank,
        ):
            result = collector(device, app_id, now)
            if result is not None:
                return result
        return 0.0, 0.0

    def _gfxinfo_counter_fps_and_jank(self, device: DeviceInfo, app_id: str, now: float) -> tuple[float, float] | None:
        output = self._shell(device.serial, f"dumpsys gfxinfo {shlex.quote(app_id)}", timeout=5.0)
        total_frames = int(parse_first_float(r"Total frames rendered:\s*(\d+)", output, 0.0))
        janky_frames = int(parse_first_float(r"Janky frames:\s*(\d+)", output, 0.0))
        if not total_frames:
            return None
        key = (device.serial, app_id)
        previous = self._frame_cache.get(key)
        self._frame_cache[key] = (now, total_frames, janky_frames)
        if not previous:
            return None
        prev_time, prev_frames, prev_janky = previous
        seconds = max(now - prev_time, 0.1)
        frame_delta = max(total_frames - prev_frames, 0)
        jank_delta = max(janky_frames - prev_janky, 0)
        if frame_delta <= 0:
            return None
        fps = min(frame_delta / seconds, 240.0)
        jank_percent = (jank_delta / frame_delta * 100.0) if frame_delta else 0.0
        return fps, jank_percent

    def _gfxinfo_framestats_fps_and_jank(self, device: DeviceInfo, app_id: str, now: float) -> tuple[float, float] | None:
        output = self._shell(device.serial, f"dumpsys gfxinfo {shlex.quote(app_id)} framestats", timeout=5.0)
        frame_times = self._parse_gfxinfo_framestats(output)
        return self._fps_from_frame_times(
            (device.serial, app_id),
            frame_times,
            self._framestats_cache,
            now,
            0,
        )

    def _surface_fps_and_jank(self, device: DeviceInfo, app_id: str, now: float) -> tuple[float, float] | None:
        refresh_period_ns, frame_times = self._surface_latency_frames(device, app_id)
        return self._fps_from_frame_times(
            (device.serial, app_id),
            frame_times,
            self._surface_frame_cache,
            now,
            refresh_period_ns,
        )

    def _fps_from_frame_times(
        self,
        key: tuple[str, str],
        frame_times: list[int],
        cache: dict[tuple[str, str], tuple[float, int]],
        now: float,
        refresh_period_ns: int,
    ) -> tuple[float, float] | None:
        frame_times = sorted(set(value for value in frame_times if value > 0))
        if len(frame_times) < 2:
            return None
        previous = cache.get(key)
        last_frame_ns = frame_times[-1]
        if previous:
            _previous_time, previous_frame_ns = previous
            if last_frame_ns <= previous_frame_ns:
                cache[key] = (now, last_frame_ns)
                return None
            interval_frames = [previous_frame_ns, *[value for value in frame_times if value > previous_frame_ns]]
        else:
            interval_frames = frame_times[-180:]
        cache[key] = (now, last_frame_ns)
        if len(interval_frames) < 2:
            return None
        span_seconds = (interval_frames[-1] - interval_frames[0]) / 1_000_000_000.0
        if span_seconds <= 0:
            return None
        fps = min(max(len(interval_frames) - 1, 0) / span_seconds, 240.0)
        jank_percent = self._surface_jank_percent(interval_frames, refresh_period_ns)
        return fps, jank_percent

    def _surface_latency_frames(self, device: DeviceInfo, app_id: str) -> tuple[int, list[int]]:
        surface = self._surface_name(device, app_id)
        if not surface:
            return 0, []
        output = self._shell(
            device.serial,
            f"dumpsys SurfaceFlinger --latency {shlex.quote(surface)}",
            timeout=4.0,
        )
        refresh_period_ns, frame_times = self._parse_surface_latency(output)
        if frame_times:
            return refresh_period_ns, frame_times
        self._surface_cache.pop((device.serial, app_id), None)
        surface = self._surface_name(device, app_id)
        if not surface:
            return 0, []
        output = self._shell(
            device.serial,
            f"dumpsys SurfaceFlinger --latency {shlex.quote(surface)}",
            timeout=4.0,
        )
        return self._parse_surface_latency(output)

    @staticmethod
    def _parse_gfxinfo_framestats(output: str) -> list[int]:
        frame_times: list[int] = []
        frame_completed_index: int | None = None
        flags_index: int | None = None
        in_profile_data = False
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line == "---PROFILEDATA---":
                in_profile_data = not in_profile_data
                continue
            if not in_profile_data or "," not in line:
                continue
            parts = [part.strip() for part in line.split(",")]
            if "FrameCompleted" in parts:
                frame_completed_index = parts.index("FrameCompleted")
                flags_index = parts.index("Flags") if "Flags" in parts else None
                continue
            if frame_completed_index is None:
                continue
            if len(parts) <= frame_completed_index:
                continue
            try:
                if flags_index is not None and len(parts) > flags_index and int(parts[flags_index]) != 0:
                    continue
                frame_completed = int(parts[frame_completed_index])
            except ValueError:
                continue
            if frame_completed > 0:
                frame_times.append(frame_completed)
        return sorted(set(frame_times))

    def _surface_name(self, device: DeviceInfo, app_id: str) -> str:
        key = (device.serial, app_id)
        cached = self._surface_cache.get(key)
        if cached:
            return cached
        candidates: dict[str, int] = {}
        for name in self._surface_name_candidates(device, app_id):
            score = self._surface_score(name, app_id)
            if score <= 0:
                continue
            candidates[name] = max(candidates.get(name, 0), score)
        if not candidates:
            return ""
        surface = sorted(candidates.items(), key=lambda item: (-item[1], len(item[0])))[0][0]
        self._surface_cache[key] = surface
        return surface

    def _surface_name_candidates(self, device: DeviceInfo, app_id: str) -> list[str]:
        outputs = [
            self._shell(device.serial, "dumpsys SurfaceFlinger --list", timeout=5.0),
            self._shell(device.serial, "dumpsys window", timeout=6.0),
        ]
        candidates: list[str] = []
        seen: set[str] = set()
        for output in outputs:
            for raw_line in output.splitlines():
                for name in self._surface_names_from_line(raw_line):
                    if app_id not in name or name in seen:
                        continue
                    seen.add(name)
                    candidates.append(name)
        return candidates

    @classmethod
    def _surface_names_from_line(cls, raw_line: str) -> list[str]:
        line = raw_line.strip()
        if not line:
            return []
        names: list[str] = []
        requested_match = re.search(r"RequestedLayerState\{(.+?)(?:\s+parentId=|$)", line)
        if requested_match:
            names.append(requested_match.group(1).strip())
        layer_match = re.search(r"^\s*layer\s+\d+\s+(.+?)(?::\s*$|$)", line)
        if layer_match:
            names.append(layer_match.group(1).strip())
        for surface_match in re.finditer(r"Surface\(name=([^)]+)\)", line):
            names.append(surface_match.group(1).strip())
        names.append(line)
        cleaned: list[str] = []
        seen: set[str] = set()
        for name in names:
            normalized = cls._normalize_surface_name(name)
            if normalized and normalized not in seen:
                seen.add(normalized)
                cleaned.append(normalized)
        return cleaned

    @staticmethod
    def _normalize_surface_name(name: str) -> str:
        name = name.strip()
        if not name:
            return ""
        name = re.sub(r"^\s*layer\s+\d+\s+", "", name)
        name = re.sub(r":\s*$", "", name)
        name = re.sub(r"\s+fps:\s*[-0-9.]+.*$", "", name)
        name = re.sub(r"\s+screenbounds:.*$", "", name)
        name = re.sub(r"^Surface\(name=", "", name)
        name = re.sub(r"\)/@0x[0-9a-fA-F]+.*$", "", name)
        if " - animation-leash " in name:
            return ""
        return name.strip()

    @staticmethod
    def _surface_score(name: str, app_id: str) -> int:
        if app_id not in name:
            return 0
        ignored_tokens = (
            "Background for",
            "Bounds for",
            "ActivityRecordInputSink",
            "ActivityRecord{",
            "InputMethod",
            "StatusBar",
            "NavigationBar",
            "Wallpaper",
            "Dim layer",
        )
        if any(token in name for token in ignored_tokens):
            return 0
        score = 1
        if "SurfaceView" in name:
            score += 20
        if "(BLAST)" in name:
            score += 12
        elif "BLAST" in name:
            score += 6
        if "/" in name:
            score += 4
        if name.startswith(app_id):
            score += 3
        if "#" in name:
            score += 1
        return score

    @staticmethod
    def _parse_surface_latency(output: str) -> tuple[int, list[int]]:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        if not lines:
            return 0, []
        try:
            refresh_period_ns = int(lines[0].split()[0])
        except Exception:
            refresh_period_ns = 0
        frame_times: list[int] = []
        for line in lines[1:]:
            parts = line.split()
            if len(parts) < 3:
                continue
            try:
                present_time = int(parts[0])
                ready_time = int(parts[2])
            except ValueError:
                continue
            if present_time > 0 and ready_time > 0:
                frame_times.append(present_time)
        return refresh_period_ns, sorted(set(frame_times))

    @staticmethod
    def _surface_jank_percent(frame_times: list[int], refresh_period_ns: int) -> float:
        if len(frame_times) < 2:
            return 0.0
        if refresh_period_ns <= 0:
            deltas = [b - a for a, b in zip(frame_times, frame_times[1:]) if b > a]
            if not deltas:
                return 0.0
            refresh_period_ns = sorted(deltas)[len(deltas) // 2]
        threshold = refresh_period_ns * 1.5
        intervals = [b - a for a, b in zip(frame_times, frame_times[1:]) if b > a]
        if not intervals:
            return 0.0
        janky = sum(1 for interval in intervals if interval > threshold)
        return janky / len(intervals) * 100.0

    def collect_sample(self, device: DeviceInfo, app_id: str, start_time: float) -> PerfSample:
        current = time.time()
        key = (device.serial, app_id)
        sample_count = self._sample_count.get(key, 0) + 1
        self._sample_count[key] = sample_count
        foreground_app = self.foreground_app(device) if device.platform == "Android" else ""
        fps, jank_percent = self._fps_and_jank(device, app_id, current)
        battery, temperature, power = self._battery(device)
        rx, tx = self._network_kbps(device, app_id, current)
        cpu = self._cpu_percent(device, app_id)
        memory = self._memory_mb(device, app_id)
        note = self._android_sample_note(device, app_id, sample_count, fps, cpu, memory, rx, tx)
        foreground_note = self._foreground_session_note(device, app_id, foreground_app)
        if foreground_note:
            note = f"{foreground_note}；{note}" if note else foreground_note
        network_note = self._network_note_cache.get(key, "")
        if network_note:
            note = f"{note}；{network_note}" if note else network_note
        return PerfSample(
            timestamp=current,
            elapsed=current - start_time,
            fps=fps,
            jank_percent=jank_percent,
            cpu_percent=cpu,
            memory_mb=memory,
            battery_percent=battery,
            temperature_c=temperature,
            power_w=power,
            rx_kbps=rx,
            tx_kbps=tx,
            note=note,
        )

    def _foreground_session_note(self, device: DeviceInfo, app_id: str, foreground_app: str) -> str:
        if not app_id or not foreground_app:
            return ""
        key = (device.serial, app_id)
        if foreground_app != app_id:
            self._foreground_missing.add(key)
            self._foreground_recovery_remaining.pop(key, None)
            return f"目标应用不在前台，当前前台为 {foreground_app}。"
        if key in self._foreground_missing:
            self._foreground_missing.discard(key)
            self._foreground_recovery_remaining[key] = 2
        remaining = self._foreground_recovery_remaining.get(key, 0)
        if remaining > 0:
            self._foreground_recovery_remaining[key] = remaining - 1
            return "目标应用刚回到前台，恢复窗口内 FPS/CPU 可能受 Surface 和进程缓存重建影响。"
        self._foreground_recovery_remaining.pop(key, None)
        return ""

    def _android_sample_note(
        self,
        device: DeviceInfo,
        app_id: str,
        sample_count: int,
        fps: float,
        cpu: float,
        memory: float,
        rx: float,
        tx: float,
    ) -> str:
        if sample_count < 3 or not app_id:
            return ""
        notes: list[str] = []
        pid = self._pid_cache.get((device.serial, app_id))
        if pid is None and (cpu <= 0 or memory <= 0):
            notes.append("Android 未匹配到目标 PID，请确认 App 正在前台运行。")
        if fps <= 0:
            surface = self._surface_cache.get((device.serial, app_id), "")
            if not surface:
                notes.append("Android FPS 未采集到 Surface，请在目标页面停留 2-3 秒后重试，或确认目标 App 有可见界面。")
            else:
                notes.append(f"Android FPS 当前无帧增量，Surface={surface}。低端机/静止页面可能需要更长采样窗口。")
        if cpu <= 0 and pid is not None:
            notes.append("Android CPU 当前无进程增量，可能是采样间隔过短或系统限制读取 /proc。")
        if rx <= 0 and tx <= 0:
            uid = self._uid_cache.get((device.serial, app_id))
            if uid is None:
                notes.append("Android 网络未匹配到 App UID，无法按应用统计上下行。")
            else:
                notes.append("Android 网络当前无流量或系统未开放 per-UID 统计；请触发网络请求后观察。")
        return "；".join(notes[:3])

    def capture_screenshot(self, device: DeviceInfo, target: Path) -> Path | None:
        if not self.adb_path:
            return None
        ensure_dirs()
        with target.open("wb") as handle:
            try:
                result = subprocess.run(
                    [self.adb_path, "-s", device.serial, "exec-out", "screencap", "-p"],
                    stdout=handle,
                    stderr=subprocess.PIPE,
                    timeout=10,
                    check=False,
                )
            except Exception:
                return None
        if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
            target.unlink(missing_ok=True)
            return None
        return target


class IOSAdapter(BaseAdapter):
    platform_name = "iOS"

    def __init__(self) -> None:
        self.idevice_id = shutil.which("idevice_id")
        self.ideviceinfo = shutil.which("ideviceinfo")
        self.idevicediagnostics = shutil.which("idevicediagnostics")
        self.pymobiledevice3 = resolve_pymobiledevice3_path()
        self.xcrun = shutil.which("xcrun")
        self._pid_cache: dict[tuple[str, str], int] = {}
        self._note_cache: dict[tuple[str, str], str] = {}
        self._process_name_cache: dict[tuple[str, str], list[str]] = {}
        self._process_record_cache: dict[tuple[str, str], tuple[float, dict[str, object] | None]] = {}
        self._dvt_process_cache: dict[str, tuple[float, list[dict[str, object]]]] = {}
        self._app_record_cache: dict[str, tuple[float, list[dict[str, object]]]] = {}
        self._graphics_sessions: dict[str, subprocess.Popen[str]] = {}
        self._graphics_threads: dict[str, threading.Thread] = {}
        self._graphics_fps: dict[str, tuple[float, float]] = {}
        self._graphics_notes: dict[str, str] = {}
        self._graphics_started_at: dict[str, float] = {}
        self._graphics_retry_after: dict[str, float] = {}
        self._graphics_lock = threading.Lock()
        self._network_threads: dict[str, threading.Thread] = {}
        self._network_stop_events: dict[str, threading.Event] = {}
        self._network_totals: dict[tuple[str, int], tuple[int, int]] = {}
        self._network_name_totals: dict[tuple[str, str], tuple[int, int]] = {}
        self._network_rate_cache: dict[tuple[str, str], tuple[float, int, int]] = {}
        self._network_notes: dict[str, str] = {}
        self._network_retry_after: dict[str, float] = {}
        self._network_lock = threading.Lock()

    def is_available(self) -> bool:
        return any([self.idevice_id, self.xcrun, self.pymobiledevice3])

    def capability_note(self) -> str:
        tools = []
        if self.xcrun:
            tools.append("Xcode/xcrun")
        if self.idevice_id:
            tools.append("libimobiledevice")
        if self.pymobiledevice3:
            tools.append("pymobiledevice3")
        if tools:
            return "iOS: " + ", ".join(tools)
        return "未检测到 iOS 工具链。建议安装 Xcode，并按需安装 libimobiledevice 或 pymobiledevice3。"

    def list_devices(self) -> list[DeviceInfo]:
        devices: list[DeviceInfo] = []
        seen: set[str] = set()
        if self.pymobiledevice3:
            code, output = self._pmobile(["usbmux", "list"], timeout=8.0)
            payload = extract_json_payload(output) if code == 0 else None
            if isinstance(payload, list):
                for item in payload:
                    if not isinstance(item, dict):
                        continue
                    serial = str(item.get("UniqueDeviceID") or item.get("Identifier") or "").strip()
                    if not serial:
                        continue
                    if serial in seen:
                        continue
                    seen.add(serial)
                    connection = str(item.get("ConnectionType") or "unknown")
                    devices.append(
                        DeviceInfo(
                            "iOS",
                            serial,
                            str(item.get("DeviceName") or serial),
                            str(item.get("ProductVersion") or ""),
                            str(item.get("ProductType") or item.get("DeviceClass") or ""),
                            "ready",
                            f"pymobiledevice3/{connection}",
                        )
                    )
        if self.idevice_id:
            code, output = run_command([self.idevice_id, "-l"], timeout=5.0)
            if code == 0:
                for serial in [line.strip() for line in output.splitlines() if line.strip()]:
                    if serial in seen:
                        continue
                    seen.add(serial)
                    name = self._info(serial, "DeviceName") or serial
                    version = self._info(serial, "ProductVersion")
                    model = self._info(serial, "ProductType")
                    devices.append(DeviceInfo("iOS", serial, name, version, model, "ready", "libimobiledevice"))
        if self.xcrun:
            for device in self._devicectl_devices():
                serial = device.serial
                if serial in seen:
                    continue
                seen.add(serial)
                devices.append(device)
        if self.xcrun:
            code, output = run_command([self.xcrun, "xctrace", "list", "devices"], timeout=8.0)
            if code == 0:
                section = ""
                for line in output.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("==") and stripped.endswith("=="):
                        section = stripped.strip("= ").lower()
                        continue
                    match = re.search(r"^\s*(.+?)\s+\(([\d.]+)\)\s+\(([0-9A-Fa-f-]{8,})\)", line)
                    if not match:
                        continue
                    name, version, serial = match.groups()
                    if serial in seen or "Mac" in name:
                        continue
                    seen.add(serial)
                    status = "offline" if "offline" in section else "ready"
                    devices.append(DeviceInfo("iOS", serial, name, version, "", status, f"xctrace/{section or 'devices'}"))
        return devices

    def _info(self, serial: str, key: str) -> str:
        if not self.ideviceinfo:
            return ""
        code, output = run_command([self.ideviceinfo, "-u", serial, "-k", key], timeout=4.0)
        return output.strip() if code == 0 else ""

    def _pmobile(self, args: list[str], timeout: float = 8.0) -> tuple[int, str]:
        if not self.pymobiledevice3:
            return 1, "pymobiledevice3 not found"
        return run_command([self.pymobiledevice3, *args], timeout=timeout)

    def _run_devicectl_json(self, args: list[str], timeout: float = 12.0) -> object | None:
        if not self.xcrun:
            return None
        handle = tempfile.NamedTemporaryFile(prefix="mobileperflab_", suffix=".json", delete=False)
        json_path = Path(handle.name)
        handle.close()
        try:
            command = [self.xcrun, "devicectl", *args, "--json-output", str(json_path), "--quiet"]
            code, _output = run_command(command, timeout=timeout)
            if code != 0 or not json_path.exists() or json_path.stat().st_size == 0:
                return None
            return json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        finally:
            json_path.unlink(missing_ok=True)

    def _devicectl_devices(self) -> list[DeviceInfo]:
        payload = self._run_devicectl_json(["list", "devices"], timeout=12.0)
        if not isinstance(payload, dict):
            return []
        result = payload.get("result")
        if not isinstance(result, dict):
            return []
        raw_devices = result.get("devices")
        if not isinstance(raw_devices, list):
            return []
        devices: list[DeviceInfo] = []
        for item in raw_devices:
            if not isinstance(item, dict):
                continue
            hardware = item.get("hardwareProperties") if isinstance(item.get("hardwareProperties"), dict) else {}
            properties = item.get("deviceProperties") if isinstance(item.get("deviceProperties"), dict) else {}
            connection = item.get("connectionProperties") if isinstance(item.get("connectionProperties"), dict) else {}
            platform = str(hardware.get("platform") or "")
            if platform != "iOS":
                continue
            serial = str(hardware.get("udid") or item.get("identifier") or "").strip()
            if not serial:
                continue
            transport = str(connection.get("transportType") or "")
            developer_mode = str(properties.get("developerModeStatus") or "")
            boot_state = str(properties.get("bootState") or "")
            connected = bool(transport) and (
                boot_state == "booted"
                or developer_mode == "enabled"
                or bool(hardware.get("internalStorageCapacity"))
            )
            status = "ready" if connected else "offline"
            detail_parts = ["devicectl"]
            if transport:
                detail_parts.append(transport)
            tunnel_state = connection.get("tunnelState")
            if tunnel_state:
                detail_parts.append(f"tunnel:{tunnel_state}")
            devices.append(
                DeviceInfo(
                    "iOS",
                    serial,
                    str(properties.get("name") or serial),
                    str(properties.get("osVersionNumber") or ""),
                    str(hardware.get("marketingName") or hardware.get("productType") or ""),
                    status,
                    "/".join(detail_parts),
                )
            )
        return devices

    def list_apps(self, device: DeviceInfo) -> list[str]:
        raw_apps = self._ios_app_records(device)
        apps: list[str] = []
        for item in raw_apps:
            bundle_id = str(item.get("bundleIdentifier") or "")
            if not bundle_id:
                continue
            name = str(item.get("name") or "")
            apps.append(f"{bundle_id}    {name}" if name else bundle_id)
        if apps:
            return sorted(set(apps))
        code, output = self._pmobile(["developer", "dvt", "applist", "--udid", device.serial], timeout=12.0)
        if code == 0:
            for line in output.splitlines():
                match = re.search(r"([A-Za-z][\w-]*(?:\.[\w-]+)+)", line)
                if match:
                    apps.append(match.group(1))
        return sorted(set(apps))

    def foreground_app(self, device: DeviceInfo) -> str:
        foreground_records = [
            record
            for record in self._dvt_process_records(device, max_age=0.5)
            if bool(record.get("foregroundRunning")) and str(record.get("bundleIdentifier") or "")
        ]
        for record in foreground_records:
            bundle_id = str(record.get("bundleIdentifier") or "")
            real_name = str(record.get("realAppName") or "")
            if not bundle_id.startswith("com.apple.") and "/System/" not in real_name:
                return bundle_id
        for record in foreground_records:
            if not bool(record.get("foregroundRunning")):
                continue
            bundle_id = str(record.get("bundleIdentifier") or "")
            if bundle_id:
                return bundle_id

        records = self._ios_app_records(device)
        processes = self._ios_running_processes(device)
        if not records or not processes:
            return ""

        by_app_dir: dict[str, dict[str, object]] = {}
        by_name: dict[str, dict[str, object]] = {}
        for record in records:
            bundle_id = str(record.get("bundleIdentifier") or "")
            if not bundle_id:
                continue
            app_dir = self._app_dir_name(record)
            display_name = str(record.get("name") or "")
            if app_dir:
                by_app_dir[app_dir.lower()] = record
            if display_name:
                by_name[self._normalize_process_name(display_name)] = record

        candidates: list[tuple[int, int, str, str]] = []
        for process in processes:
            executable = str(process.get("executable") or "")
            pid = int(self._to_float(process.get("processIdentifier")))
            path = self._file_url_path(executable)
            if not path:
                continue
            path_parts = [part for part in path.split("/") if part]
            if any(part.endswith(".appex") for part in path_parts) or "PlugIns" in path_parts:
                continue
            app_component = next((part for part in path_parts if part.endswith(".app")), "")
            if not app_component:
                continue
            app_dir = app_component[:-4]
            executable_name = Path(path).name
            record = by_app_dir.get(app_dir.lower()) or by_name.get(self._normalize_process_name(executable_name))
            if not record:
                continue
            bundle_id = str(record.get("bundleIdentifier") or "")
            if not bundle_id:
                continue
            score = pid
            if bool(record.get("builtByDeveloper")):
                score += 2_000_000
            if bool(record.get("removable")) and not bool(record.get("defaultApp")):
                score += 1_000_000
            if bundle_id.startswith("com.apple."):
                score -= 500_000
            candidates.append((score, pid, bundle_id, app_dir))

        if not candidates:
            return ""
        candidates.sort(reverse=True)
        return candidates[0][2]

    def _ios_app_records(self, device: DeviceInfo) -> list[dict[str, object]]:
        cached = self._app_record_cache.get(device.serial)
        if cached and time.time() - cached[0] < 30:
            return cached[1]
        payload = self._run_devicectl_json(
            ["device", "info", "apps", "--device", device.serial, "--include-all-apps", "--timeout", "15"],
            timeout=18.0,
        )
        apps: list[dict[str, object]] = []
        if isinstance(payload, dict):
            result = payload.get("result")
            raw_apps = result.get("apps") if isinstance(result, dict) else None
            if isinstance(raw_apps, list):
                for item in raw_apps:
                    if isinstance(item, dict) and item.get("bundleIdentifier"):
                        apps.append(item)
        self._app_record_cache[device.serial] = (time.time(), apps)
        return apps

    def _ios_running_processes(self, device: DeviceInfo) -> list[dict[str, object]]:
        payload = self._run_devicectl_json(
            ["device", "info", "processes", "--device", device.serial, "--timeout", "10"],
            timeout=12.0,
        )
        if not isinstance(payload, dict):
            return []
        result = payload.get("result")
        raw_processes = result.get("runningProcesses") if isinstance(result, dict) else None
        if not isinstance(raw_processes, list):
            return []
        return [item for item in raw_processes if isinstance(item, dict)]

    @staticmethod
    def _file_url_path(value: str) -> str:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme == "file":
            return urllib.parse.unquote(parsed.path)
        return urllib.parse.unquote(value)

    def _app_dir_name(self, record: dict[str, object]) -> str:
        url = str(record.get("url") or "")
        path = self._file_url_path(url).rstrip("/")
        name = Path(path).name
        return name[:-4] if name.endswith(".app") else ""

    def _target_process_names(self, device: DeviceInfo, app_id: str) -> list[str]:
        key = (device.serial, app_id)
        cached = self._process_name_cache.get(key)
        if cached:
            return cached

        names: list[str] = []

        def add(value: object) -> None:
            text = str(value or "").strip()
            if text and text not in names:
                names.append(text)

        for record in self._ios_app_records(device):
            if str(record.get("bundleIdentifier") or "") != app_id:
                continue
            add(self._app_dir_name(record))
            for field in ("bundleExecutable", "executableName", "executable", "name"):
                add(record.get(field))
            break

        if not names and app_id:
            add(app_id.rsplit(".", 1)[-1])

        self._process_name_cache[key] = names
        return names

    @staticmethod
    def _normalize_process_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]", "", value.lower())

    def start_session(self, device: DeviceInfo, app_id: str) -> None:
        key = (device.serial, app_id)
        self._pid_cache.pop(key, None)
        self._process_record_cache.pop(key, None)
        self._note_cache[key] = ""
        self._start_graphics_session(device)
        self._start_network_session(device)
        process_names = self._target_process_names(device, app_id)
        if not self.pymobiledevice3:
            self._note_cache[key] = "iOS 真实采集需要 pymobiledevice3；当前只能识别 Xcode 设备状态。"
            return

        record = self._target_process_record(device, app_id, max_age=0.0)
        record_pid = self._item_pid(record) if record else None
        if record_pid is not None:
            self._pid_cache[key] = record_pid
            record_name = str(record.get("name") or "")
            if record_name and record_name not in process_names:
                process_names.append(record_name)
                self._process_name_cache[key] = process_names
            return

        code, output = self._pmobile(
            ["developer", "dvt", "process-id-for-bundle-id", "--udid", device.serial, app_id],
            timeout=10.0,
        )
        if self._is_tunnel_error(output):
            self._note_cache[key] = (
                "iOS CPU/内存实时采集需要先启动 tunneld："
                "双击“启动iOS采集服务.command”并输入电脑密码，保持窗口打开。"
            )
            return
        pid = self._extract_pid(output)
        if pid is None:
            if process_names:
                self._note_cache[key] = f"PID 读取失败，已改用进程名匹配：{', '.join(process_names)}"
            else:
                self._note_cache[key] = f"未找到运行中的 iOS App：{app_id}。请先在手机上打开目标 App。"
            return
        self._pid_cache[key] = pid

    def stop_session(self, device: DeviceInfo, app_id: str) -> None:
        self._process_record_cache.pop((device.serial, app_id), None)
        self._stop_graphics_session(device.serial)
        self._stop_network_session(device.serial)

    def collect_sample(self, device: DeviceInfo, app_id: str, start_time: float) -> PerfSample:
        current = time.time()
        battery, temperature, power = self._battery(device)
        key = (device.serial, app_id)
        pid = self._pid_cache.get(key)
        note = self._note_cache.get(key, "")
        process_names = self._target_process_names(device, app_id)

        record = self._target_process_record(device, app_id, max_age=2.0)
        record_pid = self._item_pid(record) if record else None
        if record:
            record_name = str(record.get("name") or "")
            if record_name and record_name not in process_names:
                process_names.append(record_name)
                self._process_name_cache[key] = process_names
        if record_pid is not None and record_pid != pid:
            pid = record_pid
            self._pid_cache[key] = record_pid
            self._clear_graphics_fps(device.serial)
            note = f"检测到 iOS App 进程切换，已重绑 PID：{record_pid}"

        cpu_percent, memory_mb, metric_note, matched_pid = self._process_metrics(device, app_id, pid, process_names)
        if matched_pid is not None:
            self._pid_cache[key] = matched_pid
        if metric_note:
            note = self._merge_note(note, metric_note)
        rx, tx, network_note = self._network_kbps(device, self._pid_cache.get(key) or pid, process_names)
        if network_note:
            note = self._merge_note(note, network_note)
        if record is not None and self._has_dvt_foreground_state(device) and not bool(record.get("foregroundRunning")):
            self._clear_graphics_fps(device.serial)
            fps = 0.0
            jank_percent = 0.0
            note = self._merge_note(note, "目标 iOS App 当前不在前台，已暂停 FPS/Jank 展示，CPU/内存仍按目标进程采集。")
        else:
            fps, fps_note = self._latest_graphics_fps(device)
            if fps_note:
                note = self._merge_note(note, fps_note)
            jank_percent = self._estimate_ios_jank_percent(fps)
        return PerfSample(
            timestamp=current,
            elapsed=current - start_time,
            fps=fps,
            jank_percent=jank_percent,
            cpu_percent=cpu_percent,
            memory_mb=memory_mb,
            battery_percent=battery,
            temperature_c=temperature,
            power_w=power,
            rx_kbps=rx,
            tx_kbps=tx,
            note=note,
        )

    @staticmethod
    def _merge_note(current: str, addition: str) -> str:
        current = current.strip()
        addition = addition.strip()
        if not addition or addition in current:
            return current
        cpu_tunnel = "iOS CPU/内存实时采集需要先启动 tunneld"
        fps_tunnel = "iOS FPS 采集需要启动 iOS 采集服务"
        if cpu_tunnel in current and cpu_tunnel in addition:
            return current
        if fps_tunnel in current and fps_tunnel in addition:
            return current
        if not current:
            return addition
        return f"{current}；{addition}"

    def _target_process_record(
        self,
        device: DeviceInfo,
        app_id: str,
        max_age: float = 2.0,
    ) -> dict[str, object] | None:
        key = (device.serial, app_id)
        cached = self._process_record_cache.get(key)
        if cached and time.time() - cached[0] <= max_age:
            return cached[1]

        record: dict[str, object] | None = None
        records = self._dvt_process_records(device, max_age=max_age)
        if records:
            process_names = self._target_process_names(device, app_id)
            record = self._find_process_record(records, app_id, process_names)

        self._process_record_cache[key] = (time.time(), record)
        return record

    def _dvt_process_records(self, device: DeviceInfo, max_age: float = 2.0) -> list[dict[str, object]]:
        cached = self._dvt_process_cache.get(device.serial)
        if cached and time.time() - cached[0] <= max_age:
            return cached[1]
        records: list[dict[str, object]] = []
        if self.pymobiledevice3:
            code, output = self._pmobile(["developer", "dvt", "proclist", "--udid", device.serial], timeout=8.0)
            if code == 0 and not self._is_tunnel_error(output):
                payload = extract_json_payload(output)
                if isinstance(payload, list):
                    records = [item for item in payload if isinstance(item, dict)]
        self._dvt_process_cache[device.serial] = (time.time(), records)
        return records

    def _has_dvt_foreground_state(self, device: DeviceInfo) -> bool:
        return any("foregroundRunning" in record for record in self._dvt_process_records(device, max_age=2.0))

    def _find_process_record(
        self,
        processes: list[object],
        app_id: str,
        process_names: list[str],
    ) -> dict[str, object] | None:
        records = [item for item in processes if isinstance(item, dict)]
        for record in records:
            bundle_id = str(record.get("bundleIdentifier") or record.get("bundleID") or "")
            if bundle_id == app_id:
                return record

        normalized_names = {
            self._normalize_process_name(name)
            for name in process_names
            if self._normalize_process_name(name)
        }
        if not normalized_names:
            return None

        for record in records:
            name = str(record.get("name") or record.get("processName") or record.get("executable") or "")
            if self._normalize_process_name(name) in normalized_names:
                return record
        return None

    def _start_network_session(self, device: DeviceInfo, force: bool = False) -> None:
        if not self.pymobiledevice3:
            return
        now = time.time()
        with self._network_lock:
            thread = self._network_threads.get(device.serial)
            if thread and thread.is_alive():
                return
            if not force and now < self._network_retry_after.get(device.serial, 0.0):
                return
            stop_event = threading.Event()
            self._network_stop_events[device.serial] = stop_event
            self._network_notes[device.serial] = "iOS 网络采集通道启动中..."
            self._network_retry_after[device.serial] = now + 8.0
        thread = threading.Thread(
            target=self._network_monitor_thread,
            args=(device.serial, stop_event),
            daemon=True,
        )
        with self._network_lock:
            self._network_threads[device.serial] = thread
        thread.start()

    def _stop_network_session(self, serial: str) -> None:
        with self._network_lock:
            stop_event = self._network_stop_events.pop(serial, None)
            self._network_threads.pop(serial, None)
            self._network_notes.pop(serial, None)
            self._network_retry_after.pop(serial, None)
            self._network_rate_cache = {
                key: value for key, value in self._network_rate_cache.items() if key[0] != serial
            }
            self._network_totals = {key: value for key, value in self._network_totals.items() if key[0] != serial}
            self._network_name_totals = {
                key: value for key, value in self._network_name_totals.items() if key[0] != serial
            }
        if stop_event:
            stop_event.set()

    def _network_kbps(
        self,
        device: DeviceInfo,
        pid: int | None,
        process_names: list[str] | None = None,
    ) -> tuple[float, float, str]:
        self._start_network_session(device)
        with self._network_lock:
            note = self._network_notes.get(device.serial, "")
            thread = self._network_threads.get(device.serial)
        if (not thread or not thread.is_alive()) and time.time() >= self._network_retry_after.get(device.serial, 0.0):
            self._start_network_session(device, force=True)
        normalized_names = self._normalized_network_names(process_names or [])
        if pid is None and not normalized_names:
            return 0.0, 0.0, note
        with self._network_lock:
            pid_totals = self._network_totals.get((device.serial, pid), (0, 0)) if pid is not None else (0, 0)
            name_totals = self._network_totals_for_names_locked(device.serial, normalized_names)
            rx_total, tx_total = max((pid_totals, name_totals), key=lambda item: item[0] + item[1])
            cache_key = (device.serial, f"pid:{pid or 0}|names:{','.join(normalized_names)}")
            previous = self._network_rate_cache.get(cache_key)
        now = time.time()
        with self._network_lock:
            self._network_rate_cache[cache_key] = (now, rx_total, tx_total)
        if not previous:
            return 0.0, 0.0, note
        prev_time, prev_rx, prev_tx = previous
        seconds = max(now - prev_time, 0.1)
        rx = max(rx_total - prev_rx, 0) / 1024.0 / seconds
        tx = max(tx_total - prev_tx, 0) / 1024.0 / seconds
        return rx, tx, note

    @classmethod
    def _normalized_network_names(cls, process_names: list[str]) -> list[str]:
        names: list[str] = []
        for name in process_names:
            normalized = cls._normalize_process_name(name)
            if normalized and normalized not in names:
                names.append(normalized)
        return names

    def _network_totals_for_names_locked(self, serial: str, normalized_names: list[str]) -> tuple[int, int]:
        rx_total = 0
        tx_total = 0
        for name in normalized_names:
            rx, tx = self._network_name_totals.get((serial, name), (0, 0))
            rx_total += rx
            tx_total += tx
        return rx_total, tx_total

    @staticmethod
    def _pcap_packet_network_bytes(packet: object) -> tuple[int, int]:
        length = int(getattr(packet, "packet_length", 0) or len(getattr(packet, "data", b"") or b""))
        if length <= 0:
            return 0, 0
        direction = int(getattr(packet, "io", 0) or 0)
        # pcapd reports outbound app packets as 0x01 and inbound packets as 0x10.
        if direction & 0x10:
            return length, 0
        if direction & 0x01:
            return 0, length
        return 0, 0

    @staticmethod
    def _valid_pcap_pid(value: object) -> int | None:
        try:
            pid = int(value)
        except (TypeError, ValueError):
            return None
        if pid <= 0 or pid == 0xFFFFFFFF:
            return None
        return pid

    @classmethod
    def _pcap_packet_pids(cls, packet: object) -> list[int]:
        pids: list[int] = []
        for field in ("pid", "epid"):
            pid = cls._valid_pcap_pid(getattr(packet, field, None))
            if pid is not None and pid not in pids:
                pids.append(pid)
        return pids

    @classmethod
    def _pcap_packet_names(cls, packet: object) -> list[str]:
        names: list[str] = []
        for field in ("comm", "ecomm"):
            normalized = cls._normalize_process_name(str(getattr(packet, field, "") or ""))
            if normalized and normalized not in names:
                names.append(normalized)
        return names

    def _network_monitor_thread(self, serial: str, stop_event: threading.Event) -> None:
        try:
            asyncio.run(self._network_monitor_loop(serial, stop_event))
        except Exception as exc:
            with self._network_lock:
                if not stop_event.is_set():
                    self._network_notes[serial] = f"iOS 网络采集失败：{self._short_error(str(exc))}"
                    self._network_retry_after[serial] = time.time() + 8.0
        finally:
            with self._network_lock:
                current = self._network_stop_events.get(serial)
                if current is stop_event:
                    self._network_threads.pop(serial, None)

    async def _network_monitor_loop(self, serial: str, stop_event: threading.Event) -> None:
        try:
            await self._pcap_network_monitor_loop(serial, stop_event)
            return
        except Exception as exc:
            if stop_event.is_set():
                return
            with self._network_lock:
                self._network_notes[serial] = f"iOS pcapd 网络采集不可用，尝试 DVT 网络通道：{self._short_error(str(exc))}"

        await self._dvt_network_monitor_loop(serial, stop_event)

    async def _pcap_network_monitor_loop(self, serial: str, stop_event: threading.Event) -> None:
        try:
            from pymobiledevice3.lockdown import create_using_usbmux
            from pymobiledevice3.services.pcapd import PcapdService
        except Exception as exc:
            raise RuntimeError(f"缺少 pymobiledevice3 pcapd 模块：{exc}") from exc

        provider = await create_using_usbmux(serial=serial, autopair=False)
        service = PcapdService(lockdown=provider)
        with self._network_lock:
            self._network_notes[serial] = ""
            self._network_retry_after[serial] = 0.0

        async for packet in service.watch(packets_count=-1):
            if stop_event.is_set():
                break
            rx_bytes, tx_bytes = self._pcap_packet_network_bytes(packet)
            if rx_bytes <= 0 and tx_bytes <= 0:
                continue

            pids = self._pcap_packet_pids(packet)
            names = self._pcap_packet_names(packet)
            if not pids and not names:
                continue

            with self._network_lock:
                for pid in pids:
                    total_rx, total_tx = self._network_totals.get((serial, pid), (0, 0))
                    self._network_totals[(serial, pid)] = (total_rx + rx_bytes, total_tx + tx_bytes)
                for name in names:
                    total_rx, total_tx = self._network_name_totals.get((serial, name), (0, 0))
                    self._network_name_totals[(serial, name)] = (total_rx + rx_bytes, total_tx + tx_bytes)

    async def _dvt_network_monitor_loop(self, serial: str, stop_event: threading.Event) -> None:
        try:
            from pymobiledevice3.exceptions import InvalidServiceError, RSDRequiredError, TunneldConnectionError
            from pymobiledevice3.lockdown import create_using_usbmux
            from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
            from pymobiledevice3.services.dvt.instruments.network_monitor import (
                ConnectionDetectionEvent,
                ConnectionUpdateEvent,
                NetworkMonitor,
            )
            from pymobiledevice3.tunneld.api import get_tunneld_device_by_udid
        except Exception as exc:
            with self._network_lock:
                self._network_notes[serial] = f"iOS 网络采集失败：缺少 pymobiledevice3 网络模块：{exc}"
            return

        connection_pids: dict[int, int] = {}
        connection_totals: dict[int, tuple[int, int]] = {}

        async def monitor_provider(provider) -> None:
            async with DvtProvider(provider) as dvt, NetworkMonitor(dvt) as monitor:
                with self._network_lock:
                    self._network_notes[serial] = ""
                    self._network_retry_after[serial] = 0.0
                iterator = monitor.__aiter__()
                while not stop_event.is_set():
                    try:
                        event = await asyncio.wait_for(anext(iterator), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    if isinstance(event, ConnectionDetectionEvent):
                        connection_pids[int(event.serial_number)] = int(event.pid)
                        continue
                    if not isinstance(event, ConnectionUpdateEvent):
                        continue
                    connection_id = int(event.connection_serial)
                    pid = connection_pids.get(connection_id)
                    if pid is None:
                        continue
                    rx_bytes = int(event.rx_bytes or 0)
                    tx_bytes = int(event.tx_bytes or 0)
                    prev_rx, prev_tx = connection_totals.get(connection_id, (0, 0))
                    delta_rx = rx_bytes - prev_rx if rx_bytes >= prev_rx else rx_bytes
                    delta_tx = tx_bytes - prev_tx if tx_bytes >= prev_tx else tx_bytes
                    connection_totals[connection_id] = (rx_bytes, tx_bytes)
                    if delta_rx <= 0 and delta_tx <= 0:
                        continue
                    key = (serial, pid)
                    with self._network_lock:
                        total_rx, total_tx = self._network_totals.get(key, (0, 0))
                        self._network_totals[key] = (total_rx + max(delta_rx, 0), total_tx + max(delta_tx, 0))

        try:
            provider = await create_using_usbmux(serial=serial)
            try:
                await monitor_provider(provider)
            except (InvalidServiceError, RSDRequiredError):
                tunnel_provider = await get_tunneld_device_by_udid(serial)
                if tunnel_provider is None:
                    raise TunneldConnectionError()
                await monitor_provider(tunnel_provider)
        except TunneldConnectionError:
            with self._network_lock:
                self._network_notes[serial] = "iOS 网络采集需要启动 iOS 采集服务：双击“启动iOS采集服务.command”并保持窗口打开。"
                self._network_retry_after[serial] = time.time() + 8.0
        except Exception as exc:
            if not stop_event.is_set():
                with self._network_lock:
                    self._network_notes[serial] = f"iOS 网络采集失败：{self._short_error(str(exc))}"
                    self._network_retry_after[serial] = time.time() + 8.0

    def _start_graphics_session(self, device: DeviceInfo, force: bool = False) -> None:
        if not self.pymobiledevice3:
            return
        now = time.time()
        with self._graphics_lock:
            if not force and now < self._graphics_retry_after.get(device.serial, 0.0):
                return
        if not self._is_pymobiledevice_visible(device.serial):
            with self._graphics_lock:
                self._graphics_notes[device.serial] = (
                    f"iOS FPS 采集失败：pymobiledevice3 当前未识别设备 {device.serial}。"
                    "请重新插拔/信任设备，或选择 iOS 采集服务可见的设备。"
                )
            return
        with self._graphics_lock:
            process = self._graphics_sessions.get(device.serial)
            if process and process.poll() is None:
                return
            self._graphics_sessions.pop(device.serial, None)
            self._graphics_threads.pop(device.serial, None)

        args = [
            self.pymobiledevice3,
            "developer",
            "dvt",
            "graphics",
            "--udid",
            device.serial,
        ]
        try:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
        except Exception as exc:
            with self._graphics_lock:
                self._graphics_notes[device.serial] = f"iOS FPS 采集启动失败：{exc}"
            return

        thread = threading.Thread(
            target=self._graphics_reader_loop,
            args=(device.serial, process),
            daemon=True,
        )
        with self._graphics_lock:
            self._graphics_sessions[device.serial] = process
            self._graphics_threads[device.serial] = thread
            self._graphics_started_at[device.serial] = time.time()
            self._graphics_notes[device.serial] = "iOS FPS 图形采集通道启动中..."
        thread.start()

    def _is_pymobiledevice_visible(self, serial: str) -> bool:
        if not self.pymobiledevice3 or not serial:
            return False
        code, output = self._pmobile(["usbmux", "list"], timeout=5.0)
        if code != 0:
            return False
        payload = extract_json_payload(output)
        if not isinstance(payload, list):
            return serial in output
        for item in payload:
            if not isinstance(item, dict):
                continue
            candidates = {
                str(item.get("UniqueDeviceID") or "").strip(),
                str(item.get("Identifier") or "").strip(),
            }
            if serial in candidates:
                return True
        return False

    def _stop_graphics_session(self, serial: str) -> None:
        with self._graphics_lock:
            process = self._graphics_sessions.pop(serial, None)
            self._graphics_threads.pop(serial, None)
            self._graphics_notes.pop(serial, None)
            self._graphics_fps.pop(serial, None)
            self._graphics_started_at.pop(serial, None)
            self._graphics_retry_after.pop(serial, None)
        if not process or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=2.0)
        except Exception:
            try:
                process.kill()
            except Exception:
                pass

    def _clear_graphics_fps(self, serial: str) -> None:
        with self._graphics_lock:
            self._graphics_fps.pop(serial, None)

    def _graphics_reader_loop(self, serial: str, process: subprocess.Popen[str]) -> None:
        last_message = ""
        try:
            stream = process.stdout
            if stream is None:
                with self._graphics_lock:
                    self._graphics_notes[serial] = "iOS FPS 采集失败：graphics 输出流不可用。"
                return
            for line in stream:
                stripped = line.strip()
                if stripped:
                    last_message = stripped
                    with self._graphics_lock:
                        if self._graphics_sessions.get(serial) is process:
                            if self._graphics_command_error(stripped):
                                self._graphics_notes[serial] = self._graphics_error_note(stripped)
                            elif not self._graphics_fps.get(serial):
                                self._graphics_notes[serial] = "iOS FPS 图形采集通道已连接，等待帧率事件..."
                fps = self._parse_graphics_fps(line)
                if fps is not None:
                    with self._graphics_lock:
                        self._graphics_fps[serial] = (time.time(), fps)
                        self._graphics_notes[serial] = ""
                    continue
                if self._is_tunnel_error(line):
                    with self._graphics_lock:
                        self._graphics_notes[serial] = "iOS FPS 采集需要启动 iOS 采集服务：双击“启动iOS采集服务.command”并保持窗口打开。"
        finally:
            try:
                code = process.wait(timeout=0.2)
            except Exception:
                code = process.poll()
            with self._graphics_lock:
                current = self._graphics_sessions.get(serial)
                if current is process:
                    self._graphics_sessions.pop(serial, None)
                    self._graphics_started_at.pop(serial, None)
                    if last_message:
                        self._graphics_notes[serial] = self._graphics_error_note(last_message)
                    elif code not in (0, None):
                        detail = last_message[-160:] if last_message else str(code)
                        self._graphics_notes[serial] = f"iOS FPS 采集进程已退出：{detail}"
                    else:
                        self._graphics_notes[serial] = "iOS FPS 图形采集通道已结束，等待自动重试。"
                    self._graphics_retry_after[serial] = time.time() + 8.0

    @staticmethod
    def _parse_graphics_fps(line: str) -> float | None:
        patterns = [
            r"CoreAnimationFramesPerSecond['\"]?\s*[:=]\s*([0-9.]+)",
            r"framesPerSecond['\"]?\s*[:=]\s*([0-9.]+)",
            r"frames_per_second['\"]?\s*[:=]\s*([0-9.]+)",
            r"Frames Per Second['\"]?\s*[:=]\s*([0-9.]+)",
            r"\bfps['\"]?\s*[:=]\s*([0-9.]+)",
            r"\bFPS['\"]?\s*[:=]\s*([0-9.]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, line)
            if not match:
                continue
            try:
                return max(0.0, min(float(match.group(1)), 240.0))
            except ValueError:
                continue
        return None

    def _latest_graphics_fps(self, device: DeviceInfo) -> tuple[float, str]:
        now = time.time()
        process_to_stop: subprocess.Popen[str] | None = None
        should_start = False
        with self._graphics_lock:
            latest = self._graphics_fps.get(device.serial)
            note = self._graphics_notes.get(device.serial, "")
            process = self._graphics_sessions.get(device.serial)
            started_at = self._graphics_started_at.get(device.serial, 0.0)
        if latest:
            timestamp, fps = latest
            if now - timestamp <= 5.0:
                return fps, ""

        with self._graphics_lock:
            process = self._graphics_sessions.get(device.serial)
            if process and process.poll() is None:
                started_at = self._graphics_started_at.get(device.serial, now)
                latest_age = now - latest[0] if latest else math.inf
                if now - started_at > 8.0 and latest_age > 8.0:
                    process_to_stop = process
                    self._graphics_sessions.pop(device.serial, None)
                    self._graphics_started_at.pop(device.serial, None)
                    self._graphics_retry_after[device.serial] = now + 8.0
                    self._graphics_notes[device.serial] = (
                        "iOS FPS 图形采集 8 秒内未返回帧率，已准备自动重试。"
                        "请确认“iOS采集服务”窗口已启动并保持打开。"
                    )
                    note = self._graphics_notes[device.serial]
                else:
                    return 0.0, note or "iOS FPS 图形采集通道等待帧率事件。"
            elif now >= self._graphics_retry_after.get(device.serial, 0.0):
                should_start = True
                self._graphics_retry_after[device.serial] = now + 8.0
                note = note or "iOS FPS 图形采集通道未运行，正在自动重试。"

        if process_to_stop:
            try:
                process_to_stop.terminate()
                process_to_stop.wait(timeout=1.0)
            except Exception:
                try:
                    process_to_stop.kill()
                except Exception:
                    pass
        if should_start:
            self._start_graphics_session(device, force=True)
        return 0.0, note or "iOS FPS 图形采集通道未运行。"

    @staticmethod
    def _graphics_command_error(output: str) -> bool:
        text = output.lower()
        return (
            "error" in text
            or "connection was terminated abruptly" in text
            or "device not found" in text
            or "operation not supported" in text
            or "no selection was made" in text
            or "choose device" in text
            or "traceback" in text
        )

    @classmethod
    def _graphics_error_note(cls, output: str) -> str:
        if cls._is_tunnel_error(output):
            return "iOS FPS 采集需要启动 iOS 采集服务：双击“启动iOS采集服务.command”并保持窗口打开。"
        text = output.lower()
        if "connection was terminated abruptly" in text:
            return "iOS FPS 图形服务连接被设备断开，正在等待自动重试；请确认 iOS 采集服务已启动。"
        if "device not found" in text:
            return "iOS FPS 采集未找到当前设备，请重新刷新设备或重新插拔手机。"
        if "operation not supported" in text:
            return "当前设备/系统未返回 iOS FPS 图形数据，CPU/内存/温度仍可继续采集。"
        detail = cls._short_error(output)
        return f"iOS FPS 采集失败：{detail}"

    @staticmethod
    def _estimate_ios_jank_percent(fps: float) -> float:
        if fps <= 0:
            return 0.0
        target_fps = 60.0
        return max(0.0, min((target_fps - fps) / target_fps * 100.0, 100.0))

    def _battery(self, device: DeviceInfo) -> tuple[float, float, float]:
        if self.pymobiledevice3:
            code, output = self._pmobile(["diagnostics", "battery", "single", "--udid", device.serial], timeout=6.0)
            payload = extract_json_payload(output) if code == 0 else None
            if isinstance(payload, dict):
                level = self._to_float(payload.get("CurrentCapacity") or payload.get("AppleRawCurrentCapacity"))
                if level > 100:
                    max_capacity = self._to_float(payload.get("AppleRawMaxCapacity") or payload.get("MaxCapacity"))
                    level = level / max_capacity * 100.0 if max_capacity else 0.0
                temperature = self._temperature_value(
                    payload.get("Temperature")
                    or payload.get("VirtualTemperature")
                    or (payload.get("BatteryData") or {}).get("AlgoTemperature")
                )
                voltage_mv = self._to_float(payload.get("Voltage") or payload.get("AppleRawBatteryVoltage"))
                amperage_ma = abs(self._to_float(payload.get("InstantAmperage") or payload.get("Amperage")))
                telemetry = payload.get("PowerTelemetryData") if isinstance(payload.get("PowerTelemetryData"), dict) else {}
                power_mw = self._first_positive(
                    telemetry.get("BatteryPower"),
                    telemetry.get("SystemPowerIn"),
                    telemetry.get("SystemLoad"),
                    telemetry.get("WallEnergyEstimate"),
                )
                power_w = power_mw / 1000.0
                if not power_w and voltage_mv and amperage_ma:
                    power_w = voltage_mv * amperage_ma / 1_000_000.0
                return level, temperature, power_w
        if not self.idevicediagnostics:
            return 0.0, 0.0, 0.0
        code, output = run_command(
            [self.idevicediagnostics, "-u", device.serial, "ioregentry", "AppleSmartBattery"],
            timeout=6.0,
        )
        if code != 0:
            return 0.0, 0.0, 0.0
        level = parse_first_float(r'"BatteryCurrentCapacity"\s*=\s*(\d+)', output)
        temp_raw = parse_first_float(r'"Temperature"\s*=\s*(\d+)', output)
        voltage_mv = parse_first_float(r'"Voltage"\s*=\s*(\d+)', output)
        amperage_ma = abs(parse_first_float(r'"InstantAmperage"\s*=\s*(-?\d+)', output))
        temperature = temp_raw / 100.0 if temp_raw > 100 else temp_raw
        power_w = voltage_mv / 1000.0 * amperage_ma / 1000.0 if voltage_mv and amperage_ma else 0.0
        return level, temperature, power_w

    def _process_metrics(
        self,
        device: DeviceInfo,
        app_id: str,
        pid: int | None,
        process_names: list[str],
    ) -> tuple[float, float, str, int | None]:
        last_error = ""
        if pid is not None:
            code, output = self._sysmon_process_snapshot(device, f"pid={pid}", timeout=8.0)
            if self._is_tunnel_error(output):
                return 0.0, 0.0, "iOS CPU/内存实时采集需要先启动 tunneld：双击“启动iOS采集服务.command”并保持窗口打开。", None
            if code == 0:
                item = self._find_sysmon_item(self._sysmon_items(output), pid, process_names)
                if item:
                    cpu, memory = self._metrics_from_sysmon_item(item)
                    return cpu, memory, "", self._item_pid(item) or pid
                last_error = "PID 已失效或目标进程未在过滤结果中。"
            else:
                last_error = self._short_error(output)

        code, output = self._sysmon_process_snapshot(device, None, timeout=10.0)
        if self._is_tunnel_error(output):
            return 0.0, 0.0, "iOS CPU/内存实时采集需要先启动 tunneld：双击“启动iOS采集服务.command”并保持窗口打开。", None
        if code != 0:
            detail = self._short_error(output) or last_error
            return 0.0, 0.0, f"iOS sysmon 采样失败：{detail}", None

        item = self._find_sysmon_item(self._sysmon_items(output), pid, process_names)
        if item:
            cpu, memory = self._metrics_from_sysmon_item(item)
            return cpu, memory, "", self._item_pid(item)

        target = app_id
        if process_names:
            target = f"{app_id} / {', '.join(process_names)}"
        if last_error:
            return 0.0, 0.0, f"iOS sysmon 未匹配到目标进程：{target}（{last_error}）", None
        return 0.0, 0.0, f"iOS sysmon 未匹配到目标进程：{target}。请确认 App 正在前台运行。", None

    def _sysmon_process_snapshot(self, device: DeviceInfo, filter_expr: str | None, timeout: float) -> tuple[int, str]:
        args = [
            "developer",
            "dvt",
            "sysmon",
            "process",
            "single",
            "--udid",
            device.serial,
        ]
        if filter_expr:
            args.extend(["-f", filter_expr])
        args.extend(
            [
                "-k",
                "pid",
                "-k",
                "name",
                "-k",
                "cpuUsage",
                "-k",
                "physFootprint",
                "-k",
                "memResidentSize",
                "-k",
                "memVirtualSize",
            ]
        )
        return self._pmobile(args, timeout=timeout)

    def _sysmon_items(self, output: str) -> list[dict[str, object]]:
        payload = extract_json_payload(output)
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            return [payload]

        items: list[dict[str, object]] = []
        for line in output.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                items.append(item)
        return items

    def _find_sysmon_item(
        self,
        items: list[dict[str, object]],
        pid: int | None,
        process_names: list[str],
    ) -> dict[str, object] | None:
        normalized_names = {
            self._normalize_process_name(name)
            for name in process_names
            if self._normalize_process_name(name)
        }
        if pid is not None:
            for item in items:
                if self._item_pid(item) != pid:
                    continue
                name = str(item.get("name") or item.get("processName") or item.get("executable") or "")
                normalized = self._normalize_process_name(name)
                if not normalized_names or not normalized or normalized in normalized_names:
                    return item
        if not normalized_names:
            return None

        for item in items:
            name = str(item.get("name") or item.get("processName") or item.get("executable") or "")
            if self._normalize_process_name(name) in normalized_names:
                return item
        return None

    def _metrics_from_sysmon_item(self, item: dict[str, object]) -> tuple[float, float]:
        cpu = self._lookup_float(item, {"cpuusage", "cpu", "cpupercent"})
        memory = self._lookup_float(
            item,
            {"physfootprint", "memresidentsize", "residentsize", "residentmemory", "memory"},
        )
        if memory > 10_000:
            memory = memory / 1024.0 / 1024.0
        return cpu, memory

    def _item_pid(self, item: dict[str, object]) -> int | None:
        pid = int(self._to_float(item.get("pid") or item.get("processIdentifier")))
        return pid if pid > 0 else None

    @staticmethod
    def _is_tunnel_error(output: str) -> bool:
        text = output.lower()
        return (
            "unable to connect to tunneld" in text
            or "you can start one using" in text
            or "start-tunnel" in text
            or "remote tunneld" in text
            or "requires root privileges" in text
            or "device is not connected" in text
        )

    @staticmethod
    def _short_error(output: str) -> str:
        for line in reversed(output.splitlines()):
            stripped = line.strip()
            if stripped and "warning" not in stripped.lower():
                return stripped[:160]
        return "请确认设备已解锁、已信任电脑，并已开启开发者模式。"

    @staticmethod
    def _extract_pid(output: str) -> int | None:
        for line in reversed(output.splitlines()):
            stripped = line.strip()
            if stripped.isdigit():
                return int(stripped)
        if "ERROR" in output or "Unable" in output:
            return None
        return None

    @classmethod
    def _lookup_float(cls, payload: object, keys: set[str]) -> float:
        if isinstance(payload, dict):
            for key, value in payload.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
                if normalized in keys:
                    return cls._to_float(value)
            for value in payload.values():
                found = cls._lookup_float(value, keys)
                if found:
                    return found
        elif isinstance(payload, list):
            for value in payload:
                found = cls._lookup_float(value, keys)
                if found:
                    return found
        return 0.0

    @staticmethod
    def _to_float(value: object) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            match = re.search(r"-?\d+(?:\.\d+)?", value)
            if match:
                return float(match.group(0))
        return 0.0

    @classmethod
    def _first_positive(cls, *values: object) -> float:
        for value in values:
            number = cls._to_float(value)
            if number > 0:
                return number
        return 0.0

    @classmethod
    def _temperature_value(cls, value: object) -> float:
        raw = cls._to_float(value)
        if raw > 100_000:
            return raw / 100_000.0
        if raw > 1000:
            return raw / 100.0
        if raw > 100:
            return raw / 10.0
        return raw


class DemoAdapter(BaseAdapter):
    platform_name = "Demo"

    def __init__(self) -> None:
        self.phase = random.random() * 10.0

    def list_devices(self) -> list[DeviceInfo]:
        return [
            DeviceInfo("Android", "demo-android", "Pixel 8 Demo", "15", "Pixel 8", "ready", "演示数据"),
            DeviceInfo("iOS", "demo-ios", "iPhone Demo", "18.4", "iPhone16,2", "ready", "演示数据"),
        ]

    def list_apps(self, device: DeviceInfo) -> list[str]:
        return ["com.example.game", "com.example.app", "com.company.live"]

    def foreground_app(self, device: DeviceInfo) -> str:
        return "com.example.game"

    def collect_sample(self, device: DeviceInfo, app_id: str, start_time: float) -> PerfSample:
        current = time.time()
        elapsed = current - start_time
        jitter = random.uniform(-1.0, 1.0)
        fps = max(30.0, min(120.0, 58.0 + math.sin(elapsed / 5.0 + self.phase) * 5.0 + jitter))
        jank = max(0.0, 5.0 + math.sin(elapsed / 7.0) * 4.0 + random.uniform(-1, 1))
        cpu = max(0.0, 38.0 + math.sin(elapsed / 8.0) * 17.0 + random.uniform(-4, 4))
        memory = 740.0 + math.sin(elapsed / 18.0) * 80.0 + elapsed * 0.6
        temp = 34.0 + min(elapsed / 90.0, 7.0) + math.sin(elapsed / 12.0)
        power = 2.8 + math.sin(elapsed / 4.5) * 0.8 + random.uniform(0, 0.25)
        rx = max(0.0, 140.0 + math.sin(elapsed / 3.0) * 90.0 + random.uniform(-30, 30))
        tx = max(0.0, 40.0 + math.cos(elapsed / 4.0) * 24.0 + random.uniform(-10, 10))
        return PerfSample(
            timestamp=current,
            elapsed=elapsed,
            fps=fps,
            jank_percent=jank,
            cpu_percent=cpu,
            memory_mb=memory,
            battery_percent=max(1.0, 92.0 - elapsed / 110.0),
            temperature_c=temp,
            power_w=power,
            rx_kbps=rx,
            tx_kbps=tx,
        )

    def capture_screenshot(self, device: DeviceInfo, target: Path) -> Path | None:
        return None


class SessionRecorder:
    def __init__(self) -> None:
        self.samples: list[PerfSample] = []
        self.markers: list[dict[str, float | str]] = []
        self.logs: list[str] = []
        self.start_time = 0.0
        self.device: DeviceInfo | None = None
        self.app_id = ""

    def reset(self, device: DeviceInfo, app_id: str) -> None:
        self.samples.clear()
        self.markers.clear()
        self.logs.clear()
        self.start_time = time.time()
        self.device = device
        self.app_id = app_id

    def append(self, sample: PerfSample) -> None:
        self.samples.append(sample)
        if len(self.samples) > SAMPLE_LIMIT:
            self.samples = self.samples[-SAMPLE_LIMIT:]

    def log(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.logs.append(f"[{stamp}] {text}")
        self.logs = self.logs[-300:]

    def mark(self, label: str) -> None:
        elapsed = time.time() - self.start_time if self.start_time else 0.0
        self.markers.append({"elapsed": round(elapsed, 3), "label": label})
        self.log(f"已添加标记：{label} @ {elapsed:.1f}s")

    def summary(self) -> dict[str, float | str]:
        if not self.samples:
            return {}

        def avg(name: str) -> float:
            values = [getattr(sample, name) for sample in self.samples if getattr(sample, name) > 0]
            return round(sum(values) / len(values), 3) if values else 0.0

        def peak(name: str) -> float:
            values = [getattr(sample, name) for sample in self.samples]
            return round(max(values), 3) if values else 0.0

        return {
            "device": self.device.display_name if self.device else "",
            "app_id": self.app_id,
            "duration_seconds": round(self.samples[-1].elapsed, 3),
            "avg_fps": avg("fps"),
            "avg_cpu_percent": avg("cpu_percent"),
            "peak_memory_mb": peak("memory_mb"),
            "peak_temperature_c": peak("temperature_c"),
            "avg_power_w": avg("power_w"),
            "avg_rx_kbps": avg("rx_kbps"),
            "avg_tx_kbps": avg("tx_kbps"),
        }

    def quality_summary(self) -> dict[str, object]:
        total = len(self.samples)
        if total <= 0:
            return {
                "sample_count": 0,
                "noted_samples": 0,
                "noted_percent": 0.0,
                "network_source": "无数据",
                "network_fallback_samples": 0,
                "network_fallback_percent": 0.0,
                "issues": [],
            }
        noted_samples = [sample for sample in self.samples if sample.note]
        fallback_samples = [sample for sample in self.samples if "设备级网络兜底" in sample.note]
        missing_tokens = {
            "FPS 未采集": ("FPS 未采集", "帧率数据缺失，通常与 Surface 识别、页面静止或系统输出受限有关。"),
            "FPS 当前无帧增量": ("FPS 无帧增量", "采样窗口内没有新增帧，低端机或静止页面可能更常见。"),
            "CPU 当前无进程增量": ("CPU 无进程增量", "CPU 进程计数未变化，可能是采样间隔过短或 /proc 读取受限。"),
            "网络未匹配": ("网络未匹配 UID", "未拿到目标 App UID，无法确认 per-UID 上下行。"),
            "无法按应用统计": ("网络无法按应用统计", "系统未开放目标 App per-UID 网络统计。"),
            "网络采集失败": ("网络采集失败", "网络采集通道返回错误。"),
            "未匹配到目标 PID": ("PID 未匹配", "目标进程未匹配，CPU/内存/FPS 可能不可信。"),
        }
        issues: list[dict[str, object]] = []
        for token, (label, detail) in missing_tokens.items():
            count = sum(1 for sample in self.samples if token in sample.note)
            if count:
                issues.append(
                    {
                        "label": label,
                        "count": count,
                        "percent": round(count / total * 100.0, 1),
                        "detail": detail,
                    }
                )
        if fallback_samples:
            issues.append(
                {
                    "label": "设备级网络兜底",
                    "count": len(fallback_samples),
                    "percent": round(len(fallback_samples) / total * 100.0, 1),
                    "detail": "上下行来自设备总流量，不是目标 App 独占流量。",
                }
            )
        network_source = "目标 App per-UID"
        if fallback_samples and len(fallback_samples) == total:
            network_source = "设备级网络兜底"
        elif fallback_samples:
            network_source = "per-UID + 设备级兜底"
        elif any("网络未匹配" in sample.note or "无法按应用统计" in sample.note for sample in self.samples):
            network_source = "per-UID 不可用"
        return {
            "sample_count": total,
            "noted_samples": len(noted_samples),
            "noted_percent": round(len(noted_samples) / total * 100.0, 1),
            "network_source": network_source,
            "network_fallback_samples": len(fallback_samples),
            "network_fallback_percent": round(len(fallback_samples) / total * 100.0, 1),
            "issues": issues,
        }

    def export_bundle(self, folder: Path) -> tuple[Path, Path, Path]:
        folder.mkdir(parents=True, exist_ok=True)
        device_label = safe_name(self.device.name if self.device else "device")
        app_label = safe_name(self.app_id or "app")
        base = folder / f"{device_label}_{app_label}_{now_slug()}"
        csv_path = base.with_suffix(".csv")
        json_path = base.with_suffix(".json")
        html_path = base.with_suffix(".html")
        headers = [
            "timestamp",
            "elapsed",
            "fps",
            "jank_percent",
            "cpu_percent",
            "memory_mb",
            "battery_percent",
            "temperature_c",
            "power_w",
            "rx_kbps",
            "tx_kbps",
            "note",
        ]
        with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            for sample in self.samples:
                row = asdict(sample)
                row["timestamp"] = datetime.fromtimestamp(sample.timestamp).isoformat(timespec="seconds")
                writer.writerow(row)
        payload = {
            "app": APP_NAME,
            "version": APP_VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "device": asdict(self.device) if self.device else None,
            "target_app": self.app_id,
            "summary": self.summary(),
            "quality": self.quality_summary(),
            "markers": self.markers,
            "samples": [asdict(sample) for sample in self.samples],
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        html_path.write_text(self._render_html(payload), encoding="utf-8")
        return csv_path, json_path, html_path

    def _render_html(self, payload: dict) -> str:
        summary = payload.get("summary", {})
        quality = payload.get("quality", {})
        summary_labels = {
            "device": "设备",
            "app_id": "目标应用",
            "duration_seconds": "时长",
            "avg_fps": "平均 FPS",
            "avg_cpu_percent": "平均 CPU",
            "peak_memory_mb": "峰值内存",
            "peak_temperature_c": "峰值温度",
            "avg_power_w": "平均功耗",
            "avg_rx_kbps": "平均下行",
            "avg_tx_kbps": "平均上行",
        }
        summary_units = {
            "duration_seconds": "s",
            "avg_fps": "FPS",
            "avg_cpu_percent": "%",
            "peak_memory_mb": "MB",
            "peak_temperature_c": "°C",
            "avg_power_w": "W",
            "avg_rx_kbps": "KB/s",
            "avg_tx_kbps": "KB/s",
        }
        kpi_keys = [
            "duration_seconds",
            "avg_fps",
            "avg_cpu_percent",
            "peak_memory_mb",
            "peak_temperature_c",
            "avg_power_w",
        ]
        kpi_cards = "".join(
            "<article class='kpi'>"
            f"<span>{html.escape(summary_labels.get(key, key))}</span>"
            f"<strong>{html.escape(str(summary.get(key, '-')))}</strong>"
            f"<em>{html.escape(summary_units.get(key, ''))}</em>"
            "</article>"
            for key in kpi_keys
        )
        rows = "".join(
            f"<tr><th>{html.escape(summary_labels.get(key, key))}</th><td>{html.escape(str(value))} {html.escape(summary_units.get(key, ''))}</td></tr>"
            for key, value in summary.items()
        )
        marker_rows = "".join(
            f"<tr><td>{index}</td><td>{html.escape(str(marker.get('elapsed', '')))}s</td><td>{html.escape(str(marker.get('label', '')))}</td></tr>"
            for index, marker in enumerate(self.markers, start=1)
        ) or "<tr><td colspan='3'>暂无标记</td></tr>"
        quality_cards = "".join(
            "<article class='quality-card'>"
            f"<span>{html.escape(label)}</span>"
            f"<strong>{html.escape(value)}</strong>"
            f"<p>{html.escape(detail)}</p>"
            "</article>"
            for label, value, detail in [
                ("样本数", str(quality.get("sample_count", 0)), "本次报告中的原始采样点数量。"),
                ("带说明样本", f"{quality.get('noted_samples', 0)} / {quality.get('noted_percent', 0)}%", "出现采集说明、异常或兜底提示的样本。"),
                ("网络来源", str(quality.get("network_source", "无数据")), "优先目标 App per-UID；不可用时可能使用设备级兜底。"),
                ("网络兜底", f"{quality.get('network_fallback_samples', 0)} / {quality.get('network_fallback_percent', 0)}%", "设备级网络兜底不是目标 App 独占流量。"),
            ]
        )
        issue_rows = "".join(
            f"<tr><td>{html.escape(str(issue.get('label', '')))}</td><td>{html.escape(str(issue.get('count', 0)))}</td><td>{html.escape(str(issue.get('percent', 0)))}%</td><td>{html.escape(str(issue.get('detail', '')))}</td></tr>"
            for issue in quality.get("issues", [])
            if isinstance(issue, dict)
        ) or "<tr><td colspan='4'>未发现明显采集异常或兜底说明</td></tr>"
        quality_intervals = quality_intervals_from_points(
            [(float(sample.elapsed), sample_quality_tag(sample)) for sample in self.samples]
        )
        interval_rows = "".join(
            "<tr>"
            f"<td>{html.escape('采集异常' if str(interval.get('quality')) == 'issue' else '设备级兜底')}</td>"
            f"<td>{html.escape(format_report_seconds(float(interval.get('start', 0.0))))}</td>"
            f"<td>{html.escape(format_report_seconds(float(interval.get('end', 0.0))))}</td>"
            f"<td>{html.escape(format_report_seconds(max(0.0, float(interval.get('end', 0.0)) - float(interval.get('start', 0.0)))))}</td>"
            "</tr>"
            for interval in quality_intervals
        ) or "<tr><td colspan='4'>未发现连续异常或兜底区间</td></tr>"
        chart_cards = "".join(
            f"<section class='chart-card' data-metric='{key}'><div class='chart-head'><div><h3>{title}</h3><p>{desc}</p></div><div class='chart-stat' id='stat-{key}'>--</div></div><div class='chart-scroll'><canvas id='chart-{key}'></canvas></div></section>"
            for key, title, desc in [
                ("fps", "FPS 帧率", "越高越流畅，关注突降和长时间低帧。"),
                ("jank_percent", "Jank 卡顿率", "越低越稳定，尖峰通常对应卡顿。"),
                ("cpu_percent", "CPU 进程占用", "观察峰值、持续高位和突降后的恢复。"),
                ("memory_mb", "内存", "关注持续爬升和峰值。"),
                ("temperature_c", "温度", "观察发热趋势和平台期。"),
                ("power_w", "功耗", "观察功耗峰值和平均负载。"),
                ("rx_kbps", "下行网络", "接收流量速率。"),
                ("tx_kbps", "上行网络", "发送流量速率。"),
            ]
        )
        chart_config = [
            {"key": "fps", "unit": "FPS", "color": "#2563eb", "suggestedMax": 60, "decimals": 1, "guide": 60, "guideLabel": "60 FPS"},
            {"key": "jank_percent", "unit": "%", "color": "#f59e0b", "suggestedMax": 10, "decimals": 1, "guide": 5, "guideLabel": "5%"},
            {"key": "cpu_percent", "unit": "%", "color": "#ef4444", "suggestedMax": 100, "decimals": 1, "guide": 80, "guideLabel": "80%"},
            {"key": "memory_mb", "unit": "MB", "color": "#4f46e5", "suggestedMax": 0, "decimals": 1},
            {"key": "temperature_c", "unit": "°C", "color": "#dc2626", "suggestedMax": 45, "decimals": 1, "guide": 42, "guideLabel": "42°C"},
            {"key": "power_w", "unit": "W", "color": "#0891b2", "suggestedMax": 5, "decimals": 2},
            {"key": "rx_kbps", "unit": "KB/s", "color": "#16a34a", "suggestedMax": 1, "decimals": 1},
            {"key": "tx_kbps", "unit": "KB/s", "color": "#0d9488", "suggestedMax": 1, "decimals": 1},
        ]
        report_samples = []
        for sample in self.samples:
            row = asdict(sample)
            row["qualityTag"] = sample_quality_tag(sample)
            report_samples.append(row)
        data = json.dumps(report_samples, ensure_ascii=False).replace("</", "<\\/")
        markers = json.dumps(self.markers, ensure_ascii=False).replace("</", "<\\/")
        charts = json.dumps(chart_config, ensure_ascii=False).replace("</", "<\\/")
        html_text = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>__APP_NAME__ Report</title>
  <style>
    * { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #172033; background: #edf1f6; }
    header { padding: 24px 32px; color: white; background: #142033; }
    main { padding: 24px 32px 36px; }
    h1 { margin: 0 0 8px; font-size: 24px; }
    h2 { margin: 28px 0 12px; font-size: 18px; }
    h3 { margin: 0; font-size: 16px; }
    p { margin: 5px 0 0; color: #64748b; font-size: 13px; }
    table { width: 100%; border-collapse: collapse; background: white; border: 1px solid #d8e0ea; }
    th, td { padding: 10px 12px; border-bottom: 1px solid #e6ebf2; text-align: left; }
    th { width: 220px; color: #5b687a; background: #f7f9fc; }
    .meta { color: #c7d2e3; }
    .kpi-grid { display: grid; grid-template-columns: repeat(6, minmax(120px, 1fr)); gap: 12px; }
    .kpi { padding: 14px 16px; background: white; border: 1px solid #d8e0ea; border-radius: 8px; }
    .kpi span { display: block; color: #64748b; font-size: 13px; }
    .kpi strong { display: inline-block; margin-top: 8px; font-size: 24px; line-height: 1; }
    .kpi em { margin-left: 5px; color: #64748b; font-style: normal; }
    .quality-grid { display: grid; grid-template-columns: repeat(4, minmax(160px, 1fr)); gap: 12px; }
    .quality-card { padding: 14px 16px; background: white; border: 1px solid #d8e0ea; border-radius: 8px; }
    .quality-card span { display: block; color: #64748b; font-size: 13px; }
    .quality-card strong { display: block; margin-top: 7px; font-size: 19px; }
    .quality-card p { min-height: 34px; }
    .issue-table th, .issue-table td { width: auto; }
    .issue-table th:nth-child(2), .issue-table td:nth-child(2) { width: 100px; }
    .issue-table th:nth-child(3), .issue-table td:nth-child(3) { width: 100px; }
    .legend { display: flex; flex-wrap: wrap; gap: 12px 18px; margin: 10px 0 14px; color: #475569; font-size: 13px; }
    .legend-item { display: inline-flex; align-items: center; gap: 7px; }
    .legend-dot { width: 10px; height: 10px; border-radius: 50%; background: #2563eb; display: inline-block; }
    .legend-ring { width: 12px; height: 12px; border-radius: 50%; border: 2px solid #f59e0b; display: inline-block; }
    .legend-triangle { width: 0; height: 0; border-left: 6px solid transparent; border-right: 6px solid transparent; border-bottom: 11px solid #ef4444; display: inline-block; }
    .chart-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
    .chart-card { min-width: 0; padding: 16px; background: white; border: 1px solid #d8e0ea; border-radius: 8px; }
    .chart-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; margin-bottom: 10px; }
    .chart-stat { flex: 0 0 auto; color: #172033; font-weight: 700; text-align: right; white-space: nowrap; }
    .chart-scroll { width: 100%; overflow-x: auto; overflow-y: hidden; padding-bottom: 8px; scrollbar-gutter: stable; }
    .chart-scroll canvas { min-width: 100%; height: 250px; display: block; }
    .marker-table th, .marker-table td { width: auto; }
    .marker-table th:first-child, .marker-table td:first-child { width: 72px; }
    .marker-table th:nth-child(2), .marker-table td:nth-child(2) { width: 160px; }
    .hint { color: #64748b; font-size: 13px; }
    @media (max-width: 1100px) {
      .kpi-grid { grid-template-columns: repeat(3, minmax(120px, 1fr)); }
      .quality-grid { grid-template-columns: repeat(2, minmax(160px, 1fr)); }
      .chart-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>__APP_NAME__ 性能报告</h1>
    <div class="meta">__CREATED_AT__ · __DEVICE__ · __APP_ID__</div>
  </header>
  <main>
    <h2>摘要</h2>
    <div class="kpi-grid">__KPI_CARDS__</div>
    <table style="margin-top: 16px;">__SUMMARY_ROWS__</table>
    <h2>采集质量</h2>
    <div class="quality-grid">__QUALITY_CARDS__</div>
    <table class="issue-table" style="margin-top: 16px;"><tr><th>类型</th><th>样本数</th><th>占比</th><th>说明</th></tr>__ISSUE_ROWS__</table>
    <h2>异常区间</h2>
    <table class="issue-table"><tr><th>类型</th><th>开始</th><th>结束</th><th>持续</th></tr>__INTERVAL_ROWS__</table>
    <h2>曲线</h2>
    <div class="hint">每张图使用独立单位和坐标轴；虚线为参考阈值，标记会显示为竖线。</div>
    <div class="legend" aria-label="曲线标识">
      <span class="legend-item"><span class="legend-dot"></span>正常样本</span>
      <span class="legend-item"><span class="legend-ring"></span>设备级网络兜底</span>
      <span class="legend-item"><span class="legend-triangle"></span>采集异常样本</span>
      <span class="legend-item">浅橙/浅红背景表示连续兜底或异常区间</span>
    </div>
    <div class="chart-grid">__CHART_CARDS__</div>
    <h2>标记</h2>
    <table class="marker-table"><tr><th>序号</th><th>Elapsed</th><th>Label</th></tr>__MARKER_ROWS__</table>
  </main>
  <script>
    const samples = __DATA__;
    const markers = __MARKERS__;
    const chartConfigs = __CHARTS__;
    const VIEW_SECONDS = 30 * 60;
    const MIN_VIEW_SECONDS = 10;
    let syncingChartScroll = false;

    const finiteValues = (key) => samples
      .map(sample => Number(sample[key] || 0))
      .filter(value => Number.isFinite(value));

    const fmt = (value, decimals = 1) => Number(value || 0).toFixed(decimals);
    const timeLabel = (seconds) => {
      const total = Math.max(0, Math.round(Number(seconds || 0)));
      const h = Math.floor(total / 3600);
      const m = Math.floor((total % 3600) / 60);
      const s = String(total % 60).padStart(2, '0');
      if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${s}`;
      return `${m}:${s}`;
    };

    function niceCeil(value) {
      if (!Number.isFinite(value) || value <= 0) return 1;
      const power = Math.pow(10, Math.floor(Math.log10(value)));
      const scaled = value / power;
      const nice = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
      return nice * power;
    }

    function syncChartScroll(source) {
      const sourceMax = source.scrollWidth - source.clientWidth;
      if (sourceMax <= 0) return;
      const ratio = source.scrollLeft / sourceMax;
      syncingChartScroll = true;
      document.querySelectorAll('.chart-scroll').forEach(target => {
        if (target === source) return;
        const targetMax = target.scrollWidth - target.clientWidth;
        if (targetMax <= 0) return;
        target.dataset.autoScrolling = '1';
        target.scrollLeft = ratio * targetMax;
        requestAnimationFrame(() => { target.dataset.autoScrolling = '0'; });
      });
      requestAnimationFrame(() => { syncingChartScroll = false; });
    }

    function markerDisplayText(label) {
      const text = String(label || '标记');
      return text.length > 10 ? `${text.slice(0, 9)}…` : text;
    }

    function layoutMarkerLabels(ctx, xFor, width, pad) {
      ctx.font = '12px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
      const laneCount = 5;
      const laneHeight = 22;
      const gap = 6;
      const lanes = Array(laneCount).fill(-Infinity);
      return markers
        .map((marker, index) => {
          const x = xFor(marker.elapsed);
          return {
            marker,
            index,
            x,
            raw: String(marker.label || `标记 ${index + 1}`),
          };
        })
        .filter(item => item.x >= pad.left && item.x <= width - pad.right)
        .sort((left, right) => left.x - right.x)
        .map(item => {
          const label = markerDisplayText(item.raw);
          const boxH = 18;
          const boxW = Math.min(Math.max(ctx.measureText(label).width + 16, 36), 132);
          let labelX = Math.min(Math.max(item.x + 5, pad.left + 2), width - pad.right - boxW - 2);
          let lane = lanes.findIndex(rightEdge => labelX >= rightEdge + gap);
          let compact = false;
          if (lane < 0) {
            compact = true;
            lane = item.index % laneCount;
            labelX = Math.min(Math.max(item.x - 4, pad.left + 2), width - pad.right - 10);
          } else {
            lanes[lane] = labelX + boxW;
          }
          return {
            ...item,
            label,
            compact,
            boxW,
            boxH,
            labelX,
            labelY: pad.top + 6 + lane * laneHeight,
          };
        });
    }

    function drawMarkerLabel(ctx, layout) {
      if (layout.compact) {
        ctx.fillStyle = '#475569';
        ctx.beginPath();
        ctx.arc(layout.x, layout.labelY + 8, 3.5, 0, Math.PI * 2);
        ctx.fill();
        return;
      }
      ctx.fillStyle = 'rgba(255, 255, 255, 0.88)';
      ctx.fillRect(layout.labelX, layout.labelY, layout.boxW, layout.boxH);
      ctx.strokeStyle = '#cbd5e1';
      ctx.lineWidth = 1;
      ctx.strokeRect(layout.labelX, layout.labelY, layout.boxW, layout.boxH);
      ctx.fillStyle = '#334155';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(layout.label, layout.labelX + 7, layout.labelY + layout.boxH / 2);
      ctx.textBaseline = 'alphabetic';
    }

    function sampleQualityTag(sample) {
      const note = String(sample.note || '');
      if (sample.qualityTag) return sample.qualityTag;
      if (note.includes('设备级网络兜底')) return 'fallback';
      const issueTokens = ['未采集', '无帧增量', '无进程增量', '未匹配', '无法按应用统计', '采集失败', '采集不可用', '未找到运行中的'];
      return issueTokens.some(token => note.includes(token)) ? 'issue' : 'ok';
    }

    function drawQualityMarker(ctx, x, y, tag) {
      if (tag === 'fallback') {
        ctx.save();
        ctx.strokeStyle = '#f59e0b';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(x, y, 5.2, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
        return;
      }
      if (tag === 'issue') {
        ctx.save();
        ctx.fillStyle = '#ef4444';
        ctx.beginPath();
        ctx.moveTo(x, y - 6);
        ctx.lineTo(x - 5.5, y + 5);
        ctx.lineTo(x + 5.5, y + 5);
        ctx.closePath();
        ctx.fill();
        ctx.restore();
      }
    }

    function qualityIntervals(points) {
      const intervals = [];
      let active = null;
      points
        .map(point => ({ elapsed: Number(point.elapsed || 0), quality: sampleQualityTag(point) }))
        .sort((left, right) => left.elapsed - right.elapsed)
        .forEach(point => {
          const tag = point.quality === 'issue' || point.quality === 'fallback' ? point.quality : 'ok';
          if (tag === 'ok') {
            if (active) intervals.push(active);
            active = null;
            return;
          }
          if (!active || active.quality !== tag) {
            if (active) intervals.push(active);
            active = { start: point.elapsed, end: point.elapsed, quality: tag };
          } else {
            active.end = point.elapsed;
          }
        });
      if (active) intervals.push(active);
      return intervals;
    }

    function drawQualityIntervals(ctx, intervals, xFor, pad, plotH, width) {
      intervals.forEach(interval => {
        const x1 = Math.max(pad.left, xFor(interval.start));
        let x2 = Math.min(width - pad.right, xFor(interval.end));
        if (x2 <= x1) x2 = Math.min(width - pad.right, x1 + 4);
        ctx.save();
        ctx.fillStyle = interval.quality === 'issue' ? 'rgba(239, 68, 68, 0.10)' : 'rgba(245, 158, 11, 0.14)';
        ctx.fillRect(x1, pad.top, x2 - x1, plotH);
        ctx.restore();
      });
    }

    function drawChart(config) {
      const canvas = document.getElementById(`chart-${config.key}`);
      if (!canvas) return;
      const scroller = canvas.parentElement;
      const pad = { left: 56, right: 18, top: 18, bottom: 34 };
      const dpr = window.devicePixelRatio || 1;
      const viewportWidth = Math.max(640, Math.round(scroller.getBoundingClientRect().width || 640));
      const viewportPlotW = Math.max(1, viewportWidth - pad.left - pad.right);
      const elapsed = samples
        .map(sample => Number(sample.elapsed || 0))
        .filter(value => Number.isFinite(value));
      const timelineSeconds = Math.max(...elapsed, MIN_VIEW_SECONDS);
      const viewportSeconds = Math.min(timelineSeconds, VIEW_SECONDS);
      const width = Math.round(pad.left + pad.right + viewportPlotW * Math.max(1, timelineSeconds / viewportSeconds));
      const height = 250;
      const wasAtEnd = scroller.scrollLeft + scroller.clientWidth >= scroller.scrollWidth - 4;
      const stickToEnd = !scroller.dataset.userScrolled || wasAtEnd;
      if (!scroller.dataset.bound) {
        scroller.addEventListener('scroll', () => {
          if (scroller.dataset.autoScrolling === '1' || syncingChartScroll) return;
          scroller.dataset.userScrolled = '1';
          syncChartScroll(scroller);
        });
        scroller.dataset.bound = '1';
      }

      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      const ctx = canvas.getContext('2d');
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      const plotW = width - pad.left - pad.right;
      const plotH = height - pad.top - pad.bottom;
      const values = finiteValues(config.key);
      const valueMax = Math.max(...values, Number(config.suggestedMax || 0), 1);
      const maxY = niceCeil(valueMax * 1.08);
      const minY = 0;
      const range = Math.max(maxY - minY, 1);
      const xFor = (seconds) => pad.left + (Math.max(0, Math.min(Number(seconds || 0), timelineSeconds)) / timelineSeconds) * plotW;
      const yFor = (value) => pad.top + (1 - (Number(value || 0) - minY) / range) * plotH;
      const markerLayouts = layoutMarkerLabels(ctx, xFor, width, pad);

      ctx.clearRect(0, 0, width, height);
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, width, height);

      ctx.strokeStyle = '#e6edf5';
      ctx.lineWidth = 1;
      ctx.fillStyle = '#64748b';
      ctx.font = '12px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
      ctx.textAlign = 'left';
      const axisLabelX = [6];
      for (let second = viewportSeconds; second < timelineSeconds; second += viewportSeconds) {
        axisLabelX.push(Math.max(6, xFor(second) - pad.left + 6));
      }
      for (let i = 0; i <= 4; i++) {
        const y = pad.top + (plotH / 4) * i;
        const value = maxY - (range / 4) * i;
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(width - pad.right, y);
        ctx.stroke();
        for (const labelX of axisLabelX) {
          ctx.fillText(`${fmt(value, config.decimals)}${config.unit}`, labelX, y + 4);
        }
      }

      ctx.fillStyle = '#64748b';
      ctx.textAlign = 'center';
      const labelStep = Math.max(viewportSeconds / 2, 1);
      for (let second = 0; second <= timelineSeconds + 1; second += labelStep) {
        const x = xFor(second);
        ctx.fillText(timeLabel(second), x, height - 10);
      }
      if (timelineSeconds % labelStep > 1) {
        ctx.fillText(timeLabel(timelineSeconds), xFor(timelineSeconds), height - 10);
      }
      ctx.textAlign = 'left';

      if (config.guide && config.guide <= maxY) {
        const y = yFor(config.guide);
        ctx.setLineDash([6, 5]);
        ctx.strokeStyle = config.color;
        ctx.globalAlpha = 0.45;
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(width - pad.right, y);
        ctx.stroke();
        ctx.globalAlpha = 1;
        ctx.setLineDash([]);
        ctx.fillStyle = config.color;
        ctx.textAlign = 'right';
        ctx.fillText(config.guideLabel || `${config.guide}${config.unit}`, width - pad.right - 8, y - 6);
        ctx.textAlign = 'left';
      }

      for (const layout of markerLayouts) {
        const x = layout.x;
        ctx.strokeStyle = '#94a3b8';
        ctx.setLineDash([3, 4]);
        ctx.beginPath();
        ctx.moveTo(x, pad.top);
        ctx.lineTo(x, pad.top + plotH);
        ctx.stroke();
        ctx.setLineDash([]);
      }
      drawQualityIntervals(ctx, qualityIntervals(samples), xFor, pad, plotH, width);

      if (!values.length || values.every(value => value === 0)) {
        ctx.fillStyle = '#94a3b8';
        ctx.font = '13px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
        ctx.fillText('无有效数据', pad.left + 12, pad.top + plotH / 2);
      } else {
        const gradient = ctx.createLinearGradient(0, pad.top, 0, pad.top + plotH);
        gradient.addColorStop(0, `${config.color}2e`);
        gradient.addColorStop(1, `${config.color}00`);
        ctx.beginPath();
        samples.forEach((sample, index) => {
          const x = xFor(sample.elapsed);
          const y = yFor(sample[config.key]);
          if (index === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.lineTo(xFor(samples[samples.length - 1]?.elapsed || 0), pad.top + plotH);
        ctx.lineTo(xFor(samples[0]?.elapsed || 0), pad.top + plotH);
        ctx.closePath();
        ctx.fillStyle = gradient;
        ctx.fill();

        ctx.beginPath();
        samples.forEach((sample, index) => {
          const x = xFor(sample.elapsed);
          const y = yFor(sample[config.key]);
          if (index === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.strokeStyle = config.color;
        ctx.lineWidth = 2.4;
        ctx.stroke();

        if (samples.length <= 80) {
          ctx.fillStyle = config.color;
          samples.forEach(sample => {
            const x = xFor(sample.elapsed);
            const y = yFor(sample[config.key]);
            ctx.beginPath();
            ctx.arc(x, y, 2.4, 0, Math.PI * 2);
            ctx.fill();
          });
        }
        samples.forEach(sample => {
          const tag = sampleQualityTag(sample);
          if (tag === 'ok') return;
          const x = xFor(sample.elapsed);
          const y = yFor(sample[config.key]);
          drawQualityMarker(ctx, x, y, tag);
        });
      }

      for (const layout of markerLayouts) {
        drawMarkerLabel(ctx, layout);
      }

      const avg = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
      const peak = values.length ? Math.max(...values) : 0;
      const stat = document.getElementById(`stat-${config.key}`);
      if (stat) {
        stat.textContent = `avg ${fmt(avg, config.decimals)}${config.unit} / max ${fmt(peak, config.decimals)}${config.unit}`;
      }
      if (stickToEnd) {
        scroller.dataset.autoScrolling = '1';
        scroller.scrollLeft = scroller.scrollWidth;
        requestAnimationFrame(() => { scroller.dataset.autoScrolling = '0'; });
      }
    }

    function renderAll() {
      for (const config of chartConfigs) drawChart(config);
    }
    renderAll();
    window.addEventListener('resize', renderAll);
  </script>
</body>
</html>
"""
        return (
            html_text
            .replace("__APP_NAME__", html.escape(APP_NAME))
            .replace("__CREATED_AT__", html.escape(str(payload.get("created_at", ""))))
            .replace("__DEVICE__", html.escape(str(summary.get("device", ""))))
            .replace("__APP_ID__", html.escape(str(summary.get("app_id", ""))))
            .replace("__KPI_CARDS__", kpi_cards)
            .replace("__SUMMARY_ROWS__", rows)
            .replace("__QUALITY_CARDS__", quality_cards)
            .replace("__ISSUE_ROWS__", issue_rows)
            .replace("__INTERVAL_ROWS__", interval_rows)
            .replace("__CHART_CARDS__", chart_cards)
            .replace("__MARKER_ROWS__", marker_rows)
            .replace("__DATA__", data)
            .replace("__MARKERS__", markers)
            .replace("__CHARTS__", charts)
        )


class SamplerThread(threading.Thread):
    def __init__(
        self,
        adapter: BaseAdapter,
        device: DeviceInfo,
        app_id: str,
        interval: float,
        output: queue.Queue[tuple[str, object]],
    ) -> None:
        super().__init__(daemon=True)
        self.adapter = adapter
        self.device = device
        self.app_id = app_id
        self.interval = interval
        self.output = output
        self.stop_event = threading.Event()
        self.start_time = time.time()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        try:
            self.adapter.start_session(self.device, self.app_id)
            self.output.put(("log", "采集会话已初始化。"))
        except Exception as exc:
            self.output.put(("log", f"初始化采集失败：{exc}"))
        while not self.stop_event.is_set():
            loop_start = time.time()
            try:
                sample = self.adapter.collect_sample(self.device, self.app_id, self.start_time)
                self.output.put(("sample", sample))
                if sample.note:
                    self.output.put(("note", sample.note))
            except Exception as exc:
                self.output.put(("log", f"采样失败：{exc}"))
            spent = time.time() - loop_start
            self.stop_event.wait(max(self.interval - spent, 0.05))
        try:
            self.adapter.stop_session(self.device, self.app_id)
        except Exception as exc:
            self.output.put(("log", f"停止采集清理失败：{exc}"))
        self.output.put(("log", "采集会话已停止。"))


class MetricCard(ttk.Frame):
    def __init__(self, master: tk.Widget, title: str, unit: str, color: str) -> None:
        super().__init__(master, style="Card.TFrame", padding=(12, 10))
        self.unit = unit
        self.color = color
        self.title_label = ttk.Label(self, text=title, style="CardTitle.TLabel")
        self.title_label.pack(anchor="w")
        self.value_label = ttk.Label(self, text=f"-- {unit}", style="MetricValue.TLabel")
        self.value_label.pack(anchor="w", pady=(8, 0))
        self.sub_label = ttk.Label(self, text="等待采集", style="Muted.TLabel")
        self.sub_label.pack(anchor="w", pady=(6, 0))

    def set_value(self, value: float, sub: str = "") -> None:
        if self.unit == "%":
            display = f"{value:.1f}{self.unit}"
        elif self.unit in ("MB", "KB/s"):
            display = f"{value:.1f} {self.unit}"
        elif self.unit == "C":
            display = f"{value:.1f}°C"
        elif self.unit == "W":
            display = f"{value:.2f} W"
        else:
            display = f"{value:.1f} {self.unit}".strip()
        self.value_label.configure(text=display)
        self.sub_label.configure(text=sub or "实时")


class GraphPanel(ttk.Frame):
    def __init__(self, master: tk.Widget, title: str, metric: str, unit: str, color: str, max_points: int = SAMPLE_LIMIT) -> None:
        super().__init__(master, style="Panel.TFrame", padding=(10, 10))
        self.title = title
        self.metric = metric
        self.unit = unit
        self.color = color
        self.max_points = max_points
        self.points: list[tuple[float, float, str]] = []
        self.view_start = 0.0
        self.view_seconds = 10.0
        self.header = ttk.Frame(self, style="Panel.TFrame")
        self.header.pack(fill="x")
        ttk.Label(self.header, text=title, style="PanelTitle.TLabel").pack(side="left")
        self.value_var = tk.StringVar(value="--")
        ttk.Label(self.header, textvariable=self.value_var, style="GraphValue.TLabel").pack(side="right")
        self.canvas = tk.Canvas(self, height=132, background="#FFFFFF", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, pady=(8, 0))
        self.canvas.bind("<Configure>", lambda _event: self.redraw())

    def append(self, elapsed: float, value: float, quality: str = "ok") -> None:
        self.points.append((max(0.0, float(elapsed)), float(value), quality))
        self.points = self.points[-self.max_points :]
        self.value_var.set(self._format(value))
        self.redraw()

    def reset(self) -> None:
        self.points.clear()
        self.view_start = 0.0
        self.view_seconds = 10.0
        self.value_var.set("--")
        self.redraw()

    def set_view(self, view_start: float, view_seconds: float) -> None:
        self.view_start = max(0.0, float(view_start))
        self.view_seconds = max(1.0, float(view_seconds))
        self.redraw()

    def _format(self, value: float) -> str:
        if self.unit == "%":
            return f"{value:.1f}%"
        if self.unit == "C":
            return f"{value:.1f}°C"
        if self.unit == "W":
            return f"{value:.2f} W"
        return f"{value:.1f} {self.unit}".strip()

    @staticmethod
    def _format_time(seconds: float) -> str:
        total = max(0, int(round(seconds)))
        hours = total // 3600
        minutes = (total % 3600) // 60
        secs = total % 60
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _visible_points(self, view_start: float, view_end: float) -> list[tuple[float, float, str]]:
        visible: list[tuple[float, float, str]] = []
        previous: tuple[float, float, str] | None = None
        for point in self.points:
            elapsed, _value, _quality = point
            if elapsed < view_start:
                previous = point
                continue
            if elapsed <= view_end:
                if previous is not None and not visible:
                    visible.append(previous)
                visible.append(point)
                continue
            if visible:
                visible.append(point)
            break
        return visible

    def redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 320)
        height = max(canvas.winfo_height(), 120)
        pad_left, pad_right, pad_top, pad_bottom = 34, 12, 10, 22
        plot_w = width - pad_left - pad_right
        plot_h = height - pad_top - pad_bottom
        grid_color = "#E8EDF4"
        text_color = "#7A8594"
        for index in range(5):
            y = pad_top + index * plot_h / 4
            canvas.create_line(pad_left, y, width - pad_right, y, fill=grid_color)
        canvas.create_text(pad_left - 4, pad_top, anchor="e", text="max", fill=text_color, font=("Helvetica", 9))
        canvas.create_text(pad_left - 4, pad_top + plot_h, anchor="e", text="0", fill=text_color, font=("Helvetica", 9))
        view_start = self.view_start
        view_seconds = max(self.view_seconds, 1.0)
        view_end = view_start + view_seconds
        mid = view_start + view_seconds / 2
        canvas.create_text(pad_left, height - 4, anchor="w", text=self._format_time(view_start), fill=text_color, font=("Helvetica", 9))
        canvas.create_text(width / 2, height - 4, anchor="center", text=self._format_time(mid), fill=text_color, font=("Helvetica", 9))
        canvas.create_text(width - pad_right, height - 4, anchor="e", text=self._format_time(view_end), fill=text_color, font=("Helvetica", 9))
        visible_points = self._visible_points(view_start, view_end)
        if len(visible_points) < 2:
            canvas.create_text(
                width / 2,
                height / 2,
                text="等待实时数据",
                fill="#A0A8B4",
                font=("Helvetica", 12),
            )
            return
        values = [value for elapsed, value, _quality in visible_points if view_start <= elapsed <= view_end] or [value for _elapsed, value, _quality in visible_points]
        max_value = max(max(values), 1.0)
        if self.metric == "fps":
            max_value = max(max_value, 60.0)
        points: list[float] = []
        last_visible: tuple[float, float] | None = None
        quality_points: list[tuple[float, float, str]] = []
        interval_points: list[tuple[float, str]] = []
        for elapsed, value, quality in visible_points:
            x = pad_left + ((elapsed - view_start) / view_seconds) * plot_w
            y = pad_top + plot_h - min(value / max_value, 1.0) * plot_h
            points.extend([x, y])
            if view_start <= elapsed <= view_end:
                last_visible = (x, y)
                interval_points.append((elapsed, quality))
                if quality != "ok":
                    quality_points.append((x, y, quality))
        for interval in quality_intervals_from_points(interval_points):
            start = float(interval["start"])
            end = float(interval["end"])
            quality = str(interval["quality"])
            x1 = pad_left + ((max(start, view_start) - view_start) / view_seconds) * plot_w
            x2 = pad_left + ((min(end, view_end) - view_start) / view_seconds) * plot_w
            if x2 <= x1:
                x2 = min(width - pad_right, x1 + 3)
            fill = "#FEE2E2" if quality == "issue" else "#FEF3C7"
            canvas.create_rectangle(x1, pad_top, x2, pad_top + plot_h, fill=fill, outline="")
        shadow = points.copy()
        canvas.create_line(*shadow, fill="#DCEBFF", width=5, smooth=True)
        canvas.create_line(*points, fill=self.color, width=2.2, smooth=True)
        for x, y, quality in quality_points:
            if quality == "fallback":
                canvas.create_oval(x - 5, y - 5, x + 5, y + 5, outline="#F59E0B", width=2)
            elif quality == "issue":
                canvas.create_polygon(x, y - 6, x - 5.5, y + 5, x + 5.5, y + 5, fill="#EF4444", outline="#FFFFFF")
        last_x, last_y = last_visible or (points[-2], points[-1])
        canvas.create_oval(last_x - 4, last_y - 4, last_x + 4, last_y + 4, fill=self.color, outline="#FFFFFF", width=2)


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("1320x820")
        self.root.minsize(1120, 720)
        self.root.after(50, self._open_fullscreen_window)
        ensure_dirs()

        self.android = AndroidAdapter()
        self.ios = IOSAdapter()
        self.demo = DemoAdapter()
        self.adapters: dict[str, BaseAdapter] = {
            "Android": self.android,
            "iOS": self.ios,
            "Demo": self.demo,
        }
        self.devices: list[DeviceInfo] = []
        self.selected_device: DeviceInfo | None = None
        self.sampler: SamplerThread | None = None
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.recorder = SessionRecorder()
        self.last_notes: set[str] = set()
        self.last_export_folder: Path = EXPORT_DIR
        self.graph_last_elapsed = 0.0
        self.graph_view_start = 0.0
        self.graph_view_seconds = 10.0
        self.graph_follow_latest = True
        self.stabilizer = MetricStabilizer()
        self.health_analyzer = MetricHealthAnalyzer()
        self.live_quality = LiveQualityTracker()
        self.last_quality_event_tag = "ok"
        self.weak_proxy = WeakNetworkProxy(self._threadsafe_log)
        self.weak_registry = WeakProxyDeviceRegistry()

        self.platform_filter = tk.StringVar(value="All")
        self.app_var = tk.StringVar()
        self.interval_var = tk.StringVar(value="1.0")
        self.status_var = tk.StringVar(value="就绪")
        self.session_var = tk.StringVar(value="未开始")
        self.device_var = tk.StringVar(value="未选择设备")
        self.app_hint_var = tk.StringVar(value="选择设备后可刷新应用列表或读取前台应用。")
        self.capability_var = tk.StringVar(value="")
        self.marker_var = tk.StringVar(value="关键操作")
        self.quality_var = tk.StringVar(value="采集质量：等待数据")
        self.smoothing_var = tk.BooleanVar(value=True)
        self.weak_profile_var = tk.StringVar(value="弱网")
        self.weak_port_var = tk.StringVar(value="18888")
        self.weak_latency_var = tk.StringVar(value="300")
        self.weak_jitter_var = tk.StringVar(value="120")
        self.weak_loss_var = tk.StringVar(value="2")
        self.weak_down_var = tk.StringVar(value="512")
        self.weak_up_var = tk.StringVar(value="256")
        self.weak_status_var = tk.StringVar(value="弱网代理未启动")
        self.weak_diagnostic_summary_var = tk.StringVar(value="弱网代理未就绪")
        self.weak_diagnostic_row_vars: list[tuple[tk.StringVar, tk.StringVar, tk.StringVar]] = []
        self.weak_traffic_vars: dict[str, tk.StringVar] = {}
        self.metric_health_vars: dict[str, tk.StringVar] = {}

        self._configure_styles()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_devices()
        self.root.after(250, self._drain_events)
        self.root.after(1000, self._tick)

    def _open_fullscreen_window(self) -> None:
        try:
            self.root.state("zoomed")
            return
        except Exception:
            pass
        try:
            self.root.attributes("-zoomed", True)
            return
        except Exception:
            pass
        width = self.root.winfo_screenwidth()
        height = self.root.winfo_screenheight()
        self.root.geometry(f"{width}x{height}+0+0")

    def _threadsafe_log(self, text: str) -> None:
        self.events.put(("log", text))

    def _configure_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", font=("Helvetica", 12), background="#F4F7FB", foreground="#1A2533")
        style.configure("Root.TFrame", background="#F4F7FB")
        style.configure("Top.TFrame", background="#172235")
        style.configure("TopTitle.TLabel", background="#172235", foreground="#FFFFFF", font=("Helvetica", 18, "bold"))
        style.configure("TopSub.TLabel", background="#172235", foreground="#B5C4D8", font=("Helvetica", 11))
        style.configure("Sidebar.TFrame", background="#FFFFFF")
        style.configure("Panel.TFrame", background="#FFFFFF", relief="solid", borderwidth=1)
        style.configure("Card.TFrame", background="#FFFFFF", relief="solid", borderwidth=1)
        style.configure("PanelTitle.TLabel", background="#FFFFFF", foreground="#18212F", font=("Helvetica", 13, "bold"))
        style.configure("CardTitle.TLabel", background="#FFFFFF", foreground="#6A7482", font=("Helvetica", 10))
        style.configure("MetricValue.TLabel", background="#FFFFFF", foreground="#18212F", font=("Helvetica", 20, "bold"))
        style.configure("GraphValue.TLabel", background="#FFFFFF", foreground="#18212F", font=("Helvetica", 13, "bold"))
        style.configure("Muted.TLabel", background="#FFFFFF", foreground="#748091", font=("Helvetica", 10))
        style.configure("Health.TLabel", background="#FFFFFF", foreground="#243044", font=("Helvetica", 10, "bold"))
        style.configure("Quality.TLabel", background="#FFFFFF", foreground="#334155", font=("Helvetica", 11, "bold"))
        style.configure("SidebarTitle.TLabel", background="#FFFFFF", foreground="#18212F", font=("Helvetica", 13, "bold"))
        style.configure("Status.TLabel", background="#172235", foreground="#EAF2FF", font=("Helvetica", 11))
        style.configure("Primary.TButton", padding=(14, 8), font=("Helvetica", 12, "bold"))
        style.configure("Tool.TButton", padding=(10, 7), font=("Helvetica", 11))
        style.configure("Danger.TButton", padding=(14, 8), font=("Helvetica", 12, "bold"))
        style.configure("TEntry", padding=6)
        style.configure("TCombobox", padding=6)
        style.configure("Treeview", rowheight=28, fieldbackground="#FFFFFF", background="#FFFFFF")
        style.configure("Treeview.Heading", font=("Helvetica", 11, "bold"))

    def _build_ui(self) -> None:
        root_frame = ttk.Frame(self.root, style="Root.TFrame")
        root_frame.pack(fill="both", expand=True)
        self._build_header(root_frame)
        body = ttk.Frame(root_frame, style="Root.TFrame", padding=(14, 14, 14, 14))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, minsize=320, weight=0)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        self._build_sidebar(body)
        self.workspace_tabs = ttk.Notebook(body)
        self.workspace_tabs.grid(row=0, column=1, sticky="nsew")
        self.performance_tab = ttk.Frame(self.workspace_tabs, style="Root.TFrame")
        self.network_tab = ttk.Frame(self.workspace_tabs, style="Root.TFrame")
        self.workspace_tabs.add(self.performance_tab, text="性能采集")
        self.workspace_tabs.add(self.network_tab, text="弱网工具")
        self._build_dashboard(self.performance_tab)
        self._build_network_workspace(self.network_tab)

    def _build_header(self, master: tk.Widget) -> None:
        header = ttk.Frame(master, style="Top.TFrame", padding=(18, 14, 18, 14))
        header.pack(fill="x")
        title_group = ttk.Frame(header, style="Top.TFrame")
        title_group.pack(side="left")
        ttk.Label(title_group, text=APP_NAME, style="TopTitle.TLabel").pack(anchor="w")
        ttk.Label(title_group, text="移动端实时性能测试分析台", style="TopSub.TLabel").pack(anchor="w", pady=(2, 0))
        actions = ttk.Frame(header, style="Top.TFrame")
        actions.pack(side="right")
        ttk.Label(actions, textvariable=self.status_var, style="Status.TLabel").pack(side="left", padx=(0, 14))
        self.start_button = ttk.Button(actions, text="开始采集", style="Primary.TButton", command=self.start_sampling)
        self.start_button.pack(side="left", padx=4)
        self.stop_button = ttk.Button(actions, text="停止", style="Danger.TButton", command=self.stop_sampling, state="disabled")
        self.stop_button.pack(side="left", padx=4)
        ttk.Button(actions, text="导出报告", style="Tool.TButton", command=self.export_report).pack(side="left", padx=4)
        ttk.Button(actions, text="打开文件夹", style="Tool.TButton", command=self.open_export_folder).pack(side="left", padx=4)

    def _build_sidebar(self, master: tk.Widget) -> None:
        sidebar = ttk.Frame(master, style="Sidebar.TFrame", padding=(14, 14))
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        sidebar.rowconfigure(4, weight=1)
        ttk.Label(sidebar, text="设备", style="SidebarTitle.TLabel").grid(row=0, column=0, sticky="w")
        filter_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        filter_row.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        for label in ("All", "Android", "iOS", "Demo"):
            ttk.Radiobutton(
                filter_row,
                text=label,
                variable=self.platform_filter,
                value=label,
                command=self._render_devices,
            ).pack(side="left", padx=(0, 8))
        button_row = ttk.Frame(sidebar, style="Sidebar.TFrame")
        button_row.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(button_row, text="刷新设备", style="Tool.TButton", command=self.refresh_devices).pack(side="left")
        ttk.Button(button_row, text="演示模式", style="Tool.TButton", command=self.use_demo_devices).pack(side="left", padx=(8, 0))
        self.device_tree = ttk.Treeview(sidebar, columns=("platform", "status"), show="tree headings", height=8)
        self.device_tree.heading("#0", text="名称")
        self.device_tree.heading("platform", text="平台")
        self.device_tree.heading("status", text="状态")
        self.device_tree.column("#0", width=164, stretch=True)
        self.device_tree.column("platform", width=72, anchor="center")
        self.device_tree.column("status", width=70, anchor="center")
        self.device_tree.grid(row=3, column=0, sticky="ew")
        self.device_tree.bind("<<TreeviewSelect>>", self._on_device_selected)
        app_panel = ttk.Frame(sidebar, style="Sidebar.TFrame")
        app_panel.grid(row=4, column=0, sticky="nsew", pady=(16, 0))
        app_panel.columnconfigure(0, weight=1)
        ttk.Label(app_panel, text="目标应用", style="SidebarTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(app_panel, textvariable=self.app_var).grid(row=1, column=0, sticky="ew", pady=(10, 8))
        app_actions = ttk.Frame(app_panel, style="Sidebar.TFrame")
        app_actions.grid(row=2, column=0, sticky="ew")
        ttk.Button(app_actions, text="前台应用", style="Tool.TButton", command=self.detect_foreground_app).pack(side="left")
        ttk.Button(app_actions, text="应用列表", style="Tool.TButton", command=self.refresh_apps).pack(side="left", padx=(8, 0))
        self.app_list = tk.Listbox(
            app_panel,
            height=8,
            borderwidth=1,
            highlightthickness=0,
            activestyle="none",
            font=("Helvetica", 11),
            bg="#FFFFFF",
            fg="#18212F",
            selectbackground="#DCEBFF",
            selectforeground="#0A4E92",
        )
        self.app_list.grid(row=3, column=0, sticky="nsew", pady=(10, 8))
        self.app_list.bind("<<ListboxSelect>>", self._on_app_selected)
        ttk.Label(app_panel, textvariable=self.app_hint_var, style="Muted.TLabel", wraplength=280).grid(row=4, column=0, sticky="ew")
        settings = ttk.Frame(sidebar, style="Sidebar.TFrame")
        settings.grid(row=5, column=0, sticky="ew", pady=(16, 0))
        settings.columnconfigure(1, weight=1)
        ttk.Label(settings, text="采样间隔", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        interval = ttk.Combobox(settings, textvariable=self.interval_var, values=("0.5", "1.0", "2.0"), width=6, state="readonly")
        interval.grid(row=0, column=1, sticky="e")
        ttk.Checkbutton(settings, text="稳定曲线", variable=self.smoothing_var).grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(10, 0),
        )
        ttk.Button(settings, text="iOS采集服务", style="Tool.TButton", command=self.start_ios_service).grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(12, 0),
        )
        ttk.Label(settings, textvariable=self.capability_var, style="Muted.TLabel", wraplength=280).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))

    def _build_weak_network_panel(self, master: tk.Widget, row: int) -> None:
        panel = ttk.Frame(master, style="Panel.TFrame", padding=(10, 10))
        panel.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        panel.columnconfigure(1, weight=1)
        ttk.Label(panel, text="弱网工具", style="PanelTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        profile = ttk.Combobox(
            panel,
            textvariable=self.weak_profile_var,
            values=tuple(WEAK_NETWORK_PROFILES),
            state="readonly",
            width=10,
        )
        profile.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 6))
        profile.bind("<<ComboboxSelected>>", lambda _event: self.apply_weak_profile())
        fields = [
            ("端口", self.weak_port_var),
            ("延迟 ms", self.weak_latency_var),
            ("抖动 ms", self.weak_jitter_var),
            ("丢包 %", self.weak_loss_var),
            ("下行 KB/s", self.weak_down_var),
            ("上行 KB/s", self.weak_up_var),
        ]
        for index, (label, variable) in enumerate(fields, start=2):
            ttk.Label(panel, text=label, style="Muted.TLabel").grid(row=index, column=0, sticky="w", pady=(4, 0))
            ttk.Entry(panel, textvariable=variable, width=10).grid(row=index, column=1, sticky="ew", pady=(4, 0))
        actions = ttk.Frame(panel, style="Panel.TFrame")
        actions.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="启动代理", style="Tool.TButton", command=self.start_weak_proxy).pack(side="left")
        ttk.Button(actions, text="停止", style="Tool.TButton", command=self.stop_weak_proxy).pack(side="left", padx=(8, 0))
        device_actions = ttk.Frame(panel, style="Panel.TFrame")
        device_actions.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(device_actions, text="应用到Android", style="Tool.TButton", command=self.apply_android_proxy).pack(side="left")
        ttk.Button(device_actions, text="清除代理", style="Tool.TButton", command=self.clear_android_proxy).pack(side="left", padx=(8, 0))
        ttk.Label(panel, textvariable=self.weak_status_var, style="Muted.TLabel", wraplength=250).grid(
            row=10,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(8, 0),
        )

    def _build_network_workspace(self, master: tk.Widget) -> None:
        master.columnconfigure(0, weight=1)
        master.rowconfigure(1, weight=1)
        hero = ttk.Frame(master, style="Panel.TFrame", padding=(16, 14))
        hero.grid(row=0, column=0, sticky="ew")
        ttk.Label(hero, text="弱网工具", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            hero,
            text="通过本机 HTTP/HTTPS 代理模拟延迟、抖动、丢包和上下行限速；Android 可一键写入或清除系统代理。",
            style="Muted.TLabel",
            wraplength=900,
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(hero, textvariable=self.weak_status_var, style="GraphValue.TLabel").grid(row=0, column=1, sticky="e", padx=(16, 0))
        hero.columnconfigure(0, weight=1)

        content = ttk.Frame(master, style="Root.TFrame")
        content.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        content.columnconfigure(0, weight=0, minsize=360)
        content.columnconfigure(1, weight=1)
        content.rowconfigure(0, weight=1)

        controls = ttk.Frame(content, style="Panel.TFrame", padding=(16, 14))
        controls.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text="网络预设", style="PanelTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        profile = ttk.Combobox(
            controls,
            textvariable=self.weak_profile_var,
            values=tuple(WEAK_NETWORK_PROFILES),
            state="readonly",
        )
        profile.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 14))
        profile.bind("<<ComboboxSelected>>", lambda _event: self.apply_weak_profile())
        fields = [
            ("代理端口", self.weak_port_var, "本机监听端口"),
            ("基础延迟 ms", self.weak_latency_var, "请求和响应都会生效"),
            ("随机抖动 ms", self.weak_jitter_var, "每个数据块增加随机延迟"),
            ("连接丢弃 %", self.weak_loss_var, "模拟请求失败"),
            ("下行 KB/s", self.weak_down_var, "0 表示不限速"),
            ("上行 KB/s", self.weak_up_var, "0 表示不限速"),
        ]
        for index, (label, variable, hint) in enumerate(fields, start=2):
            ttk.Label(controls, text=label, style="Muted.TLabel").grid(row=index, column=0, sticky="w", pady=(7, 0))
            ttk.Entry(controls, textvariable=variable, width=12).grid(row=index, column=1, sticky="ew", pady=(7, 0))
            ttk.Label(controls, text=hint, style="Muted.TLabel").grid(row=index, column=2, sticky="w", padx=(8, 0), pady=(7, 0))
        actions = ttk.Frame(controls, style="Panel.TFrame")
        actions.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(18, 0))
        ttk.Button(actions, text="启动代理", style="Primary.TButton", command=self.start_weak_proxy).pack(side="left")
        ttk.Button(actions, text="停止代理", style="Tool.TButton", command=self.stop_weak_proxy).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="应用到 Android", style="Tool.TButton", command=self.apply_android_proxy).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="清除 Android 代理", style="Tool.TButton", command=self.clear_android_proxy).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="刷新状态", style="Tool.TButton", command=self.refresh_android_proxy_status).pack(side="left", padx=(8, 0))

        guide = ttk.Frame(content, style="Panel.TFrame", padding=(16, 14))
        guide.grid(row=0, column=1, sticky="nsew")
        guide.columnconfigure(0, weight=1)
        ttk.Label(guide, text="链路诊断", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(guide, textvariable=self.weak_diagnostic_summary_var, style="GraphValue.TLabel").grid(
            row=1,
            column=0,
            sticky="w",
            pady=(6, 0),
        )
        self._build_weak_diagnostic_rows(guide, row=2)
        self._build_proxy_traffic_panel(guide, row=3)
        ttk.Label(guide, text="使用流程", style="PanelTitle.TLabel").grid(row=4, column=0, sticky="w", pady=(18, 0))
        text = (
            "1. 选择一台 Android 设备，并确认手机和电脑在同一网络。\n"
            "2. 选择弱网预设或手动配置参数。\n"
            "3. 点击“启动代理”，再点击“应用到 Android”。\n"
            "4. 在 App 内执行目标场景，同时回到“性能采集”观察 FPS、CPU、网络曲线。\n"
            "5. 测试结束务必点击“清除 Android 代理”。\n\n"
            "覆盖范围：当前为系统 HTTP/HTTPS 代理模式，不需要 Root。对 UDP、QUIC、私有代理栈或主动绕过系统代理的 App，后续需要 VPN/tun 模式。"
        )
        tk.Message(
            guide,
            text=text,
            width=760,
            bg="#FFFFFF",
            fg="#243044",
            font=("Helvetica", 12),
            borderwidth=0,
            justify="left",
        ).grid(row=5, column=0, sticky="new", pady=(12, 0))
        self.proxy_preview_text = tk.Text(
            guide,
            height=8,
            wrap="word",
            borderwidth=1,
            highlightthickness=0,
            bg="#F8FAFC",
            fg="#243044",
            font=("Menlo", 11),
        )
        self.proxy_preview_text.grid(row=6, column=0, sticky="ew", pady=(16, 0))
        self.proxy_preview_text.insert(
            "1.0",
            "代理地址将在启动后显示。\nAndroid 写入命令示例：settings put global http_proxy <host>:<port>\n清理命令：settings put global http_proxy :0",
        )
        self.proxy_preview_text.configure(state="disabled")
        self._refresh_weak_diagnostics()
        self._refresh_proxy_traffic()

    def _build_weak_diagnostic_rows(self, master: tk.Widget, row: int) -> None:
        table = ttk.Frame(master, style="Panel.TFrame")
        table.grid(row=row, column=0, sticky="ew", pady=(10, 0))
        table.columnconfigure(2, weight=1)
        self.weak_diagnostic_row_vars = []
        for index in range(4):
            name_var = tk.StringVar(value="-")
            state_var = tk.StringVar(value="-")
            detail_var = tk.StringVar(value="-")
            self.weak_diagnostic_row_vars.append((name_var, state_var, detail_var))
            ttk.Label(table, textvariable=name_var, style="Muted.TLabel").grid(row=index, column=0, sticky="w", pady=(4, 0))
            ttk.Label(table, textvariable=state_var, style="Quality.TLabel").grid(row=index, column=1, sticky="w", padx=(14, 0), pady=(4, 0))
            ttk.Label(table, textvariable=detail_var, style="Muted.TLabel", wraplength=520).grid(
                row=index,
                column=2,
                sticky="ew",
                padx=(14, 0),
                pady=(4, 0),
            )

    def _build_proxy_traffic_panel(self, master: tk.Widget, row: int) -> None:
        panel = ttk.Frame(master, style="Panel.TFrame", padding=(12, 10))
        panel.grid(row=row, column=0, sticky="ew", pady=(18, 0))
        ttk.Label(panel, text="代理真实流量", style="PanelTitle.TLabel").grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(
            panel,
            text="统计所有经过本机弱网代理的 HTTP/HTTPS 流量，用于验证弱网是否真实生效。",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))
        metrics = [
            ("down_rate", "实时下行", "0.0 KB/s"),
            ("up_rate", "实时上行", "0.0 KB/s"),
            ("down_total", "累计下行", "0 B"),
            ("up_total", "累计上行", "0 B"),
            ("connections", "连接", "0 活跃 / 0 总计"),
            ("drops", "丢弃", "0"),
            ("activity", "最近活动", "无"),
        ]
        for index, (key, label, default) in enumerate(metrics, start=1):
            value_var = tk.StringVar(value=default)
            self.weak_traffic_vars[key] = value_var
            item = ttk.Frame(panel, style="Panel.TFrame", padding=(10, 8))
            item.grid(row=2 + (index - 1) // 4, column=(index - 1) % 4, sticky="ew", padx=(0 if (index - 1) % 4 == 0 else 8, 0), pady=(10, 0))
            ttk.Label(item, text=label, style="Muted.TLabel").pack(anchor="w")
            ttk.Label(item, textvariable=value_var, style="GraphValue.TLabel").pack(anchor="w", pady=(3, 0))
        for column in range(4):
            panel.columnconfigure(column, weight=1)

    def _build_dashboard(self, master: tk.Widget) -> None:
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        main = ttk.Frame(master, style="Root.TFrame")
        main.grid(row=0, column=0, sticky="nsew")
        main.columnconfigure(0, weight=1)
        main.rowconfigure(5, weight=1)
        target = ttk.Frame(main, style="Panel.TFrame", padding=(14, 12))
        target.grid(row=0, column=0, sticky="ew")
        ttk.Label(target, textvariable=self.device_var, style="PanelTitle.TLabel").pack(side="left")
        ttk.Label(target, textvariable=self.session_var, style="Muted.TLabel").pack(side="right")

        quality = ttk.Frame(main, style="Panel.TFrame", padding=(12, 9))
        quality.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(quality, textvariable=self.quality_var, style="Quality.TLabel").pack(side="left")

        cards = ttk.Frame(main, style="Root.TFrame")
        cards.grid(row=2, column=0, sticky="ew", pady=(12, 12))
        for col in range(4):
            cards.columnconfigure(col, weight=1)
        self.cards: dict[str, MetricCard] = {
            "fps": MetricCard(cards, "FPS", "", "#1F8FFF"),
            "jank_percent": MetricCard(cards, "Jank", "%", "#E8590C"),
            "cpu_percent": MetricCard(cards, "CPU", "%", "#FF8A34"),
            "memory_mb": MetricCard(cards, "Memory", "MB", "#4F46E5"),
            "temperature_c": MetricCard(cards, "Temp", "C", "#EF4444"),
            "power_w": MetricCard(cards, "Power", "W", "#0E9F6E"),
            "rx_kbps": MetricCard(cards, "Down", "KB/s", "#16A34A"),
            "tx_kbps": MetricCard(cards, "Up", "KB/s", "#0D9488"),
        }
        for index, card in enumerate(self.cards.values()):
            row = index // 4
            col = index % 4
            card.grid(
                row=row,
                column=col,
                sticky="ew",
                padx=(0 if col == 0 else 8, 0),
                pady=(0 if row == 0 else 8, 0),
            )

        self._build_metric_health_strip(main, row=3)

        self.graph_panel_row_height = 194
        self.graph_row_gap = 10
        self.graph_row_scroll_pixels = self.graph_panel_row_height + self.graph_row_gap
        graph_view_height = self.graph_panel_row_height * 2 + self.graph_row_gap + 22
        graph_view = ttk.Frame(main, style="Root.TFrame", height=graph_view_height)
        graph_view.grid(row=4, column=0, sticky="ew")
        graph_view.grid_propagate(False)
        graph_view.columnconfigure(0, weight=1)
        graph_view.rowconfigure(0, weight=1)
        self.graph_canvas = tk.Canvas(
            graph_view,
            background="#F4F7FB",
            borderwidth=0,
            highlightthickness=0,
            yscrollincrement=self.graph_row_scroll_pixels,
        )
        graph_scroll = ttk.Scrollbar(graph_view, orient="vertical", command=self.graph_canvas.yview)
        self.graph_canvas.configure(yscrollcommand=graph_scroll.set)
        self.graph_canvas.grid(row=0, column=0, sticky="nsew")
        graph_scroll.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.graph_time_scroll = ttk.Scrollbar(graph_view, orient="horizontal", command=self._on_graph_time_scroll)
        self.graph_time_scroll.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.graph_time_scroll.state(["disabled"])
        graphs = ttk.Frame(self.graph_canvas, style="Root.TFrame")
        self.graph_window_id = self.graph_canvas.create_window((0, 0), window=graphs, anchor="nw")
        self.graph_canvas.bind("<Configure>", self._resize_graph_scroll_window)
        graphs.bind("<Configure>", self._refresh_graph_scroll_region)
        graphs.columnconfigure(0, weight=1)
        graphs.columnconfigure(1, weight=1)
        for row in range(4):
            graphs.rowconfigure(row, minsize=self.graph_panel_row_height, weight=0)
        self.graphs: dict[str, GraphPanel] = {
            "fps": GraphPanel(graphs, "帧率", "fps", "FPS", "#1F8FFF"),
            "jank_percent": GraphPanel(graphs, "Jank", "jank_percent", "%", "#E8590C"),
            "cpu_percent": GraphPanel(graphs, "CPU 占用", "cpu_percent", "%", "#FF8A34"),
            "memory_mb": GraphPanel(graphs, "内存", "memory_mb", "MB", "#4F46E5"),
            "temperature_c": GraphPanel(graphs, "温度", "temperature_c", "C", "#EF4444"),
            "power_w": GraphPanel(graphs, "功耗", "power_w", "W", "#0E9F6E"),
            "rx_kbps": GraphPanel(graphs, "网络下行", "rx_kbps", "KB/s", "#16A34A"),
            "tx_kbps": GraphPanel(graphs, "网络上行", "tx_kbps", "KB/s", "#0D9488"),
        }
        positions = [
            ("fps", 0, 0),
            ("jank_percent", 0, 1),
            ("cpu_percent", 1, 0),
            ("memory_mb", 1, 1),
            ("temperature_c", 2, 0),
            ("power_w", 2, 1),
            ("rx_kbps", 3, 0),
            ("tx_kbps", 3, 1),
        ]
        for key, row, col in positions:
            self.graphs[key].grid(row=row, column=col, sticky="nsew", padx=(0 if col == 0 else 10, 0), pady=(0 if row == 0 else 10, 0))
        self._bind_graph_mousewheel(graph_view)

        bottom = ttk.Frame(main, style="Root.TFrame")
        bottom.grid(row=5, column=0, sticky="nsew", pady=(12, 0))
        bottom.columnconfigure(1, weight=1)
        bottom.columnconfigure(2, weight=1)
        bottom.rowconfigure(0, weight=1)
        marker_panel = ttk.Frame(bottom, style="Panel.TFrame", padding=(12, 10))
        marker_panel.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        ttk.Label(marker_panel, text="事件标记", style="PanelTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Entry(marker_panel, textvariable=self.marker_var, width=18).grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Button(marker_panel, text="添加", style="Tool.TButton", command=self.add_marker).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
        ttk.Button(marker_panel, text="截图", style="Tool.TButton", command=self.capture_screenshot).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        quality_event_panel = ttk.Frame(bottom, style="Panel.TFrame", padding=(12, 10))
        quality_event_panel.grid(row=0, column=1, sticky="nsew", padx=(0, 12))
        ttk.Label(quality_event_panel, text="质量事件", style="PanelTitle.TLabel").pack(anchor="w")
        self.quality_event_tree = ttk.Treeview(
            quality_event_panel,
            columns=("time", "kind", "detail"),
            show="headings",
            height=5,
        )
        self.quality_event_tree.heading("time", text="时间")
        self.quality_event_tree.heading("kind", text="类型")
        self.quality_event_tree.heading("detail", text="说明")
        self.quality_event_tree.column("time", width=70, anchor="center", stretch=False)
        self.quality_event_tree.column("kind", width=96, anchor="center", stretch=False)
        self.quality_event_tree.column("detail", width=280, stretch=True)
        self.quality_event_tree.pack(fill="both", expand=True, pady=(8, 0))
        log_panel = ttk.Frame(bottom, style="Panel.TFrame", padding=(12, 10))
        log_panel.grid(row=0, column=2, sticky="nsew")
        ttk.Label(log_panel, text="日志", style="PanelTitle.TLabel").pack(anchor="w")
        self.log_text = tk.Text(
            log_panel,
            height=6,
            wrap="word",
            borderwidth=0,
            highlightthickness=0,
            bg="#FFFFFF",
            fg="#243044",
            font=("Menlo", 11),
        )
        self.log_text.pack(fill="both", expand=True, pady=(8, 0))
        self.log_text.configure(state="disabled")

    def _build_metric_health_strip(self, master: tk.Widget, row: int) -> None:
        panel = ttk.Frame(master, style="Panel.TFrame", padding=(12, 10))
        panel.grid(row=row, column=0, sticky="ew", pady=(0, 12))
        panel.columnconfigure(1, weight=1)
        ttk.Label(panel, text="采集健康", style="PanelTitle.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 12))
        grid = ttk.Frame(panel, style="Panel.TFrame")
        grid.grid(row=0, column=1, sticky="ew")
        labels = [
            ("fps", "FPS"),
            ("jank_percent", "Jank"),
            ("cpu_percent", "CPU"),
            ("memory_mb", "内存"),
            ("battery_percent", "电量"),
            ("temperature_c", "温度"),
            ("power_w", "Power"),
            ("rx_kbps", "下行"),
            ("tx_kbps", "上行"),
        ]
        for col, (metric, label) in enumerate(labels):
            grid.columnconfigure(col, weight=1)
            variable = tk.StringVar(value=f"{label}: 等待")
            self.metric_health_vars[metric] = variable
            ttk.Label(grid, textvariable=variable, style="Health.TLabel").grid(row=0, column=col, sticky="w", padx=(0 if col == 0 else 10, 0))

    def _resize_graph_scroll_window(self, event: tk.Event) -> None:
        if hasattr(self, "graph_canvas") and hasattr(self, "graph_window_id"):
            self.graph_canvas.itemconfigure(self.graph_window_id, width=event.width)
            self._refresh_graph_scroll_region()

    def _refresh_graph_scroll_region(self, _event: tk.Event | None = None) -> None:
        if hasattr(self, "graph_canvas"):
            self.graph_canvas.configure(scrollregion=self.graph_canvas.bbox("all"))

    def _graph_timeline_seconds(self) -> float:
        return max(self.graph_last_elapsed, 10.0)

    def _graph_view_duration(self) -> float:
        return min(self._graph_timeline_seconds(), float(CHART_VIEW_SECONDS))

    def _graph_max_view_start(self) -> float:
        return max(self._graph_timeline_seconds() - self._graph_view_duration(), 0.0)

    def _refresh_graph_time_axis(self) -> None:
        self.graph_view_seconds = self._graph_view_duration()
        max_start = self._graph_max_view_start()
        if self.graph_follow_latest:
            self.graph_view_start = max_start
        else:
            self.graph_view_start = min(max(self.graph_view_start, 0.0), max_start)

        if hasattr(self, "graph_time_scroll"):
            timeline = self._graph_timeline_seconds()
            if max_start <= 0:
                self.graph_time_scroll.set(0.0, 1.0)
                self.graph_time_scroll.state(["disabled"])
            else:
                first = min(max(self.graph_view_start / timeline, 0.0), 1.0)
                last = min(max((self.graph_view_start + self.graph_view_seconds) / timeline, first), 1.0)
                self.graph_time_scroll.state(["!disabled"])
                self.graph_time_scroll.set(first, last)

        if hasattr(self, "graphs"):
            for graph in self.graphs.values():
                graph.set_view(self.graph_view_start, self.graph_view_seconds)

    def _on_graph_time_scroll(self, *args: str) -> None:
        max_start = self._graph_max_view_start()
        if not args or max_start <= 0:
            return
        action = args[0]
        timeline = self._graph_timeline_seconds()
        if action == "moveto" and len(args) >= 2:
            self.graph_view_start = float(args[1]) * timeline
        elif action == "scroll" and len(args) >= 3:
            amount = int(args[1])
            step = self.graph_view_seconds * 0.8 if args[2] == "pages" else max(self.graph_view_seconds / 20, 1.0)
            self.graph_view_start += amount * step
        self.graph_view_start = min(max(self.graph_view_start, 0.0), max_start)
        self.graph_follow_latest = self.graph_view_start >= max_start - 1.0
        self._refresh_graph_time_axis()

    def _bind_graph_mousewheel(self, widget: tk.Widget) -> None:
        widget.bind("<MouseWheel>", self._on_graph_mousewheel)
        widget.bind("<Button-4>", self._on_graph_mousewheel)
        widget.bind("<Button-5>", self._on_graph_mousewheel)
        for child in widget.winfo_children():
            self._bind_graph_mousewheel(child)

    def _on_graph_mousewheel(self, event: tk.Event) -> str:
        if not hasattr(self, "graph_canvas"):
            return "break"
        if getattr(event, "state", 0) & 0x0001:
            if getattr(event, "num", None) == 4:
                units = -1
            elif getattr(event, "num", None) == 5:
                units = 1
            else:
                delta = int(getattr(event, "delta", 0) or 0)
                units = -1 if delta > 0 else 1 if delta < 0 else 0
            if units:
                self._on_graph_time_scroll("scroll", str(units), "units")
            return "break"
        view_start, view_end = self.graph_canvas.yview()
        if view_start <= 0.0 and view_end >= 1.0:
            return "break"
        if getattr(event, "num", None) == 4:
            units = -1
        elif getattr(event, "num", None) == 5:
            units = 1
        else:
            delta = int(getattr(event, "delta", 0) or 0)
            units = -1 if delta > 0 else 1 if delta < 0 else 0
        if units:
            self.graph_canvas.yview_scroll(units, "units")
        return "break"

    def adapter_for(self, device: DeviceInfo | None) -> BaseAdapter | None:
        if not device:
            return None
        if device.detail == "演示数据" or device.serial.startswith("demo-"):
            return self.demo
        return self.adapters.get(device.platform)

    def refresh_devices(self) -> None:
        self.status_var.set("正在刷新设备...")
        self.devices = []
        for adapter in (self.android, self.ios):
            try:
                self.devices.extend(adapter.list_devices())
            except Exception as exc:
                self.recorder.log(f"{adapter.platform_name} 设备刷新失败：{exc}")
        self._render_devices()
        self.capability_var.set(self._capability_text())
        if not self.devices:
            self.status_var.set("未检测到真机，可使用演示模式预览。")
        else:
            self.status_var.set(f"检测到 {len(self.devices)} 台设备")

    def use_demo_devices(self) -> None:
        self.devices = self.demo.list_devices()
        self.platform_filter.set("Demo")
        self._render_devices()
        self.status_var.set("演示模式已启用")
        self.capability_var.set("演示模式只用于预览界面与报告流程，不代表真实设备数据。")

    def _capability_text(self) -> str:
        return "\n".join([self.android.capability_note(), self.ios.capability_note()])

    def apply_weak_profile(self) -> None:
        profile = WEAK_NETWORK_PROFILES.get(self.weak_profile_var.get())
        if not profile:
            return
        latency, jitter, loss, down, up = profile
        self.weak_latency_var.set(str(latency))
        self.weak_jitter_var.set(str(jitter))
        self.weak_loss_var.set(str(loss).rstrip("0").rstrip("."))
        self.weak_down_var.set(f"{down:g}")
        self.weak_up_var.set(f"{up:g}")

    def _weak_config_values(self) -> tuple[int, int, int, float, float, float] | None:
        try:
            return (
                int(float(self.weak_port_var.get())),
                int(float(self.weak_latency_var.get())),
                int(float(self.weak_jitter_var.get())),
                float(self.weak_loss_var.get()),
                float(self.weak_down_var.get()),
                float(self.weak_up_var.get()),
            )
        except ValueError:
            messagebox.showwarning(APP_NAME, "弱网参数必须是数字。")
            return None

    def start_weak_proxy(self) -> None:
        values = self._weak_config_values()
        if values is None:
            return
        port, latency, jitter, loss, down, up = values
        try:
            restart = self.weak_proxy.is_running() and port != self.weak_proxy.port
            if restart:
                self.weak_proxy.stop()
            self.weak_proxy.configure(port, latency, jitter, loss, down, up)
            self.weak_proxy.reset_traffic()
            self.weak_proxy.start()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"启动弱网代理失败：{exc}")
            return
        self.weak_status_var.set(f"代理运行中：{self.weak_proxy.local_endpoint()}")
        self._refresh_proxy_preview()
        self._refresh_weak_diagnostics()
        self._refresh_proxy_traffic()
        self.append_log(
            f"弱网配置：延迟 {latency}ms，抖动 {jitter}ms，丢包 {loss:g}%，"
            f"下行 {down:g}KB/s，上行 {up:g}KB/s。"
        )

    def stop_weak_proxy(self) -> None:
        self.weak_proxy.stop()
        self.weak_status_var.set("弱网代理未启动")
        self._refresh_proxy_preview()
        self._refresh_weak_diagnostics()
        self._refresh_proxy_traffic()

    def _refresh_proxy_preview(self) -> None:
        if not hasattr(self, "proxy_preview_text"):
            return
        endpoint = self.weak_proxy.local_endpoint() if self.weak_proxy.is_running() else "<host>:<port>"
        text = (
            f"当前代理地址：{endpoint}\n"
            f"Android 写入命令：settings put global http_proxy {endpoint}\n"
            "Android 清理命令：settings put global http_proxy :0\n\n"
            "提示：应用弱网前请确认 Android 设备能访问电脑所在局域网 IP。"
        )
        self.proxy_preview_text.configure(state="normal")
        self.proxy_preview_text.delete("1.0", tk.END)
        self.proxy_preview_text.insert("1.0", text)
        self.proxy_preview_text.configure(state="disabled")

    def _refresh_weak_diagnostics(
        self,
        current_proxy: str | None = None,
        probe_connectivity: bool = False,
    ) -> None:
        if not hasattr(self, "weak_diagnostic_summary_var"):
            return
        device = self.selected_device if self.selected_device and self.selected_device.platform == "Android" else None
        endpoint = self.weak_proxy.local_endpoint()
        if current_proxy is None and device:
            current_proxy = self.android.current_http_proxy(device)
        proxy_reachable: bool | None = None
        if probe_connectivity and device and self.weak_proxy.is_running():
            host, port_text = endpoint.rsplit(":", 1)
            try:
                proxy_reachable, detail = self.android.probe_tcp_connectivity(device, host, int(port_text))
            except Exception as exc:
                proxy_reachable = False
                detail = str(exc)
            self.append_log(
                f"Android 到弱网代理端口{'可达' if proxy_reachable else '不可达'}：{detail}"
            )
        diagnostics = build_weak_network_diagnostics(
            proxy_running=self.weak_proxy.is_running(),
            endpoint=endpoint,
            device=device,
            current_proxy=current_proxy or "",
            proxy_reachable=proxy_reachable,
        )
        self.weak_diagnostic_summary_var.set(diagnostics.summary)
        for index, variables in enumerate(self.weak_diagnostic_row_vars):
            name_var, state_var, detail_var = variables
            if index < len(diagnostics.rows):
                name, state, detail = diagnostics.rows[index]
                name_var.set(name)
                state_var.set(state)
                detail_var.set(detail)
            else:
                name_var.set("-")
                state_var.set("-")
                detail_var.set("-")

    def _refresh_proxy_traffic(self) -> None:
        if not self.weak_traffic_vars:
            return
        values = format_proxy_traffic_snapshot(self.weak_proxy.traffic_snapshot())
        for key, text in values.items():
            variable = self.weak_traffic_vars.get(key)
            if variable:
                variable.set(text)

    def _selected_android_device(self) -> DeviceInfo | None:
        device = self.selected_device
        if not device or device.platform != "Android":
            messagebox.showinfo(APP_NAME, "请先选择 Android 设备。")
            return None
        return device

    def apply_android_proxy(self) -> None:
        device = self._selected_android_device()
        if not device:
            return
        if not self.weak_proxy.is_running():
            self.start_weak_proxy()
        host, port_text = self.weak_proxy.local_endpoint().rsplit(":", 1)
        ok, detail = self.android.set_http_proxy(device, host, int(port_text))
        if ok:
            expected_proxy = detail or f"{host}:{port_text}"
            current_proxy = self.android.current_http_proxy(device)
            verification = verify_android_proxy_state(expected_proxy, current_proxy)
            self.append_log(verification.log_text)
            if verification.confirmed:
                self.append_log(f"已给 Android 设备设置弱网代理：{expected_proxy}")
                self.weak_registry.mark_applied(device, expected_proxy)
            else:
                self.weak_registry.mark_cleared(device)
                messagebox.showwarning(APP_NAME, verification.status_text)
            self.weak_status_var.set(f"{device.name}：{verification.status_text}")
            self._refresh_weak_diagnostics(current_proxy, probe_connectivity=verification.confirmed)
            if hasattr(self, "workspace_tabs"):
                self.workspace_tabs.select(self.network_tab)
        else:
            messagebox.showerror(APP_NAME, f"设置 Android 代理失败：{detail}")
            self._refresh_weak_diagnostics()

    def clear_android_proxy(self) -> None:
        device = self._selected_android_device()
        if not device:
            return
        ok, detail = self.android.clear_http_proxy(device)
        if ok:
            self.append_log(f"已清除 Android 设备代理：{device.name}")
            self.weak_registry.mark_cleared(device)
            self.weak_status_var.set("已清除 Android 代理")
            self._refresh_weak_diagnostics("")
        else:
            messagebox.showwarning(APP_NAME, f"清除 Android 代理可能未完全成功：{detail}")
            self._refresh_weak_diagnostics()

    def refresh_android_proxy_status(self) -> None:
        device = self._selected_android_device()
        if not device:
            return
        raw_proxy = self.android.current_http_proxy(device)
        proxy = normalize_android_proxy_value(raw_proxy)
        if proxy:
            self.weak_status_var.set(f"{device.name} 当前代理：{proxy}")
            self.append_log(f"Android 当前代理：{proxy}")
        else:
            self.weak_status_var.set(f"{device.name} 当前未设置系统代理")
            self.append_log("Android 当前未设置系统代理。")
        self._refresh_weak_diagnostics(raw_proxy, probe_connectivity=bool(proxy))

    def _cleanup_weak_proxy_devices(self) -> None:
        cleared = self.weak_registry.cleanup(self.android)
        if cleared:
            self.append_log(f"退出前已清理 Android 代理：{', '.join(cleared)}")

    def on_close(self) -> None:
        try:
            if self.sampler:
                self.sampler.stop()
                self.sampler = None
        except Exception:
            pass
        try:
            self._cleanup_weak_proxy_devices()
        except Exception as exc:
            self.append_log(f"退出前清理 Android 代理失败：{exc}")
        try:
            self.weak_proxy.stop()
        except Exception:
            pass
        self.root.destroy()

    def start_ios_service(self) -> None:
        script = BASE_DIR / "启动iOS采集服务.command"
        if not script.exists():
            messagebox.showwarning(APP_NAME, f"未找到脚本：{script}")
            return
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(script)])
            else:
                subprocess.Popen([str(script)])
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"启动 iOS 采集服务失败：{exc}")
            return
        self.append_log("已打开 iOS 采集服务窗口，请输入本机密码并保持窗口打开。")

    def _render_devices(self) -> None:
        for item in self.device_tree.get_children():
            self.device_tree.delete(item)
        selected_filter = self.platform_filter.get()
        for index, device in enumerate(self.devices):
            if selected_filter not in ("All", "Demo") and device.platform != selected_filter:
                continue
            if selected_filter == "Demo" and device.detail != "演示数据":
                continue
            self.device_tree.insert("", "end", iid=str(index), text=device.name, values=(device.platform, device.status))

    def _on_device_selected(self, _event: tk.Event | None = None) -> None:
        selection = self.device_tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if index >= len(self.devices):
            return
        self.selected_device = self.devices[index]
        device = self.selected_device
        self.device_var.set(f"{device.display_name} · OS {device.os_version or '-'} · {device.serial}")
        self.app_hint_var.set("可直接输入包名/Bundle ID，或点击读取前台应用。")
        self.status_var.set(f"已选择 {device.display_name}")
        self.app_list.delete(0, tk.END)
        if device.platform == "iOS":
            if device.status != "ready":
                self.app_hint_var.set("该 iOS 设备当前离线或不可连接，请解锁设备、信任电脑并确认 USB/网络连接。")
            else:
                self.app_hint_var.set("iOS 请填写 Bundle ID；电量/温度可直接采集，CPU/内存需要启动 iOS 采集服务。")
        self._refresh_weak_diagnostics()

    def _on_app_selected(self, _event: tk.Event | None = None) -> None:
        selection = self.app_list.curselection()
        if selection:
            raw = self.app_list.get(selection[0])
            self.app_var.set(raw.split()[0] if raw.split() else raw)

    def refresh_apps(self) -> None:
        device = self.selected_device
        adapter = self.adapter_for(device)
        if not device or not adapter:
            messagebox.showinfo(APP_NAME, "请先选择设备。")
            return
        self.app_hint_var.set("正在读取应用列表...")
        self.root.update_idletasks()
        try:
            apps = adapter.list_apps(device)
        except Exception as exc:
            self.app_hint_var.set(f"读取失败：{exc}")
            return
        self.app_list.delete(0, tk.END)
        for app_id in apps[:500]:
            self.app_list.insert(tk.END, app_id)
        self.app_hint_var.set(f"已读取 {len(apps)} 个应用。" if apps else "未读取到应用，请手动输入。")

    def detect_foreground_app(self) -> None:
        device = self.selected_device
        adapter = self.adapter_for(device)
        if not device or not adapter:
            messagebox.showinfo(APP_NAME, "请先选择设备。")
            return
        try:
            app_id = adapter.foreground_app(device)
        except Exception as exc:
            app_id = ""
            self.append_log(f"读取前台应用失败：{exc}")
        if app_id:
            self.app_var.set(app_id)
            self.app_hint_var.set(f"前台应用：{app_id}")
        else:
            self.app_hint_var.set("未识别到前台应用，请手动输入包名或 Bundle ID。")

    def start_sampling(self) -> None:
        if self.sampler:
            return
        device = self.selected_device
        adapter = self.adapter_for(device)
        if not device or not adapter:
            messagebox.showinfo(APP_NAME, "请先选择设备。")
            return
        if device.status not in ("ready", "device"):
            messagebox.showwarning(APP_NAME, f"设备状态不可用：{device.status}")
            return
        app_id = self.app_var.get().strip().split()[0] if self.app_var.get().strip() else ""
        if not app_id and device.platform == "Android":
            app_id = adapter.foreground_app(device)
            self.app_var.set(app_id)
        if not app_id:
            messagebox.showinfo(APP_NAME, "请填写目标应用包名或 Bundle ID。")
            return
        try:
            interval = max(float(self.interval_var.get()), 0.2)
        except ValueError:
            interval = DEFAULT_INTERVAL_SECONDS
        self.recorder.reset(device, app_id)
        self.recorder.log(f"开始采集：{device.display_name} / {app_id}")
        self.last_notes.clear()
        self._reset_metrics()
        self.stabilizer.reset()
        self.sampler = SamplerThread(adapter, device, app_id, interval, self.events)
        self.sampler.start()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status_var.set("采集中")
        self.session_var.set("00:00 · 0 samples")
        smoothing = "开启" if self.smoothing_var.get() else "关闭"
        self.append_log(f"采集已启动。稳定曲线：{smoothing}（报告仍保存原始采样）。")
        if device.platform == "Android":
            self.append_log("Android 采集已启用多路前台识别和多进程 CPU 汇总。")

    def stop_sampling(self) -> None:
        if not self.sampler:
            return
        self.sampler.stop()
        self.sampler = None
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status_var.set("采集已停止")
        self.append_log("正在停止采集线程。")

    def _reset_metrics(self) -> None:
        self.graph_last_elapsed = 0.0
        self.graph_view_start = 0.0
        self.graph_view_seconds = 10.0
        self.graph_follow_latest = True
        self.stabilizer.reset()
        self.live_quality.reset()
        self.last_quality_event_tag = "ok"
        self.quality_var.set("采集质量：等待数据")
        self._clear_quality_events()
        for graph in self.graphs.values():
            graph.reset()
        for card in self.cards.values():
            card.set_value(0.0, "等待数据")
        self._reset_metric_health()
        self._refresh_graph_time_axis()

    def _reset_metric_health(self) -> None:
        labels = {
            "fps": "FPS",
            "jank_percent": "Jank",
            "cpu_percent": "CPU",
            "memory_mb": "内存",
            "battery_percent": "电量",
            "temperature_c": "温度",
            "power_w": "Power",
            "rx_kbps": "下行",
            "tx_kbps": "上行",
        }
        for metric, variable in self.metric_health_vars.items():
            variable.set(f"{labels.get(metric, metric)}: 等待")

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "sample" and isinstance(payload, PerfSample):
                    self._handle_sample(payload)
                elif kind == "log":
                    self.append_log(str(payload))
                elif kind == "note":
                    note = str(payload)
                    if note and note not in self.last_notes:
                        self.last_notes.add(note)
                        self.append_log(note)
        except queue.Empty:
            pass
        self.root.after(250, self._drain_events)

    def _handle_sample(self, sample: PerfSample) -> None:
        self.recorder.append(sample)
        display_sample = self.stabilizer.smooth_sample(sample) if self.smoothing_var.get() else sample
        quality_tag = sample_quality_tag(sample)
        self._update_metric_health(sample)
        self.quality_var.set(f"采集质量：{self.live_quality.update(sample)}")
        self.cards["fps"].set_value(display_sample.fps, "越高越流畅")
        self.cards["jank_percent"].set_value(display_sample.jank_percent, "越低越稳")
        self.cards["cpu_percent"].set_value(display_sample.cpu_percent, "进程占用")
        self.cards["memory_mb"].set_value(display_sample.memory_mb, "PSS/Total")
        self.cards["temperature_c"].set_value(display_sample.temperature_c, "电池温度")
        self.cards["power_w"].set_value(display_sample.power_w, "估算功耗")
        self.cards["rx_kbps"].set_value(display_sample.rx_kbps, "接收速率")
        self.cards["tx_kbps"].set_value(display_sample.tx_kbps, "发送速率")
        self.graphs["fps"].append(display_sample.elapsed, display_sample.fps, quality_tag)
        self.graphs["jank_percent"].append(display_sample.elapsed, display_sample.jank_percent, quality_tag)
        self.graphs["cpu_percent"].append(display_sample.elapsed, display_sample.cpu_percent, quality_tag)
        self.graphs["memory_mb"].append(display_sample.elapsed, display_sample.memory_mb, quality_tag)
        self.graphs["temperature_c"].append(display_sample.elapsed, display_sample.temperature_c, quality_tag)
        self.graphs["power_w"].append(display_sample.elapsed, display_sample.power_w, quality_tag)
        self.graphs["rx_kbps"].append(display_sample.elapsed, display_sample.rx_kbps, quality_tag)
        self.graphs["tx_kbps"].append(display_sample.elapsed, display_sample.tx_kbps, quality_tag)
        self.graph_last_elapsed = max(self.graph_last_elapsed, sample.elapsed)
        self._refresh_graph_time_axis()
        self.session_var.set(f"{self._format_elapsed(sample.elapsed)} · {len(self.recorder.samples)} samples")
        self._append_quality_event(sample)

    def _clear_quality_events(self) -> None:
        if not hasattr(self, "quality_event_tree"):
            return
        for item in self.quality_event_tree.get_children():
            self.quality_event_tree.delete(item)

    def _append_quality_event(self, sample: PerfSample) -> None:
        tag = sample_quality_tag(sample)
        if tag == "ok":
            self.last_quality_event_tag = "ok"
            return
        if tag == self.last_quality_event_tag:
            return
        event = quality_event_from_sample(sample)
        self.last_quality_event_tag = tag
        if not event or not hasattr(self, "quality_event_tree"):
            return
        self.quality_event_tree.insert("", "end", values=event)
        children = self.quality_event_tree.get_children()
        for item in children[:-80]:
            self.quality_event_tree.delete(item)
        self.quality_event_tree.yview_moveto(1.0)

    def _update_metric_health(self, sample: PerfSample) -> None:
        labels = {
            "fps": "FPS",
            "jank_percent": "Jank",
            "cpu_percent": "CPU",
            "memory_mb": "内存",
            "battery_percent": "电量",
            "temperature_c": "温度",
            "power_w": "Power",
            "rx_kbps": "下行",
            "tx_kbps": "上行",
        }
        prefixes = {
            "ok": "●",
            "waiting": "○",
            "idle": "○",
            "missing": "!",
        }
        health = self.health_analyzer.analyze(sample)
        for metric, status in health.items():
            variable = self.metric_health_vars.get(metric)
            if not variable:
                continue
            prefix = prefixes.get(status.state, "○")
            variable.set(f"{prefix} {labels.get(metric, metric)}: {status.label}")

    def _tick(self) -> None:
        if self.sampler and self.recorder.start_time:
            elapsed = time.time() - self.recorder.start_time
            self.session_var.set(f"{self._format_elapsed(elapsed)} · {len(self.recorder.samples)} samples")
        self._refresh_proxy_traffic()
        self.root.after(1000, self._tick)

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes:02d}:{secs:02d}"

    def append_log(self, text: str) -> None:
        self.recorder.log(text)
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, "\n".join(self.recorder.logs[-80:]))
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def add_marker(self) -> None:
        if not self.recorder.start_time:
            messagebox.showinfo(APP_NAME, "采集开始后才能添加标记。")
            return
        label = self.marker_var.get().strip() or "标记"
        self.recorder.mark(label)
        self.append_log(f"标记：{label}")

    def capture_screenshot(self) -> None:
        device = self.selected_device
        adapter = self.adapter_for(device)
        if not device or not adapter:
            messagebox.showinfo(APP_NAME, "请先选择设备。")
            return
        target = SCREENSHOT_DIR / f"{safe_name(device.name)}_{now_slug()}.png"
        path = adapter.capture_screenshot(device, target)
        if path:
            self.append_log(f"截图已保存：{path}")
            messagebox.showinfo(APP_NAME, f"截图已保存：\n{path}")
        else:
            self.append_log("当前平台/设备暂不支持截图或截图失败。")

    def export_report(self) -> None:
        if not self.recorder.samples:
            messagebox.showinfo(APP_NAME, "暂无采样数据可导出。")
            return
        folder = filedialog.askdirectory(initialdir=str(EXPORT_DIR), title="选择报告导出目录")
        if not folder:
            return
        csv_path, json_path, html_path = self.recorder.export_bundle(Path(folder))
        self.last_export_folder = html_path.parent
        self.append_log(f"报告已导出：{html_path}")
        self._show_export_success(csv_path, json_path, html_path)

    def open_export_folder(self, folder: Path | None = None) -> None:
        target = folder or self.last_export_folder or EXPORT_DIR
        if target.suffix:
            target = target.parent
        target.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            elif os.name == "nt":
                os.startfile(str(target))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"打开文件夹失败：{exc}")

    def _show_export_success(self, csv_path: Path, json_path: Path, html_path: Path) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title(APP_NAME)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.grab_set()

        container = ttk.Frame(dialog, style="Sidebar.TFrame", padding=(18, 16))
        container.pack(fill="both", expand=True)
        ttk.Label(container, text="已导出报告：", style="SidebarTitle.TLabel").pack(anchor="w")
        message = f"{csv_path}\n{json_path}\n{html_path}"
        ttk.Label(container, text=message, style="Muted.TLabel", wraplength=460, justify="left").pack(anchor="w", pady=(10, 16))

        buttons = ttk.Frame(container, style="Sidebar.TFrame")
        buttons.pack(fill="x")
        ttk.Button(buttons, text="打开文件夹", style="Tool.TButton", command=lambda: self.open_export_folder(html_path.parent)).pack(side="left")
        ok_button = ttk.Button(buttons, text="OK", style="Primary.TButton", command=dialog.destroy)
        ok_button.pack(side="right")
        ok_button.focus_set()

        dialog.update_idletasks()
        x = self.root.winfo_rootx() + max((self.root.winfo_width() - dialog.winfo_width()) // 2, 0)
        y = self.root.winfo_rooty() + max((self.root.winfo_height() - dialog.winfo_height()) // 2, 0)
        dialog.geometry(f"+{x}+{y}")
        dialog.wait_window()


def main() -> int:
    ensure_dirs()
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
