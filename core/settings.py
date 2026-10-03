from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    port: str = ""
    baud: int = 115200
    rotation_deg: int = 45
    z_step_mm: int = 300
    z_max_mm: int = 1355
    x_speed: int = 45
    z_speed: int = 40
    x_acceleration: int = 13
    settle_s: float = 0.7
    poll_s: float = 0.1
    position_tolerance: float = 1.0
    home_timeout_s: float = 120
    move_timeout_s: float = 90
    capture_timeout_s: float = 15
    white_light: int = 100
    left_sn: str = "GCA25140001"
    right_sn: str = "GCA25140002"
    left_index: int = 0
    right_index: int = 1
    camera_width: int = 4096
    camera_height: int = 3000
    exposure_us: int = 30000
    swap_red_blue: bool = True

    def validate(self):
        for field in fields(self):
            value = getattr(self, field.name)
            default = field.default
            if isinstance(default, bool):
                valid = isinstance(value, bool)
            elif isinstance(default, str):
                valid = isinstance(value, str)
            else:
                valid = (isinstance(value, (int, float)) and not isinstance(value, bool)
                         and math.isfinite(value))
                if isinstance(default, int):
                    valid = valid and isinstance(value, int)
            if not valid:
                raise ValueError(f"Некорректное значение {field.name}: {value!r}")
        for name in ("baud", "rotation_deg", "z_step_mm", "z_max_mm", "x_speed", "z_speed",
                     "x_acceleration", "poll_s", "position_tolerance", "home_timeout_s",
                     "move_timeout_s", "capture_timeout_s", "camera_width", "camera_height",
                     "exposure_us"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} должно быть больше нуля")
        if not 0 < self.rotation_deg < 180:
            raise ValueError("rotation_deg должно быть от 1 до 179°")
        if not 0 < self.z_max_mm <= 1355:
            raise ValueError("z_max_mm должно быть от 1 до 1355 мм")
        if self.position_tolerance >= min(self.rotation_deg, self.z_step_mm) / 4:
            raise ValueError("position_tolerance слишком велико для выбранного шага")
        if not 0 <= self.white_light <= 100 or self.settle_s < 0:
            raise ValueError("Проверьте white_light (0–100) и settle_s (≥ 0)")
        if min(self.left_index, self.right_index) < 0 or self.left_index == self.right_index:
            raise ValueError("Индексы камер должны быть разными и неотрицательными")
        if self.left_sn and self.left_sn == self.right_sn:
            raise ValueError("Серийные номера камер должны различаться")
        return self


def load_settings(path: Path | None = None) -> Settings:
    path = path or ROOT / "config.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    allowed = {field.name for field in fields(Settings)}
    if not isinstance(data, dict) or set(data) - allowed:
        raise ValueError("Неизвестные параметры в config.json")
    for env, key in (("VCP_PORT", "port"), ("DAHENG_LEFT_SN", "left_sn"),
                     ("DAHENG_RIGHT_SN", "right_sn")):
        if os.getenv(env):
            data[key] = os.environ[env].strip()
    for env, key in (("DAHENG_LEFT_INDEX", "left_index"),
                     ("DAHENG_RIGHT_INDEX", "right_index"),
                     ("DAHENG_EXPOSURE_TIME", "exposure_us")):
        if os.getenv(env):
            data[key] = int(os.environ[env])
    return Settings(**data).validate()
