"""
lumina_ui.py — Lumina-LY Sentinel desktop overlay.

Floating translucent PyQt6 widget that:
  - watches the project directory for file changes
  - displays project file stats with one-click introspection
  - provides a query interface to the SentinelBrain vector memory
  - collapses into a minimalist title bar on double-click
"""

import sys
import os
import ast
import datetime
from typing import Optional

from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLabel,
                             QScrollArea, QFrame, QGraphicsOpacityEffect,
                             QPushButton, QInputDialog)
from PyQt6.QtCore import Qt, QPoint, QPropertyAnimation, QEasingCurve, \
    QFileSystemWatcher, QTimer
from PyQt6.QtGui import QFont, QMouseEvent, QResizeEvent

try:
    import file_manager
except ImportError:
    file_manager = None  # type: ignore[assignment]

try:
    from rag_core import SentinelBrain
except ImportError:
    SentinelBrain = None  # type: ignore[assignment]


class LuminaSentinel(QWidget):
    """Floating translucent project-monitor panel.

    Window behaviour
    ----------------
    - macOS native title bar with real traffic-light buttons (✕ ─ 口).
    - Stays on top of all windows.
    - Full native Spaces support (real NSWindow).
    - Double-click toggles between full (340×560) and mini (340×45) mode.
    - Drag by the title area to reposition.

    Core loop
    ---------
    A ``QFileSystemWatcher`` monitors the current working directory.
    On every change the new content is audited (style check) and
    memorised (vector ingestion), then the file list is refreshed.
    """

    def __init__(self) -> None:
        """Initialise window flags, brain, watchers, and UI."""
        super().__init__()

        # macOS native window → real NSWindow with native title bar
        # and traffic-light buttons.  Full Spaces support out of the box.
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Lumina-LY Sentinel")

        # ── Geometry ─────────────────────────────────────────────
        self.normal_size: tuple[int, int] = (340, 560)
        self.mini_size: tuple[int, int] = (340, 45)
        self.setGeometry(100, 100, self.normal_size[0], self.normal_size[1])
        self.old_pos: Optional[QPoint] = None
        self.is_mini: bool = False

        # ── Brain ────────────────────────────────────────────────
        if SentinelBrain is not None:
            self.brain: Optional[SentinelBrain] = SentinelBrain()
        else:
            self.brain = None

        # ── File watcher ─────────────────────────────────────────
        self.watcher: QFileSystemWatcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self.on_file_changed)
        self.watcher.fileChanged.connect(self.on_file_changed)

        self.refresh_timer: QTimer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.execute_refresh)

        self.init_ui()

    # ── UI Construction ───────────────────────────────────────────────

    def init_ui(self) -> None:
        """Build the complete widget tree.

        Layout hierarchy::

            self (QVBoxLayout)
             ├── glow_bar (QFrame)
             ├── container (QWidget)
             │    ├── title_label
             │    ├── search_btn (QPushButton → open_command_center)
             │    ├── chat_result (QLabel, hidden by default)
             │    ├── line (QFrame, separator)
             │    ├── scroll_area (QScrollArea → file list)
             │    └── log_label
        """
        self.main_layout: QVBoxLayout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)

        # ── Glow bar ────────────────────────────────────────────
        self.glow_bar: QFrame = QFrame()
        self.glow_bar.setFixedHeight(6)
        self.set_glow_color("rag")
        self.main_layout.addWidget(self.glow_bar)

        self.opacity_effect: QGraphicsOpacityEffect = QGraphicsOpacityEffect(self.glow_bar)
        self.glow_bar.setGraphicsEffect(self.opacity_effect)
        self.animation: QPropertyAnimation = QPropertyAnimation(self.opacity_effect,
                                                                b"opacity")
        self.animation.setDuration(2500)
        self.animation.setStartValue(0.3)
        self.animation.setKeyValueAt(0.5, 1.0)
        self.animation.setEndValue(0.3)
        self.animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self.animation.setLoopCount(-1)
        self.animation.start()

        # ── Container ───────────────────────────────────────────
        self.container: QWidget = QWidget()
        self.container.setObjectName("MainContainer")
        self.container.setStyleSheet(
            "#MainContainer { background-color: rgba(230, 230, 250, 235); "
            "border: 1.5px solid #D1C4E9; border-radius: 18px; }"
        )
        self.container_layout: QVBoxLayout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(15, 0, 15, 15)

        # ── Title ───────────────────────────────────────────────
        self.title_label: QLabel = QLabel("Lumina-LY Sentinel (双击折叠)")
        self.title_label.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
        self.title_label.setStyleSheet(
            "color: #512DA8; margin-top: 5px; border: none; background: transparent;"
        )
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.container_layout.addWidget(self.title_label)

        # ── Search button (disguised as input field) ────────────
        self.search_btn: QPushButton = QPushButton(" 🔍 点击唤醒: 哨兵指令舱...")
        self.search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.search_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 150);
                border: 1px solid #D1C4E9; border-radius: 10px;
                padding: 6px 10px; color: #512DA8; text-align: left;
            }
            QPushButton:hover {
                border: 1.5px solid #9575CD;
                background-color: rgba(255, 255, 255, 200);
            }
        """)
        self.search_btn.clicked.connect(self.open_command_center)
        self.container_layout.addWidget(self.search_btn)

        # ── Chat result label ───────────────────────────────────
        self.chat_result: QLabel = QLabel("")
        self.chat_result.setWordWrap(True)
        self.chat_result.setStyleSheet(
            "color: #E65100; font-weight: bold; "
            "background: rgba(255,200,150,80); border-radius: 5px; padding: 5px;"
        )
        self.chat_result.hide()
        self.container_layout.addWidget(self.chat_result)

        # ── Separator ───────────────────────────────────────────
        self.line: QFrame = QFrame()
        self.line.setFrameShape(QFrame.Shape.HLine)
        self.line.setStyleSheet("color: rgba(81, 45, 168, 40); border: none; background: transparent;")
        self.container_layout.addWidget(self.line)

        # ── Scrollable file list ────────────────────────────────
        self.scroll_area: QScrollArea = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setStyleSheet("background: transparent; border: none;")
        self.scroll_content: QWidget = QWidget()
        self.scroll_content.setStyleSheet("background: transparent; border: none;")
        self.file_list_layout: QVBoxLayout = QVBoxLayout(self.scroll_content)
        self.file_list_layout.setContentsMargins(0, 5, 0, 5)
        self.file_list_layout.setSpacing(8)
        self.file_list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(self.scroll_content)
        self.container_layout.addWidget(self.scroll_area)

        # ── Status bar ──────────────────────────────────────────
        self.log_label: QLabel = QLabel("哨兵待命...")
        self.log_label.setFont(QFont("Courier New", 10))
        self.log_label.setStyleSheet("color: #9575CD; background: transparent; padding-top: 5px;")
        self.log_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.container_layout.addWidget(self.log_label)

        self.main_layout.addWidget(self.container)

        # ── Initial population ──────────────────────────────────
        self.refresh_files()
        self.watcher.addPath(os.getcwd())

    # ── Glow Styling ──────────────────────────────────────────────────

    def set_glow_color(self, state: str) -> None:
        """Set the glowing top bar gradient by *state*.

        States
        ------
        ``"rag"``     — purple &#8596; gold (idle)
        ``"update"``  — purple &#8596; green (safe file change)
        ``"warning"`` — purple &#8596; red (style anomaly)
        """
        if state == "rag":
            self.glow_bar.setStyleSheet(
                "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                "stop:0 rgba(81, 45, 168, 220), "
                "stop:0.5 rgba(255, 215, 0, 255), "
                "stop:1 rgba(81, 45, 168, 220)); "
                "border-top-left-radius: 18px; border-top-right-radius: 18px;"
            )
        elif state == "update":
            self.glow_bar.setStyleSheet(
                "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                "stop:0 rgba(81, 45, 168, 220), "
                "stop:0.5 rgba(0, 250, 154, 255), "
                "stop:1 rgba(81, 45, 168, 220)); "
                "border-top-left-radius: 18px; border-top-right-radius: 18px;"
            )
        elif state == "warning":
            self.glow_bar.setStyleSheet(
                "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                "stop:0 rgba(81, 45, 168, 220), "
                "stop:0.5 rgba(255, 69, 0, 255), "
                "stop:1 rgba(81, 45, 168, 220)); "
                "border-top-left-radius: 18px; border-top-right-radius: 18px;"
            )

    # ── Command Center ────────────────────────────────────────────────

    def open_command_center(self) -> None:
        """Open a ``QInputDialog`` so the user can query the brain.

        The dialog is used instead of an inline ``QLineEdit`` because the
        native dialog reliably grabs keyboard focus on all desktops /
        window managers.

        On success, the brain's ``recall()`` result is shown in the
        ``chat_result`` label, which auto-hides after 6 seconds.
        """
        if not self.brain:
            self.chat_result.setText("⚠️ 大脑未连接！")
            self.chat_result.show()
            QTimer.singleShot(3000, self.chat_result.hide)
            return

        text, ok = QInputDialog.getText(
            self, "Lumina 指令舱",
            "请输入你的问题：\n(例如：文件过滤的逻辑在哪里？)"
        )

        if ok and text:
            self.chat_result.setText("🔍 正在大脑中检索记忆...")
            self.chat_result.show()
            QApplication.processEvents()

            answer = self.brain.recall(text)
            if answer["documents"] and answer["documents"][0]:
                file_name = answer["metadatas"][0][0]["source"]
                part = answer["metadatas"][0][0].get("part", "全局(旧记忆)")
                self.chat_result.setText(
                    f"🤖 答案线索：\n在 【{file_name}】 的第 {part} 块。"
                )
            else:
                self.chat_result.setText("🤖 记忆中没有找到相关线索...")

            QTimer.singleShot(6000, self.chat_result.hide)

    # ── File Change Handler ───────────────────────────────────────────

    def on_file_changed(self, path: str) -> None:
        """React to a watched file or directory change.

        Skips JetBrains backup artifacts (``___jb_``) and hidden files.
        When the file exists the content is audited for style drift,
        then memorised into the vector DB.  The file list is refreshed
        after a 500 ms debounce.
        """
        filename = os.path.basename(path)
        if "___jb_" not in filename and not filename.startswith("."):
            if self.brain and os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read()
                    is_safe, dist = self.brain.audit(content)
                    if is_safe:
                        self.set_glow_color("update")
                        self.log_label.setText(f"✅ 风格安全 (得分:{dist:.2f})")
                    else:
                        self.set_glow_color("warning")
                        self.log_label.setStyleSheet("color: red;")
                        self.log_label.setText(f"🚨 异体入侵！(得分:{dist:.2f})")
                        QTimer.singleShot(
                            4000,
                            lambda: self.log_label.setStyleSheet("color: #9575CD;")
                        )

                    self.brain.memorize(filename, content)
                except Exception:
                    pass
        self.refresh_timer.start(500)

    def execute_refresh(self) -> None:
        """Debounced file-list rebuild.

        Called 500 ms after the last file-system event.  Resets the glow
        bar to the idle ``"rag"`` state after a further 1.5 s.
        """
        self.refresh_files()
        QTimer.singleShot(1500, lambda: self.set_glow_color("rag"))

    def refresh_files(self) -> None:
        """Rebuild the scrollable file list from ``file_manager`` output.

        Filters out common noise directories (``.venv``, ``venv``, ``env``,
        ``bin``, ``site-packages``, ``__pycache__``, ``lib``, ``.egg-info``,
        JetBrains artifacts, and hidden files).
        """
        path = os.getcwd()
        full_paths = (
            [str(p) for p in file_manager.list_py_files_pathlib(path)]
            if file_manager
            else ["Error"]
        )

        # Clear existing items
        while self.file_list_layout.count() > 0:
            item = self.file_list_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
                item.widget().deleteLater()

        filtered_files = [
            f for f in full_paths
            if ".venv" not in f
               and "/venv/" not in f.lower()
               and "/env/" not in f.lower()
               and "/bin/" not in f.lower()
               and "site-packages" not in f
               and "__pycache__" not in f
               and "/lib" not in f.lower()
               and ".egg-info" not in f
               and not f.endswith("~")
               and "___jb_" not in f
               and not os.path.basename(f).startswith(".")
        ]

        if filtered_files:
            for f in filtered_files:
                if f not in self.watcher.files():
                    self.watcher.addPath(f)
                self.add_file_item(os.path.basename(f), f)
        else:
            self.add_file_item("No project files found.", None)

    def add_file_item(self, filename: str, full_path: Optional[str]) -> None:
        """Append a single file button to the scroll layout.

        Parameters
        ----------
        filename : str
            Display name (basename) of the file.
        full_path : str or None
            Absolute path used for stat introspection; ``None`` for the
            placeholder message.
        """
        btn = QPushButton(f" ✨  {filename}")
        btn.setFont(QFont("Segoe UI", 10))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setStyleSheet(
            "QPushButton { color: #673AB7; "
            "background-color: rgba(255, 255, 255, 120); "
            "border: 1px solid rgba(209, 196, 233, 100); "
            "border-radius: 8px; padding: 8px; text-align: left; } "
            "QPushButton:hover { background-color: rgba(255, 255, 255, 180); "
            "border: 1px solid #9575CD; }"
        )
        if full_path is not None:
            btn.clicked.connect(
                lambda checked, b=btn, p=full_path: self.reveal_file_stats(b, p)
            )
        self.file_list_layout.addWidget(btn)

    def reveal_file_stats(self, btn: QPushButton, file_path: str) -> None:
        """Parse *file_path* and display AST stats on the button.

        The button text is replaced with a multi-line summary showing:
        line count, size in KB, class names (top-level), and the first
        4 function names.
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            lines = content.splitlines()
            tree = ast.parse(content)
            classes = [node.name for node in ast.walk(tree)
                       if isinstance(node, ast.ClassDef)]
            funcs = [node.name for node in ast.walk(tree)
                     if isinstance(node, ast.FunctionDef)]
            size_kb = os.path.getsize(file_path) / 1024

            original_name = btn.text().split("\n")[0].split("  |  ")[0]
            details = f"{original_name}  |  {len(lines)} 行 ({size_kb:.1f} KB)"
            if classes:
                details += f"\n 🧱 类: {', '.join(classes)}"
            if funcs:
                details += (f"\n ⚙️ 函数: {', '.join(funcs[:4])}"
                            + ("..." if len(funcs) > 4 else ""))
            btn.setText(details)
        except Exception:
            pass

    # ── Window Interaction Overrides ──────────────────────────────────

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Toggle between full-size and mini (collapsed) mode."""
        if event.button() == Qt.MouseButton.LeftButton:
            if not self.is_mini:
                self.scroll_area.hide()
                self.log_label.hide()
                self.line.hide()
                self.search_btn.hide()
                self.title_label.setText("Lumina 待命")
                self.resize(self.mini_size[0], self.mini_size[1])
                self.is_mini = True
            else:
                self.scroll_area.show()
                self.log_label.show()
                self.line.show()
                self.search_btn.show()
                self.title_label.setText("Lumina-LY Sentinel (双击折叠)")
                self.resize(self.normal_size[0], self.normal_size[1])
                self.is_mini = False

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Begin dragging — store the initial cursor position."""
        if event.button() == Qt.MouseButton.LeftButton:
            self.old_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Continue dragging — offset the window position."""
        if self.old_pos is not None:
            delta = QPoint(event.globalPosition().toPoint() - self.old_pos)
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.old_pos = event.globalPosition().toPoint()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """End dragging — reset the stored position."""
        self.old_pos = None


# ── Entry Point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    app = QApplication(sys.argv)
    sentinel = LuminaSentinel()
    sentinel.show()
    sys.exit(app.exec())
