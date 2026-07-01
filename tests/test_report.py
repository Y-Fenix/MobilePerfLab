import json
import tempfile
import unittest
from pathlib import Path

from mobileperflab import DeviceInfo, PerfSample, SessionRecorder


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
        self.assertIn("1.0s", html_text)
        self.assertIn("FPS 未采集", html_text)
        self.assertIn("非目标 App 独占流量", html_text)
        self.assertIn("前台恢复窗口", html_text)
        self.assertIn("采样耗时过长", html_text)
        self.assertIn('"quality"', payload)
        self.assertIn('"quality_gate"', payload)
        self.assertIn('"cadence"', payload)

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
        self.assertEqual(payload["weak_network"]["history"][1]["down_kbps"], 12.3)
        self.assertIn("弱网真实流量", html_text)
        self.assertIn("流量状态", html_text)
        self.assertIn("已命中目标流量", html_text)
        self.assertIn("proxyTrafficHistory", html_text)
        self.assertIn("127.0.0.1:18888", html_text)
        self.assertIn("↓12.3 KB/s", html_text)
        self.assertIn("↑4.5 KB/s", html_text)


if __name__ == "__main__":
    unittest.main()
