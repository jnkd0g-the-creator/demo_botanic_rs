from __future__ import annotations

import math
import time

import numpy as np
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QImage, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from core.pea_cloud import PeaCloud


class CloudView(QWidget):
    """XYZ-облако с depth buffer; работает и без OpenGL-драйвера."""

    def __init__(self, cloud: PeaCloud, parent=None):
        super().__init__(parent)
        self.cloud = cloud
        self.center = np.array([0, 0, 620], dtype=np.float32)
        self.yaw, self.pitch, self.zoom = 0.3, 0.08, 1.0
        self.drag = None
        self.resume_at = 0
        self.last_tick = time.monotonic()
        self.setMinimumSize(320, 320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.timer = QTimer(self)
        self.timer.setInterval(60)
        self.timer.timeout.connect(self._tick)

    def showEvent(self, event):
        self.last_tick = time.monotonic()
        self.timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def _tick(self):
        now = time.monotonic()
        if self.drag is None and now > self.resume_at:
            self.yaw += min(now - self.last_tick, 0.12) * 0.12
            self.update()
        self.last_tick = now

    def _projection(self, points, width, height):
        points = points - self.center
        cy, sy, cp, sp = math.cos(self.yaw), math.sin(self.yaw), math.cos(self.pitch), math.sin(self.pitch)
        horizontal = points[:, 0] * cy - points[:, 1] * sy
        depth = points[:, 0] * sy + points[:, 1] * cy
        vertical = points[:, 2] * cp - depth * sp
        depth = depth * cp + points[:, 2] * sp
        scale = min(height / 1460, width / 760) * self.zoom
        perspective = 2500 / (2500 + depth)
        return (width * 0.5 + horizontal * scale * perspective,
                height * 0.49 - vertical * scale * perspective, depth)

    def paintEvent(self, event):
        width, height = max(1, self.width()), max(1, self.height())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#0B1118"))
        painter.setPen(QPen(QColor("#27313E"), 1))
        painter.drawRoundedRect(QRectF(0.5, 0.5, width - 1, height - 1), 13, 13)

        # Растр облака прозрачен; сетка — только ориентир виджета, не часть XYZ.
        painter.setPen(QPen(QColor("#1D2C38"), 1))
        for value in range(-300, 301, 100):
            for endpoints in (([value, -300, 0], [value, 300, 0]),
                              ([-300, value, 0], [300, value, 0])):
                x, y, _ = self._projection(np.array(endpoints), width, height)
                painter.drawLine(QPointF(float(x[0]), float(y[0])), QPointF(float(x[1]), float(y[1])))

        # Ограничение внутреннего разрешения сохраняет отзывчивость на больших экранах.
        ratio = min(1.0, 1100 / width, 1000 / height)
        rw, rh = max(1, int(width * ratio)), max(1, int(height * ratio))
        x, y, depth = self._projection(self.cloud.points, rw, rh)
        x, y = np.rint(x).astype(np.int32), np.rint(y).astype(np.int32)
        valid = (x >= 2) & (x < rw - 2) & (y >= 2) & (y < rh - 2)
        x, y, depth = x[valid], y[valid], depth[valid]
        light = np.clip(1.03 - depth / 1800, 0.68, 1.18)
        colors = np.clip(self.cloud.colors[valid] * light[:, None], 0, 255).astype(np.uint8)
        canvas = np.zeros((rh * rw, 4), dtype=np.uint8)
        z_buffer = np.full(rh * rw, np.inf, dtype=np.float32)
        # Пять splat-пикселей вместо соединённых поверхностей сохраняют вид облака.
        for dx, dy in ((0, 0), (-1, 0), (1, 0), (0, -1), (0, 1)):
            indices = (y + dy) * rw + x + dx
            np.minimum.at(z_buffer, indices, depth)
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (0, 0)):
            indices = (y + dy) * rw + x + dx
            visible = depth <= z_buffer[indices] + 0.01
            selected = indices[visible]
            canvas[selected, :3] = colors[visible]
            canvas[selected, 3] = 255 if dx == dy == 0 else 185
        image = QImage(canvas.data, rw, rh, rw * 4, QImage.Format.Format_RGBA8888)
        painter.drawImage(QRectF(0, 0, width, height), image)
        painter.setPen(QColor("#8A99AB"))
        painter.drawText(18, 28, f"XYZ · RGB     {len(self.cloud.points):,} точек".replace(",", " "))
        painter.drawText(18, height - 18, "Мышь — вращение   ·   Колесо — масштаб   ·   Двойной щелчок — сброс")
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag is not None:
            delta = event.position() - self.drag
            self.yaw += delta.x() * 0.008
            self.pitch = min(0.95, max(-0.95, self.pitch + delta.y() * 0.005))
            self.drag = event.position()
            self.update()

    def mouseReleaseEvent(self, event):
        self.drag = None
        self.resume_at = time.monotonic() + 5
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def mouseDoubleClickEvent(self, event):
        self.yaw, self.pitch, self.zoom = 0.3, 0.08, 1.0
        self.drag = None
        self.resume_at = time.monotonic() + 3
        self.update()

    def wheelEvent(self, event):
        self.zoom = min(2.5, max(0.65, self.zoom * 1.12 ** (event.angleDelta().y() / 120)))
        self.resume_at = time.monotonic() + 3
        self.update()
        event.accept()
