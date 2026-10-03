import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from core.pea_cloud import make_pea_cloud
from core.session import Session
from core.settings import ROOT, load_settings
from hardware.controller import Busy, Cancelled, Controller, ControllerError, Position, parse_position
from tests.fakes import FastController, PositionOnlySerial


class StorageTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".checks").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".checks")
        self.addCleanup(self.temp.cleanup)
        self.session = Session(Path(self.temp.name))
        self.frames = tuple(np.full((24, 16, 3), [220, 30, 60], dtype=np.uint8) for _ in range(2))

    def test_pngs_and_manifest_keep_both_sides_and_rgb_colors(self):
        pair = self.session.save_pair(self.frames, Position(45, 300), threading.Event())
        self.assertEqual(pair["number"], 1)
        self.session.finish("completed", "", Position(45, 300))
        for side in ("left", "right"):
            image = cv2.imread(str(self.session.path / pair[side]))
            np.testing.assert_array_equal(image[0, 0], [60, 30, 220])
        data = json.loads((self.session.path / "session.json").read_text())
        self.assertEqual(len(data["pairs"]), 1)
        self.assertTrue(data["report_is_demonstration"])

    def test_encoding_failure_does_not_leave_partial_stereo_pair(self):
        original = cv2.imencode
        count = 0
        def encoder(*args):
            nonlocal count
            count += 1
            return original(*args) if count == 1 else (False, None)
        with patch("cv2.imencode", side_effect=encoder), self.assertRaises(OSError):
            self.session.save_pair(self.frames, Position(0, 0), threading.Event())
        self.assertEqual(self.session.count, 0)
        self.assertFalse(list(self.session.path.rglob("*.png*")))

    def test_late_frame_cannot_commit_after_report_has_been_finalized(self):
        entered, release = threading.Event(), threading.Event()
        original = cv2.imencode
        def encoder(*args):
            entered.set()
            if not release.wait(2):
                raise TimeoutError("Test did not release encoder")
            return original(*args)
        errors = []
        def write():
            try:
                self.session.save_pair(self.frames, Position(0, 0), threading.Event())
            except Exception as exc:
                errors.append(exc)
        with patch("cv2.imencode", side_effect=encoder):
            thread = threading.Thread(target=write)
            thread.start()
            self.assertTrue(entered.wait(1))
            self.session.finish("stopped", "", Position(0, 0))
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], Cancelled)
        self.assertFalse(list(self.session.path.rglob("*.png*")))
        self.assertEqual(self.session.count, 0)


class FakeSerial(PositionOnlySerial):
    def __init__(self, responses):
        super().__init__()
        self.reply = list(responses)
        self.on_write = lambda: None

    def write(self, value):
        self.writes.append(value)
        self.responses.extend(self.reply)
        self.on_write()
        return len(value)


class ProtocolTests(unittest.TestCase):
    def client(self, responses):
        self.serial = FakeSerial(responses)
        client = Controller("fake", 115200, serial_factory=lambda **kwargs: self.serial)
        self.addCleanup(client.close)
        return client

    def test_position_units_and_fragmented_serial_response(self):
        self.assertEqual(parse_position(["OK", "POS Z=125.000mm X=-45deg"]), Position(-45, 125))
        client = self.client([b"POS Z=300mm X", b"=90deg\r\n"])
        self.assertEqual(client.position(), Position(90, 300))
        self.assertEqual(self.serial.writes, [b"POS\n"])

    def test_reject_incomplete_and_invalid_position(self):
        for response in ([], ["OK"], ["POS X=45"], ["POS X=nan Z=0"]):
            with self.subTest(response=response), self.assertRaises(ControllerError):
                parse_position(response)

    def test_controller_errors_and_busy_are_not_success(self):
        with self.assertRaises(Busy):
            self.client([b"ERR:BUSY\n"]).command("HOME")
        with self.assertRaises(ControllerError):
            self.client([b"ERR:LIMIT\n"]).command("Z_1400")

    def test_control_commands_accept_empty_reply_but_pos_still_requires_coordinates(self):
        serial = FakeSerial([])
        client = FastController("fake", 115200, serial_factory=lambda **_: serial)
        self.addCleanup(client.close)
        for command in ("SMLZ_1355", "SPDZ_40", "SPDX_45", "ACLX_13", "HOME",
                        "XX_+45", "Z_300", "W_100", "STOP"):
            with self.subTest(command=command):
                self.assertEqual(client.command(command), [])
        with self.assertRaisesRegex(ControllerError, "Нет ответа на POS"):
            client.position()

    def test_position_waits_past_old_600_ms_timeout_for_delayed_coordinates(self):
        client = self.client([])
        client.RESPONSE_QUIET_S = 0.01
        def reply():
            self.serial.responses.append(b"OK\n")
            timer = threading.Timer(0.8, self.serial.responses.append, args=(b"POS Z=600 X=135\n",))
            self.addCleanup(timer.join)
            timer.start()
        self.serial.on_write = reply
        self.assertEqual(client.position(), Position(135, 600))
        self.assertEqual(self.serial.writes, [b"POS\n"])

    def test_stop_event_prevents_serial_motion_write(self):
        cancel = threading.Event()
        cancel.set()
        client = self.client([])
        with self.assertRaises(Cancelled):
            client.command("HOME", cancel)
        self.assertEqual(self.serial.writes, [])

    def test_stop_can_interrupt_serial_response_wait(self):
        cancel = threading.Event()
        client = self.client([])
        def written():
            timer = threading.Timer(0.04, cancel.set)
            self.addCleanup(timer.join)
            timer.start()
        self.serial.on_write = written
        with self.assertRaises(Cancelled):
            client.command("XX_+45", cancel)
        self.assertEqual(self.serial.writes, [b"XX_+45\n"])


class CloudAndSettingsTests(unittest.TestCase):
    def test_cloud_is_finite_3d_reproducible_and_contains_only_plant_organs(self):
        cloud = make_pea_cloud()
        self.assertGreater(len(cloud.points), 30000)
        self.assertEqual(cloud.points.shape, cloud.colors.shape)
        self.assertTrue(np.isfinite(cloud.points).all())
        self.assertGreater(float(np.ptp(cloud.points[:, 1])), 100)
        self.assertGreaterEqual(float(cloud.points[:, 2].min()), 0)
        self.assertEqual(set(cloud.organs), set(range(6)))
        # У основания только узкий стебель, а не горшок или плоскость грунта.
        base = cloud.points[cloud.points[:, 2] < 30]
        self.assertLess(float(np.ptp(base[:, 0])), 15)
        self.assertAlmostEqual(cloud.metrics["height_cm"], cloud.points[:, 2].max() / 10, delta=0.1)
        np.testing.assert_array_equal(cloud.points, make_pea_cloud().points)

    def test_config_is_loaded_relative_to_project(self):
        config = load_settings()
        self.assertEqual((config.rotation_deg, config.z_step_mm, config.z_max_mm), (45, 300, 1355))


if __name__ == "__main__":
    unittest.main()
