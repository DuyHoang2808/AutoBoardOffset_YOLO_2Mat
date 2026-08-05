"""
Main entry point for Sony Camera Control Application
"""
import sys
from PyQt5.QtWidgets import QApplication
from ui import CameraControlApp


def main():
    app = QApplication(sys.argv)
    window = CameraControlApp()
    window.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
