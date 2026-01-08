; -- UFF-Search Installer Skript --

[Setup]
; Der Name, der überall steht
AppName=UFF Text Search
AppVersion=3.0
AppPublisher=Konstantin Roßmann
AppPublisherURL=https://rossmann-it-solutions.de

; Wo soll standardmäßig installiert werden? {autopf} ist "Program Files"
DefaultDirName={autopf}\UFF-Search
; Name der Gruppe im Startmenü
DefaultGroupName=UFF Search

; Speicherort der fertigen setup.exe (z.B. auf dem Desktop oder im Projektordner)
OutputDir=.
OutputBaseFilename=UFF_Search_Installer_v3
Compression=lzma
SolidCompression=yes

; Icon für den Installer selbst (optional, sonst weglassen)
; SetupIconFile=app.ico 

; Administrator-Rechte anfordern für Installation in Program Files
PrivilegesRequired=admin

[Languages]
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
; Checkbox: "Desktop Verknüpfung erstellen"
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; !!! WICHTIG: HIER DEN PFAD ZU DEINER EXE ANPASSEN !!!
; "Source" muss auf die Datei zeigen, die PyInstaller im "dist" Ordner erstellt hat.
Source: "C:\Users\konst\Arbeit\unsorted-folder-full-text-search\dist\UFF-Search.exe"; DestDir: "{app}"; Flags: ignoreversion

; Falls du ein Icon mitliefern willst (optional)
; Source: "C:\Pfad\Zu\Deinem\Projekt\app.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Verknüpfung im Startmenü
Name: "{group}\UFF Text Search"; Filename: "{app}\UFF-Search.exe"
; Verknüpfung zum Deinstallieren
Name: "{group}\Uninstall UFF Search"; Filename: "{uninstallexe}"
; Verknüpfung auf dem Desktop (wenn vom User ausgewählt)
Name: "{commondesktop}\UFF Text Search"; Filename: "{app}\UFF-Search.exe"; Tasks: desktopicon

[Run]
; Checkbox am Ende: "Programm jetzt starten"
Description: "{cm:LaunchProgram,UFF Text Search}"; Filename: "{app}\UFF-Search.exe"; Flags: nowait postinstall skipifsilent