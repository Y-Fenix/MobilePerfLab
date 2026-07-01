import unittest

from mobileperflab import (
    LiveQualityTracker,
    MetricHealthAnalyzer,
    MetricStabilizer,
    PerfSample,
    SAMPLING_INTERVAL_OPTIONS,
    build_recent_window_health,
    live_recent_window_summary,
    live_sampling_action_label,
    performance_conclusion_status,
    performance_conclusion_text,
    recommended_sampling_interval,
    recommended_sampling_interval_button_text,
    session_quality_gate,
    sampling_cadence_summary,
    append_sampling_latency_note,
    quality_intervals_from_points,
    quality_event_from_sample,
    quality_interval_label,
    sample_quality_tag,
)


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

    def test_extends_fps_hold_for_slow_low_end_sampling_without_changing_raw_sample(self) -> None:
        stabilizer = MetricStabilizer()
        stabilizer.smooth_sample(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0))
        raw_gap = PerfSample(
            timestamp=5.2,
            elapsed=5.2,
            fps=0.0,
            note="采样耗时 1.60s 超过采样间隔 1.00s，低端机或 adb 慢命令可能导致曲线时间窗不稳定。",
        )

        display = stabilizer.smooth_sample(raw_gap)

        self.assertEqual(raw_gap.fps, 0.0)
        self.assertGreater(display.fps, 20.0)

    def test_dampens_single_frame_fps_dip_for_low_end_device_display(self) -> None:
        stabilizer = MetricStabilizer()
        stabilizer.smooth_sample(PerfSample(timestamp=1.0, elapsed=1.0, fps=58.0))
        raw_dip = PerfSample(timestamp=2.0, elapsed=2.0, fps=20.0)

        display = stabilizer.smooth_sample(raw_dip)

        self.assertEqual(raw_dip.fps, 20.0)
        self.assertGreater(display.fps, 42.0)

    def test_long_sampling_gap_moves_display_closer_to_current_value(self) -> None:
        stable_interval = MetricStabilizer()
        slow_interval = MetricStabilizer()
        stable_interval.smooth_sample(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0))
        slow_interval.smooth_sample(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0))

        stable_display = stable_interval.smooth_sample(PerfSample(timestamp=2.0, elapsed=2.0, fps=30.0))
        slow_display = slow_interval.smooth_sample(
            PerfSample(
                timestamp=5.0,
                elapsed=5.0,
                fps=30.0,
                note="采样耗时 3.20s 超过采样间隔 1.00s，低端机或 adb 慢命令可能导致曲线时间窗不稳定。",
            )
        )

        self.assertGreater(stable_display.fps, 43.0)
        self.assertLess(slow_display.fps, stable_display.fps - 3.0)
        self.assertGreater(slow_display.fps, 30.0)

    def test_dampens_single_sample_cpu_spike_for_display(self) -> None:
        stabilizer = MetricStabilizer()
        stabilizer.smooth_sample(PerfSample(timestamp=1.0, elapsed=1.0, cpu_percent=18.0))

        display = stabilizer.smooth_sample(PerfSample(timestamp=2.0, elapsed=2.0, cpu_percent=88.0))

        self.assertLess(display.cpu_percent, 55.0)

    def test_smooths_more_when_recent_fps_history_is_volatile(self) -> None:
        stable = MetricStabilizer()
        stable.smooth_sample(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0))
        stable.smooth_sample(PerfSample(timestamp=2.0, elapsed=2.0, fps=60.0))
        stable_display = stable.smooth_sample(PerfSample(timestamp=3.0, elapsed=3.0, fps=20.0))

        volatile = MetricStabilizer()
        volatile.smooth_sample(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0))
        volatile.smooth_sample(PerfSample(timestamp=2.0, elapsed=2.0, fps=40.0))
        volatile.smooth_sample(PerfSample(timestamp=3.0, elapsed=3.0, fps=80.0))
        volatile.smooth_sample(PerfSample(timestamp=4.0, elapsed=4.0, fps=60.0))
        volatile_display = volatile.smooth_sample(PerfSample(timestamp=5.0, elapsed=5.0, fps=20.0))

        self.assertGreater(volatile_display.fps, stable_display.fps + 3.0)

    def test_quality_notes_make_display_step_more_conservative(self) -> None:
        normal = MetricStabilizer()
        conservative = MetricStabilizer()
        first = PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0, cpu_percent=20.0)
        normal.smooth_sample(first)
        conservative.smooth_sample(first)

        normal_display = normal.smooth_sample(PerfSample(timestamp=2.0, elapsed=2.0, fps=35.0, cpu_percent=80.0))
        issue_display = conservative.smooth_sample(
            PerfSample(
                timestamp=2.0,
                elapsed=2.0,
                fps=35.0,
                cpu_percent=80.0,
                note="Android FPS 当前无帧增量，Surface=com.example.Surface；采样耗时 1.60s 超过采样间隔 1.00s。",
            )
        )

        self.assertGreater(issue_display.fps, normal_display.fps + 2.0)
        self.assertLess(issue_display.cpu_percent, normal_display.cpu_percent - 2.0)

    def test_low_end_quality_notes_reduce_display_oscillation_range(self) -> None:
        normal = MetricStabilizer()
        low_end = MetricStabilizer()
        series = [60.0, 22.0, 55.0, 18.0, 58.0, 20.0, 56.0]
        normal_values: list[float] = []
        low_end_values: list[float] = []

        for index, fps in enumerate(series, start=1):
            normal_values.append(
                normal.smooth_sample(PerfSample(timestamp=float(index), elapsed=float(index), fps=fps, cpu_percent=20.0)).fps
            )
            low_end_values.append(
                low_end.smooth_sample(
                    PerfSample(
                        timestamp=float(index),
                        elapsed=float(index),
                        fps=fps,
                        cpu_percent=20.0,
                        note="" if index == 1 else "采样耗时 1.60s 超过采样间隔 1.00s，低端机或 adb 慢命令可能导致曲线时间窗不稳定。",
                    )
                ).fps
            )

        normal_range = max(normal_values) - min(normal_values)
        low_end_range = max(low_end_values) - min(low_end_values)

        self.assertLess(low_end_range, normal_range * 0.8)

    def test_conservative_display_mode_dampens_low_end_fps_and_cpu_swings(self) -> None:
        normal = MetricStabilizer()
        conservative = MetricStabilizer()
        first = PerfSample(timestamp=1.0, elapsed=1.0, fps=58.0, cpu_percent=18.0)
        normal.smooth_sample(first)
        conservative.smooth_sample(first, conservative=True)

        raw = PerfSample(timestamp=2.0, elapsed=2.0, fps=24.0, cpu_percent=92.0)
        normal_display = normal.smooth_sample(raw)
        conservative_display = conservative.smooth_sample(raw, conservative=True)

        self.assertEqual(raw.fps, 24.0)
        self.assertEqual(raw.cpu_percent, 92.0)
        self.assertGreater(conservative_display.fps, normal_display.fps + 2.0)
        self.assertLess(conservative_display.cpu_percent, normal_display.cpu_percent - 2.0)


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

    def test_marks_device_network_fallback_as_fallback_when_it_has_values(self) -> None:
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

        self.assertEqual(health["rx_kbps"].state, "fallback")
        self.assertEqual(health["tx_kbps"].state, "fallback")
        self.assertEqual(health["rx_kbps"].label, "兜底")

    def test_marks_individual_metric_failures_from_parallel_android_sampling(self) -> None:
        sample = PerfSample(
            timestamp=10.0,
            elapsed=10.0,
            fps=58.0,
            memory_mb=512.0,
            temperature_c=36.0,
            note="Android CPU 采集失败：proc denied；Android 电量/温度/功耗 采集失败：battery denied",
        )

        health = MetricHealthAnalyzer().analyze(sample)

        self.assertEqual(health["cpu_percent"].state, "missing")
        self.assertEqual(health["battery_percent"].state, "missing")
        self.assertEqual(health["temperature_c"].state, "ok")
        self.assertEqual(health["power_w"].state, "missing")


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
        self.assertIn("兜底：下行/上行", text)
        self.assertIn("异常样本 1/2", text)
        self.assertIn("兜底 1/2", text)

    def test_summarizes_live_metric_availability_for_low_end_devices(self) -> None:
        tracker = LiveQualityTracker()

        text = tracker.update(
            PerfSample(
                timestamp=1.0,
                elapsed=5.0,
                memory_mb=512.0,
                temperature_c=36.8,
                note="Android FPS 未采集到 Surface；Android CPU 当前无进程增量；Android 网络采集不可用：未读取到 per-UID 或设备级网络计数；Android 电量/温度/功耗 采集失败：power denied",
            )
        )

        self.assertIn("可用：内存/温度", text)
        self.assertIn("不可用：FPS/CPU/Power/下行/上行", text)

    def test_summarizes_session_confidence_foreground_and_slow_sampling(self) -> None:
        tracker = LiveQualityTracker()
        tracker.update(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0))
        tracker.update(
            PerfSample(
                timestamp=2.0,
                elapsed=2.0,
                fps=0.0,
                note="目标应用不在前台，当前前台为 com.example.home。",
            )
        )
        text = tracker.update(
            PerfSample(
                timestamp=3.0,
                elapsed=3.0,
                fps=45.0,
                note="采样耗时 1.60s 超过采样间隔 1.00s，低端机或 adb 慢命令可能导致曲线时间窗不稳定。",
            )
        )

        self.assertIn("不可信 33.3%", text)
        self.assertIn("前台 1", text)
        self.assertIn("慢采样 1", text)

    def test_summarizes_slow_sampling_from_elapsed_intervals_without_note(self) -> None:
        tracker = LiveQualityTracker()
        tracker.update(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0))
        tracker.update(PerfSample(timestamp=2.7, elapsed=2.7, fps=55.0))
        tracker.update(PerfSample(timestamp=4.5, elapsed=4.5, fps=54.0))
        text = tracker.update(PerfSample(timestamp=5.5, elapsed=5.5, fps=53.0))

        self.assertIn("不可信", text)
        self.assertIn("慢采样 2", text)
        self.assertTrue(tracker.low_end_display_mode())
        self.assertIn("展示：低端机保守", text)

    def test_respects_custom_expected_interval_before_marking_slow_sampling(self) -> None:
        tracker = LiveQualityTracker(expected_interval=2.0)
        tracker.update(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0))
        tracker.update(PerfSample(timestamp=3.0, elapsed=3.0, fps=58.0))
        text = tracker.update(PerfSample(timestamp=5.1, elapsed=5.1, fps=57.0))

        self.assertIn("高可信", text)
        self.assertIn("慢采样 0", text)
        self.assertFalse(tracker.low_end_display_mode())

    def test_recent_window_health_identifies_slow_low_end_window(self) -> None:
        samples = [
            PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0),
            PerfSample(timestamp=2.8, elapsed=2.8, fps=54.0),
            PerfSample(timestamp=4.6, elapsed=4.6, fps=20.0, note="Android FPS 当前无帧增量"),
            PerfSample(timestamp=6.5, elapsed=6.5, fps=52.0),
        ]

        health = build_recent_window_health(samples, expected_interval=1.0, window_size=4)

        self.assertEqual(health["state"], "bad")
        self.assertEqual(health["label"], "窗口：节拍失稳")
        self.assertEqual(health["slow_samples"], 3)
        self.assertIn("最近 4 个样本", health["detail"])

    def test_recent_window_health_distinguishes_real_fps_volatility_from_collection_jitter(self) -> None:
        performance_samples = [
            PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0),
            PerfSample(timestamp=2.0, elapsed=2.0, fps=42.0),
            PerfSample(timestamp=3.0, elapsed=3.0, fps=58.0),
            PerfSample(timestamp=4.0, elapsed=4.0, fps=40.0),
        ]
        collection_samples = [
            PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0),
            PerfSample(timestamp=2.8, elapsed=2.8, fps=20.0, note="Android FPS 当前无帧增量"),
            PerfSample(timestamp=4.6, elapsed=4.6, fps=58.0),
            PerfSample(timestamp=6.5, elapsed=6.5, fps=22.0, note="采样耗时 1.60s 超过采样间隔 1.00s"),
        ]

        performance = build_recent_window_health(performance_samples, expected_interval=1.0, window_size=4)
        collection = build_recent_window_health(collection_samples, expected_interval=1.0, window_size=4)

        self.assertEqual(performance["trend_source"], "performance")
        self.assertEqual(performance["trend_label"], "趋势：性能波动")
        self.assertEqual(collection["trend_source"], "collection")
        self.assertEqual(collection["trend_label"], "趋势：采集波动")

    def test_status_text_includes_recent_window_health(self) -> None:
        tracker = LiveQualityTracker()
        tracker.update(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0))
        tracker.update(PerfSample(timestamp=2.8, elapsed=2.8, fps=55.0))
        tracker.update(PerfSample(timestamp=4.6, elapsed=4.6, fps=54.0))
        text = tracker.update(PerfSample(timestamp=6.5, elapsed=6.5, fps=53.0))

        self.assertIn("窗口：节拍失稳", text)

    def test_status_text_includes_recent_window_trend_source(self) -> None:
        tracker = LiveQualityTracker()
        tracker.update(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0))
        tracker.update(PerfSample(timestamp=2.0, elapsed=2.0, fps=42.0))
        tracker.update(PerfSample(timestamp=3.0, elapsed=3.0, fps=58.0))
        text = tracker.update(PerfSample(timestamp=4.0, elapsed=4.0, fps=40.0))

        self.assertIn("趋势：性能波动", text)

    def test_live_sampling_action_recommends_next_interval_for_low_end_collection_jitter(self) -> None:
        recent_window = {
            "state": "bad",
            "label": "窗口：节拍失稳",
            "trend_source": "collection",
            "slow_samples": 3,
            "issue_samples": 1,
        }

        self.assertEqual(
            live_sampling_action_label(recent_window, low_end_display_mode=True, expected_interval=1.0),
            "建议：采样间隔调到 1.5s，优先看稳定展示",
        )
        self.assertEqual(
            live_sampling_action_label(recent_window, low_end_display_mode=True, expected_interval=1.5),
            "建议：采样间隔调到 2.0s，优先看稳定展示",
        )

    def test_live_recent_window_summary_prioritizes_collection_jitter_action(self) -> None:
        recent_window = {
            "state": "bad",
            "label": "窗口：节拍失稳",
            "trend_source": "collection",
            "trend_label": "趋势：采集波动",
            "slow_samples": 3,
            "issue_samples": 1,
        }

        self.assertEqual(
            live_recent_window_summary(recent_window, low_end_display_mode=True, expected_interval=1.0),
            "采集波动 · 窗口：节拍失稳 · 推荐 1.5s",
        )

    def test_live_recent_window_summary_distinguishes_real_performance_volatility(self) -> None:
        recent_window = {
            "state": "caution",
            "label": "窗口：谨慎参考",
            "trend_source": "performance",
            "trend_label": "趋势：性能波动",
            "slow_samples": 0,
            "issue_samples": 0,
        }

        self.assertEqual(
            live_recent_window_summary(recent_window, low_end_display_mode=False, expected_interval=1.0),
            "性能波动 · 窗口：谨慎参考 · 按真实性能分析",
        )

    def test_performance_conclusion_status_blocks_collection_jitter(self) -> None:
        status = performance_conclusion_status(
            {
                "state": "bad",
                "label": "窗口：节拍失稳",
                "trend_source": "collection",
                "slow_samples": 3,
                "issue_samples": 1,
            }
        )

        self.assertEqual(status["state"], "blocked")
        self.assertEqual(status["label"], "先修采集链路")
        self.assertIn("不能直接作为性能结论", status["detail"])

    def test_performance_conclusion_status_allows_real_performance_volatility(self) -> None:
        status = performance_conclusion_status(
            {
                "state": "caution",
                "label": "窗口：谨慎参考",
                "trend_source": "performance",
                "slow_samples": 0,
                "issue_samples": 0,
            }
        )

        self.assertEqual(status["state"], "actionable")
        self.assertEqual(status["label"], "可分析性能")
        self.assertIn("真实性能波动", status["detail"])

    def test_performance_conclusion_text_formats_realtime_summary(self) -> None:
        self.assertEqual(
            performance_conclusion_text({"label": "先修采集链路", "detail": "最近窗口主要是采集波动，不能直接作为性能结论。"}),
            "性能结论：先修采集链路 · 最近窗口主要是采集波动，不能直接作为性能结论。",
        )

    def test_performance_conclusion_text_includes_next_sampling_interval_for_blocked_collection(self) -> None:
        self.assertEqual(
            performance_conclusion_text(
                {"state": "blocked", "label": "先修采集链路", "detail": "最近窗口主要是采集波动，不能直接作为性能结论。"},
                expected_interval=1.0,
            ),
            "性能结论：先修采集链路 · 最近窗口主要是采集波动，不能直接作为性能结论。 · 采样间隔 1.0s -> 1.5s",
        )

    def test_recommended_sampling_interval_returns_selectable_option(self) -> None:
        self.assertEqual(recommended_sampling_interval(1.0), 1.5)
        self.assertEqual(recommended_sampling_interval(1.5), 2.0)
        self.assertEqual(recommended_sampling_interval(2.0), 2.0)
        self.assertIn(f"{recommended_sampling_interval(1.0):.1f}", SAMPLING_INTERVAL_OPTIONS)

    def test_recommended_sampling_interval_button_text_shows_next_target(self) -> None:
        self.assertEqual(recommended_sampling_interval_button_text(1.0), "推荐 1.5s")
        self.assertEqual(recommended_sampling_interval_button_text(1.5), "推荐 2.0s")
        self.assertEqual(recommended_sampling_interval_button_text(2.0), "推荐 2.0s")

    def test_status_text_includes_live_sampling_action(self) -> None:
        tracker = LiveQualityTracker()
        tracker.update(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0))
        tracker.update(PerfSample(timestamp=2.8, elapsed=2.8, fps=55.0))
        tracker.update(PerfSample(timestamp=4.6, elapsed=4.6, fps=20.0, note="Android FPS 当前无帧增量"))
        text = tracker.update(PerfSample(timestamp=6.5, elapsed=6.5, fps=52.0))

        self.assertIn("建议：采样间隔调到 1.5s", text)

    def test_session_quality_gate_marks_clean_session_as_trustworthy(self) -> None:
        gate = session_quality_gate(sample_count=10, issue_count=1, fallback_count=0, foreground_count=0, slow_count=0)

        self.assertEqual(gate.state, "good")
        self.assertEqual(gate.label, "高可信")
        self.assertEqual(gate.confidence_percent, 90.0)

    def test_session_quality_gate_marks_mixed_session_as_caution(self) -> None:
        gate = session_quality_gate(sample_count=10, issue_count=2, fallback_count=2, foreground_count=0, slow_count=0)

        self.assertEqual(gate.state, "caution")
        self.assertEqual(gate.label, "谨慎参考")
        self.assertEqual(gate.confidence_percent, 60.0)

    def test_session_quality_gate_marks_foreground_or_slow_session_as_untrusted(self) -> None:
        foreground_gate = session_quality_gate(sample_count=10, issue_count=2, fallback_count=0, foreground_count=2, slow_count=0)
        slow_gate = session_quality_gate(sample_count=10, issue_count=2, fallback_count=0, foreground_count=0, slow_count=3)

        self.assertEqual(foreground_gate.state, "bad")
        self.assertEqual(foreground_gate.label, "不可信")
        self.assertIn("前台异常", foreground_gate.detail)
        self.assertEqual(slow_gate.state, "bad")
        self.assertIn("慢采样", slow_gate.detail)

    def test_sampling_cadence_summary_marks_stable_intervals(self) -> None:
        samples = [
            PerfSample(timestamp=1.0, elapsed=1.0),
            PerfSample(timestamp=2.0, elapsed=2.0),
            PerfSample(timestamp=3.0, elapsed=3.0),
            PerfSample(timestamp=4.0, elapsed=4.0),
        ]

        summary = sampling_cadence_summary(samples, expected_interval=1.0)

        self.assertEqual(summary["state"], "good")
        self.assertEqual(summary["label"], "节拍稳定")
        self.assertEqual(summary["slow_percent"], 0.0)
        self.assertAlmostEqual(float(summary["avg_interval"]), 1.0)

    def test_sampling_cadence_summary_marks_low_end_jitter_as_bad(self) -> None:
        samples = [
            PerfSample(timestamp=1.0, elapsed=1.0),
            PerfSample(timestamp=2.7, elapsed=2.7),
            PerfSample(timestamp=4.5, elapsed=4.5),
            PerfSample(timestamp=5.5, elapsed=5.5),
            PerfSample(timestamp=7.3, elapsed=7.3),
        ]

        summary = sampling_cadence_summary(samples, expected_interval=1.0)

        self.assertEqual(summary["state"], "bad")
        self.assertEqual(summary["label"], "节拍失稳")
        self.assertGreaterEqual(float(summary["slow_percent"]), 50.0)
        self.assertIn("慢间隔", str(summary["detail"]))


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

    def test_prioritizes_collection_issue_over_network_fallback_note(self) -> None:
        sample = PerfSample(
            timestamp=4.0,
            elapsed=4.0,
            note="Android 网络使用设备级网络兜底，非目标 App 独占流量。；Android FPS 未采集到 Surface",
        )

        self.assertEqual(sample_quality_tag(sample), "issue")

    def test_does_not_mark_sample_issue_when_only_power_channel_fails(self) -> None:
        sample = PerfSample(
            timestamp=1.0,
            elapsed=1.0,
            fps=58.0,
            cpu_percent=22.0,
            memory_mb=520.0,
            temperature_c=36.5,
            note="Android 电量/温度/功耗 采集失败：battery current denied",
        )

        self.assertEqual(sample_quality_tag(sample), "ok")

    def test_classifies_slow_sampling_window_as_issue(self) -> None:
        sample = PerfSample(timestamp=3.0, elapsed=3.0, fps=60.0)
        annotated = append_sampling_latency_note(sample, spent_seconds=1.6, interval_seconds=1.0)

        self.assertIn("采样耗时 1.60s 超过采样间隔 1.00s", annotated.note)
        self.assertEqual(sample.note, "")
        self.assertEqual(sample_quality_tag(annotated), "issue")

    def test_classifies_foreground_state_quality_from_note(self) -> None:
        self.assertEqual(
            sample_quality_tag(PerfSample(timestamp=1.0, elapsed=1.0, note="目标应用不在前台，当前前台为 com.example.home。")),
            "issue",
        )
        self.assertEqual(
            sample_quality_tag(
                PerfSample(
                    timestamp=2.0,
                    elapsed=2.0,
                    note="目标应用刚回到前台，恢复窗口内 FPS/CPU 可能受 Surface 和进程缓存重建影响。",
                )
            ),
            "fallback",
        )


