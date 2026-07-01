import socket
import threading
import unittest

from mobileperflab import (
    AndroidAdapter,
    DeviceInfo,
    ProxyTrafficSnapshot,
    ProxyTrafficHistory,
    WeakNetworkProxy,
    WeakProxyDeviceRegistry,
    build_weak_network_diagnostics,
    build_weak_network_effectiveness,
    build_weak_network_report_payload,
    format_weak_network_config,
    format_live_proxy_summary,
    format_proxy_traffic_snapshot,
    weak_hit_status_text,
    weak_readiness_display_text,
    verify_android_proxy_state,
)


class FakeAndroid:
    def __init__(self) -> None:
        self.cleared: list[str] = []

    def clear_http_proxy(self, device: DeviceInfo) -> tuple[bool, str]:
        self.cleared.append(device.serial)
        return True, ""


class FakeProbeAndroid(AndroidAdapter):
    def __init__(self, responses: list[tuple[int, str]]) -> None:
        self.responses = responses
        self.commands: list[str] = []

    def _adb(self, serial: str, shell_args: list[str], timeout: float = 8.0) -> tuple[int, str]:
        self.commands.append(shell_args[-1])
        return self.responses.pop(0)


class WeakProxyDeviceRegistryTest(unittest.TestCase):
    def test_cleans_only_registered_android_devices_once(self) -> None:
        registry = WeakProxyDeviceRegistry()
        android = FakeAndroid()
        first = DeviceInfo("Android", "serial-1", "Phone", "13", "P", "ready")
        second = DeviceInfo("Android", "serial-2", "Phone2", "14", "P2", "ready")

        registry.mark_applied(first, "192.168.1.2:18888")
        registry.mark_applied(first, "192.168.1.2:18888")
        registry.mark_applied(second, "192.168.1.2:18888")

        self.assertEqual(registry.cleanup(android), ["serial-1", "serial-2"])
        self.assertEqual(android.cleared, ["serial-1", "serial-2"])
        self.assertEqual(registry.cleanup(android), [])


