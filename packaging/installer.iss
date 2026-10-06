; Inno Setup script for Manim Editor — see ADR 0005.
; Build via scripts\build.ps1 (it passes /DAppVersion); compiling by hand without
; the define falls back to a clearly-marked dev version.
#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#define RootDir ".."
#define BundleDir RootDir + "\dist\ManimEditor"

#if DirExists(BundleDir) == 0
  #error "dist\\ManimEditor not found — run scripts\\build.ps1 first."
#endif

[Setup]
AppId={{7B7F4382-07AF-4DA0-A735-7219E90A6E4C}
AppName=Manim Editor
AppVersion={#AppVersion}
AppPublisher=Manim Editor Team
DefaultDirName={autopf}\Manim Editor
DefaultGroupName=Manim Editor
; Per-user install: no admin prompt, works on locked-down machines.
PrivilegesRequired=lowest
OutputDir={#RootDir}\dist
OutputBaseFilename=ManimEditor-Setup-{#AppVersion}
SetupIconFile={#RootDir}\packaging\manim_editor.ico
UninstallDisplayIcon={app}\ManimEditor.exe
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
WizardStyle=modern
DisableProgramGroupPage=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Manim Editor"; Filename: "{app}\ManimEditor.exe"
Name: "{group}\Manim Editor CLI"; Filename: "{app}\manim-editor.exe"
Name: "{autodesktop}\Manim Editor"; Filename: "{app}\ManimEditor.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ManimEditor.exe"; Description: "{cm:LaunchProgram,Manim Editor}"; Flags: nowait postinstall skipifsilent
