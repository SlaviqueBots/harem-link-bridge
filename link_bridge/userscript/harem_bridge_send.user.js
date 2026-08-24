// ==UserScript==
// @name         Harem Link Bridge — Send to Bridge
// @namespace    https://github.com/SlaviqueBots/harem-link-bridge
// @version      1.2.1
// @description  Send the current Danbooru / Rule34 post to Harem Link Bridge
// @match        https://danbooru.donmai.us/posts/*
// @match        https://*.donmai.us/posts/*
// @match        https://rule34.xxx/index.php?page=post*
// @match        https://*.rule34.xxx/index.php?page=post*
// @grant        GM_xmlhttpRequest
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// ==/UserScript==
// Works in Firefox (Violentmonkey) and Chromium (Tampermonkey / Violentmonkey).
// Chromium needs GM_xmlhttpRequest — a plain fetch from https pages cannot reach http://127.0.0.1.

(function () {
  "use strict";

  const HOOK = "http://127.0.0.1:8767/send";
  const BAR_ID = "harem-bridge-bar";

  const TOP_ACTIONS = [
    { id: "checkres", label: "checkres", title: "Send checkres to bot DMs only" },
    { id: "conjure", label: "conjure", title: "Run Conjure Finder only (DM result if from browser)" },
  ];
  const BOTH_ACTION = {
    id: "both",
    label: "both",
    title: "checkres + Conjure Finder",
  };

  const BTN_STYLE =
    "padding:3px 5px;border:none;border-radius:5px;cursor:pointer;" +
    "background:#5865f2;color:#fff;line-height:1;font:inherit;";

  function postUrl() {
    const u = new URL(location.href);
    if (/donmai\.us$/i.test(u.hostname) || u.hostname.endsWith(".donmai.us")) {
      const m = u.pathname.match(/\/posts\/(\d+)/);
      if (m) return `https://danbooru.donmai.us/posts/${m[1]}`;
    }
    if (/rule34\.xxx$/i.test(u.hostname) || u.hostname.endsWith(".rule34.xxx")) {
      const id = u.searchParams.get("id");
      if (id) return `https://rule34.xxx/index.php?page=post&s=view&id=${id}`;
    }
    return location.href.split("#")[0];
  }

  function toast(msg, ok) {
    let el = document.getElementById("harem-bridge-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "harem-bridge-toast";
      el.style.cssText =
        "position:fixed;bottom:24px;right:24px;z-index:999999;padding:10px 14px;" +
        "border-radius:8px;font:13px/1.4 system-ui,sans-serif;color:#fff;" +
        "box-shadow:0 4px 16px rgba(0,0,0,.35);max-width:320px;";
      document.body.appendChild(el);
    }
    el.style.background = ok ? "#1b8f4a" : "#c0392b";
    el.textContent = msg;
    clearTimeout(el._t);
    el._t = setTimeout(() => el.remove(), 3500);
  }

  function send(action, btn) {
    const url = postUrl();
    const body = JSON.stringify({ url: url, source: "browser", action: action });
    if (typeof GM_xmlhttpRequest !== "function") {
      toast("Install Tampermonkey/Violentmonkey with GM_xmlhttpRequest enabled.", false);
      return;
    }
    const prev = btn.textContent;
    btn.disabled = true;
    btn.textContent = "…";
    GM_xmlhttpRequest({
      method: "POST",
      url: HOOK,
      headers: { "Content-Type": "application/json" },
      data: body,
      timeout: 12000,
      onload(res) {
        btn.disabled = false;
        btn.textContent = prev;
        try {
          const j = JSON.parse(res.responseText || "{}");
          if (j.ok) toast("Sent to Bridge (" + action + ")", true);
          else toast(j.error || "Bridge rejected request", false);
        } catch (_e) {
          toast("Bridge replied unexpectedly", false);
        }
      },
      onerror() {
        btn.disabled = false;
        btn.textContent = prev;
        toast("Bridge not running? Start Harem Link Bridge on this PC.", false);
      },
      ontimeout() {
        btn.disabled = false;
        btn.textContent = prev;
        toast("Bridge timed out", false);
      },
    });
  }

  function makeButton(act, flex) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = act.label;
    btn.title = act.title;
    btn.style.cssText = BTN_STYLE + (flex ? "flex:1;min-width:0;" : "width:100%;box-sizing:border-box;");
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      send(act.id, btn);
    });
    return btn;
  }

  function inject() {
    if (document.getElementById(BAR_ID)) return;
    const bar = document.createElement("div");
    bar.id = BAR_ID;
    bar.style.cssText =
      "position:fixed;top:72px;right:16px;z-index:999998;display:flex;flex-direction:column;gap:3px;" +
      "padding:3px;border-radius:7px;background:rgba(30,31,34,.92);" +
      "box-shadow:0 2px 10px rgba(0,0,0,.25);font:600 10px system-ui,sans-serif;";

    const topRow = document.createElement("div");
    topRow.style.cssText = "display:flex;gap:3px;width:100%;";
    for (const act of TOP_ACTIONS) {
      topRow.appendChild(makeButton(act, true));
    }

    const bottomRow = document.createElement("div");
    bottomRow.style.cssText = "width:100%;";
    bottomRow.appendChild(makeButton(BOTH_ACTION, false));

    bar.appendChild(topRow);
    bar.appendChild(bottomRow);
    document.body.appendChild(bar);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", inject);
  } else {
    inject();
  }
})();
