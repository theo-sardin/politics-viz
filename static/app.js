/* Vanilla helpers to fetch JSON and render Chart.js charts.
   Exported globals: pv.fetchJSON, pv.barChart, pv.stackedBarChart. */
window.pv = (function () {
    async function fetchJSON(url) {
        const r = await fetch(url);
        if (!r.ok) throw new Error(`${url} → ${r.status}`);
        return r.json();
    }

    function barChart(canvas, { labels, data, color = "#0d378a", onBarClick, label = "" }) {
        const ctx = canvas.getContext("2d");
        const chart = new Chart(ctx, {
            type: "bar",
            data: { labels, datasets: [{ label, data, backgroundColor: color }] },
            options: {
                indexAxis: "y",
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                onClick: (evt, items) => {
                    if (!onBarClick || !items.length) return;
                    onBarClick(items[0].index);
                },
                scales: {
                    x: { ticks: { callback: (v) => formatEur(v) } },
                },
            },
        });
        return chart;
    }

    function stackedBarChart(canvas, { labels, datasets, onBarClick }) {
        const ctx = canvas.getContext("2d");
        return new Chart(ctx, {
            type: "bar",
            data: { labels, datasets },
            options: {
                indexAxis: "y",
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { position: "bottom" } },
                onClick: (evt, items) => {
                    if (!onBarClick || !items.length) return;
                    onBarClick(items[0].index);
                },
                scales: {
                    x: { stacked: true, ticks: { callback: (v) => formatEur(v) } },
                    y: { stacked: true },
                },
            },
        });
    }

    function formatEur(n) {
        if (n === null || n === undefined) return "";
        const abs = Math.abs(n);
        if (abs >= 1e9) return (n / 1e9).toFixed(1) + " Md€";
        if (abs >= 1e6) return (n / 1e6).toFixed(1) + " M€";
        if (abs >= 1e3) return (n / 1e3).toFixed(0) + " k€";
        return n + " €";
    }

    return { fetchJSON, barChart, stackedBarChart, formatEur };
})();
