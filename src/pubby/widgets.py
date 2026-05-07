from __future__ import annotations

import time

import hashlib
import os
from PySide6.QtCore import *
from PySide6.QtGui import *
from PySide6.QtWidgets import *
from datetime import datetime

from pubby.workers import JobManager, HtmlExportRunnable

# ---------------------------------------------------------------------------
# Configuration & Constants
# ---------------------------------------------------------------------------

SHOWS = [
    {"name": "testshow", "thumbnail": None},
    {"name": "spam", "thumbnail": None},
    {"name": "iforgotwhatyoudidlastweek", "thumbnail": None},
    {"name": "ショボン", "thumbnail": None},
    {"name": "spoon", "thumbnail": None},
]

# Column indices
COL_NAME, COL_SIZE, COL_TYPE, COL_DATE, COL_CREATED, COL_STATUS, COL_PROGRESS, COL_DURATION, COL_RESOLUTION, COL_FPS, COL_CODEC = range(
    11)
COLUMNS = [
    "Name",
    "Size",
    "Type",
    "Date Modified",
    "Date Created",
    "Status",
    "Progress",
    "Duration",
    "Resolution",
    "FPS",
    "Codec",
]

# Custom roles for sort keys
SIZE_ROLE = Qt.UserRole + 10  # raw bytes (int)
DATE_ROLE = Qt.UserRole + 11  # epoch ms (int)
CREATED_ROLE = Qt.UserRole + 12  # epoch ms (int)
DURATION_ROLE = Qt.UserRole + 13  # seconds (float)
RESOLUTION_ROLE = Qt.UserRole + 14  # width (int) for sorting
FPS_ROLE = Qt.UserRole + 15  # fps (float)
CODEC_ROLE = Qt.UserRole + 16  # codec string
PATH_ROLE = Qt.UserRole + 17  # full path (str)
JOB_STATUS_ROLE = Qt.UserRole + 18  # e.g., "Pending", "Copying", "Complete", "Error"
JOB_PROGRESS_ROLE = Qt.UserRole + 19  # float: 0.0 to 1.0

# Section height constraints
SECTION_MIN_HEIGHT = 150
SECTION_MAX_HEIGHT = 640
QWIDGETSIZE_MAX = 5120

# Only probe these extensions to save time
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.insv', '.mxf', '.mkv'}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def format_duration(seconds: float) -> str:
    """Formats seconds into a human-readable duration string (e.g., 5h 23m)."""
    if seconds <= 0:
        return "N/A"

    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0 or not parts:  # Always show minutes if no other units
        parts.append(f"{minutes}m")

    return " ".join(parts)


def format_speed(bytes_per_sec: float) -> str:
    """Formats bytes per second into MB/s."""
    if bytes_per_sec <= 0:
        return "N/A"

    mbps = bytes_per_sec / (1024 * 1024)
    return f"{mbps:.2f} MB/sec"


def get_folder_size(path: str) -> int:
    total = 0
    with os.scandir(path) as it:
        for entry in it:
            if entry.is_file(follow_symlinks=False):
                total += entry.stat().st_size
            elif entry.is_dir(follow_symlinks=False):
                total += get_folder_size(entry.path)
    return total


