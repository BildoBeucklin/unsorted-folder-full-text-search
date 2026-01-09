import sys
import os
import sqlite3
import pdfplumber
import numpy as np
import zipfile  
import io       
from sentence_transformers import SentenceTransformer, util

from rapidfuzz import process, fuzz

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLineEdit, QPushButton, QLabel, 
                             QFileDialog, QTextBrowser, QProgressBar, QMessageBox,
                             QListWidget, QListWidgetItem, QSplitter, QFrame)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QDesktopServices

if os.name == 'nt':
    base_dir = os.getenv('LOCALAPPDATA')
else:
    base_dir = os.path.join(os.path.expanduser("~"), ".local", "share")

log_dir = os.path.join(base_dir, "UFF_Search")
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

log_file_path = os.path.join(log_dir, "uff.log")

# Logger-Klasse, die alles in die Datei schreibt
class Logger(object):
    def __init__(self):
        self.log = open(log_file_path, "w", encoding="utf-8") # "w" überschreibt bei jedem Neustart

    def write(self, message):
        self.log.write(message)
        self.log.flush()  # Sofort schreiben, damit nichts verloren geht

    def flush(self):
        self.log.flush()

# stdout und stderr umleiten
sys.stdout = Logger()
sys.stderr = sys.stdout

print(f"--- START LOGGING ---")
print(f"Logfile liegt hier: {log_file_path}")

# Font-Warnungen unterdrücken
os.environ["QT_LOGGING_RULES"] = "qt.qpa.fonts.warning=false;qt.text.fonts.db.warning=false"


# --- 1. DATENBANK MANAGER (Mit Hybrid Search Scoring) ---

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

        print("Lade das semantische Modell (all-MiniLM-L6-v2)...")
        self.model = SentenceTransformer('all-MiniLM-L6-v2')
        print("Modell geladen.")

        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        # FTS-Tabelle für die Stichwortsuche
        cursor.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS documents 
            USING fts5(filename, path, content);
        """)
        # Tabelle für die Ordner
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS folders (
                path TEXT PRIMARY KEY,
                alias TEXT
            );
        """)
        # Tabelle für die Vektor-Embeddings
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
        if not query.strip(): return []
        
        # --- PHASE 1: SEMANTISCHE SUCHE (Vektor) ---
        query_embedding = self.model.encode(query, convert_to_tensor=False)
        
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute("SELECT doc_id, vec FROM embeddings")
        all_embeddings_data = cursor.fetchall()
        
        doc_ids = [item[0] for item in all_embeddings_data]
        
        if not doc_ids:
            conn.close()
            return []

        # BLOBs zurück zu Vektoren
        all_embeddings = np.array([np.frombuffer(item[1], dtype=np.float32) for item in all_embeddings_data])
        
        # Cosine Similarity (Werte zwischen -1 und 1)
        # clip auf 0, da negative Werte hier irrelevant sind
        cos_scores = util.cos_sim(query_embedding, all_embeddings)[0].numpy()
        cos_scores = np.clip(cos_scores, 0, 1) 
        
        # Map: doc_id -> Semantic Score (0.0 - 1.0)
        semantic_map = {doc_id: float(score) for doc_id, score in zip(doc_ids, cos_scores)}

        # --- PHASE 2: STICHWORTSUCHE (FTS & Fuzzy) ---
        words = query.replace('"', '').split()
        if not words: words = [query]
        
        sql_query_parts = [f'"{w}"*' for w in words]
        sql_query_string = " OR ".join(sql_query_parts)
        
        try:
            # Wir holen Kandidaten, die die Wörter enthalten
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
            # Fuzzy-Score berechnen (0 bis 100) -> normalisieren auf 0.0 - 1.0
            ratio_name = fuzz.partial_ratio(query.lower(), filename.lower())
            ratio_content = fuzz.partial_token_set_ratio(query.lower(), content[:5000].lower())
            
            best_ratio = max(ratio_name, ratio_content)
            lexical_map[doc_id] = best_ratio / 100.0

        # --- PHASE 3: HYBRID FUSION (Kombination) ---
        final_scores = {}
        
        # Gewichtung anpassen
        ALPHA = 0.65  # 65% Semantik
        BETA = 0.35   # 35% Stichwort

        for doc_id, sem_score in semantic_map.items():
            # Filter: Nur Ergebnisse mit minimaler Relevanz betrachten
            if sem_score < 0.15 and doc_id not in lexical_map:
                continue

            lex_score = lexical_map.get(doc_id, 0.0)
            
            # Hybrid Score
            hybrid_score = (sem_score * ALPHA) + (lex_score * BETA)
            
            # Bonus: Wenn beides hoch ist (Semantik UND Keyword)
            if sem_score > 0.4 and lex_score > 0.6:
                hybrid_score += 0.1
                
            final_scores[doc_id] = hybrid_score

        # --- PHASE 4: SORTIEREN & AUSGEBEN ---
        sorted_ids = sorted(final_scores.keys(), key=lambda x: final_scores[x], reverse=True)
        
        results = []
        for doc_id in sorted_ids[:50]: # Top 50 Ergebnisse
            row = cursor.execute(
                "SELECT filename, path, snippet(documents, 2, '<b>', '</b>', '...', 15) FROM documents WHERE rowid = ?", 
                (doc_id,)
            ).fetchone()
            if row:
                results.append(row)
        
        conn.close()
        return results

