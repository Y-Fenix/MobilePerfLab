import time
import unittest
from unittest.mock import patch

from mobileperflab import AndroidAdapter, DeviceInfo, format_android_collection_diagnostics


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

    def _memory_mb(self, device: DeviceInfo, app_id: str) -> float:
        self._record("memory")
        return 512.0


class FailingMetricAndroidAdapter(SlowMetricAndroidAdapter):
    def _cpu_percent(self, device: DeviceInfo, app_id: str) -> float:
        self._record("cpu")
        raise RuntimeError("proc denied")


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

    def test_qtaguid_parser_sums_matching_uid_rows(self) -> None:
        output = """
        idx iface acct_tag_hex uid_tag_int cnt_set rx_bytes rx_packets tx_bytes tx_packets
        2 wlan0 0x0 10234 0 1000 10 500 5
        3 wlan0 0x0 10234 1 3000 30 1500 15
        4 wlan0 0x0 10000 0 9999 9 9999 9
        """

        self.assertEqual(AndroidAdapter._parse_qtaguid_stats(output, 10234), (4000, 2000))

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
        self.assertEqual(adapter._foreground_recovery_remaining[key], 1)
        self.assertEqual(
            adapter._foreground_session_note(self.device, "com.example.game", "com.example.game"),
            "目标应用刚回到前台，恢复窗口内 FPS/CPU 可能受 Surface 和进程缓存重建影响。",
        )
        self.assertEqual(adapter._foreground_session_note(self.device, "com.example.game", "com.example.game"), "")

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
        self.assertEqual(second, (60.0, 10.0))

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

    def test_collect_sample_keeps_partial_android_data_when_one_metric_fails(self) -> None:
        adapter = FailingMetricAndroidAdapter(sleep_seconds=0.0)

        sample = adapter.collect_sample(self.device, "com.example.game", time.time())

        self.assertEqual(sample.fps, 58.0)
        self.assertEqual(sample.memory_mb, 512.0)
        self.assertEqual(sample.temperature_c, 36.0)
        self.assertEqual(sample.cpu_percent, 0.0)
        self.assertIn("Android CPU 采集失败", sample.note)

    def test_collect_sample_resets_delta_caches_when_target_returns_to_foreground(self) -> None:
        adapter = ForegroundSequenceAndroidAdapter(["com.example.game"])
        key = (self.device.serial, "com.example.game")
        adapter._foreground_missing.add(key)
        adapter._frame_cache[key] = (1.0, 100, 10)
        adapter._framestats_cache[key] = (1.0, 200)
        adapter._surface_frame_cache[key] = (1.0, 300)
        adapter._net_cache[key] = (1.0, 1_000, 2_000)
        adapter._device_net_cache[key] = (1.0, 3_000, 4_000)
        adapter._cpu_proc_cache[key] = (1.0, {101: 100})

        sample = adapter.collect_sample(self.device, "com.example.game", time.time())

        self.assertIn("目标应用刚回到前台", sample.note)
        self.assertNotIn(key, adapter._frame_cache)
        self.assertNotIn(key, adapter._framestats_cache)
        self.assertNotIn(key, adapter._surface_frame_cache)
        self.assertNotIn(key, adapter._net_cache)
        self.assertNotIn(key, adapter._device_net_cache)
        self.assertNotIn(key, adapter._cpu_proc_cache)

    def test_collect_sample_reuses_recent_foreground_result_to_avoid_slow_dumpsys_every_second(self) -> None:
        adapter = ForegroundSequenceAndroidAdapter(["com.example.game", "com.example.home"])

        with patch("mobileperflab.time.time", side_effect=[100.0, 100.5, 103.0]):
            first = adapter.collect_sample(self.device, "com.example.game", 100.0)
            second = adapter.collect_sample(self.device, "com.example.game", 100.0)
            third = adapter.collect_sample(self.device, "com.example.game", 100.0)

        self.assertNotIn("目标应用不在前台", first.note)
        self.assertNotIn("目标应用不在前台", second.note)
        self.assertIn("目标应用不在前台", third.note)
        self.assertEqual(adapter.calls.count("foreground"), 2)

    def test_collect_sample_reuses_metric_executor_during_session(self) -> None:
        adapter = SlowMetricAndroidAdapter(sleep_seconds=0.0)

        adapter.collect_sample(self.device, "com.example.game", 100.0)
        first_executor = adapter._metric_executor
        adapter.collect_sample(self.device, "com.example.game", 100.0)

        self.assertIsNotNone(first_executor)
        self.assertIs(adapter._metric_executor, first_executor)

        adapter.stop_session(self.device, "com.example.game")

        self.assertIsNone(adapter._metric_executor)

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
