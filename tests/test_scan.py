import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path

from core.scan import Scan
from core.settings import ROOT, Settings
from hardware.controller import Busy, Cancelled
from tests.fakes import BlockedCamera, FakeController, ImmediateCamera


class FastScan(Scan):
    @staticmethod
    def pause(seconds, cancel):
        if cancel.wait(min(seconds, 0.001)):
            raise Cancelled()


class ScanTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".checks").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".checks")
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.settings = replace(Settings(), poll_s=0.001, settle_s=0, move_timeout_s=0.08,
                                home_timeout_s=0.08, capture_timeout_s=0.08)
        self.events = []
        self.controller = FakeController(self.events)
        self.camera = ImmediateCamera(self.events)
        self.cancel = threading.Event()

    def scan(self, **kwargs):
        return FastScan(self.controller, self.camera, self.settings, self.output, **kwargs)

    def manifest(self, result):
        return json.loads((Path(result.session_path) / "session.json").read_text())

    def test_complete_pass_has_five_real_pairs_and_clamped_last_rise(self):
        result = self.scan().run(self.cancel)
        self.assertEqual(result.reason, "completed", result.error)
        self.assertEqual(result.pairs, 5)
        self.assertEqual(result.position.z, 1355)
        relevant = [event for event in self.events if isinstance(event, tuple)
                    or event == "HOME" or event.startswith(("XX_", "Z_"))]
        self.assertEqual(relevant, ["HOME", "XX_+45", ("photo", 45, 0), "Z_300",
                                    "XX_+45", ("photo", 90, 300), "Z_600",
                                    "XX_+45", ("photo", 135, 600), "Z_900",
                                    "XX_+45", ("photo", 180, 900), "Z_1200",
                                    "XX_+45", ("photo", 225, 1200), "Z_1355"])
        self.assertEqual(self.events[-2:], ["STOP", "LED_OFF"])
        self.assertEqual(len(list(Path(result.session_path).rglob("*.png"))), 10)
        self.assertEqual(self.manifest(result)["status"], "completed")

    def test_stop_while_home_rotation_or_lifting_has_no_later_moves(self):
        for phase in ("HOME", "XX_+45", "Z_300"):
            with self.subTest(phase=phase):
                self.events.clear()
                self.cancel = threading.Event()
                self.controller = FakeController(self.events, z=600)
                self.controller.on_command = lambda text: self.cancel.set() if text == phase else None
                result = self.scan().run(self.cancel)
                self.assertEqual(result.reason, "stopped", result.error)
                index = self.events.index(phase)
                self.assertEqual(self.events[index + 1:], ["STOP", "LED_OFF"])
                self.assertEqual(self.manifest(result)["status"], "stopped")

    def test_stop_in_settle_delay_does_not_capture(self):
        def status(text):
            if text == "Стабилизация перед снимком":
                self.cancel.set()
        result = self.scan(status=status).run(self.cancel)
        self.assertEqual(result.reason, "stopped")
        self.assertFalse(any(isinstance(event, tuple) for event in self.events))

    def test_stop_during_capture_does_not_wait_for_camera_or_resume_on_late_result(self):
        self.camera = BlockedCamera()
        self.settings = replace(self.settings, capture_timeout_s=10)
        results = []
        thread = threading.Thread(target=lambda: results.append(self.scan().run(self.cancel)))
        thread.start()
        self.assertTrue(self.camera.called.wait(2))
        self.cancel.set()
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results[0].reason, "stopped")
        self.camera.request.done.set()
        self.assertEqual(self.events[-2:], ["STOP", "LED_OFF"])
        self.assertFalse(any(isinstance(event, str) and event.startswith("Z_") for event in self.events))

    def test_timeout_of_stationary_axis_stops_without_taking_photo(self):
        self.controller.frozen = "x"
        result = self.scan().run(self.cancel)
        self.assertEqual(result.reason, "error")
        self.assertIn("Не достигнута позиция X", result.error)
        self.assertEqual(result.pairs, 0)
        self.assertEqual(self.events[-2:], ["STOP", "LED_OFF"])

    def test_capture_timeout_stops_without_raising_cameras(self):
        self.camera = BlockedCamera()
        result = self.scan().run(self.cancel)
        self.assertEqual(result.reason, "error")
        self.assertIn("стереопары", result.error)
        self.assertEqual(self.events[-2:], ["STOP", "LED_OFF"])
        self.assertNotIn("Z_300", self.events)

    def test_cancelled_queued_start_never_sends_home(self):
        self.cancel.set()
        result = self.scan().run(self.cancel)
        self.assertEqual(result.reason, "stopped")
        self.assertEqual(self.events, ["STOP", "LED_OFF"])
        self.assertFalse(list(self.output.iterdir()))

    def test_wrap_at_360_and_repeat_start_from_bottom(self):
        self.controller.x = self.controller.target_x = 330
        self.controller.z = self.controller.target_z = 1355
        first = self.scan().run(self.cancel)
        self.assertEqual(first.reason, "completed", first.error)
        self.assertIn(("photo", 15, 0), self.events)
        second = self.scan().run(threading.Event())
        self.assertEqual(second.reason, "completed", second.error)
        self.assertEqual(self.events.count("HOME"), 2)
        self.assertNotEqual(first.session_path, second.session_path)
        self.assertEqual([p["z_mm"] for p in self.manifest(second)["pairs"]], [0, 300, 600, 900, 1200])

    def test_busy_response_is_retried_before_capture(self):
        first = True
        def reject_once(text):
            nonlocal first
            if first and text == "XX_+45":
                first = False
                self.controller.target_x = self.controller.x
                raise Busy("ERR:BUSY")
        self.controller.on_command = reject_once
        result = self.scan().run(self.cancel)
        self.assertEqual(result.reason, "completed", result.error)
        self.assertEqual(result.pairs, 5)
        self.assertEqual(self.events.count("XX_+45"), 6)

    def test_stop_failure_is_visible_and_persisted(self):
        def fail_stop(text):
            if text == "HOME":
                self.cancel.set()
            if text == "STOP":
                raise OSError("USB disconnected")
        self.controller.on_command = fail_stop
        result = self.scan().run(self.cancel)
        self.assertEqual(result.reason, "error")
        self.assertIn("STOP: USB disconnected", result.error)
        self.assertIn("USB disconnected", self.manifest(result)["error"])

    def test_camera_loss_during_motion_is_recorded_as_error(self):
        self.controller.on_command = lambda text: self.cancel.set() if text == "HOME" else None
        result = self.scan(abort_error=lambda: "Камеры отключены").run(self.cancel)
        self.assertEqual(result.reason, "error")
        self.assertEqual(self.manifest(result)["error"], "Камеры отключены")


if __name__ == "__main__":
    unittest.main()
