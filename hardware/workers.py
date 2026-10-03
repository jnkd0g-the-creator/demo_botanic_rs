from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field

import cv2
from PyQt6.QtCore import QThread, pyqtSignal

from core.scan import Outcome, Scan
from core.settings import ROOT, Settings
from hardware.cameras import DahengPair
from hardware.controller import Cancelled, Controller

log = logging.getLogger(__name__)


@dataclass
class CaptureRequest:
    session: object
    position: object
    cancel: threading.Event
    done: threading.Event = field(default_factory=threading.Event)
    error: Exception | None = None


@dataclass
class Job:
    cancel: threading.Event = field(default_factory=threading.Event)
    error: str = ""


class CameraWorker(QThread):
    connection = pyqtSignal(bool, str)

    def __init__(self, settings: Settings, parent=None, factory=DahengPair):
        super().__init__(parent)
        self.settings = settings
        self.factory = factory
        self.shutdown = threading.Event()
        self.ready = threading.Event()
        self.requests = queue.Queue(maxsize=1)
        self.preview_lock = threading.Lock()
        self.preview = None

    def capture(self, session, position, cancel):
        request = CaptureRequest(session, position, cancel)
        if not self.ready.is_set() or self.shutdown.is_set():
            request.error = RuntimeError("Камеры не готовы к съёмке")
            request.done.set()
        else:
            try:
                self.requests.put_nowait(request)
            except queue.Full:
                request.error = RuntimeError("Предыдущая съёмка ещё не завершена")
                request.done.set()
        return request

    def take_preview(self):
        with self.preview_lock:
            preview, self.preview = self.preview, None
            return preview

    def _preview(self, frames):
        images = tuple(cv2.resize(frame, (420, round(frame.shape[0] * 420 / frame.shape[1])),
                                  interpolation=cv2.INTER_AREA) for frame in frames)
        with self.preview_lock:
            self.preview = images

    def run(self):
        cameras = None
        failures = 0
        try:
            while not self.shutdown.is_set():
                if cameras is None:
                    try:
                        cameras = self.factory(self.settings)
                    except Exception as exc:
                        self.connection.emit(False, str(exc))
                        self.shutdown.wait(3)
                        continue
                try:
                    request = self.requests.get_nowait()
                except queue.Empty:
                    request = None
                try:
                    if request and request.cancel.is_set():
                        raise Cancelled()
                    frames = cameras.read(fresh=request is not None)
                    if request:
                        request.session.save_pair(frames, request.position, request.cancel)
                    self._preview(frames)
                    failures = 0
                    if not self.ready.is_set():
                        self.ready.set()
                        self.connection.emit(True, "Две камеры Daheng подключены")
                except Cancelled as exc:
                    if request:
                        request.error = exc
                except Exception as exc:
                    if request:
                        request.error = exc
                    failures += 1
                    if request or failures >= 3:
                        self.ready.clear()
                        self.connection.emit(False, str(exc))
                        cameras.close()
                        cameras = None
                        failures = 0
                        self.shutdown.wait(1)
                finally:
                    if request:
                        request.done.set()
                self.shutdown.wait(0.06)
        finally:
            self.ready.clear()
            if cameras:
                cameras.close()
            while True:
                try:
                    request = self.requests.get_nowait()
                except queue.Empty:
                    break
                request.error = Cancelled()
                request.done.set()


class RigWorker(QThread):
    connection = pyqtSignal(bool, str)
    status = pyqtSignal(str)
    position = pyqtSignal(object)
    captured = pyqtSignal(int)
    completed = pyqtSignal(object)

    def __init__(self, settings: Settings, camera: CameraWorker, parent=None, *, output=None, connector=None):
        super().__init__(parent)
        self.settings = settings
        self.camera = camera
        self.shutdown = threading.Event()
        self.jobs = queue.Queue(maxsize=1)
        self.active_job = None
        self.output = output if output is not None else ROOT / "captures"
        self.connector = connector or Controller.connect

    def submit(self, job: Job):
        self.jobs.put_nowait(job)

    def stop_worker(self):
        self.shutdown.set()
        if self.active_job:
            self.active_job.cancel.set()

    def _fail_pending(self, message):
        try:
            job = self.jobs.get_nowait()
        except queue.Empty:
            return
        stopped = job.cancel.is_set() and not job.error
        job.cancel.set()
        self.completed.emit(Outcome(reason="stopped" if stopped else "error",
                                    error="" if stopped else (job.error or message)))

    def run(self):
        client = None
        next_poll = 0
        try:
            while not self.shutdown.is_set():
                if client is None:
                    self._fail_pending("Потеряна связь с контроллером")
                    try:
                        client = self.connector(self.settings, self.shutdown)
                        self.connection.emit(True, f"Контроллер · {client.port}")
                    except Cancelled:
                        break
                    except Exception as exc:
                        self.connection.emit(False, str(exc))
                        self.shutdown.wait(2)
                        continue
                try:
                    job = self.jobs.get(timeout=0.1)
                except queue.Empty:
                    job = None
                if job is not None:
                    self.active_job = job
                    if self.shutdown.is_set():
                        job.cancel.set()
                    scan = Scan(client, self.camera, self.settings, self.output,
                                self.status.emit, self.position.emit, self.captured.emit,
                                abort_error=lambda: job.error)
                    outcome = scan.run(job.cancel)
                    self.active_job = None
                    if outcome.error:
                        self.connection.emit(False, outcome.error)
                        client.close()
                        client = None
                    self.completed.emit(outcome)
                elif time.monotonic() >= next_poll:
                    try:
                        self.position.emit(client.position(self.shutdown))
                    except Cancelled:
                        break
                    except Exception as exc:
                        self.connection.emit(False, str(exc))
                        client.close()
                        client = None
                    next_poll = time.monotonic() + 1
        finally:
            self._fail_pending("Приложение закрывается")
            if client:
                try:
                    client.command("STOP")
                    client.command("LED_OFF")
                except Exception:
                    log.exception("Ошибка остановки при закрытии")
                client.close()
