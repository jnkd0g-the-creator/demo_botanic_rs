from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QHBoxLayout, QMainWindow, QProgressBar, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from core.scan import Outcome
from core.settings import Settings
from hardware.workers import CameraWorker, Job, RigWorker
from ui.report import Report
from ui.widgets import CameraView, card, label


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, *, report_only=False, start_hardware=True):
        super().__init__()
        self.settings = settings
        self.report_only = report_only
        self.controller_ready = self.camera_ready = False
        self.controller_error = self.camera_error = ""
        self.job = None
        self.camera = self.rig = None
        self.closing = False
        self.setWindowTitle("Botanic RS · Демонстрация")
        self.resize(1440, 900)
        self.setMinimumSize(960, 640)
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.main_page = self._build_main()
        self.stack.addWidget(self.main_page)
        self.report = Report()
        self.report.close_requested.connect(self.close_report)
        self.stack.addWidget(self.report)
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(80)
        self.preview_timer.timeout.connect(self._preview)
        self.close_timer = QTimer(self)
        self.close_timer.setInterval(100)
        self.close_timer.timeout.connect(self._finish_close)
        if report_only:
            self.show_report(Outcome(reason="preview"))
        elif start_hardware:
            self.camera = CameraWorker(settings, self)
            self.rig = RigWorker(settings, self.camera, self)
            self.camera.connection.connect(self._camera_connection)
            self.rig.connection.connect(self._controller_connection)
            self.rig.status.connect(self.status_label.setText)
            self.rig.position.connect(self._position)
            self.rig.captured.connect(self._captured)
            self.rig.completed.connect(self._completed)
            self.camera.start()
            self.rig.start()
            self.preview_timer.start()
        self._buttons()

    def _build_main(self):
        page = QWidget()
        page.setObjectName("page")
        root = QVBoxLayout(page)
        root.setContentsMargins(16, 12, 16, 14)
        root.setSpacing(12)
        top, layout = card()
        row = QHBoxLayout()
        title_column = QVBoxLayout()
        title_column.addWidget(label("BOTANIC RS", "title"))
        title_column.addWidget(label("Сегментация и фенотипирование гороха", "subtitle"))
        row.addLayout(title_column)
        row.addWidget(label("DEMO", "demoBadge"))
        row.addStretch()
        self.controller_label = label("Контроллер · поиск", "chip")
        self.camera_label = label("Камеры · подключение", "chip")
        row.addWidget(self.controller_label)
        row.addWidget(self.camera_label)
        layout.addLayout(row)
        root.addWidget(top)
        information = QHBoxLayout()
        self.x_label = label("X · —°", "chip")
        self.z_label = label(f"Z · — / {self.settings.z_max_mm} мм", "chipAccent")
        self.pair_label = label("Стереопар · 0", "chip")
        information.addWidget(self.x_label)
        information.addWidget(self.z_label)
        information.addWidget(self.pair_label)
        information.addStretch()
        information.addWidget(label(f"Поворот {self.settings.rotation_deg}°  ·  Подъём {self.settings.z_step_mm} мм",
                                    "subtitle"))
        root.addLayout(information)
        cameras = QHBoxLayout()
        cameras.setSpacing(12)
        self.views = (CameraView("Левая камера", self.settings.z_max_mm, scales=True),
                      CameraView("Правая камера", self.settings.z_max_mm))
        for view in self.views:
            cameras.addWidget(view, 1)
        root.addLayout(cameras, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, self.settings.z_max_mm)
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        root.addWidget(self.progress)
        bottom, layout = card()
        row = QHBoxLayout()
        state = QVBoxLayout()
        self.status_label = label("Подключение оборудования…", "statusLabel", wrap=True)
        self.hint_label = label("Старт → домой → поворот → снимок → подъём", "subtitle", wrap=True)
        state.addWidget(self.status_label)
        state.addWidget(self.hint_label)
        row.addLayout(state, 1)
        self.start_button = QPushButton("Старт")
        self.start_button.setObjectName("startButton")
        self.stop_button = QPushButton("Стоп")
        self.stop_button.setObjectName("stopButton")
        for button in (self.start_button, self.stop_button):
            button.setMinimumSize(160, 56)
            row.addWidget(button)
        self.start_button.clicked.connect(self.start_scan)
        self.stop_button.clicked.connect(self.stop_scan)
        layout.addLayout(row)
        root.addWidget(bottom)
        return page

    def _buttons(self):
        idle = self.job is None and not self.closing and self.stack.currentWidget() == self.main_page
        self.start_button.setEnabled(idle and self.controller_ready and self.camera_ready and self.rig is not None)
        self.stop_button.setEnabled(self.job is not None and not self.job.cancel.is_set() and not self.closing)

    def _idle_status(self):
        if self.job is not None or self.closing:
            return
        if self.controller_ready and self.camera_ready:
            self.status_label.setText("Установка готова")
            self.hint_label.setText("Нажмите «Старт» для нового прохода снизу")
        else:
            self.status_label.setText("Ожидание оборудования")
            messages = [error for error in (self.controller_error, self.camera_error) if error]
            self.hint_label.setText("\n".join(messages) or "Подключение контроллера и камер…")

    def _camera_connection(self, ready, message):
        self.camera_ready = ready
        self.camera_error = "" if ready else message
        self.camera_label.setText("Камеры · 2 / 2" if ready else "Камеры · нет связи")
        self.camera_label.setToolTip(message)
        if not ready:
            for view in self.views:
                view.clear_frame()
            if self.job and not self.job.cancel.is_set():
                self.job.error = message
                self.job.cancel.set()
        self._idle_status()
        self._buttons()

    def _controller_connection(self, ready, message):
        self.controller_ready = ready
        self.controller_error = "" if ready else message
        self.controller_label.setText("Контроллер · подключён" if ready else "Контроллер · нет связи")
        self.controller_label.setToolTip(message)
        self._idle_status()
        self._buttons()

    def _position(self, position):
        self.x_label.setText(f"X · {position.x % 360:.0f}°")
        self.z_label.setText(f"Z · {position.z:.0f} / {self.settings.z_max_mm} мм")
        self.progress.setValue(round(position.z))
        for view in self.views:
            view.set_position(position)

    def _captured(self, count):
        self.pair_label.setText(f"Стереопар · {count}")

    def _preview(self):
        if not self.camera or not self.camera_ready:
            return
        frames = self.camera.take_preview()
        if frames and self.stack.currentWidget() == self.main_page:
            for view, frame in zip(self.views, frames):
                view.set_frame(frame)

    def start_scan(self):
        if self.job is not None or not self.start_button.isEnabled():
            return
        self.job = Job()
        self._captured(0)
        self.status_label.setText("Начало нового прохода")
        self.hint_label.setText("«Стоп» завершит движение и откроет демонстрационный отчёт")
        self.rig.submit(self.job)
        self._buttons()

    def stop_scan(self):
        if self.job is not None:
            self.job.cancel.set()
            self.status_label.setText("Остановка установки…")
            self._buttons()

    def _completed(self, outcome):
        self.job = None
        self._captured(outcome.pairs)
        if not self.closing:
            self.show_report(outcome)
        self._buttons()

    def show_report(self, outcome):
        self.report.set_outcome(outcome)
        self.stack.setCurrentWidget(self.report)

    def close_report(self):
        if self.report_only:
            self.close()
            return
        self.stack.setCurrentWidget(self.main_page)
        self._idle_status()
        self._buttons()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self.stack.currentWidget() == self.report:
                self.close_report()
            elif self.job:
                self.stop_scan()
            else:
                self.close()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        workers = [worker for worker in (self.rig, self.camera) if worker is not None]
        if any(worker.isRunning() for worker in workers):
            event.ignore()
            self.closing = True
            if self.job:
                self.job.cancel.set()
            if self.rig:
                self.rig.stop_worker()
            if self.camera:
                self.camera.shutdown.set()
            self.preview_timer.stop()
            self.status_label.setText("Остановка и закрытие оборудования…")
            self._buttons()
            self.close_timer.start()
        else:
            self.close_timer.stop()
            event.accept()

    def _finish_close(self):
        if all(worker is None or not worker.isRunning() for worker in (self.rig, self.camera)):
            self.close_timer.stop()
            self.close()
