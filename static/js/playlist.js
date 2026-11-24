// 매수 플레이리스트: 매수한 곡을 매수 순서대로 또는 셔플로 이어 듣기
// 디깅 카드와 같은 조작(위로 밀기·휠 내림·↓ = 다음, 아래로 밀기·휠 올림·↑ = 이전), 곡이 끝나면 자동으로 다음 곡
// 플레이어는 늘 화면에 보이게 둔다 (YouTube 정책: 가리기·백그라운드 재생 금지)
(function () {
  const $ = (id) => document.getElementById(id);
  const data = JSON.parse($("pl-data").textContent);
  const songs = data.songs;
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* 저장 못 해도 동작 */ } },
  };
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const state = {
    order: data.order === "shuffle" ? "shuffle" : store.get("digger.plorder", "bought"),
    mode: store.get("digger.plmode", "intro"),   // 이어 듣기는 처음부터가 기본
    queue: songs.map((_, i) => i),                  // 재생 순서 (songs의 번호)
    pos: 0,
    activated: false,
    errors: 0,
    autoNext: store.get("digger.plauto", "1") === "1",
  };
  let player = null, ready = false, pending = null;
  let endedFor = null;   // 이미 '곡 끝' 처리한 재생 위치 (이벤트와 확인이 겹쳐도 한 번만 넘어가게)

  function say(text, isError) {
    const el = $("deck-msg");
    el.hidden = !text;
    el.textContent = text || "";
    el.className = "deck-msg" + (isError ? " error" : "");
  }
  const fmt = (s) => { s = Math.max(0, Math.round(s)); return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0"); };
  const now = () => songs[state.queue[state.pos]];

  function shuffled(list) {  // Fisher–Yates, 브라우저 난수(crypto)
    const a = list.slice();
    const r = new Uint32Array(1);
    for (let i = a.length - 1; i > 0; i--) {
      crypto.getRandomValues(r);
      const j = r[0] % (i + 1);
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  // ---------- 순서 ----------
  function setOrder(order, keepCurrent) {
    const cur = keepCurrent ? state.queue[state.pos] : null;
    state.order = order;
    store.set("digger.plorder", order);
    const all = songs.map((_, i) => i);
    if (order === "shuffle") {
      const rest = shuffled(cur === null ? all : all.filter((i) => i !== cur));
      state.queue = cur === null ? rest : [cur].concat(rest);   // 지금 곡은 그대로 두고 나머지를 섞음
      state.pos = 0;
    } else {
      state.queue = all;
      state.pos = cur === null ? 0 : cur;
    }
    document.querySelectorAll(".cat[data-order]").forEach((b) => {
      const on = b.dataset.order === order;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-checked", on);
    });
    paintQueue();
  }

  function setMode(mode) {
    state.mode = mode;
    store.set("digger.plmode", mode);
    document.querySelectorAll(".mode-btn").forEach((b) => {
      const on = b.dataset.mode === mode;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-checked", on);
    });
  }

  // ---------- 화면 ----------
  function render() {
    const s = now();
    $("booth").dataset.group = s.groups[0] || "none";
    $("pos").textContent = (state.pos + 1) + " / " + state.queue.length;
    $("sv-title").textContent = s.title;
    $("sv-artist").textContent = s.artist;
    const line = $("sv-line");
    line.textContent = s.category + (s.genre ? " · " + s.genre : "") + " · " + s.rank + "번째 매수자";
    if (s.early) {
      const fd = document.createElement("span");
      fd.className = "first-digger";
      fd.textContent = "First Digger";
      line.appendChild(fd);
    }
    const ul = $("moods");
    ul.textContent = "";
    s.moods.forEach((m, i) => {
      const li = document.createElement("li");
      li.textContent = m;
      li.dataset.g = s.groups[i];
      ul.appendChild(li);
    });
    $("btn-hl").disabled = s.start_sec < 5;
    $("scrub-fill").style.width = "0";
    $("t-now").textContent = "0:00";
    $("t-end").textContent = "-:--";
    $("scrub-band").hidden = true;
    paintQueue();
  }

  function paintQueue() {
    const ol = $("queue");
    ol.textContent = "";
    state.queue.forEach((idx, p) => {
      const s = songs[idx];
      const li = document.createElement("li");
      if (p === state.pos) li.className = "is-now";
      else if (p < state.pos) li.className = "is-done";
      const b = document.createElement("button");
      b.type = "button";
      const n = document.createElement("span"); n.className = "q-n"; n.textContent = p === state.pos ? "▶" : p + 1;
      const t = document.createElement("span"); t.textContent = s.title;
      const a = document.createElement("span"); a.className = "q-a"; a.textContent = s.artist;
      b.append(n, t, a);
      if (p === state.pos) b.setAttribute("aria-current", "true");
      b.addEventListener("click", () => { activate(); go(p, p < state.pos); });
      li.appendChild(b);
      ol.appendChild(li);
    });
    $("queue-sub").textContent = state.queue.length + "곡 · " + (state.order === "shuffle" ? "셔플" : "매수 순서");
  }

  function transition(fn, back) {
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
    endedFor = null;
    const s = now();
    const opts = { videoId: s.video_id, startSeconds: state.mode === "highlight" ? s.start_sec : 0 };
    if (!ready) { pending = opts; return; }
    if (!state.activated) player.mute();
    player.loadVideoById(opts);
  }

  function go(p, back) {
    state.pos = p;
    transition(render, back);
    load();
  }

  function next(auto) {
    if (state.pos + 1 < state.queue.length) { go(state.pos + 1, false); return; }
    // 끝까지 들었으면 처음으로 (셔플이면 다시 섞어서)
    if (state.order === "shuffle") setOrder("shuffle", false); else state.pos = 0;
    go(0, false);
    say(auto ? "플레이리스트를 다 들어서 처음부터 다시 재생해요." : "마지막 곡이라 처음으로 돌아왔어요.");
  }

  function prev() {
    if (state.pos === 0) { say("첫 곡이에요."); return; }
    say("");
    go(state.pos - 1, true);
  }

  function activate() {
    if (state.activated) return;
    state.activated = true;
    $("btn-sound").hidden = true;
    if (ready) { player.unMute(); player.setVolume(100); }
  }

  function duration() {
    if (!ready || typeof player.getDuration !== "function") return 0;
    const v = typeof player.getVideoData === "function" ? player.getVideoData() : null;
    if (v && v.video_id && v.video_id !== now().video_id) return 0;
    return player.getDuration() || 0;
  }

  function tick() {
    const dur = duration();
    if (!dur) return;
    const s = now();
    const band = $("scrub-band");
    if (band.hidden) {  // 길이를 알게 된 순간 하이라이트 띠 표시
      band.hidden = false;
      band.style.left = (s.start_sec / dur) * 100 + "%";
      band.style.width = (Math.min(data.length, dur - s.start_sec) / dur) * 100 + "%";
      $("t-end").textContent = fmt(dur);
    }
    const t = player.getCurrentTime();
    $("scrub-fill").style.width = Math.min(100, Math.max(0, (t / dur) * 100)) + "%";
    $("t-now").textContent = fmt(Math.min(dur, t));
    // ENDED 이벤트를 놓치는 경우(탭이 뒤에 있을 때 등)를 대비해 상태를 직접 확인
    if (typeof player.getPlayerState === "function" && player.getPlayerState() === YT.PlayerState.ENDED) onEnded();
  }

  function onEnded() {
    if (endedFor === state.pos) return;
    endedFor = state.pos;
    if (state.autoNext) next(true); else say("곡이 끝났어요. 위로 밀면 다음 곡이에요.");
  }

  window.onYouTubeIframeAPIReady = function () {
    player = new YT.Player("player", {
      width: "100%", height: "100%",
      playerVars: { playsinline: 1, rel: 0, autoplay: 1 },
      events: {
        onReady: () => {
          ready = true;
          if (!state.activated) { player.mute(); $("btn-sound").hidden = false; }
          if (pending) { player.loadVideoById(pending); pending = null; }
          setInterval(tick, 250);
        },
        onStateChange: (e) => {
          if (e.data === YT.PlayerState.PLAYING) state.errors = 0;
          if (e.data === YT.PlayerState.ENDED) onEnded();   // 곡이 끝나면 다음 곡 (끄면 그 자리에서 멈춤)
        },
        onError: (e) => {  // 재생이 안 되는 곡은 건너뛴다. 연달아 실패하면 멈추고 알림
          state.errors += 1;
          if (state.errors >= 3) {
            say("여러 곡이 연달아 재생되지 않아요. 인터넷 연결이나 YouTube 제한을 확인해 주세요 (오류 " + e.data + ").", true);
            return;
          }
          say("이 곡은 지금 재생할 수 없어 다음 곡으로 넘어가요 (오류 " + e.data + ").", true);
          setTimeout(() => next(false), 800);
        },
      },
    });
  };

  // ---------- 입력 ----------
  const deck = $("deck");
  let start = null, wheelLock = 0, wheelAcc = 0, wheelTimer = null;
  deck.addEventListener("pointerdown", (e) => {
    activate();
    if (e.target.closest("button, a")) return;
    start = { x: e.clientX, y: e.clientY };
    try { deck.setPointerCapture(e.pointerId); } catch (err) { /* 무시 */ }
  });
  deck.addEventListener("pointerup", (e) => {
    if (!start) return;
    const dx = e.clientX - start.x, dy = e.clientY - start.y;
    start = null;
    if (Math.abs(dy) > 60 && Math.abs(dy) > Math.abs(dx)) { if (dy < 0) next(false); else prev(); }
  });
  deck.addEventListener("pointercancel", () => { start = null; });
  // 휠은 카드 위에서만 곡을 넘긴다 (아래 재생 목록은 페이지를 내려서 본다)
  deck.addEventListener("wheel", (e) => {
    e.preventDefault();
    clearTimeout(wheelTimer);
    wheelTimer = setTimeout(() => { wheelAcc = 0; }, 250);
    if (Date.now() < wheelLock || e.deltaY === 0) return;
    if (Math.sign(e.deltaY) !== Math.sign(wheelAcc)) wheelAcc = 0;
    wheelAcc += e.deltaY;
    if (Math.abs(wheelAcc) > 60) {
      const down = wheelAcc > 0;
      wheelAcc = 0; wheelLock = Date.now() + 1200;
      if (down) next(false); else prev();
    }
  }, { passive: false });
  document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea")) return;
    activate();
    if (e.key === "ArrowDown") { e.preventDefault(); next(false); }
    else if (e.key === "ArrowUp") { e.preventDefault(); prev(); }
    else if (e.key === "s" || e.key === "S") { setOrder(state.order === "shuffle" ? "bought" : "shuffle", true); render(); }
    else if (e.key === "m" || e.key === "M") { if (ready) (player.isMuted() ? player.unMute() : player.mute()); }
  });
  document.addEventListener("pointerdown", activate, { once: true });

  $("btn-next2").addEventListener("click", () => next(false));
  $("btn-prev").addEventListener("click", prev);
  $("btn-sound").addEventListener("click", activate);
  $("auto-next").setAttribute("aria-checked", state.autoNext);
  $("auto-next").addEventListener("click", (e) => {   // 진행 바 옆 스위치: 곡이 끝나면 자동으로 다음 곡
    e.stopPropagation();
    state.autoNext = !state.autoNext;
    e.currentTarget.setAttribute("aria-checked", state.autoNext);
    store.set("digger.plauto", state.autoNext ? "1" : "0");
    endedFor = null;
  });
  document.querySelectorAll(".cat[data-order]").forEach((b) => b.addEventListener("click", () => {
    if (b.dataset.order === state.order && b.dataset.order !== "shuffle") return;
    setOrder(b.dataset.order, true);   // 셔플을 다시 누르면 남은 곡을 새로 섞음
    render();
    say(b.dataset.order === "shuffle" ? "지금 곡 다음부터 섞었어요." : "매수 순서로 바꿨어요.");
  }));
  document.querySelectorAll(".mode-btn").forEach((b) => b.addEventListener("click", () => {
    setMode(b.dataset.mode);
    if (ready) { player.seekTo(b.dataset.mode === "highlight" ? now().start_sec : 0, true); player.playVideo(); }
  }));

  // ---------- 시작 ----------
  setMode(state.mode);
  setOrder(state.order, false);
  render();
  load();
})();