class QualityIntervalsTest(unittest.TestCase):
    def test_groups_contiguous_non_ok_points_into_intervals(self) -> None:
        intervals = quality_intervals_from_points(
            [
                (0.0, "ok"),
                (1.0, "issue"),
                (2.0, "issue"),
                (3.0, "ok"),
                (4.0, "fallback"),
                (5.0, "fallback"),
                (6.0, "ok"),
            ]
        )

        self.assertEqual(
            intervals,
            [
                {"start": 1.0, "end": 2.0, "quality": "issue"},
                {"start": 4.0, "end": 5.0, "quality": "fallback"},
            ],
        )

    def test_labels_foreground_recovery_interval_separately_from_network_fallback(self) -> None:
        recovery = PerfSample(
            timestamp=1.0,
            elapsed=1.0,
            note="目标应用刚回到前台，恢复窗口内 FPS/CPU 可能受 Surface 和进程缓存重建影响。",
        )
        network = PerfSample(
            timestamp=2.0,
            elapsed=2.0,
            note="Android 网络使用设备级网络兜底，非目标 App 独占流量。",
        )

        self.assertEqual(quality_interval_label("fallback", recovery.note), "前台恢复窗口")
        self.assertEqual(quality_interval_label("fallback", network.note), "设备级兜底")
        self.assertEqual(quality_interval_label("issue", "采样耗时 1.60s 超过采样间隔 1.00s"), "采样耗时过长")


