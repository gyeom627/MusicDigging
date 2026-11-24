// 포트폴리오 듣기: 누른 곡을 위쪽 플레이어에서 하이라이트부터 재생. 플레이어는 늘 보이게 둔다 (YouTube 정책)
(function () {
  let player = null, ready = false, pending = null;
  const box = document.getElementById("pf-player");

  function play(btn) {
    const v = { videoId: btn.dataset.video, startSeconds: Number(btn.dataset.start) || 0 };
    box.hidden = false;
    document.getElementById("pf-now").textContent = btn.dataset.name;
    document.querySelectorAll(".play").forEach((b) => {
      const on = b === btn;
      b.classList.toggle("is-on", on);
      b.textContent = on ? "재생 중" : "듣기";
    });
    if (ready) player.loadVideoById(v); else pending = v;
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  window.onYouTubeIframeAPIReady = function () {
    player = new YT.Player("player", {
      width: "100%", height: "100%", playerVars: { playsinline: 1, rel: 0 },
      events: { onReady: () => { ready = true; if (pending) player.loadVideoById(pending); } },
    });
  };

  document.querySelectorAll(".play").forEach((b) => b.addEventListener("click", () => play(b)));
})();
