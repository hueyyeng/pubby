from __future__ import annotations

import hashlib
import os

from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *


SHOWS = [
    {"name": "testshow", "thumbnail": None},
    {"name": "spam", "thumbnail": None},
    {"name": "iforgotwhatyoudidlastweek", "thumbnail": None},
    {"name": "ショボン", "thumbnail": None},
    {"name": "spoon", "thumbnail": None},
]


def get_initials(name: str) -> str:
    return name[:2].upper()


def get_deterministic_color(name: str) -> QColor:
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()
    hue = int(digest[:4], 16) % 360
    return QColor.fromHsv(hue, 160, 200)


def make_initials_pixmap(name: str, size: int = 48) -> QPixmap:
    bg_color = get_deterministic_color(name)
    initials = get_initials(name)

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    painter.setBrush(QBrush(bg_color))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(0, 0, size, size)

    painter.setPen(QColor("white"))
    font = QFont()
    font.setPixelSize(int(size * 0.38))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(QRect(0, 0, size, size), Qt.AlignCenter, initials)

    painter.end()
    return pixmap


def make_thumbnail_pixmap(path: str, size: int = 48) -> QPixmap | None:
    pixmap = QPixmap(path)
    if pixmap.isNull():
        return None
    return pixmap.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)


def format_size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


def get_folder_size(path: str) -> int:
    total = 0
    with os.scandir(path) as it:
        for entry in it:
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
            elif entry.is_dir(follow_symlinks=False):
                total += get_folder_size(entry.path)
    return total


# ---------------------------------------------------------------------------
# ShowComboBox
# ---------------------------------------------------------------------------

class ShowItemDelegate(QStyledItemDelegate):
    THUMB_SIZE = 40
    PADDING = 6

    def paint(self, painter, option, index):
        painter.save()

        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        elif option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, option.palette.highlight().color().lighter(160))

        pixmap = index.data(Qt.DecorationRole)
        name = index.data(Qt.DisplayRole) or ""

        thumb_x = option.rect.x() + self.PADDING
        thumb_y = option.rect.y() + (option.rect.height() - self.THUMB_SIZE) // 2

        if pixmap:
            painter.drawPixmap(thumb_x, thumb_y, self.THUMB_SIZE, self.THUMB_SIZE, pixmap)

        text_x = thumb_x + self.THUMB_SIZE + self.PADDING
        text_rect = QRect(
            text_x,
            option.rect.y(),
            option.rect.width() - text_x + option.rect.x(),
            option.rect.height(),
        )

        if option.state & QStyle.State_Selected:
            painter.setPen(option.palette.highlightedText().color())
        else:
            painter.setPen(option.palette.text().color())

        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, name)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(200, self.THUMB_SIZE + self.PADDING * 2)


class ShowComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(220)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)

        delegate = ShowItemDelegate(self)
        self.setItemDelegate(delegate)
        self.view().setSpacing(2)

        self._populate(SHOWS)

        completer = QCompleter(self.model(), self)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.setCompleter(completer)

        self.setCurrentIndex(-1)
        self.lineEdit().clear()
        self.lineEdit().setFocusPolicy(Qt.ClickFocus)

        # Placeholder label overlay since Qt's built-in placeholder is unreliable on KDE
        self._placeholder_label = QLabel("Select show", self.lineEdit())
        color = self.lineEdit().palette().color(QPalette.Text)
        color.setAlpha(128)
        self._placeholder_label.setStyleSheet(
            f"color: rgba({color.red()},{color.green()},{color.blue()},{color.alpha()}); background: transparent;"
        )
        self._placeholder_label.move(4, 0)
        self._placeholder_label.resize(self.lineEdit().size())
        self._placeholder_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.lineEdit().textChanged.connect(
            lambda t: self._placeholder_label.setVisible(t == "")
        )

    def _populate(self, shows: list[dict]):
        self.clear()
        for show in shows:
            name = show["name"]
            path = show.get("thumbnail")

            pixmap = make_thumbnail_pixmap(path) if path else None
            if pixmap is None:
                pixmap = make_initials_pixmap(name)

            self.addItem(name)
            self.setItemData(self.count() - 1, pixmap, Qt.DecorationRole)


# ---------------------------------------------------------------------------
# SourcePanel
# ---------------------------------------------------------------------------

class FolderSizeWorkerSignals(QObject):
    done = Signal(str, object)  # path, size in bytes


