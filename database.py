# database.py
import sqlite3
import os
import numpy as np
import traceback 
from sentence_transformers import util
from rapidfuzz import fuzz
from config import DB_NAME, APP_DATA_DIR

class DatabaseHandler:
    """
    Handles all database operations, including initialization,
    folder management, and searching.
    """
    def __init__(self):
        """
        Initializes the DatabaseHandler, sets up the database path,
        and initializes the database schema.
        """
        self.app_data_dir = APP_DATA_DIR
        self.db_name = DB_NAME
        self.model = None 
        self.init_db()

    def init_db(self):
        """
        Initializes the database schema by creating the necessary tables
        (documents, folders, embeddings) if they don't already exist.
        """
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        cursor.execute("CREATE VIRTUAL TABLE IF NOT EXISTS documents USING fts5(filename, path, content);")
        cursor.execute("CREATE TABLE IF NOT EXISTS folders (path TEXT PRIMARY KEY, alias TEXT);")
        cursor.execute("CREATE TABLE IF NOT EXISTS embeddings (doc_id INTEGER PRIMARY KEY, vec BLOB);")
        conn.commit()
        conn.close()

    def add_folder(self, path):
        """
        Adds a new folder path to the database to be indexed.

        Args:
            path (str): The absolute path of the folder to add.

        Returns:
            bool: True if the folder was added successfully, False otherwise.
        """
        conn = sqlite3.connect(self.db_name)
        try:
            conn.execute("INSERT OR IGNORE INTO folders (path, alias) VALUES (?, ?)", (path, os.path.basename(path)))
            conn.commit()
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def remove_folder(self, path):
        """
        Removes a folder and all its associated indexed files from the database.

        Args:
            path (str): The absolute path of the folder to remove.
        """
        conn = sqlite3.connect(self.db_name)
        cursor = conn.cursor()
        # Find all document IDs associated with the folder path
        cursor.execute("SELECT rowid FROM documents WHERE path LIKE ?", (f"{path}%",))
        ids = [row[0] for row in cursor.fetchall()]
        if ids:
            # Delete documents and their embeddings
            cursor.execute("DELETE FROM documents WHERE path LIKE ?", (f"{path}%",))
            placeholders = ','.join('?' * len(ids))
            cursor.execute(f"DELETE FROM embeddings WHERE doc_id IN ({placeholders})", ids)
        # Remove the folder entry
        cursor.execute("DELETE FROM folders WHERE path = ?", (path,))
        conn.commit()
        conn.close()

    def get_folders(self):
        """
        Retrieves a list of all indexed folder paths.

        Returns:
            list: A list of folder paths.
        """
        conn = sqlite3.connect(self.db_name)
        rows = conn.execute("SELECT path FROM folders").fetchall()
        conn.close()
        return [r[0] for r in rows]

    def search(self, query):
        """
        Performs a hybrid search combining semantic and lexical (keyword) search.

        Args:
            query (str): The search query.

        Returns:
            list: A list of search results, each containing
                  (filename, path, snippet).
        """
        # Safety check
        if not query.strip() or not self.model: 
            return []
        
        try:
            # 1. Semantic Preparation
            q_vec = self.model.encode(query, convert_to_tensor=False)
            
            conn = sqlite3.connect(self.db_name)
            cursor = conn.cursor()
            
            # Load embeddings
            cursor.execute("SELECT doc_id, vec FROM embeddings")
            data = cursor.fetchall()
            doc_ids = [d[0] for d in data]
            
            if not doc_ids:
                conn.close()
                return []

            # Convert BLOB -> Numpy Array
            # This can fail if the DB is corrupt or dimensions mismatch
            vecs = np.array([np.frombuffer(d[1], dtype=np.float32) for d in data])
            
            # Calculate Cosine Similarity
            scores = util.cos_sim(q_vec, vecs)[0].numpy()
            scores = np.clip(scores, 0, 1)
            sem_map = {did: float(s) for did, s in zip(doc_ids, scores)}

            # 2. Lexical Search (FTS)
            words = query.replace('"', '').split()
            if not words: words = [query]
            fts_query = " OR ".join([f'"{w}"*' for w in words])
            
            try:
                fts_rows = cursor.execute("SELECT rowid, filename, content FROM documents WHERE documents MATCH ? LIMIT 100", (fts_query,)).fetchall()
            except Exception as e:
                print(f"FTS Error (ignored): {e}")
                fts_rows = []

            lex_map = {}
            for did, fname, content in fts_rows:
                r1 = fuzz.partial_ratio(query.lower(), fname.lower())
                # Truncate content for performance
                r2 = fuzz.partial_token_set_ratio(query.lower(), content[:5000].lower())
                lex_map[did] = max(r1, r2) / 100.0

            # 3. Hybrid Fusion
            final = {}
            ALPHA = 0.65  # Weight for semantic score
            BETA = 0.35   # Weight for lexical score
            for did, s_score in sem_map.items():
                if s_score < 0.15 and did not in lex_map: continue
                l_score = lex_map.get(did, 0.0)
                h_score = (s_score * ALPHA) + (l_score * BETA)
                # Small boost if both scores are good
                if s_score > 0.4 and l_score > 0.6: h_score += 0.1
                final[did] = h_score

            # 4. Fetch Results
            sorted_ids = sorted(final.keys(), key=lambda x: final[x], reverse=True)[:50]
            results = []
            for did in sorted_ids:
                row = cursor.execute("SELECT filename, path, snippet(documents, 2, '<b>', '</b>', '...', 15) FROM documents WHERE rowid = ?", (did,)).fetchone()
                if row: results.append(row)
            
            conn.close()
            return results

        except Exception as e:
            # NEW: This part writes the error to the log file
            print(f"!!! CRITICAL ERROR IN SEARCH !!!")
            print(f"Error: {e}")
            print(traceback.format_exc())
            return []