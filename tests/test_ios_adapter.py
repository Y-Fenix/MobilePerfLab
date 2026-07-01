import unittest

from mobileperflab import DeviceInfo, IOSAdapter


class FakeIOSAdapter(IOSAdapter):
    def __init__(self, app_records: list[dict[str, object]], process_records: list[dict[str, object]]) -> None:
        super().__init__()
        self._app_records = app_records
        self._process_records = process_records

    def _ios_app_records(self, device: DeviceInfo) -> list[dict[str, object]]:
        return self._app_records

    def _dvt_process_records(self, device: DeviceInfo, max_age: float = 2.0) -> list[dict[str, object]]:
        return self._process_records


class IOSAdapterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.device = DeviceInfo("iOS", "ios-1", "iPhone", "18.0", "iPhone", "ready")

    def test_process_record_matches_target_app_by_executable_app_directory_when_bundle_id_is_missing(self) -> None:
        adapter = FakeIOSAdapter(
            app_records=[
                {
                    "bundleIdentifier": "com.example.game",
                    "name": "Example Game",
                    "url": "file:///private/var/containers/Bundle/Application/ABC/ExampleGame.app/",
                }
            ],
            process_records=[
                {
                    "pid": 4321,
                    "name": "Runner",
                    "executable": "file:///private/var/containers/Bundle/Application/ABC/ExampleGame.app/ExampleGame",
                }
            ],
        )

        record = adapter._target_process_record(self.device, "com.example.game", max_age=0.0)

        self.assertIsNotNone(record)
        self.assertEqual(record["pid"], 4321)


if __name__ == "__main__":
    unittest.main()
