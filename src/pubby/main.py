import sys
from PySide6.QtWidgets import QApplication
from pubby.widgets import PublisherDialog


def main():
    app = QApplication(sys.argv)
    dialog = PublisherDialog()
    dialog.exec()
    sys.exit(0)


if __name__ == "__main__":
    main()
