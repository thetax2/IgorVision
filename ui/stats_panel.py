"""
IgorVision – Batch statistics panel
=====================================

Compact summary shown below the results table (Quality tab only):

* **score histogram** – 60-bin score distribution with a precise
  0.00–1.00 axis (ticks every 0.10, labels every 0.25) and threshold
  tick.  A **selection range** is built directly into the chart as two
  thick handles (left / right) with a shaded area between them:

  - drag the **right handle** → move the high edge (expand / shrink)
  - drag the **left handle**  → move the low edge (expand / shrink)
  - drag the **middle**       → slide the whole range left / right
  - **double-click**          → reset the range to 0.00–1.00 (clears the
    table selection)

  While a handle is dragged the matching table rows are selected live.
* **status counts** – sharp / blurry / exposure / clipping / few
  features / duplicates / errors

The panel is a plain widget (no analysis logic) and updates from
``set_results``.
"""
from __future__ import annotations

from PyQt5.QtCore import QEvent, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from models import ImageQualityMetrics

_N_BINS = 60

_C_RED = QColor(214, 94, 94)
_C_YELLOW = QColor(232, 196, 84)
_C_GREEN = QColor(110, 178, 106)
_C_TEXT = QColor(90, 90, 90)
_C_AXIS = QColor(180, 180, 180)
_C_TICK = QColor(120, 120, 120)
_C_THRESHOLD = QColor(240, 180, 60)
_C_SEL_FILL = QColor(120, 170, 255, 48)
_C_SEL_LINE = QColor(120, 170, 255)
_C_HANDLE = QColor(110, 160, 250)
_C_HANDLE_EDGE = QColor(210, 225, 255)
_C_HANDLE_DOT = QColor(25, 35, 60)


