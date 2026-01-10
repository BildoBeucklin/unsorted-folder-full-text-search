# config.py
import sys
import os

# --- PFADE ---
if os.name == 'nt':
    base_dir = os.getenv('LOCALAPPDATA')
else:
    base_dir = os.path.join(os.path.expanduser("~"), ".local", "share")

APP_DATA_DIR = os.path.join(base_dir, "UFF_Search")
if not os.path.exists(APP_DATA_DIR):
    os.makedirs(APP_DATA_DIR)

DB_NAME = os.path.join(APP_DATA_DIR, "uff_index.db")
LOG_FILE = os.path.join(APP_DATA_DIR, "uff.log")

# --- LOGGING KLASSE ---
class Logger(object):
    def __init__(self):
        # "w" überschreibt bei jedem Start. Nutze "a" für anhängen (append).
        self.terminal = sys.stdout # Optional: Falls du es AUCH im Terminal sehen willst
        self.log = open(LOG_FILE, "w", encoding="utf-8")

    def write(self, message):
        # Optional: ins Terminal schreiben (auskommentieren, wenn du nur Logfile willst)
        # self.terminal.write(message) 
        
        self.log.write(message)
        self.log.flush()

    def flush(self):
        # self.terminal.flush()
        self.log.flush()

# --- AKTIVIERUNG DES LOGGERS ---
# Das passiert jetzt sofort beim Import dieser Datei!
sys.stdout = Logger()
sys.stderr = sys.stdout # Fehler auch ins Log umleiten

print(f"--- LOGGER START ---")
print(f"Logfile: {LOG_FILE}")


# --- QT MESSAGE HANDLER (Filter) ---
def qt_message_handler(mode, context, message):
    msg_lower = message.lower()
    ignore = ["qt.text.font", "qt.qpa.fonts", "opentype", "directwrite", "fontbbox", "script"]
    if any(k in msg_lower for k in ignore): return
    try:
        sys.stdout.write(f"[Qt] {message}\n")
    except: pass

# --- STYLESHEET ---
STYLESHEET = """
QMainWindow { background-color: #f4f7f6; }
QFrame#Sidebar { background-color: #2c3e50; border: none; }
QLabel#SidebarTitle { color: #ecf0f1; font-weight: bold; font-size: 16px; padding: 10px; }
QListWidget { background-color: #34495e; color: #ecf0f1; border: none; font-size: 13px; }
QListWidget::item { padding: 8px; border-bottom: 1px solid #2c3e50; }
QListWidget::item:selected { background-color: #1abc9c; color: white; }
QPushButton#SidebarBtn { background-color: #34495e; color: #bdc3c7; border: 1px solid #2c3e50; padding: 8px; text-align: left; border-radius: 4px; margin: 2px 10px; }
QPushButton#SidebarBtn:hover { background-color: #1abc9c; color: white; border: 1px solid #16a085; }
QPushButton#CancelBtn { background-color: #e74c3c; color: white; font-weight: bold; border-radius: 4px; margin: 10px; padding: 8px; }
QLineEdit { padding: 10px; border: 1px solid #bdc3c7; border-radius: 20px; font-size: 14px; background-color: white; }
QLineEdit:focus { border: 2px solid #3498db; }
QPushButton#SearchBtn { background-color: #3498db; color: white; font-weight: bold; border-radius: 20px; padding: 10px 20px; font-size: 14px; }
QPushButton#SearchBtn:hover { background-color: #2980b9; }
QScrollArea { border: none; background-color: transparent; }
QWidget#ResultsContainer { background-color: transparent; }
"""