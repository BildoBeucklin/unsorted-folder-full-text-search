# database.py
import sqlite3
import os
import numpy as np
import traceback  # WICHTIG: Damit wir den vollen Fehler sehen
from sentence_transformers import util
from rapidfuzz import fuzz
from config import DB_NAME, APP_DATA_DIR

class DatabaseHandler:
    def __init__(self):
        self.app_data_dir = APP_DATA_DIR
        self.db_name = DB_NAME
        self.model = None 
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute("CREATE VIRTUAL TABLE IF NOT EXISTS documents USING fts5(filename, path, content);")
        cursor.execute("CREATE TABLE IF NOT EXISTS folders (path TEXT PRIMARY KEY, alias TEXT);")
        cursor.execute("CREATE TABLE IF NOT EXISTS embeddings (doc_id INTEGER PRIMARY KEY, vec BLOB);")
        conn.commit()
        conn.close()

    def add_folder(self, path):
        conn = sqlite3.connect(self.db_name)
        try:
            conn.execute("INSERT OR IGNORE INTO folders (path, alias) VALUES (?, ?)", (path, os.path.basename(path)))
            conn.commit()
            return True
        except: return False
        finally: conn.close()

    def remove_folder(self, path):
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute("SELECT rowid FROM documents WHERE path LIKE ?", (f"{path}%",))
        ids = [row[0] for row in cursor.fetchall()]
        if ids:
            cursor.execute("DELETE FROM documents WHERE path LIKE ?", (f"{path}%",))
            cursor.execute(f"DELETE FROM embeddings WHERE doc_id IN ({','.join('?'*len(ids))})", ids)
        cursor.execute("DELETE FROM folders WHERE path = ?", (path,))
        conn.commit()
        conn.close()

    def get_folders(self):
        conn = sqlite3.connect(self.db_name)
        rows = conn.execute("SELECT path FROM folders").fetchall()
        conn.close()
        return [r[0] for r in rows]

    def search(self, query):
        # Sicherheitscheck
        if not query.strip() or not self.model: 
            return []
        
        try:
            # 1. Semantische Vorbereitung
            q_vec = self.model.encode(query, convert_to_tensor=False)
            
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            # Embeddings laden
            cursor.execute("SELECT doc_id, vec FROM embeddings")
            data = cursor.fetchall()
            doc_ids = [d[0] for d in data]
            
            if not doc_ids:
                conn.close()
                return []

            # Umwandlung BLOB -> Numpy Array
            # Hier knallt es oft, wenn die DB korrupt ist oder Dimensionen nicht passen
            vecs = np.array([np.frombuffer(d[1], dtype=np.float32) for d in data])
            
            # Cosine Similarity berechnen
            scores = util.cos_sim(q_vec, vecs)[0].numpy()
            scores = np.clip(scores, 0, 1)
            sem_map = {did: float(s) for did, s in zip(doc_ids, scores)}

            # 2. Lexikalische Suche (FTS)
            words = query.replace('"', '').split()
            if not words: words = [query]
            fts_query = " OR ".join([f'"{w}"*' for w in words])
            
            try:
                fts_rows = cursor.execute("SELECT rowid, filename, content FROM documents WHERE documents MATCH ? LIMIT 100", (fts_query,)).fetchall()
            except Exception as e:
                print(f"FTS Fehler (ignoriert): {e}")
                fts_rows = []

            lex_map = {}
            for did, fname, content in fts_rows:
                r1 = fuzz.partial_ratio(query.lower(), fname.lower())
                # Content kürzen für Performance
                r2 = fuzz.partial_token_set_ratio(query.lower(), content[:5000].lower())
                lex_map[did] = max(r1, r2) / 100.0

            # 3. Hybrid Fusion
            final = {}
            ALPHA = 0.65
            BETA = 0.35
            for did, s_score in sem_map.items():
                if s_score < 0.15 and did not in lex_map: continue
                l_score = lex_map.get(did, 0.0)
                h_score = (s_score * ALPHA) + (l_score * BETA)
                # Kleiner Boost wenn beides passt
                if s_score > 0.4 and l_score > 0.6: h_score += 0.1
                final[did] = h_score

            # 4. Ergebnisse holen
            sorted_ids = sorted(final.keys(), key=lambda x: final[x], reverse=True)[:50]
            results = []
            for did in sorted_ids:
                row = cursor.execute("SELECT filename, path, snippet(documents, 2, '<b>', '</b>', '...', 15) FROM documents WHERE rowid = ?", (did,)).fetchone()
                if row: results.append(row)
            
            conn.close()
            return results

        except Exception as e:
            # DIESER TEIL IST NEU: Er schreibt den Fehler ins Logfile
            print(f"!!! KRITISCHER FEHLER IN DER SUCHE !!!")
            print(f"Fehler: {e}")
            print(traceback.format_exc())
            return []