import sys
import os
import sqlite3
import pdfplumber
import numpy as np
import zipfile  
import io       
import traceback

from sentence_transformers import SentenceTransformer, util
from rapidfuzz import process, fuzz

# Wichtige Importe für UI und Signale
from PyQt6.QtCore import qInstallMessageHandler, QtMsgType, Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLineEdit, QPushButton, QLabel, 
                             QFileDialog, QTextBrowser, QProgressBar, QMessageBox,
                             QListWidget, QListWidgetItem, QSplitter, QFrame, QSplashScreen)
from PyQt6.QtGui import QDesktopServices, QPixmap

# --- 0. LOGGING & SYSTEM-SETUP ---

if os.name == 'nt':
    base_dir = os.getenv('LOCALAPPDATA')
else:
    base_dir = os.path.join(os.path.expanduser("~"), ".local", "share")

log_dir = os.path.join(base_dir, "UFF_Search")
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

log_file_path = os.path.join(log_dir, "uff.log")

# Logger-Klasse
class Logger(object):
    def __init__(self):
        self.log = open(log_file_path, "w", encoding="utf-8")

    def write(self, message):
        self.log.write(message)
        self.log.flush()

    def flush(self):
        self.log.flush()

# stdout und stderr umleiten
sys.stdout = Logger()
sys.stderr = sys.stdout

print(f"--- START LOGGING ---")
print(f"Logfile liegt hier: {log_file_path}")

# --- QT MESSAGE HANDLER (Der Filter für C++ Errors) ---
def qt_message_handler(mode, context, message):
    """
    Fängt interne Qt-Nachrichten ab und filtert Font-Fehler heraus.
    """
    msg_lower = message.lower()
    
    # FILTER-LISTE: Erweitert basierend auf deinen Logs
    ignore_keywords = [
        "qt.text.font", 
        "qt.qpa.fonts", 
        "opentype support missing", 
        "directwrite", 
        "unable to create font", 
        "fontbbox",
        "script 66",
        "script 9",
        "script 10",
        "script 20",
        "script 32"
    ]

    # Wenn eines der Keywords vorkommt -> Nachricht ignorieren (return)
    if any(keyword in msg_lower for keyword in ignore_keywords):
        return

    # Formatierung für das Logfile
    mode_str = "INFO"
    if mode == QtMsgType.QtWarningMsg: mode_str = "WARNING"
    elif mode == QtMsgType.QtCriticalMsg: mode_str = "CRITICAL"
    elif mode == QtMsgType.QtFatalMsg: mode_str = "FATAL"
    
    # Nur relevante Nachrichten ins Log schreiben
    try:
        sys.stdout.write(f"[Qt {mode_str}] {message}\n")
    except:
        pass 

# Handler installieren (Muss VOR der App-Erstellung passieren)
qInstallMessageHandler(qt_message_handler)

# Zusätzlich Environment Variable setzen
os.environ["QT_LOGGING_RULES"] = "qt.text.font.db=false;qt.qpa.fonts=false"


# --- 1. DATENBANK MANAGER ---

