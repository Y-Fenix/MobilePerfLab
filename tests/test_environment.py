import unittest
from pathlib import Path

from mobileperflab import (
    AndroidCollectionDiagnostics,
    App,
    WORKBENCH_SHELL_REGIONS,
    collection_diagnostic_status_rows,
    build_environment_checks,
    format_environment_checks,
    format_graph_view_height,
    format_quality_mode_label,
    graph_quality_badge_text,
    graph_quality_badge_text_for_context,
    graph_latest_display_value_for_context,
    graph_display_max_value,
    graph_display_series,
    graph_display_series_for_context,
    graph_scroll_row_step,
    graph_visible_rows_for_height,
    live_recent_window_summary,
    LiveQualityTracker,
    MetricHealthAnalyzer,
    MetricStabilizer,
    metric_graph_layout,
    PerfSample,
    recommended_sampling_interval_button_text,
    SAMPLING_INTERVAL_OPTIONS,
    smooth_graph_series,
    workbench_sidebar_steps,
    workbench_top_status_items,
)


class FakeFullscreenRoot:
    def __init__(self, fail_state: bool = False, fail_attributes: bool = False) -> None:
        self.fail_state = fail_state
        self.fail_attributes = fail_attributes
        self.calls: list[tuple[str, object]] = []

    def state(self, value: str) -> None:
        self.calls.append(("state", value))
        if self.fail_state:
            raise RuntimeError("state unsupported")

    def attributes(self, key: str, value: bool) -> None:
        self.calls.append(("attributes", (key, value)))
        if self.fail_attributes:
            raise RuntimeError("attributes unsupported")

    def winfo_screenwidth(self) -> int:
        return 1440

    def winfo_screenheight(self) -> int:
        return 900

    def geometry(self, value: str) -> None:
        self.calls.append(("geometry", value))


class EnvironmentCheckTest(unittest.TestCase):
    def test_marks_android_adb_as_required_for_real_android_sampling(self) -> None:
        checks = build_environment_checks(
            {
                "python": "/usr/bin/python3",
                "adb": "",
                "pymobiledevice3": "",
                "xcrun": "",
            }
        )

        adb = next(check for check in checks if check.key == "adb")

        self.assertEqual(adb.state, "missing")
        self.assertEqual(adb.level, "required")
        self.assertIn("Android 真机", adb.detail)
        self.assertIn("Platform-Tools", adb.action)

    def test_reports_ios_toolchain_as_ready_when_pymobiledevice3_exists(self) -> None:
        checks = build_environment_checks(
            {
                "python": "/usr/bin/python3",
                "adb": "/opt/android/adb",
                "pymobiledevice3": "/tmp/pymobiledevice3",
                "xcrun": "/usr/bin/xcrun",
            }
        )

        ios = next(check for check in checks if check.key == "pymobiledevice3")

        self.assertEqual(ios.state, "ok")
        self.assertEqual(ios.level, "optional")
        self.assertIn("iOS", ios.detail)

    def test_formats_environment_checks_for_sidebar_and_logs(self) -> None:
        checks = build_environment_checks(
            {
                "python": "/usr/bin/python3",
                "adb": "",
                "pymobiledevice3": "/tmp/pymobiledevice3",
                "xcrun": "",
            }
        )

        text = format_environment_checks(checks)

        self.assertIn("Python 3：可用", text)
        self.assertIn("Android ADB：缺失", text)
        self.assertIn("iOS pymobiledevice3：可用", text)
        self.assertIn("Xcode xcrun：缺失", text)


class FullscreenStartupTest(unittest.TestCase):
    def test_fullscreen_prefers_zoomed_state(self) -> None:
        root = FakeFullscreenRoot()

        App._open_fullscreen_window_for_root(root)

        self.assertEqual(root.calls, [("state", "zoomed")])

    def test_fullscreen_falls_back_to_screen_geometry(self) -> None:
        root = FakeFullscreenRoot(fail_state=True, fail_attributes=True)

        App._open_fullscreen_window_for_root(root)

        self.assertEqual(
            root.calls,
            [
                ("state", "zoomed"),
                ("attributes", ("-zoomed", True)),
                ("geometry", "1440x900+0+0"),
            ],
        )


