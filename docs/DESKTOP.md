# 데스크톱 앱

> 저장소: **https://github.com/plnman/Vidoe_clip** (브랜치 `main`)
> 클론: `git clone https://github.com/plnman/Vidoe_clip.git`

4K Video Downloader처럼 **어느 PC에나 설치해서 바로 쓰는** 형태. Python과 ffmpeg을
직접 설치해야 하던 것을 없애는 것이 목표였다.

> **윈도우는 됐다.** 2026-08-29 기준 `dist\installer\YoutubeClipper-setup.exe` (79MB,
> 푼 크기 253MB)가 나오고, ffmpeg·node가 **전혀 없는 PATH**에서 실제 유튜브 영상
> 전 과정을 완주한다. 아래 1~5단계는 전부 밟았고, 실제로 걸렸던 함정은 6장에 옮겨 적었다.
> 맥·리눅스 빌드 스크립트는 있으나 그 OS에서 돌려본 적은 없다.
>
> ```powershell
> .\packaging\build.ps1 -Installer
> ```

먼저 `docs/DESIGN.md`를 읽을 것. 왜 이런 구조인지 모른 채 패키징하면 핵심 설계를
망가뜨리기 쉽다.

---

## 0. 목표와 비목표

**목표**

- 설치 파일 하나를 받아 실행하면 바로 쓸 수 있다. Python·ffmpeg 설치 불필요
- 창이 열리고 그 안에서 지금의 화면이 그대로 돈다
- 유튜브가 규칙을 바꿔도 앱을 다시 설치하지 않고 대응된다 (yt-dlp 자동 갱신)

**비목표**

- 자동 업데이트(앱 자체) — 나중에. 우선은 yt-dlp만 갱신되면 된다
- 앱스토어 배포 — 샌드박스 제약이 커서 이 앱과 맞지 않는다
- 서버 버전 폐기 — `run.sh` 경로는 그대로 남긴다. 데스크톱은 포장만 달리한 것이다

**중요한 제약** — 패키징은 **대상 OS에서만 만들 수 있다.** 윈도우 exe는 윈도우에서,
맥 앱은 맥에서 빌드해야 한다. 크로스 빌드는 안 된다.

---

## 1. 구조

```
사용자가 실행 파일을 더블클릭
  ↓
app/desktop.py  ── 빈 포트 찾기 → uvicorn을 백그라운드 스레드로 기동
  ↓             └─ /api/health 응답할 때까지 대기
pywebview 창 열기 (실패하면 기본 브라우저)
  ↓
지금의 웹 화면 그대로 (app/static/)
  ↓
창을 닫으면 서버도 내려감
```

화면과 서버 코드는 **한 줄도 바꾸지 않는다.** 실행 방식만 바뀐다.

### 코드에 반영된 것

| 파일 | 내용 |
|---|---|
| `packaging/entry.py` | 묶을 때의 진입점. `app.desktop`을 **패키지째** import 한다(6장 참고) |
| `app/desktop.py` | 포트 자동 선택, 서버 기동 대기, 창 열기, OS 파일 선택, 종료 정리 |
| `app/config.py` | `FROZEN`, `user_data_dir()`, `bundled_bin_dir()`, `use_bundled_bin()` |
| `app/__init__.py` | 순서가 중요한 두 줄 — yt-dlp 갱신본 우선, 묶은 `bin/`을 PATH 앞으로 |
| `app/updater.py` | yt-dlp를 앱과 따로 갱신 (2.5) |
| `app/media.py` | `_tool()` — 함께 묶은 ffmpeg을 PATH보다 먼저 찾는다 |
| `app/downloader.py` | 묶은 ffmpeg 위치 전달, 런타임 이름≠실행 파일 이름 처리 |
| `tests/test_desktop.py` | 포트 선택, 기동 대기, 창 실패 시 브라우저 대체, PATH 주입 |
| `tests/test_updater.py` | 갱신 받기·갈아끼우기·손상 거절·부팅 시 우선순위 |

개발 중에도 그대로 돈다:

```bash
./.venv/bin/pip install pywebview
./.venv/bin/python -m app.desktop
```

`--browser`로 기본 브라우저, `--no-open`으로 서버만 띄운다.

---

## 2. 결정 사항과 이유

### 2.1 창은 pywebview로 (Electron/Tauri 아님)

**이유** — OS에 이미 있는 웹뷰(Windows: WebView2, macOS: WKWebView, Linux: WebKitGTK)를
빌려 쓴다. Electron은 크로미움을 통째로 넣어 150MB 이상 늘어난다. 우리 화면은
프레임워크 없는 HTML/CSS/JS라 최신 브라우저 기능이 필요 없다.

