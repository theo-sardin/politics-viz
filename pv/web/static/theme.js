/* Applies a saved theme before first paint. Loaded synchronously from <head>,
   as a file rather than inline so the page can forbid inline scripts. */
(function () {
    try {
        var t = new URLSearchParams(location.search).get("theme") || localStorage.getItem("pv-theme");
        if (t === "dark" || t === "light") document.documentElement.dataset.theme = t;
    } catch (e) { /* storage unavailable: keep the OS setting */ }
})();
