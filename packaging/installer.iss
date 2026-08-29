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
; 설치할 때 관리자 권한을 한 번 받는다.
;
; 처음에는 권한 없이(lowest) 깔리게 했는데, 그러면 덮어 설치가 깨진다. Inno는 이전에
; 깔린 자리를 기억했다가 거기에 쓰려고 하는데, 그 자리가 Program Files면 권한이 없어
; "DeleteFile failed; code 5"로 멈춘다. 실제로 그렇게 막혔다.
;
; 권한이 없는 PC를 위해 사용자 폴더로 우회하는 길도 만들어 봤지만, 그러면 옛 설치와
; 새 설치가 양쪽에 남아 어느 것이 도는지 알 수 없어진다. 한 번 묻고 제자리에
; 덮어쓰는 쪽이 사용자에게 훨씬 단순하다. 권한이 없는 PC에서는 아래 dialog로
; '이 사용자만' 설치를 고를 수 있다.
PrivilegesRequired=admin
; dialog = 권한이 없으면 '이 사용자만' 설치를 고를 수 있게. commandline = /CURRENTUSER 허용.
PrivilegesRequiredOverridesAllowed=commandline dialog
; 앱이 떠 있으면 실행 파일을 바꿀 수 없다("DeleteFile failed; code 5").
; yes로는 부족하다 — 조용히 설치할 때는 닫지 않고 넘어가서 그대로 실패한다.
; force는 파일을 잡고 있는 프로세스를 실제로 닫는다. 실측으로 확인했다.
CloseApplications=force
CloseApplicationsFilter=*.exe,*.dll
RestartApplications=no
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
