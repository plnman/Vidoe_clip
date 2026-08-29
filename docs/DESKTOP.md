# 데스크톱 앱 전환 계획

> 저장소: **https://github.com/plnman/Vidoe_clip** (브랜치 `main`)
> 클론: `git clone https://github.com/plnman/Vidoe_clip.git`

4K Video Downloader처럼 **어느 PC에나 설치해서 바로 쓰는** 형태로 만드는 작업 지시서.
지금은 Python과 ffmpeg을 직접 설치해야 한다. 그 두 가지를 없애는 것이 목표다.

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

### 이미 되어 있는 것 (코드에 반영 완료)

| 파일 | 내용 |
|---|---|
| `app/desktop.py` | 진입점. 포트 자동 선택, 서버 기동 대기, 창 열기, 종료 정리 |
| `app/config.py` | `FROZEN`, `user_data_dir()`, `bundled_bin_dir()` |
| `app/media.py` | `_tool()` — 함께 묶은 ffmpeg을 PATH보다 먼저 찾는다 |
| `app/downloader.py` | 묶은 ffmpeg 위치를 yt-dlp에 `ffmpeg_location`으로 전달 |
| `tests/test_desktop.py` | 포트 선택, 기동 대기, 창 실패 시 브라우저 대체 |

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

**반드시 검증할 것** — yt-dlp의 quickjs provider가 실제로 챌린지를 푸는지는
대상 PC에서 확인해야 한다. `bin/`에 `qjs`를 넣고 `python -m app.doctor <URL>`로
확인할 것. 안 되면 deno로 바꾸고 크기를 감수한다.

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

**구현 위치** — `app/updater.py`(신규) + `app/main.py`에 엔드포인트 두 개
(`GET /api/updates`, `POST /api/updates/ytdlp`). 진입점에서 4번보다 먼저 3번을 해야 하므로
`app/desktop.py` 맨 위에서 경로를 잡는다.

**대안** — standalone `yt-dlp` 실행 파일을 `bin/`에 두고 subprocess로 부르는 방식.
교체가 파일 하나로 끝나 더 단순하지만, 지금 코드는 Python API를 쓰고 있어
`downloader.py`를 다시 써야 한다. 위 방식을 먼저 시도할 것.

### 2.6 작업 파일은 사용자 폴더에

임시 폴더는 OS가 청소해서 결과물이 사라질 수 있다. `config.FROZEN`일 때
`user_data_dir()/work`를 쓰도록 이미 바꿔뒀다.

**추가로 할 일** — 설정 화면에 `작업 폴더 비우기` 버튼. 지금은 TTL로만 지운다.

---

## 3. 단계별 작업

각 단계가 끝날 때마다 실제로 실행해서 확인하고 커밋할 것.

### 1단계 — 빌드가 되게 만든다 (ffmpeg 없이)

```bash
pip install pyinstaller pywebview
pyinstaller packaging/clipper.spec
dist/YoutubeClipper/YoutubeClipper --browser
```

확인: 창(또는 브라우저)이 열리고 1단계 화면이 뜬다. ffmpeg이 PATH에 있으면 전체 동작.

막히는 지점 두 가지가 예상된다.

- **정적 파일 누락** — `app/static/`이 안 들어가서 화면이 404. spec의 `datas`로 넣는다
- **yt-dlp 추출기 누락** — PyInstaller가 동적 import를 못 찾는다.
  `hiddenimports`에 `yt_dlp.extractor`를 넣거나 `--collect-all yt_dlp`

### 2단계 — ffmpeg을 넣는다

`packaging/bin/`에 플랫폼별 바이너리를 두고 spec이 `bin/`으로 복사하게 한다.
확인: **PATH에서 ffmpeg을 지운 상태로** 실행해 전체가 도는지.

```bash
env PATH=/usr/bin:/bin dist/YoutubeClipper/YoutubeClipper --no-open &
# 다른 터미널에서 진단
dist/YoutubeClipper/YoutubeClipper --help
```

### 3단계 — quickjs를 넣고 실제 유튜브로 검증

```bash
dist/YoutubeClipper/YoutubeClipper --no-open &
python -m app.doctor "https://youtu.be/짧은영상"
```

`자바스크립트 런타임` 항목에 `quickjs`가 잡히고, 8단계가 전부 통과해야 한다.

### 4단계 — yt-dlp 갱신 경로 (2.5)

확인: 갱신 폴더에 일부러 낡은 yt-dlp를 넣고, 갱신 버튼을 눌러 버전이 바뀌는지.
진단의 `yt-dlp 버전` 줄로 확인된다.

### 5단계 — 설치 프로그램

| OS | 도구 | 결과물 |
|---|---|---|
| Windows | [Inno Setup](https://jrsoftware.org/isinfo.php) | `YoutubeClipper-setup.exe` |
| macOS | `hdiutil` + `create-dmg` | `YoutubeClipper.dmg` |
| Linux | tar.gz 또는 AppImage | `YoutubeClipper.tar.gz` |

**서명 문제** — 서명하지 않으면 윈도우는 SmartScreen 경고, 맥은 Gatekeeper가 막는다.
개인 개발자에게는 비용이 든다(윈도우 코드사이닝 인증서 연 10만원대, 애플 개발자 프로그램 연 $99).

우선은 서명 없이 배포하고 README에 우회 방법을 적는다.

- 윈도우: `추가 정보` → `실행`
- 맥: 우클릭 → `열기`, 또는 `xattr -dr com.apple.quarantine /Applications/YoutubeClipper.app`

### 6단계 — 자동 빌드 (선택)

GitHub Actions에서 세 OS 러너로 빌드해 릴리스에 올린다.
`windows-latest`, `macos-latest`, `ubuntu-latest`. 태그를 밀면 설치 파일이 나오게.

---

## 4. 만들어야 할 파일

```
packaging/
├── clipper.spec          # PyInstaller 설정
├── build.sh              # macOS / Linux 빌드
├── build.ps1             # Windows 빌드
├── fetch-binaries.sh     # ffmpeg·qjs 받아서 packaging/bin/에 두기
├── installer.iss         # Inno Setup (Windows)
└── bin/                  # 받아둔 바이너리 (.gitignore 대상, 용량이 크다)
app/
└── updater.py            # yt-dlp 갱신 (2.5)
```

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

전부 만족해야 끝이다.

- [ ] **Python·ffmpeg이 없는 깨끗한 PC**에서 설치 파일 하나로 설치·실행된다
- [ ] 실행하면 창이 열리고, 링크 → 구간 → 준비 → 편집 → 저장이 전부 된다
- [ ] `bin/`의 ffmpeg을 쓴다 (PATH에 ffmpeg이 없어도 동작)
- [ ] JS 런타임이 잡혀 봇 차단에 걸리지 않는다
- [ ] yt-dlp를 앱 안에서 갱신할 수 있다
- [ ] 창을 닫으면 서버 프로세스가 남지 않는다
- [ ] 두 번 실행해도 포트 충돌이 없다
- [ ] 작업 파일이 사용자 폴더에 쌓이고, 비울 수 있다
- [ ] 윈도우/맥 각각에서 실제 유튜브 영상으로 완주
- [ ] `pytest` 전부 통과 (지금 101개)

## 6. 예상되는 함정

| 증상 | 원인과 대처 |
|---|---|
| 화면이 404 | `app/static`이 안 들어감. spec의 `datas` 확인 |
| `No module named yt_dlp.extractor.…` | 동적 import 누락. `--collect-all yt_dlp` |
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
