'use strict';

const $ = (id) => document.getElementById(id);
const state = {
  project: null, cards: new Map(), poll: null, resultCuts: [], resultUrl: null, resultMedia: null, password: null,
  // 앱 창이면 OS 파일 선택 창을 쓸 수 있다. 브라우저면 파일을 올려야 한다.
  filePicker: false, savedPath: null,
  // 받을 양이 영상의 이 비율을 넘으면 전체 받기가 더 빠르다. 서버가 알려준다.
  wholeFasterAbove: 0.08,
  // 편집은 즉시 화면에 반영하고 서버에는 조금 뒤에 보낸다. 그 사이에 도착한
  // 폴링 응답이 방금 한 편집을 덮어쓰지 않도록 순번으로 비교한다.
  editSeq: 0, syncedSeq: 0,
};
const hasUnsyncedEdits = () => state.editSeq !== state.syncedSeq;

/* ---------- 공용 ---------- */

async function api(path, { method = 'GET', body } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (state.password) headers['X-Clipper-Password'] = state.password;
  const res = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });

  if (res.status === 401) {
    const pw = window.prompt('비밀번호를 입력하세요');
    if (pw === null) throw new Error('취소했습니다');
    state.password = pw;
    await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pw }),
    });
    return api(path, { method, body });
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `요청 실패 (${res.status})`);
  return data;
}

// 초 -> "1:23.4" (소수점은 필요할 때만)
function fmt(seconds, withTenths = true) {
  seconds = Math.max(0, Number(seconds) || 0);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const whole = Math.floor(s);
  const tenths = Math.round((s - whole) * 10);
  const base = h
    ? `${h}:${String(m).padStart(2, '0')}:${String(whole).padStart(2, '0')}`
    : `${m}:${String(whole).padStart(2, '0')}`;
  return withTenths && tenths ? `${base}.${tenths}` : base;
}

function fmtDuration(seconds) {
  const total = Math.round(seconds || 0);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return m ? `${m}분 ${s}초` : `${s}초`;
}

// "1:23.5" / "83" / "01:02:03" -> 초. 못 읽으면 null
function parseTC(text) {
  const raw = String(text || '').trim().replace(',', '.');
  if (!/^\d{1,3}(:\d{1,2}){0,2}(\.\d{1,3})?$/.test(raw)) return null;
  return raw.split(':').reduce((acc, part) => acc * 60 + parseFloat(part), 0);
}

function show(el, visible = true) { el.hidden = !visible; }

function notice(el, text, kind = 'err') {
  if (!text) { show(el, false); return; }
  el.className = `notice ${kind}`;
  el.textContent = text;
  show(el, true);
}

function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

/* ---------- 1. 영상 불러오기 ---------- */

// 소스가 유튜브든 내 파일이든 여기서부터는 완전히 같은 흐름이다.
function openProject(project) {
  state.project = project;
  state.cards.clear();
  state.resultUrl = null;
  $('cutList').innerHTML = '';
  const video = project.video;
  $('videoThumb').src = video.thumbnail || '';
  $('videoThumb').hidden = !video.thumbnail;
  $('videoTitle').textContent = video.title;
  $('videoSub').textContent = `${video.uploader || ''} · ${fmt(video.duration, false)}`.trim();
  show($('videoMeta'), true);
  show($('segmentCard'), true);
  show($('editCard'), false);
  show($('renderCard'), false);
  show($('resultBox'), false);

  // 내 파일은 이미 전부 있다. 받을 범위를 정하는 설정들이 의미가 없다.
  const isFile = project.source === 'file';
  show($('downloadOptions'), !isFile);
  show($('fileSourceHint'), isFile);
  $('prepareBtn').textContent = isFile ? '구간 확정' : '소스 준비';

  reparse();
  $('segText').focus();
}

async function loadVideo() {
  const url = $('url').value.trim();
  if (!url) return;
  $('loadBtn').disabled = true;
  notice($('urlError'), '');
  try {
    openProject(await api('/api/projects', { method: 'POST', body: { url } }));
  } catch (err) {
    notice($('urlError'), err.message);
  } finally {
    $('loadBtn').disabled = false;
  }
}

