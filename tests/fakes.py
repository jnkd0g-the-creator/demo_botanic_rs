from threading import Event
from types import SimpleNamespace

import numpy as np

from hardware.controller import Cancelled, Position


class FakeController:
    port = "test-controller"

    def __init__(self, events=None, x=0, z=0):
        self.events = events if events is not None else []
        self.x, self.z = float(x), float(z)
        self.target_x, self.target_z = self.x, self.z
        self.frozen = ""
        self.on_command = lambda text: None
        self.closed = False

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
