import time
import unittest
from unittest.mock import patch

from mobileperflab import (
    AndroidAdapter,
    DeviceInfo,
    collection_diagnostic_status_rows,
    format_android_collection_diagnostics,
)


class FakeAndroidAdapter(AndroidAdapter):
    def __init__(self, responses: dict[str, str]) -> None:
        super().__init__()
        self.responses = responses
        self.calls: list[str] = []

    def _shell(self, serial: str, command: str, timeout: float = 8.0) -> str:
        self.calls.append(command)
        response = self.responses.get(command, "")
        if "\n---NEXT---\n" in response:
            current, rest = response.split("\n---NEXT---\n", 1)
            self.responses[command] = rest
            return current
        return response


class SlowMetricAndroidAdapter(AndroidAdapter):
    def __init__(self, sleep_seconds: float = 0.04) -> None:
        super().__init__()
        self.sleep_seconds = sleep_seconds
        self.calls: list[str] = []

    def _record(self, name: str) -> None:
        self.calls.append(name)
        time.sleep(self.sleep_seconds)

    def foreground_app(self, device: DeviceInfo) -> str:
        self._record("foreground")
        return "com.example.game"

    def _light_foreground_app(self, device: DeviceInfo) -> str:
        return ""

    def _fps_and_jank(self, device: DeviceInfo, app_id: str, now: float) -> tuple[float, float]:
        self._record("fps")
        return 58.0, 2.0

    def _battery(self, device: DeviceInfo) -> tuple[float, float, float]:
        self._record("battery")
        return 80.0, 36.0, 1.2

    def _network_kbps(self, device: DeviceInfo, app_id: str, now: float) -> tuple[float, float]:
        self._record("network")
        return 12.0, 4.0

    def _cpu_percent(self, device: DeviceInfo, app_id: str) -> float:
        self._record("cpu")
        return 22.0

    def _cpu_normalization_capacity(self, device: DeviceInfo) -> tuple[int, float, float]:
        return 1, 1.0, 1.0

    def _memory_mb(self, device: DeviceInfo, app_id: str) -> float:
        self._record("memory")
        return 512.0


class NormalizedCpuAndroidAdapter(SlowMetricAndroidAdapter):
    def _cpu_percent(self, device: DeviceInfo, app_id: str) -> float:
        self._record("cpu")
        return 200.0

    def _cpu_normalization_capacity(self, device: DeviceInfo) -> tuple[int, float, float]:
        return 8, 12_000_000.0, 24_000_000.0


class AdSurfaceMetricAndroidAdapter(SlowMetricAndroidAdapter):
    def _fps_and_jank(self, device: DeviceInfo, app_id: str, now: float) -> tuple[float, float]:
        self._record("fps")
        self._surface_cache[(device.serial, app_id)] = f"SurfaceView[{app_id}/com.applovin.adview.FullscreenActivity]@0(BLAST)#99"
        return 58.0, 0.0


class InPackageAdActivityAndroidAdapter(SlowMetricAndroidAdapter):
    def _light_foreground_app(self, device: DeviceInfo) -> str:
        return ""

    def foreground_app(self, device: DeviceInfo) -> str:
        self._record("foreground")
        return "com.example.game"

    def _shell(self, serial: str, command: str, timeout: float = 8.0) -> str:
        if command == "dumpsys window":
            return (
                "mCurrentFocus=Window{1624f58 u0 "
                "com.example.game/com.applovin.adview.AppLovinFullscreenActivity}\n"
                "mFocusedApp=ActivityRecord{191800158 u0 "
                "com.example.game/com.applovin.adview.AppLovinFullscreenActivity t448}"
            )
        return ""


class SlowDiagnosticAndroidAdapter(AndroidAdapter):
    def __init__(self, sleep_seconds: float = 0.04) -> None:
        super().__init__()
        self.sleep_seconds = sleep_seconds
        self.calls: list[str] = []

    def _record(self, name: str) -> None:
        self.calls.append(name)
        time.sleep(self.sleep_seconds)

    def foreground_app(self, device: DeviceInfo) -> str:
        self._record("foreground")
        return "com.example.game"

    def _diagnose_process_pids(self, device: DeviceInfo, app_id: str) -> tuple[list[int], str]:
        self._record("pid")
        return [101], "pidof"

    def _diagnose_app_uid(self, device: DeviceInfo, app_id: str, pids: list[int] | None = None) -> tuple[int | None, str]:
        self._record("uid")
        return 10234, "dumpsys package"

    def _diagnose_fps_source(self, device: DeviceInfo, app_id: str, now: float) -> str:
        self._record("fps")
        return "gfxinfo counters"

    def _diagnose_network_source(self, device: DeviceInfo, app_id: str, uid: int | None) -> str:
        self._record("network")
        return "per-UID"

    def _diagnose_network_channel(self, device: DeviceInfo, app_id: str, uid: int | None) -> tuple[str, str, str]:
        self._record("network")
        return "per-UID", "per-UID", "目标 App 独占上下行"


class FailingMetricAndroidAdapter(SlowMetricAndroidAdapter):
    def _cpu_percent(self, device: DeviceInfo, app_id: str) -> float:
        self._record("cpu")
        raise RuntimeError("proc denied")


class MissingPidAndroidAdapter(FakeAndroidAdapter):
    def __init__(self) -> None:
        ps_rows = ["USER PID PPID VSZ RSS WCHAN ADDR S NAME"]
        ps_rows.extend(f"u0_a{index} {1000 + index} 1 0 0 0 0 S com.other.app{index}" for index in range(30))
        super().__init__(
            {
                "cmd activity get-foreground-activities": "mFocusedApp=ActivityRecord{42 u0 com.example.missing/.MainActivity}",
                "dumpsys window": "mCurrentFocus=Window{42ab com.example.missing/com.example.missing.MainActivity}",
                "pidof com.example.missing": "",
                "pgrep -f com.example.missing": "",
                "ps -A -o PID=,NAME=": "",
                "ps -A": "\n".join(ps_rows),
                "dumpsys cpuinfo com.example.missing": "",
                "top -b -n 1 -o PID,CPU,ARGS": "",
                "top -b -n 1": "",
                "top -n 1": "",
                "dumpsys meminfo com.example.missing": "",
                "dumpsys battery": "level: 88\ntemperature: 360\nvoltage: 3900",
                "cat /sys/class/power_supply/battery/current_now": "0",
                "cat /sys/class/power_supply/battery/voltage_now": "3900000",
                "dumpsys package com.example.missing": "userId=12345",
                "cat /proc/uid_stat/12345/tcp_rcv": "0",
                "cat /proc/uid_stat/12345/tcp_snd": "0",
                "dumpsys gfxinfo com.example.missing": "",
                "dumpsys gfxinfo com.example.missing framestats": "",
                "dumpsys SurfaceFlinger --list": "",
            }
        )


class ForegroundSequenceAndroidAdapter(SlowMetricAndroidAdapter):
    def __init__(self, foreground_sequence: list[str]) -> None:
        super().__init__(sleep_seconds=0.0)
        self.foreground_sequence = foreground_sequence

    def foreground_app(self, device: DeviceInfo) -> str:
        self._record("foreground")
        if self.foreground_sequence:
            return self.foreground_sequence.pop(0)
        return "com.example.game"


def stat_line(pid: int, name: str, utime: int, stime: int) -> str:
    fields = ["S", "0", "0", "0", "0", "0", "0", "0", "0", "0", "0", str(utime), str(stime), "0", "0", "0"]
    return f"{pid} ({name}) {' '.join(fields)}"


class AndroidAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.device = DeviceInfo("Android", "serial-1", "LowEnd", "13", "LE", "ready")

    def test_foreground_app_reads_splash_screen_package_from_window_focus(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "mCurrentFocus=Window{a1 u0 Splash Screen com.example.game}",
            }
        )

        self.assertEqual(adapter.foreground_app(self.device), "com.example.game")

    def test_foreground_app_reads_plain_window_component_without_user_prefix(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "mCurrentFocus=Window{42ab com.example.game/com.example.game.MainActivity}",
            }
        )

        self.assertEqual(adapter.foreground_app(self.device), "com.example.game")

    def test_foreground_app_falls_back_to_activity_stack_when_window_has_no_package(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "mCurrentFocus=null",
                "dumpsys activity activities": "mResumedActivity: ActivityRecord{42 u0 com.example.game/.MainActivity t7}",
            }
        )

        self.assertEqual(adapter.foreground_app(self.device), "com.example.game")
        self.assertIn("dumpsys activity activities", adapter.calls)

    def test_foreground_app_does_not_use_stale_activity_when_screen_is_locked(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "\n".join(
                    [
                        "mCurrentFocus=Window{f86200 u0 NotificationShade}",
                        "mAwake=false mScreenOn: false",
                        "mDreamingLockscreen=true",
                        "isKeyguardShowing=true",
                    ]
                ),
                "dumpsys activity activities": "topResumedActivity=ActivityRecord{42 u0 com.example.game/.MainActivity t7}",
            }
        )

        self.assertEqual(adapter.foreground_app(self.device), "")
        self.assertIn("dumpsys window", adapter.calls)

    def test_foreground_app_prefers_resumed_activity_over_stale_window_history(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys activity activities": (
                    "topResumedActivity=ActivityRecord{143 u0 "
                    "com.example.game/com.example.game.MainActivity t343}"
                ),
                "dumpsys window": "\n".join(
                    [
                        "ID_SETTING_UI_SIDE_KEY, keyCode: 26, ACTION_START_ACTIVITY, dispatching: -1, "
                        "Intent { cmp=com.sec.android.app.camera/.Camera }",
                        "mCurrentFocus=Window{fe4be98 u0 com.example.game/com.example.game.MainActivity}",
                    ]
                ),
            }
        )

        self.assertEqual(adapter.foreground_app(self.device), "com.example.game")
        self.assertEqual(adapter.calls[0], "dumpsys activity activities")

    def test_foreground_app_reads_bare_package_from_focused_app_line(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "mFocusedApp=ActivityRecord{abc u0 com.example.game t10}",
            }
        )

        self.assertEqual(adapter.foreground_app(self.device), "com.example.game")

    def test_foreground_app_reads_component_info_without_window_focus(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "",
                "dumpsys activity activities": "topActivity=ComponentInfo{com.example.game/com.example.game.MainActivity}",
            }
        )

        self.assertEqual(adapter.foreground_app(self.device), "com.example.game")

    def test_parse_foreground_app_keeps_play_store_overlay_before_target_task(self) -> None:
        output = "\n".join(
            [
                "topResumedActivity=ActivityRecord{1 u0 com.android.vending/com.google.android.finsky.MainActivity t449}",
                "mCurrentFocus=Window{1 u0 com.android.vending/com.google.android.finsky.MainActivity}",
                "* Hist #0: ActivityRecord{2 u0 com.example.game/.MainActivity t448}",
            ]
        )

        self.assertEqual(AndroidAdapter._parse_foreground_app(output), "com.android.vending")

    def test_foreground_app_falls_back_to_activity_top_for_vendor_roms(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "",
                "dumpsys activity activities": "",
                "cmd activity get-foreground-activities": "",
                "dumpsys activity top": "ACTIVITY com.example.game/.MainActivity 123 pid=101\n",
            }
        )

        self.assertEqual(adapter.foreground_app(self.device), "com.example.game")
        self.assertIn("dumpsys activity top", adapter.calls)

    def test_collection_diagnostics_uses_activity_top_foreground_fallback(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "",
                "dumpsys activity activities": "",
                "dumpsys activity top": "ACTIVITY com.example.game/.MainActivity 123 pid=101\n",
                "cmd activity get-foreground-activities": "",
                "pidof com.example.game": "101",
                "cat /proc/101/status": "Uid:\t10234\t10234\t10234\t10234\n",
                "dumpsys gfxinfo com.example.game": "Total frames rendered: 120\nJanky frames: 6\n",
                "cat /proc/uid_stat/10234/tcp_rcv": "4096",
                "cat /proc/uid_stat/10234/tcp_snd": "2048",
            }
        )

        diagnostics = adapter.collection_diagnostics(self.device, "com.example.game", now=100.0)

        self.assertEqual(diagnostics.foreground_state, "ok")
        self.assertEqual(diagnostics.foreground_app, "com.example.game")
        self.assertIn(("前台", "匹配", "当前前台 com.example.game"), diagnostics.rows)

    def test_ensure_target_app_foreground_launches_selected_app_when_home_is_foreground(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys activity activities": "\n---NEXT---\n".join(
                    [
                        "topResumedActivity=ActivityRecord{111 u0 com.sec.android.app.launcher/.Launcher t1}",
                        "topResumedActivity=ActivityRecord{222 u0 com.example.game/.MainActivity t2}",
                    ]
                ),
            }
        )

        launched, foreground = adapter.ensure_target_app_foreground(self.device, "com.example.game")

        self.assertTrue(launched)
        self.assertEqual(foreground, "com.example.game")
        self.assertIn("monkey -p com.example.game -c android.intent.category.LAUNCHER 1", adapter.calls)

    def test_ensure_device_ready_for_sampling_wakes_and_dismisses_keyguard(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "\n---NEXT---\n".join(
                    [
                        "screenState=SCREEN_STATE_OFF\nKeyguardServiceDelegate\n  showing=true\n  isKeyguardShowing=true",
                        "mAwake=true\nmScreenOn: true\nKeyguardServiceDelegate\n  showing=false\n  isKeyguardShowing=false",
                    ]
                )
            }
        )

        ready, reason = adapter.ensure_device_ready_for_sampling(self.device, timeout=0.2)

        self.assertTrue(ready)
        self.assertEqual(reason, "")
        self.assertIn("input keyevent KEYCODE_WAKEUP", adapter.calls)
        self.assertIn("wm dismiss-keyguard", adapter.calls)

    def test_ensure_device_ready_for_sampling_reports_locked_screen_when_keyguard_remains(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "screenState=SCREEN_STATE_OFF\nKeyguardServiceDelegate\n  showing=true\n  isKeyguardShowing=true",
            }
        )

        ready, reason = adapter.ensure_device_ready_for_sampling(self.device, timeout=0.0)

        self.assertFalse(ready)
        self.assertIn("锁屏", reason)

    def test_window_dump_screen_on_ignores_unrelated_state_off_tokens(self) -> None:
        output = "mAwake=true\nmScreenOn: true\nsome_state=OFF\nKeyguardServiceDelegate\n  showing=false"

        self.assertTrue(AndroidAdapter._window_dump_screen_on(output))

    def test_cpu_percent_sums_all_pids_returned_by_pidof(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "pidof com.example.game": "101 202",
                "getconf CLK_TCK": "100",
                "cat /proc/101/stat": "\n---NEXT---\n".join(
                    [
                        stat_line(101, "main", 10, 5),
                        stat_line(101, "main", 30, 15),
                    ]
                ),
                "cat /proc/202/stat": "\n---NEXT---\n".join(
                    [
                        stat_line(202, "render", 20, 5),
                        stat_line(202, "render", 35, 10),
                    ]
                ),
            }
        )

        with patch("mobileperflab.time.time", side_effect=[100.0, 101.0]):
            self.assertIsNone(adapter._cpu_percent_from_proc(self.device, "com.example.game"))
            self.assertAlmostEqual(adapter._cpu_percent_from_proc(self.device, "com.example.game"), 50.0)

    def test_cpu_percent_sums_matching_dumpsys_cpuinfo_processes_when_proc_has_no_delta(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "pidof com.example.game": "",
                "pgrep -f com.example.game": "",
                "ps -A -o PID=,NAME=": "",
                "ps -A": "",
                "dumpsys cpuinfo com.example.game": "\n".join(
                    [
                        "CPU usage from 5000ms to 0ms ago:",
                        "  12% 1234/com.example.game: 8% user + 4% kernel",
                        "  6.5% 2345/com.example.game:render: 3% user + 3.5% kernel",
                        "  22% 3456/com.example.other: 20% user + 2% kernel",
                        "  44% TOTAL: 30% user + 14% kernel",
                    ]
                ),
            }
        )

        self.assertAlmostEqual(adapter._cpu_percent(self.device, "com.example.game"), 18.5)

    def test_cpu_percent_recovers_immediately_when_cached_pid_disappears_after_app_restart(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "pidof com.example.game": "\n---NEXT---\n".join(["101", "202"]),
                "getconf CLK_TCK": "100",
                "cat /proc/101/stat": "\n---NEXT---\n".join(
                    [
                        stat_line(101, "main", 10, 5),
                        "",
                    ]
                ),
                "cat /proc/202/stat": stat_line(202, "main", 30, 10),
                "dumpsys cpuinfo com.example.game": "  14% 202/com.example.game: 9% user + 5% kernel",
            }
        )

        with patch("mobileperflab.time.time", side_effect=[100.0, 101.0]):
            self.assertIsNone(adapter._cpu_percent_from_proc(self.device, "com.example.game"))
            self.assertAlmostEqual(adapter._cpu_percent(self.device, "com.example.game"), 14.0)

        self.assertEqual(adapter._pid_cache[(self.device.serial, "com.example.game")], 202)
        self.assertEqual(adapter._cpu_proc_cache[(self.device.serial, "com.example.game")][1], {202: 40})

    def test_cpu_percent_refreshes_pid_list_to_include_new_render_process(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "pidof com.example.game": "\n---NEXT---\n".join(["101", "101 202"]),
                "getconf CLK_TCK": "100",
                "cat /proc/101/stat": "\n---NEXT---\n".join(
                    [
                        stat_line(101, "main", 10, 5),
                        stat_line(101, "main", 30, 15),
                    ]
                ),
                "cat /proc/202/stat": stat_line(202, "render", 35, 10),
                "dumpsys cpuinfo com.example.game": "  28% 101/com.example.game: 18% user + 10% kernel\n  16% 202/com.example.game:render: 10% user + 6% kernel",
            }
        )

        with patch("mobileperflab.time.time", side_effect=[100.0, 101.0]):
            self.assertIsNone(adapter._cpu_percent_from_proc(self.device, "com.example.game"))
            self.assertAlmostEqual(adapter._cpu_percent(self.device, "com.example.game"), 44.0)

        self.assertEqual(adapter._pid_cache[(self.device.serial, "com.example.game")], 101)
        self.assertEqual(adapter._pid_list_cache[(self.device.serial, "com.example.game")], [101, 202])

    def test_cpu_percent_keeps_cached_render_pid_when_low_end_pidof_temporarily_omits_it(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "pidof com.example.game": "\n---NEXT---\n".join(["101 202", "101"]),
                "getconf CLK_TCK": "100",
                "cat /proc/101/stat": "\n---NEXT---\n".join(
                    [
                        stat_line(101, "main", 10, 5),
                        stat_line(101, "main", 30, 15),
                    ]
                ),
                "cat /proc/202/stat": "\n---NEXT---\n".join(
                    [
                        stat_line(202, "render", 20, 5),
                        stat_line(202, "render", 35, 10),
                    ]
                ),
            }
        )

        with patch("mobileperflab.time.time", side_effect=[100.0, 101.0]):
            self.assertIsNone(adapter._cpu_percent_from_proc(self.device, "com.example.game"))
            self.assertAlmostEqual(adapter._cpu_percent_from_proc(self.device, "com.example.game"), 50.0)

        self.assertEqual(adapter._pid_list_cache[(self.device.serial, "com.example.game")], [101, 202])

    def test_cpu_percent_falls_back_to_top_when_cpuinfo_is_empty(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "pidof com.example.game": "",
                "pgrep -f com.example.game": "",
                "ps -A -o PID=,NAME=": "",
                "ps -A": "",
                "dumpsys cpuinfo com.example.game": "",
                "top -b -n 1 -o PID,CPU,ARGS": "\n".join(
                    [
                        "  PID CPU ARGS",
                        " 1234 17.0 com.example.game",
                        " 2345  4.5 com.example.game:render",
                        " 3456 28.0 com.example.gamehelper",
                    ]
                ),
            }
        )

        self.assertAlmostEqual(adapter._cpu_percent(self.device, "com.example.game"), 21.5)

    def test_cpu_percent_parses_toybox_top_status_cpu_header(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "pidof com.example.game": "",
                "pgrep -f com.example.game": "",
                "ps -A -o PID=,NAME=": "",
                "ps -A": "",
                "dumpsys cpuinfo com.example.game": "",
                "top -b -n 1 -o PID,CPU,ARGS": "\n".join(
                    [
                        "  PID USER         PR  NI VIRT  RES  SHR S[%CPU] %MEM     TIME+ ARGS",
                        " 1234 u0_a234      20   0 1.2G 100M  50M S 17.0  2.0   0:01.23 com.example.game",
                        " 2345 u0_a234      20   0 1.1G  80M  40M S  4.5  1.0   0:00.40 com.example.game:render",
                        " 3456 u0_a999      20   0 1.0G  70M  30M S 28.0  2.0   0:01.00 com.example.other",
                    ]
                ),
            }
        )

        self.assertAlmostEqual(adapter._cpu_percent(self.device, "com.example.game"), 21.5)

    def test_process_pids_falls_back_to_ps_when_pidof_and_pgrep_are_empty(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "pidof com.example.game": "",
                "pgrep -f com.example.game": "",
                "ps -A -o PID=,NAME=": "101 com.example.game\n202 com.example.game:render\n303 com.example.other\n",
            }
        )

        self.assertEqual(adapter._process_pids(self.device, "com.example.game"), [101, 202])

    def test_process_pids_falls_back_to_standard_ps_table_output(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "pidof com.example.game": "",
                "pgrep -f com.example.game": "",
                "ps -A -o PID=,NAME=": "",
                "ps -A": "\n".join(
                    [
                        "USER           PID  PPID     VSZ    RSS WCHAN            ADDR S NAME",
                        "u0_a234        101   888 123456  34567 0                   0 S com.example.game",
                        "u0_a234        202   888 123456  34567 0                   0 S com.example.game:render",
                        "u0_a999        303   888 123456  34567 0                   0 S com.example.other",
                    ]
                ),
            }
        )

        self.assertEqual(adapter._process_pids(self.device, "com.example.game"), [101, 202])

    def test_process_pids_reads_proc_cmdline_when_ps_process_name_is_truncated(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "pidof com.example.game": "",
                "pgrep -f com.example.game": "",
                "ps -A -o PID=,NAME=": "",
                "ps -A": "\n".join(
                    [
                        "USER           PID  PPID     VSZ    RSS WCHAN            ADDR S NAME",
                        "u0_a234        101   888 123456  34567 0                   0 S game",
                        "u0_a999        303   888 123456  34567 0                   0 S other",
                    ]
                ),
                "cat /proc/101/cmdline": "com.example.game\x00",
                "cat /proc/303/cmdline": "com.example.other\x00",
            }
        )

        self.assertEqual(adapter._process_pids(self.device, "com.example.game"), [101])
        self.assertIn("cat /proc/101/cmdline", adapter.calls)

    def test_collection_diagnostics_reports_proc_cmdline_pid_source(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "mCurrentFocus=Window{42ab com.example.game/com.example.game.MainActivity}",
                "pidof com.example.game": "",
                "pgrep -f com.example.game": "",
                "ps -A -o PID=,NAME=": "",
                "ps -A": "u0_a234        101   888 123456  34567 0 0 S game\n",
                "cat /proc/101/cmdline": "com.example.game\x00",
                "cat /proc/101/status": "Uid:\t10234\t10234\t10234\t10234\n",
                "dumpsys gfxinfo com.example.game": "Total frames rendered: 120\nJanky frames: 6\n",
                "cat /proc/uid_stat/10234/tcp_rcv": "4096",
                "cat /proc/uid_stat/10234/tcp_snd": "2048",
            }
        )

        diagnostics = adapter.collection_diagnostics(self.device, "com.example.game", now=100.0)

        self.assertEqual(diagnostics.pid_source, "/proc cmdline")
        self.assertEqual(diagnostics.pids, [101])

    def test_netstats_parser_reads_named_uid_bucket_values(self) -> None:
        output = """
        Bucket{uid=10234 tag=0x0 set=DEFAULT metered=false defaultNetwork=true}:
          NetworkStatsHistory: bucketDuration=3600
          bucketStart=1710000000000 activeTime=2500 rxBytes=4096 rxPackets=8 txBytes=2048 txPackets=4 operations=0
        Bucket{uid=10234 tag=0x0 set=FOREGROUND} bucketStart=1710000001000 rxBytes=1024 rxPackets=2 txBytes=512 txPackets=1
        """

        self.assertEqual(AndroidAdapter._parse_netstats_detail_for_uid(output, 10234), (5120, 2560))

    def test_netstats_parser_reads_colon_named_uid_bucket_values(self) -> None:
        output = """
        Bucket{uid=10234 tag=0x0 set=DEFAULT}:
          bucketStart=1710000000000 activeTime=2500 rxBytes: 4096 rxPackets: 8 txBytes: 2048 txPackets: 4 operations: 0
        Bucket{uid=10001 tag=0x0 set=DEFAULT}:
          bucketStart=1710000000000 activeTime=2500 rxBytes: 9999 rxPackets: 9 txBytes: 9999 txPackets: 9 operations: 0
        """

        self.assertEqual(AndroidAdapter._parse_netstats_detail_for_uid(output, 10234), (4096, 2048))

    def test_netstats_parser_reads_snake_case_named_uid_bucket_values(self) -> None:
        output = """
        Bucket{uid=10234 tag=0x0 set=DEFAULT}:
          bucket_start=1710000000000 active_time=2500 rx_bytes=4096 rx_packets=8 tx_bytes=2048 tx_packets=4 operations=0
        Bucket{uid=10001 tag=0x0 set=DEFAULT}:
          bucket_start=1710000000000 active_time=2500 rx_bytes=9999 rx_packets=9 tx_bytes=9999 tx_packets=9 operations=0
        """

        self.assertEqual(AndroidAdapter._parse_netstats_detail_for_uid(output, 10234), (4096, 2048))

    def test_netstats_parser_reads_bucket_header_with_positional_history_values(self) -> None:
        output = """
        ident=[{type=WIFI, subType=COMBINED, networkId="lab"}] uid=10234 set=DEFAULT tag=0x0
          bucketDuration=3600
          1710000000000 4096 8 2048 4 0
          1710000001000 1024 2 512 1 0
        ident=[{type=WIFI, subType=COMBINED, networkId="lab"}] uid=10001 set=DEFAULT tag=0x0
          1710000000000 9999 9 9999 9 0
        """

        self.assertEqual(AndroidAdapter._parse_netstats_detail_for_uid(output, 10234), (5120, 2560))

    def test_netstats_parser_reads_abbreviated_history_values(self) -> None:
        output = """
        ident=[{type=MOBILE, subType=COMBINED, subscriberId=123}] uid=10234 set=DEFAULT tag=0x0
          NetworkStatsHistory: bucketDuration=1.00h
          st=1710000000000 rb=4096 rp=8 tb=2048 tp=4 op=0
          st=1710000001000 rb=1024 rp=2 tb=512 tp=1 op=0
        ident=[{type=WIFI, subType=COMBINED, networkId="lab"}] uid=10001 set=DEFAULT tag=0x0
          st=1710000000000 rb=9999 rp=9 tb=9999 tp=9 op=0
        """

        self.assertEqual(AndroidAdapter._parse_netstats_detail_for_uid(output, 10234), (5120, 2560))

    def test_parse_gfxinfo_framestats_skips_invalid_rows_but_keeps_valid_frames(self) -> None:
        output = """
        ---PROFILEDATA---
        IntendedVsync,VSync,InputEventStart,AnimationStart,PerformTraversalsStart,DrawStart,SyncQueued,SyncStart,IssueDrawCommandsStart,SwapBuffers,FrameCompleted,Flags
        1,2,3,4,5,6,7,8,9,10,1000,0
        bad,line,that,should,be,ignored
        1,2,3,4,5,6,7,8,9,10,2000,0
        1,2,3,4,5,6,7,8,9,10,3000,1
        ---PROFILEDATA---
        """

        self.assertEqual(AndroidAdapter._parse_gfxinfo_framestats(output), [1000, 2000])

    def test_parse_gfxinfo_framestats_ignores_empty_and_partial_profile_blocks(self) -> None:
        output = """
        ---PROFILEDATA---
        IntendedVsync,VSync,InputEventStart,AnimationStart,PerformTraversalsStart,DrawStart,SyncQueued,SyncStart,IssueDrawCommandsStart,SwapBuffers,FrameCompleted,Flags
        1,2,3,4,5,6,7,8,9,10,1000,0
        ---PROFILEDATA---
        ---PROFILEDATA---
        IntendedVsync,VSync,InputEventStart,AnimationStart,PerformTraversalsStart,DrawStart,SyncQueued,SyncStart,IssueDrawCommandsStart,SwapBuffers,FrameCompleted,Flags
        bad,line,that,should,be,ignored
        ---PROFILEDATA---
        """

        self.assertEqual(AndroidAdapter._parse_gfxinfo_framestats(output), [1000])

    def test_surface_name_prefers_surfaceview_blast_over_generic_activity_layer(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys SurfaceFlinger --list": "\n".join(
                    [
                        "com.example.game/com.example.game.MainActivity#0",
                        "SurfaceView[com.example.game/com.example.game.MainActivity](BLAST)#1",
                    ]
                ),
                "dumpsys window": "",
            }
        )

        self.assertEqual(
            adapter._surface_name(self.device, "com.example.game"),
            "SurfaceView[com.example.game/com.example.game.MainActivity](BLAST)#1",
        )

    def test_surface_names_strip_layer_name_prefixes_from_surfaceflinger_dump(self) -> None:
        lines = [
            "name=SurfaceView[com.example.game/com.example.game.MainActivity](BLAST)#12",
            "Layer name: SurfaceView[com.example.game/com.example.game.MainActivity](BLAST)#12",
            "+ Layer 0xb400 name=SurfaceView[com.example.game/com.example.game.MainActivity](BLAST)#12 parent=0x0",
            "RequestedLayerState{name=SurfaceView[com.example.game/com.example.game.MainActivity](BLAST)#12 parentId=42}",
        ]

        for line in lines:
            with self.subTest(line=line):
                self.assertIn(
                    "SurfaceView[com.example.game/com.example.game.MainActivity](BLAST)#12",
                    AndroidAdapter._surface_names_from_line(line),
                )

    def test_parse_surface_latency_uses_ready_time_when_present_time_is_zero(self) -> None:
        output = "\n".join(
            [
                "16666666",
                "0 1000000000 1000000000",
                "0 1016666666 1016666666",
            ]
        )

        refresh_period, frame_times = AndroidAdapter._parse_surface_latency(output)

        self.assertEqual(refresh_period, 16666666)
        self.assertEqual(frame_times, [1000000000, 1016666666])

    def test_parse_surface_latency_handles_headers_commas_and_pending_present_times(self) -> None:
        output = "\n".join(
            [
                "refresh-period-ns: 16666666",
                "9223372036854775807 1000000000 1000000000",
                "0,1016666666,1016666666",
            ]
        )

        refresh_period, frame_times = AndroidAdapter._parse_surface_latency(output)

        self.assertEqual(refresh_period, 16666666)
        self.assertEqual(frame_times, [1000000000, 1016666666])

    def test_surface_latency_frames_falls_back_to_next_candidate_when_first_surface_is_empty(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys SurfaceFlinger --list": "\n".join(
                    [
                        "SurfaceView[com.example.game/com.example.game.MainActivity](BLAST)#1",
                        "com.example.game/com.example.game.MainActivity#0",
                    ]
                ),
                "dumpsys window": "",
                "dumpsys SurfaceFlinger --latency 'SurfaceView[com.example.game/com.example.game.MainActivity](BLAST)#1'": "",
                "dumpsys SurfaceFlinger --latency 'com.example.game/com.example.game.MainActivity#0'": "\n".join(
                    [
                        "16666666",
                        "0 1000000000 1000000000",
                        "0 1016666666 1016666666",
                    ]
                ),
            }
        )

        refresh_period, frame_times = adapter._surface_latency_frames(self.device, "com.example.game")

        self.assertEqual(refresh_period, 16666666)
        self.assertEqual(frame_times, [1000000000, 1016666666])

    def test_surface_candidates_ignore_window_intent_and_refresh_history_lines(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys SurfaceFlinger --list": "",
                "dumpsys window": "\n".join(
                    [
                        "ID_SETTING_UI_SIDE_KEY, keyCode: 26, ACTION_START_ACTIVITY, dispatching: -1, "
                        "Intent { act=android.intent.action.MAIN cat=[android.intent.category.LAUNCHER] "
                        "cmp=com.sec.android.app.camera/.Camera }",
                        "#4 06-25 16:04:55.929  Requested ( modeId=2 "
                        "w=Window{522820d u0 com.sec.android.app.camera/com.sec.android.app.camera.Camera})",
                        "#8 06-25 16:04:56.274  Requested ( refreshRate=60.0 "
                        "w=Window{522820d u0 com.sec.android.app.camera/com.sec.android.app.camera.Camera})",
                    ]
                ),
            }
        )

        candidates = adapter._surface_name_candidates(self.device, "com.sec.android.app.camera")

        self.assertEqual(candidates, [])

    def test_android_collection_diagnostics_requires_surface_latency_frames_before_claiming_surfaceflinger_fps(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "mCurrentFocus=Window{42ab com.sec.android.app.camera/com.sec.android.app.camera.Camera}",
                "pidof com.sec.android.app.camera": "",
                "pgrep -f com.sec.android.app.camera": "",
                "ps -A -o PID=,NAME=": "",
                "ps -A": "",
                "dumpsys package com.sec.android.app.camera": "userId=10123",
                "dumpsys gfxinfo com.sec.android.app.camera": "",
                "dumpsys gfxinfo com.sec.android.app.camera framestats": "",
                "dumpsys SurfaceFlinger --list": "",
                "cat /proc/uid_stat/10123/tcp_rcv": "0",
                "cat /proc/uid_stat/10123/tcp_snd": "0",
            }
        )

        diagnostics = adapter.collection_diagnostics(self.device, "com.sec.android.app.camera", now=10.0)

        self.assertEqual(diagnostics.fps_source, "missing")
        self.assertIn(("FPS", "不可用", "未发现 gfxinfo/framestats/SurfaceFlinger 帧数据"), diagnostics.rows)

    def test_battery_power_falls_back_to_dumpsys_current_when_sysfs_is_unreadable(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys battery": "\n".join(
                    [
                        "Max charging voltage: 0",
                        "level: 100",
                        "voltage: 4408",
                        "temperature: 334",
                        "current now: -327343",
                    ]
                ),
                "cat /sys/class/power_supply/battery/current_now": "NA",
                "cat /sys/class/power_supply/battery/voltage_now": "NA",
            }
        )

        level, temperature, power_w = adapter._battery(self.device)

        self.assertEqual(level, 100)
        self.assertAlmostEqual(temperature, 33.4)
        self.assertAlmostEqual(power_w, 1.443, places=3)

    def test_network_kbps_falls_back_to_device_totals_with_note_when_uid_is_missing(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys package com.example.game": "",
                "cat /proc/net/dev": "\n---NEXT---\n".join(
                    [
                        "Inter-| Receive | Transmit\n wlan0: 100000 0 0 0 0 0 0 0 200000 0 0 0 0 0 0 0",
                        "Inter-| Receive | Transmit\n wlan0: 104096 0 0 0 0 0 0 0 202048 0 0 0 0 0 0 0",
                    ]
                ),
            }
        )

        with patch("mobileperflab.time.time", side_effect=[10.0, 11.0]):
            rx1, tx1 = adapter._network_kbps(self.device, "com.example.game", 10.0)
            rx2, tx2 = adapter._network_kbps(self.device, "com.example.game", 11.0)

        self.assertEqual((rx1, tx1), (0.0, 0.0))
        self.assertAlmostEqual(rx2, 4.0)
        self.assertAlmostEqual(tx2, 2.0)
        self.assertIn("设备级网络兜底", adapter._network_note_cache[("serial-1", "com.example.game")])

    def test_network_kbps_marks_unavailable_when_uid_and_device_fallback_are_missing(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys package com.example.game": "",
                "pidof com.example.game": "",
                "pgrep -f com.example.game": "",
                "ps -A -o PID=,NAME=": "",
                "ps -A": "",
                "pm list packages -U com.example.game": "",
                "cmd package list packages -U com.example.game": "",
                "cat /proc/net/dev": "Inter-| Receive | Transmit\n lo: 100 0 0 0 0 0 0 0 100 0 0 0 0 0 0 0",
            }
        )

        with patch("mobileperflab.time.time", return_value=10.0):
            rx, tx = adapter._network_kbps(self.device, "com.example.game", 10.0)

        self.assertEqual((rx, tx), (0.0, 0.0))
        self.assertIn("网络采集不可用", adapter._network_note_cache[("serial-1", "com.example.game")])

    def test_android_sample_note_does_not_mark_zero_network_as_error_when_uid_is_known(self) -> None:
        adapter = FakeAndroidAdapter({})
        adapter._uid_cache[(self.device.serial, "com.example.game")] = 10234
        adapter._pid_cache[(self.device.serial, "com.example.game")] = 101

        note = adapter._android_sample_note(
            self.device,
            "com.example.game",
            sample_count=3,
            fps=55.0,
            cpu=20.0,
            memory=512.0,
            rx=0.0,
            tx=0.0,
        )

        self.assertNotIn("网络当前无流量", note)
        self.assertNotIn("系统未开放 per-UID", note)

    def test_network_kbps_uses_process_status_uid_when_package_uid_is_missing(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys package com.example.game": "",
                "pidof com.example.game": "101",
                "cat /proc/101/status": "Name:\tcom.example.game\nUid:\t10234\t10234\t10234\t10234\n",
                "cat /proc/uid_stat/10234/tcp_rcv": "\n---NEXT---\n".join(["1024", "5120"]),
                "cat /proc/uid_stat/10234/tcp_snd": "\n---NEXT---\n".join(["512", "2560"]),
            }
        )

        with patch("mobileperflab.time.time", side_effect=[10.0, 11.0]):
            rx1, tx1 = adapter._network_kbps(self.device, "com.example.game", 10.0)
            rx2, tx2 = adapter._network_kbps(self.device, "com.example.game", 11.0)

        self.assertEqual((rx1, tx1), (0.0, 0.0))
        self.assertAlmostEqual(rx2, 4.0)
        self.assertAlmostEqual(tx2, 2.0)
        self.assertNotIn("设备级网络兜底", adapter._network_note_cache[("serial-1", "com.example.game")])

    def test_network_kbps_uses_counter_read_time_to_reduce_low_end_rate_jitter(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys package com.example.game": "userId=10234",
                "cat /proc/uid_stat/10234/tcp_rcv": "\n---NEXT---\n".join(["1024", "5120"]),
                "cat /proc/uid_stat/10234/tcp_snd": "\n---NEXT---\n".join(["512", "2560"]),
            }
        )

        with patch("mobileperflab.time.time", side_effect=[10.8, 12.8]):
            rx1, tx1 = adapter._network_kbps(self.device, "com.example.game", 10.0)
            rx2, tx2 = adapter._network_kbps(self.device, "com.example.game", 11.0)

        self.assertEqual((rx1, tx1), (0.0, 0.0))
        self.assertAlmostEqual(rx2, 2.0)
        self.assertAlmostEqual(tx2, 1.0)

    def test_network_kbps_keeps_zero_uid_stat_as_target_idle_instead_of_device_fallback(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys package com.example.game": "userId=10234",
                "cat /proc/uid_stat/10234/tcp_rcv": "\n---NEXT---\n".join(["0", "0"]),
                "cat /proc/uid_stat/10234/tcp_snd": "\n---NEXT---\n".join(["0", "0"]),
                "cat /proc/net/dev": "\n---NEXT---\n".join(
                    [
                        "Inter-| Receive | Transmit\n wlan0: 100000 0 0 0 0 0 0 0 200000 0 0 0 0 0 0 0",
                        "Inter-| Receive | Transmit\n wlan0: 120480 0 0 0 0 0 0 0 210240 0 0 0 0 0 0 0",
                    ]
                ),
            }
        )

        with patch("mobileperflab.time.time", side_effect=[10.0, 11.0]):
            rx1, tx1 = adapter._network_kbps(self.device, "com.example.game", 10.0)
            rx2, tx2 = adapter._network_kbps(self.device, "com.example.game", 11.0)

        self.assertEqual((rx1, tx1), (0.0, 0.0))
        self.assertEqual((rx2, tx2), (0.0, 0.0))
        self.assertEqual(adapter._network_note_cache[("serial-1", "com.example.game")], "")
        self.assertNotIn("cat /proc/net/dev", adapter.calls)

    def test_network_kbps_resets_device_fallback_baseline_after_per_uid_recovers(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys package com.example.game": "userId=10234",
                "cat /proc/uid_stat/10234/tcp_rcv": "\n---NEXT---\n".join(["", "1024", ""]),
                "cat /proc/uid_stat/10234/tcp_snd": "\n---NEXT---\n".join(["", "512", ""]),
                "cat /proc/net/xt_qtaguid/stats": "\n---NEXT---\n".join(["", ""]),
                "dumpsys netstats detail": "\n---NEXT---\n".join(["", ""]),
                "cat /proc/net/dev": "\n---NEXT---\n".join(
                    [
                        "Inter-| Receive | Transmit\n wlan0: 100000 0 0 0 0 0 0 0 200000 0 0 0 0 0 0 0",
                        "Inter-| Receive | Transmit\n wlan0: 200000 0 0 0 0 0 0 0 260000 0 0 0 0 0 0 0",
                    ]
                ),
            }
        )

        with patch("mobileperflab.time.time", side_effect=[10.0, 11.0, 12.0]):
            self.assertEqual(adapter._network_kbps(self.device, "com.example.game", 10.0), (0.0, 0.0))
            self.assertEqual(adapter._network_kbps(self.device, "com.example.game", 11.0), (0.0, 0.0))
            self.assertEqual(adapter._network_kbps(self.device, "com.example.game", 12.0), (0.0, 0.0))

        self.assertEqual(adapter._network_note_cache[("serial-1", "com.example.game")], "")

    def test_network_kbps_reads_uid_equals_from_dumpsys_package(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys package com.example.game": "Package [com.example.game]\n  uid=10234\n",
                "cat /proc/uid_stat/10234/tcp_rcv": "\n---NEXT---\n".join(["1024", "5120"]),
                "cat /proc/uid_stat/10234/tcp_snd": "\n---NEXT---\n".join(["512", "2560"]),
            }
        )

        with patch("mobileperflab.time.time", side_effect=[10.0, 11.0]):
            rx1, tx1 = adapter._network_kbps(self.device, "com.example.game", 10.0)
            rx2, tx2 = adapter._network_kbps(self.device, "com.example.game", 11.0)

        self.assertEqual((rx1, tx1), (0.0, 0.0))
        self.assertAlmostEqual(rx2, 4.0)
        self.assertAlmostEqual(tx2, 2.0)
        self.assertEqual(adapter._uid_cache[(self.device.serial, "com.example.game")], 10234)
        self.assertNotIn("设备级网络兜底", adapter._network_note_cache[("serial-1", "com.example.game")])

    def test_network_kbps_uses_pm_list_packages_uid_when_package_and_proc_status_are_missing(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys package com.example.game": "",
                "pidof com.example.game": "101",
                "cat /proc/101/status": "",
                "pm list packages -U com.example.game": "package:com.example.game uid:10234\n",
                "cat /proc/uid_stat/10234/tcp_rcv": "\n---NEXT---\n".join(["2048", "6144"]),
                "cat /proc/uid_stat/10234/tcp_snd": "\n---NEXT---\n".join(["1024", "3072"]),
            }
        )

        with patch("mobileperflab.time.time", side_effect=[10.0, 11.0]):
            rx1, tx1 = adapter._network_kbps(self.device, "com.example.game", 10.0)
            rx2, tx2 = adapter._network_kbps(self.device, "com.example.game", 11.0)

        self.assertEqual((rx1, tx1), (0.0, 0.0))
        self.assertAlmostEqual(rx2, 4.0)
        self.assertAlmostEqual(tx2, 2.0)
        self.assertNotIn("设备级网络兜底", adapter._network_note_cache[("serial-1", "com.example.game")])

    def test_network_kbps_reads_pm_list_packages_uid_with_spaced_separator(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys package com.example.game": "",
                "pidof com.example.game": "101",
                "cat /proc/101/status": "",
                "pm list packages -U com.example.game": "package:com.example.game uid: 10234\n",
                "cat /proc/uid_stat/10234/tcp_rcv": "\n---NEXT---\n".join(["1024", "5120"]),
                "cat /proc/uid_stat/10234/tcp_snd": "\n---NEXT---\n".join(["512", "2560"]),
            }
        )

        with patch("mobileperflab.time.time", side_effect=[10.0, 11.0]):
            rx1, tx1 = adapter._network_kbps(self.device, "com.example.game", 10.0)
            rx2, tx2 = adapter._network_kbps(self.device, "com.example.game", 11.0)

        self.assertEqual((rx1, tx1), (0.0, 0.0))
        self.assertAlmostEqual(rx2, 4.0)
        self.assertAlmostEqual(tx2, 2.0)
        self.assertEqual(adapter._uid_cache[(self.device.serial, "com.example.game")], 10234)

    def test_network_kbps_uses_cmd_package_uid_when_pm_list_packages_is_missing(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys package com.example.game": "",
                "pidof com.example.game": "101",
                "cat /proc/101/status": "",
                "pm list packages -U com.example.game": "",
                "cmd package list packages -U com.example.game": "package:com.example.game uid:10234\n",
                "cat /proc/uid_stat/10234/tcp_rcv": "\n---NEXT---\n".join(["4096", "8192"]),
                "cat /proc/uid_stat/10234/tcp_snd": "\n---NEXT---\n".join(["2048", "4096"]),
            }
        )

        with patch("mobileperflab.time.time", side_effect=[10.0, 11.0]):
            rx1, tx1 = adapter._network_kbps(self.device, "com.example.game", 10.0)
            rx2, tx2 = adapter._network_kbps(self.device, "com.example.game", 11.0)

        self.assertEqual((rx1, tx1), (0.0, 0.0))
        self.assertAlmostEqual(rx2, 4.0)
        self.assertAlmostEqual(tx2, 2.0)
        self.assertNotIn("设备级网络兜底", adapter._network_note_cache[("serial-1", "com.example.game")])

    def test_network_kbps_uses_netstats_snake_case_when_uid_stat_is_unavailable(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys package com.example.game": "userId=10234",
                "cat /proc/uid_stat/10234/tcp_rcv": "",
                "cat /proc/uid_stat/10234/tcp_snd": "",
                "cat /proc/net/xt_qtaguid/stats": "",
                "dumpsys netstats detail": "\n---NEXT---\n".join(
                    [
                        """
                        Bucket{uid=10234 tag=0x0 set=DEFAULT}:
                          bucket_start=1710000000000 active_time=2500 rx_bytes=1024 rx_packets=8 tx_bytes=512 tx_packets=4 operations=0
                        """,
                        """
                        Bucket{uid=10234 tag=0x0 set=DEFAULT}:
                          bucket_start=1710000001000 active_time=2500 rx_bytes=5120 rx_packets=8 tx_bytes=2560 tx_packets=4 operations=0
                        """,
                    ]
                ),
            }
        )

        with patch("mobileperflab.time.time", side_effect=[10.0, 11.0]):
            rx1, tx1 = adapter._network_kbps(self.device, "com.example.game", 10.0)
            rx2, tx2 = adapter._network_kbps(self.device, "com.example.game", 11.0)

        self.assertEqual((rx1, tx1), (0.0, 0.0))
        self.assertAlmostEqual(rx2, 4.0)
        self.assertAlmostEqual(tx2, 2.0)
        self.assertNotIn("设备级网络兜底", adapter._network_note_cache[("serial-1", "com.example.game")])

    def test_network_kbps_keeps_zero_netstats_uid_as_target_idle_instead_of_device_fallback(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys package com.example.game": "userId=10234",
                "cat /proc/uid_stat/10234/tcp_rcv": "",
                "cat /proc/uid_stat/10234/tcp_snd": "",
                "cat /proc/net/xt_qtaguid/stats": "",
                "dumpsys netstats detail": "\n---NEXT---\n".join(
                    [
                        "Bucket{uid=10234 tag=0x0 set=DEFAULT}: rx_bytes=0 rx_packets=0 tx_bytes=0 tx_packets=0",
                        "Bucket{uid=10234 tag=0x0 set=DEFAULT}: rx_bytes=0 rx_packets=0 tx_bytes=0 tx_packets=0",
                    ]
                ),
                "cat /proc/net/dev": "\n---NEXT---\n".join(
                    [
                        "Inter-| Receive | Transmit\n wlan0: 100000 0 0 0 0 0 0 0 200000 0 0 0 0 0 0 0",
                        "Inter-| Receive | Transmit\n wlan0: 120480 0 0 0 0 0 0 0 210240 0 0 0 0 0 0 0",
                    ]
                ),
            }
        )

        with patch("mobileperflab.time.time", side_effect=[10.0, 11.0]):
            rx1, tx1 = adapter._network_kbps(self.device, "com.example.game", 10.0)
            rx2, tx2 = adapter._network_kbps(self.device, "com.example.game", 11.0)

        self.assertEqual((rx1, tx1), (0.0, 0.0))
        self.assertEqual((rx2, tx2), (0.0, 0.0))
        self.assertEqual(adapter._network_note_cache[("serial-1", "com.example.game")], "")
        self.assertNotIn("cat /proc/net/dev", adapter.calls)

    def test_qtaguid_parser_sums_matching_uid_rows(self) -> None:
        output = """
        idx iface acct_tag_hex uid_tag_int cnt_set rx_bytes rx_packets tx_bytes tx_packets
        2 wlan0 0x0 10234 0 1000 10 500 5
        3 wlan0 0x0 10234 1 3000 30 1500 15
        4 wlan0 0x0 10000 0 9999 9 9999 9
        """

        self.assertEqual(AndroidAdapter._parse_qtaguid_stats(output, 10234), (4000, 2000))

    def test_qtaguid_parser_reports_matching_zero_uid_rows_as_readable(self) -> None:
        output = """
        idx iface acct_tag_hex uid_tag_int cnt_set rx_bytes rx_packets tx_bytes tx_packets
        2 wlan0 0x0 10234 0 0 0 0 0
        3 wlan0 0x0 10000 0 9999 9 9999 9
        """

        self.assertEqual(AndroidAdapter._parse_qtaguid_stats_with_match(output, 10234), (0, 0, True))

    def test_marks_sample_when_target_app_leaves_foreground(self) -> None:
        adapter = FakeAndroidAdapter({})

        note = adapter._foreground_session_note(self.device, "com.example.game", "com.example.home")

        self.assertEqual(note, "目标应用不在前台，当前前台为 com.example.home。")

    def test_marks_short_recovery_window_after_target_returns_foreground(self) -> None:
        adapter = FakeAndroidAdapter({})
        key = (self.device.serial, "com.example.game")

        self.assertEqual(
            adapter._foreground_session_note(self.device, "com.example.game", "com.example.home"),
            "目标应用不在前台，当前前台为 com.example.home。",
        )
        self.assertEqual(
            adapter._foreground_session_note(self.device, "com.example.game", "com.example.game"),
            "目标应用刚回到前台，恢复窗口内 FPS/CPU 可能受 Surface 和进程缓存重建影响。",
        )
        self.assertEqual(adapter._foreground_recovery_remaining[key], adapter._FOREGROUND_RECOVERY_SAMPLE_COUNT - 1)
        for _index in range(adapter._FOREGROUND_RECOVERY_SAMPLE_COUNT - 1):
            self.assertEqual(
                adapter._foreground_session_note(self.device, "com.example.game", "com.example.game"),
                "目标应用刚回到前台，恢复窗口内 FPS/CPU 可能受 Surface 和进程缓存重建影响。",
            )
        self.assertEqual(adapter._foreground_session_note(self.device, "com.example.game", "com.example.game"), "")

    def test_debounces_single_foreground_mismatch_after_ad_return(self) -> None:
        adapter = FakeAndroidAdapter({})

        self.assertEqual(adapter._foreground_session_note(self.device, "com.example.game", "com.example.game"), "")

        notes = [
            adapter._foreground_session_note(self.device, "com.example.game", "com.ad.network"),
            adapter._foreground_session_note(self.device, "com.example.game", "com.example.game"),
            adapter._foreground_session_note(self.device, "com.example.game", "com.ad.network"),
            adapter._foreground_session_note(self.device, "com.example.game", "com.example.game"),
        ]

        self.assertEqual(notes, ["", "", "", ""])

    def test_marks_persistent_foreground_mismatch_after_confirmation(self) -> None:
        adapter = FakeAndroidAdapter({})

        self.assertEqual(adapter._foreground_session_note(self.device, "com.example.game", "com.example.game"), "")
        self.assertEqual(adapter._foreground_session_note(self.device, "com.example.game", "com.example.home"), "")
        self.assertEqual(
            adapter._foreground_session_note(self.device, "com.example.game", "com.example.home"),
            "目标应用不在前台，当前前台为 com.example.home。",
        )
        self.assertEqual(
            adapter._foreground_session_note(self.device, "com.example.game", "com.example.home"),
            "目标应用不在前台，当前前台为 com.example.home。",
        )

    def test_ignores_target_launched_store_overlay_as_ad_flow_not_background(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys activity activities": "\n".join(
                    [
                        "ACTIVITY MANAGER ACTIVITIES",
                        "* Task{abc #449 A=10267:com.android.vending visible=true}",
                        "  * Hist  #0: ActivityRecord{1 u0 com.android.vending/.MainActivity t449}",
                        "    packageName=com.android.vending processName=com.android.vending",
                        "    launchedFromUid=10861 launchedFromPackage=com.example.game launchedFromFeature=null",
                        "    Intent { act=android.intent.action.VIEW dat=https://play.google.com/store/apps/details?id=com.other.game&referrer=applovin_test }",
                        "* Task{def #448 A=10861:com.example.game visible=true}",
                        "  * Hist  #0: ActivityRecord{2 u0 com.example.game/.MainActivity t448}",
                        "    packageName=com.example.game processName=com.example.game",
                    ]
                )
            }
        )

        self.assertEqual(adapter._foreground_session_note(self.device, "com.example.game", "com.example.game"), "")
        notes = [
            adapter._foreground_session_note(self.device, "com.example.game", "com.android.vending")
            for _index in range(4)
        ]

        self.assertTrue(all("广告/商店覆盖层" in note for note in notes))
        self.assertTrue(all("目标应用不在前台" not in note for note in notes))

    def test_treats_visible_ad_sdk_foreground_package_as_ad_overlay(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys activity activities": "\n".join(
                    [
                        "ACTIVITY MANAGER ACTIVITIES",
                        "* Task{abc #449 A=10267:com.applovin.sdk visible=true}",
                        "  * Hist  #0: ActivityRecord{1 u0 com.applovin.sdk/.FullscreenActivity t449}",
                        "    packageName=com.applovin.sdk processName=com.applovin.sdk",
                        "* Task{def #448 A=10861:com.example.game visible=true}",
                        "  * Hist  #0: ActivityRecord{2 u0 com.example.game/.MainActivity t448}",
                        "    packageName=com.example.game processName=com.example.game",
                    ]
                )
            }
        )

        self.assertEqual(adapter._foreground_session_note(self.device, "com.example.game", "com.example.game"), "")

        note = adapter._foreground_session_note(self.device, "com.example.game", "com.applovin.sdk")

        self.assertIn("广告/商店覆盖层", note)
        self.assertNotIn("目标应用不在前台", note)

    def test_does_not_ignore_stale_invisible_target_launched_store_task(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys activity activities": "\n".join(
                    [
                        "ACTIVITY MANAGER ACTIVITIES",
                        "* Task{abc #449 A=10267:com.android.vending visible=false visibleRequested=false}",
                        "  * Hist  #0: ActivityRecord{1 u0 com.android.vending/.MainActivity t449}",
                        "    packageName=com.android.vending processName=com.android.vending",
                        "    launchedFromUid=10861 launchedFromPackage=com.example.game launchedFromFeature=null",
                        "* Task{xyz #450 A=10267:com.android.vending visible=true visibleRequested=true}",
                        "  * Hist  #0: ActivityRecord{3 u0 com.android.vending/.AssetBrowserActivity t450}",
                        "    packageName=com.android.vending processName=com.android.vending",
                        "    launchedFromUid=2000 launchedFromPackage=com.android.shell launchedFromFeature=null",
                    ]
                )
            }
        )

        self.assertEqual(adapter._foreground_session_note(self.device, "com.example.game", "com.example.game"), "")
        self.assertEqual(adapter._foreground_session_note(self.device, "com.example.game", "com.android.vending"), "")
        self.assertEqual(
            adapter._foreground_session_note(self.device, "com.example.game", "com.android.vending"),
            "目标应用不在前台，当前前台为 com.android.vending。",
        )

    def test_foreground_recovery_window_covers_ad_return_surface_rebuild(self) -> None:
        adapter = FakeAndroidAdapter({})

        adapter._foreground_session_note(self.device, "com.example.game", "com.ad.network")
        notes = [
            adapter._foreground_session_note(self.device, "com.example.game", "com.example.game")
            for _index in range(adapter._FOREGROUND_RECOVERY_SAMPLE_COUNT)
        ]

        self.assertTrue(all("目标应用刚回到前台" in note for note in notes))
        self.assertEqual(
            adapter._foreground_session_note(self.device, "com.example.game", "com.example.game"),
            "",
        )

    def test_gfxinfo_counter_fps_uses_counter_read_time_to_reduce_low_end_rate_jitter(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys gfxinfo com.example.game": "\n---NEXT---\n".join(
                    [
                        "Total frames rendered: 100\nJanky frames: 5",
                        "Total frames rendered: 220\nJanky frames: 17",
                    ]
                )
            }
        )

        with patch("mobileperflab.time.time", side_effect=[10.8, 12.8]):
            first = adapter._gfxinfo_counter_fps_and_jank(self.device, "com.example.game", 10.0)
            second = adapter._gfxinfo_counter_fps_and_jank(self.device, "com.example.game", 11.0)

        self.assertIsNone(first)
        self.assertEqual(second, (60.0, 0.0))

    def test_gfxinfo_counter_jank_uses_fps_deficit_instead_of_raw_janky_ratio(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys gfxinfo com.example.game": "\n---NEXT---\n".join(
                    [
                        "Total frames rendered: 100\nJanky frames: 10",
                        "Total frames rendered: 190\nJanky frames: 60",
                    ]
                )
            }
        )

        with patch("mobileperflab.time.time", side_effect=[10.0, 12.0]):
            first = adapter._gfxinfo_counter_fps_and_jank(self.device, "com.example.game", 10.0)
            second = adapter._gfxinfo_counter_fps_and_jank(self.device, "com.example.game", 12.0)

        self.assertIsNone(first)
        self.assertEqual(second, (45.0, 25.0))

    def test_fps_counter_source_does_not_fall_back_to_heavy_collectors_on_no_frame_delta(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys gfxinfo com.example.game": "\n---NEXT---\n".join(
                    [
                        "Total frames rendered: 100\nJanky frames: 5",
                        "Total frames rendered: 100\nJanky frames: 5",
                    ]
                ),
                "dumpsys gfxinfo com.example.game framestats": """
                ---PROFILEDATA---
                IntendedVsync,FrameCompleted,Flags
                1,1000000000,0
                2,1016666666,0
                ---PROFILEDATA---
                """,
                "dumpsys SurfaceFlinger --list": "SurfaceView[com.example.game/com.example.game.MainActivity](BLAST)#1",
            }
        )

        with patch("mobileperflab.time.time", side_effect=[10.0, 11.0]):
            self.assertEqual(adapter._fps_and_jank(self.device, "com.example.game", 10.0), (0.0, 0.0))
            self.assertEqual(adapter._fps_and_jank(self.device, "com.example.game", 11.0), (0.0, 0.0))

        self.assertNotIn("dumpsys gfxinfo com.example.game framestats", adapter.calls)
        self.assertNotIn("dumpsys SurfaceFlinger --list", adapter.calls)

    def test_fps_counter_source_reprobes_heavy_collectors_after_repeated_no_frame_delta(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys gfxinfo com.example.game": "\n---NEXT---\n".join(
                    [
                        "Total frames rendered: 100\nJanky frames: 5",
                        "Total frames rendered: 100\nJanky frames: 5",
                        "Total frames rendered: 100\nJanky frames: 5",
                    ]
                ),
                "dumpsys gfxinfo com.example.game framestats": """
                ---PROFILEDATA---
                IntendedVsync,FrameCompleted,Flags
                1,1000000000,0
                2,1016666666,0
                3,1033333332,0
                ---PROFILEDATA---
                """,
            }
        )

        with patch("mobileperflab.time.time", side_effect=[10.0, 11.0, 12.0]):
            self.assertEqual(adapter._fps_and_jank(self.device, "com.example.game", 10.0), (0.0, 0.0))
            self.assertEqual(adapter._fps_and_jank(self.device, "com.example.game", 11.0), (0.0, 0.0))
            fps, jank = adapter._fps_and_jank(self.device, "com.example.game", 12.0)

        self.assertGreater(fps, 0.0)
        self.assertEqual(jank, 0.0)
        self.assertEqual(adapter.calls.count("dumpsys gfxinfo com.example.game framestats"), 1)

    def test_fps_counter_reset_does_not_trigger_heavy_reprobe(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys gfxinfo com.example.game": "\n---NEXT---\n".join(
                    [
                        "Total frames rendered: 320\nJanky frames: 12",
                        "Total frames rendered: 18\nJanky frames: 1",
                        "Total frames rendered: 54\nJanky frames: 4",
                    ]
                ),
                "dumpsys gfxinfo com.example.game framestats": """
                ---PROFILEDATA---
                IntendedVsync,FrameCompleted,Flags
                1,1000000000,0
                2,1016666666,0
                ---PROFILEDATA---
                """,
                "dumpsys SurfaceFlinger --list": "SurfaceView[com.example.game/com.example.game.MainActivity](BLAST)#1",
            }
        )

        with patch("mobileperflab.time.time", side_effect=[10.0, 11.0, 12.0]):
            self.assertEqual(adapter._fps_and_jank(self.device, "com.example.game", 10.0), (0.0, 0.0))
            self.assertEqual(adapter._fps_and_jank(self.device, "com.example.game", 11.0), (0.0, 0.0))
            self.assertEqual(adapter._fps_and_jank(self.device, "com.example.game", 12.0), (36.0, 40.0))

        self.assertNotIn("dumpsys gfxinfo com.example.game framestats", adapter.calls)
        self.assertNotIn("dumpsys SurfaceFlinger --list", adapter.calls)

    def test_surface_layer_frame_counter_gives_stable_fps_before_latency_fallback(self) -> None:
        surface = "4dc50d SurfaceView[com.example.game/com.example.game.MainActivity]@0(BLAST)#29474"
        surface_dump_first = "\n".join(
            [
                f"  Layer [29474] {surface}",
                "    visible reason= buffer=131048042135557 frame=41058 contentDirty",
                "    metadata{9:4bytes}",
                "    frameRate: 60.00 Hz, category: Default, selectionStrategy: Propagate, uid: 10856",
            ]
        )
        surface_dump_second = surface_dump_first.replace("frame=41058", "frame=41148")
        adapter = FakeAndroidAdapter(
            {
                "dumpsys SurfaceFlinger": f"{surface_dump_first}\n---NEXT---\n{surface_dump_second}",
                "dumpsys gfxinfo com.example.game framestats": "",
                "dumpsys SurfaceFlinger --list": surface,
                f"dumpsys SurfaceFlinger --latency '{surface}'": "\n".join(
                    [
                        "16666666",
                        "1000000000 1100000000 0",
                        "1050000000 1100000000 0",
                        "1066666666 1100000000 1050000000",
                    ]
                ),
            }
        )

        with patch("mobileperflab.time.time", side_effect=[10.0, 11.5]):
            self.assertEqual(adapter._surface_layer_counter_fps_and_jank(self.device, "com.example.game"), (60.0, 0.0))
            fps, jank = adapter._surface_fps_and_jank(self.device, "com.example.game", 11.5)

        self.assertEqual(fps, 60.0)
        self.assertEqual(jank, 0.0)
        self.assertNotIn(f"dumpsys SurfaceFlinger --latency '{surface}'", adapter.calls)

    def test_surface_layer_frame_rate_wins_when_counter_under_reports_unity_blast_fps(self) -> None:
        surface = "4dc50d SurfaceView[com.example.game/com.example.game.MainActivity]@0(BLAST)#29474"
        first = "\n".join(
            [
                f"  Layer [29474] {surface}",
                "    visible reason= buffer=131048042135557 frame=41058 contentDirty",
                "    frameRate: 60.00 Hz, category: Default, selectionStrategy: Propagate, uid: 10856",
            ]
        )
        second = first.replace("frame=41058", "frame=41125")
        adapter = FakeAndroidAdapter({"dumpsys SurfaceFlinger": f"{first}\n---NEXT---\n{second}"})

        with patch("mobileperflab.time.time", side_effect=[10.0, 11.5]):
            adapter._surface_layer_counter_fps_and_jank(self.device, "com.example.game")
            fps, jank = adapter._surface_layer_counter_fps_and_jank(self.device, "com.example.game")

        self.assertEqual(fps, 60.0)
        self.assertEqual(jank, 0.0)

    def test_collect_sample_runs_android_metrics_in_parallel_to_reduce_low_end_drift(self) -> None:
        adapter = SlowMetricAndroidAdapter(sleep_seconds=0.04)

        started = time.perf_counter()
        sample = adapter.collect_sample(self.device, "com.example.game", started)
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.16)
        self.assertEqual(sample.fps, 58.0)
        self.assertEqual(sample.cpu_percent, 22.0)
        self.assertEqual(sample.memory_mb, 512.0)
        self.assertEqual(set(adapter.calls), {"foreground", "fps", "battery", "network", "cpu", "memory"})

    def test_collect_sample_keeps_raw_cpu_and_adds_perfdog_style_normalized_cpu(self) -> None:
        adapter = NormalizedCpuAndroidAdapter(sleep_seconds=0.0)

        sample = adapter.collect_sample(self.device, "com.example.game", time.time())

        self.assertEqual(sample.cpu_percent, 200.0)
        self.assertEqual(sample.cpu_core_count, 8)
        self.assertEqual(sample.cpu_normalized_percent, 12.5)

    def test_collect_sample_marks_in_app_ad_surface_even_when_fps_is_available(self) -> None:
        adapter = AdSurfaceMetricAndroidAdapter(sleep_seconds=0.0)

        sample = adapter.collect_sample(self.device, "com.example.game", time.time())

        self.assertEqual(sample.fps, 58.0)
        self.assertIn("目标 App 正在播放广告 Surface", sample.note)
        self.assertIn("com.applovin.adview", sample.note)

    def test_collect_sample_marks_in_package_ad_activity_even_when_package_stays_foreground(self) -> None:
        adapter = InPackageAdActivityAndroidAdapter(sleep_seconds=0.0)

        sample = adapter.collect_sample(self.device, "com.example.game", time.time())

        self.assertEqual(sample.fps, 58.0)
        self.assertIn("目标 App 正在播放广告 Activity", sample.note)
        self.assertIn("com.applovin.adview.AppLovinFullscreenActivity", sample.note)

    def test_cpu_normalization_capacity_ignores_partial_frequency_coverage(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "getconf _NPROCESSORS_ONLN": "8",
                "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq": "1200000",
                "cat /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq": "2400000",
            }
        )

        core_count, current_total, max_total = adapter._cpu_normalization_capacity(self.device)

        self.assertEqual(core_count, 8)
        self.assertEqual(current_total, 0.0)
        self.assertEqual(max_total, 0.0)

    def test_collect_sample_keeps_partial_android_data_when_one_metric_fails(self) -> None:
        adapter = FailingMetricAndroidAdapter(sleep_seconds=0.0)

        sample = adapter.collect_sample(self.device, "com.example.game", time.time())

        self.assertEqual(sample.fps, 58.0)
        self.assertEqual(sample.memory_mb, 512.0)
        self.assertEqual(sample.temperature_c, 36.0)
        self.assertEqual(sample.cpu_percent, 0.0)
        self.assertIn("Android CPU 采集失败", sample.note)

    def test_collect_sample_does_not_scan_all_proc_cmdlines_when_target_pid_is_missing(self) -> None:
        adapter = MissingPidAndroidAdapter()

        sample = adapter.collect_sample(self.device, "com.example.missing", time.time())

        self.assertEqual(sample.battery_percent, 88.0)
        self.assertEqual(sample.temperature_c, 36.0)
        self.assertIn("Android 目标进程未找到", sample.note)
        proc_cmdline_calls = [command for command in adapter.calls if "/proc/" in command and "cmdline" in command]
        self.assertLessEqual(len(proc_cmdline_calls), 4)

    def test_collect_sample_resets_delta_caches_when_target_returns_to_foreground(self) -> None:
        adapter = ForegroundSequenceAndroidAdapter(["com.example.game"])
        key = (self.device.serial, "com.example.game")
        adapter._foreground_missing.add(key)
        adapter._frame_cache[key] = (1.0, 100, 10)
        adapter._framestats_cache[key] = (1.0, 200)
        adapter._surface_frame_cache[key] = (1.0, 300)
        adapter._surface_layer_counter_cache[key] = (1.0, 400)
        adapter._net_cache[key] = (1.0, 1_000, 2_000)
        adapter._device_net_cache[key] = (1.0, 3_000, 4_000)
        adapter._cpu_proc_cache[key] = (1.0, {101: 100})

        sample = adapter.collect_sample(self.device, "com.example.game", time.time())

        self.assertIn("目标应用刚回到前台", sample.note)
        self.assertNotIn(key, adapter._frame_cache)
        self.assertNotIn(key, adapter._framestats_cache)
        self.assertNotIn(key, adapter._surface_frame_cache)
        self.assertNotIn(key, adapter._surface_layer_counter_cache)
        self.assertNotIn(key, adapter._net_cache)
        self.assertNotIn(key, adapter._device_net_cache)
        self.assertNotIn(key, adapter._cpu_proc_cache)

    def test_collect_sample_reuses_recent_foreground_result_to_avoid_slow_dumpsys_every_second(self) -> None:
        adapter = ForegroundSequenceAndroidAdapter(["com.example.game", "com.example.home", "com.example.home"])

        with patch("mobileperflab.time.time", side_effect=[100.0, 100.5, 103.0, 105.5]):
            first = adapter.collect_sample(self.device, "com.example.game", 100.0)
            second = adapter.collect_sample(self.device, "com.example.game", 100.0)
            third = adapter.collect_sample(self.device, "com.example.game", 100.0)
            fourth = adapter.collect_sample(self.device, "com.example.game", 100.0)

        self.assertNotIn("目标应用不在前台", first.note)
        self.assertNotIn("目标应用不在前台", second.note)
        self.assertNotIn("目标应用不在前台", third.note)
        self.assertIn("目标应用不在前台", fourth.note)
        self.assertEqual(adapter.calls.count("foreground"), 3)

    def test_collect_sample_does_not_emit_alternating_foreground_events_for_single_ad_probe_mismatches(self) -> None:
        adapter = ForegroundSequenceAndroidAdapter(
            ["com.example.game", "com.ad.network", "com.example.game", "com.ad.network", "com.example.game"]
        )

        with patch("mobileperflab.time.time", side_effect=[100.0, 103.0, 106.0, 109.0, 112.0]):
            samples = [
                adapter.collect_sample(self.device, "com.example.game", 100.0)
                for _index in range(5)
            ]

        notes = [sample.note for sample in samples]
        self.assertTrue(all("目标应用不在前台" not in note for note in notes))
        self.assertTrue(all("目标应用刚回到前台" not in note for note in notes))

    def test_cached_foreground_app_uses_light_probe_to_catch_fast_background_switch(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "cmd activity get-foreground-activities": "\n---NEXT---\n".join(
                    [
                        "packageName=com.example.game",
                        "packageName=com.example.home",
                    ]
                ),
                "dumpsys window": "mCurrentFocus=Window{42ab com.example.game/com.example.game.MainActivity}",
            }
        )

        first = adapter._cached_foreground_app(self.device, "com.example.game", 100.0)
        second = adapter._cached_foreground_app(self.device, "com.example.game", 100.5)

        self.assertEqual(first, "com.example.game")
        self.assertEqual(second, "com.example.home")
        self.assertNotIn("dumpsys activity activities", adapter.calls)
        self.assertEqual(adapter.calls.count("cmd activity get-foreground-activities"), 2)

    def test_cached_foreground_app_reuses_recent_heavy_probe_when_light_probe_is_empty(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "cmd activity get-foreground-activities": "",
                "dumpsys window": "mCurrentFocus=Window{42ab com.example.game/com.example.game.MainActivity}",
            }
        )

        first = adapter._cached_foreground_app(self.device, "com.example.game", 100.0)
        second = adapter._cached_foreground_app(self.device, "com.example.game", 100.5)

        self.assertEqual(first, "com.example.game")
        self.assertEqual(second, "com.example.game")
        self.assertEqual(adapter.calls.count("cmd activity get-foreground-activities"), 2)
        self.assertEqual(adapter.calls.count("dumpsys window"), 1)

    def test_collect_sample_reuses_metric_executor_during_session(self) -> None:
        adapter = SlowMetricAndroidAdapter(sleep_seconds=0.0)

        adapter.collect_sample(self.device, "com.example.game", 100.0)
        first_executor = adapter._metric_executor
        adapter.collect_sample(self.device, "com.example.game", 100.0)

        self.assertIsNotNone(first_executor)
        self.assertIs(adapter._metric_executor, first_executor)

        adapter.stop_session(self.device, "com.example.game")

        self.assertIsNone(adapter._metric_executor)

    def test_start_session_clears_cached_uid_after_app_reinstall(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys package com.example.game": "userId=20234",
            }
        )
        key = (self.device.serial, "com.example.game")
        adapter._uid_cache[key] = 10234

        adapter.start_session(self.device, "com.example.game")

        self.assertNotIn(key, adapter._uid_cache)
        self.assertEqual(adapter._app_uid(self.device, "com.example.game"), 20234)

    def test_android_collection_diagnostics_reports_healthy_sources(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "mCurrentFocus=Window{42ab com.example.game/com.example.game.MainActivity}",
                "pidof com.example.game": "101 202",
                "dumpsys package com.example.game": "userId=10234",
                "dumpsys gfxinfo com.example.game": "\n".join(
                    [
                        "Total frames rendered: 100",
                        "Janky frames: 4",
                    ]
                ),
                "dumpsys gfxinfo com.example.game framestats": "",
                "dumpsys SurfaceFlinger --list": "SurfaceView[com.example.game/com.example.game.MainActivity](BLAST)#1",
                "dumpsys window": "\n".join(
                    [
                        "mCurrentFocus=Window{42ab com.example.game/com.example.game.MainActivity}",
                        "Surface(name=SurfaceView[com.example.game/com.example.game.MainActivity](BLAST)#1)",
                    ]
                ),
                "cat /proc/uid_stat/10234/tcp_rcv": "1024",
                "cat /proc/uid_stat/10234/tcp_snd": "512",
            }
        )
        adapter._frame_cache[(self.device.serial, "com.example.game")] = (9.0, 40, 2)

        diagnostics = adapter.collection_diagnostics(self.device, "com.example.game", now=10.0)

        self.assertEqual(diagnostics.overall_state, "ok")
        self.assertEqual(diagnostics.foreground_app, "com.example.game")
        self.assertEqual(diagnostics.pid_source, "pidof")
        self.assertEqual(diagnostics.pids, [101, 202])
        self.assertEqual(diagnostics.uid, 10234)
        self.assertEqual(diagnostics.uid_source, "dumpsys package")
        self.assertEqual(diagnostics.fps_source, "gfxinfo counters")
        self.assertEqual(diagnostics.network_source, "per-UID")

    def test_android_collection_diagnostics_marks_fps_counter_no_delta_as_limited(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "mCurrentFocus=Window{42ab com.example.game/com.example.game.MainActivity}",
                "pidof com.example.game": "101",
                "dumpsys package com.example.game": "userId=10234",
                "dumpsys gfxinfo com.example.game": "Total frames rendered: 100\nJanky frames: 4\n",
                "dumpsys gfxinfo com.example.game framestats": "",
                "dumpsys SurfaceFlinger --list": "",
                "cat /proc/uid_stat/10234/tcp_rcv": "4096",
                "cat /proc/uid_stat/10234/tcp_snd": "2048",
            }
        )
        adapter._frame_cache[(self.device.serial, "com.example.game")] = (9.0, 100, 4)

        diagnostics = adapter.collection_diagnostics(self.device, "com.example.game", now=10.0)
        formatted = format_android_collection_diagnostics(diagnostics)
        rows = collection_diagnostic_status_rows(diagnostics)

        self.assertEqual(diagnostics.overall_state, "warning")
        self.assertEqual(diagnostics.fps_source, "gfxinfo counters · 当前无新增帧")
        self.assertIn(("FPS", "源可读", "gfxinfo counters 当前无新增帧，页面静止或三星/低端机短采样窗口可能导致计数不增长"), diagnostics.rows)
        self.assertIn("FPS: 源可读", formatted)
        self.assertIn(("FPS", "受限", "gfxinfo counters 当前无新增帧，页面静止或三星/低端机短采样窗口可能导致计数不增长。下一步：保持页面可见并产生动画/滚动，再检查 gfxinfo 或 SurfaceFlinger 来源。", "limited"), rows)

    def test_android_collection_diagnostics_marks_surfaceflinger_no_delta_as_limited(self) -> None:
        surface = "SurfaceView[com.example.game/com.example.game.MainActivity](BLAST)#1"
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "mCurrentFocus=Window{42ab com.example.game/com.example.game.MainActivity}",
                "pidof com.example.game": "101",
                "dumpsys package com.example.game": "userId=10234",
                "dumpsys gfxinfo com.example.game": "",
                "dumpsys gfxinfo com.example.game framestats": "",
                "dumpsys SurfaceFlinger --list": surface,
                f"dumpsys SurfaceFlinger --latency '{surface}'": "\n".join(
                    [
                        "16666666",
                        "0 1000000000 1000000000",
                        "0 1016666666 1016666666",
                    ]
                ),
                "cat /proc/uid_stat/10234/tcp_rcv": "4096",
                "cat /proc/uid_stat/10234/tcp_snd": "2048",
            }
        )
        adapter._surface_frame_cache[(self.device.serial, "com.example.game")] = (9.0, 1016666666)

        diagnostics = adapter.collection_diagnostics(self.device, "com.example.game", now=10.0)

        self.assertEqual(diagnostics.overall_state, "warning")
        self.assertEqual(diagnostics.fps_source, f"SurfaceFlinger: {surface} · 当前无新增帧")
        self.assertIn(("FPS", "源可读", f"SurfaceFlinger: {surface} 当前无新增帧，页面静止或三星/低端机短采样窗口可能导致计数不增长"), diagnostics.rows)

    def test_android_collection_diagnostics_runs_independent_probes_concurrently(self) -> None:
        adapter = SlowDiagnosticAndroidAdapter(sleep_seconds=0.04)

        start = time.monotonic()
        diagnostics = adapter.collection_diagnostics(self.device, "com.example.game", now=10.0)
        elapsed = time.monotonic() - start

        self.assertEqual(diagnostics.overall_state, "ok")
        self.assertEqual(set(adapter.calls), {"foreground", "pid", "uid", "fps", "network"})
        self.assertLess(elapsed, 0.17)

    def test_android_collection_diagnostics_treats_zero_uid_stat_as_per_uid_available(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "mCurrentFocus=Window{42ab com.example.game/com.example.game.MainActivity}",
                "pidof com.example.game": "101",
                "dumpsys package com.example.game": "userId=10234",
                "dumpsys gfxinfo com.example.game": "Total frames rendered: 100\nJanky frames: 4\n",
                "cat /proc/uid_stat/10234/tcp_rcv": "0",
                "cat /proc/uid_stat/10234/tcp_snd": "0",
                "cat /proc/net/dev": "Inter-| Receive | Transmit\n wlan0: 100000 0 0 0 0 0 0 0 200000 0 0 0 0 0 0 0",
            }
        )

        diagnostics = adapter.collection_diagnostics(self.device, "com.example.game", now=10.0)
        formatted = format_android_collection_diagnostics(diagnostics)

        self.assertEqual(diagnostics.network_source, "per-UID")
        self.assertIn("网络: per-UID", formatted)
        self.assertNotIn("cat /proc/net/dev", adapter.calls)

    def test_android_collection_diagnostics_marks_zero_uid_stat_as_no_traffic_not_normal(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "mCurrentFocus=Window{42ab com.example.game/com.example.game.MainActivity}",
                "pidof com.example.game": "101",
                "dumpsys package com.example.game": "userId=10234",
                "dumpsys gfxinfo com.example.game": "Total frames rendered: 100\nJanky frames: 4\n",
                "cat /proc/uid_stat/10234/tcp_rcv": "0",
                "cat /proc/uid_stat/10234/tcp_snd": "0",
                "cat /proc/net/dev": "Inter-| Receive | Transmit\n wlan0: 100000 0 0 0 0 0 0 0 200000 0 0 0 0 0 0 0",
            }
        )

        diagnostics = adapter.collection_diagnostics(self.device, "com.example.game", now=10.0)
        formatted = format_android_collection_diagnostics(diagnostics)
        rows = collection_diagnostic_status_rows(diagnostics)

        self.assertEqual(diagnostics.overall_state, "warning")
        self.assertEqual(diagnostics.network_source, "per-UID")
        self.assertIn(("网络", "per-UID 无流量", "per-UID 可读，但目标 App 当前累计上下行为 0；请在 App 内触发下载/上传或联网请求"), diagnostics.rows)
        self.assertIn("网络: per-UID 无流量", formatted)
        self.assertIn(("网络", "受限", "per-UID 可读，但目标 App 当前累计上下行为 0；请在 App 内触发下载/上传或联网请求。下一步：制造明确下载/上传动作，确认 per-UID 网络统计可读；设备级兜底只作趋势参考。", "limited"), rows)

    def test_android_collection_diagnostics_marks_per_uid_no_delta_as_no_traffic(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "mCurrentFocus=Window{42ab com.example.game/com.example.game.MainActivity}",
                "pidof com.example.game": "101",
                "dumpsys package com.example.game": "userId=10234",
                "dumpsys gfxinfo com.example.game": "Total frames rendered: 100\nJanky frames: 4\n",
                "cat /proc/uid_stat/10234/tcp_rcv": "4096",
                "cat /proc/uid_stat/10234/tcp_snd": "2048",
            }
        )
        adapter._net_cache[(self.device.serial, "com.example.game")] = (9.0, 4096, 2048)

        diagnostics = adapter.collection_diagnostics(self.device, "com.example.game", now=10.0)

        self.assertEqual(diagnostics.overall_state, "warning")
        self.assertEqual(diagnostics.network_source, "per-UID")
        self.assertIn(("网络", "per-UID 无流量", "per-UID 可读，但最近采样没有上下行增量；请在 App 内触发下载/上传或联网请求"), diagnostics.rows)

    def test_android_collection_diagnostics_treats_zero_netstats_uid_as_per_uid_available(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "mCurrentFocus=Window{42ab com.example.game/com.example.game.MainActivity}",
                "pidof com.example.game": "101",
                "dumpsys package com.example.game": "userId=10234",
                "dumpsys gfxinfo com.example.game": "Total frames rendered: 100\nJanky frames: 4\n",
                "cat /proc/uid_stat/10234/tcp_rcv": "",
                "cat /proc/uid_stat/10234/tcp_snd": "",
                "cat /proc/net/xt_qtaguid/stats": "",
                "dumpsys netstats detail": "Bucket{uid=10234 tag=0x0 set=DEFAULT}: rx_bytes=0 rx_packets=0 tx_bytes=0 tx_packets=0",
                "cat /proc/net/dev": "Inter-| Receive | Transmit\n wlan0: 100000 0 0 0 0 0 0 0 200000 0 0 0 0 0 0 0",
            }
        )

        diagnostics = adapter.collection_diagnostics(self.device, "com.example.game", now=10.0)
        formatted = format_android_collection_diagnostics(diagnostics)

        self.assertEqual(diagnostics.network_source, "per-UID")
        self.assertIn("网络: per-UID", formatted)
        self.assertNotIn("cat /proc/net/dev", adapter.calls)

    def test_android_collection_diagnostics_explains_fallbacks_and_missing_channels(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "mCurrentFocus=Window{42ab com.example.home/com.example.home.MainActivity}",
                "pidof com.example.game": "",
                "pgrep -f com.example.game": "",
                "ps -A -o PID=,NAME=": "",
                "ps -A": "",
                "dumpsys package com.example.game": "",
                "pm list packages -U com.example.game": "",
                "cmd package list packages -U com.example.game": "",
                "dumpsys gfxinfo com.example.game": "",
                "dumpsys gfxinfo com.example.game framestats": "",
                "dumpsys SurfaceFlinger --list": "",
                "cat /proc/net/dev": "Inter-| Receive | Transmit\n wlan0: 100000 0 0 0 0 0 0 0 200000 0 0 0 0 0 0 0",
            }
        )

        diagnostics = adapter.collection_diagnostics(self.device, "com.example.game", now=10.0)
        formatted = format_android_collection_diagnostics(diagnostics)

        self.assertEqual(diagnostics.overall_state, "warning")
        self.assertEqual(diagnostics.foreground_state, "mismatch")
        self.assertEqual(diagnostics.pid_source, "missing")
        self.assertEqual(diagnostics.uid_source, "missing")
        self.assertEqual(diagnostics.fps_source, "missing")
        self.assertEqual(diagnostics.network_source, "device")
        self.assertIn("前台不一致", formatted)
        self.assertIn("PID: 未找到", formatted)
        self.assertIn("FPS: 不可用", formatted)
        self.assertIn("网络: 设备级兜底", formatted)


if __name__ == "__main__":
    unittest.main()