# --- 2. INDEXER (Mit ZIP Support & Recursion) ---

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
        """
        Liest Text aus einem Dateiobjekt (Stream) oder Pfad, basierend auf der Endung.
        Robuster gegen defekte PDF-Seiten.
        """
        ext = os.path.splitext(filename)[1].lower()
        text = ""

        try:
            if ext == ".pdf":
                # pdfplumber kann direkt Dateiobjekte (BytesIO) lesen
                try:
                    with pdfplumber.open(file_stream) as pdf:
                        for page in pdf.pages:
                            try:
                                # Versuch, Text von der einzelnen Seite zu holen
                                if page_text := page.extract_text():
                                    text += page_text + "\n"
                            except Exception as e:
                                # Wenn eine Seite defekt ist (z.B. FontBBox Fehler), überspringen wir nur diese Seite
                                print(f"Warnung: Konnte eine Seite in '{filename}' nicht lesen (übersprungen). Fehler: {e}")
                                continue
                except Exception as e:
                    # Wenn die ganze PDF nicht geöffnet werden kann
                    print(f"Warnung: PDF '{filename}' konnte nicht geöffnet werden. Fehler: {e}")
                    return None
            
            elif ext in [".txt", ".md", ".py", ".json", ".csv", ".html", ".log", ".ini", ".xml"]:
                # Wir lesen die Bytes und decodieren sie
                if hasattr(file_stream, 'read'):
                    content_bytes = file_stream.read()
                    if isinstance(content_bytes, str): 
                        # Fallback
                        with open(file_stream, 'r', encoding='utf-8', errors='ignore') as f:
                            text = f.read()
                    else:
                        text = content_bytes.decode('utf-8', errors='ignore')
                else:
                    # Echter Dateipfad
                    with open(file_stream, "r", encoding="utf-8", errors="ignore") as f:
                        text = f.read()
        except Exception as e:
            # Allgemeiner Fehler beim Lesen
            # print(f"Lese-Fehler bei {filename}: {e}")
            return None
            
        return text

    def run(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Bereinigen alter Einträge
        cursor.execute("SELECT rowid FROM documents WHERE path LIKE ?", (f"{self.folder_path}%",))
        ids_to_delete = [row[0] for row in cursor.fetchall()]
        if ids_to_delete:
            cursor.execute("DELETE FROM documents WHERE path LIKE ?", (f"{self.folder_path}%",))
            cursor.execute(f"DELETE FROM embeddings WHERE doc_id IN ({','.join('?'*len(ids_to_delete))})", ids_to_delete)
            conn.commit()

        indexed = 0
        skipped = 0
        was_cancelled = False

        # --- REKURSIVES DURCHSUCHEN ---
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

                # A. ZIP-DATEIEN BEHANDELN
                if file.lower().endswith('.zip'):
                    try:
                        with zipfile.ZipFile(file_path, 'r') as z:
                            for z_info in z.infolist():
                                if z_info.is_dir(): continue
                                
                                # Virtueller Pfad: C:\Ordner\Archiv.zip :: innen/datei.txt
                                virtual_path = f"{file_path} :: {z_info.filename}"
                                
                                with z.open(z_info) as z_file:
                                    # Inhalt in RAM laden (BytesIO)
                                    file_in_memory = io.BytesIO(z_file.read())
                                    
                                    content = self._extract_text_from_stream(file_in_memory, z_info.filename)
                                    
                                    if content and len(content.strip()) > 20:
                                        self._save_to_db(cursor, z_info.filename, virtual_path, content)
                                        indexed += 1
                    except Exception as e:
                        print(f"Zip Error {file}: {e}")
                        skipped += 1

                # B. NORMALE DATEIEN
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
        # 1. Text speichern
        cursor.execute(
            "INSERT INTO documents (filename, path, content) VALUES (?, ?, ?)", 
            (filename, path, content)
        )
        doc_id = cursor.lastrowid
        
        # 2. Embedding erstellen (Max 8000 chars)
        embedding = self.model.encode(content[:8000], convert_to_tensor=False)
        embedding_blob = embedding.tobytes()
        
        # 3. Vektor speichern
        cursor.execute("INSERT INTO embeddings (doc_id, vec) VALUES (?, ?)", (doc_id, embedding_blob))

# --- 3. UI (Unverändert) ---

class UffWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = DatabaseHandler()
        self.indexer_thread = None
        self.initUI()
        self.load_saved_folders()

    def initUI(self):
        self.setWindowTitle("UFF Text Search v5.0 (Hybrid Zip)")
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
        self.input_search.setPlaceholderText("Suche... (Hybrid: Inhalt + Keywords)")
        self.input_search.returnPressed.connect(self.perform_search)
        self.input_search.setStyleSheet("padding: 8px; font-size: 14px;")
        
        btn_go = QPushButton("Suchen")
        btn_go.setFixedWidth(100)
        btn_go.clicked.connect(self.perform_search)
        
        search_container.addWidget(self.input_search)
        search_container.addWidget(btn_go)

        self.lbl_status = QLabel("Bereit. Hybrid-Modell geladen.")
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
            # Falls es eine Datei im Zip ist, müssen wir den Link anpassen,
            # damit er zumindest das Zip öffnet.
            if " :: " in filepath:
                real_path = filepath.split(" :: ")[0]
                display_path = filepath # Zeige den virtuellen Pfad
            else:
                real_path = filepath
                display_path = filepath
            
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

    def link_clicked(self, url):
        QDesktopServices.openUrl(url)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = UffWindow()
    window.show()
    sys.exit(app.exec())