/* ---------- 1-b. 내 영상 파일 ---------- */

function selectSource(which) {
  const file = which === 'file';
  $('tabUrl').classList.toggle('on', !file);
  $('tabFile').classList.toggle('on', file);
  show($('paneUrl'), !file);
  show($('paneFile'), file);
  notice($('urlError'), '');
}

// 같은 PC의 파일은 경로만 넘긴다. 올리지 않으니 용량과 무관하게 즉시 열린다.
async function openLocalPath(path) {
  const value = (path || '').trim();
  if (!value) return;
  $('localBtn').disabled = true;
  notice($('urlError'), '');
  try {
    openProject(await api('/api/projects/local', { method: 'POST', body: { path: value } }));
  } catch (err) {
    notice($('urlError'), err.message);
  } finally {
    $('localBtn').disabled = false;
  }
}

// 다른 기기에서 접속했을 때. XHR을 쓰는 이유는 fetch가 업로드 진행률을 주지 않아서다.
function uploadFile(file) {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append('file', file, file.name);
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api/projects/upload');
    if (state.password) xhr.setRequestHeader('X-Clipper-Password', state.password);
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      const fraction = event.loaded / event.total;
      $('uploadBar').style.width = `${fraction * 100}%`;
      $('uploadPct').textContent = `${Math.round(fraction * 100)}%`;
      $('uploadMsg').textContent =
        fraction < 1 ? `${file.name} 올리는 중` : '읽는 중';
    };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* 본문이 없을 수도 있다 */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data);
      else reject(new Error(data.detail || `업로드 실패 (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error('업로드 중 연결이 끊겼습니다'));
    xhr.send(form);
  });
}

async function openUploadedFile(file) {
  if (!file) return;
  notice($('urlError'), '');
  $('uploadBar').style.width = '0%';
  $('uploadPct').textContent = '0%';
  $('uploadMsg').textContent = `${file.name} 올리는 중`;
  show($('uploadProgress'), true);
  try {
    openProject(await uploadFile(file));
  } catch (err) {
    notice($('urlError'), err.message);
  } finally {
    show($('uploadProgress'), false);
  }
}

// 앱 창에서는 OS 파일 선택 창을 띄워 경로를 받는다(복사 없음).
// 브라우저에서는 경로를 알 수 없으므로 파일 자체를 받아 올린다.
async function chooseFile() {
  if (state.filePicker) {
    try {
      const picked = await api('/api/pick-file', { method: 'POST' });
      if (picked.path) await openLocalPath(picked.path);
      return;
    } catch (err) {
      notice($('urlError'), err.message);
      return;
    }
  }
  $('fileInput').click();
}

function bindFileSource(health) {
  state.filePicker = Boolean(health.file_picker);
  show($('pathRow'), Boolean(health.local_files));
  $('fileHint').textContent = health.local_files
    ? '이 PC의 파일은 복사하지 않고 그 자리에서 씁니다. 원본은 건드리지 않습니다.'
    : `다른 기기에서 접속 중이라 파일을 올려야 합니다(최대 ${health.max_upload_mb}MB).`;

  $('tabUrl').addEventListener('click', () => selectSource('url'));
  $('tabFile').addEventListener('click', () => selectSource('file'));
  $('pickBtn').addEventListener('click', chooseFile);
  $('fileInput').addEventListener('change', (e) => {
    openUploadedFile(e.target.files[0]);
    e.target.value = '';  // 같은 파일을 다시 골라도 change가 오도록
  });
  $('localBtn').addEventListener('click', () => openLocalPath($('localPath').value));
  $('localPath').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') openLocalPath($('localPath').value);
  });

  const drop = $('drop');
  for (const type of ['dragenter', 'dragover']) {
    drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.add('over'); });
  }
  for (const type of ['dragleave', 'drop']) {
    drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.remove('over'); });
  }
  drop.addEventListener('drop', (e) => openUploadedFile(e.dataTransfer.files[0]));
}

/* ---------- 2. 구간 파싱 미리보기 ---------- */

const reparse = debounce(async () => {
  const text = $('segText').value;
  const box = $('parseSummary');
  const issues = $('parseIssues');
  issues.innerHTML = '';
  if (!text.trim()) { show(box, false); return; }

  const duration = state.project ? state.project.video.duration : null;
  const result = await api('/api/parse', {
    method: 'POST',
    body: { text, duration, pad: Number($('pad').value) },
  });

  box.innerHTML =
    `<span>구간 <b>${result.segments.length}개</b></span>` +
    `<span>합계 <b>${fmtDuration(result.total)}</b></span>` +
    (result.source_span
      ? `<span>받을 양 <b>${fmtDuration(result.source_span)}</b></span>`
      : '');
  show(box, true);
  adviseWholeDownload({
    source: state.project ? state.project.source : 'youtube',
    options: { whole: $('whole').checked },
    task: { status: 'idle' },
    video: { duration },
    source_span: result.source_span || 0,
  });

  for (const warning of result.warnings) {
    const el = document.createElement('div');
    el.className = 'notice warn';
    el.textContent = warning;
    issues.appendChild(el);
  }
  for (const error of result.errors) {
    const el = document.createElement('div');
    el.className = 'notice err';
    el.textContent = `“${error.text}” — ${error.reason}`;
    issues.appendChild(el);
  }
  $('prepareBtn').disabled = result.segments.length === 0;
}, 350);

/* ---------- 3. 소스 준비 ---------- */

async function prepare(resend = false) {
  notice($('prepareError'), '');
  try {
    if (resend) await flushEdits();
    await api(`/api/projects/${state.project.id}/options`, {
      method: 'PATCH',
      body: {
        max_height: Number($('height').value),
        prefer: $('prefer').value,
        pad: Number($('pad').value),
        whole: $('whole').checked,
      },
    });
    if (!resend) {
      const res = await api(`/api/projects/${state.project.id}/segments`, {
        method: 'POST',
        body: { text: $('segText').value },
      });
      state.project = res.project;
    }
    state.project = await api(`/api/projects/${state.project.id}/prepare`, { method: 'POST' });
    startPolling();
  } catch (err) {
    notice($('prepareError'), err.message);
  }
}

/* ---------- 폴링 ---------- */

function startPolling() {
  stopPolling();
  applyProject(state.project);
  state.poll = setInterval(async () => {
    // 요청을 보낸 시점의 편집 순번. 응답이 오는 사이에 사용자가 구간을 고쳤다면
    // 이 응답의 구간 정보는 이미 낡은 것이다.
    const seq = state.editSeq;
    try {
      const project = await api(`/api/projects/${state.project.id}`);
      applyProject(project, seq);
      if (project.task.status !== 'running') stopPolling();
    } catch (err) {
      stopPolling();
      notice($('prepareError'), err.message);
    }
  }, 700);
}

function stopPolling() {
  if (state.poll) clearInterval(state.poll);
  state.poll = null;
}

/* ---------- 화면 갱신 ---------- */

// 진행률을 모르는 단계에서는 퍼센트 대신 흐르는 막대를 보여준다.
function setBar(bar, percentLabel, task) {
  const unknown = task.status === 'running' && task.indeterminate;
  bar.parentElement.classList.toggle('unknown', unknown);
  bar.style.width = unknown ? '' : `${task.progress * 100}%`;
  // 퍼센트를 모를 때도 받은 용량은 말해줄 수 있다. '멈춤'과 '받는 중'이 구분된다.
  percentLabel.textContent = unknown
    ? (task.detail || '시작하는 중…')
    : `${Math.round(task.progress * 100)}%`;
}

function applyProject(project, seqAtRequest = state.editSeq) {
  // 서버 응답의 구간 정보는 '보낸 뒤 편집이 없었고, 못 보낸 편집도 없을 때'만 믿는다.
  const cutsAreStale = hasUnsyncedEdits() || seqAtRequest !== state.editSeq;
  if (cutsAreStale && state.project) {
    project.cuts = state.project.cuts;
    project.pending = state.project.pending;
    project.total_duration = state.project.total_duration;
  }
  state.project = project;
  const task = project.task;
  const running = task.status === 'running';
  const isPrepare = task.kind === 'prepare';

  // 준비 진행률
  show($('prepareProgress'), running && isPrepare);
  show($('cancelBtn'), running && isPrepare);
  $('prepareBtn').disabled = running;
  if (isPrepare) {
    $('prepareMsg').textContent = task.message;
    setBar($('prepareBar'), $('preparePct'), task);
    notice($('prepareError'), task.status === 'error' ? task.error : '');
  }

  // 렌더 진행률
  const isRender = task.kind === 'render';
  show($('renderProgress'), running && isRender);
  show($('renderCancelBtn'), running && isRender);
  $('renderBtn').disabled = running || project.pending > 0;
  if (isRender) {
    $('renderMsg').textContent = task.message;
    setBar($('renderBar'), $('renderPct'), task);
    notice($('renderError'), task.status === 'error' ? task.error : '');
  }

  adviseWholeDownload(project);

  const ready = project.cuts.some((cut) => cut.ready);
  show($('editCard'), ready);
  show($('renderCard'), ready);
  if (ready) renderCuts(project);

  if (project.result) {
    showResult(project);
  }
}

// 구간 받기는 ffmpeg 한 프로세스가 단일 연결로 받는데 유튜브가 그걸 심하게 조인다.
// 전체 받기는 조각 여러 개를 동시에 받아 수십 배 빠르다. 그래서 조금만 많이 쓰면
// 통째로 받는 쪽이 이긴다 — 켜야 할 상황이면 켜 두고, 왜 그랬는지 말해준다.
function adviseWholeDownload(project) {
  const box = $('wholeAdvice');
  const span = project.source_span || 0;
  const duration = project.video.duration || 0;
  const busy = project.task.status === 'running';

  // 기본이 전체 받기다. 일부러 끈 사람에게만, 그게 손해인 경우에 말해준다.
  if (project.source === 'file' || busy || !duration || !span || $('whole').checked) {
    show(box, false);
    return;
  }

  const share = span / duration;
  if (share < state.wholeFasterAbove) { show(box, false); return; }

  box.innerHTML =
    `구간만 받도록 꺼두셨는데, 받을 양이 영상의 <b>${Math.round(share * 100)}%</b>입니다. ` +
    `구간 받기는 연결 하나로만 받아 유튜브가 속도를 조입니다 — 이 정도 분량이면 ` +
    `<b>전체 받기가 수십 배 빠릅니다.</b> 결과물과 편집은 똑같고 디스크만 더 씁니다. `;
  const button = document.createElement('button');
  button.className = 'tiny';
  button.textContent = '전체 영상 받기로 되돌리기';
  button.onclick = () => {
    $('whole').checked = true;
    show(box, false);
  };
  box.appendChild(button);
  show(box, true);
}

function renderCuts(project) {
  const list = $('cutList');
  const seen = new Set();
  const preparing = project.task.kind === 'prepare' && project.task.status === 'running';

  project.cuts.forEach((cut, index) => {
    let card = state.cards.get(cut.id);
    if (!card) {
      card = buildCard(cut.id);
      state.cards.set(cut.id, card);
    }
    updateCard(card, cut, index, project, preparing);
    list.appendChild(card.root); // 재배치 = 순서 반영
    seen.add(cut.id);
  });

  for (const [id, card] of state.cards) {
    if (!seen.has(id)) { card.root.remove(); state.cards.delete(id); }
  }

  const enabled = project.cuts.filter((c) => c.enabled);
  $('editSummary').innerHTML =
    `<span>쓸 구간 <b>${enabled.length}개</b></span>` +
    `<span>완성 길이 <b>${fmtDuration(project.total_duration)}</b></span>`;

  if (project.pending > 0 && !preparing) {
    $('staleNotice').innerHTML =
      `여유분 밖으로 넘어간 구간이 ${project.pending}개 있습니다. 그 부분만 다시 받아야 합니다. `;
    const button = document.createElement('button');
    button.className = 'tiny';
    button.textContent = '해당 구간 다시 받기';
    button.onclick = () => prepare(true);
    $('staleNotice').appendChild(button);
    show($('staleNotice'), true);
  } else {
    show($('staleNotice'), false);
  }
}

function buildCard(cutId) {
  const root = document.createElement('div');
  root.className = 'cut';
  root.innerHTML = `
    <div>
      <video preload="metadata" playsinline controls></video>
      <div class="room" style="margin-top:6px"><i></i></div>
      <div class="room-legend"><span class="before"></span><span class="after"></span></div>
    </div>
    <div class="side">
      <div class="head">
        <span class="idx"></span>
        <input type="text" class="title" placeholder="구간 이름 (선택)">
        <label class="check" title="완성본에 포함"><input type="checkbox" class="on" checked></label>
      </div>
      <div class="times">
        <div class="field"><label>시작</label><input type="text" class="start" inputmode="numeric"></div>
        <div class="field"><label>끝</label><input type="text" class="end" inputmode="numeric"></div>
        <div class="len"></div>
      </div>
      <div class="tools">
        <button class="tiny play">구간 재생</button>
        <button class="tiny ghost setIn">여기부터</button>
        <button class="tiny ghost setOut">여기까지</button>
        <button class="tiny ghost nudge" data-edge="start" data-delta="-1">시작 −1초</button>
        <button class="tiny ghost nudge" data-edge="start" data-delta="1">시작 +1초</button>
        <button class="tiny ghost nudge" data-edge="end" data-delta="-1">끝 −1초</button>
        <button class="tiny ghost nudge" data-edge="end" data-delta="1">끝 +1초</button>
      </div>
      <div class="tools">
        <button class="tiny ghost up">↑ 위로</button>
        <button class="tiny ghost down">↓ 아래로</button>
        <button class="tiny ghost del">삭제</button>
      </div>
      <div class="warn" hidden></div>
    </div>`;

  const card = {
    root,
    id: cutId,
    cut: null,
    video: root.querySelector('video'),
    title: root.querySelector('.title'),
    on: root.querySelector('.on'),
    start: root.querySelector('.start'),
    end: root.querySelector('.end'),
    len: root.querySelector('.len'),
    idx: root.querySelector('.idx'),
    warn: root.querySelector('.warn'),
    roomBar: root.querySelector('.room > i'),
    before: root.querySelector('.before'),
    after: root.querySelector('.after'),
    src: null,
    stopAt: null,
  };

  const local = () => card.cut.start - (card.cut.clip_offset || 0);

  card.title.addEventListener('change', () => edit(card, { title: card.title.value }));
  card.on.addEventListener('change', () => edit(card, { enabled: card.on.checked }));
  card.start.addEventListener('change', () => commitTime(card, 'start', card.start.value));
  card.end.addEventListener('change', () => commitTime(card, 'end', card.end.value));

  root.querySelector('.play').addEventListener('click', () => {
    if (!card.cut.ready) return;
    card.video.currentTime = Math.max(0, local());
    card.stopAt = card.cut.end - (card.cut.clip_offset || 0);
    card.video.play();
  });
  card.video.addEventListener('timeupdate', () => {
    if (card.stopAt !== null && card.video.currentTime >= card.stopAt) {
      card.video.pause();
      card.stopAt = null;
    }
  });

  root.querySelector('.setIn').addEventListener('click', () => {
    const at = (card.cut.clip_offset || 0) + card.video.currentTime;
    if (at < card.cut.end) edit(card, { start: at });
  });
  root.querySelector('.setOut').addEventListener('click', () => {
    const at = (card.cut.clip_offset || 0) + card.video.currentTime;
    if (at > card.cut.start) edit(card, { end: at });
  });

  root.querySelectorAll('.nudge').forEach((button) => {
    button.addEventListener('click', () => {
      const edge = button.dataset.edge;
      const next = card.cut[edge] + Number(button.dataset.delta);
      commitTime(card, edge, fmt(next));
    });
  });

  root.querySelector('.up').addEventListener('click', () => move(card.id, -1));
  root.querySelector('.down').addEventListener('click', () => move(card.id, 1));
  root.querySelector('.del').addEventListener('click', () => remove(card.id));

  return card;
}

function updateCard(card, cut, index, project, preparing = false) {
  card.cut = cut;
  card.idx.textContent = index + 1;
  card.root.classList.toggle('off', !cut.enabled);
  card.root.classList.toggle('stale', !cut.ready);

  if (document.activeElement !== card.title) card.title.value = cut.title || '';
  if (document.activeElement !== card.start) card.start.value = fmt(cut.start);
  if (document.activeElement !== card.end) card.end.value = fmt(cut.end);
  card.on.checked = cut.enabled;
  card.len.textContent = fmtDuration(cut.duration);

  const src = cut.ready ? `/api/projects/${project.id}/clips/${cut.clip_id}/media` : null;
  if (src !== card.src) {
    card.src = src;
    if (src) {
      card.video.src = src;
      card.video.poster = `/api/projects/${project.id}/clips/${cut.clip_id}/poster`;
    } else {
      card.video.removeAttribute('src');
      card.video.load();
    }
  }

  if (cut.ready) {
    show(card.warn, false);
    const span = cut.clip_length || 1;
    card.roomBar.style.left = `${((cut.start - cut.clip_offset) / span) * 100}%`;
    card.roomBar.style.width = `${(cut.duration / span) * 100}%`;
    card.before.textContent = `앞 여유 ${cut.room_before.toFixed(1)}초`;
    card.after.textContent = `뒤 여유 ${cut.room_after.toFixed(1)}초`;
  } else {
    card.warn.textContent = preparing
      ? '받는 중입니다…'
      : '여유분을 벗어났습니다 — 이 구간은 다시 받아야 합니다';
    show(card.warn, true);
    card.roomBar.style.width = '0%';
    card.before.textContent = '';
    card.after.textContent = '';
  }
}

/* ---------- 편집 반영 ---------- */

function commitTime(card, edge, text) {
  const seconds = parseTC(text);
  if (seconds === null) {
    card[edge].value = fmt(card.cut[edge]);
    return;
  }
  const other = edge === 'start' ? card.cut.end : card.cut.start;
  if ((edge === 'start' && seconds >= other) || (edge === 'end' && seconds <= other)) {
    card[edge].value = fmt(card.cut[edge]);
    return;
  }
  edit(card, { [edge]: seconds });
}

function edit(card, changes) {
  Object.assign(card.cut, changes);
  card.cut.duration = Math.max(0, card.cut.end - card.cut.start);
  updateCard(card, card.cut, [...state.cards.keys()].indexOf(card.id), state.project);
  // 합계 표시가 바로 따라오도록 화면에서 먼저 계산한다.
  state.project.total_duration = state.project.cuts
    .filter((c) => c.enabled)
    .reduce((sum, c) => sum + c.duration, 0);
  renderCuts(state.project);
  markDirty();
}

function move(cutId, direction) {
  const cuts = state.project.cuts;
  const from = cuts.findIndex((c) => c.id === cutId);
  const to = from + direction;
  if (from < 0 || to < 0 || to >= cuts.length) return;
  [cuts[from], cuts[to]] = [cuts[to], cuts[from]];
  renderCuts(state.project);
  markDirty();
}

function remove(cutId) {
  state.project.cuts = state.project.cuts.filter((c) => c.id !== cutId);
  renderCuts(state.project);
  markDirty();
}

let pushTimer = null;

function markDirty() {
  state.editSeq += 1;
  clearTimeout(pushTimer);
  pushTimer = setTimeout(flushEdits, 400);
}

async function pushOnce() {
  const seq = state.editSeq;
  const project = await api(`/api/projects/${state.project.id}/cuts`, {
    method: 'PATCH',
    body: {
      cuts: state.project.cuts.map((c) => ({
        id: c.id, start: c.start, end: c.end, title: c.title, enabled: c.enabled,
      })),
    },
  });
  if (seq !== state.editSeq) return; // 그 사이 또 고쳤다면 다음 저장에 맡긴다
  state.syncedSeq = seq;
  state.project = project;
  renderCuts(project);
  $('renderBtn').disabled = project.pending > 0;
}

// 만들기·소스 준비 전에는 반드시 이걸 먼저 기다린다.
// 안 그러면 마지막 편집이 서버에 닿기 전에 작업이 시작될 수 있다.
async function flushEdits() {
  clearTimeout(pushTimer);
  pushTimer = null;
  try {
    for (let attempt = 0; attempt < 3 && hasUnsyncedEdits(); attempt += 1) {
      await pushOnce();
    }
  } catch (err) {
    notice($('renderError'), err.message);
    throw err;
  }
}

/* ---------- 4. 렌더 ---------- */

async function render() {
  notice($('renderError'), '');
  show($('resultBox'), false);
  try {
    await flushEdits();
    state.resultCuts = state.project.cuts.filter((c) => c.enabled);
    state.project = await api(`/api/projects/${state.project.id}/render`, {
      method: 'POST',
      body: {
        format: $('format').value,
        quality: $('quality').value,
        separate: $('separate').checked,
        titles: $('titles').checked,
      },
    });
    startPolling();
  } catch (err) {
    notice($('renderError'), err.message);
  }
}

function fmtSize(bytes) {
  const mb = (bytes || 0) / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round((bytes || 0) / 1024))} KB`;
}

// 포맷에 맞는 미리보기 요소를 만든다. ZIP은 미리 볼 수 없다.
function buildPreview(result, url) {
  if (!result.previewable) {
    const box = document.createElement('div');
    box.className = 'notice ok';
    box.textContent = '구간마다 파일 하나씩 만들어 ZIP으로 묶었습니다. 내려받아 압축을 푸세요.';
    return { element: box, media: null };
  }
  if (result.format === 'gif') {
    const img = document.createElement('img');
    img.src = url;
    img.alt = '완성된 GIF';
    return { element: img, media: null };
  }
  const isAudio = result.format === 'mp3' || result.format === 'm4a';
  const media = document.createElement(isAudio ? 'audio' : 'video');
  media.controls = true;
  media.preload = 'metadata';
  if (!isAudio) media.playsInline = true;
  media.src = url;
  return { element: media, media };
}

async function saveResult() {
  $('saveBtn').disabled = true;
  notice($('saveNotice'), '');
  try {
    const done = await api(`/api/projects/${state.project.id}/save`, { method: 'POST' });
    if (!done.saved) return;  // 저장 창에서 취소한 것 — 알릴 것 없다
    state.savedPath = done.path;
    notice($('saveNotice'), `저장했습니다 — ${done.path}`, 'warn');
    show($('revealBtn'), true);
  } catch (err) {
    notice($('saveNotice'), err.message);
  } finally {
    $('saveBtn').disabled = false;
  }
}

function showResult(project) {
  const result = project.result;
  const url = `/api/projects/${project.id}/result?v=${encodeURIComponent(result.size)}`;

  // 폴링 때마다 다시 만들면 재생이 끊긴다. 결과가 바뀔 때만 새로 그린다.
  if (state.resultUrl !== url) {
    state.resultUrl = url;
    // 새로 만든 결과물이다. 앞서 저장한 자리를 가리키고 있으면 안 된다.
    state.savedPath = null;
    show($('revealBtn'), false);
    notice($('saveNotice'), '');
    const preview = buildPreview(result, url);
    const box = $('resultPreview');
    box.innerHTML = '';
    box.appendChild(preview.element);
    state.resultMedia = preview.media;
  }

  // 앱 창에서는 브라우저 다운로드가 막혀 있다(WebView2). 저장 창을 직접 띄운다.
  show($('downloadLink'), !state.filePicker);
  show($('saveBtn'), state.filePicker);
  $('downloadLink').href = `/api/projects/${project.id}/download`;
  $('downloadLink').setAttribute('download', result.name);
  $('resultInfo').textContent = `${result.name} · ${fmtSize(result.size)}`;

  const markers = $('resultMarkers');
  markers.innerHTML = '';
  if (state.resultMedia) {
    let offset = 0;
    state.resultCuts.forEach((cut, index) => {
      const at = offset;
      const button = document.createElement('button');
      button.textContent = `${index + 1}. ${cut.title || fmt(cut.start, false)}`;
      button.title = `완성본 ${fmt(at, false)}부터`;
      button.onclick = () => { state.resultMedia.currentTime = at + 0.05; state.resultMedia.play(); };
      markers.appendChild(button);
      offset += cut.duration;
    });
  }

  show($('resultBox'), true);
}

/* ---------- 관리 (yt-dlp 갱신 · 작업 파일) ---------- */

function fmtSizeShort(bytes) {
  const mb = (bytes || 0) / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}

async function refreshUpdateStatus() {
  try {
    const info = await api('/api/updates');
    if (info.error) {
      $('ytdlpStatus').textContent = `${info.current || '버전 불명'} · ${info.error}`;
      return;
    }
    $('ytdlpStatus').textContent = info.outdated
      ? `${info.current} → ${info.latest} 로 갱신할 수 있습니다`
      : `${info.current} · 최신입니다`;
    show($('updateBtn'), info.outdated);
  } catch (err) {
    $('ytdlpStatus').textContent = err.message;
  }
}

function bindMaintenance(health) {
  // 갱신과 정리는 서버를 켜 둔 PC에서만. 다른 기기에서 남의 파일을 지우면 곤란하다.
  if (!health.local_files) return;
  show($('maintCard'), true);
  $('workDir').textContent = health.work_dir || '';

  $('updateBtn').addEventListener('click', async () => {
    $('updateBtn').disabled = true;
    notice($('maintNotice'), '내려받는 중…', 'warn');
    try {
      const done = await api('/api/updates/ytdlp', { method: 'POST' });
      notice($('maintNotice'), `${done.version}으로 갱신했습니다. 앱을 껐다 켜면 적용됩니다.`, 'warn');
      show($('updateBtn'), false);
    } catch (err) {
      notice($('maintNotice'), err.message);
    } finally {
      $('updateBtn').disabled = false;
    }
  });

  $('clearBtn').addEventListener('click', async () => {
    $('clearBtn').disabled = true;
    try {
      const done = await api('/api/work/clear', { method: 'POST' });
      notice($('maintNotice'), `작업 파일을 비웠습니다 (${fmtSizeShort(done.freed)} 확보).`, 'warn');
      state.project = null;
      show($('segmentCard'), false);
      show($('editCard'), false);
      show($('renderCard'), false);
      show($('videoMeta'), false);
    } catch (err) {
      notice($('maintNotice'), err.message);
    } finally {
      $('clearBtn').disabled = false;
    }
  });

  refreshUpdateStatus();
}

/* ---------- 초기화 ---------- */

async function init() {
  try {
    const health = await api('/api/health');
    if (!health.ffmpeg) notice($('health'), health.error);
    const formatSelect = $('format');
    for (const [value, label] of Object.entries(health.formats || {})) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      option.selected = value === health.default_format;
      formatSelect.appendChild(option);
    }
    $('pad').value = health.defaults.pad;
    $('pad').max = health.defaults.max_pad;
    $('height').value = String(health.defaults.height);
    if (health.defaults.whole_faster_above) {
      state.wholeFasterAbove = health.defaults.whole_faster_above;
    }
    $('whole').checked = health.defaults.whole !== false;
    bindFileSource(health);
    bindMaintenance(health);
  } catch (err) {
    notice($('health'), err.message);
  }

  $('loadBtn').addEventListener('click', loadVideo);
  $('url').addEventListener('keydown', (e) => { if (e.key === 'Enter') loadVideo(); });
  $('segText').addEventListener('input', reparse);
  // 여유분과 '전체 영상 받기'가 바뀌면 받을 양도 달라진다
  $('pad').addEventListener('input', reparse);
  $('whole').addEventListener('change', reparse);
  $('prepareBtn').addEventListener('click', () => prepare(false));
  $('renderBtn').addEventListener('click', render);
  $('saveBtn').addEventListener('click', saveResult);
  $('revealBtn').addEventListener('click', async () => {
    if (!state.savedPath) return;
    try {
      await api('/api/reveal', { method: 'POST', body: { path: state.savedPath } });
    } catch (err) {
      notice($('saveNotice'), err.message);
    }
  });
  $('backToEditBtn').addEventListener('click', () => {
    $('editCard').scrollIntoView({ behavior: 'smooth' });
  });

  const cancel = async () => {
    await api(`/api/projects/${state.project.id}/cancel`, { method: 'POST' });
  };
  $('cancelBtn').addEventListener('click', cancel);
  $('renderCancelBtn').addEventListener('click', cancel);
}

init();
