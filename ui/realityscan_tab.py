"""
ui/realityscan_tab.py
=====================
RealityScan **command centre** – a node-based pipeline editor.

Layout
------
* **Top toolbar** – RealityScan exe (browse), run mode (GUI / headless),
  quit, presets, import ``.bat``, save / open pipeline (JSON), export ``.bat``,
  **Run** / **Abort**.
* **Canvas** (left) – a node graph (Blender / ComfyUI style).  Each node is
  one CLI command with editable parameters and in/out ports.  Execution
  order is **left → right** (node x-position).  Drag a node's *output* port
  onto another node's *input* port to link them; double-click a link to
  remove it.
* **Side panel** (right) – a searchable command palette (grouped by
  category) and a free **variables** table (``%NAME%``).
* **Bottom** – live log + progress.

The pipeline is stored as structured JSON (see
:mod:`tools.realityscan_engine`) and executed directly via
:class:`tools.realityscan_worker.RealityScanRunner` – a ``.bat`` is only
ever an export artifact.
"""
from __future__ import annotations

import os

from PyQt5.QtCore import Qt, QPointF, QRectF, QSize, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen, QBrush, QFont, QTransform, QPainterPath
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsPathItem,
    QGraphicsProxyWidget,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from tools.realityscan_engine import (
    COMMANDS,
    RSPipeline,
    RSNode,
    PRESETS,
    build_batch,
    categories,
    make_preset,
    from_batch,
    from_json,
    to_json,
    spec_for,
)
from tools.realityscan_worker import RealityScanRunner
from ui.log_panel import LogPanel
from ui.path_row import PathRow


# ── category → accent colour ─────────────────────────────────────────
CATEGORY_COLORS: dict[str, str] = {
    "Project": "#4a6fa5",
    "Images": "#4f8f52",
    "Alignment": "#b5893a",
    "Reconstruction": "#a55a4a",
    "Model": "#7a5aa5",
    "Classification": "#3f9e99",
    "Settings": "#8a8a8a",
}
_NODE_W = 232
_PORT_R = 7.0


def _cat_color(command: str) -> str:
    spec = spec_for(command)
    return CATEGORY_COLORS.get(spec.category if spec else "", "#6a6a6a")


def _is_folder_param(name: str) -> bool:
    """Heuristic: does this path parameter point at a *folder*?"""
    n = name.lower()
    if "folder" in n or "directory" in n or n.endswith("dir"):
        return True
    if n in ("frames",):  # importVideo: folder with extracted frames
        return True
    return False


# ---------------------------------------------------------------------------
# port
# ---------------------------------------------------------------------------

class Port(QGraphicsEllipseItem):
    """A small in/out socket on a node (child of the node item)."""

    def __init__(self, node: "CommandNode", kind: str):
        super().__init__(
            -_PORT_R, -_PORT_R, _PORT_R * 2, _PORT_R * 2, node
        )
        self.node = node
        self.kind = kind  # "in" | "out"
        self.setBrush(QBrush(QColor("#e8e8e8")))
        self.setPen(QPen(QColor("#333333"), 1.5))
        self.setZValue(5)
        self.setAcceptHoverEvents(True)
        self.setToolTip("Output" if kind == "out" else "Input")


# ---------------------------------------------------------------------------
# connection
# ---------------------------------------------------------------------------

class Connection(QGraphicsPathItem):
    """A bezier link from a source output port to a target input port."""

    def __init__(self, src: "CommandNode", dst: "CommandNode", scene):
        super().__init__()
        self.src = src
        self.dst = dst
        self._scene = scene
        self.setPen(QPen(QColor("#7fd1c8"), 2.5))
        self.setZValue(1)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.update_path()

    def _port_scene_pos(self, node, kind) -> tuple[float, float]:
        port = node.port(kind)
        if port is None:
            return (node.pos().x(), node.pos().y())
        p = port.scenePos()
        return (p.x(), p.y())

    def update_path(self) -> None:
        sx, sy = self._port_scene_pos(self.src, "out")
        dx, dy = self._port_scene_pos(self.dst, "in")
        dx_off = max(40.0, abs(dx - sx) * 0.5)
        # Fresh path every time – reusing self.path() would accumulate a
        # new cubic subpath on every move (the "smear/ghosting" fan).
        path = QPainterPath()
        path.moveTo(sx, sy)
        path.cubicTo(sx + dx_off, sy, dx - dx_off, dy, dx, dy)
        self.setPath(path)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._scene.remove_connection(self)


# ---------------------------------------------------------------------------
# command node
# ---------------------------------------------------------------------------