class WeakProxyStopCleanupTest(unittest.TestCase):
    def test_stop_weak_proxy_cleans_registered_android_devices(self) -> None:
        class FakeStatus:
            def __init__(self) -> None:
                self.values: list[str] = []

            def set(self, value: str) -> None:
                self.values.append(value)

        class FakeWeakProxy:
            def __init__(self) -> None:
                self.stopped = False

            def stop(self) -> None:
                self.stopped = True

        from mobileperflab import App

        app = object.__new__(App)
        app.weak_proxy = FakeWeakProxy()
        app.weak_registry = WeakProxyDeviceRegistry()
        app.android = FakeAndroid()
        app.weak_status_var = FakeStatus()
        app.preview_calls = 0
        app.diagnostic_calls = 0
        app.traffic_calls = 0
        app.logs = []
        app._refresh_proxy_preview = lambda: setattr(app, "preview_calls", app.preview_calls + 1)
        app._refresh_weak_diagnostics = lambda: setattr(app, "diagnostic_calls", app.diagnostic_calls + 1)
        app._refresh_proxy_traffic = lambda: setattr(app, "traffic_calls", app.traffic_calls + 1)
        app.append_log = lambda text: app.logs.append(text)
        device = DeviceInfo("Android", "serial-1", "Phone", "13", "P", "ready")
        app.weak_registry.mark_applied(device, "192.168.1.2:18888")

        app.stop_weak_proxy()

        self.assertTrue(app.weak_proxy.stopped)
        self.assertEqual(app.android.cleared, ["serial-1"])
        self.assertEqual(app.weak_status_var.values[-1], "弱网代理未启动")
        self.assertEqual(app.preview_calls, 1)
        self.assertEqual(app.diagnostic_calls, 1)
        self.assertEqual(app.traffic_calls, 1)
        self.assertIn("停止弱网时已清理 Android 代理：serial-1", app.logs)

    def test_exit_cleanup_keeps_exit_wording(self) -> None:
        class DummyApp:
            def __init__(self) -> None:
                self.weak_registry = WeakProxyDeviceRegistry()
                self.android = FakeAndroid()
                self.logs: list[str] = []

            def append_log(self, text: str) -> None:
                self.logs.append(text)

        from mobileperflab import App

        app = DummyApp()
        device = DeviceInfo("Android", "serial-2", "Phone", "13", "P", "ready")
        app.weak_registry.mark_applied(device, "192.168.1.2:18888")

        App._cleanup_weak_proxy_devices(app)

        self.assertEqual(app.android.cleared, ["serial-2"])
        self.assertIn("退出前已清理 Android 代理：serial-2", app.logs)

    def test_refresh_proxy_traffic_uses_latest_link_diagnostics(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

        class FakeWeakProxy:
            def is_running(self) -> bool:
                return True

            def local_endpoint(self) -> str:
                return "192.168.1.2:18888"

            def traffic_snapshot(self) -> ProxyTrafficSnapshot:
                return ProxyTrafficSnapshot()

            def traffic_history(self) -> list[tuple[float, float, float]]:
                return []

        from mobileperflab import App

        app = object.__new__(App)
        app.weak_proxy = FakeWeakProxy()
        app.weak_readiness_var = FakeVar()
        app.weak_traffic_vars = {"readiness": FakeVar()}
        app.weak_live_summary_var = FakeVar()
        app.last_app_rx_kbps = 0.0
        app.last_app_tx_kbps = 0.0
        app.last_weak_diagnostics = build_weak_network_diagnostics(
            proxy_running=True,
            endpoint="192.168.1.2:18888",
            device=DeviceInfo("Android", "serial-1", "Pixel", "14", "Pixel", "ready"),
            current_proxy="192.168.1.2:18888",
            proxy_reachable=False,
        )

        App._refresh_proxy_traffic(app)

        self.assertIn("端口不可达", app.weak_live_summary_var.value)
        self.assertIn("先修弱网链路", app.weak_readiness_var.value)
        self.assertIn("防火墙", app.weak_readiness_var.value)
        self.assertEqual(app.weak_traffic_vars["readiness"].value, app.weak_readiness_var.value)

    def test_refresh_proxy_traffic_shows_readiness_action_for_proxy_bypass(self) -> None:
        class FakeVar:
            def __init__(self) -> None:
                self.value = ""

            def set(self, value: str) -> None:
                self.value = value

        class FakeWeakProxy:
            def is_running(self) -> bool:
                return True

            def local_endpoint(self) -> str:
                return "192.168.1.2:18888"

            def traffic_snapshot(self) -> ProxyTrafficSnapshot:
                return ProxyTrafficSnapshot()

            def traffic_history(self) -> list[tuple[float, float, float]]:
                return []

        from mobileperflab import App

        app = object.__new__(App)
        app.weak_proxy = FakeWeakProxy()
        app.weak_readiness_var = FakeVar()
        app.weak_traffic_vars = {"readiness": FakeVar(), "hit_status": FakeVar()}
        app.weak_live_summary_var = FakeVar()
        app.last_app_rx_kbps = 120.0
        app.last_app_tx_kbps = 8.0
        app.last_weak_diagnostics = build_weak_network_diagnostics(
            proxy_running=True,
            endpoint="192.168.1.2:18888",
            device=DeviceInfo("Android", "serial-1", "Pixel", "14", "Pixel", "ready"),
            current_proxy="192.168.1.2:18888",
            proxy_reachable=True,
        )

        App._refresh_proxy_traffic(app)

        self.assertIn("先修弱网链路", app.weak_readiness_var.value)
        self.assertIn("QUIC/UDP", app.weak_readiness_var.value)
        self.assertEqual(app.weak_traffic_vars["readiness"].value, app.weak_readiness_var.value)
        self.assertEqual(app.weak_traffic_vars["hit_status"].value, "疑似绕过代理 · App 有流量但代理未捕获")


class AndroidProxyVerificationTest(unittest.TestCase):
    def test_confirms_proxy_when_device_state_matches_expected_endpoint(self) -> None:
        result = verify_android_proxy_state("192.168.1.2:18888", "192.168.1.2:18888")

        self.assertTrue(result.confirmed)
        self.assertEqual(result.status_text, "Android 代理已确认生效：192.168.1.2:18888")
        self.assertEqual(result.log_text, "Android 代理读回确认：192.168.1.2:18888")

    def test_reports_mismatch_when_device_keeps_different_proxy(self) -> None:
        result = verify_android_proxy_state("192.168.1.2:18888", "null")

        self.assertFalse(result.confirmed)
        self.assertEqual(
            result.status_text,
            "Android 代理写入后未确认：期望 192.168.1.2:18888，当前未设置",
        )
        self.assertEqual(
            result.log_text,
            "Android 代理写入后读回不一致：期望 192.168.1.2:18888，实际 未设置",
        )


class WeakNetworkDiagnosticsTest(unittest.TestCase):
    def test_reports_confirmed_proxy_when_running_device_and_readback_match(self) -> None:
        device = DeviceInfo("Android", "serial-1", "Pixel", "14", "Pixel", "ready")

        diagnostics = build_weak_network_diagnostics(
            proxy_running=True,
            endpoint="192.168.1.2:18888",
            device=device,
            current_proxy="192.168.1.2:18888",
            proxy_reachable=True,
        )

        self.assertEqual(diagnostics.overall_state, "ok")
        self.assertEqual(diagnostics.summary, "弱网代理已确认生效，端口可达")
        self.assertEqual(
            diagnostics.rows,
            [
                ("本机代理", "运行中", "192.168.1.2:18888"),
                ("Android 设备", "已选择", "Pixel"),
                ("设备代理", "已确认", "192.168.1.2:18888"),
                ("端口连通", "可达", "Android 可连接本机代理端口"),
            ],
        )

    def test_reports_actionable_states_when_proxy_is_not_ready(self) -> None:
        diagnostics = build_weak_network_diagnostics(
            proxy_running=False,
            endpoint="192.168.1.2:18888",
            device=None,
            current_proxy="",
            proxy_reachable=None,
        )

        self.assertEqual(diagnostics.overall_state, "warning")
        self.assertEqual(diagnostics.summary, "弱网代理未就绪")
        self.assertEqual(
            diagnostics.rows,
            [
                ("本机代理", "未启动", "先点击启动代理"),
                ("Android 设备", "未选择", "请选择 Android 设备"),
                ("设备代理", "未检查", "选择设备后刷新状态"),
                ("端口连通", "未检查", "启动代理并选择设备后检测"),
            ],
        )

    def test_blocks_weak_network_when_running_without_android_device(self) -> None:
        diagnostics = build_weak_network_diagnostics(
            proxy_running=True,
            endpoint="192.168.1.2:18888",
            device=None,
            current_proxy="",
            proxy_reachable=None,
        )

        result = build_weak_network_effectiveness(
            running=True,
            traffic_state="waiting",
            diagnostics=diagnostics,
        )

        self.assertEqual(result["state"], "no_android_device")
        self.assertEqual(result["label"], "未选择 Android 设备")
        self.assertEqual(result["test_readiness"]["state"], "blocked")
        self.assertIn("选择 Android 设备", result["action"])

    def test_blocks_weak_network_for_ios_device_in_android_proxy_mode(self) -> None:
        diagnostics = build_weak_network_diagnostics(
            proxy_running=True,
            endpoint="192.168.1.2:18888",
            device=DeviceInfo("iOS", "ios-1", "iPhone", "17", "iPhone", "ready"),
            current_proxy="",
            proxy_reachable=None,
        )

        result = build_weak_network_effectiveness(
            running=True,
            traffic_state="waiting",
            diagnostics=diagnostics,
        )

        self.assertEqual(result["state"], "unsupported_device")
        self.assertEqual(result["label"], "当前弱网模式不支持该设备")
        self.assertEqual(result["test_readiness"]["state"], "blocked")
        self.assertIn("Android", result["action"])

    def test_warns_when_proxy_is_confirmed_but_port_is_unreachable(self) -> None:
        device = DeviceInfo("Android", "serial-1", "Pixel", "14", "Pixel", "ready")

        diagnostics = build_weak_network_diagnostics(
            proxy_running=True,
            endpoint="192.168.1.2:18888",
            device=device,
            current_proxy="192.168.1.2:18888",
            proxy_reachable=False,
        )

        self.assertEqual(diagnostics.overall_state, "warning")
        self.assertEqual(diagnostics.summary, "Android 已写入代理，但端口不可达")

    def test_scores_weak_network_effectiveness_when_proxy_hits_real_traffic(self) -> None:
        diagnostics = build_weak_network_diagnostics(
            proxy_running=True,
            endpoint="192.168.1.2:18888",
            device=DeviceInfo("Android", "serial-1", "Pixel", "14", "Pixel", "ready"),
            current_proxy="192.168.1.2:18888",
            proxy_reachable=True,
        )

        result = build_weak_network_effectiveness(
            running=True,
            traffic_state="hit",
            diagnostics=diagnostics,
            app_rx_kbps=12.0,
            app_tx_kbps=3.0,
        )

        self.assertEqual(result["state"], "effective")
        self.assertEqual(result["label"], "弱网已生效")
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["test_readiness"]["state"], "ready")
        self.assertEqual(result["test_readiness"]["label"], "可以开始测试")
        self.assertIn("代理已捕获真实流量", result["detail"])

    def test_scores_possible_proxy_bypass_when_app_has_traffic_but_proxy_waits(self) -> None:
        diagnostics = build_weak_network_diagnostics(
            proxy_running=True,
            endpoint="192.168.1.2:18888",
            device=DeviceInfo("Android", "serial-1", "Pixel", "14", "Pixel", "ready"),
            current_proxy="192.168.1.2:18888",
            proxy_reachable=True,
        )

        result = build_weak_network_effectiveness(
            running=True,
            traffic_state="waiting",
            diagnostics=diagnostics,
            app_rx_kbps=20.0,
            app_tx_kbps=0.0,
        )

        self.assertEqual(result["state"], "bypass")
        self.assertEqual(result["label"], "疑似绕过代理")
        self.assertEqual(result["test_readiness"]["state"], "blocked")
        self.assertEqual(result["test_readiness"]["label"], "先修弱网链路")
        self.assertIn("QUIC/UDP", result["test_readiness"]["action"])
        self.assertLess(result["score"], 60)
        self.assertIn("App 有流量但代理未捕获", result["detail"])
        self.assertIn("QUIC/UDP", result["action"])

    def test_formats_weak_readiness_display_text_with_action(self) -> None:
        self.assertEqual(
            weak_readiness_display_text({"label": "先修弱网链路", "action": "检查 QUIC/UDP、自建网络栈。"}),
            "先修弱网链路 · 检查 QUIC/UDP、自建网络栈。",
        )

    def test_formats_weak_readiness_display_text_without_empty_separator(self) -> None:
        self.assertEqual(weak_readiness_display_text({"label": "可以开始测试"}), "可以开始测试")

    def test_scores_unreachable_proxy_before_traffic_checks(self) -> None:
        diagnostics = build_weak_network_diagnostics(
            proxy_running=True,
            endpoint="192.168.1.2:18888",
            device=DeviceInfo("Android", "serial-1", "Pixel", "14", "Pixel", "ready"),
            current_proxy="192.168.1.2:18888",
            proxy_reachable=False,
        )

        result = build_weak_network_effectiveness(
            running=True,
            traffic_state="waiting",
            diagnostics=diagnostics,
            app_rx_kbps=0.0,
            app_tx_kbps=0.0,
        )

        self.assertEqual(result["state"], "unreachable")
        self.assertEqual(result["label"], "端口不可达")
        self.assertEqual(result["test_readiness"]["state"], "blocked")
        self.assertIn("手机无法连接本机代理端口", result["detail"])
        self.assertEqual(diagnostics.rows[-1], ("端口连通", "不可达", "检查手机和电脑是否同一网络/防火墙"))

    def test_marks_waiting_proxy_as_attention_before_business_traffic(self) -> None:
        diagnostics = build_weak_network_diagnostics(
            proxy_running=True,
            endpoint="192.168.1.2:18888",
            device=DeviceInfo("Android", "serial-1", "Pixel", "14", "Pixel", "ready"),
            current_proxy="192.168.1.2:18888",
            proxy_reachable=True,
        )

        result = build_weak_network_effectiveness(
            running=True,
            traffic_state="waiting",
            diagnostics=diagnostics,
            app_rx_kbps=0.0,
            app_tx_kbps=0.0,
        )

        self.assertEqual(result["state"], "waiting")
        self.assertEqual(result["test_readiness"]["state"], "attention")
        self.assertEqual(result["test_readiness"]["label"], "先触发业务请求")
        self.assertIn("HTTP/HTTPS", result["test_readiness"]["action"])