class WorkbenchLayoutContractTest(unittest.TestCase):
    def test_workbench_shell_has_professional_four_region_layout(self) -> None:
        self.assertEqual(
            WORKBENCH_SHELL_REGIONS,
            ("top_session_bar", "left_control_rail", "central_observability", "right_diagnostics_rail"),
        )

    def test_sidebar_steps_match_zero_learning_workflow(self) -> None:
        steps = workbench_sidebar_steps()

        self.assertEqual(
            [step["key"] for step in steps],
            ["connect_device", "select_app", "preflight", "sample"],
        )
        self.assertEqual(steps[0]["title"], "1 连接设备")
        self.assertEqual(steps[1]["title"], "2 选择应用")
        self.assertIn("开始采集", steps[3]["primary_action"])

    def test_top_status_items_keep_session_context_visible(self) -> None:
        items = workbench_top_status_items()

        self.assertEqual(
            [item["key"] for item in items],
            ["device", "target_app", "capture", "quality", "weak_network"],
        )
        self.assertEqual(items[0]["label"], "设备")
        self.assertEqual(items[-1]["label"], "弱网")


class GraphScrollBehaviorTest(unittest.TestCase):
    def test_graph_quality_badge_summarizes_visible_issue_and_fallback_points(self) -> None:
        self.assertEqual(
            graph_quality_badge_text(
                [
                    (0.0, 60.0, "ok"),
                    (1.0, 0.0, "issue"),
                    (2.0, 55.0, "fallback"),
                    (3.0, 20.0, "issue"),
                    (4.0, 58.0, "limited"),
                ]
            ),
            "异常 2 · 兜底 1 · 受限 1",
        )

    def test_graph_quality_badge_is_empty_for_trusted_points(self) -> None:
        self.assertEqual(graph_quality_badge_text([(0.0, 60.0, "ok"), (1.0, 59.0, "ok")]), "")

    def test_graph_quality_badge_shows_steady_display_context(self) -> None:
        self.assertEqual(
            graph_quality_badge_text_for_context(
                [(0.0, 60.0, "ok"), (1.0, 35.0, "issue")],
                smoothing_enabled=True,
                low_end_display_mode=True,
            ),
            "异常 1 · 稳态",
        )
        self.assertEqual(
            graph_quality_badge_text_for_context(
                [(0.0, 60.0, "ok"), (1.0, 58.0, "ok")],
                smoothing_enabled=True,
                low_end_display_mode=False,
            ),
            "",
        )

    def test_mousewheel_scrolls_one_row_per_notch(self) -> None:
        self.assertEqual(graph_scroll_row_step(1), 1)
        self.assertEqual(graph_scroll_row_step(3), 1)
        self.assertEqual(graph_scroll_row_step(-1), -1)
        self.assertEqual(graph_scroll_row_step(-4), -1)
        self.assertEqual(graph_scroll_row_step(0), 0)

    def test_graph_view_rows_defaults_to_two_visible_rows(self) -> None:
        self.assertEqual(graph_visible_rows_for_height(720), 2)
        self.assertEqual(graph_visible_rows_for_height(900), 2)
        self.assertEqual(graph_visible_rows_for_height(1400), 2)

    def test_graph_view_height_shows_exactly_two_rows_plus_scrollbar(self) -> None:
        self.assertEqual(format_graph_view_height(2, 176, 10, 22), 384)

    def test_metric_graph_layout_contains_all_required_graphs(self) -> None:
        layout = metric_graph_layout()
        keys = [item["key"] for item in layout]

        self.assertEqual(
            keys,
            [
                "fps",
                "jank_percent",
                "cpu_percent",
                "memory_mb",
                "temperature_c",
                "power_w",
                "rx_kbps",
                "tx_kbps",
            ],
        )
        self.assertEqual(
            [(item["row"], item["col"]) for item in layout],
            [(0, 0), (0, 1), (1, 0), (1, 1), (2, 0), (2, 1), (3, 0), (3, 1)],
        )

    def test_smooth_graph_series_reduces_display_oscillation_without_changing_length(self) -> None:
        raw = [(0.0, 0.0), (1.0, 10.0), (2.0, 0.0), (3.0, 10.0)]

        smoothed = smooth_graph_series(raw, alpha=0.25)

        raw_range = max(value for _elapsed, value in raw) - min(value for _elapsed, value in raw)
        smooth_range = max(value for _elapsed, value in smoothed) - min(value for _elapsed, value in smoothed)
        self.assertEqual(len(smoothed), len(raw))
        self.assertEqual(smoothed[0][0], 0.0)
        self.assertLess(smooth_range, raw_range)

    def test_live_graph_display_series_can_skip_second_smoothing_pass(self) -> None:
        stabilized = [(0.0, 60.0), (1.0, 45.0), (2.0, 30.0)]

        display = graph_display_series(stabilized, smooth=False)

        self.assertEqual(display, stabilized)

    def test_graph_context_series_smooths_low_end_display_without_changing_length(self) -> None:
        raw = [(0.0, 60.0), (1.0, 34.0), (2.0, 64.0), (3.0, 31.0), (4.0, 62.0)]

        display = graph_display_series_for_context(
            raw,
            smoothing_enabled=True,
            low_end_display_mode=True,
            qualities=["ok"] * len(raw),
        )

        raw_range = max(value for _elapsed, value in raw) - min(value for _elapsed, value in raw)
        display_range = max(value for _elapsed, value in display) - min(value for _elapsed, value in display)
        self.assertEqual(len(display), len(raw))
        self.assertLess(display_range, raw_range * 0.65)

    def test_graph_context_series_keeps_trusted_standard_display_responsive(self) -> None:
        stabilized = [(0.0, 60.0), (1.0, 54.0), (2.0, 50.0)]

        display = graph_display_series_for_context(
            stabilized,
            smoothing_enabled=True,
            low_end_display_mode=False,
            qualities=["ok", "ok", "ok"],
        )

        self.assertEqual(display, stabilized)

    def test_graph_context_series_smooths_visible_quality_issues_before_low_end_mode(self) -> None:
        raw = [(0.0, 60.0), (1.0, 0.0), (2.0, 58.0), (3.0, 30.0)]

        display = graph_display_series_for_context(
            raw,
            smoothing_enabled=True,
            low_end_display_mode=False,
            qualities=["ok", "issue", "ok", "fallback"],
        )

        raw_range = max(value for _elapsed, value in raw) - min(value for _elapsed, value in raw)
        display_range = max(value for _elapsed, value in display) - min(value for _elapsed, value in display)
        self.assertLess(display_range, raw_range)

    def test_graph_context_series_smooths_limited_quality_points_before_low_end_mode(self) -> None:
        raw = [(0.0, 60.0), (1.0, 0.0), (2.0, 58.0), (3.0, 55.0)]

        display = graph_display_series_for_context(
            raw,
            smoothing_enabled=True,
            low_end_display_mode=False,
            qualities=["ok", "limited", "ok", "ok"],
        )

        raw_range = max(value for _elapsed, value in raw) - min(value for _elapsed, value in raw)
        display_range = max(value for _elapsed, value in display) - min(value for _elapsed, value in display)
        self.assertEqual(len(display), len(raw))
        self.assertLess(display_range, raw_range)
        self.assertEqual(raw[1], (1.0, 0.0))

    def test_graph_latest_display_value_uses_stable_value_without_mutating_raw_points(self) -> None:
        points = [(0.0, 60.0, "ok"), (1.0, 0.0, "limited")]

        latest = graph_latest_display_value_for_context(
            points,
            smoothing_enabled=True,
            low_end_display_mode=False,
        )

        self.assertGreater(latest, 0.0)
        self.assertEqual(points[-1], (1.0, 0.0, "limited"))

    def test_graph_axis_ignores_single_quality_spike_but_keeps_real_performance_spike(self) -> None:
        issue_axis = graph_display_max_value(
            [(0.0, 58.0, "ok"), (1.0, 300.0, "issue"), (2.0, 57.0, "ok")],
            metric="fps",
            display_values=[(0.0, 58.0), (1.0, 106.4), (2.0, 96.5)],
        )
        real_axis = graph_display_max_value(
            [(0.0, 58.0, "ok"), (1.0, 95.0, "ok"), (2.0, 57.0, "ok")],
            metric="fps",
            display_values=[(0.0, 58.0), (1.0, 95.0), (2.0, 57.0)],
        )

        self.assertEqual(issue_axis, 106.4)
        self.assertEqual(real_axis, 95.0)


