import unittest

from mobileperflab import LiveQualityTracker, MetricHealthAnalyzer, MetricStabilizer, PerfSample, sample_quality_tag


class MetricStabilizerTest(unittest.TestCase):
    def test_holds_fps_through_short_zero_gap_without_changing_raw_sample(self) -> None:
        stabilizer = MetricStabilizer()
        first = PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0, cpu_percent=18.0)
        gap = PerfSample(
            timestamp=2.0,
            elapsed=2.0,
            fps=0.0,
            cpu_percent=0.0,
            note="Android FPS 当前无帧增量，低端机/静止页面可能需要更长采样窗口。",
        )

        stabilizer.smooth_sample(first)
        display = stabilizer.smooth_sample(gap)

        self.assertEqual(gap.fps, 0.0)
        self.assertGreater(display.fps, 30.0)
        self.assertGreater(display.cpu_percent, 1.0)

    def test_releases_held_fps_after_long_gap(self) -> None:
        stabilizer = MetricStabilizer()
        stabilizer.smooth_sample(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0))

        display = stabilizer.smooth_sample(PerfSample(timestamp=12.0, elapsed=12.0, fps=0.0))

        self.assertEqual(display.fps, 0.0)


class MetricHealthAnalyzerTest(unittest.TestCase):
    def test_marks_missing_android_metrics_from_note(self) -> None:
        sample = PerfSample(
            timestamp=5.0,
            elapsed=5.0,
            fps=0.0,
            cpu_percent=0.0,
            memory_mb=520.0,
            temperature_c=37.5,
            note="Android FPS 未采集到 Surface；Android CPU 当前无进程增量；Android 网络未匹配到 App UID，无法按应用统计上下行。",
        )

        health = MetricHealthAnalyzer().analyze(sample)

        self.assertEqual(health["fps"].state, "missing")
        self.assertEqual(health["cpu_percent"].state, "missing")
        self.assertEqual(health["memory_mb"].state, "ok")
        self.assertEqual(health["temperature_c"].state, "ok")
        self.assertEqual(health["rx_kbps"].state, "missing")
        self.assertEqual(health["tx_kbps"].state, "missing")

    def test_marks_zero_network_with_no_error_as_idle(self) -> None:
        sample = PerfSample(timestamp=8.0, elapsed=8.0, fps=58.0, cpu_percent=22.0, rx_kbps=0.0, tx_kbps=0.0)

        health = MetricHealthAnalyzer().analyze(sample)

        self.assertEqual(health["rx_kbps"].state, "idle")
        self.assertEqual(health["tx_kbps"].state, "idle")

    def test_marks_device_network_fallback_as_ok_when_it_has_values(self) -> None:
        sample = PerfSample(
            timestamp=9.0,
            elapsed=9.0,
            fps=58.0,
            cpu_percent=22.0,
            rx_kbps=4.0,
            tx_kbps=2.0,
            note="Android 网络使用设备级网络兜底，非目标 App 独占流量。",
        )

        health = MetricHealthAnalyzer().analyze(sample)

        self.assertEqual(health["rx_kbps"].state, "ok")
        self.assertEqual(health["tx_kbps"].state, "ok")


class LiveQualityTrackerTest(unittest.TestCase):
    def test_summarizes_network_fallback_and_missing_metrics(self) -> None:
        tracker = LiveQualityTracker()
        tracker.update(
            PerfSample(
                timestamp=1.0,
                elapsed=1.0,
                fps=0.0,
                cpu_percent=0.0,
                note="Android FPS 未采集到 Surface；Android CPU 当前无进程增量；Android 网络未匹配到 App UID，无法按应用统计上下行。",
            )
        )
        text = tracker.update(
            PerfSample(
                timestamp=2.0,
                elapsed=2.0,
                fps=56.0,
                cpu_percent=20.0,
                rx_kbps=4.0,
                tx_kbps=2.0,
                note="Android 网络使用设备级网络兜底，非目标 App 独占流量。",
            )
        )

        self.assertIn("网络来源：设备级兜底", text)
        self.assertIn("异常样本 1/2", text)
        self.assertIn("兜底 1/2", text)


class SampleQualityTagTest(unittest.TestCase):
    def test_classifies_sample_quality_from_note(self) -> None:
        self.assertEqual(
            sample_quality_tag(PerfSample(timestamp=1.0, elapsed=1.0, note="Android 网络使用设备级网络兜底，非目标 App 独占流量。")),
            "fallback",
        )
        self.assertEqual(
            sample_quality_tag(PerfSample(timestamp=2.0, elapsed=2.0, note="Android FPS 未采集到 Surface")),
            "issue",
        )
        self.assertEqual(sample_quality_tag(PerfSample(timestamp=3.0, elapsed=3.0, fps=60.0)), "ok")


if __name__ == "__main__":
    unittest.main()
