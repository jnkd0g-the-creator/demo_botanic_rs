from __future__ import annotations

import logging

import cv2

from core.settings import Settings

log = logging.getLogger(__name__)


class DahengPair:
    """Две Daheng-камеры; все вызовы SDK выполняются в одном потоке."""

    def __init__(self, settings: Settings):
        try:
            import gxipy as gx
        except Exception as exc:
            raise RuntimeError(f"Не загружен Daheng Galaxy SDK / gxipy: {exc}") from exc
        self.gx = gx
        self.settings = settings
        self.devices = []
        self.manager = gx.DeviceManager()
        try:
            count, info = self.manager.update_device_list()
            if count < 2:
                raise RuntimeError(f"Найдены камеры Daheng: {count}, необходимо 2")
            available = {str(device.get("sn", "")) for device in info}
            serials = (settings.left_sn, settings.right_sn)
            if all(sn and sn in available for sn in serials):
                for sn in serials:
                    self.devices.append(self.manager.open_device_by_sn(sn))
            else:
                # Совместимость с установкой: резервные индексы, как в botanic_rs.
                for index in (settings.left_index, settings.right_index):
                    if index >= count:
                        raise RuntimeError(f"Камера с индексом {index} не найдена")
                    self.devices.append(self.manager.open_device_by_index(index + 1))
            for device in self.devices:
                self.configure(device)
                device.stream_on()
        except Exception:
            self.close()
            raise

    @staticmethod
    def set_feature(device, name, value, required=False):
        try:
            feature = getattr(device, name)
            if isinstance(value, str):
                choices = feature.get_range()
                if isinstance(choices, dict):
                    value = choices[value]
            feature.set(value)
        except Exception as exc:
            if required:
                raise RuntimeError(f"Параметр камеры {name}: {exc}") from exc
            log.info("Не применён необязательный параметр %s: %s", name, exc)

    def configure(self, device):
        # Экспозиция, усиление и баланс белого из рабочего пресета NP-1230UC.
        # Selector-зависимые значения применяются в исходном порядке.
        for name, value in (
            ("AcquisitionMode", "Continuous"), ("TriggerSelector", "FrameBurstStart"),
            ("TriggerMode", "Off"), ("TriggerSelector", "FrameStart"),
        ):
            self.set_feature(device, name, value)
        self.set_feature(device, "TriggerMode", "Off", required=True)
        for name, value in (
            ("OffsetX", 0), ("OffsetY", 0), ("Width", self.settings.camera_width),
            ("Height", self.settings.camera_height), ("PixelFormat", "BayerRG8"),
            ("ExposureAuto", "Off"), ("ExposureTime", float(self.settings.exposure_us)),
        ):
            self.set_feature(device, name, value, required=True)
        for name, value in (
            ("AcquisitionFrameRateMode", "Off"), ("GainSelector", "AnalogAll"),
            ("GainAuto", "Off"), ("Gain", 6.0), ("GainSelector", "DigitalAll"),
            ("GainAuto", "Off"), ("Gain", 0.0), ("GainSelector", "AnalogAll"),
            ("GammaEnable", True), ("GammaMode", "SRGB"),
            ("BlackLevelSelector", "All"), ("BlackLevel", 10.0),
            ("BalanceWhiteAuto", "Off"), ("BalanceRatioSelector", "Red"),
            ("BalanceRatio", 2.10938), ("BalanceRatioSelector", "Green"),
            ("BalanceRatio", 1.0), ("BalanceRatioSelector", "Blue"),
            ("BalanceRatio", 1.95703), ("BalanceRatioSelector", "Red"),
        ):
            self.set_feature(device, name, value)

    def read(self, fresh=False):
        if fresh:
            for device in self.devices:
                device.data_stream[0].flush_queue()
        frames = []
        for index, device in enumerate(self.devices):
            raw = device.data_stream[0].get_image(timeout=500)
            if raw is None or raw.get_status() != self.gx.GxFrameStatusList.SUCCESS:
                raise RuntimeError(f"Нет полного кадра {'левой' if index == 0 else 'правой'} камеры")
            rgb = raw.convert("RGB")
            if rgb is None:
                raise RuntimeError("Ошибка преобразования кадра Daheng в RGB")
            frame = rgb.get_numpy_array()
            if frame is None:
                raise RuntimeError("Daheng вернула пустой кадр")
            if self.settings.swap_red_blue:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            # Ориентация точно соответствует установленным в botanic_rs камерам.
            rotation = cv2.ROTATE_90_CLOCKWISE if index == 0 else cv2.ROTATE_90_COUNTERCLOCKWISE
            frames.append(cv2.rotate(frame, rotation))
        return tuple(frames)

    def close(self):
        for device in self.devices:
            try:
                device.stream_off()
            except Exception:
                pass
            try:
                device.close_device()
            except Exception:
                log.exception("Ошибка закрытия камеры")
        self.devices.clear()