class CommandNode(QGraphicsProxyWidget):
    """One CLI command: title + editable parameters + in/out ports.

    Implemented as a :class:`QGraphicsProxyWidget` wrapping a plain
    ``QWidget`` (PyQt5 in this build does not allow a layout to be
    attached to a ``QGraphicsWidget`` directly).
    """

    def __init__(self, command: str, params: dict, scene: "NodeScene"):
        self.command = command
        self.node_id = ""
        self.enabled = True
        self._scene = scene
        self._edits: dict[str, QLineEdit] = {}

        spec = spec_for(command)
        color = _cat_color(command)

        # ── inner widget ──
        w = QWidget()
        w.setFixedWidth(_NODE_W)
        w.setStyleSheet(
            "QWidget { background: #23262b; border: 1px solid #3a3f47; "
            "border-radius: 6px; }"
        )
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # title bar
        self._title = QLabel(command)
        self._title.setStyleSheet(
            f"background: {color}; color: white; font-weight: 600; "
            f"padding: 6px 8px;"
        )
        if spec and spec.description:
            self._title.setToolTip(spec.description)
        lay.addWidget(self._title)

        # parameters (typed widgets per ParamSpec.kind + path browse)
        if spec and spec.params:
            for ps in spec.params:
                row = QHBoxLayout()
                row.setContentsMargins(8, 2, 8, 2)
                row.setSpacing(6)
                lbl = QLabel(ps.name)
                lbl.setStyleSheet("color: #cfcfcf; font-size: 11px;")
                tip = ps.description
                if ps.kind == "choice" and ps.choices:
                    tip = (tip + "  " if tip else "") + " / ".join(ps.choices)
                if ps.kind == "path":
                    tip = (tip + "  " if tip else "") + (
                        "(folder)" if _is_folder_param(ps.name) else "(file)")
                lbl.setToolTip(tip)
                edit = self._make_param_widget(ps, params)
                row.addWidget(lbl, 1)
                row.addWidget(edit, 2)
                self._edits[ps.name] = edit
                if ps.kind == "path":
                    is_dir = _is_folder_param(ps.name)
                    browse = QPushButton()
                    browse.setFixedSize(30, 26)
                    browse.setToolTip(
                        "Select folder…" if is_dir else "Select file…")
                    icon = self._browse_icon(is_dir)
                    if icon is not None:
                        browse.setIcon(icon)
                        browse.setIconSize(QSize(16, 16))
                    else:
                        browse.setText("…")  # safe fallback glyph
                    browse.clicked.connect(
                        lambda _=False, e=edit, p=ps: self._browse(p, e))
                    row.addWidget(browse)
                lay.addLayout(row)
        else:
            hint = QLabel("(no parameters)")
            hint.setStyleSheet("color: #8a8a8a; font-size: 11px; padding: 6px 8px;")
            lay.addWidget(hint)

        # footer: enabled + category
        foot = QHBoxLayout()
        foot.setContentsMargins(8, 4, 8, 6)
        foot.setSpacing(6)
        self._chk = QCheckBox("enabled")
        self._chk.setChecked(True)
        self._chk.setStyleSheet("color: #bfbfbf; font-size: 11px;")
        foot.addWidget(self._chk)
        foot.addStretch(1)
        cat = QLabel(spec.category if spec else "")
        cat.setStyleSheet("color: #8a8a8a; font-size: 10px;")
        foot.addWidget(cat)
        lay.addLayout(foot)

        self._w = w
        super().__init__()
        self.setWidget(w)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)

        # ports (children of the proxy item)
        self._port_in = Port(self, "in")
        self._port_out = Port(self, "out")
        self._reposition_ports()

    # ── ports ────────────────────────────────────────────────────────
    def _reposition_ports(self) -> None:
        sz = self.size()
        w, h = sz.width(), sz.height()
        if h <= 0 or w <= 0:
            return
        mid = h / 2.0
        self._port_in.setPos(0.0, mid)
        self._port_out.setPos(w, mid)

    def port(self, kind: str):
        return self._port_in if kind == "in" else self._port_out

    # ── typed parameter widgets ──────────────────────────────────────
    @staticmethod
    def _make_param_widget(ps, params: dict):
        """Build the editor widget for one parameter based on its kind.

        ``text`` / ``path`` → :class:`QLineEdit` (path gets a browse
        button in the row), ``choice`` → editable combo, ``bool`` →
        checkbox, ``number`` → spin box.
        """
        value = str(params.get(ps.name, ""))
        if ps.kind == "choice":
            combo = QComboBox()
            combo.setEditable(True)  # allow values outside the list too
            combo.addItems(list(ps.choices))
            if value:
                combo.setEditText(value)
            elif ps.choices:
                combo.setCurrentIndex(0)
            combo.setStyleSheet("font-size: 11px;")
            if ps.description:
                combo.setToolTip(ps.description)
            return combo
        if ps.kind == "bool":
            chk = QCheckBox("true")
            chk.setChecked(value.strip().lower() in ("true", "1", "yes", "on"))
            chk.setStyleSheet("color: #cfcfcf; font-size: 11px;")
            if ps.description:
                chk.setToolTip(ps.description)
            return chk
        if ps.kind == "number":
            spin = QDoubleSpinBox()
            spin.setRange(-999999.0, 999999.0)
            spin.setSingleStep(1.0)
            try:
                fv = float(value)
                spin.setDecimals(0 if fv == int(fv) else 3)  # before setValue!
                spin.setValue(fv)
            except ValueError:
                pass
            spin.setStyleSheet("font-size: 11px;")
            if ps.description:
                spin.setToolTip(ps.description)
            return spin
        # text / path → editable line edit (path gets a browse button)
        edit = QLineEdit()
        edit.setStyleSheet("font-size: 11px;")
        placeholder = ps.description or ps.name
        if ps.kind == "choice" and ps.choices:
            placeholder = " / ".join(ps.choices)
        edit.setPlaceholderText(placeholder)
        edit.setText(value)
        return edit

    def _dialog_parent(self):
        """A proper top-level window to parent file dialogs to.

        In this PyQt5 build the node's inner widget is a *top-level*
        ``QWidget`` (``setWidget`` does not reparent it into the view), so
        using it as a dialog parent makes Qt create a stray native helper
        window that shows up as a small empty "Qt" window next to the node
        on Windows.  Walk up to the real top-level window (the main window)
        via the scene's view instead.
        """
        try:
            views = self._scene.views()
            if views:
                top = views[0].window()
                if top is not None and top.isWindow():
                    return top
        except Exception:
            pass
        top = self._w.window()
        if top is not None and top is not self._w and top.isWindow():
            return top
        try:
            from PyQt5.QtWidgets import QApplication
            aw = QApplication.activeWindow()
            if aw is not None:
                return aw
        except Exception:
            pass
        return None

    @staticmethod
    def _browse_icon(is_dir: bool):
        """Native folder / file icon for the browse button.

        Emoji glyphs (📁/📄) are missing from the button's default font in
        this build and render as a thin missing-glyph bar, so we use the
        application style's standard icons instead.  Returns ``None`` when
        no icon is available (the caller then falls back to a ``…`` text
        button).  A ``@staticmethod`` so it can be called during node
        construction before the proxy widget's C++ base is initialised.
        """
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance()
        style = app.style() if app is not None else None
        if style is None:
            return None
        sp = QStyle.SP_DirIcon if is_dir else QStyle.SP_FileIcon
        icon = style.standardIcon(sp)
        return icon if not icon.isNull() else None

    def _browse(self, ps, edit: QLineEdit) -> None:
        """Open a folder / file dialog and write the choice into the field."""
        start = edit.text().strip() or os.getcwd()
        parent = self._dialog_parent()
        if _is_folder_param(ps.name):
            chosen = QFileDialog.getExistingDirectory(
                parent, "Select folder", start)
        else:
            chosen, _ = QFileDialog.getOpenFileName(
                parent, "Select file", start)
        if chosen:
            edit.setText(chosen)

    # ── values ───────────────────────────────────────────────────────
    def params(self) -> dict:
        out: dict[str, str] = {}
        for name, w in self._edits.items():
            out[name] = self._widget_value(w)
        return out

    def set_params(self, params: dict) -> None:
        for name, w in self._edits.items():
            self._set_widget_value(w, str(params.get(name, "")))

    @staticmethod
    def _widget_value(w) -> str:
        if isinstance(w, QCheckBox):
            return "true" if w.isChecked() else "false"
        if isinstance(w, QDoubleSpinBox):
            v = w.value()
            if v == int(v):
                return str(int(v))
            return ("%.3f" % v).rstrip("0").rstrip(".")
        if isinstance(w, QComboBox):
            return w.currentText().strip()
        if isinstance(w, QLineEdit):
            return w.text().strip()
        return str(w)

    @staticmethod
    def _set_widget_value(w, value: str) -> None:
        if isinstance(w, QCheckBox):
            w.setChecked(value.strip().lower() in ("true", "1", "yes", "on"))
        elif isinstance(w, QDoubleSpinBox):
            try:
                fv = float(value)
                w.setDecimals(0 if fv == int(fv) else 3)  # before setValue!
                w.setValue(fv)
            except ValueError:
                pass
        elif isinstance(w, QComboBox):
            w.setEditText(value)
        elif isinstance(w, QLineEdit):
            w.setText(value)

    def itemChange(self, change, value):  # type: ignore[no-untyped-def]
        if change == QGraphicsItem.ItemPositionChange and self._scene is not None:
            self._scene.redraw_connections()
        return super().itemChange(change, value)


