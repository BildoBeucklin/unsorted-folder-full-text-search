# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

# --- 1. SPEZIELLE BIBLIOTHEKEN SAMMELN ---
# sentence_transformers und rapidfuzz sind komplex, wir holen alles automatisch
datas = [('assets', 'assets')]
binaries = []
hiddenimports = [
    'docx', 
    'openpyxl', 
    'pptx', 
    'pdfplumber', 
    'rapidfuzz', 
    'sentence_transformers', 
    'numpy'
]

# Sammle alle Daten für die KI-Bibliothek (verhindert Import-Fehler)
tmp_ret = collect_all('sentence_transformers')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# Sammle rapidfuzz sicherheitshalber auch komplett
tmp_ret = collect_all('rapidfuzz')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


# --- 2. ANALYSE ---
a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas, 
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# --- 3. EXE ERSTELLEN ---
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='UFFSearch',  # Name der Datei (UFFSearch.exe)
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False, 
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets\\favicon.ico', # Pfad zum Icon
)

# --- 4. ORDNER ZUSAMMENSTELLEN ---
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='UFFSearch', # Name des Ordners
)