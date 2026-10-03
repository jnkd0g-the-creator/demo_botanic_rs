from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from core.session import Session
from core.settings import Settings
from hardware.controller import Busy, Cancelled, Position


@dataclass
class Outcome:
    reason: str = "completed"
    error: str = ""
    session_path: str = ""
    pairs: int = 0
    position: Position | None = None
    elapsed_s: float = 0


class Scan:
    """Последовательный автомат: HOME → X → кадр → Z; отмена через Event."""

    def __init__(self, controller, camera, settings: Settings, output: Path,
                 status=lambda text: None, position=lambda pos: None, captured=lambda n: None,
                 abort_error=lambda: ""):
        self.controller = controller
        self.camera = camera
        self.settings = settings
        self.output = output
        self.status = status
        self.on_position = position
        self.on_captured = captured
        self.abort_error = abort_error
        self.position = None

    @staticmethod
    def pause(seconds, cancel):
        if cancel.wait(max(0, seconds)):
            raise Cancelled()

    def command(self, text, cancel):
        deadline = time.monotonic() + self.settings.move_timeout_s
        while True:
            if cancel.is_set():
                raise Cancelled()
            try:
                return self.controller.command(text, cancel)
            except Busy:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Контроллер занят: {text}")
                self.pause(self.settings.poll_s, cancel)

    def read_position(self, cancel):
        pos = self.controller.position(cancel)
        if not (-2 <= pos.z <= self.settings.z_max_mm + 2):
            raise RuntimeError(f"Координата Z вне диапазона: {pos.z:.1f} мм")
        self.position = pos
        self.on_position(pos)
        return pos

    def wait_position(self, axis, target, cancel, timeout):
        deadline = time.monotonic() + timeout
        stable = 0
        previous = None
        while time.monotonic() < deadline:
            self.pause(self.settings.poll_s, cancel)
            try:
                pos = self.read_position(cancel)
            except Busy:
                continue
            value = getattr(pos, axis)
            distance = abs(value - target) if axis == "z" else abs((value - target + 180) % 360 - 180)
            stationary = previous is not None and abs(value - previous) < 0.15
            stable = stable + 1 if distance <= self.settings.position_tolerance and stationary else 0
            previous = value
            if stable >= 3:
                return pos
        raise TimeoutError(f"Не достигнута позиция {axis.upper()} = {target:g}")

    def run(self, cancel: Event) -> Outcome:
        started = time.monotonic()
        outcome = Outcome()
        session = None
        try:
            if cancel.is_set():
                raise Cancelled()
            session = Session(self.output)
            outcome.session_path = str(session.path)
            self.status("Возврат камер вниз · HOME")
            self.command("HOME", cancel)
            self.pause(0.4, cancel)
            self.wait_position("z", 0, cancel, self.settings.home_timeout_s)
            # Как в рабочем приложении: настройки осей применяются после HOME.
            self.status("Подготовка установки")
            for text in (f"SMLZ_{self.settings.z_max_mm}", f"SPDZ_{self.settings.z_speed}",
                         f"SPDX_{self.settings.x_speed}", f"ACLX_{self.settings.x_acceleration}",
                         f"W_{self.settings.white_light}"):
                self.command(text, cancel)

            height = 0
            while height < self.settings.z_max_mm:
                pos = self.read_position(cancel)
                target_x = (pos.x + self.settings.rotation_deg) % 360
                self.status(f"Поворот платформы +{self.settings.rotation_deg}°")
                self.command(f"XX_+{self.settings.rotation_deg}", cancel)
                self.wait_position("x", target_x, cancel, self.settings.move_timeout_s)
                self.status("Стабилизация перед снимком")
                self.pause(self.settings.settle_s, cancel)
                pos = self.read_position(cancel)
                self.status(f"Съёмка стереопары · Z = {pos.z:.0f} мм")
                request = self.camera.capture(session, pos, cancel)
                deadline = time.monotonic() + self.settings.capture_timeout_s
                while not request.done.wait(0.03):
                    if cancel.is_set():
                        raise Cancelled()
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Истекло время ожидания стереопары")
                if cancel.is_set():
                    raise Cancelled()
                if request.error:
                    raise request.error
                self.on_captured(session.count)
                height = min(height + self.settings.z_step_mm, self.settings.z_max_mm)
                self.status(f"Подъём камер · Z = {height} мм")
                self.command(f"Z_{height}", cancel)
                self.wait_position("z", height, cancel, self.settings.move_timeout_s)
        except Cancelled:
            outcome.reason = "stopped"
        except Exception as exc:
            outcome.reason = "error"
            outcome.error = str(exc)
        finally:
            # Закрываем доступ к новым кадрам раньше любой финализации.
            cancel.set()
            if self.abort_error():
                outcome.reason = "error"
                outcome.error = "; ".join(filter(None, (outcome.error, self.abort_error())))
            self.status("Остановка установки")
            try:
                self.controller.command("STOP")
            except Exception as exc:
                outcome.reason = "error"
                outcome.error = "; ".join(filter(None, (outcome.error, f"STOP: {exc}")))
            outcome.position = self.position
            if session:
                try:
                    session.finish(outcome.reason, outcome.error, self.position)
                except Exception as exc:
                    outcome.reason = "error"
                    outcome.error = "; ".join(filter(None, (outcome.error, f"Запись сессии: {exc}")))
                outcome.pairs = session.count
            outcome.elapsed_s = time.monotonic() - started
        return outcome
