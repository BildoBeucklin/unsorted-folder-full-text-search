import sys
import os
import sqlite3
from pypdf import PdfReader

# PyQt6 Module
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLineEdit, QPushButton, QLabel, 
                             QFileDialog, QTextBrowser, QProgressBar, QMessageBox)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QDesktopServices, QIcon

# --- 1. BACKEND (Unverändert, nur ausgelagert) ---

class DatabaseHandler:
    def __init__(self, db_name="uff_index.db"):
        self.db_name = db_name
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS documents 
            USING fts5(filename, path, content);
        """)
        conn.commit()
        conn.close()

    def search(self, query):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        safe_query = query.replace('"', '""')
        sql = """
            SELECT filename, path, snippet(documents, 2, '<b>', '</b>', '...', 15) 
            FROM documents 
            WHERE documents MATCH ? 
            ORDER BY rank LIMIT 50
        """
        try:
            results = cursor.execute(sql, (f"{safe_query}*",)).fetchall()
        except sqlite3.OperationalError:
            results = []
        conn.close()
        return results

# --- 2. WORKER THREAD (Damit die UI beim Scannen nicht einfriert) ---

class IndexerThread(QThread):
    progress_signal = pyqtSignal(str) # Sendet Text an UI
    finished_signal = pyqtSignal(int, int) # Sendet Statistiken (indexed, skipped)

    def __init__(self, folder_path, db_name="uff_index.db"):
        super().__init__()
        self.folder_path = folder_path
        self.db_name = db_name

    def _extract_text(self, filepath):
        ext = os.path.splitext(filepath)[1].lower()
        try:
            if ext == ".pdf":
                reader = PdfReader(filepath)
                text = ""
                for page in reader.pages:
                    text_page = page.extract_text()
                    if text_page: text += text_page + "\n"
                return text
            elif ext in [".txt", ".md", ".py", ".json", ".csv", ".html", ".log", ".ini"]:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
            return None
        except Exception:
            return None

    def run(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Alten Index leeren
        cursor.execute("DELETE FROM documents")
        conn.commit()

        indexed = 0
        skipped = 0

        for root, dirs, files in os.walk(self.folder_path):
            for file in files:
                self.progress_signal.emit(f"Scanne: {file}...")
                path = os.path.join(root, file)
                
                content = self._extract_text(path)
                
                if content and len(content.strip()) > 0:
                    cursor.execute(
                        "INSERT INTO documents (filename, path, content) VALUES (?, ?, ?)", 
                        (file, path, content)
                    )
                    indexed += 1
                else:
                    skipped += 1
        
        conn.commit()
        conn.close()
        self.finished_signal.emit(indexed, skipped)

# --- 3. FRONTEND (Das PyQt Fenster) ---

class UffWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = DatabaseHandler()
        self.initUI()

    def initUI(self):
        self.setWindowTitle("UFF Text Search - PyQt Edition")
        self.resize(800, 600)

        # Haupt-Container
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # --- Header ---
        title = QLabel("UFF Text Search")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #333;")
        layout.addWidget(title)

        # --- Ordner Auswahl ---
        folder_layout = QHBoxLayout()
        self.btn_folder = QPushButton("Ordner wählen")
        self.btn_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_folder.clicked.connect(self.select_folder)
        
        self.lbl_folder = QLabel("Kein Ordner gewählt")
        self.lbl_folder.setStyleSheet("color: gray; font-style: italic;")
        
        folder_layout.addWidget(self.btn_folder)
        folder_layout.addWidget(self.lbl_folder)
        folder_layout.addStretch()
        layout.addLayout(folder_layout)

        # --- Suche ---
        search_layout = QHBoxLayout()
        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("Suchbegriff eingeben und Enter drücken...")
        self.input_search.returnPressed.connect(self.perform_search)
        
        self.btn_search = QPushButton("Suchen")
        self.btn_search.clicked.connect(self.perform_search)

        search_layout.addWidget(self.input_search)
        search_layout.addWidget(self.btn_search)
        layout.addLayout(search_layout)

        # --- Status & Progress ---
        self.lbl_status = QLabel("Bereit.")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0) # Infinite Loading Animation
        self.progress_bar.hide()
        layout.addWidget(self.lbl_status)
        layout.addWidget(self.progress_bar)

        # --- Ergebnisse (Browser Engine für HTML Support) ---
        self.result_browser = QTextBrowser()
        self.result_browser.setOpenExternalLinks(False) # Wir handeln Links selbst
        self.result_browser.anchorClicked.connect(self.link_clicked)
        self.result_browser.setStyleSheet("background-color: white; padding: 10px; font-size: 14px;")
        layout.addWidget(self.result_browser)

    def select_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Ordner wählen")
        if folder:
            self.lbl_folder.setText(folder)
            self.start_indexing(folder)

    def start_indexing(self, folder):
        self.btn_folder.setEnabled(False)
        self.input_search.setEnabled(False)
        self.progress_bar.show()
        
        # Thread starten
        self.indexer_thread = IndexerThread(folder)
        self.indexer_thread.progress_signal.connect(self.update_status)
        self.indexer_thread.finished_signal.connect(self.indexing_finished)
        self.indexer_thread.start()

    def update_status(self, msg):
        self.lbl_status.setText(msg)

    def indexing_finished(self, indexed, skipped):
        self.progress_bar.hide()
        self.btn_folder.setEnabled(True)
        self.input_search.setEnabled(True)
        self.lbl_status.setText(f"Fertig! {indexed} Dateien indiziert ({skipped} übersprungen).")
        QMessageBox.information(self, "Scan beendet", f"{indexed} Dateien wurden erfolgreich indiziert.")

    def perform_search(self):
        query = self.input_search.text()
        if not query: return

        results = self.db.search(query)
        self.lbl_status.setText(f"{len(results)} Treffer gefunden.")
        
        # HTML bauen für die Anzeige
        html_content = ""
        if not results:
            html_content = "<p style='color: gray;'>Keine Ergebnisse gefunden.</p>"
        
        for filename, filepath, snippet in results:
            # Wir nutzen den Dateipfad als Link-URL
            file_url = QUrl.fromLocalFile(filepath).toString()
            
            html_content += f"""
            <div style='margin-bottom: 15px; border-bottom: 1px solid #ddd; padding-bottom: 5px;'>
                <a href="{file_url}" style='font-size: 16px; font-weight: bold; color: #2980b9; text-decoration: none;'>
                    📄 {filename}
                </a>
                <div style='color: #444; margin-top: 5px; font-family: sans-serif;'>
                    ...{snippet}...
                </div>
                <div style='color: #888; font-size: 10px; margin-top: 2px;'>
                    {filepath}
                </div>
            </div>
            """
        
        self.result_browser.setHtml(html_content)

    def link_clicked(self, url):
        # Öffnet die Datei mit dem Standard-Programm des Betriebssystems
        QDesktopServices.openUrl(url)

# --- APP START ---
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = UffWindow()
    window.show()
    sys.exit(app.exec())