class FolderSizeWorker(QRunnable):
    def __init__(self, path: str):
        super().__init__()
        self.path = path
        self.signals = FolderSizeWorkerSignals()

    def run(self):
        try:
            size = get_folder_size(self.path)
        except Exception:
            size = -1
        self.signals.done.emit(self.path, size)


class DropZone(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setAcceptDrops(False)  # parent handles drops
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        pen = QPen(Qt.gray, 1.5, Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        margin = 8
        painter.drawRoundedRect(
            margin, margin,
            self.width() - margin * 2,
            self.height() - margin * 2,
            6, 6
        )

        painter.setPen(Qt.gray)
        font = QFont()
        font.setPixelSize(12)
        painter.setFont(font)
        painter.drawText(
            self.rect(),
            Qt.AlignCenter,
            "Click to add or\ndrop folder here"
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()


class FolderCard(QWidget):
    """Hedge-style card: folder icon + name (bold) + size below."""
    removed = Signal(str)  # emits full path

    ICON_SIZE = 48

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip(path)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(6, 6, 4, 6)
        outer.setSpacing(8)

        # Folder icon
        icon_label = QLabel()
        icon = QApplication.style().standardIcon(QStyle.SP_DirIcon)
        icon_label.setPixmap(icon.pixmap(self.ICON_SIZE, self.ICON_SIZE))
        icon_label.setFixedSize(self.ICON_SIZE, self.ICON_SIZE)
        outer.addWidget(icon_label)

        # Name + size stacked vertically
        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        text_col.setContentsMargins(0, 0, 0, 0)

        self._name_label = QLabel(QFileInfo(path).fileName())
        self._name_label.setStyleSheet("font-weight: bold;")

        self._size_label = QLabel("Calculating…")
        small_font = QFont()
        small_font.setPixelSize(11)
        self._size_label.setFont(small_font)
        color = self.palette().color(QPalette.Text)
        color.setAlpha(160)
        self._size_label.setStyleSheet(
            f"color: rgba({color.red()},{color.green()},{color.blue()},{color.alpha()});"
        )

        text_col.addWidget(self._name_label)
        text_col.addWidget(self._size_label)
        outer.addLayout(text_col)

        outer.addStretch()

        # Remove button
        remove_btn = QPushButton("✕")
        remove_btn.setFixedSize(20, 20)
        remove_btn.setFlat(True)
        remove_btn.setCursor(Qt.PointingHandCursor)
        remove_btn.clicked.connect(lambda: self.removed.emit(self.path))
        outer.addWidget(remove_btn, alignment=Qt.AlignTop)

    def set_size(self, num_bytes: int):
        self._resolved_size = num_bytes
        if num_bytes < 0:
            self._size_label.setText("Unknown size")
            self._size_label.setStyleSheet("color: #c0392b;")  # red-ish
            self._size_label.setToolTip("Could not read folder size.")
        elif num_bytes == 0:
            self._size_label.setText("Empty folder — will not be processed")
            self._size_label.setStyleSheet("color: #e67e22;")  # amber
            self._size_label.setToolTip("This folder is empty and will be skipped during publishing.")
        else:
            self._size_label.setText(format_size(num_bytes))
            self._size_label.setToolTip("")

    @property
    def resolved_size(self) -> int | None:
        """None if still calculating, -1 if failed, otherwise bytes."""
        return getattr(self, "_resolved_size", None)


class SourcePanel(QWidget):
    def __init__(self, parent: PublisherDialog = None):
        super().__init__(parent)
        self.parent_: PublisherDialog = parent
        self._folders: list[str] = []

        self.setAcceptDrops(True)
        self.setAutoFillBackground(True)
        p = self.palette()
        p.setColor(self.backgroundRole(), QColor("#ADD8E6"))
        self.setPalette(p)

        self._pending_count = 0  # how many size workers still running

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Header row: label + clear + add buttons
        header_row = QHBoxLayout()
        self._header = QLabel("Source Folders (0)")
        self._header.setStyleSheet("font-weight: bold;")
        header_row.addWidget(self._header)
        header_row.addStretch()
        self._clear_btn = QPushButton("Clear All")
        self._clear_btn.setFlat(True)
        self._clear_btn.setCursor(Qt.PointingHandCursor)
        self._clear_btn.setVisible(False)
        self._clear_btn.clicked.connect(self._clear_all)
        header_row.addWidget(self._clear_btn)
        add_btn = QPushButton("+ Add")
        add_btn.setFlat(True)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self._browse)
        header_row.addWidget(add_btn)
        root.addLayout(header_row)

        # Total size label
        self._total_label = QLabel("")
        small_font = QFont()
        small_font.setPixelSize(11)
        self._total_label.setFont(small_font)
        self._total_label.setVisible(False)
        root.addWidget(self._total_label)

        # Drop zone (empty state)
        self._drop_zone = DropZone()
        self._drop_zone.clicked.connect(self._browse)
        root.addWidget(self._drop_zone)

        # Scroll area for folder cards (populated state)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setVisible(False)

        self._card_container = QWidget()
        self._card_layout = QVBoxLayout(self._card_container)
        self._card_layout.setContentsMargins(0, 0, 0, 0)
        self._card_layout.setSpacing(4)
        self._card_layout.addStretch()
        self._scroll.setWidget(self._card_container)
        root.addWidget(self._scroll)

        root.addStretch()

    def _update_header(self):
        count = len(self._folders)
        self._header.setText(f"Source Folders ({count})")
        self._clear_btn.setVisible(count > 0)
        self._total_label.setVisible(count > 0)
        self._update_total()

    def _update_total(self):
        if not self._folders:
            self._total_label.setText("")
            return
        if self._pending_count > 0:
            self._total_label.setText("Total: Calculating…")
            return
        total = 0
        for i in range(self._card_layout.count()):
            item = self._card_layout.itemAt(i)
            if item and isinstance(item.widget(), FolderCard):
                size = item.widget().resolved_size
                if size is not None and size > 0:
                    total += size
        self._total_label.setText(f"Total: {format_size(total)}")

    def _set_empty_state(self, empty: bool):
        self._drop_zone.setVisible(empty)
        self._scroll.setVisible(not empty)

    def _browse(self):
        path = QFileDialog.getExistingDirectory(self, "Select Source Folder")
        if path:
            self._add_folder(path)

    def _add_folder(self, path: str):
        path = QDir.toNativeSeparators(path)

        for existing in self._folders:
            if path.startswith(existing + QDir.separator()):
                self.parent_.status_bar.showMessage(
                    f"⚠ Skipped: '{QFileInfo(path).fileName()}' is a subfolder of '{QFileInfo(existing).fileName()}'"
                )
                return
            if existing.startswith(path + QDir.separator()):
                self.parent_.status_bar.showMessage(
                    f"⚠ Skipped: '{QFileInfo(path).fileName()}' is a parent of '{QFileInfo(existing).fileName()}'"
                )
                return
            if path == existing:
                self.parent_.status_bar.showMessage(
                    f"⚠ Skipped: '{QFileInfo(path).fileName()}' is already added"
                )
                return

        self._folders.append(path)

        card = FolderCard(path)
        card.removed.connect(self._remove_folder)
        self._card_layout.insertWidget(self._card_layout.count() - 1, card)

        # Kick off async size calculation
        self._pending_count += 1
        worker = FolderSizeWorker(path)
        worker.signals.done.connect(self._on_size_done)
        QThreadPool.globalInstance().start(worker)

        self._update_header()
        self._set_empty_state(False)
        self.parent_.status_bar.showMessage(f"Added: {path}")
        self.parent_.filelist_panel.add_folder(path)

    def _on_size_done(self, path: str, size: int):
        self._pending_count = max(0, self._pending_count - 1)
        for i in range(self._card_layout.count()):
            item = self._card_layout.itemAt(i)
            if item and isinstance(item.widget(), FolderCard) and item.widget().path == path:
                item.widget().set_size(size)
                break
        self._update_total()

    def _clear_all(self):
        self._folders.clear()
        self._pending_count = 0
        while self._card_layout.count() > 1:  # keep the stretch
            item = self._card_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._update_header()
        self._set_empty_state(True)
        self.parent_.status_bar.showMessage("Cleared all source folders.")
        self.parent_.filelist_panel.clear_all()

    def _remove_folder(self, path: str):
        self._folders.remove(path)

        for i in range(self._card_layout.count()):
            item = self._card_layout.itemAt(i)
            if item and isinstance(item.widget(), FolderCard) and item.widget().path == path:
                item.widget().deleteLater()
                self._card_layout.removeItem(item)
                break

        self._update_header()
        if not self._folders:
            self._set_empty_state(True)
        self._update_total()
        self.parent_.status_bar.showMessage(f"Removed: {path}")
        self.parent_.filelist_panel.remove_folder(path)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            if all(QFileInfo(u.toLocalFile()).isDir() for u in event.mimeData().urls()):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            self._add_folder(url.toLocalFile())


# ---------------------------------------------------------------------------
# FileListPanel
# ---------------------------------------------------------------------------

FILE_PRESETS: list[tuple[str, list[str]]] = [
    ("Generic",   []),           # empty = all files
    ("RED",       [".r3d"]),
    ("MXF",       [".mxf"]),
    ("ARW",       [".arw"]),
    ("Insta360",  [".insv"]),
]

# Section height constraints — future Jira ticket: expose in settings
SECTION_MIN_HEIGHT = 150
SECTION_MAX_HEIGHT = 640
QWIDGETSIZE_MAX = 5120


class ExtensionFilterModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._extensions: list[str] = []  # empty = show all

    def set_extensions(self, exts: list[str]):
        self._extensions = [e.lower() for e in exts]
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        source_model = self.sourceModel()
        index = source_model.index(source_row, 0, source_parent)

        # Always show directories so tree stays navigable
        if source_model.isDir(index):
            return True

        name = source_model.fileName(index).lower()

        # Extension filter
        if self._extensions and not any(name.endswith(ext) for ext in self._extensions):
            return False

        # Search filter
        pattern = self.filterRegularExpression().pattern()
        if pattern and pattern.lower() not in name:
            return False

        return True


class FolderSection(QWidget):
    """Collapsible section for one source folder with its own tree + filter."""

    def __init__(self, path: str, parent: FileListPanel = None):
        super().__init__(parent)
        self.path = path
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.setMinimumHeight(SECTION_MIN_HEIGHT)
        self.setMaximumHeight(SECTION_MAX_HEIGHT)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 6)
        root.setSpacing(0)

        # --- Header row ---
        header = QWidget()
        header.setCursor(Qt.PointingHandCursor)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(4, 4, 4, 4)
        header_layout.setSpacing(6)

        self._arrow = QLabel("▼")
        self._arrow.setFixedWidth(14)
        header_layout.addWidget(self._arrow)

        folder_name = QLabel(QFileInfo(path).fileName())
        folder_name.setStyleSheet("font-weight: bold;")
        folder_name.setToolTip(path)
        header_layout.addWidget(folder_name)
        header_layout.addStretch()

        # Search bar
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search loaded files…")
        self._search.setFixedWidth(160)
        self._search.setClearButtonEnabled(True)
        header_layout.addWidget(self._search)

        # Preset combo
        self._preset_combo = QComboBox()
        for label, _ in FILE_PRESETS:
            self._preset_combo.addItem(label)
        self._preset_combo.setFixedWidth(100)
        header_layout.addWidget(self._preset_combo)

        root.addWidget(header)

        # --- Tree view ---
        self._tree_container = QWidget()
        tree_layout = QVBoxLayout(self._tree_container)
        tree_layout.setContentsMargins(0, 0, 0, 0)

        self._fs_model = QFileSystemModel()
        self._fs_model.setRootPath(path)
        self._fs_model.setFilter(
            QDir.NoDotAndDotDot | QDir.AllDirs | QDir.Files
        )

        self._proxy = ExtensionFilterModel()
        self._proxy.setSourceModel(self._fs_model)

        self._tree = QTreeView()
        self._tree.setModel(self._proxy)
        self._tree.setRootIndex(
            self._proxy.mapFromSource(self._fs_model.index(path))
        )
        self._tree.setMinimumHeight(180)
        self._tree.setSortingEnabled(True)
        self._tree.sortByColumn(0, Qt.AscendingOrder)
        self._tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self._tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._tree.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        # Hide the "Type" column (index 2) — redundant with preset filter
        self._tree.hideColumn(2)

        tree_layout.addWidget(self._tree)

        # Hint label
        hint = QLabel("ℹ Search only covers expanded folders")
        hint_font = QFont()
        hint_font.setPixelSize(10)
        hint.setFont(hint_font)
        color = self.palette().color(QPalette.Text)
        color.setAlpha(120)
        hint.setStyleSheet(
            f"color: rgba({color.red()},{color.green()},{color.blue()},{color.alpha()});"
        )
        tree_layout.addWidget(hint)
        root.addWidget(self._tree_container)

        # Separator line
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        root.addWidget(line)

        # --- Signals ---
        header.mousePressEvent = lambda e: self._toggle_collapse()
        self._search.textChanged.connect(self._proxy.setFilterRegularExpression)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)

    def _toggle_collapse(self):
        collapsed = self._tree_container.isVisible()
        self._tree_container.setVisible(not collapsed)
        self._arrow.setText("▶" if collapsed else "▼")

    def _on_preset_changed(self, index: int):
        _, exts = FILE_PRESETS[index]
        self._proxy.set_extensions(exts)


