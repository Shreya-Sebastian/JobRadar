// Light / dark mode. Loaded in <head> before the stylesheet paints, so a saved choice never flashes the other theme.
// Without a choice the site follows the device (prefers-color-scheme); a [data-theme-toggle] button flips it, the
// choice is kept in this browser, and a "themechange" event lets pages redraw their charts.
(() => {
  const KEY = "radar.theme";
  const root = document.documentElement;
  const dark = window.matchMedia("(prefers-color-scheme: dark)");
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") root.dataset.theme = saved;
  } catch { /* storage blocked: follow the device */ }
  const current = () => root.dataset.theme || (dark.matches ? "dark" : "light");
  const nl = () => root.lang === "nl";
  function paint() {
    const isDark = current() === "dark";
    const label = isDark ? (nl() ? "Lichte modus" : "Switch to light mode") : (nl() ? "Donkere modus" : "Switch to dark mode");
    document.querySelectorAll("[data-theme-toggle]").forEach((b) => {
      b.setAttribute("aria-pressed", String(isDark)); b.setAttribute("aria-label", label); b.title = label;
    });
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-theme-toggle]");
    if (!b) return;
    const next = current() === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem(KEY, next); } catch { /* storage blocked: this page only */ }
    paint();
    window.dispatchEvent(new CustomEvent("themechange", { detail: next }));
  });
  document.addEventListener("DOMContentLoaded", paint);
  // tells the visit statistics that this page's script ran (radar/analytics.py): scrapers that only fetch the HTML
  // never send it, whatever browser they claim to be
  addEventListener("load", () => {
    if (location.pathname.startsWith("/admin")) return;
    try {
      navigator.sendBeacon("/api/e", new Blob([JSON.stringify({ e: "seen", p: location.pathname })], { type: "application/json" }));
    } catch { /* statistics are optional */ }
  });
  if (dark.addEventListener) dark.addEventListener("change", () => { if (!root.dataset.theme) { paint(); window.dispatchEvent(new CustomEvent("themechange")); } });
})();