class WeakNetworkProxyHealthTest(unittest.TestCase):
    def test_recognizes_internal_health_check_request(self) -> None:
        request = b"GET /__mobileperflab_health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"

        self.assertTrue(WeakNetworkProxy._is_health_check_request(request))

    def test_internal_health_check_does_not_count_as_real_proxy_traffic(self) -> None:
        proxy = WeakNetworkProxy(lambda _text: None)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        proxy.configure(port, 0, 0, 0.0, 0.0, 0.0)
        proxy.start()
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=2.0) as client:
                client.sendall(b"GET /__mobileperflab_health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
                response = client.recv(4096)

            self.assertIn(b"mobileperflab-ok", response)
            self.assertEqual(proxy.traffic_snapshot().total_connections, 0)
        finally:
            proxy.stop()


class WeakNetworkProxyTrafficTest(unittest.TestCase):
    def test_runtime_config_reflects_last_configured_proxy_values(self) -> None:
        proxy = WeakNetworkProxy(lambda _text: None)

        proxy.configure(18888, 300, 120, 2.0, 512.0, 256.0)
        proxy.configure(19999, 500, 250, 4.0, 384.0, 128.0)

        self.assertEqual(
            proxy.runtime_config("地铁"),
            {
                "profile": "地铁",
                "port": 19999,
                "latency_ms": 500,
                "jitter_ms": 250,
                "loss_percent": 4.0,
                "down_kbps": 384.0,
                "up_kbps": 128.0,
            },
        )

    def test_tracks_real_proxy_bytes_and_recent_rates(self) -> None:
        proxy = WeakNetworkProxy(lambda _text: None)
        proxy.reset_traffic(now=10.0)

        proxy._record_connection_open(now=10.0)
        proxy._record_transfer("up", 2048, now=10.5)
        proxy._record_transfer("down", 4096, now=11.0)

        snapshot = proxy.traffic_snapshot(now=11.0)

        self.assertEqual(snapshot.up_bytes, 2048)
        self.assertEqual(snapshot.down_bytes, 4096)
        self.assertAlmostEqual(snapshot.up_kbps, 2.0)
        self.assertAlmostEqual(snapshot.down_kbps, 4.0)
        self.assertEqual(snapshot.active_connections, 1)
        self.assertEqual(snapshot.total_connections, 1)

        proxy._record_connection_close()
        self.assertEqual(proxy.traffic_snapshot(now=12.0).active_connections, 0)

    def test_proxy_rates_decay_to_zero_when_traffic_stops(self) -> None:
        proxy = WeakNetworkProxy(lambda _text: None)
        proxy.reset_traffic(now=20.0)

        proxy._record_transfer("down", 1024, now=21.0)

        self.assertAlmostEqual(proxy.traffic_snapshot(now=21.0).down_kbps, 1.0)
        self.assertEqual(proxy.traffic_snapshot(now=30.0).down_kbps, 0.0)

    def test_tracks_dropped_proxy_connections(self) -> None:
        proxy = WeakNetworkProxy(lambda _text: None)
        proxy.reset_traffic(now=1.0)

        proxy._record_connection_open(now=1.0)
        proxy._record_dropped_connection(now=1.2)
        proxy._record_connection_close()

        snapshot = proxy.traffic_snapshot(now=2.0)

        self.assertEqual(snapshot.total_connections, 1)
        self.assertEqual(snapshot.dropped_connections, 1)
        self.assertEqual(snapshot.active_connections, 0)


class ProxyTrafficFormattingTest(unittest.TestCase):
    def test_formats_proxy_traffic_for_live_panel(self) -> None:
        values = format_proxy_traffic_snapshot(
            ProxyTrafficSnapshot(
                up_bytes=1536,
                down_bytes=2 * 1024 * 1024,
                up_kbps=12.345,
                down_kbps=456.789,
                active_connections=2,
                total_connections=9,
                dropped_connections=1,
                last_activity_age=1.4,
            )
        )

        self.assertEqual(values["down_rate"], "456.8 KB/s")
        self.assertEqual(values["up_rate"], "12.3 KB/s")
        self.assertEqual(values["down_total"], "2.0 MB")
        self.assertEqual(values["up_total"], "1.5 KB")
        self.assertEqual(values["connections"], "2 活跃 / 9 总计")
        self.assertEqual(values["drops"], "1")
        self.assertEqual(values["activity"], "1.4s 前")

    def test_formats_proxy_traffic_without_activity(self) -> None:
        values = format_proxy_traffic_snapshot(ProxyTrafficSnapshot())

        self.assertEqual(values["down_total"], "0 B")
        self.assertEqual(values["up_total"], "0 B")
        self.assertEqual(values["activity"], "无")

    def test_formats_weak_network_config_for_reports(self) -> None:
        text = format_weak_network_config(
            {
                "profile": "地铁",
                "port": 18888,
                "latency_ms": 500,
                "jitter_ms": 250,
                "loss_percent": 4,
                "down_kbps": 384,
                "up_kbps": 128,
            }
        )

        self.assertEqual(text, "地铁 · 端口 18888 · 延迟 500ms · 抖动 250ms · 丢包 4.0% · ↓384 KB/s · ↑128 KB/s")

    def test_formats_live_proxy_summary_for_performance_dashboard(self) -> None:
        text = format_live_proxy_summary(
            running=True,
            endpoint="192.168.1.2:18888",
            snapshot=ProxyTrafficSnapshot(
                up_kbps=12.3,
                down_kbps=45.6,
                active_connections=2,
                total_connections=8,
                dropped_connections=1,
                last_activity_age=0.8,
            ),
        )

        self.assertIn("弱网 ON", text)
        self.assertIn("192.168.1.2:18888", text)
        self.assertIn("↓45.6 KB/s", text)
        self.assertIn("↑12.3 KB/s", text)
        self.assertIn("2/8 连接", text)
        self.assertIn("丢弃 1", text)

    def test_formats_running_proxy_without_real_traffic_as_waiting(self) -> None:
        text = format_live_proxy_summary(True, "192.168.1.2:18888", ProxyTrafficSnapshot())

        self.assertIn("弱网 ON", text)
        self.assertIn("等待目标流量", text)
        self.assertIn("先触发业务请求", text)
        self.assertIn("未捕获请求", text)

    def test_formats_live_proxy_summary_with_ready_action_after_real_traffic(self) -> None:
        text = format_live_proxy_summary(
            True,
            "192.168.1.2:18888",
            ProxyTrafficSnapshot(total_connections=1, down_bytes=2048, up_bytes=1024),
        )

        self.assertIn("弱网已生效", text)
        self.assertIn("可以开始测试", text)

    def test_formats_live_proxy_bypass_warning_when_app_has_network_but_proxy_waits(self) -> None:
        text = format_live_proxy_summary(
            True,
            "192.168.1.2:18888",
            ProxyTrafficSnapshot(),
            app_rx_kbps=120.0,
            app_tx_kbps=8.0,
        )

        self.assertIn("疑似绕过系统代理", text)
        self.assertIn("疑似绕过代理", text)
        self.assertIn("先修弱网链路", text)
        self.assertIn("App ↑↓有流量", text)

    def test_formats_weak_hit_status_for_proxy_bypass_and_real_hits(self) -> None:
        self.assertEqual(
            weak_hit_status_text(
                running=True,
                traffic_state="waiting",
                app_rx_kbps=120.0,
                app_tx_kbps=8.0,
            ),
            "疑似绕过代理 · App 有流量但代理未捕获",
        )
        self.assertEqual(
            weak_hit_status_text(
                running=True,
                traffic_state="hit",
                app_rx_kbps=0.0,
                app_tx_kbps=0.0,
            ),
            "已命中目标流量 · 弱网规则有生效证据",
        )

    def test_formats_live_proxy_summary_with_link_diagnostics(self) -> None:
        diagnostics = build_weak_network_diagnostics(
            proxy_running=True,
            endpoint="192.168.1.2:18888",
            device=DeviceInfo("Android", "serial-1", "Pixel", "14", "Pixel", "ready"),
            current_proxy="192.168.1.2:18888",
            proxy_reachable=False,
        )

        text = format_live_proxy_summary(
            True,
            "192.168.1.2:18888",
            ProxyTrafficSnapshot(),
            diagnostics=diagnostics,
        )

        self.assertIn("端口不可达", text)
        self.assertIn("等待目标流量", text)

    def test_formats_running_proxy_with_connections_as_traffic_hit(self) -> None:
        text = format_live_proxy_summary(
            True,
            "192.168.1.2:18888",
            ProxyTrafficSnapshot(total_connections=1, down_bytes=2048, up_bytes=1024),
        )

        self.assertIn("已命中目标流量", text)

    def test_formats_dropped_proxy_connection_as_hit_and_dropped(self) -> None:
        text = format_live_proxy_summary(
            True,
            "192.168.1.2:18888",
            ProxyTrafficSnapshot(dropped_connections=2),
        )

        self.assertIn("已命中并丢弃", text)
        self.assertIn("只见丢弃", text)

    def test_formats_disabled_live_proxy_summary(self) -> None:
        text = format_live_proxy_summary(False, "<host>:<port>", ProxyTrafficSnapshot())

        self.assertEqual(text, "弱网 OFF · 未启动")

    def test_builds_report_payload_with_relative_proxy_history(self) -> None:
        snapshot = ProxyTrafficSnapshot(
            down_bytes=2048,
            up_bytes=1024,
            down_kbps=8.0,
            up_kbps=2.0,
            active_connections=1,
            total_connections=3,
            dropped_connections=0,
            last_activity_age=0.2,
        )

        payload = build_weak_network_report_payload(
            True,
            "127.0.0.1:18888",
            snapshot,
            [(100.0, 0.0, 0.0), (101.5, 8.0, 2.0)],
            {
                "profile": "地铁",
                "latency_ms": 500,
                "jitter_ms": 250,
                "loss_percent": 4.0,
                "down_kbps": 384.0,
                "up_kbps": 128.0,
            },
        )

        self.assertEqual(payload["endpoint"], "127.0.0.1:18888")
        self.assertEqual(payload["config"]["profile"], "地铁")
        self.assertEqual(payload["config"]["latency_ms"], 500)
        self.assertEqual(payload["snapshot"]["down_kbps"], 8.0)
        self.assertEqual(payload["history"][0]["elapsed"], 0.0)
        self.assertEqual(payload["history"][1]["elapsed"], 1.5)
        self.assertIn("↓8.0 KB/s", payload["summary"])
        self.assertEqual(payload["traffic_state"], "hit")
        self.assertEqual(payload["hit_status"], "已命中目标流量 · 弱网规则有生效证据")
        self.assertEqual(payload["effectiveness"]["state"], "effective")
        self.assertEqual(payload["effectiveness"]["label"], "弱网已生效")
        self.assertEqual(payload["readiness_display"], "可以开始测试 · 继续执行业务场景并观察代理真实流量曲线。")

    def test_builds_report_payload_with_waiting_proxy_traffic_state(self) -> None:
        payload = build_weak_network_report_payload(True, "127.0.0.1:18888", ProxyTrafficSnapshot(), [])

        self.assertEqual(payload["traffic_state"], "waiting")
        self.assertEqual(payload["hit_status"], "等待目标流量 · 在 App 内触发 HTTP/HTTPS 请求")
        self.assertIn("等待目标流量", payload["summary"])
        self.assertEqual(payload["effectiveness"]["state"], "waiting")
        self.assertIn("先触发业务请求", payload["readiness_display"])

    def test_builds_report_payload_with_dropped_proxy_traffic_state(self) -> None:
        payload = build_weak_network_report_payload(
            True,
            "127.0.0.1:18888",
            ProxyTrafficSnapshot(dropped_connections=1),
            [],
        )

        self.assertEqual(payload["traffic_state"], "dropped")
        self.assertEqual(payload["traffic_state_label"], "已命中并丢弃")

    def test_builds_report_payload_with_export_time_diagnostics(self) -> None:
        device = DeviceInfo("Android", "serial-1", "Pixel", "14", "Pixel", "ready")
        diagnostics = build_weak_network_diagnostics(
            proxy_running=True,
            endpoint="192.168.1.2:18888",
            device=device,
            current_proxy="192.168.1.2:18888",
            proxy_reachable=True,
        )

        payload = build_weak_network_report_payload(
            True,
            "192.168.1.2:18888",
            ProxyTrafficSnapshot(),
            [],
            diagnostics=diagnostics,
        )

        self.assertEqual(payload["diagnostics"]["overall_state"], "ok")
        self.assertEqual(payload["diagnostics"]["summary"], "弱网代理已确认生效，端口可达")
        self.assertEqual(payload["diagnostics"]["rows"][2]["name"], "设备代理")
        self.assertEqual(payload["diagnostics"]["rows"][2]["state"], "已确认")
        self.assertEqual(payload["diagnostics"]["rows"][3]["detail"], "Android 可连接本机代理端口")


class ProxyTrafficHistoryTest(unittest.TestCase):
    def test_keeps_recent_proxy_rate_points_for_live_chart(self) -> None:
        history = ProxyTrafficHistory(limit=3)

        history.append(10.0, ProxyTrafficSnapshot(down_kbps=1.0, up_kbps=0.5))
        history.append(11.0, ProxyTrafficSnapshot(down_kbps=2.0, up_kbps=1.5))
        history.append(12.0, ProxyTrafficSnapshot(down_kbps=3.0, up_kbps=2.5))
        history.append(13.0, ProxyTrafficSnapshot(down_kbps=4.0, up_kbps=3.5))

        self.assertEqual(
            history.points(),
            [(11.0, 2.0, 1.5), (12.0, 3.0, 2.5), (13.0, 4.0, 3.5)],
        )

    def test_reset_clears_proxy_rate_points(self) -> None:
        history = ProxyTrafficHistory(limit=3)
        history.append(10.0, ProxyTrafficSnapshot(down_kbps=1.0, up_kbps=0.5))

        history.reset()

        self.assertEqual(history.points(), [])

    def test_export_snapshot_can_skip_mutating_history(self) -> None:
        proxy = WeakNetworkProxy(lambda _text: None)
        proxy.reset_traffic(now=10.0)
        proxy._record_transfer("down", 1024, now=10.5)

        snapshot = proxy.traffic_snapshot(now=11.0, record_history=False)

        self.assertEqual(snapshot.down_kbps, 1.0)
        self.assertEqual(proxy.traffic_history(), [])


class ProxyTrafficChartCompatibilityTest(unittest.TestCase):
    def test_history_points_can_drive_a_graph_panel_like_curve(self) -> None:
        class DummyPanel:
            def __init__(self) -> None:
                self.points: list[tuple[float, float, str]] = []

            def append(self, elapsed: float, value: float, quality: str = "ok") -> None:
                self.points.append((elapsed, value, quality))

        history = ProxyTrafficHistory(limit=3)
        panel = DummyPanel()

        history.append(10.0, ProxyTrafficSnapshot(down_kbps=1.0, up_kbps=0.5))
        history.append(11.0, ProxyTrafficSnapshot(down_kbps=2.0, up_kbps=1.5))
        history.append(12.0, ProxyTrafficSnapshot(down_kbps=3.0, up_kbps=2.5))

        for elapsed, down, up in history.points():
            panel.append(elapsed, down)
            panel.append(elapsed, up)

        self.assertEqual(
            panel.points,
            [
                (10.0, 1.0, "ok"),
                (10.0, 0.5, "ok"),
                (11.0, 2.0, "ok"),
                (11.0, 1.5, "ok"),
                (12.0, 3.0, "ok"),
                (12.0, 2.5, "ok"),
            ],
        )


class WeakNetworkProxyIntegrationTest(unittest.TestCase):
    @staticmethod
    def _free_port() -> int:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    def test_plain_http_proxy_request_counts_real_up_and_down_traffic(self) -> None:
        target = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target.bind(("127.0.0.1", 0))
        target.listen(1)
        target_port = target.getsockname()[1]
        received: list[bytes] = []
        server_done = threading.Event()

        def serve_once() -> None:
            try:
                conn, _address = target.accept()
                with conn:
                    received.append(conn.recv(4096))
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello")
            finally:
                target.close()
                server_done.set()

        thread = threading.Thread(target=serve_once, daemon=True)
        thread.start()

        proxy = WeakNetworkProxy(lambda _text: None)
        proxy_port = self._free_port()
        proxy.configure(proxy_port, 0, 0, 0.0, 0.0, 0.0)
        proxy.start()
        try:
            request = (
                f"GET http://127.0.0.1:{target_port}/ping HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{target_port}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
            response = b""
            with socket.create_connection(("127.0.0.1", proxy_port), timeout=2.0) as client:
                client.sendall(request)
                while True:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    response += chunk

            self.assertTrue(server_done.wait(1.0))
            self.assertIn(b"hello", response)
            self.assertTrue(received)
            snapshot = proxy.traffic_snapshot()
            self.assertEqual(snapshot.total_connections, 1)
            self.assertGreaterEqual(snapshot.up_bytes, len(received[0]))
            self.assertGreaterEqual(snapshot.down_bytes, len(response))
        finally:
            proxy.stop()


class AndroidConnectivityProbeTest(unittest.TestCase):
    def test_falls_back_to_http_health_check_when_nc_is_unavailable(self) -> None:
        device = DeviceInfo("Android", "serial-1", "Pixel", "14", "Pixel", "ready")
        android = FakeProbeAndroid(
            [
                (127, "toybox: nc: not found"),
                (127, "nc: not found"),
                (0, "mobileperflab-ok"),
            ]
        )

        ok, detail = android.probe_tcp_connectivity(device, "192.168.1.2", 18888)

        self.assertTrue(ok)
        self.assertEqual(detail, "192.168.1.2:18888")
        self.assertIn("/__mobileperflab_health", android.commands[-1])


if __name__ == "__main__":
    unittest.main()
