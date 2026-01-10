# main.py
import sys
import os
from PyQt6.QtWidgets import QApplication, QSplashScreen
from PyQt6.QtGui import QPixmap, QFont
from PyQt6.QtCore import qInstallMessageHandler

from config import Logger, qt_message_handler, LOG_FILE
from ui import UffWindow

# 1. Logging Setup
sys.stdout = Logger()
sys.stderr = sys.stdout
print(f"--- APP START ---")
print(f"Logfile: {LOG_FILE}")

# 2. Filter für Qt Meldungen installieren
qInstallMessageHandler(qt_message_handler)
os.environ["QT_LOGGING_RULES"] = "qt.text.font.db=false;qt.qpa.fonts=false"

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Globale Schriftart
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