class QualityEventTest(unittest.TestCase):
    def test_builds_realtime_event_for_issue_sample(self) -> None:
        event = quality_event_from_sample(
            PerfSample(
                timestamp=1.0,
                elapsed=12.4,
                fps=0.0,
                note="Android FPS 未采集到 Surface；Android CPU 当前无进程增量。",
            )
        )

        self.assertEqual(event, ("12.4s", "采集异常", "Android FPS 未采集到 Surface"))

    def test_builds_realtime_event_for_network_fallback_sample(self) -> None:
        event = quality_event_from_sample(
            PerfSample(
                timestamp=1.0,
                elapsed=5.0,
                rx_kbps=3.0,
                note="Android 网络使用设备级网络兜底，非目标 App 独占流量。",
            )
        )

        self.assertEqual(event, ("5.0s", "设备级兜底", "非目标 App 独占流量"))

    def test_builds_realtime_event_for_collection_issue_before_network_fallback(self) -> None:
        event = quality_event_from_sample(
            PerfSample(
                timestamp=1.0,
                elapsed=5.0,
                rx_kbps=3.0,
                note="Android 网络使用设备级网络兜底，非目标 App 独占流量。；Android FPS 未采集到 Surface",
            )
        )

        self.assertEqual(event, ("5.0s", "采集异常", "Android FPS 未采集到 Surface"))

    def test_builds_realtime_event_for_foreground_recovery_sample(self) -> None:
        event = quality_event_from_sample(
            PerfSample(
                timestamp=1.0,
                elapsed=7.5,
                fps=20.0,
                note="目标应用刚回到前台，恢复窗口内 FPS/CPU 可能受 Surface 和进程缓存重建影响。",
            )
        )

        self.assertEqual(event, ("7.5s", "前台恢复窗口", "目标应用刚回到前台"))

    def test_ignores_ok_sample(self) -> None:
        self.assertIsNone(quality_event_from_sample(PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0)))


if __name__ == "__main__":
    unittest.main()
