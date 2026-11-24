/* List pages render every row; this hides, sorts and pages them in the browser.

   Markup contract:
     <form data-filters>
        <select name="x" data-filter>      a row matches when its data-x tokens include the value
        <input  name="q" data-filter>      a row matches when its data-text contains every word
        <button data-set="x" data-value>   same as a select, as a segmented control
        <select name="tri" data-sort>      sort by data-sort-<value>; option[data-dir=asc] to invert
     <… data-list data-page-size="60">     direct children carrying data-item are the rows
     [data-count] [data-empty] [data-more]
   The state lives in the query string, so a filtered view can be linked to. */
(function () {
    "use strict";
    const list = document.querySelector("[data-list]");
    const form = document.querySelector("form[data-filters]");
    if (!list || !form) return;

    const items = Array.from(list.querySelectorAll(":scope > [data-item]"));
    items.forEach((el, i) => { el._i = i; });
    const pageSize = parseInt(list.dataset.pageSize || "60", 10);
    const count = document.querySelector("[data-count]");
    const empty = document.querySelector("[data-empty]");
    const more = document.querySelector("[data-more]");
    const controls = Array.from(form.querySelectorAll("[data-filter]"));
    const sorter = form.querySelector("[data-sort]");
    const buttons = Array.from(form.querySelectorAll("[data-set]"));
    let shown = pageSize;

    const fold = (s) => s.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
        .replace(/[^a-z0-9]+/g, " ").trim();

    function readUrl() {
        const params = new URLSearchParams(location.search);
        controls.forEach((c) => { if (params.has(c.name)) c.value = params.get(c.name); });
        if (sorter && params.has(sorter.name)) sorter.value = params.get(sorter.name);
        buttons.forEach((b) => b.classList.toggle("on", (params.get(b.dataset.set) || "") === b.dataset.value));
    }

    function state() {
        const s = {};
        controls.forEach((c) => { if (c.value) s[c.name] = c.value; });
        buttons.forEach((b) => { if (b.classList.contains("on") && b.dataset.value) s[b.dataset.set] = b.dataset.value; });
        if (sorter && sorter.value) s[sorter.name] = sorter.value;
        return s;
    }

    function writeUrl(s) {
        const params = new URLSearchParams(location.search);
        const keys = new Set([...controls.map((c) => c.name), ...buttons.map((b) => b.dataset.set)]);
        if (sorter) keys.add(sorter.name);
        keys.forEach((k) => params.delete(k));
        Object.entries(s).forEach(([k, v]) => params.set(k, v));
        const qs = params.toString();
        history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "") + location.hash);
    }

    function apply() {
        const s = state();
        const words = s.q ? fold(s.q).split(" ").filter(Boolean) : [];
        const tokens = Object.entries(s).filter(([k]) => k !== "q" && !(sorter && k === sorter.name));
        const matches = items.filter((el) => {
            for (const [key, value] of tokens) {
                if (!(el.dataset[key] || "").split(" ").includes(value)) return false;
            }
            if (words.length) {
                const text = el.dataset.text || "";
                for (const w of words) if (!text.includes(w)) return false;
            }
            return true;
        });

        const key = sorter && sorter.value;
        if (key) {
            const attr = "sort" + key.charAt(0).toUpperCase() + key.slice(1);
            const asc = sorter.selectedOptions[0].dataset.dir === "asc";
            matches.sort((a, b) => {
                const x = a.dataset[attr] || "", y = b.dataset[attr] || "";
                const nx = Number(x), ny = Number(y);
                const cmp = (x !== "" && y !== "" && !isNaN(nx) && !isNaN(ny)) ? nx - ny : x.localeCompare(y, "fr");
                return (asc ? cmp : -cmp) || a._i - b._i;
            });
        } else {
            matches.sort((a, b) => a._i - b._i);
        }

        const keep = new Set(matches.slice(0, shown));
        items.forEach((el) => { el.hidden = !keep.has(el); });
        matches.slice(0, shown).forEach((el) => list.appendChild(el));

        if (count) count.textContent = matches.length + (matches.length > 1 ? " résultats" : " résultat");
        if (empty) empty.hidden = matches.length > 0;
        if (more) {
            more.hidden = matches.length <= shown;
            more.textContent = "Afficher la suite (" + (matches.length - shown) + " de plus)";
        }
        writeUrl(s);
    }

    function changed() { shown = pageSize; apply(); }

    controls.forEach((c) => c.addEventListener(c.tagName === "INPUT" ? "input" : "change", changed));
    if (sorter) sorter.addEventListener("change", changed);
    buttons.forEach((b) => b.addEventListener("click", () => {
        buttons.filter((o) => o.dataset.set === b.dataset.set).forEach((o) => o.classList.toggle("on", o === b));
        changed();
    }));
    if (more) more.addEventListener("click", () => { shown += pageSize; apply(); });
    form.addEventListener("submit", (e) => e.preventDefault());
    window.addEventListener("popstate", () => { readUrl(); changed(); });

    readUrl();
    apply();
})();