class QualityModeLabelTest(unittest.TestCase):
    def test_formats_low_end_bias_label(self) -> None:
        self.assertEqual(format_quality_mode_label(True, False), "稳定曲线：开 · 报告：稳态+原始")
        self.assertEqual(format_quality_mode_label(True, True), "稳定曲线：开 · 低端机保守模式 · 报告：稳态+原始")
        self.assertEqual(format_quality_mode_label(False, True), "稳定曲线：关 · 报告：原始采样")

    def test_app_default_quality_mode_label_matches_default_smoothing_state(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")

        self.assertIn('self.smoothing_var = tk.BooleanVar(value=True)', text)
        self.assertIn('self.quality_mode_var = tk.StringVar(value=format_quality_mode_label(True, False))', text)
        self.assertNotIn('self.quality_mode_var = tk.StringVar(value="稳定曲线：开 · 报告：原始采样")', text)

    def test_sampling_interval_options_include_low_end_guidance_target(self) -> None:
        self.assertIn("1.5", SAMPLING_INTERVAL_OPTIONS)
        self.assertIn("2.0", SAMPLING_INTERVAL_OPTIONS)

    def test_app_applies_recommended_sampling_interval(self) -> None:
        class FakeVar:
            def __init__(self, value: str) -> None:
                self.value = value

            def get(self) -> str:
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        class FakeIntervalTarget:
            def __init__(self) -> None:
                self.expected_interval = 0.0

            def set_expected_interval(self, value: float) -> None:
                self.expected_interval = value

        app = object.__new__(App)
        app.interval_var = FakeVar("1.0")
        app.recommended_interval_var = FakeVar(recommended_sampling_interval_button_text(1.0))
        app.recorder = FakeIntervalTarget()
        app.live_quality = FakeIntervalTarget()
        app.logs: list[str] = []
        app.append_log = lambda text: app.logs.append(text)

        App.apply_recommended_sampling_interval(app)

        self.assertEqual(app.interval_var.get(), "1.5")
        self.assertEqual(app.recommended_interval_var.get(), "推荐 2.0s")
        self.assertEqual(app.recorder.expected_interval, 1.5)
        self.assertEqual(app.live_quality.expected_interval, 1.5)
        self.assertIn("推荐采样间隔", app.logs[-1])

    def test_app_applies_recommended_sampling_interval_to_running_sampler(self) -> None:
        class FakeVar:
            def __init__(self, value: str) -> None:
                self.value = value

            def get(self) -> str:
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        class FakeIntervalTarget:
            def set_expected_interval(self, _value: float) -> None:
                pass

        class FakeSampler:
            def __init__(self) -> None:
                self.interval = 1.0

            def set_interval(self, value: float) -> None:
                self.interval = value

        app = object.__new__(App)
        app.interval_var = FakeVar("1.0")
        app.recorder = FakeIntervalTarget()
        app.live_quality = FakeIntervalTarget()
        app.sampler = FakeSampler()
        app.append_log = lambda _text: None

        App.apply_recommended_sampling_interval(app)

        self.assertEqual(app.sampler.interval, 1.5)

    def test_live_recent_window_summary_has_short_ui_message(self) -> None:
        summary = live_recent_window_summary(
            {
                "state": "bad",
                "label": "窗口：节拍失稳",
                "trend_source": "collection",
                "slow_samples": 2,
                "issue_samples": 0,
            },
            low_end_display_mode=True,
            expected_interval=1.5,
        )

        self.assertEqual(summary, "采集波动 · 窗口：节拍失稳 · 推荐 2.0s")

    def test_handle_sample_updates_live_performance_conclusion(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

            def get(self) -> bool:
                return True

        class FakeRecorder:
            def __init__(self) -> None:
                self.samples: list[PerfSample] = []

            def append(self, sample: PerfSample) -> None:
                self.samples.append(sample)

        class FakeMetricHealth:
            def analyze(self, _sample: PerfSample) -> dict[str, object]:
                return {}

        class FakeCard:
            def set_value(self, _value: object, _sub: str) -> None:
                pass

        class FakeGraph:
            def __init__(self) -> None:
                self.smoothing_contexts: list[tuple[bool, bool]] = []

            def set_display_context(self, smoothing_enabled: bool, low_end_display_mode: bool) -> None:
                self.smoothing_contexts.append((smoothing_enabled, low_end_display_mode))

            def append(self, _elapsed: float, _value: float, _quality: str) -> None:
                pass

        app = object.__new__(App)
        app.recorder = FakeRecorder()
        app.last_app_rx_kbps = 0.0
        app.last_app_tx_kbps = 0.0
        app.metric_health_vars = {}
        app.collection_link_vars = {}
        app.health_analyzer = FakeMetricHealth()
        app.live_quality = LiveQualityTracker()
        app.quality_summary_var = FakeVar()
        app.performance_conclusion_var = FakeVar()
        app.quality_var = FakeVar()
        app.quality_mode_var = FakeVar()
        app.smoothing_var = FakeVar()
        app.stabilizer = MetricStabilizer()
        app.graph_last_elapsed = 0.0
        app.session_var = FakeVar()
        app.cards = {
            "fps": FakeCard(),
            "jank_percent": FakeCard(),
            "cpu_percent": FakeCard(),
            "memory_mb": FakeCard(),
            "temperature_c": FakeCard(),
            "power_w": FakeCard(),
            "rx_kbps": FakeCard(),
            "tx_kbps": FakeCard(),
        }
        app.graphs = {key: FakeGraph() for key in app.cards}
        app._refresh_graph_time_axis = lambda: None
        app._format_elapsed = lambda elapsed: f"{elapsed:.1f}s"
        app._append_quality_event = lambda _sample: None
        app._refresh_proxy_traffic = lambda: None

        App._handle_sample(app, PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0))
        App._handle_sample(app, PerfSample(timestamp=2.8, elapsed=2.8, fps=55.0))
        App._handle_sample(
            app,
            PerfSample(timestamp=4.6, elapsed=4.6, fps=20.0, note="Android FPS 当前无帧增量"),
        )

        self.assertIn("性能结论：先修采集链路", app.performance_conclusion_var.value)
        self.assertIn("采样间隔 1.0s -> 1.5s", app.performance_conclusion_var.value)
        self.assertIn((True, True), app.graphs["fps"].smoothing_contexts)

    def test_handle_sample_surfaces_live_session_usability_when_core_metrics_missing(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

            def get(self) -> bool:
                return True

        class FakeRecorder:
            def __init__(self) -> None:
                self.samples: list[PerfSample] = []

            def append(self, sample: PerfSample) -> None:
                self.samples.append(sample)

        class FakeMetricHealth:
            def analyze(self, _sample: PerfSample) -> dict[str, object]:
                return {}

        class FakeCard:
            def __init__(self) -> None:
                self.value: object = None
                self.sub = ""

            def set_value(self, value: object, sub: str) -> None:
                self.value = value
                self.sub = sub

        class FakeGraph:
            def set_display_context(self, _smoothing_enabled: bool, _low_end_display_mode: bool) -> None:
                pass

            def append(self, _elapsed: float, _value: float, _quality: str) -> None:
                pass

        app = object.__new__(App)
        app.recorder = FakeRecorder()
        app.last_app_rx_kbps = 0.0
        app.last_app_tx_kbps = 0.0
        app.metric_health_vars = {}
        app.collection_link_vars = {}
        app.health_analyzer = MetricHealthAnalyzer()
        app.live_quality = LiveQualityTracker()
        app.quality_summary_var = FakeVar()
        app.performance_conclusion_var = FakeVar()
        app.quality_var = FakeVar()
        app.quality_mode_var = FakeVar()
        app.smoothing_var = FakeVar()
        app.stabilizer = MetricStabilizer()
        app.graph_last_elapsed = 0.0
        app.session_var = FakeVar()
        app.cards = {
            "fps": FakeCard(),
            "jank_percent": FakeCard(),
            "cpu_percent": FakeCard(),
            "memory_mb": FakeCard(),
            "temperature_c": FakeCard(),
            "power_w": FakeCard(),
            "rx_kbps": FakeCard(),
            "tx_kbps": FakeCard(),
        }
        app.graphs = {key: FakeGraph() for key in app.cards}
        app._refresh_graph_time_axis = lambda: None
        app._format_elapsed = lambda elapsed: f"{elapsed:.1f}s"
        app._append_quality_event = lambda _sample: None
        app._refresh_proxy_traffic = lambda: None

        App._handle_sample(
            app,
            PerfSample(
                timestamp=1.0,
                elapsed=1.0,
                memory_mb=512.0,
                temperature_c=36.8,
                note="Android FPS 未采集到 Surface；Android CPU 当前无进程增量；Android 网络采集不可用：未读取到 per-UID 或设备级网络计数。",
            ),
        )

        self.assertIn("会话可用性：只可参考部分指标", app.performance_conclusion_var.value)
        self.assertIn("FPS/CPU/网络不可用", app.performance_conclusion_var.value)
        self.assertEqual(app.cards["fps"].value, "不可用")
        self.assertEqual(app.cards["cpu_percent"].value, "不可用")
        self.assertEqual(app.cards["rx_kbps"].value, "不可用")
        self.assertEqual(app.cards["tx_kbps"].value, "不可用")
        self.assertEqual(app.cards["memory_mb"].value, 512.0)

    def test_handle_sample_marks_foreground_recovery_as_recovering_not_unavailable(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

            def get(self) -> bool:
                return True

        class FakeRecorder:
            def __init__(self) -> None:
                self.samples: list[PerfSample] = []

            def append(self, sample: PerfSample) -> None:
                self.samples.append(sample)

        class FakeCard:
            def set_value(self, _value: float, _sub: str) -> None:
                pass

        class FakeGraph:
            def set_display_context(self, _smoothing_enabled: bool, _low_end_display_mode: bool) -> None:
                pass

            def append(self, _elapsed: float, _value: float, _quality: str) -> None:
                pass

        app = object.__new__(App)
        app.recorder = FakeRecorder()
        app.last_app_rx_kbps = 0.0
        app.last_app_tx_kbps = 0.0
        app.metric_health_vars = {}
        app.collection_link_vars = {}
        app.health_analyzer = MetricHealthAnalyzer()
        app.live_quality = LiveQualityTracker()
        app.quality_summary_var = FakeVar()
        app.performance_conclusion_var = FakeVar()
        app.quality_var = FakeVar()
        app.quality_mode_var = FakeVar()
        app.smoothing_var = FakeVar()
        app.stabilizer = MetricStabilizer()
        app.graph_last_elapsed = 0.0
        app.session_var = FakeVar()
        app.cards = {
            "fps": FakeCard(),
            "jank_percent": FakeCard(),
            "cpu_percent": FakeCard(),
            "memory_mb": FakeCard(),
            "temperature_c": FakeCard(),
            "power_w": FakeCard(),
            "rx_kbps": FakeCard(),
            "tx_kbps": FakeCard(),
        }
        app.graphs = {key: FakeGraph() for key in app.cards}
        app._refresh_graph_time_axis = lambda: None
        app._format_elapsed = lambda elapsed: f"{elapsed:.1f}s"
        app._append_quality_event = lambda _sample: None
        app._refresh_proxy_traffic = lambda: None

        App._handle_sample(
            app,
            PerfSample(
                timestamp=20.0,
                elapsed=20.0,
                fps=0.0,
                cpu_percent=0.0,
                memory_mb=512.0,
                note="目标应用刚回到前台，恢复窗口内 FPS/CPU 可能受 Surface 和进程缓存重建影响。",
            ),
        )

        self.assertIn("会话可用性：恢复窗口", app.performance_conclusion_var.value)
        self.assertIn("等待 FPS/CPU/网络重新建立基线", app.performance_conclusion_var.value)
        self.assertIn("恢复中：FPS/CPU/下行/上行", app.quality_var.value)
        self.assertNotIn("FPS/CPU/网络不可用", app.performance_conclusion_var.value)

    def test_handle_sample_feeds_graphs_raw_values_while_cards_show_stable_values(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

            def get(self) -> bool:
                return True

        class FakeRecorder:
            def __init__(self) -> None:
                self.samples: list[PerfSample] = []

            def append(self, sample: PerfSample) -> None:
                self.samples.append(sample)

        class FakeCard:
            def __init__(self) -> None:
                self.value: object = None
                self.sub = ""

            def set_value(self, value: object, sub: str) -> None:
                self.value = value
                self.sub = sub

        class FakeGraph:
            def __init__(self) -> None:
                self.points: list[tuple[float, float, str]] = []

            def set_display_context(self, _smoothing_enabled: bool, _low_end_display_mode: bool) -> None:
                pass

            def append(self, elapsed: float, value: float, quality: str) -> None:
                self.points.append((elapsed, value, quality))

        class SpyStabilizer(MetricStabilizer):
            def __init__(self) -> None:
                super().__init__()
                self.outputs: list[PerfSample] = []

            def smooth_sample(self, sample: PerfSample, conservative: bool = False, quality_tag: str = "ok") -> PerfSample:
                display = super().smooth_sample(sample, conservative=conservative, quality_tag=quality_tag)
                self.outputs.append(display)
                return display

        app = object.__new__(App)
        app.recorder = FakeRecorder()
        app.last_app_rx_kbps = 0.0
        app.last_app_tx_kbps = 0.0
        app.metric_health_vars = {}
        app.collection_link_vars = {}
        app.health_analyzer = MetricHealthAnalyzer()
        app.live_quality = LiveQualityTracker()
        app.quality_summary_var = FakeVar()
        app.performance_conclusion_var = FakeVar()
        app.quality_var = FakeVar()
        app.quality_mode_var = FakeVar()
        app.smoothing_var = FakeVar()
        app.stabilizer = SpyStabilizer()
        app.graph_last_elapsed = 0.0
        app.session_var = FakeVar()
        app.cards = {
            "fps": FakeCard(),
            "jank_percent": FakeCard(),
            "cpu_percent": FakeCard(),
            "memory_mb": FakeCard(),
            "temperature_c": FakeCard(),
            "power_w": FakeCard(),
            "rx_kbps": FakeCard(),
            "tx_kbps": FakeCard(),
        }
        app.graphs = {key: FakeGraph() for key in app.cards}
        app._refresh_graph_time_axis = lambda: None
        app._format_elapsed = lambda elapsed: f"{elapsed:.1f}s"
        app._append_quality_event = lambda _sample: None
        app._refresh_proxy_traffic = lambda: None

        App._handle_sample(app, PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0, cpu_percent=20.0))
        App._handle_sample(
            app,
            PerfSample(timestamp=2.0, elapsed=2.0, fps=0.0, cpu_percent=95.0, note="Android FPS 当前无帧增量"),
        )

        self.assertGreater(app.stabilizer.outputs[-1].fps, 0.0)
        self.assertEqual(app.graphs["fps"].points[-1], (2.0, 0.0, "limited"))
        self.assertEqual(app.recorder.samples[-1].fps, 0.0)


class CollectionDiagnosticStatusRowsTest(unittest.TestCase):
    def test_marks_all_android_collection_links_as_ok_when_sources_are_healthy(self) -> None:
        diagnostics = AndroidCollectionDiagnostics(
            overall_state="ok",
            summary="Android 采集自检通过",
            rows=[
                ("前台", "匹配", "当前前台 com.example.game"),
                ("PID", "已找到", "pidof: 101, 202"),
                ("UID", "已找到", "dumpsys package: 10234"),
                ("FPS", "可用", "gfxinfo counters"),
                ("网络", "per-UID", "目标 App 独占上下行"),
            ],
            foreground_state="ok",
            pid_source="pidof",
            pids=[101, 202],
            uid_source="dumpsys package",
            uid=10234,
            fps_source="gfxinfo counters",
            network_source="per-UID",
        )

        rows = collection_diagnostic_status_rows(diagnostics)

        self.assertEqual(rows[0], ("前台", "正常", "当前前台 com.example.game", "ok"))
        self.assertEqual(rows[1], ("PID", "正常", "pidof: 101, 202", "ok"))
        self.assertEqual(rows[2], ("UID", "正常", "dumpsys package: 10234", "ok"))
        self.assertEqual(rows[3], ("FPS", "正常", "gfxinfo counters", "ok"))
        self.assertEqual(rows[4], ("网络", "正常", "目标 App 独占上下行", "ok"))

    def test_marks_fallback_and_missing_android_collection_links_as_warnings(self) -> None:
        diagnostics = AndroidCollectionDiagnostics(
            overall_state="warning",
            summary="Android 采集自检发现 4 项风险",
            rows=[
                ("前台", "前台不一致", "当前前台 com.example.home"),
                ("PID", "未找到", "App 可能未运行"),
                ("UID", "未找到", "上下行网络无法按 App 统计"),
                ("FPS", "不可用", "未发现帧数据"),
                ("网络", "设备级兜底", "非目标 App 独占流量"),
            ],
            foreground_state="mismatch",
            pid_source="missing",
            uid_source="missing",
            fps_source="missing",
            network_source="device",
        )

        rows = collection_diagnostic_status_rows(diagnostics)

        self.assertEqual(rows[0], ("前台", "异常", "当前前台 com.example.home", "issue"))
        self.assertEqual(rows[1], ("PID", "异常", "App 可能未运行", "issue"))
        self.assertEqual(rows[2], ("UID", "异常", "上下行网络无法按 App 统计", "issue"))
        self.assertEqual(rows[3], ("FPS", "异常", "未发现帧数据", "issue"))
        self.assertEqual(rows[4], ("网络", "兜底", "非目标 App 独占流量", "fallback"))


if __name__ == "__main__":
    unittest.main()
