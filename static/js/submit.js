// 곡 제보: 링크 확인 → 곡 정보 → AI 추천 칩 확인·수정 → 게시 (서버 판정은 submit.py, 화면은 결과만 보여 준다)
(function () {
  const $ = (id) => document.getElementById(id);
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const { options, limits } = JSON.parse($("tag-options").textContent);
  const AXES = [  // 화면 순서와 이름. single = 하나만 고르는 축
    { key: "moods", name: "분위기", note: "1~3개, 누른 순서가 순위" },
    { key: "genre", name: "장르", note: "1개", single: true },
    { key: "subgenres", name: "세부 장르", note: "0~2개", fold: true },
    { key: "energy", name: "에너지", note: "0~1개", single: true, optional: true },
    { key: "situations", name: "어울리는 상황", note: "0~2개" },
    { key: "times", name: "시간·날씨", note: "0~2개" },
    { key: "instruments", name: "들리는 악기", note: "0~4개" },
  ];
  const GROUP_OF = {};
  Object.entries(options.moods).forEach(([g, ms]) => ms.forEach((m) => { GROUP_OF[m] = g; }));

  let player = null, playerReady = false, pendingVideo = null;
  let category = null, selected = null, suggested = null, busy = false;

  async function api(path, body) {
    try {
      const res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json", "X-CSRFToken": csrf },
                                      body: JSON.stringify(body || {}) });
      if (res.status === 401) { location.href = "/login?next=/submit"; return { ok: false }; }
      return await res.json();
    } catch (e) {
      return { ok: false, error: "서버에 연결하지 못했어요. 인터넷 연결을 확인해 주세요." };
    }
  }

  function msg(id, text, kind) {
    const el = $(id);
    el.hidden = !text;
    el.textContent = text || "";
    el.className = "msg" + (kind ? " " + kind : "");
  }

  const fmt = (s) => Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0");
  function parseTime(t) {
    const m = /^(\d{1,2}):([0-5]\d)$/.exec((t || "").trim());
    return m ? Number(m[1]) * 60 + Number(m[2]) : null;
  }

  // ---------- 1. 링크 ----------
  async function check(e) {
    if (e) e.preventDefault();
    if (busy) return;
    busy = true;
    $("btn-check").disabled = true;
    msg("link-msg", "영상 정보를 확인하고 있어요.");
    const d = await api("/api/submit/check", { url: $("url").value });
    busy = false;
    $("btn-check").disabled = false;
    if (!d.ok) { msg("link-msg", d.error || "확인하지 못했어요.", "error"); return; }
    msg("link-msg", d.warn || "등록할 수 있는 영상이에요.", d.warn ? "warn" : "ok");
    $("title").value = d.title;
    $("artist").value = d.artist;
    $("video-meta").textContent = d.video_title + " · " + d.channel + " · " + fmt(d.duration);
    $("step-info").hidden = false;
    $("step-tags").hidden = true;
    msg("info-msg", "");
    loadVideo(d.video_id);
    $("step-info").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function loadVideo(id) {
    if (playerReady) player.cueVideoById(id); else pendingVideo = id;
  }

  window.onYouTubeIframeAPIReady = function () {
    player = new YT.Player("player", {
      width: "100%", height: "100%", playerVars: { playsinline: 1, rel: 0 },
      events: { onReady: () => { playerReady = true; if (pendingVideo) player.cueVideoById(pendingVideo); } },
    });
  };

  // ---------- 2. 곡 정보 ----------
  function setCategory(code) {
    category = code;
    document.querySelectorAll(".seg-btn").forEach((b) => {
      const on = b.dataset.cat === code;
      b.classList.toggle("is-on", on);
      b.setAttribute("aria-checked", on);
    });
  }

  function form() {
    return { title: $("title").value.trim(), artist: $("artist").value.trim(), category,
             start_sec: parseTime($("start").value), intro: $("intro").value.trim() };
  }

  async function suggest(e) {
    e.preventDefault();
    if (busy) return;
    const f = form();
    if (!f.category) { msg("info-msg", "카테고리를 골라 주세요.", "error"); return; }
    if (f.start_sec === null) { msg("info-msg", "하이라이트 시작 시점을 분:초(예: 1:05)로 입력해 주세요.", "error"); return; }
    busy = true;
    const btn = $("btn-suggest");
    btn.disabled = true; btn.textContent = "AI가 듣는 중";
    msg("info-msg", "같은 곡의 다른 영상 조회수를 확인하고, Gemini가 하이라이트 60초를 듣고 있어요. 최대 1분쯤 걸려요.");
    const d = await api("/api/submit/suggest", f);
    busy = false;
    btn.disabled = false; btn.textContent = "AI 추천 다시 받기";
    if (!d.ok) { msg("info-msg", d.error || "추천을 받지 못했어요.", "error"); $("step-tags").hidden = true; return; }
    msg("info-msg", "");
    selected = d.selected; suggested = d.suggested;
    renderNotes(d);
    renderAxes();
    $("step-tags").hidden = false;
    msg("tags-msg", "");
    $("step-tags").scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function note(text, kind, linkText, href) {
    const p = document.createElement("p");
    p.className = "note " + (kind || "");
    p.textContent = text;
    if (href) {
      const a = document.createElement("a");
      a.href = href; a.target = "_blank"; a.rel = "noopener"; a.textContent = linkText;
      p.append(" ", a);
    }
    return p;
  }

  function renderNotes(d) {
    const box = $("notes");
    box.textContent = "";
    if (d.views.status === "PASS") box.appendChild(note("곡 단위 조회수 확인을 통과했어요.", "ok"));
    else box.appendChild(note("관리자 확인 후 공개돼요. " + d.views.reason, "warn"));
    if (d.mv) {
      const p = note("검색에 이 곡의 MV가 있어요: " + d.mv.title, "");
      const b = document.createElement("button");
      b.type = "button"; b.className = "btn-line small"; b.textContent = "MV로 바꿔서 등록";
      b.addEventListener("click", () => { $("url").value = "https://youtu.be/" + d.mv.video_id; check(); });
      p.append(" ", b);
      box.appendChild(p);
    }
    if (d.audio_msg) box.appendChild(note(d.audio_msg, "warn"));
    if (d.web.status === "FOUND") box.appendChild(note("장르 칩 일부는 Discogs 정보예요.", "", "Data provided by Discogs", d.web.url));
  }

  // ---------- 3. 칩 ----------
  function values(key) {
    const o = options[key];
    if (key === "moods") return Object.values(o).flat();
    if (key === "subgenres") return Object.values(o).flat();
    return o;
  }

  function isOn(key, v) {
    const s = selected[key];
    return Array.isArray(s) ? s.includes(v) : s === v;
  }

  function toggle(ax, v) {
    if (ax.single) {
      selected[ax.key] = selected[ax.key] === v ? null : v;
    } else {
      const list = selected[ax.key] = selected[ax.key] || [];
      const i = list.indexOf(v);
      if (i >= 0) list.splice(i, 1);
      else if (list.length >= limits[ax.key][1]) { msg("tags-msg", ax.name + "는 " + limits[ax.key][1] + "개까지 고를 수 있어요. 하나를 끄고 골라 주세요.", "error"); return; }
      else list.push(v);
    }
    msg("tags-msg", "");
    renderAxes();
  }

  function chip(ax, v) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip";
    b.setAttribute("aria-pressed", isOn(ax.key, v));
    if (ax.key === "moods") b.dataset.g = GROUP_OF[v];
    b.textContent = v;
    if (ax.key === "moods" && isOn("moods", v)) {
      const r = document.createElement("span");
      r.className = "rank";
      r.textContent = selected.moods.indexOf(v) + 1;
      b.prepend(r);
    }
    const src = (suggested[ax.key] || {})[v];
    if (src) {
      const m = document.createElement("span");
      m.className = "mark";
      m.textContent = src === "AI" ? "AI" : (src === "DISCOGS" ? "Discogs" : "AI·Discogs");
      b.appendChild(m);
    }
    b.addEventListener("click", () => toggle(ax, v));
    return b;
  }

  const unfolded = {};
  function renderAxes() {
    const box = $("axes");
    box.textContent = "";
    AXES.forEach((ax) => {
      const sec = document.createElement("div");
      sec.className = "axis";
      const h = document.createElement("p");
      h.className = "axis-name";
      h.textContent = ax.name + " ";
      const small = document.createElement("small");
      small.textContent = ax.note;
      h.appendChild(small);
      sec.appendChild(h);
      const chips = document.createElement("div");
      chips.className = "chips";
      let list = values(ax.key);
      const shortList = ax.fold && !unfolded[ax.key];
      if (shortList) list = list.filter((v) => isOn(ax.key, v) || (suggested[ax.key] || {})[v]);
      list.forEach((v) => chips.appendChild(chip(ax, v)));
      if (ax.fold) {
        const more = document.createElement("button");
        more.type = "button";
        more.className = "chip more";
        more.textContent = shortList ? "목록에서 더 고르기" : "접기";
        more.addEventListener("click", () => { unfolded[ax.key] = !unfolded[ax.key]; renderAxes(); });
        chips.appendChild(more);
      }
      sec.appendChild(chips);
      box.appendChild(sec);
    });
  }

  async function publish() {
    if (busy) return;
    const f = form();
    if (!selected.moods || !selected.moods.length) { msg("tags-msg", "분위기를 하나 이상 골라 주세요.", "error"); return; }
    if (!selected.genre) { msg("tags-msg", "장르를 하나 골라 주세요.", "error"); return; }
    busy = true;
    $("btn-publish").disabled = true;
    msg("tags-msg", "영상을 다시 확인하고 게시하고 있어요.");
    const d = await api("/api/submit/publish", { ...f, tags: selected });
    busy = false;
    $("btn-publish").disabled = false;
    if (!d.ok) {
      msg("tags-msg", d.error || "게시하지 못했어요.", "error");
      if (d.redo) $("step-info").scrollIntoView({ behavior: "smooth" });
      return;
    }
    if (player && playerReady) player.stopVideo();
    ["step-link", "step-info", "step-tags"].forEach((id) => { $(id).hidden = true; });
    $("step-done").hidden = false;
    if (d.status === "ACTIVE") {
      $("done-title").textContent = "디깅 풀에 들어갔어요";
      $("done-body").textContent = "다른 디거들이 블라인드로 이 곡을 만나게 돼요. 제보한 곡도 디깅에서 만나면 매수할 수 있어요.";
    } else {
      $("done-title").textContent = "검토 대기로 등록했어요";
      $("done-body").textContent = "관리자가 확인하면 디깅 풀에 들어가요. 사유: " + (d.review_reason || "");
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // ---------- 연결 ----------
  $("link-form").addEventListener("submit", check);
  $("info-form").addEventListener("submit", suggest);
  $("btn-publish").addEventListener("click", publish);
  $("btn-now").addEventListener("click", () => {
    if (playerReady && typeof player.getCurrentTime === "function") $("start").value = fmt(player.getCurrentTime());
  });
  document.querySelectorAll(".seg-btn").forEach((b) => b.addEventListener("click", () => setCategory(b.dataset.cat)));
  // 제목·아티스트를 바꾸면 추천을 다시 받아야 한다 (서버도 검색한 조합과 다르면 거부)
  ["title", "artist"].forEach((id) => $(id).addEventListener("input", () => {
    if (!$("step-tags").hidden) msg("tags-msg", "제목이나 아티스트를 바꿨어요. [AI 추천 다시 받기]를 눌러 주세요.", "warn");
  }));
})();
