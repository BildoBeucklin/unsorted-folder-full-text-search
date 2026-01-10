# main.py
import sys
import os

from config import qt_message_handler, LOG_FILE

from PyQt6.QtWidgets import QApplication, QSplashScreen
from PyQt6.QtGui import QPixmap, QFont
from PyQt6.QtCore import qInstallMessageHandler

from ui import UffWindow

qInstallMessageHandler(qt_message_handler)
os.environ["QT_LOGGING_RULES"] = "qt.text.font.db=false;qt.qpa.fonts=false"

if __name__ == "__main__":
    try:
        app = QApplication(sys.argv)
        app.setFont(QFont("Segoe UI", 10))

        splash = None
        if os.path.exists("assets/uff_banner.jpeg"):
            try:
                splash = QSplashScreen(QPixmap("assets/uff_banner.jpeg"))
                splash.show()
            except: pass

        window = UffWindow(splash)
        window.show()
        window.start_model_loading()
        
        sys.exit(app.exec())
    except Exception as e:
        import traceback
        print("CRITICAL MAIN CRASH:")
        print(traceback.format_exc())