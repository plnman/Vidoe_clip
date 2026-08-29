# 설치형 앱을 만든다 (Windows). 결과: dist\YoutubeClipper\
#
#   .\packaging\build.ps1              빌드만
#   .\packaging\build.ps1 -Installer   Inno Setup으로 설치 파일까지
#
# 크로스 빌드는 안 된다. 윈도우 exe는 윈도우에서만 만들어진다.

param(
    [switch]$Installer,
    [switch]$SkipBinaries
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "가상환경이 없습니다. 먼저 만듭니다."
    python -m venv .venv
}

Write-Host "== 의존성 =="
& $python -m pip install -q --upgrade pip
& $python -m pip install -q -r requirements.txt
& $python -m pip install -q pyinstaller pywebview

if (-not $SkipBinaries) {
    Write-Host "== 함께 넣을 바이너리 =="
    & (Join-Path $PSScriptRoot "fetch-binaries.ps1")
}

Write-Host "== 빌드 =="
Remove-Item -Recurse -Force (Join-Path $root "build"), (Join-Path $root "dist") -ErrorAction SilentlyContinue
& $python -m PyInstaller --noconfirm --clean (Join-Path $PSScriptRoot "clipper.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 실패" }

# ffmpeg 등은 실행 파일 옆 bin/ 에 있어야 한다. spec의 datas로 넣으면
# _internal/ 안으로 들어가는데, config.bundled_bin_dir()이 보는 자리는 여기다.
$dist = Join-Path $root "dist\YoutubeClipper"
$srcBin = Join-Path $PSScriptRoot "bin"
if (Test-Path $srcBin) {
    Write-Host "== bin/ 복사 =="
    $dstBin = Join-Path $dist "bin"
    New-Item -ItemType Directory -Force -Path $dstBin | Out-Null
    Copy-Item (Join-Path $srcBin "*") $dstBin -Recurse -Force
}
Copy-Item (Join-Path $root "LICENSE") $dist -Force -ErrorAction SilentlyContinue

$size = (Get-ChildItem $dist -Recurse | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ""
Write-Host ("완성: {0}  ({1:N0} MB)" -f $dist, $size)

if ($Installer) {
    Write-Host "== 설치 파일 =="
    $iscc = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) {
        $cmd = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($cmd) { $iscc = $cmd.Source }
    }
    if (-not $iscc) {
        throw "Inno Setup을 찾지 못했습니다.  winget install JRSoftware.InnoSetup"
    }
    & $iscc (Join-Path $PSScriptRoot "installer.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup 실패" }
    Write-Host ("설치 파일: {0}" -f (Join-Path $root "dist\installer"))
}
