from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from threading import Event

from core.settings import Settings


class Cancelled(Exception):
    pass


class ControllerError(RuntimeError):
    pass


class Busy(ControllerError):
    pass


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
    """Единственный владелец serial — поток установки; никаких очередей движений."""

    RESPONSE_TIMEOUT_S = 0.5
    RESPONSE_QUIET_S = 0.08

    def __init__(self, port: str, baud: int, serial_factory=None):
        if serial_factory is None:
            import serial
            serial_factory = serial.Serial
        self.port = port
        self.serial = serial_factory(port=port, baudrate=baud, timeout=0.025, write_timeout=0.3)

    def close(self):
        self.serial.close()

    def command(self, text: str, cancel: Event | None = None) -> list[str]:
        """Настройки/движение могут не иметь ACK; запрос POS обязан вернуть координаты."""
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        self.serial.reset_input_buffer()
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        self.serial.write((text + "\n").encode("ascii"))
        deadline = time.monotonic() + self.RESPONSE_TIMEOUT_S
        quiet_until = deadline
        lines = []
        partial = b""
        while time.monotonic() < min(deadline, quiet_until):
            raw = self.serial.readline()
            if raw:
                partial += raw
                while b"\n" in partial:
                    line, partial = partial.split(b"\n", 1)
                    line = line.decode(errors="replace").strip()
                    if line:
                        lines.append(line)
                reply_complete = text != "POS" or any("ERR" in line.upper() for line in lines)
                if not reply_complete:
                    try:
                        parse_position(lines)
                        reply_complete = True
                    except ControllerError:
                        pass
                # Эхо/OK может прийти раньше координат. Ждём именно ответ POS,
                # а не заканчиваем запрос после первого произвольного сообщения.
                if reply_complete:
                    quiet_until = time.monotonic() + self.RESPONSE_QUIET_S
            # Ожидание ответа прерывается; следующий serial write будет STOP.
            if cancel is not None and cancel.is_set():
                raise Cancelled()
        if partial.strip():
            lines.append(partial.decode(errors="replace").strip())
        for line in lines:
            if "ERR" in line.upper():
                if "BUSY" in line.upper():
                    raise Busy(line)
                raise ControllerError(f"{text}: {line}")
        if not lines and text == "POS":
            raise ControllerError(f"Нет ответа на {text} ({self.port})")
        # Прошивка установки не обязана подтверждать SMLZ/SPD/HOME/XX/Z/STOP/W.
        # При этом явные ERR выше остаются ошибками, а движение проверяется по POS.
        return lines

    def position(self, cancel: Event | None = None) -> Position:
        return parse_position(self.command("POS", cancel))

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
