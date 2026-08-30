// ==UserScript==
// @name         Harem Link Bridge — Send to Bridge
// @namespace    https://github.com/SlaviqueBots/harem-link-bridge
// @version      1.3.0
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

  const ACTIONS = [
    [
      { id: "checkres", label: "checkres", title: "Send checkres to bot DMs only" },
      { id: "conjure", label: "conjure", title: "Run Conjure Finder only (DM result if from browser)" },
    ],
    [
      { id: "both", label: "both", title: "checkres + Conjure Finder" },
      { id: "craft", label: "craft", title: "Add this post to the open OmniCraft card’s crafting plan" },
    ],
  ];

  const BTN_STYLE =
    "flex:1 1 0;min-width:0;height:22px;padding:0 4px;border:none;border-radius:5px;" +
    "cursor:pointer;background:#5865f2;color:#fff;line-height:22px;font:inherit;" +
    "text-align:center;box-sizing:border-box;";

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

  function tagTexts(selectors) {
    const out = [];
    const seen = new Set();
    for (const sel of selectors) {
      document.querySelectorAll(sel).forEach((el) => {
        let t = (
          el.getAttribute("data-tag-name") ||
          el.getAttribute("data-name") ||
          el.textContent ||
          ""
        )
          .trim()
          .replace(/\s+/g, "_")
          .toLowerCase();
        if (t && !seen.has(t)) {
          seen.add(t);
          out.push(t);
        }
      });
    }
    return out;
  }

  function pageRating() {
    const html = document.documentElement;
    let r =
      html.dataset.rating ||
      document.body.dataset.rating ||
      "";
    const cls = (document.body.className || "") + " " + (html.className || "");
    const m = cls.match(/rating-([gsqe])/i);
    if (m) r = m[1];
    const info = document.querySelector(
      "#post-info-rating, li#post-info-rating, #stats li"
    );
    const blob = ((info && info.textContent) || document.body.innerText || "").slice(0, 8000);
    const named = blob.match(/Rating:\s*(General|Sensitive|Safe|Questionable|Explicit)/i);
    if (named) r = named[1];
    r = String(r || "").trim().toLowerCase();
    if (r === "general") return "g";
    if (r === "sensitive" || r === "safe") return "s";
    if (r === "questionable") return "q";
    if (r === "explicit") return "e";
    if ("gsqe".includes(r) && r.length === 1) return r;
    return r;
  }

  function pageTags() {
    const artists = tagTexts([
      ".artist-tag-list a.search-tag",
      "ul.artist-tag-list a",
      "li.tag-type-1 > a.search-tag",
      "#tag-sidebar li.tag-type-artist a",
      "li.tag-type-artist > a",
    ]);
    const characters = tagTexts([
      ".character-tag-list a.search-tag",
      "ul.character-tag-list a",
      "li.tag-type-4 > a.search-tag",
      "#tag-sidebar li.tag-type-character a",
      "li.tag-type-character > a",
    ]);
    const general = tagTexts([
      ".general-tag-list a.search-tag",
      "ul.general-tag-list a",
      "li.tag-type-0 > a.search-tag",
      "#tag-sidebar li.tag-type-general a",
      "li.tag-type-general > a",
    ]);
    const solo = general.includes("solo");
    return {
      artists,
      characters,
      general,
      rating: pageRating(),
      solo,
    };
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
    const payload = { url: url, source: "browser", action: action };
    if (action === "craft") payload.tags = pageTags();
    const body = JSON.stringify(payload);
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
          if (j.ok) toast(j.detail || "Sent to Bridge (" + action + ")", true);
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

  function makeButton(act) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = act.label;
    btn.title = act.title;
    btn.style.cssText = BTN_STYLE;
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
      "width:168px;padding:3px;border-radius:7px;background:rgba(30,31,34,.92);" +
      "box-shadow:0 2px 10px rgba(0,0,0,.25);font:600 10px system-ui,sans-serif;";

    for (const rowActs of ACTIONS) {
      const row = document.createElement("div");
      row.style.cssText = "display:flex;gap:3px;width:100%;";
      for (const act of rowActs) {
        row.appendChild(makeButton(act));
      }
      bar.appendChild(row);
    }
    document.body.appendChild(bar);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", inject);
  } else {
    inject();
  }
})();
