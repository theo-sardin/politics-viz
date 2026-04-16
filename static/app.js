/* Vanilla helpers to fetch JSON and render Chart.js charts.
   Exported globals: pv.fetchJSON, pv.barChart, pv.stackedBarChart, pv.formatEur, pv.fillTable. */
window.pv = (function () {
    async function fetchJSON(url) {
        const r = await fetch(url);
        if (!r.ok) throw new Error(`${url} → ${r.status}`);
        return r.json();
    }

    function barChart(canvas, { labels, data, color = "#0d378a", onBarClick, label = "" }) {
        return new Chart(canvas.getContext("2d"), {
            type: "bar",
            data: { labels, datasets: [{ label, data, backgroundColor: color, borderRadius: 3 }] },
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
                    x: { ticks: { callback: (v) => formatEur(v) }, grid: { color: gridColor() } },
                    y: { ticks: { autoSkip: false, font: { size: 11 } }, grid: { display: false } },
                },
            },
        });
    }

    function stackedBarChart(canvas, { labels, datasets, onBarClick }) {
        datasets.forEach(ds => { ds.borderRadius = 2; });
        return new Chart(canvas.getContext("2d"), {
            type: "bar",
            data: { labels, datasets },
            options: {
                indexAxis: "y",
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 10 }, padding: 8 } } },
                onClick: (evt, items) => {
                    if (!onBarClick || !items.length) return;
                    onBarClick(items[0].index);
                },
                scales: {
                    x: { stacked: true, ticks: { callback: (v) => formatEur(v) }, grid: { color: gridColor() } },
                    y: { stacked: true, ticks: { autoSkip: false, font: { size: 11 } }, grid: { display: false } },
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

    function fillTable(id, rows, columns) {
        const tbody = document.querySelector('#' + id + ' tbody');
        if (!rows || rows.length === 0) {
            tbody.innerHTML = `<tr><td colspan="${columns || 3}" class="empty">No data for this period.</td></tr>`;
            return;
        }
        return rows; // signal to caller that data exists
    }

    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    }

    function gridColor() {
        return window.matchMedia('(prefers-color-scheme: dark)').matches ? '#2b2c28' : '#e5e5e0';
    }

    return { fetchJSON, barChart, stackedBarChart, formatEur, fillTable, escapeHtml, gridColor };
})();
