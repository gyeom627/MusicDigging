// 화면 톤 바꾸기: 기본(밝게) ↔ 다크. 이 브라우저에만 저장된다
(function () {
  const root = document.documentElement;
  const btn = document.getElementById("theme-toggle");
  if (!btn) return;
  function paint() {
    const dark = root.dataset.theme === "dark";
    btn.setAttribute("aria-pressed", dark);
    const label = dark ? "기본 모드로 바꾸기" : "다크 모드 켜기";
    btn.setAttribute("aria-label", label);
    btn.title = label;
  }
  btn.addEventListener("click", () => {
    root.dataset.theme = root.dataset.theme === "dark" ? "light" : "dark";
    try { localStorage.setItem("digger.theme", root.dataset.theme); } catch (e) { /* 저장 못 해도 이번 화면에는 적용 */ }
    paint();
  });
  paint();
})();
