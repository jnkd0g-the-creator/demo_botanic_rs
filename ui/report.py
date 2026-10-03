from datetime import datetime
import re

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QPushButton, QScrollArea, QVBoxLayout, QWidget

from core.pea_cloud import ORGAN_COLORS, ORGAN_NAMES, make_pea_cloud
from core.scan import Outcome
from ui.cloud_view import CloudView
from ui.widgets import card, label


class Report(QWidget):
    close_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("page")
        self.cloud = make_pea_cloud()
        metrics = self.cloud.metrics
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(14)
        top, top_layout = card()
        row = QHBoxLayout()
        titles = QVBoxLayout()
        titles.addWidget(label("Отчёт о фенотипировании", "title"))
        self.subtitle = label("Pisum sativum · DEMO-001", "subtitle")
        titles.addWidget(self.subtitle)
        row.addLayout(titles, 1)
        row.addWidget(label("ДЕМОНСТРАЦИЯ", "demoBadge"))
        self.close_button = QPushButton("Закрыть")
        self.close_button.setMinimumWidth(135)
        self.close_button.clicked.connect(self.close_requested.emit)
        row.addWidget(self.close_button)
        top_layout.addLayout(row)
        root.addWidget(top)

        body = QHBoxLayout()
        body.setSpacing(14)
        model_column = QVBoxLayout()
        heading = QHBoxLayout()
        heading.addWidget(label("ПРОСТРАНСТВЕННАЯ МОДЕЛЬ РАСТЕНИЯ", "cardTitle"))
        heading.addStretch()
        heading.addWidget(label("мм · RGB", "subtitle"))
        model_column.addLayout(heading)
        self.viewer = CloudView(self.cloud)
        model_column.addWidget(self.viewer, 1)
        legend = QHBoxLayout()
        for name, color in zip(ORGAN_NAMES, ORGAN_COLORS):
            rgb = ",".join(map(str, color))
            item = label(f'<span style="color:rgb({rgb})">●</span> {name}', "subtitle")
            legend.addWidget(item)
        legend.addStretch()
        model_column.addLayout(legend)
        body.addLayout(model_column, 3)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(305)
        scroll.setMaximumWidth(430)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        contents = QWidget()
        details = QVBoxLayout(contents)
        details.setContentsMargins(0, 0, 3, 0)
        details.setSpacing(12)
        details.addWidget(label("ДЕМОНСТРАЦИОННЫЕ ПОКАЗАТЕЛИ", "cardTitle"))
        grid = QGridLayout()
        grid.setSpacing(9)
        entries = (("ВЫСОТА", f"{metrics['height_cm']:.1f}", "см"),
                   ("ДЛИНА СТЕБЛЯ", f"{metrics['stem_length_cm']:.1f}", "см"),
                   ("УЗЛЫ", metrics["nodes"], "шт."),
                   ("БОБЫ", metrics["pods"], "шт."),
                   ("ЛИСТОЧКИ", metrics["leaflets"], "шт."),
                   ("ЦВЕТКИ", metrics["flowers"], "шт."))
        for index, (name, value, unit) in enumerate(entries):
            tile, layout = card(name)
            layout.setContentsMargins(12, 11, 12, 11)
            value_row = QHBoxLayout()
            value_row.addWidget(label(str(value).replace(".", ","), "metric"))
            value_row.addWidget(label(unit, "subtitle"), alignment=Qt.AlignmentFlag.AlignBottom)
            value_row.addStretch()
            layout.addLayout(value_row)
            grid.addWidget(tile, index // 2, index % 2)
        details.addLayout(grid)
        morphology, layout = card("МОРФОЛОГИЯ И ПРОДУКТИВНОСТЬ")
        for name, value in (("Прилистники", f"{metrics['stipules']} шт."),
                            ("Усики", f"{metrics['tendrils']} шт."),
                            ("Площадь листьев", f"{metrics['leaf_area_cm2']:.1f} см²"),
                            ("Продуктивные узлы", f"{metrics['productive_nodes']} шт."),
                            ("Нижний боб", f"{metrics['lower_pod_height_cm']:.1f} см"),
                            ("Семена", f"{metrics['seeds']} шт."),
                            ("Масса зерна", f"{metrics['grain_mass_g']:.1f} г")):
            item = QHBoxLayout()
            item.addWidget(label(name, "subtitle"))
            item.addStretch()
            item.addWidget(label(re.sub(r"(?<=\d)\.(?=\d)", ",", value)))
            layout.addLayout(item)
        details.addWidget(morphology)
        session, layout = card("СЕАНС СЪЁМКИ")
        self.session_summary = label("", "subtitle", wrap=True)
        layout.addWidget(self.session_summary)
        self.error_label = label("", "error", wrap=True)
        layout.addWidget(self.error_label)
        details.addWidget(session)
        details.addStretch()
        scroll.setWidget(contents)
        body.addWidget(scroll, 2)
        root.addLayout(body, 1)
        root.addWidget(label("Демонстрационные показатели и синтетическое облако точек гороха без горшка.",
                             "subtitle"))

    def set_outcome(self, outcome: Outcome):
        reason = {"completed": "Достигнута максимальная высота", "stopped": "Остановлено пользователем",
                  "error": "Съёмка остановлена: ошибка оборудования", "preview": "Предпросмотр отчёта"}[outcome.reason]
        self.subtitle.setText(f"Pisum sativum · DEMO-001 · {datetime.now():%d.%m.%Y %H:%M}")
        actual = (f"{outcome.pairs} стереопар · {outcome.pairs * 2} фотографий\n"
                  f"Время: {outcome.elapsed_s:.0f} с")
        if outcome.position:
            actual += f" · Z = {outcome.position.z:.0f} мм"
        self.session_summary.setText(f"{reason}\n\n{actual}" if outcome.reason != "preview" else reason)
        self.session_summary.setToolTip(outcome.session_path)
        self.error_label.setText(outcome.error)
        self.error_label.setVisible(bool(outcome.error))
        self.viewer.yaw, self.viewer.pitch, self.viewer.zoom = 0.3, 0.08, 1.0
        self.viewer.update()
