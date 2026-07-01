import queue
import unittest
from unittest.mock import patch

from mobileperflab import BaseAdapter, DeviceInfo, PerfSample, SamplerThread


class FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.current = start
        self.waits: list[float] = []

    def time(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.advance(seconds)


class ScriptedAdapter(BaseAdapter):
    def __init__(self, clock: FakeClock, sample_durations: list[float]) -> None:
        self.clock = clock
        self.sample_durations = sample_durations
        self.samples = 0

    def collect_sample(self, device: DeviceInfo, app_id: str, start_time: float) -> PerfSample:
        duration = self.sample_durations.pop(0)
        self.clock.advance(duration)
        self.samples += 1
        return PerfSample(timestamp=self.clock.time(), elapsed=self.clock.time() - start_time, fps=60.0)


class StopAfterSamples:
    def __init__(self, clock: FakeClock, adapter: ScriptedAdapter, limit: int) -> None:
        self.clock = clock
        self.adapter = adapter
        self.limit = limit

    def is_set(self) -> bool:
        return self.adapter.samples >= self.limit

    def set(self) -> None:
        self.limit = self.adapter.samples

    def wait(self, seconds: float) -> bool:
        self.clock.wait(seconds)
        return self.is_set()


class SamplerThreadTest(unittest.TestCase):
    def test_sampler_interval_can_be_updated_while_running(self) -> None:
        clock = FakeClock()
        adapter = ScriptedAdapter(clock, [0.2])
        sampler = SamplerThread(adapter, DeviceInfo("Android", "serial", "LowEnd", "", "", "ready"), "com.example", 1.0, queue.Queue())

        sampler.set_interval(1.5)

        self.assertEqual(sampler.interval, 1.5)

    def test_sampler_resets_cadence_after_slow_low_end_sample(self) -> None:
        clock = FakeClock()
        adapter = ScriptedAdapter(clock, [0.2, 1.6, 0.2])
        sampler = SamplerThread(adapter, DeviceInfo("Android", "serial", "LowEnd", "", "", "ready"), "com.example", 1.0, queue.Queue())
        sampler.stop_event = StopAfterSamples(clock, adapter, limit=3)  # type: ignore[assignment]

        with patch("mobileperflab.time.time", clock.time):
            sampler.run()

        self.assertEqual(len(clock.waits), 3)
        for actual, expected in zip(clock.waits, [0.8, 1.0, 0.8]):
            self.assertAlmostEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