class DatabaseHandler:
    def __init__(self):
        if os.name == 'nt':
            base_dir = os.getenv('LOCALAPPDATA')
        else:
            base_dir = os.path.join(os.path.expanduser("~"), ".local", "share")

        self.app_data_dir = os.path.join(base_dir, "UFF_Search")
        
        if not os.path.exists(self.app_data_dir):
            os.makedirs(self.app_data_dir)

        self.db_name = os.path.join(self.app_data_dir, "uff_index.db")
        print(f"Datenbank Pfad: {self.db_name}")
        self.model = None 

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
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS embeddings (
                doc_id INTEGER PRIMARY KEY,
                vec BLOB
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
        cursor = conn.cursor()
        cursor.execute("SELECT rowid FROM documents WHERE path LIKE ?", (f"{path}%",))
        ids_to_delete = [row[0] for row in cursor.fetchall()]
        if ids_to_delete:
            cursor.execute("DELETE FROM documents WHERE path LIKE ?", (f"{path}%",))
            cursor.execute(f"DELETE FROM embeddings WHERE doc_id IN ({','.join('?'*len(ids_to_delete))})", ids_to_delete)
        cursor.execute("DELETE FROM folders WHERE path = ?", (path,))
        conn.commit()
        conn.close()

    def get_folders(self):
        conn = sqlite3.connect(self.db_name)
        rows = conn.execute("SELECT path FROM folders").fetchall()
        conn.close()
        return [r[0] for r in rows]

    def search(self, query):
        if not query.strip() or not self.model: return []
        
        # PHASE 1: SEMANTIK
        query_embedding = self.model.encode(query, convert_to_tensor=False)
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute("SELECT doc_id, vec FROM embeddings")
        all_embeddings_data = cursor.fetchall()
        doc_ids = [item[0] for item in all_embeddings_data]
        
        if not doc_ids:
            conn.close()
            return []

        all_embeddings = np.array([np.frombuffer(item[1], dtype=np.float32) for item in all_embeddings_data])
        cos_scores = util.cos_sim(query_embedding, all_embeddings)[0].numpy()
        cos_scores = np.clip(cos_scores, 0, 1) 
        semantic_map = {doc_id: float(score) for doc_id, score in zip(doc_ids, cos_scores)}

        # PHASE 2: LEXIKALISCH
        words = query.replace('"', '').split()
        if not words: words = [query]
        sql_query_parts = [f'"{w}"*' for w in words]
        sql_query_string = " OR ".join(sql_query_parts)
        
        try:
            fts_rows = cursor.execute("""
                SELECT rowid, filename, content 
                FROM documents 
                WHERE documents MATCH ? 
                LIMIT 100
            """, (sql_query_string,)).fetchall()
        except:
            fts_rows = []

        lexical_map = {}
        for doc_id, filename, content in fts_rows:
            ratio_name = fuzz.partial_ratio(query.lower(), filename.lower())
            ratio_content = fuzz.partial_token_set_ratio(query.lower(), content[:5000].lower())
            best_ratio = max(ratio_name, ratio_content)
            lexical_map[doc_id] = best_ratio / 100.0

        # PHASE 3: HYBRID
        final_scores = {}
        ALPHA = 0.65  
        BETA = 0.35   
        for doc_id, sem_score in semantic_map.items():
            if sem_score < 0.15 and doc_id not in lexical_map:
                continue
            lex_score = lexical_map.get(doc_id, 0.0)
            hybrid_score = (sem_score * ALPHA) + (lex_score * BETA)
            if sem_score > 0.4 and lex_score > 0.6:
                hybrid_score += 0.1
            final_scores[doc_id] = hybrid_score

        # PHASE 4: SORT
        sorted_ids = sorted(final_scores.keys(), key=lambda x: final_scores[x], reverse=True)
        results = []
        for doc_id in sorted_ids[:50]: 
            row = cursor.execute(
                "SELECT filename, path, snippet(documents, 2, '<b>', '</b>', '...', 15) FROM documents WHERE rowid = ?", 
                (doc_id,)
            ).fetchone()
            if row:
                results.append(row)
        conn.close()
        return results

# --- 2. MODEL LOADER ---
class ModelLoaderThread(QThread):
    model_loaded = pyqtSignal(object)

    def run(self):
        print("Lade das semantische Modell (all-MiniLM-L6-v2)...")
        try:
            model = SentenceTransformer('all-MiniLM-L6-v2')
            print("Modell geladen.")
            self.model_loaded.emit(model)
        except Exception as e:
            print(f"Fehler beim Laden des Modells: {e}")
            self.model_loaded.emit(None)

# --- 3. INDEXER ---
class IndexerThread(QThread):
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int, int, bool)

    def __init__(self, folder_path, db_name, model):
        super().__init__()
        self.folder_path = folder_path
        self.db_name = db_name
        self.model = model
        self.is_running = True

    def stop(self):
        self.is_running = False

    def _extract_text_from_stream(self, file_stream, filename):
        ext = os.path.splitext(filename)[1].lower()
        text = ""
        try:
            if ext == ".pdf":
                try:
                    with pdfplumber.open(file_stream) as pdf:
                        for page in pdf.pages:
                            try:
                                if page_text := page.extract_text():
                                    text += page_text + "\n"
                            except Exception as e:
                                print(f"Warnung: Konnte eine Seite in '{filename}' nicht lesen. Fehler: {e}")
                                continue
                except Exception as e:
                    print(f"Warnung: PDF '{filename}' defekt. Fehler: {e}")
                    return None
            elif ext in [".txt", ".md", ".py", ".json", ".csv", ".html", ".log", ".ini", ".xml"]:
                if hasattr(file_stream, 'read'):
                    content_bytes = file_stream.read()
                    if isinstance(content_bytes, str): 
                        with open(file_stream, 'r', encoding='utf-8', errors='ignore') as f:
                            text = f.read()
                    else:
                        text = content_bytes.decode('utf-8', errors='ignore')
                else:
                    with open(file_stream, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
        except Exception as e:
            return None
        return text

    def run(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute("SELECT rowid FROM documents WHERE path LIKE ?", (f"{self.folder_path}%",))
        ids_to_delete = [row[0] for row in cursor.fetchall()]
        if ids_to_delete:
            cursor.execute("DELETE FROM documents WHERE path LIKE ?", (f"{self.folder_path}%",))
            cursor.execute(f"DELETE FROM embeddings WHERE doc_id IN ({','.join('?'*len(ids_to_delete))})", ids_to_delete)
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

                file_path = os.path.join(root, file)
                self.progress_signal.emit(f"Prüfe: {file}...")

                if file.lower().endswith('.zip'):
                    try:
                        with zipfile.ZipFile(file_path, 'r') as z:
                            for z_info in z.infolist():
                                if z_info.is_dir(): continue
                                virtual_path = f"{file_path} :: {z_info.filename}"
                                with z.open(z_info) as z_file:
                                    file_in_memory = io.BytesIO(z_file.read())
                                    content = self._extract_text_from_stream(file_in_memory, z_info.filename)
                                    if content and len(content.strip()) > 20:
                                        self._save_to_db(cursor, z_info.filename, virtual_path, content)
                                        indexed += 1
                    except Exception as e:
                        print(f"Zip Error {file}: {e}")
                        skipped += 1
                else:
                    content = self._extract_text_from_stream(file_path, file)
                    if content and len(content.strip()) > 20:
                        self._save_to_db(cursor, file, file_path, content)
                        indexed += 1
                    else:
                        skipped += 1

            if was_cancelled: break
        
        conn.commit()
        conn.close()
        self.finished_signal.emit(indexed, skipped, was_cancelled)

    def _save_to_db(self, cursor, filename, path, content):
        cursor.execute("INSERT INTO documents (filename, path, content) VALUES (?, ?, ?)", (filename, path, content))
        doc_id = cursor.lastrowid
        embedding = self.model.encode(content[:8000], convert_to_tensor=False)
        embedding_blob = embedding.tobytes()
        cursor.execute("INSERT INTO embeddings (doc_id, vec) VALUES (?, ?)", (doc_id, embedding_blob))


# --- 4. UI ---

class UffWindow(QMainWindow):
    def __init__(self, splash=None):
        super().__init__()
        self.splash = splash
        self.db = DatabaseHandler()
        self.indexer_thread = None
        self.initUI()
        self.load_saved_folders()

    def initUI(self):
        self.setWindowTitle("UFF Text Search")
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

        self.btn_add = QPushButton(" + Hinzufügen")
        self.btn_add.clicked.connect(self.add_new_folder)
        self.btn_remove = QPushButton(" - Entfernen")
        self.btn_remove.clicked.connect(self.delete_selected_folder)
        self.btn_rescan = QPushButton(" ↻ Neu scannen")
        self.btn_rescan.clicked.connect(self.rescan_selected_folder)
        self.btn_cancel = QPushButton("🛑 Abbrechen")
        self.btn_cancel.setStyleSheet("background-color: #ffcccc; color: #cc0000; font-weight: bold;")
        self.btn_cancel.clicked.connect(self.cancel_indexing)
        self.btn_cancel.hide()

        left_layout.addWidget(lbl_folders)
        left_layout.addWidget(self.folder_list)
        left_layout.addWidget(self.btn_add)
        left_layout.addWidget(self.btn_remove)
        left_layout.addStretch()
        left_layout.addWidget(self.btn_rescan)
        left_layout.addWidget(self.btn_cancel)

        # RECHTS
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        search_container = QHBoxLayout()
        self.input_search = QLineEdit()
        self.input_search.setPlaceholderText("Suche... (Hybrid: Inhalt + Keywords)")
        self.input_search.returnPressed.connect(self.perform_search)
        self.input_search.setStyleSheet("padding: 8px; font-size: 14px;")
        
        self.btn_go = QPushButton("Suchen")
        self.btn_go.setFixedWidth(100)
        self.btn_go.clicked.connect(self.perform_search)
        
        search_container.addWidget(self.input_search)
        search_container.addWidget(self.btn_go)

        self.lbl_status = QLabel("Initialisiere...")
        self.lbl_status.setStyleSheet("color: #666;")
        self.progress_bar = QProgressBar()
        self.progress_bar.hide()

        # STANDARD BROWSER MIT RICHTIGEN EINSTELLUNGEN
        self.result_browser = QTextBrowser()
        # WICHTIG: Interne Links deaktivieren, damit wir sie abfangen können
        self.result_browser.setOpenExternalLinks(False) 
        # Wenn wir darauf klicken, wird unser Slot aufgerufen
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
        self.set_main_ui_enabled(False)

    def set_main_ui_enabled(self, enabled):
        self.input_search.setEnabled(enabled)
        self.btn_go.setEnabled(enabled)
        self.folder_list.setEnabled(enabled)
        self.btn_add.setEnabled(enabled)
        self.btn_remove.setEnabled(enabled)
        self.btn_rescan.setEnabled(enabled)

    def start_model_loading(self):
        if self.splash:
            self.splash.showMessage("Lade semantisches Modell...", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, Qt.GlobalColor.white)
        self.model_loader = ModelLoaderThread()
        self.model_loader.model_loaded.connect(self.on_model_loaded)
        self.model_loader.start()

    def on_model_loaded(self, model):
        if self.splash:
            self.splash.showMessage("Modell geladen. Starte Benutzeroberfläche...", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, Qt.GlobalColor.white)
        
        if model is None:
            self.lbl_status.setText("Fehler: Modell konnte nicht geladen werden.")
            QMessageBox.critical(self, "Kritischer Fehler", "Das semantische Modell konnte nicht geladen werden.")
            self.close()
        else:
            self.db.model = model
            self.lbl_status.setText("Bereit. Hybrid-Modell geladen.")
            self.set_main_ui_enabled(True)
        
        if self.splash:
            self.splash.finish(self)

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
        if not self.db.model:
            QMessageBox.warning(self, "Bitte warten", "Das Suchmodell wird noch geladen.")
            return

        self.set_ui_busy(True)
        self.lbl_status.setText(f"Starte... {os.path.basename(folder)}")
        
        self.indexer_thread = IndexerThread(folder, db_name=self.db.db_name, model=self.db.model)
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
        self.btn_add.setEnabled(not busy)
        self.btn_remove.setEnabled(not busy)
        self.btn_go.setEnabled(not busy)
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
        
        self.lbl_status.setText("Suche läuft...")
        QApplication.processEvents()

        results = self.db.search(query)
        self.lbl_status.setText(f"{len(results)} relevante Treffer.")
        
        html = ""
        if not results:
            html = "<h3 style='color: gray; text-align: center; margin-top: 20px;'>Nichts gefunden.</h3>"
        
        for filename, filepath, snippet in results:
            if " :: " in filepath:
                real_path = filepath.split(" :: ")[0]
                display_path = filepath
            else:
                real_path = filepath
                display_path = filepath
            
            # Link für QTextBrowser
            file_url = QUrl.fromLocalFile(real_path).toString()
            
            html += f"""
            <div style='margin-bottom: 10px; padding: 10px; background-color: #f9f9f9; border-left: 4px solid #2980b9;'>
                <a href="{file_url}" style='font-size: 16px; font-weight: bold; color: #2980b9; text-decoration: none;'>
                    {filename}
                </a>
                <div style='color: #333; margin-top: 5px; font-family: sans-serif; font-size: 13px;'>{snippet}</div>
                <div style='color: #999; font-size: 11px; margin-top: 4px;'>{display_path}</div>
            </div>
            """
        self.result_browser.setHtml(html)

    # --- DIE FUNKTION ZUM ÖFFNEN DER LINKS ---
    def link_clicked(self, url):
        print(f"Versuche zu öffnen: {url.toString()}")
        QDesktopServices.openUrl(url)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    splash = None
    try:
        pixmap = QPixmap("assets/uff_banner.jpeg")
        splash = QSplashScreen(pixmap)
        splash.show()
        splash.showMessage("Initialisiere Anwendung...", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, Qt.GlobalColor.white)
    except:
        pass

    app.processEvents()

    window = UffWindow(splash)
    window.show()
    window.start_model_loading()
    
    sys.exit(app.exec())