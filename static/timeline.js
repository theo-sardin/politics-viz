/* Interactive timeline slider for selecting an "as of" month.
   Depends on: pv.escapeHtml (from app.js).
   Usage: pv.timeline.render(container, months, onChange)

   months: [{month: 'YYYY-MM', n: int}, ...] sorted chronologically
   onChange(month): called with 'YYYY-MM' when the user picks a new month  */

window.pv = window.pv || {};
pv.timeline = (function () {

    const MONTH_NAMES = [
        'Janvier', 'Février', 'Mars', 'Avril', 'Mai', 'Juin',
        'Juillet', 'Août', 'Septembre', 'Octobre', 'Novembre', 'Décembre',
    ];

    /** 'YYYY-MM' → 'Juillet 2024' */
    function formatMonth(ym) {
        const [y, m] = ym.split('-');
        return MONTH_NAMES[parseInt(m, 10) - 1] + ' ' + y;
    }

    /**
     * Render the timeline inside *container*.
     *
     * @param {HTMLElement} container  - wrapper div
     * @param {Array}       months    - [{month, n}] sorted chronologically
     * @param {Function}    onChange  - callback(month) on selection change
     * @returns {{ setMonth: Function }}  control handle
     */
    function render(container, months, onChange) {
        if (!months.length) return { setMonth() {} };

        let selectedIdx = months.length - 1;  // default: latest month
        const maxN = Math.max(...months.map(m => m.n));

        // Build DOM
        container.innerHTML = `
            <div class="timeline-row">
                <button class="timeline-btn" data-dir="-1" title="Mois précédent">◀</button>
                <div class="timeline-bar"></div>
                <button class="timeline-btn" data-dir="1" title="Mois suivant">▶</button>
            </div>
            <div class="timeline-label"></div>
        `;

        const bar = container.querySelector('.timeline-bar');
        const label = container.querySelector('.timeline-label');

        // Create one tick per month
        const ticks = months.map((m, i) => {
            const tick = document.createElement('div');
            tick.className = 'timeline-tick';
            // Height: 20-100% proportional to log(n) for visual balance
            const h = 20 + 80 * Math.log(1 + m.n) / Math.log(1 + maxN);
            tick.style.height = h.toFixed(0) + '%';
            tick.title = formatMonth(m.month) + ' — ' + m.n + ' déclarations';
            tick.addEventListener('click', () => select(i));
            bar.appendChild(tick);
            return tick;
        });

        // Arrow buttons
        container.querySelectorAll('.timeline-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const dir = parseInt(btn.dataset.dir, 10);
                select(Math.max(0, Math.min(months.length - 1, selectedIdx + dir)));
            });
        });

        function select(idx) {
            if (idx === selectedIdx && ticks[idx].classList.contains('active')) return;
            ticks[selectedIdx]?.classList.remove('active');
            selectedIdx = idx;
            ticks[idx].classList.add('active');
            const m = months[idx];
            label.textContent = formatMonth(m.month) + ' · ' + m.n + ' déclarations';
            onChange(m.month);
        }

        function setMonth(ym) {
            const idx = months.findIndex(m => m.month === ym);
            if (idx >= 0) select(idx);
        }

        // Initial selection (latest month), no callback — caller loads default data
        ticks[selectedIdx].classList.add('active');
        const m = months[selectedIdx];
        label.textContent = formatMonth(m.month) + ' · ' + m.n + ' déclarations';

        return { setMonth };
    }

    return { render, formatMonth };
})();
