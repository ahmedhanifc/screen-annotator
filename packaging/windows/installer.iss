; Inno Setup script for screen-annotator.
;
; Build (from the repo root, after PyInstaller has produced dist\screen-annotator\):
;   iscc /DMyAppVersion=1.0.0 packaging\windows\installer.iss
;
; Ships UNSIGNED for now: first-run shows Windows SmartScreen's "unknown
; publisher" prompt (More info -> Run anyway). A signing step can be slotted into
; the release workflow later with no change here.

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppName "Screen Annotator"
#define MyAppExeName "screen-annotator.exe"
#define MyAppPublisher "Ahmed Hanif"
#define MyAppURL "https://github.com/ahmedhanifc/screen-annotator"

[Setup]
; A stable, unique GUID identifies this app across upgrades/uninstalls.
AppId={{7F3A9C2E-4B1D-4E6A-9C8F-2A5B1E0D3F44}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
; Per-user install into %LOCALAPPDATA% so there's no UAC/admin prompt — one less
; barrier for a non-technical audience (the VS Code default pattern).
PrivilegesRequired=lowest
DefaultDirName={autopf}\screen-annotator
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Paths below are relative to the repo root:
SourceDir=..\..
LicenseFile=LICENSE
SetupIconFile=screen_annotator\assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
OutputDir=dist\installer
OutputBaseFilename=screen-annotator-setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon"; Description: "Launch Screen Annotator automatically when I sign in"; Flags: unchecked

[Files]
Source: "dist\screen-annotator\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{cm:UninstallProgram,{#MyAppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; "Launch at login" — a per-user Run entry, removed cleanly on uninstall.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "screen-annotator"; \
    ValueData: """{app}\{#MyAppExeName}"""; Tasks: startupicon; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#MyAppName}}"; \
    Flags: nowait postinstall skipifsilent
