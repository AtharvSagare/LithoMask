; Inno Setup script for the LithoMask Windows installer.
; Build:  1) run build_portable_exe.bat (or build_exe.bat) to create dist\LithoMask.exe
;         2) compile this file with Inno Setup 6:  ISCC.exe installer\LithoMask-Setup.iss
; Output: dist\LithoMask-Setup.exe
[Setup]
AppId={{B789E79C-2B08-4B0F-940E-7815B93422C7}
AppName=LithoMask
AppVersion=1.0.0
AppVerName=LithoMask 1.0.0
AppPublisher=Atharv Sagare (Oppeniers)
AppPublisherURL=https://github.com/AtharvSagare/LithoMask
AppSupportURL=https://github.com/AtharvSagare/LithoMask/issues
AppUpdatesURL=https://github.com/AtharvSagare/LithoMask/releases
DefaultDirName={localappdata}\LithoMask
DefaultGroupName=LithoMask
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
LicenseFile=..\LICENSE
OutputDir=..\dist
OutputBaseFilename=LithoMask-Setup
SetupIconFile=..\LithoMask.ico
UninstallDisplayIcon={app}\LithoMask.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible

[Files]
Source: "..\dist\LithoMask.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\USER_GUIDE.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\LithoMask_User_Guide.pdf"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\LithoMask"; Filename: "{app}\LithoMask.exe"; WorkingDir: "{app}"
Name: "{autoprograms}\LithoMask User Guide"; Filename: "{app}\LithoMask_User_Guide.pdf"
Name: "{autodesktop}\LithoMask"; Filename: "{app}\LithoMask.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\LithoMask.exe"; Description: "Launch LithoMask"; Flags: nowait postinstall skipifsilent