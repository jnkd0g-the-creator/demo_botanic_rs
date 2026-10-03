from __future__ import annotations

import math
import re
import time
from collections import deque
from dataclasses import dataclass
from threading import Condition, Event, Lock, Thread

from core.settings import Settings


class Cancelled(Exception):
    pass


class ControllerError(RuntimeError):
    pass


class Busy(ControllerError):
    pass


class TransportError(ControllerError):
    pass


class PositionUnavailable(ControllerError):
    """Ответ получен без полных координат либо не пришёл в срок; POS можно повторить."""


@dataclass(frozen=True)
class Position:
    x: float
    z: float


def parse_position(lines: list[str]) -> Position:
    for line in reversed(lines):
        values = {key.upper(): float(value) for key, value in re.findall(
            r"\b([XZ])\s*[=:]\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))", line, re.I
        )}
        if {"X", "Z"} <= values.keys() and all(math.isfinite(v) for v in values.values()):
            return Position(values["X"], values["Z"])
    raise ControllerError("Контроллер не вернул координаты X/Z: " + " | ".join(lines))


class Controller:
    """Последовательные команды, постоянный читатель; очереди движений нет."""

    RESPONSE_TIMEOUT_S = 0.6
    RESPONSE_QUIET_S = 0.08
    POSITION_RESPONSE_TIMEOUT_S = 2.0
    POSITION_ATTEMPTS = 3
    POSITION_INTERVAL_S = 1.5
    POSITION_SEND_TIMEOUT_S = 5.0
    STOP_SEND_TIMEOUT_S = 2.0
    CANCEL_WAIT_S = 0.3

    def __init__(self, port: str, baud: int, serial_factory=None):
        if serial_factory is None:
            import serial
            serial_factory = serial.Serial
        self.port = port
        # Как в исходном VCPClient: чтение непрерывное, запись без лимита 300 мс.
        self.serial = serial_factory(port=port, baudrate=baud, timeout=0.1, write_timeout=None)
        self._closed = Event()
        self._command_lock = Lock()
        self._received = Condition()
        self._lines = deque(maxlen=500)
        self._read_error = None
        self._next_position = 0.0
        self._resync = False
        self._reader = Thread(target=self._read, name=f"VCP reader {port}", daemon=True)
        self._reader.start()

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self.serial.cancel_read()
        except (AttributeError, OSError):
            pass
        try:
            self.serial.close()
        finally:
            with self._received:
                self._received.notify_all()
            self._reader.join(timeout=0.5)

    def _read(self):
        partial = b""
        since = 0.0
        try:
            while not self._closed.is_set():
                raw = self.serial.readline()
                if not raw:
                    continue
                if not partial:
                    since = time.monotonic()
                partial += raw
                with self._received:
                    while b"\n" in partial:
                        line, partial = partial.split(b"\n", 1)
                        line = line.decode(errors="replace").strip()
                        if line:
                            self._lines.append((since, time.monotonic(), line))
                        since = time.monotonic()
                    self._received.notify_all()
        except Exception as exc:
            if not self._closed.is_set():
                with self._received:
                    self._read_error = exc
                    self._received.notify_all()

    def _check(self, text, cancel, deadline=math.inf):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        if self._closed.is_set():
            raise TransportError(f"{text} ({self.port}): serial-порт закрыт")
        if self._read_error is not None and text != "STOP":
            raise TransportError(f"{text} ({self.port}): чтение: {self._read_error}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"{text} ({self.port}): истекло время ожидания")

    def _send(self, text, cancel, deadline=math.inf):
        """write + flush могут блокироваться в драйвере; отмену обслуживает вызывающий поток."""
        done, abort = Event(), Event()
        errors = []
        phase = "запись"
        resync = self._resync
        payload = (("\n" if resync else "") + text + "\n").encode("ascii")

        def transmit():
            nonlocal phase, payload
            try:
                if abort.is_set() or self._closed.is_set() or (cancel is not None and cancel.is_set()):
                    return
                if resync and text != "STOP":
                    # После отмены фонового POS отложенный cancel_write может
                    # затронуть следующую запись. Его принимает разделитель,
                    # а не HOME нового прохода. Повтор движения не требуется.
                    self.serial.write(b"\n")
                    if abort.is_set() or self._closed.is_set():
                        return
                written = self.serial.write(payload)
                if abort.is_set() or self._closed.is_set():
                    return
                # cancel_write на POSIX может затронуть следующую запись.
                # Повторять допустимо только идемпотентный STOP, но не движение.
                if written != len(payload) and text == "STOP":
                    payload = b"\nSTOP\n"
                    written = self.serial.write(payload)
                if abort.is_set() or self._closed.is_set():
                    return
                if written != len(payload):
                    raise OSError(f"неполная запись: {written} из {len(payload)} байт")
                phase = "flush"
                self.serial.flush()
            except Exception as exc:
                errors.append(exc)
            finally:
                done.set()

        writer = Thread(target=transmit, name=f"VCP send {text}", daemon=True)
        writer.start()
        timeout = (self.STOP_SEND_TIMEOUT_S if text == "STOP" else
                   self.POSITION_SEND_TIMEOUT_S if text == "POS" else None)
        send_deadline = time.monotonic() + timeout if timeout is not None else math.inf
        try:
            while not done.wait(0.02):
                self._check(text, cancel, deadline)
                if time.monotonic() >= send_deadline:
                    raise TransportError(f"{text} ({self.port}): {phase} не завершена за {timeout:g} с")
        except (Cancelled, TransportError, TimeoutError):
            abort.set()
            self._resync = True
            try:
                if phase == "запись":
                    self.serial.cancel_write()
                # cancel_write не очищает исходящие данные и не прерывает tcdrain.
                self.serial.reset_output_buffer()
            except Exception as exc:
                self.close()
                raise TransportError(f"{text} ({self.port}): отмена {phase}: {exc}") from exc
            if not done.wait(self.CANCEL_WAIT_S):
                self.close()
                raise TransportError(f"{text} ({self.port}): драйвер не прервал {phase}; порт закрыт")
            raise
        finally:
            if done.is_set():
                writer.join()
        if errors:
            self._resync = True
            raise TransportError(f"{text} ({self.port}): {phase}: {errors[0]}") from errors[0]
        self._resync = False
        self._check(text, cancel, deadline)

    def command(self, text: str, cancel: Event | None = None, *, deadline=math.inf) -> list[str]:
        """Настройки/движение могут не иметь ACK; запрос POS обязан вернуть координаты."""
        while not self._command_lock.acquire(timeout=0.02):
            self._check(text, cancel, deadline)
        try:
            self._check(text, cancel, deadline)
            if text == "POS":
                # Единый лимит для подключения, ожидания движения и фонового опроса.
                # Каждый вызов ждёт новый ответ; старые координаты не кэшируются.
                while time.monotonic() < self._next_position:
                    self._check(text, cancel, deadline)
                    self._closed.wait(min(0.02, self._next_position - time.monotonic()))
                self._check(text, cancel, deadline)
            since = time.monotonic()
            try:
                self._send(text, cancel, deadline)
            finally:
                if text == "POS":
                    self._next_position = time.monotonic() + self.POSITION_INTERVAL_S
            return self._response(text, since, cancel, deadline)
        finally:
            self._command_lock.release()

    def _response(self, text, since, cancel, deadline=math.inf):
        timeout = self.POSITION_RESPONSE_TIMEOUT_S if text == "POS" else self.RESPONSE_TIMEOUT_S
        response_deadline = min(deadline, time.monotonic() + timeout)
        with self._received:
            while True:
                self._check(text, cancel, deadline)
                received = [(at, line) for start, at, line in self._lines if start >= since]
                lines = [line for _, line in received]
                for line in lines:
                    if "ERR" in line.upper():
                        error = Busy if "BUSY" in line.upper() else ControllerError
                        raise error(f"{text} ({self.port}): {line}")
                complete = bool(lines) and text != "POS"
                if text == "POS":
                    try:
                        parse_position(lines)
                        complete = True
                    except ControllerError:
                        pass
                until = (min(response_deadline, received[-1][0] + self.RESPONSE_QUIET_S)
                         if complete else response_deadline)
                remaining = until - time.monotonic()
                if remaining <= 0:
                    break
                self._received.wait(min(0.02, remaining))
        if text == "POS":
            if not lines:
                raise PositionUnavailable(f"Нет ответа на POS ({self.port})")
            try:
                parse_position(lines)
            except ControllerError as exc:
                raise PositionUnavailable(f"POS ({self.port}): {exc}") from exc
        return lines

    def position(self, cancel: Event | None = None, *, deadline=math.inf) -> Position:
        for attempt in range(1, self.POSITION_ATTEMPTS + 1):
            try:
                return parse_position(self.command("POS", cancel, deadline=deadline))
            except PositionUnavailable as exc:
                # Повторяется только запрос координат. ERR, ошибки транспорта,
                # отмена и общий таймаут операции сразу передаются вызывающему коду.
                if attempt == self.POSITION_ATTEMPTS:
                    raise PositionUnavailable(f"Не получены координаты после {attempt} попыток. {exc}") from exc

    @classmethod
    def connect(cls, settings: Settings, shutdown: Event):
        from serial.tools import list_ports

        ports = [settings.port] if settings.port else [p.device for p in sorted(
            list_ports.comports(), key=lambda p: ("ACM" not in p.device, "USB" not in p.device, p.device)
        )]
        last_error = "Serial-порты не найдены. Подключите контроллер или задайте VCP_PORT."
        for port in ports:
            if shutdown.is_set():
                raise Cancelled()
            client = None
            try:
                client = cls(port, settings.baud)
                # Только опрос: подключение не запускает HOME или другие движения.
                client.position(shutdown)
                return client
            except Cancelled:
                if client:
                    client.close()
                raise
            except Exception as exc:
                last_error = f"{port}: {exc}"
                if client:
                    client.close()
        raise ControllerError(last_error)