# ---------------------------------------------------------------------------
# scene
# ---------------------------------------------------------------------------

class NodeScene(QGraphicsScene):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSceneRect(-5000, -5000, 10000, 10000)
        self.nodes: list[CommandNode] = []
        self.connections: list[Connection] = []
        self._pending_src: CommandNode | None = None
        self._rubber: QGraphicsPathItem | None = None

    # ── nodes ────────────────────────────────────────────────────────
    def add_node(self, node: CommandNode) -> None:
        self.addItem(node)
        self.nodes.append(node)

    def remove_node(self, node: CommandNode) -> None:
        for c in list(self.connections):
            if c.src is node or c.dst is node:
                self.remove_connection(c)
        if node in self.nodes:
            self.nodes.remove(node)
        self.removeItem(node)

    def clear(self) -> None:
        for c in list(self.connections):
            self.remove_connection(c)
        for n in list(self.nodes):
            self.remove_node(n)

    # ── connections ──────────────────────────────────────────────────
    def add_connection(self, src: CommandNode, dst: CommandNode) -> None:
        if src is dst:
            return
        for c in self.connections:
            if c.src is src and c.dst is dst:
                return
        conn = Connection(src, dst, self)
        self.addItem(conn)
        self.connections.append(conn)

    def remove_connection(self, conn: Connection) -> None:
        if conn in self.connections:
            self.connections.remove(conn)
        self.removeItem(conn)

    def redraw_connections(self) -> None:
        for c in self.connections:
            c.update_path()

    # ── pending connection (drag from an output port) ────────────────
    def begin_pending(self, src: CommandNode) -> None:
        self._pending_src = src
        self._rubber = QGraphicsPathItem()
        self._rubber.setPen(QPen(QColor("#7fd1c8"), 2.0, Qt.DashLine))
        self.addItem(self._rubber)

    def update_pending(self, scene_pos) -> None:  # type: ignore[no-untyped-def]
        if self._rubber is None or self._pending_src is None:
            return
        sx, sy = self._pending_src.port("out").scenePos().x(), self._pending_src.port("out").scenePos().y()
        dx, dy = scene_pos.x(), scene_pos.y()
        off = max(40.0, abs(dx - sx) * 0.5)
        # Fresh path – reusing the old one accumulates subpaths (smear).
        p = QPainterPath()
        p.moveTo(sx, sy)
        p.cubicTo(sx + off, sy, dx - off, dy, dx, dy)
        self._rubber.setPath(p)

    def finish_pending(self, scene_pos) -> bool:  # type: ignore[no-untyped-def]
        """Return True if a connection was created."""
        src = self._pending_src
        self._pending_src = None
        if self._rubber is not None:
            self.removeItem(self._rubber)
            self._rubber = None
        if src is None:
            return False
        item = self.itemAt(scene_pos, QTransform())
        while item is not None and not isinstance(item, Port):
            item = item.parentItem() if hasattr(item, "parentItem") else None
            if isinstance(item, CommandNode):
                item = None
                break
        if isinstance(item, Port) and item.kind == "in" and item.node is not src:
            self.add_connection(src, item.node)
            return True
        return False

    # ── ordered (execution) ──────────────────────────────────────────
    def ordered_nodes(self) -> list[CommandNode]:
        return sorted(
            (n for n in self.nodes if n._chk.isChecked()),
            key=lambda n: (round(n.pos().x() / 8.0), n.pos().y(), id(n)),
        )


