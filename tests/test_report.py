import json
import tempfile
import unittest
from pathlib import Path

from mobileperflab import AndroidCollectionDiagnostics, DeviceInfo, PerfSample, SessionRecorder, build_session_usability


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

    def test_quality_summary_keeps_sample_trusted_when_only_power_channel_fails(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(
            PerfSample(
                timestamp=1.0,
                elapsed=1.0,
                fps=58.0,
                cpu_percent=22.0,
                memory_mb=520.0,
                temperature_c=36.8,
                note="Android 电量/温度/功耗 采集失败：battery current denied",
            )
        )

        summary = recorder.quality_summary()
        labels = {str(issue["label"]) for issue in summary["issues"]}  # type: ignore[index]

        self.assertEqual(summary["quality_gate"]["label"], "高可信")
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
        self.assertEqual(payload["quality"]["performance_conclusion"]["state"], "unavailable")
        self.assertEqual(payload["quality"]["performance_conclusion"]["label"], "先恢复关键指标")
        self.assertIn("FPS/CPU/网络不可用", payload["quality"]["performance_conclusion"]["detail"])
        self.assertIn("指标可用性", html_text)
        self.assertIn("FPS", html_text)
        self.assertIn("不可用", html_text)
        self.assertIn("先恢复关键指标", html_text)
        self.assertNotIn("结论可信", html_text)
        self.assertIn("memory_mb", json.dumps(payload["quality"]["metric_availability"], ensure_ascii=False))

    def test_metric_availability_marks_per_uid_zero_network_as_idle(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.set_collection_diagnostics(
            AndroidCollectionDiagnostics(
                overall_state="ok",
                summary="Android 采集链路正常",
                rows=[
                    ("前台", "匹配", "com.example.game"),
                    ("PID", "已获取", "101"),
                    ("UID", "已获取", "10234"),
                    ("FPS", "可用", "gfxinfo counters"),
                    ("网络", "per-UID", "目标 App 独占上下行"),
                ],
                foreground_app="com.example.game",
                foreground_state="ok",
                pid_source="pidof",
                pids=[101],
                uid_source="dumpsys package",
                uid=10234,
                fps_source="gfxinfo counters",
                network_source="per-UID",
            )
        )
        recorder.append(PerfSample(timestamp=1.0, elapsed=4.0, fps=58.0, cpu_percent=22.0, memory_mb=512.0))
        recorder.append(PerfSample(timestamp=2.0, elapsed=5.0, fps=59.0, cpu_percent=23.0, memory_mb=514.0))

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            html_text = html_path.read_text(encoding="utf-8")

        availability = {item["key"]: item for item in payload["quality"]["metric_availability"]}

        self.assertEqual(availability["rx_kbps"]["state"], "idle")
        self.assertEqual(availability["tx_kbps"]["state"], "idle")
        self.assertEqual(availability["rx_kbps"]["state_label"], "无流量")
        self.assertIn("目标 App 当前无网络流量", availability["rx_kbps"]["detail"])
        self.assertIn("无流量", html_text)
        self.assertIn("const emptyLabel = config.availabilityLabel || '无有效数据'", html_text)
        self.assertIn("ctx.fillText(emptyLabel", html_text)
        self.assertIn("note.includes('网络无流量') && !note.includes('网络采集')", html_text)

    def test_metric_availability_treats_fps_no_frame_delta_as_idle_when_source_exists(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.set_collection_diagnostics(
            AndroidCollectionDiagnostics(
                overall_state="ok",
                summary="Android 采集链路正常",
                rows=[
                    ("前台", "匹配", "com.example.game"),
                    ("PID", "已获取", "101"),
                    ("UID", "已获取", "10234"),
                    ("FPS", "可用", "gfxinfo counters"),
                    ("网络", "per-UID", "目标 App 独占上下行"),
                ],
                foreground_app="com.example.game",
                foreground_state="ok",
                pid_source="pidof",
                pids=[101],
                uid_source="dumpsys package",
                uid=10234,
                fps_source="gfxinfo counters",
                network_source="per-UID",
            )
        )
        recorder.append(
            PerfSample(
                timestamp=1.0,
                elapsed=4.0,
                cpu_percent=22.0,
                memory_mb=512.0,
                note="Android FPS 当前无帧增量，Surface=SurfaceView[com.example.game]。低端机/静止页面可能需要更长采样窗口。",
            )
        )
        recorder.append(
            PerfSample(
                timestamp=2.0,
                elapsed=5.0,
                cpu_percent=23.0,
                memory_mb=514.0,
                note="Android FPS 当前无帧增量，Surface=SurfaceView[com.example.game]。低端机/静止页面可能需要更长采样窗口。",
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            html_text = html_path.read_text(encoding="utf-8")

        availability = {item["key"]: item for item in payload["quality"]["metric_availability"]}

        self.assertEqual(availability["fps"]["state"], "no_frame_delta")
        self.assertEqual(availability["fps"]["state_label"], "无新增帧")
        self.assertIn("FPS 来源可用", availability["fps"]["detail"])
        self.assertIn("无新增帧", html_text)
        self.assertIn("const emptyLabel = config.availabilityLabel || '无有效数据'", html_text)

    def test_metric_availability_treats_cpu_no_process_delta_as_idle_when_pid_exists(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.set_collection_diagnostics(
            AndroidCollectionDiagnostics(
                overall_state="ok",
                summary="Android 采集链路正常",
                rows=[
                    ("前台", "匹配", "com.example.game"),
                    ("PID", "已获取", "101"),
                    ("UID", "已获取", "10234"),
                    ("FPS", "可用", "gfxinfo counters"),
                    ("网络", "per-UID", "目标 App 独占上下行"),
                ],
                foreground_app="com.example.game",
                foreground_state="ok",
                pid_source="pidof",
                pids=[101],
                uid_source="dumpsys package",
                uid=10234,
                fps_source="gfxinfo counters",
                network_source="per-UID",
            )
        )
        recorder.append(
            PerfSample(
                timestamp=1.0,
                elapsed=4.0,
                fps=58.0,
                memory_mb=512.0,
                note="Android CPU 当前无进程增量，可能是采样间隔过短或系统限制读取 /proc。",
            )
        )
        recorder.append(
            PerfSample(
                timestamp=2.0,
                elapsed=5.0,
                fps=59.0,
                memory_mb=514.0,
                note="Android CPU 当前无进程增量，可能是采样间隔过短或系统限制读取 /proc。",
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            html_text = html_path.read_text(encoding="utf-8")

        availability = {item["key"]: item for item in payload["quality"]["metric_availability"]}

        self.assertEqual(availability["cpu_percent"]["state"], "no_cpu_delta")
        self.assertEqual(availability["cpu_percent"]["state_label"], "CPU 无增量")
        self.assertIn("PID 可用", availability["cpu_percent"]["detail"])
        self.assertIn("CPU 无增量", html_text)

    def test_no_delta_report_states_do_not_count_as_collection_failures(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.set_collection_diagnostics(
            AndroidCollectionDiagnostics(
                overall_state="ok",
                summary="Android 采集链路正常",
                rows=[
                    ("前台", "匹配", "com.example.game"),
                    ("PID", "已获取", "101"),
                    ("UID", "已获取", "10234"),
                    ("FPS", "可用", "gfxinfo counters"),
                    ("网络", "per-UID", "目标 App 独占上下行"),
                ],
                foreground_app="com.example.game",
                foreground_state="ok",
                pid_source="pidof",
                pids=[101],
                uid_source="dumpsys package",
                uid=10234,
                fps_source="gfxinfo counters",
                network_source="per-UID",
            )
        )
        recorder.append(
            PerfSample(
                timestamp=1.0,
                elapsed=1.0,
                fps=0.0,
                cpu_percent=0.0,
                memory_mb=512.0,
                rx_kbps=0.0,
                tx_kbps=0.0,
                note="Android FPS 当前无帧增量，Surface=SurfaceView[com.example.game]。低端机/静止页面可能需要更长采样窗口。",
            )
        )
        recorder.append(
            PerfSample(
                timestamp=2.0,
                elapsed=2.0,
                fps=0.0,
                cpu_percent=0.0,
                memory_mb=514.0,
                rx_kbps=0.0,
                tx_kbps=0.0,
                note="Android CPU 当前无进程增量，可能是采样间隔过短或系统限制读取 /proc。",
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            html_text = html_path.read_text(encoding="utf-8")

        quality = payload["quality"]
        checklist = {item["key"]: item for item in quality["validation_checklist"]}
        labels = {str(issue["label"]) for issue in quality["issues"]}

        self.assertEqual(quality["quality_gate"]["label"], "高可信")
        self.assertEqual(quality["limited_samples"], 2)
        self.assertEqual(quality["limited_percent"], 100.0)
        self.assertEqual(quality["display_strategy"]["mode"], "standard")
        self.assertEqual(checklist["fps"]["state"], "warning")
        self.assertIn("无新增帧", checklist["fps"]["detail"])
        self.assertEqual(checklist["cpu"]["state"], "warning")
        self.assertIn("CPU 无增量", checklist["cpu"]["detail"])
        self.assertNotIn("FPS 无帧增量", labels)
        self.assertNotIn("CPU 无进程增量", labels)
        self.assertIn("只可参考部分指标", quality["session_usability"]["label"])
        self.assertIn("FPS 无新增帧", quality["session_usability"]["detail"])
        self.assertIn("CPU 无增量", quality["session_usability"]["detail"])
        self.assertEqual(payload["display_samples"][0]["qualityTag"], "limited")
        self.assertEqual(payload["display_samples"][1]["qualityTag"], "limited")
        self.assertNotIn('"qualityTag": "issue"', html_text)
        self.assertIn('"qualityTag": "limited"', html_text)
        self.assertIn("受限样本", html_text)
        self.assertIn("2 / 100.0%", html_text)
        self.assertEqual(quality["recent_window"]["state"], "caution")
        self.assertEqual(quality["recent_window"]["label"], "窗口：受限")
        self.assertEqual(quality["recent_window"]["trend_source"], "limited")
        self.assertEqual(quality["recent_window"]["limited_samples"], 2)
        self.assertEqual(quality["recent_window"]["summary"], "受限样本 · 窗口：受限 · 触发业务动作")
        self.assertIn("受限样本 · 窗口：受限 · 触发业务动作", html_text)
        self.assertEqual(quality["performance_conclusion"]["state"], "limited")
        self.assertEqual(quality["performance_conclusion"]["label"], "先触发业务动作")
        self.assertIn("缺少有效变化", quality["performance_conclusion"]["detail"])
        self.assertIn("先触发业务动作", html_text)

    def test_session_usability_blocks_performance_conclusion_when_core_metrics_are_missing(self) -> None:
        availability = [
            {"key": "fps", "state": "unavailable", "coverage_percent": 0.0},
            {"key": "cpu_percent", "state": "unavailable", "coverage_percent": 0.0},
            {"key": "memory_mb", "state": "available", "coverage_percent": 100.0},
            {"key": "temperature_c", "state": "available", "coverage_percent": 100.0},
            {"key": "power_w", "state": "unavailable", "coverage_percent": 0.0},
            {"key": "rx_kbps", "state": "unavailable", "coverage_percent": 0.0},
            {"key": "tx_kbps", "state": "unavailable", "coverage_percent": 0.0},
        ]

        usability = build_session_usability(
            availability,
            {"state": "bad", "label": "不可信", "confidence_percent": 30.0},
        )

        self.assertEqual(usability["state"], "blocked")
        self.assertEqual(usability["label"], "只可参考部分指标")
        self.assertIn("FPS/CPU/网络不可用", usability["detail"])
        self.assertIn("不能用于判断流畅度", usability["action"])

    def test_session_usability_limits_network_fallback(self) -> None:
        availability = [
            {"key": "fps", "state": "available", "coverage_percent": 100.0},
            {"key": "cpu_percent", "state": "available", "coverage_percent": 100.0},
            {"key": "rx_kbps", "state": "fallback", "coverage_percent": 100.0},
            {"key": "tx_kbps", "state": "fallback", "coverage_percent": 100.0},
        ]

        usability = build_session_usability(
            availability,
            {"state": "good", "label": "高可信", "confidence_percent": 100.0},
        )

        self.assertEqual(usability["state"], "limited")
        self.assertEqual(usability["label"], "只可参考部分指标")
        self.assertIn("网络设备级兜底", usability["detail"])
        self.assertIn("不能用于判断目标 App 独占上下行", usability["action"])

    def test_report_exports_session_usability_for_memory_temperature_only_runs(self) -> None:
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
                note="Android FPS 未采集到 Surface；Android CPU 当前无进程增量；Android 网络采集不可用：未读取到 per-UID 或设备级网络计数。",
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

        usability = payload["quality"]["session_usability"]

        self.assertEqual(usability["state"], "blocked")
        self.assertEqual(usability["label"], "只可参考部分指标")
        self.assertIn("FPS/CPU/网络不可用", usability["detail"])
        self.assertIn("会话可用性", html_text)
        self.assertIn("只可参考部分指标", html_text)
        self.assertIn("不能用于判断流畅度", html_text)
        self.assertIn("平均 FPS", html_text)
        self.assertIn("不可用", html_text)
        self.assertNotIn("<strong>0.0</strong><em>FPS</em>", html_text)
        self.assertNotIn("<strong>0.0</strong><em>%</em>", html_text)
        self.assertIn('"availabilityState": "unavailable"', html_text)
        self.assertIn("stat.textContent = config.availabilityLabel", html_text)

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

    def test_export_bundle_marks_low_end_conservative_display_strategy(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0, cpu_percent=18.0, memory_mb=520.0))
        recorder.append(
            PerfSample(
                timestamp=2.7,
                elapsed=2.7,
                fps=24.0,
                cpu_percent=92.0,
                memory_mb=521.0,
                note="采样耗时 1.60s 超过采样间隔 1.00s，低端机或 adb 慢命令可能导致曲线时间窗不稳定。",
            )
        )
        recorder.append(
            PerfSample(
                timestamp=4.5,
                elapsed=4.5,
                fps=26.0,
                cpu_percent=88.0,
                memory_mb=522.0,
                note="采样耗时 1.70s 超过采样间隔 1.00s，低端机或 adb 慢命令可能导致曲线时间窗不稳定。",
            )
        )

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            html_text = html_path.read_text(encoding="utf-8")

        self.assertEqual(payload["samples"][1]["fps"], 24.0)
        self.assertGreater(payload["display_samples"][1]["fps"], payload["samples"][1]["fps"])
        self.assertLess(payload["display_samples"][1]["cpu_percent"], payload["samples"][1]["cpu_percent"])
        self.assertEqual(payload["quality"]["display_strategy"]["mode"], "conservative")
        self.assertIn("低端机保守展示", html_text)

    def test_export_bundle_marks_cadence_inferred_slow_samples_on_graph(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0, cpu_percent=18.0, memory_mb=520.0))
        recorder.append(PerfSample(timestamp=2.8, elapsed=2.8, fps=24.0, cpu_percent=92.0, memory_mb=521.0))
        recorder.append(PerfSample(timestamp=4.6, elapsed=4.6, fps=26.0, cpu_percent=88.0, memory_mb=522.0))

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            html_text = html_path.read_text(encoding="utf-8")

        self.assertEqual(payload["samples"][1]["note"], "")
        self.assertEqual(payload["quality"]["cadence"]["slow_intervals"], 2)
        self.assertEqual(payload["quality"]["display_strategy"]["mode"], "conservative")
        self.assertEqual(payload["display_samples"][0]["qualityTag"], "ok")
        self.assertEqual(payload["display_samples"][1]["qualityTag"], "issue")
        self.assertEqual(payload["display_samples"][2]["qualityTag"], "issue")
        self.assertIn('"qualityTag": "issue"', html_text)

    def test_export_bundle_uses_quality_tags_to_isolate_bad_display_samples(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0, cpu_percent=18.0, memory_mb=520.0))
        recorder.append(PerfSample(timestamp=2.8, elapsed=2.8, fps=0.0, cpu_percent=92.0, memory_mb=521.0))
        recorder.append(PerfSample(timestamp=3.8, elapsed=3.8, fps=58.0, cpu_percent=20.0, memory_mb=522.0))

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, _html_path = recorder.export_bundle(Path(tmp))
            payload = json.loads(json_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["samples"][1]["fps"], 0.0)
        self.assertEqual(payload["display_samples"][1]["qualityTag"], "issue")
        self.assertEqual(payload["quality"]["display_strategy"]["mode"], "standard")
        self.assertGreater(payload["display_samples"][1]["fps"], 50.0)
        self.assertGreater(payload["display_samples"][2]["fps"], 55.0)

    def test_quality_summary_respects_custom_expected_interval_for_low_end_runs(self) -> None:
        recorder = SessionRecorder(expected_interval=2.0)
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0, cpu_percent=18.0, memory_mb=520.0))
        recorder.append(PerfSample(timestamp=3.0, elapsed=3.0, fps=58.0, cpu_percent=20.0, memory_mb=521.0))
        recorder.append(PerfSample(timestamp=5.1, elapsed=5.1, fps=57.0, cpu_percent=19.0, memory_mb=522.0))

        quality = recorder.quality_summary()

        self.assertEqual(quality["cadence"]["state"], "good")
        self.assertEqual(quality["cadence"]["slow_intervals"], 0)
        self.assertEqual(quality["display_strategy"]["mode"], "standard")

    def test_quality_summary_recommends_next_interval_from_current_interval(self) -> None:
        recorder = SessionRecorder(expected_interval=1.5)
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0))
        recorder.append(PerfSample(timestamp=3.2, elapsed=3.2, fps=50.0))
        recorder.append(PerfSample(timestamp=5.4, elapsed=5.4, fps=24.0, note="Android FPS 当前无帧增量"))

        quality = recorder.quality_summary()

        self.assertIn("采样间隔调到 2.0s", quality["recent_window"]["action"])

    def test_quality_summary_includes_recent_window_health(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0, cpu_percent=18.0, memory_mb=520.0))
        recorder.append(PerfSample(timestamp=2.8, elapsed=2.8, fps=55.0, cpu_percent=20.0, memory_mb=521.0))
        recorder.append(PerfSample(timestamp=4.6, elapsed=4.6, fps=20.0, cpu_percent=86.0, memory_mb=522.0, note="Android FPS 当前无帧增量"))
        recorder.append(PerfSample(timestamp=6.5, elapsed=6.5, fps=54.0, cpu_percent=22.0, memory_mb=523.0))

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp))
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            html_text = html_path.read_text(encoding="utf-8")

        self.assertEqual(payload["quality"]["recent_window"]["state"], "bad")
        self.assertEqual(payload["quality"]["recent_window"]["label"], "窗口：节拍失稳")
        self.assertEqual(payload["quality"]["recent_window"]["trend_source"], "collection")
        self.assertIn("采样间隔调到 1.5s", payload["quality"]["recent_window"]["action"])
        self.assertEqual(payload["quality"]["recent_window"]["summary"], "采集波动 · 窗口：节拍失稳 · 推荐 1.5s")
        self.assertEqual(payload["quality"]["performance_conclusion"]["state"], "blocked")
        self.assertEqual(payload["quality"]["performance_conclusion"]["label"], "先修采集链路")
        self.assertIn("最近窗口", html_text)
        self.assertIn("采集波动 · 窗口：节拍失稳 · 推荐 1.5s", html_text)
        self.assertIn("性能结论", html_text)
        self.assertIn("先修采集链路", html_text)
        self.assertIn("窗口：节拍失稳", html_text)
        self.assertIn("趋势：采集波动", html_text)
        self.assertIn("采样建议", html_text)
        self.assertIn("采样间隔调到 1.5s", html_text)

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
        self.assertIn("连续受限、兜底或异常区间", html_text)
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

    def test_html_report_marks_proxy_traffic_without_app_traffic_as_target_unconfirmed(self) -> None:
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
            "effectiveness": {
                "state": "effective",
                "label": "弱网已生效",
                "score": 100,
                "detail": "代理已捕获真实流量，弱网规则有命中证据。",
                "action": "继续执行业务场景并观察代理真实流量曲线。",
                "test_readiness": {
                    "state": "ready",
                    "label": "可以开始测试",
                    "detail": "弱网链路和真实流量均已确认。",
                    "action": "继续执行业务场景并观察代理真实流量曲线。",
                },
            },
            "readiness_display": "可以开始测试 · 继续执行业务场景并观察代理真实流量曲线。",
        }

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp), weak_network=weak_network)
            html_text = html_path.read_text(encoding="utf-8")
            payload = json.loads(json_path.read_text(encoding="utf-8"))

        recommendations = {item["key"]: item for item in payload["quality"]["recommendations"]}

        self.assertIn("weak_network", payload)
        self.assertEqual(payload["weak_network"]["endpoint"], "127.0.0.1:18888")
        self.assertEqual(payload["weak_network"]["traffic_state"], "hit")
        self.assertEqual(payload["weak_network"]["config"]["profile"], "弱网")
        self.assertEqual(payload["weak_network"]["history"][1]["down_kbps"], 12.3)
        self.assertEqual(payload["weak_network"]["diagnostics"]["summary"], "弱网代理已确认生效，端口可达")
        self.assertEqual(payload["weak_network"]["hit_status"], "代理有流量 · 目标 App 待确认")
        self.assertEqual(payload["weak_network"]["effectiveness"]["state"], "target_unconfirmed")
        self.assertIn("目标 App 上下行未确认", payload["weak_network"]["risk_message"])
        self.assertIn("不能认定弱网命中当前测试 App", payload["weak_network"]["risk_message"])
        self.assertIn("weak_network", recommendations)
        self.assertIn("目标 App 上下行", recommendations["weak_network"]["reason"])
        self.assertIn("下载/上传", recommendations["weak_network"]["action"])
        self.assertIn("弱网真实流量", html_text)
        self.assertIn("弱网链路诊断", html_text)
        self.assertIn("弱网配置", html_text)
        self.assertIn("延迟 300ms", html_text)
        self.assertIn("丢包 2.0%", html_text)
        self.assertIn("流量命中", html_text)
        self.assertIn("目标 App 待确认", html_text)
        self.assertIn("流量状态", html_text)
        self.assertIn("代理有真实流量", html_text)
        self.assertIn("弱网命中结论", html_text)
        self.assertIn("代理有流量，目标待确认", html_text)
        self.assertIn("弱网测试结论", html_text)
        self.assertIn("确认目标流量", html_text)
        self.assertIn("就绪动作", html_text)
        self.assertIn("触发明确下载/上传", html_text)
        self.assertIn("测试就绪", html_text)
        self.assertIn("风险提示", html_text)
        self.assertIn("目标 App 上下行未确认", html_text)
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

    def test_report_flags_possible_proxy_bypass_when_app_has_network_but_proxy_waits(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(PerfSample(timestamp=1.0, elapsed=1.0, fps=52.0, cpu_percent=24.0, memory_mb=520.0))
        recorder.append(
            PerfSample(
                timestamp=2.0,
                elapsed=2.0,
                fps=53.0,
                cpu_percent=25.0,
                memory_mb=522.0,
                rx_kbps=128.0,
                tx_kbps=16.0,
            )
        )

        weak_network = {
            "running": True,
            "endpoint": "127.0.0.1:18888",
            "traffic_state": "waiting",
            "traffic_state_label": "等待目标流量",
            "summary": "弱网 ON · 127.0.0.1:18888 · 等待目标流量 · ↓0.0 KB/s ↑0.0 KB/s · 0/0 连接 · 丢弃 0",
            "config": {"profile": "弱网", "port": 18888},
            "snapshot": {"down_kbps": 0.0, "up_kbps": 0.0, "total_connections": 0},
            "snapshot_display": {},
            "history": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp), weak_network=weak_network)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            html_text = html_path.read_text(encoding="utf-8")

        recommendations = {item["key"]: item for item in payload["quality"]["recommendations"]}

        self.assertEqual(payload["weak_network"]["effectiveness"]["state"], "bypass")
        self.assertEqual(payload["weak_network"]["bypass_evidence"]["state"], "bypass")
        self.assertEqual(payload["weak_network"]["bypass_evidence"]["app_peak_kbps"], 144.0)
        self.assertEqual(payload["weak_network"]["bypass_evidence"]["proxy_peak_kbps"], 0.0)
        self.assertIn("App 峰值 144.0 KB/s", payload["weak_network"]["bypass_evidence"]["detail"])
        self.assertIn("先修弱网链路", payload["weak_network"]["readiness_display"])
        self.assertIn("疑似绕过代理", payload["weak_network"]["summary"])
        self.assertIn("疑似绕过系统代理", payload["weak_network"]["risk_message"])
        checklist = {item["key"]: item for item in payload["quality"]["validation_checklist"]}
        self.assertEqual(checklist["weak_network"]["state"], "fail")
        self.assertIn("疑似绕过代理", checklist["weak_network"]["detail"])
        self.assertIn("App 上下行已有流量", recommendations["weak_network"]["reason"])
        self.assertIn("QUIC/UDP", recommendations["weak_network"]["action"])
        self.assertIn("疑似绕过系统代理", html_text)
        self.assertIn("弱网绕过证据", html_text)
        self.assertIn("App 峰值 144.0 KB/s", html_text)

    def test_report_does_not_mark_weak_network_ready_when_app_and_proxy_traffic_mismatch(self) -> None:
        recorder = SessionRecorder()
        recorder.reset(DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready"), "com.example.game")
        recorder.append(PerfSample(timestamp=1.0, elapsed=1.0, fps=52.0, cpu_percent=24.0, memory_mb=520.0))
        recorder.append(
            PerfSample(
                timestamp=2.0,
                elapsed=2.0,
                fps=53.0,
                cpu_percent=25.0,
                memory_mb=522.0,
                rx_kbps=500.0,
                tx_kbps=80.0,
            )
        )

        weak_network = {
            "running": True,
            "endpoint": "127.0.0.1:18888",
            "traffic_state": "hit",
            "traffic_state_label": "代理有真实流量",
            "summary": "弱网 ON · 127.0.0.1:18888 · 代理有真实流量 · ↓1.0 KB/s ↑0.2 KB/s · 1/2 连接 · 丢弃 0",
            "config": {"profile": "弱网", "port": 18888},
            "snapshot": {"down_kbps": 1.0, "up_kbps": 0.2, "total_connections": 2},
            "snapshot_display": {},
            "history": [{"elapsed": 0.0, "down_kbps": 1.0, "up_kbps": 0.2}],
        }

        with tempfile.TemporaryDirectory() as tmp:
            _csv_path, json_path, html_path = recorder.export_bundle(Path(tmp), weak_network=weak_network)
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            html_text = html_path.read_text(encoding="utf-8")

        recommendations = {item["key"]: item for item in payload["quality"]["recommendations"]}
        checklist = {item["key"]: item for item in payload["quality"]["validation_checklist"]}

        self.assertEqual(payload["weak_network"]["bypass_evidence"]["state"], "mismatch")
        self.assertEqual(payload["weak_network"]["effectiveness"]["state"], "target_unconfirmed")
        self.assertEqual(payload["weak_network"]["effectiveness"]["test_readiness"]["state"], "attention")
        self.assertLess(payload["weak_network"]["effectiveness"]["score"], 100)
        self.assertIn("峰值比 483.33x", payload["weak_network"]["bypass_evidence"]["detail"])
        self.assertIn("App 与弱网代理流量不匹配", payload["weak_network"]["risk_message"])
        self.assertEqual(checklist["weak_network"]["state"], "warning")
        self.assertIn("流量不匹配", checklist["weak_network"]["detail"])
        self.assertIn("流量不匹配", recommendations["weak_network"]["reason"])
        self.assertIn("同步变化", recommendations["weak_network"]["action"])
        self.assertIn("峰值比 483.33x", html_text)
        self.assertNotIn("弱网链路和真实流量均已确认", html_text)

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
        self.assertIn("sampling_action", recommendations)
        self.assertIn("weak_network", recommendations)
        self.assertIn("保持目标页面可见", recommendations["fps"]["action"])
        self.assertIn("设备级兜底不能当目标 App 独占流量", recommendations["network"]["action"])
        self.assertIn("采样间隔", recommendations["cadence"]["action"])
        self.assertIn("优先看稳定展示", recommendations["sampling_action"]["action"])
        self.assertIn("系统 HTTP/HTTPS 代理", recommendations["weak_network"]["action"])
        self.assertIn("修复建议", html_text)
        self.assertIn("保持目标页面可见", html_text)
        self.assertIn("优化低端机采样", html_text)
        self.assertIn("系统 HTTP/HTTPS 代理", html_text)


if __name__ == "__main__":
    unittest.main()
