from collections import deque
from threading import Event
from types import SimpleNamespace
import time

import numpy as np

from hardware.controller import Cancelled, Controller, Position


class FakeController:
    port = "test-controller"

    def __init__(self, events=None, x=0, z=0):
        self.events = events if events is not None else []
        self.x, self.z = float(x), float(z)
        self.target_x, self.target_z = self.x, self.z
        self.frozen = ""
        self.on_command = lambda text: None
        self.closed = False
        self.white_light = 60

    def command(self, text, cancel=None):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        self.events.append(text)
        if text == "HOME":
            self.target_z = 0
        elif text.startswith("XX_+"):
            self.target_x = self.x + int(text[4:])
        elif text.startswith("Z_"):
            self.target_z = int(text[2:])
        elif text == "STOP":
            self.target_x, self.target_z = self.x, self.z
        elif text.startswith("W_"):
            self.white_light = int(text[2:])
        elif text == "LED_OFF":
            self.white_light = 0
        self.on_command(text)
        return ["OK"]

    def position(self, cancel=None):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if self.frozen != "x":
            self.x += float(np.clip(self.target_x - self.x, -15, 15))
        if self.frozen != "z":
            self.z += float(np.clip(self.target_z - self.z, -100, 100))
        return Position(self.x % 360, self.z)

    def close(self):
        self.closed = True


class FastController(Controller):
    """Производственный парсер/транспорт с короткими таймаутами для эмулятора."""

    RESPONSE_TIMEOUT_S = 0.03
    RESPONSE_QUIET_S = 0.003


class PositionOnlySerial:
    """Применяет команды, но отвечает только на POS — как установка без ACK."""

    def __init__(self, plant=None):
        self.plant = plant if plant is not None else FakeController()
        self.responses = deque()
        self.writes = []
        self.closed = False
        self.drop_positions = 0

    def reset_input_buffer(self):
        self.responses.clear()

    def write(self, data):
        if self.closed:
            raise OSError("Serial port is closed")
        self.writes.append(data)
        command = data.decode("ascii").strip()
        if command == "POS":
            if self.drop_positions:
                self.drop_positions -= 1
            else:
                pos = self.plant.position()
                self.responses.append(f"POS Z={pos.z:.3f}mm X={pos.x:.3f}deg\n".encode())
        else:
            self.plant.command(command)
        return len(data)

    def readline(self):
        if self.responses:
            return self.responses.popleft()
        time.sleep(0.001)
        return b""

    def close(self):
        self.closed = True
        self.plant.close()


class FakeCameras:
    def __init__(self, settings=None):
        self.closed = False
        self.fresh_calls = []

    def read(self, fresh=False):
        self.fresh_calls.append(fresh)
        left = np.zeros((80, 60, 3), dtype=np.uint8)
        left[:, :, 1] = 140
        return left, left.copy()

    def close(self):
        self.closed = True


class ImmediateCamera:
    def __init__(self, events=None):
        self.events = events if events is not None else []

    def capture(self, session, position, cancel):
        self.events.append(("photo", position.x, position.z))
        request = SimpleNamespace(done=Event(), error=None)
        try:
            session.save_pair(FakeCameras().read(), position, cancel)
        except Exception as exc:
            request.error = exc
        request.done.set()
        return request


class BlockedCamera:
    def __init__(self):
        self.called = Event()
        self.request = SimpleNamespace(done=Event(), error=None)

    def capture(self, session, position, cancel):
        self.called.set()
        return self.request
