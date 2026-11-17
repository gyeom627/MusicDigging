// 디깅 플레이어: 하이라이트 1분 재생 → 매수(→) / 패스(←)
(function () {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const $ = (id) => document.getElementById(id);
  let player = null, playerReady = false, pending = null, busy = false, timerId = null;

  async function api(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
      body: JSON.stringify(body || {}),
    });
    let data = {};
    try { data = await res.json(); } catch (e) { data = { ok: false, error: "서버 응답을 읽지 못했습니다." }; }
    if (res.status === 401) { location.href = "/login?next=/"; }
    return data;
  }

  function msg(text, isError) {
    const el = $("dig-msg");
    el.hidden = !text;
    el.textContent = text || "";
    el.className = "flash" + (isError ? " error" : "");
  }

  function setTickets(n) {
    if (typeof n !== "number") return;
    $("tickets-left").textContent = n;
    const badge = $("tickets-badge");
    if (badge) badge.textContent = n;
    $("btn-buy").disabled = n <= 0;
    $("btn-buy").title = n <= 0 ? "오늘의 매수권을 모두 썼습니다" : "";
  }

  function category() {
    return document.querySelector('input[name="category"]:checked').value;
  }

  function startTimer(sec) {
    clearInterval(timerId);
    let left = sec;
    $("timer").textContent = left + "초";
    timerId = setInterval(() => {
      left -= 1;
      $("timer").textContent = left > 0 ? left + "초" : "끝 — 매수 또는 패스";
      if (left <= 0) clearInterval(timerId);
    }, 1000);
  }

  function play(song) {
    const opts = { videoId: song.video_id, startSeconds: song.start_sec, endSeconds: song.start_sec + song.length };
    if (playerReady) { player.loadVideoById(opts); }
    else { pending = opts; }
    startTimer(song.length);
  }

  async function next() {
    if (busy) return;
    busy = true; msg("");
    $("reveal").hidden = true;
    const d = await api("/api/dig/next", { category: category() });
    busy = false;
    if (!d.ok) { msg(d.error, true); return; }
    setTickets(d.tickets_left);
    if (d.empty) {
      $("dig-stage").hidden = true;
      $("dig-start").hidden = false;
      if (player && playerReady) player.stopVideo();
      msg("이 카테고리에서 아직 듣지 않은 곡이 없습니다. 다른 카테고리를 고르거나 곡을 제보해 주세요.");
      return;
    }
    $("dig-start").hidden = true;
    $("dig-stage").hidden = false;
    $("swipe-zone").hidden = false;
    $("cat-label").textContent = { KR: "국내", JP: "J-Pop", POP: "Pop" }[d.category] || d.category;
    const chips = $("mood-chips");
    chips.textContent = "";
    d.moods.forEach((m, i) => {
      const s = document.createElement("span");
      s.className = "mood rank" + (i + 1);  // 1순위가 가장 진하게
      s.textContent = m;
      chips.appendChild(s);
    });
    play(d);
    $("swipe-zone").focus();
  }

  async function buy() {
    if (busy || $("btn-buy").disabled) return;
    busy = true;
    const d = await api("/api/dig/buy");
    busy = false;
    if (!d.ok) { msg(d.error, true); if (d.tickets_left === 0) setTickets(0); return; }
    setTickets(d.tickets_left);
    $("swipe-zone").hidden = true;  // 플레이어는 보이는 채로 두고(가리기·숨김 재생 금지) 버튼만 숨김
    $("rv-title").textContent = d.title;
    $("rv-artist").textContent = d.artist;
    $("rv-meta").textContent = [d.genre, (d.subgenres || []).join(", ")].filter(Boolean).join(" · ") +
      "  |  " + d.rank + "번째 매수자";
    $("rv-badge").hidden = !d.is_early;
    $("reveal").hidden = false;
  }

  async function pass() {
    if (busy) return;
    busy = true;
    const d = await api("/api/dig/pass");
    busy = false;
    if (!d.ok) { msg(d.error, true); return; }
    next();
  }

  async function reportError() {
    await api("/api/dig/error");
    msg("재생할 수 없는 영상이라 다음 곡으로 넘어갑니다.");
    next();
  }

  // YouTube IFrame API가 준비되면 호출됨 (플레이어는 가리지 않고 200×200 이상으로 보여야 함)
  window.onYouTubeIframeAPIReady = function () {
    player = new YT.Player("player", {
      width: "100%", height: "100%",
      playerVars: { playsinline: 1, rel: 0 },
      events: {
        onReady: () => { playerReady = true; if (pending) { player.loadVideoById(pending); pending = null; } },
        onError: () => { if (!$("dig-stage").hidden) reportError(); },
      },
    });
  };

  // 스와이프: 플레이어 위가 아닌 아래 영역에서만 (iframe 위의 터치는 페이지로 전달되지 않음)
  let x0 = null;
  const zone = $("swipe-zone");
  zone.addEventListener("pointerdown", (e) => { x0 = e.clientX; });
  zone.addEventListener("pointerup", (e) => {
    if (x0 === null) return;
    const dx = e.clientX - x0; x0 = null;
    if (dx > 80) buy(); else if (dx < -80) pass();
  });
  zone.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") buy();
    if (e.key === "ArrowLeft") pass();
  });

  $("btn-start").addEventListener("click", next);
  $("btn-next").addEventListener("click", next);
  $("btn-buy").addEventListener("click", buy);
  $("btn-pass").addEventListener("click", pass);
  setTickets(Number($("tickets-left").textContent));
})();