**주의** — 윈도우에서 WebView2 런타임이 없는 구형 PC가 있다. 설치 프로그램에
부트스트래퍼를 포함시키거나, 없으면 기본 브라우저로 넘어가면 된다
(`open_window()`가 이미 False를 돌려주고 브라우저로 대체한다).

### 2.2 PyInstaller onedir (onefile 아님)

**이유** — onefile은 실행할 때마다 임시 폴더에 압축을 풀어서 시작이 느리고,
ffmpeg 같은 큰 바이너리를 같이 넣으면 더 심해진다. 백신 오탐도 잦다.
onedir로 만들어 **설치 프로그램으로 감싸면** 사용자에게는 똑같이 파일 하나다.

### 2.3 ffmpeg을 함께 배포한다

**이유** — "설치만 하면 바로"의 핵심이다. 사용자가 ffmpeg을 따로 깔게 하면
데스크톱 앱을 만드는 의미가 절반 사라진다.

**배치** — 실행 파일 옆 `bin/` 폴더. `config.bundled_bin_dir()`이 이 경로를 본다.

```
YoutubeClipper/
├── YoutubeClipper(.exe)
├── bin/
│   ├── ffmpeg(.exe)
│   ├── ffprobe(.exe)
│   └── qjs(.exe)        # 2.4 참고
└── _internal/           # PyInstaller가 만드는 것
```

**받는 곳**

| OS | 출처 |
|---|---|
| Windows | https://www.gyan.dev/ffmpeg/builds/ (release essentials) 또는 BtbN/FFmpeg-Builds |
| macOS | https://evermeet.cx/ffmpeg/ (ffmpeg, ffprobe 따로) |
| Linux | https://johnvansickle.com/ffmpeg/ (static) |

**라이선스 — 반드시 확인할 것**

libx264가 포함된 ffmpeg 빌드는 **GPL**이다. 이걸 함께 배포하면 앱 전체를
GPL로 배포해야 한다. 이 저장소는 공개 저장소이므로 문제는 없지만,

- 저장소에 `LICENSE` 파일을 GPL-3.0으로 추가할 것
- 설치 프로그램과 앱 안에 ffmpeg 라이선스 고지를 포함할 것
- 나중에 비공개로 전환할 생각이라면 LGPL 빌드(libx264 없음)를 써야 하는데,
  그러면 H.264 인코딩을 못 하므로 `media.FORMATS`의 mp4가 깨진다. 사실상 GPL로 가는 게 맞다

### 2.4 자바스크립트 런타임은 quickjs를 넣는다

**이유** — 유튜브는 봇을 거르려고 JS 챌린지를 건다. yt-dlp는 이걸 JS 런타임으로
푸는데(`yt_dlp/extractor/youtube/jsc/`), 기본값은 `deno` 하나만 본다.
데스크톱 앱은 사용자 PC에 무엇이 깔려 있는지 알 수 없으므로 하나를 넣어야 한다.

| 후보 | 크기 | 판단 |
|---|---|---|
| **quickjs (`qjs`)** | **약 1MB** | **채택.** 압도적으로 작다 |
| node | 50MB+ | 큼 |
| deno | 40MB+ | 큼 |
| bun | 90MB+ | 큼 |

`downloader.available_js_runtimes()`가 `bin/`을 먼저 뒤지므로 넣기만 하면 된다.

**검증됨** — node·deno·bun을 전부 뺀 PATH에 `qjs.exe`만 두고 진단을 돌려 8단계가
모두 통과하는 것을 확인했다(2026-08-29). deno로 바꿀 필요가 없다.

```
[ OK ] 자바스크립트 런타임    quickjs
[ OK ] 영상 정보 조회         유발하라리 박사님 통찰 감사합니다 · 40:03
```

받는 곳은 quickjs-ng 릴리스다(`qjs-windows-x86_64.exe` 등, 2MB대).
`packaging/fetch-binaries.ps1` / `.sh`가 알아서 받는다.

**주의** — 실행 파일 이름은 `quickjs`가 아니라 **`qjs`**다. 처음에 런타임 이름 그대로
찾도록 되어 있어서 넣어도 못 찾았다. `downloader.JS_RUNTIMES`가 이름과 실행 파일
후보를 따로 들고 있는 이유다.

### 2.5 yt-dlp는 실행 시점에 갱신할 수 있어야 한다

**이것이 데스크톱 전환에서 가장 위험한 지점이다.**

