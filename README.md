# UFF Search

UFF Search is a desktop application for Windows that allows you to perform fast, fuzzy full-text searches on your local files.

It builds a search index for the folders you specify, allowing you to quickly find documents even with typos in your search query.

## Features

*   **Local Full-Text Search:** Indexes and searches the content of files in your selected folders.
*   **Fuzzy Search:** Finds relevant files even if your search term has typos, powered by `rapidfuzz`.
*   **Wide File Type Support:** Extracts text from PDFs, and various plain text formats (`.txt`, `.md`, `.py`, `.json`, `.csv`, `.html`, `.log`, `.ini`, `.xml`).
*   **Simple UI:** An easy-to-use interface to manage your indexed folders and view search results.
*   **Click to Open:** Search results can be clicked to open the file directly.
*   **Self-Contained:** Stores its index in your local application data folder.

## Installation

### Windows Installer
A pre-built installer (`UFF_Search_Installer_v3.exe`) is available for easy installation.

### From Source
To run the application from the source code, you'll need Python and the following dependencies:

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd unsorted-folder-full-text-search
    ```

2.  **Install dependencies:**
    It is recommended to use a virtual environment.
    ```bash
    pip install -r requirements.txt
    ```

3.  **Run the application:**
    ```bash
    python uff_app.py
    ```

## Usage

1.  Start the application.
2.  Click **" + Hinzufügen"** (Add) to select a folder you want to index. The application will start scanning it immediately.
3.  Once indexing is complete, type your search query into the search bar and press Enter or click **"Suchen"** (Search).
4.  Results will appear below. Click on any result to open the file.
5.  To re-scan a folder for changes, select it from the list and click **"↻ Neu scannen"** (Rescan).
6.  To remove a folder, select it and click **" - Entfernen"** (Remove).

## License

This project is licensed under the GNU Affero General Public License v3.0. See the [LICENSE](LICENSE) file for details.
