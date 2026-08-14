; 9x Security - Inno Setup installer script
; Built in CI with: ISCC /DAppVersion=1.0.N installer.iss

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
AppId={{B7E4C9D1-5A2F-4E8B-9C3D-9XSEC0GATE01}
AppName=9x Security
AppVersion={#AppVersion}
AppPublisher=9x Security
DefaultDirName={localappdata}\9xSecurity
DefaultGroupName=9x Security
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=9xSecuritySetup-v{#AppVersion}
Compression=lzma2
SolidCompression=yes
CloseApplications=force
WizardStyle=modern
UninstallDisplayName=9x Security

[Files]
Source: "dist\9xSecurity\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{group}\9x Security"; Filename: "{app}\9xSecurity.exe"
Name: "{userdesktop}\9x Security"; Filename: "{app}\9xSecurity.exe"

[Run]
Filename: "{app}\9xSecurity.exe"; Description: "Launch 9x Security"; Flags: nowait postinstall
