# ui.py
import os
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLineEdit, QPushButton, QLabel, QFileDialog, 
                             QProgressBar, QMessageBox, QListWidget, QListWidgetItem, 
                             QSplitter, QFrame, QScrollArea, QStyle, QGraphicsDropShadowEffect,
                             QSplashScreen) # QSplashScreen hier wichtig
from PyQt6.QtCore import Qt, QUrl, QThread, pyqtSignal, QRect
from PyQt6.QtGui import QDesktopServices, QColor, QFont, QPainter, QIcon, QPixmap # Painter & Icon neu
from sentence_transformers import SentenceTransformer

from database import DatabaseHandler
from indexer import IndexerThread
from config import STYLESHEET

# --- NEU: Ein moderner Splash Screen mit Ladebalken ---
class ModernSplashScreen(QSplashScreen):
    def __init__(self, pixmap):
        super().__init__(pixmap)
        self.progress = 0
        self.message = "Initialisiere..."
        # Schriftart für den Ladetext
        self.font = QFont("Segoe UI", 10, QFont.Weight.Bold)

    def set_progress(self, value, text):
        self.progress = value
        self.message = text
        self.repaint() # Erzwingt neuzeichnen

    def drawContents(self, painter):
        # 1. Das normale Bild zeichnen
        super().drawContents(painter)

        # 2. Ladebalken-Hintergrund (unten)
        # Wir malen direkt auf das Bild
        bg_rect = self.rect()
        bar_height = 20
        # Position: Ganz unten am Bild
        bar_rect = QRect(0, bg_rect.height() - bar_height, bg_rect.width(), bar_height)
        
        # Hintergrund des Balkens (dunkelgrau)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(50, 50, 50))
        painter.drawRect(bar_rect)

        # 3. Der Fortschritt (türkis/blau)
        # Breite basierend auf % berechnen
        progress_width = int(bg_rect.width() * (self.progress / 100))
        prog_rect = QRect(0, bg_rect.height() - bar_height, progress_width, bar_height)
        
        painter.setBrush(QColor("#3498db")) # UFF-Blau
        painter.drawRect(prog_rect)

        # 4. Text zeichnen (zentriert über dem Balken oder darin)
        painter.setPen(QColor("white"))
        painter.setFont(self.font)
        # Text etwas oberhalb des Balkens zeichnen
        text_rect = QRect(0, bg_rect.height() - bar_height - 30, bg_rect.width(), 25)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.message)

# --- Thread zum Laden des Modells ---
class ModelLoaderThread(QThread):
    model_loaded = pyqtSignal(object)
    
    def run(self):
        try:
            # Das ist der schwere Teil, der dauert
            model = SentenceTransformer('all-MiniLM-L6-v2')
            self.model_loaded.emit(model)
        except: 
            self.model_loaded.emit(None)

