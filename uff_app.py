import sys
import os
import sqlite3
from pypdf import PdfReader

# NEU: Für die Fuzzy-Logik
from rapidfuzz import process, fuzz

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLineEdit, QPushButton, QLabel, 
                             QFileDialog, QTextBrowser, QProgressBar, QMessageBox,
                             QListWidget, QListWidgetItem, QSplitter, QFrame)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QDesktopServices

# --- 1. DATENBANK MANAGER (Mit Fuzzy-Ranking) ---

class DatabaseHandler:
    def __init__(self):
        # 1. Wir ermitteln den korrekten AppData Ordner für den User
        # Windows: C:\Users\Name\AppData\Local\UFF_Search
        if os.name == 'nt':
            base_dir = os.getenv('LOCALAPPDATA')
        else:
            # Mac/Linux: ~/.local/share/uff_search
            base_dir = os.path.join(os.path.expanduser("~"), ".local", "share")

        # 2. Wir erstellen unseren eigenen Unterordner
        self.app_data_dir = os.path.join(base_dir, "UFF_Search")
        
        # Falls der Ordner nicht existiert, erstellen wir ihn
        if not os.path.exists(self.app_data_dir):
            os.makedirs(self.app_data_dir)

        # 3. Der Pfad zur Datenbank
        self.db_name = os.path.join(self.app_data_dir, "uff_index.db")
        
        # Debug-Info (falls du es im Terminal testest)
        print(f"Datenbank Pfad: {self.db_name}")

        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS documents 
            USING fts5(filename, path, content);
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                path TEXT PRIMARY KEY,
                alias TEXT
            );
        """)
        conn.commit()
        conn.close()

    def add_folder(self, path):
        conn = sqlite3.connect(self.db_name)
        try:
            conn.execute("INSERT OR IGNORE INTO folders (path, alias) VALUES (?, ?)", (path, os.path.basename(path)))
            conn.commit()
            return True
        except:
            return False
        finally:
            conn.close()

    def remove_folder(self, path):
        conn = sqlite3.connect(self.db_name)
        conn.execute("DELETE FROM folders WHERE path = ?", (path,))
        conn.execute("DELETE FROM documents WHERE path LIKE ?", (f"{path}%",))
        conn.commit()
        conn.close()

    def get_folders(self):
        conn = sqlite3.connect(self.db_name)
        rows = conn.execute("SELECT path FROM folders").fetchall()
        conn.close()
        return [r[0] for r in rows]

    def search(self, query):
        if not query.strip(): return []
        
        conn = sqlite3.connect(self.db_name)
        
        # 1. Versuch: Strikte Datenbank-Suche (Schnell)
        words = query.replace('"', '').split()
        # Wir suchen nach "Wort*" -> findet Wortanfänge
        sql_query_parts = [f'"{w}"*' for w in words] 
        sql_query_string = " OR ".join(sql_query_parts)
        
        sql = """
            SELECT filename, path, snippet(documents, 2, '<b>', '</b>', '...', 15), content
            FROM documents 
            WHERE documents MATCH ? 
            LIMIT 200 
        """
        
        try:
            rows = conn.execute(sql, (sql_query_string,)).fetchall()
        except:
            rows = []
        
        # 2. Versuch (FALLBACK): Wenn DB nichts findet, laden wir ALLES
        # Das ist der "Panic Mode" für starke Tippfehler (wie "vertraaag")
        if len(rows) < 5: 
            # Wir holen einfach mal die ersten 1000 Dokumente ohne Filter
            fallback_sql = """
                SELECT filename, path, snippet(documents, 2, '<b>', '</b>', '...', 15), content
                FROM documents 
                LIMIT 1000
            """
            rows = conn.execute(fallback_sql).fetchall()
        
        conn.close()

        # 3. Python Fuzzy Re-Ranking (RapidFuzz)
        scored_results = []
        
        for filename, path, snippet, content in rows:
            # Wir berechnen Scores
            score_name = fuzz.partial_ratio(query.lower(), filename.lower())
            
            # Content-Check: Wir nehmen Content (falls snippet zu kurz ist)
            # Begrenzung auf die ersten 5000 Zeichen für Performance
            check_content = content[:5000] if content else ""
            score_content = fuzz.partial_token_set_ratio(query.lower(), check_content.lower())
            
            final_score = max(score_name, score_content)
            
            # Bonus für exakte Wort-Treffer
            if all(w.lower() in (filename + check_content).lower() for w in words):
                final_score += 10
            
            # Filter: Nur anzeigen, wenn Score halbwegs okay ist
            # Bei "vertraaag" vs "vertrag" ist der Score meist > 70
            if final_score > 55:
                scored_results.append({
                    "score": final_score,
                    "data": (filename, path, snippet)
                })

        # 4. Sortieren
        scored_results.sort(key=lambda x: x["score"], reverse=True)

        return [item["data"] for item in scored_results[:50]]

# --- 2. INDEXER (Unverändert) ---

class IndexerThread(QThread):
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int, int, bool)

    def __init__(self, folder_path, db_name="uff_index.db"):
        super().__init__()
        self.folder_path = folder_path
        self.db_name = db_name
        self.is_running = True

    def stop(self):
        self.is_running = False

    def _extract_text(self, filepath):
        ext = os.path.splitext(filepath)[1].lower()
        try:
            if ext == ".pdf":
                reader = PdfReader(filepath)
                text = ""
                for page in reader.pages:
                    if page_text := page.extract_text(): text += page_text + "\n"
                return text
            elif ext in [".txt", ".md", ".py", ".json", ".csv", ".html", ".log", ".ini", ".xml"]:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
            return None
        except:
            return None

    def run(self):
        conn = sqlite3.connect(self.db_name)
        conn.execute("DELETE FROM documents WHERE path LIKE ?", (f"{self.folder_path}%",))
        conn.commit()

        indexed = 0
        skipped = 0
        was_cancelled = False

        for root, dirs, files in os.walk(self.folder_path):
            if not self.is_running:
                was_cancelled = True
                break
            for file in files:
                if not self.is_running:
                    was_cancelled = True
                    break

                self.progress_signal.emit(f"Lese: {file}...")
                path = os.path.join(root, file)
                content = self._extract_text(path)
                
                if content and len(content.strip()) > 0:
                    conn.execute(
                        "INSERT INTO documents (filename, path, content) VALUES (?, ?, ?)", 
                        (file, path, content)
                    )
                    indexed += 1
                else:
                    skipped += 1
            if was_cancelled: break
        
        conn.commit()
        conn.close()
        self.finished_signal.emit(indexed, skipped, was_cancelled)

# --- 3. UI (Unverändert) ---

class UffWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = DatabaseHandler()
        self.indexer_thread = None
        self.initUI()
        self.load_saved_folders()

    def initUI(self):
        self.setWindowTitle("UFF Text Search v3.0 (Fuzzy)")
        self.resize(1000, 700)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # LINKS
        left_panel = QFrame()
        left_panel.setFixedWidth(250)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)

        lbl_folders = QLabel("📂 Meine Ordner")
        lbl_folders.setStyleSheet("font-weight: bold; font-size: 14px;")
        
        self.folder_list = QListWidget()
        self.folder_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)

        btn_add = QPushButton(" + Hinzufügen")
        btn_add.clicked.connect(self.add_new_folder)
        
        btn_remove = QPushButton(" - Entfernen")
        btn_remove.clicked.connect(self.delete_selected_folder)
        
        self.btn_rescan = QPushButton(" ↻ Neu scannen")
        self.btn_rescan.clicked.connect(self.rescan_selected_folder)

        self.btn_cancel = QPushButton("🛑 Abbrechen")
        self.btn_cancel.setStyleSheet("background-color: #ffcccc; color: #cc0000; font-weight: bold;")
        self.btn_cancel.clicked.connect(self.cancel_indexing)
        self.btn_cancel.hide()

        left_layout.addWidget(lbl_folders)
        left_layout.addWidget(self.folder_list)
        left_layout.addWidget(btn_add)
        left_layout.addWidget(btn_remove)
        left_layout.addStretch()
        left_layout.addWidget(self.btn_rescan)
        left_layout.addWidget(self.btn_cancel)

        # RECHTS
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        search_container = QHBoxLayout()
        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("Suchbegriff... (Fuzzy aktiv)")
        self.input_search.returnPressed.connect(self.perform_search)
        self.input_search.setStyleSheet("padding: 8px; font-size: 14px;")
        
        btn_go = QPushButton("Suchen")
        btn_go.setFixedWidth(100)
        btn_go.clicked.connect(self.perform_search)
        
        search_container.addWidget(self.input_search)
        search_container.addWidget(btn_go)

        self.lbl_status = QLabel("Bereit.")
        self.lbl_status.setStyleSheet("color: #666;")
        self.progress_bar = QProgressBar()
        self.progress_bar.hide()

        self.result_browser = QTextBrowser()
        self.result_browser.setOpenExternalLinks(False)
        self.result_browser.anchorClicked.connect(self.link_clicked)
        self.result_browser.setStyleSheet("background-color: white; border: 1px solid #ccc;")

        right_layout.addLayout(search_container)
        right_layout.addWidget(self.lbl_status)
        right_layout.addWidget(self.progress_bar)
        right_layout.addWidget(self.result_browser)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([250, 750])

        main_layout.addWidget(splitter)

    # LOGIK
    def load_saved_folders(self):
        self.folder_list.clear()
        folders = self.db.get_folders()
        for f in folders:
            item = QListWidgetItem(f)
            item.setToolTip(f)
            self.folder_list.addItem(item)

    def add_new_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Ordner wählen")
        if folder:
            if self.db.add_folder(folder):
                self.load_saved_folders()
                self.start_indexing(folder)
            else:
                QMessageBox.warning(self, "Info", "Ordner ist bereits vorhanden.")

    def delete_selected_folder(self):
        item = self.folder_list.currentItem()
        if not item: return
        path = item.text()
        if QMessageBox.question(self, "Löschen", f"Ordner entfernen?\n{path}", 
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.db.remove_folder(path)
            self.load_saved_folders()
            self.result_browser.clear()
            self.lbl_status.setText("Ordner entfernt.")

    def rescan_selected_folder(self):
        item = self.folder_list.currentItem()
        if not item:
            QMessageBox.information(self, "Info", "Bitte Ordner links auswählen.")
            return
        self.start_indexing(item.text())

    def start_indexing(self, folder):
        self.set_ui_busy(True)
        self.lbl_status.setText(f"Starte... {os.path.basename(folder)}")
        
        # HIER WAR DER FEHLER:
        # Wir müssen dem Thread explizit sagen, wo die Datenbank liegt!
        # self.db.db_name enthält den korrekten Pfad (C:\Users\...\AppData\...)
        self.indexer_thread = IndexerThread(folder, db_name=self.db.db_name)
        
        self.indexer_thread.progress_signal.connect(lambda msg: self.lbl_status.setText(msg))
        self.indexer_thread.finished_signal.connect(self.indexing_finished)
        self.indexer_thread.start()

    def cancel_indexing(self):
        if self.indexer_thread and self.indexer_thread.isRunning():
            self.lbl_status.setText("Breche ab...")
            self.indexer_thread.stop()

    def indexing_finished(self, indexed, skipped, was_cancelled):
        self.set_ui_busy(False)
        if was_cancelled:
            self.lbl_status.setText(f"Abgebrochen. ({indexed} indiziert).")
            QMessageBox.information(self, "Abbruch", f"Vorgang abgebrochen.\nBis dahin indiziert: {indexed}")
        else:
            self.lbl_status.setText(f"Fertig. {indexed} neu, {skipped} übersprungen.")
            QMessageBox.information(self, "Fertig", f"Scan abgeschlossen!\n{indexed} Dateien im Index.")

    def set_ui_busy(self, busy):
        self.input_search.setEnabled(not busy)
        self.folder_list.setEnabled(not busy)
        self.btn_rescan.setVisible(not busy)
        self.btn_cancel.setVisible(busy)
        if busy:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.show()
        else:
            self.progress_bar.hide()

    def perform_search(self):
        query = self.input_search.text()
        if not query: return
        
        # Suche ausführen (jetzt mit Fuzzy!)
        results = self.db.search(query)
        self.lbl_status.setText(f"{len(results)} relevante Treffer.")
        
        html = ""
        if not results:
            html = "<h3 style='color: gray; text-align: center; margin-top: 20px;'>Nichts gefunden.</h3>"
        
        for filename, filepath, snippet in results:
            file_url = QUrl.fromLocalFile(filepath).toString()
            html += f"""
            <div style='margin-bottom: 10px; padding: 10px; background-color: #f9f9f9; border-left: 4px solid #2980b9;'>
                <a href="{file_url}" style='font-size: 16px; font-weight: bold; color: #2980b9; text-decoration: none;'>
                    {filename}
                </a>
                <div style='color: #333; margin-top: 5px; font-family: sans-serif; font-size: 13px;'>{snippet}</div>
                <div style='color: #999; font-size: 11px; margin-top: 4px;'>{filepath}</div>
            </div>
            """
        self.result_browser.setHtml(html)

    def link_clicked(self, url):
        QDesktopServices.openUrl(url)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = UffWindow()
    window.show()
    sys.exit(app.exec())