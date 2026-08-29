; 설치 프로그램 (Inno Setup 6)
;   빌드: packaging\build.ps1 -Installer
;   결과: dist\installer\YoutubeClipper-setup.exe
;
; PyInstaller는 폴더를 만든다(onedir). 사용자에게 파일 하나로 보이게 하는 것이
; 이 설치 프로그램의 역할이다.

#define AppName "유튜브 구간 편집기"
#define AppExe "YoutubeClipper.exe"
#define AppVersion "1.0.0"
#define AppPublisher "plnman"
#define AppUrl "https://github.com/plnman/Vidoe_clip"

[Setup]
AppId={{7C1D6A2E-4F3B-4E0B-9E2A-5B7C9D1E0A31}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppUrl}
AppSupportURL={#AppUrl}
DefaultDirName={autopf}\YoutubeClipper
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; 관리자 권한 없이 각 사용자 폴더에 설치한다. 회사 PC에서도 걸리지 않는다.
PrivilegesRequiredOverridesAllowed=dialog
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=YoutubeClipper-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; 함께 넣는 ffmpeg이 GPL이라 앱도 GPL로 배포한다
LicenseFile=..\LICENSE
UninstallDisplayIcon={app}\{#AppExe}

[Languages]
Name: "korean"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면에 바로가기 만들기"; GroupDescription: "추가 작업:"

[Files]
Source: "..\dist\YoutubeClipper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "지금 실행"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 작업 파일과 yt-dlp 갱신본. 지우지 않으면 사용자 폴더에 남는다.
Type: filesandordirs; Name: "{localappdata}\YoutubeClipper"