class FileListPanel(QWidget):
    def __init__(self, parent: PublisherDialog = None):
        super().__init__(parent)
        self.parent_: PublisherDialog = parent
        self._folders: set[str] = set()

        self.setAutoFillBackground(True)
        p = self.palette()
        p.setColor(self.backgroundRole(), QColor("#FFFACD"))
        self.setPalette(p)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

        # Empty state label
        self._empty_label = QLabel("Add source folders to see files.")
        self._empty_label.setAlignment(Qt.AlignCenter)
        color = self.palette().color(QPalette.Text)
        color.setAlpha(120)
        self._empty_label.setStyleSheet(
            f"color: rgba({color.red()},{color.green()},{color.blue()},{color.alpha()});"
        )
        root.addWidget(self._empty_label)

        # Vertical splitter holding all collapsible sections
        self._splitter = QSplitter(Qt.Vertical)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setVisible(False)
        root.addWidget(self._splitter)

    def add_folder(self, path: str):
        self._folders.add(path)
        section = FolderSection(path, self)
        self._splitter.addWidget(section)
        self._empty_label.setVisible(False)
        self._splitter.setVisible(True)
        self._update_size_policies()

    def remove_folder(self, path: str):
        for i in range(self._splitter.count()):
            widget = self._splitter.widget(i)
            if isinstance(widget, FolderSection) and widget.path == path:
                self._folders.remove(path)
                widget.deleteLater()
                break
        if self._splitter.count() == 0:
            self._empty_label.setVisible(True)
            self._splitter.setVisible(False)
        else:
            self._update_size_policies()

    def clear_all(self):
        for i in range(self._splitter.count()):
            widget = self._splitter.widget(i)
            widget.deleteLater()
        self._empty_label.setVisible(True)
        self._splitter.setVisible(False)
        self._folders.clear()

    def _update_size_policies(self):
        count = self._splitter.count()
        total_folders = len(self._folders)
        print(f"{total_folders=}, {count} splitters")
        for i in range(total_folders):
            widget = self._splitter.widget(i)
            if total_folders == 1:
                widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                widget.setMaximumHeight(QWIDGETSIZE_MAX)
            else:
                widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
                widget.setMaximumHeight(SECTION_MAX_HEIGHT)


