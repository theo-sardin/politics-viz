/* Site behaviour, no dependency: search box, hover tooltips, hemicycle modes. */
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

    // ---- search ----
    const input = document.getElementById("q");
    const box = document.getElementById("q-results");
    let timer = null, active = -1, seq = 0;

    function render(results) {
        box.replaceChildren();
        active = -1;
        if (!results.length) {
            const empty = document.createElement("a");
            empty.style.gridTemplateColumns = "1fr";
            empty.style.color = "var(--muted)";
            empty.textContent = "Aucun résultat";
            box.appendChild(empty);
        }
        for (const r of results) {
            const a = document.createElement("a");
            a.href = r.url;
            a.setAttribute("role", "option");
            const type = document.createElement("span");
            type.className = "t";
            type.textContent = r.type;
            const body = document.createElement("span");
            body.textContent = r.label;
            if (r.sub || r.code) {
                const sub = document.createElement("span");
                sub.className = "s";
                sub.textContent = [r.code, r.sub].filter(Boolean).join(" · ");
                body.appendChild(sub);
            }
            a.append(type, body);
            box.appendChild(a);
        }
        box.style.display = "block";
    }

    if (input) {
        input.addEventListener("input", () => {
            clearTimeout(timer);
            const q = input.value.trim();
            if (q.length < 2) { box.style.display = "none"; return; }
            timer = setTimeout(async () => {
                const mine = ++seq;
                try {
                    const r = await fetch("/api/search?q=" + encodeURIComponent(q));
                    if (!r.ok || mine !== seq) return;
                    render(await r.json());
                } catch (err) { /* offline: leave the box as it is */ }
            }, 160);
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

    // ---- filter forms submit on change ----
    document.querySelectorAll("form.filters select").forEach((sel) => {
        sel.addEventListener("change", () => sel.form.submit());
    });
})();
