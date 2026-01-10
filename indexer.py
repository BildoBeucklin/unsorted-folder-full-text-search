# indexer.py
import os
import sqlite3
import pdfplumber
import zipfile
import io
from PyQt6.QtCore import QThread, pyqtSignal

# Importe optionaler Libraries
try: import docx
except ImportError: docx = None
try: import openpyxl
except ImportError: openpyxl = None
try: from pptx import Presentation
except ImportError: Presentation = None

class IndexerThread(QThread):
    progress_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(int, int, bool)

    def __init__(self, folder, db_name, model):
        super().__init__()
        self.folder_path = folder
        self.db_name = db_name
        self.model = model
        self.is_running = True

    def stop(self): self.is_running = False

    def _extract_text(self, stream, filename):
        ext = os.path.splitext(filename)[1].lower()
        text = ""
        try:
            if ext == ".pdf":
                try:
                    with pdfplumber.open(stream) as pdf:
                        for p in pdf.pages:
                            if t := p.extract_text(): text += t + "\n"
                except: pass
            
            elif ext == ".docx" and docx:
                try:
                    doc = docx.Document(stream)
                    for para in doc.paragraphs: text += para.text + "\n"
                except: pass

            elif ext == ".xlsx" and openpyxl:
                try:
                    wb = openpyxl.load_workbook(stream, data_only=True, read_only=True)
                    for sheet in wb.worksheets:
                        text += f"\n--- {sheet.title} ---\n"
                        for row in sheet.iter_rows(values_only=True):
                            row_text = " ".join([str(c) for c in row if c is not None])
                            if row_text.strip(): text += row_text + "\n"
                except: pass

            elif ext == ".pptx" and Presentation:
                try:
                    prs = Presentation(stream)
                    for i, slide in enumerate(prs.slides):
                        text += f"\n--- Folie {i+1} ---\n"
                        for shape in slide.shapes:
                            if shape.has_text_frame:
                                for p in shape.text_frame.paragraphs:
                                    for r in p.runs: text += r.text + " "
                                    text += "\n"
                except: pass

            elif ext in [".txt", ".md", ".py", ".json", ".csv", ".html", ".log", ".ini", ".xml"]:
                try:
                    content = stream.read()
                    if isinstance(content, str): text = content
                    else: text = content.decode('utf-8', errors='ignore')
                except: pass
        except: pass
        return text

    def run(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        
        # Cleanup old entries
        cursor.execute("SELECT rowid FROM documents WHERE path LIKE ?", (f"{self.folder_path}%",))
        ids = [r[0] for r in cursor.fetchall()]
        if ids:
            cursor.execute("DELETE FROM documents WHERE path LIKE ?", (f"{self.folder_path}%",))
            cursor.execute(f"DELETE FROM embeddings WHERE doc_id IN ({','.join('?'*len(ids))})", ids)
            conn.commit()

        indexed = 0
        skipped = 0
        cancelled = False

        for root, dirs, files in os.walk(self.folder_path):
            if not self.is_running: cancelled = True; break
            for file in files:
                if not self.is_running: cancelled = True; break
                path = os.path.join(root, file)
                self.progress_signal.emit(f"Prüfe: {file}...")

                if file.lower().endswith('.zip'):
                    try:
                        with zipfile.ZipFile(path, 'r') as z:
                            for zi in z.infolist():
                                if zi.is_dir(): continue
                                vpath = f"{path} :: {zi.filename}"
                                with z.open(zi) as zf:
                                    content = self._extract_text(io.BytesIO(zf.read()), zi.filename)
                                    if content and len(content.strip()) > 20:
                                        self._save(cursor, zi.filename, vpath, content)
                                        indexed += 1
                    except: skipped += 1
                else:
                    try:
                        with open(path, "rb") as f:
                            file_content = io.BytesIO(f.read())
                            content = self._extract_text(file_content, file)
                        if content and len(content.strip()) > 20:
                            self._save(cursor, file, path, content)
                            indexed += 1
                        else: skipped += 1
                    except: skipped += 1

            if cancelled: break
        
        conn.commit()
        conn.close()
        self.finished_signal.emit(indexed, skipped, cancelled)

    def _save(self, cursor, fname, path, content):
        cursor.execute("INSERT INTO documents (filename, path, content) VALUES (?, ?, ?)", (fname, path, content))
        did = cursor.lastrowid
        vec = self.model.encode(content[:8000], convert_to_tensor=False).tobytes()
        cursor.execute("INSERT INTO embeddings (doc_id, vec) VALUES (?, ?)", (did, vec))