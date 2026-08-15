; Inno Setup script for the Kumiko Lamp desktop app.
;
;   pyinstaller packaging/kumiko.spec --noconfirm --distpath build/dist --workpath build/work
;   ISCC.exe packaging\installer.iss
;
; Produces build\Setup-KumikoLamp-<version>.exe.

#define AppName        "Kumiko Lamp"
#define AppVersion      "1.0.0"
#define AppPublisher    "Kumiko Lamp"
#define AppExe          "KumikoLamp.exe"

[Setup]
; Never change AppId: it is what lets a new version upgrade an old one in place
; rather than installing a second copy beside it.
AppId={{BA7FA47F-1DB0-4D57-B7CA-9973D1F54A7A}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExe}
OutputDir=..\build
OutputBaseFilename=Setup-KumikoLamp-{#AppVersion}
SetupIconFile=kumiko.ico
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-user by default, so installing needs no administrator and no UAC prompt.
; The dialog still offers all-users for anyone who wants it.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; The whole PyInstaller onedir tree: the launcher plus _internal, which carries
; Python, numpy, scipy, trimesh, manifold3d and web/index.html.
Source: "..\build\dist\KumikoLamp\*"; DestDir: "{app}"; \
    Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; \
    Flags: nowait postinstall skipifsilent

[UninstallDelete]
; PyInstaller leaves __pycache__ behind under _internal on first run; without
; this the uninstaller leaves the install directory behind.
Type: filesandordirs; Name: "{app}\_internal"
