// 디깅 부스: 들어오자마자 첫 곡이 재생되고, 위로 밀면 다음 곡, 오른쪽으로 밀거나 두 번 탭하면 매수
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
  };
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

  function segment() {
    const s = state.song;
    const end = s.start_sec + s.length;
    return { start: state.mode === "intro" ? 0 : s.start_sec, end };
  }

  function paintScrub() {
    const s = state.song;
    if (!s) return;
    const seg = segment();
    const span = seg.end - seg.start || 1;
    const band = $("scrub-band");
    band.style.left = ((s.start_sec - seg.start) / span) * 100 + "%";  // 하이라이트 구간 표시
    band.style.width = (s.length / span) * 100 + "%";
    $("t-end").textContent = fmt(span);
  }

  function tick() {
    if (!player || !playerReady || !state.song || typeof player.getCurrentTime !== "function") return;
    const seg = segment();
    const t = player.getCurrentTime() - seg.start;
    const span = seg.end - seg.start || 1;
    $("scrub-fill").style.width = Math.min(100, Math.max(0, (t / span) * 100)) + "%";
    $("t-now").textContent = fmt(Math.min(span, t));
  }

  function renderSong(d) {
    $("booth").dataset.group = d.mood_groups[0] || "none";
    $("cat-tag").textContent = CAT_NAME[d.category] || d.category;
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

  function cardTransition(fn) {
    const card = $("deck-card");
    if (reduceMotion) { fn(); return; }
    card.classList.add("leaving");
    setTimeout(() => {
      fn();
      card.classList.remove("leaving");
      card.classList.add("arriving");
      requestAnimationFrame(() => requestAnimationFrame(() => card.classList.remove("arriving")));
    }, 200);
  }

  // ---------- 재생 ----------
  function load() {
    const seg = segment();
    const opts = { videoId: state.song.video_id, startSeconds: seg.start, endSeconds: seg.end };
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
      if (playerReady) { player.seekTo(segment().start, true); player.playVideo(); }
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
    const d = await api("/api/dig/next", { category: state.category });
    state.busy = false;
    if (!d.ok) { say(d.error || "다음 곡을 불러오지 못했습니다.", true); state.phase = "empty"; return; }
    setTickets(d.tickets_left);
    if (d.empty) {
      state.phase = "empty"; state.song = null;
      if (playerReady) player.stopVideo();
      $("booth").dataset.group = "none";
      say("", false, state.category === "ALL"
        ? '들을 수 있는 곡을 모두 들었어요. 새 곡을 <a href="/submit">제보</a>해 디깅 풀을 채워 주세요.'
        : '이 카테고리는 모두 들었어요. <button type="button" class="link" id="to-all">전체에서 디깅하기</button>');
      const toAll = $("to-all");
      if (toAll) toAll.addEventListener("click", () => setCategory("ALL"));
      return;
    }
    state.song = d;
    state.phase = "listening";
    cardTransition(() => renderSong(d));
    load();
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
    const sleeve = $("sleeve");
    $("sv-title").textContent = d.title;
    $("sv-artist").textContent = d.artist;
    const note = $("sv-note");
    note.textContent = "";
    const line = document.createElement("span");
    line.className = "rank-line";
    const rank = document.createElement("span");
    rank.textContent = d.rank + "번째 매수자" + (d.genre ? ", " + d.genre : "");
    line.appendChild(rank);
    if (d.is_early) {
      const fd = document.createElement("span");
      fd.className = "first-digger";
      fd.textContent = "First Digger";
      line.appendChild(fd);
    }
    note.appendChild(line);
    sleeve.classList.add("revealed");
    const card = $("deck-card");
    card.classList.remove("bought"); void card.offsetWidth; card.classList.add("bought");
    document.querySelector(".moves").hidden = true;
    $("btn-next").hidden = false;
    // 매수한 곡은 계속 들을 수 있게 재생을 멈추지 않는다 (플레이어도 계속 보이게)
  }

  function setCategory(cat) {
    state.category = cat;
    store.set("digger.cat", cat);
    document.querySelectorAll(".cat").forEach((b) => {
      const on = b.dataset.cat === cat;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-checked", on);
    });
    next({ skipPass: true });  // 카테고리를 바꾼 것은 곡을 평가한 것이 아니므로 패스로 기록하지 않음
  }

  async function reportError() {
    if (state.phase !== "listening") return;
    await api("/api/dig/error");
    state.phase = "loading";
    say("재생할 수 없는 영상이라 다음 곡으로 넘어갑니다.");
    next({ skipPass: true });
  }

  // ---------- YouTube 플레이어 ----------
  window.onYouTubeIframeAPIReady = function () {
    player = new YT.Player("player", {
      width: "100%", height: "100%",
      playerVars: { playsinline: 1, rel: 0, autoplay: 1 },
      events: {
        onReady: () => {
          playerReady = true;
          if (!state.activated) { player.mute(); $("btn-sound").hidden = false; }
          if (pendingLoad) { player.loadVideoById(pendingLoad); pendingLoad = null; }
          pollId = setInterval(tick, 250);
        },
        onError: () => reportError(),
        onStateChange: (e) => {
          if (e.data === YT.PlayerState.ENDED && state.phase === "listening") {
            say("미리듣기가 끝났어요. 마음에 들면 매수하고, 아니면 위로 밀어 넘기세요.");
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
    if (dx > 80 && Math.abs(dx) > Math.abs(dy)) { buy(); return; }            // 오른쪽 밀기 = 매수
    if (quick && Math.abs(dx) < 10 && Math.abs(dy) < 10) {                    // 두 번 탭 = 매수
      const now = Date.now();
      if (now - lastTap < 320) { buy(); lastTap = 0; } else { lastTap = now; }
    }
  });
  deck.addEventListener("pointercancel", () => { start = null; $("deck-card").classList.remove("nudge-right"); });
  deck.addEventListener("wheel", (e) => {
    if (e.deltaY > 40 && Date.now() > wheelLock) { wheelLock = Date.now() + 900; next(); }
  }, { passive: true });
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea")) return;
    activate();
    if (e.key === "ArrowDown") { e.preventDefault(); next(); }
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
  document.querySelectorAll(".cat").forEach((b) => b.addEventListener("click", () => setCategory(b.dataset.cat)));
  document.querySelectorAll(".mode-btn").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));

  // ---------- 시작: 들어오자마자 첫 곡 ----------
  setTickets(state.tickets);
  setMode(state.mode);
  document.querySelectorAll(".cat").forEach((b) => {
    const on = b.dataset.cat === state.category;
    b.classList.toggle("is-on", on);
    b.setAttribute("aria-checked", on);
  });
  next({ skipPass: true });
})();
