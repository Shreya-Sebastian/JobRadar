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
  // ...and that someone used it: the first real key press, tap, click, scroll wheel or mouse movement. Scripted
  // browsers that load pages to copy them run the script but rarely send input
  const inputs = ["pointerdown", "keydown", "wheel", "touchstart", "mousemove"];
  const active = (ev) => {
    if (!ev.isTrusted) return;
    inputs.forEach((n) => removeEventListener(n, active, true));
    if (location.pathname.startsWith("/admin")) return;
    try {
      navigator.sendBeacon("/api/e", new Blob([JSON.stringify({ e: "active", p: location.pathname })], { type: "application/json" }));
    } catch { /* statistics are optional */ }
  };
  inputs.forEach((n) => addEventListener(n, active, { capture: true, passive: true }));

  // Consent for one first-party cookie with an anonymous ID, so the statistics can tell a returning browser from a
  // new one (radar/analytics.py `returning`, the privacy statement). Asked once; Accept and Decline look the same and
  // either closes it; "Cookie settings" in the footer asks again. Declining changes nothing on the site.
  const CONSENT = "radar_consent", ID = "radar_id", KEEP = 395 * 86400;  // 13 months
  const cookie = (n) => (document.cookie.match(new RegExp("(?:^|; )" + n + "=([^;]*)")) || [])[1];
  const setCookie = (n, v, age) => {
    document.cookie = `${n}=${v}; Max-Age=${age}; Path=/; SameSite=Lax${location.protocol === "https:" ? "; Secure" : ""}`;
  };
  const newId = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), (x) => x.toString(16).padStart(2, "0")).join("");
  function choose(yes) {
    setCookie(CONSENT, yes ? "yes" : "no", KEEP);
    if (yes && !cookie(ID)) setCookie(ID, newId(), KEEP);
    if (!yes) setCookie(ID, "", 0);
    try {
      navigator.sendBeacon("/api/e", new Blob([JSON.stringify({ e: "consent", d: yes ? "yes" : "no", p: location.pathname })], { type: "application/json" }));
    } catch { /* statistics are optional */ }
    const box = document.getElementById("consent");
    if (box) box.remove();
  }
  function ask() {
    if (document.getElementById("consent")) return;
    const t = nl()
      ? { title: "Help Tech Jobs Radar verbeteren", yes: "Accepteren", no: "Weigeren", more: "Meer in de privacyverklaring", href: "/nl/privacy#terugkerend",
          body: "Bezoeken worden zonder cookies geteld. Met toestemming krijgt deze browser ook een anonieme ID, zodat te zien is of mensen terugkomen. Geen advertenties, geen tracking op andere sites en niets wordt gedeeld." }
      : { title: "Help improve Tech Jobs Radar", yes: "Accept", no: "Decline", more: "More in the privacy statement", href: "/privacy#returning",
          body: "Visits are counted without cookies. With permission, this browser also gets an anonymous ID, so it is possible to see whether people come back. No ads, no tracking on other sites and nothing is shared." };
    const box = document.createElement("div");
    box.id = "consent";
    box.className = "consent";
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", t.title);
    box.innerHTML = `<p class="consent-title">${t.title}</p><p class="consent-body">${t.body} <a href="${t.href}">${t.more}</a></p>`
      + `<div class="consent-actions"><button type="button" data-consent="no">${t.no}</button><button type="button" data-consent="yes">${t.yes}</button></div>`;
    box.addEventListener("click", (e) => { const b = e.target.closest("[data-consent]"); if (b) choose(b.dataset.consent === "yes"); });
    document.body.appendChild(box);
  }
  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-cookie-settings]");
    if (a) { e.preventDefault(); ask(); }
  });
  document.addEventListener("DOMContentLoaded", () => {
    if (location.pathname.startsWith("/admin")) return;
    const label = nl() ? "Cookie-instellingen" : "Cookie settings";
    document.querySelectorAll(".foot-bottom").forEach((p) => p.insertAdjacentHTML("beforeend", ` · <a href="#" data-cookie-settings>${label}</a>`));
    if (!cookie(CONSENT)) ask();
  });
  if (dark.addEventListener) dark.addEventListener("change", () => { if (!root.dataset.theme) { paint(); window.dispatchEvent(new CustomEvent("themechange")); } });
})();