`docs/DESIGN.md` D8에 적었듯, 이 앱은 유튜브 변화 대응을 전적으로 yt-dlp에 의존한다.
그런데 PyInstaller로 얼리면 yt-dlp도 같이 얼어붙어, 유튜브가 바뀌는 순간
**앱이 죽고 사용자는 새 설치 파일을 기다려야 한다.** 이러면 안 된다.

**설계**

1. 빌드 시점의 yt-dlp를 기본으로 넣는다 (오프라인에서도 일단 동작)
2. 사용자 데이터 폴더에 갱신본을 둘 자리를 만든다
   `%LOCALAPPDATA%/YoutubeClipper/runtime/` (`config.user_data_dir()`)
3. 앱 시작 시 갱신본이 있으면 **그쪽을 먼저 import** 한다
   `sys.path.insert(0, str(runtime_dir))`
4. 화면에 `yt-dlp 업데이트` 버튼을 둔다. 누르면 최신 wheel을 받아 위 폴더에 푼다
5. 주 1회 정도 조용히 확인하고, 새 버전이 있으면 화면에 알린다

**구현됨** — `app/updater.py` + `app/main.py`의 엔드포인트 두 개
(`GET /api/updates`, `POST /api/updates/ytdlp`). 화면 맨 아래 `관리` 칸에서 누른다.

경로를 잡는 자리는 `app/desktop.py`가 아니라 **`app/__init__.py`**로 갔다. 진입점이
`app.main`(웹앱)·`app.desktop`(앱 창)·`app.doctor`(진단) 셋인데 전부 이 패키지를 지나므로,
거기 두면 어느 길로 들어와도 yt_dlp보다 먼저 실행된다.

pip이 없는 환경(묶인 앱)에서도 되어야 해서 wheel을 직접 받아 푼다. yt-dlp wheel은
순수 파이썬이라 문제없다. 받은 것을 다 푼 **뒤에** 갈아끼우므로 중간에 실패해도
쓰던 버전이 남는다. sha256도 확인한다.

**대안** — standalone `yt-dlp` 실행 파일을 `bin/`에 두고 subprocess로 부르는 방식.
교체가 파일 하나로 끝나 더 단순하지만, 지금 코드는 Python API를 쓰고 있어
`downloader.py`를 다시 써야 한다. 위 방식으로 됐으므로 쓰지 않았다.

### 2.6 작업 파일은 사용자 폴더에

임시 폴더는 OS가 청소해서 결과물이 사라질 수 있다. `config.FROZEN`일 때
`user_data_dir()/work`를 쓴다. 묶은 앱에서 실제로 `%LOCALAPPDATA%\YoutubeClipper\work`로
잡히는 것을 확인했다.

`작업 폴더 비우기`도 넣었다 — `POST /api/work/clear`, 화면의 `관리` 칸.
지우기와 갱신은 둘 다 루프백에서만 받는다(다른 기기에서 남의 PC 파일을 지우면 곤란하다).

---

## 3. 빌드하기

윈도우는 한 줄이면 된다. 의존성 설치, 바이너리 받기, 빌드, `bin/` 복사, 설치 파일까지
스크립트가 한다.

```powershell
.\packaging\build.ps1 -Installer
```

| 옵션 | 뜻 |
|---|---|
| (없음) | `dist\YoutubeClipper\` 까지만 |
| `-Installer` | Inno Setup으로 `dist\installer\YoutubeClipper-setup.exe` 까지 |
| `-SkipBinaries` | `packaging\bin\`을 이미 받아뒀을 때 (ffmpeg 200MB를 다시 안 받는다) |

맥·리눅스는 `./packaging/build.sh`. **크로스 빌드는 안 된다** — 해당 OS에서 돌려야 한다.

### 실제로 확인한 것 (윈도우, 2026-08-29)

각 단계는 실제로 돌려서 결과를 봤다. 다시 할 때도 같은 방식으로 확인할 것.

**1) 묶인 앱이 뜨는가**

```powershell
$env:PATH = "C:\Windows\system32;C:\Windows"   # ffmpeg도 node도 없는 PATH
.\dist\YoutubeClipper\YoutubeClipper.exe --no-open --port 8765
curl http://127.0.0.1:8765/api/health
# {"ok":true,"ffmpeg":true, ... "work_dir":"...\\LocalAppData\\YoutubeClipper\\work"}
```

`ffmpeg:true`가 핵심이다 — PATH에 없는데 true면 묶어 온 것을 쓰고 있다는 뜻이다.

**2) 그 상태로 실제 유튜브 전 과정**

같은 clean PATH에서 링크 → 구간 3개 → 준비 → 렌더 → 저장까지 API로 완주시켰고,
결과는 **48.000초 h264+aac 2.9MB**로 개발 실행본과 바이트까지 같았다.

**3) quickjs만으로 챌린지가 풀리는가**

node·deno·bun을 전부 뺀 PATH에 `qjs.exe`만 두고 진단 8단계 통과 (2.4 참고).

**4) 앱 창과 종료 정리**

인자 없이 실행하면 WebView2 창이 뜨고 `file_picker:true`가 된다(OS 파일 선택 가능).
창을 닫으면 3초 안에 서버가 내려가고 프로세스가 남지 않는다.
`--port`를 달리해 두 개를 동시에 띄워도 충돌하지 않는다.

**반드시 콘솔 없이도 확인할 것** — 이것 때문에 한 번 크게 틀렸다. 셸에서 실행하면
GUI 앱이라도 콘솔 핸들을 물려받아 `sys.stdout`이 멀쩡하다. 바탕화면 아이콘으로
누르면 None이 되고, 그 차이 하나로 서버가 아예 안 떴다. 셸 테스트는 전부 통과했는데
설치본만 안 되는 상황이 나온다.

```powershell
Start-Process ".\dist\YoutubeClipper\YoutubeClipper.exe"   # 콘솔 없이
# 12초쯤 뒤 리스닝 포트가 있는지 확인
Get-NetTCPConnection -State Listen | Where-Object {
    $_.OwningProcess -in (Get-Process YoutubeClipper).Id }