# ---------------------------------------------------------------------------
# view
# ---------------------------------------------------------------------------

class NodeView(QGraphicsView):
    drop_requested = pyqtSignal(str, object)  # (command, scene QPointF)

    def __init__(self, scene: NodeScene, parent=None):
        super().__init__(scene, parent)
        self._scene = scene
        self._drag_node = None
        self._drag_offset = QPointF(0, 0)
        self._panning = False
        self._pan_last = None
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(QColor("#17191d")))
        self.setAcceptDrops(True)

    def _pan_by(self, dx: int, dy: int) -> None:
        """Pan the canvas by a viewport-pixel delta (content follows mouse)."""
        self.horizontalScrollBar().setValue(
            self.horizontalScrollBar().value() - dx
        )
        self.verticalScrollBar().setValue(
            self.verticalScrollBar().value() - dy
        )

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_last = event.pos()
            self.viewport().setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            sp = self.mapToScene(event.pos())
            item = self._scene.itemAt(sp, QTransform())
            if isinstance(item, Port) and item.kind == "out":
                self._scene.begin_pending(item.node)
                event.accept()
                return
            node = self._node_at(sp)
            if node is not None and not self._on_editable(node, sp):
                self._drag_node = node
                self._drag_offset = sp - node.pos()
                event.accept()
                return
        super().mousePressEvent(event)

    def _node_at(self, scene_pos):
        item = self._scene.itemAt(scene_pos, QTransform())
        while item is not None:
            if isinstance(item, CommandNode):
                return item
            item = item.parentItem()
        return None

    def _on_editable(self, node, scene_pos) -> bool:
        """True if the press landed on an editable field (don't drag)."""
        w = node._w
        local = node.mapFromScene(scene_pos)
        child = w.childAt(local.toPoint())
        return isinstance(child, (QLineEdit, QCheckBox, QComboBox,
                                  QPushButton, QDoubleSpinBox))

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._panning and self._pan_last is not None:
            delta = event.pos() - self._pan_last
            self._pan_last = event.pos()
            self._pan_by(delta.x(), delta.y())
            event.accept()
            return
        if self._drag_node is not None:
            sp = self.mapToScene(event.pos())
            self._drag_node.setPos(sp - self._drag_offset)
            self._scene.redraw_connections()
            event.accept()
            return
        if self._scene._pending_src is not None:
            self._scene.update_pending(self.mapToScene(event.pos()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        """Double-click a link to remove it (hit-test ourselves – this
        PyQt5 build does not reliably deliver the event to the item)."""
        if event.button() == Qt.LeftButton:
            item = self._scene.itemAt(self.mapToScene(event.pos()), QTransform())
            if isinstance(item, Connection):
                self._scene.remove_connection(item)
                event.accept()
                return
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._panning and event.button() == Qt.MiddleButton:
            self._panning = False
            self._pan_last = None
            self.viewport().unsetCursor()
            event.accept()
            return
        if self._drag_node is not None:
            self._drag_node = None
            event.accept()
            return
        if self._scene._pending_src is not None:
            self._scene.finish_pending(self.mapToScene(event.pos()))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ── drag & drop from the command palette ─────────────────────────
    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        text = event.mimeData().text().strip()
        if text:
            self.drop_requested.emit(text, self.mapToScene(event.pos()))
            event.acceptProposedAction()

    def fit(self, min_scale: float = 0.0) -> None:
        """Fit all nodes into the view (skipped while the viewport is
        still degenerate, e.g. during window construction).  If the
        resulting zoom would be smaller than *min_scale*, fall back to
        100 % centred on the left-most node instead."""
        if self.viewport().width() < 100 or self.viewport().height() < 100:
            return
        items = self._scene.nodes
        if not items:
            return
        rect = self._scene.itemsBoundingRect()
        if rect.isNull():
            return
        self.fitInView(rect.adjusted(-60, -60, 60, 60), Qt.KeepAspectRatio)
        if min_scale > 0 and self.transform().m11() < min_scale:
            self.reset_to_origin()

    def reset_to_origin(self) -> None:
        """100 % zoom, centred on the left-most node."""
        self.resetTransform()
        nodes = self._scene.nodes
        if not nodes:
            return
        first = min(nodes, key=lambda n: (n.pos().x(), n.pos().y()))
        sz = first.size()
        self.centerOn(first.pos().x() + sz.width() / 2.0,
                      first.pos().y() + sz.height() / 2.0)


# ---------------------------------------------------------------------------
# command palette (drag source)
# ---------------------------------------------------------------------------

class CommandPalette(QListWidget):
    """QListWidget that drags the command name as ``text/plain``
    (the default QListWidget drag only sets
    ``application/x-qabstractitemmodeldatalist``)."""

    def mimeData(self, items):  # type: ignore[no-untyped-def]
        md = super().mimeData(items)
        if items:
            cmd = items[0].data(Qt.UserRole)
            if cmd:
                md.setText(cmd)
        return md


# ---------------------------------------------------------------------------
# the tab
# ---------------------------------------------------------------------------

class RealityScanTab(QWidget):
    def __init__(self, title: str = "RealityScan", parent=None):
        super().__init__(parent)
        self._pipeline = RSPipeline()
        self._runner = RealityScanRunner(self)
        self._build()
        self._wire_runner()
        self._load_preset("HighDetail RAW + Distances")

    # ── UI ───────────────────────────────────────────────────────────
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 8)
        root.setSpacing(8)

        # ── top toolbar ──
        top = QHBoxLayout()
        top.setSpacing(8)

        self._pr_exe = PathRow("RealityScan.exe:", kind="file")
        self._pr_exe.set_value(
            r"C:\Program Files\Epic Games\RealityScan_2.1\RealityCapture.exe"
        )
        top.addWidget(self._pr_exe, 3)

        self._cmb_mode = QComboBox()
        self._cmb_mode.addItems(["GUI (open)", "Headless"])
        self._cmb_mode.setFixedWidth(120)
        top.addWidget(self._cmb_mode)

        self._chk_quit = QCheckBox("-quit")
        top.addWidget(self._chk_quit)

        self._btn_run = QPushButton("▶  Run")
        self._btn_run.setObjectName("btnPrimary")
        self._btn_run.setMinimumHeight(34)
        self._btn_run.clicked.connect(self._run)
        top.addWidget(self._btn_run)

        self._btn_abort = QPushButton("⏹  Abort")
        self._btn_abort.setObjectName("btnDanger")
        self._btn_abort.setMinimumHeight(34)
        self._btn_abort.setEnabled(False)
        self._btn_abort.clicked.connect(self._abort)
        top.addWidget(self._btn_abort)

        top.addStretch(1)
        root.addLayout(top)

        # ── second toolbar: pipeline management ──
        tb2 = QHBoxLayout()
        tb2.setSpacing(6)

        self._cmb_preset = QComboBox()
        self._cmb_preset.addItems(list(PRESETS.keys()))
        self._cmb_preset.setFixedWidth(220)
        self._btn_preset = QPushButton("Load preset")
        self._btn_preset.clicked.connect(self._load_preset_current)
        tb2.addWidget(self._cmb_preset)
        tb2.addWidget(self._btn_preset)

        self._btn_import = QPushButton("Import .bat…")
        self._btn_import.clicked.connect(self._import_bat)
        tb2.addWidget(self._btn_import)

        self._btn_save = QPushButton("Save")
        self._btn_save.clicked.connect(self._save)
        tb2.addWidget(self._btn_save)

        self._btn_open = QPushButton("Open…")
        self._btn_open.clicked.connect(self._open)
        tb2.addWidget(self._btn_open)

        self._btn_export = QPushButton("Export .bat")
        self._btn_export.clicked.connect(self._export_bat)
        tb2.addWidget(self._btn_export)

        tb2.addStretch(1)
        root.addLayout(tb2)

        # ── main splitter: canvas | side panel ──
        self._split = QSplitter(Qt.Horizontal)

        # left: canvas + mini toolbar
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)

        self._scene = NodeScene()
        self._view = NodeView(self._scene)

        mini = QHBoxLayout()
        mini.setSpacing(6)
        self._btn_add = QPushButton("+ Add command…")
        self._btn_add.clicked.connect(self._add_menu)
        mini.addWidget(self._btn_add)
        self._btn_del = QPushButton("Delete selected")
        self._btn_del.clicked.connect(self._delete_selected)
        mini.addWidget(self._btn_del)
        self._btn_fit = QPushButton("Fit view")
        self._btn_fit.clicked.connect(self._view.fit)
        mini.addWidget(self._btn_fit)
        self._btn_clear = QPushButton("Clear")
        self._btn_clear.clicked.connect(self._clear_canvas)
        mini.addWidget(self._btn_clear)
        self._lbl_order = QLabel("Order: left → right")
        self._lbl_order.setStyleSheet("color: #8a8a8a; font-size: 11px;")
        mini.addStretch(1)
        mini.addWidget(self._lbl_order)
        ll.addLayout(mini)

        ll.addWidget(self._view, 1)
        self._view.drop_requested.connect(self._on_drop_command)

        # right: palette + variables
        right = QWidget()
        right.setMinimumWidth(300)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(6)

        rl.addWidget(self._section_label("Command palette"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter commands…")
        self._search.textChanged.connect(self._refresh_palette)
        rl.addWidget(self._search)

        self._palette = CommandPalette()
        self._palette.setSelectionMode(QAbstractItemView.SingleSelection)
        self._palette.setDragEnabled(True)
        self._palette.setDefaultDropAction(Qt.CopyAction)
        self._palette.setStyleSheet(
            "QListWidget { background: #1b1e23; color: #d8d8d8; "
            "border: 1px solid #3a3f47; }"
            "QListWidget::item { padding: 2px 6px; }"
            "QListWidget::item:hover { background: #2a2e35; }"
            "QListWidget::item:selected { background: #2f4a6b; color: #ffffff; }"
        )
        self._palette.itemDoubleClicked.connect(self._palette_add)
        self._palette.currentItemChanged.connect(self._on_palette_current)
        rl.addWidget(self._palette, 3)

        # ── bottom: tabbed group (Info | Paths) ──
        self._side_tabs = QTabWidget()
        self._side_tabs.setDocumentMode(True)

        # ── Info tab: command documentation ──
        self._info_view = QTextBrowser()
        self._info_view.setOpenExternalLinks(False)
        self._info_view.setStyleSheet(
            "QTextBrowser { background: #1b1e23; color: #d8d8d8; "
            "border: 1px solid #3a3f47; padding: 6px; }"
            "QTextBrowser table { border-collapse: collapse; width: 100%; }"
            "QTextBrowser th { background: #23262b; color: #bfbfbf; "
            "padding: 3px 6px; text-align: left; }"
            "QTextBrowser td { padding: 3px 6px; border-top: 1px solid #2c3038; }"
        )
        self._side_tabs.addTab(self._info_view, "Info")
        self._show_command_info("")

        # ── Paths tab: pipeline variables ──
        path_page = QWidget()
        p_lay = QVBoxLayout(path_page)
        p_lay.setContentsMargins(0, 0, 0, 0)
        p_lay.setSpacing(6)

        hint = QLabel("Variables are referenced in node parameters as %NAME%.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8a8a8a; font-size: 11px;")
        p_lay.addWidget(hint)

        self._var_table = QTableWidget(0, 2)
        self._var_table.setHorizontalHeaderLabels(["Name", "Value"])
        self._var_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._var_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._var_table.verticalHeader().setVisible(False)
        p_lay.addWidget(self._var_table, 1)

        var_btns = QHBoxLayout()
        self._btn_var_add = QPushButton("+ Add")
        self._btn_var_add.clicked.connect(lambda: self._var_table.insertRow(self._var_table.rowCount()))
        self._btn_var_del = QPushButton("− Remove")
        self._btn_var_del.clicked.connect(self._remove_var_row)
        var_btns.addWidget(self._btn_var_add)
        var_btns.addWidget(self._btn_var_del)
        var_btns.addStretch(1)
        p_lay.addLayout(var_btns)

        self._side_tabs.addTab(path_page, "Paths")

        rl.addWidget(self._side_tabs, 2)

        self._split.addWidget(left)
        self._split.addWidget(right)
        self._split.setSizes([900, 340])
        self._split.setChildrenCollapsible(False)
        root.addWidget(self._split, 1)

        # ── bottom: progress + log ──
        prog_row = QHBoxLayout()
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setFixedHeight(18)
        self._lbl_status = QLabel("Ready")
        self._lbl_status.setStyleSheet("color: #bfbfbf; font-size: 11px;")
        prog_row.addWidget(self._progress, 1)
        prog_row.addWidget(self._lbl_status)
        root.addLayout(prog_row)

        self._log = LogPanel()
        self._log.setMaximumHeight(170)
        root.addWidget(self._log)

        self._refresh_palette("")

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: 600; font-size: 12px; color: #d8d8d8;")
        return lbl

    # ── palette ──────────────────────────────────────────────────────
    def _refresh_palette(self, filter_text: str) -> None:
        self._palette.clear()
        ft = filter_text.strip().lower()
        for cat in categories():
            cmds = [
                name for name, spec in COMMANDS.items()
                if spec.category == cat
                and (not ft or ft in name.lower())
            ]
            if not cmds:
                continue
            header = QListWidgetItem(f"──  {cat}  ({len(cmds)})  ──")
            header.setFlags(Qt.NoItemFlags)
            header.setForeground(QColor(_cat_color(cmds[0])))
            self._palette.addItem(header)
            for name in sorted(cmds):
                item = QListWidgetItem(name)
                item.setData(Qt.UserRole, name)
                spec = spec_for(name)
                if spec and spec.description:
                    item.setToolTip(spec.description)
                self._palette.addItem(item)

    def _palette_add(self, item: QListWidgetItem) -> None:
        cmd = item.data(Qt.UserRole)
        if cmd:
            self._add_node(cmd)

    def _on_palette_current(self, current, previous) -> None:  # type: ignore[no-untyped-def]
        """Live-update the Info tab when a palette command is selected."""
        if current is not None:
            cmd = current.data(Qt.UserRole)
            if cmd:
                self._show_command_info(cmd)

    def _show_command_info(self, name: str) -> None:
        """Fill the Info tab with the command's documentation."""
        if not name:
            self._info_view.setHtml(
                "<i>Select a command in the palette to see what it does "
                "and which parameters it needs.</i>"
            )
            return
        spec = spec_for(name)
        if spec is None:
            self._info_view.setHtml(f"<b>{name}</b><br><i>Unknown command.</i>")
            return
        esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        parts = [f"<h3 style='margin:0 0 2px 0'>{esc(name)}</h3>"]
        meta = [esc(spec.category)]
        if spec.produces:
            meta.append("produces: " + esc(spec.produces))
        if spec.consumes:
            meta.append("consumes: " + esc(spec.consumes))
        parts.append("<span style='color:#8a8a8a'>" + " · ".join(meta) + "</span>")
        if spec.description:
            parts.append(f"<p style='margin:6px 0'>{esc(spec.description)}</p>")
        if spec.params:
            parts.append(
                "<table cellspacing='0' cellpadding='0' width='100%'>"
                "<tr><th align='left'>Parameter</th><th align='left'>Type</th>"
                "<th align='left'>Req.</th></tr>"
            )
            for ps in spec.params:
                req = "yes" if ps.required else "opt."
                color = "#e8c454" if ps.required else "#8a8a8a"
                kind = esc(ps.kind)
                if ps.kind == "path":
                    kind = "folder" if _is_folder_param(ps.name) else "file"
                if ps.choices:
                    kind += " (" + esc(" / ".join(ps.choices)) + ")"
                tip = esc(ps.description) if ps.description else ""
                parts.append(
                    f"<tr><td title='{tip}'><b>{esc(ps.name)}</b></td>"
                    f"<td>{kind}</td>"
                    f"<td style='color:{color}'>{req}</td></tr>"
                )
            parts.append("</table>")
            parts.append(
                "<p style='color:#8a8a8a;margin:6px 0 0 0;font-size:11px'>"
                "<b>yes</b> = required parameter, <b>opt.</b> = optional.")
        else:
            parts.append("<p style='color:#8a8a8a;margin:6px 0'>(no parameters)</p>")
        self._info_view.setHtml("".join(parts))

    def _on_drop_command(self, command: str, scene_pos) -> None:
        """Create a node at the drop position (from a palette drag)."""
        if command in COMMANDS:
            node = self._add_node(command)
            node.setPos(scene_pos.x(), scene_pos.y())
            self._log.append(f"Added node: {command}")

    # ── node helpers ─────────────────────────────────────────────────
    def _add_node(self, command: str, params: dict | None = None) -> CommandNode:
        params = params or {}
        spec = spec_for(command)
        node = CommandNode(command, params, self._scene)
        # place to the right of the current right-most node
        if self._scene.nodes:
            max_x = max(n.pos().x() for n in self._scene.nodes)
            node.setPos(max_x + _NODE_W + 40, 40)
        else:
            node.setPos(60, 60)
        self._scene.add_node(node)
        return node

    def _delete_selected(self) -> None:
        sel_nodes = [
            i for i in self._scene.selectedItems() if isinstance(i, CommandNode)
        ]
        sel_conns = [
            i for i in self._scene.selectedItems() if isinstance(i, Connection)
        ]
        for c in sel_conns:
            self._scene.remove_connection(c)
        for n in sel_nodes:
            self._scene.remove_node(n)

    def _clear_canvas(self) -> None:
        self._scene.clear()

    def _add_menu(self) -> None:
        menu = QMenu(self)
        for cat in categories():
            cmds = sorted(
                name for name, spec in COMMANDS.items() if spec.category == cat
            )
            if not cmds:
                continue
            sub = menu.addMenu(cat)
            for name in cmds:
                act = sub.addAction(name)
                act.triggered.connect(lambda _c=False, n=name: self._add_node(n))
        menu.exec_(self._btn_add.mapToGlobal(self._btn_add.rect().bottomLeft()))

    # ── pipeline <-> UI ──────────────────────────────────────────────
    def _collect_pipeline(self) -> RSPipeline:
        p = RSPipeline(
            exe=self._pr_exe.value.strip(),
            mode="headless" if self._cmb_mode.currentIndex() == 1 else "gui",
            quit=self._chk_quit.isChecked(),
        )
        p.variables = self._collect_variables()
        for i, node in enumerate(self._scene.ordered_nodes()):
            p.nodes.append(RSNode(
                command=node.command,
                params=node.params(),
                x=node.pos().x(),
                y=node.pos().y(),
                enabled=node._chk.isChecked(),
                id=node.node_id or f"n{i}",
            ))
        p.connections = [(c.src.node_id or c.src.command, c.dst.node_id or c.dst.command)
                         for c in self._scene.connections]
        return p

    def _collect_variables(self) -> dict:
        out: dict[str, str] = {}
        for r in range(self._var_table.rowCount()):
            name_item = self._var_table.item(r, 0)
            val_item = self._var_table.item(r, 1)
            name = (name_item.text().strip() if name_item else "")
            value = (val_item.text() if val_item else "")
            if name:
                out[name] = value
        return out

    def _set_variables(self, variables: dict) -> None:
        self._var_table.setRowCount(0)
        for name, value in variables.items():
            r = self._var_table.rowCount()
            self._var_table.insertRow(r)
            self._var_table.setItem(r, 0, QTableWidgetItem(name))
            self._var_table.setItem(r, 1, QTableWidgetItem(str(value)))

    def _remove_var_row(self) -> None:
        row = self._var_table.currentRow()
        if row >= 0:
            self._var_table.removeRow(row)

    def _load_pipeline(self, p: RSPipeline) -> None:
        self._pr_exe.set_value(p.exe)
        self._cmb_mode.setCurrentIndex(1 if p.mode == "headless" else 0)
        self._chk_quit.setChecked(p.quit)
        self._set_variables(p.variables)
        self._scene.clear()
        for node in p.nodes:
            n = self._add_node(node.command, node.params)
            n.setPos(node.x, node.y)
            n.node_id = node.id
            n._chk.setChecked(node.enabled)
        # Fit the whole pipeline, but never zoom out below 50 % –
        # long pipelines stay readable at 100 % (Fit view is a button).
        self._view.fit(min_scale=0.5)

    # ── presets / import / save / open / export ──────────────────────
    def _load_preset(self, name: str) -> None:
        try:
            p = make_preset(name)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Preset", f"Failed to load preset:\n{exc}")
            return
        self._pipeline = p
        self._load_pipeline(p)
        self._log.append(f"Loaded preset: {name} ({len(p.nodes)} steps).")

    def _load_preset_current(self) -> None:
        self._load_preset(self._cmb_preset.currentText())

    def _import_bat(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import RealityScan .bat", "",
            "Batch files (*.bat *.cmd);;All files (*)",
        )
        if not path:
            return
        try:
            p = from_batch(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Import", f"Failed to import:\n{exc}")
            return
        self._pipeline = p
        self._load_pipeline(p)
        self._log.append(f"Imported {os.path.basename(path)}: "
                         f"{len(p.nodes)} steps, {len(p.variables)} variables.")

    def _save(self) -> None:
        p = self._collect_pipeline()
        path, _ = QFileDialog.getSaveFileName(
            self, "Save pipeline", "pipeline.json",
            "Pipeline JSON (*.json);;All files (*)",
        )
        if not path:
            return
        to_json(p, path)
        self._log.append(f"Saved pipeline → {path}")

    def _open(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open pipeline", "",
            "Pipeline JSON (*.json);;All files (*)",
        )
        if not path:
            return
        try:
            p = from_json(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Open", f"Failed to open:\n{exc}")
            return
        self._pipeline = p
        self._load_pipeline(p)
        self._log.append(f"Opened {os.path.basename(path)}: {len(p.nodes)} steps.")

    def _export_bat(self) -> None:
        p = self._collect_pipeline()
        path, _ = QFileDialog.getSaveFileName(
            self, "Export .bat", "pipeline.bat",
            "Batch files (*.bat);;All files (*)",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(build_batch(p))
        self._log.append(f"Exported .bat → {path}")

    # ── run ──────────────────────────────────────────────────────────
    def _wire_runner(self) -> None:
        self._runner.log.connect(self._log.append)
        self._runner.progress.connect(self._on_progress)
        self._runner.state_changed.connect(self._on_state)
        self._runner.finished.connect(self._on_finished)

    def _run(self) -> None:
        p = self._collect_pipeline()
        if not p.nodes:
            QMessageBox.warning(self, "Empty pipeline", "Add at least one command node.")
            return
        if not p.exe or not os.path.isfile(p.exe):
            QMessageBox.warning(
                self, "RealityScan not found",
                f"The executable was not found:\n{p.exe}\n\n"
                "Use the file browser in the toolbar to point IgorVision "
                "at RealityCapture.exe / RealityScan.exe.",
            )
            return
        self._progress.setValue(0)
        self._lbl_status.setText("Running…")
        self._btn_run.setEnabled(False)
        self._btn_abort.setEnabled(True)
        self._runner.run(p)

    def _abort(self) -> None:
        self._runner.abort()

    def _on_progress(self, pct: int, label: str) -> None:
        self._progress.setValue(pct)
        self._lbl_status.setText(label)

    def _on_state(self, state: str) -> None:
        if state == "running":
            self._lbl_status.setText("Running…")
        elif state == "finished":
            self._lbl_status.setText("Finished")
        elif state == "aborted":
            self._lbl_status.setText("Aborted")
        elif state == "error":
            self._lbl_status.setText("Error")

    def _on_finished(self, code: int) -> None:
        self._btn_run.setEnabled(True)
        self._btn_abort.setEnabled(False)
        if code == 0:
            self._progress.setValue(100)
            self._lbl_status.setText("Finished (OK)")
        else:
            self._lbl_status.setText(f"Finished (exit {code})")
