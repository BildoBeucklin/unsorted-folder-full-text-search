# main.py
import sys
import os
import time
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QPixmap, QFont, QIcon
from PyQt6.QtCore import qInstallMessageHandler, QTimer, Qt

# Config zuerst!
from config import qt_message_handler, LOG_FILE, resource_path

from ui import UffWindow, ModernSplashScreen, ModelLoaderThread

qInstallMessageHandler(qt_message_handler)
os.environ["QT_LOGGING_RULES"] = "qt.text.font.db=false;qt.qpa.fonts=false"

if __name__ == "__main__":
    try:
        app = QApplication(sys.argv)
        app.setFont(QFont("Segoe UI", 10))

        icon_path = resource_path("assets/uff_icon.jpeg") # <--- HIER
        if os.path.exists(icon_path):
            app_icon = QIcon(icon_path)
            app.setWindowIcon(app_icon)
        
        # SPLASH LADEN (Mit resource_path)
        banner_path = resource_path("assets/uff_banner.jpeg")
        splash_pix = QPixmap(banner_path)
        if splash_pix.isNull():
            splash_pix = QPixmap(600, 400)
            splash_pix.fill(Qt.GlobalColor.white)
            
        splash = ModernSplashScreen(splash_pix)
        splash.show()

        
        splash.set_progress(10, "Lade Konfiguration...")
        app.processEvents()
        time.sleep(0.3) # Nur für den Effekt

        splash.set_progress(30, "Verbinde Datenbank...")
        app.processEvents()
        
        # Hauptfenster erstellen (aber noch versteckt lassen)
        window = UffWindow() 
        
        splash.set_progress(50, "Lade Benutzeroberfläche...")
        app.processEvents()
        time.sleep(0.2)

        # 4. DAS SCHWERE KI-MODELL LADEN
        splash.set_progress(60, "Lade KI-Modell (das dauert kurz)...")
        app.processEvents()

        # Wir starten den Thread, aber wir müssen warten bis er fertig ist,
        # bevor wir den Splash schließen.
        loader = ModelLoaderThread()
        
        def on_loaded(model):
            splash.set_progress(100, "Fertig!")
            app.processEvents()
            time.sleep(0.5) # Kurz warten bei 100%
            
            window.on_model_loaded(model) # Modell an Fenster übergeben
            window.show() # Fenster zeigen
            splash.finish(window) # Splash schließen

        loader.model_loaded.connect(on_loaded)
        loader.start()
        
        sys.exit(app.exec())
        
    except Exception as e:
        import traceback
        print("CRITICAL MAIN CRASH:")
        print(traceback.format_exc())