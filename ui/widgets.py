from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget


def label(text, name="", wrap=False):
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def card(title=None):
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(8)
    if title:
        layout.addWidget(label(title, "cardTitle"))
    return frame, layout


class CameraView(QWidget):
    def __init__(self, title, max_height, scales=False, parent=None):
        super().__init__(parent)
        self.title, self.max_height, self.scales = title, max_height, scales
        self.frame = None
        self.x = self.z = 0.0
        self.setMinimumSize(280, 260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_frame(self, frame):
        height, width, _ = frame.shape
        image = QImage(frame.data, width, height, frame.strides[0], QImage.Format.Format_RGB888)
        self.frame = QPixmap.fromImage(image)
        self.update()

    def clear_frame(self):
        self.frame = None
        self.update()

    def set_position(self, position):
        self.x, self.z = position.x, position.z
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor("#0B0F15"))
        p.setPen(QPen(QColor("#27313E"), 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1), 14, 14)
        p.setPen(QColor("#E6EDF5"))
        p.setFont(QFont("DejaVu Sans", 11, QFont.Weight.Bold))
        p.drawText(18, 28, self.title)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#2FBF71" if self.frame else "#54677E"))
        p.drawEllipse(QPointF(self.width() - 24, 24), 4, 4)
        area = QRectF(16, 44, self.width() - (76 if self.scales else 32),
                      self.height() - (106 if self.scales else 64))
        if self.frame:
            size = self.frame.size().scaled(int(area.width()), int(area.height()), Qt.AspectRatioMode.KeepAspectRatio)
            destination = QRectF(area.center().x() - size.width() / 2,
                                 area.center().y() - size.height() / 2, size.width(), size.height())
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap(destination, self.frame, QRectF(self.frame.rect()))
        else:
            p.setPen(QColor("#8A99AB"))
            p.setFont(QFont("DejaVu Sans", 12))
            p.drawText(area, Qt.AlignmentFlag.AlignCenter, "Ожидание камеры\n\nDAHENG · LIVE")
        if self.scales:
            self._axes(p, area)
        p.end()

    def _axes(self, p, area):
        p.setFont(QFont("DejaVu Sans", 8))
        p.setPen(QPen(QColor("#3B4C61"), 1))
        axis_x = self.width() - 48
        p.drawLine(QPointF(axis_x, area.top()), QPointF(axis_x, area.bottom()))
        for z in sorted(set([0, self.max_height] + list(range(300, self.max_height, 300)))):
            y = area.bottom() - z / self.max_height * area.height()
            p.drawLine(QPointF(axis_x - 4, y), QPointF(axis_x + 3, y))
            p.setPen(QColor("#8A99AB"))
            p.drawText(QRectF(axis_x + 7, y - 8, 38, 16), Qt.AlignmentFlag.AlignVCenter, str(z))
            p.setPen(QColor("#3B4C61"))
        p.setBrush(QColor("#FFB020"))
        p.setPen(Qt.PenStyle.NoPen)
        marker_y = area.bottom() - min(1, max(0, self.z / self.max_height)) * area.height()
        p.drawEllipse(QPointF(axis_x, marker_y), 4, 4)
        axis_y = self.height() - 39
        p.setPen(QColor("#3B4C61"))
        p.drawLine(QPointF(area.left(), axis_y), QPointF(area.right(), axis_y))
        for angle in (0, 90, 180, 270, 360):
            x = area.left() + angle / 360 * area.width()
            p.drawLine(QPointF(x, axis_y - 3), QPointF(x, axis_y + 3))
            p.setPen(QColor("#8A99AB"))
            p.drawText(QRectF(x - 16, axis_y + 8, 34, 16), Qt.AlignmentFlag.AlignCenter, f"{angle}°")
            p.setPen(QColor("#3B4C61"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(area.left() + self.x % 360 / 360 * area.width(), axis_y), 4, 4)
