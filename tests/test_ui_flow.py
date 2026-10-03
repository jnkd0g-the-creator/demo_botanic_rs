import json
import os
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from core.settings import ROOT, Settings

runtime = ROOT / ".checks" / "runtime"
runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["XDG_RUNTIME_DIR"] = str(runtime)

from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPushButton

from hardware.workers import CameraWorker, RigWorker
from tests.fakes import FakeCameras, FakeController, FastController, PositionOnlySerial
from ui.main_window import MainWindow
from ui.theme import apply_theme


class UiFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        apply_theme(cls.app)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".checks")
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.controller = FakeController()
        self.serial = PositionOnlySerial(self.controller)
        self.client = FastController("fake", 115200, serial_factory=lambda **_: self.serial)
        self.connect_count = 0
        self.devices = []
        self.block_capture = False
        self.fail_camera = False
        self.capture_entered = threading.Event()
        self.capture_release = threading.Event()
        owner = self

        class Cameras(FakeCameras):
            def read(self, fresh=False):
                if owner.fail_camera:
                    raise RuntimeError("USB камеры отключён")
                if fresh and owner.block_capture:
                    owner.capture_entered.set()
                    owner.capture_release.wait(3)
                return super().read(fresh)

        def factory(settings):
            backend = Cameras(settings)
            self.devices.append(backend)
            return backend

        def camera_worker(settings, parent):
            return CameraWorker(settings, parent, factory=factory)

        def connect(settings, shutdown):
            self.connect_count += 1
            self.client.position(shutdown)
            return self.client

        def rig_worker(settings, camera, parent):
            return RigWorker(settings, camera, parent, output=self.output,
                             connector=connect)

        settings = replace(Settings(), poll_s=0.003, settle_s=0, move_timeout_s=2,
                           home_timeout_s=2, capture_timeout_s=5)
        with patch("ui.main_window.CameraWorker", side_effect=camera_worker), \
                patch("ui.main_window.RigWorker", side_effect=rig_worker):
            self.window = MainWindow(settings)
        self.window.resize(1280, 800)
        self.window.show()
        self.wait(lambda: self.window.start_button.isEnabled())

    def tearDown(self):
        self.capture_release.set()
        self.window.close()
        self.wait(lambda: not self.window.rig.isRunning() and not self.window.camera.isRunning())
        self.app.processEvents()
        self.assertTrue(self.controller.closed)
        self.assertTrue(all(device.closed for device in self.devices))
        self.assertNotIn("LED_OFF", self.controller.events)

    def wait(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate(), "Истекло ожидание события Qt")

    def report_visible(self):
        return self.window.stack.currentWidget() == self.window.report

    def test_complete_then_close_restart_and_stop(self):
        self.assertNotIn("HOME", self.controller.events)
        buttons = self.window.main_page.findChildren(QPushButton)
        self.assertEqual([b.text() for b in buttons], ["Старт", "Стоп"])
        self.window.start_button.click()
        self.window.start_button.click()  # Заблокированный повторный старт не ставит второй цикл.
        self.wait(self.report_visible)
        self.assertEqual(self.controller.events.count("HOME"), 1)
        self.assertFalse(self.window.report.error_label.text())
        self.assertEqual(self.controller.white_light, 100)
        self.assertEqual(len(list(self.output.rglob("*.png"))), 10)
        self.assertTrue(any(device.fresh_calls.count(True) == 5 for device in self.devices))
        self.assertEqual(self.window.report.geometry(), self.window.stack.contentsRect())
        self.assertEqual([b.text() for b in self.window.report.findChildren(QPushButton)], ["Закрыть"])
        self.window.report.viewer.timer.stop()
        self.window.grab().save(str(ROOT / ".checks" / "report.png"))
        self.window.resize(1024, 768)
        self.app.processEvents()
        self.assertTrue(self.window.report.close_button.isVisible())
        self.window.grab().save(str(ROOT / ".checks" / "report_1024.png"))
        self.window.report.close_button.click()
        self.wait(lambda: self.window.start_button.isEnabled())
        self.window.start_button.click()
        self.wait(lambda: self.controller.events.count("HOME") == 2)
        self.window.stop_button.click()
        self.wait(self.report_visible)
        self.assertIn("Остановлено пользователем", self.window.report.session_summary.text())
        manifests = [json.loads(p.read_text()) for p in self.output.rglob("session.json")]
        self.assertEqual(sorted(m["status"] for m in manifests), ["completed", "stopped"])

    def test_stop_report_opens_while_camera_is_still_returning_frame(self):
        self.block_capture = True
        self.window.start_button.click()
        self.wait(self.capture_entered.is_set)
        self.window.stop_button.click()
        self.wait(self.report_visible, timeout=1)
        self.assertFalse(self.capture_release.is_set())
        self.assertIn("0 стереопар", self.window.report.session_summary.text())
        self.assertNotIn("Z_300", self.controller.events)
        self.capture_release.set()
        QTest.qWait(150)
        self.assertFalse(list(self.output.rglob("*.png")))

    def test_camera_disconnect_interrupts_lift_and_reports_error(self):
        def disconnect(text):
            if text == "Z_300":
                self.controller.frozen = "z"
                self.fail_camera = True
        self.controller.on_command = disconnect
        self.window.start_button.click()
        self.wait(self.report_visible)
        self.assertIn("USB камеры отключён", self.window.report.error_label.text())
        self.assertEqual(self.controller.events.count("XX_+45"), 1)
        manifest = json.loads(next(self.output.rglob("session.json")).read_text())
        self.assertEqual(manifest["status"], "error")
        polls = self.serial.writes.count(b"POS\n")
        self.wait(lambda: self.serial.writes.count(b"POS\n") > polls)
        self.assertEqual(self.connect_count, 1)
        self.assertFalse(self.serial.closed)
        self.assertTrue(self.window.controller_ready)

    def test_one_missed_position_poll_recovers_without_reopening_port(self):
        self.serial.drop_positions = 1
        self.wait(lambda: not self.window.controller_ready)
        self.wait(lambda: self.window.controller_ready)
        self.assertEqual(self.connect_count, 1)
        self.assertFalse(self.serial.closed)

    def test_window_close_during_home_stops_workers(self):
        self.controller.z = self.controller.target_z = 1355
        self.window.start_button.click()
        self.wait(lambda: "HOME" in self.controller.events)
        self.window.close()
        self.wait(lambda: not self.window.rig.isRunning() and not self.window.camera.isRunning())
        self.assertIn("STOP", self.controller.events)
        self.assertNotIn("XX_+45", self.controller.events)


if __name__ == "__main__":
    unittest.main()
