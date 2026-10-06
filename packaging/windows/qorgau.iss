; Build through scripts/build_installer.py; all paths are explicit preprocessor inputs.
#ifndef AppVersion
  #error AppVersion is required
#endif
#ifndef SourceDir
  #error SourceDir is required
#endif
#ifndef OutputPath
  #error OutputPath is required
#endif

[Setup]
AppId={{AF12B236-70BA-4B34-A991-A94AC778451D}
AppName=Qorgau Student
AppVersion={#AppVersion}
AppPublisher=Qorgau
AppPublisherURL=https://github.com/ovverage/hackaton-kru
AppSupportURL=https://github.com/ovverage/hackaton-kru
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\Qorgau Student
DefaultGroupName=Qorgau
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible and not arm64
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
AppMutex=Local\Qorgau.Student.Running
SetupMutex=Qorgau.Student.Setup
CloseApplications=no
RestartApplications=no
UninstallDisplayIcon={app}\Qorgau-Student.exe
WizardStyle=modern
SetupIconFile=qorgau.ico
Compression=lzma2/fast
SolidCompression=yes
OutputDir={#OutputPath}
OutputBaseFilename=Qorgau-Student-Setup-{#AppVersion}-x64
SetupLogging=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Qorgau Student"; Filename: "{app}\Qorgau-Student.exe"; Parameters: "--show"; WorkingDir: "{app}"
Name: "{autodesktop}\Qorgau Student"; Filename: "{app}\Qorgau-Student.exe"; Parameters: "--show"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\Qorgau-Student.exe"; Parameters: "--show"; Description: "{cm:LaunchProgram,Qorgau Student}"; Flags: nowait postinstall skipifsilent unchecked

; No [UninstallDelete]: ~/.qorgau contains credentials/evidence and must survive removal.
