; Inno Setup script for Manim Studio — see ADR 0005.
; Build via scripts\build.ps1 (it passes /DAppVersion); compiling by hand without
; the define falls back to a clearly-marked dev version.
#ifndef AppVersion
  #define AppVersion "0.0.0-dev"
#endif
#define RootDir ".."
#define BundleDir RootDir + "\dist\ManimStudio"

#if DirExists(BundleDir) == 0
  #error "dist\\ManimStudio not found — run scripts\\build.ps1 first."
#endif

[Setup]
AppId={{7B7F4382-07AF-4DA0-A735-7219E90A6E4C}
AppName=Manim Studio
AppVersion={#AppVersion}
AppPublisher=Manim Studio Team
DefaultDirName={autopf}\Manim Studio
DefaultGroupName=Manim Studio
; Per-user install: no admin prompt, works on locked-down machines.
PrivilegesRequired=lowest
OutputDir={#RootDir}\dist
OutputBaseFilename=ManimStudio-Setup-{#AppVersion}
SetupIconFile={#RootDir}\packaging\manim_studio.ico
UninstallDisplayIcon={app}\ManimStudio.exe
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
Name: "{group}\Manim Studio"; Filename: "{app}\ManimStudio.exe"
Name: "{group}\Manim Studio CLI"; Filename: "{app}\manim-studio.exe"
Name: "{autodesktop}\Manim Studio"; Filename: "{app}\ManimStudio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ManimStudio.exe"; Description: "{cm:LaunchProgram,Manim Studio}"; Flags: nowait postinstall skipifsilent