class _Histogram(QWidget):
    """Score histogram (0.00–1.00) with a built-in handle selection range."""

    range_changed = pyqtSignal(float, float)   # lo, hi (emitted while dragging)
    selection_cleared = pyqtSignal()           # range reset to full

    _M_TOP = 10
    _M_BOTTOM = 22    # room for axis labels
    _M_LEFT = 4
    _M_RIGHT = 4
    _HANDLE_HIT = 8   # px hit-zone around a handle edge
    _MIN_RANGE = 0.01 # minimum selectable range width
    _FULL = (0.0, 1.0)

    def __init__(self, threshold: float):
        super().__init__()
        self._threshold = threshold
        self._counts: list[float] = [0.0] * _N_BINS
        self._sel: tuple[float, float] = (0.0, 1.0)
        self._sel_count: int | None = None
        self._drag: str | None = None   # 'lo' | 'hi' | 'move'
        self._drag_offset: float = 0.0
        self._enabled = True
        self.setMinimumHeight(96)
        self.setSizePolicy(1, 0)  # stretch horizontally

    # ------------------------------------------------------------------
    # geometry helpers (plot area = score 0.0 … 1.0)
    # ------------------------------------------------------------------

    def _plot(self) -> tuple[int, int, int, int]:
        return (self._M_LEFT, self.width() - self._M_RIGHT,
                self._M_TOP, self.height() - self._M_BOTTOM)

    def _score_at(self, x: float) -> float:
        x0, x1, _, _ = self._plot()
        if x1 <= x0:
            return 0.0
        return min(1.0, max(0.0, (x - x0) / (x1 - x0)))

    def _x_at(self, score: float) -> float:
        x0, x1, _, _ = self._plot()
        return x0 + score * (x1 - x0)

    def _hit_test(self, x: float) -> str:
        lo, hi = self._sel
        lx = self._x_at(lo)
        rx = self._x_at(hi)
        if abs(x - lx) <= self._HANDLE_HIT:
            return "left"
        if abs(x - rx) <= self._HANDLE_HIT:
            return "right"
        if lx < x < rx:
            return "center"
        return "outside"

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def set_scores(self, scores: list[float]) -> None:
        if scores:
            import numpy as np
            counts, _ = np.histogram(scores, bins=_N_BINS, range=(0.0, 1.0))
            self._counts = [float(c) for c in counts]
        else:
            self._counts = [0.0] * _N_BINS
        self.update()

    def set_threshold(self, threshold: float) -> None:
        """Move the threshold tick (live config change)."""
        self._threshold = threshold
        self.update()

    def set_selection_count(self, n: int) -> None:
        """Number of images inside the selected range (drawn as hint)."""
        self._sel_count = n
        self.update()

    def reset_range(self) -> None:
        """Reset the selection range to 0.00–1.00 (clears the selection)."""
        self._sel = (0.0, 1.0)
        self._sel_count = None
        self.update()
        self.selection_cleared.emit()

    def has_selection(self) -> bool:
        lo, hi = self._sel
        return lo > 0.0 or hi < 1.0

    def current_range(self) -> tuple[float, float]:
        return self._sel

    def set_enabled(self, on: bool) -> None:
        """Enable / disable the handle selection interaction."""
        self._enabled = on
        if not on:
            self._drag = None
        self.update()

    # ------------------------------------------------------------------
    # mouse: drag handles / middle to select a range
    # ------------------------------------------------------------------

    def mousePressEvent(self, ev) -> None:  # noqa: N802
        if not self._enabled:
            return
        if ev.type() == QEvent.MouseButtonDblClick:
            self.reset_range()
            return
        if ev.button() != Qt.LeftButton:
            return
        x0, x1, _, _ = self._plot()
        x = min(max(ev.x(), x0), x1)
        hit = self._hit_test(x)
        if hit == "left":
            self._drag = "lo"
        elif hit == "right":
            self._drag = "hi"
        elif hit == "center":
            self._drag = "move"
            self._drag_offset = self._score_at(x) - self._sel[0]

    def mouseMoveEvent(self, ev) -> None:  # noqa: N802
        if not self._enabled:
            self.setCursor(Qt.ArrowCursor)
            return
        x0, x1, _, _ = self._plot()
        x = min(max(ev.x(), x0), x1)
        hit = self._hit_test(x)
        if hit in ("left", "right"):
            self.setCursor(Qt.SizeHorCursor)
        elif hit == "center":
            self.setCursor(Qt.ClosedHandCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

        if self._drag is None:
            return
        s = self._score_at(x)
        lo, hi = self._sel
        if self._drag == "lo":
            new = (round(min(max(s, 0.0), hi - self._MIN_RANGE), 3), hi)
        elif self._drag == "hi":
            new = (lo, round(max(min(s, 1.0), lo + self._MIN_RANGE), 3))
        else:  # move the whole range
            width = hi - lo
            new_lo = min(max(s - self._drag_offset, 0.0), 1.0 - width)
            new = (round(new_lo, 3), round(new_lo + width, 3))
        if new != self._sel:
            self._sel = new
            self.update()
            self.range_changed.emit(new[0], new[1])

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if ev.button() != Qt.LeftButton:
            return
        self._drag = None

    # ------------------------------------------------------------------
    # paint
    # ------------------------------------------------------------------

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        x0, x1, y0, y1 = self._plot()
        plot_w, plot_h = x1 - x0, y1 - y0
        max_c = max(self._counts) if any(self._counts) else 1.0
        bin_w = plot_w / _N_BINS

        lo, hi = self._sel
        lx = int(self._x_at(lo))
        rx = int(self._x_at(hi))

        # selection fill (behind the bars)
        p.fillRect(lx, y0, max(1, rx - lx), plot_h, _C_SEL_FILL)

        for i, c in enumerate(self._counts):
            if c <= 0:
                continue
            bx = x0 + i * bin_w
            bh = max(2, int(plot_h * c / max_c))
            t = (i + 0.5) / _N_BINS
            if t < self._threshold - 0.15:
                col = _C_RED
            elif t > self._threshold + 0.15:
                col = _C_GREEN
            else:
                col = _C_YELLOW
            p.fillRect(int(bx) + 1, y1 - bh, max(1, int(bin_w) - 2), bh, col)

        # selection handles
        self._draw_handle(p, lx, y0, y1)
        self._draw_handle(p, rx, y0, y1)

        # baseline
        p.setPen(_C_AXIS)
        p.drawLine(x0, y1, x1, y1)

        # ticks every 0.10
        p.setPen(_C_TICK)
        for k in range(11):
            tx = int(self._x_at(k / 10))
            p.drawLine(tx, y1, tx, y1 - 4)

        # labels every 0.25
        p.setPen(_C_TEXT)
        for v in (0.0, 0.25, 0.5, 0.75, 1.0):
            tx = int(self._x_at(v))
            p.drawText(tx - 16, y1 + 2, 32, 16, Qt.AlignCenter, f"{v:.2f}")

        # threshold tick (dashed, full height)
        tx = int(self._x_at(self._threshold))
        p.setPen(QPen(_C_THRESHOLD, 1, Qt.DashLine))
        p.drawLine(tx, y0, tx, y1)

        # range hint (top right)
        hint = f"{lo:.2f} – {hi:.2f}"
        if self._sel_count is not None:
            hint += f"  ·  {self._sel_count} img"
        p.setPen(_C_SEL_LINE)
        p.drawText(x1, y0 - 2, 220, 14, Qt.AlignRight, hint)

        p.end()

    def _draw_handle(self, p: QPainter, cx: int, y0: int, y1: int) -> None:
        w = 5
        x = cx - w // 2
        fill = _C_HANDLE if self._enabled else QColor(90, 95, 105)
        edge = _C_HANDLE_EDGE if self._enabled else QColor(125, 130, 140)
        p.fillRect(x, y0, w, y1 - y0, fill)
        p.setPen(QPen(edge, 1))
        p.drawRect(x, y0, w, y1 - y0)
        if self._enabled:
            p.setPen(QPen(_C_HANDLE_DOT, 1))
            mid_y = (y0 + y1) // 2
            for dy in (-6, 0, 6):
                p.drawPoint(cx, mid_y + dy)


class StatsPanel(QWidget):
    """Batch summary: score histogram (handle selection) + status counts."""

    range_changed = pyqtSignal(float, float)    # score range [lo, hi]
    selection_cleared = pyqtSignal()

    def __init__(self, threshold: float = 0.5, parent: QWidget | None = None):
        super().__init__(parent)
        self._sel_count: int | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        # diagram on top …
        self._hist = _Histogram(threshold)
        self._hist.range_changed.connect(self._on_hist_range_changed)
        self._hist.selection_cleared.connect(self._on_hist_cleared)
        layout.addWidget(self._hist)

        # … key metrics (left) + current range (right) on one line
        mid_row = QHBoxLayout()
        self._counts_label = QLabel("No results yet")
        self._counts_label.setStyleSheet("padding: 2px;")
        mid_row.addWidget(self._counts_label)
        mid_row.addStretch(1)
        self._range_label = QLabel("Range: 0.00 – 1.00")
        self._range_label.setStyleSheet("padding: 2px;")
        mid_row.addWidget(self._range_label)
        layout.addLayout(mid_row)

        # NOTE: the enable/disable toggle button lives in the main window's
        # filter row (next to the threshold slider) – it drives the panel
        # through set_selection_enabled() / is_selection_enabled().

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def set_threshold(self, threshold: float) -> None:
        """Update the histogram threshold tick (live config change)."""
        self._hist.set_threshold(threshold)

    def set_selection_count(self, n: int) -> None:
        self._sel_count = n
        self._hist.set_selection_count(n)
        self._update_range_label()

    def reset_range(self) -> None:
        self._sel_count = None
        self._hist.reset_range()

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _on_hist_range_changed(self, lo: float, hi: float) -> None:
        self.range_changed.emit(lo, hi)
        self._update_range_label()

    def _on_hist_cleared(self) -> None:
        self._sel_count = None
        self.selection_cleared.emit()
        self._update_range_label()

    def _update_range_label(self) -> None:
        lo, hi = self._hist.current_range()
        if self._sel_count is not None:
            self._range_label.setText(
                f"Range: {lo:.2f} – {hi:.2f}  ·  {self._sel_count} img"
            )
        else:
            self._range_label.setText(f"Range: {lo:.2f} – {hi:.2f}")

    def set_selection_enabled(self, on: bool) -> None:
        """Enable / disable the range-selection handles (external toggle)."""
        self._hist.set_enabled(on)
        if not on:
            self._hist.reset_range()

    def is_selection_enabled(self) -> bool:
        return self._hist._enabled

    def has_selection(self) -> bool:
        return self._hist.has_selection()

    def current_range(self) -> tuple[float, float]:
        return self._hist.current_range()

    def set_results(self, results: list[ImageQualityMetrics]) -> None:
        ok = [r for r in results if not r.is_error]
        errors = len(results) - len(ok)

        clean = (lambda r: not r.is_blurry and r.exposure_ok and r.clipping_ok
                 and not r.low_features and not r.duplicate_of
                 and not r.exposure_outlier and not r.wb_outlier
                 and not r.low_dynamic_range and not r.high_iso
                 and not r.aperture_outlier and not r.soft_motion_blur)
        sharp = sum(1 for r in ok if clean(r))
        blurry = sum(1 for r in ok if r.is_blurry)
        exposure = sum(1 for r in ok if not r.exposure_ok)
        clipping = sum(1 for r in ok if not r.clipping_ok)
        few = sum(1 for r in ok if r.low_features)
        dups = sum(1 for r in ok if r.duplicate_of)
        expo_delta = sum(1 for r in ok if r.exposure_outlier)
        wb_delta = sum(1 for r in ok if r.wb_outlier)
        hi_iso = sum(1 for r in ok if r.high_iso)
        soft = sum(1 for r in ok if r.soft_motion_blur)

        chips = [
            f"Σ {len(ok)}",
            f"🟢 {sharp}",
            f"🔴 {blurry}",
            f"⚠️ exp {exposure}",
            f"clip {clipping}",
            f"few {few}",
            f"dup {dups}",
        ]
        if expo_delta:
            chips.append(f"expoΔ {expo_delta}")
        if wb_delta:
            chips.append(f"wbΔ {wb_delta}")
        if hi_iso:
            chips.append(f"isoΔ {hi_iso}")
        if soft:
            chips.append(f"🟡 soft {soft}")
        if errors:
            chips.append(f"⚫ err {errors}")
        self._counts_label.setText("   ".join(chips))

        self._hist.set_scores([r.composite_score for r in ok])
