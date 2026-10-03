import os
import select
import threading
import time
import unittest

from hardware.controller import Busy, Cancelled, Controller, ControllerError, Position, PositionUnavailable, TransportError
from tests.fakes import BlockedSerial, FastController, PositionOnlySerial


class TransportTests(unittest.TestCase):
    def client(self, serial=None, cls=FastController):
        serial = serial if serial is not None else PositionOnlySerial()
        self.options = {}

        def factory(**options):
            self.options.update(options)
            return serial

        client = cls("test-vcp", 115200, serial_factory=factory)
        self.addCleanup(client.close)
        return client, serial

    def delayed(self, delay, callback):
        timer = threading.Timer(delay, callback)
        timer.start()
        self.addCleanup(timer.join)
        return timer

    def run_command(self, client, command, cancel):
        errors = []

        def run():
            try:
                client.command(command, cancel)
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 1)
        self.addCleanup(cancel.set)
        return thread, errors

    def test_write_and_flush_longer_than_300_ms_are_accepted_without_resending(self):
        for phase in ("write", "flush"):
            with self.subTest(phase=phase):
                client, serial = self.client(BlockedSerial())
                serial.block_command = "SMLZ_1355"
                serial.block_phase = phase
                thread, errors = self.run_command(client, "SMLZ_1355", threading.Event())
                self.assertTrue(serial.entered.wait(1))
                self.delayed(0.4, serial.release.set)
                thread.join(1.5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertIsNone(self.options["write_timeout"])
                self.assertEqual(serial.writes, [b"SMLZ_1355\n"])
                self.assertEqual(serial.flushes, 1)
                self.assertEqual(client.position(), Position(0, 0))
                client.close()

    def test_idle_and_inflight_input_is_read_without_using_stale_coordinates(self):
        client, serial = self.client(BlockedSerial())
        serial.responses.append(b"POS Z=999 X=999\n")
        with client._received:
            self.assertTrue(client._received.wait_for(lambda: len(client._lines) == 1, timeout=1))
        serial.block_command = "HOME"
        cancel = threading.Event()
        thread, errors = self.run_command(client, "HOME", cancel)
        self.assertTrue(serial.entered.wait(1))
        serial.responses.append(b"controller is alive\n")
        with client._received:
            self.assertTrue(client._received.wait_for(lambda: len(client._lines) == 2, timeout=1))
        serial.release.set()
        thread.join(1)
        self.assertEqual(errors, [])
        serial.drop_positions = 3
        with self.assertRaisesRegex(ControllerError, "Нет ответа на POS"):
            client.position()
        self.assertEqual(serial.writes.count(b"POS\n"), 3)
        self.assertEqual(client.position(), Position(0, 0))

    def test_all_position_calls_share_1500_ms_limit_and_return_fresh_data(self):
        client, serial = self.client(cls=Controller)
        client.RESPONSE_TIMEOUT_S = 0.03
        client.POSITION_RESPONSE_TIMEOUT_S = 0.03
        client.RESPONSE_QUIET_S = 0.003
        serial.position_replies.append([b"OK\n"])
        self.assertEqual(client.position(), Position(0, 0))
        client.command("HOME")
        serial.plant.z = serial.plant.target_z = 300
        self.assertIn("Z=300.000", client.command("POS")[0])
        serial.plant.z = serial.plant.target_z = 600
        self.assertEqual(client.position(), Position(0, 600))
        times = [at for data, at in zip(serial.writes, serial.write_times) if data == b"POS\n"]
        self.assertEqual(len(times), 4)
        self.assertTrue(all(b - a >= 1.5 for a, b in zip(times, times[1:])))

    def test_missing_and_ack_only_replies_recover_with_fresh_coordinates_on_third_attempt(self):
        client, serial = self.client()
        serial.position_replies.extend([[], [b"OK\n"]])
        serial.plant.x = serial.plant.target_x = 135
        serial.plant.z = serial.plant.target_z = 600
        self.assertEqual(client.position(), Position(135, 600))
        self.assertEqual(serial.writes, [b"POS\n"] * 3)
        self.assertFalse(serial.closed)

    def test_three_missing_or_incomplete_position_replies_are_a_visible_error(self):
        for reply in ([], [b"OK\n"], [b"POS X=135\n"]):
            with self.subTest(reply=reply):
                client, serial = self.client()
                serial.position_replies.extend([reply] * 3)
                with self.assertRaisesRegex(PositionUnavailable, "после 3 попыток") as result:
                    client.position()
                self.assertIn("test-vcp", str(result.exception))
                self.assertEqual(serial.writes, [b"POS\n"] * 3)
                client.close()

    def test_explicit_position_errors_and_transport_failures_are_not_retried(self):
        for reply, error in ((b"ERR:BUSY\n", Busy), (b"ERR:LIMIT\n", ControllerError)):
            with self.subTest(reply=reply):
                client, serial = self.client()
                serial.position_replies.append([reply])
                with self.assertRaises(error):
                    client.position()
                self.assertEqual(serial.writes, [b"POS\n"])
                client.close()

        client, serial = self.client()
        writes = []

        def failed(data):
            writes.append(data)
            raise OSError("USB disconnected")

        serial.write = failed
        with self.assertRaisesRegex(TransportError, "USB disconnected"):
            client.position()
        self.assertEqual(writes, [b"POS\n"])

    def test_stop_interrupts_ack_wait_and_gap_before_position_retry(self):
        for response_timeout in (2.0, 0.03):
            with self.subTest(response_timeout=response_timeout):
                client, serial = self.client(BlockedSerial())
                client.POSITION_RESPONSE_TIMEOUT_S = response_timeout
                client.POSITION_INTERVAL_S = 1.5
                serial.position_replies.append([b"OK\n"])
                cancel = threading.Event()
                serial.on_write = lambda data: self.delayed(0.08, cancel.set) if data == b"POS\n" else None
                started = time.monotonic()
                with self.assertRaises(Cancelled):
                    client.position(cancel)
                client.command("STOP")
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertEqual(serial.writes, [b"POS\n", b"STOP\n"])
                client.close()

    def test_position_recovery_respects_deadline_in_send_response_and_retry_gap(self):
        for phase in ("send", "response", "retry_gap"):
            with self.subTest(phase=phase):
                client, serial = self.client(BlockedSerial())
                client.POSITION_RESPONSE_TIMEOUT_S = 0.03 if phase == "retry_gap" else 2
                client.POSITION_INTERVAL_S = 1.5
                serial.position_replies.append([b"OK\n"])
                if phase == "send":
                    serial.block_command = "POS"
                started = time.monotonic()
                with self.assertRaisesRegex(TimeoutError, "POS .*истекло время"):
                    client.position(deadline=started + 0.08)
                client.command("STOP")
                self.assertLess(time.monotonic() - started, 0.5)
                self.assertEqual(serial.writes[-1].strip(), b"STOP")
                if phase == "send":
                    self.assertTrue(serial.write_cancelled.is_set())
                else:
                    self.assertEqual(serial.writes.count(b"POS\n"), 1)
                client.close()

    def test_cancel_during_position_rate_limit_does_not_delay_stop(self):
        client, serial = self.client()
        client.POSITION_INTERVAL_S = 1.5
        client.position()
        cancel = threading.Event()
        self.delayed(0.04, cancel.set)
        started = time.monotonic()
        with self.assertRaises(Cancelled):
            client.position(cancel)
        client.command("STOP")
        self.assertLess(time.monotonic() - started, 0.4)
        self.assertEqual(serial.writes, [b"POS\n", b"STOP\n"])

    def test_cancel_aborts_write_or_flush_before_stop_without_replaying_motion(self):
        for phase in ("write", "flush"):
            with self.subTest(phase=phase):
                client, serial = self.client(BlockedSerial())
                serial.block_command = "XX_+45"
                serial.block_phase = phase
                cancel = threading.Event()
                thread, errors = self.run_command(client, "XX_+45", cancel)
                self.assertTrue(serial.entered.wait(1))
                cancel.set()
                thread.join(0.5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], Cancelled)
                client.command("STOP")
                self.assertEqual(serial.writes[-1], b"\nSTOP\n")
                self.assertEqual(serial.writes.count(b"XX_+45\n"), int(phase == "flush"))
                self.assertEqual(serial.output_resets, 1)
                self.assertFalse(serial.closed)
                self.assertEqual(client.position(), Position(0, 0))
                client.close()

    def test_write_flush_and_short_write_errors_name_command_port_and_phase(self):
        for phase in ("write", "flush", "short_write"):
            with self.subTest(phase=phase):
                client, serial = self.client()
                calls = []

                def failed(*args):
                    calls.append(args)
                    if phase == "short_write":
                        return 2
                    raise OSError("USB disconnected")

                setattr(serial, "flush" if phase == "flush" else "write", failed)
                with self.assertRaises(TransportError) as result:
                    client.command("XX_+45")
                self.assertIn("XX_+45 (test-vcp)", str(result.exception))
                self.assertIn("flush" if phase == "flush" else "запись", str(result.exception))
                self.assertEqual(len(calls), 1)
                client.close()

    def test_cancelled_poll_does_not_cancel_next_home_when_driver_defers_abort(self):
        client, serial = self.client(BlockedSerial())
        serial.block_command = "POS"
        cancel = threading.Event()
        thread, errors = self.run_command(client, "POS", cancel)
        self.assertTrue(serial.entered.wait(1))
        cancel.set()
        thread.join(0.5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], Cancelled)
        serial.block_command = ""
        original = serial.write
        pending_abort = True

        def write(data):
            nonlocal pending_abort
            count = original(data)
            if pending_abort:
                pending_abort = False
                return 0  # На POSIX cancel_write может прервать будущую запись.
            return count

        serial.write = write
        client.command("HOME")
        self.assertEqual(serial.plant.events, ["HOME"])
        self.assertEqual(serial.writes, [b"\n", b"\nHOME\n"])

    def test_disconnected_reader_fails_next_command_but_still_attempts_stop(self):
        serial = PositionOnlySerial()

        def failed():
            raise OSError("USB disconnected")

        serial.readline = failed
        client, serial = self.client(serial)
        with client._received:
            self.assertTrue(client._received.wait_for(lambda: client._read_error is not None, timeout=1))
        with self.assertRaisesRegex(TransportError, "HOME .*чтение.*USB disconnected"):
            client.command("HOME")
        client.command("STOP")
        self.assertEqual(serial.writes, [b"STOP\n"])

    def test_undeliverable_stop_is_bounded_and_visible(self):
        client, serial = self.client(BlockedSerial())
        serial.block_command = "STOP"
        client.STOP_SEND_TIMEOUT_S = 0.05
        started = time.monotonic()
        with self.assertRaisesRegex(TransportError, "STOP .*запись не завершена"):
            client.command("STOP")
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(serial.write_cancelled.is_set())

    def test_driver_ignoring_cancellation_is_closed_before_any_further_command(self):
        client, serial = self.client(BlockedSerial())
        serial.block_command = "HOME"
        serial.block_phase = "flush"
        serial.reset_output_buffer = lambda: None
        client.CANCEL_WAIT_S = 0.03
        cancel = threading.Event()
        thread, errors = self.run_command(client, "HOME", cancel)
        self.assertTrue(serial.entered.wait(1))
        cancel.set()
        thread.join(0.5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], TransportError)
        self.assertIn("драйвер не прервал flush", str(errors[0]))
        self.assertTrue(serial.closed)
        self.assertFalse(client._reader.is_alive())
        with self.assertRaisesRegex(TransportError, "порт закрыт"):
            client.command("XX_+45")
        self.assertEqual(serial.writes, [b"HOME\n"])

    @unittest.skipUnless(os.name == "posix", "Требуется POSIX serial/pty")
    def test_real_pyserial_survives_400_ms_of_os_backpressure(self):
        import termios

        master, slave = os.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        client = FastController(os.ttyname(slave), 115200)
        self.addCleanup(client.close)
        # Реальный драйвер TTY блокирует отправку, как при задержке USB/VCP.
        termios.tcflow(slave, termios.TCOOFF)
        try:
            started = time.monotonic()
            self.delayed(0.4, lambda: termios.tcflow(slave, termios.TCOON))
            client.command("SMLZ_1355", threading.Event())
            self.assertGreaterEqual(time.monotonic() - started, 0.35)
            self.assertTrue(select.select([master], [], [], 1)[0])
            self.assertEqual(os.read(master, 1024), b"SMLZ_1355\n")
        finally:
            termios.tcflow(slave, termios.TCOON)


if __name__ == "__main__":
    unittest.main()
