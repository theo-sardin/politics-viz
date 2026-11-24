/* Site behaviour, no dependency: hover tooltips, search box, hemicycle modes, theme. */
(function () {
    "use strict";

    // ---- tooltips: any element with data-tip, on hover and on keyboard focus ----
    const tip = document.getElementById("tip");
    let tipTarget = null;

    function showTip(el, x, y) {
        const text = el.getAttribute("data-tip");
        if (!text) return;
        tipTarget = el;
        tip.replaceChildren();
        text.split("\n").forEach((line, i) => {
            const node = document.createElement(i === 0 ? "b" : "span");
            node.textContent = line;
            tip.appendChild(node);
        });
        tip.style.display = "block";
        placeTip(x, y);
    }
    function placeTip(x, y) {
        const r = tip.getBoundingClientRect();
        let left = x + 14, top = y + 16;
        if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
        if (top + r.height > window.innerHeight - 8) top = y - r.height - 12;
        tip.style.left = Math.max(8, left) + "px";
        tip.style.top = Math.max(8, top) + "px";
    }
    function hideTip() { tip.style.display = "none"; tipTarget = null; }

    document.addEventListener("pointermove", (e) => {
        const el = e.target.closest && e.target.closest("[data-tip]");
        if (!el) { if (tipTarget) hideTip(); return; }
        if (el !== tipTarget) showTip(el, e.clientX, e.clientY); else placeTip(e.clientX, e.clientY);
    });
    document.addEventListener("focusin", (e) => {
        const el = e.target.closest && e.target.closest("[data-tip]");
        if (!el) return;
        const r = el.getBoundingClientRect();
        showTip(el, r.left + r.width / 2, r.bottom);
    });
    document.addEventListener("focusout", hideTip);
    document.addEventListener("scroll", hideTip, { passive: true });

    // ---- search: the whole index is fetched once, then matched here ----
    // BASE is the path the site is served under ("" at a domain root).
    const BASE = document.documentElement.dataset.base || "";
    const input = document.getElementById("q");
    const box = document.getElementById("q-results");
    const LIMITS = { "Élu": 8, "Société": 5, "Texte": 5 };
    let index = null, loading = null, active = -1;

    const fold = (s) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase()
        .replace(/[^a-z0-9]+/g, " ").trim();

    function loadIndex() {
        if (!loading) {
            loading = fetch(BASE + "/data/search.json")
                .then((r) => (r.ok ? r.json() : []))
                .then((rows) => { index = rows; })
                .catch(() => { index = []; loading = null; });
        }
        return loading;
    }

    function lookup(q) {
        const words = fold(q).split(" ").filter(Boolean);
        if (!words.length || !index) return [];
        const taken = {}, out = [];
        for (const row of index) {  // already ordered best-first within each type
            if ((taken[row.t] || 0) >= (LIMITS[row.t] || 5)) continue;
            if (words.every((w) => row.k.includes(w))) {
                taken[row.t] = (taken[row.t] || 0) + 1;
                out.push(row);
            }
        }
        return out;
    }

    function render(results) {
        box.replaceChildren();
        active = -1;
        if (!results.length) {
            const none = document.createElement("a");
            none.style.gridTemplateColumns = "1fr";
            none.style.color = "var(--muted)";
            none.textContent = "Aucun résultat";
            box.appendChild(none);
        }
        for (const r of results) {
            const a = document.createElement("a");
            a.href = BASE + r.u;
            a.setAttribute("role", "option");
            const type = document.createElement("span");
            type.className = "t";
            type.textContent = r.t;
            const body = document.createElement("span");
            body.textContent = r.l;
            if (r.s) {
                const sub = document.createElement("span");
                sub.className = "s";
                sub.textContent = r.s;
                body.appendChild(sub);
            }
            a.append(type, body);
            box.appendChild(a);
        }
        box.style.display = "block";
    }

    if (input) {
        input.addEventListener("focus", loadIndex, { once: true });
        input.addEventListener("input", async () => {
            const q = input.value.trim();
            if (q.length < 2) { box.style.display = "none"; return; }
            await loadIndex();
            if (input.value.trim() === q) render(lookup(q));
        });
        input.addEventListener("keydown", (e) => {
            const items = [...box.querySelectorAll("a[href]")];
            if (e.key === "Escape") { box.style.display = "none"; input.blur(); return; }
            if (!items.length) return;
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                active = (active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
                items.forEach((a, i) => a.classList.toggle("on", i === active));
            } else if (e.key === "Enter") {
                e.preventDefault();
                (items[Math.max(active, 0)]).click();
            }
        });
        document.addEventListener("click", (e) => {
            if (!e.target.closest(".search")) box.style.display = "none";
        });
        document.addEventListener("keydown", (e) => {
            if (e.key === "/" && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
                e.preventDefault();
                input.focus();
            }
        });
    }

    // ---- hemicycle colouring modes ----
    const modes = document.getElementById("hemi-modes");
    const hemi = document.getElementById("hemi");
    if (modes && hemi) {
        modes.addEventListener("click", (e) => {
            const btn = e.target.closest("button[data-mode]");
            if (!btn) return;
            const mode = btn.dataset.mode;
            hemi.classList.remove("by-signal", "by-wealth", "by-group");
            hemi.classList.add(mode);
            modes.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b === btn));
            document.querySelectorAll(".hemi-legend").forEach((l) => l.classList.toggle("on", l.dataset.for === mode));
        });
    }

    // ---- a portrait that fails to load becomes the person's initials ----
    function toInitials(img) {
        const span = document.createElement("span");
        span.className = img.className + " ph";
        span.style.cssText = img.style.cssText;
        span.textContent = img.dataset.i || "";
        span.setAttribute("aria-hidden", "true");
        img.replaceWith(span);
    }
    document.querySelectorAll("img.avatar").forEach((img) => {
        if (img.complete && img.naturalWidth === 0) toInitials(img);
        else img.addEventListener("error", () => toInitials(img), { once: true });
    });

    // ---- theme toggle: the reader's choice overrides the OS setting ----
    const themeBtn = document.getElementById("theme");
    if (themeBtn) {
        themeBtn.addEventListener("click", () => {
            const root = document.documentElement;
            const osDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
            const current = root.dataset.theme || (osDark ? "dark" : "light");
            const next = current === "dark" ? "light" : "dark";
            root.dataset.theme = next;
            try { localStorage.setItem("pv-theme", next); } catch (e) { /* private mode */ }
        });
    }

})();