```

**문제가 생기면 로그부터** — `%LOCALAPPDATA%\YoutubeClipper\app.log`.
창 모드에서는 콘솔에 찍어야 아무도 못 보므로, 시작 시각과 오류를 이 파일에 남긴다.
서버가 끝내 못 뜨면 메시지 상자로 원인과 로그 위치를 알려준다.

**5) 설치 파일**

`YoutubeClipper-setup.exe` 79MB (푼 크기 253MB). 관리자 권한 없이 사용자 폴더에 설치된다.

### 설치 프로그램

| OS | 도구 | 결과물 |
|---|---|---|
| Windows | [Inno Setup](https://jrsoftware.org/isinfo.php) — `winget install JRSoftware.InnoSetup` | `YoutubeClipper-setup.exe` |
| macOS | `hdiutil` + `create-dmg` | `YoutubeClipper.dmg` |
| Linux | tar.gz 또는 AppImage | `YoutubeClipper.tar.gz` |

**서명 문제** — 서명하지 않으면 윈도우는 SmartScreen 경고, 맥은 Gatekeeper가 막는다.
개인 개발자에게는 비용이 든다(윈도우 코드사이닝 인증서 연 10만원대, 애플 개발자 프로그램 연 $99).

우선은 서명 없이 배포하고 README에 우회 방법을 적는다.

- 윈도우: `추가 정보` → `실행`
- 맥: 우클릭 → `열기`, 또는 `xattr -dr com.apple.quarantine /Applications/YoutubeClipper.app`

### 아직 안 한 것 — 자동 빌드

GitHub Actions에서 세 OS 러너로 빌드해 릴리스에 올린다.
`windows-latest`, `macos-latest`, `ubuntu-latest`. 태그를 밀면 설치 파일이 나오게.

---

## 4. 파일 구성

```
packaging/
├── entry.py              # 묶을 때의 진입점 (app.desktop을 패키지째 import)
├── clipper.spec          # PyInstaller 설정
├── build.ps1             # Windows 빌드 (+ -Installer)
├── build.sh              # macOS / Linux 빌드
├── fetch-binaries.ps1    # ffmpeg·qjs 받아서 packaging/bin/에 두기 (Windows)
├── fetch-binaries.sh     # 같은 것 (macOS / Linux)
├── installer.iss         # Inno Setup (Windows)
└── bin/                  # 받아둔 바이너리 (.gitignore 대상, 용량이 크다)
app/
└── updater.py            # yt-dlp 갱신 (2.5)
```

**윈도우 스크립트는 UTF-8 BOM으로 저장해야 한다.** Windows PowerShell 5.1은 BOM이 없는
`.ps1`을 ANSI(cp949)로 읽어서, 한글 주석이 든 스크립트가 통째로 파싱 오류를 낸다.
실제로 처음 실행할 때 이것부터 걸렸다.

`.gitignore`에 `packaging/bin/`, `build/`, `dist/`, `*.spec.bak`을 추가할 것.

### clipper.spec 뼈대

```python
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path(SPECPATH).parent
BIN = ROOT / "packaging" / "bin"

