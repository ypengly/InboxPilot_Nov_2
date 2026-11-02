; Inno Setup script (https://jrsoftware.org/isinfo.php). Used by build_windows.ps1, or run: iscc packaging\installer.iss
[Setup]
AppName=InboxPilot
AppVersion=1.0.0
DefaultDirName={autopf}\InboxPilot
DefaultGroupName=InboxPilot
OutputDir=..\dist
OutputBaseFilename=InboxPilot-Setup
Compression=lzma2
SolidCompression=yes
PrivilegesRequired=lowest
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "..\dist\InboxPilot\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\InboxPilot"; Filename: "{app}\InboxPilot.exe"
Name: "{autodesktop}\InboxPilot"; Filename: "{app}\InboxPilot.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Run]
Filename: "{app}\InboxPilot.exe"; Description: "Launch InboxPilot"; Flags: nowait postinstall skipifsilent
