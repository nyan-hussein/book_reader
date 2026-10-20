; Bookshelf Windows installer settings (Inno Setup).
; installers/build_app.py builds the app and passes the version and folders below.

#ifndef AppVersion
  #define AppVersion "1.3.1"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\build\app\Bookshelf"
#endif
#ifndef IconFile
  #define IconFile "..\..\build\bookshelf.ico"
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

[Setup]
AppId={{6B1E4C1A-3F0D-4A55-9C2E-7D8B9B2F1A10}
AppName=Bookshelf
AppVersion={#AppVersion}
AppVerName=Bookshelf {#AppVersion}
AppPublisher=Bookshelf
VersionInfoDescription=Bookshelf book reader
; installs just for the current user: no administrator password needed
PrivilegesRequired=lowest
DefaultDirName={autopf}\Bookshelf
DefaultGroupName=Bookshelf
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=Bookshelf-Setup
SetupIconFile={#IconFile}
UninstallDisplayIcon={app}\Bookshelf.exe
UninstallDisplayName=Bookshelf
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; if Bookshelf is open while updating or uninstalling, offer to close it
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Bookshelf"; Filename: "{app}\Bookshelf.exe"
Name: "{group}\Uninstall Bookshelf"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Bookshelf"; Filename: "{app}\Bookshelf.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Bookshelf.exe"; Description: "Open Bookshelf now"; Flags: nowait postinstall skipifsilent

; Note: uninstalling removes the app but keeps your library, progress and highlights
; (in %APPDATA%\Bookshelf), so reinstalling later brings everything back.
