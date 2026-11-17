// 디깅 부스: 들어오자마자 첫 곡이 재생되고, 위로 밀면 다음 곡, 아래로 밀면 이전 곡, 오른쪽으로 밀거나 두 번 탭하면 매수
(function () {
  const $ = (id) => document.getElementById(id);
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const CAT_NAME = { KR: "국내", JP: "J-Pop", POP: "Pop" };
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const store = {  // 브라우저 저장소는 막혀 있을 수 있으니 실패해도 기본값으로 동작
    get(k, d) { try { return localStorage.getItem(k) || d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* 무시 */ } },
  };

  const state = {
    song: null,            // 지금 곡 (next 응답)
    phase: "loading",      // loading | listening | revealed | empty
    mode: store.get("digger.mode", "highlight"),   // highlight | intro
    category: store.get("digger.cat", "ALL"),
    activated: false,      // 사용자가 한 번이라도 누르면 소리를 켤 수 있음 (브라우저 자동재생 정책)
    busy: false,
    tickets: Number($("tickets-badge") ? $("tickets-badge").textContent : 0),
    filter: loadFilter(),  // 무드 서치 조건 { moods: [], genres: [] }
  };

  function loadFilter() {
    try {
      const f = JSON.parse(store.get("digger.filter", "{}"));
      return { moods: Array.isArray(f.moods) ? f.moods : [], genres: Array.isArray(f.genres) ? f.genres : [] };
    } catch (e) { return { moods: [], genres: [] }; }
  }
  const filterOn = (f) => f.moods.length + f.genres.length > 0;
  let player = null, playerReady = false, pendingLoad = null, pollId = null;

  // ---------- 서버 ----------
  async function api(path, body) {
    let res;
    try {
      res = await fetch(path, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
        body: JSON.stringify(body || {}),
      });
    } catch (e) {
      return { ok: false, error: "서버에 연결하지 못했습니다. 인터넷 연결을 확인하세요." };
    }
    if (res.status === 401) { location.href = "/login?next=/"; return { ok: false }; }
    try { return await res.json(); } catch (e) { return { ok: false, error: "서버 응답을 읽지 못했습니다." }; }
  }

  // ---------- 화면 ----------
  function say(text, isError, html) {
    const el = $("deck-msg");
    el.hidden = !text && !html;
    el.className = "deck-msg" + (isError ? " error" : "");
    if (html) el.innerHTML = html; else el.textContent = text || "";
  }

  function setTickets(n) {
    if (typeof n !== "number") return;
    state.tickets = n;
    if ($("tickets-badge")) $("tickets-badge").textContent = n;
    const buy = $("btn-buy");
    buy.disabled = n <= 0;
    $("buy-label").textContent = n <= 0 ? "매수권 소진 (자정 충전)" : "매수";
  }

  function fmt(sec) {
    sec = Math.max(0, Math.round(sec));
    return Math.floor(sec / 60) + ":" + String(sec % 60).padStart(2, "0");
  }

  // 곡 전체를 끝까지 재생한다. 시작 위치만 고른다: 하이라이트부터 / 처음부터
  function startAt() {
    return state.mode === "intro" ? 0 : state.song.start_sec;
  }

  function duration() {  // 지금 곡의 길이 (영상이 바뀌는 중이면 0)
    if (!player || !playerReady || typeof player.getDuration !== "function") return 0;
    const v = typeof player.getVideoData === "function" ? player.getVideoData() : null;
    if (v && v.video_id && v.video_id !== state.song.video_id) return 0;
    return player.getDuration() || 0;
  }

  function paintScrub() {  // 진행 바 = 곡 전체, 옅은 띠 = 하이라이트 1분
    const s = state.song;
    if (!s) return;
    const dur = duration();
    const band = $("scrub-band");
    band.hidden = !dur;
    if (dur) {
      band.style.left = (s.start_sec / dur) * 100 + "%";
      band.style.width = (Math.min(s.length, dur - s.start_sec) / dur) * 100 + "%";
    }
    $("t-end").textContent = dur ? fmt(dur) : "-:--";
  }

  function tick() {
    if (!player || !playerReady || !state.song || typeof player.getCurrentTime !== "function") return;
    const dur = duration();
    if (!dur) return;
    if ($("t-end").textContent === "-:--") paintScrub();   // 길이를 처음 알게 된 순간
    const t = player.getCurrentTime();
    $("scrub-fill").style.width = Math.min(100, Math.max(0, (t / dur) * 100)) + "%";
    $("t-now").textContent = fmt(Math.min(dur, t));
  }

  function renderSong(d) {
    $("booth").dataset.group = d.mood_groups[0] || "none";
    $("cat-tag").textContent = catLabel(d.category);
    $("scrub-fill").style.width = "0";
    $("t-now").textContent = "0:00";
    const ul = $("moods");
    ul.textContent = "";
    d.moods.forEach((m, i) => {
      const li = document.createElement("li");
      li.textContent = m;
      li.dataset.g = d.mood_groups[i];
      ul.appendChild(li);
    });
    const sleeve = $("sleeve");
    sleeve.classList.remove("revealed");
    $("sv-title").innerHTML = '<span class="redact" style="width:62%"></span>';
    $("sv-artist").innerHTML = '<span class="redact" style="width:34%"></span>';
    $("sv-note").textContent = "제목과 아티스트는 매수하면 공개돼요";
    $("btn-next").hidden = true;
    document.querySelector(".moves").hidden = false;
    $("btn-intro").disabled = d.start_sec < 5;  // 하이라이트가 곧 처음이면 고를 필요 없음
    paintScrub();
  }

  function catLabel(cat) {  // 카드 왼쪽 위: 곡의 카테고리 + 무드 서치 조건
    const f = state.filter, picked = f.moods.concat(f.genres);
    const name = cat === "ALL" ? "전체" : (CAT_NAME[cat] || cat);
    if (!picked.length) return name;
    return name + " · " + picked.slice(0, 2).join(", ") + (picked.length > 2 ? " 외 " + (picked.length - 2) : "");
  }

  function cardTransition(fn, back) {  // 다음 곡은 위로, 이전 곡은 아래로 빠진다
    const card = $("deck-card");
    if (reduceMotion) { fn(); return; }
    const out = back ? "leaving-back" : "leaving", into = back ? "arriving-back" : "arriving";
    card.classList.add(out);
    setTimeout(() => {
      fn();
      card.classList.remove(out);
      card.classList.add(into);
      requestAnimationFrame(() => requestAnimationFrame(() => card.classList.remove(into)));
    }, 200);
  }

  // ---------- 재생 ----------
  function load() {
    const opts = { videoId: state.song.video_id, startSeconds: startAt() };
    if (!playerReady) { pendingLoad = opts; return; }
    if (!state.activated) player.mute();
    player.loadVideoById(opts);
  }

  function setMode(mode) {
    state.mode = mode;
    store.set("digger.mode", mode);
    document.querySelectorAll(".mode-btn").forEach((b) => {
      const on = b.dataset.mode === mode;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-checked", on);
    });
    if (state.song && state.phase !== "empty") {
      paintScrub();
      if (playerReady) { player.seekTo(startAt(), true); player.playVideo(); }
    }
  }

  function activate() {  // 첫 터치·클릭·키 입력에서 소리 켜기
    if (state.activated) return;
    state.activated = true;
    $("btn-sound").hidden = true;
    if (playerReady) { player.unMute(); player.setVolume(100); }
  }

  // ---------- 동작 ----------
  async function next(opts) {
    if (state.busy) return;
    state.busy = true;
    say("");
    if (state.phase === "listening" && !(opts && opts.skipPass)) {
      const p = await api("/api/dig/pass");      // 들은 곡을 넘김 (매수권 0이면 서버가 기록하지 않음)
      if (!p.ok && p.error) { say(p.error, true); }
    }
    state.phase = "loading";
    const d = await api("/api/dig/next", { category: state.category, moods: state.filter.moods,
                                           genres: state.filter.genres, fresh: !!(opts && opts.fresh) });
    state.busy = false;
    if (!d.ok) { say(d.error || "다음 곡을 불러오지 못했습니다.", true); state.phase = "empty"; return; }
    setTickets(d.tickets_left);
    if (d.empty) {
      state.phase = "empty"; state.song = null;
      if (playerReady) player.stopVideo();
      showEmpty(d.stats || {});
      return;
    }
    present(d, false);
  }

  async function prev() {  // 릴스처럼 이전 곡으로. 지금 곡은 패스로 기록하지 않는다 (아직 판단 전)
    if (state.busy) return;
    state.busy = true;
    const d = await api("/api/dig/prev");
    state.busy = false;
    if (!d.ok) { say(d.error || "이전 곡을 불러오지 못했습니다.", true); return; }
    if (d.none) { say("처음 들은 곡이에요. 더 이전 곡은 없어요."); return; }
    say("");
    present(d, true);
  }

  function present(d, back) {  // 받은 곡을 카드에 올리고 재생
    setTickets(d.tickets_left);
    showSong();
    state.song = d;
    state.phase = d.bought ? "revealed" : "listening";
    cardTransition(() => { renderSong(d); if (d.bought) reveal(d, false); }, back);
    load();
  }

  function showEmpty(st) {
    $("booth").dataset.group = "none";
    $("stage").hidden = true;            // 재생을 멈춘 뒤에만 숨긴다
    $("song").hidden = true;
    $("empty").hidden = false;
    $("mode").hidden = true;
    $("btn-sound").hidden = true;
    const all = state.category === "ALL";
    const filtered = filterOn(state.filter);
    $("cat-tag").textContent = catLabel(state.category);
    const passed = st.passed || 0, bought = st.bought || 0;
    state.passedCount = passed;
    if (filtered) {                  // 무드 서치 조건 안에서 다 들음
      $("empty-title").textContent = "고른 무드·장르의 곡을 모두 들었어요";
      $("empty-body").textContent = "조건을 풀거나 바꾸면 다른 곡을 계속 디깅할 수 있어요."
        + (passed > 0 ? " 넘긴 곡을 다시 들을 수도 있어요." : "");
    } else if (st.active === 0) {   // 들은 게 아니라 풀 자체가 비어 있음
      $("empty-title").textContent = all ? "디깅 풀에 아직 곡이 없어요" : "이 카테고리에는 아직 곡이 없어요";
      $("empty-body").textContent = "곡이 등록되면 바로 디깅할 수 있어요. 알고 있는 숨은 곡을 제보해 주세요.";
    } else {
      $("empty-title").textContent = all ? "디깅 풀의 곡을 모두 들었어요" : "이 카테고리의 곡을 모두 들었어요";
      $("empty-body").textContent = "넘긴 곡 " + passed + "곡, 매수한 곡 " + bought + "곡이에요. "
        + (passed > 0 ? "넘긴 곡은 처음부터 다시 들을 수 있어요." : "새 곡이 등록되면 다시 디깅할 수 있어요.");
    }
    $("btn-reset").hidden = passed === 0;
    $("btn-reset").textContent = "넘긴 곡 " + passed + "곡 다시 듣기";
    $("btn-to-all").hidden = all || filtered;
    $("btn-unfilter").hidden = !filtered;
  }

  function showSong() {
    $("empty").hidden = true;
    $("mode").hidden = false;
    $("stage").hidden = false;
    $("song").hidden = false;
    if (!state.activated && playerReady) $("btn-sound").hidden = false;
  }

  async function buy() {
    if (state.busy || state.phase !== "listening" || state.tickets <= 0) {
      if (state.tickets <= 0 && state.phase === "listening") say("오늘의 매수권을 모두 썼어요. 한국 시간 자정에 10장이 다시 채워집니다.");
      return;
    }
    state.busy = true;
    const d = await api("/api/dig/buy");
    state.busy = false;
    if (!d.ok) { say(d.error, true); if (d.tickets_left === 0) setTickets(0); return; }
    setTickets(d.tickets_left);
    state.phase = "revealed";
    reveal(d, true);
    // 매수한 곡은 계속 들을 수 있게 재생을 멈추지 않는다 (플레이어도 계속 보이게)
  }

  function reveal(d, justBought) {  // 제목·아티스트 공개. 이전 곡으로 돌아온 매수 곡도 같은 모습
    const sleeve = $("sleeve");
    $("sv-title").textContent = d.title;
    $("sv-artist").textContent = d.artist;
    const note = $("sv-note");
    note.textContent = "";
    const line = document.createElement("span");
    line.className = "rank-line";
    const rank = document.createElement("span");
    rank.textContent = (justBought ? "" : "매수한 곡, ") + d.rank + "번째 매수자" + (d.genre ? ", " + d.genre : "");
    line.appendChild(rank);
    if (d.is_early) {
      const fd = document.createElement("span");
      fd.className = "first-digger";
      fd.textContent = "First Digger";
      line.appendChild(fd);
    }
    note.appendChild(line);
    sleeve.classList.toggle("revealed", justBought);
    if (justBought) {
      const card = $("deck-card");
      card.classList.remove("bought"); void card.offsetWidth; card.classList.add("bought");
    }
    document.querySelector(".moves").hidden = true;
    $("btn-next").hidden = false;
  }

  function setCategory(cat) {
    state.category = cat;
    store.set("digger.cat", cat);
    document.querySelectorAll(".cat[data-cat]").forEach((b) => {
      const on = b.dataset.cat === cat;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-checked", on);
    });
    if (draft) loadFacets();                // 무드 서치를 연 채로 카테고리를 바꾸면 숫자도 새로
    next({ skipPass: true, fresh: true });  // 카테고리를 바꾼 것은 곡을 평가한 것이 아니므로 패스로 기록하지 않음
  }

  async function resetPasses() {  // 넘긴 곡을 다시 풀에 넣고 바로 디깅 (매수한 곡은 그대로 빠져 있음)
    if (state.busy) return;
    state.busy = true;
    const d = await api("/api/dig/reset", { category: state.category });
    state.busy = false;
    if (!d.ok) { say(d.error || "다시 듣기를 준비하지 못했습니다.", true); return; }
    await next({ skipPass: true, fresh: true });
    if (state.phase === "listening") say("넘긴 곡 " + d.restored + "곡을 다시 디깅 풀에 넣었어요.");
  }

  // 재생 오류. 영상 자체 문제(100 삭제·비공개, 101·150 퍼가기 금지)만 서버에 보고하고 다음 곡으로.
  // 그 밖의 오류(2·5·153 등)는 브라우저·네트워크 쪽 문제일 수 있어 보고하지 않는다.
  // 영상 문제가 연달아 나면 학교·회사 망에서 YouTube가 제한된 것일 수 있으니 멈추고 알린다
  const VIDEO_ERRORS = [100, 101, 150];
  let errorStreak = 0;
  async function onPlayerError(code) {
    if (state.phase !== "listening") return;
    if (!VIDEO_ERRORS.includes(code) || errorStreak >= 2) {
      say("이 영상을 지금 재생하지 못했어요 (오류 " + code + "). "
        + (errorStreak >= 2 ? "여러 곡이 연달아 재생되지 않아요. 지금 쓰는 인터넷에서 YouTube가 제한됐을 수 있어요. " : "")
        + "위로 밀면 다음 곡으로 넘어가요.", true);
      return;
    }
    errorStreak += 1;
    await api("/api/dig/error");
    state.phase = "loading";
    await next({ skipPass: true });
    if (state.phase === "listening") say("재생할 수 없는 영상이라 다음 곡으로 넘어왔어요.");
  }

  // ---------- 무드 서치 ----------
  let draft = null, facetSeq = 0;
  const panel = $("filter-panel");

  function paintFilterButton() {
    const n = state.filter.moods.length + state.filter.genres.length;
    $("filter-count").textContent = n ? " " + n : "";
    $("btn-filter").classList.toggle("is-set", n > 0);
  }

  function chip(name, n, picked, axis, group) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "fchip";
    b.dataset.axis = axis;
    b.dataset.name = name;
    if (group) b.dataset.g = group;
    b.setAttribute("aria-pressed", picked);
    b.disabled = !picked && n === 0;   // 남은 곡이 없는 칸은 고를 수 없게 (이미 고른 칸은 풀 수 있게)
    b.textContent = name;
    const small = document.createElement("small");
    small.textContent = n;
    b.appendChild(small);
    b.addEventListener("click", () => {
      const list = draft[axis];
      const i = list.indexOf(name);
      if (i >= 0) list.splice(i, 1); else list.push(name);
      loadFacets();
    });
    return b;
  }

  async function loadFacets() {
    const seq = ++facetSeq;
    const d = await api("/api/dig/facets", { category: state.category, moods: draft.moods, genres: draft.genres });
    if (seq !== facetSeq || !draft) return;   // 그사이 더 새 요청이 있었으면 무시
    if (!d.ok) { say(d.error || "무드 목록을 불러오지 못했습니다.", true); return; }
    const box = $("f-moods");
    box.textContent = "";
    const groups = {};
    d.moods.forEach((m) => { (groups[m.group] = groups[m.group] || []).push(m); });
    Object.keys(groups).forEach((g) => {
      const row = document.createElement("div");
      row.className = "fgroup";
      const label = document.createElement("p");
      label.className = "fgroup-name";
      label.textContent = g;
      const chips = document.createElement("div");
      chips.className = "fchips";
      groups[g].forEach((m) => chips.appendChild(chip(m.name, m.n, draft.moods.includes(m.name), "moods", g)));
      row.append(label, chips);
      box.appendChild(row);
    });
    const gbox = $("f-genres");
    gbox.textContent = "";
    d.genres.forEach((x) => gbox.appendChild(chip(x.name, x.n, draft.genres.includes(x.name), "genres")));
    const apply = $("filter-apply");
    apply.disabled = d.matching === 0;
    apply.textContent = d.matching === 0 ? "조건에 맞는 새 곡이 없어요"
      : (filterOn(draft) ? "이 조건으로 디깅 (" + d.matching + "곡)" : "전체에서 디깅 (" + d.matching + "곡)");
  }

  function openFilter(open) {
    panel.hidden = !open;
    $("btn-filter").setAttribute("aria-expanded", open);
    if (open) {
      draft = { moods: state.filter.moods.slice(), genres: state.filter.genres.slice() };
      loadFacets();
    } else {
      draft = null;
    }
  }

  function applyFilter(f) {
    state.filter = f;
    store.set("digger.filter", JSON.stringify(f));
    paintFilterButton();
    openFilter(false);
    next({ skipPass: true, fresh: true });   // 조건을 바꾼 것은 곡을 평가한 것이 아니므로 패스로 기록하지 않음
  }

  // ---------- YouTube 플레이어 ----------
  window.onYouTubeIframeAPIReady = function () {
    player = new YT.Player("player", {
      width: "100%", height: "100%",
      playerVars: { playsinline: 1, rel: 0, autoplay: 1 },
      events: {
        onReady: () => {
          playerReady = true;
          if (!state.activated) { player.mute(); $("btn-sound").hidden = state.phase === "empty"; }
          if (pendingLoad) { player.loadVideoById(pendingLoad); pendingLoad = null; }
          pollId = setInterval(tick, 250);
        },
        onError: (e) => onPlayerError(e.data),
        onStateChange: (e) => {
          if (e.data === YT.PlayerState.PLAYING) errorStreak = 0;  // 한 곡이라도 재생되면 정상
          if (e.data === YT.PlayerState.ENDED && state.phase === "listening") {
            say("곡이 끝났어요. 마음에 들면 매수하고, 아니면 위로 밀어 넘기세요.");
          }
        },
      },
    });
  };

  // ---------- 입력: 스와이프·두 번 탭·휠·키보드 ----------
  const deck = $("deck");
  let start = null, lastTap = 0, wheelLock = 0;
  deck.addEventListener("pointerdown", (e) => {
    activate();
    if (e.target.closest("button, a")) return;
    start = { x: e.clientX, y: e.clientY, t: Date.now() };
    try { deck.setPointerCapture(e.pointerId); } catch (err) { /* 무시 */ }  // 덱 밖에서 손을 떼도 스와이프로 인식
  });
  deck.addEventListener("pointermove", (e) => {
    if (!start) return;
    const dx = e.clientX - start.x;
    $("deck-card").classList.toggle("nudge-right", dx > 40 && state.phase === "listening");
  });
  deck.addEventListener("pointerup", (e) => {
    $("deck-card").classList.remove("nudge-right");
    if (!start) return;
    const dx = e.clientX - start.x, dy = e.clientY - start.y;
    const quick = Date.now() - start.t < 600;
    start = null;
    if (dy < -60 && Math.abs(dy) > Math.abs(dx)) { next(); return; }          // 위로 밀기 = 다음 곡
    if (dy > 60 && Math.abs(dy) > Math.abs(dx)) { prev(); return; }           // 아래로 밀기 = 이전 곡
    if (dx > 80 && Math.abs(dx) > Math.abs(dy)) { buy(); return; }            // 오른쪽 밀기 = 매수
    if (quick && Math.abs(dx) < 10 && Math.abs(dy) < 10) {                    // 두 번 탭 = 매수
      const now = Date.now();
      if (now - lastTap < 320) { buy(); lastTap = 0; } else { lastTap = now; }
    }
  });
  deck.addEventListener("pointercancel", () => { start = null; $("deck-card").classList.remove("nudge-right"); });
  // 휠: 페이지를 내리지 않고 다음 곡으로. 트랙패드 관성으로 여러 곡이 넘어가지 않게 한 번에 한 곡.
  // 창이 너무 낮아 카드가 화면 밖으로 잘릴 때만, 카드 밖에서 굴린 휠은 원래대로 페이지를 움직인다
  let wheelAcc = 0, wheelTimer = null;
  $("booth").addEventListener("wheel", (e) => {
    const r = deck.getBoundingClientRect();
    const cardCut = r.bottom > window.innerHeight + 1 || r.top < -1;
    if (cardCut && !deck.contains(e.target)) return;
    e.preventDefault();
    clearTimeout(wheelTimer);
    wheelTimer = setTimeout(() => { wheelAcc = 0; }, 250);   // 휠을 멈추면 다시 셈
    if (state.busy || Date.now() < wheelLock || e.deltaY === 0) return;
    if (Math.sign(e.deltaY) !== Math.sign(wheelAcc)) wheelAcc = 0;   // 방향이 바뀌면 새로 셈
    wheelAcc += e.deltaY;
    if (Math.abs(wheelAcc) > 60) {
      const down = wheelAcc > 0;
      wheelAcc = 0; wheelLock = Date.now() + 1200;
      if (down) next(); else prev();                                   // 휠 내림 = 다음 곡, 올림 = 이전 곡
    }
  }, { passive: false });
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea")) return;
    activate();
    if (e.key === "ArrowDown") { e.preventDefault(); next(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); prev(); }
    else if (e.key === "ArrowRight") { e.preventDefault(); buy(); }
    else if (e.key === "ArrowLeft") { e.preventDefault(); next(); }
    else if (e.key === "m" || e.key === "M") { if (playerReady) (player.isMuted() ? player.unMute() : player.mute()); }
    else if (e.key === "h" || e.key === "H") { if (!$("btn-intro").disabled) setMode(state.mode === "highlight" ? "intro" : "highlight"); }
  });
  document.addEventListener("pointerdown", activate, { once: true });

  $("btn-pass").addEventListener("click", () => next());
  $("btn-next").addEventListener("click", () => next());
  $("btn-buy").addEventListener("click", buy);
  $("btn-sound").addEventListener("click", activate);
  $("btn-to-all").addEventListener("click", () => setCategory("ALL"));
  $("btn-reset").addEventListener("click", resetPasses);
  $("btn-unfilter").addEventListener("click", () => applyFilter({ moods: [], genres: [] }));
  $("btn-filter").addEventListener("click", () => openFilter(panel.hidden));
  $("filter-clear").addEventListener("click", () => { draft = { moods: [], genres: [] }; loadFacets(); });
  $("filter-apply").addEventListener("click", () => applyFilter(draft));
  document.querySelectorAll(".cat[data-cat]").forEach((b) => b.addEventListener("click", () => setCategory(b.dataset.cat)));
  document.querySelectorAll(".mode-btn").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));

  // ---------- 시작: 들어오자마자 첫 곡 ----------
  setTickets(state.tickets);
  setMode(state.mode);
  paintFilterButton();
  document.querySelectorAll(".cat[data-cat]").forEach((b) => {
    const on = b.dataset.cat === state.category;
    b.classList.toggle("is-on", on);
    b.setAttribute("aria-checked", on);
  });
  next({ skipPass: true });
})();
