"""
IgorVision – Design System
==========================

QSS-based design system with dark and light variants.

The slider style is deliberately fully defined:
- only a thin 6 px track is drawn;
- the unfilled track is dark in Dark Theme and light in Light Theme;
- no large light or dark background box is painted behind the slider.
"""

from __future__ import annotations


DARK = {
    "bg": "#1e2023",
    "bg_raised": "#26282c",
    "bg_input": "#2c2f34",
    "slider_track": "#2c2f34",
    "bg_hover": "#34383e",
    "bg_active": "#3d424a",
    "log_bg": "#17181b",
    "border": "#3a3e45",
    "border_strong": "#4a4f58",
    "text": "#e6e8eb",
    "text_muted": "#9aa0a8",
    "accent": "#4f8cff",
    "accent_hover": "#6ba0ff",
    "accent_text": "#ffffff",
    "selection": "rgba(79, 140, 255, 0.30)",
    "success": "#6eb26a",
    "warning": "#f5a623",
    "danger": "#ef5350",
    "alt_row": "#2a2d32",
}

LIGHT = {
    "bg": "#f4f5f7",
    "bg_raised": "#ffffff",
    "bg_input": "#ffffff",
    "slider_track": "#dcdfe5",
    "bg_hover": "#e8eaee",
    "bg_active": "#dcdfe5",
    "log_bg": "#f7f8fa",
    "border": "#d5d9e0",
    "border_strong": "#b8bec8",
    "text": "#24272c",
    "text_muted": "#6b7280",
    "accent": "#3574d4",
    "accent_hover": "#2f66bd",
    "accent_text": "#ffffff",
    "selection": "rgba(53, 116, 212, 0.22)",
    "success": "#3d8b40",
    "warning": "#b07310",
    "danger": "#c53030",
    "alt_row": "#f0f1f4",
}


