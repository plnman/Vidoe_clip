# 유튜브 구간 편집기 — 작업 안내

> 저장소: **https://github.com/plnman/Vidoe_clip** (브랜치 `main`)
> 클론: `git clone https://github.com/plnman/Vidoe_clip.git`

유튜브 링크나 내 컴퓨터의 영상 파일에서 구간 목록(제미나이가 정리해준 텍스트)만큼만
잘라 이어붙이는 로컬 웹앱. Python + FastAPI + yt-dlp + ffmpeg.
설치형 데스크톱 앱으로도 묶는다(`packaging/`).

## 지금 상태

**전부 실제로 검증됐다.** 오래 남아 있던 '실제 유튜브 미검증'은 해소됐다.

- 실제 유튜브 — `f84W0KtxDls`(40:03)로 진단 8단계 통과, 구간 3개 → 48.0초 완성본
- 내 영상 파일 소스 — 같은 영상으로 19.0초 완성본
- 묶은 앱 — ffmpeg·node가 **전혀 없는 PATH**에서 위 전 과정 완주
- quickjs만으로 유튜브 챌린지 통과(node/deno 없이)
- 테스트 133개

무언가 안 될 때 첫 명령은 여전히 이것이다. 어느 단계에서 막혔는지 한 줄로 나온다:

```bash
./.venv/bin/python -m app.doctor "https://youtu.be/짧은영상ID"
```

## 먼저 읽을 것

| 문서 | 내용 |
|---|---|
| `docs/DESIGN.md` | **왜 이런 모양인지.** 결정 11개를 이유·버린 대안·되돌리면 생기는 일로 정리. 겉보기에 더 단순한 대안(전체 다운로드, 스트림 카피, 브라우저에서 처리)은 대부분 이미 검토하고 버린 것들이다. 구조를 바꾸기 전에 반드시 볼 것 |
| `docs/DESKTOP.md` | 설치형 데스크톱 앱. 결정·단계·실제로 밟은 함정·완료 판정 |
| `README.md` | 사용자용. 설치, 실행 방법, 구간 형식, 설정, 문제 해결 |

## 설계에서 절대 깨면 안 되는 것

**소스는 여유분(pad)을 붙여 한 번만 받고, 컷은 렌더할 때만 한다.**
구간을 고쳐 다시 만들어도 재다운로드가 없는 이유가 이것이다.

이 구조는 하나의 전제 위에 있다 — **받은 파일의 0초 = 소스의 요청 시각.**
`downloader.fetch_range(start=..., end=...)`가 `force_keyframes_at_cuts`를 쓰는 이유다.
`tests/test_downloader.py`가 색 블록 영상으로 이 정렬을 검증한다.
이 전제를 건드리면 `projects.Clip.covers()`부터 여유분 계산이 전부 무너진다.

**내 파일 소스는 이 구조를 그대로 탄다.** 파일은 처음부터 전부 있으므로 프로젝트를
만들 때 전체를 담은 조각 하나를 넣어둔다. 그러면 여유분이 영상 전체로 넓어진 것과
같아져서, 그다음 편집·렌더는 유튜브와 완전히 같은 길을 간다. 파일 소스 전용 렌더 경로를
따로 만들지 말 것 — 두 벌이 되는 순간 어긋난다.

## 구조

| 파일 | 역할 |
|---|---|
| `app/segments.py` | 자유 형식 구간 텍스트 파싱 (목록/표/JSON/챕터). 서버가 유일한 파서 |
| `app/downloader.py` | yt-dlp 래퍼. 구간만 받기, 화질 상한, 쿠키, JS 런타임, 오류 문구 |
| `app/media.py` | ffmpeg 래퍼. 컷+이어붙이기를 한 번의 패스로. 포맷 정의(`FORMATS`) |
| `app/projects.py` | 프로젝트 상태, 여유분 커버리지, 작업 큐. 앱의 두뇌 |
| `app/main.py` | FastAPI 라우트 |
| `app/updater.py` | yt-dlp를 앱과 따로 갱신. 묶어서 배포할 때 생명줄이다 |
| `app/doctor.py` | 실제 유튜브로 어디까지 되는지 찍어주는 진단 |
| `app/desktop.py` | 데스크톱 진입점. 포트 선택, 서버 기동 대기, 창, 파일 선택 대화상자 |
| `app/static/` | 화면. 프레임워크 없는 HTML/CSS/JS |
| `packaging/` | PyInstaller spec, 바이너리 수집, 빌드 스크립트, Inno Setup |

`app/__init__.py`에 순서가 중요한 두 줄이 있다 — yt-dlp 갱신본을 `sys.path` 앞에 두는 것과
묶어 온 `bin/`을 `PATH` 앞에 두는 것. 둘 다 다른 것을 import 하기 전이어야 해서 거기 있다.