class ProgressDelegate(QStyledItemDelegate):
    """Custom delegate to render progress bars in the Progress column."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex):
        # Only paint for the Progress column
        if index.column() != COL_PROGRESS:
            super().paint(painter, option, index)
            return

        # Get the progress value from custom role
        progress_ratio = index.data(JOB_PROGRESS_ROLE) or 0.0

        # Calculate percentage for display
        percent = int(progress_ratio * 100) if progress_ratio >= 0 else 0

        # Draw background rectangle
        bg_color = option.palette.window()
        painter.fillRect(option.rect, bg_color)

        # Draw progress bar
        if progress_ratio > 0 and progress_ratio < 1.0:
            width = int(option.rect.width() * progress_ratio)
            # Create a blue gradient for the progress bar
            from PySide6.QtGui import QLinearGradient
            gradient = QLinearGradient(option.rect.topLeft(), option.rect.topRight())
            gradient.setColorAt(0, QColor("#478CBF"))  # Darker blue
            gradient.setColorAt(1, QColor("#79B5EC"))  # Lighter blue

            painter.fillRect(option.rect.x(), option.rect.y(), width, option.rect.height(),
                             QBrush(gradient))

            # Draw percentage text centered on the progress bar
            painter.setPen(QColor("white"))
            font = QFont()
            font.setBold(True)
            painter.setFont(font)
            text_rect = QRect(option.rect.x(), option.rect.y(), width, option.rect.height())
            painter.drawText(text_rect, Qt.AlignCenter, f"{percent}%")
        elif progress_ratio >= 1.0:
            # Draw full bar with checkmark
            painter.fillRect(option.rect, QColor("#2ecc71"))  # Green for complete
            painter.setPen(QColor("white"))
            font = QFont()
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(option.rect, Qt.AlignCenter, "✓")
        else:
            # No progress or error - draw empty bar
            painter.fillRect(option.rect, QColor("#ddd"))  # Light gray for empty

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(120, 20)  # Give progress bars appropriate height


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
        text_rect = QRect(text_x, option.rect.y(), option.rect.width() - text_x + option.rect.x(), option.rect.height())

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
        self.setItemDelegate(ShowItemDelegate(self))
        self.view().setSpacing(2)
        self._populate(SHOWS)

        completer = QCompleter(self.model(), self)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.setCompleter(completer)
        self.setCurrentIndex(-1)
        self.lineEdit().clear()
        self.lineEdit().setFocusPolicy(Qt.ClickFocus)

        self._placeholder_label = QLabel("Select show", self.lineEdit())
        color = self.lineEdit().palette().color(QPalette.Text)
        color.setAlpha(128)
        self._placeholder_label.setStyleSheet(
            f"color: rgba({color.red()},{color.green()},{color.blue()},{color.alpha()}); background: transparent;"
        )
        self._placeholder_label.move(4, 0)
        self._placeholder_label.resize(self.lineEdit().size())
        self._placeholder_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.lineEdit().textChanged.connect(lambda t: self._placeholder_label.setVisible(t == ""))

    def _populate(self, shows: list[dict]):
        self.clear()
        for show in shows:
            name = show["name"]
            path = show.get("thumbnail")
            pixmap = make_thumbnail_pixmap(path) if path else make_initials_pixmap(name)
            self.addItem(name)
            self.setItemData(self.count() - 1, pixmap, Qt.DecorationRole)


# ---------------------------------------------------------------------------
# SourcePanel
# ---------------------------------------------------------------------------

class FolderSizeWorkerSignals(QObject):
    done = Signal(str, object)


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
        self.setAcceptDrops(False)
        self.setCursor(Qt.PointingHandCursor)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(Qt.gray, 1.5, Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        margin = 8
        painter.drawRoundedRect(margin, margin, self.width() - margin * 2, self.height() - margin * 2, 6, 6)
        painter.setPen(Qt.gray)
        font = QFont()
        font.setPixelSize(12)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, "Click to add or\ndrop folder here")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()


class FolderCard(QWidget):
    removed = Signal(str)
    ICON_SIZE = 48

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip(path)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(6, 6, 4, 6)
        outer.setSpacing(8)

        icon_label = QLabel()
        icon_label.setPixmap(
            QApplication.style().standardIcon(QStyle.SP_DirIcon).pixmap(self.ICON_SIZE, self.ICON_SIZE))
        icon_label.setFixedSize(self.ICON_SIZE, self.ICON_SIZE)
        outer.addWidget(icon_label)

        text_col = QVBoxLayout()
        text_col.setSpacing(2)
        self._name_label = QLabel(QFileInfo(path).fileName())
        self._name_label.setStyleSheet("font-weight: bold;")
        self._size_label = QLabel("Calculating…")
        small_font = QFont()
        small_font.setPixelSize(11)
        self._size_label.setFont(small_font)
        color = self.palette().color(QPalette.Text)
        color.setAlpha(160)
        self._size_label.setStyleSheet(f"color: rgba({color.red()},{color.green()},{color.blue()},{color.alpha()});")
        text_col.addWidget(self._name_label)
        text_col.addWidget(self._size_label)
        outer.addLayout(text_col)
        outer.addStretch()

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
            self._size_label.setStyleSheet("color: #c0392b;")
        elif num_bytes == 0:
            self._size_label.setText("Empty folder — will not be processed")
            self._size_label.setStyleSheet("color: #e67e22;")
        else:
            self._size_label.setText(format_size(num_bytes))
            self._size_label.setToolTip("")

    @property
    def resolved_size(self) -> int | None:
        return getattr(self, "_resolved_size", None)


class SourcePanel(QWidget):
    def __init__(self, parent: PublisherDialog = None):
        super().__init__(parent)
        self.parent_: PublisherDialog = parent
        self._folders: list[str] = []
        self.setAcceptDrops(True)
        self._pending_count = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(4)

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

        self._total_label = QLabel("")
        small_font = QFont()
        small_font.setPixelSize(11)
        self._total_label.setFont(small_font)
        self._total_label.setVisible(False)
        root.addWidget(self._total_label)

        self._drop_zone = DropZone()
        self._drop_zone.clicked.connect(self._browse)
        root.addWidget(self._drop_zone)

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
                    f"⚠ Skipped: '{QFileInfo(path).fileName()}' is a subfolder of '{QFileInfo(existing).fileName()}'")
                return
            if existing.startswith(path + QDir.separator()):
                self.parent_.status_bar.showMessage(
                    f"⚠ Skipped: '{QFileInfo(path).fileName()}' is a parent of '{QFileInfo(existing).fileName()}'")
                return
            if path == existing:
                self.parent_.status_bar.showMessage(f"⚠ Skipped: '{QFileInfo(path).fileName()}' is already added")
                return

        self._folders.append(path)
        card = FolderCard(path)
        card.removed.connect(self._remove_folder)
        self._card_layout.insertWidget(self._card_layout.count() - 1, card)
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
        while self._card_layout.count() > 1:
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
# FileListPanel — static QStandardItemModel replacing QFileSystemModel
# ---------------------------------------------------------------------------

FILE_PRESETS: list[tuple[str, list[str]]] = [
    ("Generic", []),
    ("RED", [".r3d"]),
    ("MXF", [".mxf"]),
    ("ARW", [".arw"]),
    ("Insta360", [".insv"]),
]


class FolderScanWorkerSignals(QObject):
    result = Signal(str, list)  # root path, list[dict]


class FolderScanWorker(QRunnable):
    """Walk a directory tree in a thread and emit a flat list of file dicts."""

    def __init__(self, root: str):
        super().__init__()
        self.root = root
        self.signals = FolderScanWorkerSignals()

    def run(self):
        rows: list[dict] = []
        try:
            for dirpath, dirnames, filenames in os.walk(self.root):
                dirnames.sort()
                rel_dir = os.path.relpath(dirpath, self.root)
                for name in sorted(filenames):
                    full = os.path.join(dirpath, name)
                    ext = os.path.splitext(name)[1].lower()
                    try:
                        st = os.stat(full)
                        ctime = getattr(st, 'st_birthtime', st.st_mtime)
                        rows.append({
                            "name": name,
                            "path": full,
                            "rel": rel_dir,
                            "size": st.st_size,
                            "mtime": st.st_mtime,
                            "ctime": ctime,
                            "ext": ext,
                        })
                    except OSError:
                        pass
        except OSError:
            pass
        self.signals.result.emit(self.root, rows)


# ---------------------------------------------------------------------------
# NEW: Media Metadata Worker (Lazy Loading)
# ---------------------------------------------------------------------------

class MediaMetaWorkerSignals(QObject):
    done = Signal(str, float, str, float, str)  # path, duration, resolution, fps, codec


class MediaMetaWorker(QRunnable):
    """Probes video files in the background and emits metadata as it finishes."""

    def __init__(self, video_paths: list[str]):
        super().__init__()
        self.video_paths = video_paths
        self.signals = MediaMetaWorkerSignals()

    def run(self):
        try:
            import ffmpeg
        except ImportError:
            return  # ffmpeg-python not installed, skip probing

        for path in self.video_paths:
            try:
                probe = ffmpeg.probe(path)
                video_stream = next((s for s in probe['streams'] if s['codec_type'] == 'video'), None)
                if video_stream:
                    fps_str = video_stream.get('r_frame_rate', '0')
                    try:
                        fps_val = float(fps_str.split('/')[0]) / float(
                            fps_str.split('/')[1]) if '/' in fps_str else float(fps_str)
                    except (ValueError, ZeroDivisionError):
                        fps_val = 0.0

                    duration = float(video_stream.get('duration', 0))
                    resolution = f"{video_stream.get('width', '?')}x{video_stream.get('height', '?')}"
                    codec = video_stream.get('codec_name', '?')

                    self.signals.done.emit(path, duration, resolution, fps_val, codec)
            except Exception:
                pass  # Skip files that fail to probe


# ---------------------------------------------------------------------------
# StaticFileModel
# ---------------------------------------------------------------------------

class StaticFileModel(QStandardItemModel):
    DIR_ICON: QIcon | None = None
    FILE_ICON: QIcon | None = None

    def __init__(self, parent=None):
        super().__init__(0, 9, parent)
        self.setHorizontalHeaderLabels(COLUMNS)
        self._path_to_item: dict[str, QStandardItem] = {}  # Cache for fast lookups

    @classmethod
    def _icons(cls):
        if cls.DIR_ICON is None:
            style = QApplication.style()
            cls.DIR_ICON = style.standardIcon(QStyle.SP_DirIcon)
            cls.FILE_ICON = style.standardIcon(QStyle.SP_FileIcon)

    def populate(self, rows: list[dict]):
        """Replace all content with the given scanned rows."""
        self._icons()
        self.beginResetModel()
        self.removeRows(0, self.rowCount())
        self._path_to_item.clear()

        dir_items: dict[str, QStandardItem] = {}

        def get_dir_item(rel: str) -> QStandardItem:
            norm = rel if rel not in (".", "") else ""
            if norm == "":
                return self.invisibleRootItem()
            if norm in dir_items:
                return dir_items[norm]

            parent_rel = os.path.dirname(norm)
            parent_item = get_dir_item(parent_rel)
            folder_name = os.path.basename(norm)

            name_item = QStandardItem(folder_name)
            name_item.setIcon(self.DIR_ICON)
            name_item.setEditable(False)

            # Initialize all 9 columns for directories
            size_item = QStandardItem("")
            size_item.setData(-1, SIZE_ROLE)
            size_item.setEditable(False)
            type_item = QStandardItem("")
            type_item.setEditable(False)
            date_item = QStandardItem("")
            date_item.setData(0, DATE_ROLE)
            date_item.setEditable(False)
            created_item = QStandardItem("")
            created_item.setData(0, CREATED_ROLE)
            created_item.setEditable(False)
            duration_item = QStandardItem("")
            duration_item.setData(0, DURATION_ROLE)
            duration_item.setEditable(False)
            resolution_item = QStandardItem("")
            resolution_item.setData(0, RESOLUTION_ROLE)
            resolution_item.setEditable(False)
            fps_item = QStandardItem("")
            fps_item.setData(0, FPS_ROLE)
            fps_item.setEditable(False)
            codec_item = QStandardItem("")
            codec_item.setData(0, CODEC_ROLE)
            codec_item.setEditable(False)

            # Initialize job-related columns
            status_item = QStandardItem("—")
            status_item.setData("Pending", JOB_STATUS_ROLE)
            status_item.setEditable(False)

            progress_item = QStandardItem("")
            progress_item.setData(0.0, JOB_PROGRESS_ROLE)
            progress_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            progress_item.setEditable(False)

            # Append to row
            parent_item.appendRow(
                [
                    name_item,
                    size_item,
                    type_item,
                    date_item,
                    created_item,
                    status_item,
                    progress_item,
                    duration_item,
                    resolution_item,
                    fps_item,
                    codec_item,
                ]
            )
            dir_items[norm] = name_item
            return name_item

        for r in rows:
            parent_item = get_dir_item(r["rel"])

            dt_str = QDateTime.fromSecsSinceEpoch(int(r["mtime"])).toString("yyyy-MM-dd HH:mm")
            epoch_ms = int(r["mtime"] * 1000)
            ctime_str = QDateTime.fromSecsSinceEpoch(int(r["ctime"])).toString("yyyy-MM-dd HH:mm")
            ctime_epoch = int(r["ctime"] * 1000)

            name_item = QStandardItem(r["name"])
            name_item.setIcon(self.FILE_ICON)
            name_item.setEditable(False)
            name_item.setToolTip(r["path"])
            name_item.setData(r["path"], PATH_ROLE)  # Store path for lazy lookup
            self._path_to_item[r["path"]] = name_item

            size_item = QStandardItem(format_size(r["size"]))
            size_item.setData(r["size"], SIZE_ROLE)
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            size_item.setEditable(False)

            type_item = QStandardItem(r["ext"])
            type_item.setEditable(False)

            date_item = QStandardItem(dt_str)
            date_item.setData(epoch_ms, DATE_ROLE)
            date_item.setEditable(False)

            created_item = QStandardItem(ctime_str)
            created_item.setData(ctime_epoch, CREATED_ROLE)
            created_item.setEditable(False)

            # Initialize media columns to empty (will be populated by MediaMetaWorker)
            duration_item = QStandardItem("")
            duration_item.setData(0, DURATION_ROLE)
            duration_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            duration_item.setEditable(False)

            resolution_item = QStandardItem("")
            resolution_item.setData(0, RESOLUTION_ROLE)
            resolution_item.setEditable(False)

            fps_item = QStandardItem("")
            fps_item.setData(0, FPS_ROLE)
            fps_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            fps_item.setEditable(False)

            codec_item = QStandardItem("")
            codec_item.setData(0, CODEC_ROLE)
            codec_item.setEditable(False)

            # Initialize job-related columns
            status_item = QStandardItem("—")
            status_item.setData("Pending", JOB_STATUS_ROLE)
            status_item.setEditable(False)

            progress_item = QStandardItem("")
            progress_item.setData(0.0, JOB_PROGRESS_ROLE)
            progress_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            progress_item.setEditable(False)

            # Append to row
            parent_item.appendRow(
                [
                    name_item,
                    size_item,
                    type_item,
                    date_item,
                    created_item,
                    status_item,
                    progress_item,
                    duration_item,
                    resolution_item,
                    fps_item,
                    codec_item,
                ]
            )

        self.endResetModel()

    def update_media_meta(self, path: str, duration: float, resolution: str, fps: float, codec: str):
        """Update the media metadata for a specific file path."""
        name_item = self._path_to_item.get(path)
        if not name_item:
            return

        # In QStandardItemModel, root-level files return None for parent().
        # Fallback to invisibleRootItem() to safely access child columns.
        parent = name_item.parent() or self.invisibleRootItem()
        row = name_item.row()

        # Update Duration
        duration_item = parent.child(row, COL_DURATION)
        duration_item.setText(format_duration(duration))
        duration_item.setData(duration, DURATION_ROLE)

        # Update Resolution
        resolution_item = parent.child(row, COL_RESOLUTION)
        resolution_item.setText(resolution)
        width_val = int(resolution.split('x')[0]) if 'x' in resolution else 0
        resolution_item.setData(width_val, RESOLUTION_ROLE)

        # Update FPS
        fps_item = parent.child(row, COL_FPS)
        fps_item.setText(f"{fps:.2f}" if fps else "")
        fps_item.setData(fps, FPS_ROLE)

        # Update Codec
        codec_item = parent.child(row, COL_CODEC)
        codec_item.setText(codec)
        codec_item.setData(codec, CODEC_ROLE)


class StaticFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._extensions: list[str] = []
        self.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.setFilterKeyColumn(COL_NAME)

    def set_extensions(self, exts: list[str]):
        self._extensions = [e.lower() for e in exts]
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        src = self.sourceModel()
        index = src.index(source_row, COL_NAME, source_parent)
        if src.hasChildren(index):
            return True
        name = (src.data(index, Qt.DisplayRole) or "").lower()
        if self._extensions:
            if not any(name.endswith(ext) for ext in self._extensions):
                return False
        pattern = self.filterRegularExpression().pattern()
        if pattern and pattern.lower() not in name:
            return False
        return True

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        col = left.column()
        src = self.sourceModel()
        if col == COL_SIZE:
            return (src.data(left, SIZE_ROLE) or 0) < (src.data(right, SIZE_ROLE) or 0)
        if col == COL_DATE:
            return (src.data(left, DATE_ROLE) or 0) < (src.data(right, DATE_ROLE) or 0)
        if col == COL_CREATED:
            return (src.data(left, CREATED_ROLE) or 0) < (src.data(right, CREATED_ROLE) or 0)
        if col == COL_DURATION:
            return (src.data(left, DURATION_ROLE) or 0) < (src.data(right, DURATION_ROLE) or 0)
        if col == COL_RESOLUTION:
            return (src.data(left, RESOLUTION_ROLE) or 0) < (src.data(right, RESOLUTION_ROLE) or 0)
        if col == COL_FPS:
            return (src.data(left, FPS_ROLE) or 0) < (src.data(right, FPS_ROLE) or 0)
        if col == COL_CODEC:
            return (src.data(left, CODEC_ROLE) or "") < (src.data(right, CODEC_ROLE) or "")
        return super().lessThan(left, right)


class FolderSection(QWidget):
    def __init__(self, path: str, parent: FileListPanel):
        super().__init__(parent)
        self.parent_ = parent
        self.path = path
        self.job_manager = None  # Will be set by FileListPanel

        # Initial size policy: let splitter manage height, but start with preferred width
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)  # Add padding inside the section
        root.setSpacing(0)  # No gaps between header and tree

        header = QWidget()
        header.setCursor(Qt.PointingHandCursor)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(4, 4, 4, 4)
        header_layout.setSpacing(6)

        self._arrow = QLabel("▼")
        self._arrow.setFixedWidth(14)
        header_layout.addWidget(self._arrow)

        folder_name = QLabel(QFileInfo(path).fileName())
        folder_name.setStyleSheet("font-weight: bold; font-size: 12px;")
        folder_name.setToolTip(path)
        header_layout.addWidget(folder_name)
        header_layout.addStretch()

        # Add progress bar for folder-level progress
        self._progress_bar = QProgressBar()
        self._progress_bar.setFixedWidth(150)
        self._progress_bar.setMinimumSize(100, 20)
        header_layout.addWidget(self._progress_bar)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search files…")
        self._search.setFixedWidth(160)
        self._search.setClearButtonEnabled(True)
        header_layout.addWidget(self._search)

        self._preset_combo = QComboBox()
        for label, _ in FILE_PRESETS:
            self._preset_combo.addItem(label)
        self._preset_combo.setFixedWidth(100)
        header_layout.addWidget(self._preset_combo)

        self._refresh_btn = QPushButton("↺")
        self._refresh_btn.setFixedSize(24, 24)
        self._refresh_btn.setFlat(True)
        self._refresh_btn.setCursor(Qt.PointingHandCursor)
        self._refresh_btn.setToolTip("Re-scan folder")
        self._refresh_btn.clicked.connect(self._scan)
        header_layout.addWidget(self._refresh_btn)

        root.addWidget(header)  # Header is now strictly at the top of this section

        self._loading_label = QLabel("  Scanning…")
        loading_font = QFont()
        loading_font.setPixelSize(11)
        loading_font.setItalic(True)
        self._loading_label.setFont(loading_font)
        self._loading_label.setVisible(False)
        root.addWidget(self._loading_label)

        self._tree_container = QWidget()
        tree_layout = QVBoxLayout(self._tree_container)
        tree_layout.setContentsMargins(0, 0, 0, 0)  # Remove extra padding inside container

        self._model = StaticFileModel()
        self._proxy = StaticFilterProxy()
        self._proxy.setSourceModel(self._model)

        self._tree = QTreeView()
        self._tree.setModel(self._proxy)
        self._tree.setMinimumHeight(180)  # Minimum view height before scrollbars appear
        self._tree.setSortingEnabled(True)
        self._tree.sortByColumn(COL_NAME, Qt.AscendingOrder)

        # Apply custom delegate for progress column
        progress_delegate = ProgressDelegate()
        self._tree.setItemDelegateForColumn(COL_PROGRESS, progress_delegate)

        # Resize modes for all columns
        # We use ResizeToContents initially so they fit content, then make Name Interactive for manual resizing
        for col in range(9):
            self._tree.header().setSectionResizeMode(col, QHeaderView.ResizeToContents)

        self._tree.header().setSectionResizeMode(COL_STATUS, QHeaderView.Interactive)
        self._tree.header().setSectionResizeMode(COL_PROGRESS, QHeaderView.ResizeToContents)

        # Make the Name column interactive (resizable by user after initial fit)
        self._tree.header().setSectionResizeMode(COL_NAME, QHeaderView.Interactive)

        self._tree.setUniformRowHeights(True)
        tree_layout.addWidget(self._tree)

        root.addWidget(self._tree_container)  # Will expand/contract dynamically now

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        root.addWidget(line)

        header.mousePressEvent = lambda e: self._toggle_collapse()
        self._search.textChanged.connect(self._proxy.setFilterRegularExpression)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_changed)

        # In FolderSection.__init__, replace the job manager connection part with:
        if hasattr(self.parent_, 'parent_'):
            if hasattr(self.parent_.parent_, 'job_manager'):
                job_manager = self.parent_.parent_.job_manager
                self.job_manager = job_manager
                self.job_manager.file_updated.connect(self._on_file_job_update)
            else:
                print(f"DEBUG FolderSection __init__: parent_.parent_ has no job_manager attribute")
        else:
            print(f"DEBUG FolderSection __init__: self.parent_ has no parent_ attribute")

        self._scan()

    def _on_folder_progress(self, folder_path: str, progress: float):
        """Handle folder-level progress updates"""
        if folder_path == self.path:
            self._progress_bar.setValue(int(progress * 100))

    # ... rest of the existing methods remain the same

    def _toggle_collapse(self):
        collapsed = self._tree_container.isVisible()
        self._tree_container.setVisible(not collapsed)
        self._arrow.setText("▶" if collapsed else "▼")

        if collapsed:
            # Make this section very thin (just the header + line)
            self.setMinimumHeight(50)
            self.setMaximumHeight(50)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        else:
            # Restore normal behavior
            self.setMinimumHeight(150)
            self.setMaximumHeight(640)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        if self.parent():
            self.parent().updateGeometry()

    def _on_file_job_update(self, path: str, update_data: dict):
        """Slot called when a job for this file reports status/progress updates."""
        name_item = self._model._path_to_item.get(path)
        if not name_item:
            return

        # Find the row
        parent_item = name_item.parent() or self._model.invisibleRootItem()
        row = name_item.row()

        status_col = COL_STATUS
        progress_col = COL_PROGRESS

        # Update Status Column
        if 'status' in update_data:
            new_status = update_data['status']
            status_item = parent_item.child(row, status_col)
            if status_item:
                status_item.setText(new_status)
                status_item.setData(new_status, JOB_STATUS_ROLE)

                # Color coding
                if new_status == 'Complete':
                    status_item.setForeground(QColor("#2ecc71"))  # Green
                elif 'Error' in new_status:
                    status_item.setForeground(QColor("#e74c3c"))  # Red
                elif new_status == 'Copying':
                    status_item.setForeground(QColor("#3498db"))  # Blue
                else:
                    status_item.setForeground(Qt.black)

        # Update Progress Column
        if 'progress' in update_data:
            progress_ratio = update_data['progress']
            progress_item = parent_item.child(row, progress_col)
            if progress_item:
                progress_item.setData(progress_ratio, JOB_PROGRESS_ROLE)

                if progress_ratio >= 0 and progress_ratio < 1.0:
                    progress_item.setText(f"{progress_ratio * 100:.0f}%")
                elif progress_ratio >= 1.0:
                    progress_item.setText("✓")

                # Force view update
                idx = self._model.index(row, progress_col)
                self._model.dataChanged.emit(idx, idx, [Qt.DisplayRole])

        # --- NEW: Recalculate Folder Progress ---
        self._update_folder_progress()

    def _update_folder_progress(self):
        """Calculate and update the folder-level progress bar based on current file states."""
        if not hasattr(self, '_progress_bar'):
            return

        print(f"DEBUG FolderSection: _update_folder_progress called for {self.path}")

        total_size = 0.0
        transferred_size = 0.0
        file_count = 0

        # Iterate over all items in this folder's model
        root_item = self._model.invisibleRootItem()
        num_rows = root_item.rowCount()

        print(f"DEBUG FolderSection: Found {num_rows} rows in folder")

        for row in range(num_rows):
            name_item = root_item.child(row, COL_NAME)
            if not name_item:
                continue

            file_count += 1

            # Get the size from the model (assuming it's stored as raw bytes)
            size_item = root_item.child(row, COL_SIZE)
            file_size = 0
            if size_item:
                size_data = size_item.data(Qt.DisplayRole)

                # Try to parse the size - handle both numeric and formatted strings
                try:
                    if isinstance(size_data, (int, float)):
                        file_size = int(size_data)
                    elif isinstance(size_data, str):
                        # Handle formatted sizes like "2.5 GB", "123 MB", etc.
                        size_str = size_data.strip().upper()
                        if 'GB' in size_str:
                            parts = size_str.split('GB')
                            file_size = int(float(parts[0].strip()) * 1024 * 1024 * 1024)
                        elif 'MB' in size_str:
                            parts = size_str.split('MB')
                            file_size = int(float(parts[0].strip()) * 1024 * 1024)
                        elif 'KB' in size_str:
                            parts = size_str.split('KB')
                            file_size = int(float(parts[0].strip()) * 1024)
                        else:
                            # Try to parse as pure number
                            file_size = int(size_str.replace(',', ''))
                except Exception as e:
                    print(f"DEBUG FolderSection: Error parsing size for row {row}: {e}")
                    file_size = 0

            # If no valid size found, use a default weight to prevent division by zero issues
            if file_size == 0:
                # Try to get actual file size from filesystem as fallback
                try:
                    path_item = root_item.child(row, COL_NAME)
                    path_data = path_item.data(Qt.UserRole) if path_item else None
                    if path_data and os.path.exists(path_data):
                        file_size = os.path.getsize(path_data)
                    else:
                        file_size = 1024 * 1024  # Default 1MB weight
                except Exception as e:
                    print(f"DEBUG FolderSection: Error getting filesystem size for row {row}: {e}")
                    file_size = 1024 * 1024  # Default 1MB weight

            total_size += file_size

            # Get the progress ratio from the Progress column item
            progress_item = root_item.child(row, COL_PROGRESS)
            if progress_item:
                progress_ratio = progress_item.data(JOB_PROGRESS_ROLE)
                if progress_ratio is not None and isinstance(progress_ratio,
                                                             (int, float)) and 0 <= progress_ratio <= 1.0:
                    transferred_size += file_size * progress_ratio

        # Calculate folder progress
        if total_size > 0:
            folder_progress = min(transferred_size / total_size, 1.0)
        else:
            # If no files or total size is 0, check if any files are copying
            if file_count > 0 and transferred_size > 0:
                folder_progress = min(file_count * 0.5 / file_count,
                                      1.0)  # Simple average if we can't calculate by size
            else:
                folder_progress = 0.0

        # Update the UI progress bar
        progress_percent = int(folder_progress * 100)
        self._progress_bar.setValue(progress_percent)

    def _update_model_item_for_path(self, path: str, update_data: dict):
        """Update the corresponding item in all FolderSection models"""
        if not self.parent_ or not hasattr(self.parent_, 'filelist_panel'):
            return

        filelist_panel = self.parent_.filelist_panel
        for i in range(filelist_panel._splitter.count()):
            widget = filelist_panel._splitter.widget(i)
            if isinstance(widget, FolderSection):
                # Update the item in this section's model
                name_item = widget._model._path_to_item.get(path)
                if name_item:
                    parent_item = name_item.parent() or widget._model.invisibleRootItem()
                    row = name_item.row()

                    # Update Status Column
                    status_col = COL_STATUS
                    progress_col = COL_PROGRESS

                    if 'status' in update_data:
                        status_item = parent_item.child(row, status_col)
                        new_status = update_data['status']

                        # Set both display text and custom role data
                        status_item.setText(new_status)
                        status_item.setData(new_status, JOB_STATUS_ROLE)

                        # Color coding
                        if new_status == 'Complete':
                            status_item.setForeground(QColor("#2ecc71"))  # Green
                            progress_item = parent_item.child(row, progress_col)
                            progress_item.setText("✓")
                            progress_item.setData(1.0, JOB_PROGRESS_ROLE)
                        elif 'Error' in new_status:
                            status_item.setForeground(QColor("#e74c3c"))  # Red
                            progress_item = parent_item.child(row, progress_col)
                            progress_item.setText("")
                            progress_item.setData(-1.0, JOB_PROGRESS_ROLE)
                        elif new_status == 'Copying':
                            status_item.setForeground(QColor("#3498db"))  # Blue
                        else:
                            status_item.setForeground(Qt.black)

                    if 'progress' in update_data:
                        progress_ratio = update_data['progress']

                        # Only update if it's a valid progress (not -1 for errors)
                        if progress_ratio >= 0:
                            progress_item = parent_item.child(row, progress_col)
                            progress_item.setData(progress_ratio, JOB_PROGRESS_ROLE)

                            if progress_ratio < 1.0:
                                progress_item.setText(f"{progress_ratio * 100:.0f}%")
                            else:
                                # Handled in 'Complete' block above, but safe fallback
                                pass

                            # Force view update to ensure delegate gets notified
                            idx = widget._model.index(row, progress_col)
                            widget._model.dataChanged.emit(idx, idx, [Qt.DisplayRole])

    def _scan(self):
        self._model.removeRows(0, self._model.rowCount())
        self._loading_label.setVisible(True)
        self._refresh_btn.setEnabled(False)
        worker = FolderScanWorker(self.path)
        worker.signals.result.connect(self._on_scan_done)
        QThreadPool.globalInstance().start(worker)

    def _on_scan_done(self, root: str, rows: list[dict]):
        if root != self.path:
            return
        self._model.populate(rows)
        self._tree.expandToDepth(0)
        self._loading_label.setVisible(False)
        self._refresh_btn.setEnabled(True)

        video_paths = [r["path"] for r in rows if r["ext"] in VIDEO_EXTENSIONS]
        if video_paths:
            meta_worker = MediaMetaWorker(video_paths)
            meta_worker.signals.done.connect(self._model.update_media_meta)
            QThreadPool.globalInstance().start(meta_worker)

    def _on_preset_changed(self, index: int):
        _, exts = FILE_PRESETS[index]
        self._proxy.set_extensions(exts)


class FileListPanel(QWidget):
    def __init__(self, parent: PublisherDialog = None):
        super().__init__(parent)
        self.parent_: PublisherDialog = parent
        self._folders: set[str] = set()

        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(0)  # No gap between splitter and edges

        self._empty_label = QLabel("Add source folders to see files.")
        self._empty_label.setAlignment(Qt.AlignCenter)
        color = self.palette().color(QPalette.Text)
        color.setAlpha(120)
        self._empty_label.setStyleSheet(
            f"color: rgba({color.red()},{color.green()},{color.blue()},{color.alpha()});"
        )
        root.addWidget(self._empty_label)

        self._splitter = QSplitter(Qt.Vertical)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setVisible(False)
        self._splitter.setHandleWidth(0)  # Zero-width handles for tight packing

        # Let splitter handle the vertical expansion naturally
        root.addWidget(self._splitter)

    def add_folder(self, path: str):
        self._folders.add(path)
        section = FolderSection(path, self)
        # Pass job manager reference directly
        if hasattr(self.parent_, 'job_manager'):
            section.job_manager = self.parent_.job_manager  # Add this attribute
        self._splitter.addWidget(section)
        self._empty_label.setVisible(False)
        self._splitter.setVisible(True)

    def remove_folder(self, path: str):
        for i in range(self._splitter.count()):
            widget = self._splitter.widget(i)
            if isinstance(widget, FolderSection) and widget.path == path:
                self._folders.discard(path)
                widget.deleteLater()
                break
        if self._splitter.count() == 0:
            self._empty_label.setVisible(True)
            self._splitter.setVisible(False)

    def clear_all(self):
        for i in range(self._splitter.count()):
            widget = self._splitter.widget(i)
            widget.deleteLater()
        self._empty_label.setVisible(True)
        self._splitter.setVisible(False)
        self._folders.clear()


# ---------------------------------------------------------------------------
# DetailsPanel / StatusBar
# ---------------------------------------------------------------------------

class DetailsPanel(QWidget):
    """Displays job details and real-time stats for the active offload."""

    def __init__(self, parent: PublisherDialog = None):
        super().__init__(parent)
        self.parent_: PublisherDialog = parent
        self._current_job: JobManager = None  # References the currently running job

        # Fonts
        self.TITLE_FONT = QFont("Segoe UI", 10, QFont.DemiBold)
        self.BODY_FONT = QFont("Segoe UI", 9)
        self.SMALL_LABEL_FONT = QFont("Segoe UI", 8, QFont.DemiBold)

        self._build_ui()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(2)

        # --- 1. Job Header Section ---
        header_container = QWidget()
        header_layout = QVBoxLayout(header_container)
        header_layout.setContentsMargins(12, 16, 12, 8)

        self._job_title = QLabel("No Active Job")
        self._job_title.setFont(self.TITLE_FONT)
        header_layout.addWidget(self._job_title)

        # Sub-labels container for job info (Size, State, Duration)
        sub_label_layout = QHBoxLayout()
        self._lbl_folder = QLabel("")
        self._lbl_state = QLabel("State:")
        self._val_state = QLabel("N/A")
        self._lbl_start = QLabel("Start:")
        self._val_start = QLabel("N/A")
        self._lbl_duration = QLabel("Duration:")
        self._val_duration = QLabel("0m")

        sub_label_layout.addWidget(self._lbl_folder)
        sub_label_layout.addStretch()
        sub_label_layout.addWidget(self._lbl_state)
        sub_label_layout.addWidget(self._val_state)
        sub_label_layout.addWidget(self._lbl_start)
        sub_label_layout.addWidget(self._val_start)
        sub_label_layout.addWidget(self._lbl_duration)
        sub_label_layout.addWidget(self._val_duration)

        header_layout.addLayout(sub_label_layout)
        root_layout.addWidget(header_container)

        # --- 2. Configuration Section ---
        config_group = QGroupBox("Offload Configuration")
        config_layout = QFormLayout(config_group)
        config_layout.setSpacing(6)
        config_layout.setLabelAlignment(Qt.AlignLeft)

        self._lbl_source = QLabel("N/A")
        self._lbl_dest_1 = QLabel("N/A")
        self._lbl_hash = QLabel("None")
        self._lbl_overwrite = QLabel("No")
        self._lbl_verify = QLabel("No")

        config_layout.addRow("Source Folder:", self._lbl_source)
        config_layout.addRow("Destination Path:", self._lbl_dest_1)
        config_layout.addRow("Hash Type:", self._lbl_hash)
        config_layout.addRow("Overwrite Existing:", self._lbl_overwrite)
        config_layout.addRow("Source Verification:", self._lbl_verify)

        root_layout.addWidget(config_group)

        # --- 3. Statistics Section ---
        stats_group = QGroupBox("Performance")
        stats_layout = QFormLayout(stats_group)
        stats_layout.setSpacing(6)

        self._lbl_avg_speed = QLabel("N/A")

        stats_layout.addRow("Average Copy Speed:", self._lbl_avg_speed)

        root_layout.addWidget(stats_group)

        # --- 4. Actions Section with Run Publish button ---
        actions_group = QGroupBox("Actions")
        actions_layout = QVBoxLayout(actions_group)

        # Run Publish button
        self._btn_run_publish = QPushButton("Run Publish")
        self._btn_run_publish.setFixedHeight(28)
        self._btn_run_publish.setCursor(Qt.PointingHandCursor)
        self._btn_run_publish.clicked.connect(self._run_publish)
        actions_layout.addWidget(self._btn_run_publish)

        # Cancel Job button
        self._btn_cancel = QPushButton("Cancel Job")
        self._btn_cancel.setFixedHeight(28)
        self._btn_cancel.setCursor(Qt.PointingHandCursor)
        self._btn_cancel.clicked.connect(self._cancel_job)
        actions_layout.addWidget(self._btn_cancel)

        self._btn_export_csv = QPushButton("Export CSV")
        self._btn_export_csv.setFixedHeight(28)
        self._btn_export_csv.setCursor(Qt.PointingHandCursor)
        self._btn_export_csv.clicked.connect(self._export_csv)
        actions_layout.addWidget(self._btn_export_csv)

        root_layout.addWidget(actions_group)

        self._btn_export_html = QPushButton("Export HTML Report")
        self._btn_export_html.setFixedHeight(28)
        self._btn_export_html.setCursor(Qt.PointingHandCursor)
        self._btn_export_html.clicked.connect(self._export_html)
        actions_layout.addWidget(self._btn_export_html)

    def _export_html(self):
        from datetime import datetime
        from PySide6.QtWidgets import QFileDialog, QProgressDialog

        job_manager = self.parent_.job_manager

        now = datetime.now()
        default_filename = f"{now.strftime('%Y-%m-%d-%H%M%S')}-report.html"

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export HTML Report",
            default_filename,
            "HTML Files (*.html);;All Files (*)"
        )

        if file_path:
            # 1. Create the progress dialog
            progress_dialog = QProgressDialog("Generating report...", "Cancel", 0, 100, self)
            progress_dialog.setWindowTitle("Exporting Report")
            progress_dialog.setWindowModality(Qt.ApplicationModal)

            # 2. Create the Runnable worker
            worker = HtmlExportRunnable(job_manager, file_path)

            # 3. Connect signals to update the UI
            worker.signals.progress.connect(progress_dialog.setValue)

            # FIXED: Capture both arguments (pct, msg) and pass only 'msg' to setLabelText
            worker.signals.progress.connect(lambda pct, msg: progress_dialog.setLabelText(msg))

            # When finished or error occurs, close the dialog
            worker.signals.finished.connect(progress_dialog.close)
            worker.signals.error.connect(progress_dialog.close)

            # Allow user to cancel (just closes the dialog; background task will finish anyway)
            progress_dialog.canceled.connect(progress_dialog.close)

            # 4. Start the job in the global thread pool
            QThreadPool.globalInstance().start(worker)

    def _export_csv(self):
        job_manager = self.parent_.job_manager

        # 1. Generate default filename based on current time (e.g., 2023-10-27-1430-report.csv)
        now = datetime.now()
        default_filename = f"{now.strftime('%Y-%m-%d-%H%M%S')}-report.csv"

        # 2. Open the "Save As" dialog
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export CSV Report",  # Window Title
            default_filename,  # Default filename suggestion
            "CSV Files (*.csv);;All Files (*)"  # File filter options
        )

        # 3. If user didn't cancel (file_path is not empty), execute the export
        if file_path:
            job_manager.export_csv(file_path)

    def update_job_title(self, job_manager: JobManager):
        """Updates the panel title and info when a new job is started."""
        self._current_job = job_manager

        if not job_manager or not job_manager.active_jobs:
            self._job_title.setText("No Active Job")
            self._lbl_folder.setText("")
            self._val_state.setText("N/A")
            self._val_duration.setText("0m")
            return

        # For this demo, we'll pull info from the first active source path in the manager
        paths = list(job_manager.active_jobs.keys())
        if not paths:
            self._job_title.setText("No Active Job")
            self._lbl_folder.setText("")
            self._val_state.setText("N/A")
            return

        sample_path = paths[0]
        folder_name = QFileInfo(sample_path).fileName()

        self._job_title.setText(f"Job: {folder_name}")
        self._lbl_folder.setText(QDir.toNativeSeparators(folder_name))

    def update_status(self, path: str, data: dict):
        """Updates UI in real-time based on JobManager signals."""
        if not self._current_job:
            return

        # Update State
        state = data.get('status', 'Copying')
        if state == 'Complete':
            self._val_state.setText("Complete")
            self._val_state.setStyleSheet("color: green; font-weight: bold;")
        elif 'Error' in state:
            self._val_state.setText("Error")
            self._val_state.setStyleSheet("color: red; font-weight: bold;")
            self._lbl_avg_speed.setText(f"Error: {state}")
        else:
            self._val_state.setText("Running")
            self._val_state.setStyleSheet("")

        # Update Speed
        speed = data.get('speed', 0)
        avg_speed = format_speed(speed)
        self._lbl_avg_speed.setText(avg_speed)

        # Update Duration (approximate based on system time for demo)
        # In a real app, you'd store the start timestamp in the JobManager
        current_time = time.time()
        if not hasattr(self, 'start_time'):
            self.start_time = current_time

        duration_secs = current_time - self.start_time
        self._val_duration.setText(format_duration(duration_secs))

    def _run_publish(self):
        """Trigger the publish/offload process for all source folders."""
        if not self.parent_ or not hasattr(self.parent_, 'job_manager'):
            return

        job_manager = self.parent_.job_manager
        source_panel = self.parent_.source_panel

        # Get all source folders
        source_folders = getattr(source_panel, '_folders', [])

        if not source_folders:
            self.parent_.status_bar.showMessage("No source folders added.", 3000)
            return

        # Get the selected show name from combo box
        show_name = "default"
        if hasattr(self.parent_, 'show_combo'):
            current_index = self.parent_.show_combo.currentIndex()
            if current_index >= 0:
                show_name = self.parent_.show_combo.currentText()

        # Update the job title before starting jobs
        self.update_job_title(job_manager)

        # Reset start time for duration tracking
        self.start_time = time.time()

        # Start jobs for each file in each source folder
        total_files = 0
        for folder_path in source_folders:
            try:
                import os
                files = []
                for item in os.listdir(folder_path):
                    full_path = os.path.join(folder_path, item)
                    if os.path.isfile(full_path):
                        files.append((item, full_path))

                files_to_process = files

                for filename, source_file in files_to_process:
                    # Create destination path preserving relative structure
                    dest_base = os.path.join(
                        "/tmp/pubby_offload",  # For dry run temp dir
                        show_name,
                        QFileInfo(folder_path).fileName()
                    )

                    dest_folder = dest_base

                    try:
                        os.makedirs(dest_folder, exist_ok=True)
                    except Exception as e:
                        self.parent_.status_bar.showMessage(f"Error creating dest folder: {e}", 3000)
                        continue

                    dest_file = os.path.join(dest_folder, filename)

                    try:
                        job_manager.start_job(source_file, dest_file)
                        total_files += 1
                        # Also update the corresponding item in all FolderSection models
                        self._update_model_item_for_path(source_file, {'status': 'Copying', 'progress': 0.0})
                    except Exception as e:
                        self.parent_.status_bar.showMessage(f"Error starting job for {filename}: {e}", 3000)

            except Exception as e:
                self.parent_.status_bar.showMessage(f"Error accessing folder {folder_path}: {e}", 3000)

        if total_files > 0:
            self.parent_.status_bar.showMessage(
                f"Started publishing {total_files} file(s).",
                3000
            )
        else:
            self.parent_.status_bar.showMessage("No files to publish.", 3000)

    def _update_model_item_for_path(self, path: str, update_data: dict):
        """Update the corresponding item in all FolderSection models"""
        if not self.parent_ or not hasattr(self.parent_, 'filelist_panel'):
            return

        filelist_panel = self.parent_.filelist_panel
        for i in range(filelist_panel._splitter.count()):
            widget = filelist_panel._splitter.widget(i)
            if isinstance(widget, FolderSection):
                # Update the item in this section's model
                name_item = widget._model._path_to_item.get(path)
                if name_item:
                    parent_item = name_item.parent() or widget._model.invisibleRootItem()
                    row = name_item.row()

                    # Update Status Column
                    status_col = COL_STATUS
                    progress_col = COL_PROGRESS

                    if 'status' in update_data:
                        status_item = parent_item.child(row, status_col)
                        status_item.setText(update_data['status'])
                        widget._model.setData(widget._model.index(row, status_col), update_data['status'],
                                              Qt.DisplayRole)

                    if 'progress' in update_data:
                        progress_item = parent_item.child(row, progress_col)
                        progress_ratio = update_data['progress']
                        progress_item.setData(progress_ratio, JOB_PROGRESS_ROLE)

                        if progress_ratio >= 1.0:
                            progress_item.setText("✓")
                        elif progress_ratio > 0:
                            progress_item.setText(f"{progress_ratio * 100:.0f}%")
                        else:
                            progress_item.setText("")

    def _cancel_job(self):
        if self._current_job:
            # In a real app, this would iterate through active_jobs and call subprocess.kill()
            # For now, we'll just clear the UI
            for path in list(self._current_job.active_jobs.keys()):
                self._current_job.stop_job(path)

            self._job_title.setText("Job Cancelled")
            self._val_state.setText("Cancelled")
            self._lbl_avg_speed.setText("N/A")
            self.parent_.status_bar.showMessage("Job cancellation requested.", 3000)


class StatusBar(QStatusBar):
    def __init__(self, parent: PublisherDialog):
        super().__init__(parent)
        self.parent_: PublisherDialog = parent
        self.setFixedHeight(28)
        self.showMessage("Ready.")


# ---------------------------------------------------------------------------
# Main dialog
# ---------------------------------------------------------------------------

class PublisherDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Pubby")
        self.resize(1100, 700)
        self.job_manager = JobManager(self)
        self._build_ui()

        # After initializing all components, ensure signals are properly connected
        for i in range(self.filelist_panel._splitter.count()):
            widget = self.filelist_panel._splitter.widget(i)
            if isinstance(widget, FolderSection):
                if self.job_manager:
                    print(f"DEBUG PublisherDialog: Explicitly connecting job_manager to folder section {widget.path}")
                    self.job_manager.file_updated.connect(widget._on_file_job_update)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 0)
        root.setSpacing(6)

        # Top row with show selection and dry run controls
        top_row = QHBoxLayout()

        self.show_combo = ShowComboBox()
        self.show_combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        top_row.addWidget(self.show_combo)

        # Add some spacing after combo box
        spacer1 = QWidget()
        spacer1.setFixedWidth(20)
        top_row.addWidget(spacer1)

        # Dry Run checkbox
        self.dry_run_checkbox = QCheckBox("Dry Run")
        top_row.addWidget(self.dry_run_checkbox)
        self.dry_run_checkbox.stateChanged.connect(self._on_dry_run_toggled)

        # Network speed radio buttons (initially hidden)
        self.speed_group = QButtonGroup()
        self.speed_1gbps = QRadioButton("1 Gbps")
        self.speed_10gbps = QRadioButton("10 Gbps")
        self.speed_10gbps.setChecked(True)  # Default to higher speed
        self.rclone_dry = QRadioButton("Rclone")

        top_row.addWidget(self.speed_1gbps)
        top_row.addWidget(self.speed_10gbps)
        top_row.addWidget(self.rclone_dry)

        self.speed_group.addButton(self.speed_1gbps, 1)
        self.speed_group.addButton(self.speed_10gbps, 2)
        self.speed_group.addButton(self.rclone_dry, 3)
        self.speed_group.buttonClicked.connect(self._on_speed_changed)

        # Initially hide radio buttons since dry run is unchecked
        self.speed_1gbps.setVisible(False)
        self.speed_10gbps.setVisible(False)
        self.rclone_dry.setVisible(False)

        top_row.addStretch()
        root.addLayout(top_row)

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

        # Connect JobManager signals to Details Panel only
        if self.job_manager:
            self.job_manager.file_updated.connect(self.details_panel.update_status)

    def _on_dry_run_toggled(self, state):
        is_checked = self.dry_run_checkbox.isChecked()

        if is_checked:
            # Show radio buttons
            self.speed_1gbps.setVisible(True)
            self.speed_10gbps.setVisible(True)
            self.rclone_dry.setVisible(True)

            # Apply the currently selected radio button's settings immediately
            checked_button = self.speed_group.checkedButton()
            if checked_button:
                self._on_speed_changed(checked_button)

            self.status_bar.showMessage("Dry Run mode enabled - simulating file copies")
        else:
            # Hide radio buttons and disable dry run mode (set to 0)
            self.speed_1gbps.setVisible(False)
            self.speed_10gbps.setVisible(False)
            self.rclone_dry.setVisible(False)

            self.job_manager.set_dry_run_mode(0)
            self.status_bar.showMessage("Live mode enabled - actual file copies will be performed")

    def _on_speed_changed(self, button):
        if button == self.speed_1gbps:
            self.job_manager.set_dry_run_mode(1)
            self.job_manager.set_network_speed(1.0)
            self.status_bar.showMessage("Network speed set to 1 Gbps (Dry Run)")
        elif button == self.speed_10gbps:
            self.job_manager.set_dry_run_mode(2)
            self.job_manager.set_network_speed(10.0)
            self.status_bar.showMessage("Network speed set to 10 Gbps (Dry Run)")
        elif button == self.rclone_dry:
            # This sets the mode to 3, which triggers the Rclone --dry-run logic in workers.py
            self.job_manager.set_dry_run_mode(3)
            self.status_bar.showMessage("Using Rclone --dry-run flags (Dry Run)")
