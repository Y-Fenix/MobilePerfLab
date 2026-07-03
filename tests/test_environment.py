import queue
import threading
import unittest
from pathlib import Path

from mobileperflab import (
    AndroidAdapter,
    AndroidCollectionDiagnostics,
    App,
    WORKBENCH_SHELL_REGIONS,
    collection_diagnostic_status_rows,
    DeviceInfo,
    build_environment_checks,
    format_environment_checks,
    format_graph_view_height,
    format_quality_mode_label,
    format_workbench_status_chip,
    graph_diagnostic_summary_text,
    graph_quality_badge_text,
    graph_quality_badge_text_for_context,
    graph_latest_display_value_for_context,
    graph_display_max_value,
    graph_display_series,
    graph_display_series_for_context,
    graph_summary_text,
    graph_scroll_row_step,
    graph_visible_rows_for_height,
    ios_service_launch_plan,
    live_recent_window_summary,
    LiveQualityTracker,
    MetricHealthAnalyzer,
    MetricStabilizer,
    metric_graph_layout,
    PerfSample,
    recommended_sampling_interval_button_text,
    SAMPLING_INTERVAL_OPTIONS,
    smooth_graph_series,
    workbench_primary_metric_order,
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
        self.assertIn("iOS采集服务", ios.action)
        self.assertNotIn("启动iOS采集服务.command", ios.action)

    def test_ios_environment_action_stays_inside_app_when_pymobiledevice3_is_missing(self) -> None:
        checks = build_environment_checks(
            {
                "python": "/usr/bin/python3",
                "adb": "/opt/android/adb",
                "pymobiledevice3": "",
                "xcrun": "/usr/bin/xcrun",
            }
        )

        ios = next(check for check in checks if check.key == "pymobiledevice3")

        self.assertEqual(ios.state, "missing")
        self.assertIn("安装iOS依赖.command", ios.action)
        self.assertIn("iOS采集服务", ios.action)
        self.assertNotIn("双击", ios.action)
        self.assertNotIn("启动iOS采集服务.command", ios.action)

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


class IOSServiceLaunchTest(unittest.TestCase):
    def test_ios_service_launch_plan_uses_background_noninteractive_command_on_macos(self) -> None:
        plan = ios_service_launch_plan("/tmp/pymobiledevice3", platform="darwin")

        self.assertEqual(plan.state, "ready")
        self.assertEqual(
            plan.command,
            ["sudo", "-n", "/tmp/pymobiledevice3", "remote", "tunneld", "--protocol", "tcp"],
        )
        self.assertNotIn("open", plan.command)
        self.assertIn("不打开额外终端窗口", plan.detail)

    def test_ios_service_launch_plan_guides_dependency_install_when_tool_is_missing(self) -> None:
        plan = ios_service_launch_plan("", platform="darwin")

        self.assertEqual(plan.state, "missing_dependency")
        self.assertEqual(plan.command, [])
        self.assertIn("安装iOS依赖.command", plan.action)

    def test_start_ios_service_runs_silently_and_updates_ui_state(self) -> None:
        import mobileperflab

        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

        class FakeIOS:
            pymobiledevice3 = "/tmp/pymobiledevice3"

        class FakeProcess:
            def poll(self) -> None:
                return None

        calls: list[dict[str, object]] = []

        def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
            calls.append({"command": command, **kwargs})
            return FakeProcess()

        app = object.__new__(App)
        app.ios = FakeIOS()
        app.status_var = FakeVar()
        app.app_hint_var = FakeVar()
        app.capability_var = FakeVar()
        app.logs: list[str] = []
        app.ios_service_process = None
        app.append_log = lambda text: app.logs.append(text)
        app._refresh_session_chips = lambda: None
        app._ios_service_log_path = lambda: Path("/tmp/mobileperflab-ios-service-test.log")
        app._schedule_ios_service_startup_check = lambda _process, _log_path, _action: None

        original_popen = mobileperflab.subprocess.Popen
        mobileperflab.subprocess.Popen = fake_popen
        try:
            App.start_ios_service(app)
        finally:
            mobileperflab.subprocess.Popen = original_popen

        self.assertEqual(calls[0]["command"][:3], ["sudo", "-n", "/tmp/pymobiledevice3"])
        self.assertIs(calls[0]["stdin"], mobileperflab.subprocess.DEVNULL)
        self.assertTrue(calls[0]["start_new_session"])
        self.assertIn("后台启动中", app.status_var.value)
        self.assertIn("静默尝试启动", app.app_hint_var.value)
        self.assertTrue(any("iOS 采集服务后台启动中" in line for line in app.logs))

    def test_start_ios_service_schedules_quick_failure_check(self) -> None:
        import mobileperflab

        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

        class FakeIOS:
            pymobiledevice3 = "/tmp/pymobiledevice3"

        class FakeProcess:
            pass

        class FakeRoot:
            def __init__(self) -> None:
                self.after_calls: list[tuple[int, object]] = []

            def after(self, delay_ms: int, callback: object) -> None:
                self.after_calls.append((delay_ms, callback))

        def fake_popen(_command: list[str], **_kwargs: object) -> FakeProcess:
            return FakeProcess()

        app = object.__new__(App)
        app.root = FakeRoot()
        app.ios = FakeIOS()
        app.status_var = FakeVar()
        app.app_hint_var = FakeVar()
        app.capability_var = FakeVar()
        app.logs: list[str] = []
        app.append_log = lambda text: app.logs.append(text)
        app._refresh_session_chips = lambda: None
        app._ios_service_log_path = lambda: Path("/tmp/mobileperflab-ios-service-test.log")

        original_popen = mobileperflab.subprocess.Popen
        mobileperflab.subprocess.Popen = fake_popen
        try:
            App.start_ios_service(app)
        finally:
            mobileperflab.subprocess.Popen = original_popen

        self.assertEqual(app.root.after_calls[0][0], 1200)

    def test_ios_service_startup_check_surfaces_fast_sudo_failure(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

        class FailedProcess:
            def poll(self) -> int:
                return 1

        app = object.__new__(App)
        app.ios_service_process = FailedProcess()
        app.status_var = FakeVar()
        app.app_hint_var = FakeVar()
        app.logs: list[str] = []
        app.append_log = lambda text: app.logs.append(text)
        app._refresh_session_chips = lambda: None

        App._check_ios_service_startup_result(
            app,
            app.ios_service_process,
            Path("/tmp/ios-service.log"),
            "请先完成 sudo 授权。",
        )

        self.assertIsNone(app.ios_service_process)
        self.assertIn("启动失败", app.status_var.value)
        self.assertIn("sudo 授权", app.app_hint_var.value)
        self.assertTrue(any("快速退出" in line for line in app.logs))

    def test_ios_service_startup_check_marks_running_process_ready(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

        class RunningProcess:
            def poll(self) -> None:
                return None

        app = object.__new__(App)
        app.ios_service_process = RunningProcess()
        app.status_var = FakeVar()
        app.app_hint_var = FakeVar()
        app.logs: list[str] = []
        app.append_log = lambda text: app.logs.append(text)
        app._refresh_session_chips = lambda: None

        App._check_ios_service_startup_result(
            app,
            app.ios_service_process,
            Path("/tmp/ios-service.log"),
            "请先完成 sudo 授权。",
        )

        self.assertIn("运行中", app.status_var.value)
        self.assertIn("已在后台运行", app.app_hint_var.value)
        self.assertTrue(any("已在后台运行" in line for line in app.logs))

    def test_ios_runtime_guidance_does_not_send_users_to_keep_extra_script_window_open(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")

        self.assertNotIn("双击“启动iOS采集服务.command”并保持窗口打开", text)
        self.assertNotIn("双击“启动iOS采集服务.command”并输入电脑密码，保持窗口打开", text)
        self.assertNotIn("iOS采集服务”窗口已启动并保持打开", text)
        self.assertIn("点击 iOS采集服务", text)
        self.assertIn("静默尝试启动", text)

    def test_exit_cleanup_stops_only_ios_service_process_started_by_app(self) -> None:
        class FakeProcess:
            def __init__(self) -> None:
                self.terminated = False
                self.killed = False

            def poll(self) -> None:
                return None

            def terminate(self) -> None:
                self.terminated = True

            def wait(self, timeout: float) -> None:
                raise TimeoutError("still running")

            def kill(self) -> None:
                self.killed = True

        process = FakeProcess()
        app = object.__new__(App)
        app.ios_service_process = process
        app.logs: list[str] = []
        app.append_log = lambda text: app.logs.append(text)

        App._cleanup_ios_service_process(app)

        self.assertTrue(process.terminated)
        self.assertTrue(process.killed)
        self.assertIsNone(app.ios_service_process)
        self.assertIn("退出前已停止 iOS 采集服务后台进程。", app.logs)

    def test_exit_cleanup_ignores_missing_or_already_stopped_ios_service_process(self) -> None:
        class StoppedProcess:
            def __init__(self) -> None:
                self.terminated = False

            def poll(self) -> int:
                return 0

            def terminate(self) -> None:
                self.terminated = True

        process = StoppedProcess()
        app = object.__new__(App)
        app.ios_service_process = process
        app.logs: list[str] = []
        app.append_log = lambda text: app.logs.append(text)

        App._cleanup_ios_service_process(app)

        self.assertFalse(process.terminated)
        self.assertIsNone(app.ios_service_process)
        self.assertEqual(app.logs, [])

    def test_start_sampling_auto_ensures_ios_service_before_sampler_starts(self) -> None:
        import mobileperflab

        class FakeVar:
            def __init__(self, value: str = "") -> None:
                self.value = value

            def get(self) -> str:
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        class FakeRecorder:
            def __init__(self) -> None:
                self.logs: list[str] = []

            def set_expected_interval(self, _interval: float) -> None:
                pass

            def reset(self, _device: DeviceInfo, _app_id: str) -> None:
                pass

            def log(self, text: str) -> None:
                self.logs.append(text)

        class FakeLiveQuality:
            def set_expected_interval(self, _interval: float) -> None:
                pass

        class FakeButton:
            def configure(self, **_kwargs: object) -> None:
                pass

        class FakeSampler:
            instances: list["FakeSampler"] = []

            def __init__(self, *_args: object) -> None:
                self.started = False
                FakeSampler.instances.append(self)

            def start(self) -> None:
                self.started = True

        app = object.__new__(App)
        app.sampler = None
        app.selected_device = DeviceInfo("iOS", "ios-1", "iPhone", "18", "iPhone", "ready")
        app.ios = object()
        app.adapter_for = lambda _device: app.ios
        app.app_var = FakeVar("com.example.game")
        app.interval_var = FakeVar("1.0")
        app.smoothing_var = FakeVar("1")
        app.recorder = FakeRecorder()
        app.live_quality = FakeLiveQuality()
        app.events = object()
        app.last_notes = set()
        app.start_button = FakeButton()
        app.stop_button = FakeButton()
        app.status_var = FakeVar()
        app.session_var = FakeVar()
        app.logs: list[str] = []
        app._reset_metrics = lambda: None
        app.stabilizer = type("FakeStabilizer", (), {"reset": lambda self: None})()
        app._refresh_session_chips = lambda: None
        app.append_log = lambda text: app.logs.append(text)
        app.ensure_calls = 0
        app._ensure_ios_service_for_sampling = lambda: setattr(app, "ensure_calls", app.ensure_calls + 1)

        original_sampler = mobileperflab.SamplerThread
        mobileperflab.SamplerThread = FakeSampler
        try:
            App.start_sampling(app)
        finally:
            mobileperflab.SamplerThread = original_sampler

        self.assertEqual(app.ensure_calls, 1)
        self.assertTrue(FakeSampler.instances[-1].started)

    def test_ensure_ios_service_for_sampling_reuses_live_background_process(self) -> None:
        class FakeProcess:
            def poll(self) -> None:
                return None

        app = object.__new__(App)
        app.ios_service_process = FakeProcess()
        app.logs: list[str] = []
        app.append_log = lambda text: app.logs.append(text)
        app.start_ios_service = lambda: (_ for _ in ()).throw(AssertionError("should not restart"))

        App._ensure_ios_service_for_sampling(app)

        self.assertIn("iOS 采集服务已自动检查：后台服务正在运行。", app.logs)

    def test_ensure_ios_service_for_sampling_silently_starts_when_missing(self) -> None:
        app = object.__new__(App)
        app.ios_service_process = None
        app.logs: list[str] = []
        app.start_calls = 0
        app.append_log = lambda text: app.logs.append(text)
        app.start_ios_service = lambda: setattr(app, "start_calls", app.start_calls + 1)

        App._ensure_ios_service_for_sampling(app)

        self.assertEqual(app.start_calls, 1)
        self.assertIn("iOS 采集服务已自动检查：已尝试静默启动。", app.logs)

    def test_start_sampling_does_not_auto_start_ios_service_for_android(self) -> None:
        class FakeVar:
            def __init__(self, value: str = "") -> None:
                self.value = value

            def get(self) -> str:
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        app = object.__new__(App)
        app.sampler = object()
        app.selected_device = DeviceInfo("Android", "android-1", "Pixel", "14", "Pixel", "ready")
        app.ensure_calls = 0
        app._ensure_ios_service_for_sampling = lambda: setattr(app, "ensure_calls", app.ensure_calls + 1)

        App.start_sampling(app)

        self.assertEqual(app.ensure_calls, 0)

    def test_start_sampling_runs_android_preflight_in_background_after_sampler_starts(self) -> None:
        import mobileperflab

        class FakeVar:
            def __init__(self, value: str = "") -> None:
                self.value = value

            def get(self) -> str:
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        class FakeRecorder:
            def __init__(self) -> None:
                self.logs: list[str] = []

            def set_expected_interval(self, _interval: float) -> None:
                pass

            def reset(self, _device: DeviceInfo, _app_id: str) -> None:
                pass

            def log(self, text: str) -> None:
                self.logs.append(text)

        class FakeLiveQuality:
            def set_expected_interval(self, _interval: float) -> None:
                pass

        class FakeButton:
            def configure(self, **_kwargs: object) -> None:
                pass

        class FakeSampler:
            instances: list["FakeSampler"] = []

            def __init__(self, *_args: object) -> None:
                self.started = False
                FakeSampler.instances.append(self)

            def start(self) -> None:
                self.started = True

        class FakeAdapter(AndroidAdapter):
            def collection_diagnostics(self, _device: DeviceInfo, _app_id: str) -> AndroidCollectionDiagnostics:
                started.set()
                release.wait(2.0)
                return AndroidCollectionDiagnostics("ok", "Android 采集自检通过", [])

        started = threading.Event()
        release = threading.Event()
        app = object.__new__(App)
        app.sampler = None
        app.selected_device = DeviceInfo("Android", "android-1", "Pixel", "14", "Pixel", "ready")
        app.android = FakeAdapter()
        app.adapter_for = lambda _device: app.android
        app.app_var = FakeVar("com.example.game")
        app.app_hint_var = FakeVar()
        app.interval_var = FakeVar("1.0")
        app.smoothing_var = FakeVar("1")
        app.recorder = FakeRecorder()
        app.live_quality = FakeLiveQuality()
        app.events = queue.Queue()
        app.last_notes = set()
        app.start_button = FakeButton()
        app.stop_button = FakeButton()
        app.status_var = FakeVar()
        app.session_var = FakeVar()
        app.logs: list[str] = []
        app._reset_metrics = lambda: None
        app.stabilizer = type("FakeStabilizer", (), {"reset": lambda self: None})()
        app._refresh_session_chips = lambda: None
        app.append_log = lambda text: app.logs.append(text)
        app.app_task_thread = None
        app.app_task_generation = 0

        original_sampler = mobileperflab.SamplerThread
        mobileperflab.SamplerThread = FakeSampler
        try:
            App.start_sampling(app)
        finally:
            mobileperflab.SamplerThread = original_sampler

        self.assertTrue(FakeSampler.instances[-1].started)
        self.assertEqual(app.status_var.value, "采集中")
        self.assertTrue(started.wait(0.5))
        self.assertTrue(app.events.empty())
        release.set()
        app.app_task_thread.join(1.0)
        self.assertFalse(app.events.empty())

    def test_start_sampling_brings_selected_android_app_to_foreground_before_sampler_starts(self) -> None:
        import mobileperflab

        class FakeVar:
            def __init__(self, value: str = "") -> None:
                self.value = value

            def get(self) -> str:
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        class FakeRecorder:
            def __init__(self) -> None:
                self.logs: list[str] = []

            def set_expected_interval(self, _interval: float) -> None:
                pass

            def reset(self, _device: DeviceInfo, _app_id: str) -> None:
                pass

            def log(self, text: str) -> None:
                self.logs.append(text)

        class FakeLiveQuality:
            def set_expected_interval(self, _interval: float) -> None:
                pass

        class FakeButton:
            def configure(self, **_kwargs: object) -> None:
                pass

        class FakeSampler:
            instances: list["FakeSampler"] = []

            def __init__(self, *_args: object) -> None:
                self.started = False
                FakeSampler.instances.append(self)

            def start(self) -> None:
                self.started = True

        class LaunchingAdapter(AndroidAdapter):
            def __init__(self) -> None:
                super().__init__()
                self.ensure_calls: list[tuple[DeviceInfo, str]] = []

            def ensure_target_app_foreground(self, device: DeviceInfo, app_id: str) -> tuple[bool, str]:
                self.ensure_calls.append((device, app_id))
                return True, app_id

            def collection_diagnostics(self, _device: DeviceInfo, _app_id: str) -> AndroidCollectionDiagnostics:
                return AndroidCollectionDiagnostics("ok", "Android 采集自检通过", [])

        adapter = LaunchingAdapter()
        app = object.__new__(App)
        app.sampler = None
        app.selected_device = DeviceInfo("Android", "android-1", "Pixel", "14", "Pixel", "ready")
        app.android = adapter
        app.adapter_for = lambda _device: app.android
        app.app_var = FakeVar("com.example.game")
        app.app_hint_var = FakeVar()
        app.interval_var = FakeVar("1.0")
        app.smoothing_var = FakeVar("1")
        app.recorder = FakeRecorder()
        app.live_quality = FakeLiveQuality()
        app.events = queue.Queue()
        app.last_notes = set()
        app.start_button = FakeButton()
        app.stop_button = FakeButton()
        app.status_var = FakeVar()
        app.session_var = FakeVar()
        app.logs: list[str] = []
        app._reset_metrics = lambda: None
        app.stabilizer = type("FakeStabilizer", (), {"reset": lambda self: None})()
        app._refresh_session_chips = lambda: None
        app.append_log = lambda text: app.logs.append(text)
        app.app_task_thread = None
        app.app_task_generation = 0

        original_sampler = mobileperflab.SamplerThread
        mobileperflab.SamplerThread = FakeSampler
        try:
            App.start_sampling(app)
        finally:
            mobileperflab.SamplerThread = original_sampler

        self.assertEqual(adapter.ensure_calls, [(app.selected_device, "com.example.game")])
        self.assertTrue(FakeSampler.instances[-1].started)
        self.assertTrue(any("已尝试拉起目标应用" in line for line in app.logs))

    def test_start_sampling_stops_when_selected_android_app_cannot_enter_foreground(self) -> None:
        import mobileperflab

        class FakeVar:
            def __init__(self, value: str = "") -> None:
                self.value = value

            def get(self) -> str:
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        class FakeRecorder:
            def set_expected_interval(self, _interval: float) -> None:
                pass

            def reset(self, _device: DeviceInfo, _app_id: str) -> None:
                pass

            def log(self, _text: str) -> None:
                pass

        class FakeLiveQuality:
            def set_expected_interval(self, _interval: float) -> None:
                pass

        class FakeButton:
            def configure(self, **_kwargs: object) -> None:
                pass

        class FakeSampler:
            instances: list["FakeSampler"] = []

            def __init__(self, *_args: object) -> None:
                FakeSampler.instances.append(self)

            def start(self) -> None:
                pass

        class BlockedLaunchAdapter(AndroidAdapter):
            def ensure_target_app_foreground(self, _device: DeviceInfo, _app_id: str) -> tuple[bool, str]:
                return True, "com.example.home"

        app = object.__new__(App)
        app.sampler = None
        app.selected_device = DeviceInfo("Android", "android-1", "Pixel", "14", "Pixel", "ready")
        app.android = BlockedLaunchAdapter()
        app.adapter_for = lambda _device: app.android
        app.app_var = FakeVar("com.example.game")
        app.app_hint_var = FakeVar()
        app.interval_var = FakeVar("1.0")
        app.smoothing_var = FakeVar("1")
        app.recorder = FakeRecorder()
        app.live_quality = FakeLiveQuality()
        app.events = queue.Queue()
        app.last_notes = set()
        app.start_button = FakeButton()
        app.stop_button = FakeButton()
        app.status_var = FakeVar()
        app.session_var = FakeVar()
        app.logs: list[str] = []
        app._reset_metrics = lambda: None
        app.stabilizer = type("FakeStabilizer", (), {"reset": lambda self: None})()
        app._refresh_session_chips = lambda: None
        app.append_log = lambda text: app.logs.append(text)
        app.app_task_thread = None
        app.app_task_generation = 0

        original_sampler = mobileperflab.SamplerThread
        mobileperflab.SamplerThread = FakeSampler
        try:
            App.start_sampling(app)
        finally:
            mobileperflab.SamplerThread = original_sampler

        self.assertEqual(FakeSampler.instances, [])
        self.assertEqual(app.status_var.value, "目标应用未在前台")
        self.assertIn("当前前台为 com.example.home", app.app_hint_var.value)

    def test_start_sampling_stops_when_android_screen_is_locked(self) -> None:
        import mobileperflab

        class FakeVar:
            def __init__(self, value: str = "") -> None:
                self.value = value

            def get(self) -> str:
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        class FakeRecorder:
            def set_expected_interval(self, _interval: float) -> None:
                pass

            def reset(self, _device: DeviceInfo, _app_id: str) -> None:
                pass

            def log(self, _text: str) -> None:
                pass

        class FakeLiveQuality:
            def set_expected_interval(self, _interval: float) -> None:
                pass

        class FakeButton:
            def configure(self, **_kwargs: object) -> None:
                pass

        class FakeSampler:
            instances: list["FakeSampler"] = []

            def __init__(self, *_args: object) -> None:
                FakeSampler.instances.append(self)

            def start(self) -> None:
                pass

        class LockedAdapter(AndroidAdapter):
            def ensure_device_ready_for_sampling(self, _device: DeviceInfo) -> tuple[bool, str]:
                return False, "设备仍处于锁屏或息屏状态"

        app = object.__new__(App)
        app.sampler = None
        app.selected_device = DeviceInfo("Android", "android-1", "Pixel", "14", "Pixel", "ready")
        app.android = LockedAdapter()
        app.adapter_for = lambda _device: app.android
        app.app_var = FakeVar("com.example.game")
        app.app_hint_var = FakeVar()
        app.interval_var = FakeVar("1.0")
        app.smoothing_var = FakeVar("1")
        app.recorder = FakeRecorder()
        app.live_quality = FakeLiveQuality()
        app.events = queue.Queue()
        app.last_notes = set()
        app.start_button = FakeButton()
        app.stop_button = FakeButton()
        app.status_var = FakeVar()
        app.session_var = FakeVar()
        app.logs: list[str] = []
        app._reset_metrics = lambda: None
        app.stabilizer = type("FakeStabilizer", (), {"reset": lambda self: None})()
        app._refresh_session_chips = lambda: None
        app.append_log = lambda text: app.logs.append(text)
        app.app_task_thread = None
        app.app_task_generation = 0

        original_sampler = mobileperflab.SamplerThread
        mobileperflab.SamplerThread = FakeSampler
        try:
            App.start_sampling(app)
        finally:
            mobileperflab.SamplerThread = original_sampler

        self.assertEqual(FakeSampler.instances, [])
        self.assertEqual(app.status_var.value, "设备未解锁")
        self.assertIn("锁屏", app.app_hint_var.value)


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

    def test_main_keeps_app_instance_attached_to_root(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")

        self.assertIn("root._mobileperflab_app = App(root)", text)

    def test_app_init_defers_device_refresh_until_after_first_paint(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")
        init_start = text.index("def __init__(self, root: tk.Tk) -> None:")
        init_end = text.index("    @staticmethod", init_start)
        init_body = text[init_start:init_end]

        self.assertIn("self._schedule_startup_refresh()", init_body)
        self.assertNotIn("self.refresh_devices()", init_body)
        self.assertLess(init_body.index("self._build_ui()"), init_body.index("self._schedule_startup_refresh()"))

    def test_startup_refresh_logs_environment_then_refreshes_devices(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")

        self.assertIn("def _schedule_startup_refresh(self) -> None:", text)
        self.assertIn('self.status_var.set("正在识别设备...")', text)
        self.assertIn("self.root.after(300, self._startup_refresh_devices)", text)
        self.assertIn("def _startup_refresh_devices(self) -> None:", text)
        self.assertIn("self._log_environment_checks()", text)
        self.assertIn("self.refresh_devices()", text)

    def test_refresh_devices_runs_discovery_in_background(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

        class FakeAdapter:
            platform_name = "Android"

            def __init__(self) -> None:
                self.called = False

            def list_devices(self) -> list[DeviceInfo]:
                self.called = True
                started.set()
                release.wait(2.0)
                return [DeviceInfo("Android", "serial-1", "Pixel", "15", "Pixel", "ready")]

        started = threading.Event()
        release = threading.Event()
        app = object.__new__(App)
        app.status_var = FakeVar()
        app.session_chip_vars = {}
        app.events = queue.Queue()
        app.android = FakeAdapter()
        app.ios = FakeAdapter()
        app.device_refresh_thread = None
        app.device_refresh_generation = 0
        app.devices = [DeviceInfo("Demo", "demo", "Demo", "-", "-", "ready", "演示数据")]
        app._refresh_session_chips = lambda: None

        App.refresh_devices(app)

        self.assertEqual(app.status_var.value, "正在刷新设备...")
        self.assertTrue(started.wait(0.5))
        self.assertEqual(app.devices[0].serial, "demo")
        self.assertTrue(app.events.empty())
        release.set()
        app.device_refresh_thread.join(1.0)
        self.assertFalse(app.events.empty())

    def test_device_refresh_result_event_updates_device_list(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

        device = DeviceInfo("Android", "serial-1", "Pixel", "15", "Pixel", "ready")
        app = object.__new__(App)
        app.devices = []
        app.status_var = FakeVar()
        app.capability_var = FakeVar()
        app.device_refresh_generation = 1
        app.render_calls = 0
        app._render_devices = lambda: setattr(app, "render_calls", app.render_calls + 1)
        app._capability_text = lambda: "capabilities"
        app._refresh_session_chips = lambda: None

        App._handle_device_refresh_result(app, {"generation": 1, "devices": [device], "errors": []})

        self.assertEqual(app.devices, [device])
        self.assertEqual(app.status_var.value, "检测到 1 台设备")
        self.assertEqual(app.capability_var.value, "capabilities")
        self.assertEqual(app.render_calls, 1)

    def test_selecting_device_clears_previous_target_app(self) -> None:
        class FakeVar:
            def __init__(self, value: str = "") -> None:
                self.value = value

            def set(self, value: str) -> None:
                self.value = value

            def get(self) -> str:
                return self.value

        class FakeTree:
            def selection(self) -> tuple[str, ...]:
                return ("1",)

        class FakeList:
            def __init__(self) -> None:
                self.deleted = False

            def delete(self, _start: object, _end: object = None) -> None:
                self.deleted = True

        app = object.__new__(App)
        app.devices = [
            DeviceInfo("Android", "serial-1", "Pixel", "15", "Pixel", "ready"),
            DeviceInfo("Android", "serial-2", "Galaxy", "16", "Galaxy", "ready"),
        ]
        app.device_tree = FakeTree()
        app.selected_device = app.devices[0]
        app.app_task_generation = 0
        app.device_var = FakeVar()
        app.app_var = FakeVar("com.old.game")
        app.app_picker_var = FakeVar("com.old.game")
        app.app_hint_var = FakeVar()
        app.status_var = FakeVar()
        app.app_list = FakeList()
        app._refresh_proxy_preview = lambda: None
        app._refresh_weak_diagnostics = lambda: None
        app._refresh_session_chips = lambda: None

        App._on_device_selected(app)

        self.assertEqual(app.selected_device, app.devices[1])
        self.assertEqual(app.app_var.value, "")
        self.assertEqual(app.app_picker_var.value, "")
        self.assertTrue(app.app_list.deleted)

    def test_stale_device_refresh_result_does_not_override_demo_mode(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

            def get(self) -> str:
                return self.value

        demo_device = DeviceInfo("Demo", "demo", "Demo", "-", "-", "ready", "演示数据")
        real_device = DeviceInfo("Android", "serial-1", "Pixel", "15", "Pixel", "ready")
        app = object.__new__(App)
        app.devices = [demo_device]
        app.status_var = FakeVar()
        app.capability_var = FakeVar()
        app.device_refresh_generation = 2
        app.render_calls = 0
        app._render_devices = lambda: setattr(app, "render_calls", app.render_calls + 1)
        app._capability_text = lambda: "capabilities"
        app._refresh_session_chips = lambda: None

        App._handle_device_refresh_result(app, {"generation": 1, "devices": [real_device], "errors": []})

        self.assertEqual(app.devices, [demo_device])
        self.assertEqual(app.render_calls, 0)

    def test_refresh_apps_runs_list_apps_in_background(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

        class FakeAdapter:
            def list_apps(self, _device: DeviceInfo) -> list[str]:
                started.set()
                release.wait(2.0)
                return ["com.example.game"]

        started = threading.Event()
        release = threading.Event()
        app = object.__new__(App)
        app.selected_device = DeviceInfo("Android", "serial-1", "Pixel", "15", "Pixel", "ready")
        app.app_hint_var = FakeVar()
        app.events = queue.Queue()
        app.app_task_thread = None
        app.app_task_generation = 0
        app.adapter_for = lambda _device: FakeAdapter()
        app._refresh_session_chips = lambda: None

        App.refresh_apps(app)

        self.assertEqual(app.app_hint_var.value, "正在读取应用列表...")
        self.assertTrue(started.wait(0.5))
        self.assertTrue(app.events.empty())
        release.set()
        app.app_task_thread.join(1.0)
        self.assertFalse(app.events.empty())

    def test_detect_foreground_app_runs_lookup_in_background(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

        class FakeAdapter:
            def foreground_app(self, _device: DeviceInfo) -> str:
                started.set()
                release.wait(2.0)
                return "com.example.game"

        started = threading.Event()
        release = threading.Event()
        app = object.__new__(App)
        app.selected_device = DeviceInfo("Android", "serial-1", "Pixel", "15", "Pixel", "ready")
        app.app_hint_var = FakeVar()
        app.events = queue.Queue()
        app.app_task_thread = None
        app.app_task_generation = 0
        app.adapter_for = lambda _device: FakeAdapter()
        app._refresh_session_chips = lambda: None

        App.detect_foreground_app(app)

        self.assertEqual(app.app_hint_var.value, "正在识别前台应用...")
        self.assertTrue(started.wait(0.5))
        self.assertTrue(app.events.empty())
        release.set()
        app.app_task_thread.join(1.0)
        self.assertFalse(app.events.empty())

    def test_app_list_result_event_updates_list_without_blocking_selection(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

        class FakeList:
            def __init__(self) -> None:
                self.items: list[str] = ["old"]

            def delete(self, _start: object, _end: object = None) -> None:
                self.items.clear()

            def insert(self, _index: object, value: str) -> None:
                self.items.append(value)

        class FakePicker:
            def __init__(self) -> None:
                self.values: tuple[str, ...] = ()

            def configure(self, **kwargs: object) -> None:
                self.values = tuple(kwargs.get("values", ()))

        app = object.__new__(App)
        app.app_hint_var = FakeVar()
        app.app_task_generation = 1
        app.app_list = FakeList()
        app.app_picker = FakePicker()
        app._refresh_session_chips = lambda: None

        App._handle_app_task_result(app, {"generation": 1, "kind": "list_apps", "apps": ["com.a", "com.b"], "error": ""})

        self.assertEqual(app.app_list.items, ["com.a", "com.b"])
        self.assertEqual(app.app_picker.values, ("com.a", "com.b"))
        self.assertEqual(app.app_hint_var.value, "已读取 2 个应用。")

    def test_app_list_result_keeps_all_apps_available_for_selection(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

        class FakeList:
            def __init__(self) -> None:
                self.items: list[str] = []

            def delete(self, _start: object, _end: object = None) -> None:
                self.items.clear()

            def insert(self, _index: object, value: str) -> None:
                self.items.append(value)

        class FakePicker:
            def __init__(self) -> None:
                self.values: tuple[str, ...] = ()

            def configure(self, **kwargs: object) -> None:
                self.values = tuple(kwargs.get("values", ()))

        apps = [f"com.example.app{index:03d}" for index in range(600)]
        app = object.__new__(App)
        app.app_hint_var = FakeVar()
        app.app_task_generation = 1
        app.app_list = FakeList()
        app.app_picker = FakePicker()
        app._refresh_session_chips = lambda: None

        App._handle_app_task_result(app, {"generation": 1, "kind": "list_apps", "apps": apps, "error": ""})

        self.assertEqual(len(app.app_list.items), 600)
        self.assertEqual(len(app.app_picker.values), 600)
        self.assertEqual(app.app_list.items[-1], "com.example.app599")
        self.assertEqual(app.app_picker.values[-1], "com.example.app599")

    def test_app_picker_selection_updates_target_app_var(self) -> None:
        class FakeVar:
            def __init__(self, value: str = "") -> None:
                self.value = value

            def get(self) -> str:
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        app = object.__new__(App)
        app.app_picker_var = FakeVar("com.example.game")
        app.app_var = FakeVar()
        app._refresh_session_chips = lambda: None

        App._on_app_picker_selected(app)

        self.assertEqual(app.app_var.value, "com.example.game")

    def test_app_list_selection_keeps_picker_and_target_app_in_sync(self) -> None:
        class FakeVar:
            def __init__(self, value: str = "") -> None:
                self.value = value

            def get(self) -> str:
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        class FakeList:
            def curselection(self) -> tuple[int, ...]:
                return (0,)

            def get(self, _index: int) -> str:
                return "com.example.game"

        app = object.__new__(App)
        app.app_list = FakeList()
        app.app_var = FakeVar()
        app.app_picker_var = FakeVar()
        app._refresh_session_chips = lambda: None

        App._on_app_selected(app)

        self.assertEqual(app.app_var.value, "com.example.game")
        self.assertEqual(app.app_picker_var.value, "com.example.game")

    def test_foreground_result_event_updates_app_var(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

        app = object.__new__(App)
        app.app_var = FakeVar()
        app.app_hint_var = FakeVar()
        app.app_task_generation = 1
        app._refresh_session_chips = lambda: None

        App._handle_app_task_result(app, {"generation": 1, "kind": "foreground", "app_id": "com.example.game", "error": ""})

        self.assertEqual(app.app_var.value, "com.example.game")
        self.assertEqual(app.app_hint_var.value, "前台应用：com.example.game")

    def test_stale_app_task_result_does_not_override_current_app(self) -> None:
        class FakeVar:
            def __init__(self, value: str = "") -> None:
                self.value = value

            def set(self, value: str) -> None:
                self.value = value

        app = object.__new__(App)
        app.app_var = FakeVar("com.current")
        app.app_hint_var = FakeVar()
        app.app_task_generation = 2
        app._refresh_session_chips = lambda: None

        App._handle_app_task_result(app, {"generation": 1, "kind": "foreground", "app_id": "com.old", "error": ""})

        self.assertEqual(app.app_var.value, "com.current")
        self.assertEqual(app.app_hint_var.value, "")

    def test_collection_diagnostics_runs_in_background(self) -> None:
        class FakeVar:
            def __init__(self, value: str = "") -> None:
                self.value = value

            def set(self, value: str) -> None:
                self.value = value

            def get(self) -> str:
                return self.value

        class FakeAdapter(AndroidAdapter):
            def collection_diagnostics(self, _device: DeviceInfo, _app_id: str) -> AndroidCollectionDiagnostics:
                started.set()
                release.wait(2.0)
                return AndroidCollectionDiagnostics("ok", "Android 采集自检通过", [])

        started = threading.Event()
        release = threading.Event()
        app = object.__new__(App)
        app.selected_device = DeviceInfo("Android", "serial-1", "Pixel", "15", "Pixel", "ready")
        app.app_var = FakeVar("com.example.game")
        app.app_hint_var = FakeVar()
        app.events = queue.Queue()
        app.app_task_thread = None
        app.app_task_generation = 0
        app.adapter_for = lambda _device: FakeAdapter()
        app._refresh_session_chips = lambda: None

        App.run_collection_diagnostics(app)

        self.assertEqual(app.app_hint_var.value, "正在执行采集自检...")
        self.assertTrue(started.wait(0.5))
        self.assertTrue(app.events.empty())
        release.set()
        app.app_task_thread.join(1.0)
        self.assertFalse(app.events.empty())

    def test_collection_diagnostics_result_updates_links_recorder_and_log(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

        class FakeRecorder:
            def __init__(self) -> None:
                self.diagnostics: AndroidCollectionDiagnostics | None = None

            def set_collection_diagnostics(self, diagnostics: AndroidCollectionDiagnostics) -> None:
                self.diagnostics = diagnostics

        diagnostics = AndroidCollectionDiagnostics("ok", "Android 采集自检通过", [])
        app = object.__new__(App)
        app.app_hint_var = FakeVar()
        app.app_task_generation = 1
        app.recorder = FakeRecorder()
        app.updated: AndroidCollectionDiagnostics | None = None
        app.logs: list[str] = []
        app._update_collection_links = lambda value: setattr(app, "updated", value)
        app.append_log = lambda value: app.logs.append(value)
        app._refresh_session_chips = lambda: None

        App._handle_app_task_result(
            app,
            {"generation": 1, "kind": "collection_diagnostics", "diagnostics": diagnostics, "error": ""},
        )

        self.assertEqual(app.app_hint_var.value, "Android 采集自检通过")
        self.assertIs(app.updated, diagnostics)
        self.assertIs(app.recorder.diagnostics, diagnostics)
        self.assertTrue(any("Android 采集自检通过" in line for line in app.logs))

    def test_ios_collection_diagnostics_guidance_returns_without_background_thread(self) -> None:
        class FakeVar:
            def __init__(self, value: str = "") -> None:
                self.value = value

            def set(self, value: str) -> None:
                self.value = value

            def get(self) -> str:
                return self.value

        app = object.__new__(App)
        app.selected_device = DeviceInfo("iOS", "ios-1", "iPhone", "18", "iPhone", "ready")
        app.app_var = FakeVar("com.example.ios")
        app.app_hint_var = FakeVar()
        app.app_task_thread = None
        app.adapter_for = lambda _device: object()
        app.logs: list[str] = []
        app.append_log = lambda value: app.logs.append(value)
        app._refresh_session_chips = lambda: None

        App.run_collection_diagnostics(app)

        self.assertIsNone(app.app_task_thread)
        self.assertEqual(app.app_hint_var.value, "iOS 采集服务状态请查看日志。")
        self.assertTrue(any("iOS 采集自检" in line for line in app.logs))


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

    def test_workbench_primary_metric_order_prioritizes_core_readability(self) -> None:
        self.assertEqual(
            workbench_primary_metric_order(),
            ["fps", "cpu_percent", "memory_mb", "rx_kbps", "tx_kbps", "jank_percent", "temperature_c", "power_w"],
        )

    def test_metric_graph_layout_uses_workbench_priority_for_first_four_graphs(self) -> None:
        layout = metric_graph_layout()

        self.assertEqual([item["key"] for item in layout[:4]], ["fps", "cpu_percent", "memory_mb", "rx_kbps"])

    def test_metric_cards_follow_workbench_priority_for_first_screen_readability(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")
        dashboard_start = text.index("def _build_dashboard")
        dashboard_end = text.index("def _build_metric_health_strip", dashboard_start)
        dashboard_body = text[dashboard_start:dashboard_end]

        self.assertIn("card_definitions", dashboard_body)
        self.assertIn("for key in workbench_primary_metric_order()", dashboard_body)
        self.assertNotIn("for index, card in enumerate(self.cards.values())", dashboard_body)

    def test_metric_health_strip_follows_workbench_priority_before_optional_battery(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")
        health_start = text.index("def _build_metric_health_strip")
        health_end = text.index("def _build_collection_link_strip", health_start)
        health_body = text[health_start:health_end]

        self.assertIn("health_labels", health_body)
        self.assertIn("for col, metric in enumerate((*workbench_primary_metric_order(), \"battery_percent\"))", health_body)
        self.assertNotIn('("jank_percent", "Jank"),\\n            ("cpu_percent", "CPU")', health_body)

    def test_weak_network_workspace_surfaces_three_step_path_and_status_lights(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")
        workspace_start = text.index("def _build_network_workspace")
        workspace_end = text.index("def _build_weak_diagnostic_rows", workspace_start)
        workspace_body = text[workspace_start:workspace_end]

        self.assertIn("3 步弱网测试", workspace_body)
        self.assertIn("选择预设", workspace_body)
        self.assertIn("启动并应用", workspace_body)
        self.assertIn("触发请求看命中", workspace_body)
        self.assertIn("self._build_weak_status_lights", workspace_body)

        self.assertIn("def _build_weak_status_lights", text)
        self.assertIn("weak_network_status_lights", text)
        for label in ("代理监听", "设备代理", "端口连通", "代理流量", "目标命中"):
            self.assertIn(label, text)

    def test_workbench_status_chip_keeps_short_labels_for_empty_state(self) -> None:
        self.assertEqual(format_workbench_status_chip("设备", ""), "设备：未选择")
        self.assertEqual(format_workbench_status_chip("弱网", "弱网 OFF · 未启动"), "弱网：OFF")

    def test_workbench_status_chip_truncates_long_operational_text(self) -> None:
        text = format_workbench_status_chip(
            "质量",
            "高可信 95.0% · 网络来源：目标 App per-UID · 窗口：稳定 · 趋势：平稳",
        )

        self.assertLessEqual(len(text), 28)
        self.assertEqual(text, "质量：高可信 95.0%")

    def test_app_build_ui_uses_four_workbench_regions(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")

        self.assertIn("self._build_session_bar(root_frame)", text)
        self.assertIn("self._build_control_rail(shell)", text)
        self.assertIn("self._build_observability_workspace(shell)", text)
        self.assertIn("self._build_diagnostics_rail(shell)", text)
        self.assertNotIn("self._build_header(root_frame)", text)
        self.assertNotIn("self._build_sidebar(body)", text)

    def test_session_bar_contains_status_chip_variables(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")

        self.assertIn("self.session_chip_vars", text)
        self.assertIn('format_workbench_status_chip("设备"', text)
        self.assertIn('format_workbench_status_chip("目标应用"', text)
        self.assertIn('format_workbench_status_chip("采集"', text)
        self.assertIn('format_workbench_status_chip("质量"', text)
        self.assertIn('format_workbench_status_chip("弱网"', text)

    def test_control_rail_renders_step_titles_without_long_help_text(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")
        control_start = text.index("def _build_control_rail")
        control_end = text.index("def _build_observability_workspace", control_start)
        control_body = text[control_start:control_end]

        self.assertIn("for step in workbench_sidebar_steps()", control_body)
        self.assertIn("StepTitle.TLabel", text)
        self.assertIn("StepDetail.TLabel", text)
        self.assertIn("1 连接设备", text)
        self.assertIn("2 选择应用", text)
        self.assertIn("3 采集自检", text)
        self.assertIn("4 开始采集", text)

    def test_control_rail_owns_sidebar_body_and_sidebar_is_compatibility_wrapper(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")
        control_start = text.index("def _build_control_rail")
        control_end = text.index("def _build_observability_workspace", control_start)
        control_body = text[control_start:control_end]
        sidebar_start = text.index("def _build_sidebar")
        sidebar_end = text.index("def refresh_recommended_sampling_interval_label", sidebar_start)
        sidebar_body = text[sidebar_start:sidebar_end]

        self.assertIn('ttk.Frame(master, style="Sidebar.TFrame"', control_body)
        self.assertIn("for step in workbench_sidebar_steps()", control_body)
        self.assertNotIn("self._build_sidebar(master)", control_body)
        self.assertIn("self._build_control_rail(master)", sidebar_body)

    def test_control_rail_keeps_target_app_picker_visible_with_stable_list_height(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")
        control_start = text.index("def _build_control_rail")
        control_end = text.index("def _build_observability_workspace", control_start)
        control_body = text[control_start:control_end]

        self.assertIn("目标应用", control_body)
        self.assertIn("command=self.refresh_apps", control_body)
        self.assertIn("self.app_list = tk.Listbox", control_body)
        self.assertIn("self.app_picker = ttk.Combobox", control_body)
        self.assertIn("command=self.refresh_apps", control_body)
        self.assertIn("app_panel.rowconfigure(4, weight=1, minsize=120)", control_body)
        self.assertIn('self.app_list.grid(row=4, column=0, sticky="nsew"', control_body)

    def test_diagnostics_rail_owns_quality_events_weak_status_and_logs(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")
        diagnostics_start = text.index("def _build_diagnostics_rail")
        diagnostics_end = text.index("def _build_header", diagnostics_start)
        diagnostics_body = text[diagnostics_start:diagnostics_end]

        self.assertIn("采集链路", diagnostics_body)
        self.assertIn("弱网状态", diagnostics_body)
        self.assertIn("质量事件", diagnostics_body)
        self.assertIn("日志", diagnostics_body)
        self.assertIn("self.collection_link_vars", diagnostics_body)
        self.assertIn("self.weak_live_summary_var", diagnostics_body)
        self.assertIn("self.quality_event_tree", diagnostics_body)
        self.assertIn("self.log_text", diagnostics_body)
        self.assertNotIn("将在这里汇总", diagnostics_body)

        dashboard_start = text.index("def _build_dashboard")
        dashboard_end_marker = "def _build_bottom_event_log_area"
        dashboard_end = (
            text.index(dashboard_end_marker, dashboard_start)
            if dashboard_end_marker in text[dashboard_start:]
            else text.index("def _build_metric_health_strip", dashboard_start)
        )
        dashboard_body = text[dashboard_start:dashboard_end]

        self.assertNotIn("self.quality_event_tree", dashboard_body)
        self.assertNotIn("self.log_text", dashboard_body)
        self.assertNotIn('text="质量事件"', dashboard_body)
        self.assertNotIn('text="日志"', dashboard_body)

    def test_diagnostics_rail_owns_event_marker_controls(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")
        diagnostics_start = text.index("def _build_diagnostics_rail")
        diagnostics_end = text.index("def _build_header", diagnostics_start)
        diagnostics_body = text[diagnostics_start:diagnostics_end]
        dashboard_start = text.index("def _build_dashboard")
        dashboard_end = text.index("def _build_metric_health_strip", dashboard_start)
        dashboard_body = text[dashboard_start:dashboard_end]

        self.assertIn("事件标记", diagnostics_body)
        self.assertIn("self.marker_var", diagnostics_body)
        self.assertIn("command=self.add_marker", diagnostics_body)
        self.assertIn("command=self.capture_screenshot", diagnostics_body)
        self.assertNotIn("事件标记", dashboard_body)
        self.assertNotIn("command=self.add_marker", dashboard_body)
        self.assertNotIn("command=self.capture_screenshot", dashboard_body)

    def test_diagnostics_rail_keeps_logs_fixed_below_quality_events(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")
        diagnostics_start = text.index("def _build_diagnostics_rail")
        diagnostics_end = text.index("def _build_header", diagnostics_start)
        diagnostics_body = text[diagnostics_start:diagnostics_end]
        log_start = diagnostics_body.index("self.log_text = tk.Text")
        log_end = diagnostics_body.index("self.log_text.grid", log_start)
        log_body = diagnostics_body[log_start:log_end]

        self.assertIn("rail.rowconfigure(4, weight=1)", diagnostics_body)
        self.assertNotIn("rail.rowconfigure(5, weight=1)", diagnostics_body)
        self.assertIn("height=6", log_body)
        self.assertNotIn("height=7", log_body)

    def test_workbench_styles_use_professional_neutral_palette(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")

        self.assertIn("#0F172A", text)
        self.assertIn("#F8FAFC", text)
        self.assertIn("StatusChip.TLabel", text)
        self.assertIn("StepTitle.TLabel", text)
        self.assertNotIn("#172235", text)


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

    def test_graph_summary_text_exposes_current_average_and_peak(self) -> None:
        self.assertEqual(
            graph_summary_text([(0.0, 10.0), (1.0, 20.0), (2.0, 30.0)], "KB/s"),
            "当前 30.0 KB/s · 均值 20.0 KB/s · 峰值 30.0 KB/s",
        )
        self.assertEqual(graph_summary_text([], "FPS"), "当前 -- · 均值 -- · 峰值 --")

    def test_graph_diagnostic_summary_text_explains_limited_and_fallback_points(self) -> None:
        self.assertEqual(
            graph_diagnostic_summary_text(
                "fps",
                [(0.0, 60.0, "ok"), (1.0, 0.0, "limited"), (2.0, 48.0, "fallback")],
                "FPS 来源可用但当前无新增帧，页面静止或低端机短采样窗口较常见",
            ),
            "受限 1 · 兜底 1 · FPS 无新增帧 · 看稳态线",
        )

    def test_graph_diagnostic_summary_text_flags_network_bypass_risk(self) -> None:
        self.assertEqual(
            graph_diagnostic_summary_text(
                "rx_kbps",
                [(0.0, 0.0, "ok"), (1.0, 128.0, "ok")],
                "弱网代理等待目标流量，App 峰值已有网络，疑似绕过系统代理",
            ),
            "弱网疑似绕过 · 对比代理流量",
        )

    def test_graph_panel_summary_uses_display_series_for_limited_points(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")
        append_start = text.index("def append(self, elapsed: float, value: float, quality: str = \"ok\")")
        append_end = text.index("def set_display_context", append_start)
        append_body = text[append_start:append_end]

        self.assertIn("graph_display_series_for_context", append_body)
        self.assertIn("summary_points", append_body)
        self.assertNotIn("graph_summary_text([(elapsed, value) for elapsed, value, _quality in self.points]", append_body)

    def test_graph_panel_declares_summary_and_fixed_quality_legend(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")
        panel_start = text.index("class GraphPanel")
        panel_end = text.index("class TrafficMiniChart", panel_start)
        panel_body = text[panel_start:panel_end]

        self.assertIn("self.summary_var", panel_body)
        self.assertIn("graph_summary_text", panel_body)
        self.assertIn("正常", panel_body)
        self.assertIn("兜底", panel_body)
        self.assertIn("受限", panel_body)
        self.assertIn("异常", panel_body)

    def test_graph_panel_declares_diagnostic_summary_line(self) -> None:
        source = Path(__file__).resolve().parents[1] / "mobileperflab.py"
        text = source.read_text(encoding="utf-8")
        panel_start = text.index("class GraphPanel")
        panel_end = text.index("class TrafficMiniChart", panel_start)
        panel_body = text[panel_start:panel_end]

        self.assertIn("self.diagnostic_var", panel_body)
        self.assertIn("graph_diagnostic_summary_text", panel_body)
        self.assertIn("def set_diagnostic_detail", panel_body)

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
                "cpu_percent",
                "memory_mb",
                "rx_kbps",
                "tx_kbps",
                "jank_percent",
                "temperature_c",
                "power_w",
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

    def test_metric_cards_surface_idle_and_fallback_network_health(self) -> None:
        class FakeCard:
            def __init__(self) -> None:
                self.value: object = None
                self.sub = ""

            def set_value(self, value: object, sub: str) -> None:
                self.value = value
                self.sub = sub

        app = object.__new__(App)
        app.cards = {
            "rx_kbps": FakeCard(),
            "tx_kbps": FakeCard(),
        }
        analyzer = MetricHealthAnalyzer()
        idle_health = analyzer.analyze(
            PerfSample(timestamp=8.0, elapsed=8.0, fps=58.0, cpu_percent=22.0, rx_kbps=0.0, tx_kbps=0.0)
        )
        fallback_health = analyzer.analyze(
            PerfSample(
                timestamp=9.0,
                elapsed=9.0,
                fps=58.0,
                cpu_percent=22.0,
                rx_kbps=4.0,
                tx_kbps=2.0,
                note="Android 网络使用设备级网络兜底，非目标 App 独占流量。",
            )
        )

        App._set_metric_card(app, "rx_kbps", 0.0, "接收速率", idle_health)
        App._set_metric_card(app, "tx_kbps", 2.0, "发送速率", fallback_health)

        self.assertEqual(app.cards["rx_kbps"].value, "无流量")
        self.assertEqual(app.cards["rx_kbps"].sub, "当前没有应用网络流量")
        self.assertEqual(app.cards["tx_kbps"].value, 2.0)
        self.assertEqual(app.cards["tx_kbps"].sub, "设备级网络兜底，非目标 App 独占流量")
        self.assertNotEqual(app.cards["tx_kbps"].sub, "发送速率")

    def test_network_graph_diagnostic_detail_includes_weak_network_bypass_summary(self) -> None:
        class FakeVar:
            def get(self) -> str:
                return "弱网：先修弱网链路\n弱网 ON · 疑似绕过代理 · App 峰值 128.0 KB/s"

        class FakeCard:
            def set_value(self, _value: object, _sub: str) -> None:
                pass

        class FakeGraph:
            def __init__(self) -> None:
                self.detail = ""

            def set_diagnostic_detail(self, detail: str) -> None:
                self.detail = detail

        app = object.__new__(App)
        app.cards = {"rx_kbps": FakeCard()}
        app.graphs = {"rx_kbps": FakeGraph()}
        app.weak_live_summary_var = FakeVar()
        app.recorder = type("FakeRecorder", (), {"samples": []})()
        health = MetricHealthAnalyzer().analyze(
            PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0, cpu_percent=12.0, rx_kbps=128.0)
        )

        App._set_metric_card(app, "rx_kbps", 128.0, "接收速率", health)

        self.assertIn("疑似绕过代理", app.graphs["rx_kbps"].detail)
        self.assertIn("App 峰值 128.0 KB/s", app.graphs["rx_kbps"].detail)

    def test_reset_metrics_uses_waiting_placeholders_not_zero_values(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

            def get(self) -> bool:
                return True

        class FakeCard:
            def __init__(self) -> None:
                self.value: object = None
                self.sub = ""

            def set_value(self, value: object, sub: str) -> None:
                self.value = value
                self.sub = sub

        class FakeGraph:
            def __init__(self) -> None:
                self.reset_called = False

            def reset(self) -> None:
                self.reset_called = True

        app = object.__new__(App)
        app.graphs = {item["key"]: FakeGraph() for item in metric_graph_layout()}
        app.cards = {item["key"]: FakeCard() for item in metric_graph_layout()}
        app.metric_health_vars = {}
        app.collection_link_vars = {}
        app.stabilizer = MetricStabilizer()
        app.live_quality = LiveQualityTracker()
        app.quality_summary_var = FakeVar()
        app.performance_conclusion_var = FakeVar()
        app.quality_var = FakeVar()
        app.quality_mode_var = FakeVar()
        app.smoothing_var = FakeVar()
        app._clear_quality_events = lambda: None
        app._refresh_graph_time_axis = lambda: None

        App._reset_metrics(app)

        self.assertTrue(all(graph.reset_called for graph in app.graphs.values()))
        self.assertTrue(all(card.value == "--" for card in app.cards.values()))
        self.assertTrue(all(card.sub == "等待数据" for card in app.cards.values()))

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

    def test_handle_sample_adds_recent_average_and_peak_to_healthy_metric_cards(self) -> None:
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
        app.cards = {item["key"]: FakeCard() for item in metric_graph_layout()}
        app.graphs = {key: FakeGraph() for key in app.cards}
        app._refresh_graph_time_axis = lambda: None
        app._format_elapsed = lambda elapsed: f"{elapsed:.1f}s"
        app._append_quality_event = lambda _sample: None
        app._refresh_proxy_traffic = lambda: None

        App._handle_sample(app, PerfSample(timestamp=1.0, elapsed=1.0, fps=60.0, cpu_percent=20.0))
        App._handle_sample(app, PerfSample(timestamp=2.0, elapsed=2.0, fps=58.0, cpu_percent=22.0))

        self.assertIn("正常", app.cards["cpu_percent"].sub)
        self.assertIn("进程占用", app.cards["cpu_percent"].sub)
        self.assertIn("均值 21.0%", app.cards["cpu_percent"].sub)
        self.assertIn("峰值 22.0%", app.cards["cpu_percent"].sub)

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

        self.assertEqual(rows[0][0:2], ("前台", "异常"))
        self.assertIn("当前前台 com.example.home", rows[0][2])
        self.assertEqual(rows[0][3], "issue")
        self.assertEqual(rows[1][0:2], ("PID", "异常"))
        self.assertIn("App 可能未运行", rows[1][2])
        self.assertEqual(rows[1][3], "issue")
        self.assertEqual(rows[2][0:2], ("UID", "异常"))
        self.assertIn("上下行网络无法按 App 统计", rows[2][2])
        self.assertEqual(rows[2][3], "issue")
        self.assertEqual(rows[3][0:2], ("FPS", "异常"))
        self.assertIn("未发现帧数据", rows[3][2])
        self.assertEqual(rows[3][3], "issue")
        self.assertEqual(rows[4][0:2], ("网络", "兜底"))
        self.assertIn("非目标 App 独占流量", rows[4][2])
        self.assertEqual(rows[4][3], "fallback")

    def test_collection_link_hints_include_operator_next_steps_for_missing_android_sources(self) -> None:
        diagnostics = AndroidCollectionDiagnostics(
            overall_state="warning",
            summary="Android 采集自检发现 5 项风险",
            rows=[
                ("前台", "前台不一致", "当前前台 com.example.home"),
                ("PID", "未找到", "App 可能未运行"),
                ("UID", "未找到", "上下行网络无法按 App 统计"),
                ("FPS", "不可用", "未发现帧数据"),
                ("网络", "不可用", "未读取到 per-UID 或设备级网络计数"),
            ],
            foreground_state="mismatch",
            pid_source="missing",
            uid_source="missing",
            fps_source="missing",
            network_source="missing",
        )

        rows = {name: (label, hint, state) for name, label, hint, state in collection_diagnostic_status_rows(diagnostics)}

        self.assertIn("保持目标 App 在前台", rows["前台"][1])
        self.assertIn("重新读取前台应用", rows["PID"][1])
        self.assertIn("检查包名", rows["UID"][1])
        self.assertIn("保持页面可见", rows["FPS"][1])
        self.assertIn("下载/上传", rows["网络"][1])

    def test_update_collection_links_keeps_next_step_visible_in_right_rail(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

        app = object.__new__(App)
        app.collection_link_vars = {"网络": FakeVar()}
        diagnostics = AndroidCollectionDiagnostics(
            overall_state="warning",
            summary="Android 采集自检发现 1 项风险",
            rows=[("网络", "不可用", "未读取到 per-UID 或设备级网络计数")],
            network_source="missing",
        )

        App._update_collection_links(app, diagnostics)

        self.assertIn("网络: 异常", app.collection_link_vars["网络"].value)
        self.assertIn("下一步", app.collection_link_vars["网络"].value)
        self.assertIn("下载/上传", app.collection_link_vars["网络"].value)


if __name__ == "__main__":
    unittest.main()