## 이미 겪고 해결한 함정 (다시 밟지 말 것)

- **구간 다운로드는 진행률을 주지 않는다.** yt-dlp가 ffmpeg에 맡기기 때문.
  전체 다운로드는 준다. 그래서 `Task.indeterminate`로 화면에 알린다.
- **JS 런타임이 없으면 봇 차단에 걸린다.** yt-dlp 기본값은 `deno`만 본다.
  `downloader.available_js_runtimes()`가 설치된 걸 찾아 넘긴다.
- **HTTP Range 없는 서버에서는 구간 받기가 실패한다.** ffmpeg이 탐색을 못 한다.
  테스트 서버가 Range를 지원하는 이유. 유튜브는 지원하므로 실사용에선 문제없다.
- **폴링과 편집이 경합한다.** 화면에서 구간을 고치는 동안 도착한 조회 응답이
  편집을 덮어쓸 수 있다. `app.js`의 `editSeq`/`syncedSeq` 순번으로 막는다.
  렌더 전에는 `flushEdits()`로 편집을 먼저 밀어넣어야 완성본이 화면과 일치한다.
- **ffmpeg 출력을 OS 기본 인코딩으로 읽으면 안 된다.** ffmpeg은 UTF-8로 찍는데
  한글 윈도우는 cp949로 읽어서, 한글이 섞인 경로면 `probe`가 UnicodeDecodeError로 죽는다.
  `media._TEXT`를 subprocess에 항상 붙일 것.
- **묶은 ffmpeg은 옵션이 아니라 PATH로 알려야 한다.** yt-dlp는 '구간만 받기가 되나'를
  판단할 때 downloader 없이 `FFmpegFD.available()`을 부르고, 그 자리에서는 우리가 넘긴
  `ffmpeg_location`이 보이지 않는다. `config.use_bundled_bin()`이 그래서 있다.
- **런타임 이름 ≠ 실행 파일 이름.** quickjs의 실행 파일은 `qjs`다.
  `downloader.JS_RUNTIMES`가 이름과 실행 파일 후보를 따로 들고 있는 이유.

## 실행과 테스트

```bash
./run.sh                 # 로컬 (윈도우는 run.bat)
./run.sh --lan           # 같은 공유기의 다른 기기에서도
CLIPPER_PASSWORD=x ./run.sh --share   # 공개 https 주소 (cloudflared 필요)

pytest                   # 133개. 유튜브 접속 없이 전부 실제로 돌린다
python -m app.desktop    # 데스크톱 형태로 (pywebview 필요, --browser로 대체 가능)
```

설치형 앱을 만들 때 (대상 OS에서만 만들어진다):

```bash
.\packaging\build.ps1 -Installer   # 윈도우 → dist\installer\YoutubeClipper-setup.exe
./packaging/build.sh               # macOS / Linux → dist/YoutubeClipper/
```

화면까지 확인하려면:

```bash
python tests/fake_server.py 8765 &     # 유튜브 대신 합성 영상을 내주는 서버
python tests/browser_smoke.py          # playwright 필요
```

`CLIPPER_FAKE_SILENT=1`을 주면 진행률을 주지 않는 상황을 재현한다.

## 작업 규칙

- **주석과 문서, 커밋 메시지는 한국어.** 코드 식별자는 영어.
- 주석은 '무엇'이 아니라 '왜'를 적는다. 코드를 다시 읽어 쓴 주석은 넣지 않는다.
- 동작을 고치면 테스트를 함께 남긴다. 특히 실제로 겪은 버그는 반드시.
- 테스트는 유튜브에 붙지 않는다. 다운로더가 필요하면 로컬 HTTP 서버를 쓴다
  (`tests/test_downloader.py`의 Range 지원 핸들러 참고).
- yt-dlp 버전은 고정하지 않는다. 유튜브 변화 대응을 yt-dlp에 의존하는 구조다.
- 푸시는 `main`으로.

## 사용자에 대해

- 한국어로 대화한다.
- 유료 편집기가 "별것도 아닌데 비싸다"는 불만에서 출발했다. 무료·로컬이 핵심 가치.
- 집 PC와 노트북 양쪽에서 쓰는 게 목적이다. 그래서 UI는 웹이고 실행은 로컬이다.
  설치형 앱은 그 위에 포장만 더한 것이지, 웹앱 경로를 대체하지 않는다.
- 구간 목록은 제미나이로 정리해서 붙여넣는다. 파서가 형식에 관대해야 하는 이유.
- **흉내나 시뮬레이션을 싫어한다.** 실제로 돌려서 결과를 보여줄 것.
  "될 것이다"가 아니라 "돌려봤고 이렇게 나왔다"로 말한다.
