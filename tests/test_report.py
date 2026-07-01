import tempfile
import unittest
from pathlib import Path

from mobileperflab import DeviceInfo, PerfSample, SessionRecorder


class ReportExportTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