_QSS_TEMPLATE = r"""
/* ── Base ─────────────────────────────────────────────────────────────── */

* {
    outline: none;
}

/*
Kein globales QWidget-background setzen.

Ein globales:
    QWidget { background: ...; }

würde auf QSlider und Kind-Widgets durchschlagen und die unerwünschte
rechteckige graue Fläche hinter dem Slider erzeugen.
*/
QWidget {
    color: %(text)s;
    font-size: 13px;
}

QMainWindow,
QDialog {
    background: %(bg)s;
}

/*
QScrollArea-Viewport (und die darinliegende Page) sind plain QWidgets,
die Qt mit der hellen Windows-Palette malt – ein heller Kasten im
Dark Theme. Viewport + Page transparent setzen, damit der
Themen-Hintergrund des Eltern-Widgets (Tab-Pane) durchscheint.

Nur auf QScrollArea beschränkt – keine globale QWidget-Regel
(sonst wieder die graue Box hinter Slidern, siehe oben).
*/
QScrollArea,
QScrollArea > QWidget,
QScrollArea > QWidget > QWidget {
    background: transparent;
}

QToolTip {
    background: %(bg_raised)s;
    color: %(text)s;
    border: 1px solid %(border_strong)s;
    padding: 4px 8px;
}

QLabel {
    background: transparent;
}

QLabel:disabled {
    color: %(text_muted)s;
}

/* ── Menus ────────────────────────────────────────────────────────────── */

QMenuBar {
    background: %(bg)s;
    border-bottom: 1px solid %(border)s;
}

QMenuBar::item {
    background: transparent;
    padding: 5px 10px;
}

QMenuBar::item:selected {
    background: %(bg_hover)s;
    border-radius: 4px;
}

QMenu {
    background: %(bg_raised)s;
    border: 1px solid %(border)s;
    padding: 4px;
}

QMenu::item {
    padding: 5px 26px 5px 18px;
    border-radius: 4px;
}

QMenu::item:selected {
    background: %(selection)s;
}

QMenu::item:disabled {
    color: %(text_muted)s;
}

QMenu::separator {
    height: 1px;
    background: %(border)s;
    margin: 4px 8px;
}

/* ── Tabs ─────────────────────────────────────────────────────────────── */

QTabWidget::pane {
    background: %(bg)s;
    border: 1px solid %(border)s;
    border-radius: 6px;
    top: -1px;
}

QTabBar {
    background: transparent;
}

QTabBar::tab {
    background: transparent;
    color: %(text_muted)s;
    padding: 8px 20px;
    margin: 0 1px 0 0;
    border-bottom: 2px solid transparent;
}

QTabBar::tab:hover {
    color: %(text)s;
}

QTabBar::tab:selected {
    color: %(text)s;
    font-weight: 600;
    border-bottom: 2px solid %(accent)s;
}

/* ── Buttons ──────────────────────────────────────────────────────────── */

QPushButton {
    background: %(bg_hover)s;
    color: %(text)s;
    border: 1px solid %(border)s;
    border-radius: 6px;
    padding: 6px 14px;
}

QPushButton:hover {
    background: %(bg_active)s;
    border-color: %(border_strong)s;
}

QPushButton:pressed {
    background: %(border_strong)s;
}

QPushButton:disabled {
    background: %(bg)s;
    color: %(text_muted)s;
    border-color: %(border)s;
}

#btnPrimary {
    background: %(accent)s;
    border: 1px solid %(accent)s;
    color: %(accent_text)s;
    font-weight: 600;
}

#btnPrimary:hover {
    background: %(accent_hover)s;
    border-color: %(accent_hover)s;
}

#btnPrimary:pressed {
    background: %(accent)s;
}

#btnPrimary:disabled {
    background: %(bg_hover)s;
    border-color: %(border)s;
    color: %(text_muted)s;
}

#btnDanger {
    background: transparent;
    border: 1px solid %(danger)s;
    color: %(danger)s;
}

#btnDanger:hover {
    background: rgba(239, 83, 80, 0.12);
}

#btnDanger:pressed {
    background: rgba(239, 83, 80, 0.22);
}

#btnDanger:disabled {
    background: transparent;
    border-color: %(border)s;
    color: %(text_muted)s;
}

/* ── Group boxes ──────────────────────────────────────────────────────── */

QGroupBox {
    background: %(bg_raised)s;
    border: 1px solid %(border)s;
    border-radius: 6px;
    margin-top: 14px;
    padding-top: 6px;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 5px;
    color: %(text_muted)s;
    background: %(bg_raised)s;
}

/* ── Inputs ───────────────────────────────────────────────────────────── */

QLineEdit,
QComboBox,
QSpinBox,
QDoubleSpinBox {
    background: %(bg_input)s;
    color: %(text)s;
    border: 1px solid %(border)s;
    border-radius: 5px;
    padding: 5px 8px;
    selection-background-color: %(accent)s;
    selection-color: %(accent_text)s;
}

QLineEdit:focus,
QComboBox:focus,
QSpinBox:focus,
QDoubleSpinBox:focus {
    border-color: %(accent)s;
}

QLineEdit:disabled,
QComboBox:disabled,
QSpinBox:disabled,
QDoubleSpinBox:disabled {
    background: %(bg)s;
    color: %(text_muted)s;
}

QComboBox::drop-down {
    border: none;
    width: 22px;
}

QComboBox::down-arrow {
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid %(text_muted)s;
    margin-right: 8px;
}

QComboBox QAbstractItemView {
    background: %(bg_raised)s;
    color: %(text)s;
    border: 1px solid %(border_strong)s;
    selection-background-color: %(selection)s;
}

/* ── Checkboxes and radio buttons ─────────────────────────────────────── */

QCheckBox,
QRadioButton {
    background: transparent;
    spacing: 7px;
}

QCheckBox:disabled,
QRadioButton:disabled {
    color: %(text_muted)s;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
}

QCheckBox::indicator:unchecked {
    background: %(bg_input)s;
    border: 1px solid %(border_strong)s;
    border-radius: 4px;
}

QCheckBox::indicator:checked {
    background: %(accent)s;
    border: 1px solid %(accent)s;
    border-radius: 4px;
}

QCheckBox::indicator:disabled {
    background: %(bg)s;
    border-color: %(border)s;
}

QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border-radius: 8px;
}

QRadioButton::indicator:unchecked {
    background: %(bg_input)s;
    border: 1px solid %(border_strong)s;
}

QRadioButton::indicator:checked {
    background: %(bg_input)s;
    border: 5px solid %(accent)s;
}

QRadioButton::indicator:disabled {
    background: %(bg)s;
    border-color: %(border)s;
}

/* ── Sliders ──────────────────────────────────────────────────────────── */

/*
sub-page: Bereich links vom Griff, also der aktuelle Wert.
add-page: Bereich rechts vom Griff, also der Restbereich.

Wichtig (per Pixel-Probe + Render nachgewiesen):
* `QSlider { background: transparent; }` IST noetig – OHNE eine
  Widget-background-Regel malt Qt die gesamte Widget-Flaeche mit der
  nativen Windows-Base-Farbe (helles Grau #efefef) – das ist die hohe,
  hellgraue Box hinter dem Slider. `transparent` macht die Flaeche
  wirklich durchsichtig, sodass der Toolbar-Hintergrund durchscheint.
* Die Schiene wird NUR ueber die Subcontrols (groove / sub-page /
  add-page / handle) mit einer dnnen 6px-Hohe gestylt.
*/

QSlider {
    background: transparent;
}

QSlider::groove:horizontal {
    border: none;
    height: 6px;
    background: %(slider_track)s;
    border-radius: 3px;
}

QSlider::sub-page:horizontal {
    background: %(accent)s;
    border-radius: 3px;
}

QSlider::add-page:horizontal {
    background: %(slider_track)s;
    border-radius: 3px;
}

QSlider::handle:horizontal {
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
    background: %(text)s;
    border: 2px solid %(bg)s;
}

/* ── Progress bars ────────────────────────────────────────────────────── */

QProgressBar {
    background: %(bg_input)s;
    border: 1px solid %(border)s;
    border-radius: 6px;
    text-align: center;
    color: %(text)s;
    font-size: 11px;
    min-height: 18px;
}

QProgressBar::chunk {
    background: %(accent)s;
    border-radius: 5px;
}

/* ── Tables ───────────────────────────────────────────────────────────── */

QTableView,
QTableWidget {
    background: %(bg_input)s;
    alternate-background-color: %(alt_row)s;
    gridline-color: %(border)s;
    border: 1px solid %(border)s;
    border-radius: 6px;
    selection-background-color: %(selection)s;
    selection-color: %(text)s;
}

QTableView::item,
QTableWidget::item {
    padding: 2px 4px;
}

QTableView::item:selected,
QTableWidget::item:selected {
    background: %(selection)s;
    color: %(text)s;
}

QHeaderView {
    background: transparent;
}

QHeaderView::section {
    background: %(bg_raised)s;
    color: %(text_muted)s;
    border: none;
    border-bottom: 1px solid %(border)s;
    border-right: 1px solid %(border)s;
    padding: 6px 8px;
    font-weight: 600;
}

QTableCornerButton::section {
    background: %(bg_raised)s;
    border: none;
}

/* ── Scrollbars ───────────────────────────────────────────────────────── */

QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}

QScrollBar::handle:vertical {
    background: %(border_strong)s;
    border-radius: 4px;
    min-height: 30px;
    margin: 2px;
}

QScrollBar::handle:vertical:hover {
    background: %(text_muted)s;
}

QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0;
}

QScrollBar::handle:horizontal {
    background: %(border_strong)s;
    border-radius: 4px;
    min-width: 30px;
    margin: 2px;
}

QScrollBar::handle:horizontal:hover {
    background: %(text_muted)s;
}

QScrollBar::add-line,
QScrollBar::sub-line {
    width: 0;
    height: 0;
}

QScrollBar::add-page,
QScrollBar::sub-page {
    background: transparent;
}

/* ── Log view ─────────────────────────────────────────────────────────── */

#logView {
    background: %(log_bg)s;
    color: %(text_muted)s;
    border: 1px solid %(border)s;
    border-radius: 6px;
    font-family: Consolas, "Cascadia Mono", "Courier New", monospace;
    font-size: 12px;
}

/* ── Miscellaneous ────────────────────────────────────────────────────── */

QSplitter::handle {
    background: %(border)s;
}

QSplitter::handle:horizontal {
    width: 3px;
}

QSplitter::handle:vertical {
    height: 3px;
}

QSplitter::handle:hover {
    background: %(accent)s;
}

QStatusBar {
    background: %(bg)s;
    border-top: 1px solid %(border)s;
    color: %(text_muted)s;
}

QFrame {
    border: none;
}
"""


def build_qss(theme: str = "dark") -> str:
    """Return the compiled stylesheet for dark or light mode."""
    palette = DARK if theme.lower() == "dark" else LIGHT
    return _QSS_TEMPLATE % palette


def apply_stylesheet(app, theme: str = "dark") -> None:
    """Apply the selected theme to the QApplication."""
    app.setStyleSheet(build_qss(theme))


def current_theme(app) -> str:
    """Infer the active theme from the currently installed stylesheet."""
    qss = app.styleSheet() or ""

    if DARK["bg"] in qss:
        return "dark"

    if LIGHT["bg"] in qss:
        return "light"

    return "dark"