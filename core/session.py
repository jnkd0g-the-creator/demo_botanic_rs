from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from hardware.controller import Cancelled, Position


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Session:
    """Фиксация стереопары и завершение сессии взаимно исключают друг друга."""

    def __init__(self, root: Path):
        self.lock = threading.Lock()
        self.finished = False
        self.path = root / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.path.mkdir(parents=True)
        (self.path / "left").mkdir()
        (self.path / "right").mkdir()
        self.data = {"schema_version": 1, "id": self.path.name, "started_at": now_iso(),
                     "status": "running", "pairs": [], "report_is_demonstration": True}
        self._write()

    def _write(self):
        temporary = self.path / "session.json.tmp"
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path / "session.json")

    def save_pair(self, frames, position: Position, cancel: threading.Event):
        import cv2

        temporary = []
        targets = []
        committed = False
        try:
            with self.lock:
                number = len(self.data["pairs"]) + 1
            for side, frame in zip(("left", "right"), frames):
                if cancel.is_set():
                    raise Cancelled()
                if frame is None:
                    raise RuntimeError(f"Нет кадра {side}")
                filename = f"{side}_x{position.x % 360:.1f}_z{position.z:.1f}_photo_{number:03d}.png"
                target = self.path / side / filename
                temp = target.with_suffix(".png.tmp")
                targets.append(target)
                temporary.append(temp)
                ok, encoded = cv2.imencode(".png", cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                                           [cv2.IMWRITE_PNG_COMPRESSION, 1])
                if not ok:
                    raise OSError(f"Не удалось закодировать {side}")
                temp.write_bytes(encoded.tobytes())
            with self.lock:
                if cancel.is_set() or self.finished:
                    raise Cancelled()
                for temp, target in zip(temporary, targets):
                    temp.replace(target)
                pair = {"number": number, "captured_at": now_iso(), "x_deg": position.x % 360,
                        "z_mm": position.z, "left": str(targets[0].relative_to(self.path)),
                        "right": str(targets[1].relative_to(self.path))}
                self.data["pairs"].append(pair)
                try:
                    self._write()
                except Exception:
                    self.data["pairs"].pop()
                    raise
                committed = True
                return pair
        finally:
            for temp in temporary:
                temp.unlink(missing_ok=True)
            if not committed:
                for target in targets:
                    target.unlink(missing_ok=True)

    def finish(self, reason: str, error: str, position: Position | None):
        with self.lock:
            self.finished = True
            self.data.update(status=reason, error=error, finished_at=now_iso())
            if position:
                self.data["last_position"] = {"x_deg": position.x, "z_mm": position.z}
            self._write()

    @property
    def count(self):
        with self.lock:
            return len(self.data["pairs"])