# --- SearchResultItem (Unverändert, aber der Vollständigkeit halber hier) ---
class SearchResultItem(QFrame):
    def __init__(self, filename, filepath, snippet, parent=None):
        super().__init__(parent)
        self.filepath = filepath
        self.setToolTip(filepath)
        
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            SearchResultItem { background-color: white; border: 1px solid #e0e0e0; border-radius: 8px; }
            SearchResultItem:hover { border: 1px solid #3498db; background-color: #fbfbfb; }
        """)
        
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(10)
        shadow.setXOffset(0)
        shadow.setYOffset(2)
        shadow.setColor(QColor(0, 0, 0, 30))
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(5)
        
        self.btn_title = QPushButton(filename)
        self.btn_title.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_title.setMouseTracking(True)
        self.btn_title.setStyleSheet("""
            QPushButton { text-align: left; font-weight: bold; font-size: 16px; color: #2c3e50; border: none; background: transparent; padding: 0px; }
            QPushButton:hover { color: #3498db; text-decoration: underline; }
        """)
        self.btn_title.clicked.connect(self.open_file)
        
        self.lbl_snippet = QLabel(snippet)
        self.lbl_snippet.setWordWrap(True)
        self.lbl_snippet.setStyleSheet("color: #555; font-size: 13px; line-height: 1.4;")
        
        path_layout = QHBoxLayout()
        lbl_icon = QLabel("📄") 
        lbl_icon.setStyleSheet("font-size: 10px; color: #95a5a6;")
        
        self.lbl_path = QLabel(filepath)
        self.lbl_path.setStyleSheet("color: #95a5a6; font-size: 11px;")
        
        path_layout.addWidget(lbl_icon)
        path_layout.addWidget(self.lbl_path)
        path_layout.addStretch()
        
        layout.addWidget(self.btn_title)
        layout.addWidget(self.lbl_snippet)
        layout.addLayout(path_layout)
    
    def open_file(self):
        target = self.filepath.split(" :: ")[0] if " :: " in self.filepath else self.filepath
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))

# --- Das Hauptfenster ---
class UffWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = DatabaseHandler()
        self.initUI()
        
       
        self.load_saved_folders()

    def initUI(self):
        self.setWindowTitle("UFF Search v1.0")
        self.resize(1100, 750)
        self.setStyleSheet(STYLESHEET)
        
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # -- SIDEBAR --
        left_panel = QFrame()
        left_panel.setObjectName("Sidebar")
        left_panel.setFixedWidth(260)
        left = QVBoxLayout(left_panel)
        left.setContentsMargins(0, 20, 0, 20)
        
        lbl_title = QLabel(" UFF SEARCH")
        lbl_title.setObjectName("SidebarTitle")
        
        self.folder_list = QListWidget()
        
        btn_add = QPushButton(" Ordner hinzufügen")
        btn_add.setObjectName("SidebarBtn")
        btn_add.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogNewFolder))
        btn_add.clicked.connect(self.add_new_folder)
        
        btn_del = QPushButton(" Ordner entfernen")
        btn_del.setObjectName("SidebarBtn")
        btn_del.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        btn_del.clicked.connect(self.delete_selected_folder)
        
        self.btn_rescan = QPushButton(" Neu scannen")
        self.btn_rescan.setObjectName("SidebarBtn")
        self.btn_rescan.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        self.btn_rescan.clicked.connect(self.rescan)
        
        self.btn_cancel = QPushButton("STOPPEN")
        self.btn_cancel.setObjectName("CancelBtn")
        self.btn_cancel.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCancelButton))
        self.btn_cancel.clicked.connect(self.cancel_idx)
        self.btn_cancel.hide()

        left.addWidget(lbl_title)
        left.addSpacing(10)
        left.addWidget(self.folder_list)
        left.addSpacing(10)
        left.addWidget(btn_add)
        left.addWidget(btn_del)
        left.addWidget(self.btn_rescan)
        left.addWidget(self.btn_cancel)

        # -- MAIN AREA --
        right_panel = QWidget()
        right_panel.setObjectName("MainArea")
        right = QVBoxLayout(right_panel)
        right.setContentsMargins(30, 30, 30, 30)
        right.setSpacing(15)
        
        search_box = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Wonach suchst du heute?")
        self.input.returnPressed.connect(self.search)
        
        self.btn_go = QPushButton("Suchen")
        self.btn_go.setObjectName("SearchBtn")
        self.btn_go.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_go.clicked.connect(self.search)
        
        search_box.addWidget(self.input)
        search_box.addWidget(self.btn_go)

        status_box = QHBoxLayout()
        self.lbl_status = QLabel("Bereit.")
        self.lbl_status.setObjectName("StatusLabel")
        self.prog = QProgressBar()
        self.prog.hide()
        status_box.addWidget(self.lbl_status)
        status_box.addWidget(self.prog)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.res_cont = QWidget()
        self.res_cont.setObjectName("ResultsContainer")
        self.res_layout = QVBoxLayout(self.res_cont)
        self.res_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.res_layout.setSpacing(15)
        self.scroll.setWidget(self.res_cont)

        right.addLayout(search_box)
        right.addLayout(status_box)
        right.addWidget(self.scroll)

        main_layout.addWidget(left_panel)
        main_layout.addWidget(right_panel)
        self.set_ui_enabled(False)

    def set_ui_enabled(self, enabled):
        self.input.setEnabled(enabled)
        self.btn_go.setEnabled(enabled)
        self.folder_list.setEnabled(enabled)
    
    # Methoden für Model Loading (wird jetzt von main gesteuert)
    def on_model_loaded(self, model):
        if not model:
            QMessageBox.critical(self, "Fehler", "Modell konnte nicht geladen werden.")
            return
        self.db.model = model
        self.lbl_status.setText("Bereit für deine Suche.")
        self.set_ui_enabled(True)

    # ... RESTLICHE METHODEN (search, add_folder etc.) bleiben gleich wie vorher ...
    # (Kopiere hier einfach die Methoden aus deiner alten ui.py rein, 
    # search, load_saved_folders, add_new_folder, delete_selected_folder, rescan, start_idx, cancel_idx, idx_done)
    
    def search(self):
        query = self.input.text()
        if not query: return
        self.lbl_status.setText("Suche läuft...")
        QApplication.processEvents()

        while self.res_layout.count():
            child = self.res_layout.takeAt(0)
            if child.widget(): child.widget().deleteLater()

        results = self.db.search(query)
        self.lbl_status.setText(f"{len(results)} Treffer gefunden.")

        if not results:
            lbl = QLabel("Leider keine Ergebnisse.")
            lbl.setStyleSheet("color: #95a5a6; font-size: 18px; margin-top: 40px;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.res_layout.addWidget(lbl)
        else:
            for fname, fpath, snippet in results:
                self.res_layout.addWidget(SearchResultItem(fname, fpath, snippet))
        self.res_layout.addStretch()

    def load_saved_folders(self):
        self.folder_list.clear()
        for f in self.db.get_folders():
            item = QListWidgetItem(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon), f)
            item.setToolTip(f)
            self.folder_list.addItem(item)

    def add_new_folder(self):
        f = QFileDialog.getExistingDirectory(self, "Ordner wählen")
        if f and self.db.add_folder(f):
            self.load_saved_folders()
            self.start_idx(f)

    def delete_selected_folder(self):
        item = self.folder_list.currentItem()
        if item and QMessageBox.question(self, "Löschen", f"Weg damit?\n{item.text()}", QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.db.remove_folder(item.text())
            self.load_saved_folders()

    def rescan(self):
        if item := self.folder_list.currentItem(): self.start_idx(item.text())

    def start_idx(self, folder):
        if not self.db.model: return
        self.set_ui_enabled(False)
        self.btn_cancel.show(); self.btn_rescan.hide(); self.prog.show()
        self.idx_thread = IndexerThread(folder, self.db.db_name, self.db.model)
        self.idx_thread.progress_signal.connect(self.lbl_status.setText)
        self.idx_thread.finished_signal.connect(self.idx_done)
        self.idx_thread.start()

    def cancel_idx(self):
        if self.idx_thread: self.idx_thread.stop()

    def idx_done(self, n, s, c):
        self.set_ui_enabled(True)
        self.btn_cancel.hide(); self.btn_rescan.show(); self.prog.hide()
        msg = "Abgebrochen" if c else "Indexierung fertig"
        self.lbl_status.setText(f"{msg}: {n} neu, {s} übersprungen.")