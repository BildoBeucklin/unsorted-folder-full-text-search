[![UFF Banner](assets/uff_banner.jpeg)](https://github.com/BildoBeucklin/unsorted-folder-full-text-search)

# UFF Search - Unsorted Folder Full-Text Search

![GitHub stars](https://img.shields.io/github/stars/BildoBeucklin/unsorted-folder-full-text-search?style=social)
![GitHub forks](https://img.shields.io/github/forks/BildoBeucklin/unsorted-folder-full-text-search?style=social)
![GitHub license](https://img.shields.io/github/license/BildoBeucklin/unsorted-folder-full-text-search)

UFF Search is a powerful desktop application for Windows that allows you to perform fast, intelligent, and fuzzy full-text searches on your local files, including searching inside ZIP archives.

It builds a local search index for the folders you specify, allowing you to quickly find documents based on their meaning (semantic search) and specific keywords, even with typos in your search query.

## Key Features

*   **Hybrid Search:** Combines state-of-the-art **semantic search** (understanding the *meaning* of your query) with traditional **keyword search** (finding exact words). This delivers more relevant results than simple text matching.
*   **ZIP Archive Search:** Indexes and searches the content of files *inside* `.zip` archives.
*   **Fuzzy Search:** Finds relevant files even if your search term has typos, powered by `rapidfuzz`.
*   **Wide File Type Support:** Extracts text from:
    *   PDFs (`.pdf`)
    *   Microsoft Office (`.docx`, `.xlsx`, `.pptx`)
    *   Plain text formats (`.txt`, `.md`, `.py`, `.json`, `.csv`, `.html`, `.log`, `.ini`, `.xml`)
*   **Simple UI:** An easy-to-use interface to manage your indexed folders and view search results.
*   **Click to Open:** Search results can be clicked to open the file directly (or the containing ZIP archive).
*   **Self-Contained:** Stores its index and all data in your local application data folder for privacy and portability.

## How It Works

UFF Search uses a two-pronged approach for searching:

1.  **Semantic Search:** When you search, your query is converted into a numerical representation (a vector) using the `all-MiniLM-L6-v2` sentence-transformer model. The application finds files whose content is semantically similar to your query.
2.  **Keyword Search:** The application also uses a traditional full-text search (SQLite FTS5) and fuzzy matching to find files containing the exact keywords in your query.

A hybrid scoring system ranks the results, giving you the best of both worlds.

## Installation

### Windows Installer
A pre-built installer (`UFF_Search_Installer_v3.exe`) is available for easy installation. This is the recommended method for most users.

### From Source
To run the application from the source code, you'll need Python 3.

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/BildoBeucklin/unsorted-folder-full-text-search.git
    cd unsorted-folder-full-text-search
    ```

2.  **Install dependencies:**
    It is highly recommended to use a virtual environment.
    ```bash
    pip install -r requirements.txt
    ```

3.  **Run the application:**
    ```bash
    python main.py
    ```

## Building from Source

To create a standalone executable from the source code, you can use `pyinstaller`:

1.  **Install PyInstaller:**
    ```bash
    pip install pyinstaller
    ```

2.  **Build the executable:**
    ```bash
    pyinstaller --name "UFF_Search" --windowed --onefile --icon="favicon.ico" --add-data "assets;assets" main.py
    ```
This command will create a single executable file in the `dist` folder.

## Usage

1.  Start the application.
2.  Click **" + Hinzufügen"** (Add) to select a folder you want to index. The application will start scanning it immediately.
3.  Once indexing is complete, type your search query into the search bar and press Enter or click **"Suchen"** (Search).
4.  Results will appear below. Click on any result to open the file. If the file is inside a ZIP archive, the ZIP file will be opened.
5.  To re-scan a folder for changes, select it from the list and click **"↻ Neu scannen"** (Rescan).
6.  To remove a folder, select it and click **" - Entfernen"** (Remove).

## Technical Details

*   **Framework:** PyQt6
*   **Database:** SQLite with FTS5 for full-text indexing.
*   **Search Technology:**
    *   `sentence-transformers` (specifically `all-MiniLM-L6-v2`) for semantic search.
    *   `rapidfuzz` for fuzzy string matching.
*   **File Processing:** 
    *   `pdfplumber` for PDF text extraction.
    *   `python-docx` for `.docx` files.
    *   `openpyxl` for `.xlsx` files.
    *   `python-pptx` for `.pptx` files.
*   **Index Location:** The search index database (`uff_index.db`) is stored in `%LOCALAPPDATA%\UFF_Search` on Windows.

## License

This project is licensed under the GNU Affero General Public License v3.0. See the [LICENSE](LICENSE) file for details.
This license requires that if you use this software in a product or service that is accessed over a network, you must also make the source code available to the users of that product or service.