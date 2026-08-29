# 함께 배포할 바이너리를 packaging/bin 에 모은다 (Windows).
#
#   ffmpeg / ffprobe : 영상을 자르고 이어붙인다. 없으면 앱이 아무것도 못 한다.
#   qjs              : 유튜브의 자바스크립트 챌린지를 푼다. 없으면 '봇으로 판단' 오류.
#
# 라이선스 — 여기서 받는 ffmpeg은 libx264가 포함된 GPL 빌드다. 함께 배포하면
# 이 앱도 GPL로 배포해야 한다(LICENSE 파일 참고). H.264 인코딩을 포기하지 않는 한
# 피할 수 없는 선택이다. docs/DESKTOP.md 2.3에 정리해 두었다.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # 진행률 표시가 다운로드를 크게 느리게 한다

$root = Split-Path -Parent $PSScriptRoot
$binDir = Join-Path $PSScriptRoot "bin"
$work = Join-Path $env:TEMP ("clipper-fetch-" + [guid]::NewGuid().ToString("N").Substring(0, 8))

New-Item -ItemType Directory -Force -Path $binDir | Out-Null
New-Item -ItemType Directory -Force -Path $work | Out-Null

try {
    # --- ffmpeg -------------------------------------------------------------
    if ((Test-Path (Join-Path $binDir "ffmpeg.exe")) -and (Test-Path (Join-Path $binDir "ffprobe.exe"))) {
        Write-Host "ffmpeg: 이미 있음 — 건너뜁니다"
    } else {
        $url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
        $zip = Join-Path $work "ffmpeg.zip"
        Write-Host "ffmpeg 받는 중 ... $url"
        Invoke-WebRequest -Uri $url -OutFile $zip
        Expand-Archive -Path $zip -DestinationPath $work -Force
        foreach ($name in @("ffmpeg.exe", "ffprobe.exe")) {
            $found = Get-ChildItem -Path $work -Recurse -Filter $name | Select-Object -First 1
            if (-not $found) { throw "$name 을(를) 압축 안에서 찾지 못했습니다" }
            Copy-Item $found.FullName (Join-Path $binDir $name) -Force
        }
        # 배포물에 라이선스 고지를 함께 넣어야 한다
        $license = Get-ChildItem -Path $work -Recurse -Filter "LICENSE*" | Select-Object -First 1
        if ($license) { Copy-Item $license.FullName (Join-Path $binDir "FFMPEG-LICENSE.txt") -Force }
    }

    # --- quickjs ------------------------------------------------------------
    # node/deno/bun은 40~90MB인데 qjs는 1MB대다. 챌린지만 풀면 되므로 이걸로 충분하다.
    $qjs = Join-Path $binDir "qjs.exe"
    if (Test-Path $qjs) {
        Write-Host "qjs: 이미 있음 — 건너뜁니다"
    } else {
        $arch = if ([Environment]::Is64BitOperatingSystem) { "x86_64" } else { "x86" }
        $url = "https://github.com/quickjs-ng/quickjs/releases/latest/download/qjs-windows-$arch.exe"
        Write-Host "qjs 받는 중 ... $url"
        Invoke-WebRequest -Uri $url -OutFile $qjs
    }

    # --- 글꼴 ---------------------------------------------------------------
    # 구간 제목을 화면에 얹으려면 글꼴 파일이 있어야 한다. 윈도우에는 보통 맑은 고딕이
    # 있지만 한국어 글꼴이 빠진 설치본도 있어서, 한글이 확실히 나오는 것을 함께 넣는다.
    # 나눔고딕은 OFL이라 배포에 문제가 없다. 2MB대다.
    $font = Join-Path $binDir "font.ttf"
    if (Test-Path $font) {
        Write-Host "글꼴: 이미 있음 — 건너뜁니다"
    } else {
        $url = "https://raw.githubusercontent.com/google/fonts/main/ofl/nanumgothic/NanumGothic-Regular.ttf"
        Write-Host "글꼴 받는 중 ... $url"
        Invoke-WebRequest -Uri $url -OutFile $font
        $license = Join-Path $binDir "FONT-LICENSE.txt"
        Invoke-WebRequest -Uri "https://raw.githubusercontent.com/google/fonts/main/ofl/nanumgothic/OFL.txt" -OutFile $license
    }

    Write-Host ""
    Write-Host "packaging/bin 준비 완료:"
    Get-ChildItem $binDir | ForEach-Object {
        "  {0,-24} {1,8:N1} MB" -f $_.Name, ($_.Length / 1MB)
    }
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
