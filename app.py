"""Запуск демонстрационной версии Botanic RS."""
import argparse
import logging
import signal
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from core.settings import load_settings
from ui.main_window import MainWindow
from ui.theme import apply_theme


def main():
    parser = argparse.ArgumentParser(description="Botanic RS · демонстрация фенотипирования гороха")
    parser.add_argument("--windowed", action="store_true", help="Открыть приложение в обычном окне")
    parser.add_argument("--report-only", action="store_true", help="Показать только отчёт, без подключения оборудования")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        settings = load_settings()
    except (OSError, ValueError, TypeError) as exc:
        parser.error(f"Не удалось загрузить config.json: {exc}")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Botanic RS Demo")
    apply_theme(app)
    window = MainWindow(settings, report_only=args.report_only)
    window.show() if args.windowed else window.showFullScreen()
    # Qt регулярно возвращает управление Python для обработки Ctrl+C / SIGTERM.
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(200)
    signal.signal(signal.SIGINT, lambda *_: window.close())
    signal.signal(signal.SIGTERM, lambda *_: window.close())
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
