import json
import tempfile
import unittest
from pathlib import Path

from mobileperflab import AndroidCollectionDiagnostics, DeviceInfo, PerfSample, SessionRecorder


class ReportExportTest(unittest.TestCase):
    def test_quality_summary_counts_parallel_metric_failures(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(
            PerfSample(
                timestamp=1.0,
                elapsed=5.0,
                fps=55.0,
                memory_mb=512.0,
                note="Android CPU 采集失败：proc denied；Android 电量/温度/功耗 采集失败：battery denied",
            )
        )

        labels = {str(issue["label"]) for issue in recorder.quality_summary()["issues"]}  # type: ignore[index]

        self.assertIn("CPU 采集失败", labels)
        self.assertIn("电量/温度/功耗采集失败", labels)

    def test_quality_summary_counts_network_unavailable_as_per_uid_unavailable(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(
            PerfSample(
                timestamp=1.0,
                elapsed=5.0,
                fps=55.0,
                cpu_percent=22.0,
                memory_mb=512.0,
                note="Android 网络采集不可用：未读取到 per-UID 或设备级网络计数。",
            )
        )

        summary = recorder.quality_summary()
        labels = {str(issue["label"]) for issue in summary["issues"]}  # type: ignore[index]

        self.assertIn("网络采集不可用", labels)
        self.assertEqual(summary["network_source"], "per-UID 不可用")

    def test_report_prioritizes_collection_issue_when_sample_also_uses_network_fallback(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(
            PerfSample(
                timestamp=1.0,
                elapsed=5.0,
                fps=0.0,
                cpu_percent=20.0,
                memory_mb=512.0,
                rx_kbps=8.0,
                tx_kbps=2.0,
                note="Android 网络使用设备级网络兜底，非目标 App 独占流量。；Android FPS 未采集到 Surface",
            )
        )

        summary = recorder.quality_summary()
        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            html_text = html_path.read_text(encoding="utf-8")

        self.assertEqual(summary["network_fallback_samples"], 1)
        self.assertEqual(payload["quality"]["quality_gate"]["label"], "不可信")
        self.assertIn('"qualityTag": "issue"', html_text)

    def test_quality_summary_uses_cadence_slow_intervals_even_without_notes(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        for elapsed in [1.0, 2.7, 4.5, 5.5, 7.3]:
            recorder.append(PerfSample(timestamp=elapsed, elapsed=elapsed, fps=55.0, cpu_percent=20.0, memory_mb=512.0))

        quality = recorder.quality_summary()
        gate = quality["quality_gate"]
        cadence = quality["cadence"]

        self.assertEqual(cadence["state"], "bad")
        self.assertEqual(cadence["slow_intervals"], 3)
        self.assertEqual(gate["label"], "不可信")
        self.assertIn("慢采样", gate["detail"])

    def test_quality_summary_includes_real_device_validation_checklist(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(
            PerfSample(
                timestamp=1.0,
                elapsed=1.0,
                fps=0.0,
                cpu_percent=0.0,
                memory_mb=512.0,
                note="Android FPS 未采集到 Surface；Android CPU 当前无进程增量；Android 网络未匹配到 App UID，无法按应用统计上下行。",
            )
        )
        recorder.append(
            PerfSample(
                timestamp=2.8,
                elapsed=2.8,
                fps=55.0,
                cpu_percent=21.0,
                memory_mb=520.0,
                rx_kbps=12.0,
                tx_kbps=3.0,
                note="Android 网络使用设备级网络兜底，非目标 App 独占流量。",
            )
        )

        quality = recorder.quality_summary()
        checklist = quality["validation_checklist"]
        by_key = {item["key"]: item for item in checklist}

        self.assertEqual(by_key["fps"]["state"], "fail")
        self.assertIn("FPS", by_key["fps"]["detail"])
        self.assertEqual(by_key["cpu"]["state"], "warning")
        self.assertEqual(by_key["network"]["state"], "warning")
        self.assertIn("设备级", by_key["network"]["detail"])
        self.assertEqual(by_key["cadence"]["state"], "fail")
        self.assertEqual(by_key["foreground"]["state"], "pass")

    def test_export_bundle_includes_android_collection_diagnostics(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.set_collection_diagnostics(
            AndroidCollectionDiagnostics(
                overall_state="warning",
                summary="Android 采集链路需关注",
                rows=[
                    ("前台", "匹配", "com.example.game"),
                    ("PID", "已获取", "101, 202"),
                    ("UID", "已获取", "10234"),
                    ("FPS", "缺失", "gfxinfo 无帧增量"),
                    ("网络", "设备级兜底", "非目标 App 独占流量"),
                ],
                foreground_app="com.example.game",
                foreground_state="ok",
                pid_source="pidof",
                pids=[101, 202],
                uid_source="dumpsys package",
                uid=10234,
                fps_source="missing",
                network_source="device",
            )
        )
        recorder.append(PerfSample(timestamp=1.0, elapsed=1.0, fps=52.0, cpu_percent=24.0, memory_mb=520.0))

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            html_text = html_path.read_text(encoding="utf-8")

        diagnostics = payload["collection_diagnostics"]
        self.assertEqual(diagnostics["summary"], "Android 采集链路需关注")
        self.assertEqual(diagnostics["foreground_app"], "com.example.game")
        self.assertEqual(diagnostics["pids"], [101, 202])
        self.assertEqual(diagnostics["uid"], 10234)
        self.assertEqual(diagnostics["network_source"], "device")
        self.assertEqual(diagnostics["rows"][3]["name"], "FPS")
        self.assertIn("采集链路自检", html_text)
        self.assertIn("Android 采集链路需关注", html_text)
        self.assertIn("设备级兜底", html_text)

    def test_recommendations_include_android_collection_diagnostic_sources(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.set_collection_diagnostics(
            AndroidCollectionDiagnostics(
                overall_state="warning",
                summary="Android 采集链路需关注",
                rows=[
                    ("前台", "匹配", "com.example.game"),
                    ("PID", "缺失", "未匹配到目标 PID"),
                    ("UID", "缺失", "未匹配到目标 UID"),
                    ("FPS", "缺失", "gfxinfo/SurfaceFlinger 均不可用"),
                    ("网络", "缺失", "per-UID 与设备级计数均不可读"),
                ],
                foreground_app="com.example.game",
                foreground_state="ok",
                pid_source="missing",
                pids=[],
                uid_source="missing",
                uid=None,
                fps_source="missing",
                network_source="missing",
            )
        )
        recorder.append(PerfSample(timestamp=1.0, elapsed=1.0, memory_mb=520.0))

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            html_text = html_path.read_text(encoding="utf-8")

        recommendations = {item["key"]: item for item in payload["quality"]["recommendations"]}

        self.assertIn("fps", recommendations)
        self.assertIn("network", recommendations)
        self.assertIn("pid", recommendations)
        self.assertIn("uid", recommendations)
        self.assertIn("fps_source=missing", recommendations["fps"]["reason"])
        self.assertIn("network_source=missing", recommendations["network"]["reason"])
        self.assertIn("pid_source=missing", recommendations["pid"]["reason"])
        self.assertIn("uid_source=missing", recommendations["uid"]["reason"])
        self.assertIn("重新选择当前前台应用", recommendations["pid"]["action"])
        self.assertIn("dumpsys package", recommendations["uid"]["action"])
        self.assertIn("fps_source=missing", html_text)
        self.assertIn("network_source=missing", html_text)

    def test_report_includes_metric_availability_matrix(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.set_collection_diagnostics(
            AndroidCollectionDiagnostics(
                overall_state="warning",
                summary="Android 采集链路需关注",
                rows=[
                    ("前台", "匹配", "com.example.game"),
                    ("PID", "缺失", "未匹配到目标 PID"),
                    ("UID", "缺失", "未匹配到目标 UID"),
                    ("FPS", "缺失", "gfxinfo/SurfaceFlinger 均不可用"),
                    ("网络", "缺失", "per-UID 与设备级计数均不可读"),
                ],
                foreground_app="com.example.game",
                foreground_state="ok",
                pid_source="missing",
                pids=[],
                uid_source="missing",
                uid=None,
                fps_source="missing",
                network_source="missing",
            )
        )
        recorder.append(
            PerfSample(
                timestamp=1.0,
                elapsed=1.0,
                memory_mb=512.0,
                temperature_c=36.5,
                note="Android FPS 未采集到 Surface；Android CPU 当前无进程增量；Android 网络采集不可用：未读取到 per-UID 或设备级网络计数；Android 电量/温度/功耗 采集失败：power denied",
            )
        )
        recorder.append(
            PerfSample(
                timestamp=2.0,
                elapsed=2.0,
                memory_mb=516.0,
                temperature_c=36.8,
                note="Android FPS 未采集到 Surface；Android CPU 当前无进程增量；Android 网络采集不可用：未读取到 per-UID 或设备级网络计数。",
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            html_text = html_path.read_text(encoding="utf-8")

        availability = {item["key"]: item for item in payload["quality"]["metric_availability"]}

        self.assertEqual(availability["fps"]["state"], "unavailable")
        self.assertEqual(availability["fps"]["source"], "fps_source=missing")
        self.assertEqual(availability["cpu_percent"]["state"], "unavailable")
        self.assertEqual(availability["memory_mb"]["state"], "available")
        self.assertEqual(availability["memory_mb"]["coverage_percent"], 100.0)
        self.assertEqual(availability["temperature_c"]["state"], "available")
        self.assertEqual(availability["power_w"]["state"], "unavailable")
        self.assertEqual(availability["rx_kbps"]["state"], "unavailable")
        self.assertEqual(availability["tx_kbps"]["source"], "network_source=missing")
        self.assertIn("指标可用性", html_text)
        self.assertIn("FPS", html_text)
        self.assertIn("不可用", html_text)
        self.assertIn("memory_mb", json.dumps(payload["quality"]["metric_availability"], ensure_ascii=False))

    def test_export_bundle_keeps_raw_samples_and_adds_display_smoothed_samples(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0, cpu_percent=20.0, memory_mb=520.0))
        recorder.append(PerfSample(timestamp=2.0, elapsed=2.0, fps=60.0, cpu_percent=20.0, memory_mb=521.0))
        recorder.append(PerfSample(timestamp=3.0, elapsed=3.0, fps=20.0, cpu_percent=80.0, memory_mb=522.0))

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            html_text = html_path.read_text(encoding="utf-8")

        self.assertEqual(payload["samples"][2]["fps"], 20.0)
        self.assertIn("display_samples", payload)
        self.assertEqual(len(payload["display_samples"]), 3)
        self.assertGreater(payload["display_samples"][2]["fps"], payload["samples"][2]["fps"])
        checklist = {item["key"]: item for item in payload["quality"]["validation_checklist"]}
        self.assertEqual(checklist["cadence"]["state"], "pass")
        self.assertIn("const displaySamples", html_text)
        self.assertIn("原始值", html_text)
        self.assertIn("稳定展示", html_text)

    def test_html_report_includes_quality_summary_and_network_source(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(
            PerfSample(
                timestamp=1.0,
                elapsed=1.0,
                fps=0.0,
                cpu_percent=0.0,
                memory_mb=512.0,
                rx_kbps=0.0,
                tx_kbps=0.0,
                note="Android FPS 未采集到 Surface；Android CPU 当前无进程增量；Android 网络未匹配到 App UID，无法按应用统计上下行。",
            )
        )
        recorder.append(
            PerfSample(
                timestamp=2.0,
                elapsed=2.0,
                fps=0.0,
                cpu_percent=0.0,
                memory_mb=516.0,
                note="Android FPS 未采集到 Surface；Android CPU 当前无进程增量。",
            )
        )
        recorder.append(
            PerfSample(
                timestamp=4.0,
                elapsed=4.0,
                fps=55.0,
                cpu_percent=24.0,
                memory_mb=520.0,
                rx_kbps=4.0,
                tx_kbps=2.0,
                note="Android 网络使用设备级网络兜底，非目标 App 独占流量。",
            )
        )
        recorder.append(
            PerfSample(
                timestamp=5.0,
                elapsed=5.0,
                fps=56.0,
                cpu_percent=24.0,
                memory_mb=521.0,
                rx_kbps=3.0,
                tx_kbps=2.0,
                note="Android 网络使用设备级网络兜底，非目标 App 独占流量。",
            )
        )
        recorder.append(
            PerfSample(
                timestamp=6.0,
                elapsed=6.0,
                fps=45.0,
                cpu_percent=30.0,
                memory_mb=522.0,
                note="目标应用刚回到前台，恢复窗口内 FPS/CPU 可能受 Surface 和进程缓存重建影响。",
            )
        )
        recorder.append(
            PerfSample(
                timestamp=7.0,
                elapsed=7.0,
                fps=52.0,
                cpu_percent=32.0,
                memory_mb=523.0,
                note="采样耗时 1.60s 超过采样间隔 1.00s，低端机或 adb 慢命令可能导致曲线时间窗不稳定。",
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp))
            html_text = html_path.read_text(encoding="utf-8")
            payload = json_path.read_text(encoding="utf-8")

        self.assertIn("采集质量", html_text)
        self.assertIn("质量门禁", html_text)
        self.assertIn("采样节拍", html_text)
        self.assertIn("节拍波动", html_text)
        self.assertIn("不可信", html_text)
        self.assertIn("曲线标识", html_text)
        self.assertIn("设备级网络兜底", html_text)
        self.assertIn("qualityTag", html_text)
        self.assertIn("qualityIntervals", html_text)
        self.assertIn("连续兜底或异常区间", html_text)
        self.assertIn("异常区间", html_text)
        self.assertIn("采集异常", html_text)
        self.assertIn("设备级兜底", html_text)
        self.assertIn("实机验证清单", html_text)
        self.assertIn("FPS 链路", html_text)
        self.assertIn("网络链路", html_text)
        self.assertIn("1.0s", html_text)
        self.assertIn("FPS 未采集", html_text)
        self.assertIn("非目标 App 独占流量", html_text)
        self.assertIn("前台恢复窗口", html_text)
        self.assertIn("采样耗时过长", html_text)
        self.assertIn('"quality"', payload)
        self.assertIn('"quality_gate"', payload)
        self.assertIn('"cadence"', payload)
        self.assertIn('"validation_checklist"', payload)

    def test_html_report_includes_weak_network_real_traffic_snapshot(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(
            PerfSample(
                timestamp=1.0,
                elapsed=1.0,
                fps=52.0,
                cpu_percent=24.0,
                memory_mb=520.0,
            )
        )

        weak_network = {
            "running": True,
            "endpoint": "127.0.0.1:18888",
            "traffic_state": "hit",
            "traffic_state_label": "已命中目标流量",
            "summary": "弱网 ON · 127.0.0.1:18888 · 已命中目标流量 · ↓12.3 KB/s ↑4.5 KB/s · 2/8 连接 · 丢弃 1",
            "diagnostics": {
                "overall_state": "ok",
                "summary": "弱网代理已确认生效，端口可达",
                "rows": [
                    {"name": "本机代理", "state": "运行中", "detail": "127.0.0.1:18888"},
                    {"name": "Android 设备", "state": "已选择", "detail": "LowEnd"},
                    {"name": "设备代理", "state": "已确认", "detail": "127.0.0.1:18888"},
                    {"name": "端口连通", "state": "可达", "detail": "Android 可连接本机代理端口"},
                ],
            },
            "config": {
                "profile": "弱网",
                "port": 18888,
                "latency_ms": 300,
                "jitter_ms": 120,
                "loss_percent": 2.0,
                "down_kbps": 512.0,
                "up_kbps": 256.0,
            },
            "snapshot": {
                "down_bytes": 123456,
                "up_bytes": 45678,
                "down_kbps": 12.3,
                "up_kbps": 4.5,
                "active_connections": 2,
                "total_connections": 8,
                "dropped_connections": 1,
                "last_activity_age": 0.8,
            },
            "history": [
                {"elapsed": 0.0, "down_kbps": 0.0, "up_kbps": 0.0},
                {"elapsed": 1.0, "down_kbps": 12.3, "up_kbps": 4.5},
                {"elapsed": 2.0, "down_kbps": 18.0, "up_kbps": 5.8},
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp), weak_network=weak_network)
            html_text = html_path.read_text(encoding="utf-8")
            payload = json.loads(json_path.read_text(encoding="utf-8"))

        self.assertIn("weak_network", payload)
        self.assertEqual(payload["weak_network"]["endpoint"], "127.0.0.1:18888")
        self.assertEqual(payload["weak_network"]["traffic_state"], "hit")
        self.assertEqual(payload["weak_network"]["config"]["profile"], "弱网")
        self.assertEqual(payload["weak_network"]["history"][1]["down_kbps"], 12.3)
        self.assertEqual(payload["weak_network"]["diagnostics"]["summary"], "弱网代理已确认生效，端口可达")
        self.assertIn("弱网真实流量", html_text)
        self.assertIn("弱网链路诊断", html_text)
        self.assertIn("弱网配置", html_text)
        self.assertIn("延迟 300ms", html_text)
        self.assertIn("丢包 2.0%", html_text)
        self.assertIn("流量状态", html_text)
        self.assertIn("已命中目标流量", html_text)
        self.assertIn("设备代理", html_text)
        self.assertIn("端口连通", html_text)
        self.assertIn("Android 可连接本机代理端口", html_text)
        self.assertIn("proxyTrafficHistory", html_text)
        self.assertIn("127.0.0.1:18888", html_text)
        self.assertIn("↓12.3 KB/s", html_text)
        self.assertIn("↑4.5 KB/s", html_text)

    def test_html_report_warns_when_weak_proxy_has_not_seen_real_traffic(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(PerfSample(timestamp=1.0, elapsed=1.0, fps=52.0, cpu_percent=24.0, memory_mb=520.0))

        weak_network = {
            "running": True,
            "endpoint": "127.0.0.1:18888",
            "traffic_state": "waiting",
            "traffic_state_label": "等待目标流量",
            "summary": "弱网 ON · 127.0.0.1:18888 · 等待目标流量 · ↓0.0 KB/s ↑0.0 KB/s · 0/0 连接 · 丢弃 0",
            "config": {"profile": "弱网", "port": 18888},
            "snapshot": {},
            "snapshot_display": {},
            "history": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, _json_path, html_path = recorder.export_bundle(Path(tmp), weak_network=weak_network)
            html_text = html_path.read_text(encoding="utf-8")

        self.assertIn("等待目标流量", html_text)
        self.assertIn("报告导出时弱网代理没有捕获到目标请求", html_text)

    def test_export_bundle_includes_actionable_quality_recommendations(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(
            PerfSample(
                timestamp=1.0,
                elapsed=1.0,
                fps=0.0,
                cpu_percent=0.0,
                memory_mb=512.0,
                note="Android FPS 未采集到 Surface；Android CPU 当前无进程增量；Android 网络未匹配到 App UID，无法按应用统计上下行。",
            )
        )
        recorder.append(
            PerfSample(
                timestamp=3.1,
                elapsed=3.1,
                fps=0.0,
                cpu_percent=0.0,
                memory_mb=516.0,
                rx_kbps=8.0,
                tx_kbps=3.0,
                note="Android FPS 未采集到 Surface；Android CPU 当前无进程增量；Android 网络使用设备级网络兜底，非目标 App 独占流量。",
            )
        )
        weak_network = {
            "running": True,
            "endpoint": "127.0.0.1:18888",
            "traffic_state": "waiting",
            "traffic_state_label": "等待目标流量",
            "summary": "弱网 ON · 127.0.0.1:18888 · 等待目标流量",
            "config": {"profile": "弱网", "port": 18888},
            "snapshot": {},
            "history": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp), weak_network=weak_network)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            html_text = html_path.read_text(encoding="utf-8")

        recommendations = {item["key"]: item for item in payload["quality"]["recommendations"]}

        self.assertIn("fps", recommendations)
        self.assertIn("network", recommendations)
        self.assertIn("cadence", recommendations)
        self.assertIn("weak_network", recommendations)
        self.assertIn("保持目标页面可见", recommendations["fps"]["action"])
        self.assertIn("设备级兜底不能当目标 App 独占流量", recommendations["network"]["action"])
        self.assertIn("采样间隔", recommendations["cadence"]["action"])
        self.assertIn("系统 HTTP/HTTPS 代理", recommendations["weak_network"]["action"])
        self.assertIn("修复建议", html_text)
        self.assertIn("保持目标页面可见", html_text)
        self.assertIn("系统 HTTP/HTTPS 代理", html_text)


if __name__ == "__main__":
    unittest.main()