class DetailsPanel(QWidget):
    def __init__(self, parent: PublisherDialog = None):
        super().__init__(parent)
        self.parent_: PublisherDialog = parent

        self.setAutoFillBackground(True)
        p = self.palette()
        p.setColor(self.backgroundRole(), QColor("#90EE90"))
        self.setPalette(p)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Details"))
        layout.addStretch()


class StatusBar(QStatusBar):
    def __init__(self, parent: PublisherDialog = None):
        super().__init__(parent)
        self.parent_: PublisherDialog = parent

        self.setFixedHeight(28)
        self.setAutoFillBackground(True)
        p = self.palette()
        p.setColor(self.backgroundRole(), QColor("#FFB6C1"))
        self.setPalette(p)
        self.showMessage("Ready.")


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class PublisherDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pubby")
        self.resize(1100, 700)
        self._build_ui()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 0)
        root.setSpacing(6)

        self.show_combo = ShowComboBox()
        self.show_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        root.addWidget(self.show_combo)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setChildrenCollapsible(False)

        self.source_panel = SourcePanel(self)
        self.filelist_panel = FileListPanel(self)
        self.details_panel = DetailsPanel(self)

        self.splitter.addWidget(self.source_panel)
        self.splitter.addWidget(self.filelist_panel)
        self.splitter.addWidget(self.details_panel)

        self.splitter.setSizes([250, 600, 250])
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 1)

        root.addWidget(self.splitter)

        self.status_bar = StatusBar(self)
        root.addWidget(self.status_bar)