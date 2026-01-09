import sys
import os
import sqlite3
import pdfplumber
import numpy as np
from sentence_transformers import SentenceTransformer, util

# NEU: Für die Fuzzy-Logik
from rapidfuzz import process, fuzz

from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLineEdit, QPushButton, QLabel, 
                             QFileDialog, QTextBrowser, QProgressBar, QMessageBox,
                             QListWidget, QListWidgetItem, QSplitter, QFrame)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl
from PyQt6.QtGui import QDesktopServices

# --- 1. DATENBANK MANAGER (Mit Semantischer Suche) ---

class DatabaseHandler:
    def __init__(self):
        # ... (same as before)
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

        # 4. Semantisches Modell laden
        # Wir geben dem User Feedback, weil das dauern kann
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
        # NEU: Tabelle für die Vektor-Embeddings
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
        # Finde alle doc_ids, die zu dem Ordner gehören
        cursor.execute("SELECT rowid FROM documents WHERE path LIKE ?", (f"{path}%",))
        ids_to_delete = [row[0] for row in cursor.fetchall()]
        
        if ids_to_delete:
            # Lösche Einträge aus 'documents' und 'embeddings'
            cursor.execute("DELETE FROM documents WHERE path LIKE ?", (f"{path}%",))
            cursor.execute(f"DELETE FROM embeddings WHERE doc_id IN ({','.join('?'*len(ids_to_delete))})", ids_to_delete)

        # Lösche den Ordner-Eintrag selbst
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
        
        # --- PHASE 1: SEMANTISCHE SUCHE ---
        query_embedding = self.model.encode(query, convert_to_tensor=False)
        
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        cursor.execute("SELECT doc_id, vec FROM embeddings")
        all_embeddings_data = cursor.fetchall()
        
        doc_ids = [item[0] for item in all_embeddings_data]
        
        # Konvertiere BLOBs zurück zu Vektoren
        all_embeddings = np.array([np.frombuffer(item[1], dtype=np.float32) for item in all_embeddings_data])
        
        # Cosine Similarity berechnen
        semantic_scores = {}
        if len(all_embeddings) > 0:
            cos_scores = util.cos_sim(query_embedding, all_embeddings)[0].numpy()
            
            for i, score in enumerate(cos_scores):
                # Nur relevante Ergebnisse (>35% Ähnlichkeit) berücksichtigen
                if score > 0.35:
                    # Wir gewichten die semantische Suche hoch (z.B. max 100 Pkt)
                    semantic_scores[doc_ids[i]] = float(score) * 100

        # --- PHASE 2: STICHWORTSUCHE (FTS) ---
        words = query.replace('"', '').split()
        sql_query_parts = [f'"{w}"*' for w in words]
        sql_query_string = " OR ".join(sql_query_parts)
        
        sql = """
            SELECT rowid, filename, path, content
            FROM documents 
            WHERE documents MATCH ? 
            LIMIT 200
        """
        try:
            fts_rows = cursor.execute(sql, (sql_query_string,)).fetchall()
        except:
            fts_rows = []

        # --- PHASE 3: KOMBINATION & BEWERTUNG ---
        combined_scores = {}

        # Scores aus der semantischen Suche übernehmen
        for doc_id, score in semantic_scores.items():
            combined_scores[doc_id] = score

        # Scores aus der FTS-Suche hinzufügen/kombinieren
        for doc_id, filename, path, content in fts_rows:
            # Fuzzy-Score für Relevanz
            score_name = fuzz.WRatio(query.lower(), filename.lower())
            check_content = content[:5000] if content else ""
            score_content = fuzz.partial_token_set_ratio(query.lower(), check_content.lower())
            fuzzy_score = (score_name * 0.2) + (score_content * 0.8)

            # Bonus für exakte Wort-Treffer
            if all(w.lower() in (filename + check_content).lower() for w in words):
                fuzzy_score += 20
            
            # Wenn das Dokument bereits durch die semantische Suche gefunden wurde,
            # geben wir einen massiven Bonus. Ansonsten normaler Score.
            if doc_id in combined_scores:
                combined_scores[doc_id] += fuzzy_score + 50 # Bonus!
            else:
                combined_scores[doc_id] = fuzzy_score
        
        # --- PHASE 4: SORTIEREN & ERGEBNISSE HOLEN ---
        # Sortiere die doc_ids nach dem höchsten Score
        sorted_doc_ids = sorted(combined_scores.keys(), key=lambda doc_id: combined_scores[doc_id], reverse=True)
        
        # Top 50 Ergebnisse
        final_results = []
        for doc_id in sorted_doc_ids[:50]:
            # Holen der Metadaten für die Anzeige
            res = cursor.execute(
                "SELECT filename, path, snippet(documents, 2, '<b>', '</b>', '...', 15) FROM documents WHERE rowid = ?", 
                (doc_id,)
            ).fetchone()
            
            if res:
                final_results.append(res)
        
        conn.close()
        return final_results

# --- 2. INDEXER (Unverändert) ---

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

    def _extract_text(self, filepath):
        ext = os.path.splitext(filepath)[1].lower()
        try:
            if ext == ".pdf":
                with pdfplumber.open(filepath) as pdf:
                    text = ""
                    for page in pdf.pages:
                        if page_text := page.extract_text():
                            text += page_text + "\n"
                    return text
            elif ext in [".txt", ".md", ".py", ".json", ".csv", ".html", ".log", ".ini", ".xml"]:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    return f.read()
            return None
        except:
            return None

    def run(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Finde alle doc_ids, die zu dem Ordner gehören, um sie später zu löschen
        cursor.execute("SELECT rowid FROM documents WHERE path LIKE ?", (f"{self.folder_path}%",))
        ids_to_delete = [row[0] for row in cursor.fetchall()]
        
        if ids_to_delete:
            # Lösche alte Einträge aus 'documents' und 'embeddings'
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

                self.progress_signal.emit(f"Lese: {file}...")
                path = os.path.join(root, file)
                content = self._extract_text(path)
                
                if content and len(content.strip()) > 0:
                    # 1. In FTS-Tabelle einfügen
                    cursor.execute(
                        "INSERT INTO documents (filename, path, content) VALUES (?, ?, ?)", 
                        (file, path, content)
                    )
                    doc_id = cursor.lastrowid
                    
                    # 2. Embedding erstellen und in BLOB umwandeln
                    embedding = self.model.encode(content[:8192], convert_to_tensor=False)
                    embedding_blob = embedding.tobytes()
                    
                    # 3. Embedding in Tabelle einfügen
                    cursor.execute("INSERT INTO embeddings (doc_id, vec) VALUES (?, ?)", (doc_id, embedding_blob))
                    
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
        self.setWindowTitle("UFF Text Search v4.0 (Semantic)")
        self.resize(1000, 700)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # ... (UI initialisation remains the same)
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
        self.input_search.setPlaceholderText("Suchbegriff... (Semantische Suche aktiv)")
        self.input_search.returnPressed.connect(self.perform_search)
        self.input_search.setStyleSheet("padding: 8px; font-size: 14px;")
        
        btn_go = QPushButton("Suchen")
        btn_go.setFixedWidth(100)
        btn_go.clicked.connect(self.perform_search)
        
        search_container.addWidget(self.input_search)
        search_container.addWidget(btn_go)

        self.lbl_status = QLabel("Bereit. Semantisches Modell geladen.")
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
    
    # ... (Rest of UI Class)

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
        
        # Dem Thread jetzt das Modell mitgeben
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