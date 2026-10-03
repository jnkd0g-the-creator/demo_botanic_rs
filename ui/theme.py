from PyQt6.QtGui import QColor, QPalette

# Палитра и геометрия панелей соответствуют botanic_rs/styles/dark_theme.py.
DARK_STYLESHEET = """
QMainWindow, QDialog, QWidget#page { background: #0D1117; color: #E6EDF5; }
QWidget { color: #E6EDF5; font-family: 'DejaVu Sans'; font-size: 14px; }
QLabel { background: transparent; border: none; }
QFrame#card, QFrame#topBar, QFrame#bottomBar {
    background: #151B23; border: 1px solid #27313E; border-radius: 14px;
}
QLabel#title { font-size: 22px; font-weight: bold; }
QLabel#subtitle { color: #8A99AB; font-size: 12px; }
QLabel#cardTitle { color: #8A99AB; font-size: 11px; font-weight: bold; }
QLabel#chip, QLabel#chipAccent, QLabel#demoBadge {
    background: #1B2430; border: 1px solid #27313E; border-radius: 9px;
    padding: 7px 11px; font-size: 12px; font-weight: bold;
}
QLabel#chipAccent { background: #172B45; border-color: #315789; color: #A8CBFF; }
QLabel#demoBadge { background: #332A19; border-color: #68512B; color: #FFCB75; }
QLabel#metric { font-size: 29px; font-weight: bold; color: #E6EDF5; }
QLabel#statusLabel { font-size: 15px; font-weight: bold; }
QLabel#error { color: #FF8A8D; font-size: 12px; }
QPushButton {
    background: #1B2430; color: #E6EDF5; border: 1px solid #2C3A4B;
    border-radius: 12px; padding: 12px 24px; font-size: 15px; font-weight: bold;
}
QPushButton:hover { background: #243040; border-color: #3B4C61; }
QPushButton:pressed { background: #131A23; }
QPushButton#startButton { background: #3D8BFD; border-color: #5199FF; color: white; }
QPushButton#startButton:hover { background: #5199FF; }
QPushButton#stopButton { background: #E5484D; border-color: #FF6B6E; color: white; }
QPushButton#stopButton:hover { background: #FF5A5F; }
QPushButton:disabled, QPushButton#startButton:disabled, QPushButton#stopButton:disabled {
    background: #1A222C; color: #5C6979; border-color: #232C38;
}
QProgressBar { background: #1B2430; border: none; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: #3D8BFD; border-radius: 3px; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #151B23; width: 6px; margin: 0; }
QScrollBar::handle:vertical { background: #33445A; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { background: #1B2430; color: #E6EDF5; border: 1px solid #27313E; }
"""


def apply_theme(app):
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in ((QPalette.ColorRole.Window, "#0D1117"),
                        (QPalette.ColorRole.WindowText, "#E6EDF5"),
                        (QPalette.ColorRole.Base, "#10161E"),
                        (QPalette.ColorRole.Text, "#E6EDF5"),
                        (QPalette.ColorRole.Button, "#1B2430"),
                        (QPalette.ColorRole.ButtonText, "#E6EDF5"),
                        (QPalette.ColorRole.Highlight, "#3D8BFD")):
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet(DARK_STYLESHEET)
