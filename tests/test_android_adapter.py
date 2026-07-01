import unittest
from unittest.mock import patch

from mobileperflab import AndroidAdapter, DeviceInfo


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

    def test_foreground_app_falls_back_to_activity_stack_when_window_has_no_package(self) -> None:
        adapter = FakeAndroidAdapter(
            {
                "dumpsys window": "mCurrentFocus=null",
                "dumpsys activity activities": "mResumedActivity: ActivityRecord{42 u0 com.example.game/.MainActivity t7}",
            }
        )

        self.assertEqual(adapter.foreground_app(self.device), "com.example.game")
        self.assertIn("dumpsys activity activities", adapter.calls)

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

    def test_netstats_parser_reads_named_uid_bucket_values(self) -> None:
        output = """
        Bucket{uid=10234 tag=0x0 set=DEFAULT metered=false defaultNetwork=true}:
          NetworkStatsHistory: bucketDuration=3600
          bucketStart=1710000000000 activeTime=2500 rxBytes=4096 rxPackets=8 txBytes=2048 txPackets=4 operations=0
        Bucket{uid=10234 tag=0x0 set=FOREGROUND} bucketStart=1710000001000 rxBytes=1024 rxPackets=2 txBytes=512 txPackets=1
        """

        self.assertEqual(AndroidAdapter._parse_netstats_detail_for_uid(output, 10234), (5120, 2560))

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

        rx1, tx1 = adapter._network_kbps(self.device, "com.example.game", 10.0)
        rx2, tx2 = adapter._network_kbps(self.device, "com.example.game", 11.0)

        self.assertEqual((rx1, tx1), (0.0, 0.0))
        self.assertAlmostEqual(rx2, 4.0)
        self.assertAlmostEqual(tx2, 2.0)
        self.assertIn("设备级网络兜底", adapter._network_note_cache[("serial-1", "com.example.game")])

    def test_qtaguid_parser_sums_matching_uid_rows(self) -> None:
        output = """
        idx iface acct_tag_hex uid_tag_int cnt_set rx_bytes rx_packets tx_bytes tx_packets
        2 wlan0 0x0 10234 0 1000 10 500 5
        3 wlan0 0x0 10234 1 3000 30 1500 15
        4 wlan0 0x0 10000 0 9999 9 9999 9
        """

        self.assertEqual(AndroidAdapter._parse_qtaguid_stats(output, 10234), (4000, 2000))


if __name__ == "__main__":
    unittest.main()