a = Analysis(
    [str(ROOT / "app" / "desktop.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "app" / "static"), "app/static")],
    hiddenimports=["uvicorn.logging", "uvicorn.loops.auto",
                   "uvicorn.protocols.http.auto", "uvicorn.lifespan.on"],
    hookspath=[], noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, name="YoutubeClipper", console=False,
          icon=str(ROOT / "packaging" / "icon.ico"))
coll = COLLECT(exe, a.binaries, a.datas, name="YoutubeClipper")
```

`bin/` 복사는 `COLLECT` 뒤에 후처리로 넣거나 `datas`에 `(str(BIN), "bin")`을 더한다.
yt-dlp는 `--collect-all yt_dlp` 또는 `hiddenimports`에 추출기를 명시해야 한다.

---

## 5. 완료 판정

- [x] `bin/`의 ffmpeg을 쓴다 (PATH에 ffmpeg이 없어도 동작)
- [x] JS 런타임이 잡혀 봇 차단에 걸리지 않는다 (quickjs 단독으로)
- [x] 실행하면 창이 열리고, 링크 → 구간 → 준비 → 편집 → 저장이 전부 된다
- [x] yt-dlp를 앱 안에서 갱신할 수 있다
- [x] 창을 닫으면 서버 프로세스가 남지 않는다
- [x] 두 번 실행해도 포트 충돌이 없다
- [x] 작업 파일이 사용자 폴더에 쌓이고, 비울 수 있다
- [x] 설치 파일 하나로 설치·실행된다 (79MB)
- [x] `pytest` 전부 통과 (155개)
- [ ] **Python·ffmpeg이 한 번도 깔린 적 없는 다른 PC**에서 확인
      — 빌드한 PC에서는 PATH를 비워 확인했지만, 진짜 깨끗한 기기에서 한 번 더 볼 것
- [ ] 맥에서 실제 유튜브 영상으로 완주 (맥이 필요하다)

## 6. 함정

★ 표시는 이 앱을 실제로 묶으면서 걸린 것들이다. 나머지는 예상해 둔 것.

| 증상 | 원인과 대처 |
|---|---|
| ★ `.ps1`이 통째로 파싱 오류 | PowerShell 5.1이 BOM 없는 UTF-8을 cp949로 읽는다. **UTF-8 BOM으로 저장** |
| ★ `attempted relative import with no known parent package` | `app/desktop.py`를 spec에 직접 넘기면 `__main__`이 된다. `packaging/entry.py`를 거칠 것 |
| ★ 조회는 되는데 `ffmpeg is not installed`로 다운로드만 실패 | yt-dlp의 `FFmpegFD.available()`은 넘긴 `ffmpeg_location`을 못 본다. **`bin/`을 PATH 앞에 둘 것**(`config.use_bundled_bin`) |
| ★ quickjs를 넣었는데 안 잡힘 | 실행 파일 이름은 `qjs`다 (2.4) |
| ★ 한글 경로에서 `UnicodeDecodeError` | ffmpeg 출력을 UTF-8로 읽을 것 (`media._TEXT`) |
| ★ **설치한 앱을 눌러도 아무 일이 없음** | 창 모드 앱을 콘솔 없이 실행하면 `sys.stdout`이 None이고, uvicorn이 `sys.stdout.isatty()`에서 죽는다. `desktop.ensure_streams()`가 먼저 로그 파일로 바꿔 끼운다 |
| ★ **'PC에 저장'을 눌러도 아무 일이 없음** | pywebview의 `ALLOW_DOWNLOADS`가 기본 False라 WebView2가 다운로드를 취소한다. 앱에서는 `create_file_dialog(FileDialog.SAVE)`로 저장 창을 띄우고 직접 복사한다 |
| 화면이 404 | `app/static`이 안 들어감. spec의 `datas` 확인 |
| `No module named yt_dlp.extractor.…` | 동적 import 누락. `collect_all("yt_dlp")` |
| 실행은 되는데 다운로드만 실패 | `bin/ffmpeg` 실행 권한. 맥/리눅스는 `chmod +x` |
| 맥에서 "손상된 파일" | 서명 없음. `xattr -dr com.apple.quarantine` |
| 백신이 지움 | PyInstaller 오탐. onedir + 서명으로 완화 |
| 창은 뜨는데 흰 화면 | 서버 기동 전에 창을 열었음. `wait_until_ready()` 확인 |
| 종료 후 프로세스 잔존 | `server.should_exit` 처리 확인. 스레드가 daemon인지 |

## 7. 참고

- PyInstaller: https://pyinstaller.org/en/stable/
- pywebview: https://pywebview.flowrl.com/
- Inno Setup: https://jrsoftware.org/isinfo.php
- yt-dlp 옵션: https://github.com/yt-dlp/yt-dlp#usage-and-options
- 이 저장소: **https://github.com/plnman/Vidoe_clip**
